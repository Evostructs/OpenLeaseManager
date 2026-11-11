<script setup>
import { t } from '../i18n/index.js'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChartCard from '../components/ChartCard.vue'
import EChartPanel from '../components/EChartPanel.vue'
import RentTimelineAnalytics from '../components/RentTimelineAnalytics.vue'
import { cachedApi } from '../lib/prefetchCache'
import { api } from '../lib/api'
import { cockpitBuildingPath } from '../lib/cockpitDomain.js'
import { buildingAnalyticsSnapshot, hasAnalyticsRoomAreas } from '../lib/cockpitData.js'
import { formatArea, formatCurrency, formatNumber } from '../lib/format'
import { profileAnalysisSegments, roomAnalysisSegment, usesLegacyDemoAFloorPlan } from '../lib/roomsDomain'

const props = defineProps({
  buildingCode: { type: String, default: '' },
  snapshot: { type: Object, default: null },
  loadRooms: { type: Function, default: null },
  showRentTimeline: { type: Boolean, default: true },
  stats: {
    type: Object,
    default: () => ({})
  },
  user: {
    type: Object,
    default: () => ({ role: 'viewer' })
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
  }
})
const emit = defineEmits(['select-tab', 'drilldown', 'notify', 'open-contract'])

const statusOrder = ['在租', '即将到期', '待生效', '已变更', '已退租', '逾期占用']
const statusPalette = {
  在租: { color: '#3b82f6', bg: '#dbeafe' },
  即将到期: { color: '#d97706', bg: '#fef3c7' },
  待生效: { color: '#10b981', bg: '#d1fae5' },
  已变更: { color: '#047857', bg: '#d1fae5' },
  已退租: { color: '#64748b', bg: '#e2e8f0' },
  逾期占用: { color: '#dc2626', bg: '#fee2e2' }
}

const towerRoomArea = ref({})
const operationsSnapshot = ref({})
const operationsTrendRows = ref([])
const analyticsLoadError = ref('')
let loadSequence = 0

function roundTo(value, digits = 0) {
  const factor = 10 ** digits
  return Math.round(Number(value || 0) * factor) / factor
}

function numberValue(value) {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

function shortValue(value, unit = '') {
  const numeric = Number(value || 0)
  if (Math.abs(numeric) >= 10000) return `${roundTo(numeric / 10000, 1)}万${unit}`
  return `${formatNumber(roundTo(numeric, 0))}${unit}`
}

function compactMoney(value) {
  const numeric = numberValue(value)
  const absolute = Math.abs(numeric)
  if (absolute >= 100000000) return `${String(roundTo(numeric / 100000000, 1)).replace(/\.0$/, '')}亿`
  if (absolute >= 10000) return `${String(roundTo(numeric / 10000, 1)).replace(/\.0$/, '')}万`
  return formatCurrency(numeric)
}

function formatSignedPercent(value) {
  const number = roundTo(numberValue(value), 1)
  return `${number > 0 ? '+' : ''}${number}%`
}

function formatSignedPoints(value) {
  const number = roundTo(numberValue(value), 1)
  return `${number > 0 ? '+' : ''}${number}个百分点`
}

function formatSignedShortPoints(value) {
  const number = roundTo(numberValue(value), 1)
  return `${number > 0 ? '+' : ''}${number}点`
}

function pressureRank(tone) {
  return {
    danger: 4,
    error: 4,
    warning: 3,
    info: 2,
    success: 1
  }[tone] || 2
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, roundTo(numberValue(value), 1)))
}

function normalizeText(value) {
  return String(value ?? '').trim()
}

const analysisSegments = computed(() => profileAnalysisSegments(props.operationsProfile))
const towerOrder = computed(() => analysisSegments.value.map((item) => item.label))
const analysisBreakdownTitle = computed(() => (
  analysisSegments.value.length === 1 && analysisSegments.value[0].code === 'WHOLE_BUILDING'
    ? '整楼表现 / 面积去化'
    : '分区表现 / 面积去化'
))

function normalizeTowerGroup(value) {
  const tower = normalizeText(value)
  const matched = analysisSegments.value.find((item) => item.aliases.includes(tower))
  if (matched) return matched.label
  if (analysisSegments.value.length === 1 && analysisSegments.value[0].code === 'WHOLE_BUILDING') {
    return analysisSegments.value[0].label
  }
  return towerOrder.value.includes(tower) ? tower : null
}

function getLeasingGroup(room) {
  return roomAnalysisSegment(room, props.operationsProfile)?.label || null
}

function normalizeGroupList(groups) {
  const normalized = []
  for (const group of groups || []) {
    const name = normalizeTowerGroup(group)
    if (name && !normalized.includes(name)) normalized.push(name)
  }
  return normalized
}

const metrics = computed(() => props.stats.metrics || {})
const operations = computed(() => operationsSnapshot.value || {})
const operationsMetrics = computed(() => operations.value.metrics || {})
const receivableStats = computed(() => props.stats.receivables || {})

const statusRows = computed(() => {
  const entries = Object.entries(props.stats.statuses || {})
  const total = entries.reduce((sum, [, count]) => sum + Number(count || 0), 0)
  return entries
    .map(([label, count]) => ({
      label,
      count: Number(count || 0),
      percent: total ? Math.round((Number(count || 0) / total) * 100) : 0,
      ...(statusPalette[label] || { color: '#94a3b8', bg: '#e5e7eb' }),
      order: statusOrder.indexOf(label) === -1 ? 999 : statusOrder.indexOf(label)
    }))
    .sort((a, b) => a.order - b.order || b.count - a.count)
})

const statusTotal = computed(() => statusRows.value.reduce((sum, item) => sum + Number(item.count || 0), 0))
const statusInsight = computed(() => {
  const active = statusRows.value.find((item) => item.label === '在租')
  const expiring = statusRows.value.find((item) => item.label === '即将到期')
  return `在租 ${formatNumber(active?.count || 0)} 份，占比 ${active?.percent || 0}%；90天到期 ${formatNumber(expiring?.count || 0)} 份。`
})

const statusOption = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: ({ name, value, percent }) => `${name}<br/>数量：${formatNumber(value)}<br/>占比：${percent}%`
  },
  series: [
    {
      type: 'pie',
      radius: ['56%', '76%'],
      center: ['42%', '54%'],
      avoidLabelOverlap: true,
      label: { show: false },
      itemStyle: { borderColor: '#ffffff', borderWidth: 4 },
      data: statusRows.value.map((item) => ({
        name: item.label,
        value: item.count,
        itemStyle: { color: item.color }
      }))
    }
  ]
}))

const normalizedTowerData = computed(() => {
  const summary = {}
  for (const [rawTower, item] of Object.entries(props.stats.tower_data || {})) {
    const tower = normalizeTowerGroup(rawTower)
    if (!tower) continue
    const entry = summary[tower] || { count: 0, area: 0, rent: 0, room_area: 0 }
    entry.count += Number(item.count || 0)
    entry.area += Number(item.area || 0)
    entry.rent += Number(item.rent || 0)
    entry.room_area += Number(item.room_area || 0)
    summary[tower] = entry
  }
  return summary
})

const towerRows = computed(() => {
  const towerData = normalizedTowerData.value
  const totalRent = towerOrder.value.reduce((sum, tower) => sum + Number(towerData[tower]?.rent || 0), 0)
  return towerOrder.value.map((tower) => {
    const item = towerData[tower] || {}
    const area = Number(item.area || 0)
    const rent = Number(item.rent || 0)
    const roomArea = Number(item.room_area || 0) || Number(towerRoomArea.value[tower] || 0)
    const occupancyRate = roomArea > 0 ? roundTo((area / roomArea) * 100, 1) : 0
    return {
      tower,
      count: Number(item.count || 0),
      area,
      roomArea,
      vacantArea: Math.max(roomArea - area, 0),
      rent,
      occupancyRate,
      revenueShare: totalRent > 0 ? roundTo((rent / totalRent) * 100, 1) : 0,
      avgContractArea: item.count ? roundTo(area / item.count, 0) : 0
    }
  })
})

const customerRows = computed(() => {
  const rows = props.stats.customer_data || []
  const totalArea = Number(metrics.value.total_area || 0)
  return rows
    .map((item) => {
      const area = Number(item.area || 0)
      const rent = Number(item.rent || 0)
      const groups = normalizeGroupList(item.groups)
      return {
        ...item,
        area,
        rent,
        areaShare: totalArea > 0 ? roundTo((area / totalArea) * 100, 1) : 0,
        groupsText: groups.join('、') || '--',
        roomsText: (item.rooms || []).slice(0, 4).join('、') || '--',
        hasMoreRooms: Number(item.room_count || 0) > 4
      }
    })
    .sort((a, b) => b.area - a.area || b.rent - a.rent || a.tenant_name.localeCompare(b.tenant_name, 'zh-Hans-CN'))
})

const topCustomerRows = computed(() => customerRows.value.slice(0, 6))

const customerOverview = computed(() => {
  const topArea = customerRows.value[0]
  const topRent = [...customerRows.value].sort((a, b) => b.rent - a.rent)[0]
  return {
    count: customerRows.value.length,
    topAreaName: topArea?.tenant_name || '--',
    topAreaShare: topArea?.areaShare || 0,
    topRentName: topRent?.tenant_name || '--',
    topRent: topRent?.rent || 0
  }
})

const totalRoomArea = computed(() => {
  const metricArea = Number(metrics.value.room_total_area || 0)
  if (metricArea > 0) return metricArea
  return towerRows.value.reduce((sum, item) => sum + Number(item.roomArea || 0), 0)
})

const portfolioSummary = computed(() => {
  const totalArea = Number(metrics.value.total_area || 0)
  const totalRent = Number(metrics.value.total_monthly_rent || 0)
  const roomArea = totalRoomArea.value
  return {
    activeContracts: Number(props.stats.active_count || 0),
    totalArea,
    totalRent,
    roomArea,
    occupancyRate: roomArea > 0 ? roundTo((totalArea / roomArea) * 100, 1) : Number(metrics.value.occupancy_rate || 0)
  }
})

const vacancyFocusTower = computed(() => {
  const candidates = towerRows.value.filter((item) => Number(item.roomArea || 0) > 0)
  if (!candidates.length) return null
  return [...candidates].sort((a, b) => a.occupancyRate - b.occupancyRate || b.vacantArea - a.vacantArea)[0]
})

const towerHighlights = computed(() => {
  const rentLeader = [...towerRows.value].sort((a, b) => b.rent - a.rent)[0] || null
  const occupancyLeader = [...towerRows.value].sort((a, b) => b.occupancyRate - a.occupancyRate)[0] || null
  return {
    rentLeader,
    occupancyLeader,
    vacancyFocus: vacancyFocusTower.value
  }
})

const towerMixInsight = computed(() => {
  const focus = towerHighlights.value.vacancyFocus
  return focus ? `${t(focus.tower)} 当前待租 ${formatArea(focus.vacantArea)} ㎡，出租率 ${focus.occupancyRate}%。` : '暂无集中空置。'
})

const stackedBarEndRadius = [0, 8, 8, 0]
const stackedBarJoinRadius = [0, 0, 0, 0]

function leasedAreaBarData(item) {
  const vacantArea = Number(item.vacantArea || 0)
  return {
    value: item.area,
    itemStyle: {
      borderRadius: vacantArea > 0 ? stackedBarJoinRadius : stackedBarEndRadius
    }
  }
}

function vacantAreaBarData(item) {
  const vacantArea = Number(item.vacantArea || 0)
  return {
    value: item.vacantArea,
    itemStyle: {
      borderRadius: vacantArea > 0 ? stackedBarEndRadius : stackedBarJoinRadius
    }
  }
}

const towerMixOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: {
      type: 'shadow',
      shadowStyle: { color: 'rgba(148, 163, 184, 0.06)' }
    },
    formatter: (params) => {
      const leased = params.find((item) => item.seriesName === '已租面积')
      const vacant = params.find((item) => item.seriesName === '待租面积')
      const rate = params.find((item) => item.seriesName === '出租率')
      return [
        params[0]?.name || '',
        `已租面积：${formatArea(leased?.value || 0)} ㎡`,
        `待租面积：${formatArea(vacant?.value || 0)} ㎡`,
        `出租率：${rate?.value || 0}%`
      ].join('<br/>')
    }
  },
  legend: {
    top: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#6b7280', fontSize: 12 }
  },
  grid: { left: 64, right: 132, top: 44, bottom: 40, containLabel: true },
  xAxis: [
    {
      type: 'value',
      name: '面积（㎡）',
      nameLocation: 'middle',
      nameGap: 22,
      splitLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: {
        color: '#6b7280',
        formatter: (value) => shortValue(value)
      },
      nameTextStyle: { color: '#6b7280' }
    },
    {
      type: 'value',
      name: '出租率（%）',
      nameLocation: 'middle',
      nameGap: 22,
      min: 0,
      max: 100,
      splitLine: { show: false },
      axisLabel: { color: '#6b7280' },
      nameTextStyle: { color: '#6b7280' }
    }
  ],
  yAxis: {
    type: 'category',
    data: towerRows.value.map((item) => item.tower),
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: { color: '#374151' }
  },
  series: [
    {
      name: '已租面积',
      type: 'bar',
      stack: 'area',
      z: 2,
      barWidth: 16,
      itemStyle: { color: '#3b82f6' },
      emphasis: { focus: 'none', itemStyle: { color: '#2563eb' } },
      blur: { itemStyle: { opacity: 1 } },
      data: towerRows.value.map(leasedAreaBarData)
    },
    {
      name: '待租面积',
      type: 'bar',
      stack: 'area',
      z: 2,
      barWidth: 16,
      itemStyle: { color: '#bfdbfe' },
      emphasis: { focus: 'none', itemStyle: { color: '#93c5fd' } },
      blur: { itemStyle: { opacity: 1 } },
      data: towerRows.value.map(vacantAreaBarData)
    },
    {
      name: '出租率',
      type: 'line',
      xAxisIndex: 1,
      z: 6,
      smooth: true,
      connectNulls: true,
      symbol: 'circle',
      symbolSize: 8,
      lineStyle: { width: 3, color: '#0f766e' },
      itemStyle: { color: '#0f766e', borderColor: '#ffffff', borderWidth: 2 },
      emphasis: {
        focus: 'none',
        lineStyle: { width: 4, color: '#0f766e' },
        itemStyle: { color: '#0f766e', borderColor: '#ffffff', borderWidth: 2 }
      },
      blur: { lineStyle: { opacity: 1 }, itemStyle: { opacity: 1 } },
      data: towerRows.value.map((item) => item.occupancyRate)
    }
  ]
}))

const expirySummary = computed(() => {
  const rows = (props.stats.expiry_months || []).map((item) => ({
    month: item.month,
    count: Number(item.count || 0)
  }))
  const total = rows.reduce((sum, item) => sum + item.count, 0)
  const peak = [...rows].sort((a, b) => b.count - a.count)[0] || null
  return { rows, total, peak }
})

const collectionSummary = computed(() => {
  const current = operations.value.current_month || {}
  const statsReceivables = receivableStats.value
  const receivable = numberValue(current.receivable ?? statsReceivables.total_rent)
  const received = numberValue(current.received ?? statsReceivables.total_received)
  const unpaidBalance = numberValue(current.unpaid_balance ?? statsReceivables.total_unpaid_balance)
  const overdueBalance = numberValue(current.overdue_balance ?? statsReceivables.total_overdue_balance)
  const receivedRate = numberValue(current.received_rate ?? statsReceivables.received_rate)
  return {
    month: current.month || statsReceivables.month || '--',
    receivable,
    received,
    unpaidBalance,
    overdueBalance,
    receivedRate,
    unpaidCount: numberValue(current.unpaid_count ?? statsReceivables.unpaid_count),
    overdueCount: numberValue(current.overdue_count ?? statsReceivables.overdue_count)
  }
})

const collectionTrendRows = computed(() => {
  const rows = operationsTrendRows.value.length ? operationsTrendRows.value : (receivableStats.value.trend || [])
  return rows
    .map((item) => ({
      month: item.month,
      receivable: numberValue(item.receivable ?? item.total_rent),
      received: numberValue(item.received ?? item.total_received),
      unpaidBalance: numberValue(item.unpaid_balance ?? item.total_unpaid_balance),
      overdueBalance: numberValue(item.overdue_balance ?? item.total_overdue_balance),
      receivedRate: numberValue(item.received_rate)
    }))
    .filter((item) => item.month)
})

const collectionTrendSummary = computed(() => {
  const rows = collectionTrendRows.value
  if (rows.length < 2) {
    return {
      tone: 'info',
      title: '暂无趋势',
      detail: '样本不足',
      receivedRateDelta: 0,
      sixMonthReceivedRateDelta: 0,
      overdueDelta: 0,
      receivableDelta: 0
    }
  }
  const first = rows[0]
  const previous = rows[rows.length - 2]
  const current = rows[rows.length - 1]
  const receivedRateDelta = roundTo(current.receivedRate - previous.receivedRate, 1)
  const sixMonthReceivedRateDelta = roundTo(current.receivedRate - first.receivedRate, 1)
  const overdueDelta = roundTo(current.overdueBalance - previous.overdueBalance, 2)
  const receivableDelta = previous.receivable
    ? roundTo(((current.receivable - previous.receivable) / previous.receivable) * 100, 1)
    : 0

  if (overdueDelta > 0 && current.overdueBalance > 0) {
    return {
      tone: 'danger',
      title: '本月逾期上行',
      detail: `${previous.month} 至 ${current.month} 本月逾期增加 ${formatCurrency(overdueDelta)}`,
      receivedRateDelta,
      sixMonthReceivedRateDelta,
      overdueDelta,
      receivableDelta
    }
  }
  if (receivedRateDelta < -5) {
    return {
      tone: 'warning',
      title: '回款率走弱',
      detail: `${previous.month} 至 ${current.month} 回款率 ${formatSignedPoints(receivedRateDelta)}`,
      receivedRateDelta,
      sixMonthReceivedRateDelta,
      overdueDelta,
      receivableDelta
    }
  }
  if (receivedRateDelta > 5 || overdueDelta < 0) {
    return {
      tone: 'success',
      title: '回款趋势改善',
      detail: `${previous.month} 至 ${current.month} 回款率 ${formatSignedPoints(receivedRateDelta)}`,
      receivedRateDelta,
      sixMonthReceivedRateDelta,
      overdueDelta,
      receivableDelta
    }
  }
  return {
    tone: 'info',
    title: '回款趋势平稳',
    detail: `${previous.month} 至 ${current.month} 回款率 ${formatSignedPoints(receivedRateDelta)}`,
    receivedRateDelta,
    sixMonthReceivedRateDelta,
    overdueDelta,
    receivableDelta
  }
})

const portfolioTrendSummary = computed(() => {
  const trends = props.stats.metrics_trends || {}
  const rentMom = numberValue(trends.rent?.mom)
  const areaMom = numberValue(trends.area?.mom)
  const occupancyMom = numberValue(trends.occupancy_rate?.mom)
  const tone = rentMom < -3 || occupancyMom < -2
    ? 'warning'
    : (rentMom > 1 || occupancyMom > 1 ? 'success' : 'info')
  const title = rentMom > 1 ? '租金环比上行' : rentMom < -1 ? '租金环比下行' : '租金环比平稳'
  return {
    tone,
    title,
    rentMom,
    areaMom,
    occupancyMom,
    detail: `租金 ${t(formatSignedPercent(rentMom))} · 出租率 ${t(formatSignedPoints(occupancyMom))}`
  }
})

const collectionDecision = computed(() => {
  const summary = collectionSummary.value
  const tone = summary.overdueBalance > 0 || summary.receivedRate < 80
    ? 'danger'
    : (summary.unpaidBalance > 0 || summary.receivedRate < 95 ? 'warning' : 'success')
  return {
    key: 'collection',
    label: '回款状态',
    tone,
    title: summary.overdueBalance > 0
      ? `本月逾期 ${formatCurrency(summary.overdueBalance)}`
      : `回款率 ${roundTo(summary.receivedRate, 1)}%`,
    detail: `本月未收 ${formatCurrency(summary.unpaidBalance)} · 本月逾期 ${formatNumber(summary.overdueCount)} 笔`
  }
})

const vacancyDecision = computed(() => {
  const focus = vacancyFocusTower.value
  const vacancyArea = focus ? numberValue(focus.vacantArea) : numberValue(operationsMetrics.value.vacancy_area)
  const occupancyRate = focus
    ? numberValue(focus.occupancyRate)
    : numberValue(operations.value.vacancy?.occupancy_rate ?? portfolioSummary.value.occupancyRate)
  const tone = occupancyRate < 75 ? 'danger' : (vacancyArea > 0 || occupancyRate < 90 ? 'warning' : 'success')
  return {
    key: 'vacancy',
    label: '招商状态',
    tone,
    title: focus ? `${t(focus.tower)} 待租 ${formatArea(vacancyArea)} ㎡` : `空置 ${formatArea(vacancyArea)} ㎡`,
    detail: `出租率 ${roundTo(occupancyRate, 1)}%`
  }
})

const expiryDecision = computed(() => {
  const count90 = numberValue(operationsMetrics.value.expiring_contract_count_90)
  const total12 = numberValue(expirySummary.value.total)
  const peak = expirySummary.value.peak
  const tone = count90 >= 8 ? 'danger' : (count90 > 0 || total12 > 0 ? 'warning' : 'success')
  return {
    key: 'expiry',
    label: '到期分布',
    tone,
    title: count90 > 0 ? `90天到期 ${formatNumber(count90)} 份` : `12个月到期 ${formatNumber(total12)} 份`,
    detail: peak?.count ? `峰值 ${peak.month}：${formatNumber(peak.count)} 份` : '暂无集中到期'
  }
})

const trendDecision = computed(() => ({
  key: 'trend',
  label: '趋势方向',
  tone: collectionTrendSummary.value.tone === 'danger' ? 'warning' : portfolioTrendSummary.value.tone,
  title: portfolioTrendSummary.value.title,
  detail: `${portfolioTrendSummary.value.detail} · 6月回款 ${formatSignedPoints(collectionTrendSummary.value.sixMonthReceivedRateDelta)}`
}))

const portfolioInsights = computed(() => [
  trendDecision.value,
  collectionDecision.value,
  vacancyDecision.value,
  expiryDecision.value
])

const primaryDecision = computed(() => (
  [...portfolioInsights.value].sort((a, b) => pressureRank(b.tone) - pressureRank(a.tone))[0] || {
    label: '优先判断',
    tone: 'info',
    title: '等待经营数据',
    detail: '暂无可判断信号'
  }
))

const portfolioHealth = computed(() => {
  const priority = primaryDecision.value
  const collectionRate = roundTo(collectionSummary.value.receivedRate, 1)
  const occupancyRate = roundTo(portfolioSummary.value.occupancyRate, 1)
  const tone = ['danger', 'error'].includes(priority.tone)
    ? 'danger'
    : (priority.tone === 'warning' ? 'warning' : 'success')
  return {
    tone,
    title: tone === 'danger' ? '需要处理' : (tone === 'warning' ? '需要跟进' : '健康稳定'),
    detail: `${priority.label} · ${priority.title}`,
    collectionRate,
    occupancyRate
  }
})

const analyticsLeadFacts = computed(() => [
  {
    key: 'occupancy',
    label: '出租',
    value: `${portfolioHealth.value.occupancyRate}%`,
    helper: `空置 ${formatArea(operationsMetrics.value.vacancy_area || 0)}㎡`,
    tone: vacancyDecision.value.tone
  },
  {
    key: 'collection',
    label: '已收',
    value: compactMoney(collectionSummary.value.received),
    fullValue: formatCurrency(collectionSummary.value.received),
    helper: `回款率 ${roundTo(collectionSummary.value.receivedRate, 1)}%`,
    tone: collectionDecision.value.tone
  },
  {
    key: 'overdue',
    label: '逾期',
    value: compactMoney(collectionSummary.value.overdueBalance),
    fullValue: formatCurrency(collectionSummary.value.overdueBalance),
    helper: `${formatNumber(collectionSummary.value.overdueCount)} 笔`,
    tone: collectionSummary.value.overdueBalance > 0 ? 'danger' : 'success'
  },
  {
    key: 'trend',
    label: '趋势',
    value: formatSignedPercent(portfolioTrendSummary.value.rentMom),
    helper: `出租 ${t(formatSignedShortPoints(portfolioTrendSummary.value.occupancyMom))}`,
    tone: portfolioTrendSummary.value.tone
  }
])

const decisionSignals = computed(() => {
  const vacancyFocus = vacancyFocusTower.value
  const vacancyOccupancy = vacancyFocus
    ? numberValue(vacancyFocus.occupancyRate)
    : numberValue(operations.value.vacancy?.occupancy_rate ?? portfolioSummary.value.occupancyRate)
  const expiring90 = numberValue(operationsMetrics.value.expiring_contract_count_90)
  const expiryTotal = numberValue(expirySummary.value.total)

  const signals = [
    {
      ...collectionDecision.value,
      testId: 'analytics-signal-cash',
      metricLabel: `回款 ${roundTo(collectionSummary.value.receivedRate, 1)}%`,
      barValue: clampPercent(collectionSummary.value.receivedRate)
    },
    {
      ...vacancyDecision.value,
      testId: 'analytics-signal-vacancy',
      metricLabel: `出租 ${roundTo(vacancyOccupancy, 1)}%`,
      barValue: clampPercent(vacancyOccupancy)
    },
    {
      ...expiryDecision.value,
      testId: 'analytics-signal-expiry',
      metricLabel: expiring90 > 0 ? `90天 ${formatNumber(expiring90)}份` : `12月 ${formatNumber(expiryTotal)}份`,
      barValue: clampPercent(Math.min((expiring90 || expiryTotal) * 10, 100))
    }
  ]
  return signals.filter((item) => item.key !== primaryDecision.value.key)
})

const analyticsActions = computed(() => [
  {
    key: 'receivables',
    tone: collectionDecision.value.tone,
    title: '进入应收',
    detail: collectionSummary.value.overdueBalance > 0
      ? `逾期 ${formatCurrency(collectionSummary.value.overdueBalance)}`
      : `${collectionSummary.value.month} 回款率 ${roundTo(collectionSummary.value.receivedRate, 1)}%`,
    intent: receivableDrilldownIntent(collectionSummary.value.month === '--' ? null : collectionSummary.value.month)
  },
  {
    key: 'rooms',
    tone: vacancyDecision.value.tone,
    title: '进入房源',
    detail: vacancyFocusTower.value
      ? `${t(vacancyFocusTower.value.tower)} 待租 ${formatArea(vacancyFocusTower.value.vacantArea)} ㎡`
      : '空置与出租',
    intent: roomDrilldownIntent(vacancyFocusTower.value?.tower || '', {
      occupancy: vacancyFocusTower.value?.vacantArea > 0 ? '空置' : ''
    })
  },
  {
    key: 'contracts',
    tone: expiryDecision.value.tone,
    title: '进入合同',
    detail: expiryDecision.value.title,
    intent: expiryContractDrilldownIntent(expirySummary.value.peak)
  },
  {
    key: 'customers',
    tone: customerOverview.value.topAreaShare >= 30 ? 'warning' : 'info',
    title: '进入客户',
    detail: customerOverview.value.topAreaName === '--'
      ? '客户资料'
      : `${customerOverview.value.topAreaName} 占 ${customerOverview.value.topAreaShare}%`,
    intent: customerDrilldownIntent(customerRows.value[0])
  }
])

function readAnalyticsData(path) {
  return props.buildingCode
    ? api(cockpitBuildingPath(path, props.buildingCode), { includeScope: false })
    : cachedApi(path)
}

async function fetchTowerRoomArea(sequence) {
  if (hasAnalyticsRoomAreas(props.stats)) { towerRoomArea.value = {}; return }
  try {
    const rooms = props.loadRooms ? await props.loadRooms() : await readAnalyticsData('/api/rooms')
    if (sequence !== loadSequence) return
    const summary = {}
    for (const room of rooms || []) {
      const group = getLeasingGroup(room)
      if (!group) continue
      summary[group] = (summary[group] || 0) + Number(room.area || 0)
    }
    towerRoomArea.value = summary
  } catch {
    if (sequence !== loadSequence) return
    // 失败时不再静默降级为零值，提示用户刷新重试
    towerRoomArea.value = {}
    analyticsLoadError.value = '数据加载失败，请刷新重试'
  }
}

async function fetchOperationsContext(sequence) {
  const snapshotContext = buildingAnalyticsSnapshot(props.snapshot, props.buildingCode)
  if (snapshotContext) {
    operationsSnapshot.value = snapshotContext.operations
    operationsTrendRows.value = snapshotContext.trend
    return
  }
  const [operationsResult, trendResult] = await Promise.allSettled([
    readAnalyticsData('/api/dashboard/operations?include_trend=false&queue_limit=1&risk_limit=3'),
    readAnalyticsData('/api/dashboard/operations/trend')
  ])
  if (sequence !== loadSequence) return
  operationsSnapshot.value = operationsResult.status === 'fulfilled' ? (operationsResult.value || {}) : {}
  operationsTrendRows.value = trendResult.status === 'fulfilled' ? (trendResult.value?.trend || []) : []
  if (operationsResult.status === 'rejected' || trendResult.status === 'rejected') {
    analyticsLoadError.value = '数据加载失败，请刷新重试'
  }
}

function selectTab(key) {
  emit('select-tab', key)
}

function emitDrilldown(intent) {
  if (!intent?.target) return
  emit('drilldown', intent)
}

function segmentRoomUse(tower) {
  return usesLegacyDemoAFloorPlan(props.operationsProfile) && tower === '商业配套' ? 'retail' : ''
}

function roomDrilldownIntent(tower, extraFilters = {}) {
  const filters = {
    tower: tower || '',
    use: segmentRoomUse(tower),
    ...extraFilters
  }
  const title = [
    tower || '全部分区',
    filters.occupancy || '',
    filters.use === 'retail' ? '商业用途' : ''
  ].filter(Boolean).join(' / ')
  return {
    target: 'rooms',
    filters,
    message: `来自经营分析：进入${title}房源`
  }
}

function statusDrilldownIntent(status) {
  return {
    target: 'contracts',
    filters: { status },
    message: `来自经营分析：已筛选 ${status || '全部'} 合同`
  }
}

function customerDrilldownIntent(customer) {
  const name = customer?.tenant_name || ''
  return {
    target: 'customers',
    filters: {
      customerId: customer?.customer_id || null,
      keyword: customer?.customer_id ? '' : name
    },
    message: name
      ? `来自经营分析：进入客户 ${name}`
      : '来自经营分析：进入客户管理'
  }
}

function expiryContractDrilldownIntent(peak) {
  const peakText = peak?.month && peak.count ? `，峰值 ${peak.month} ${peak.count} 份` : ''
  return {
    target: 'contracts',
    filters: { status: '即将到期', sortBy: 'end_date', sortDir: 'asc' },
    message: `来自经营分析：进入未来到期合同${peakText}`
  }
}

function receivableDrilldownIntent(month) {
  return {
    target: 'receivables',
    filters: month ? { month } : {},
    message: month ? `来自经营分析：进入 ${month} 月度应收` : '来自经营分析：进入月度应收'
  }
}

function handleAnalyticsAction(item) {
  if (item.intent) {
    emitDrilldown(item.intent)
    return
  }
  if (item.tab) selectTab(item.tab)
}

watch(() => [props.snapshot, props.buildingCode], () => {
  const sequence = ++loadSequence
  analyticsLoadError.value = ''
  void fetchTowerRoomArea(sequence)
  void fetchOperationsContext(sequence)
}, { immediate: true })
onBeforeUnmount(() => { ++loadSequence })
</script>

<template>
  <section class="content dashboard-page operations-analytics-page" data-testid="analytics-page">
    <div v-if="analyticsLoadError" class="analytics-load-error" role="alert" data-testid="analytics-load-error">
      {{ $t(analyticsLoadError) }}
    </div>
    <div class="analytics-layout">
      <article class="card dashboard-card analytics-diagnostic-card" data-testid="analytics-insights">
        <div class="card-header compact">
          <div>
            <div class="section-label">{{ $t("经营分析") }}</div>
            <h3 class="card-title">{{ $t("经营驾驶舱") }}</h3>
          </div>
          <div class="pill-group">
            <span class="metric-pill" :class="primaryDecision.tone">{{ $t("重点 ") }}{{ $t(primaryDecision.label) }}</span>
            <span class="metric-pill info">{{ $t("在租 ") }}{{ $t(formatNumber(portfolioSummary.activeContracts)) }}{{ $t(" 份") }}</span>
            <span class="metric-pill success">{{ $t("月租 ") }}{{ $t(formatCurrency(portfolioSummary.totalRent)) }}</span>
          </div>
        </div>

        <div class="analytics-diagnostic-body">
          <div class="analytics-decision-board">
            <div class="analytics-command-board" data-testid="analytics-command-board">
              <section class="analytics-lead-panel" :class="primaryDecision.tone">
                <div class="analytics-lead-copy">
                  <span>{{ $t("经营状态 · ") }}{{ $t(primaryDecision.label) }}</span>
                  <strong>{{ $t(primaryDecision.title) }}</strong>
                  <small>{{ $t(primaryDecision.detail) }}</small>
                </div>
                <div class="analytics-lead-facts" :aria-label="$t(&quot;经营关键事实&quot;)">
                  <span v-for="fact in analyticsLeadFacts" :key="fact.key" :class="fact.tone">
                    <small>{{ $t(fact.label) }}</small>
                    <strong :title="$t(fact.fullValue || fact.value)">{{ $t(fact.value) }}</strong>
                    <em :title="$t(fact.helper)">{{ $t(fact.helper) }}</em>
                  </span>
                </div>
              </section>
            </div>

            <div class="analytics-insight-grid">
              <article
                v-for="item in decisionSignals"
                :key="item.key"
                class="analytics-insight-item"
                :class="item.tone"
                :data-testid="item.testId"
              >
                <div class="analytics-insight-head">
                  <span>{{ $t(item.label) }}</span>
                  <em>{{ $t(item.metricLabel) }}</em>
                </div>
                <strong>{{ $t(item.title) }}</strong>
                <small>{{ $t(item.detail) }}</small>
                <div class="analytics-signal-bar" aria-hidden="true">
                  <span :style="{ width: `${item.barValue}%` }" />
                </div>
              </article>
            </div>
          </div>

          <div class="analytics-action-strip" :aria-label="$t(&quot;工作区入口&quot;)">
            <span class="analytics-action-title">{{ $t("工作区入口") }}</span>
            <button
              v-for="item in analyticsActions"
              :key="item.key"
              class="analytics-action-button"
              :class="item.tone"
              type="button"
              :data-testid="`analytics-action-${item.key}`"
              @click="handleAnalyticsAction(item)"
            >
              <span>{{ $t(item.title) }}</span>
              <strong>{{ $t(item.detail) }}</strong>
            </button>
          </div>
        </div>
      </article>

      <RentTimelineAnalytics
        v-if="showRentTimeline"
        :user="user"
        :building-code="buildingCode"
        @notify="emit('notify', $event)"
        @open-contract="emit('open-contract', $event)"
      />

      <div class="analytics-top-grid">
        <article class="card dashboard-card analytics-status-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">{{ $t("合同状态") }}</div>
            <h3 class="card-title">{{ $t("合同状态概览") }}</h3>
          </div>
          <div class="pill-group">
            <span class="metric-pill info">{{ $t("合同 ") }}{{ $t(formatNumber(statusTotal)) }}</span>
          </div>
        </div>

        <div class="chart-layout status-chart-layout" data-testid="analytics-status-chart">
          <EChartPanel :option="statusOption" :empty="!statusRows.length" :empty-text="$t(&quot;暂无状态&quot;)" :height="180" />
          <div class="legend-list status-legend-list">
            <div v-for="item in statusRows" :key="item.label" class="legend-row analytics-legend-row">
              <div class="legend-meta">
                <div class="legend-title">
                  <span class="status-swatch" :style="{ background: item.color }" />
                  <span>{{ $t(item.label) }}</span>
                </div>
                <small>{{ $t(item.percent) }}%</small>
              </div>
              <div class="analytics-row-action">
                <strong>{{ $t(item.count) }}</strong>
                <button
                  class="btn btn-compact"
                  type="button"
                  :data-testid="`analytics-status-drilldown-${item.label}`"
                  @click="emitDrilldown(statusDrilldownIntent(item.label))"
                >{{ $t(" 进入合同 ") }}</button>
              </div>
            </div>
          </div>
        </div>
      </article>

        <article class="card dashboard-card analytics-customer-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">{{ $t("客户概览") }}</div>
            <h3 class="card-title">{{ $t("客户概览 / 客户排行") }}</h3>
          </div>
          <div class="pill-group">
            <span class="metric-pill info">{{ $t("客户数 ") }}{{ $t(customerOverview.count) }}</span>
            <span class="metric-pill success">{{ $t("最大面积占比 ") }}{{ $t(customerOverview.topAreaShare) }}%</span>
          </div>
        </div>

        <div class="customer-overview-grid" data-testid="analytics-customer-rank">
          <div class="customer-overview-metrics">
            <div class="mini-stat">
              <span>{{ $t("最大承租客户") }}</span>
              <strong class="customer-name-ellipsis" :title="$t(customerOverview.topAreaName)">{{ $t(customerOverview.topAreaName) }}</strong>
            </div>
            <div class="mini-stat">
              <span>{{ $t("最高月租客户") }}</span>
              <strong class="customer-name-ellipsis" :title="$t(customerOverview.topRentName)">{{ $t(customerOverview.topRentName) }}</strong>
              <small>{{ $t(formatCurrency(customerOverview.topRent)) }}</small>
            </div>
          </div>

          <div class="customer-rank-list">
            <article v-for="(item, index) in topCustomerRows" :key="item.tenant_name" class="customer-rank-row">
              <div class="customer-rank-main">
                <span class="customer-rank-index">{{ $t(index + 1) }}</span>
                <div class="customer-rank-copy">
                  <strong class="customer-name-ellipsis" :title="item.tenant_name">{{ item.tenant_name }}</strong>
                  <p class="analytics-line-ellipsis" :title="$t(`${item.groupsText} · ${item.room_count} 个房源 · ${item.roomsText}${item.hasMoreRooms ? ' 等' : ''}`)">{{ $t(item.groupsText) }} · {{ $t(item.room_count) }}{{ $t(" 个房源 · ") }}{{ $t(item.roomsText) }}<template v-if="item.hasMoreRooms">{{ $t(" 等") }}</template></p>
                </div>
              </div>
              <div class="customer-rank-values">
                <div>
                  <span>{{ $t("合并面积") }}</span>
                  <strong>{{ $t(formatArea(item.area)) }} {{ $t("㎡") }}</strong>
                </div>
                <div>
                  <span>{{ $t("月租金") }}</span>
                  <strong>{{ $t(formatCurrency(item.rent)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("最近到期") }}</span>
                  <strong>{{ $t(item.nearest_end_date || '--') }}</strong>
                </div>
              </div>
              <div class="customer-rank-actions">
                <button
                  class="btn btn-compact"
                  type="button"
                  :data-testid="`analytics-customer-drilldown-${index}`"
                  @click="emitDrilldown(customerDrilldownIntent(item))"
                >{{ $t(" 进入客户 ") }}</button>
              </div>
              <div class="customer-area-track">
                <span :style="{ width: `${Math.min(item.areaShare, 100)}%` }" />
              </div>
            </article>
            <div v-if="!topCustomerRows.length" class="compact-empty">{{ $t("暂无客户排行") }}</div>
          </div>
        </div>
      </article>
      </div>

      <details class="demo-secondary-details analytics-secondary-details">
        <summary>
          <span>{{ $t("分区结构") }}</span>
          <strong>{{ $t(analysisBreakdownTitle) }}</strong>
        </summary>
        <div class="analytics-secondary-stack">
      <article class="card dashboard-card analytics-tower-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">{{ $t("分区表现") }}</div>
            <h3 class="card-title">{{ $t("分区表现") }}</h3>
          </div>
        </div>

        <div class="tower-focus-strip">
          <div>
            <span>{{ $t("收入主力") }}</span>
            <strong>{{ $t(towerHighlights.rentLeader?.tower || '--') }}</strong>
            <small>{{ $t(formatCurrency(towerHighlights.rentLeader?.rent || 0)) }}{{ $t(" / 月") }}</small>
          </div>
          <div>
            <span>{{ $t("出租领先") }}</span>
            <strong>{{ $t(towerHighlights.occupancyLeader?.tower || '--') }}</strong>
            <small>{{ $t("出租率 ") }}{{ $t(towerHighlights.occupancyLeader?.occupancyRate || 0) }}%</small>
          </div>
          <div>
            <span>{{ $t("关注去化") }}</span>
            <strong>{{ $t(towerHighlights.vacancyFocus?.tower || '--') }}</strong>
            <small>{{ $t("待租 ") }}{{ $t(formatArea(towerHighlights.vacancyFocus?.vacantArea || 0)) }} {{ $t("㎡") }}</small>
          </div>
        </div>

        <div class="tower-summary-grid" data-testid="analytics-tower-summary">
          <article v-for="item in towerRows" :key="item.tower" class="tower-summary-card">
            <div class="tower-summary-head">
              <strong>{{ $t(item.tower) }}</strong>
              <span>{{ $t(item.count) }}{{ $t(" 份在租合同") }}</span>
            </div>
            <div class="tower-summary-metrics">
              <div>
                <span>{{ $t("在租面积") }}</span>
                <strong>{{ $t(formatArea(item.area)) }} {{ $t("㎡") }}</strong>
              </div>
              <div>
                <span>{{ $t("出租率") }}</span>
                <strong>{{ $t(item.occupancyRate) }}%</strong>
              </div>
              <div>
                <span>{{ $t("待租面积") }}</span>
                <strong>{{ $t(formatArea(item.vacantArea)) }} {{ $t("㎡") }}</strong>
              </div>
              <div>
                <span>{{ $t("月租金") }}</span>
                <strong>{{ $t(formatCurrency(item.rent)) }}</strong>
              </div>
            </div>
            <div class="tower-summary-foot">
              <span>{{ $t("收入占比 ") }}{{ $t(item.revenueShare) }}%</span>
              <span>{{ $t("单份合同平均面积 ") }}{{ $t(formatArea(item.avgContractArea)) }} {{ $t("㎡") }}</span>
            </div>
            <div class="tower-summary-actions">
              <button
                class="btn btn-compact"
                type="button"
                :data-testid="`analytics-tower-rooms-${item.tower}`"
                @click="emitDrilldown(roomDrilldownIntent(item.tower))"
              >{{ $t(" 进入房源 ") }}</button>
              <button
                class="btn btn-compact"
                type="button"
                :disabled="item.vacantArea <= 0"
                :data-testid="`analytics-tower-vacancy-${item.tower}`"
                @click="emitDrilldown(roomDrilldownIntent(item.tower, { occupancy: '空置' }))"
              >{{ $t(" 进入空置 ") }}</button>
            </div>
          </article>
        </div>

        <div class="tower-chart-grid">
          <ChartCard
            :title="$t(&quot;面积去化&quot;)"
            :subtitle="$t(&quot;在租 / 空置&quot;)"
            :option="towerMixOption"
            :empty="!towerRows.length"
            :empty-text="$t(&quot;暂无面积&quot;)"
            :height="260"
            :insight="towerMixInsight"
            tone="success"
            data-testid="analytics-tower-mix-chart"
          />
        </div>
      </article>
        </div>
      </details>
    </div>
  </section>
</template>
