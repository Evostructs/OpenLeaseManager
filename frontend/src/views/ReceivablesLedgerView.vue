<script setup>
import { t } from '../i18n/index.js'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { startOperation } from '../lib/operationTelemetry'
import SavedFilterViews from '../components/SavedFilterViews.vue'
import ResponsiveDisclosure from '../components/ResponsiveDisclosure.vue'
import BaseModal from '../components/BaseModal.vue'
import ChartCard from '../components/ChartCard.vue'
import ChartSkeleton from '../components/ChartSkeleton.vue'
import DatePickerInput from '../components/DatePickerInput.vue'
import DocumentGenerateModal from '../components/DocumentGenerateModal.vue'
import InfoHint from '../components/InfoHint.vue'
import MetricSkeleton from '../components/MetricSkeleton.vue'
import ReceivableDunningLedger from '../components/ReceivableDunningLedger.vue'
import ReceivableInvoiceLedger from '../components/ReceivableInvoiceLedger.vue'
import ReceivablePaymentModal from '../components/ReceivablePaymentModal.vue'
import TableSkeleton from '../components/TableSkeleton.vue'
import { api, openDownload } from '../lib/api'
import { receivableSubmissionFor, releaseReceivableSubmission } from '../lib/receivableSubmission'
import { currentDateValue, currentMonthValue } from '../lib/dates'
import { cachedApi, invalidateApiCache } from '../lib/prefetchCache'
import { formatCurrencyFixed } from '../lib/format'
import { dunningStatusTone, invoiceStatusTone } from '../lib/receivableStatusTone'
import {
  clampPager,
  includesFilter,
  listTotalPages,
  pageLabel,
  pageRows,
  showingText,
  sortTableRows
} from '../lib/table'
import {
  compareReceivablePriority,
  hasReceivableActiveDunning,
  hasReceivableFollowUp,
  hasReceivableFollowUpDue,
  hasReceivablePendingInvoice,
  isReceivableOverdue,
  sumReceivableAmount
} from '../lib/receivablesDomain'

const props = defineProps({
  user: {
    type: Object,
    required: true
  },
  propertyScope: {
    type: Object,
    default: () => ({})
  },
  drilldownIntent: {
    type: Object,
    default: null
  },
  drilldownToken: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['notify', 'open-contract', 'open-customer', 'select-tab'])

const isAdmin = computed(() => props.user.role === 'admin' && props.propertyScope?.type === 'building')
const canViewReceivables = computed(() => props.user.role !== 'viewer')
const showReceivables = computed(() => canViewReceivables.value)

const pageSizeOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
  { label: '100', value: 100 },
  { label: '不限', value: 0 }
]
const RECEIVABLES_CACHE_TTL_MS = 20000

const receivables = ref(null)
const invoiceLedger = ref(null)
const dunningLedger = ref(null)
const receivablesLoading = ref(false)
const invoiceLedgerLoading = ref(false)
const dunningLedgerLoading = ref(false)
const month = ref(currentMonthValue())
const receivableDialogOpen = ref(false)
const receivableSubmission = shallowRef(null)
const receivableSaving = computed(() => Boolean(receivableSubmission.value?.state.saving))
const receivableUncertain = computed(() => Boolean(receivableSubmission.value?.state.uncertain))
const receivableSaveError = computed(() => receivableSubmission.value?.state.error || '')
const receivableRefreshWarning = ref('')
let receivableSubmissionKey = ''
let receivableContext = null
let viewDisposed = false
onBeforeUnmount(() => { viewDisposed = true })
const documentDialogOpen = ref(false)
const documentRequest = ref(null)
const receivableForm = reactive({
  contract_id: null,
  contract_no: '',
  tenant_name: '',
  total: 0,
  balance: 0,
  received_amount: 0,
  adjustment_amount: 0,
  settlement_amount: 0,
  overpaid_amount: 0,
  payment_count: 0,
  payments: [],
  received_date: '',
  payment_amount: null,
  payment_date: '',
  allocation_type: '收款',
  payment_method: '转账',
  reference_no: '',
  payment_notes: '',
  invoice_status: '未开票',
  invoice_no: '',
  invoice_date: '',
  invoice_amount: 0,
  invoice_tax_rate: 0,
  invoice_notes: '',
  dunning_status: '未催收',
  dunning_owner: '',
  last_dunning_date: '',
  next_follow_up_date: '',
  dunning_notes: '',
  notes: ''
})
const receivableFilters = reactive({
  keyword: '',
  status: 'all',
  contract_no: '',
  tenant_name: '',
  room_no: '',
  rent: '',
  property_fee: '',
  invoice_status: ''
})
const invoiceFilters = reactive({
  invoice_status: '',
  keyword: ''
})
const dunningFilters = reactive({
  dunning_status: '',
  dunning_owner: '',
  overdue_only: '',
  focus: true
})
const receivableSort = reactive({
  key: '',
  direction: ''
})
const receivablePager = reactive({ page: 1, pageSize: 25 })
const invoicePager = reactive({ page: 1, pageSize: 25 })
const dunningPager = reactive({ page: 1, pageSize: 25 })
const receivableDetailLedgerOpen = ref(false)
const activeReceivableLedger = ref('main')
const currentViewFilters = computed(() => ({ month: month.value, keyword: receivableFilters.keyword, status: receivableFilters.status, invoiceStatus: receivableFilters.invoice_status, contractNo: receivableFilters.contract_no, tenantName: receivableFilters.tenant_name, roomNo: receivableFilters.room_no, dunningOwner: dunningFilters.dunning_owner, dunningStatus: dunningFilters.dunning_status, overdueOnly: dunningFilters.overdue_only, ledger: activeReceivableLedger.value }))
async function applySavedView(filters) {
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(filters.month || '')) month.value = filters.month
  Object.assign(receivableFilters, { keyword: filters.keyword || '', status: filters.status || 'all', invoice_status: filters.invoiceStatus || '', contract_no: filters.contractNo || '', tenant_name: filters.tenantName || '', room_no: filters.roomNo || '' })
  Object.assign(dunningFilters, { dunning_owner: filters.dunningOwner || '', dunning_status: filters.dunningStatus || '', overdue_only: filters.overdueOnly || '' })
  receivablePager.page = 1; dunningPager.page = 1; invoicePager.page = 1
  await refreshReceivablePage({ force: true })
  await openReceivableLedger(['main','invoice','dunning'].includes(filters.ledger) ? filters.ledger : 'main')
}
const receivableMainLedgerSection = ref(null)
const invoiceLedgerSection = ref(null)
const dunningLedgerSection = ref(null)

const receivablePaymentDeleteConfirm = reactive({ payment: null, deleting: false })

let receivablesRequestId = 0
let invoiceLedgerRequestId = 0
let dunningLedgerRequestId = 0

let appliedDrilldownToken = 0

const allReceivableRows = computed(() => receivables.value?.rows || [])
const statementGenerating = ref(false)
const statementSummary = computed(() => {
  const rows = receivables.value?.rows || []
  if (!rows.length) return null
  const frozen = rows.filter((row) => row.statement_frozen).length
  return { frozen, total: rows.length }
})

async function generateStatements(regenerate = false) {
  if (!isAdmin.value || statementGenerating.value) return
  const label = regenerate ? '重新出账将覆盖本月已固化金额' : '出账后本月应收金额将固化为账单'
  const confirmed = window.confirm(t(
    `确定对 ${month.value} 执行${regenerate ? '重新出账' : '出账'}？\n${label}，合同与条款变更不再回溯影响该月。`
  ))
  if (!confirmed) return
  statementGenerating.value = true
  try {
    const result = await api(
      `/api/contracts/receivables/statements/${month.value}/generate?regenerate=${regenerate}`,
      { method: 'POST' }
    )
    emit('notify', { text: `${result.month} 出账完成：新增 ${result.created}，覆盖 ${result.updated}，跳过 ${result.skipped}` })
    await fetchReceivables({ force: true })
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    statementGenerating.value = false
  }
}

const receivablesInitialLoading = computed(() => showReceivables.value && receivablesLoading.value && !receivables.value)
const receivablesRefreshing = computed(() => showReceivables.value && receivablesLoading.value && Boolean(receivables.value))

const receivableDueCopy = computed(() => `${receivables.value?.month || month.value} 月末到期`)

const receivableExceptionLanes = computed(() => {
  const rows = allReceivableRows.value
  const overdueRows = rows.filter(isReceivableOverdue)
  const notDueRows = rows.filter((row) => Number(row.not_due_balance || 0) > 0 && !isReceivableOverdue(row))
  const invoiceRows = rows.filter(hasReceivablePendingInvoice)
  const followRows = rows.filter(hasReceivableFollowUp)

  return [
    {
      key: 'overdue',
      status: 'overdue',
      label: '本月逾期',
      tone: 'danger',
      amount: sumReceivableAmount(overdueRows, 'overdue_balance'),
      count: overdueRows.length,
      meta: '本月已超过应收日',
      action: '看本月逾期'
    },
    {
      key: 'not_due',
      status: 'not_due',
      label: '本月未到期未收',
      tone: 'warning',
      amount: sumReceivableAmount(notDueRows, 'not_due_balance'),
      count: notDueRows.length,
      meta: '本月未收但未逾期',
      action: '看未到期'
    },
    {
      key: 'invoice_pending',
      status: 'invoice_pending',
      label: '待开票',
      tone: 'info',
      amount: sumReceivableAmount(invoiceRows, 'balance'),
      count: invoiceRows.length,
      meta: '未开票 / 待开票',
      action: '看开票'
    },
    {
      key: 'dunning',
      status: 'dunning',
      label: '收款跟进',
      tone: 'primary',
      amount: sumReceivableAmount(followRows, 'balance'),
      count: followRows.length,
      meta: '提醒或跟进临近',
      action: '看跟进'
    }
  ]
})
const receivableTriageSegments = computed(() => {
  const rows = allReceivableRows.value
  const overdueRows = rows.filter(isReceivableOverdue)
  const followUpRows = rows.filter(hasReceivableFollowUpDue)
  const activeDunningRows = rows.filter(hasReceivableActiveDunning)
  const invoiceRows = rows.filter(hasReceivablePendingInvoice)
  const notDueRows = rows.filter((row) => Number(row.not_due_balance || 0) > 0 && !isReceivableOverdue(row))

  return [
    {
      key: 'overdue',
      status: 'overdue',
      label: '本月逾期收款',
      action: '核对收款',
      pathLabel: '先核对本月逾期款项',
      reason: '本月已经超过应收日，优先保障本月现金回收',
      nextAction: '点本月逾期收款，登记收款或发通知',
      stepKey: 'overdue',
      tone: 'danger',
      amount: sumReceivableAmount(overdueRows, 'overdue_balance'),
      count: overdueRows.length,
      meta: '超过应收日'
    },
    {
      key: 'follow-up',
      status: 'follow_up',
      label: '跟进到期',
      action: '联系确认',
      pathLabel: '再处理跟进到期',
      reason: '已到约定回访时间，避免付款承诺断档',
      nextAction: '点跟进到期，联系后登记结果',
      stepKey: 'follow-up',
      tone: 'warning',
      amount: sumReceivableAmount(followUpRows, 'balance'),
      count: followUpRows.length,
      meta: '本轮需要回访'
    },
    {
      key: 'active-dunning',
      status: 'active_dunning',
      label: '收款推进',
      action: '推进收款',
      pathLabel: '推进已安排事项',
      reason: '已有提醒或升级动作，需要保持跟进节奏',
      nextAction: '点收款推进，发通知或更新负责人',
      stepKey: 'dunning',
      tone: 'primary',
      amount: sumReceivableAmount(activeDunningRows, 'balance'),
      count: activeDunningRows.length,
      meta: '已有提醒动作'
    },
    {
      key: 'invoice-pending',
      status: 'invoice_pending',
      label: '开票待办',
      action: '安排开票',
      pathLabel: '补齐待开票',
      reason: '发票未登记会阻滞付款和对账',
      nextAction: '点开票待办，登记发票状态',
      stepKey: 'invoice',
      tone: 'info',
      amount: sumReceivableAmount(invoiceRows, 'balance'),
      count: invoiceRows.length,
      meta: '发票仍需登记'
    },
    {
      key: 'not-due',
      status: 'not_due',
      label: '本月未到期未收',
      action: '持续观察',
      pathLabel: '最后观察未到期',
      reason: '本月尚未逾期，不并入本月逾期口径',
      nextAction: '点本月未到期未收，保留筛选观察',
      stepKey: 'not-due',
      tone: 'neutral',
      amount: sumReceivableAmount(notDueRows, 'not_due_balance'),
      count: notDueRows.length,
      meta: '本月未收但未逾期'
    }
  ]
})
const receivableActionSteps = computed(() => receivableTriageSegments.value.map((segment, index) => ({
  ...segment,
  rank: index + 1
})))
const receivableFocusRows = computed(() => {
  const rows = allReceivableRows.value
  const alertIds = new Set((receivables.value?.dunning_alerts || []).map((item) => item.contract_id))
  return rows
    .filter((row) => (
      alertIds.has(row.contract_id)
      || isReceivableOverdue(row)
      || hasReceivableFollowUp(row)
      || (Number(row.balance || 0) > 0 && hasReceivablePendingInvoice(row))
    ))
    .sort((a, b) => compareReceivablePriority(a, b, alertIds))
    .slice(0, 5)
})
const receivableStatusOptions = computed(() => {
  const rows = allReceivableRows.value
  const count = (status) => rows.filter((row) => matchesReceivableStatus(row, status)).length
  return [
    { value: 'all', label: '全部', count: rows.length },
    { value: 'unpaid', label: '本月未收', count: count('unpaid') },
    { value: 'overdue', label: '本月逾期', count: count('overdue') },
    { value: 'not_due', label: '本月未到期', count: count('not_due') },
    { value: 'partial', label: '部分收款', count: count('partial') },
    { value: 'paid', label: '已收', count: count('paid') },
    { value: 'overpaid', label: '超收', count: count('overpaid') },
    { value: 'invoice_pending', label: '待开票', count: count('invoice_pending') },
    { value: 'follow_up', label: '跟进到期', count: count('follow_up') },
    { value: 'active_dunning', label: '收款推进', count: count('active_dunning') },
    { value: 'dunning', label: '跟进中', count: count('dunning') }
  ]
})

const receivableRows = computed(() => {
  const rows = allReceivableRows.value.filter((row) => {
    if (!matchesReceivableKeyword(row, receivableFilters.keyword)) return false
    if (!matchesReceivableStatus(row, receivableFilters.status)) return false
    if (!includesFilter(row.contract_no, receivableFilters.contract_no)) return false
    if (!includesFilter(row.tenant_name, receivableFilters.tenant_name)) return false
    if (!includesFilter(row.room_no, receivableFilters.room_no)) return false
    if (!includesFilter(row.rent, receivableFilters.rent)) return false
    if (!includesFilter(row.property_fee, receivableFilters.property_fee)) return false
    return true
  })

  if (!receivableSort.key || !receivableSort.direction) return rows

  const accessors = {
    contract_no: (row) => row.contract_no,
    tenant_name: (row) => row.tenant_name,
    room_no: (row) => row.room_no,
    rent: (row) => row.rent,
    property_fee: (row) => row.property_fee,
    received_amount: (row) => row.received_amount,
    balance: (row) => row.balance,
    dunning_owner: (row) => row.dunning_owner
  }

  return sortTableRows(rows, accessors[receivableSort.key], receivableSort.direction)
})

const receivableAgingRows = computed(() => {
  const buckets = receivables.value?.aging_buckets || {}
  return Object.entries(buckets).map(([key, item]) => ({
    key,
    label: item.label,
    amount: Number(item.amount || 0),
    count: Number(item.count || 0)
  }))
})

const keyDebtorRows = computed(() => receivables.value?.key_debtors || [])
const receivableVisualColors = {
  info: '#2563eb',
  success: '#059669',
  warning: '#d97706',
  danger: '#dc2626',
  muted: '#64748b'
}
const receivableCollectionItems = computed(() => {
  const current = receivables.value || {}
  return [
    { name: '已收', value: Number(current.total_received || 0), itemStyle: { color: receivableVisualColors.success } },
    { name: '核销调整', value: Number(current.total_adjustment || 0), itemStyle: { color: receivableVisualColors.info } },
    { name: '本月未到期未收', value: Number(current.total_not_due_balance || 0), itemStyle: { color: receivableVisualColors.warning } },
    { name: '本月逾期', value: Number(current.total_overdue_balance || 0), itemStyle: { color: receivableVisualColors.danger } }
  ].filter((item) => item.value > 0)
})
const receivableCollectionOverviewTotal = computed(() => {
  const sum = receivableCollectionItems.value.reduce((total, item) => total + Number(item.value || 0), 0)
  return sum || Number(receivables.value?.total_rent || 0) || 1
})
const receivableCollectionOverviewSegments = computed(() => {
  const fallbackItems = receivableCollectionItems.value.length
    ? receivableCollectionItems.value
    : [{ name: '暂无数据', value: 1, tone: 'muted' }]
  return fallbackItems.map((item) => ({
    ...item,
    tone: item.tone || (
      item.name === '已收' ? 'success'
        : item.name === '本月未到期未收' ? 'warning'
          : item.name === '本月逾期' ? 'danger'
            : item.name === '核销调整' ? 'info'
              : 'muted'
    ),
    percent: Math.max(4, (Number(item.value || 0) / receivableCollectionOverviewTotal.value) * 100)
  }))
})
const receivableFlowSteps = computed(() => {
  const current = receivables.value || {}
  const rows = allReceivableRows.value
  const paidRows = rows.filter((row) => matchesReceivableStatus(row, 'paid'))
  const unpaidRows = rows.filter((row) => matchesReceivableStatus(row, 'unpaid'))
  const overdueRows = rows.filter((row) => matchesReceivableStatus(row, 'overdue'))
  const actionRows = rows.filter((row) => (
    isReceivableOverdue(row)
    || hasReceivableFollowUp(row)
    || (Number(row.balance || 0) > 0 && hasReceivablePendingInvoice(row))
  ))

  return [
    {
      key: 'due',
      label: '应收',
      action: '看全部',
      status: 'all',
      target: 'main',
      tone: 'info',
      amount: Number(current.total_rent || 0),
      count: rows.length,
      meta: '本月合同应收'
    },
    {
      key: 'received',
      label: '已收',
      action: '看已收',
      status: 'paid',
      target: 'main',
      tone: 'success',
      amount: Number(current.total_received || 0),
      count: paidRows.length,
      meta: '已登记回款'
    },
    {
      key: 'unpaid',
      label: '本月未收',
      action: '看本月未收',
      status: 'unpaid',
      target: 'main',
      tone: 'warning',
      amount: Number(current.total_unpaid_balance ?? current.total_balance ?? 0),
      count: unpaidRows.length,
      meta: '含未到期'
    },
    {
      key: 'overdue',
      label: '本月逾期',
      action: '看本月逾期',
      status: 'overdue',
      target: 'dunning',
      tone: 'danger',
      amount: Number(current.total_overdue_balance || 0),
      count: overdueRows.length,
      meta: '只看超过应收日',
      dunningStatus: '',
      overdueOnly: 'true',
      focus: true
    },
    {
      key: 'todo',
      label: '收款待办',
      action: '看收款待办',
      status: 'dunning',
      target: 'dunning',
      tone: 'primary',
      amount: sumReceivableAmount(actionRows, 'balance'),
      count: actionRows.length,
      meta: '收款 / 开票 / 跟进',
      dunningStatus: '',
      overdueOnly: '',
      focus: true
    }
  ]
})
const receivableLedgerPresetSteps = computed(() =>
  receivableFlowSteps.value.filter((step) => ['unpaid', 'overdue', 'todo', 'received'].includes(step.key))
)
const receivableSecondaryMetrics = computed(() => {
  const current = receivables.value || {}
  return [
    { label: '租金实收', value: formatCurrencyFixed(current.total_received), meta: `回款率 ${current.received_rate || 0}%` },
    { label: '未到期未收', value: formatCurrencyFixed(current.total_not_due_balance || 0), meta: `${current.unpaid_count || 0} 份未结清` },
    { label: '超收余额', value: formatCurrencyFixed(current.total_overpaid), meta: '余额以核销后金额为准' },
    { label: '核销调整', value: formatCurrencyFixed(current.total_adjustment), meta: '减免 / 坏账 / 调整' },
    { label: '物业费参考', value: formatCurrencyFixed(current.total_property), meta: '仅作参考' }
  ]
})
const receivableCollectionOption = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (params) => `${params.name}<br/>${formatCurrencyFixed(params.value)} (${params.percent}%)`
  },
  legend: {
    bottom: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#64748b', fontSize: 11 }
  },
  series: [
    {
      name: '回款结构',
      type: 'pie',
      radius: ['52%', '76%'],
      center: ['50%', '43%'],
      avoidLabelOverlap: true,
      label: { formatter: '{b}', color: '#475569', fontSize: 11 },
      data: receivableCollectionItems.value
    }
  ]
}))
const receivableAgingOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params) => {
      const item = params?.[0]
      return item ? `${item.name}<br/>${formatCurrencyFixed(item.value)}` : ''
    }
  },
  grid: { left: 42, right: 12, top: 18, bottom: 42 },
  xAxis: {
    type: 'category',
    data: receivableAgingRows.value.map((item) => item.label),
    axisLabel: { color: '#64748b', interval: 0, fontSize: 10 }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#94a3b8', formatter: (value) => `${Math.round(Number(value || 0) / 10000)}万` },
    splitLine: { lineStyle: { color: '#e5e7eb' } }
  },
  series: [
    {
      name: '本月未收',
      type: 'bar',
      barMaxWidth: 28,
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: receivableVisualColors.warning
      },
      data: receivableAgingRows.value.map((item) => item.amount)
    }
  ]
}))
const receivableDebtorOption = computed(() => {
  const rows = keyDebtorRows.value.slice(0, 5)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const item = params?.[0]
        return item ? `${item.name}<br/>${formatCurrencyFixed(item.value)}` : ''
      }
    },
    grid: { left: 96, right: 20, top: 14, bottom: 22 },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8', formatter: (value) => `${Math.round(Number(value || 0) / 10000)}万` },
      splitLine: { lineStyle: { color: '#e5e7eb' } }
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: rows.map((item) => item.tenant_name),
      axisLabel: {
        color: '#475569',
        fontSize: 11,
        width: 86,
        overflow: 'truncate'
      }
    },
    series: [
      {
        name: '本月未收',
        type: 'bar',
        barMaxWidth: 18,
        label: {
          show: true,
          position: 'right',
          color: '#111827',
          fontSize: 10,
          formatter: (params) => formatCurrencyFixed(params.value)
        },
        itemStyle: {
          borderRadius: [0, 6, 6, 0],
          color: receivableVisualColors.info
        },
        data: rows.map((item) => Number(item.balance || 0))
      }
    ]
  }
})
const receivableChartInsight = computed(() => {
  const current = receivables.value || {}
  const unpaid = Number(current.total_unpaid_balance ?? current.total_balance ?? 0)
  const overdue = Number(current.total_overdue_balance || 0)
  return `本月未收 ${formatCurrencyFixed(unpaid)}，本月逾期 ${formatCurrencyFixed(overdue)}。`
})
const invoiceLedgerRows = computed(() => invoiceLedger.value?.rows || [])
const dunningLedgerRows = computed(() => dunningLedger.value?.rows || [])
const receivableLedgerTabs = computed(() => [
  { key: 'main', label: '主台账', count: receivableRows.value.length },
  { key: 'invoice', label: '开票台账', count: invoiceLedgerRows.value.length },
  { key: 'dunning', label: '催收台账', count: dunningLedgerRows.value.length }
])
const ledgerFlowStatusBars = computed(() => [
  {
    key: 'invoice',
    label: '开票状态',
    total: invoiceLedgerRows.value.length,
    items: ['未开票', '待开票', '已开票', '无需开票', '红冲', '作废'].map((status) => {
      const rows = invoiceLedgerRows.value.filter((row) => row.invoice_status === status)
      return {
        status,
        count: rows.length,
        amount: sumReceivableAmount(rows, 'balance'),
        tone: invoiceStatusTone(status)
      }
    }).filter((item) => item.count)
  },
  {
    key: 'dunning',
    label: '催收状态',
    total: dunningLedgerRows.value.length,
    items: ['未催收', '已提醒', '催收中', '已升级', '无需催收'].map((status) => {
      const rows = dunningLedgerRows.value.filter((row) => row.dunning_status === status)
      return {
        status,
        count: rows.length,
        amount: sumReceivableAmount(rows, 'balance'),
        tone: dunningStatusTone(status)
      }
    }).filter((item) => item.count)
  }
])
const ledgerFlowLanes = computed(() => {
  const invoiceRows = invoiceLedgerRows.value
  const dunningRows = dunningLedgerRows.value
  const pendingInvoiceRows = invoiceRows.filter((row) => ['未开票', '待开票'].includes(row.invoice_status))
  const issuedUnpaidRows = invoiceRows.filter((row) => row.invoice_status === '已开票' && Number(row.balance || 0) > 0)
  const overdueDunningRows = dunningRows.filter((row) => Number(row.overdue_balance || 0) > 0 && row.dunning_status === '未催收')
  const plannedFollowRows = dunningRows.filter((row) => (
    Boolean(row.next_follow_up_date)
    || ['已提醒', '催收中', '已升级'].includes(row.dunning_status)
  ))
  const pendingFollowRows = dunningRows.filter((row) => (
    (row.follow_up_due || row.dunning_status === '未催收')
    && Number(row.balance || 0) > 0
    && Number(row.overdue_balance || 0) <= 0
  ))

  return [
    {
      key: 'pending-invoice',
      label: '未开票',
      action: '补齐开票',
      target: 'invoice',
      invoiceStatus: '未开票',
      tone: 'info',
      amountKey: 'balance',
      rows: pendingInvoiceRows
    },
    {
      key: 'issued-unpaid',
      label: '已开票未收',
      action: '确认付款',
      target: 'invoice',
      invoiceStatus: '已开票',
      tone: 'warning',
      amountKey: 'balance',
      rows: issuedUnpaidRows
    },
    {
      key: 'overdue-dunning',
      label: '本月逾期待催',
      action: '安排下次跟进',
      target: 'dunning',
      dunningStatus: '未催收',
      overdueOnly: 'true',
      focus: true,
      tone: 'danger',
      amountKey: 'overdue_balance',
      rows: overdueDunningRows
    },
    {
      key: 'planned-follow',
      label: '已安排跟进',
      action: '确认付款',
      target: 'dunning',
      dunningStatus: '',
      overdueOnly: '',
      focus: true,
      tone: 'primary',
      amountKey: 'balance',
      rows: plannedFollowRows
    },
    {
      key: 'pending-follow',
      label: '待跟进',
      action: '未到期观察',
      target: 'dunning',
      dunningStatus: '未催收',
      overdueOnly: 'false',
      focus: true,
      tone: 'neutral',
      amountKey: 'balance',
      rows: pendingFollowRows
    }
  ].map((lane) => ({
    ...lane,
    amount: sumReceivableAmount(lane.rows, lane.amountKey),
    count: lane.rows.length,
    rows: lane.rows
      .slice()
      .sort((a, b) => Number(b[lane.amountKey] || 0) - Number(a[lane.amountKey] || 0))
      .slice(0, 4)
  }))
})
const receivableTotalPages = computed(() => listTotalPages(receivableRows.value.length, receivablePager.pageSize))
const hasReceivablePagination = computed(() => receivablePager.pageSize !== 0 && receivableTotalPages.value > 1)
const paginatedReceivableRows = computed(() => pageRows(receivableRows.value, receivablePager))
const receivablePageLabel = computed(() => pageLabel(receivablePager, receivableTotalPages.value))
const receivableShowingText = computed(() => showingText(receivableRows.value.length, receivablePager))

function paymentStatusTone(status) {
  if (['已结清', '无应收'].includes(status)) return 'success'
  if (status === '超收') return 'info'
  if (String(status || '').includes('部分')) return 'warning'
  return 'warning'
}

function matchesReceivableKeyword(row, keyword) {
  const normalized = String(keyword || '').trim().toLowerCase()
  if (!normalized) return true
  return [row.contract_no, row.tenant_name, row.room_no, row.tower, row.invoice_no, row.dunning_owner]
    .some((value) => String(value || '').toLowerCase().includes(normalized))
}

function matchesReceivableStatus(row, status) {
  switch (status) {
    case 'unpaid':
      return Number(row.balance || 0) > 0
    case 'overdue':
      return Number(row.overdue_balance || 0) > 0 || Boolean(row.is_overdue)
    case 'not_due':
      return Number(row.not_due_balance || 0) > 0 && !isReceivableOverdue(row)
    case 'partial':
      return String(row.payment_status || '').includes('部分')
    case 'paid':
      return Number(row.balance || 0) <= 0 && Number(row.overpaid_amount || 0) <= 0 && Number(row.rent || 0) > 0
    case 'overpaid':
      return Number(row.overpaid_amount || 0) > 0
    case 'invoice_pending':
      return ['未开票', '待开票'].includes(row.invoice_status)
    case 'follow_up':
      return hasReceivableFollowUpDue(row)
    case 'active_dunning':
      return hasReceivableActiveDunning(row)
    case 'dunning':
      return hasReceivableFollowUp(row)
    case 'all':
    default:
      return true
  }
}

function receivableBalanceText(row) {
  return row.overpaid_amount > 0 ? `+${formatCurrencyFixed(row.overpaid_amount)}` : formatCurrencyFixed(row.balance)
}

function receivableOverdueText(row) {
  if (Number(row.overdue_balance || 0) > 0) return `${formatCurrencyFixed(row.overdue_balance)} · ${row.aging_days || 0} 天`
  if (Number(row.not_due_balance || 0) > 0) return '未到期'
  return '--'
}

function receivablePriorityReason(row) {
  if (Number(row.overdue_balance || 0) > 0) return `逾期 ${row.aging_days || 0} 天`
  if (row.follow_up_due) return '跟进到期'
  if (hasReceivableFollowUp(row)) return row.dunning_status || '需跟进'
  if (hasReceivablePendingInvoice(row)) return row.invoice_status
  if (Number(row.not_due_balance || 0) > 0) return '未到期未收'
  return '查看账款'
}

function receivablePriorityTone(row) {
  if (Number(row.overdue_balance || 0) > 0) return 'danger'
  if (row.follow_up_due || hasReceivableFollowUp(row)) return 'warning'
  if (hasReceivablePendingInvoice(row)) return 'info'
  return 'muted'
}

function receivablePriorityAmount(row) {
  return Number(row.overdue_balance || 0) > 0 ? Number(row.overdue_balance || 0) : Number(row.balance || 0)
}

function receivablePriorityAmountLabel(row) {
  return Number(row.overdue_balance || 0) > 0 ? '逾期' : '未收'
}

function setReceivableStatus(status) {
  receivableFilters.status = status
  receivablePager.page = 1
}

function scrollReceivableLedgerTarget(target) {
  const section = target === 'invoice'
    ? invoiceLedgerSection.value
    : target === 'dunning'
      ? dunningLedgerSection.value
      : receivableMainLedgerSection.value
  section?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function openReceivableLedger(target = 'main') {
  activeReceivableLedger.value = target
  receivableDetailLedgerOpen.value = true
  await nextTick()
  scrollReceivableLedgerTarget(target)
}

async function applyReceivableFlowStep(step) {
  setReceivableStatus(step.status)
  if (step.target === 'dunning') {
    dunningFilters.dunning_status = step.dunningStatus || ''
    dunningFilters.overdue_only = step.overdueOnly || ''
    dunningFilters.focus = step.focus !== undefined ? Boolean(step.focus) : true
    dunningPager.page = 1
    await fetchDunningLedger()
  }
  if (step.target === 'invoice') {
    invoicePager.page = 1
    await fetchInvoiceLedger()
  }
  await openReceivableLedger(step.target)
}

async function clearReceivableFilters() {
  receivableFilters.keyword = ''
  receivableFilters.status = 'all'
  receivableFilters.contract_no = ''
  receivableFilters.tenant_name = ''
  receivableFilters.room_no = ''
  receivableFilters.rent = ''
  receivableFilters.property_fee = ''
  receivableFilters.invoice_status = ''
  await refreshReceivablePage({ force: true })
}

function ledgerFlowRowMeta(row, lane) {
  if (lane.amountKey === 'overdue_balance') return `${row.aging_days || 0} 天`
  if (row.next_follow_up_date) return row.next_follow_up_date
  if (Number(row.not_due_balance || 0) > 0) return '未到期观察'
  return row.dunning_status || row.invoice_status || '--'
}

function scrollLedgerSection(target) {
  const section = target === 'invoice' ? invoiceLedgerSection.value : dunningLedgerSection.value
  section?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function invalidateReceivablesCache() {
  invalidateApiCache('/api/contracts/receivables')
}

async function applyLedgerFlowLane(lane) {
  if (lane.target === 'invoice') {
    invoiceFilters.invoice_status = lane.invoiceStatus || ''
    invoicePager.page = 1
    await fetchInvoiceLedger()
    await openReceivableLedger('invoice')
    return
  }

  dunningFilters.dunning_status = lane.dunningStatus || ''
  dunningFilters.overdue_only = lane.overdueOnly || ''
  dunningFilters.focus = Boolean(lane.focus)
  dunningPager.page = 1
  await fetchDunningLedger()
  await openReceivableLedger('dunning')
}

async function fetchReceivables(options = {}) {
  if (!canViewReceivables.value) return
  const requestId = ++receivablesRequestId
  receivablesLoading.value = true
  try {
    const params = new URLSearchParams({ month: month.value })
    if (receivableFilters.invoice_status) params.set('invoice_status', receivableFilters.invoice_status)
    const data = await cachedApi(`/api/contracts/receivables/monthly?${params}`, {
      force: options.force,
      ttlMs: RECEIVABLES_CACHE_TTL_MS
    })
    if (requestId !== receivablesRequestId) return
    receivables.value = data
    if (!options.preservePage) receivablePager.page = 1
    return true
  } catch (error) {
    if (requestId === receivablesRequestId && !options.quiet) {
      emit('notify', { type: 'error', text: error.message })
    }
    return false
  } finally {
    if (requestId === receivablesRequestId) {
      receivablesLoading.value = false
    }
  }
}

async function fetchInvoiceLedger(options = {}) {
  if (!canViewReceivables.value) return
  const requestId = ++invoiceLedgerRequestId
  invoiceLedgerLoading.value = true
  try {
    const params = new URLSearchParams({ month: month.value })
    if (invoiceFilters.invoice_status) params.set('invoice_status', invoiceFilters.invoice_status)
    if (invoiceFilters.keyword) params.set('keyword', invoiceFilters.keyword)
    const data = await cachedApi(`/api/contracts/receivables/invoices?${params}`, {
      force: options.force,
      ttlMs: RECEIVABLES_CACHE_TTL_MS
    })
    if (requestId !== invoiceLedgerRequestId) return
    invoiceLedger.value = data
    if (!options.preservePage) invoicePager.page = 1
    return true
  } catch (error) {
    if (requestId === invoiceLedgerRequestId && !options.quiet) {
      emit('notify', { type: 'error', text: error.message })
    }
    return false
  } finally {
    if (requestId === invoiceLedgerRequestId) {
      invoiceLedgerLoading.value = false
    }
  }
}

async function fetchDunningLedger(options = {}) {
  if (!canViewReceivables.value) return
  const requestId = ++dunningLedgerRequestId
  dunningLedgerLoading.value = true
  try {
    const params = new URLSearchParams({ month: month.value, focus: dunningFilters.focus ? 'true' : 'false' })
    if (dunningFilters.dunning_status) params.set('dunning_status', dunningFilters.dunning_status)
    if (dunningFilters.dunning_owner) params.set('dunning_owner', dunningFilters.dunning_owner)
    if (dunningFilters.overdue_only) params.set('overdue_only', dunningFilters.overdue_only)
    const data = await cachedApi(`/api/contracts/receivables/dunning?${params}`, {
      force: options.force,
      ttlMs: RECEIVABLES_CACHE_TTL_MS
    })
    if (requestId !== dunningLedgerRequestId) return
    dunningLedger.value = data
    if (!options.preservePage) dunningPager.page = 1
    return true
  } catch (error) {
    if (requestId === dunningLedgerRequestId && !options.quiet) {
      emit('notify', { type: 'error', text: error.message })
    }
    return false
  } finally {
    if (requestId === dunningLedgerRequestId) {
      dunningLedgerLoading.value = false
    }
  }
}

async function refreshReceivablePage(options = {}) {
  await Promise.all([fetchReceivables(options), fetchInvoiceLedger(options), fetchDunningLedger(options)])
}

async function queryReceivablePage() {
  await refreshReceivablePage({ force: true })
}

async function applyDrilldownIntent({ refresh = false } = {}) {
  const intent = props.drilldownIntent
  if (!showReceivables.value || !intent || intent.target !== 'receivables' || props.drilldownToken === appliedDrilldownToken) return
  appliedDrilldownToken = props.drilldownToken
  const filters = intent.filters || {}
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(filters.month || '')) month.value = filters.month
  receivableFilters.keyword = filters.keyword || ''
  receivableFilters.status = filters.status || 'all'
  receivableFilters.contract_no = filters.contractNo || ''
  receivableFilters.tenant_name = filters.tenantName || ''
  receivableFilters.room_no = filters.roomNo || ''
  receivableFilters.invoice_status = filters.invoiceStatus || ''
  dunningFilters.dunning_owner = filters.dunningOwner || ''
  dunningFilters.dunning_status = filters.dunningStatus || ''
  dunningFilters.overdue_only = filters.overdueOnly || ''
  if (['main','invoice','dunning'].includes(filters.ledger)) { activeReceivableLedger.value = filters.ledger; receivableDetailLedgerOpen.value = true }
  receivablePager.page = 1
  emit('notify', { text: intent.message || '已应用来自经营分析的月度应收上下文' })
  if (refresh) await refreshReceivablePage({ force: true })
}

function exportInvoiceLedger() {
  const params = new URLSearchParams({ month: month.value })
  if (invoiceFilters.invoice_status) params.set('invoice_status', invoiceFilters.invoice_status)
  if (invoiceFilters.keyword) params.set('keyword', invoiceFilters.keyword)
  openDownload(`/api/contracts/receivables/invoices/export?${params}`)
}

function exportDunningLedger() {
  const params = new URLSearchParams({ month: month.value, focus: dunningFilters.focus ? 'true' : 'false' })
  if (dunningFilters.dunning_status) params.set('dunning_status', dunningFilters.dunning_status)
  if (dunningFilters.dunning_owner) params.set('dunning_owner', dunningFilters.dunning_owner)
  if (dunningFilters.overdue_only) params.set('overdue_only', dunningFilters.overdue_only)
  openDownload(`/api/contracts/receivables/dunning/export?${params}`)
}

function changeReceivablePageSize(size) {
  receivablePager.pageSize = size
  receivablePager.page = 1
}

function openDocumentDialog(documentType, row, payment = null) {
  if (!isAdmin.value) return
  documentRequest.value = {
    document_type: documentType,
    contract_id: row.contract_id,
    month: row.period_month || receivables.value?.month || dunningLedger.value?.month || invoiceLedger.value?.month || month.value,
    payment_id: payment?.id || null,
    contract_no: row.contract_no || receivableForm.contract_no,
    tenant_name: row.tenant_name || receivableForm.tenant_name,
    payment_summary: payment ? `${payment.payment_date || '--'} · ${formatCurrencyFixed(payment.amount)}` : ''
  }
  documentDialogOpen.value = true
}

function openReceivableRecord(row) {
  if (!isAdmin.value || receivableSaving.value) return
  if (receivableDialogOpen.value && receivableUncertain.value) return
  releaseReceivableSubmission(receivableSubmissionKey)
  receivableContext = {
    contractId: row.contract_id,
    buildingCode: props.propertyScope.code,
    month: row.period_month || receivables.value?.month || month.value,
    userId: props.user.id || props.user.username,
  }
  receivableSubmissionKey = JSON.stringify(Object.values(receivableContext))
  receivableSubmission.value = receivableSubmissionFor(receivableSubmissionKey, api)
  Object.assign(receivableForm, {
    contract_id: row.contract_id,
    contract_no: row.contract_no,
    tenant_name: row.tenant_name,
    total: row.total ?? row.rent ?? 0,
    balance: row.balance,
    received_amount: row.received_amount || 0,
    adjustment_amount: row.adjustment_amount || 0,
    settlement_amount: row.settlement_amount || 0,
    overpaid_amount: row.overpaid_amount || 0,
    payment_count: row.payment_count || 0,
    payments: row.payments || [],
    received_date: row.received_date || currentDateValue(),
    payment_amount: null,
    payment_date: currentDateValue(),
    allocation_type: '收款',
    payment_method: '转账',
    reference_no: '',
    payment_notes: '',
    invoice_status: row.invoice_status || '未开票',
    invoice_no: row.invoice_no || '',
    invoice_date: row.invoice_date || '',
    invoice_amount: row.invoice_amount || 0,
    invoice_tax_rate: row.invoice_tax_rate || 0,
    invoice_notes: row.invoice_notes || '',
    dunning_status: row.dunning_status || '未催收',
    dunning_owner: row.dunning_owner || row.internal_contact_person || '',
    last_dunning_date: row.last_dunning_date || '',
    next_follow_up_date: row.next_follow_up_date || '',
    dunning_notes: row.dunning_notes || '',
    notes: row.receivable_notes || ''
  })
  const pendingForm = receivableSubmission.value.pendingForm()
  if (pendingForm) Object.assign(receivableForm, pendingForm)
  receivableDialogOpen.value = true
}

function openReceivableContract(row) {
  if (row?.contract_id) emit('open-contract', row.contract_id)
}

function openReceivableCustomer(row) {
  if (row?.customer_id) emit('open-customer', row.customer_id)
}

function closeReceivableRecord() {
  if (receivableSaving.value || receivableUncertain.value) return
  receivableDialogOpen.value = false
  releaseReceivableSubmission(receivableSubmissionKey)
}

async function refreshAfterReceivableSave() {
  const results = await Promise.all([
    fetchReceivables({ force: true, quiet: true, preservePage: true }),
    fetchInvoiceLedger({ force: true, quiet: true, preservePage: true }),
    fetchDunningLedger({ force: true, quiet: true, preservePage: true })
  ])
  receivableRefreshWarning.value = results.includes(false)
    ? '账目已保存，部分列表暂未刷新。请重试刷新，无需重新登记收款。' : ''
}

async function saveReceivableRecord() {
  if (!isAdmin.value || !receivableDialogOpen.value || receivableSaving.value) return
  const context = receivableContext
  const submission = receivableSubmission.value
  const key = receivableSubmissionKey
  if (!submission || !context) return
  const metadata = {
    month: context.month,
    invoice_status: receivableForm.invoice_status,
    invoice_no: receivableForm.invoice_no,
    invoice_date: receivableForm.invoice_date || null,
    invoice_amount: receivableForm.invoice_amount || 0,
    invoice_tax_rate: receivableForm.invoice_tax_rate || 0,
    invoice_notes: receivableForm.invoice_notes,
    dunning_status: receivableForm.dunning_status,
    dunning_owner: receivableForm.dunning_owner,
    last_dunning_date: receivableForm.last_dunning_date || null,
    next_follow_up_date: receivableForm.next_follow_up_date || null,
    dunning_notes: receivableForm.dunning_notes,
    notes: receivableForm.notes
  }
  const amount = Number(receivableForm.payment_amount || 0)
  if (!Number.isFinite(amount) || amount < 0) {
    emit('notify', { type: 'error', text: '核销金额须为有效的非负数' })
    return
  }
  const body = amount > 0 ? {
    month: context.month, amount, metadata,
    payment_date: receivableForm.payment_date || null,
    allocation_type: receivableForm.allocation_type,
    payment_method: receivableForm.payment_method,
    reference_no: receivableForm.reference_no,
    notes: receivableForm.payment_notes
  } : null
  const timing = startOperation('receipt-save', 'receivables')
  let outcome = 'superseded'
  try {
    const receipt = await submission.submit({ ...context, metadata, body, form: receivableForm })
    if (!receipt) return
    invalidateReceivablesCache()
    // The request keeps its original building. Ignore UI results after a scope
    // or account change; current permissions always decide what can be shown.
    if (viewDisposed || props.propertyScope.code !== context.buildingCode
      || (props.user.id || props.user.username) !== context.userId || !isAdmin.value) return
    receivableForm.payment_amount = null
    receivableDialogOpen.value = false
    emit('notify', { text: receipt.status === 'deleted'
      ? '本次收款曾登记成功，之后已删除；未重新记账，请核对明细。'
      : `${receivableForm.contract_no} ${receipt.status === 'metadata_saved' ? '发票催收信息已保存' : '收款已登记'}` })
    await refreshAfterReceivableSave()
    outcome = receivableRefreshWarning.value ? 'degraded' : 'ok'
  } catch {
    outcome = submission.state.uncertain ? 'uncertain' : 'error'
    // The persistent inline message distinguishes rejection from unknown outcome.
  } finally {
    releaseReceivableSubmission(key)
    timing.finish(outcome)
  }
}

function requestDeleteReceivablePayment(payment) {
  if (!isAdmin.value || receivableSaving.value || receivableUncertain.value) return
  if (!payment?.id) return
  receivablePaymentDeleteConfirm.payment = {
    ...payment,
    contract_id: receivableForm.contract_id,
    contract_no: receivableForm.contract_no,
    tenant_name: receivableForm.tenant_name,
    period_month: payment.month || receivables.value?.month || month.value
  }
}

function closeReceivablePaymentDeleteConfirm() {
  if (receivablePaymentDeleteConfirm.deleting) return
  receivablePaymentDeleteConfirm.payment = null
}

async function confirmDeleteReceivablePayment() {
  if (!isAdmin.value) return
  const payment = receivablePaymentDeleteConfirm.payment
  if (!payment?.id) return
  receivablePaymentDeleteConfirm.deleting = true
  try {
    receivables.value = await api(`/api/contracts/receivables/payments/${payment.id}`, { method: 'DELETE' })
    invalidateReceivablesCache()
    await Promise.all([fetchInvoiceLedger({ force: true }), fetchDunningLedger({ force: true })])
    const latest = (receivables.value?.rows || []).find((row) => row.contract_id === receivableForm.contract_id)
    if (latest) {
      Object.assign(receivableForm, {
        balance: latest.balance,
        received_amount: latest.received_amount || 0,
        adjustment_amount: latest.adjustment_amount || 0,
        settlement_amount: latest.settlement_amount || 0,
        overpaid_amount: latest.overpaid_amount || 0,
        payment_count: latest.payment_count || 0,
        payments: latest.payments || [],
        received_date: latest.received_date || '',
        invoice_status: latest.invoice_status || '未开票',
        invoice_no: latest.invoice_no || '',
        invoice_date: latest.invoice_date || '',
        invoice_amount: latest.invoice_amount || 0,
        invoice_tax_rate: latest.invoice_tax_rate || 0,
        invoice_notes: latest.invoice_notes || '',
        dunning_status: latest.dunning_status || '未催收',
        dunning_owner: latest.dunning_owner || '',
        last_dunning_date: latest.last_dunning_date || '',
        next_follow_up_date: latest.next_follow_up_date || '',
        dunning_notes: latest.dunning_notes || ''
      })
    }
    emit('notify', { text: '收款核销记录已删除' })
    receivablePaymentDeleteConfirm.payment = null
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    receivablePaymentDeleteConfirm.deleting = false
  }
}

function toggleReceivableSort(key) {
  receivablePager.page = 1
  if (receivableSort.key !== key) {
    receivableSort.key = key
    receivableSort.direction = 'asc'
  } else if (receivableSort.direction === 'asc') {
    receivableSort.direction = 'desc'
  } else if (receivableSort.direction === 'desc') {
    receivableSort.key = ''
    receivableSort.direction = ''
  } else {
    receivableSort.direction = 'asc'
  }
}

function receivableSortIndicator(key) {
  if (receivableSort.key !== key) return '↕'
  return receivableSort.direction === 'asc' ? '↑' : '↓'
}

watch(
  () => [
    receivableFilters.keyword,
    receivableFilters.status,
    receivableFilters.contract_no,
    receivableFilters.tenant_name,
    receivableFilters.room_no,
    receivableFilters.rent,
    receivableFilters.property_fee
  ],
  () => {
    receivablePager.page = 1
  }
)

watch(
  () => receivableRows.value.length,
  () => {
    clampPager(receivablePager, receivableTotalPages.value)
  }
)

watch(
  () => [props.drilldownIntent, props.drilldownToken],
  () => {
    applyDrilldownIntent({ refresh: true })
  }
)

onMounted(async () => {
  if (showReceivables.value) {
    await applyDrilldownIntent()
    await refreshReceivablePage()
  }
})
</script>

<template>
  <section class="content">
    <article v-if="showReceivables" class="card tool-report-card" data-testid="receivables-page">
      <div v-if="receivableRefreshWarning" class="inline-warning" role="status" data-testid="receivable-refresh-warning">
        {{ $t(receivableRefreshWarning) }}
        <button class="btn btn-compact" @click="refreshAfterReceivableSave">{{ $t("重试刷新") }}</button>
      </div>
      <div class="card-header compact receivable-header">
        <div class="receivable-title-line">
          <h3 class="card-title">{{ $t("月度应收") }}</h3>
        </div>
        <InfoHint :text="$t(&quot;查看本月应收、回款和未收分布。&quot;)" />
        <span v-if="receivablesRefreshing || invoiceLedgerLoading || dunningLedgerLoading" class="metric-pill info refreshing-indicator">{{ $t("同步中") }}</span>
      </div>

      <SavedFilterViews :user="user" :scope="propertyScope" page="receivables" :filters="currentViewFilters" @apply="applySavedView" />
      <div class="receivable-command-bar">
        <div v-if="receivables" class="receivable-mobile-shortcuts">
          <button class="btn btn-primary" @click="openReceivableLedger('main')">{{ $t("查看收款明细") }}</button>
          <button class="btn" @click="openReceivableLedger('dunning')">{{ $t("催收跟进") }}</button>
        </div>
        <div class="receivable-query-row">
          <DatePickerInput v-model="month" mode="month" class="month-input" />
          <input
            v-model="receivableFilters.keyword"
            class="input receivable-search-input"
            :placeholder="$t(&quot;合同 / 客户 / 房源 / 发票 / 跟进人&quot;)"
            :aria-label="$t(&quot;查找应收记录&quot;)"
            @keyup.enter="queryReceivablePage"
          />
          <select v-model="receivableFilters.invoice_status" class="select receivable-invoice-filter">
            <option value="">{{ $t("全部发票状态") }}</option>
            <option value="未开票">{{ $t("未开票") }}</option>
            <option value="待开票">{{ $t("待开票") }}</option>
            <option value="已开票">{{ $t("已开票") }}</option>
            <option value="无需开票">{{ $t("无需开票") }}</option>
            <option value="红冲/作废">{{ $t("红冲/作废") }}</option>
          </select>
          <button class="btn btn-primary" data-testid="receivables-query" @click="queryReceivablePage">{{ $t("查询") }}</button>
          <button v-if="receivables?.rows?.length" class="btn" @click="openDownload('/api/contracts/export/excel')">{{ $t("导出合同表") }}</button>
          <button
            v-if="isAdmin && statementSummary && statementSummary.frozen < statementSummary.total"
            class="btn"
            data-testid="statement-generate"
            :disabled="statementGenerating"
            @click="generateStatements(false)"
          >
            {{ $t(statementGenerating ? '出账中…' : `出账 ${month}`) }}
          </button>
          <button
            v-if="isAdmin && statementSummary && statementSummary.frozen > 0"
            class="btn btn-ghost"
            data-testid="statement-regenerate"
            :disabled="statementGenerating"
            @click="generateStatements(true)"
          >{{ $t(" 重新出账 ") }}</button>
          <span
            v-if="statementSummary && statementSummary.frozen > 0"
            class="metric-pill"
            :class="statementSummary.frozen === statementSummary.total ? 'success' : 'info'"
            data-testid="statement-status"
          >{{ $t(" 已出账 ") }}{{ $t(statementSummary.frozen) }}/{{ $t(statementSummary.total) }}
          </span>
        </div>
        <ResponsiveDisclosure :title="$t(`收款状态 · ${receivableStatusOptions.find(item => item.value === receivableFilters.status)?.label || '全部'}`)">
        <div v-if="receivables" class="contract-status-chips receivable-status-chips" data-testid="receivable-status-chips">
          <button
            v-for="option in receivableStatusOptions"
            :key="option.value"
            type="button"
            :class="{ active: receivableFilters.status === option.value }"
            @click="setReceivableStatus(option.value)"
          >
            {{ $t(option.label) }} {{ $t(option.count) }}
          </button>
        </div>
        </ResponsiveDisclosure>
      </div>

      <div v-if="receivablesInitialLoading" class="receivables-loading-layout" data-testid="receivables-skeleton">
        <MetricSkeleton :count="5" />
        <div class="page-skeleton-grid">
          <ChartSkeleton :height="230" />
          <ChartSkeleton :height="230" />
          <ChartSkeleton :height="230" />
        </div>
        <TableSkeleton :rows="8" :columns="6" />
      </div>

      <ResponsiveDisclosure v-if="receivables" :title="$t(&quot;本月应收概览与处理建议&quot;)">
      <section v-if="receivables" class="receivable-collection-overview" data-testid="receivable-collection-overview">
        <div class="receivable-collection-main">
          <div class="receivable-collection-head">
            <span class="section-label">{{ $t("本月应收结构") }}</span>
            <strong>{{ $t(formatCurrencyFixed(receivables.total_rent)) }}</strong>
            <small>{{ $t(receivables.count || 0) }}{{ $t(" 份合同") }}</small>
          </div>
          <div class="receivable-collection-track" data-testid="receivable-collection-segment" :aria-label="$t(&quot;回款结构分段&quot;)">
            <span
              v-for="segment in receivableCollectionOverviewSegments"
              :key="segment.name"
              class="receivable-collection-segment"
              :class="segment.tone"
              :style="{ flexBasis: `${segment.percent}%` }"
            >
              <b>{{ $t(segment.name) }}</b>
              <small>{{ $t(formatCurrencyFixed(segment.value)) }}</small>
            </span>
          </div>
          <div v-if="receivableCollectionOverviewSegments.length > 1" class="receivable-collection-legend">
            <span v-for="segment in receivableCollectionOverviewSegments" :key="`legend-${segment.name}`" :class="segment.tone">
              {{ $t(segment.name) }} {{ $t(formatCurrencyFixed(segment.value)) }}
            </span>
          </div>
        </div>
        <div class="receivable-primary-metrics" data-testid="receivable-secondary-metrics">
          <span>
            <small>{{ $t("已收金额") }}</small>
            <strong>{{ $t(formatCurrencyFixed(receivables.total_received || 0)) }}</strong>
          </span>
          <span>
            <small>{{ $t("本月未收") }}</small>
            <strong>{{ $t(formatCurrencyFixed(receivables.total_unpaid_balance || receivables.total_balance)) }}</strong>
          </span>
          <span class="danger">
            <small>{{ $t("本月逾期") }}</small>
            <strong>{{ $t(formatCurrencyFixed(receivables.total_overdue_balance || 0)) }}</strong>
          </span>
        </div>
        <details class="receivable-reference-details">
          <summary>{{ $t("更多收款明细") }}</summary>
          <div class="receivable-reference-grid">
            <span v-for="metric in receivableSecondaryMetrics" :key="metric.label">
              <small>{{ $t(metric.label) }}</small>
              <strong>{{ $t(metric.value) }}</strong>
              <em>{{ $t(metric.meta) }}</em>
            </span>
          </div>
        </details>
      </section>

      <section v-if="receivables" class="receivable-flow-overview" data-testid="receivable-flow-overview" :aria-label="$t(&quot;本月收款状态与去向&quot;)">
        <div class="receivable-flow-head">
          <div>
            <span class="section-label">{{ $t("状态与去向") }}</span>
            <h4>{{ $t("本月钱在哪里") }}</h4>
          </div>
          <small>{{ $t("本月未收含未到期；本月逾期只看超过应收日。") }}</small>
        </div>
        <div class="receivable-flow-steps">
          <button
            v-for="step in receivableFlowSteps"
            :key="step.key"
            type="button"
            class="receivable-flow-step"
            :class="[step.tone, { active: receivableFilters.status === step.status }]"
            :data-testid="`receivable-flow-step-${step.key}`"
            :aria-pressed="receivableFilters.status === step.status ? 'true' : 'false'"
            @click="applyReceivableFlowStep(step)"
          >
            <span class="receivable-flow-label">{{ $t(step.label) }}</span>
            <strong>{{ $t(formatCurrencyFixed(step.amount)) }}</strong>
            <small>{{ $t(step.count) }}{{ $t(" 份 · ") }}{{ $t(step.meta) }}</small>
            <b>{{ $t(step.action) }}</b>
          </button>
        </div>
      </section>

      <section v-if="receivables" class="receivable-triage-board" data-testid="receivable-triage-board" :aria-label="$t(&quot;月度应收收款路径&quot;)">
        <div class="receivable-triage-head">
          <div>
            <span class="section-label">{{ $t("收款路径") }}</span>
            <h4>{{ $t("先看未收，再登记，再开票") }}</h4>
          </div>
          <InfoHint :text="$t(&quot;按本月未收、收款登记、开票查看客户。&quot;)" />
          <button
            type="button"
            class="btn btn-compact receivable-triage-all"
            data-testid="receivable-triage-segment-all"
            :aria-pressed="receivableFilters.status === 'all' ? 'true' : 'false'"
            :class="{ active: receivableFilters.status === 'all' }"
            @click="setReceivableStatus('all')"
          >{{ $t(" 全部本月账款 ") }}{{ $t(allReceivableRows.length) }}
          </button>
        </div>
        <div class="receivable-action-path" data-testid="receivable-action-path" :aria-label="$t(&quot;月度应收处理路径&quot;)">
          <button
            v-for="step in receivableActionSteps"
            :key="`action-${step.key}`"
            type="button"
            class="receivable-action-step"
            :class="[step.tone, { active: receivableFilters.status === step.status }]"
            :data-testid="`receivable-action-step-${step.stepKey}`"
            :aria-pressed="receivableFilters.status === step.status ? 'true' : 'false'"
            @click="setReceivableStatus(step.status)"
          >
            <span class="receivable-action-rank">{{ $t(step.rank) }}</span>
            <span class="receivable-action-copy">
              <strong>{{ $t(step.pathLabel) }}</strong>
              <small>{{ $t(step.reason) }}</small>
            </span>
            <span class="receivable-action-metric">
              <b>{{ $t(formatCurrencyFixed(step.amount)) }}</b>
              <small>{{ $t(step.count) }}{{ $t(" 份") }}</small>
            </span>
            <span class="receivable-action-next">{{ $t(step.nextAction) }}</span>
          </button>
        </div>
      </section>

      <details v-if="receivables" class="demo-secondary-details receivable-priority-details">
        <summary>
          <span>{{ $t("处理客户") }}</span>
          <strong>{{ $t("未收 / 登记 / 开票") }}</strong>
        </summary>
      <div v-if="receivables" class="receivable-workspace">
        <div class="receivable-exception-lanes" data-testid="receivable-exception-lanes">
          <button
            v-for="lane in receivableExceptionLanes"
            :key="lane.key"
            type="button"
            class="receivable-exception-lane"
            :class="[lane.tone, { active: receivableFilters.status === lane.status }]"
            @click="setReceivableStatus(lane.status)"
          >
            <span class="receivable-lane-label">{{ $t(lane.label) }}</span>
            <strong>{{ $t(formatCurrencyFixed(lane.amount)) }}</strong>
            <small>{{ $t(lane.count) }}{{ $t(" 份 · ") }}{{ $t(lane.meta) }}</small>
            <span class="receivable-lane-action">{{ $t(lane.action) }}</span>
          </button>
        </div>

        <section class="chart-card receivable-priority-card receivable-action-board" data-testid="receivable-priority-card">
          <div class="chart-card-header">
            <div class="chart-card-copy">
              <span class="section-label">{{ $t("处理客户") }}</span>
              <h3>{{ $t("未收 / 登记 / 开票") }}</h3>
            </div>
          </div>
          <div class="receivable-priority-list">
            <div v-for="item in receivableFocusRows" :key="`focus-${item.contract_id}`" class="receivable-priority-row">
              <div class="receivable-priority-main">
                <div class="receivable-priority-title-row">
                  <strong>{{ item.tenant_name }}</strong>
                  <span class="receivable-signal-pill" :class="receivablePriorityTone(item)">{{ $t(receivablePriorityReason(item)) }}</span>
                </div>
                <small>{{ item.contract_no }} · {{ item.room_no }} · {{ $t(item.dunning_owner || '未分配') }}</small>
              </div>
              <div class="receivable-priority-money">
                <b>{{ $t(formatCurrencyFixed(receivablePriorityAmount(item))) }}</b>
                <small>{{ $t(receivablePriorityAmountLabel(item)) }}</small>
              </div>
              <div class="receivable-priority-actions">
                <button v-if="isAdmin" class="btn btn-compact btn-primary" @click="openReceivableRecord(item)">{{ $t("登记") }}</button>
                <button v-if="isAdmin" class="btn btn-compact" @click="openDocumentDialog('payment_notice', item)">{{ $t("缴款") }}</button>
                <button v-if="isAdmin" class="btn btn-compact" @click="openDocumentDialog('dunning_notice', item)">{{ $t("催收") }}</button>
                <button class="btn btn-compact" @click="openReceivableContract(item)">{{ $t("合同") }}</button>
              </div>
            </div>
            <div v-if="!receivableFocusRows.length" class="compact-empty slim">{{ $t("当前月份没有需要优先处理的未收或收款事项") }}</div>
          </div>
        </section>
      </div>
      </details>

      </ResponsiveDisclosure>

      <details v-if="receivables" class="receivable-visual-details">
        <summary>{{ $t("回款图表参考") }}</summary>
        <div class="visual-grid receivable-visual-grid">
          <ChartCard
            :title="$t(&quot;回款结构&quot;)"
            :option="receivableCollectionOption"
            :empty="!receivableCollectionItems.length"
            :empty-text="$t(&quot;当前月份暂无回款结构数据&quot;)"
            :height="230"
            :insight="receivableChartInsight"
            tone="info"
            data-testid="receivable-collection-chart"
          />
          <ChartCard
            :title="$t(&quot;未收账龄&quot;)"
            :option="receivableAgingOption"
            :empty="!receivableAgingRows.length"
            :empty-text="$t(&quot;当前月份暂无未收账龄&quot;)"
            :height="230"
            tone="warning"
            data-testid="receivable-aging-chart"
          />
          <ChartCard
            :title="$t(&quot;重点未收客户&quot;)"
            :option="receivableDebtorOption"
            :empty="!keyDebtorRows.length"
            :empty-text="$t(&quot;当前月份暂无未收客户&quot;)"
            :height="230"
            tone="danger"
            data-testid="receivable-debtor-chart"
          />
        </div>
      </details>

      <section v-if="receivables" class="receivable-detail-ledger" data-testid="receivable-detail-ledger">
        <div class="receivable-detail-ledger-head">
          <div>
            <span class="section-label">{{ $t("明细台账") }}</span>
            <h4>{{ $t("收款与跟进明细") }}</h4>
            <small>{{ $t("按合同登记收款，或切换至开票、催收台账。") }}</small>
          </div>
          <button
            type="button"
            class="btn"
            data-testid="receivable-detail-ledger-toggle"
            @click="receivableDetailLedgerOpen ? (receivableDetailLedgerOpen = false) : openReceivableLedger(activeReceivableLedger)"
          >
            {{ $t(receivableDetailLedgerOpen ? '收起明细' : '查看明细台账') }}
          </button>
        </div>
        <div class="receivable-ledger-entry-row">
          <button
            v-for="tab in receivableLedgerTabs"
            :key="tab.key"
            type="button"
            class="receivable-ledger-entry"
            :class="{ active: receivableDetailLedgerOpen && activeReceivableLedger === tab.key }"
            :data-testid="`receivable-ledger-tab-${tab.key}`"
            @click="openReceivableLedger(tab.key)"
          >
            <span>{{ $t(tab.label) }}</span>
            <strong>{{ $t(tab.count) }}</strong>
          </button>
        </div>

        <div class="receivable-ledger-preset-row" data-testid="receivable-ledger-presets">
          <span>{{ $t("常用视图") }}</span>
          <button
            v-for="step in receivableLedgerPresetSteps"
            :key="`preset-${step.key}`"
            type="button"
            class="receivable-ledger-preset"
            :class="[step.tone, { active: receivableFilters.status === step.status }]"
            :data-testid="`receivable-ledger-preset-${step.key}`"
            :aria-pressed="receivableFilters.status === step.status ? 'true' : 'false'"
            @click="applyReceivableFlowStep(step)"
          >
            <strong>{{ $t(step.label) }}</strong>
            <small>{{ $t(step.count) }}{{ $t(" 份 · ") }}{{ $t(formatCurrencyFixed(step.amount)) }}</small>
          </button>
        </div>

        <div v-if="receivableDetailLedgerOpen" class="receivable-ledger-panels">
          <section ref="receivableMainLedgerSection" v-show="activeReceivableLedger === 'main'" class="ledger-workbench-section receivable-main-ledger-section">
            <div class="workbench-header">
              <div>
                <h4>{{ $t("主台账") }}</h4>
              </div>
            </div>
      <div v-if="receivableRows.length" class="table-wrapper compact-table">
        <table class="data-table receivables-ledger-table" data-testid="receivables-table">
          <colgroup>
            <col class="receivable-col-identity" />
            <col class="receivable-col-room" />
            <col class="receivable-col-money" />
            <col class="receivable-col-money" />
            <col class="receivable-col-money" />
            <col class="receivable-col-money" />
            <col class="receivable-col-money" />
            <col class="receivable-col-status" />
            <col class="receivable-col-aging" />
            <col class="receivable-col-follow" />
            <col class="receivable-col-invoice" />
            <col class="receivable-col-action" />
          </colgroup>
          <thead>
            <tr>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'contract_no' }" @click="toggleReceivableSort('contract_no')">{{ $t("合同 / 客户 ") }}{{ $t(receivableSortIndicator('contract_no')) }}</button></th>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'room_no' }" @click="toggleReceivableSort('room_no')">{{ $t("房源 ") }}{{ $t(receivableSortIndicator('room_no')) }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'rent' }" @click="toggleReceivableSort('rent')">{{ $t("应收 / 参考 ") }}{{ $t(receivableSortIndicator('rent')) }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'property_fee' }" @click="toggleReceivableSort('property_fee')">{{ $t("物业参考 ") }}{{ $t(receivableSortIndicator('property_fee')) }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'received_amount' }" @click="toggleReceivableSort('received_amount')">{{ $t("实收 ") }}{{ $t(receivableSortIndicator('received_amount')) }}</button></th>
              <th class="right">{{ $t("核销") }}</th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'balance' }" @click="toggleReceivableSort('balance')">{{ $t("本月未收 ") }}{{ $t(receivableSortIndicator('balance')) }}</button></th>
              <th>{{ $t("收款") }}</th>
              <th>{{ $t("本月逾期") }}</th>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'dunning_owner' }" @click="toggleReceivableSort('dunning_owner')">{{ $t("催收跟进 ") }}{{ $t(receivableSortIndicator('dunning_owner')) }}</button></th>
              <th>{{ $t("发票") }}</th>
              <th>{{ $t("操作") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in paginatedReceivableRows" :key="row.contract_no">
              <td>
                <div class="cell-stack receivable-identity">
                  <strong>{{ row.contract_no }}</strong>
                  <span>{{ row.tenant_name }}</span>
                  <small>{{ $t(row.internal_contact_person || '未设负责人') }}</small>
                  <small v-if="row.statement_frozen" class="statement-frozen-badge" :title="$t(`已出账固化于 ${row.statement_generated_at || ''}`)">{{ $t("已出账") }}</small>
                </div>
              </td>
              <td>
                <div class="cell-stack receivable-room">
                  <strong>{{ row.room_no }}</strong>
                  <span>{{ $t(row.tower) }} · {{ $t(row.lease_days) }}/{{ $t(row.month_days) }}{{ $t(" 天") }}</span>
                </div>
              </td>
              <td class="right money-cell">
                <div class="cell-stack compact receivable-money-stack">
                  <strong>{{ $t(formatCurrencyFixed(row.rent)) }}</strong>
                  <small>{{ $t(row.due_date || receivableDueCopy) }}</small>
                </div>
              </td>
              <td class="right money-cell muted">{{ $t(formatCurrencyFixed(row.property_fee)) }}</td>
              <td class="right money-cell">{{ $t(formatCurrencyFixed(row.received_amount)) }}</td>
              <td class="right money-cell muted">{{ $t(formatCurrencyFixed(row.adjustment_amount)) }}</td>
              <td class="right money-cell balance-cell" :class="{ danger: row.balance > 0, success: row.overpaid_amount > 0 }">
                {{ $t(receivableBalanceText(row)) }}
              </td>
              <td>
                <div class="cell-stack compact">
                  <span class="badge compact" :class="paymentStatusTone(row.payment_status)">{{ $t(row.payment_status) }}</span>
                  <small>{{ $t(row.payment_count || 0) }}{{ $t(" 笔") }}</small>
                </div>
              </td>
              <td>
                <div class="cell-stack compact">
                  <span class="aging-text" :class="{ danger: row.aging_days }">{{ $t(receivableOverdueText(row)) }}</span>
                  <small>{{ $t(row.due_date || receivableDueCopy) }}</small>
                </div>
              </td>
              <td>
                <div class="cell-stack compact">
                  <span class="badge compact" :class="dunningStatusTone(row.dunning_status)">{{ $t(row.dunning_status) }}</span>
                  <small>{{ $t(row.dunning_owner || '--') }} · {{ $t(row.next_follow_up_date || '--') }}</small>
                </div>
              </td>
              <td>
                <div class="cell-stack compact">
                  <span class="badge compact" :class="invoiceStatusTone(row.invoice_status)">{{ $t(row.invoice_status) }}</span>
                  <small>{{ $t(row.invoice_no || '--') }}</small>
                </div>
              </td>
              <td class="receivable-action-cell">
                <button v-if="isAdmin" class="btn btn-compact btn-primary" :data-testid="`receivable-register-${row.contract_id}`" @click="openReceivableRecord(row)">{{ $t("登记") }}</button>
                <button v-if="isAdmin" class="btn btn-compact" :data-testid="`receivable-payment-notice-${row.contract_id}`" @click="openDocumentDialog('payment_notice', row)">{{ $t("缴款") }}</button>
                <button v-if="isAdmin" class="btn btn-compact" :data-testid="`receivable-dunning-notice-${row.contract_id}`" @click="openDocumentDialog('dunning_notice', row)">{{ $t("催收") }}</button>
                <button class="btn btn-compact" :data-testid="`receivable-contract-${row.contract_id}`" @click="openReceivableContract(row)">{{ $t("合同") }}</button>
                <button class="btn btn-compact" :data-testid="`receivable-customer-${row.contract_id}`" :disabled="!row.customer_id" @click="openReceivableCustomer(row)">{{ $t("客户") }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="receivableRows.length" class="table-footer ledger-table-footer">
        <div class="page-size-control">
          <span class="helper-text">{{ $t("每页显示") }}</span>
          <div class="page-size-switch" data-testid="receivables-page-size">
            <button
              v-for="option in pageSizeOptions"
              :key="option.value"
              type="button"
              :class="{ active: receivablePager.pageSize === option.value }"
              @click="changeReceivablePageSize(option.value)"
            >
              {{ $t(option.label) }}
            </button>
          </div>
        </div>
        <span class="helper-text">{{ $t("共 ") }}{{ $t(receivableRows.length) }}{{ $t(" 条 · ") }}{{ $t(receivableShowingText) }}</span>
        <div class="spacer" />
        <button v-if="hasReceivablePagination" class="btn" :disabled="receivablePager.page <= 1" @click="receivablePager.page -= 1">{{ $t("上一页") }}</button>
        <span v-if="hasReceivablePagination">{{ $t(receivablePageLabel) }}</span>
        <button
          v-if="hasReceivablePagination"
          class="btn"
          :disabled="receivablePager.page >= receivableTotalPages"
          @click="receivablePager.page += 1"
        >{{ $t(" 下一页 ") }}</button>
      </div>
      <div v-else-if="!receivablesInitialLoading" class="compact-empty receivable-empty-state">
        <strong>{{ $t(allReceivableRows.length ? '当前筛选无结果' : '当前月份暂无应收数据') }}</strong>
        <span>{{ $t(allReceivableRows.length ? '可清空状态、关键词或发票筛选后重新查看。' : '调整月份后点击查询，或确认该月是否存在有效合同。') }}</span>
        <button class="btn" @click="clearReceivableFilters">{{ $t("清空筛选") }}</button>
      </div>
          </section>

      <section class="ledger-flow" data-testid="ledger-flow" :aria-label="$t(&quot;开票催收状态流&quot;)">
        <div class="ledger-flow-bars">
          <div v-for="bar in ledgerFlowStatusBars" :key="bar.key" class="ledger-flow-bar">
            <div class="ledger-flow-bar-head">
              <strong>{{ $t(bar.label) }}</strong>
              <span>{{ $t(bar.total) }}{{ $t(" 件") }}</span>
            </div>
            <div class="ledger-flow-track">
              <span
                v-for="item in bar.items"
                :key="`${bar.key}-${item.status}`"
                class="ledger-flow-segment"
                :class="item.tone"
                :style="{ flexGrow: Math.max(item.count, 1) }"
                :title="$t(`${item.status} ${item.count} 件`)"
              >
                {{ $t(item.status) }} {{ $t(item.count) }}
              </span>
              <span v-if="!bar.items.length" class="ledger-flow-empty">{{ $t("暂无记录") }}</span>
            </div>
          </div>
        </div>
        <div class="ledger-flow-lanes">
          <article
            v-for="lane in ledgerFlowLanes"
            :key="lane.key"
            class="ledger-flow-lane"
            :class="lane.tone"
            role="button"
            tabindex="0"
            :data-testid="`ledger-flow-lane-${lane.key}`"
            @click="applyLedgerFlowLane(lane)"
            @keydown.enter.prevent="applyLedgerFlowLane(lane)"
            @keydown.space.prevent="applyLedgerFlowLane(lane)"
          >
            <div class="ledger-flow-lane-head">
              <span>{{ $t(lane.label) }}</span>
              <strong>{{ $t(formatCurrencyFixed(lane.amount)) }}</strong>
              <small>{{ $t(lane.count) }}{{ $t(" 件") }}</small>
            </div>
            <div class="ledger-flow-items">
              <div v-for="item in lane.rows" :key="`${lane.key}-${item.contract_id}`" class="ledger-flow-item">
                <div>
                  <strong>{{ item.contract_no || item.tenant_name }}</strong>
                  <small>{{ item.tenant_name || '--' }}</small>
                </div>
                <span>{{ $t(formatCurrencyFixed(item[lane.amountKey] || 0)) }}</span>
                <small>{{ $t(ledgerFlowRowMeta(item, lane)) }}</small>
                <div class="ledger-flow-item-actions">
                  <button v-if="isAdmin" type="button" class="btn btn-compact" @click.stop="openReceivableRecord(item)">{{ $t("登记") }}</button>
                  <button v-if="isAdmin && lane.target === 'dunning'" type="button" class="btn btn-compact" @click.stop="openDocumentDialog('dunning_notice', item)">{{ $t("催收") }}</button>
                  <button type="button" class="btn btn-compact" @click.stop="openReceivableContract(item)">{{ $t("合同") }}</button>
                </div>
              </div>
              <div v-if="!lane.rows.length" class="ledger-flow-empty compact">{{ $t(lane.action) }}</div>
            </div>
            <span class="ledger-flow-action">{{ $t(lane.action) }}</span>
          </article>
        </div>
      </section>

      <section ref="invoiceLedgerSection" v-show="activeReceivableLedger === 'invoice'" class="ledger-workbench-section">
        <ReceivableInvoiceLedger
          :ledger="invoiceLedger"
          :rows="invoiceLedgerRows"
          :filters="invoiceFilters"
          :pager="invoicePager"
          :page-size-options="pageSizeOptions"
          :is-admin="isAdmin"
          @refresh="fetchInvoiceLedger"
          @export="exportInvoiceLedger"
          @edit="openReceivableRecord"
        />
      </section>

      <section ref="dunningLedgerSection" v-show="activeReceivableLedger === 'dunning'" class="ledger-workbench-section">
        <ReceivableDunningLedger
          :ledger="dunningLedger"
          :rows="dunningLedgerRows"
          :filters="dunningFilters"
          :pager="dunningPager"
          :page-size-options="pageSizeOptions"
          :is-admin="isAdmin"
          @refresh="fetchDunningLedger"
          @export="exportDunningLedger"
          @edit="openReceivableRecord"
          @open-document="openDocumentDialog('dunning_notice', $event)"
        />
      </section>
        </div>
      </section>

      <ReceivablePaymentModal
        :open="receivableDialogOpen"
        :form="receivableForm"
        :is-admin="isAdmin"
        :saving="receivableSaving"
        :uncertain="receivableUncertain"
        :error="receivableSaveError"
        @close="closeReceivableRecord"
        @save="saveReceivableRecord"
        @delete-payment="requestDeleteReceivablePayment"
        @open-document="openDocumentDialog"
      />

      <BaseModal :open="Boolean(receivablePaymentDeleteConfirm.payment)" :title="$t(&quot;删除收款核销记录&quot;)" size="small" @close="closeReceivablePaymentDeleteConfirm">
        <div data-testid="receivable-payment-delete-confirm-modal" class="detail-list">
          <p>{{ $t(" 确定删除 ") }}{{ $t(receivablePaymentDeleteConfirm.payment?.payment_date || '--') }}{{ $t(" 的 ") }}{{ $t(receivablePaymentDeleteConfirm.payment?.allocation_type || '收款') }}
            {{ $t(formatCurrencyFixed(receivablePaymentDeleteConfirm.payment?.amount || 0)) }}{{ $t(" 记录吗？ ") }}</p>
          <p class="helper-text">
            {{ receivablePaymentDeleteConfirm.payment?.contract_no || receivableForm.contract_no }} ·
            {{ receivablePaymentDeleteConfirm.payment?.tenant_name || receivableForm.tenant_name }}
          </p>
          <p class="helper-text">{{ $t("确认后将从本月收款核销明细中移除。") }}</p>
        </div>
        <template #footer>
          <button class="btn" data-testid="receivable-payment-delete-cancel" :disabled="receivablePaymentDeleteConfirm.deleting" @click="closeReceivablePaymentDeleteConfirm">{{ $t(" 取消 ") }}</button>
          <button class="btn btn-danger" data-testid="receivable-payment-delete-confirm" :disabled="receivablePaymentDeleteConfirm.deleting" @click="confirmDeleteReceivablePayment">
            {{ $t(receivablePaymentDeleteConfirm.deleting ? '删除中' : '确认删除') }}
          </button>
        </template>
      </BaseModal>

      <DocumentGenerateModal
        :open="documentDialogOpen"
        :request="documentRequest"
        @close="documentDialogOpen = false"
        @notify="emit('notify', $event)"
      />
    </article>
  </section>
</template>
