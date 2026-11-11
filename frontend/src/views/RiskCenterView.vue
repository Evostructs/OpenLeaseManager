<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import BaseModal from '../components/BaseModal.vue'
import RiskFlagList from '../components/RiskFlagList.vue'
import MetricSkeleton from '../components/MetricSkeleton.vue'
import TableSkeleton from '../components/TableSkeleton.vue'
import { api, openDownload } from '../lib/api'
import { aiHighlights, aiMainText, aiResultText, aiSuggestedActions, hasAiResultText, writeTextToClipboard } from '../lib/aiAssistText'
import { formatCurrency, formatDate, formatNumber } from '../lib/format'
import { cachedApi } from '../lib/prefetchCache'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify', 'open-contract', 'open-customer', 'select-tab'])

const riskFlags = ref({ summary: { total: 0, by_severity: { high: 0, medium: 0, low: 0 }, by_type: {} }, items: [] })
const riskLoading = ref(false)
const riskLoaded = ref(false)
const riskAiOpen = ref(false)
const riskAiLoading = ref(false)
const riskAiError = ref('')
const riskAiResult = ref(null)
const riskAiTarget = ref(null)
const selectedRisk = ref(null)
const riskMoreOpen = ref(false)
const riskTargetOpen = ref(false)
const riskTarget = ref(null)
const riskTargetMode = ref('')
const attachmentTasks = ref({ total: 0, summary: { by_target_type: {}, by_severity: {} }, items: [] })
const attachmentTaskLoading = ref(false)
const attachmentTasksLoaded = ref(false)
const riskFilters = ref({
  risk_type: '',
  severity: '',
  query: ''
})

const canExportRisks = computed(() => props.user.role !== 'viewer')
const riskInitialLoading = computed(() => riskLoading.value && !riskLoaded.value)
const riskRefreshing = computed(() => riskLoading.value && riskLoaded.value)
const attachmentTaskSummary = computed(() => attachmentTasks.value.summary || { by_target_type: {}, by_severity: {} })
const riskAiExplanationText = computed(() => aiMainText(riskAiResult.value, 'explanation'))
const riskAiHighlights = computed(() => aiHighlights(riskAiResult.value, 'explanation'))
const riskAiSuggestedActions = computed(() => aiSuggestedActions(riskAiResult.value, 'explanation'))

const riskTypeOptions = [
  { value: '', label: '全部类型' },
  { value: 'overdue_receivable', label: '历史逾期' },
  { value: 'contract_expiring', label: '90天内到期' },
  { value: 'follow_up_overdue', label: '跟进逾期' },
  { value: 'deposit_unpaid', label: '押金缺口' },
  { value: 'attachment_missing', label: '附件缺失' },
  { value: 'large_adjustment', label: '大额减免' }
]

const riskSeverityOptions = [
  { value: '', label: '全部级别' },
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' }
]

const riskTypeMeta = {
  overdue_receivable: { label: '历史逾期', helper: '历史账期', tone: 'danger' },
  contract_expiring: { label: '90天内到期', helper: '续租/出租', tone: 'warning' },
  follow_up_overdue: { label: '跟进逾期', helper: '客户动作', tone: 'warning' },
  deposit_unpaid: { label: '押金缺口', helper: '未收/不足', tone: 'danger' },
  attachment_missing: { label: '附件缺失', helper: '资料完整', tone: 'info' },
  large_adjustment: { label: '大额减免', helper: '条款复核', tone: 'danger' }
}

const severityMeta = {
  high: { label: '高风险', shortLabel: '高', tone: 'danger' },
  medium: { label: '中风险', shortLabel: '中', tone: 'warning' },
  low: { label: '低风险', shortLabel: '低', tone: 'success' }
}

const severityOrder = ['high', 'medium', 'low']
const RISK_CACHE_TTL_MS = 20000
const ATTACHMENT_TASK_CACHE_TTL_MS = 20000

const visibleRisks = computed(() => {
  const query = riskFilters.value.query.trim().toLowerCase()
  const items = riskFlags.value.items || []
  if (!query) return items
  return items.filter((risk) => {
    const fields = [
      risk.title,
      risk.description,
      risk.risk_type,
      risk.contract_no,
      risk.customer_name,
      risk.room_no,
      risk.action_label,
      risk.tower
    ]
    return fields.some((field) => String(field || '').toLowerCase().includes(query))
  })
})

const visibleSeverity = computed(() => {
  return visibleRisks.value.reduce(
    (acc, risk) => {
      acc[risk.severity] = (acc[risk.severity] || 0) + 1
      return acc
    },
    { high: 0, medium: 0, low: 0 }
  )
})

const visibleTypes = computed(() => {
  return visibleRisks.value.reduce((acc, risk) => {
    acc[risk.risk_type] = (acc[risk.risk_type] || 0) + 1
    return acc
  }, {})
})

const overdueRiskCount = computed(() => visibleRisks.value.filter((risk) => isOverdueRisk(risk)).length)

const urgentRisks = computed(() => {
  return [...visibleRisks.value]
    .sort((left, right) => riskUrgencyScore(right) - riskUrgencyScore(left))
    .slice(0, 3)
})

const riskTypeTiles = computed(() => {
  return riskTypeOptions.slice(1).map((item) => {
    const meta = riskTypeMeta[item.value] || { label: item.label, helper: '需要跟进', tone: 'info' }
    const typeRisks = visibleRisks.value.filter((risk) => risk.risk_type === item.value)
    return {
      key: item.value,
      label: meta.label,
      helper: meta.helper,
      tone: meta.tone,
      count: visibleTypes.value[item.value] || 0,
      highCount: typeRisks.filter((risk) => risk.severity === 'high').length,
      overdueCount: typeRisks.filter((risk) => isOverdueRisk(risk)).length,
      active: riskFilters.value.risk_type === item.value
    }
  })
})

const riskMatrixRows = computed(() => {
  return riskTypeTiles.value.map((type) => {
    const counts = { high: 0, medium: 0, low: 0 }
    for (const risk of visibleRisks.value) {
      if (risk.risk_type === type.key && counts[risk.severity] !== undefined) {
        counts[risk.severity] += 1
      }
    }
    const total = severityOrder.reduce((sum, severity) => sum + counts[severity], 0)
    const dominantSeverity = severityOrder.find((severity) => counts[severity]) || 'low'
    return { ...type, counts, total, dominantSeverity }
  })
})

const activeFilterText = computed(() => {
  const type = riskTypeOptions.find((item) => item.value === riskFilters.value.risk_type)?.label
  const severity = riskSeverityOptions.find((item) => item.value === riskFilters.value.severity)?.label
  const parts = []
  if (type && riskFilters.value.risk_type) parts.push(type)
  if (severity && riskFilters.value.severity) parts.push(`${severity}风险`)
  if (riskFilters.value.query.trim()) parts.push('搜索匹配')
  return parts.length ? parts.join(' / ') : '全部类型与级别'
})

const riskTreatmentStats = computed(() => [
  { key: 'high', label: severityMeta.high.label, value: visibleSeverity.value.high || 0, tone: severityMeta.high.tone },
  { key: 'medium', label: severityMeta.medium.label, value: visibleSeverity.value.medium || 0, tone: severityMeta.medium.tone },
  { key: 'low', label: severityMeta.low.label, value: visibleSeverity.value.low || 0, tone: severityMeta.low.tone }
])

const severityFilterChips = computed(() => [
  { key: '', label: '全部风险', tone: 'info', count: visibleRisks.value.length },
  ...severityOrder.map((severity) => ({
    key: severity,
    label: severityMeta[severity].label,
    tone: severityMeta[severity].tone,
    count: visibleSeverity.value[severity] || 0
  }))
])

const hasRiskFilters = computed(() => Boolean(
  riskFilters.value.risk_type ||
  riskFilters.value.severity ||
  riskFilters.value.query.trim()
))

const selectedRiskKey = computed(() => riskKey(selectedRisk.value))
const selectedRiskTone = computed(() => riskTone(selectedRisk.value?.severity))
const selectedRiskStatus = computed(() => {
  const status = selectedRisk.value?.status || selectedRisk.value?.meta?.status || ''
  return {
    label: riskStatusLabel(status),
    tone: riskStatusTone(status)
  }
})
const selectedRiskFacts = computed(() => riskDetailFacts(selectedRisk.value))
const riskTargetFacts = computed(() => riskDetailFacts(riskTarget.value))
const riskTargetContractId = computed(() => riskContractId(riskTarget.value))
const riskTargetCustomerId = computed(() => riskCustomerId(riskTarget.value))
const riskTargetTitle = computed(() => riskTarget.value?.title || riskTypeLabel(riskTarget.value?.risk_type))
const riskTargetActionText = computed(() => {
  if (!riskTarget.value) return ''
  if (riskTargetMode.value === 'receivable') return props.user.role === 'viewer' ? '查看合同' : '查看应收'
  return riskTargetActionLabel(riskTarget.value)
})

watch(visibleRisks, (risks) => {
  if (!risks.length) {
    selectedRisk.value = null
    return
  }
  const currentKey = riskKey(selectedRisk.value)
  if (!currentKey) {
    selectedRisk.value = risks[0]
    return
  }
  selectedRisk.value = risks.find((risk) => riskKey(risk) === currentKey) || risks[0]
})

async function selectRiskType(type) {
  riskFilters.value.risk_type = riskFilters.value.risk_type === type ? '' : type
  await fetchRiskFlags()
  selectFirstVisibleRisk()
}

async function selectRiskSeverity(severity) {
  riskFilters.value.severity = riskFilters.value.severity === severity ? '' : severity
  await fetchRiskFlags()
  selectFirstVisibleRisk()
}

async function selectRiskSegment(type, severity) {
  riskFilters.value.risk_type = type
  riskFilters.value.severity = severity
  await fetchRiskFlags()
  selectFirstVisibleRisk()
}

function selectRisk(risk) {
  selectedRisk.value = risk
}

function selectFirstVisibleRisk() {
  selectedRisk.value = visibleRisks.value[0] || null
}

function riskKey(risk) {
  return risk?.stable_key || risk?.id || ''
}

function riskTypeLabel(type) {
  return riskTypeMeta[type]?.label || '其他风险'
}

function riskSeverityLabel(severity) {
  return severityMeta[severity]?.label || '未分级'
}

function riskTone(severity) {
  return severityMeta[severity]?.tone || 'info'
}

function riskActionLabel(risk) {
  return risk?.action_label || '去处理'
}

function riskStatusLabel(status) {
  return {
    open: '未处理',
    pending: '跟进中',
    resolved: '已处理',
    done: '已完成',
    closed: '已关闭'
  }[status] || '未处理'
}

function riskStatusTone(status) {
  if (['resolved', 'done', 'closed'].includes(status)) return 'success'
  if (status === 'open' || status === 'pending' || !status) return 'warning'
  return 'info'
}

function riskTargetActionLabel(risk) {
  const target = risk?.action_target || {}
  const targetType = target.type || risk?.entity_type || ''
  const customerId = target.customer_id || risk?.customer_id
  const contractId = target.contract_id || risk?.contract_id
  if (targetType === 'receivable') return props.user.role === 'viewer' ? '查看合同' : '查看应收'
  if (targetType === 'customer' && customerId) return '查看客户'
  if (targetType === 'contract' && contractId) return '查看合同'
  if (targetType === 'room') return '查看房源'
  if (contractId) return '查看合同'
  if (customerId) return '查看客户'
  if (risk?.room_no) return '查看房源'
  return '查看目标'
}

function hasRiskTarget(risk) {
  const target = risk?.action_target || {}
  return Boolean(
    target.type ||
    target.customer_id ||
    target.contract_id ||
    risk?.contract_id ||
    risk?.customer_id ||
    risk?.room_no
  )
}

function isOverdueRisk(risk) {
  const anchor = riskFlags.value.date || ''
  const dueDate = risk?.due_date || ''
  return (
    risk?.risk_type === 'overdue_receivable' ||
    risk?.risk_type === 'follow_up_overdue' ||
    Number(risk?.meta?.overdue_days || 0) > 0 ||
    Boolean(anchor && dueDate && dueDate < anchor && risk?.severity === 'high')
  )
}

function riskDateLabel(risk) {
  if (risk?.risk_type === 'contract_expiring') return '到期'
  if (risk?.risk_type === 'follow_up_overdue') return '跟进'
  if (risk?.risk_type === 'large_adjustment') return '生效'
  return '日期'
}

function riskDetailFacts(risk) {
  if (!risk) return []
  const target = risk.action_target || {}
  const facts = []
  const customer = risk.meta?.customer_name || risk.customer_name || target.customer_name || (risk.customer_id ? `#${risk.customer_id}` : '')
  const contract = risk.meta?.contract_no || risk.contract_no || target.contract_no || (risk.contract_id ? `#${risk.contract_id}` : '')
  const room = [risk.meta?.tower || risk.tower, risk.room_no || target.room_no].filter(Boolean).join(' / ')
  const owner = risk.meta?.owner || risk.owner || ''
  if (customer) facts.push({ key: 'customer', label: '客户', value: customer, tone: 'info' })
  if (contract) facts.push({ key: 'contract', label: '合同', value: contract, tone: 'info' })
  if (room) facts.push({ key: 'room', label: '房源', value: room, tone: 'info' })
  if (risk.amount != null) facts.push({ key: 'amount', label: '金额', value: formatCurrency(risk.amount), tone: 'danger' })
  if (risk.due_date) facts.push({ key: 'due_date', label: riskDateLabel(risk), value: formatDate(risk.due_date), tone: isOverdueRisk(risk) ? 'danger' : 'warning' })
  if (owner) facts.push({ key: 'owner', label: '负责人', value: owner, tone: 'info' })
  return facts
}

function riskUrgencyScore(risk) {
  const severityScore = { high: 3000, medium: 1800, low: 700 }[risk?.severity] || 0
  const status = risk?.status || risk?.meta?.status || 'open'
  const statusScore = ['resolved', 'done', 'closed'].includes(status) ? -1200 : 300
  const overdueDays = Number(risk?.meta?.overdue_days || 0)
  const daysLeft = risk?.meta?.days_left == null ? 90 : Number(risk.meta.days_left)
  const amount = Number(risk?.amount || 0)
  const typeScore = {
    overdue_receivable: 900,
    deposit_unpaid: 760,
    follow_up_overdue: 680,
    contract_expiring: 520,
    large_adjustment: 420,
    attachment_missing: 260
  }[risk?.risk_type] || 0
  return severityScore + statusScore + typeScore + overdueDays * 35 + Math.max(0, 90 - daysLeft) * 8 + Math.min(amount / 1000, 900)
}

function urgentRiskObject(risk) {
  return risk?.customer_name || risk?.contract_no || risk?.room_no || risk?.title || '未标明对象'
}

function urgentRiskSignals(risk) {
  const signals = []
  if (risk?.amount != null) signals.push({ label: '金额', value: formatCurrency(risk.amount), tone: 'danger' })
  if (risk?.meta?.overdue_days != null) signals.push({ label: '逾期', value: `${risk.meta.overdue_days} 天`, tone: 'danger' })
  if (risk?.meta?.days_left != null) signals.push({ label: '剩余', value: `${risk.meta.days_left} 天`, tone: risk.meta.days_left <= 30 ? 'danger' : 'warning' })
  if (risk?.due_date) signals.push({ label: riskDateLabel(risk), value: formatDate(risk.due_date), tone: isOverdueRisk(risk) ? 'danger' : 'warning' })
  if (risk?.meta?.owner) signals.push({ label: '负责人', value: risk.meta.owner, tone: 'info' })
  return signals.slice(0, 3)
}

function riskSegmentStyle(row, severity) {
  const value = row.counts[severity] || 0
  const total = Math.max(1, row.total)
  return { flexGrow: Math.max(1, value || total * 0.12) }
}

async function clearRiskFilters() {
  riskFilters.value = { risk_type: '', severity: '', query: '' }
  riskMoreOpen.value = false
  await fetchRiskFlags()
  selectFirstVisibleRisk()
}

async function refreshRiskFlags() {
  riskMoreOpen.value = false
  await fetchRiskFlags({ force: true })
  selectFirstVisibleRisk()
}

async function fetchRiskFlags(options = {}) {
  riskLoading.value = true
  try {
    const params = new URLSearchParams({ limit: '100' })
    if (riskFilters.value.risk_type) params.set('risk_type', riskFilters.value.risk_type)
    if (riskFilters.value.severity) params.set('severity', riskFilters.value.severity)
    riskFlags.value = await cachedApi(`/api/risks?${params}`, {
      force: options.force,
      ttlMs: RISK_CACHE_TTL_MS
    })
    riskLoaded.value = true
  } catch (error) {
    if (!riskLoaded.value) {
      riskFlags.value = { summary: { total: 0, by_severity: { high: 0, medium: 0, low: 0 }, by_type: {} }, items: [] }
    }
    emit('notify', { type: 'error', text: error.message })
  } finally {
    riskLoading.value = false
  }
}

async function fetchAttachmentTasks(options = {}) {
  attachmentTaskLoading.value = true
  try {
    attachmentTasks.value = await cachedApi('/api/attachments/completeness-tasks?limit=20', {
      force: options.force,
      ttlMs: ATTACHMENT_TASK_CACHE_TTL_MS
    })
    attachmentTasksLoaded.value = true
  } catch (error) {
    if (!attachmentTasksLoaded.value) {
      attachmentTasks.value = { total: 0, summary: { by_target_type: {}, by_severity: {} }, items: [] }
    }
    emit('notify', { type: 'error', text: error.message })
  } finally {
    attachmentTaskLoading.value = false
  }
}

async function refreshAttachmentTasks() {
  await fetchAttachmentTasks({ force: true })
}

function exportRiskFlags() {
  if (!canExportRisks.value) return
  riskMoreOpen.value = false
  const params = new URLSearchParams()
  if (riskFilters.value.risk_type) params.set('risk_type', riskFilters.value.risk_type)
  if (riskFilters.value.severity) params.set('severity', riskFilters.value.severity)
  openDownload(`/api/risks/export${params.toString() ? `?${params}` : ''}`)
}

function openAttachmentTask(task) {
  const target = task.action_target || {}
  if (target.type === 'customer' && target.customer_id) {
    emit('open-customer', target.customer_id)
    return
  }
  if (target.type === 'contract' && target.contract_id) {
    emit('open-contract', target.contract_id)
  }
}

function riskTargetType(risk) {
  const target = risk?.action_target || {}
  return target.type || risk?.entity_type || ''
}

function riskCustomerId(risk) {
  const target = risk?.action_target || {}
  return target.customer_id || risk?.customer_id || null
}

function riskContractId(risk) {
  const target = risk?.action_target || {}
  return target.contract_id || risk?.contract_id || null
}

function openRiskTargetContext(risk, mode = '') {
  if (!risk) return
  riskTarget.value = risk
  riskTargetMode.value = mode || riskTargetType(risk)
  riskTargetOpen.value = true
}

function closeRiskTargetContext() {
  riskTargetOpen.value = false
}

function openRiskContractContext(risk) {
  const contractId = riskContractId(risk)
  if (!contractId) return false
  emit('open-contract', contractId)
  return true
}

function openRiskCustomerContext(risk) {
  const customerId = riskCustomerId(risk)
  if (!customerId) return false
  emit('open-customer', customerId)
  return true
}

function openReceivableContext(risk) {
  const contractId = riskContractId(risk)
  if (contractId) {
    emit('open-contract', { id: contractId, initialSection: 'receivables' })
    return
  }
  openRiskTargetContext(risk, 'receivable')
}

function openRiskTargetContract() {
  const contractId = riskTargetContractId.value
  if (!contractId) return
  if (riskTargetMode.value === 'receivable') {
    emit('open-contract', { id: contractId, initialSection: 'receivables' })
  } else {
    emit('open-contract', contractId)
  }
  closeRiskTargetContext()
}

function openRiskTargetCustomer() {
  if (!riskTargetCustomerId.value) return
  emit('open-customer', riskTargetCustomerId.value)
  closeRiskTargetContext()
}

function goRiskTargetRoom() {
  closeRiskTargetContext()
  emit('select-tab', 'rooms')
}

function goRiskTargetReceivables() {
  closeRiskTargetContext()
  emit('select-tab', props.user.role === 'viewer' ? 'contracts' : 'receivables')
}

function openRiskItem(risk) {
  const targetType = riskTargetType(risk)
  const customerId = riskCustomerId(risk)
  const contractId = riskContractId(risk)
  if (targetType === 'customer' && customerId) {
    openRiskCustomerContext(risk)
    return
  }
  if (targetType === 'contract' && contractId) {
    openRiskContractContext(risk)
    return
  }
  if (targetType === 'room') {
    openRiskTargetContext(risk, 'room')
    return
  }
  if (targetType === 'receivable') {
    openReceivableContext(risk)
    return
  }
  if (contractId) {
    openRiskContractContext(risk)
    return
  }
  if (customerId) {
    openRiskCustomerContext(risk)
    return
  }
  if (risk?.room_no) {
    openRiskTargetContext(risk, 'room')
  }
}

function riskAiPayload(risk) {
  return {
    id: risk.id || risk.stable_key || '',
    risk_type: risk.risk_type || '',
    target_type: risk.entity_type || '',
    target_id: risk.entity_id || null,
    customer_id: risk.customer_id || null,
    contract_id: risk.contract_id || null
  }
}

async function explainRisk(risk) {
  if (!risk || riskAiLoading.value) return
  riskAiOpen.value = true
  riskAiTarget.value = risk
  riskAiResult.value = null
  riskAiError.value = ''
  riskAiLoading.value = true
  try {
    riskAiResult.value = await api('/api/ai/risk-explanation', {
      method: 'POST',
      body: JSON.stringify(riskAiPayload(risk))
    })
  } catch (error) {
    riskAiError.value = '生成失败，请稍后重试。'
  } finally {
    riskAiLoading.value = false
  }
}

async function copyRiskAiResult() {
  const text = aiResultText(riskAiResult.value, 'explanation', '风险原因、影响和处理方向')
  if (!text.trim()) return
  try {
    await writeTextToClipboard(text)
    emit('notify', { text: '结果已复制' })
  } catch {
    emit('notify', { type: 'error', text: '复制失败，请手动选择文本复制' })
  }
}

onMounted(() => {
  fetchRiskFlags()
  fetchAttachmentTasks()
})
</script>

<template>
  <section class="content risk-center-page" data-testid="risk-center-page">
    <article class="card risk-center-card risk-overview-card">
      <div class="card-header compact">
        <div>
          <div class="section-label">风险中心</div>
          <h3 class="card-title">风险分诊台</h3>
        </div>
        <div class="pill-group risk-command-actions">
          <span v-if="riskRefreshing" class="metric-pill info refreshing-indicator">同步中</span>
          <span v-else class="metric-pill info">{{ activeFilterText }}</span>
        </div>
      </div>

      <div class="risk-triage-board">
        <section class="risk-matrix-overview" data-testid="risk-matrix-overview">
          <div class="risk-triage-summary">
            <div class="risk-triage-total">
              <span>当前风险</span>
              <strong>{{ formatNumber(visibleRisks.length) }}</strong>
              <small>{{ activeFilterText }}</small>
            </div>
            <div class="risk-triage-pills">
              <span class="metric-pill danger">高风险 {{ formatNumber(visibleSeverity.high || 0) }}</span>
              <span class="metric-pill warning">中风险 {{ formatNumber(visibleSeverity.medium || 0) }}</span>
              <span class="metric-pill danger">逾期 {{ formatNumber(overdueRiskCount || 0) }}</span>
              <span class="metric-pill success">资料待补 {{ formatNumber(attachmentTasks.total || 0) }}</span>
            </div>
          </div>

          <MetricSkeleton v-if="riskInitialLoading" :count="3" />
          <div v-else class="risk-matrix" data-testid="risk-matrix">
            <div class="risk-matrix-title">
              <div>
                <div class="section-label">类型 × 等级</div>
                <strong>风险分布</strong>
              </div>
              <span class="metric-pill info">点击筛选</span>
            </div>
            <div class="risk-matrix-head">
              <span>类型</span>
              <span>级别分布</span>
              <span>合计</span>
            </div>
            <div v-for="row in riskMatrixRows" :key="`matrix-${row.key}`" class="risk-matrix-row">
              <button type="button" class="risk-matrix-label" :class="row.dominantSeverity" @click="selectRiskType(row.key)">
                <strong>{{ row.label }}</strong>
                <small>{{ row.helper }}</small>
              </button>
              <div class="risk-matrix-bars">
                <button
                  v-for="severity in severityOrder"
                  :key="`${row.key}-${severity}`"
                  type="button"
                  class="risk-matrix-segment"
                  :class="[severityMeta[severity].tone, { active: riskFilters.risk_type === row.key && riskFilters.severity === severity }]"
                  :style="riskSegmentStyle(row, severity)"
                  :disabled="!row.counts[severity]"
                  data-risk-segment="true"
                  :data-testid="`risk-matrix-cell-${row.key}-${severity}`"
                  :data-risk-type="row.key"
                  :data-risk-severity="severity"
                  @click="selectRiskSegment(row.key, severity)"
                >
                  <span>{{ severityMeta[severity].shortLabel }}</span>
                  <strong>{{ formatNumber(row.counts[severity]) }}</strong>
                </button>
              </div>
              <div class="risk-matrix-total">
                <strong>{{ formatNumber(row.total) }}</strong>
                <small>{{ row.active ? '筛选中' : '条风险' }}</small>
              </div>
            </div>
          </div>
        </section>

        <section class="urgent-risk-queue" data-testid="urgent-risk-queue">
          <div class="urgent-risk-head">
            <div>
              <div class="section-label">今日优先</div>
              <strong>最急 3 项</strong>
            </div>
            <span class="metric-pill info">{{ formatNumber(urgentRisks.length) }} 项</span>
          </div>

          <div v-if="riskInitialLoading" class="compact-empty compact-empty-tight">读取中...</div>
          <div v-else-if="urgentRisks.length" class="urgent-risk-list">
            <article
              v-for="(risk, index) in urgentRisks"
              :key="`urgent-${riskKey(risk) || index}`"
              class="urgent-risk-row"
              :class="riskTone(risk.severity)"
              data-testid="urgent-risk-item"
              @click="selectRisk(risk)"
            >
              <span class="urgent-risk-rank">{{ index + 1 }}</span>
              <div class="urgent-risk-main">
                <div class="urgent-risk-title">
                  <strong>{{ risk.title }}</strong>
                  <span class="badge compact" :class="riskTone(risk.severity)">
                    {{ riskSeverityLabel(risk.severity) }}
                  </span>
                </div>
                <small>{{ riskTypeLabel(risk.risk_type) }} / {{ urgentRiskObject(risk) }}</small>
                <div v-if="urgentRiskSignals(risk).length" class="urgent-risk-signals">
                  <span
                    v-for="signal in urgentRiskSignals(risk)"
                    :key="`${riskKey(risk)}-${signal.label}-${signal.value}`"
                    :class="signal.tone"
                  >
                    {{ signal.label }} {{ signal.value }}
                  </span>
                </div>
              </div>
              <button type="button" class="btn btn-compact" @click.stop="openRiskItem(risk)">
                {{ riskTargetActionLabel(risk) }}
              </button>
            </article>
          </div>
          <div v-else class="compact-empty compact-empty-tight">暂无今日急项</div>
        </section>
      </div>
    </article>

    <div class="risk-center-workspace">
      <details class="demo-secondary-details risk-list-details" data-testid="risk-list-details">
        <summary>
          <span>明细列表</span>
          <strong>{{ formatNumber(visibleRisks.length) }} 项 / 筛选与导出</strong>
        </summary>
      <article class="card risk-center-card risk-list-card">
        <div class="card-header compact risk-list-header">
          <div>
            <div class="section-label">筛选与处置</div>
            <h3 class="card-title">风险项列表</h3>
          </div>
          <div class="risk-filter-row risk-filter-bar" data-testid="risk-filter-bar">
            <input
              v-model.trim="riskFilters.query"
              class="input"
              data-testid="risk-search-input"
              placeholder="搜索合同 / 客户 / 房间"
            >
            <select
              v-model="riskFilters.risk_type"
              class="visually-hidden"
              data-testid="risk-type-select"
              aria-hidden="true"
              tabindex="-1"
            >
              <option v-for="item in riskTypeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
            <select
              v-model="riskFilters.severity"
              class="visually-hidden"
              data-testid="risk-severity-select"
              aria-hidden="true"
              tabindex="-1"
            >
              <option v-for="item in riskSeverityOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
            <span class="metric-pill info">{{ activeFilterText }}</span>
            <div class="risk-severity-chips" aria-label="风险级别筛选">
              <button
                v-for="item in severityFilterChips"
                :key="`severity-${item.key || 'all'}`"
                type="button"
                class="risk-severity-chip"
                :class="[item.tone, { active: riskFilters.severity === item.key }]"
                @click="selectRiskSeverity(item.key)"
              >
                <span>{{ item.label }}</span>
                <strong>{{ formatNumber(item.count) }}</strong>
              </button>
            </div>
            <button
              v-if="hasRiskFilters"
              type="button"
              class="btn btn-compact"
              @click="clearRiskFilters"
            >
              清空筛选
            </button>
            <div class="risk-more-actions" data-testid="risk-more-actions">
              <button
                type="button"
                class="btn btn-compact risk-more-trigger"
                data-testid="risk-more-trigger"
                aria-controls="risk-more-panel"
                :aria-expanded="String(riskMoreOpen)"
                @click="riskMoreOpen = !riskMoreOpen"
              >
                更多
              </button>
              <div
                v-if="riskMoreOpen"
                id="risk-more-panel"
                class="risk-more-panel"
                data-testid="risk-more-panel"
              >
                <section class="risk-more-section">
                  <span>数据</span>
                  <div class="risk-more-buttons">
                    <button type="button" class="btn btn-compact" :disabled="riskLoading" @click="refreshRiskFlags">
                      刷新风险
                    </button>
                    <button
                      v-if="canExportRisks"
                      type="button"
                      class="btn btn-compact"
                      data-testid="risk-export-button"
                      @click="exportRiskFlags"
                    >
                      导出风险
                    </button>
                  </div>
                </section>
              </div>
            </div>
          </div>
        </div>

        <div class="risk-type-strip" data-testid="risk-type-strip">
          <button
            v-for="item in riskTypeTiles"
            :key="item.key"
            type="button"
            class="risk-type-chip"
            :class="[item.tone, { active: item.active }]"
            @click="selectRiskType(item.key)"
          >
            <span class="risk-type-chip-head">
              <span>{{ item.label }}</span>
              <strong>{{ formatNumber(item.count) }}</strong>
            </span>
            <small>{{ item.helper }}</small>
            <em v-if="item.highCount || item.overdueCount">
              高 {{ formatNumber(item.highCount) }} / 逾期 {{ formatNumber(item.overdueCount) }}
            </em>
          </button>
        </div>

        <div class="risk-treatment-strip">
          <span class="helper-text">当前列表 {{ formatNumber(visibleRisks.length) }} 项</span>
          <span
            v-for="item in riskTreatmentStats"
            :key="item.key"
            class="metric-pill"
            :class="item.tone"
          >
            {{ item.label }} {{ formatNumber(item.value) }}
          </span>
        </div>

        <TableSkeleton v-if="riskInitialLoading" class="risk-loading-layout" :rows="7" :columns="4" />
        <RiskFlagList
          v-else
          :risks="visibleRisks"
          :loading="riskLoading"
          empty-text="当前筛选下暂无风险项"
          :row-clickable="false"
          selectable
          :selected-key="selectedRiskKey"
          explainable
          @open-risk="openRiskItem"
          @select-risk="selectRisk"
          @explain-risk="explainRisk"
        />

        <div v-if="!riskLoading && !visibleRisks.length" class="risk-empty-actions">
          <button type="button" class="btn btn-compact" @click="clearRiskFilters">清空筛选</button>
          <button type="button" class="btn btn-compact" @click="emit('select-tab', 'operations')">查看待办队列</button>
        </div>
      </article>
      </details>

      <details class="demo-secondary-details risk-side-details">
        <summary>
          <span>处置详情</span>
          <strong>选中风险 / 资料待补</strong>
        </summary>
      <aside class="risk-side-workspace">
        <article class="card risk-center-card risk-selected-card" data-testid="risk-selected-inspector">
          <div class="card-header compact">
            <div>
              <div class="section-label">选中风险</div>
              <h3 class="card-title">处置指令板</h3>
            </div>
            <span v-if="selectedRisk" class="badge compact" :class="selectedRiskTone">
              {{ riskSeverityLabel(selectedRisk.severity) }}
            </span>
          </div>

          <template v-if="selectedRisk">
            <div class="risk-selected-title">
              <strong>{{ selectedRisk.title }}</strong>
              <div class="risk-selected-badges">
                <span class="badge compact muted">{{ riskTypeLabel(selectedRisk.risk_type) }}</span>
                <span class="badge compact" :class="selectedRiskStatus.tone">{{ selectedRiskStatus.label }}</span>
              </div>
            </div>

            <div v-if="selectedRiskFacts.length" class="risk-selected-facts">
              <span
                v-for="fact in selectedRiskFacts"
                :key="fact.key"
                class="risk-selected-fact"
                :class="fact.tone"
              >
                <small>{{ fact.label }}</small>
                <b>{{ fact.value }}</b>
              </span>
            </div>

            <div class="risk-selected-reason">
              <span>简要原因</span>
              <p>{{ selectedRisk.description || '暂无补充说明' }}</p>
            </div>

            <div class="risk-selected-next">
              <span>下一步</span>
              <strong>{{ riskActionLabel(selectedRisk) }}</strong>
              <div class="risk-selected-actions">
                <button
                  type="button"
                  class="btn btn-primary btn-compact"
                  data-testid="risk-detail-action-primary"
                  :disabled="!hasRiskTarget(selectedRisk)"
                  @click="openRiskItem(selectedRisk)"
                >
                  {{ riskTargetActionLabel(selectedRisk) }}
                </button>
                <button type="button" class="btn btn-compact" @click="explainRisk(selectedRisk)">
                  解释风险
                </button>
              </div>
            </div>
          </template>
          <div v-else class="compact-empty compact-empty-tight">请选择一条风险</div>
        </article>

        <article class="card risk-center-card risk-attachment-card" data-testid="attachment-completeness-card">
        <div class="card-header compact">
          <div>
            <div class="section-label">附件完整性</div>
            <h3 class="card-title">资料待补 {{ attachmentTasks.total || 0 }}</h3>
          </div>
          <div class="pill-group">
            <button class="btn btn-compact" :disabled="attachmentTaskLoading" @click="refreshAttachmentTasks">刷新</button>
          </div>
        </div>

        <div class="attachment-summary-strip">
          <span class="metric-pill warning">合同 {{ attachmentTaskSummary.by_target_type?.contract || 0 }}</span>
          <span class="metric-pill info">客户 {{ attachmentTaskSummary.by_target_type?.customer || 0 }}</span>
          <span class="metric-pill warning">中风险 {{ attachmentTaskSummary.by_severity?.medium || 0 }}</span>
        </div>

        <div v-if="attachmentTaskLoading" class="compact-empty compact-empty-tight">读取中...</div>
        <div v-else-if="attachmentTasks.items?.length" class="attachment-task-list">
          <div v-for="task in attachmentTasks.items.slice(0, 8)" :key="task.id" class="attachment-task-row">
            <span class="badge" :class="task.severity === 'high' ? 'error' : 'warning'">{{ task.priority === 'high' ? '高' : '中' }}</span>
            <div class="attachment-task-main">
              <div class="attachment-task-title">
                <strong>{{ task.title }}</strong>
                <span>{{ task.contract_no || task.customer_name || task.target_id }}</span>
              </div>
              <small>{{ [task.customer_name, task.room_no, task.missing_category].filter(Boolean).join(' / ') }}</small>
              <p>{{ task.reason }}</p>
            </div>
            <button type="button" class="btn btn-compact" @click="openAttachmentTask(task)">补资料</button>
          </div>
        </div>
        <div v-else class="compact-empty compact-empty-tight">暂无待补附件</div>
        </article>
      </aside>
      </details>
    </div>
  </section>

  <BaseModal :open="riskTargetOpen" title="风险目标" size="small" @close="closeRiskTargetContext">
    <div v-if="riskTarget" class="risk-target-context" data-testid="risk-target-context-modal">
      <div class="risk-target-summary">
        <div class="risk-selected-badges">
          <span class="badge compact" :class="riskTone(riskTarget.severity)">
            {{ riskSeverityLabel(riskTarget.severity) }}
          </span>
          <span class="badge compact muted">{{ riskTypeLabel(riskTarget.risk_type) }}</span>
        </div>
        <strong>{{ riskTargetTitle }}</strong>
        <p>{{ riskTarget.description || '暂无补充说明' }}</p>
      </div>

      <div v-if="riskTargetFacts.length" class="risk-selected-facts">
        <span
          v-for="fact in riskTargetFacts"
          :key="`target-${fact.key}`"
          class="risk-selected-fact"
          :class="fact.tone"
        >
          <small>{{ fact.label }}</small>
          <b>{{ fact.value }}</b>
        </span>
      </div>

      <div class="risk-selected-next">
        <span>下一步</span>
        <strong>{{ riskTargetActionText }}</strong>
        <div class="risk-target-actions">
          <button
            v-if="riskTargetContractId"
            type="button"
            class="btn btn-primary btn-compact"
            data-testid="risk-target-contract-button"
            @click="openRiskTargetContract"
          >
            {{ riskTargetMode === 'receivable' && props.user.role !== 'viewer' ? '查看应收' : '查看合同' }}
          </button>
          <button
            v-if="riskTargetCustomerId"
            type="button"
            class="btn btn-compact"
            data-testid="risk-target-customer-button"
            @click="openRiskTargetCustomer"
          >
            查看客户
          </button>
          <button
            v-if="riskTargetMode === 'room'"
            type="button"
            class="btn btn-compact"
            data-testid="risk-target-room-tab-button"
            @click="goRiskTargetRoom"
          >
            前往房间管理
          </button>
          <button
            v-if="riskTargetMode === 'receivable' && !riskTargetContractId"
            type="button"
            class="btn btn-compact"
            data-testid="risk-target-receivables-tab-button"
            @click="goRiskTargetReceivables"
          >
            {{ props.user.role === 'viewer' ? '前往合同管理' : '前往月度应收' }}
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn" @click="closeRiskTargetContext">关闭</button>
    </template>
  </BaseModal>

  <BaseModal :open="riskAiOpen" title="解释风险" size="small" @close="riskAiOpen = false">
    <div class="ai-assist-modal" data-testid="risk-ai-modal">
      <div v-if="riskAiTarget" class="risk-ai-target">
        <span class="badge" :class="riskAiTarget.severity === 'high' ? 'error' : 'warning'">{{ riskSeverityLabel(riskAiTarget.severity) }}</span>
        <strong>{{ riskAiTarget.title || riskTypeLabel(riskAiTarget.risk_type) }}</strong>
        <p>{{ riskAiTarget.description || '' }}</p>
      </div>
      <div v-if="riskAiLoading" class="compact-empty compact-empty-tight">正在分析风险原因...</div>
      <p v-if="riskAiError" class="form-error" data-testid="risk-ai-error">{{ riskAiError }}</p>
      <div v-if="riskAiResult" class="ai-result-block" data-testid="risk-ai-result">
        <strong>风险原因、影响和处理方向</strong>
        <p>{{ riskAiExplanationText || '暂未生成有效解释，请稍后重试或补充风险上下文。' }}</p>
        <div v-if="riskAiHighlights.length" class="ai-chip-row">
          <span v-for="item in riskAiHighlights" :key="item" class="badge info">{{ item }}</span>
        </div>
        <ul v-if="riskAiSuggestedActions.length" class="ai-action-list">
          <li v-for="item in riskAiSuggestedActions" :key="item">{{ item }}</li>
        </ul>
        <div v-if="hasAiResultText(riskAiResult, 'explanation')" class="ai-result-actions">
          <button
            class="btn btn-compact"
            data-testid="risk-ai-copy"
            @click="copyRiskAiResult"
          >
            复制解释
          </button>
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn" @click="riskAiOpen = false">关闭</button>
    </template>
  </BaseModal>
</template>
