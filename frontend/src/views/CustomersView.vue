<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatArea, formatCurrencyFixed, formatDate } from '../lib/format'
import { cachedApi, invalidateApiCache } from '../lib/prefetchCache'
import {
  compactCurrency,
  customerRiskScore,
  customerRiskSignals,
  customerRiskTier,
  customerSignalKey,
  customerValueTier,
  numericValue,
  rowCustomerName,
  roomsText,
  roundTo,
  shareValue
} from '../lib/customersDomain'
import BaseModal from '../components/BaseModal.vue'
import ChartCard from '../components/ChartCard.vue'
import CustomerDetailModal from '../components/CustomerDetailModal.vue'
import TableSkeleton from '../components/TableSkeleton.vue'

const props = defineProps({
  user: {
    type: Object,
    required: true
  },
  propertyScope: {
    type: Object,
    default: () => ({})
  },
  focusCustomerId: {
    type: [Number, String],
    default: null
  },
  focusToken: {
    type: Number,
    default: 0
  },
  focusFollowUpPrefill: {
    type: Object,
    default: null
  },
  focusFollowUpPrefillToken: {
    type: Number,
    default: 0
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

const emit = defineEmits(['notify', 'open-contract'])
const canEdit = computed(() => (
  ['admin', 'editor'].includes(props.user?.role) && props.propertyScope?.type === 'building'
))
const isAdmin = computed(() => props.user?.role === 'admin' && props.propertyScope?.type === 'building')
const canManageCustomerMaster = computed(() => (
  props.user?.scope?.effective_capabilities || []
).includes('customer_master_manage'))
const canCreateCustomer = computed(() => canEdit.value && canManageCustomerMaster.value)
const canExport = computed(() => ['admin', 'editor'].includes(props.user?.role))
const pageSizeOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
  { label: '100', value: 100 },
  { label: '不限', value: 0 }
]
const CUSTOMER_WORKSPACE_CACHE_TTL_MS = 20000

const state = reactive({
  loading: false,
  list: [],
  total: 0,
  page: 1,
  pageSize: 25,
  keyword: '',
  status: '',
  dueFollowUps: [],
  dueTotal: 0,
  dueLoading: false,
  signalLoading: false,
  contractStats: {},
  operations: {},
  attachmentTasks: { total: 0, summary: {}, items: [] }
})

const formOpen = ref(false)
const customerDetailOpen = ref(false)
const customerDetailId = ref(null)
const customerDetailFollowUpPrefill = ref(null)
const customerDetailFollowUpPrefillToken = ref(0)
const customerDetailRefreshToken = ref(0)
const saving = ref(false)
const editingId = ref(null)
const customerImportInput = ref(null)
const followUpImportInput = ref(null)
const customerPreviewInput = ref(null)
const followUpPreviewInput = ref(null)
const importResult = ref(null)
const batchActionsOpen = ref(false)
const hasLoadedCustomers = ref(false)
let appliedFollowUpPrefillToken = 0
let appliedDrilldownToken = 0
let workspaceSignalRequestToken = 0
let customersRequestToken = 0

const blankCustomer = () => ({
  name: '',
  short_name: '',
  credit_code: '',
  customer_type: '',
  industry: '',
  contact_name: '',
  contact_phone: '',
  contact_email: '',
  address: '',
  notes: '',
  local_contact: '',
  local_notes: '',
  owner: '',
  status: 'active'
})

const form = reactive(blankCustomer())
const contactPhoneWarn = ref('')
const contactEmailWarn = ref('')

const totalPages = computed(() => (state.pageSize === 0 ? 1 : Math.max(1, Math.ceil(state.total / state.pageSize))))
const hasPagination = computed(() => state.pageSize !== 0 && totalPages.value > 1)
const customersInitialLoading = computed(() => state.loading && !hasLoadedCustomers.value && !state.list.length)
const customersRefreshing = computed(() => state.loading && (hasLoadedCustomers.value || state.list.length > 0))
const pageLabel = computed(() => (state.pageSize === 0 ? '全部' : `${state.page} / ${totalPages.value}`))
const showingText = computed(() => {
  if (state.pageSize === 0) return `显示全部 ${state.total} 条`
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0
  const end = Math.min(state.page * state.pageSize, state.total)
  return `显示 ${start}-${end} 条`
})
const dueFollowUpMap = computed(() => {
  const map = new Map()
  for (const item of state.dueFollowUps || []) {
    if (item.customer_id) map.set(Number(item.customer_id), item)
  }
  return map
})
const contractCustomerRows = computed(() => state.contractStats?.customer_data || [])
const operationsMetrics = computed(() => state.operations?.metrics || {})
const operationsQueues = computed(() => state.operations?.queues || {})
const attachmentItems = computed(() => state.attachmentTasks?.items || [])
const customerStatsById = computed(() => {
  const map = new Map()
  for (const item of contractCustomerRows.value) {
    if (item.customer_id) map.set(Number(item.customer_id), item)
  }
  return map
})
const attachmentTaskByCustomerId = computed(() => {
  const map = new Map()
  for (const item of attachmentItems.value) {
    if (item.customer_id && !map.has(Number(item.customer_id))) map.set(Number(item.customer_id), item)
  }
  return map
})
const overdueCustomerRows = computed(() => {
  const grouped = new Map()
  for (const item of operationsQueues.value.overdue_contracts || []) {
    const name = item.tenant_name || item.customer_name || item.contract_no || '--'
    const key = customerSignalKey(item.customer_id, name)
    const entry = grouped.get(key) || {
      key,
      customerId: item.customer_id || null,
      name,
      overdueBalance: 0,
      unpaidBalance: 0,
      contractCount: 0,
      maxAgingDays: 0,
      rooms: new Set()
    }
    entry.overdueBalance += numericValue(item.overdue_balance)
    entry.unpaidBalance += numericValue(item.unpaid_balance)
    entry.contractCount += 1
    entry.maxAgingDays = Math.max(entry.maxAgingDays, Number(item.max_aging_days || 0))
    if (item.room_no) entry.rooms.add(item.room_no)
    grouped.set(key, entry)
  }
  return [...grouped.values()]
    .map((item) => ({ ...item, rooms: [...item.rooms].sort() }))
    .sort((left, right) => right.overdueBalance - left.overdueBalance || right.maxAgingDays - left.maxAgingDays || left.name.localeCompare(right.name, 'zh-Hans-CN'))
})
const overdueCustomerMap = computed(() => {
  const map = new Map()
  for (const item of overdueCustomerRows.value) map.set(item.key, item)
  return map
})
const topValueCustomers = computed(() => [...contractCustomerRows.value]
  .sort((left, right) => numericValue(right.rent) - numericValue(left.rent) || numericValue(right.area) - numericValue(left.area) || (left.tenant_name || '').localeCompare(right.tenant_name || '', 'zh-Hans-CN'))
  .slice(0, 5))
const customerExposureTotals = computed(() => {
  const rowRentTotal = contractCustomerRows.value.reduce((sum, item) => sum + numericValue(item.rent), 0)
  const rowAreaTotal = contractCustomerRows.value.reduce((sum, item) => sum + numericValue(item.area), 0)
  return {
    rent: numericValue(state.contractStats?.metrics?.total_monthly_rent) || rowRentTotal,
    area: numericValue(state.contractStats?.metrics?.total_area) || rowAreaTotal
  }
})
const customerConcentrationRows = computed(() => {
  const totals = customerExposureTotals.value
  return [...contractCustomerRows.value]
    .map((item) => {
      const rent = numericValue(item.rent)
      const area = numericValue(item.area)
      const name = rowCustomerName(item)
      return {
        ...item,
        key: customerSignalKey(item.customer_id, name),
        customerId: item.customer_id || null,
        keyword: name,
        name,
        rent,
        area,
        rentShare: shareValue(rent, totals.rent),
        areaShare: shareValue(area, totals.area),
        contractCount: numericValue(item.count),
        roomCount: numericValue(item.room_count)
      }
    })
    .filter((item) => item.rent > 0 || item.area > 0 || item.contractCount > 0)
    .sort((left, right) => right.rent - left.rent || right.area - left.area || left.name.localeCompare(right.name, 'zh-Hans-CN'))
    .slice(0, 8)
})
const customerConcentrationInsight = computed(() => {
  const leader = customerConcentrationRows.value[0]
  if (!leader) return ''
  return `${leader.name} 当前月租占比 ${leader.rentShare}%；前 ${customerConcentrationRows.value.length} 客户合计 ${compactCurrency(customerConcentrationRows.value.reduce((sum, item) => sum + item.rent, 0))}。`
})
const customerConcentrationOption = computed(() => {
  const rows = [...customerConcentrationRows.value].reverse()
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (items) => {
        const data = items?.[0]?.data || {}
        return [
          data.name || '--',
          `当前月租：${formatCurrencyFixed(data.rent || 0)}`,
          `租金占比：${data.rentShare || 0}%`,
          `面积占比：${data.areaShare || 0}%`
        ].join('<br/>')
      }
    },
    grid: { left: 92, right: 54, top: 20, bottom: 28, containLabel: true },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#6b7280', formatter: (value) => compactCurrency(value) },
      splitLine: { lineStyle: { color: '#e5e7eb' } }
    },
    yAxis: {
      type: 'category',
      data: rows.map((item) => item.name),
      axisLabel: {
        color: '#374151',
        formatter: (value) => (String(value).length > 8 ? `${String(value).slice(0, 8)}...` : value)
      },
      axisLine: { lineStyle: { color: '#d1d5db' } }
    },
    series: [
      {
        name: '当前月租',
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: '#2563eb', borderRadius: [0, 8, 8, 0] },
        label: {
          show: true,
          position: 'right',
          color: '#1d4ed8',
          fontSize: 11,
          formatter: ({ data }) => `${data.rentShare}%`
        },
        data: rows.map((item) => ({
          value: item.rent,
          name: item.name,
          rent: item.rent,
          rentShare: item.rentShare,
          areaShare: item.areaShare
        }))
      }
    ]
  }
})
const customerRiskValueColumns = [
  { key: 'high', label: '高租金', helper: '租金占比较高' },
  { key: 'medium', label: '有租金', helper: '有在租合同' },
  { key: 'low', label: '暂无租金', helper: '暂无在租租金' }
]
const customerRiskTiers = [
  { key: 'high', label: '立即处理', helper: '应收、跟进待办', tone: 'danger' },
  { key: 'medium', label: '需要跟进', helper: '跟进、附件、资料', tone: 'warning' },
  { key: 'low', label: '平稳', helper: '暂无运营提示', tone: 'success' }
]
const customerRiskRows = computed(() => {
  const totals = customerExposureTotals.value
  const map = new Map()
  const ensureRow = (key, seed = {}) => {
    const row = map.get(key) || {
      key,
      customerId: null,
      keyword: '',
      name: '--',
      rent: 0,
      area: 0,
      rentShare: 0,
      areaShare: 0,
      contractCount: 0,
      roomCount: 0,
      overdueBalance: 0,
      maxAgingDays: 0,
      followUpLabel: '',
      followUpOverdueDays: 0,
      hasFollowUp: false,
      attachmentGap: '',
      profileGap: false
    }
    Object.assign(row, seed)
    map.set(key, row)
    return row
  }

  for (const item of contractCustomerRows.value) {
    const name = rowCustomerName(item)
    const rent = numericValue(item.rent)
    const area = numericValue(item.area)
    ensureRow(customerSignalKey(item.customer_id, name), {
      customerId: item.customer_id || null,
      keyword: name,
      name,
      rent,
      area,
      rentShare: shareValue(rent, totals.rent),
      areaShare: shareValue(area, totals.area),
      contractCount: numericValue(item.count),
      roomCount: numericValue(item.room_count)
    })
  }

  for (const customer of state.list) {
    const key = customerSignalKey(customer.id, customer.name)
    const row = ensureRow(key, {
      customerId: customer.id,
      keyword: customer.name,
      name: customer.name || '--',
      profileGap: hasProfileGap(customer)
    })
    if (!row.keyword) row.keyword = customer.name
  }

  for (const item of overdueCustomerRows.value) {
    const row = ensureRow(item.key, {
      customerId: item.customerId || null,
      keyword: item.name,
      name: item.name || '--'
    })
    row.overdueBalance = numericValue(item.overdueBalance)
    row.maxAgingDays = numericValue(item.maxAgingDays)
  }

  for (const item of state.dueFollowUps || []) {
    const name = item.customer_name || '--'
    const row = ensureRow(customerSignalKey(item.customer_id, name), {
      customerId: item.customer_id || null,
      keyword: name,
      name
    })
    row.hasFollowUp = true
    row.followUpOverdueDays = numericValue(item.overdue_days)
    row.followUpLabel = row.followUpOverdueDays > 0 ? `跟进逾期 ${row.followUpOverdueDays} 天` : `下次跟进 ${formatDate(item.next_follow_up_date)}`
  }

  for (const item of attachmentItems.value) {
    const name = item.customer_name || '--'
    const row = ensureRow(customerSignalKey(item.customer_id, name), {
      customerId: item.customer_id || null,
      keyword: name,
      name
    })
    row.attachmentGap = item.missing_category || '附件'
  }

  const byValue = [...map.values()]
    .map((row) => ({
      ...row,
      rentShare: shareValue(row.rent, totals.rent),
      areaShare: shareValue(row.area, totals.area)
    }))
    .sort((left, right) => right.rent - left.rent || right.area - left.area || left.name.localeCompare(right.name, 'zh-Hans-CN'))

  return byValue
    .map((row, index) => {
      const riskScore = customerRiskScore(row)
      const riskTier = customerRiskTier(row)
      return {
        ...row,
        valueRank: index + 1,
        valueTier: customerValueTier(row, index + 1),
        riskScore,
        riskTier,
        riskTone: riskTier === 'high' ? 'danger' : riskTier === 'medium' ? 'warning' : 'success',
        riskSignals: customerRiskSignals(row)
      }
    })
    .sort((left, right) => right.riskScore - left.riskScore || right.rent - left.rent || left.name.localeCompare(right.name, 'zh-Hans-CN'))
})
const customerRiskMatrixRows = computed(() => customerRiskTiers.map((tier) => ({
  ...tier,
  cells: customerRiskValueColumns.map((column) => {
    const rows = customerRiskRows.value.filter((row) => row.riskTier === tier.key && row.valueTier === column.key)
    return {
      key: `${tier.key}-${column.key}`,
      count: rows.length,
      rent: rows.reduce((sum, row) => sum + numericValue(row.rent), 0),
      leader: rows[0] || null
    }
  })
})))
const customerRiskRowById = computed(() => {
  const map = new Map()
  for (const row of customerRiskRows.value) {
    if (row.customerId) map.set(Number(row.customerId), row)
  }
  return map
})
const customerOverviewCards = computed(() => {
  const activeCustomerCount = contractCustomerRows.value.filter((customer) => numericValue(customer.count) > 0).length
  const activeContractCount = numericValue(state.contractStats?.active_count)
  const totalMonthlyRent = numericValue(state.contractStats?.metrics?.total_monthly_rent)
  const dueCustomerCount = numericValue(operationsMetrics.value.due_follow_up_customer_count) || state.dueTotal
  const attachmentGapCount = numericValue(state.attachmentTasks?.summary?.customer) || attachmentItems.value.length
  const profileGapCount = state.list.filter((customer) => hasProfileGap(customer)).length
  return [
    { key: 'matched', label: '客户总数', value: state.total, helper: `当前显示 ${state.list.length} 个`, tone: 'info' },
    { key: 'value', label: '在租月租', value: compactCurrency(totalMonthlyRent), helper: `${activeCustomerCount} 客户 / ${activeContractCount} 份合同`, tone: totalMonthlyRent ? 'success' : 'info' },
    { key: 'due', label: '待跟进', value: dueCustomerCount, helper: `${dueCustomerCount} 个客户`, tone: dueCustomerCount ? 'warning' : 'success' },
    { key: 'profile', label: '资料待补', value: attachmentGapCount || profileGapCount, helper: `附件 ${attachmentGapCount}，资料 ${profileGapCount}`, tone: attachmentGapCount || profileGapCount ? 'warning' : 'info' }
  ]
})
const attentionRows = computed(() => {
  const rows = []
  const usedKeys = new Set()
  for (const item of state.dueFollowUps.slice(0, 4)) {
    const key = customerSignalKey(item.customer_id, item.customer_name)
    if (usedKeys.has(key) && rows.length >= 4) continue
    rows.push({
      key: `follow-${item.customer_id}`,
      tone: item.overdue_days > 0 ? 'error' : 'warning',
      label: '跟进',
      title: item.customer_name || '--',
      meta: item.overdue_days > 0 ? `跟进逾期 ${item.overdue_days} 天` : `下次跟进 ${formatDate(item.next_follow_up_date)}`,
      detail: item.next_action || item.content || '待跟进',
      customerId: item.customer_id,
      keyword: item.customer_name
    })
    usedKeys.add(key)
  }
  for (const item of attachmentItems.value.slice(0, 3)) {
    const key = customerSignalKey(item.customer_id, item.customer_name)
    if (usedKeys.has(key) || rows.length >= 6) continue
    rows.push({
      key: `attachment-${item.id}`,
      tone: 'warning',
      label: '附件',
      title: item.customer_name || '--',
      meta: item.missing_category || '资料',
      detail: item.suggested_action || item.title || '补齐客户附件',
      customerId: item.customer_id,
      keyword: item.customer_name
    })
    usedKeys.add(key)
  }
  return rows.slice(0, 6)
})

function customerAccountStats(customer) {
  return customerStatsById.value.get(Number(customer.id)) || null
}

function customerOverdue(customer) {
  return overdueCustomerMap.value.get(customerSignalKey(customer.id, customer.name)) || null
}

function customerAttachmentTask(customer) {
  return attachmentTaskByCustomerId.value.get(Number(customer.id)) || null
}

function statusLabel(status) {
  return status === 'inactive' ? '停用' : '在用'
}

function setStatusFilter(status) {
  state.status = status
  fetchFromFirstPage()
}

function hasProfileGap(customer) {
  return !customer.contact_name || !customer.credit_code
}

function customerDueFollowUp(customer) {
  return dueFollowUpMap.value.get(Number(customer.id))
}

function customerBadges(customer) {
  const badges = []
  const due = customerDueFollowUp(customer)
  const stats = customerAccountStats(customer)
  const overdue = customerOverdue(customer)
  const attachmentTask = customerAttachmentTask(customer)
  if (overdue) badges.push({ key: 'overdue', label: `应收 ${compactCurrency(overdue.overdueBalance)}`, tone: 'danger' })
  if (due) badges.push({ key: 'due', label: due.overdue_days > 0 ? `跟进逾期 ${due.overdue_days} 天` : '待跟进', tone: due.overdue_days > 0 ? 'error' : 'warning' })
  badges.push({ key: 'status', label: statusLabel(customer.status), tone: customer.status === 'inactive' ? 'warning' : 'success' })
  if (stats && numericValue(stats.rent) > 0) badges.push({ key: 'rent', label: `月租 ${compactCurrency(stats.rent)}`, tone: 'info' })
  if ((stats?.count || customer.contract_count || 0) >= 2) badges.push({ key: 'multi-contract', label: '多合同', tone: 'info' })
  if ((customer.contract_count || 0) === 0) badges.push({ key: 'no-contract', label: '未关联合同', tone: 'compact' })
  if (attachmentTask) badges.push({ key: 'attachment-gap', label: `缺${attachmentTask.missing_category || '附件'}`, tone: 'warning' })
  if (hasProfileGap(customer)) badges.push({ key: 'profile-gap', label: '资料待补', tone: 'warning' })
  return badges
}

function customerOperatingCue(customer) {
  const overdue = customerOverdue(customer)
  const due = customerDueFollowUp(customer)
  const attachmentTask = customerAttachmentTask(customer)
  const stats = customerAccountStats(customer)
  if (overdue) return `历史逾期待核对 ${compactCurrency(overdue.overdueBalance)} · ${overdue.contractCount} 份合同 · ${roomsText(overdue)}`
  if (due) return due.next_action || due.content || (due.overdue_days > 0 ? `跟进逾期 ${due.overdue_days} 天` : `下次跟进 ${formatDate(due.next_follow_up_date)}`)
  if (attachmentTask) return attachmentTask.suggested_action || attachmentTask.reason || '补齐客户附件'
  if (stats) return `月租 ${compactCurrency(stats.rent)} · ${roomsText(stats)} · 最近到期 ${stats.nearest_end_date || '--'}`
  if ((customer.contract_count || 0) > 0) return `${customer.contract_count || 0} 个关联合同，点击查看经营详情`
  if (hasProfileGap(customer)) return '补齐客户资料，便于后续合同和跟进关联'
  return customer.notes || '客户资料已建立'
}

function customerContactLine(customer) {
  const contact = customer.contact_name || '未登记联系人'
  const channel = customer.contact_phone || customer.contact_email || '未登记联系方式'
  return `${contact} · ${channel}`
}

function customerValueLine(customer) {
  const stats = customerAccountStats(customer)
  if (!stats) return '--'
  return `${compactCurrency(stats.rent)} / 月`
}

function customerAreaLine(customer) {
  const stats = customerAccountStats(customer)
  if (!stats) return '--'
  return `${formatArea(stats.area || 0)} ㎡`
}

function customerRoomLine(customer) {
  const stats = customerAccountStats(customer)
  if (!stats) return `${customer.contract_count || 0} 份合同`
  return `${roomsText(stats)} / ${stats.count || 0} 份`
}

function customerCreditLine(customer) {
  const attachmentTask = customerAttachmentTask(customer)
  if (attachmentTask) return `缺${attachmentTask.missing_category || '附件'}`
  return customer.credit_code || '--'
}

function customerRiskRow(customer) {
  return customerRiskRowById.value.get(Number(customer.id)) || {
    riskScore: 0,
    riskTier: 'low',
    riskTone: 'success',
    riskSignals: ['暂无运营提示'],
    rentShare: 0,
    areaShare: 0,
    valueRank: 0,
    valueTier: 'low'
  }
}

function customerRiskClass(customer) {
  return `risk-${customerRiskRow(customer).riskTier}`
}

function customerRiskLabel(customer) {
  const tier = customerRiskRow(customer).riskTier
  if (tier === 'high') return '高优先'
  if (tier === 'medium') return '待关注'
  return '平稳'
}

function customerRiskBarStyle(customer) {
  const score = numericValue(customerRiskRow(customer).riskScore)
  const width = score > 0 ? Math.max(8, Math.min(100, score)) : 6
  return { width: `${width}%` }
}

function customerValueBarStyle(customer) {
  const row = customerRiskRow(customer)
  const share = numericValue(row.rentShare)
  const width = numericValue(row.rent) > 0 ? Math.max(8, Math.min(100, share)) : 4
  return { width: `${width}%` }
}

function customerValueRankLine(customer) {
  const row = customerRiskRow(customer)
  if (!row.valueRank || numericValue(row.rent) <= 0) return '暂无在租租金'
  return `价值第 ${row.valueRank} · 租金占比 ${row.rentShare}%`
}

function customerDebtLine(customer) {
  const overdue = customerOverdue(customer)
  if (!overdue) return '正常'
  return `${compactCurrency(overdue.overdueBalance)} · ${overdue.maxAgingDays || 0} 天`
}

function customerContractRoomLine(customer) {
  const stats = customerAccountStats(customer)
  if (!stats) return `${customer.contract_count || 0} 份合同`
  return `${stats.count || 0} 份合同 · ${roomsText(stats)}`
}

function customerNextAction(customer) {
  const overdue = customerOverdue(customer)
  const due = customerDueFollowUp(customer)
  const attachmentTask = customerAttachmentTask(customer)
  const stats = customerAccountStats(customer)
  if (overdue) return `核对历史逾期，查看 ${roomsText(overdue, 2)}`
  if (due) return due.next_action || due.content || (due.overdue_days > 0 ? `补跟进，已逾期 ${due.overdue_days} 天` : `按 ${formatDate(due.next_follow_up_date)} 跟进`)
  if (attachmentTask) return attachmentTask.suggested_action || `补齐${attachmentTask.missing_category || '附件'}`
  if (hasProfileGap(customer)) return '补齐联系人或信用代码'
  if (stats && numericValue(stats.rent) > 0) return '保持合同关系，关注到期节奏'
  return '查看客户详情'
}

function customerPrimaryActionLabel(customer) {
  const tier = customerRiskRow(customer).riskTier
  return tier === 'low' ? '查看' : '处理'
}

function openCustomerSignal(row) {
  const id = row?.customerId || row?.customer_id
  if (id) {
    openDetail(id)
    return
  }
  const keyword = row?.keyword || rowCustomerName(row)
  if (!keyword || keyword === '--') return
  state.keyword = keyword
  state.status = ''
  fetchFromFirstPage()
}

async function openFollowUpFromList(customer) {
  if (!canEdit.value) return
  const id = typeof customer === 'object' ? customer?.id : customer
  if (!id) return
  customerDetailId.value = id
  customerDetailFollowUpPrefill.value = null
  customerDetailFollowUpPrefillToken.value += 1
  customerDetailOpen.value = true
}

function resetForm(customer = null) {
  Object.assign(form, blankCustomer(), customer || {})
  contactPhoneWarn.value = ''
  contactEmailWarn.value = ''
}

function checkContactPhone() {
  const raw = String(form.contact_phone || '').trim()
  if (!raw) {
    contactPhoneWarn.value = ''
    return
  }
  const digits = raw.replace(/[+\-\s()（）]/g, '')
  contactPhoneWarn.value = /^\d{6,20}$/.test(digits)
    ? ''
    : '电话格式可能有误：建议 6-20 位数字，可含 +、-、空格、括号'
}

function checkContactEmail() {
  const raw = String(form.contact_email || '').trim()
  if (!raw) {
    contactEmailWarn.value = ''
    return
  }
  contactEmailWarn.value = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)
    ? ''
    : '邮箱格式可能有误：应类似 name@example.com'
}

function invalidateCustomerWorkspaceCache() {
  invalidateApiCache('/api/customers')
  invalidateApiCache('/api/contracts/stats')
  invalidateApiCache('/api/dashboard/operations')
  invalidateApiCache('/api/attachments/completeness-tasks')
}

async function fetchCustomers(options = {}) {
  const requestToken = ++customersRequestToken
  state.loading = true
  try {
    const params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize)
    })
    if (state.keyword) params.set('keyword', state.keyword)
    if (state.status) params.set('status', state.status)
    const data = await cachedApi(`/api/customers?${params}`, {
      force: options.force,
      ttlMs: CUSTOMER_WORKSPACE_CACHE_TTL_MS
    })
    if (requestToken !== customersRequestToken) return
    state.list = data.items || []
    state.total = data.total || 0
    hasLoadedCustomers.value = true
  } catch (error) {
    if (requestToken === customersRequestToken) {
      emit('notify', { type: 'error', text: error.message })
    }
  } finally {
    if (requestToken === customersRequestToken) {
      state.loading = false
    }
  }
}

async function fetchDueFollowUps(options = {}) {
  state.dueLoading = true
  try {
    const data = await cachedApi('/api/customers/follow-ups/due?limit=8', {
      force: options.force,
      ttlMs: CUSTOMER_WORKSPACE_CACHE_TTL_MS
    })
    state.dueFollowUps = data.items || []
    state.dueTotal = data.total || 0
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    state.dueLoading = false
  }
}

async function fetchWorkspaceSignals(options = {}) {
  const requestToken = ++workspaceSignalRequestToken
  state.signalLoading = true
  const [contractStatsResult, operationsResult, attachmentTasksResult] = await Promise.allSettled([
    cachedApi('/api/contracts/stats', { force: options.force, ttlMs: CUSTOMER_WORKSPACE_CACHE_TTL_MS }),
    cachedApi('/api/dashboard/operations?include_trend=false&queue_limit=8&risk_limit=0', {
      force: options.force,
      ttlMs: CUSTOMER_WORKSPACE_CACHE_TTL_MS
    }),
    cachedApi('/api/attachments/completeness-tasks?target_type=customer&limit=12', {
      force: options.force,
      ttlMs: CUSTOMER_WORKSPACE_CACHE_TTL_MS
    })
  ])
  if (requestToken !== workspaceSignalRequestToken) return
  const errors = []
  if (contractStatsResult.status === 'fulfilled') {
    state.contractStats = contractStatsResult.value || {}
  } else {
    errors.push(contractStatsResult.reason)
  }
  if (operationsResult.status === 'fulfilled') {
    state.operations = operationsResult.value || {}
  } else {
    errors.push(operationsResult.reason)
  }
  if (attachmentTasksResult.status === 'fulfilled') {
    state.attachmentTasks = attachmentTasksResult.value || { total: 0, summary: {}, items: [] }
  } else {
    errors.push(attachmentTasksResult.reason)
  }
  if (errors.length) emit('notify', { type: 'error', text: errors[0]?.message || '客户工作台信号读取失败' })
  state.signalLoading = false
}

async function refreshCustomerWorkspace(options = {}) {
  await Promise.all([fetchDueFollowUps(options), fetchWorkspaceSignals(options)])
}

function fetchFromFirstPage() {
  state.page = 1
  return fetchCustomers()
}

function changePage(delta) {
  state.page = Math.min(totalPages.value, Math.max(1, state.page + delta))
  fetchCustomers()
}

function changePageSize(size) {
  state.pageSize = size
  state.page = 1
  fetchCustomers()
}

function customerQueryString() {
  const params = new URLSearchParams()
  if (state.keyword) params.set('keyword', state.keyword)
  if (state.status) params.set('status', state.status)
  return params.toString()
}

function exportCustomers() {
  const query = customerQueryString()
  openDownload(`/api/customers/export${query ? `?${query}` : ''}`)
}

function exportAllFollowUps() {
  const params = new URLSearchParams()
  if (state.keyword) params.set('keyword', state.keyword)
  openDownload(`/api/customers/follow-ups/export${params.toString() ? `?${params}` : ''}`)
}

function toggleCustomerBatchActions() {
  batchActionsOpen.value = !batchActionsOpen.value
}

function closeCustomerBatchActions() {
  batchActionsOpen.value = false
}

function downloadCustomerTemplate() {
  closeCustomerBatchActions()
  openDownload('/api/customers/import/template')
}

function downloadFollowUpTemplate() {
  closeCustomerBatchActions()
  openDownload('/api/customers/follow-ups/import/template')
}

function exportCustomersFromBatch() {
  closeCustomerBatchActions()
  exportCustomers()
}

function exportFollowUpsFromBatch() {
  closeCustomerBatchActions()
  exportAllFollowUps()
}

function triggerCustomerPreviewFromBatch() {
  closeCustomerBatchActions()
  triggerCustomerPreview()
}

function triggerCustomerImportFromBatch() {
  closeCustomerBatchActions()
  triggerCustomerImport()
}

function triggerFollowUpPreviewFromBatch() {
  closeCustomerBatchActions()
  triggerFollowUpPreview()
}

function triggerFollowUpImportFromBatch() {
  closeCustomerBatchActions()
  triggerFollowUpImport()
}

function triggerCustomerImport() {
  customerImportInput.value?.click()
}

function triggerFollowUpImport() {
  followUpImportInput.value?.click()
}

function triggerCustomerPreview() {
  customerPreviewInput.value?.click()
}

function triggerFollowUpPreview() {
  followUpPreviewInput.value?.click()
}

function importIssueText(issue) {
  if (!issue) return ''
  if (typeof issue === 'string') return issue
  const parts = [
    issue.row_number || issue.row ? `第 ${issue.row_number || issue.row} 行` : '',
    issue.customer_name ? `客户 ${issue.customer_name}` : '',
    issue.field ? `字段 ${issue.field}` : ''
  ].filter(Boolean)
  return `${parts.length ? `${parts.join(' ')}：` : ''}${issue.reason || issue.message || '导入失败'}`
}

function buildImportResult(result, label) {
  const issues = (result.errors || []).map(importIssueText).filter(Boolean)
  return {
    type: result.failed ? 'warning' : 'success',
    title: `${label}完成：成功 ${result.imported || 0} 条，失败 ${result.failed || 0} 条`,
    issues: issues.slice(0, 5),
    extraCount: Math.max(0, issues.length - 5)
  }
}

function buildPreviewResult(result, label) {
  const errors = (result.errors || []).map(importIssueText).filter(Boolean)
  const warnings = (result.warnings || []).map(importIssueText).filter(Boolean)
  const errorRows = result.error_rows || 0
  const warningRows = result.warning_rows || 0
  return {
    type: errorRows ? 'error' : (warningRows ? 'warning' : 'success'),
    title: `${label}预检：总 ${result.total_rows || 0} 行，可导入 ${result.valid_rows || 0} 行，错误 ${errorRows} 行，警告 ${warningRows} 行`,
    stats: [
      { label: '总行数', value: result.total_rows || 0 },
      { label: '可导入', value: result.valid_rows || 0 },
      { label: '错误行', value: errorRows },
      { label: '警告行', value: warningRows }
    ],
    issues: errors.slice(0, 8),
    warnings: warnings.slice(0, 8),
    extraCount: Math.max(0, errors.length + warnings.length - 16),
    ready: !errorRows
  }
}

async function previewImport(event, endpoint, label) {
  const file = event.target.files?.[0]
  if (!file) return
  const payload = new FormData()
  payload.append('file', file)
  try {
    const result = await api(endpoint, { method: 'POST', body: payload })
    importResult.value = buildPreviewResult(result, label)
    emit('notify', {
      text: importResult.value.ready ? `${label}预检通过，可以导入` : `${label}预检发现错误，请先修正`,
      type: importResult.value.ready ? 'info' : 'error'
    })
  } catch (error) {
    importResult.value = { type: 'error', title: error.message || `${label}预检失败`, stats: [], issues: [], warnings: [], extraCount: 0, ready: false }
    emit('notify', { type: 'error', text: importResult.value.title })
  } finally {
    event.target.value = ''
  }
}

async function previewCustomers(event) {
  await previewImport(event, '/api/customers/import/preview', '客户导入')
}

async function previewFollowUps(event) {
  await previewImport(event, '/api/customers/follow-ups/import/preview', '跟进导入')
}

async function importCustomers(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const payload = new FormData()
  payload.append('file', file)
  try {
    const result = await api('/api/customers/import', { method: 'POST', body: payload })
    importResult.value = buildImportResult(result, '客户导入')
    emit('notify', { text: importResult.value.title, type: importResult.value.type === 'warning' ? 'error' : 'info' })
    invalidateCustomerWorkspaceCache()
    await fetchCustomers({ force: true })
    await fetchWorkspaceSignals({ force: true })
  } catch (error) {
    importResult.value = { type: 'error', title: error.message || '客户导入失败', issues: [], extraCount: 0 }
    emit('notify', { type: 'error', text: importResult.value.title })
  } finally {
    event.target.value = ''
  }
}

async function importFollowUps(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const payload = new FormData()
  payload.append('file', file)
  try {
    const result = await api('/api/customers/follow-ups/import', { method: 'POST', body: payload })
    importResult.value = buildImportResult(result, '跟进导入')
    emit('notify', { text: importResult.value.title, type: importResult.value.type === 'warning' ? 'error' : 'info' })
    invalidateCustomerWorkspaceCache()
    await refreshCustomerWorkspace({ force: true })
    if (customerDetailOpen.value) customerDetailRefreshToken.value += 1
  } catch (error) {
    importResult.value = { type: 'error', title: error.message || '跟进导入失败', issues: [], extraCount: 0 }
    emit('notify', { type: 'error', text: importResult.value.title })
  } finally {
    event.target.value = ''
  }
}

function openCreate() {
  if (!canCreateCustomer.value) return
  editingId.value = null
  resetForm()
  formOpen.value = true
}

function openEdit(customer) {
  if (!canEdit.value) return
  editingId.value = customer.id
  resetForm(customer)
  formOpen.value = true
}

async function openDetail(customer) {
  const id = typeof customer === 'object' ? customer?.id : customer
  if (!id) return
  customerDetailId.value = id
  customerDetailFollowUpPrefill.value = null
  customerDetailOpen.value = true
}

async function saveCustomer() {
  if (
    !canEdit.value
    || (!editingId.value && !canManageCustomerMaster.value)
    || !form.name.trim()
  ) return
  saving.value = true
  try {
    const path = editingId.value ? `/api/customers/${editingId.value}` : '/api/customers'
    const method = editingId.value ? 'PUT' : 'POST'
    const payload = canManageCustomerMaster.value
      ? {
          name: form.name,
          short_name: form.short_name,
          credit_code: form.credit_code,
          customer_type: form.customer_type,
          industry: form.industry,
          contact_name: form.contact_name,
          contact_phone: form.contact_phone,
          contact_email: form.contact_email,
          address: form.address,
          notes: form.notes,
          status: form.status,
          local_contact: form.local_contact,
          local_notes: form.local_notes,
          owner: form.owner
        }
      : {
          local_contact: form.local_contact,
          local_notes: form.local_notes,
          owner: form.owner
        }
    const saved = await api(path, { method, body: JSON.stringify(payload) })
    emit('notify', { text: editingId.value ? '客户已更新' : '客户已创建' })
    formOpen.value = false
    invalidateCustomerWorkspaceCache()
    await fetchCustomers({ force: true })
    await fetchWorkspaceSignals({ force: true })
    if (customerDetailOpen.value && Number(customerDetailId.value) === Number(saved.id)) customerDetailRefreshToken.value += 1
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    saving.value = false
  }
}

async function applyFocusedCustomer(customerId) {
  if (!customerId) return
  await openDetail(customerId)
  if (
    props.focusFollowUpPrefillToken &&
    props.focusFollowUpPrefillToken !== appliedFollowUpPrefillToken &&
    props.focusFollowUpPrefill
  ) {
    customerDetailFollowUpPrefill.value = props.focusFollowUpPrefill
    customerDetailFollowUpPrefillToken.value += 1
    appliedFollowUpPrefillToken = props.focusFollowUpPrefillToken
  }
}

async function applyDrilldownIntent() {
  const intent = props.drilldownIntent
  if (!intent || intent.target !== 'customers' || props.drilldownToken === appliedDrilldownToken) return
  appliedDrilldownToken = props.drilldownToken
  const filters = intent.filters || {}
  state.keyword = filters.keyword || ''
  state.status = filters.status || ''
  await fetchFromFirstPage()
  if (filters.customerId) await openDetail(filters.customerId)
  emit('notify', { text: intent.message || '已应用来自经营分析的客户上下文' })
}

async function handleCustomerDetailUpdated() {
  invalidateCustomerWorkspaceCache()
  await Promise.all([fetchCustomers({ force: true }), refreshCustomerWorkspace({ force: true })])
}

watch(
  () => [props.focusCustomerId, props.focusToken, props.focusFollowUpPrefillToken],
  ([customerId]) => {
    applyFocusedCustomer(customerId)
  }
)

watch(
  () => [props.drilldownIntent, props.drilldownToken],
  () => {
    applyDrilldownIntent()
  }
)

onMounted(async () => {
  await Promise.all([fetchCustomers(), refreshCustomerWorkspace()])
  await applyDrilldownIntent()
  if (props.focusCustomerId) await applyFocusedCustomer(props.focusCustomerId)
})
</script>

<template>
  <section class="content" data-testid="customers-page">
    <article class="card customer-workspace-panel" data-testid="customer-workspace-summary">
      <div class="customer-workspace-head">
        <div>
          <div class="section-label">{{ $t("客户工作台") }}</div>
          <h3 class="card-title">{{ $t("客户运营重点") }}</h3>
        </div>
        <div class="btn-row">
          <button class="btn btn-compact" :disabled="state.dueLoading || state.signalLoading" @click="refreshCustomerWorkspace">
            {{ $t(state.signalLoading || state.dueLoading ? '刷新中...' : '刷新信号') }}
          </button>
          <button v-if="canCreateCustomer" class="btn btn-primary btn-compact" @click="openCreate">{{ $t("新增客户") }}</button>
        </div>
      </div>
      <div class="customer-kpi-grid">
        <div v-for="card in customerOverviewCards" :key="card.key" class="customer-kpi-card metric-tile" :class="card.tone">
          <span>{{ $t(card.label) }}</span>
          <strong>{{ $t(card.value) }}</strong>
          <small>{{ $t(card.helper) }}</small>
        </div>
      </div>
      <div class="customer-account-grid" data-testid="customer-signal-band">
        <section class="customer-value-board">
          <div class="customer-board-head">
            <span>{{ $t("租金贡献") }}</span>
            <small>{{ $t("Top 5 在租客户") }}</small>
          </div>
          <div class="customer-value-list">
            <button
              v-for="(row, index) in topValueCustomers"
              :key="`${row.customer_id || row.tenant_name}-${index}`"
              type="button"
              class="customer-value-row"
              @click="openCustomerSignal(row)"
            >
              <span class="customer-rank-index">{{ $t(index + 1) }}</span>
              <span class="customer-value-main">
                <strong class="customer-name-ellipsis" :title="$t(rowCustomerName(row))">{{ $t(rowCustomerName(row)) }}</strong>
                <small>{{ $t(roomsText(row)) }} · {{ $t(row.count || 0) }}{{ $t(" 份合同") }}</small>
              </span>
              <span class="customer-value-metrics">
                <strong>{{ $t(compactCurrency(row.rent)) }}</strong>
                <small>{{ $t(formatArea(row.area || 0)) }} {{ $t("㎡") }}</small>
              </span>
            </button>
            <div v-if="!topValueCustomers.length" class="compact-empty compact-empty-tight">{{ $t("暂无在租客户") }}</div>
          </div>
        </section>

        <section class="customer-next-board">
          <div class="customer-board-head">
            <span>{{ $t("下一动作") }}</span>
            <small>{{ $t("跟进 / 资料 / 关系") }}</small>
          </div>
          <div class="customer-attention-strip">
            <button
              v-for="row in attentionRows"
              :key="row.key"
              type="button"
              class="customer-attention-item"
              :class="row.tone"
              @click="openCustomerSignal(row)"
            >
              <span class="badge" :class="row.tone">{{ $t(row.label) }}</span>
              <div>
                <strong class="customer-name-ellipsis" :title="$t(row.title)">{{ $t(row.title) }}</strong>
                <span>{{ $t(row.detail) }}</span>
              </div>
              <small>{{ $t(row.meta) }}</small>
            </button>
            <div v-if="!attentionRows.length" class="compact-empty compact-empty-tight">{{ $t("暂无客户动作") }}</div>
          </div>
        </section>
      </div>
    </article>

    <details class="demo-secondary-details customer-finance-details">
      <summary>
        <span>{{ $t("历史逾期客户") }}</span>
        <strong>{{ $t(overdueCustomerRows.length) }}{{ $t(" 个客户") }}</strong>
      </summary>
      <section class="customer-exposure-board">
        <div class="customer-board-head">
          <span>{{ $t("历史逾期") }}</span>
          <small>{{ $t(overdueCustomerRows.length) }}{{ $t(" 个客户") }}</small>
        </div>
        <div class="customer-exposure-list">
          <button
            v-for="row in overdueCustomerRows.slice(0, 4)"
            :key="row.key"
            type="button"
            class="customer-exposure-row"
            @click="openCustomerSignal(row)"
          >
            <span>
              <strong class="customer-name-ellipsis" :title="row.name">{{ row.name }}</strong>
              <small>{{ $t(row.contractCount) }}{{ $t(" 份合同 · ") }}{{ $t(roomsText(row, 2)) }}</small>
            </span>
            <span>
              <strong>{{ $t(compactCurrency(row.overdueBalance)) }}</strong>
              <small>{{ $t(row.maxAgingDays || 0) }}{{ $t(" 天") }}</small>
            </span>
          </button>
          <div v-if="!overdueCustomerRows.length" class="compact-empty compact-empty-tight">{{ $t("暂无历史逾期客户") }}</div>
        </div>
      </section>
    </details>

    <details class="demo-secondary-details customer-visual-details">
      <summary>
        <span>{{ $t("客户结构图") }}</span>
        <strong>{{ $t("集中度 / 待办分布") }}</strong>
      </summary>
    <section class="customer-visual-grid" data-testid="customer-value-risk-view">
      <ChartCard
        :title="$t(&quot;当前合同租金集中度&quot;)"
        :subtitle="$t(&quot;按当前在租合同月租汇总&quot;)"
        :option="customerConcentrationOption"
        :empty="!customerConcentrationRows.length"
        :empty-text="$t(&quot;暂无在租客户数据&quot;)"
        :height="260"
        :insight="customerConcentrationInsight"
        tone="info"
        data-testid="customer-concentration-chart"
      >
        <div v-if="customerConcentrationRows.length" class="customer-concentration-list">
          <button
            v-for="(row, index) in customerConcentrationRows"
            :key="row.key"
            type="button"
            class="customer-concentration-row"
            @click="openCustomerSignal(row)"
          >
            <span class="customer-rank-index">{{ $t(index + 1) }}</span>
            <span class="customer-concentration-main">
              <strong class="customer-name-ellipsis" :title="row.name">{{ row.name }}</strong>
              <small>{{ $t(row.contractCount) }}{{ $t(" 份合同 · ") }}{{ $t(roomsText(row, 2)) }}</small>
            </span>
            <span class="customer-concentration-metrics">
              <strong>{{ $t(compactCurrency(row.rent)) }}</strong>
              <small>{{ $t("租金 ") }}{{ $t(row.rentShare) }}{{ $t("% · 面积 ") }}{{ $t(row.areaShare) }}%</small>
            </span>
          </button>
        </div>
      </ChartCard>

      <article class="chart-card customer-risk-card tone-warning" data-testid="customer-risk-matrix">
        <header class="chart-card-header">
          <div class="chart-card-copy">
            <span class="section-label">{{ $t("重点观察") }}</span>
            <h3>{{ $t("客户待办分布") }}</h3>
            <p>{{ $t("租金贡献 × 待办类型") }}</p>
          </div>
          <div class="chart-card-actions">
            <span class="metric-pill warning">{{ $t("关注 ") }}{{ customerRiskRows.filter((row) => row.riskTier !== 'low').length }}</span>
          </div>
        </header>

        <div v-if="customerRiskRows.length" class="customer-risk-matrix-grid">
          <div class="customer-risk-matrix-head">
            <span>{{ $t("状态 / 租金") }}</span>
            <span v-for="column in customerRiskValueColumns" :key="column.key">
              {{ $t(column.label) }}
              <small>{{ $t(column.helper) }}</small>
            </span>
          </div>
          <div v-for="tier in customerRiskMatrixRows" :key="tier.key" class="customer-risk-matrix-row">
            <div class="customer-risk-tier" :class="tier.tone">
              <strong>{{ $t(tier.label) }}</strong>
              <small>{{ $t(tier.helper) }}</small>
            </div>
            <button
              v-for="cell in tier.cells"
              :key="cell.key"
              type="button"
              class="customer-risk-cell"
              :class="[tier.tone, { empty: !cell.count }]"
              :disabled="!cell.leader"
              @click="openCustomerSignal(cell.leader)"
            >
              <strong>{{ $t(cell.count) }}</strong>
              <small>{{ $t(compactCurrency(cell.rent)) }}</small>
            </button>
          </div>
        </div>
        <div v-else class="chart-empty customer-risk-empty">{{ $t("暂无客户分布数据") }}</div>

        <div v-if="customerRiskRows.length" class="customer-risk-rank">
          <button
            v-for="row in customerRiskRows.slice(0, 5)"
            :key="row.key"
            type="button"
            class="customer-risk-rank-row"
            :class="row.riskTone"
            @click="openCustomerSignal(row)"
          >
            <span class="customer-risk-score">{{ $t(row.riskScore) }}</span>
            <span class="customer-risk-main">
              <strong class="customer-name-ellipsis" :title="row.name">{{ row.name }}</strong>
              <small>{{ $t(row.riskSignals.join(' · ')) }}</small>
            </span>
            <span class="customer-risk-value">
              <strong>{{ $t(compactCurrency(row.rent)) }}</strong>
              <small>{{ $t("租金 ") }}{{ $t(row.rentShare) }}{{ $t("% · 面积 ") }}{{ $t(row.areaShare) }}%</small>
            </span>
          </button>
        </div>
      </article>
    </section>
    </details>

    <article class="card table-card">
      <div class="contract-toolbar-shell customer-toolbar-shell">
        <div class="customer-search-stack">
          <div class="filter-cluster customer-filter-cluster">
            <input v-model="state.keyword" class="input" :placeholder="$t(&quot;客户名称 / 简称 / 信用代码&quot;)" @keyup.enter="fetchFromFirstPage" />
            <button class="btn contract-search-btn" :disabled="state.loading" @click="fetchFromFirstPage">{{ $t("搜索") }}</button>
          </div>
          <div class="customer-filter-chips" data-testid="customer-status-chips">
            <button type="button" :class="{ active: state.status === '' }" @click="setStatusFilter('')">{{ $t("全部") }}</button>
            <button type="button" :class="{ active: state.status === 'active' }" @click="setStatusFilter('active')">{{ $t("在用") }}</button>
            <button type="button" :class="{ active: state.status === 'inactive' }" @click="setStatusFilter('inactive')">{{ $t("停用") }}</button>
          </div>
        </div>
        <div class="action-cluster customer-action-cluster">
          <span v-if="customersRefreshing" class="metric-pill info refreshing-indicator">{{ $t("同步中") }}</span>
          <div v-if="canExport" class="customer-action-group customer-action-group-primary">
            <button v-if="canCreateCustomer" class="btn btn-primary" @click="openCreate">{{ $t("新增客户") }}</button>
            <div
              class="customer-batch-actions"
              data-testid="customer-batch-actions"
              @keydown.esc.stop.prevent="closeCustomerBatchActions"
            >
              <button
                type="button"
                class="btn customer-batch-trigger"
                data-testid="customer-batch-trigger"
                aria-controls="customer-batch-panel"
                :aria-expanded="String(batchActionsOpen)"
                @click="toggleCustomerBatchActions"
              >{{ $t(" 批量处理 ") }}</button>
              <div
                v-if="batchActionsOpen"
                id="customer-batch-panel"
                class="customer-batch-popover"
                data-testid="customer-batch-panel"
              >
                <div class="customer-batch-head">
                  <strong>{{ $t("批量处理") }}</strong>
                  <button type="button" class="icon-btn" :title="$t(&quot;关闭&quot;)" @click="closeCustomerBatchActions">×</button>
                </div>
                <div class="customer-batch-grid">
                  <section v-if="isAdmin && canManageCustomerMaster" class="customer-batch-section">
                    <span>{{ $t("客户资料") }}</span>
                    <div class="customer-batch-buttons">
                      <button class="btn" @click="downloadCustomerTemplate">{{ $t("模板") }}</button>
                      <button class="btn" data-testid="customer-preview-button" @click="triggerCustomerPreviewFromBatch">{{ $t("校验") }}</button>
                      <button class="btn" @click="triggerCustomerImportFromBatch">{{ $t("导入") }}</button>
                    </div>
                  </section>
                  <section v-if="isAdmin" class="customer-batch-section">
                    <span>{{ $t("跟进记录") }}</span>
                    <div class="customer-batch-buttons">
                      <button class="btn" @click="downloadFollowUpTemplate">{{ $t("模板") }}</button>
                      <button class="btn" data-testid="follow-up-preview-button" @click="triggerFollowUpPreviewFromBatch">{{ $t("校验") }}</button>
                      <button class="btn" @click="triggerFollowUpImportFromBatch">{{ $t("导入") }}</button>
                    </div>
                  </section>
                  <section class="customer-batch-section">
                    <span>{{ $t("导出") }}</span>
                    <div class="customer-batch-buttons">
                      <button class="btn" @click="exportCustomersFromBatch">{{ $t("客户") }}</button>
                      <button class="btn" @click="exportFollowUpsFromBatch">{{ $t("跟进") }}</button>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          </div>
          <input ref="customerImportInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importCustomers" />
          <input ref="followUpImportInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importFollowUps" />
          <input ref="customerPreviewInput" class="visually-hidden" type="file" accept=".xlsx,.xls" data-testid="customer-preview-input" @change="previewCustomers" />
          <input ref="followUpPreviewInput" class="visually-hidden" type="file" accept=".xlsx,.xls" data-testid="follow-up-preview-input" @change="previewFollowUps" />
        </div>
      </div>

      <div v-if="importResult" class="import-result-panel" :class="importResult.type" data-testid="customer-import-preview-result">
        <div class="import-result-header">
          <strong>{{ $t(importResult.title) }}</strong>
          <button class="icon-btn" :title="$t(&quot;关闭导入结果&quot;)" @click="importResult = null">×</button>
        </div>
        <div v-if="importResult.stats?.length" class="import-preview-stats">
          <div v-for="stat in importResult.stats" :key="stat.label" class="import-preview-stat">
            <span>{{ $t(stat.label) }}</span>
            <strong>{{ $t(stat.value) }}</strong>
          </div>
        </div>
        <div v-if="importResult.ready" class="helper-text">{{ $t("校验通过，可导入。") }}</div>
        <ul v-if="importResult.issues.length" class="import-result-list">
          <li v-for="issue in importResult.issues" :key="issue">{{ $t(issue) }}</li>
        </ul>
        <ul v-if="importResult.warnings?.length" class="import-result-list warning-list">
          <li v-for="warning in importResult.warnings" :key="warning">{{ $t(warning) }}</li>
        </ul>
        <div v-if="importResult.extraCount" class="helper-text">{{ $t("另有 ") }}{{ $t(importResult.extraCount) }}{{ $t(" 条未显示") }}</div>
      </div>

      <div class="customer-worklist" data-testid="customers-table">
        <TableSkeleton v-if="customersInitialLoading" class="customers-table-skeleton" :rows="6" :columns="4" />
        <template v-else>
          <article
            v-for="customer in state.list"
            :key="customer.id"
            class="customer-work-row customer-compact-row"
            :class="customerRiskClass(customer)"
            data-testid="customer-compact-row"
          >
            <div class="customer-row-risk-rail" aria-hidden="true"></div>
            <div class="customer-work-main">
              <div class="customer-name-line">
                <strong :title="customer.name">{{ customer.name }}</strong>
                <span class="badge" :class="customer.status === 'inactive' ? 'warning' : 'success'">{{ $t(statusLabel(customer.status)) }}</span>
                <span v-if="customer.short_name" :title="customer.short_name">{{ customer.short_name }}</span>
              </div>
              <p>{{ $t(customerNextAction(customer)) }}</p>
              <div class="customer-compact-facts">
                <span>{{ $t(customerContractRoomLine(customer)) }}</span>
                <span>{{ $t(customerValueRankLine(customer)) }}</span>
              </div>
            </div>
            <div class="customer-work-meta">
              <div class="customer-compact-metric">
                <span>{{ $t("月租 / 面积") }}</span>
                <strong>{{ $t(customerValueLine(customer)) }} · {{ $t(customerAreaLine(customer)) }}</strong>
                <span class="customer-mini-track" aria-hidden="true">
                  <span class="customer-value-bar" :style="customerValueBarStyle(customer)"></span>
                </span>
              </div>
              <div class="customer-compact-metric">
                <span>{{ $t("应收状态") }}</span>
                <strong>{{ $t(customerDebtLine(customer)) }}</strong>
                <span class="customer-mini-track" data-testid="customer-risk-bar" :aria-label="$t(customerRiskLabel(customer))">
                  <span class="customer-risk-bar" :style="customerRiskBarStyle(customer)"></span>
                </span>
              </div>
              <div class="customer-compact-metric">
                <span>{{ $t("运营提示") }}</span>
                <strong>{{ $t(customerRiskLabel(customer)) }}</strong>
                <small>{{ $t(customerRiskRow(customer).riskSignals.join(' · ')) }}</small>
              </div>
              <div class="customer-compact-metric">
                <span>{{ $t("下一步") }}</span>
                <strong>{{ $t(customerPrimaryActionLabel(customer)) }}</strong>
                <small>{{ $t(customerOperatingCue(customer)) }}</small>
              </div>
            </div>
            <div class="customer-row-actions customer-row-action-strip" data-testid="customer-row-action-strip">
              <div class="customer-row-primary-action" data-testid="customer-row-primary-action">
                <button class="btn btn-primary btn-compact" data-testid="customer-view-button" @click="openDetail(customer)">
                  {{ $t(customerPrimaryActionLabel(customer)) }}
                </button>
              </div>
            </div>
          </article>
          <div v-if="!state.list.length" class="list-empty">{{ $t("暂无客户资料") }}</div>
        </template>
      </div>

      <div class="table-footer">
        <div class="page-size-control">
          <span class="helper-text">{{ $t("每页显示") }}</span>
          <div class="page-size-switch" data-testid="customers-page-size">
            <button
              v-for="option in pageSizeOptions"
              :key="option.value"
              type="button"
              :class="{ active: state.pageSize === option.value }"
              @click="changePageSize(option.value)"
            >
              {{ $t(option.label) }}
            </button>
          </div>
        </div>
        <span class="helper-text">{{ $t("共 ") }}{{ $t(state.total) }}{{ $t(" 条 · ") }}{{ $t(showingText) }}</span>
        <span class="spacer"></span>
        <button v-if="hasPagination" class="btn btn-compact" :disabled="state.page <= 1" @click="changePage(-1)">{{ $t("上一页") }}</button>
        <span v-if="hasPagination" class="helper-text">{{ $t(pageLabel) }}</span>
        <button v-if="hasPagination" class="btn btn-compact" :disabled="state.page >= totalPages" @click="changePage(1)">{{ $t("下一页") }}</button>
      </div>
    </article>
  </section>

  <BaseModal :open="formOpen" :title="$t(editingId ? '编辑客户' : '新增客户')" @close="formOpen = false">
    <div class="form-grid">
      <div class="form-field full">
        <span class="section-label">{{ $t("各楼宇共用资料") }}</span>
        <span class="helper-text">{{ $t("各楼宇共用的客户资料") }}</span>
      </div>
      <label class="form-field">
        <span class="section-label">{{ $t("客户名称") }}</span>
        <input v-model="form.name" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("客户简称") }}</span>
        <input v-model="form.short_name" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("统一社会信用代码") }}</span>
        <input v-model="form.credit_code" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("状态") }}</span>
        <select v-model="form.status" class="select" :disabled="!canManageCustomerMaster">
          <option value="active">{{ $t("在用") }}</option>
          <option value="inactive">{{ $t("停用") }}</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("客户类型") }}</span>
        <input v-model="form.customer_type" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("行业") }}</span>
        <input v-model="form.industry" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("共享联系人") }}</span>
        <input v-model="form.contact_name" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("共享联系电话") }}</span>
        <input v-model="form.contact_phone" type="tel" maxlength="30" class="input" :disabled="!canManageCustomerMaster" @blur="checkContactPhone" @input="contactPhoneWarn = ''" />
        <span v-if="contactPhoneWarn" class="form-field-warn">{{ $t(contactPhoneWarn) }}</span>
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("共享邮箱") }}</span>
        <input v-model="form.contact_email" type="email" maxlength="100" class="input" :disabled="!canManageCustomerMaster" @blur="checkContactEmail" @input="contactEmailWarn = ''" />
        <span v-if="contactEmailWarn" class="form-field-warn">{{ $t(contactEmailWarn) }}</span>
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("地址") }}</span>
        <input v-model="form.address" class="input" :disabled="!canManageCustomerMaster" />
      </label>
      <label class="form-field full">
        <span class="section-label">{{ $t("共享备注") }}</span>
        <textarea v-model="form.notes" class="textarea" :disabled="!canManageCustomerMaster" />
      </label>
      <div class="form-field full">
        <span class="section-label">{{ $t("当前楼宇资料") }}</span>
        <span class="helper-text">{{ $t("当前楼宇的客户资料") }}</span>
      </div>
      <label class="form-field">
        <span class="section-label">{{ $t("楼宇联系人") }}</span>
        <input v-model="form.local_contact" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("楼宇负责人") }}</span>
        <input v-model="form.owner" class="input" />
      </label>
      <label class="form-field full">
        <span class="section-label">{{ $t("楼宇备注") }}</span>
        <textarea v-model="form.local_notes" class="textarea" />
      </label>
    </div>

    <template #footer>
      <button class="btn" @click="formOpen = false">{{ $t("取消") }}</button>
      <button
        class="btn btn-primary"
        :disabled="saving || !form.name.trim() || (!editingId && !canManageCustomerMaster)"
        @click="saveCustomer"
      >{{ $t("保存") }}</button>
    </template>
  </BaseModal>

  <CustomerDetailModal
    :open="customerDetailOpen"
    :customer-id="customerDetailId"
    :user="user"
    :property-scope="propertyScope"
    :follow-up-prefill="customerDetailFollowUpPrefill"
    :follow-up-prefill-token="customerDetailFollowUpPrefillToken"
    :refresh-token="customerDetailRefreshToken"
    @close="customerDetailOpen = false"
    @notify="emit('notify', $event)"
    @open-contract="emit('open-contract', $event)"
    @edit-customer="openEdit"
    @updated="handleCustomerDetailUpdated"
  />

</template>
