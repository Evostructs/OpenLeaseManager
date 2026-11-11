<script setup>
import { computed, onMounted, ref } from 'vue'
import ExecutionActions from '../components/ExecutionActions.vue'
import FinanceOperations from '../components/FinanceOperations.vue'
import BaseModal from '../components/BaseModal.vue'
import MetricSkeleton from '../components/MetricSkeleton.vue'
import TableSkeleton from '../components/TableSkeleton.vue'
import { cachedApi } from '../lib/prefetchCache'
import { formatArea, formatCurrency, formatDate, formatNumber } from '../lib/format'

const props = defineProps({
  operations: {
    type: Object,
    default: () => ({})
  },
  user: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['notify', 'open-contract', 'open-customer', 'select-tab'])

const operationsData = ref(props.operations || {})
const operationsV2Data = ref(null)
const operationsLoading = ref(false)
const operationsV2Loading = ref(false)
const trendRows = ref([])
const trendLoading = ref(false)
const activeQueueFilter = ref('all')
const operationTargetItem = ref(null)

const operations = computed(() => operationsData.value || {})
const metrics = computed(() => operations.value.metrics || {})
const operationTrendRows = computed(() => trendRows.value)
const hasOperationsSnapshot = computed(() => Boolean(operations.value.month || Object.keys(metrics.value).length || totalQueueCount.value))
const operationsInitialLoading = computed(() => operationsLoading.value && !hasOperationsSnapshot.value)
const operationsRefreshing = computed(() => operationsLoading.value && hasOperationsSnapshot.value)
const trendInitialLoading = computed(() => trendLoading.value && !operationTrendRows.value.length)
const trendRefreshing = computed(() => trendLoading.value && operationTrendRows.value.length > 0)

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

const operationsV2LaneMap = computed(() => {
  const lanes = operationsV2Data.value?.lanes
  if (!Array.isArray(lanes)) return {}
  return Object.fromEntries(lanes.filter((lane) => lane?.key).map((lane) => [lane.key, lane]))
})

function operationV2LaneCount(laneKey, fallbackCount) {
  const lane = operationsV2LaneMap.value[laneKey]
  return lane && Object.prototype.hasOwnProperty.call(lane, 'count')
    ? metricNumber(lane.count)
    : metricNumber(fallbackCount)
}

function operationV2LaneTone(laneKey, fallbackTone = 'success') {
  return operationsV2LaneMap.value[laneKey]?.tone || fallbackTone
}

function formatOperationsV2Metric(metric) {
  const rawValue = metric.raw_value ?? metric.value ?? 0
  const unit = metric.unit || ''
  const value = unit === 'CNY'
    ? formatCurrency(rawValue)
    : unit === '%'
      ? `${formatNumber(rawValue)}%`
      : unit
        ? `${formatNumber(rawValue)} ${unit}`
        : String(metric.value ?? formatNumber(rawValue))
  return {
    key: metric.key,
    label: metric.label,
    value,
    helper: metric.helper || '',
    progress: metric.progress,
    tone: metric.tone || 'info'
  }
}

const totalQueueCount = computed(() => {
  const counts = operations.value.queue_counts || {}
  return Object.values(counts).reduce((sum, value) => sum + metricNumber(value), 0)
})

const operationQueueGroups = computed(() => {
  const queues = operations.value.queues || {}
  const queueCounts = operations.value.queue_counts || {}
  const m = metrics.value
  const buildGroup = (key, label, helper, tone, items) => {
    const rows = items || []
    const totalCount = Number(queueCounts[key] ?? rows.length) || 0
    return {
      key,
      label,
      helper,
      tone,
      items: rows,
      totalCount
    }
  }
  return [
    buildGroup('overdue_contracts', '收款待核对', `合计 ${formatCurrency(m.overdue_balance || 0)}`, 'danger', queues.overdue_contracts),
    buildGroup('due_follow_up_customers', '客户待跟进', `${formatNumber(operations.value.follow_ups?.follow_up_count || m.due_follow_up_customer_count || 0)} 条跟进`, 'warning', queues.due_follow_up_customers),
    buildGroup('expiring_contracts', '到期安排', '续租、退租和招商排期', 'warning', queues.expiring_contracts),
    buildGroup('unpaid_deposits', '押金缺口', `${formatNumber(m.unpaid_deposit_count || 0)} 笔未收/不足`, 'warning', queues.unpaid_deposits),
    buildGroup('missing_contract_body', '资料待补齐', '合同正文未归档', 'info', queues.missing_contract_body)
  ]
})

const operationQueueFilters = computed(() => [
  {
    key: 'all',
    label: '全部待办',
    shortLabel: '全部',
    totalCount: totalQueueCount.value,
    tone: countTone(totalQueueCount.value, 'warning')
  },
  ...operationQueueGroups.value.map((group) => ({
    key: group.key,
    label: group.label,
    shortLabel: {
      overdue_contracts: '回款',
      due_follow_up_customers: '跟进',
      expiring_contracts: '到期',
      unpaid_deposits: '押金',
      missing_contract_body: '资料'
    }[group.key] || group.label,
    totalCount: group.totalCount,
    tone: group.tone
  }))
])

const visibleQueueGroups = computed(() => {
  if (activeQueueFilter.value === 'all') return operationQueueGroups.value
  return operationQueueGroups.value.filter((group) => group.key === activeQueueFilter.value)
})

const hasVisibleQueueItems = computed(() => {
  return visibleQueueGroups.value.some((group) => group.items.length)
})

const activeQueueFilterLabel = computed(() => {
  const item = operationQueueFilters.value.find((filter) => filter.key === activeQueueFilter.value)
  return item?.label || '全部待办'
})

const operationPrimaryFocus = computed(() => {
  for (const group of operationQueueGroups.value) {
    const item = group.items[0]
    if (item) return { group, item }
  }
  return null
})

const operationBlockingCount = computed(() => {
  return operationQueueGroups.value.reduce((sum, group) => {
    return sum + group.items.filter((item) =>
      group.tone === 'danger' ||
      item.severity === 'danger' ||
      item.type === 'missing_contract_body' ||
      item.type === 'unpaid_deposit'
    ).length
  }, 0)
})

function operationGroupByKey(key) {
  return operationQueueGroups.value.find((group) => group.key === key)
}

function operationFirstItem(keys) {
  for (const key of keys) {
    const item = operationGroupByKey(key)?.items[0]
    if (item) return item
  }
  return null
}

function operationLaneRank(lane) {
  const toneRank = { danger: 0, error: 0, warning: 1, info: 2, success: 3 }
  return toneRank[lane.tone] ?? 2
}

const operationFocusLanes = computed(() => {
  const paymentGroup = operationGroupByKey('overdue_contracts')
  const expiringGroup = operationGroupByKey('expiring_contracts')
  const depositGroup = operationGroupByKey('unpaid_deposits')
  const materialGroup = operationGroupByKey('missing_contract_body')
  const paymentFallbackCount = metricNumber(paymentGroup?.totalCount) + metricNumber(depositGroup?.totalCount)
  const paymentCount = operationV2LaneCount('payment', paymentFallbackCount)
  const materialCount = operationV2LaneCount('materials', materialGroup?.totalCount || 0)
  const expiryCount = operationV2LaneCount('expiring', expiringGroup?.totalCount || 0)
  const maxCount = Math.max(paymentCount, materialCount, expiryCount, 1)
  return [
    {
      key: 'payment',
      label: '收款',
      shortLabel: '收款',
      tone: operationV2LaneTone('payment', paymentGroup?.tone || depositGroup?.tone || 'success'),
      totalCount: paymentCount,
      item: operationFirstItem(['overdue_contracts', 'unpaid_deposits']),
      purpose: '回款和押金',
      emptyText: '暂无收款待办',
      targetFilter: paymentGroup?.totalCount ? 'overdue_contracts' : 'unpaid_deposits',
      width: `${Math.max(6, (paymentCount / maxCount) * 100)}%`
    },
    {
      key: 'materials',
      label: '资料',
      shortLabel: '资料',
      tone: operationV2LaneTone('materials', materialGroup?.tone || 'success'),
      totalCount: materialCount,
      item: materialGroup?.items[0] || null,
      purpose: '合同附件',
      emptyText: '暂无资料待补',
      targetFilter: 'missing_contract_body',
      width: `${Math.max(6, (materialCount / maxCount) * 100)}%`
    },
    {
      key: 'expiring',
      label: '到期',
      shortLabel: '到期',
      tone: operationV2LaneTone('expiring', expiringGroup?.tone || 'success'),
      totalCount: expiryCount,
      item: expiringGroup?.items[0] || null,
      purpose: '续租退租',
      emptyText: '暂无 90 天内到期',
      targetFilter: 'expiring_contracts',
      width: `${Math.max(6, (expiryCount / maxCount) * 100)}%`
    }
  ].sort((left, right) => operationLaneRank(left) - operationLaneRank(right) || right.totalCount - left.totalCount)
})

const operationPendingItems = computed(() => {
  const rows = operationQueueGroups.value.flatMap((group) =>
    group.items.map((item) => ({
      group,
      item,
      label: operationPendingLabel(item)
    }))
  )
  return rows.filter(({ item }) =>
    item.severity === 'danger' ||
    item.type === 'missing_contract_body' ||
    item.type === 'unpaid_deposit'
  ).slice(0, 4)
})

const operationTodayActionQueue = computed(() => {
  const groupOrder = {
    overdue_contracts: 0,
    expiring_contracts: 1,
    due_follow_up_customers: 2,
    unpaid_deposits: 3,
    missing_contract_body: 4
  }
  const severityOrder = { danger: 0, warning: 1, info: 2 }
  return operationQueueGroups.value
    .flatMap((group) =>
      group.items.map((item) => ({
        group,
        item,
        groupRank: groupOrder[group.key] ?? 9,
        severityRank: severityOrder[item.severity] ?? severityOrder[group.tone] ?? 2
      }))
    )
    .sort((left, right) => left.severityRank - right.severityRank || left.groupRank - right.groupRank)
    .slice(0, 5)
})

const operationHealthCards = computed(() => {
  const v2Metrics = operationsV2Data.value?.metrics
  if (Array.isArray(v2Metrics) && v2Metrics.length) {
    return v2Metrics.map(formatOperationsV2Metric)
  }
  const m = metrics.value
  const vacancy = operations.value.vacancy || {}
  return [
    {
      key: 'current_month_unpaid_balance',
      label: '本月未收',
      value: formatCurrency(m.current_month_unpaid_balance || 0),
      helper: '含未到期未收',
      tone: metricNumber(m.current_month_unpaid_balance) > 0 ? 'warning' : 'success'
    },
    {
      key: 'vacancy',
      label: '空置房间',
      value: `${formatNumber(m.vacancy_room_count || 0)} 间`,
      helper: `${formatArea(m.vacancy_area || 0)} ㎡ / 入驻率 ${formatNumber(vacancy.occupancy_rate || 0)}%`,
      progress: clampPercent(vacancy.occupancy_rate),
      tone: metricNumber(m.vacancy_room_count) > 0 ? 'info' : 'success'
    },
    {
      key: 'expiring_contract_count_90',
      label: '90天到期',
      value: formatNumber(m.expiring_contract_count_90 || 0),
      helper: '影响续租、退租和招商安排',
      tone: countTone(m.expiring_contract_count_90, 'warning')
    },
    {
      key: 'due_follow_up_customer_count',
      label: '待跟进客户',
      value: formatNumber(m.due_follow_up_customer_count || 0),
      helper: '今日及逾期跟进',
      tone: countTone(m.due_follow_up_customer_count, 'warning')
    },
    {
      key: 'unpaid_deposit_amount',
      label: '未收押金',
      value: formatCurrency(m.unpaid_deposit_amount || 0),
      helper: `${formatNumber(m.unpaid_deposit_count || 0)} 笔未收/不足`,
      tone: metricNumber(m.unpaid_deposit_amount) > 0 ? 'warning' : 'success'
    },
    {
      key: 'missing_contract_body_count',
      label: '附件待补',
      value: formatNumber(m.missing_contract_body_count || 0),
      helper: '合同正文未归档',
      tone: countTone(m.missing_contract_body_count, 'warning')
    }
  ]
})

const trendMaxAmount = computed(() => {
  const values = operationTrendRows.value.flatMap((item) => [
    metricNumber(item.receivable),
    metricNumber(item.unpaid_balance),
    metricNumber(item.overdue_balance)
  ])
  return Math.max(...values, 1)
})

const latestTrend = computed(() => {
  const rows = operationTrendRows.value
  return rows.length ? rows[rows.length - 1] : null
})

function trendBarWidth(item, key) {
  return `${Math.max(4, (metricNumber(item[key]) / trendMaxAmount.value) * 100)}%`
}

function operationItemTitle(item) {
  return item.tenant_name || item.customer_name || item.contract_no || item.label || '--'
}

function operationSignal(label, value) {
  const text = value == null ? '' : String(value)
  if (!text || text === '--') return null
  return { label, value: text }
}

function operationLanePurpose(key) {
  return {
    overdue_contracts: '先锁定历史逾期金额',
    due_follow_up_customers: '今天需要回访的客户',
    expiring_contracts: '提前确认续租或退租',
    unpaid_deposits: '补齐履约保障',
    missing_contract_body: '补齐归档缺口'
  }[key] || '按优先级处理'
}

function operationLaneEmptyText(key) {
  return {
    overdue_contracts: '暂无历史逾期',
    due_follow_up_customers: '暂无今日跟进',
    expiring_contracts: '暂无近期到期',
    unpaid_deposits: '暂无未收押金',
    missing_contract_body: '暂无附件缺口'
  }[key] || '暂无待办'
}

function operationItemMoney(item) {
  if (metricNumber(item.overdue_balance) > 0) return formatCurrency(item.overdue_balance)
  if (metricNumber(item.unpaid_amount) > 0) return formatCurrency(item.unpaid_amount)
  if (metricNumber(item.expected_amount) > 0) return formatCurrency(item.expected_amount)
  return '不涉及金额'
}

function operationItemTiming(item) {
  if (item.type === 'overdue_contract' && item.max_aging_days != null) return `${item.max_aging_days || 0} 天账龄`
  if (item.type === 'due_follow_up_customer') {
    if (item.overdue_days) return `逾期 ${item.overdue_days} 天`
    return item.next_follow_up_date ? formatDate(item.next_follow_up_date) : '今天'
  }
  if (item.days_left != null) return `${item.days_left} 天`
  if (item.due_date) return formatDate(item.due_date)
  if (item.next_follow_up_date) return formatDate(item.next_follow_up_date)
  return '今天'
}

function operationItemOwnerName(item) {
  return item.owner || item.dunning_owner || item.internal_contact_person || '待分配'
}

function operationItemNextAction(item) {
  switch (item.type) {
    case 'overdue_contract':
      return item.next_follow_up_date ? `跟进回款 ${formatDate(item.next_follow_up_date)}` : '核回应收并催收'
    case 'expiring_contract':
      return '确认续租或退租'
    case 'due_follow_up_customer':
      return item.next_action || '完成客户跟进'
    case 'unpaid_deposit':
      return '确认押金收款'
    case 'missing_contract_body':
      return '补齐合同正文'
    default:
      return '查看并处理'
  }
}

function operationLaneDigest(item) {
  return `${operationItemReason(item)} · ${operationItemNextAction(item)}`
}

function focusOperationLane(key) {
  const lane = operationFocusLanes.value.find((item) => item.key === key)
  activeQueueFilter.value = lane?.targetFilter || key
}

function operationPendingLabel(item) {
  if (item.type === 'missing_contract_body') return '合同正文缺失'
  if (item.type === 'unpaid_deposit') return '押金未收/不足'
  if (item.type === 'overdue_contract') return '历史逾期需跟进'
  if (item.severity === 'danger') return `${operationItemReason(item)}需跟进`
  return '资料或收款需补齐'
}

function operationItemSignals(item) {
  const signals = []
  const add = (label, value) => {
    const signal = operationSignal(label, value)
    if (signal) signals.push(signal)
  }

  switch (item.type) {
    case 'overdue_contract':
      add('账龄', `${item.max_aging_days || 0} 天`)
      add('房间', item.room_no)
      add('合同', item.contract_no)
      break
    case 'expiring_contract':
      add('到期', formatDate(item.end_date))
      add('房间', item.room_no)
      add('合同', item.contract_no)
      break
    case 'due_follow_up_customer':
      add('负责人', item.owner)
      add('下次', formatDate(item.next_follow_up_date))
      add('动作', item.next_action || '待跟进')
      break
    case 'unpaid_deposit':
      add('应收', formatDate(item.due_date))
      add('类型', item.deposit_type || '押金')
      add('合同', item.contract_no)
      break
    case 'missing_contract_body':
      add('缺口', item.missing_category || '合同正文')
      add('房间', item.room_no)
      add('合同', item.contract_no)
      break
    default:
      add('合同', item.contract_no)
      add('房间', item.room_no)
  }

  return signals.slice(0, 3)
}

function operationItemReason(item) {
  switch (item.type) {
    case 'overdue_contract':
      return '收款核对'
    case 'expiring_contract':
      return '续租排期'
    case 'due_follow_up_customer':
      return '客户动作'
    case 'unpaid_deposit':
      return '履约保障'
    case 'missing_contract_body':
      return '资料完整'
    default:
      return '查看事项'
  }
}

function operationItemValue(item) {
  if (item.overdue_balance) return formatCurrency(item.overdue_balance)
  if (item.unpaid_amount) return formatCurrency(item.unpaid_amount)
  if (item.days_left != null) return `${item.days_left} 天`
  if (item.overdue_days) return `${item.overdue_days} 天`
  if (item.open_follow_up_count) return `${item.open_follow_up_count} 条`
  return ''
}

function operationItemActionLabel(item) {
  switch (item.type) {
    case 'overdue_contract':
      return props.user?.role === 'viewer' ? '查看合同' : '查看应收'
    case 'expiring_contract':
      return '续租/退租'
    case 'due_follow_up_customer':
      return '客户跟进'
    case 'unpaid_deposit':
      return '确认押金'
    case 'missing_contract_body':
      return '补附件'
    default:
      return '查看'
  }
}

function operationItemValueLabel(item) {
  switch (item.type) {
    case 'overdue_contract':
      return '历史逾期'
    case 'expiring_contract':
      return '剩余天数'
    case 'due_follow_up_customer':
      return item.overdue_days ? '逾期天数' : '跟进数量'
    case 'unpaid_deposit':
      return '未收押金'
    case 'missing_contract_body':
      return '附件状态'
    default:
      return '处理指标'
  }
}

function operationItemDestinationLabel(item) {
  if (item.target_type === 'customer' && item.customer_id) return '客户详情'
  if (item.contract_id) return '合同详情'
  if (item.room_no) return '房源概览'
  return '待办队列'
}

const operationTargetFacts = computed(() => {
  const item = operationTargetItem.value
  if (!item) return []
  const facts = []
  const add = (label, value, tone = '') => {
    const text = value == null ? '' : String(value)
    if (text && text !== '--') facts.push({ label, value: text, tone })
  }
  add('房号', item.room_no)
  add('客户', item.tenant_name || item.customer_name)
  add('合同', item.contract_no)
  add('金额', operationItemMoney(item), metricNumber(item.overdue_balance || item.unpaid_amount) > 0 ? 'warning' : '')
  add('事项', item.missing_category || item.label || operationItemReason(item))
  return facts
})

const operationTargetTitle = computed(() => {
  const item = operationTargetItem.value
  if (!item) return ''
  return item.room_no || operationItemTitle(item)
})

const operationTargetNextAction = computed(() => {
  const item = operationTargetItem.value
  if (!item) return ''
  return operationItemNextAction(item)
})

function operationContractInitialSection(item) {
  if (item?.type === 'overdue_contract') return 'receivables'
  return ''
}

function operationRowRank(groupIndex, itemIndex) {
  const previousCount = visibleQueueGroups.value
    .slice(0, groupIndex)
    .reduce((sum, group) => sum + group.items.length, 0)
  return previousCount + itemIndex + 1
}

function operationSeverityClass(severity) {
  if (severity === 'danger') return 'error'
  if (severity === 'warning') return 'warning'
  return 'info'
}

function openOperationItem(item) {
  if (item.target_type === 'customer' && item.customer_id) {
    emit('open-customer', item.customer_id)
    return
  }
  if (item.contract_id) {
    const initialSection = operationContractInitialSection(item)
    emit('open-contract', initialSection ? { id: item.contract_id, initialSection } : item.contract_id)
    return
  }
  if (item.room_no) {
    openOperationTarget(item)
    return
  }
}

function openOperationAction(item) {
  openOperationItem(item)
}

function operationSecondaryActionLabel(item) {
  if (item.type === 'overdue_contract') {
    return props.user?.role === 'viewer' ? '' : '前往月度应收'
  }
  if (item.target_type === 'customer' && item.customer_id) {
    return '前往客户管理'
  }
  if (item.room_no && !item.contract_id) return '前往房间管理'
  return ''
}

function navigateOperationItem(item) {
  if (item.type === 'overdue_contract' && props.user?.role !== 'viewer') {
    emit('select-tab', 'receivables')
    return
  }
  if (item.target_type === 'customer' && item.customer_id) {
    emit('select-tab', 'customers')
    return
  }
  if (item.room_no && !item.contract_id) {
    emit('select-tab', 'rooms')
  }
}

function openOperationTarget(item) {
  operationTargetItem.value = item
}

function closeOperationTarget() {
  operationTargetItem.value = null
}

function openOperationTargetContract() {
  const item = operationTargetItem.value
  if (!item?.contract_id) return
  emit('open-contract', item.contract_id)
  closeOperationTarget()
}

function openOperationTargetCustomer() {
  const item = operationTargetItem.value
  if (!item?.customer_id) return
  emit('open-customer', item.customer_id)
  closeOperationTarget()
}

function goOperationTargetRoom() {
  closeOperationTarget()
  emit('select-tab', 'rooms')
}

async function fetchOperations() {
  operationsLoading.value = true
  try {
    operationsData.value = await cachedApi('/api/dashboard/operations?include_trend=false&queue_limit=0&risk_limit=0')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    operationsLoading.value = false
  }
}

async function fetchOperationsV2() {
  operationsV2Loading.value = true
  try {
    operationsV2Data.value = await cachedApi('/api/dashboard/operations/v2?include_trend=false&queue_limit=0&risk_limit=0')
  } catch {
    if (!operationsV2Data.value) operationsV2Data.value = null
  } finally {
    operationsV2Loading.value = false
  }
}

async function fetchOperationTrend() {
  trendLoading.value = true
  try {
    const data = await cachedApi('/api/dashboard/operations/trend')
    trendRows.value = data.trend || []
  } catch (error) {
    if (!trendRows.value.length) trendRows.value = []
    emit('notify', { type: 'error', text: error.message })
  } finally {
    trendLoading.value = false
  }
}

onMounted(() => {
  fetchOperations()
  fetchOperationsV2()
  fetchOperationTrend()
})
</script>

<template>
  <section class="content operations-workbench-page" data-testid="operations-page">
    <FinanceOperations @open-contract="emit('open-contract', $event)" />
    <ExecutionActions @open-contract="emit('open-contract', $event)" />
    <article class="card operations-dashboard-card operations-command-card">
      <div class="card-header compact">
        <div>
          <div class="section-label">待办队列</div>
          <h3 class="card-title">今日运营处理台</h3>
        </div>
        <div class="pill-group operation-command-actions">
          <span v-if="operationsRefreshing || operationsV2Loading || trendRefreshing" class="metric-pill info refreshing-indicator">同步中</span>
          <span class="metric-pill info">月份 {{ operations.month || '--' }}</span>
          <span class="metric-pill warning">完整队列 {{ formatNumber(totalQueueCount) }}</span>
        </div>
      </div>

      <MetricSkeleton v-if="operationsInitialLoading" :count="4" />

      <div class="operation-command-board" data-testid="operation-command-board">
        <section
          class="today-priority-map"
          data-testid="today-priority-map"
          aria-label="今日处理主线"
        >
          <div class="today-priority-signal" :class="operationPrimaryFocus?.group.tone || 'success'">
            <span class="section-label">今日处理主线</span>
            <strong>{{ operationPrimaryFocus ? operationItemTitle(operationPrimaryFocus.item) : '暂无今日优先事项' }}</strong>
            <small>
              {{
                operationPrimaryFocus
                  ? `${operationPrimaryFocus.group.label} · ${operationItemNextAction(operationPrimaryFocus.item)}`
                  : '今日队列已清空'
              }}
            </small>
            <span class="today-priority-facts">
              <b>总待办 {{ formatNumber(totalQueueCount) }}</b>
              <b>高优先级 {{ formatNumber(operationBlockingCount) }}</b>
              <b>{{ formatCurrency(metrics.overdue_balance || 0) }}</b>
            </span>
          </div>
          <div class="today-priority-lanes">
            <button
              v-for="lane in operationFocusLanes"
              :key="lane.key"
              type="button"
              class="today-priority-lane"
              :class="[lane.tone, { active: activeQueueFilter === lane.targetFilter }]"
              :aria-pressed="activeQueueFilter === lane.targetFilter"
              :data-testid="`today-priority-lane-${lane.key}`"
              @click="focusOperationLane(lane.key)"
            >
              <span class="today-priority-lane-head">
                <strong>{{ lane.label }}</strong>
                <b>{{ formatNumber(lane.totalCount) }}</b>
              </span>
              <span class="today-priority-meter" aria-hidden="true">
                <i :style="{ width: lane.width }"></i>
              </span>
              <span class="today-priority-lane-copy">
                <span>{{ lane.item ? operationItemTitle(lane.item) : lane.emptyText }}</span>
                <small>{{ lane.item ? operationLaneDigest(lane.item) : lane.purpose }}</small>
              </span>
            </button>
          </div>
        </section>

        <section
          v-if="operationTodayActionQueue.length"
          class="today-action-queue"
          data-testid="today-action-queue"
          aria-label="今日紧急队列"
        >
          <div class="today-action-queue-head">
            <span class="section-label">今日紧急队列</span>
            <b>{{ formatNumber(operationTodayActionQueue.length) }}</b>
          </div>
          <div class="today-action-queue-list">
            <article
              v-for="(entry, index) in operationTodayActionQueue"
              :key="`${entry.group.key}-${entry.item.contract_id || entry.item.customer_id || entry.item.deposit_id || entry.item.follow_up_id}`"
              class="today-action-row"
              :class="entry.group.tone"
            >
              <button
                type="button"
                class="today-action-main"
                :data-testid="`today-action-row-${index}`"
                @click="openOperationItem(entry.item)"
              >
                <span class="operation-rank-chip">{{ index + 1 }}</span>
                <span>
                  <strong>{{ operationItemTitle(entry.item) }}</strong>
                  <small>{{ entry.group.label }} · {{ operationItemTiming(entry.item) }}</small>
                </span>
              </button>
              <button type="button" class="operation-priority-action" @click="openOperationAction(entry.item)">
                {{ operationItemActionLabel(entry.item) }}
              </button>
            </article>
          </div>
        </section>
        <section v-else class="today-action-queue compact-empty" data-testid="today-action-queue">
          <strong>今日紧急队列已清空</strong>
        </section>

        <section class="operation-pending-strip" data-testid="operation-pending-strip" aria-label="资料和押金缺口">
          <div class="operation-pending-head">
            <span class="section-label">关键缺口</span>
            <b>{{ formatNumber(operationPendingItems.length) }}</b>
          </div>
          <button
            v-for="entry in operationPendingItems"
            :key="`${entry.group.key}-${entry.item.contract_id || entry.item.customer_id || entry.item.deposit_id || entry.item.follow_up_id}`"
            type="button"
            class="operation-pending-item"
            :class="entry.group.tone"
            @click="openOperationItem(entry.item)"
          >
            <strong>{{ entry.label }}</strong>
            <span>{{ operationItemTitle(entry.item) }}</span>
            <small>{{ operationItemNextAction(entry.item) }}</small>
          </button>
          <span v-if="!operationPendingItems.length" class="operation-pending-empty">暂无资料或押金缺口</span>
        </section>
      </div>
    </article>

    <div class="operation-dashboard-layout">
      <article class="card operations-dashboard-card operation-queue-panel" data-testid="operations-queue">
        <div class="card-header compact">
          <div>
            <div class="section-label">优先处理</div>
            <h3 class="card-title">处理队列</h3>
          </div>
          <span class="operation-panel-actions">
            <span class="metric-pill info">{{ activeQueueFilterLabel }}</span>
          </span>
        </div>

        <div class="operation-filter-strip" data-testid="operation-filter-strip" role="tablist" aria-label="待办类型筛选">
          <button
            v-for="filter in operationQueueFilters"
            :key="filter.key"
            type="button"
            class="operation-filter-chip"
            :class="{ active: activeQueueFilter === filter.key }"
            role="tab"
            :aria-selected="activeQueueFilter === filter.key"
            :data-testid="`operation-filter-${filter.key}`"
            @click="activeQueueFilter = filter.key"
          >
            <span>{{ filter.shortLabel }}</span>
            <b>{{ formatNumber(filter.totalCount) }}</b>
          </button>
        </div>

        <TableSkeleton
          v-if="operationsInitialLoading"
          class="operation-queue-skeleton"
          data-testid="operations-queue-skeleton"
          :rows="5"
          :columns="4"
        />
        <div v-else-if="hasVisibleQueueItems" class="operation-worklist operation-queue-groups" data-testid="operation-priority-list">
          <section v-for="(group, groupIndex) in visibleQueueGroups" :key="group.key" class="operation-workgroup operation-queue-group" :class="group.tone">
            <div class="operation-workgroup-title operation-queue-title">
              <span class="operation-queue-heading">
                <strong>{{ group.label }}</strong>
                <small>{{ group.helper }}</small>
              </span>
              <span class="metric-pill" :class="group.tone">{{ formatNumber(group.totalCount) }}</span>
            </div>
            <div v-if="group.items.length" class="operation-work-rows">
              <div
                v-for="(item, itemIndex) in group.items"
                :key="`${group.key}-${item.contract_id || item.customer_id || item.deposit_id || item.follow_up_id}`"
                class="operation-work-item"
                :class="operationSeverityClass(item.severity)"
              >
                <button
                  type="button"
                  class="operation-work-row"
                  :class="operationSeverityClass(item.severity)"
                  :data-testid="`operation-priority-row-${operationRowRank(groupIndex, itemIndex) - 1}`"
                  @click="openOperationItem(item)"
                >
                  <span class="operation-severity-rail" aria-hidden="true"></span>
                  <span class="operation-work-main">
                    <span class="operation-work-title">
                      <span class="operation-rank-chip">{{ operationRowRank(groupIndex, itemIndex) }}</span>
                      <strong>{{ operationItemTitle(item) }}</strong>
                    </span>
                    <span class="operation-work-reason">
                      <span class="badge" :class="operationSeverityClass(item.severity)">{{ item.label }}</span>
                      <small>{{ operationItemReason(item) }}</small>
                    </span>
                  </span>
                  <span class="operation-signal-list">
                    <span v-for="signal in operationItemSignals(item)" :key="`${signal.label}-${signal.value}`" class="operation-signal">
                      <small>{{ signal.label }}</small>
                      <b>{{ signal.value }}</b>
                    </span>
                  </span>
                  <span class="operation-row-action">
                    <em>{{ operationItemValueLabel(item) }}</em>
                    <b>{{ operationItemValue(item) || '查看' }}</b>
                    <small>{{ operationItemDestinationLabel(item) }}</small>
                  </span>
                </button>
                <span class="operation-action-stack">
                  <button
                    type="button"
                    class="operation-priority-action"
                    :data-testid="`operation-priority-action-${operationRowRank(groupIndex, itemIndex) - 1}`"
                    @click="openOperationAction(item)"
                  >
                    {{ operationItemActionLabel(item) }}
                  </button>
                  <button
                    v-if="operationSecondaryActionLabel(item)"
                    type="button"
                    class="operation-secondary-action"
                    :data-testid="`operation-secondary-action-${operationRowRank(groupIndex, itemIndex) - 1}`"
                    @click="navigateOperationItem(item)"
                  >
                    {{ operationSecondaryActionLabel(item) }}
                  </button>
                </span>
              </div>
            </div>
          </section>
        </div>
        <div v-else class="compact-empty operation-empty-state">
          <strong>当前无高优先级事项</strong>
          <div class="btn-row">
            <button v-if="activeQueueFilter !== 'all'" type="button" class="btn btn-compact" @click="activeQueueFilter = 'all'">查看全部</button>
            <button type="button" class="btn btn-compact" @click="emit('select-tab', 'contracts')">查看合同</button>
            <button type="button" class="btn btn-compact" @click="emit('select-tab', 'risks')">风险中心</button>
          </div>
        </div>
      </article>

      <details class="demo-secondary-details operations-secondary-details">
        <summary>
          <span>指标与趋势</span>
          <strong>关键指标 / 回款走势</strong>
        </summary>
        <aside class="operation-side-stack">
          <article class="card operations-dashboard-card operation-health-card" data-testid="operations-health">
          <div class="card-header compact">
            <div>
              <div class="section-label">经营指标</div>
              <h3 class="card-title">关键指标</h3>
            </div>
          </div>
          <div class="operation-health-grid">
            <div v-for="item in operationHealthCards" :key="item.key" class="health-metric" :class="item.tone">
              <div class="health-metric-head">
                <span>{{ item.label }}</span>
                <i :class="item.tone"></i>
              </div>
              <strong>{{ item.value }}</strong>
              <small>{{ item.helper }}</small>
              <div v-if="item.progress != null" class="health-meter" :class="item.tone">
                <i :style="{ width: `${item.progress}%` }"></i>
              </div>
            </div>
          </div>
        </article>

        <article class="card operations-dashboard-card operation-trend-panel" data-testid="operations-trend">
          <div class="card-header compact">
            <div>
              <div class="section-label">趋势</div>
              <h3 class="card-title">近 6 个月回款走势</h3>
            </div>
            <span v-if="trendRefreshing" class="metric-pill info refreshing-indicator">同步中</span>
            <span v-else-if="latestTrend" class="metric-pill info">{{ latestTrend.month }} 回款率 {{ latestTrend.received_rate || 0 }}%</span>
          </div>

          <div v-if="trendInitialLoading" class="operation-trend-skeleton" data-testid="operations-trend-skeleton">
            <div v-for="index in 6" :key="`operation-trend-skeleton-${index}`" class="operation-trend-row">
              <span class="skeleton-line skeleton-line-xs"></span>
              <span class="operation-trend-track">
                <i class="skeleton-line"></i>
                <i class="skeleton-line"></i>
              </span>
              <span class="skeleton-line skeleton-line-xs"></span>
            </div>
          </div>
          <div v-else-if="operationTrendRows.length" class="operation-trend-bars">
            <div v-for="item in operationTrendRows" :key="item.month" class="operation-trend-row">
              <strong>{{ item.month.slice(5) }}</strong>
              <span class="operation-trend-track">
                <i class="received" :style="{ width: trendBarWidth(item, 'received') }"></i>
                <i class="unpaid" :style="{ width: trendBarWidth(item, 'unpaid_balance') }"></i>
              </span>
              <small>{{ item.received_rate || 0 }}%</small>
            </div>
          </div>
          <div v-else class="operation-queue-empty">暂无趋势数据</div>

          <div class="operation-trend-table-wrap">
            <TableSkeleton v-if="trendInitialLoading" :rows="4" :columns="5" />
            <table v-else class="operation-trend-table">
              <thead>
                <tr>
                  <th>月份</th>
                  <th>应收</th>
                  <th>已收</th>
                  <th>未收</th>
                  <th>逾期</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in operationTrendRows" :key="`table-${item.month}`">
                  <td>{{ item.month }}</td>
                  <td>{{ formatCurrency(item.receivable || 0) }}</td>
                  <td>{{ formatCurrency(item.received || 0) }}</td>
                  <td>{{ formatCurrency(item.unpaid_balance || 0) }}</td>
                  <td>{{ formatCurrency(item.overdue_balance || 0) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
        </aside>
      </details>
    </div>

    <BaseModal :open="Boolean(operationTargetItem)" title="房源事项" size="small" @close="closeOperationTarget">
      <div v-if="operationTargetItem" class="operation-target-context" data-testid="operation-target-context-modal">
        <div class="risk-target-summary">
          <div class="risk-selected-badges">
            <span class="badge compact" :class="operationSeverityClass(operationTargetItem.severity)">
              {{ operationTargetItem.label || operationItemReason(operationTargetItem) }}
            </span>
            <span class="badge compact muted">{{ operationItemDestinationLabel(operationTargetItem) }}</span>
          </div>
          <strong>{{ operationTargetTitle }}</strong>
        </div>

        <div v-if="operationTargetFacts.length" class="risk-selected-facts">
          <span
            v-for="fact in operationTargetFacts"
            :key="`operation-target-${fact.label}`"
            class="risk-selected-fact"
            :class="fact.tone"
          >
            <small>{{ fact.label }}</small>
            <b>{{ fact.value }}</b>
          </span>
        </div>

        <div class="risk-selected-next">
          <span>下一步</span>
          <strong>{{ operationTargetNextAction }}</strong>
          <div class="risk-target-actions">
            <button
              v-if="operationTargetItem.contract_id"
              type="button"
              class="btn btn-primary btn-compact"
              data-testid="operation-target-contract-button"
              @click="openOperationTargetContract"
            >
              查看合同
            </button>
            <button
              v-if="operationTargetItem.customer_id"
              type="button"
              class="btn btn-compact"
              data-testid="operation-target-customer-button"
              @click="openOperationTargetCustomer"
            >
              查看客户
            </button>
            <button
              v-if="operationTargetItem.room_no"
              type="button"
              class="btn btn-compact"
              data-testid="operation-target-room-tab-button"
              @click="goOperationTargetRoom"
            >
              前往房间管理
            </button>
          </div>
        </div>
      </div>

      <template #footer>
        <button class="btn" @click="closeOperationTarget">关闭</button>
      </template>
    </BaseModal>
  </section>
</template>
