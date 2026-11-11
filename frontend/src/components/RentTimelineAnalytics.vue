<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef } from 'vue'
import ChartSkeleton from './ChartSkeleton.vue'
import EChartPanel from './EChartPanel.vue'
import { api, openDownload } from '../lib/api'
import { formatArea, formatCurrency, formatNumber } from '../lib/format'
import { cockpitBuildingPath } from '../lib/cockpitDomain.js'
import {
  addDays,
  addYearsClamped,
  aggregateRentTimeline,
  alignedComparisonTimeline,
  buildRentAnalyticsIndex,
  chooseTimelineGranularity,
  clampDateRange,
  compareRentRange,
  eventImpactsForSegments,
  eventsForRange,
  fitRentExportImage,
  hasFutureRentData,
  inclusiveDayCount,
  isoDateTimestamp,
  normalizeRentAnalytics,
  rentCoverageForRange,
  rentRowsForRange,
  rentExportQuery,
  summarizeRentRange
} from '../lib/rentAnalyticsDomain'

const props = defineProps({
  compact: Boolean,
  minimumPixelRatio: { type: Number, default: 2 },
  buildingCode: { type: String, default: '' },
  user: {
    type: Object,
    default: () => ({ role: 'viewer' })
  }
})
const emit = defineEmits(['notify', 'open-contract'])

const chartPanel = ref(null)
// 服务端快照仅整体替换，不为每一天、每个分区创建深层响应式代理。
const analytics = shallowRef(null)
const loading = ref(true)
const loadError = ref('')
const selectedSegments = ref(['overall'])
const startDate = ref('')
const endDate = ref('')
const previewStartDate = ref('')
const previewEndDate = ref('')
const draftStartDate = ref('')
const draftEndDate = ref('')
const activePreset = ref('')
const comparisonMode = ref('none')
const rangeError = ref('')
const rangeStatus = ref('')
const selectedWindow = ref(null)
const pngExporting = ref(false)
let loadSequence = 0
let lastLoadedAt = 0
let zoomCommitTimer = null
let zoomPointerDown = false
let pendingZoomRange = null

const canExportExcel = computed(() => ['admin', 'editor'].includes(props.user?.role))
const segmentOptions = computed(() => analytics.value?.segments || [])
const segmentByKey = computed(() => Object.fromEntries(segmentOptions.value.map((item) => [item.key, item])))
const timelineIndex = computed(() => (
  analytics.value
    ? buildRentAnalyticsIndex(analytics.value.daily, analytics.value.segments)
    : { dates: [], segments: {} }
))
const liveRange = computed(() => ({
  start: previewStartDate.value || startDate.value,
  end: previewEndDate.value || endDate.value
}))
const committedRange = computed(() => ({ start: startDate.value, end: endDate.value }))
const granularity = computed(() => chooseTimelineGranularity(startDate.value, endDate.value))
const granularityLabel = computed(() => ({
  day: '按日显示',
  week: '按周显示',
  month: '按月显示'
}[granularity.value] || '按日显示'))

const liveSummary = computed(() => {
  if (!analytics.value || !liveRange.value.start || !liveRange.value.end) return {}
  return summarizeRentRange(
    timelineIndex.value,
    liveRange.value.start,
    liveRange.value.end,
    selectedSegments.value,
    analytics.value.bounds.today
  )
})

const primarySegmentKey = computed(() => selectedSegments.value[0] || 'overall')
const primaryComparison = computed(() => {
  if (!analytics.value || !liveRange.value.start || !liveRange.value.end) return null
  return compareRentRange(
    timelineIndex.value,
    liveRange.value.start,
    liveRange.value.end,
    primarySegmentKey.value,
    analytics.value.bounds
  )
})
const currentCoverageStatus = computed(() => (
  analytics.value
    ? rentCoverageForRange(analytics.value.coverage, liveRange.value.start, liveRange.value.end)
    : { isComplete: true, unknownCount: 0, affectedCount: 0 }
))
const previousCoverageStatus = computed(() => (
  analytics.value && primaryComparison.value?.previousRange
    ? rentCoverageForRange(
        analytics.value.coverage,
        primaryComparison.value.previousRange.start,
        primaryComparison.value.previousRange.end
      )
    : { isComplete: true, unknownCount: 0, affectedCount: 0 }
))
const yoyCoverageStatus = computed(() => (
  analytics.value && primaryComparison.value?.yoyRange
    ? rentCoverageForRange(
        analytics.value.coverage,
        primaryComparison.value.yoyRange.start,
        primaryComparison.value.yoyRange.end
      )
    : { isComplete: true, unknownCount: 0, affectedCount: 0 }
))
const previousCoverageComplete = computed(() => (
  currentCoverageStatus.value.isComplete && previousCoverageStatus.value.isComplete
))
const yoyCoverageComplete = computed(() => (
  currentCoverageStatus.value.isComplete && yoyCoverageStatus.value.isComplete
))
const previousComparisonAvailable = computed(() => (
  previousCoverageComplete.value
  && primaryComparison.value?.previous?.effectiveDailyRent != null
))
const yoyComparisonAvailable = computed(() => (
  yoyCoverageComplete.value
  && primaryComparison.value?.yoy?.effectiveDailyRent != null
))
const comparisonUnavailableNote = computed(() => {
  if (!analytics.value) return ''
  if (currentCoverageStatus.value.unknownCount) {
    return `有 ${currentCoverageStatus.value.unknownCount} 份排除合同缺少有效起止日期，无法判断影响范围；前期和同比均已关闭。`
  }
  if (currentCoverageStatus.value.affectedCount) {
    return `当前窗口与 ${currentCoverageStatus.value.affectedCount} 份排除合同重叠；前期和同比均已关闭。`
  }
  const coverageNotes = []
  if (previousCoverageStatus.value.affectedCount) {
    coverageNotes.push(`前期基线与 ${previousCoverageStatus.value.affectedCount} 份排除合同重叠，前期已关闭`)
  }
  if (yoyCoverageStatus.value.affectedCount) {
    coverageNotes.push(`同比基线与 ${yoyCoverageStatus.value.affectedCount} 份排除合同重叠，同比已关闭`)
  }
  if (coverageNotes.length) {
    return `${coverageNotes.join('；')}。`
  }
  if (!previousComparisonAvailable.value || !yoyComparisonAvailable.value) {
    return '灰色选项表示所需历史区间超出数据范围，或该区间没有有效出租面积。'
  }
  return '前期为紧邻的等长区间；同比仅支持不超过一年的窗口。'
})
const futureDataAvailable = computed(() => (
  analytics.value
    ? hasFutureRentData(analytics.value.daily, analytics.value.bounds.today)
    : false
))

const summaryCards = computed(() => selectedSegments.value.map((key) => ({
  key,
  label: segmentByKey.value[key]?.label || key,
  color: segmentByKey.value[key]?.color || '#1d4ed8',
  ...(liveSummary.value[key] || {})
})))

const rangeEvents = computed(() => {
  if (!analytics.value || !startDate.value || !endDate.value) return []
  return eventsForRange(
    analytics.value.events,
    startDate.value,
    endDate.value,
    selectedSegments.value
  )
})

const eventDateOptions = computed(() => {
  const grouped = new Map()
  for (const event of rangeEvents.value) {
    const entry = grouped.get(event.date) || { date: event.date, count: 0, label: event.label }
    entry.count += 1
    grouped.set(event.date, entry)
  }
  return [...grouped.values()].sort((a, b) => a.date.localeCompare(b.date))
})

const selectedWindowEvents = computed(() => {
  if (!selectedWindow.value || !analytics.value) return []
  return eventsForRange(
    analytics.value.events,
    selectedWindow.value.start,
    selectedWindow.value.end,
    selectedSegments.value
  ).map((item) => ({
    ...item,
    displayImpacts: eventImpactsForSegments(
      item,
      selectedSegments.value,
      analytics.value.segments
    )
  }))
})

const selectedWindowLabel = computed(() => {
  if (!selectedWindow.value) return ''
  return selectedWindow.value.start === selectedWindow.value.end
    ? selectedWindow.value.start
    : `${selectedWindow.value.start} 至 ${selectedWindow.value.end}`
})

const selectedRangeLabel = computed(() => (
  liveRange.value.start && liveRange.value.end
    ? `${liveRange.value.start} 至 ${liveRange.value.end}`
    : '--'
))

const presetRanges = computed(() => {
  const bounds = analytics.value?.bounds
  if (!bounds?.today) return []
  const year = bounds.today.slice(0, 4)
  const bounded = (key, label, start, end, extra = {}) => ({
    key,
    label,
    ...clampDateRange(start, end, bounds.minDate, bounds.maxDate),
    ...extra
  })
  return [
    bounded('year', '本年度', `${year}-01-01`, bounds.today),
    bounded(
      'past12',
      '近12个月',
      addDays(addYearsClamped(bounds.today, -1), 1),
      bounds.today
    ),
    bounded(
      'next12',
      '未来12个月',
      addDays(bounds.today, 1),
      addYearsClamped(bounds.today, 1),
      {
        disabled: !futureDataAvailable.value,
        disabledReason: '目前没有今天之后的已签合同数据'
      }
    ),
    bounded('all', '全部', bounds.minDate, bounds.maxDate)
  ]
})

const aggregatedPoints = computed(() => (
  analytics.value
    ? aggregateRentTimeline(
        rentRowsForRange(analytics.value.daily, startDate.value, endDate.value),
        selectedSegments.value,
        granularity.value
      )
    : []
))

const dailyByDate = computed(() => new Map((analytics.value?.daily || []).map((row) => [row.date, row])))

function roundTo(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return null
  const factor = 10 ** digits
  return Math.round(Number(value) * factor) / factor
}

function rateText(value) {
  return value == null ? '--' : Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}

function signedPercent(value) {
  const rounded = roundTo(value, 1)
  if (rounded == null) return '数据不足'
  return `${rounded > 0 ? '+' : ''}${rounded}%`
}

function comparisonPercentText(value, coverageComplete) {
  return coverageComplete ? signedPercent(value) : '覆盖不足'
}

function comparisonTone(value) {
  if (value == null || Math.abs(value) < 0.05) return 'muted'
  return value > 0 ? 'success' : 'warning'
}

function metricPhaseLabel(card) {
  if (card.history && card.forecast) return '历史 / 已签预测'
  if (card.forecast) return '已签合同预测平均日租'
  return '历史有效平均日租'
}

function metricRangeDescription(metric) {
  if (!metric) return '暂无有效出租面积'
  return `应计 ${formatCurrency(metric.rent)} 元 · 日均在租 ${formatArea(metric.averageArea)} ㎡`
}

function notify(text, type = 'info') {
  emit('notify', { text, type })
}

function syncRangeFields(start, end) {
  previewStartDate.value = start
  previewEndDate.value = end
  draftStartDate.value = start
  draftEndDate.value = end
}

function applyRange(start, end, { fromChart = false, presetKey = '' } = {}) {
  if (!analytics.value) return
  if (!start || !end) {
    rangeError.value = '请选择完整的起止日期'
    return
  }
  if (start > end) {
    rangeError.value = '起始日期不能晚于结束日期'
    return
  }
  const range = clampDateRange(
    start,
    end,
    analytics.value.bounds.minDate,
    analytics.value.bounds.maxDate
  )
  clearPendingZoom()
  rangeError.value = ''
  startDate.value = range.start
  endDate.value = range.end
  activePreset.value = presetKey
  syncRangeFields(range.start, range.end)
  selectedWindow.value = null
  if (
    comparisonMode.value === 'previous' && !previousComparisonAvailable.value
    || comparisonMode.value === 'yoy' && !yoyComparisonAvailable.value
  ) {
    comparisonMode.value = 'none'
  }
  rangeStatus.value = fromChart
    ? '已按日级数据精确核对'
    : `已更新为 ${range.start} 至 ${range.end}`
}

function applyPreset(preset) {
  if (preset.disabled) {
    notify(preset.disabledReason || '该时间范围暂无数据', 'error')
    return
  }
  applyRange(preset.start, preset.end, { presetKey: preset.key })
}

function commitDraftRange() {
  applyRange(draftStartDate.value, draftEndDate.value)
}

function toggleSegment(key) {
  const current = [...selectedSegments.value]
  const index = current.indexOf(key)
  if (index >= 0) {
    if (current.length === 1) {
      notify('至少保留一个分析范围', 'error')
      return
    }
    current.splice(index, 1)
  } else {
    current.push(key)
  }
  selectedSegments.value = current
  selectedWindow.value = null
  if (comparisonMode.value !== 'none') comparisonMode.value = 'none'
}

function zoomValueToIso(value) {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) return value.slice(0, 10)
  const timestamp = Number(value)
  if (!Number.isFinite(timestamp)) return ''
  return new Date(timestamp).toISOString().slice(0, 10)
}

function percentToDate(percent) {
  const rows = analytics.value?.daily || []
  if (!rows.length) return ''
  const bounded = Math.max(0, Math.min(100, Number(percent || 0)))
  const index = Math.round((bounded / 100) * (rows.length - 1))
  return rows[index]?.date || ''
}

function handleDataZoom(event) {
  if (!analytics.value) return
  const detail = event?.batch?.[0] || event || {}
  const rawStart = zoomValueToIso(detail.startValue) || percentToDate(detail.start)
  const rawEnd = zoomValueToIso(detail.endValue) || percentToDate(detail.end)
  if (!rawStart || !rawEnd) return
  const range = clampDateRange(
    rawStart,
    rawEnd,
    analytics.value.bounds.minDate,
    analytics.value.bounds.maxDate
  )
  if (range.start === startDate.value && range.end === endDate.value) {
    clearPendingZoom()
    syncRangeFields(range.start, range.end)
    rangeStatus.value = '已按日级数据精确核对'
    return
  }
  previewStartDate.value = range.start
  previewEndDate.value = range.end
  draftStartDate.value = range.start
  draftEndDate.value = range.end
  rangeStatus.value = '拖动预览中'
  pendingZoomRange = range
  if (zoomCommitTimer) window.clearTimeout(zoomCommitTimer)
  // 拖动中只更新摘要与日期；图表由 ECharts 自身平移，松手才重算完整序列。
  // 滚轮和程序触发的缩放没有 pointerup，继续按停顿提交。
  if (!zoomPointerDown) zoomCommitTimer = window.setTimeout(commitPendingZoom, 240)
}

function clearPendingZoom() {
  if (zoomCommitTimer) window.clearTimeout(zoomCommitTimer)
  zoomCommitTimer = null
  pendingZoomRange = null
}

function beginZoomInteraction() {
  zoomPointerDown = true
  if (zoomCommitTimer) window.clearTimeout(zoomCommitTimer)
  zoomCommitTimer = null
}

function commitPendingZoom() {
  zoomPointerDown = false
  if (pendingZoomRange) {
    applyRange(pendingZoomRange.start, pendingZoomRange.end, { fromChart: true })
  }
}

function cancelZoomPreview() {
  zoomPointerDown = false
  clearPendingZoom()
  syncRangeFields(startDate.value, endDate.value)
  if (rangeStatus.value === '拖动预览中') rangeStatus.value = '已按日级数据精确核对'
}

function selectEventDate(event) {
  const date = event.target.value
  selectedWindow.value = date ? { start: date, end: date } : null
}

function handleChartClick(params) {
  const data = params?.data || {}
  const eventDate = data.eventDate || data.date
  if (eventDate) {
    selectedWindow.value = { start: eventDate, end: eventDate }
    return
  }
  if (data.bucketStart && data.bucketEnd) {
    selectedWindow.value = { start: data.bucketStart, end: data.bucketEnd }
  }
}

function openDriverContract(event) {
  if (!event?.contractId) return
  emit('open-contract', { id: event.contractId, initialSection: 'rent' })
}

function eventTypeLabel(type) {
  return {
    contract_start: '合同起租',
    contract_end: '合同到期',
    rent_term_start: '租金调整',
    rent_term_end: '租金调整结束',
    rent_free_start: '免租开始',
    rent_free_end: '免租结束',
    area_change: '面积变化'
  }[type] || '合同变化'
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function shortNumber(value) {
  const number = Number(value || 0)
  if (Math.abs(number) >= 10000) return `${roundTo(number / 10000, 1)}万`
  return formatNumber(roundTo(number, 0))
}

function axisDateLabel(value) {
  const date = new Date(Number(value))
  if (Number.isNaN(date.getTime())) return ''
  const year = date.getUTCFullYear()
  const month = String(date.getUTCMonth() + 1).padStart(2, '0')
  if (granularity.value === 'month') return `${year}-${month}`
  const day = String(date.getUTCDate()).padStart(2, '0')
  return `${month}-${day}`
}

function pointData(point, metric) {
  return {
    value: [point.timestamp, metric === 'area' ? point.averageArea : point.effectiveDailyRent],
    bucketStart: point.startDate,
    bucketEnd: point.endDate,
    segmentKey: point.segmentKey,
    phase: point.phase,
    rent: point.rent,
    averageArea: point.averageArea
  }
}

function tooltipFormatter(params) {
  const visible = (Array.isArray(params) ? params : [params]).filter((item) => !item.data?.continuity)
  const first = visible[0]?.data
  if (!first) return ''
  const range = first.bucketStart === first.bucketEnd
    ? first.bucketStart
    : `${first.bucketStart} 至 ${first.bucketEnd}`
  const lines = [`<strong>${escapeHtml(range)}</strong>`]
  for (const item of visible) {
    const data = item.data || {}
    if (item.seriesName.includes('在租面积')) {
      lines.push(`${item.marker}${escapeHtml(item.seriesName)}：${formatArea(data.averageArea)} ㎡`)
    } else if (data.value?.[1] != null) {
      lines.push(`${item.marker}${escapeHtml(item.seriesName)}：${rateText(data.value[1])} 元/㎡/天`)
    }
  }
  lines.push('<small>月界的轻微变化可能来自按当月自然日摊分，并不一定代表调租。</small>')
  return lines.join('<br/>')
}

function eventMarkerData() {
  if (!analytics.value) return []
  const seen = new Set()
  const markers = []
  for (const event of rangeEvents.value) {
    if (seen.has(event.date)) continue
    const row = dailyByDate.value.get(event.date)
    const rate = row?.segments?.[primarySegmentKey.value]?.effectiveDailyRent
    if (rate == null) continue
    seen.add(event.date)
    markers.push({
      name: event.label,
      coord: [isoDateTimestamp(event.date), rate],
      eventDate: event.date,
      value: eventDateOptions.value.find((item) => item.date === event.date)?.count || 1
    })
  }
  return markers
}

const committedComparisonAvailable = computed(() => {
  if (!analytics.value || comparisonMode.value === 'none') return false
  const comparison = compareRentRange(
    timelineIndex.value, startDate.value, endDate.value, primarySegmentKey.value, analytics.value.bounds
  )
  const comparisonRange = comparisonMode.value === 'previous' ? comparison.previousRange : comparison.yoyRange
  const metric = comparisonMode.value === 'previous' ? comparison.previous : comparison.yoy
  return !!comparisonRange && metric?.effectiveDailyRent != null
    && rentCoverageForRange(analytics.value.coverage, startDate.value, endDate.value).isComplete
    && rentCoverageForRange(analytics.value.coverage, comparisonRange.start, comparisonRange.end).isComplete
})

const chartOption = computed(() => {
  if (!analytics.value) return {}
  const colors = segmentByKey.value
  const series = []
  const topSeries = []
  const lowerSeries = []
  const pointsByKey = new Map()
  for (const point of aggregatedPoints.value) {
    const key = `${point.segmentKey}|${point.phase}`
    const rows = pointsByKey.get(key) || []
    rows.push(point)
    pointsByKey.set(key, rows)
  }

  for (const segmentKey of selectedSegments.value) {
    const segment = colors[segmentKey] || { label: segmentKey, color: '#1d4ed8' }
    const history = pointsByKey.get(`${segmentKey}|history`) || []
    const forecast = pointsByKey.get(`${segmentKey}|forecast`) || []
    const lastHistory = history[history.length - 1]
    const continuityRate = lastHistory
      ? {
          ...pointData(lastHistory, 'rate'),
          continuity: true
        }
      : null
    const continuityArea = lastHistory
      ? {
          ...pointData(lastHistory, 'area'),
          continuity: true
        }
      : null
    topSeries.push(
      {
        name: `${segment.label}｜历史`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        step: 'end',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 3, color: segment.color, type: 'solid' },
        itemStyle: { color: segment.color },
        emphasis: { focus: 'series', lineStyle: { width: 4 } },
        data: history.map((point) => pointData(point, 'rate'))
      },
      {
        name: `${segment.label}｜已签预测`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        step: 'end',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 3, color: segment.color, type: 'dashed', opacity: 0.88 },
        itemStyle: { color: segment.color },
        emphasis: { focus: 'series', lineStyle: { width: 4 } },
        data: [
          ...(continuityRate ? [continuityRate] : []),
          ...forecast.map((point) => pointData(point, 'rate'))
        ]
      }
    )
    lowerSeries.push(
      {
        name: `${segment.label}｜历史在租面积`,
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        step: 'end',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.8, color: segment.color, type: 'solid' },
        areaStyle: { color: segment.color, opacity: 0.1 },
        itemStyle: { color: segment.color },
        data: history.map((point) => pointData(point, 'area'))
      },
      {
        name: `${segment.label}｜预测在租面积`,
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        step: 'end',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.8, color: segment.color, type: 'dashed', opacity: 0.82 },
        areaStyle: { color: segment.color, opacity: 0.045 },
        itemStyle: { color: segment.color },
        data: [
          ...(continuityArea ? [continuityArea] : []),
          ...forecast.map((point) => pointData(point, 'area'))
        ]
      }
    )
  }

  if (committedComparisonAvailable.value) {
    const comparison = alignedComparisonTimeline(
      analytics.value.daily,
      committedRange.value,
      primarySegmentKey.value,
      comparisonMode.value,
      granularity.value
    )
    if (comparison.length) {
      topSeries.push({
        name: comparisonMode.value === 'yoy' ? '上年同期' : '前一等长区间',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 2, color: '#64748b', type: 'dotted', opacity: 0.9 },
        itemStyle: { color: '#64748b' },
        data: comparison.map((point) => pointData(point, 'rate'))
      })
    }
  }

  const annotationSeries = topSeries.find((item) => item.data.length) || topSeries[0]
  if (annotationSeries) {
    annotationSeries.markLine = {
      silent: true,
      symbol: 'none',
      lineStyle: { color: '#475569', width: 1.5 },
      label: {
        show: true,
        formatter: '今天',
        color: '#334155',
        backgroundColor: '#ffffff',
        padding: [3, 6],
        borderRadius: 4
      },
      data: [{ xAxis: isoDateTimestamp(analytics.value.bounds.today) }]
    }
    annotationSeries.markArea = {
      silent: true,
      label: {
        show: true,
        position: 'insideTopRight',
        formatter: '已签合同预测',
        color: '#64748b',
        fontSize: 11
      },
      itemStyle: { color: 'rgba(148, 163, 184, 0.08)' },
      data: [[
        { xAxis: isoDateTimestamp(addDays(analytics.value.bounds.today, 1)) },
        { xAxis: isoDateTimestamp(analytics.value.bounds.maxDate) }
      ]]
    }
    annotationSeries.markPoint = {
      symbol: 'diamond',
      symbolSize: 13,
      label: { show: false },
      itemStyle: { color: '#f59e0b', borderColor: '#ffffff', borderWidth: 2 },
      emphasis: { scale: 1.35 },
      data: eventMarkerData()
    }
  }
  series.push(...topSeries, ...lowerSeries)

  const reducedMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  return {
    animation: !reducedMotion,
    animationDuration: reducedMotion ? 0 : 180,
    color: selectedSegments.value.map((key) => colors[key]?.color || '#1d4ed8'),
    aria: {
      enabled: true,
      description: `有效平均日租与在租面积，范围 ${startDate.value} 至 ${endDate.value}。实线为历史，虚线为已签合同预测。`
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
      label: { backgroundColor: '#334155' }
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'line', snap: true },
      formatter: tooltipFormatter
    },
    grid: [
      { left: 68, right: 28, top: 44, height: '46%', containLabel: false },
      { left: 68, right: 28, top: '66%', height: '15%', containLabel: false }
    ],
    xAxis: [
      {
        type: 'time',
        gridIndex: 0,
        min: isoDateTimestamp(analytics.value.bounds.minDate),
        max: isoDateTimestamp(analytics.value.bounds.maxDate),
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        splitLine: { show: false }
      },
      {
        type: 'time',
        gridIndex: 1,
        min: isoDateTimestamp(analytics.value.bounds.minDate),
        max: isoDateTimestamp(analytics.value.bounds.maxDate),
        axisLabel: { color: '#64748b', formatter: axisDateLabel, hideOverlap: true },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        splitLine: { show: false }
      }
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        name: '有效平均日租\n元/㎡/天',
        nameTextStyle: { color: '#64748b', align: 'right', lineHeight: 16 },
        scale: true,
        axisLabel: { color: '#64748b', formatter: (value) => roundTo(value, 2) },
        splitLine: { lineStyle: { color: '#e2e8f0', type: 'dashed' } }
      },
      {
        type: 'value',
        gridIndex: 1,
        name: '在租面积（㎡）',
        nameTextStyle: { color: '#64748b', align: 'right' },
        min: 0,
        axisLabel: { color: '#64748b', formatter: shortNumber },
        splitLine: { lineStyle: { color: '#eef2f7' } }
      }
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        filterMode: 'none',
        startValue: isoDateTimestamp(startDate.value),
        endValue: isoDateTimestamp(endDate.value),
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseMove: true,
        moveOnMouseWheel: true,
        preventDefaultMouseMove: false,
        throttle: 48
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        filterMode: 'none',
        bottom: 8,
        height: 28,
        startValue: isoDateTimestamp(startDate.value),
        endValue: isoDateTimestamp(endDate.value),
        realtime: true,
        throttle: 48,
        brushSelect: false,
        showDetail: true,
        labelFormatter: (value) => zoomValueToIso(value),
        handleSize: 22,
        moveHandleSize: 10,
        borderColor: '#cbd5e1',
        fillerColor: 'rgba(37, 99, 235, 0.14)',
        dataBackground: {
          lineStyle: { color: '#94a3b8' },
          areaStyle: { color: '#cbd5e1', opacity: 0.35 }
        },
        selectedDataBackground: {
          lineStyle: { color: '#2563eb' },
          areaStyle: { color: '#93c5fd', opacity: 0.38 }
        },
        textStyle: { color: '#475569', fontSize: 11 }
      }
    ],
    series
  }
})

async function loadAnalytics({ preserveRange = false } = {}) {
  const requestId = ++loadSequence
  if (!analytics.value) loading.value = true
  loadError.value = ''
  try {
    const payload = props.buildingCode
      ? await api(cockpitBuildingPath('/api/rent-analytics', props.buildingCode), { includeScope: false })
      : await api('/api/rent-analytics')
    if (requestId !== loadSequence) return
    const normalized = normalizeRentAnalytics(payload)
    analytics.value = normalized
    lastLoadedAt = Date.now()
    if (!preserveRange || !startDate.value || !endDate.value) {
      applyRange(normalized.bounds.defaultStart, normalized.bounds.defaultEnd, { presetKey: 'past12' })
    } else {
      applyRange(startDate.value, endDate.value)
    }
  } catch (error) {
    if (requestId !== loadSequence) return
    loadError.value = error.message || '租金时间分析加载失败'
  } finally {
    if (requestId === loadSequence) loading.value = false
  }
}

function exportExcel() {
  if (!canExportExcel.value || !startDate.value || !endDate.value) return
  const query = rentExportQuery(startDate.value, endDate.value, selectedSegments.value)
  if (props.buildingCode) {
    openDownload(cockpitBuildingPath(`/api/rent-analytics/export?${query}`, props.buildingCode), { includeScope: false })
  } else {
    openDownload(`/api/rent-analytics/export?${query}`)
  }
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('图表图片生成失败'))
    image.src = src
  })
}

function canvasToBlob(canvas) {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
}

function drawCanvasText(ctx, text, x, y, options = {}) {
  ctx.fillStyle = options.color || '#0f172a'
  ctx.font = `${options.weight || 400} ${options.size || 26}px "Microsoft YaHei", "PingFang SC", sans-serif`
  ctx.textAlign = options.align || 'left'
  ctx.fillText(String(text), x, y)
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function exportPng() {
  if (!chartPanel.value || pngExporting.value) return
  pngExporting.value = true
  try {
    const chartUrl = chartPanel.value.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#ffffff'
    })
    if (!chartUrl) throw new Error('图表尚未加载完成')
    const image = await loadImage(chartUrl)
    const cardStep = 112
    const headerHeight = 330 + summaryCards.value.length * cardStep
    const footerHeight = 128
    const fittedChart = fitRentExportImage(image.naturalWidth, image.naturalHeight, {
      minWidth: 1500,
      maxWidth: 2200,
      maxHeight: Math.min(1700, 3000 - headerHeight - footerHeight)
    })
    const width = Math.min(2200, Math.max(1500, fittedChart.width))
    const chartX = Math.round((width - fittedChart.width) / 2)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = headerHeight + fittedChart.height + footerHeight
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    drawCanvasText(ctx, '租金时间分析', 64, 68, { size: 38, weight: 700 })
    drawCanvasText(ctx, selectedRangeLabel.value, 64, 112, { size: 23, color: '#475569' })
    drawCanvasText(
      ctx,
      '有效平均日租 = 区间应计有效租金 ÷ 在租面积天数',
      64,
      154,
      { size: 21, color: '#64748b' }
    )
    let legendX = 64
    for (const segment of summaryCards.value) {
      ctx.strokeStyle = segment.color
      ctx.lineWidth = 7
      ctx.beginPath()
      ctx.moveTo(legendX, 198)
      ctx.lineTo(legendX + 42, 198)
      ctx.stroke()
      drawCanvasText(ctx, segment.label, legendX + 54, 207, { size: 20, weight: 600 })
      legendX += 230
    }
    drawCanvasText(ctx, '实线：历史', legendX, 207, { size: 18, color: '#64748b' })
    drawCanvasText(ctx, '虚线：已签预测', legendX + 150, 207, { size: 18, color: '#64748b' })

    let cardY = 256
    for (const card of summaryCards.value) {
      ctx.fillStyle = '#f8fafc'
      ctx.fillRect(64, cardY - 28, width - 128, 96)
      drawCanvasText(ctx, card.label, 88, cardY + 5, { size: 23, weight: 700, color: card.color })
      const metricText = card.history && card.forecast
        ? `历史 ${rateText(card.history.effectiveDailyRent)} / 已签预测 ${rateText(card.forecast.effectiveDailyRent)} 元/㎡/天`
        : `${rateText(card.total?.effectiveDailyRent)} 元/㎡/天`
      drawCanvasText(ctx, metricText, 360, cardY + 5, { size: 23, weight: 700 })
      if (card.history && card.forecast) {
        drawCanvasText(ctx, `历史：${metricRangeDescription(card.history)}`, 88, cardY + 43, {
          size: 17,
          color: '#64748b'
        })
        drawCanvasText(ctx, `已签预测：${metricRangeDescription(card.forecast)}`, Math.round(width / 2), cardY + 43, {
          size: 17,
          color: '#64748b'
        })
      } else {
        drawCanvasText(ctx, metricRangeDescription(card.total), 88, cardY + 43, {
          size: 17,
          color: '#64748b'
        })
      }
      cardY += cardStep
    }
    drawCanvasText(
      ctx,
      `数据覆盖 ${roundTo(analytics.value.coverage.coverageRate, 1)}%（${analytics.value.coverage.includedContracts}/${analytics.value.coverage.totalContracts} 份合同）`,
      64,
      headerHeight - 34,
      { size: 19, color: '#475569' }
    )

    ctx.drawImage(image, chartX, headerHeight, fittedChart.width, fittedChart.height)
    const footerY = Math.min(canvas.height - 64, headerHeight + fittedChart.height + 48)
    drawCanvasText(
      ctx,
      '说明：历史按实际有效期重算；未来仅含现有已签合同，不假设续租。月租按当月自然日摊分。',
      64,
      footerY,
      { size: 18, color: '#64748b' }
    )
    const blob = await canvasToBlob(canvas)
    if (!blob) throw new Error('图片文件生成失败')
    downloadBlob(blob, `有效平均日租_${startDate.value}_${endDate.value}.png`)
    notify('高清图片已生成')
  } catch (error) {
    notify(error.message || '图片导出失败', 'error')
  } finally {
    pngExporting.value = false
  }
}

onMounted(() => {
  window.addEventListener('pointerup', commitPendingZoom)
  window.addEventListener('pointercancel', commitPendingZoom)
  loadAnalytics()
})

onActivated(() => {
  if (analytics.value && Date.now() - lastLoadedAt > 5 * 60 * 1000) {
    loadAnalytics({ preserveRange: true })
  }
})

onDeactivated(cancelZoomPreview)

onBeforeUnmount(() => {
  loadSequence += 1
  clearPendingZoom()
  window.removeEventListener('pointerup', commitPendingZoom)
  window.removeEventListener('pointercancel', commitPendingZoom)
})
</script>

<template>
  <article class="card dashboard-card rent-timeline-card" data-testid="analytics-rent-timeline">
    <header class="rent-timeline-header">
      <div>
        <div class="section-label">{{ $t("租金时间分析") }}</div>
        <h3 class="card-title">{{ $t("有效平均日租与在租面积") }}</h3>
        <p>{{ $t("按合同实际有效期、阶梯租金、免租及调整计算；回款与物业费不计入。") }}</p>
      </div>
      <div class="rent-timeline-export-actions">
        <button
          class="btn btn-compact"
          type="button"
          data-testid="rent-timeline-export-png"
          :disabled="loading || !analytics || pngExporting"
          @click="exportPng"
        >
          {{ $t(pngExporting ? '生成中…' : '导出高清图片') }}
        </button>
        <button
          v-if="canExportExcel"
          class="btn btn-compact"
          type="button"
          data-testid="rent-timeline-export-excel"
          :disabled="loading || !analytics"
          @click="exportExcel"
        >{{ $t(" 导出Excel明细 ") }}</button>
        <button
          class="btn btn-compact"
          type="button"
          data-testid="rent-timeline-refresh"
          :disabled="loading"
          @click="loadAnalytics({ preserveRange: true })"
        >{{ $t(" 刷新 ") }}</button>
      </div>
    </header>

    <ChartSkeleton v-if="loading && !analytics" :height="520" />

    <div v-else-if="loadError && !analytics" class="rent-timeline-error" role="alert">
      <strong>{{ $t("租金时间分析暂时无法加载") }}</strong>
      <span>{{ $t(loadError) }}</span>
      <button class="btn btn-compact" type="button" @click="loadAnalytics()">{{ $t("重试") }}</button>
    </div>

    <template v-else-if="analytics">
      <div class="rent-timeline-controls">
        <fieldset class="rent-segment-picker" data-testid="rent-timeline-segments">
          <legend>{{ $t("分析范围（可多选）") }}</legend>
          <div class="rent-segment-buttons">
            <button
              v-for="segment in segmentOptions"
              :key="segment.key"
              type="button"
              class="rent-segment-button"
              :class="{ active: selectedSegments.includes(segment.key) }"
              :style="{ '--segment-color': segment.color }"
              :aria-pressed="selectedSegments.includes(segment.key)"
              :data-testid="`rent-segment-${segment.key}`"
              @click="toggleSegment(segment.key)"
            >
              <span aria-hidden="true"></span>
              {{ $t(segment.label) }}
            </button>
          </div>
        </fieldset>

        <div class="rent-range-workbench">
          <div class="rent-preset-buttons" :aria-label="$t(&quot;快捷时间范围&quot;)">
            <button
              v-for="preset in presetRanges"
              :key="preset.key"
              class="rent-preset-button"
              :class="{ active: activePreset === preset.key }"
              type="button"
              :disabled="preset.disabled"
              :aria-pressed="activePreset === preset.key"
              :title="$t(preset.disabledReason || '')"
              :data-testid="`rent-range-${preset.key}`"
              @click="applyPreset(preset)"
            >
              {{ $t(preset.label) }}
            </button>
            <small v-if="!futureDataAvailable" class="rent-control-hint">{{ $t(" 暂无今天之后的已签合同数据，未来12个月不可选。 ") }}</small>
          </div>
          <div class="rent-date-inputs">
            <label for="rent-range-start">
              <span>{{ $t("起始日") }}</span>
              <input
                id="rent-range-start"
                v-model="draftStartDate"
                class="input"
                type="date"
                :min="analytics.bounds.minDate"
                :max="analytics.bounds.maxDate"
                data-testid="rent-range-start"
                @change="commitDraftRange"
              />
            </label>
            <span class="rent-date-arrow" aria-hidden="true">→</span>
            <label for="rent-range-end">
              <span>{{ $t("结束日") }}</span>
              <input
                id="rent-range-end"
                v-model="draftEndDate"
                class="input"
                type="date"
                :min="analytics.bounds.minDate"
                :max="analytics.bounds.maxDate"
                data-testid="rent-range-end"
                @change="commitDraftRange"
              />
            </label>
          </div>
        </div>

        <fieldset class="rent-comparison-picker" aria-describedby="rent-comparison-note">
          <legend>{{ $t("对比曲线（") }}{{ $t(segmentByKey[primarySegmentKey]?.label) }}）</legend>
          <div>
            <button
              type="button"
              :class="{ active: comparisonMode === 'none' }"
              :aria-pressed="comparisonMode === 'none'"
              @click="comparisonMode = 'none'"
            >{{ $t(" 关闭 ") }}</button>
            <button
              type="button"
              :disabled="!previousComparisonAvailable"
              :class="{ active: comparisonMode === 'previous' }"
              :aria-pressed="comparisonMode === 'previous'"
              @click="comparisonMode = 'previous'"
            >{{ $t(" 前期 ") }}</button>
            <button
              type="button"
              :disabled="!yoyComparisonAvailable"
              :class="{ active: comparisonMode === 'yoy' }"
              :aria-pressed="comparisonMode === 'yoy'"
              @click="comparisonMode = 'yoy'"
            >{{ $t(" 同比 ") }}</button>
          </div>
          <small id="rent-comparison-note" class="rent-control-hint">{{ $t(comparisonUnavailableNote) }}</small>
        </fieldset>
      </div>

      <p v-if="rangeError" class="rent-range-error" role="alert">{{ $t(rangeError) }}</p>

      <div class="rent-timeline-kpis" data-testid="rent-timeline-kpis">
        <section
          v-for="card in summaryCards"
          :key="card.key"
          class="rent-kpi-card rent-kpi-primary"
          :style="{ '--segment-color': card.color }"
        >
          <span>{{ $t(card.label) }} · {{ $t(metricPhaseLabel(card)) }}</span>
          <template v-if="card.history && card.forecast">
            <div class="rent-kpi-split">
              <div>
                <small>{{ $t("历史") }}</small>
                <strong>{{ $t(rateText(card.history.effectiveDailyRent)) }}</strong>
              </div>
              <div>
                <small>{{ $t("已签预测") }}</small>
                <strong>{{ $t(rateText(card.forecast.effectiveDailyRent)) }}</strong>
              </div>
            </div>
            <em>{{ $t(" 预测较历史 ") }}{{ $t(signedPercent(
                card.history.effectiveDailyRent
                  ? ((card.forecast.effectiveDailyRent - card.history.effectiveDailyRent) / Math.abs(card.history.effectiveDailyRent)) * 100
                  : null
                )) }}
            </em>
            <div class="rent-kpi-detail-split">
              <small>{{ $t("历史：") }}{{ $t(metricRangeDescription(card.history)) }}</small>
              <small>{{ $t("已签预测：") }}{{ $t(metricRangeDescription(card.forecast)) }}</small>
            </div>
          </template>
          <template v-else>
            <strong>{{ $t(rateText(card.total?.effectiveDailyRent)) }}</strong>
            <em>{{ $t("元/㎡/天") }}</em>
            <small>{{ $t(metricRangeDescription(card.total)) }}</small>
          </template>
        </section>

        <section class="rent-kpi-card">
          <span>{{ $t("自动对比 · ") }}{{ $t(segmentByKey[primarySegmentKey]?.label) }}</span>
          <div class="rent-comparison-badges">
            <strong :class="previousComparisonAvailable ? comparisonTone(primaryComparison?.previousChange) : 'muted'">{{ $t(" 前期 ") }}{{ $t(comparisonPercentText(primaryComparison?.previousChange, previousCoverageComplete)) }}
            </strong>
            <strong :class="yoyComparisonAvailable ? comparisonTone(primaryComparison?.yoyChange) : 'muted'">{{ $t(" 同比 ") }}{{ $t(comparisonPercentText(primaryComparison?.yoyChange, yoyCoverageComplete)) }}
            </strong>
          </div>
          <small>{{ $t(comparisonUnavailableNote) }}</small>
        </section>

        <section class="rent-kpi-card rent-coverage-card">
          <span>{{ $t("全量合同数据覆盖率") }}</span>
          <strong>{{ $t(roundTo(analytics.coverage.coverageRate, 1)) }}%</strong>
          <em>{{ $t(analytics.coverage.includedContracts) }}/{{ $t(analytics.coverage.totalContracts) }}{{ $t(" 份合同") }}</em>
          <small v-if="analytics.coverage.excludedContracts">
            {{ $t(analytics.coverage.excludedContracts) }}{{ $t(" 份因数据不完整未计入 ") }}</small>
          <small v-else>{{ $t("当前合同均具备计算条件") }}</small>
        </section>
      </div>

      <div class="rent-chart-toolbar">
        <div class="rent-chart-legend" :aria-label="$t(&quot;图表说明&quot;)">
          <span><i class="solid"></i>{{ $t("历史") }}</span>
          <span><i class="dashed"></i>{{ $t("已签合同预测") }}</span>
          <span><i class="event"></i>{{ $t("关键变化") }}</span>
        </div>
        <label class="rent-event-picker">
          <span>{{ $t("定位关键变化") }}</span>
          <select :value="selectedWindow?.start || ''" @change="selectEventDate">
            <option value="">{{ $t("选择日期") }}</option>
            <option v-for="item in eventDateOptions" :key="item.date" :value="item.date">
              {{ $t(item.date) }} · {{ $t(item.count) }}{{ $t(" 项 ") }}</option>
          </select>
        </label>
        <span class="metric-pill info">{{ $t(granularityLabel) }}</span>
      </div>

      <EChartPanel
        ref="chartPanel"
        :option="chartOption"
        :minimum-pixel-ratio="minimumPixelRatio"
        :empty="!analytics.daily.length"
        :empty-text="$t(&quot;该范围暂无可计算的租金数据&quot;)"
        :height="compact ? 380 : 520"
        :aria-label="$t(`有效平均日租与在租面积，当前范围 ${selectedRangeLabel}`)"
        data-testid="rent-timeline-chart"
        @chart-click="handleChartClick"
        @datazoom="handleDataZoom"
        @pointerdown="beginZoomInteraction"
      />

      <div class="rent-timeline-status-row">
        <span aria-live="polite">{{ $t(selectedRangeLabel) }} · {{ $t(rangeStatus || '已按日级数据精确核对') }}</span>
        <span>{{ $t("拖动下方双手柄可改变时间窗口；按住 Ctrl 滚轮可缩放。") }}</span>
      </div>

      <aside
        v-if="selectedWindow"
        class="rent-driver-panel"
        data-testid="rent-timeline-drivers"
        aria-live="polite"
      >
        <header>
          <div>
            <span class="section-label">{{ $t("变化驱动") }}</span>
            <h4>{{ $t(selectedWindowLabel) }}{{ $t(" · 共 ") }}{{ $t(selectedWindowEvents.length) }}{{ $t(" 项") }}</h4>
          </div>
          <button class="btn btn-compact" type="button" @click="selectedWindow = null">{{ $t("收起") }}</button>
        </header>
        <div v-if="selectedWindowEvents.length" class="rent-driver-list">
          <article v-for="(item, index) in selectedWindowEvents" :key="`${item.date}-${item.contractId}-${index}`">
            <div>
              <span>{{ $t(eventTypeLabel(item.type)) }}</span>
              <strong>{{ $t(item.label) }}</strong>
              <small>
                {{ $t(item.contractNo || '未标注合同') }}
                <template v-if="item.tenantName"> · {{ $t(item.tenantName) }}</template>
              </small>
            </div>
            <div class="rent-driver-impact">
              <div v-if="item.displayImpacts.length" class="rent-driver-impact-groups">
                <section
                  v-for="impact in item.displayImpacts"
                  :key="impact.segmentKey"
                  class="rent-driver-impact-group"
                >
                  <strong>{{ $t(impact.label) }}</strong>
                  <span v-if="impact.rateImpact">{{ $t(" 日租影响 ") }}{{ $t(impact.rateImpact > 0 ? '+' : '') }}{{ $t(rateText(impact.rateImpact)) }}
                  </span>
                  <span v-if="impact.rentDelta">{{ $t(" 租金变化 ") }}{{ $t(impact.rentDelta > 0 ? '+' : '') }}{{ $t(formatCurrency(impact.rentDelta)) }}
                  </span>
                  <span v-if="impact.areaDelta">{{ $t(" 面积变化 ") }}{{ $t(impact.areaDelta > 0 ? '+' : '') }}{{ $t(formatArea(impact.areaDelta)) }} ㎡
                  </span>
                </section>
              </div>
              <small v-else>{{ $t("所选分区净影响为 0") }}</small>
              <button
                v-if="item.contractId"
                class="btn btn-compact"
                type="button"
                @click="openDriverContract(item)"
              >{{ $t(" 打开合同 ") }}</button>
            </div>
          </article>
        </div>
        <p v-else class="rent-driver-empty">{{ $t(" 该窗口没有单一合同事件。轻微波动可能来自月租按当月自然日摊分，或多个合同组合变化，并不等于发生调租。 ") }}</p>
      </aside>

      <details v-if="analytics.coverage.excludedContracts" class="rent-exclusion-details">
        <summary>{{ $t(" 数据覆盖说明 ") }}<strong>{{ $t(analytics.coverage.excludedContracts) }}{{ $t(" 份合同未计入") }}</strong>
        </summary>
        <div class="rent-exclusion-list">
          <article v-for="(item, index) in analytics.coverage.excluded" :key="item.contract_id || item.contract_no || index">
            <strong>{{ item.contract_no || `记录 ${index + 1}` }}</strong>
            <span>{{ item.tenant_name || item.tenant || '未标注客户' }}</span>
            <small>
              {{ Array.isArray(item.reasons) && item.reasons.length
                ? item.reasons.join('；')
                : (item.reason || '缺少计算所需信息') }}
            </small>
          </article>
        </div>
      </details>

      <footer class="rent-method-note">
        <strong>{{ $t("口径说明") }}</strong>
        <span>{{ $t("区间应计有效租金 ÷ 在租面积天数；空置面积不进入分母，免租但仍占用的面积正常进入分母。") }}</span>
        <span>{{ $t("未来仅展示现有已签合同，到期后自然退出；不假设续租、重新出租或市场租金。") }}</span>
      </footer>
    </template>
  </article>
</template>
