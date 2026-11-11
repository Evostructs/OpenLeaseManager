<script setup>
import { t } from '../i18n/index.js'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatArea, formatCurrencyFixed, formatDate, formatIssueType } from '../lib/format'
import { cachedApi, invalidateApiCache } from '../lib/prefetchCache'
import { startOperation } from '../lib/operationTelemetry'
import SavedFilterViews from '../components/SavedFilterViews.vue'
import { buildViewLink } from '../lib/savedViews'
import ResponsiveDisclosure from '../components/ResponsiveDisclosure.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ContractFormModal from '../components/ContractFormModal.vue'
import ContractDetailModal from '../components/ContractDetailModal.vue'
import ContractDepositModal from '../components/ContractDepositModal.vue'
import ContractRentTermModal from '../components/ContractRentTermModal.vue'
import ContractRoomChangeModal from '../components/ContractRoomChangeModal.vue'
import ContractTerminateModal from '../components/ContractTerminateModal.vue'
import ContractChangePreviewModal from '../components/ContractChangePreviewModal.vue'
import DocumentGenerateModal from '../components/DocumentGenerateModal.vue'
import PdfUploadModal from '../components/PdfUploadModal.vue'
import BaseModal from '../components/BaseModal.vue'
import TableSkeleton from '../components/TableSkeleton.vue'
import {
  buildImportErrorResult,
  buildImportSuccessResult,
  createContractDomain,
  formatImportIssue,
  normalizeImportIssues,
  roomSummary
} from '../lib/contractsDomain'
import { profileSpaceOptions } from '../lib/roomsDomain'

const props = defineProps({
  user: {
    type: Object,
    required: true
  },
  stats: {
    type: Object,
    default: () => ({})
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
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

const emit = defineEmits(['notify', 'refresh-dashboard', 'open-customer', 'select-tab'])
const canEdit = computed(() => props.user?.role === 'admin' && props.propertyScope?.type === 'building')
const canExport = computed(() => ['admin', 'editor'].includes(props.user?.role))
const statusOptions = ['在租', '即将到期', '待生效', '已到期', '已续租', '已变更', '已退租', '逾期占用']
const closedStatuses = new Set(['已到期', '已续租', '已变更', '已退租'])
const activeStatuses = new Set(['在租', '即将到期', '逾期占用'])
const {
  contractActionPriority,
  contractActionReason,
  contractAttentionTags,
  contractNextAction,
  depositAttentionTag,
  expiryLabel,
  expiryTone,
  isExpiringSoon,
  shouldFlagMissingPdf
} = createContractDomain({ activeStatuses, closedStatuses })
const pageSizeOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
  { label: '100', value: 100 },
  { label: '不限', value: 0 }
]
const CONTRACT_LIST_CACHE_TTL_MS = 20000

const state = reactive({
  loading: false,
  list: [],
  total: 0,
  page: 1,
  pageSize: 25,
  keyword: '',
  tower: '',
  status: '',
  selected: []
})
const hasLoadedContracts = ref(false)
const contractSpaceOptions = computed(() => profileSpaceOptions(props.operationsProfile))

const contractSort = reactive({
  key: '',
  direction: ''
})

const currentViewFilters = computed(() => ({ keyword: state.keyword, tower: state.tower, status: state.status, sortBy: contractSort.key, sortDir: contractSort.direction }))
function applySavedView(filters) {
  state.keyword = filters.keyword || ''; state.tower = filters.tower || ''; state.status = filters.status || ''
  contractSort.key = filters.sortBy || ''; contractSort.direction = filters.sortDir || ''
  fetchContractsFromFirstPage()
}
const contractDetailLink = computed(() => buildViewLink(window.location.origin, 'contracts', props.propertyScope, currentViewFilters.value, { type: 'contract', id: detail.value?.id }))

const formOpen = ref(false)
const contractFormSaving = ref(false)
const detailOpen = ref(false)
const pdfOpen = ref(false)
const editing = ref(null)
const detail = ref(null)
const detailInitialSection = ref('')
const detailLoading = ref(false)
const detailLogs = ref([])
const detailPlan = ref([])
const detailPreview = ref([])
const detailSummary = ref({})
const detailDecisionCard = ref(null)
const detailDecisionLoading = ref(false)
const detailDecisionError = ref('')
const pdfContract = ref(null)
const importInput = ref(null)
const contractBatchPdfInput = ref(null)
const contractPdfPanel = ref(null)
const contractPdfFocusTarget = ref('')
const importResult = ref(null)
const roomChangeOpen = ref(false)
const roomChangeMode = ref('expand')
const roomChangeContract = ref(null)
const roomChangeRoomItem = ref(null)
const rentTermOpen = ref(false)
const rentTermContract = ref(null)
const rentTermEditing = ref(null)
const rentTermDeleteConfirm = reactive({ contractId: null, term: null, deleting: false })
const depositOpen = ref(false)
const depositMode = ref('create')
const depositContract = ref(null)
const depositEditing = ref(null)
const terminateOpen = ref(false)
const terminateContractTarget = ref(null)
const terminateDefaultEndDate = ref('')
const terminateSubmitting = ref(false)
const changePreview = ref(null)
const pendingChangePayload = ref(null)
const changeConfirmSubmitting = ref(false)
const deleteContractTarget = ref(null)
const deleteContractSubmitting = ref(false)
const pendingBatchDeleteIds = ref([])
const batchDeleteSubmitting = ref(false)
const documentDialogOpen = ref(false)
const documentRequest = ref(null)
const contractBatchActionsOpen = ref(false)
const contractRowMoreOpenId = ref(null)
const contractPdfBatchResult = ref(null)
const contractUnmatchedPdfOptions = ref([])
const contractOrphanPdfs = ref([])
const contractMissingPdfs = ref([])
const contractIntegrity = ref(null)
let detailRequestId = 0
let contractsRequestId = 0
let appliedDrilldownToken = 0

const contractTotalPages = computed(() => (state.pageSize === 0 ? 1 : Math.max(1, Math.ceil(state.total / state.pageSize))))
const hasContractPagination = computed(() => state.pageSize !== 0 && contractTotalPages.value > 1)
const isContractsInitialLoading = computed(() => state.loading && !hasLoadedContracts.value)
const isContractsRefreshing = computed(() => state.loading && hasLoadedContracts.value)
const contractsPageLabel = computed(() => (state.pageSize === 0 ? '全部' : `${state.page} / ${contractTotalPages.value}`))
const contractsShowingText = computed(() => {
  if (state.pageSize === 0) return `显示全部 ${state.total} 条`
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0
  const end = Math.min(state.page * state.pageSize, state.total)
  return `显示 ${start}-${end} 条`
})
const rentTermDeleteDateText = computed(() => {
  const term = rentTermDeleteConfirm.term
  if (!term?.start_date && !term?.end_date) return ''
  return `${formatDate(term?.start_date)} - ${formatDate(term?.end_date)}`
})
const rentTermDeleteRentText = computed(() => {
  const term = rentTermDeleteConfirm.term
  if (!term) return ''
  if (term.monthly_amount != null && term.monthly_amount !== '') return `月租 ${formatCurrencyFixed(term.monthly_amount)}`
  if (term.unit_price != null && term.unit_price !== '') return `单价 ${formatCurrencyFixed(term.unit_price)}`
  if (term.adjustment_value != null && term.adjustment_value !== '') return `调整 ${term.adjustment_value}${term.adjustment_type === 'percent' ? '%' : ''}`
  return ''
})
const statusCounts = computed(() => props.stats?.statuses || {})
const statusKeyMap = {
  在租: 'active',
  即将到期: 'expiring',
  待生效: 'pending',
  已到期: 'expired',
  已续租: 'renewed',
  已变更: 'changed',
  已退租: 'terminated',
  逾期占用: 'overdue-occupancy',
}
const lifecycleTotal = computed(() => {
  const statusTotal = Object.values(statusCounts.value || {}).reduce((sum, value) => sum + Number(value || 0), 0)
  return Math.max(Number(props.stats?.total ?? state.total ?? 0), statusTotal)
})
const lifecycleFilterHint = computed(() => (state.status ? `当前筛选：${t(state.status)}` : '点击色带可筛选台账'))
const expiring90Count = computed(() => {
  const statuses = statusCounts.value || {}
  if (Array.isArray(props.stats?.expiring_soon)) {
    return props.stats.expiring_soon.filter((item) => Number(item.days_left) <= 90).length
  }
  return Number(statuses['即将到期'] || 0)
})
const changePreviewOpen = computed(() => Boolean(changePreview.value && pendingChangePayload.value))
const deleteContractCustomerLabel = computed(() => deleteContractTarget.value?.tenant_name || deleteContractTarget.value?.customer_name || '--')
const pendingBatchDeleteCount = computed(() => pendingBatchDeleteIds.value.length)
const filteredSummary = computed(() => {
  const rows = state.list || []
  const actions = rows.reduce((acc, row) => {
    const label = contractNextAction(row).label
    acc[label] = (acc[label] || 0) + 1
    return acc
  }, {})
  return {
    attention: rows.filter((row) => contractAttentionTags(row).length).length,
    expiring: rows.filter((row) => isExpiringSoon(row)).length,
    missingPdf: rows.filter((row) => shouldFlagMissingPdf(row)).length,
    deposit: rows.filter((row) => depositAttentionTag(row)).length,
    actions,
  }
})
const statusToneMap = {
  在租: 'success',
  即将到期: 'warning',
  待生效: 'info',
  已到期: 'warning',
  已续租: 'muted',
  已变更: 'info',
  已退租: 'muted',
  逾期占用: 'danger'
}
const contractLifecycleRows = computed(() => {
  const statuses = statusCounts.value || {}
  const total = lifecycleTotal.value
  return statusOptions.map((status) => {
    const count = Number(statuses[status] || 0)
    return {
      key: statusKeyMap[status] || status,
      status,
      count,
      percent: total > 0 ? Math.round((count / total) * 100) : 0,
      tone: statusToneMap[status] || 'info',
      active: state.status === status
    }
  })
})
const hasLifecycleData = computed(() => contractLifecycleRows.value.some((item) => item.count > 0))
const contractNextActionRows = computed(() => [
  { label: '续签', count: filteredSummary.value.actions?.['续签'] || 0, tone: 'warning' },
  { label: '变更', count: filteredSummary.value.actions?.['变更'] || 0, tone: 'info' },
  { label: '退租', count: filteredSummary.value.actions?.['退租'] || 0, tone: 'danger' },
  { label: '跟进', count: filteredSummary.value.actions?.['跟进'] || 0, tone: 'warning' },
])
const contractPdfMatchedRows = computed(() => contractPdfBatchResult.value?.matched || [])
const contractPdfUnmatchedRows = computed(() => contractPdfBatchResult.value?.unmatched || [])
const contractPendingPdfCount = computed(() => (
  contractOrphanPdfs.value.length + contractPdfUnmatchedRows.value.length + contractMissingPdfs.value.length
))
const contractDisplayedIntegrityIssues = computed(() => {
  const issues = [...(contractIntegrity.value?.issues || [])]
  const hasMissingPdfIssue = issues.some((issue) => issue.type === 'missing_contract_pdfs')

  if (contractMissingPdfs.value.length && !hasMissingPdfIssue) {
    const sample = contractMissingPdfs.value
      .slice(0, 5)
      .map((item) => item.contract_no)
      .filter(Boolean)
      .join('、')
    issues.unshift({
      type: 'missing_contract_pdfs',
      contract_no: '',
      detail: `存在 ${contractMissingPdfs.value.length} 份执行中合同未上传 PDF${sample ? `，如 ${sample} 等` : ''}`
    })
  }

  return issues
})
const contractDisplayedIntegrityIssueCount = computed(() => contractDisplayedIntegrityIssues.value.length)
const contractActionQueue = computed(() => {
  const rows = (state.list || []).map((row) => {
    const action = contractNextAction(row)
    const priority = contractActionPriority(row, action)
    return {
      row,
      action,
      priority,
      reason: contractActionReason(row, action),
    }
  })
  const actionableRows = rows.filter((item) => item.priority >= 50)
  return (actionableRows.length ? actionableRows : rows)
    .sort((left, right) => right.priority - left.priority)
    .slice(0, 3)
})
const contractWorkspaceLead = computed(() => {
  const overdueOccupancy = statusCount('逾期占用')
  const expiredCount = statusCount('已到期')
  const pendingCount = statusCount('待生效')

  if (overdueOccupancy > 0) {
    return {
      title: '先处理逾期占用',
      value: `${overdueOccupancy} 份`,
      meta: '退租或占用费需确认',
      reason: '合同已超出正常履约区间，先判断退租、续签或占用费。',
      actionLabel: '筛选逾期占用',
      status: '逾期占用',
      tone: 'danger',
    }
  }
  if (expiring90Count.value > 0) {
    return {
      title: '先确认续租/退租',
      value: `${expiring90Count.value} 份`,
      meta: '90天内到期',
      reason: '临期合同会影响招商排期、续签谈判和退租交接。',
      actionLabel: '筛选临期合同',
      status: '即将到期',
      tone: 'warning',
    }
  }
  if (expiredCount > 0) {
    return {
      title: '先归档到期合同',
      value: `${expiredCount} 份`,
      meta: '已到期待处置',
      reason: '到期记录需要确认续签、退租或历史归档，避免台账悬空。',
      actionLabel: '筛选已到期',
      status: '已到期',
      tone: 'warning',
    }
  }
  if (filteredSummary.value.deposit > 0) {
    return {
      title: '先补齐押金',
      value: `${filteredSummary.value.deposit} 份`,
      meta: '本页押金提醒',
      reason: '押金缺口会影响履约保障，优先打开合同详情核对台账。',
      actionLabel: '查看本页队列',
      target: 'queue',
      tone: 'warning',
    }
  }
  if (filteredSummary.value.missingPdf > 0) {
    return {
      title: '先补齐合同正文',
      value: `${filteredSummary.value.missingPdf} 份`,
      meta: '缺少 PDF',
      reason: '附件缺口会影响审计、续租和退租交接。',
      actionLabel: '查看本页队列',
      target: 'queue',
      tone: 'warning',
    }
  }
  if (pendingCount > 0) {
    return {
      title: '先确认交付起租',
      value: `${pendingCount} 份`,
      meta: '待生效合同',
      reason: '待生效合同需要确认交付、起租准备和首期应收。',
      actionLabel: '筛选待生效',
      status: '待生效',
      tone: 'info',
    }
  }
  return {
    title: '台账运行平稳',
    value: `${lifecycleTotal.value} 份`,
    meta: '合同总数',
    reason: '当前合同台账没有集中处理项，可按状态继续巡检。',
    actionLabel: contractActionQueue.value.length ? '查看本页队列' : '查看合同列表',
    target: 'queue',
    tone: 'success',
  }
})
function statusCount(status) {
  return Number(statusCounts.value?.[status] || 0)
}

function setStatusFilter(status) {
  state.status = status
  fetchContractsFromFirstPage()
}

function openWorkspaceLead() {
  const lead = contractWorkspaceLead.value
  if (lead.status) {
    setStatusFilter(lead.status)
    return
  }
  if (lead.target === 'receivables' && props.user?.role !== 'viewer') {
    emit('select-tab', 'receivables')
    return
  }
  const firstAction = contractActionQueue.value[0]?.row
  if (firstAction) openDetail(firstAction)
}

async function fetchContracts(options = {}) {
  const timing = startOperation(options.reason || 'contract-list', 'contracts')
  let outcome = 'superseded'
  const requestId = ++contractsRequestId
  state.loading = true
  try {
    if (options.force) invalidateApiCache('/api/contracts')
    const params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize)
    })
    if (state.keyword) params.set('keyword', state.keyword)
    if (state.tower) params.set('tower', state.tower)
    if (state.status) params.set('status', state.status)
    if (contractSort.key) params.set('sort_by', contractSort.key)
    if (contractSort.direction) params.set('sort_dir', contractSort.direction)

    const data = await cachedApi(`/api/contracts?${params}`, {
      force: options.force,
      ttlMs: CONTRACT_LIST_CACHE_TTL_MS
    })
    if (requestId !== contractsRequestId) return
    state.list = data.items
    state.total = data.total
    state.selected = []
    hasLoadedContracts.value = true
    outcome = 'ok'
  } catch (error) {
    outcome = 'error'
    if (requestId === contractsRequestId) {
      emit('notify', { type: 'error', text: error.message })
    }
  } finally {
    if (requestId === contractsRequestId) {
      state.loading = false
    }
    timing.finish(outcome)
  }
}

function fetchContractsFromFirstPage() {
  state.page = 1
  fetchContracts({ reason: 'contract-filter' })
}

function applyDrilldownIntent() {
  const intent = props.drilldownIntent
  if (!intent || intent.target !== 'contracts' || props.drilldownToken === appliedDrilldownToken) return
  appliedDrilldownToken = props.drilldownToken
  const filters = intent.filters || {}
  state.keyword = filters.keyword || ''
  state.tower = filters.tower || ''
  state.status = filters.status || ''
  contractSort.key = filters.sortBy || ''
  contractSort.direction = filters.sortDir || ''
  fetchContractsFromFirstPage()
  emit('notify', { text: intent.message || '已应用来自经营分析的合同筛选' })
}

function changeContractPageSize(size) {
  state.pageSize = size
  state.page = 1
  fetchContracts()
}

async function openCreate() {
  editing.value = null
  if (canEdit.value) {
    try {
      const next = await api('/api/contracts/next-no')
      editing.value = { contract_no: next.next_no }
    } catch {
      editing.value = null
    }
  }
  formOpen.value = true
}

async function openSuccessorDraft(row, leaseType) {
  if (!canEdit.value) return
  try {
    const draft = await api(`/api/contracts/${row.id}/successor-draft?lease_type=${encodeURIComponent(leaseType)}`)
    editing.value = draft
    formOpen.value = true
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function saveContract(payload) {
  if (contractFormSaving.value) return
  contractFormSaving.value = true
  try {
    if (payload.id) {
      await api(`/api/contracts/${payload.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      emit('notify', { text: '合同更新成功' })
    } else if (shouldConfirmChangeContract(payload)) {
      const preview = await api('/api/contracts/change-preview', {
        method: 'POST',
        body: JSON.stringify(payload)
      })
      changePreview.value = preview
      pendingChangePayload.value = payload
      return
    } else {
      await api('/api/contracts', { method: 'POST', body: JSON.stringify(payload) })
      emit('notify', { text: '合同创建成功' })
    }
    formOpen.value = false
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    contractFormSaving.value = false
  }
}

function closeChangePreviewModal() {
  if (changeConfirmSubmitting.value) return
  changePreview.value = null
  pendingChangePayload.value = null
}

async function confirmContractChangePreview() {
  if (!changePreview.value || !pendingChangePayload.value || changeConfirmSubmitting.value) return
  changeConfirmSubmitting.value = true
  try {
    await api('/api/contracts/change-confirm', {
      method: 'POST',
      body: JSON.stringify({ contract: pendingChangePayload.value })
    })
    changePreview.value = null
    pendingChangePayload.value = null
    formOpen.value = false
    emit('notify', { text: '变更签已生效，旧合同已截断' })
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    changeConfirmSubmitting.value = false
  }
}

function shouldConfirmChangeContract(payload) {
  return !payload.id && payload.lease_type === '变更签' && Boolean(payload.prev_contract_no)
}

async function refreshContractDetail(contractId, options = {}) {
  const requestId = ++detailRequestId
  if (options.reset) {
    detail.value = null
    detailPlan.value = []
    detailPreview.value = []
    detailSummary.value = {}
    detailLogs.value = []
    detailDecisionCard.value = null
    detailDecisionError.value = ''
  }
  detailLoading.value = true
  detailDecisionLoading.value = true
  const decisionRequest = api(`/api/contracts/${contractId}/decision-card`)
    .then((card) => {
      if (requestId === detailRequestId) {
        detailDecisionCard.value = card
        detailDecisionError.value = ''
      }
    })
    .catch((error) => {
      if (requestId === detailRequestId) {
        detailDecisionCard.value = null
        detailDecisionError.value = error.message
      }
    })
    .finally(() => {
      if (requestId === detailRequestId) detailDecisionLoading.value = false
    })
  try {
    const detailRequest = api(`/api/contracts/${contractId}`)
    const planRequest = api(`/api/contracts/${contractId}/rent-plan`)
    const logsRequest = canEdit.value ? api(`/api/contracts/${contractId}/logs`) : Promise.resolve([])
    const [contract, plan, logs] = await Promise.all([detailRequest, planRequest, logsRequest])
    if (requestId !== detailRequestId) return
    detail.value = contract
    detailPlan.value = plan.plan || []
    detailPreview.value = plan.preview || []
    detailSummary.value = plan.summary || {}
    detailLogs.value = logs || []
  } catch (error) {
    if (requestId === detailRequestId) {
      if (options.closeOnError) detailOpen.value = false
      emit('notify', { type: 'error', text: error.message })
    }
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
    await decisionRequest
  }
}

async function openDetail(row, options = {}) {
  const contractId = typeof row === 'object' ? row?.id : row
  if (!contractId) return
  detailInitialSection.value = options.initialSection || ''
  detailOpen.value = true
  await refreshContractDetail(contractId, { reset: true, closeOnError: true })
}

function openReceivablesDetail(row) {
  openDetail(row, { initialSection: 'receivables' })
}

function toggleContractBatchActions() {
  contractBatchActionsOpen.value = !contractBatchActionsOpen.value
}

function closeContractBatchActions() {
  contractBatchActionsOpen.value = false
}

function toggleContractRowMore(row) {
  contractRowMoreOpenId.value = contractRowMoreOpenId.value === row.id ? null : row.id
}

function closeContractRowMore() {
  contractRowMoreOpenId.value = null
}

function openContractDocument({ document_type, contract, month }) {
  if (!canEdit.value || !contract?.id) return
  documentRequest.value = {
    document_type,
    contract_id: contract.id,
    month,
    contract_no: contract.contract_no,
    tenant_name: contract.tenant_name
  }
  documentDialogOpen.value = true
}

function openRoomChangeAction({ mode, contract, roomItem = null }) {
  if (!canEdit.value || !contract?.id) return
  roomChangeMode.value = mode
  roomChangeContract.value = contract
  roomChangeRoomItem.value = roomItem
  roomChangeOpen.value = true
}

async function saveRoomChange(payload) {
  const contractId = roomChangeContract.value?.id
  if (!contractId) return
  const roomItemId = roomChangeRoomItem.value?.id
  const mode = roomChangeMode.value
  const endpoint = mode === 'expand'
    ? `/api/contracts/${contractId}/rooms/expand`
    : `/api/contracts/${contractId}/rooms/${roomItemId}/${mode === 'swap' ? 'swap' : 'reduce'}`

  try {
    await api(endpoint, { method: 'POST', body: JSON.stringify(payload) })
    roomChangeOpen.value = false
    emit('notify', { text: mode === 'expand' ? '扩租已保存' : (mode === 'swap' ? '换房已保存' : '减租已保存') })
    await fetchContracts({ force: true })
    if (detailOpen.value && detail.value?.id === contractId) await refreshContractDetail(contractId)
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function openRentTermAction({ contract, term = null }) {
  if (!canEdit.value || !contract?.id) return
  rentTermContract.value = contract
  rentTermEditing.value = term
  rentTermOpen.value = true
}

async function saveRentTerm(payload) {
  const contractId = rentTermContract.value?.id
  if (!contractId) return
  const termId = rentTermEditing.value?.id
  const endpoint = termId ? `/api/contracts/${contractId}/rent-terms/${termId}` : `/api/contracts/${contractId}/rent-terms`
  try {
    await api(endpoint, { method: termId ? 'PUT' : 'POST', body: JSON.stringify(payload) })
    rentTermOpen.value = false
    emit('notify', { text: termId ? '租金条款已更新' : '租金条款已新增' })
    await fetchContracts({ force: true })
    if (detailOpen.value && detail.value?.id === contractId) await refreshContractDetail(contractId)
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function deleteRentTerm(term) {
  const contractId = detail.value?.id || term?.contract_id
  if (!contractId || !term?.id) return
  rentTermDeleteConfirm.contractId = contractId
  rentTermDeleteConfirm.term = { ...term }
}

function closeRentTermDeleteConfirm() {
  if (rentTermDeleteConfirm.deleting) return
  rentTermDeleteConfirm.contractId = null
  rentTermDeleteConfirm.term = null
}

async function confirmDeleteRentTerm() {
  const contractId = rentTermDeleteConfirm.contractId
  const term = rentTermDeleteConfirm.term
  if (!contractId || !term?.id || rentTermDeleteConfirm.deleting) return

  rentTermDeleteConfirm.deleting = true
  try {
    await api(`/api/contracts/${contractId}/rent-terms/${term.id}`, { method: 'DELETE' })
    rentTermDeleteConfirm.contractId = null
    rentTermDeleteConfirm.term = null
    emit('notify', { text: '租金条款已删除' })
    await fetchContracts({ force: true })
    if (detailOpen.value && detail.value?.id === contractId) await refreshContractDetail(contractId)
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    rentTermDeleteConfirm.deleting = false
  }
}

function openDepositAction({ mode, contract, deposit = null }) {
  if (!canEdit.value || !contract?.id) return
  depositMode.value = mode || 'create'
  depositContract.value = contract
  depositEditing.value = deposit
  depositOpen.value = true
}

async function saveDeposit(payload) {
  const contractId = depositContract.value?.id
  if (!contractId) return
  const depositId = depositEditing.value?.id
  let endpoint = `/api/contracts/${contractId}/deposits`
  let method = 'POST'
  if (depositMode.value === 'edit') {
    endpoint = `/api/contracts/${contractId}/deposits/${depositId}`
    method = 'PUT'
  } else if (['receive', 'refund', 'offset'].includes(depositMode.value)) {
    endpoint = `/api/contracts/${contractId}/deposits/${depositId}/${depositMode.value}`
  }

  try {
    await api(endpoint, { method, body: JSON.stringify(payload) })
    depositOpen.value = false
    emit('notify', { text: '押金台账已更新' })
    await fetchContracts({ force: true })
    if (detailOpen.value && detail.value?.id === contractId) await refreshContractDetail(contractId)
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function removeContract(row) {
  deleteContractTarget.value = row
}

function closeDeleteContractConfirm() {
  if (deleteContractSubmitting.value) return
  deleteContractTarget.value = null
}

async function confirmRemoveContract() {
  const row = deleteContractTarget.value
  if (!row?.id || deleteContractSubmitting.value) return

  deleteContractSubmitting.value = true
  try {
    await api(`/api/contracts/${row.id}`, { method: 'DELETE' })
    deleteContractTarget.value = null
    emit('notify', { text: '合同已删除' })
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    deleteContractSubmitting.value = false
  }
}

function todayInputValue() {
  const today = new Date()
  const year = today.getFullYear()
  const month = String(today.getMonth() + 1).padStart(2, '0')
  const day = String(today.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function terminateContract(row) {
  terminateContractTarget.value = row
  terminateDefaultEndDate.value = Number(row.days_to_expiry) < 0 && row.end_date ? row.end_date : todayInputValue()
  terminateOpen.value = true
}

function closeTerminateContract() {
  if (terminateSubmitting.value) return
  terminateOpen.value = false
  terminateContractTarget.value = null
  terminateDefaultEndDate.value = ''
}

async function confirmTerminateContract(payload) {
  const row = terminateContractTarget.value
  if (!row?.id) return

  terminateSubmitting.value = true
  try {
    await api(`/api/contracts/${row.id}/terminate`, {
      method: 'POST',
      body: JSON.stringify({ end_date: payload.end_date || null })
    })
    terminateOpen.value = false
    terminateContractTarget.value = null
    terminateDefaultEndDate.value = ''
    emit('notify', { text: '合同已标记为退租' })
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    terminateSubmitting.value = false
  }
}

function batchDelete() {
  if (!state.selected.length) return
  pendingBatchDeleteIds.value = [...state.selected]
}

function closeBatchDeleteConfirm() {
  if (batchDeleteSubmitting.value) return
  pendingBatchDeleteIds.value = []
}

async function confirmBatchDelete() {
  const ids = [...pendingBatchDeleteIds.value]
  if (!ids.length || batchDeleteSubmitting.value) return

  batchDeleteSubmitting.value = true
  try {
    await api('/api/contracts/batch-delete', { method: 'POST', body: JSON.stringify(ids) })
    pendingBatchDeleteIds.value = []
    emit('notify', { text: '批量删除成功' })
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    batchDeleteSubmitting.value = false
  }
}

async function fetchContractUnmatchedPdfs() {
  if (!canEdit.value) return
  contractOrphanPdfs.value = await api('/api/contracts/unmatched-pdfs')
}

async function fetchContractMissingPdfs() {
  if (!canEdit.value) return
  contractMissingPdfs.value = await api('/api/contracts/missing-pdfs')
}

async function fetchContractIntegrity() {
  if (!canEdit.value) return
  contractIntegrity.value = await api('/api/system/integrity-check')
}

async function refreshContractDocumentPanel() {
  if (!canEdit.value) return
  const results = await Promise.allSettled([
    fetchContractUnmatchedPdfs(),
    fetchContractMissingPdfs(),
    fetchContractIntegrity()
  ])
  const failed = results.find((result) => result.status === 'rejected')
  if (failed) emit('notify', { type: 'error', text: failed.reason?.message || '合同资料检查失败' })
}

function triggerContractBatchPdfUpload() {
  contractBatchPdfInput.value?.click()
}

function focusContractPdfPanel(issueType = 'orphan_pdfs') {
  const target = contractPdfPanel.value
  contractPdfFocusTarget.value = issueType === 'missing_contract_pdfs' ? 'missing' : 'panel'
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.clearTimeout(focusContractPdfPanel._timer)
  focusContractPdfPanel._timer = window.setTimeout(() => {
    contractPdfFocusTarget.value = ''
  }, 1800)
}

async function uploadContractBatchPdf(event) {
  const files = event.target.files
  if (!files?.length) return

  const payload = new FormData()
  Array.from(files).forEach((file) => payload.append('files', file))

  try {
    contractPdfBatchResult.value = await api('/api/contracts/batch-upload-pdf', { method: 'POST', body: payload })
    emit('notify', {
      text: `上传完成：已匹配 ${contractPdfBatchResult.value.matched_count} 个，待人工关联 ${contractPdfBatchResult.value.unmatched_count} 个`
    })
    if (contractPdfBatchResult.value.unmatched_count) {
      contractUnmatchedPdfOptions.value = await api('/api/contracts/missing-pdfs')
    }
    await refreshContractDocumentPanel()
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    event.target.value = ''
  }
}

async function matchContractPdf(item) {
  try {
    await api(`/api/contracts/match-pdf?filename=${encodeURIComponent(item.filename)}&contract_id=${item.match_id}`, {
      method: 'POST'
    })
    emit('notify', { text: `${item.filename} 已完成关联` })
    if (contractPdfBatchResult.value?.unmatched) {
      contractPdfBatchResult.value.unmatched = contractPdfBatchResult.value.unmatched.filter((entry) => entry.filename !== item.filename)
    }
    contractOrphanPdfs.value = contractOrphanPdfs.value.filter((entry) => entry.filename !== item.filename)
    await refreshContractDocumentPanel()
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function importExcel(event) {
  const file = event.target.files?.[0]
  if (!file) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    const result = await api('/api/contracts/import', { method: 'POST', body: payload })
    importResult.value = buildImportSuccessResult(result)
    emit('notify', { text: importResult.value.title })
    await fetchContracts({ force: true })
    emit('refresh-dashboard')
  } catch (error) {
    importResult.value = buildImportErrorResult(error)
    emit('notify', { type: 'error', text: importResult.value.title })
  } finally {
    event.target.value = ''
  }
}

async function uploadPdf(file) {
  if (!pdfContract.value) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    await api(`/api/contracts/${pdfContract.value.id}/upload-pdf`, { method: 'POST', body: payload })
    emit('notify', { text: 'PDF 上传成功' })
    pdfOpen.value = false
    await fetchContracts({ force: true })
    await refreshContractDocumentPanel()
    emit('refresh-dashboard')
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function exportExcel() {
  const params = new URLSearchParams()
  if (state.keyword) params.set('keyword', state.keyword)
  if (state.tower) params.set('tower', state.tower)
  if (state.status) params.set('status', state.status)
  openDownload(`/api/contracts/export/excel?${params}`)
}

function exportDeposits() {
  const params = new URLSearchParams()
  if (state.keyword) params.set('keyword', state.keyword)
  openDownload(`/api/contracts/deposits/export${params.toString() ? `?${params}` : ''}`)
}

function triggerImport() {
  importInput.value?.click()
}

function toggleContractSort(key) {
  if (contractSort.key !== key) {
    contractSort.key = key
    contractSort.direction = 'asc'
    state.page = 1
    fetchContracts()
    return
  }

  if (contractSort.direction === 'asc') {
    contractSort.direction = 'desc'
    state.page = 1
    fetchContracts()
    return
  }

  if (contractSort.direction === 'desc') {
    contractSort.key = ''
    contractSort.direction = ''
    state.page = 1
    fetchContracts()
    return
  }

  contractSort.direction = 'asc'
  state.page = 1
  fetchContracts()
}

function contractSortIndicator(key) {
  if (contractSort.key !== key) return '↕'
  return contractSort.direction === 'asc' ? '↑' : '↓'
}

watch(
  () => [props.drilldownIntent, props.drilldownToken],
  () => {
    applyDrilldownIntent()
  }
)

onMounted(async () => {
  await fetchContracts()
  await refreshContractDocumentPanel()
  applyDrilldownIntent()
})
</script>

<template>
  <section class="content" data-testid="contracts-page">
    <article class="card table-card contracts-shell">
      <div class="card-header page-block">
        <div>
          <div class="section-label">{{ $t("合同台账") }}</div>
          <h3 class="card-title">{{ $t("合同处理台") }}</h3>
          <p class="helper-text">{{ $t("收款登记请前往“月度应收”。") }}</p>
        </div>
        <span v-if="isContractsRefreshing" class="metric-pill info refreshing-indicator">{{ $t("同步中") }}</span>
      </div>

      <ResponsiveDisclosure :title="$t(&quot;生命周期与待办概览&quot;)">
      <div class="page-block contract-lifecycle-board contract-lifecycle-summary" data-testid="contracts-lifecycle-visual">
        <div class="contract-lifecycle-overview" data-testid="contracts-lifecycle-summary">
          <div class="contract-lifecycle-total" data-testid="contracts-workspace-summary">
            <span class="section-label">{{ $t("生命周期总览") }}</span>
            <strong>{{ $t(lifecycleTotal) }}</strong>
            <small>{{ $t(lifecycleFilterHint) }}</small>
          </div>
          <button type="button" class="contract-lifecycle-lead" :class="contractWorkspaceLead.tone" data-testid="contracts-pressure-lead" @click="openWorkspaceLead">
            <span>{{ $t("合同侧重点") }}</span>
            <strong>{{ $t(contractWorkspaceLead.title) }} · {{ $t(contractWorkspaceLead.value) }}</strong>
            <small>{{ $t(contractWorkspaceLead.reason) }}</small>
          </button>
        </div>

        <div v-if="hasLifecycleData" class="contract-lifecycle-bars" role="list" :aria-label="$t(&quot;合同生命周期分布&quot;)">
          <button
            v-for="item in contractLifecycleRows"
            :key="item.status"
            type="button"
            class="contract-lifecycle-segment"
            :class="[item.tone, { active: item.active }]"
            :style="{ flexGrow: Math.max(0.25, item.count || 0.25) }"
            :aria-pressed="String(item.active)"
            :data-testid="`contracts-lifecycle-segment-${item.key}`"
            role="listitem"
            @click="setStatusFilter(item.status)"
          >
            <span>{{ $t(item.status) }}</span>
            <strong>{{ $t(item.count) }}</strong>
            <small>{{ $t(item.percent) }}%</small>
          </button>
        </div>
        <div v-else class="compact-empty slim">{{ $t("暂无合同") }}</div>

      </div>

      <div class="page-block contract-command-panel">
        <section class="contract-action-queue">
          <div class="contract-action-queue-head">
            <div>
              <span class="section-label">{{ $t("行动队列") }}</span>
              <strong>{{ $t("需要先看的合同") }}</strong>
            </div>
            <span class="metric-pill warning">{{ $t("关注 ") }}{{ $t(filteredSummary.attention) }}</span>
          </div>
          <div v-if="contractActionQueue.length" class="contract-action-queue-list">
            <button
              v-for="item in contractActionQueue"
              :key="item.row.id"
              type="button"
              class="contract-action-row"
              :class="item.action.tone"
              @click="openDetail(item.row)"
            >
              <span class="contract-action-main">
                <strong>{{ $t(item.action.label) }} · {{ item.row.contract_no }}</strong>
                <small>{{ item.row.tenant_name || '--' }}</small>
              </span>
              <span class="contract-action-reason">{{ $t(item.reason) }}</span>
              <span class="contract-action-cta">{{ $t("看详情") }}</span>
            </button>
          </div>
          <div v-else class="compact-empty slim">{{ $t("暂无需要处理的合同") }}</div>
        </section>
      </div>

      </ResponsiveDisclosure>

      <details v-if="canEdit" class="demo-secondary-details contract-document-details">
        <summary>
          <span>{{ $t("资料维护") }}</span>
          <strong>{{ $t("PDF 归档 / 完整性检查") }}</strong>
        </summary>
      <div class="page-block contract-document-panel" data-testid="contracts-document-panel">
        <section
          ref="contractPdfPanel"
          class="contract-document-card"
          :class="{ 'focus-target': contractPdfFocusTarget === 'panel' || contractPdfFocusTarget === 'missing' }"
          data-testid="contracts-pdf-panel"
        >
          <div class="contract-document-head">
            <div>
              <span class="section-label">{{ $t("合同资料") }}</span>
              <h3>{{ $t("PDF 归档") }}</h3>
              <p>{{ $t("批量上传合同文件，系统按合同号自动匹配。") }}</p>
            </div>
            <button class="btn btn-primary" data-testid="contracts-pdf-upload-button" @click="triggerContractBatchPdfUpload">{{ $t("上传 PDF") }}</button>
            <input ref="contractBatchPdfInput" class="visually-hidden" type="file" multiple accept=".pdf" @change="uploadContractBatchPdf" />
          </div>
          <div class="pill-group">
            <span class="metric-pill info">{{ $t("待补资料 ") }}{{ $t(contractPendingPdfCount) }}</span>
            <span v-if="contractOrphanPdfs.length" class="metric-pill warning">{{ $t("未关联 ") }}{{ $t(contractOrphanPdfs.length) }}</span>
            <span v-if="contractMissingPdfs.length" class="metric-pill warning">{{ $t("缺 PDF ") }}{{ $t(contractMissingPdfs.length) }}</span>
          </div>

          <div v-if="contractPdfBatchResult" class="upload-summary compact-stats">
            <div class="mini-stat">
              <span>{{ $t("总文件") }}</span>
              <strong>{{ $t(contractPdfBatchResult.total) }}</strong>
            </div>
            <div class="mini-stat">
              <span>{{ $t("已匹配") }}</span>
              <strong>{{ $t(contractPdfBatchResult.matched_count) }}</strong>
            </div>
            <div class="mini-stat">
              <span>{{ $t("待人工关联") }}</span>
              <strong>{{ $t(contractPdfBatchResult.unmatched_count) }}</strong>
            </div>
          </div>

          <div v-if="contractOrphanPdfs.length || contractPdfUnmatchedRows.length || contractPdfMatchedRows.length" class="contract-document-list">
            <div v-for="item in contractOrphanPdfs" :key="`orphan-${item.filename}`" class="manual-match-card">
              <strong>{{ item.filename }}</strong>
              <div class="toolbar stacked-mobile">
                <select v-model="item.match_id" class="select">
                  <option value="">{{ $t("选择合同") }}</option>
                  <option v-for="contract in contractUnmatchedPdfOptions" :key="contract.id" :value="contract.id">
                    {{ contract.contract_no }} - {{ contract.tenant_name }}
                  </option>
                </select>
                <button class="btn btn-primary" :disabled="!item.match_id" @click="matchContractPdf(item)">{{ $t("关联") }}</button>
              </div>
            </div>
            <div v-for="item in contractPdfUnmatchedRows" :key="`batch-${item.filename}`" class="manual-match-card">
              <strong>{{ item.filename }}</strong>
              <div class="toolbar stacked-mobile">
                <select v-model="item.match_id" class="select">
                  <option value="">{{ $t("选择合同") }}</option>
                  <option v-for="contract in contractUnmatchedPdfOptions" :key="contract.id" :value="contract.id">
                    {{ contract.contract_no }} - {{ contract.tenant_name }}
                  </option>
                </select>
                <button class="btn btn-primary" :disabled="!item.match_id" @click="matchContractPdf(item)">{{ $t("关联") }}</button>
              </div>
            </div>
            <div v-for="item in contractPdfMatchedRows" :key="`matched-${item.filename}`" class="compact-list-item">
              <div>
                <strong>{{ item.filename }}</strong>
                <p>{{ item.contract_no }} / {{ item.tenant_name }}</p>
              </div>
              <span class="badge success">{{ $t("已匹配") }}</span>
            </div>
          </div>
          <div v-else class="inline-status-row compact-empty-tight" data-testid="contracts-pdf-empty-inline">{{ $t(" 当前没有待关联 PDF ") }}</div>
        </section>

        <section class="contract-document-card" data-testid="contracts-integrity-panel">
          <div class="contract-document-head">
            <div>
              <span class="section-label">{{ $t("资料检查") }}</span>
              <h3>{{ $t("完整性检查") }}</h3>
              <p>{{ $t("核对执行中合同是否缺文件或未完成关联。") }}</p>
            </div>
            <button class="btn" data-testid="contracts-integrity-refresh" @click="refreshContractDocumentPanel">{{ $t("重新检查") }}</button>
          </div>
          <div class="pill-group">
            <span class="metric-pill" :class="contractDisplayedIntegrityIssueCount ? 'warning' : 'success'">{{ $t("资料缺口 ") }}{{ $t(contractDisplayedIntegrityIssueCount) }}</span>
            <span class="metric-pill info">{{ $t("缺 PDF ") }}{{ $t(contractMissingPdfs.length) }}</span>
          </div>

          <div v-if="contractDisplayedIntegrityIssues.length" class="contract-document-list">
            <article v-for="issue in contractDisplayedIntegrityIssues" :key="`${issue.type}-${issue.contract_no}-${issue.detail}`" class="integrity-card">
              <div class="integrity-card-top">
                <span class="badge warning">{{ $t(formatIssueType(issue.type)) }}</span>
                <button
                  v-if="['orphan_pdfs', 'missing_contract_pdfs'].includes(issue.type)"
                  class="btn"
                  :data-testid="`contracts-integrity-process-${issue.type}`"
                  @click="focusContractPdfPanel(issue.type)"
                >{{ $t(" 去处理 ") }}</button>
              </div>
              <strong>{{ issue.contract_no || '未关联合同' }}</strong>
              <p>{{ $t(issue.detail) }}</p>
            </article>
          </div>
          <div v-else class="inline-status-row compact-empty-tight">
            <span class="badge success">{{ $t("状态正常") }}</span>
            <span>{{ $t("资料检查通过") }}</span>
          </div>
        </section>
      </div>
      </details>

      <SavedFilterViews class="page-block" :user="user" :scope="propertyScope" page="contracts" :filters="currentViewFilters" @apply="applySavedView" />
      <div class="page-block contract-toolbar-shell contract-toolbar-shell-contracts">
        <div class="contract-filter-stack">
          <div class="filter-cluster contract-filter-cluster">
            <input
              v-model="state.keyword"
              class="input"
              :placeholder="$t(&quot;合同编号 / 室号 / 客户&quot;)"
              :aria-label="$t(&quot;查找合同、客户或房号&quot;)"
              data-testid="contracts-search-input"
              @keyup.enter="fetchContractsFromFirstPage"
            />
            <button class="btn btn-primary contract-search-btn" data-testid="contracts-search-button" @click="fetchContractsFromFirstPage">{{ $t("查询") }}</button>
            <select v-model="state.tower" class="select" data-testid="contracts-tower-filter" @change="fetchContractsFromFirstPage">
              <option value="">{{ $t("全部分区") }}</option>
              <option v-for="option in contractSpaceOptions" :key="option.code" :value="option.value">{{ $t(option.label) }}</option>
            </select>
            <select v-model="state.status" class="select" data-testid="contracts-status-filter" @change="fetchContractsFromFirstPage">
              <option value="">{{ $t("全部状态") }}</option>
              <option value="在租">{{ $t("在租") }}</option>
              <option value="即将到期">{{ $t("即将到期") }}</option>
              <option value="已到期">{{ $t("已到期") }}</option>
              <option value="已续租">{{ $t("已续租") }}</option>
              <option value="已变更">{{ $t("已变更") }}</option>
              <option value="已退租">{{ $t("已退租") }}</option>
              <option value="逾期占用">{{ $t("逾期占用") }}</option>
              <option value="待生效">{{ $t("待生效") }}</option>
            </select>
          </div>
          <ResponsiveDisclosure :title="$t(`筛选分布与本页关注 · ${state.status || '全部状态'}`)">
          <div class="contract-status-chips" data-testid="contracts-status-chips">
            <button type="button" :class="{ active: !state.status }" @click="setStatusFilter('')">{{ $t("全部 ") }}{{ $t(props.stats?.total ?? state.total) }}</button>
            <button
              v-for="status in statusOptions"
              :key="status"
              type="button"
              :class="{ active: state.status === status }"
              @click="setStatusFilter(status)"
            >
              {{ $t(status) }} {{ $t(statusCount(status)) }}
            </button>
          </div>
          <div class="contract-attention-strip">
            <span class="metric-pill warning">{{ $t("本页关注 ") }}{{ $t(filteredSummary.attention) }}</span>
            <span class="metric-pill info">{{ $t("本页90天到期 ") }}{{ $t(filteredSummary.expiring) }}</span>
            <span class="metric-pill warning">{{ $t("缺 PDF ") }}{{ $t(filteredSummary.missingPdf) }}</span>
            <span class="metric-pill info">{{ $t("押金提醒 ") }}{{ $t(filteredSummary.deposit) }}</span>
          </div>
          <div class="contract-next-action-strip" data-testid="contracts-next-action-strip">
            <span>{{ $t("下一步") }}</span>
            <strong v-for="item in contractNextActionRows" :key="item.label" :class="item.tone">
              {{ $t(item.label) }} {{ $t(item.count) }}
            </strong>
          </div>
          </ResponsiveDisclosure>
        </div>

        <div class="action-cluster contract-action-cluster">
          <input ref="importInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importExcel" />
          <button v-if="props.user?.role !== 'viewer'" class="btn" @click="emit('select-tab', 'receivables')">{{ $t("月度应收") }}</button>
          <button v-if="canEdit" class="btn btn-primary" @click="openCreate">{{ $t("新建合同") }}</button>
          <div
            v-if="canEdit || canExport"
            class="contract-batch-actions"
            data-testid="contract-batch-actions"
            @keydown.esc.stop.prevent="closeContractBatchActions"
          >
            <button
              type="button"
              class="btn contract-batch-trigger"
              data-testid="contract-batch-trigger"
              aria-controls="contract-batch-panel"
              :aria-expanded="String(contractBatchActionsOpen)"
              @click="toggleContractBatchActions"
            >{{ $t(" 批量处理 ") }}</button>
            <div
              v-if="contractBatchActionsOpen"
              id="contract-batch-panel"
              class="contract-batch-popover"
              data-testid="contract-batch-panel"
            >
              <div class="contract-batch-head">
                <strong>{{ $t("批量处理") }}</strong>
                <button type="button" class="icon-btn" :title="$t(&quot;关闭&quot;)" @click="closeContractBatchActions">×</button>
              </div>
              <div class="contract-batch-grid">
                <section v-if="canEdit" class="contract-batch-section">
                  <span>{{ $t("合同资料") }}</span>
                  <div class="contract-batch-buttons">
                    <button class="btn" @click="openDownload('/api/contracts/export/template'); closeContractBatchActions()">{{ $t("模板") }}</button>
                    <button class="btn" @click="triggerImport(); closeContractBatchActions()">{{ $t("导入") }}</button>
                    <button v-if="canExport" class="btn" @click="exportExcel(); closeContractBatchActions()">{{ $t("导出") }}</button>
                  </div>
                </section>
                <section v-if="canExport" class="contract-batch-section">
                  <span>{{ $t("押金") }}</span>
                  <div class="contract-batch-buttons">
                    <button class="btn" @click="exportDeposits(); closeContractBatchActions()">{{ $t("导出押金") }}</button>
                  </div>
                </section>
                <section v-if="canEdit" class="contract-batch-section">
                  <span>{{ $t("选中项") }}</span>
                  <div class="contract-batch-buttons">
                    <button
                      class="btn btn-danger"
                      data-testid="contract-batch-delete-action"
                      :disabled="!state.selected.length"
                      @click="batchDelete(); closeContractBatchActions()"
                    >{{ $t(" 删除选中 ") }}{{ $t(state.selected.length) }}{{ $t(" 条 ") }}</button>
                  </div>
                </section>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="importResult" class="page-block import-result-panel" :class="importResult.type">
        <div class="import-result-header">
          <strong>{{ $t(importResult.title) }}</strong>
          <button class="icon-btn" :title="$t(&quot;关闭导入结果&quot;)" @click="importResult = null">×</button>
        </div>
        <ul v-if="importResult.issues.length" class="import-result-list">
          <li v-for="issue in importResult.issues" :key="issue">{{ $t(issue) }}</li>
        </ul>
        <div v-if="importResult.extraCount" class="helper-text">{{ $t("另有 ") }}{{ $t(importResult.extraCount) }}{{ $t(" 条未显示") }}</div>
      </div>

      <TableSkeleton
        v-if="isContractsInitialLoading"
        class="contracts-table-loading"
        :rows="8"
        :columns="canEdit ? 7 : 6"
      />

      <div v-else class="table-wrapper">
        <table class="data-table contract-ledger-table" data-testid="contracts-table">
          <thead>
            <tr>
              <th v-if="canEdit" class="check-cell">
                <input
                  type="checkbox"
                  :checked="state.list.length > 0 && state.selected.length === state.list.length"
                  @change="state.selected = $event.target.checked ? state.list.map((item) => item.id) : []"
                />
              </th>
              <th>
                <button class="table-sort-btn" data-testid="contracts-sort-contract-no" :class="{ active: contractSort.key === 'contract_no' }" @click="toggleContractSort('contract_no')">{{ $t(" 合同 / 客户 ") }}{{ $t(contractSortIndicator('contract_no')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'room_no' || contractSort.key === 'area' }" @click="toggleContractSort('room_no')">{{ $t(" 房源 / 面积 ") }}{{ $t(contractSortIndicator('room_no')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" data-testid="contracts-sort-end-date" :class="{ active: contractSort.key === 'end_date' }" @click="toggleContractSort('end_date')">{{ $t(" 周期 / 到期 ") }}{{ $t(contractSortIndicator('end_date')) }}
                </button>
              </th>
              <th class="right">
                <button class="table-sort-btn right" data-testid="contracts-sort-monthly-rent" :class="{ active: contractSort.key === 'monthly_rent' }" @click="toggleContractSort('monthly_rent')">{{ $t(" 租金 / 押金 ") }}{{ $t(contractSortIndicator('monthly_rent')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn" :class="{ active: contractSort.key === 'status' }" @click="toggleContractSort('status')">{{ $t(" 状态 / 关注 ") }}{{ $t(contractSortIndicator('status')) }}
                </button>
              </th>
              <th class="actions-cell">{{ $t("操作") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in state.list" :key="row.id">
              <td v-if="canEdit" class="check-cell">
                <input v-model="state.selected" type="checkbox" :value="row.id" />
              </td>
              <td class="contract-identity-cell">
                <button type="button" class="contract-detail-link" @click.stop="openDetail(row)">{{ row.contract_no }}</button>
                <div>{{ row.tenant_name || '--' }}</div>
                <div v-if="row.prev_contract_no" class="table-cell-subtext">{{ $t("承接：") }}{{ $t(row.prev_contract_no) }}</div>
                <div v-if="row.internal_contact_person" class="table-cell-subtext">{{ $t("我方：") }}{{ $t(row.internal_contact_person) }}</div>
              </td>
              <td>
                <div>{{ $t(roomSummary(row)) }}</div>
                <div class="table-cell-subtext">{{ $t(row.tower || '--') }} · {{ $t(formatArea(row.area)) }}㎡</div>
              </td>
              <td>
                <div>{{ $t(formatDate(row.start_date)) }} - {{ $t(formatDate(row.end_date)) }}</div>
                <span class="badge compact" :class="expiryTone(row)">{{ $t(expiryLabel(row)) }}</span>
              </td>
              <td class="right contract-money-cell">
                <strong>{{ $t(formatCurrencyFixed(row.monthly_rent)) }}</strong>
                <div class="table-cell-subtext">{{ $t("物业 ") }}{{ $t(formatCurrencyFixed(row.monthly_property_fee || 0)) }}</div>
                <div v-if="depositAttentionTag(row)" class="table-cell-subtext warning-text">{{ $t(depositAttentionTag(row)) }}</div>
              </td>
              <td>
                <StatusBadge :status="row.status" />
                <div v-if="row.disposal" class="table-cell-subtext">{{ $t("处置：") }}{{ $t(row.disposal) }}</div>
                <div v-if="contractAttentionTags(row).length" class="contract-risk-tags">
                  <span v-for="tag in contractAttentionTags(row)" :key="tag" class="badge compact warning">{{ $t(tag) }}</span>
                </div>
                <div class="contract-row-next-action" :class="contractNextAction(row).tone">
                  <span>{{ $t(contractNextAction(row).label) }}</span>
                  <small>{{ $t(contractNextAction(row).detail) }}</small>
                </div>
              </td>
              <td class="actions-cell">
                <button class="icon-btn table-action-button" :title="$t(&quot;查看详情&quot;)" :aria-label="$t(&quot;查看合同详情&quot;)" @click="openDetail(row)">{{ $t("详情") }}</button>
                <button v-if="row.customer_id" class="icon-btn table-action-button" :title="$t(&quot;查看客户&quot;)" :aria-label="$t(&quot;查看客户&quot;)" @click="emit('open-customer', row.customer_id)">{{ $t("客户") }}</button>
                <button v-if="props.user?.role !== 'viewer'" class="icon-btn table-action-button" :title="$t(&quot;查看应收&quot;)" :aria-label="$t(&quot;查看应收&quot;)" @click="openReceivablesDetail(row)">{{ $t("应收") }}</button>
                <div v-if="canEdit" class="contract-row-more-actions" data-testid="contract-row-more-actions" @keydown.esc.stop.prevent="closeContractRowMore">
                  <button
                    type="button"
                    class="icon-btn table-action-button"
                    :title="$t(&quot;更多&quot;)"
                    :aria-label="$t(&quot;更多合同操作&quot;)"
                    :aria-expanded="String(contractRowMoreOpenId === row.id)"
                    @click="toggleContractRowMore(row)"
                  >{{ $t(" 更多 ") }}</button>
                  <div v-if="contractRowMoreOpenId === row.id" class="contract-row-more-popover" data-testid="contract-row-more-menu">
                    <section class="contract-row-more-section">
                      <span>{{ $t("合同流转") }}</span>
                      <button type="button" @click="openSuccessorDraft(row, '续签'); closeContractRowMore()">{{ $t("生成续签") }}</button>
                      <button type="button" @click="openSuccessorDraft(row, '变更签'); closeContractRowMore()">{{ $t("生成变更签") }}</button>
                      <button type="button" data-testid="contract-terminate-action" @click="terminateContract(row); closeContractRowMore()">{{ $t("标记退租") }}</button>
                    </section>
                    <section class="contract-row-more-section">
                      <span>{{ $t("资料维护") }}</span>
                      <button type="button" @click="editing = row; formOpen = true; closeContractRowMore()">{{ $t("编辑合同") }}</button>
                      <button type="button" @click="pdfContract = row; pdfOpen = true; closeContractRowMore()">{{ $t("合同 PDF") }}</button>
                    </section>
                    <section class="contract-row-more-section danger">
                      <span>{{ $t("危险操作") }}</span>
                      <button type="button" data-testid="contract-delete-action" @click="removeContract(row); closeContractRowMore()">{{ $t("删除合同") }}</button>
                    </section>
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="!state.list.length && !state.loading">
              <td :colspan="canEdit ? 7 : 6" class="list-empty">{{ $t("暂无合同数据") }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="page-block table-footer">
        <div class="page-size-control">
          <span class="helper-text">{{ $t("每页显示") }}</span>
          <div class="page-size-switch" data-testid="contracts-page-size">
            <button
              v-for="option in pageSizeOptions"
              :key="option.value"
              type="button"
              :class="{ active: state.pageSize === option.value }"
              @click="changeContractPageSize(option.value)"
            >
              {{ $t(option.label) }}
            </button>
          </div>
        </div>
        <span class="helper-text">{{ $t("共 ") }}{{ $t(state.total) }}{{ $t(" 条 · ") }}{{ $t(contractsShowingText) }}</span>
        <div class="spacer" />
        <button v-if="hasContractPagination" class="btn" :disabled="state.page <= 1" @click="state.page -= 1; fetchContracts()">{{ $t("上一页") }}</button>
        <span v-if="hasContractPagination">{{ $t(contractsPageLabel) }}</span>
        <button
          v-if="hasContractPagination"
          class="btn"
          :disabled="state.page >= contractTotalPages"
          @click="state.page += 1; fetchContracts()"
        >{{ $t(" 下一页 ") }}</button>
      </div>
    </article>

    <ContractFormModal
      :open="formOpen"
      :contract="editing"
      :can-edit="canEdit"
      :saving="contractFormSaving"
      :operations-profile="operationsProfile"
      @close="formOpen = false"
      @save="saveContract"
    />
    <ContractChangePreviewModal
      :open="changePreviewOpen"
      :preview="changePreview"
      :payload="pendingChangePayload"
      :submitting="changeConfirmSubmitting"
      @close="closeChangePreviewModal"
      @confirm="confirmContractChangePreview"
    />
    <ContractDetailModal
      :detail-link="contractDetailLink"
      :open="detailOpen"
      :contract="detail"
      :loading="detailLoading"
      :logs="detailLogs"
      :rent-plan="detailPlan"
      :rent-preview="detailPreview"
      :rent-summary="detailSummary"
      :decision-card="detailDecisionCard"
      :decision-loading="detailDecisionLoading"
      :decision-error="detailDecisionError"
      :initial-section="detailInitialSection"
      :show-logs="canEdit"
      @close="detailOpen = false"
      @open-contract="openDetail"
      @open-customer="emit('open-customer', $event)"
      @refresh="detail?.id && refreshContractDetail(detail.id)"
      @notify="emit('notify', $event)"
      @refresh-decision-card="detail?.id && refreshContractDetail(detail.id)"
      @room-action="openRoomChangeAction"
      @rent-term-action="openRentTermAction"
      @delete-rent-term="deleteRentTerm"
      @deposit-action="openDepositAction"
      @generate-document="openContractDocument"
    />
    <BaseModal :open="Boolean(rentTermDeleteConfirm.term)" :title="$t(&quot;删除租金条款&quot;)" size="small" @close="closeRentTermDeleteConfirm">
      <div data-testid="rent-term-delete-confirm-modal" class="detail-list">
        <p>{{ $t("确定删除该租金条款吗？") }}</p>
        <p v-if="rentTermDeleteDateText" class="helper-text">{{ $t("周期：") }}{{ $t(rentTermDeleteDateText) }}</p>
        <p v-if="rentTermDeleteRentText" class="helper-text">{{ $t("租金：") }}{{ $t(rentTermDeleteRentText) }}</p>
      </div>
      <template #footer>
        <button class="btn" data-testid="rent-term-delete-cancel" :disabled="rentTermDeleteConfirm.deleting" @click="closeRentTermDeleteConfirm">{{ $t("取消") }}</button>
        <button class="btn btn-danger" data-testid="rent-term-delete-confirm" :disabled="rentTermDeleteConfirm.deleting" @click="confirmDeleteRentTerm">
          {{ $t(rentTermDeleteConfirm.deleting ? '删除中' : '确认删除') }}
        </button>
      </template>
    </BaseModal>
    <ContractDepositModal
      :open="depositOpen"
      :mode="depositMode"
      :contract="depositContract"
      :deposit="depositEditing"
      @close="depositOpen = false"
      @save="saveDeposit"
    />
    <ContractRentTermModal
      :open="rentTermOpen"
      :contract="rentTermContract"
      :term="rentTermEditing"
      @close="rentTermOpen = false"
      @save="saveRentTerm"
    />
    <ContractRoomChangeModal
      :open="roomChangeOpen"
      :mode="roomChangeMode"
      :contract="roomChangeContract"
      :room-item="roomChangeRoomItem"
      @close="roomChangeOpen = false"
      @save="saveRoomChange"
    />
    <ContractTerminateModal
      :open="terminateOpen"
      :contract="terminateContractTarget"
      :default-end-date="terminateDefaultEndDate"
      :submitting="terminateSubmitting"
      @close="closeTerminateContract"
      @confirm="confirmTerminateContract"
    />
    <BaseModal :open="Boolean(deleteContractTarget)" :title="$t(&quot;删除合同&quot;)" size="small" @close="closeDeleteContractConfirm">
      <div data-testid="contract-delete-confirm-modal" class="detail-list">
        <p>{{ $t("确定删除合同 ") }}{{ deleteContractTarget?.contract_no || '--' }}{{ $t(" 吗？") }}</p>
        <p class="helper-text">{{ $t("客户：") }}{{ $t(deleteContractCustomerLabel) }}</p>
        <p class="helper-text">{{ $t("删除后无法恢复。") }}</p>
      </div>
      <template #footer>
        <button class="btn" data-testid="contract-delete-cancel" :disabled="deleteContractSubmitting" @click="closeDeleteContractConfirm">{{ $t("取消") }}</button>
        <button class="btn btn-danger" data-testid="contract-delete-confirm" :disabled="deleteContractSubmitting" @click="confirmRemoveContract">
          {{ $t(deleteContractSubmitting ? '删除中' : '确认删除') }}
        </button>
      </template>
    </BaseModal>
    <BaseModal :open="pendingBatchDeleteCount > 0" :title="$t(&quot;批量删除合同&quot;)" size="small" @close="closeBatchDeleteConfirm">
      <div data-testid="contract-batch-delete-confirm-modal" class="detail-list">
        <p>{{ $t("确定删除选中的 ") }}{{ $t(pendingBatchDeleteCount) }}{{ $t(" 条合同吗？") }}</p>
        <p class="helper-text">{{ $t("删除后无法恢复。") }}</p>
      </div>
      <template #footer>
        <button class="btn" data-testid="contract-batch-delete-cancel" :disabled="batchDeleteSubmitting" @click="closeBatchDeleteConfirm">{{ $t("取消") }}</button>
        <button class="btn btn-danger" data-testid="contract-batch-delete-confirm" :disabled="batchDeleteSubmitting" @click="confirmBatchDelete">
          {{ $t(batchDeleteSubmitting ? '删除中' : '确认删除') }}
        </button>
      </template>
    </BaseModal>
    <PdfUploadModal
      :open="pdfOpen"
      :contract="pdfContract"
      @close="pdfOpen = false"
      @upload="uploadPdf"
      @view="pdfContract && openDownload(`/api/contracts/${pdfContract.id}/pdf`)"
    />
    <DocumentGenerateModal
      :open="documentDialogOpen"
      :request="documentRequest"
      @close="documentDialogOpen = false"
      @notify="emit('notify', $event)"
    />
  </section>
</template>
