<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import EChartPanel from './EChartPanel.vue'
import BaseModal from './BaseModal.vue'
import { api } from '../lib/api'
import { confirmPlanningLeaveWithoutEditor } from '../lib/planningLeaveGuard.js'
import { selectPortfolioOverviewSnapshot } from '../lib/presentationDomain.js'
import { cockpitBuildingPath, cockpitBuildings, cockpitComparison, cockpitDetailAccess, cockpitMetric, cockpitTrend } from '../lib/cockpitDomain.js'
import { COCKPIT_TOUR_SECONDS, cockpitTourStops, nextCockpitTourStop } from '../lib/cockpitTour.js'
import { buildingAnalyticsSnapshot, createCockpitResourceCache } from '../lib/cockpitData.js'
import { normalizeRoom, getUseCategory, parseFloorNumber, profileAnalysisSegments, roomAnalysisSegment } from '../lib/roomsDomain.js'
import '../styles/unified-cockpit.css'

const RentTimelineAnalytics = defineAsyncComponent(() => import('./RentTimelineAnalytics.vue'))
const OperationsAnalyticsView = defineAsyncComponent(() => import('../views/OperationsAnalyticsView.vue'))
const DecisionWorkbench = defineAsyncComponent(() => import('./DecisionWorkbench.vue'))
const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  operations: { type: Object, default: () => ({}) },
  snapshot: { type: Object, default: () => ({}) },
  portfolioSnapshot: { type: Object, default: () => ({}) },
  operationsProfile: { type: Object, default: () => ({}) },
  user: { type: Object, default: () => ({}) },
  overviewOnly: Boolean,
  availableTabs: { type: Array, default: () => [] },
  initialBuildingCode: { type: String, default: '' },
  scopeLabel: { type: String, default: '' }
})
const emit = defineEmits(['exit', 'notify'])
const topics = [
  { key: 'overview', name: '经营总览', caption: '看全局' },
  { key: 'rent', name: '租金与出租', caption: '看变化' },
  { key: 'operations', name: '经营结构', caption: '找原因' },
  { key: 'space', name: '房源分布', caption: '看空间' },
  { key: 'decision', name: '预算与策略', caption: '比方案' }
]
const topic = ref('overview')
const selectedCode = ref(props.initialBuildingCode || (props.snapshot?.scope?.scope_type === 'building' ? props.snapshot.scope.scope_code : ''))
const decisionCode = ref(selectedCode.value)
const decisionScopeTitle = ref('')
const selectedMetric = ref('occupancy.area_rate')
const detailSnapshot = ref(null)
const detailLoading = ref(false)
const loadError = ref('')
const rooms = ref([])
const roomsLoading = ref(false)
const roomUse = ref('all')
const roomStatus = ref('all')
const roomSegment = ref('all')
const visitedDecision = ref(false)
const planningEditor = ref(null)
const touring = ref(false)
const tourCountdown = ref(0)
const pageVisible = ref(!document.hidden)
const contentElement = ref(null)
const modal = ref({ open: false, title: '', rows: [], facts: [], loading: false, error: '' })
let requestSequence = 0
let roomSequence = 0
let modalSequence = 0
let tourTimer = 0
let tourDeadline = 0
const roomData = createCockpitResourceCache()

const source = computed(() => props.overviewOnly && props.portfolioSnapshot?.contract_version === 'portfolio-overview-v1' ? props.portfolioSnapshot : props.snapshot)
const buildings = computed(() => cockpitBuildings(source.value))
const tourStops = computed(() => cockpitTourStops(source.value, props.user))
const isPortfolio = computed(() => source.value?.scope?.scope_type === 'portfolio')
const selectedBuilding = computed(() => buildings.value.find(row => row.code === selectedCode.value))
const scopeTitle = computed(() => selectedBuilding.value?.name || source.value?.scope?.display_name || props.scopeLabel || '资产组合')
const selectionSnapshot = computed(() => isPortfolio.value ? selectPortfolioOverviewSnapshot(source.value, selectedCode.value) : source.value)
const visibleSnapshot = computed(() => detailSnapshot.value || selectionSnapshot.value)
const access = computed(() => cockpitDetailAccess(props.user, selectedCode.value))
const decisionAccess = computed(() => cockpitDetailAccess(props.user, decisionCode.value))
const canWritePlanning = computed(() => decisionAccess.value.allowed && (props.user?.scope?.items || [])
  .some(row => String(row.code || '').toLowerCase() === decisionCode.value.toLowerCase() && row.can_edit === true))
const identityKey = computed(() => JSON.stringify([
  props.user?.id || '', props.user?.username || '', props.user?.role || '',
  props.user?.scope?.authorization_version ?? props.user?.authorization_version ?? '',
  props.user?.scope?.gate_policy_version || '', props.user?.scope?.items || []
]))
const detailKey = computed(() => `${identityKey.value}:${selectedCode.value}`)
const liveStats = computed(() => detailSnapshot.value?.legacy_contract_stats || {})
const liveProfile = computed(() => access.value.profile)
const asOfDate = computed(() => visibleSnapshot.value?.as_of_date || source.value?.as_of_date || '—')
const sourceTime = computed(() => {
  const stamps = visibleSnapshot.value?.timestamps || {}
  return String(stamps.source_observed_at || stamps.source_updated_at || stamps.published_at || '').slice(0, 16).replace('T', ' ')
})
const metrics = [
  { key: 'occupancy.area_rate', label: '面积出租率', unit: '%', digits: 1 },
  { key: 'rent.effective_daily', label: '有效平均日租', unit: '元/㎡/天', digits: 2 },
  { key: 'area.leased_sqm', label: '在租面积', unit: '㎡', digits: 0 },
  { key: 'billing.receivable_month', label: '本月应收', unit: '万元', divisor: 10000, digits: 1 },
  { key: 'collection.rate_month', label: '本月收缴率', unit: '%', digits: 1 },
  { key: 'arrears.balance', label: '逾期余额', unit: '万元', divisor: 10000, digits: 1 },
  { key: 'expiry.area_pressure_365d', label: '一年到期压力', unit: '%', digits: 1 },
  { key: 'risk.open_items', label: '未关闭风险', unit: '项', digits: 0 }
]
const metricDefinition = computed(() => metrics.find(row => row.key === selectedMetric.value) || metrics[0])
const kpis = computed(() => metrics.map(row => ({ ...row, value: cockpitMetric(visibleSnapshot.value, row.key) })))
const compareRows = computed(() => {
  const rows = cockpitComparison(source.value, selectedMetric.value)
  if (rows.length) return rows
  return buildings.value.map(row => ({ ...row, value: cockpitMetric(source.value, selectedMetric.value) }))
})
const trend = computed(() => cockpitTrend(visibleSnapshot.value))
const format = (value, digits = 1) => value === null || value === undefined || value === '' ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
const metricText = (row) => format(row.value === null ? null : row.value / (row.divisor || 1), row.digits)
const chartGrid = { top: 35, left: 65, right: 25, bottom: 55, containLabel: true }
const comparisonOption = computed(() => ({
  animationDurationUpdate: 450,
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: chartGrid,
  xAxis: { type: 'value', name: metricDefinition.value.unit, nameLocation: 'middle', nameGap: 32 },
  yAxis: { type: 'category', inverse: true, data: compareRows.value.map(row => row.name), axisLabel: { width: 160, overflow: 'truncate' } },
  series: [{ name: metricDefinition.value.label, type: 'bar', barMaxWidth: 26, data: compareRows.value.map(row => ({ value: row.value === null ? null : row.value / (metricDefinition.value.divisor || 1), buildingCode: row.code, itemStyle: { color: row.code === selectedCode.value ? '#e6b567' : '#5b9bd5', borderRadius: [0, 4, 4, 0] } })), label: { show: true, position: 'right', formatter: p => format(p.value, metricDefinition.value.digits) } }]
}))
const trendOption = computed(() => ({
  animationDurationUpdate: 450, tooltip: { trigger: 'axis' }, legend: { bottom: 0 }, grid: chartGrid,
  xAxis: { type: 'category', data: trend.value.map(row => row.month) }, yAxis: { type: 'value', name: '万元' },
  series: [
    { name: '应收', type: 'bar', barMaxWidth: 30, itemStyle: { color: '#5b9bd5' }, data: trend.value.map(row => row.receivable === null ? null : row.receivable / 10000) },
    { name: '已收', type: 'line', symbolSize: 7, itemStyle: { color: '#e6b567' }, data: trend.value.map(row => row.received === null ? null : row.received / 10000) }
  ]
}))
const areaOption = computed(() => {
  const total = cockpitMetric(visibleSnapshot.value, 'area.rentable_sqm')
  const leased = cockpitMetric(visibleSnapshot.value, 'area.leased_sqm')
  return { tooltip: { trigger: 'item' }, legend: { bottom: 0 }, series: [{ type: 'pie', radius: ['48%', '72%'], center: ['50%', '43%'], label: { show: false }, data: total !== null && leased !== null && total >= leased ? [{ name: '在租面积', value: leased, itemStyle: { color: '#5b9bd5' } }, { name: '待租面积', value: total - leased, itemStyle: { color: '#e6b567' } }] : [] }] }
})
const roomSegments = computed(() => profileAnalysisSegments(liveProfile.value))
const filteredRooms = computed(() => rooms.value.filter(row => {
  const segment = roomAnalysisSegment(row, liveProfile.value)
  return (roomUse.value === 'all' || getUseCategory(row) === roomUse.value)
    && (roomSegment.value === 'all' || segment?.aliases?.includes(roomSegment.value))
    && (roomStatus.value === 'all' || (roomStatus.value === 'vacant' ? !row.contract : Boolean(row.contract)))
}))
const floorRows = computed(() => {
  const groups = new Map()
  for (const room of filteredRooms.value) {
    const key = `${room.tower || '楼宇'} · ${room.floor || '未注明楼层'}`
    if (!groups.has(key)) groups.set(key, { key, tower: room.tower, floor: room.floor, rooms: [] })
    groups.get(key).rooms.push(room)
  }
  return [...groups.values()].sort((a, b) => a.tower.localeCompare(b.tower, 'zh-CN') || (parseFloorNumber(b.floor) ?? 0) - (parseFloorNumber(a.floor) ?? 0))
})

function stopTour() {
  touring.value = false
  window.clearInterval(tourTimer)
  tourTimer = 0
  tourCountdown.value = 0
  tourDeadline = 0
}
async function changeTopic(key, manual = true) {
  if (manual) stopTour()
  if (manual && key === 'decision' && selectedCode.value !== decisionCode.value
    && !await requestLeave('切换财务项目')) return false
  topic.value = key
  if (key === 'decision') visitedDecision.value = true
  if (key === 'space' && access.value.allowed) void loadRooms()
}
async function chooseBuilding(code, manual = true) {
  if (manual) stopTour()
  if (code && !buildings.value.some(row => row.code === code && row.selectable)) return
  if (!code && !isPortfolio.value) return
  if (code === selectedCode.value) return true
  if (manual && topic.value === 'decision' && !await requestLeave('切换楼宇')) return false
  selectedCode.value = code
  return true
}
async function requestLeave(label, options) {
  return planningEditor.value ? planningEditor.value.requestLeave(label, options) : confirmPlanningLeaveWithoutEditor(options)
}
async function requestExit() {
  if (await requestLeave('退出驾驶舱')) emit('exit')
}
async function backToOverview() {
  if (await chooseBuilding('')) void changeTopic('overview')
}
defineExpose({ requestLeave })
function advanceTour() {
  const next = nextCockpitTourStop(tourStops.value, {
    buildingCode: selectedCode.value, topic: topic.value, metric: selectedMetric.value
  })
  if (!next) { stopTour(); return }
  chooseBuilding(next.buildingCode, false)
  if (next.metric) selectedMetric.value = next.metric
  changeTopic(next.topic, false)
  if (contentElement.value) contentElement.value.scrollTop = 0
  tourCountdown.value = COCKPIT_TOUR_SECONDS
  tourDeadline = Date.now() + COCKPIT_TOUR_SECONDS * 1000
}
function toggleTour() {
  if (touring.value) { stopTour(); return }
  touring.value = true
  // Move on the first click; never spend a whole interval redisplaying this page.
  advanceTour()
  if (!touring.value) return
  tourTimer = window.setInterval(() => {
    if (document.hidden) { tourDeadline = Date.now() + COCKPIT_TOUR_SECONDS * 1000; return }
    const remaining = Math.ceil((tourDeadline - Date.now()) / 1000)
    if (remaining <= 0) advanceTour()
    else tourCountdown.value = remaining
  }, 1000)
}
function interaction(event) {
  if (event.target?.closest?.('[data-tour-control]')) return
  if (touring.value) stopTour()
}
async function loadSelectedDetail({ quiet = false } = {}) {
  const requestId = ++requestSequence
  if (!quiet) {
    ++roomSequence; ++modalSequence
    detailSnapshot.value = null; rooms.value = []; detailLoading.value = false; roomsLoading.value = false
    modal.value.open = false
    roomUse.value = 'all'; roomStatus.value = 'all'; roomSegment.value = 'all'
  }
  loadError.value = ''
  if (!access.value.allowed || !selectedBuilding.value?.selectable) return
  const buildingCode = selectedCode.value
  if (!quiet) detailLoading.value = true
  try {
    const result = buildingAnalyticsSnapshot(source.value, buildingCode)
      ? source.value
      : await api(cockpitBuildingPath('/api/operations/snapshot', buildingCode), { includeScope: false })
    if (requestId !== requestSequence) return
    detailSnapshot.value = result
    if (topic.value === 'space') void loadRooms()
  } catch (error) {
    if (requestId === requestSequence) loadError.value = error.message || '楼宇数据暂时无法读取'
  } finally {
    if (requestId === requestSequence) detailLoading.value = false
  }
}
async function loadRooms() {
  if (!access.value.allowed || !access.value.tabs.includes('rooms')) return
  const requestId = ++roomSequence
  const buildingCode = selectedCode.value
  const resourceKey = detailKey.value
  roomsLoading.value = true
  try {
    const result = await roomData.read(resourceKey, async () => {
      const payload = await api(cockpitBuildingPath('/api/rooms?include_operations=true', buildingCode), { includeScope: false })
      return (Array.isArray(payload) ? payload : []).map(normalizeRoom)
    })
    if (requestId === roomSequence && resourceKey === detailKey.value && access.value.allowed) rooms.value = result
    return result
  } catch (error) {
    if (requestId === roomSequence) loadError.value = error.message || '房源暂时无法读取'
  } finally { if (requestId === roomSequence) roomsLoading.value = false }
}
function showFacts(title, facts) {
  stopTour(); ++modalSequence
  modal.value = { open: true, title, facts, rows: [], loading: false, error: '' }
}
function openRoom(room) {
  showFacts(`${room.tower} ${room.room_no}`, [
    ['用途', room.room_type], ['楼层', room.floor], ['面积', `${format(room.area, 2)} ㎡`],
    ['状态', room.contract ? '在租' : '空置'], ['客户', room.contract?.tenant_name || '—'],
    ['合同月租', room.contract?.monthly_rent != null ? `${format(room.contract.monthly_rent, 2)} 元` : '—']
  ])
  if (room.contract?.id || room.contract?.contract_id) modal.value.rows = [{ id: room.contract.id || room.contract.contract_id, title: '打开关联合同', subtitle: room.contract.contract_no || '', contractId: room.contract.id || room.contract.contract_id }]
}
async function openContract(payload) {
  const id = typeof payload === 'object' ? payload?.id : payload
  if (!id || !access.value.allowed || !access.value.tabs.includes('contracts')) return
  stopTour()
  const requestId = ++modalSequence
  modal.value = { open: true, title: '合同详情', facts: [], rows: [], loading: true, error: '' }
  try {
    const contract = await api(cockpitBuildingPath(`/api/contracts/${encodeURIComponent(id)}`, selectedCode.value), { includeScope: false })
    if (requestId !== modalSequence) return
    modal.value.title = contract.contract_no || '合同详情'
    modal.value.facts = [
      ['客户', contract.tenant_name || contract.customer_name], ['租赁开始', contract.start_date], ['租赁结束', contract.end_date],
      ['面积', `${format(contract.area, 2)} ㎡`], ['月租金', `${format(contract.monthly_rent, 2)} 元`],
      ['免租期', contract.free_period ?? '—'], ['状态', contract.status || '—']
    ]
  } catch (error) { if (requestId === modalSequence) modal.value.error = error.message }
  finally { if (requestId === modalSequence) modal.value.loading = false }
}
async function drilldown(intent = {}) {
  const target = intent.target || intent.tab || intent
  if (target === 'analytics') { changeTopic('rent'); return }
  if (target === 'rooms' && access.value.allowed) {
    changeTopic('space')
    roomUse.value = intent.filters?.use || 'all'
    roomSegment.value = roomSegments.value.find(segment => segment.aliases.includes(intent.filters?.tower))?.filterValue || 'all'
    roomStatus.value = intent.filters?.occupancy === '空置' ? 'vacant' : 'all'
    return
  }
  if (target === 'contracts' && access.value.allowed && access.value.tabs.includes('contracts')) {
    stopTour()
    const requestId = ++modalSequence
    modal.value = { open: true, title: intent.filters?.status ? `${intent.filters.status}合同` : '合同', facts: [], rows: [], loading: true, error: '' }
    const query = new URLSearchParams({ page: '1', page_size: '100' })
    if (intent.filters?.status) query.set('status', intent.filters.status)
    if (intent.filters?.sortBy) query.set('sort_by', intent.filters.sortBy)
    if (intent.filters?.sortDir) query.set('sort_dir', intent.filters.sortDir)
    try {
      const result = await api(cockpitBuildingPath(`/api/contracts?${query}`, selectedCode.value), { includeScope: false })
      if (requestId !== modalSequence) return
      modal.value.rows = (result.items || []).map(row => ({ id: row.id, contractId: row.id, title: row.tenant_name || row.contract_no, subtitle: `${row.contract_no || ''} · ${row.end_date || '—'} 到期`, value: `${format(row.monthly_rent, 2)} 元/月` }))
      if (result.total > 100) modal.value.facts = [['展示范围', `前 100 份，共 ${result.total} 份`]]
    } catch (error) { if (requestId === modalSequence) modal.value.error = error.message }
    finally { if (requestId === modalSequence) modal.value.loading = false }
    return
  }
  if (target === 'customers') {
    const customers = liveStats.value.customer_data || []
    const rows = Array.isArray(customers) ? customers : []
    const selected = rows.find(row => (intent.filters?.customerId && String(row.customer_id) === String(intent.filters.customerId)) || (intent.filters?.keyword && row.tenant_name === intent.filters.keyword))
    showFacts(selected?.tenant_name || '客户经营', selected ? [['承租面积', `${format(selected.area, 2)} ㎡`], ['月租金', `${format(selected.rent, 2)} 元`], ['承租房源', selected.room_count ?? '—']] : [['客户数量', liveStats.value.customer_count ?? rows.length], ['范围', scopeTitle.value]])
    return
  }
  showFacts(target === 'risks' ? '风险与到期' : '收缴情况', kpis.value.filter(row => target === 'risks' ? ['risk.open_items', 'expiry.area_pressure_365d', 'arrears.balance'].includes(row.key) : ['billing.receivable_month', 'collection.rate_month', 'arrears.balance'].includes(row.key)).map(row => [row.label, `${metricText(row)} ${row.unit}`]))
}
function selectKpi(row) { selectedMetric.value = row.key; changeTopic('overview') }
function closeModal() { ++modalSequence; modal.value.open = false }
function onVisibilityChange() { pageVisible.value = !document.hidden }

// A hidden editor keeps its own project and unsaved inputs while the display
// tour visits other buildings. Authorization changes still invalidate its key.
watch(() => [topic.value, selectedCode.value, scopeTitle.value], () => {
  if (topic.value !== 'decision') return
  decisionCode.value = selectedCode.value
  decisionScopeTitle.value = scopeTitle.value
})
watch(identityKey, () => {
  roomData.clear()
  ++requestSequence; ++roomSequence; ++modalSequence
  detailSnapshot.value = null; rooms.value = []; modal.value.open = false
}, { flush: 'sync' })
watch(detailKey, () => loadSelectedDetail(), { immediate: true })
watch(source, () => {
  roomData.clear()
  if (detailSnapshot.value && !detailLoading.value) void loadSelectedDetail({ quiet: true })
})
watch(() => buildings.value.map(row => `${row.code}:${row.selectable}`).join('|'), () => {
  if (selectedCode.value && !selectedBuilding.value?.selectable) selectedCode.value = isPortfolio.value ? '' : buildings.value.find(row => row.selectable)?.code || ''
})
watch(() => props.user, () => { if (!access.value.allowed) { ++requestSequence; ++roomSequence; ++modalSequence; detailSnapshot.value = null; rooms.value = []; modal.value.open = false; detailLoading.value = false } }, { deep: true })
onMounted(() => {
  document.body.classList.add('unified-cockpit-mode')
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  ++requestSequence; ++roomSequence; ++modalSequence; roomData.clear(); stopTour()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.body.classList.remove('unified-cockpit-mode')
})
</script>

<template>
  <section class="unified-cockpit" data-testid="unified-cockpit" @pointerdown="interaction" @keydown="interaction" @wheel.passive="interaction">
    <header class="uc-header">
      <div class="uc-brand"><span class="uc-brand-rule" aria-hidden="true"></span><div><small>{{ $t("资产经营") }}</small><h1>{{ $t("经营驾驶舱") }}</h1></div></div>
      <nav class="uc-topics" :aria-label="$t(&quot;驾驶舱主题&quot;)">
        <button v-for="item in topics" :key="item.key" type="button" :class="{ active: topic === item.key }" :aria-pressed="topic === item.key" :data-testid="`cockpit-topic-${item.key}`" @click="changeTopic(item.key)"><span>{{ item.name }}</span><small>{{ $t(item.caption) }}</small></button>
      </nav>
      <div class="uc-actions"><button type="button" class="btn" data-tour-control data-testid="cockpit-tour" :aria-pressed="touring" :disabled="!tourStops.length" @click="toggleTour">{{ $t(touring ? '暂停巡览' : '自动巡览') }}</button><button type="button" class="btn" data-testid="cockpit-exit" @click="requestExit">{{ $t("退出大屏") }}</button></div>
    </header>
    <div class="uc-context">
      <div><strong>{{ $t(scopeTitle) }}</strong><span>{{ $t("统计日 ") }}{{ $t(asOfDate) }}</span><span v-if="sourceTime">{{ $t("来源更新 ") }}{{ $t(sourceTime) }}</span><span v-if="touring" class="uc-tour-status" data-testid="cockpit-tour-status" aria-live="off">{{ $t("巡览中 · ") }}{{ $t(tourCountdown) }}{{ $t(" 秒后切换") }}</span></div>
      <div class="uc-scope-controls"><label for="uc-building">{{ $t("楼宇") }}</label><select id="uc-building" :value="selectedCode" data-testid="cockpit-building-select" @change="chooseBuilding($event.target.value); $event.target.value = selectedCode"><option v-if="isPortfolio" value="">{{ $t("全部楼宇") }}</option><option v-for="building in buildings" :key="building.code" :value="building.code" :disabled="!building.selectable">{{ building.name }}</option></select><button v-if="selectedCode && isPortfolio" class="btn" type="button" data-testid="cockpit-back-overview" @click="backToOverview">{{ $t("返回总览") }}</button></div>
    </div>
    <div ref="contentElement" class="uc-content">
      <div v-if="loadError" class="uc-error" role="alert">{{ $t(loadError) }}<button type="button" class="btn" @click="loadSelectedDetail">{{ $t("重新读取") }}</button></div>
      <template v-if="topic === 'overview'">
        <div class="uc-kpis" :aria-label="$t(&quot;关键经营指标&quot;)"><button v-for="row in kpis" :key="row.key" type="button" :class="{ active: selectedMetric === row.key }" :data-testid="`cockpit-metric-${row.key}`" @click="selectKpi(row)"><span>{{ $t(row.label) }}</span><strong>{{ $t(metricText(row)) }}<small>{{ $t(row.unit) }}</small></strong></button></div>
        <div class="uc-overview-grid">
          <section class="uc-building-panel"><div class="uc-section-title"><div><small>{{ $t("项目组合") }}</small><h2>{{ $t("从一栋楼，看到经营全貌") }}</h2></div><span>{{ $t(buildings.length) }}{{ $t(" 栋") }}</span></div>
            <div class="uc-buildings"><button v-for="(building, index) in buildings" :key="building.code" type="button" class="uc-building" :class="{ selected: selectedCode === building.code }" :disabled="!building.selectable" :data-testid="`cockpit-building-${building.code}`" @click="chooseBuilding(building.code)"><span class="uc-building-number">{{ $t(String(index + 1).padStart(2, '0')) }}</span><strong>{{ building.name }}</strong><span class="uc-building-metrics"><span v-for="row in building.metrics.slice(0, 3)" :key="row.key"><small>{{ $t(row.label) }}</small><b>{{ $t(row.value) }}</b></span></span><span class="uc-building-action">{{ $t(building.selectable ? (selectedCode === building.code ? '当前楼宇' : '展开经营情况 →') : building.statusLabel) }}</span></button></div>
          </section>
          <section class="uc-chart-panel"><div class="uc-section-title"><div><small>{{ $t("横向比较") }}</small><h2>{{ $t(metricDefinition.label) }}</h2></div><select v-model="selectedMetric" :aria-label="$t(&quot;比较指标&quot;)"><option v-for="row in metrics" :key="row.key" :value="row.key">{{ $t(row.label) }}</option></select></div><EChartPanel :option="comparisonOption" :height="300" :empty="!compareRows.some(row => row.value !== null)" :aria-label="$t(&quot;楼宇经营指标比较，点击柱形选择楼宇&quot;)" @chart-click="chooseBuilding($event.data?.buildingCode)" /></section>
          <section class="uc-chart-panel uc-trend-panel"><div class="uc-section-title"><div><small>{{ $t("期间变化") }}</small><h2>{{ $t("应收与回款") }}</h2></div><span>{{ $t(trend.length) }}{{ $t(" 个期间") }}</span></div><EChartPanel :option="trendOption" :height="260" :empty="!trend.length" :empty-text="$t(&quot;当前范围尚无可比期间&quot;)" :aria-label="$t(&quot;各月应收和回款&quot;)" /></section>
          <section class="uc-chart-panel"><div class="uc-section-title"><div><small>{{ $t("出租结构") }}</small><h2>{{ $t("在租与待租") }}</h2></div><button class="btn" type="button" @click="changeTopic('space')">{{ $t("展开房源") }}</button></div><EChartPanel :option="areaOption" :height="260" :empty="!areaOption.series[0].data.length" :aria-label="$t(&quot;在租与待租面积&quot;)" @chart-click="roomStatus = $event.name === '待租面积' ? 'vacant' : 'leased'; changeTopic('space')" /></section>
        </div>
      </template>
      <template v-else-if="topic === 'rent'">
        <div v-if="detailLoading" class="uc-empty">{{ $t("正在读取 ") }}{{ $t(scopeTitle) }}{{ $t(" 的租金与合同…") }}</div>
        <section v-if="!detailLoading && (!access.allowed || !detailSnapshot)" class="uc-chart-panel"><div class="uc-section-title"><div><small>{{ $t("租金与出租") }}</small><h2>{{ $t(selectedCode ? scopeTitle : '楼宇租金比较') }}</h2></div></div><p class="uc-context-note">{{ $t(selectedCode ? '当前楼宇按已发布的整楼指标展示。' : '选择一栋楼查看租金变化与分区；当前展示楼宇间比较。') }}</p><div class="uc-kpis"><button v-for="row in kpis.slice(0, 3)" :key="row.key" type="button" @click="selectedMetric = row.key"><span>{{ $t(row.label) }}</span><strong>{{ $t(metricText(row)) }}<small>{{ $t(row.unit) }}</small></strong></button></div><EChartPanel :option="comparisonOption" :height="340" :empty="!compareRows.some(row => row.value !== null)" @chart-click="chooseBuilding($event.data?.buildingCode)" /></section>
      </template>
      <template v-else-if="topic === 'operations'">
        <div v-if="detailLoading" class="uc-empty">{{ $t("正在读取经营结构…") }}</div>
        <div v-else-if="!access.allowed || !detailSnapshot" class="uc-overview-grid"><section class="uc-chart-panel"><h2>{{ $t("经营指标") }}</h2><div class="uc-kpis"><button v-for="row in kpis" :key="row.key" type="button" @click="showFacts(row.label, [[scopeTitle, `${metricText(row)} ${row.unit}`], ['统计日', asOfDate]])"><span>{{ $t(row.label) }}</span><strong>{{ $t(metricText(row)) }}<small>{{ $t(row.unit) }}</small></strong></button></div></section><section class="uc-chart-panel"><h2>{{ $t("应收与回款") }}</h2><EChartPanel :option="trendOption" :height="330" :empty="!trend.length" :empty-text="$t(&quot;当前范围尚无可比期间&quot;)" /></section></div>
      </template>
      <template v-else-if="topic === 'space'">
        <section class="uc-chart-panel"><div class="uc-section-title"><div><small>{{ $t("空间分布") }}</small><h2>{{ $t(scopeTitle) }}{{ $t(" · 房源") }}</h2></div><div class="uc-room-filters"><label v-if="roomSegments.length > 1">{{ $t("分区") }}<select v-model="roomSegment" data-testid="cockpit-room-segment"><option value="all">{{ $t("全部分区") }}</option><option v-for="segment in roomSegments" :key="segment.code" :value="segment.filterValue">{{ $t(segment.label) }}</option></select></label><label>{{ $t("用途") }}<select v-model="roomUse" data-testid="cockpit-room-use"><option value="all">{{ $t("全部用途") }}</option><option value="office">{{ $t("办公") }}</option><option value="retail">{{ $t("商业配套") }}</option></select></label><label>{{ $t("状态") }}<select v-model="roomStatus"><option value="all">{{ $t("全部状态") }}</option><option value="vacant">{{ $t("空置") }}</option><option value="leased">{{ $t("在租") }}</option></select></label></div></div>
          <div v-if="!access.allowed" class="uc-empty">{{ $t(selectedCode ? '当前楼宇按整楼经营指标展示。' : '选择楼宇，展开楼层与房源。') }}</div><div v-else-if="roomsLoading" class="uc-empty">{{ $t("正在读取房源…") }}</div><div v-else-if="!floorRows.length" class="uc-empty">{{ $t("当前筛选下没有房源") }}</div>
          <div v-else class="uc-floors"><div class="uc-room-legend"><span><i class="leased"></i>{{ $t("在租") }}</span><span><i class="vacant"></i>{{ $t("空置") }}</span><span>{{ $t(filteredRooms.length) }}{{ $t(" 个房源 · ") }}{{ $t(format(filteredRooms.reduce((total, room) => total + Number(room.area || 0), 0), 0)) }} ㎡</span></div><div v-for="floor in floorRows" :key="floor.key" class="uc-floor"><strong>{{ $t(floor.key) }}</strong><div><button v-for="room in floor.rooms" :key="room.id" type="button" class="uc-room" :class="room.contract ? 'leased' : 'vacant'" :aria-label="`${room.room_no} ${room.contract ? '在租' : '空置'} ${room.area}平方米`" @click="openRoom(room)"><b>{{ room.room_no }}</b><small>{{ $t(format(room.area, 0)) }} ㎡</small></button></div></div></div>
        </section>
      </template>
      <KeepAlive :key="`rent:${identityKey}`" :max="5"><RentTimelineAnalytics v-if="topic === 'rent' && !detailLoading && access.allowed && detailSnapshot" :key="detailKey" :building-code="selectedCode" :user="user" :minimum-pixel-ratio="1" compact @open-contract="openContract" @notify="emit('notify', $event)" /></KeepAlive>
      <KeepAlive :key="`operations:${identityKey}`" :max="5"><OperationsAnalyticsView v-if="topic === 'operations' && !detailLoading && access.allowed && detailSnapshot" :key="detailKey" :building-code="selectedCode" :snapshot="detailSnapshot" :load-rooms="loadRooms" :stats="liveStats" :user="user" :operations-profile="liveProfile" :show-rent-timeline="false" @drilldown="drilldown" @select-tab="drilldown" @open-contract="openContract" @notify="emit('notify', $event)" /></KeepAlive>
      <DecisionWorkbench ref="planningEditor" v-if="visitedDecision" v-show="topic === 'decision'" :active="topic === 'decision' && pageVisible" :key="identityKey" :scope-key="decisionCode || source.scope?.scope_code || 'portfolio'" :scope-label="decisionScopeTitle" :user="user" :building-code="decisionCode" :can-read-planning="decisionAccess.allowed" :can-write-planning="canWritePlanning" @open-contract="openContract" />
    </div>
    <BaseModal :open="modal.open" :title="$t(modal.title)" size="wide" :layer-index="20" @close="closeModal"><div class="uc-detail" data-testid="cockpit-detail"><p v-if="modal.loading">{{ $t("正在读取…") }}</p><p v-else-if="modal.error" role="alert">{{ $t(modal.error) }}</p><template v-else><dl v-if="modal.facts.length"><div v-for="(fact, index) in modal.facts" :key="index"><dt>{{ $t(fact[0]) }}</dt><dd>{{ $t(fact[1] ?? '—') }}</dd></div></dl><div v-if="modal.rows.length" class="uc-detail-rows"><button v-for="row in modal.rows" :key="row.id" type="button" @click="row.contractId && openContract(row.contractId)"><span><strong>{{ $t(row.title) }}</strong><small>{{ $t(row.subtitle) }}</small></span><b>{{ $t(row.value) }}</b></button></div><p v-if="!modal.facts.length && !modal.rows.length">{{ $t("当前没有符合条件的记录") }}</p></template></div></BaseModal>
  </section>
</template>
