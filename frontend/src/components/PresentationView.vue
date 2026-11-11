<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { loadChartRuntime } from '../lib/chartRuntime'
import { formatCurrency } from '../lib/format'
import { buildBuildingOperationsProfileView } from '../lib/buildingOperationsProfile'
import AnimatedNumber from './AnimatedNumber.vue'
import PortfolioPresentationView from './presentation/PortfolioPresentationView.vue'
import PresentationKpiStrip from './presentation/PresentationKpiStrip.vue'

// 演示驾驶舱：16:9 横屏一屏无滚动，专用深色大屏配色（独立于操作主题）。
// 所有数据均由 App.vue 传入的同一经营快照提供，避免大屏各自请求造成口径或时点漂移。
const props = defineProps({
  stats: {
    type: Object,
    default: () => ({})
  },
  operations: {
    type: Object,
    default: () => ({})
  },
  snapshot: {
    type: Object,
    default: () => ({})
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
  },
  overviewOnly: {
    type: Boolean,
    default: false
  },
  embedded: {
    type: Boolean,
    default: false
  },
  availableTabs: {
    type: Array,
    default: null
  }
})
const emit = defineEmits(['select-building', 'navigate', 'back-overview'])

const clock = ref('')
let clockTimer = 0
function updateClock() {
  clock.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

function metricNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number : 0
}
function optionalNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
function optionalRate(value) {
  return optionalNumber(value)
}
// 大额金额以「万」压缩显示，驾驶舱远距离可读性优先
function formatWan(value) {
  const number = optionalNumber(value)
  if (number === null) return '--'
  const wan = number / 10000
  return `${wan.toLocaleString('zh-CN', { maximumFractionDigits: 1 })} 万`
}

const metrics = computed(() => props.operations.metrics || {})
const receivables = computed(() => props.stats.receivables || {})
const vacancy = computed(() => props.operations.vacancy || {})
const queueCounts = computed(() => props.operations.queue_counts || {})
const topActions = computed(() => (props.operations.todos || []).slice(0, 6))
const isPortfolio = computed(() => props.snapshot?.scope?.scope_type === 'portfolio')
const isSimulatedData = computed(() => (
  String(props.snapshot?.data_classification || '').trim().toLowerCase() === 'simulated'
))
const presentationOperationsProfile = computed(() => (
  props.operationsProfile?.profile_version
    ? props.operationsProfile
    : props.snapshot?.scope?.operations_profile || {}
))
const operationsProfileView = computed(() => (
  buildBuildingOperationsProfileView(presentationOperationsProfile.value)
))
const isLiveDetailProjection = computed(() => (
  props.operations?.projection?.source === 'live_detail'
))
const snapshotDataModeLabel = computed(() => (
  isLiveDetailProjection.value ? '明细数据状态' : '已发布数据'
))
const snapshotScopeTitle = computed(() => (
  isLiveDetailProjection.value
    ? `${operationsProfileView.value.primaryGroup?.name || '整楼'} · 明细适配器`
    : operationsProfileView.value.primaryGroup?.name
    ? `${operationsProfileView.value.primaryGroup.name} · 已发布快照`
    : '整楼经营快照'
))
const snapshotFactScopeLabel = computed(() => (
  isLiveDetailProjection.value
    ? '当前明细适配器可核验口径'
    : operationsProfileView.value.primaryGroup?.name
    ? `已发布“${operationsProfileView.value.primaryGroup.name}”口径`
    : '已发布整楼口径'
))

const scopeTitle = computed(() => {
  const scope = props.snapshot?.scope || {}
  if (scope.scope_type === 'building') {
    return scope.building?.name || scope.display_name || scope.building?.code || scope.scope_code || scope.building_codes?.[0] || '楼宇'
  }
  return scope.display_name || scope.scope_code || '组合'
})
const contributionRows = computed(() => {
  const serverPresentation = props.snapshot?.presentation?.contributions
  if (Array.isArray(serverPresentation)) return serverPresentation
  return []
})
const detailListsAvailable = computed(() => (
  props.operations?.projection?.detail_lists_available === true
  && props.operations?.projection?.detail_facts_available !== false
))

function formatSnapshotMetric(metric) {
  const value = optionalNumber(metric?.value)
  if (value === null) return '--'
  if (metric?.unit === 'percent') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
  if (metric?.unit === 'CNY') return `${formatWan(value)}`
  if (metric?.unit === 'sqm') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} ㎡`
  if (metric?.unit === 'CNY/sqm/day') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元/㎡·天`
  if (metric?.unit === 'contract') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 份`
  if (metric?.unit === 'item') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 项`
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

const snapshotFacts = computed(() => {
  const all = props.snapshot?.metrics || {}
  return [
    'occupancy.area_rate',
    'area.rentable_sqm',
    'area.leased_sqm',
    'collection.rate_month',
    'arrears.balance',
    'expiry.contract_count_90d'
  ].map((code) => {
    const metric = all[code] || {}
    return {
      code,
      label: metric.name || code,
      value: formatSnapshotMetric(metric),
      available: optionalNumber(metric.value) !== null,
      reason: metric.unavailable_reason || '当前已发布快照未提供该指标'
    }
  })
})
const snapshotUnavailableCount = computed(() => (
  snapshotFacts.value.filter((item) => !item.available).length
))
function contributionStatus(item) {
  const gate = item?.operating_gate
  const state = String(gate?.state || '').trim().toLowerCase()
  if (['observe', 'observing', 'observation', 'shadow'].includes(state)) {
    return '观察态 · 不计入板块合计'
  }
  if (gate?.contribution_enabled === false) return '当前不计入板块合计'
  if (item.publication_status === 'live_adapter') return '当天明细适配器'
  return item.publication_status === 'missing' ? '缺少同日数据' : '已有同日快照'
}
function contributionExcluded(item) {
  return item?.operating_gate?.contribution_enabled === false
}
const coverageLabel = computed(() => {
  const coverage = props.snapshot?.coverage || {}
  const authorized = Number(coverage.authorized_buildings || 0)
  const included = Number(
    coverage.same_day_snapshot_buildings
    ?? coverage.included_same_day_buildings
    ?? coverage.published_same_day_buildings
    ?? 0
  )
  if (!authorized) return ''
  if (isLiveDetailProjection.value) {
    const available = props.operations?.projection?.detail_facts_available === false ? 0 : 1
    return `可核验明细 ${available}/${authorized} 栋`
  }
  return `同日快照 ${included}/${authorized} 栋`
})

// 风险构成横条（中列趋势图下方）：逾期 / 本月未收 / 押金缺口
const riskBars = computed(() => {
  const m = metrics.value
  const items = [
    { key: 'overdue', label: '逾期余额', value: optionalNumber(m.overdue_balance), color: '#f87171' },
    { key: 'unpaid', label: '本月未收', value: optionalNumber(m.current_month_unpaid_balance), color: '#fbbf24' },
    { key: 'deposit', label: '押金缺口', value: optionalNumber(m.unpaid_deposit_amount), color: '#38bdf8' }
  ]
  const max = Math.max(...items.map((item) => item.value || 0), 1)
  return items.map((item) => ({
    ...item,
    width: item.value > 0 ? Math.max((item.value / max) * 100, 4) : 0
  }))
})

// 数据健康（右列底部）：资料与押金的数据完整度
const dataHealth = computed(() => {
  const detailAvailable = detailListsAvailable.value
  return [
    { key: 'body', label: '合同资料缺口', value: detailAvailable ? optionalNumber(queueCounts.value.missing_contract_body) : null, unit: '份' },
    { key: 'deposit', label: '押金未收', value: detailAvailable ? optionalNumber(queueCounts.value.unpaid_deposits) : null, unit: '笔' }
  ]
})

// 分区经营、单价和走势均来自后端统一快照，前端只负责展示。
const towerRows = ref([])

// 分区单价与走势：沿用后端租金分析引擎的面积加权、免租期归零口径。
// effective_daily_rent 为元/㎡·天——与系统 rent-analytics 页及行业报价惯例同口径展示
const LEGACY_PRICE_COLORS = {
  south_office: '#7dd3fc',
  north_office: '#fbbf24',
  commercial: '#34d399'
}
const priceSegmentDefinitions = ref([])
const trendPriceSegments = computed(() => {
  const normalized = priceSegmentDefinitions.value.map((segment) => ({
    key: segment.key,
    name: segment.label || segment.key,
    color: LEGACY_PRICE_COLORS[segment.key] || segment.color || '#7dd3fc'
  }))
  const partitions = normalized.filter((segment) => segment.key !== 'overall')
  return partitions.length ? partitions : normalized
})
const segmentKeyByName = computed(() => Object.fromEntries(
  priceSegmentDefinitions.value.map((segment) => [segment.label || segment.key, segment.key])
))
const segmentPrices = ref({})
const priceTrend = ref({ months: [], bySegment: {} })

function towerPrice(towerName) {
  const onlySegment = priceSegmentDefinitions.value.length === 1
    ? priceSegmentDefinitions.value[0]?.key
    : null
  const segmentKey = segmentKeyByName.value[towerName] || onlySegment
  const price = metricNumber(segmentPrices.value[segmentKey])
  return price > 0 ? `${price.toFixed(2)} 元/㎡·天` : '--'
}

// 全场平均单价（rent-analytics 口径：在租合同面积加权，免租期按零计入）
const avgUnitPrice = computed(() => metricNumber(segmentPrices.value.overall))

function supportedTarget(target) {
  if (!Array.isArray(props.availableTabs)) return target
  return props.availableTabs.includes(target) ? target : null
}

const kpiCards = computed(() => {
  if (!detailListsAvailable.value) {
    const facts = props.snapshot?.metrics || {}
    return [
      ['receivable', '本月应收', 'billing.receivable_month', 'cyan', 'receivables'],
      ['received', '本月已收', 'collection.received_month', 'green', 'receivables'],
      ['received-rate', '本月收缴率', 'collection.rate_month', 'green', 'receivables'],
      ['occupancy', '整体出租率', 'occupancy.area_rate', 'blue', 'rooms'],
      ['overdue', '逾期余额', 'arrears.balance', 'red', 'receivables'],
      ['expiring', '90 天内到期', 'expiry.contract_count_90d', 'amber', 'contracts']
    ].map(([key, label, code, tone, target]) => {
      const metric = facts[code] || {}
      return {
        key,
        label,
        value: formatSnapshotMetric(metric),
        tone,
        target: supportedTarget(target),
        available: optionalNumber(metric.value) !== null
      }
    })
  }
  const m = metrics.value
  const receivedRate = optionalRate(m.received_rate ?? receivables.value.received_rate)
  const occupancyRate = optionalRate(vacancy.value.occupancy_rate)
  const expiring = optionalNumber(m.expiring_contract_count_90)
  return [
    { key: 'receivable', label: '本月应收', value: formatWan(m.current_month_receivable), tone: 'cyan', target: 'receivables', available: optionalNumber(m.current_month_receivable) !== null },
    { key: 'received', label: '本月已收', value: formatWan(m.current_month_received), tone: 'green', target: 'receivables', available: optionalNumber(m.current_month_received) !== null },
    { key: 'received-rate', label: '本月收缴率', value: receivedRate === null ? '--' : `${receivedRate.toFixed(1)}%`, tone: 'green', target: 'receivables', available: receivedRate !== null },
    { key: 'occupancy', label: '整体出租率', value: occupancyRate === null ? '--' : `${occupancyRate.toFixed(1)}%`, tone: 'blue', target: 'rooms', available: occupancyRate !== null },
    { key: 'overdue', label: '逾期余额', value: formatWan(m.overdue_balance), tone: 'red', target: 'receivables', available: optionalNumber(m.overdue_balance) !== null },
    { key: 'expiring', label: '90 天内到期', value: expiring === null ? '--' : `${expiring} 份`, tone: 'amber', target: 'contracts', available: expiring !== null }
  ].map((item) => ({ ...item, target: supportedTarget(item.target) }))
})

const signalLights = computed(() => {
  if (props.overviewOnly) {
    return [{ key: 'green', label: '资产组合概况', count: 0, active: true }]
  }
  if (!detailListsAvailable.value) {
    return [{ key: 'amber', label: '明细待补', count: 0, active: true }]
  }
  const overdue = metricNumber(queueCounts.value.overdue_contracts)
  const attention = metricNumber(queueCounts.value.expiring_contracts) + metricNumber(queueCounts.value.due_follow_up_customers)
  const healthy = overdue === 0 && attention === 0
  return [
    { key: 'red', label: '逾期', count: overdue, active: overdue > 0 },
    { key: 'amber', label: '待跟进', count: attention, active: attention > 0 },
    { key: 'green', label: '正常', count: healthy ? 1 : 0, active: healthy }
  ]
})

// vacancy 对象实测字段：room_count（待租间数）、area（待租面积）、occupancy_rate（出租率）
const roomSummary = computed(() => ({
  vacantCount: optionalNumber(vacancy.value.room_count),
  occupancyRate: optionalRate(vacancy.value.occupancy_rate),
  vacantArea: optionalNumber(vacancy.value.area)
}))

function actionTitle(action) {
  return action.title || action.tenant_name || action.customer_name || action.meta?.tenant_name
    || action.meta?.customer_name || action.contract_no || action.meta?.contract_no || action.label || '--'
}
function actionMeta(action) {
  const meta = action.meta || action || {}
  const room = meta.room_no ? ` · ${meta.room_no}` : ''
  if (action.type === 'overdue_contract') return `${meta.contract_no || '--'}${room} · 逾期 ${meta.max_aging_days || 0} 天`
  if (action.type === 'expiring_contract') return `${meta.contract_no || '--'}${room}`
  return meta.contract_no || action.label || ''
}
function actionAmount(action) {
  const amount = Number(action.amount || action.overdue_balance || action.unpaid_amount || 0)
  return amount > 0 ? formatCurrency(amount) : ''
}
function actionTone(action) {
  if (action.severity === 'danger') return 'red'
  if (action.severity === 'warning') return 'amber'
  return 'cyan'
}

// 近 6 个月收缴趋势（应收/已收柱状 + 收缴率折线）
const trendRows = ref([])
const trendChartEl = ref(null)
let trendChart = null
let echartsRuntime = null

function applySnapshot(snapshot) {
  const presentation = snapshot?.presentation || {}
  const price = presentation.price || {}
  towerRows.value = Array.isArray(presentation.tower)
    ? presentation.tower.map((row) => ({
        ...row,
        total: optionalNumber(row?.total),
        leased: optionalNumber(row?.leased),
        rate: optionalRate(row?.rate)
      }))
    : []
  segmentPrices.value = price.current_by_segment || {}
  priceSegmentDefinitions.value = Array.isArray(price.segments) ? price.segments : []
  priceTrend.value = {
    months: Array.isArray(price.months) ? price.months : [],
    bySegment: price.trend_by_segment || {}
  }
  trendRows.value = Array.isArray(presentation.trend) ? presentation.trend.slice(-6) : []
}

function trendOption(rows) {
  const months = rows.map((row) => row.month || '')
  const receivable = rows.map((row) => optionalNumber(row.receivable ?? row.total_rent))
  const received = rows.map((row) => optionalNumber(row.received ?? row.total_received))
  const rates = rows.map((row) => optionalRate(row.received_rate))
  return {
    backgroundColor: 'transparent',
    animationDuration: 800,
    grid: { left: 84, right: 60, top: 46, bottom: 34 },
    legend: { top: 6, textStyle: { color: '#8ea2c9', fontSize: 15 }, itemWidth: 18, itemHeight: 10 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101c36',
      borderColor: 'rgba(90, 140, 255, 0.25)',
      textStyle: { color: '#e8eefc', fontSize: 14 },
      valueFormatter: (value) => Number(value).toLocaleString('zh-CN')
    },
    xAxis: {
      type: 'category',
      data: months,
      axisLine: { lineStyle: { color: 'rgba(142, 162, 201, 0.35)' } },
      axisTick: { show: false },
      axisLabel: { color: '#8ea2c9', fontSize: 15 }
    },
    yAxis: [
      {
        type: 'value',
        splitLine: { lineStyle: { color: 'rgba(142, 162, 201, 0.12)' } },
        axisLabel: {
          color: '#8ea2c9',
          fontSize: 14,
          formatter: (value) => `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}万`
        }
      },
      {
        type: 'value',
        max: 100,
        splitLine: { show: false },
        axisLabel: { color: '#8ea2c9', fontSize: 14, formatter: '{value}%' }
      }
    ],
    series: [
      {
        name: '应收',
        type: 'bar',
        data: receivable,
        barMaxWidth: 30,
        itemStyle: { color: 'rgba(56, 189, 248, 0.7)', borderRadius: [7, 7, 0, 0] }
      },
      {
        name: '已收',
        type: 'bar',
        data: received,
        barMaxWidth: 30,
        itemStyle: { color: 'rgba(52, 211, 153, 0.85)', borderRadius: [7, 7, 0, 0] }
      },
      {
        name: '收缴率',
        type: 'line',
        yAxisIndex: 1,
        data: rates,
        smooth: true,
        symbolSize: 8,
        lineStyle: { width: 3, color: '#fbbf24' },
        itemStyle: { color: '#fbbf24' }
      }
    ]
  }
}

async function renderTrendChart() {
  if (!trendChartEl.value) return
  if (!echartsRuntime) echartsRuntime = await loadChartRuntime()
  if (!trendChart) trendChart = echartsRuntime.init(trendChartEl.value)
  trendChart.setOption(trendOption(trendRows.value))
}

// 面积构成环图（左列）：已租 vs 待租，字段取自 vacancy.total_area / vacancy.area
const areaChartEl = ref(null)
let areaChart = null

const areaBreakdown = computed(() => {
  const total = optionalNumber(vacancy.value.total_area)
  const vacantArea = optionalNumber(vacancy.value.area)
  if (total === null || vacantArea === null) {
    return { available: false, leased: null, vacant: null }
  }
  return { available: true, leased: Math.max(total - vacantArea, 0), vacant: vacantArea }
})

async function renderAreaChart() {
  if (!areaChartEl.value) return
  if (!echartsRuntime) echartsRuntime = await loadChartRuntime()
  if (!areaChart) areaChart = echartsRuntime.init(areaChartEl.value)
  const { available, leased, vacant } = areaBreakdown.value
  areaChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: '#101c36',
      borderColor: 'rgba(90, 140, 255, 0.25)',
      textStyle: { color: '#e8eefc', fontSize: 13 },
      valueFormatter: (value) => `${Number(value).toLocaleString('zh-CN')} ㎡`
    },
    legend: { bottom: 0, textStyle: { color: '#8ea2c9', fontSize: 12 }, itemWidth: 12, itemHeight: 8 },
    series: [{
      type: 'pie',
      radius: ['60%', '84%'],
      center: ['50%', '42%'],
      label: { show: false },
      itemStyle: { borderColor: '#0b1428', borderWidth: 2 },
      data: available
        ? [
            { name: '已租面积', value: Math.round(leased), itemStyle: { color: 'rgba(52, 211, 153, 0.85)' } },
            { name: '待租面积', value: Math.round(vacant), itemStyle: { color: 'rgba(251, 191, 36, 0.85)' } }
          ]
        : []
    }]
  })
}

// 分区平均单价走势（中列下半，近 6 个月三分区折线）
const priceChartEl = ref(null)
let priceChart = null

async function renderPriceChart() {
  if (!priceChartEl.value) return
  if (!echartsRuntime) echartsRuntime = await loadChartRuntime()
  if (!priceChart) priceChart = echartsRuntime.init(priceChartEl.value)
  priceChart.setOption({
    backgroundColor: 'transparent',
    animationDuration: 800,
    grid: { left: 46, right: 18, top: 38, bottom: 28 },
    legend: {
      top: 4,
      textStyle: { color: '#8ea2c9', fontSize: 13 },
      itemWidth: 16,
      itemHeight: 9
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101c36',
      borderColor: 'rgba(90, 140, 255, 0.25)',
      textStyle: { color: '#e8eefc', fontSize: 13 },
      valueFormatter: (value) => (value == null ? '--' : `${Number(value).toFixed(2)} 元/㎡·天`)
    },
    xAxis: {
      type: 'category',
      data: priceTrend.value.months,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: 'rgba(142, 162, 201, 0.35)' } },
      axisLabel: { color: '#8ea2c9', fontSize: 13, formatter: (value) => `${String(value).slice(5)}月` }
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: 'rgba(142, 162, 201, 0.12)' } },
      axisLabel: { color: '#8ea2c9', fontSize: 12 }
    },
    series: trendPriceSegments.value.map((segment) => ({
      name: segment.name,
      type: 'line',
      data: priceTrend.value.bySegment[segment.key] || [],
      smooth: true,
      connectNulls: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2.5, color: segment.color },
      itemStyle: { color: segment.color }
    }))
  })
}

function onResize() {
  if (trendChart) trendChart.resize()
  if (areaChart) areaChart.resize()
  if (priceChart) priceChart.resize()
}

function disposeCharts() {
  if (trendChart) {
    trendChart.dispose()
    trendChart = null
  }
  if (areaChart) {
    areaChart.dispose()
    areaChart = null
  }
  if (priceChart) {
    priceChart.dispose()
    priceChart = null
  }
}

watch(trendRows, () => { renderTrendChart() })
watch(areaBreakdown, () => { renderAreaChart() })
watch(priceTrend, () => { renderPriceChart() })
watch(() => props.snapshot, applySnapshot, { deep: true, immediate: true })
watch(isPortfolio, async (portfolioMode) => {
  if (portfolioMode) {
    disposeCharts()
    return
  }
  await nextTick()
  renderTrendChart()
  renderAreaChart()
  renderPriceChart()
})
watch(detailListsAvailable, async (available) => {
  if (!available) {
    disposeCharts()
    return
  }
  await nextTick()
  renderTrendChart()
  renderAreaChart()
  renderPriceChart()
})

onMounted(async () => {
  updateClock()
  clockTimer = window.setInterval(updateClock, 1000)
  window.addEventListener('resize', onResize)
  renderTrendChart()
  renderAreaChart()
  renderPriceChart()
})

onBeforeUnmount(() => {
  window.clearInterval(clockTimer)
  window.removeEventListener('resize', onResize)
  disposeCharts()
})
</script>

<template>
  <PortfolioPresentationView
    v-if="isPortfolio"
    :snapshot="snapshot"
    :overview-only="overviewOnly"
    :embedded="embedded"
    :available-tabs="availableTabs"
    @select-building="emit('select-building', $event)"
    @navigate="emit('navigate', $event)"
  />

  <div
    v-else
    class="cockpit"
    :class="{ 'is-embedded': embedded }"
    data-testid="presentation-cockpit"
    data-scope-type="building"
  >
    <header class="cockpit-header">
      <div class="cockpit-brand">
        <button
          v-if="overviewOnly"
          type="button"
          class="cockpit-back-overview"
          data-testid="leadership-overview-back"
          @click="emit('back-overview')"
        >返回板块</button>
        <strong>{{ scopeTitle }} 租赁经营总览</strong>
        <span>{{ clock }}</span>
      </div>
      <div class="cockpit-signals">
        <span
          v-if="isSimulatedData"
          class="cockpit-simulation-badge"
          data-testid="presentation-simulated-data"
        >模拟数据</span>
        <span
          v-for="light in signalLights"
          :key="light.key"
          class="cockpit-signal"
          :class="[light.key, { active: light.active }]"
        >
          <i></i>{{ light.label }}<b v-if="light.count && light.key !== 'green'">{{ light.count }}</b>
        </span>
      </div>
    </header>

    <PresentationKpiStrip
      :cards="kpiCards"
      :interactive="!overviewOnly"
      @navigate="emit('navigate', $event)"
    />

    <section
      v-if="!detailListsAvailable"
      class="cockpit-snapshot-main"
      data-testid="presentation-snapshot-summary"
    >
      <article class="cockpit-panel cockpit-snapshot-facts">
        <div class="cockpit-snapshot-heading">
          <div>
            <span>{{ snapshotDataModeLabel }}</span>
            <h3>{{ snapshotScopeTitle }}</h3>
          </div>
          <b>统计日 {{ snapshot.as_of_date || '--' }}</b>
        </div>
        <div class="cockpit-snapshot-grid">
          <div
            v-for="fact in snapshotFacts"
            :key="fact.code"
            class="cockpit-snapshot-fact"
            :class="{ unavailable: !fact.available }"
          >
            <span>{{ fact.label }}</span>
            <strong>{{ fact.value }}</strong>
            <small>{{ fact.available ? snapshotFactScopeLabel : fact.reason }}</small>
          </div>
        </div>
      </article>

      <aside class="cockpit-panel cockpit-snapshot-status">
        <div>
          <span>运行方式</span>
          <h3>{{ operationsProfileView.positioning || '整楼快照模式' }}</h3>
        </div>
        <div
          v-if="operationsProfileView.configured"
          class="cockpit-profile-structure"
          data-testid="presentation-building-profile"
          :data-structure="operationsProfileView.structureType"
        >
          <span>{{ operationsProfileView.structureLabel }} · {{ operationsProfileView.granularityLabel }}</span>
          <p>{{ operationsProfileView.structureDetail }}</p>
        </div>
        <div v-if="operationsProfileView.focus.length" class="cockpit-profile-focus">
          <b v-for="item in operationsProfileView.focus" :key="item">{{ item }}</b>
        </div>
        <div v-if="operationsProfileView.groups.length" class="cockpit-profile-groups">
          <article
            v-for="group in operationsProfileView.groups"
            :key="group.code"
            :class="{ unavailable: !group.available }"
            :data-testid="`presentation-profile-group-${group.code}`"
          >
            <div>
              <span>{{ group.name }}</span>
              <strong>{{ group.statusLabel }}</strong>
            </div>
            <small>{{ group.zoneLabel }} · {{ group.note }}</small>
          </article>
        </div>
        <p
          v-if="operationsProfileView.unavailableGroupSafety"
          class="cockpit-profile-safety"
          data-testid="presentation-profile-safety"
        >{{ operationsProfileView.unavailableGroupSafety }}</p>
        <p v-else>
          {{ isLiveDetailProjection
            ? '当前只展示明细适配器中可核验的整楼指标；未知值保持“--”。'
            : '当前只展示已经发布、可追溯的整楼指标，不展示合同、客户、房源、待办或分区明细。' }}
        </p>
        <div class="cockpit-snapshot-policy">
          <span>未知指标</span>
          <strong>{{ snapshotUnavailableCount }} 项</strong>
          <small>未知保持“--”并说明原因，不按 0 处理。</small>
        </div>
      </aside>
    </section>

    <section v-else class="cockpit-main">
      <aside class="cockpit-panel cockpit-rooms">
        <h3>房态概览</h3>
        <div class="cockpit-occupancy">
          <AnimatedNumber :value="roomSummary.occupancyRate === null ? '--' : `${roomSummary.occupancyRate.toFixed(1)}%`" />
          <span>整体出租率</span>
        </div>
        <div ref="areaChartEl" class="cockpit-areachart"></div>
        <div class="cockpit-room-facts">
          <div>
            <b>{{ roomSummary.vacantCount === null ? '--' : roomSummary.vacantCount }}</b>
            <span>待租房源</span>
          </div>
          <div>
            <b>{{ roomSummary.vacantArea === null ? '--' : roomSummary.vacantArea.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) }}</b>
            <span>待租面积 ㎡</span>
          </div>
        </div>
      </aside>

      <div class="cockpit-midcol">
        <div class="cockpit-panel cockpit-trend">
          <h3>近 6 个月收缴趋势</h3>
          <div ref="trendChartEl" class="cockpit-chart"></div>
        </div>
        <div class="cockpit-panel cockpit-prices">
          <h3>分区平均单价走势</h3>
          <div ref="priceChartEl" class="cockpit-chart"></div>
        </div>
      </div>

      <aside class="cockpit-panel cockpit-actions">
        <h3>{{ overviewOnly ? '变化说明' : '今日急项' }}</h3>
        <div v-if="!topActions.length" class="cockpit-empty">
          {{ overviewOnly ? '当前没有可展示的概况变化说明' : '今日暂无待办事项' }}
        </div>
        <div v-for="(action, index) in topActions" :key="index" class="cockpit-action" :class="actionTone(action)">
          <span class="cockpit-action-rank">{{ index + 1 }}</span>
          <span class="cockpit-action-body">
            <strong>{{ actionTitle(action) }}</strong>
            <small>{{ actionMeta(action) }}</small>
          </span>
          <b class="cockpit-action-amount">{{ actionAmount(action) }}</b>
        </div>
        <div v-if="!overviewOnly" class="cockpit-datahealth">
          <div v-for="item in dataHealth" :key="item.key">
            <span>{{ item.label }}</span>
            <b :class="{ warn: item.value > 0 }">{{ item.value === null ? '--' : `${item.value} ${item.unit}` }}</b>
          </div>
        </div>
      </aside>
    </section>

    <section v-if="detailListsAvailable" class="cockpit-panel cockpit-towers">
      <h3>分区经营<small v-if="avgUnitPrice > 0">全场平均单价 {{ avgUnitPrice.toFixed(2) }} 元/㎡·天</small></h3>
      <div class="cockpit-tower-rows">
        <div v-for="row in towerRows" :key="row.name" class="cockpit-tower-row">
          <span class="cockpit-tower-name">{{ row.name }}</span>
          <span class="cockpit-tower-track"><i :style="{ width: `${row.rate ?? 0}%` }"></i></span>
          <b>{{ row.rate === null ? '--' : `${row.rate.toFixed(1)}%` }}</b>
          <span class="cockpit-tower-area">
            {{ row.leased === null ? '--' : Math.round(row.leased).toLocaleString('zh-CN') }}
            /
            {{ row.total === null ? '--' : Math.round(row.total).toLocaleString('zh-CN') }} ㎡
          </span>
          <span class="cockpit-tower-price">{{ towerPrice(row.name) }}</span>
        </div>
      </div>
      <div class="cockpit-risk-box">
        <span>风险构成</span>
        <div class="cockpit-riskbars">
          <div v-for="bar in riskBars" :key="bar.key" class="cockpit-riskbar">
            <span class="cockpit-riskbar-label">{{ bar.label }}</span>
            <span class="cockpit-riskbar-track"><i :style="{ width: `${bar.width}%`, background: bar.color }"></i></span>
            <b>{{ formatWan(bar.value) }}</b>
          </div>
        </div>
      </div>
    </section>

    <footer class="cockpit-footer">
      <span v-if="coverageLabel" class="cockpit-coverage">{{ coverageLabel }}</span>
      <span
        v-for="item in contributionRows"
        :key="item.building?.code || item.snapshot_id"
        class="cockpit-contribution"
        :class="{
          missing: item.publication_status === 'missing',
          excluded: contributionExcluded(item)
        }"
      >
        {{ item.building?.name || item.building?.code || '未命名楼宇' }} ·
        {{ contributionStatus(item) }}
      </span>
      <span v-if="!embedded" class="cockpit-exit">ESC 退出演示</span>
    </footer>
  </div>
</template>

<style scoped>
.cockpit {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 26px 34px 14px;
  background:
    radial-gradient(1200px 600px at 85% -10%, rgba(56, 120, 255, 0.14), transparent 60%),
    linear-gradient(160deg, #060b18 0%, #0b1428 55%, #0a1020 100%);
  color: #e8eefc;
  /* 超窄窗口（低于设计下限 ~1200px）允许滚动查看，避免静默裁切 */
  overflow: auto;
}

.cockpit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 0 0 auto;
}

.cockpit-brand {
  display: flex;
  align-items: baseline;
  gap: 16px;
}

.cockpit-brand strong {
  font-size: 26px;
  letter-spacing: 5px;
}

.cockpit-brand span {
  font-size: 15px;
  color: #8ea2c9;
  font-variant-numeric: tabular-nums;
}

.cockpit.is-embedded {
  position: relative;
  inset: auto;
  z-index: auto;
  min-height: calc(100vh - 92px);
}

.cockpit-back-overview {
  flex: 0 0 auto;
  border: 1px solid rgba(125, 211, 252, 0.35);
  border-radius: 999px;
  padding: 5px 10px;
  background: rgba(14, 116, 144, 0.16);
  color: #bae6fd;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.cockpit-back-overview:hover,
.cockpit-back-overview:focus-visible {
  border-color: rgba(125, 211, 252, 0.72);
  outline: none;
}

.cockpit-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  color: #8ea2c9;
}

.cockpit-coverage,
.cockpit-contribution {
  padding: 3px 9px;
  border: 1px solid rgba(52, 211, 153, 0.28);
  border-radius: 999px;
  color: #6ee7b7;
  font-size: 12px;
  white-space: nowrap;
}

.cockpit-contribution.missing {
  border-color: rgba(251, 191, 36, 0.28);
  color: #fcd34d;
}

.cockpit-contribution.excluded {
  border-color: rgba(251, 191, 36, 0.34);
  color: #fcd34d;
}

.cockpit-exit {
  margin-left: auto;
  white-space: nowrap;
}

.cockpit-signals {
  display: flex;
  align-items: center;
  gap: 18px;
}

.cockpit-simulation-badge {
  padding: 4px 9px;
  border: 1px solid rgba(251, 191, 36, 0.42);
  border-radius: 4px;
  background: rgba(120, 72, 8, 0.18);
  color: #fcd34d;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.5px;
}

.cockpit-signal {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  color: #5d6f92;
}

.cockpit-signal i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #33415e;
}

.cockpit-signal b {
  font-variant-numeric: tabular-nums;
}

.cockpit-signal.red.active { color: #fca5a5; }
.cockpit-signal.red.active i { background: #f87171; box-shadow: 0 0 10px rgba(248, 113, 113, 0.8); }
.cockpit-signal.amber.active { color: #fcd34d; }
.cockpit-signal.amber.active i { background: #fbbf24; box-shadow: 0 0 10px rgba(251, 191, 36, 0.8); }
.cockpit-signal.green.active { color: #6ee7b7; }
.cockpit-signal.green.active i { background: #34d399; box-shadow: 0 0 10px rgba(52, 211, 153, 0.8); }

.cockpit-kpis {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 16px;
  flex: 0 0 auto;
}

.cockpit-kpi {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 22px;
  border-radius: 14px;
  background: rgba(17, 29, 51, 0.72);
  border: 1px solid rgba(90, 140, 255, 0.16);
  border-top: 3px solid transparent;
}

.cockpit-kpi-label {
  font-size: 15px;
  color: #8ea2c9;
  letter-spacing: 1px;
}

.cockpit-kpi :deep(.animated-number) {
  /* clamp 弹性字号：宽屏 36px 封顶，窄投影（720p）自动收缩防溢出 */
  font-size: clamp(21px, 1.9vw, 36px);
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.cockpit-kpi.cyan { border-top-color: #38bdf8; }
.cockpit-kpi.cyan :deep(.animated-number) { color: #7dd3fc; }
.cockpit-kpi.green { border-top-color: #34d399; }
.cockpit-kpi.green :deep(.animated-number) { color: #6ee7b7; }
.cockpit-kpi.blue { border-top-color: #60a5fa; }
.cockpit-kpi.blue :deep(.animated-number) { color: #93c5fd; }
.cockpit-kpi.red { border-top-color: #f87171; }
.cockpit-kpi.red :deep(.animated-number) { color: #fca5a5; }
.cockpit-kpi.amber { border-top-color: #fbbf24; }
.cockpit-kpi.amber :deep(.animated-number) { color: #fcd34d; }

.cockpit-main {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 380px;
  gap: 18px;
  flex: 1 1 auto;
  min-height: 0;
}

.cockpit-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px 22px;
  border-radius: 14px;
  background: rgba(17, 29, 51, 0.72);
  border: 1px solid rgba(90, 140, 255, 0.16);
  min-height: 0;
  overflow: hidden;
}

.cockpit-snapshot-main {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(300px, 0.8fr);
  gap: 18px;
  flex: 1 1 auto;
  min-height: 0;
}

.cockpit-snapshot-facts {
  justify-content: flex-start;
}

.cockpit-snapshot-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.cockpit-snapshot-heading span,
.cockpit-snapshot-status > div > span {
  display: block;
  margin-bottom: 6px;
  color: #5d6f92;
  font-size: 12px;
  letter-spacing: 1.5px;
}

.cockpit-snapshot-heading b {
  color: #93c5fd;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.cockpit-snapshot-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  flex: 1 1 auto;
}

.cockpit-snapshot-fact {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  min-width: 0;
  padding: 18px;
  border: 1px solid rgba(90, 140, 255, 0.16);
  border-radius: 12px;
  background: rgba(10, 20, 40, 0.58);
}

.cockpit-snapshot-fact span {
  color: #8ea2c9;
  font-size: 13px;
}

.cockpit-snapshot-fact strong {
  color: #7dd3fc;
  font-size: clamp(22px, 2vw, 34px);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.cockpit-snapshot-fact small {
  min-height: 2.8em;
  color: #5d6f92;
  font-size: 12px;
  line-height: 1.4;
}

.cockpit-snapshot-fact.unavailable strong,
.cockpit-snapshot-fact.unavailable small {
  color: #fcd34d;
}

.cockpit-snapshot-status {
  justify-content: flex-start;
}

.cockpit-snapshot-status p {
  margin: 0;
  color: #8ea2c9;
  font-size: 14px;
  line-height: 1.7;
}

.cockpit-profile-structure {
  display: grid;
  gap: 5px;
  border-left: 3px solid #38bdf8;
  background: rgba(14, 116, 144, 0.1);
  padding: 9px 11px;
}

.cockpit-profile-structure > span {
  color: #7dd3fc;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.04em;
}

.cockpit-profile-structure > p {
  color: #8ea2c9;
  font-size: 12px;
  line-height: 1.45;
}

.cockpit-profile-focus {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.cockpit-profile-focus b {
  border: 1px solid rgba(125, 211, 252, 0.2);
  border-radius: 999px;
  background: rgba(14, 116, 144, 0.1);
  color: #bae6fd;
  padding: 3px 8px;
  font-size: 10px;
  line-height: 1.35;
}

.cockpit-profile-groups {
  display: grid;
  gap: 7px;
}

.cockpit-profile-groups article {
  display: grid;
  gap: 4px;
  border: 1px solid rgba(52, 211, 153, 0.2);
  border-left: 3px solid #34d399;
  border-radius: 8px;
  background: rgba(16, 185, 129, 0.07);
  padding: 8px 10px;
}

.cockpit-profile-groups article.unavailable {
  border-color: rgba(251, 191, 36, 0.22);
  border-left-color: #fbbf24;
  background: rgba(120, 72, 8, 0.1);
}

.cockpit-profile-groups article > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.cockpit-profile-groups span {
  color: #d9e6fb;
  font-size: 12px;
  font-weight: 750;
}

.cockpit-profile-groups strong {
  color: #6ee7b7;
  font-size: 10px;
  white-space: nowrap;
}

.cockpit-profile-groups article.unavailable strong {
  color: #fcd34d;
  font-size: 17px;
}

.cockpit-profile-groups small {
  color: #8ea2c9;
  font-size: 10px;
  line-height: 1.4;
}

.cockpit-snapshot-status .cockpit-profile-safety {
  border-left: 3px solid #fbbf24;
  background: rgba(120, 72, 8, 0.12);
  color: #fcd34d;
  padding: 8px 10px;
  font-size: 11px;
  font-weight: 650;
  line-height: 1.45;
}

.cockpit-snapshot-policy {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border: 1px solid rgba(251, 191, 36, 0.24);
  border-radius: 12px;
  background: rgba(120, 72, 8, 0.12);
}

.cockpit-snapshot-policy span,
.cockpit-snapshot-policy small {
  color: #c4a969;
  font-size: 12px;
  line-height: 1.5;
}

.cockpit-snapshot-policy strong {
  color: #fcd34d;
  font-size: 28px;
}

.cockpit-panel.cockpit-rooms {
  gap: 10px;
  padding: 14px 18px;
}

.cockpit-panel h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #8ea2c9;
  letter-spacing: 2px;
}

.cockpit-occupancy {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(90, 140, 255, 0.12);
}

.cockpit-occupancy :deep(.animated-number) {
  font-size: 46px;
  font-weight: 700;
  color: #93c5fd;
  font-variant-numeric: tabular-nums;
}

.cockpit-occupancy span {
  font-size: 14px;
  color: #8ea2c9;
}

.cockpit-areachart {
  flex: 0 0 auto;
  height: 105px;
}

.cockpit-riskbars {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.cockpit-riskbar {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.cockpit-riskbar-label {
  flex: 0 0 62px;
  color: #8ea2c9;
}

.cockpit-riskbar-track {
  flex: 1 1 auto;
  height: 8px;
  border-radius: 4px;
  background: rgba(90, 140, 255, 0.1);
  overflow: hidden;
}

.cockpit-riskbar-track i {
  display: block;
  height: 100%;
  border-radius: 4px;
  transition: width 0.8s ease;
}

.cockpit-riskbar b {
  flex: 0 0 auto;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.cockpit-datahealth {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid rgba(90, 140, 255, 0.12);
}

.cockpit-datahealth div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: #8ea2c9;
}

.cockpit-datahealth b {
  color: #6ee7b7;
  font-variant-numeric: tabular-nums;
}

.cockpit-datahealth b.warn {
  color: #fcd34d;
}

.cockpit-room-facts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 0;
}

.cockpit-room-facts div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.cockpit-room-facts b {
  font-size: 26px;
  font-variant-numeric: tabular-nums;
}

.cockpit-room-facts span {
  font-size: 14px;
  color: #8ea2c9;
}

.cockpit-chart {
  flex: 1 1 auto;
  min-height: 0;
}

.cockpit-midcol {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
  min-height: 0;
}

.cockpit-midcol .cockpit-panel {
  flex: 1 1 0;
}

.cockpit-actions {
  gap: 10px;
}

.cockpit-action {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(10, 20, 40, 0.6);
  border-left: 3px solid #38bdf8;
}

.cockpit-action.red { border-left-color: #f87171; }
.cockpit-action.amber { border-left-color: #fbbf24; }

.cockpit-action-rank {
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 14px;
  font-weight: 700;
  background: rgba(90, 140, 255, 0.16);
  color: #bcd0f5;
}

.cockpit-action.red .cockpit-action-rank { background: rgba(248, 113, 113, 0.2); color: #fca5a5; }
.cockpit-action.amber .cockpit-action-rank { background: rgba(251, 191, 36, 0.18); color: #fcd34d; }

.cockpit-action-body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.cockpit-action-body strong {
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cockpit-action-body small {
  font-size: 12px;
  color: #8ea2c9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cockpit-action-amount {
  flex: 0 0 auto;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
  color: #fcd34d;
}

.cockpit-empty {
  padding: 22px 0;
  text-align: center;
  color: #5d6f92;
  font-size: 14px;
}

.cockpit-towers {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 280px;
  align-items: center;
  gap: 26px;
  padding: 12px 22px;
}

.cockpit-towers h3 {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cockpit-towers h3 small {
  font-size: 12px;
  font-weight: 400;
  color: #5d6f92;
  letter-spacing: 0;
}

.cockpit-tower-rows {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.cockpit-tower-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr) 58px 168px 108px;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}

.cockpit-tower-name {
  color: #bcd0f5;
}

.cockpit-tower-track {
  height: 7px;
  border-radius: 4px;
  background: rgba(90, 140, 255, 0.1);
  overflow: hidden;
}

.cockpit-tower-track i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: linear-gradient(90deg, #38bdf8, #34d399);
  transition: width 0.8s ease;
}

.cockpit-tower-row b {
  text-align: right;
  color: #7dd3fc;
  font-variant-numeric: tabular-nums;
}

.cockpit-tower-area {
  text-align: right;
  color: #8ea2c9;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.cockpit-tower-price {
  text-align: right;
  color: #fcd34d;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.cockpit-risk-box {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
  min-width: 0;
}

.cockpit-risk-box > span {
  font-size: 12px;
  color: #5d6f92;
}

.cockpit-footer {
  flex: 0 0 auto;
  text-align: right;
  font-size: 12px;
  color: #5d6f92;
  opacity: 0.7;
}

@media (max-width: 1400px) {
  .cockpit {
    gap: 14px;
    padding: 20px 24px 10px;
  }

  .cockpit-areachart {
    height: 92px;
  }

  /* 窄投影优先保住分区行核心三件套（名称/出租率/单价），面积明细让位 */
  .cockpit-tower-row {
    grid-template-columns: 84px minmax(0, 1fr) 58px 108px;
  }

  .cockpit-tower-area {
    display: none;
  }

  .cockpit-towers {
    gap: 16px;
  }

  .cockpit-occupancy :deep(.animated-number) {
    font-size: 44px;
  }

  .cockpit-main {
    grid-template-columns: 240px minmax(0, 1fr) 320px;
  }

  .cockpit-snapshot-main {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}

@media (max-width: 720px) {
  .cockpit.is-embedded {
    gap: 12px;
    padding: 14px 16px 18px;
  }

  .cockpit.is-embedded .cockpit-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
  }

  .cockpit.is-embedded .cockpit-brand {
    width: 100%;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 8px 10px;
  }

  .cockpit.is-embedded .cockpit-brand strong {
    grid-column: 1 / -1;
    font-size: 19px;
    letter-spacing: 1.5px;
    line-height: 1.35;
  }

  .cockpit.is-embedded .cockpit-brand span {
    justify-self: end;
    font-size: 12px;
  }

  .cockpit.is-embedded .cockpit-signals,
  .cockpit.is-embedded .cockpit-footer {
    flex-wrap: wrap;
    gap: 8px;
  }

  .cockpit.is-embedded .cockpit-main {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .cockpit.is-embedded .cockpit-snapshot-main {
    grid-template-columns: minmax(0, 1fr);
  }

  .cockpit.is-embedded .cockpit-snapshot-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .cockpit.is-embedded .cockpit-panel {
    min-height: 220px;
  }

  .cockpit.is-embedded .cockpit-actions {
    min-height: 140px;
  }

  .cockpit.is-embedded .cockpit-towers {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .cockpit.is-embedded .cockpit-tower-row {
    grid-template-columns: 74px minmax(0, 1fr) 52px 90px;
    gap: 8px;
  }

  .cockpit.is-embedded .cockpit-footer {
    align-items: flex-start;
    line-height: 1.4;
  }

  .cockpit.is-embedded .cockpit-coverage,
  .cockpit.is-embedded .cockpit-contribution {
    white-space: normal;
  }
}
</style>
