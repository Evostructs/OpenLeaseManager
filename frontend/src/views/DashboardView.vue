<script setup>
import { computed, ref } from 'vue'
import { formatArea, formatCurrency, formatDate, formatNumber } from '../lib/format'
import { openDownload } from '../lib/api'
import { buildBuildingOperationsProfileView } from '../lib/buildingOperationsProfile'
import MetricTile from '../components/MetricTile.vue'
import AnimatedNumber from '../components/AnimatedNumber.vue'

const props = defineProps({
  stats: {
    type: Object,
    default: () => ({})
  },
  operations: {
    type: Object,
    default: () => ({})
  },
  user: {
    type: Object,
    default: null
  },
  snapshot: {
    type: Object,
    default: () => ({})
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
  },
  availableTabs: {
    type: Array,
    default: () => []
  },
  loading: Boolean,
  operationsLoading: Boolean
})

const emit = defineEmits(['open-contract', 'open-customer', 'select-tab'])

const metrics = computed(() => props.operations.metrics || {})
const operations = computed(() => props.operations || {})
const riskSummary = computed(() => operations.value.risks?.summary || { total: 0, by_severity: { high: 0 } })
const receivables = computed(() => props.stats.receivables || {})
const queueCounts = computed(() => operations.value.queue_counts || {})
const queues = computed(() => operations.value.queues || {})
const currentMonth = computed(() => operations.value.current_month || {})
const vacancy = computed(() => operations.value.vacancy || {})
// Fail closed while the scope is changing or a request fails.  Only a live
// detail adapter with verifiable operating facts may opt into the business dashboard.
const detailListsAvailable = computed(() => (
  operations.value?.projection?.detail_lists_available === true
  && operations.value?.projection?.detail_facts_available !== false
))
const detailFactsUnavailableReason = computed(() => (
  operations.value?.projection?.detail_facts_unavailable_reason
  || '当前明细源尚无可核验的经营事实'
))
const isLiveDetailProjection = computed(() => (
  operations.value?.projection?.source === 'live_detail'
))
const dashboardDataModeLabel = computed(() => (
  isLiveDetailProjection.value ? '明细数据状态' : '已发布经营快照'
))
const operationsProfileView = computed(() => (
  buildBuildingOperationsProfileView(props.operationsProfile)
))
const isSimulatedData = computed(() => (
  String(
    props.snapshot?.data_classification
    || props.snapshot?.presentation?.data_classification
    || ''
  ).trim().toLowerCase() === 'simulated'
))
const snapshotMetricSourceLabel = computed(() => (
  isLiveDetailProjection.value
    ? '来自当前明细适配器的可核验经营事实'
    : operationsProfileView.value.primaryGroup?.name
    ? `来自“${operationsProfileView.value.primaryGroup.name}”已发布经营快照`
    : '来自当前已发布经营快照'
))

function snapshotMetricValue(metric) {
  if (metric?.value === null || metric?.value === undefined) return '--'
  const value = Number(metric.value)
  if (!Number.isFinite(value)) return String(metric.value)
  if (metric.unit === 'percent') return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
  if (metric.unit === 'CNY') return `${formatCurrency(value)} 元`
  if (metric.unit === 'sqm') return `${formatArea(value)} ㎡`
  if (metric.unit === 'CNY/sqm/day') return `${formatNumber(value)} 元/㎡·天`
  if (metric.unit === 'building') return `${formatNumber(value)} 栋`
  if (metric.unit === 'contract') return `${formatNumber(value)} 份`
  if (metric.unit === 'item') return `${formatNumber(value)} 项`
  return formatNumber(value)
}

const snapshotFacts = computed(() => {
  const facts = props.snapshot?.metrics || {}
  return [
    'occupancy.area_rate',
    'area.rentable_sqm',
    'area.leased_sqm',
    'collection.rate_month',
    'arrears.balance',
    'expiry.contract_count_90d'
  ].map((code) => {
    const metric = facts[code] || {}
    return {
      code,
      label: metric.name || code,
      value: snapshotMetricValue(metric),
      available: metric.value !== null && metric.value !== undefined,
      reason: metric.unavailable_reason || '当前快照未提供该指标'
    }
  })
})

const todayActions = computed(() => (operations.value.todos || []).slice(0, 3))
const todayActionCount = computed(() => Number((operations.value.todos || []).length || 0))
const selectedTrafficTone = ref('')

function metricNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number : 0
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, metricNumber(value)))
}

function countTone(value, warningTone = 'warning') {
  return metricNumber(value) > 0 ? warningTone : 'success'
}

function groupTone(items) {
  if (items.some((item) => item.tone === 'danger' || item.tone === 'error')) return 'danger'
  if (items.some((item) => item.tone === 'warning')) return 'warning'
  if (items.some((item) => item.tone === 'info')) return 'info'
  return 'success'
}

const judgementText = computed(() => {
  const urgent = mostUrgentLane.value
  const hasPressure = trafficOverview.value.some((item) => item.key !== 'green' && item.value > 0)
  if (!hasPressure) return '今日暂无待处理风险。'
  return `最紧急：${urgent?.label || '经营'}。先处理 Top 3 急项。`
})

const judgementChips = computed(() => {
  const m = metrics.value
  const overdueCount = metricNumber(operations.value.overdue?.contract_count || queueCounts.value.overdue_contracts)
  const chips = [
    { key: 'todos', label: '优先动作', value: `${formatNumber(todayActionCount.value)} 项`, tone: countTone(todayActionCount.value, 'warning') },
    { key: 'overdue', label: '历史逾期', value: `${formatNumber(overdueCount)} 份`, tone: countTone(overdueCount, 'danger') },
    { key: 'expiring', label: '90天到期', value: `${formatNumber(m.expiring_contract_count_90 || 0)} 份`, tone: countTone(m.expiring_contract_count_90, 'warning') },
    { key: 'followups', label: '待跟进', value: `${formatNumber(m.due_follow_up_customer_count || 0)} 个`, tone: countTone(m.due_follow_up_customer_count, 'warning') },
    { key: 'attachments', label: '附件待补', value: `${formatNumber(m.missing_contract_body_count || 0)} 份`, tone: countTone(m.missing_contract_body_count, 'warning') }
  ]
  const pressure = chips.filter((item) => item.tone !== 'success').slice(0, 3)
  return pressure.length ? pressure : chips.slice(0, 1)
})

const healthMetrics = computed(() => {
  const m = metrics.value
  const receivedRate = metricNumber(m.received_rate ?? receivables.value.received_rate)
  const currentReceivable = metricNumber(m.current_month_receivable)
  const overdueBalance = metricNumber(m.overdue_balance)
  const unpaidBalance = metricNumber(m.current_month_unpaid_balance)
  const occupancyRate = metricNumber(vacancy.value.occupancy_rate)
  const expiringCount = metricNumber(m.expiring_contract_count_90)
  const followUpCount = metricNumber(m.due_follow_up_customer_count)
  const unpaidDepositAmount = metricNumber(m.unpaid_deposit_amount)
  const missingBodyCount = metricNumber(m.missing_contract_body_count)
  const highRiskCount = metricNumber(m.high_risk_count ?? riskSummary.value.by_severity?.high)
  return [
    {
      key: 'received_rate',
      label: '本月回款率',
      value: `${formatNumber(receivedRate)}%`,
      helper: `${formatCurrency(m.current_month_received || 0)} / ${formatCurrency(m.current_month_receivable || 0)}`,
      progress: currentReceivable > 0 ? clampPercent(receivedRate) : null,
      tone: currentReceivable <= 0 || receivedRate >= 90 ? 'success' : receivedRate >= 70 ? 'warning' : 'danger'
    },
    {
      key: 'overdue_balance',
      label: '历史逾期',
      value: formatCurrency(overdueBalance),
      helper: `${formatNumber(operations.value.overdue?.contract_count || 0)} 份合同有历史逾期`,
      tone: overdueBalance > 0 ? 'danger' : 'success'
    },
    {
      key: 'current_month_unpaid_balance',
      label: '本月未收',
      value: formatCurrency(unpaidBalance),
      helper: `其中本月逾期 ${formatCurrency(m.current_month_overdue_balance || 0)}`,
      tone: unpaidBalance > 0 ? 'warning' : 'success'
    },
    {
      key: 'vacancy',
      label: '空置房间',
      value: `${formatNumber(m.vacancy_room_count || 0)} 间`,
      helper: `${formatArea(m.vacancy_area || 0)} ㎡空置 / 入驻率 ${formatNumber(occupancyRate)}%`,
      progress: clampPercent(occupancyRate),
      tone: metricNumber(m.vacancy_room_count) > 0 ? 'info' : 'success'
    },
    {
      key: 'expiring_contract_count_90',
      label: '90天到期',
      value: formatNumber(expiringCount),
      helper: '影响续租、退租和招商安排',
      tone: expiringCount > 0 ? 'warning' : 'success'
    },
    {
      key: 'due_follow_up_customer_count',
      label: '待跟进客户',
      value: formatNumber(followUpCount),
      helper: `${formatNumber(operations.value.follow_ups?.follow_up_count || followUpCount)} 条跟进记录`,
      tone: followUpCount > 0 ? 'warning' : 'success'
    },
    {
      key: 'unpaid_deposit_amount',
      label: '未收押金',
      value: formatCurrency(unpaidDepositAmount),
      helper: `${formatNumber(m.unpaid_deposit_count || 0)} 笔未收/不足`,
      tone: unpaidDepositAmount > 0 ? 'warning' : 'success'
    },
    {
      key: 'missing_contract_body_count',
      label: '附件待补',
      value: formatNumber(missingBodyCount),
      helper: '合同正文未归档',
      tone: missingBodyCount > 0 ? 'warning' : 'success'
    },
    {
      key: 'high_risk_count',
      label: '高风险数量',
      value: formatNumber(highRiskCount),
      helper: `${formatNumber(riskSummary.value.total || m.risk_count || 0)} 个风险项`,
      tone: highRiskCount > 0 ? 'danger' : 'success'
    }
  ]
})

const healthSignalGroups = computed(() => {
  const byKey = Object.fromEntries(healthMetrics.value.map((item) => [item.key, item]))
  const groups = [
    {
      key: 'cash',
      label: '回款健康',
      headline: '本月 / 历史逾期',
      keys: ['received_rate', 'current_month_unpaid_balance', 'overdue_balance']
    },
    {
      key: 'lease',
      label: '履约节奏',
      headline: '空置 / 到期',
      keys: ['vacancy', 'expiring_contract_count_90', 'due_follow_up_customer_count']
    },
    {
      key: 'materials',
      label: '资料与押金',
      headline: '押金 / 资料',
      keys: ['unpaid_deposit_amount', 'missing_contract_body_count', 'high_risk_count']
    }
  ]

  return groups.map((group) => {
    const items = group.keys.map((key) => byKey[key]).filter(Boolean)
    return { ...group, items, tone: groupTone(items) }
  })
})

const priorityLanes = computed(() => {
  const m = metrics.value
  const q = queues.value
  const counts = queueCounts.value
  const current = currentMonth.value
  const overdueRows = q.overdue_contracts || []
  const followUpRows = q.due_follow_up_customers || []
  const expiryRows = q.expiring_contracts || []
  const depositRows = q.unpaid_deposits || []
  const missingRows = q.missing_contract_body || []
  const overdueBalance = metricNumber(m.overdue_balance)
  const unpaidBalance = metricNumber(m.current_month_unpaid_balance ?? current.unpaid_balance ?? receivables.value.total_unpaid_balance)
  const receivedRate = metricNumber(m.received_rate ?? current.received_rate ?? receivables.value.received_rate)
  const followUpRecords = metricNumber(operations.value.follow_ups?.follow_up_count || m.due_follow_up_customer_count)
  const materialCount = metricNumber(counts.unpaid_deposits) + metricNumber(counts.missing_contract_body)
  const highRiskCount = metricNumber(m.high_risk_count ?? riskSummary.value.by_severity?.high)

  return [
    {
      key: 'payment',
      testId: 'today-signal-lane-payment',
      label: '收款',
      title: overdueBalance > 0 ? formatCurrency(overdueBalance) : `回款率 ${formatNumber(receivedRate)}%`,
      helper: `历史逾期 ${formatNumber(counts.overdue_contracts || overdueRows.length)} 份 · 本月未收 ${formatCurrency(unpaidBalance)}`,
      tone: overdueBalance > 0 ? 'danger' : unpaidBalance > 0 ? 'warning' : 'success',
      action: props.user?.role === 'viewer' ? '看合同' : '看应收',
      tab: props.user?.role === 'viewer' ? 'contracts' : 'receivables'
    },
    {
      key: 'expiry',
      testId: 'today-signal-lane-expiry',
      label: '到期',
      title: `${formatNumber(counts.expiring_contracts || expiryRows.length)} 份合同`,
      helper: expiryRows[0]?.days_left != null ? `最近 ${formatNumber(expiryRows[0].days_left)} 天到期` : '续租、退租和招商排期',
      tone: expiryRows.some((item) => item.severity === 'danger') ? 'danger' : countTone(counts.expiring_contracts || expiryRows.length, 'warning'),
      action: '排合同',
      tab: 'contracts'
    },
    {
      key: 'followup',
      testId: 'today-signal-lane-followup',
      label: '跟进',
      title: `${formatNumber(counts.due_follow_up_customers || followUpRows.length)} 个客户`,
      helper: `${formatNumber(followUpRecords)} 条跟进记录`,
      tone: followUpRows.some((item) => item.severity === 'danger') ? 'danger' : countTone(counts.due_follow_up_customers || followUpRows.length, 'warning'),
      action: '看客户',
      tab: 'customers'
    },
    {
      key: 'materials',
      testId: 'today-signal-lane-materials',
      label: '资料',
      title: `${formatNumber(materialCount)} 项缺口`,
      helper: `押金 ${formatNumber(counts.unpaid_deposits || depositRows.length)} 笔 · 附件 ${formatNumber(counts.missing_contract_body || missingRows.length)} 份 · 高风险 ${formatNumber(highRiskCount)} 项`,
      tone: highRiskCount > 0 ? 'danger' : materialCount > 0 ? 'warning' : 'success',
      action: '看风险',
      tab: 'risks'
    }
  ]
})

const businessSignalCounts = computed(() => {
  const m = metrics.value
  const counts = queueCounts.value
  const redCount = Math.max(
    metricNumber(counts.overdue_contracts),
    metricNumber(m.high_risk_count ?? riskSummary.value.by_severity?.high)
  )
  const yellowCount =
    metricNumber(counts.expiring_contracts) +
    metricNumber(counts.due_follow_up_customers) +
    metricNumber(counts.unpaid_deposits) +
    metricNumber(counts.missing_contract_body)
  const activeCount = metricNumber(props.stats.active_count || m.active_contract_count)
  return {
    red: redCount,
    yellow: yellowCount,
    green: Math.max(activeCount - redCount - yellowCount, 0),
    active: activeCount
  }
})

const trafficOverview = computed(() => {
  const counts = businessSignalCounts.value
  return [
    { key: 'red', label: '红', value: counts.red, helper: '优先处理', tone: 'danger' },
    { key: 'yellow', label: '黄', value: counts.yellow, helper: '需要提醒', tone: 'warning' },
    { key: 'green', label: '绿', value: counts.green, helper: '正常推进', tone: 'success' }
  ]
})

const normalProgressLane = computed(() => ({
  key: 'normal-progress',
  testId: 'today-signal-lane-normal',
  label: '正常',
  title: `${formatNumber(businessSignalCounts.value.green)} 份合同`,
  helper: businessSignalCounts.value.active > 0 ? '未进入红黄灯，正常履约推进' : '暂无可判断合同',
  tone: 'success',
  action: '看合同',
  tab: 'contracts'
}))

const filteredPriorityLanes = computed(() => {
  const tone = selectedTrafficTone.value
  if (!tone) return priorityLanes.value
  if (tone === 'green') return businessSignalCounts.value.green > 0 ? [normalProgressLane.value] : []
  return priorityLanes.value.filter((lane) => laneMatchesTrafficTone(lane, tone))
})

const mostUrgentLane = computed(() => {
  const lanes = priorityLanes.value
  return lanes.find((lane) => lane.tone === 'danger' || lane.tone === 'error') || lanes.find((lane) => lane.tone === 'warning') || lanes[0]
})

const selectedTrafficLabel = computed(() => {
  return trafficOverview.value.find((item) => item.key === selectedTrafficTone.value)?.label || ''
})

function laneMatchesTrafficTone(lane, tone) {
  if (tone === 'green') return lane.tone === 'success'
  if (tone === 'yellow') return lane.tone === 'warning'
  if (tone === 'red') return lane.tone === 'danger' || lane.tone === 'error'
  return true
}

function selectTrafficTone(tone) {
  selectedTrafficTone.value = selectedTrafficTone.value === tone ? '' : tone
}

function actionTone(action) {
  if (action.severity === 'danger') return 'error'
  if (action.severity === 'warning') return 'warning'
  return 'info'
}

function actionTitle(action) {
  return action.title || action.tenant_name || action.customer_name || action.meta?.tenant_name || action.meta?.customer_name || action.contract_no || action.meta?.contract_no || action.label || '--'
}

function actionMeta(action) {
  const meta = action.meta || action || {}
  switch (action.type) {
    case 'overdue_contract':
      return `${meta.contract_no || '--'} · ${meta.room_no || '--'} · 逾期 ${meta.max_aging_days || 0} 天`
    case 'expiring_contract':
      return `${meta.contract_no || '--'} · ${meta.room_no || '--'} · ${formatDate(action.due_date || meta.end_date)} 到期`
    case 'due_follow_up_customer':
      return `${meta.owner || '--'} · ${formatDate(action.due_date || meta.next_follow_up_date)} · ${meta.next_action || '待跟进'}`
    case 'unpaid_deposit':
      return `${meta.contract_no || '--'} · ${meta.deposit_type || '押金'} · 应收 ${formatDate(action.due_date || meta.due_date)}`
    case 'missing_contract_body':
      return `${meta.contract_no || '--'} · ${meta.room_no || '--'} · 缺 ${meta.missing_category || '合同正文'}`
    default:
      return action.label || '--'
  }
}

function actionValue(action) {
  const amount = Number(action.amount || action.overdue_balance || action.unpaid_amount || 0)
  if (amount > 0) return formatCurrency(amount)
  if (action.days_left != null) return `${action.days_left} 天`
  if (action.due_date) return formatDate(action.due_date)
  return ''
}

function actionCta(action) {
  if (action.type === 'overdue_contract') return props.user?.role === 'viewer' ? '查看合同' : '查看应收'
  if (action.type === 'expiring_contract') return '排续租'
  if (action.type === 'due_follow_up_customer') return '客户跟进'
  if (action.type === 'unpaid_deposit') return '确认押金'
  if (action.type === 'missing_contract_body') return '补附件'
  if (action.contract_id) return '打开合同'
  return '查看队列'
}

function actionContextCta(action) {
  if (action.target_type === 'customer' && action.customer_id) return '客户详情'
  if (action.contract_id) return '合同详情'
  if (action.room_no) return '房源台账'
  return '待办队列'
}

function hasActionNavigation(action) {
  return ['overdue_contract', 'expiring_contract', 'due_follow_up_customer', 'unpaid_deposit', 'missing_contract_body'].includes(action.type)
}

function openAction(action) {
  if (action.target_type === 'customer' && action.customer_id) {
    emit('open-customer', action.customer_id)
    return
  }
  if (action.contract_id) {
    emit('open-contract', action.contract_id)
    return
  }
  if (action.room_no) {
    emit('select-tab', 'rooms')
    return
  }
  emit('select-tab', 'operations')
}

function navigateAction(action) {
  if (action.type === 'overdue_contract') {
    emit('select-tab', props.user?.role === 'viewer' ? 'contracts' : 'receivables')
    return
  }
  if (action.type === 'due_follow_up_customer') {
    emit('select-tab', 'customers')
    return
  }
  if (action.type === 'missing_contract_body') {
    emit('select-tab', 'risks')
    return
  }
  if (action.contract_id) {
    emit('select-tab', 'contracts')
    return
  }
  if (action.room_no) {
    emit('select-tab', 'rooms')
    return
  }
  emit('select-tab', 'operations')
}

function navigateLane(lane) {
  emit('select-tab', lane.tab || 'operations')
}

function exportOperationsDailyReport() {
  openDownload('/api/reports/operations-daily')
}
</script>

<template>
  <section class="content dashboard-page dashboard-summary-page" data-testid="dashboard-page">
    <article v-if="detailListsAvailable" class="card dashboard-summary-card dashboard-actions-card" data-testid="dashboard-actions">
      <div class="card-header compact">
        <div>
          <div class="section-label">今日指挥板</div>
          <h3 class="card-title">今日先看这三件事</h3>
        </div>
        <div class="pill-group">
          <button class="btn btn-compact" data-testid="dashboard-export-daily-report" @click="exportOperationsDailyReport">导出经营日报</button>
          <span v-if="loading || operationsLoading" class="metric-pill info">读取中</span>
          <span class="metric-pill info">月份 {{ operations.month || '--' }}</span>
          <span class="metric-pill warning">Top 3 / {{ operations.todos?.length || 0 }}</span>
        </div>
      </div>

      <div class="today-health-overview" data-testid="today-health-overview">
        <button
          v-for="item in trafficOverview"
          :key="item.key"
          type="button"
          class="today-health-card"
          :class="[item.tone, { 'is-active': selectedTrafficTone === item.key }]"
          :data-testid="`today-health-${item.key}`"
          :aria-pressed="selectedTrafficTone === item.key"
          @click="selectTrafficTone(item.key)"
        >
          <span>{{ item.label }}</span>
          <AnimatedNumber :value="formatNumber(item.value)" />
          <small>{{ item.helper }}</small>
        </button>
      </div>

      <div class="dashboard-judgement" data-testid="dashboard-judgement">
        <strong>{{ judgementText }}</strong>
        <div class="dashboard-judgement-chips">
          <span v-for="item in judgementChips" :key="item.key" class="dashboard-chip" :class="item.tone">
            <b>{{ item.value }}</b>
            <small>{{ item.label }}</small>
          </span>
        </div>
      </div>

      <div class="dashboard-priority-lanes today-signal-lanes" data-testid="today-signal-lanes">
        <section
          v-for="lane in filteredPriorityLanes"
          :key="lane.key"
          class="dashboard-priority-lane"
          :class="lane.tone"
          :data-testid="lane.testId"
        >
          <div class="dashboard-lane-head">
            <span>{{ lane.label }}</span>
            <strong>{{ lane.title }}</strong>
            <small>{{ lane.helper }}</small>
          </div>
          <button type="button" class="dashboard-lane-nav" @click="navigateLane(lane)">
            {{ lane.action }}
          </button>
        </section>
        <div v-if="selectedTrafficTone && !filteredPriorityLanes.length" class="compact-empty">
          暂无{{ selectedTrafficLabel }}色经营信号
        </div>
      </div>

      <div class="dashboard-action-list today-top-actions" data-testid="today-top-actions">
        <div class="today-top-actions-head">
          <div>
            <span>Top 3 急项</span>
            <strong>先处理最影响今日节奏的事项</strong>
          </div>
          <button type="button" class="btn btn-compact" data-testid="today-open-operations-queue" @click="emit('select-tab', 'operations')">
            查看完整待办队列
          </button>
        </div>
        <div
          v-for="(action, index) in todayActions"
          :key="`${index}-${action.type}-${action.contract_id || action.customer_id || action.room_no || action.title}`"
          class="dashboard-action-item"
          :data-testid="`dashboard-action-item-${index}`"
        >
          <button
            type="button"
            class="dashboard-action-row"
            :data-testid="`dashboard-action-row-${index}`"
            @click="openAction(action)"
          >
            <span class="dashboard-action-rank" :class="actionTone(action)">{{ index + 1 }}</span>
            <span class="dashboard-action-main">
              <span class="dashboard-action-title-line">
                <span class="badge" :class="actionTone(action)">{{ action.label || '待办' }}</span>
                <strong>{{ actionTitle(action) }}</strong>
              </span>
              <small>{{ actionMeta(action) }}</small>
            </span>
            <span class="dashboard-action-side">
              <b>{{ actionValue(action) || '查看' }}</b>
              <small>{{ actionContextCta(action) }}</small>
            </span>
          </button>
          <button
            v-if="hasActionNavigation(action)"
            type="button"
            class="dashboard-action-nav"
            :data-testid="`dashboard-action-nav-${index}`"
            @click="navigateAction(action)"
          >
            {{ actionCta(action) }}
          </button>
        </div>
        <div v-if="!todayActions.length" class="compact-empty">
          今日暂无需要优先处理的经营动作
        </div>
      </div>
    </article>

    <article v-else class="card dashboard-summary-card dashboard-snapshot-card" data-testid="snapshot-dashboard-summary">
      <div class="card-header compact">
        <div>
          <div class="section-label">{{ dashboardDataModeLabel }}</div>
          <h3 class="card-title">{{ operationsProfileView.positioning || '当前楼宇按整楼口径展示' }}</h3>
        </div>
        <div class="dashboard-snapshot-meta">
          <span
            v-if="isSimulatedData"
            class="metric-pill warning"
            data-testid="snapshot-dashboard-simulated-data"
          >模拟数据</span>
          <span class="metric-pill info">统计日 {{ snapshot.as_of_date || '--' }}</span>
        </div>
      </div>
      <p class="compact-empty">
        {{ detailFactsUnavailableReason }}，因此不展示空白明细模块，也不把未知数据当作 0。
      </p>
      <section
        v-if="operationsProfileView.configured"
        class="building-profile-brief"
        data-testid="building-operations-profile"
        :data-structure="operationsProfileView.structureType"
      >
        <div class="building-profile-identity">
          <span>{{ isSimulatedData ? '模拟运营形态' : '楼宇运营形态' }}</span>
          <strong>{{ operationsProfileView.structureLabel }}</strong>
          <small>当前数据粒度：{{ operationsProfileView.granularityLabel }}</small>
          <p>{{ operationsProfileView.structureDetail }}</p>
        </div>
        <div class="building-profile-focus">
          <span>当前运营重点</span>
          <div>
            <b v-for="item in operationsProfileView.focus" :key="item">{{ item }}</b>
          </div>
        </div>
        <div class="building-profile-groups">
          <article
            v-for="group in operationsProfileView.groups"
            :key="group.code"
            :class="{ unavailable: !group.available }"
            :data-testid="`building-profile-group-${group.code}`"
          >
            <header>
              <span>{{ group.name }}</span>
              <strong>{{ group.statusLabel }}</strong>
            </header>
            <p>{{ group.note }}</p>
            <small>范围：{{ group.zoneLabel }}</small>
          </article>
        </div>
        <p
          v-if="operationsProfileView.unavailableGroupSafety"
          class="building-profile-safety"
          data-testid="building-profile-safety"
        >{{ operationsProfileView.unavailableGroupSafety }}</p>
      </section>
      <div class="health-signal-items" data-testid="snapshot-dashboard-metrics">
        <MetricTile
          v-for="item in snapshotFacts"
          :key="item.code"
          :label="item.label"
          :value="item.value"
          :helper="item.available ? snapshotMetricSourceLabel : item.reason"
          :tone="item.available ? 'info' : 'warning'"
        />
      </div>
      <div class="pill-group">
        <button
          v-if="availableTabs.includes('evidence')"
          type="button"
          class="btn btn-compact"
          data-testid="snapshot-dashboard-open-evidence"
          @click="emit('select-tab', 'evidence')"
        >查看数据依据</button>
        <button
          v-if="availableTabs.includes('exchange')"
          type="button"
          class="btn btn-compact"
          data-testid="snapshot-dashboard-open-exchange"
          @click="emit('select-tab', 'exchange')"
        >进入文件交换</button>
      </div>
    </article>

    <details v-if="detailListsAvailable" class="demo-secondary-details dashboard-health-details">
      <summary>
        <span>核心指标</span>
        <strong>经营健康度</strong>
      </summary>
    <article class="card dashboard-summary-card" data-testid="dashboard-health">
      <div class="card-header compact">
        <div>
          <div class="section-label">核心指标</div>
          <h3 class="card-title">经营健康度</h3>
        </div>
      </div>

      <div class="health-signal-band" data-testid="dashboard-signal-band">
        <div v-for="group in healthSignalGroups" :key="group.key" class="health-signal-group" :class="group.tone">
          <div class="health-signal-label">
            <span>{{ group.label }}</span>
            <strong>{{ group.headline }}</strong>
          </div>
          <div class="health-signal-items">
            <MetricTile
              v-for="item in group.items"
              :key="item.key"
              :label="item.label"
              :value="item.value"
              :helper="item.helper"
              :tone="item.tone"
            >
              <div v-if="item.progress != null" class="health-meter" :class="item.tone">
                <i :style="{ width: `${item.progress}%` }"></i>
              </div>
            </MetricTile>
          </div>
        </div>
      </div>
    </article>
    </details>

  </section>
</template>
