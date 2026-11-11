<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api, openDownload } from '../lib/api'
import {
  aiHighlights,
  aiMainText,
  aiResultText as formatAiResultText,
  aiSuggestedActions,
  followUpPrefillFromSuggestion,
  hasAiResultText,
  writeTextToClipboard
} from '../lib/aiAssistText'
import { formatArea, formatCurrencyFixed, formatDate, formatDateTime } from '../lib/format'
import { buildViewLink } from '../lib/savedViews'
import AttachmentPanel from './AttachmentPanel.vue'
import BaseModal from './BaseModal.vue'
import InfoHint from './InfoHint.vue'
import RiskFlagList from './RiskFlagList.vue'
import StatusBadge from './StatusBadge.vue'

const attachmentSection = ref(null)
const windowOrigin = window.location.origin

const props = defineProps({
  open: Boolean,
  customerId: {
    type: [Number, String],
    default: null
  },
  user: {
    type: Object,
    required: true
  },
  propertyScope: {
    type: Object,
    default: () => ({})
  },
  followUpPrefill: {
    type: Object,
    default: null
  },
  followUpPrefillToken: {
    type: Number,
    default: 0
  },
  refreshToken: {
    type: Number,
    default: 0
  },
  showEditAction: {
    type: Boolean,
    default: true
  },
  closeLabel: {
    type: String,
    default: '关闭'
  },
  layerIndex: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['close', 'notify', 'open-contract', 'edit-customer', 'updated'])

const canEdit = computed(() => (
  ['admin', 'editor'].includes(props.user?.role) && props.propertyScope?.type === 'building'
))
const isAdmin = computed(() => props.user?.role === 'admin' && props.propertyScope?.type === 'building')

const detail = ref(null)
const detailLoading = ref(false)
const followUpFormOpen = ref(false)
const followUpSaving = ref(false)
const followUpEditingId = ref(null)
const followUpDeleteTarget = ref(null)
const followUpDeleting = ref(false)
const aiState = reactive({
  loading: '',
  error: '',
  summary: null,
  suggestion: null
})
let detailRequestId = 0
let appliedFollowUpPrefillToken = 0

const followUpTypes = ['电话', '微信', '拜访', '邮件', '催收', '续租', '其他']
const followUpStatuses = [
  { value: 'open', label: '未完成' },
  { value: 'done', label: '已完成' }
]

const blankFollowUp = () => ({
  contract_id: '',
  follow_up_type: '电话',
  occurred_at: nowDateTimeInput(),
  content: '',
  next_action: '',
  next_follow_up_date: '',
  owner: '',
  status: 'open'
})

const followUpForm = reactive(blankFollowUp())

const operatingSummary = computed(() => detail.value?.operating_summary || {})
const aiBusy = computed(() => Boolean(aiState.loading))
const actionCues = computed(() => operatingSummary.value.action_cues || [])
const customerSubtitle = computed(() => {
  const customer = detail.value || {}
  return [
    customer.short_name || '未登记简称',
    customer.customer_type || '未登记类型',
    customer.industry
  ].filter(Boolean).join(' / ')
})
const customerContactLine = computed(() => {
  const customer = detail.value || {}
  const contact = [customer.contact_name, customer.contact_phone, customer.contact_email].filter(Boolean).join(' / ')
  return contact || '未登记联系人'
})
const summaryCards = computed(() => {
  const summary = operatingSummary.value
  const nearest = summary.nearest_expiring_contract || null
  return [
    {
      key: 'receivable',
      label: '当前月应收',
      value: formatCurrencyFixed(summary.current_month_receivable || 0),
      helper: `${summary.current_month || '--'} / 月租 ${formatCurrencyFixed(summary.current_monthly_rent || 0)}`
    },
    { key: 'balance', label: '累计未收', value: formatCurrencyFixed(summary.unpaid_balance || 0), helper: '来自月度应收' },
    { key: 'overdue', label: '历史逾期', value: formatCurrencyFixed(summary.overdue_balance || 0), helper: '已过月末应收' },
    {
      key: 'deposit-risk',
      label: '押金风险',
      value: `未收 ${formatCurrencyFixed(summary.deposit_unpaid_amount || 0)}`,
      helper: `待退 ${formatCurrencyFixed(summary.deposit_refund_pending_amount || 0)} / 不足 ${formatCurrencyFixed(summary.deposit_insufficient_amount || 0)}`
    },
    { key: 'contracts', label: '关联合同', value: String(summary.contract_count || 0), helper: '未删除合同' },
    { key: 'area', label: '在租面积', value: `${formatArea(summary.current_leased_area || 0)}㎡`, helper: '当前有效合同' },
    {
      key: 'expiry',
      label: '最近到期',
      value: nearest ? `${nearest.contract_no || '--'} / ${summary.nearest_expiry_days ?? '--'}天` : '--',
      helper: nearest ? formatDate(nearest.end_date) : '暂无'
    },
    { key: 'last-follow', label: '最近跟进', value: formatDateTime(summary.last_follow_up_time), helper: '客户跟进记录' },
    { key: 'next-follow', label: '下次跟进', value: formatDate(summary.next_follow_up_date), helper: '未完成记录' },
    { key: 'risks', label: '风险提示', value: `${(detail.value?.risks || []).length}条`, helper: '当前客户风险' }
  ]
})
const operatingSignals = computed(() => {
  const summary = operatingSummary.value
  const nearest = summary.nearest_expiring_contract || null
  const risks = detail.value?.risks || []
  const overdue = Number(summary.overdue_balance || 0)
  const unpaid = Number(summary.unpaid_balance || 0)
  const depositUnpaid = Number(summary.deposit_unpaid_amount || 0)
  const depositRefund = Number(summary.deposit_refund_pending_amount || 0)
  const depositInsufficient = Number(summary.deposit_insufficient_amount || 0)
  const depositAttention = depositUnpaid + depositRefund + depositInsufficient
  const expiryDays = summary.nearest_expiry_days

  return [
    {
      key: 'receivable',
      label: '应收',
      value: overdue > 0 ? formatCurrencyFixed(overdue) : formatCurrencyFixed(unpaid || summary.current_month_receivable || 0),
      helper: overdue > 0 ? '历史逾期' : unpaid > 0 ? '累计未收' : '本月应收',
      tone: overdue > 0 ? 'danger' : unpaid > 0 ? 'warning' : 'success'
    },
    {
      key: 'deposit',
      label: '押金',
      value: depositAttention > 0 ? formatCurrencyFixed(depositAttention) : '无缺口',
      helper: depositAttention > 0 ? `未收 ${formatCurrencyFixed(depositUnpaid)} / 待退 ${formatCurrencyFixed(depositRefund)}` : '已平衡',
      tone: depositInsufficient > 0 || depositUnpaid > 0 ? 'warning' : depositRefund > 0 ? 'info' : 'success'
    },
    {
      key: 'follow-up',
      label: '跟进',
      value: summary.next_follow_up_date ? formatDate(summary.next_follow_up_date) : formatDateTime(summary.last_follow_up_time),
      helper: summary.next_follow_up_date ? '下次跟进' : '最近记录',
      tone: summary.next_follow_up_date ? 'info' : 'warning'
    },
    {
      key: 'expiry',
      label: '到期',
      value: nearest ? `${expiryDays ?? '--'}天` : '暂无',
      helper: nearest ? `${nearest.contract_no || '--'} / ${formatDate(nearest.end_date)}` : '无到期合同',
      tone: nearest ? expiryTone(expiryDays) : 'success'
    },
    {
      key: 'risk',
      label: '风险',
      value: risks.length ? `${risks.length}条` : '暂无',
      helper: risks.length ? '待查看风险提示' : '无当前提示',
      tone: risks.some((risk) => risk.severity === 'high') ? 'danger' : risks.length ? 'warning' : 'success'
    }
  ]
})
const nextActions = computed(() => {
  const summary = operatingSummary.value
  const actions = []
  const nearest = summary.nearest_expiring_contract || null
  const risks = detail.value?.risks || []
  const overdue = Number(summary.overdue_balance || 0)
  const unpaid = Number(summary.unpaid_balance || 0)
  const depositUnpaid = Number(summary.deposit_unpaid_amount || 0)
  const depositRefund = Number(summary.deposit_refund_pending_amount || 0)
  const depositInsufficient = Number(summary.deposit_insufficient_amount || 0)

  if (overdue > 0) {
    actions.push({ key: 'overdue', tone: 'danger', title: '核对历史逾期', meta: `历史逾期 ${formatCurrencyFixed(overdue)}` })
  } else if (unpaid > 0) {
    actions.push({ key: 'unpaid', tone: 'warning', title: '跟进累计未收', meta: `累计未收 ${formatCurrencyFixed(unpaid)}` })
  }
  if (depositUnpaid > 0 || depositInsufficient > 0) {
    actions.push({
      key: 'deposit-gap',
      tone: 'warning',
      title: '处理押金缺口',
      meta: `未收 ${formatCurrencyFixed(depositUnpaid)} / 不足 ${formatCurrencyFixed(depositInsufficient)}`
    })
  }
  if (depositRefund > 0) {
    actions.push({ key: 'deposit-refund', tone: 'info', title: '确认押金退还', meta: `待退 ${formatCurrencyFixed(depositRefund)}` })
  }
  if (summary.next_follow_up_date) {
    actions.push({ key: 'next-follow', tone: 'info', title: '推进下次跟进', meta: formatDate(summary.next_follow_up_date) })
  } else {
    actions.push({ key: 'next-follow-empty', tone: 'warning', title: '补充跟进安排', meta: '暂无下次跟进日期' })
  }
  if (nearest) {
    actions.push({
      key: 'expiry',
      tone: expiryTone(summary.nearest_expiry_days),
      title: '确认到期安排',
      meta: `${nearest.contract_no || '--'} / ${formatDate(nearest.end_date)}`
    })
  }
  if (risks.length) {
    actions.push({
      key: 'risk',
      tone: risks.some((risk) => risk.severity === 'high') ? 'danger' : 'warning',
      title: '查看风险提示',
      meta: `${risks.length}条风险待跟进`
    })
  }

  return actions.slice(0, 5)
})

function statusLabel(status) {
  return status === 'inactive' ? '停用' : '在用'
}

function followUpStatusLabel(status) {
  return status === 'done' ? '已完成' : '未完成'
}

function followUpStatusClass(status) {
  return status === 'done' ? 'success' : 'warning'
}

function cueClass(level) {
  if (level === 'danger') return 'error'
  if (level === 'warning') return 'warning'
  return 'info'
}

function expiryTone(days) {
  if (days == null) return 'info'
  if (Number(days) <= 30) return 'danger'
  if (Number(days) <= 60) return 'warning'
  return 'info'
}

function resetAiState() {
  aiState.loading = ''
  aiState.error = ''
  aiState.summary = null
  aiState.suggestion = null
}

function aiResultText(result, field) {
  return aiMainText(result, field)
}

function notify(payload) {
  emit('notify', payload)
}

async function loadDetail(customerId, { preserveContent = false } = {}) {
  const id = typeof customerId === 'object' ? customerId?.id : customerId
  if (!id) return
  const requestId = ++detailRequestId
  if (!preserveContent || detail.value?.id !== id) {
    detail.value = null
    detailLoading.value = true
    followUpFormOpen.value = false
    resetAiState()
  }
  try {
    const data = await api(`/api/customers/${id}`)
    if (requestId === detailRequestId) {
      detail.value = data
      applyRequestedFollowUp()
    }
  } catch (error) {
    if (requestId === detailRequestId) {
      emit('close')
      notify({ type: 'error', text: error.message })
    }
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}

async function refreshDetail() {
  if (detail.value?.id) await loadDetail(detail.value.id, { preserveContent: true })
}

function openRisk(risk) {
  const target = risk?.action_target || {}
  const contractId = target.contract_id || risk?.contract_id
  if ((target.type === 'receivable' || risk?.entity_type === 'receivable') && contractId) {
    emit('open-contract', { id: contractId, initialSection: 'receivables' })
    return
  }
  if (target.type === 'contract' && target.contract_id) {
    emit('open-contract', { id: target.contract_id })
  }
}

async function runCustomerAi(kind) {
  if (!detail.value?.id || aiBusy.value) return
  const endpoint = kind === 'summary' ? 'customer-summary' : 'follow-up-suggestion'
  const field = kind === 'summary' ? 'summary' : 'suggestion'
  aiState.loading = kind
  aiState.error = ''
  aiState[field] = null
  try {
    const result = await api(`/api/ai/${endpoint}`, {
      method: 'POST',
      body: JSON.stringify({ customer_id: detail.value.id })
    })
    aiState[field] = result
  } catch (error) {
    aiState.error = '生成失败，请稍后重试。'
  } finally {
    aiState.loading = ''
  }
}

async function copyAiResult(result, field, title) {
  const text = formatAiResultText(result, field, title)
  if (!text.trim()) return
  try {
    await writeTextToClipboard(text)
    notify({ text: '结果已复制' })
  } catch {
    notify({ type: 'error', text: '复制失败，请手动选择文本复制' })
  }
}

function applyFollowUpPrefill(prefill) {
  if (!prefill) return
  Object.assign(followUpForm, {
    content: String(prefill.content || '').trim(),
    next_action: String(prefill.next_action || '').trim(),
    follow_up_type: prefill.follow_up_type || followUpForm.follow_up_type,
    contract_id: prefill.contract_id || followUpForm.contract_id,
    status: 'open'
  })
}

function bringSuggestionToFollowUp() {
  if (!canEdit.value || !detail.value?.id || !hasAiResultText(aiState.suggestion, 'suggestion')) return
  openCreateFollowUp(followUpPrefillFromSuggestion(aiState.suggestion))
}

function nowDateTimeInput() {
  const value = new Date()
  value.setMinutes(value.getMinutes() - value.getTimezoneOffset())
  return value.toISOString().slice(0, 16)
}

function toDateTimeInput(value) {
  if (!value) return nowDateTimeInput()
  return String(value).slice(0, 16)
}

function resetFollowUpForm(followUp = null) {
  Object.assign(followUpForm, blankFollowUp())
  if (followUp) {
    Object.assign(followUpForm, {
      contract_id: followUp.contract_id || '',
      follow_up_type: followUp.follow_up_type || '电话',
      occurred_at: toDateTimeInput(followUp.occurred_at),
      content: followUp.content || '',
      next_action: followUp.next_action || '',
      next_follow_up_date: followUp.next_follow_up_date || '',
      owner: followUp.owner || '',
      status: followUp.status || 'open'
    })
  }
}

function openCreateFollowUp(prefill = null) {
  if (!canEdit.value || !detail.value?.id) return
  followUpEditingId.value = null
  resetFollowUpForm()
  applyFollowUpPrefill(prefill)
  followUpFormOpen.value = true
}

function applyRequestedFollowUp() {
  if (!props.followUpPrefillToken || props.followUpPrefillToken === appliedFollowUpPrefillToken || !detail.value?.id) return
  openCreateFollowUp(props.followUpPrefill || null)
  appliedFollowUpPrefillToken = props.followUpPrefillToken
}

function openEditFollowUp(followUp) {
  if (!canEdit.value || !detail.value?.id) return
  followUpEditingId.value = followUp.id
  resetFollowUpForm(followUp)
  followUpFormOpen.value = true
}

function followUpPayload() {
  return {
    contract_id: followUpForm.contract_id ? Number(followUpForm.contract_id) : null,
    follow_up_type: followUpForm.follow_up_type,
    occurred_at: followUpForm.occurred_at || null,
    content: followUpForm.content,
    next_action: followUpForm.next_action,
    next_follow_up_date: followUpForm.next_follow_up_date || null,
    owner: followUpForm.owner,
    status: followUpForm.status
  }
}

async function saveFollowUp() {
  if (!canEdit.value || !detail.value?.id || !followUpForm.content.trim()) return
  followUpSaving.value = true
  try {
    const path = followUpEditingId.value
      ? `/api/customers/${detail.value.id}/follow-ups/${followUpEditingId.value}`
      : `/api/customers/${detail.value.id}/follow-ups`
    const method = followUpEditingId.value ? 'PUT' : 'POST'
    await api(path, { method, body: JSON.stringify(followUpPayload()) })
    notify({ text: followUpEditingId.value ? '跟进记录已更新' : '跟进记录已新增' })
    followUpFormOpen.value = false
    await refreshDetail()
    emit('updated', { customerId: detail.value?.id, type: 'follow-up' })
  } catch (error) {
    notify({ type: 'error', text: error.message })
  } finally {
    followUpSaving.value = false
  }
}

async function completeFollowUp(followUp) {
  if (!canEdit.value || !detail.value?.id) return
  try {
    await api(`/api/customers/${detail.value.id}/follow-ups/${followUp.id}/complete`, { method: 'POST' })
    notify({ text: '跟进记录已完成' })
    await refreshDetail()
    emit('updated', { customerId: detail.value?.id, type: 'follow-up' })
  } catch (error) {
    notify({ type: 'error', text: error.message })
  }
}

function requestDeleteFollowUp(followUp) {
  if (!canEdit.value || !detail.value?.id) return
  followUpDeleteTarget.value = {
    ...followUp,
    customerId: detail.value.id
  }
}

function closeFollowUpDeleteConfirm() {
  if (followUpDeleting.value) return
  followUpDeleteTarget.value = null
}

async function confirmDeleteFollowUp() {
  if (!canEdit.value || !followUpDeleteTarget.value?.id || !followUpDeleteTarget.value?.customerId) return
  const target = followUpDeleteTarget.value
  followUpDeleting.value = true
  try {
    await api(`/api/customers/${target.customerId}/follow-ups/${target.id}`, { method: 'DELETE' })
    notify({ text: '跟进记录已删除' })
    followUpDeleteTarget.value = null
    await refreshDetail()
    emit('updated', { customerId: detail.value?.id, type: 'follow-up' })
  } catch (error) {
    notify({ type: 'error', text: error.message })
  } finally {
    followUpDeleting.value = false
  }
}

function exportDetailFollowUps() {
  if (!detail.value?.id) return
  openDownload(`/api/customers/follow-ups/export?customer_id=${detail.value.id}`)
}

watch(
  () => [props.open, props.customerId],
  ([open, customerId]) => {
    if (open && customerId) {
      loadDetail(customerId)
    } else if (!open) {
      followUpFormOpen.value = false
      followUpDeleteTarget.value = null
    }
  },
  { immediate: true }
)

watch(
  () => props.followUpPrefillToken,
  () => {
    applyRequestedFollowUp()
  }
)

watch(
  () => props.refreshToken,
  () => {
    if (props.open && detail.value?.id) refreshDetail()
  }
)
</script>

<template>
  <BaseModal :open="open" title="客户详情" :layer-index="layerIndex" @close="emit('close')">
    <div v-if="detailLoading" class="compact-empty">正在读取客户详情...</div>
    <div v-else-if="detail" class="detail-list customer-detail" data-testid="customer-detail-modal">
      <div class="btn-row">
        <button v-if="canEdit" class="btn btn-primary" @click="openCreateFollowUp">登记跟进</button>
        <a class="btn" :href="buildViewLink(windowOrigin, 'customers', propertyScope, {}, {type:'customer',id:detail.id})" target="_blank" rel="noopener">打开此详情链接</a>
        <button class="btn" @click="attachmentSection?.scrollIntoView({ block: 'start', behavior: 'smooth' })">查看附件</button>
      </div>
      <section class="customer-detail-operating-panel" data-testid="customer-detail-operating-panel">
        <div class="customer-detail-hero">
          <div>
            <div class="section-label">客户运营面板</div>
            <h3 class="card-title">{{ detail.name }}</h3>
            <div class="customer-identity-meta">
              <span>{{ customerSubtitle }}</span>
              <span>{{ customerContactLine }}</span>
            </div>
          </div>
          <span class="badge" :class="detail.status === 'inactive' ? 'warning' : 'success'">{{ statusLabel(detail.status) }}</span>
        </div>

        <div v-if="actionCues.length" class="customer-cue-row">
          <span v-for="cue in actionCues" :key="`${cue.type}-${cue.text}`" class="badge" :class="cueClass(cue.level)">
            {{ cue.text }}
          </span>
        </div>

        <div class="customer-detail-operating-grid">
          <div class="customer-detail-main-stack">
            <div class="customer-detail-signal-band" data-testid="customer-detail-signal-band">
              <div v-for="signal in operatingSignals" :key="signal.key" class="customer-detail-signal" :class="signal.tone">
                <span>{{ signal.label }}</span>
                <strong>{{ signal.value }}</strong>
                <small>{{ signal.helper }}</small>
              </div>
            </div>

            <div class="customer-summary-grid">
              <div v-for="card in summaryCards" :key="card.key" class="customer-summary-card">
                <span>{{ card.label }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.helper }}</small>
              </div>
            </div>
          </div>

          <aside class="customer-detail-next-actions" data-testid="customer-detail-next-actions">
            <div class="section-label">下一步</div>
            <div v-for="action in nextActions" :key="action.key" class="customer-next-action-row" :class="action.tone">
              <strong>{{ action.title }}</strong>
              <span>{{ action.meta }}</span>
            </div>
          </aside>
        </div>
      </section>

      <div v-if="(detail.risks || []).length" class="detail-section customer-detail-risk-section">
        <div class="section-label">相关风险</div>
        <RiskFlagList :risks="detail.risks || []" compact @open-risk="openRisk" />
      </div>

      <div class="detail-section ai-assist-panel" data-testid="customer-ai-assist">
        <div class="section-title-row">
          <div>
            <div class="section-label">经营摘要</div>
            <h4>客户摘要</h4>
          </div>
          <div class="btn-row">
            <InfoHint text="生成后可复制或带入跟进。" />
            <button
              class="btn btn-compact"
              data-testid="customer-ai-summary-button"
              :disabled="aiBusy"
              @click="runCustomerAi('summary')"
            >
              {{ aiState.loading === 'summary' ? '生成中...' : '生成经营摘要' }}
            </button>
            <button
              class="btn btn-compact"
              data-testid="customer-ai-suggestion-button"
              :disabled="aiBusy"
              @click="runCustomerAi('suggestion')"
            >
              {{ aiState.loading === 'suggestion' ? '生成中...' : '生成跟进建议' }}
            </button>
          </div>
        </div>
        <p v-if="aiState.error" class="form-error" data-testid="customer-ai-error">{{ aiState.error }}</p>
        <div v-if="aiState.loading" class="compact-empty compact-empty-tight">
          {{ aiState.loading === 'summary' ? '正在整理客户摘要...' : '正在生成跟进建议...' }}
        </div>
        <div v-if="aiState.summary" class="ai-result-block" data-testid="customer-ai-summary-result">
          <strong>客户经营摘要</strong>
          <p>{{ aiResultText(aiState.summary, 'summary') || '暂未生成有效摘要，请稍后重试或补充客户资料。' }}</p>
          <div v-if="aiHighlights(aiState.summary, 'summary').length" class="ai-chip-row">
            <span v-for="item in aiHighlights(aiState.summary, 'summary')" :key="item" class="badge info">{{ item }}</span>
          </div>
          <ul v-if="aiSuggestedActions(aiState.summary, 'summary').length" class="ai-action-list">
            <li v-for="item in aiSuggestedActions(aiState.summary, 'summary')" :key="item">{{ item }}</li>
          </ul>
          <div v-if="hasAiResultText(aiState.summary, 'summary')" class="ai-result-actions">
            <button
              class="btn btn-compact"
              data-testid="customer-ai-summary-copy"
              @click="copyAiResult(aiState.summary, 'summary', '客户经营摘要')"
            >
              复制摘要
            </button>
          </div>
        </div>
        <div v-if="aiState.suggestion" class="ai-result-block" data-testid="customer-ai-suggestion-result">
          <strong>跟进建议</strong>
          <p>{{ aiResultText(aiState.suggestion, 'suggestion') || '暂未生成有效建议，请稍后重试或补充客户资料。' }}</p>
          <div v-if="aiHighlights(aiState.suggestion, 'suggestion').length" class="ai-chip-row">
            <span v-for="item in aiHighlights(aiState.suggestion, 'suggestion')" :key="item" class="badge info">{{ item }}</span>
          </div>
          <ul v-if="aiSuggestedActions(aiState.suggestion, 'suggestion').length" class="ai-action-list">
            <li v-for="item in aiSuggestedActions(aiState.suggestion, 'suggestion')" :key="item">{{ item }}</li>
          </ul>
          <div v-if="hasAiResultText(aiState.suggestion, 'suggestion')" class="ai-result-actions">
            <button
              class="btn btn-compact"
              data-testid="customer-ai-suggestion-copy"
              @click="copyAiResult(aiState.suggestion, 'suggestion', '跟进建议')"
            >
              复制建议
            </button>
            <button
              v-if="canEdit"
              class="btn btn-compact"
              data-testid="customer-ai-suggestion-to-follow-up"
              @click="bringSuggestionToFollowUp"
            >
              带入跟进
            </button>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-header">
          <div>
            <div class="section-label">各楼宇共用资料</div>
            <h4>各楼宇共用资料</h4>
          </div>
        </div>
        <div class="stat-grid detail-field-grid">
          <div class="detail-field">
            <div class="section-label">简称</div>
            <div>{{ detail.short_name || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">信用代码</div>
            <div>{{ detail.credit_code || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">类型 / 行业</div>
            <div>{{ detail.customer_type || '--' }} / {{ detail.industry || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">共享联系人</div>
            <div>{{ detail.contact_name || '--' }} {{ detail.contact_phone || '' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">邮箱</div>
            <div>{{ detail.contact_email || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">地址</div>
            <div>{{ detail.address || '--' }}</div>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-header">
          <div>
            <div class="section-label">当前楼宇资料</div>
            <h4>{{ detail.building_profile?.building_code || '当前楼宇' }}</h4>
          </div>
        </div>
        <div class="stat-grid detail-field-grid">
          <div class="detail-field">
            <div class="section-label">楼宇联系人</div>
            <div>{{ detail.local_contact || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">楼宇负责人</div>
            <div>{{ detail.owner || '--' }}</div>
          </div>
          <div class="detail-field">
            <div class="section-label">楼宇备注</div>
            <div>{{ detail.local_notes || '--' }}</div>
          </div>
        </div>
      </div>

      <div class="detail-section detail-note-section">
        <div class="section-label">共享备注</div>
        <p>{{ detail.notes || '暂无备注' }}</p>
      </div>

      <div ref="attachmentSection">
      <AttachmentPanel
        entity-type="customer"
        :entity-id="detail.id"
        :attachments="detail.attachments || []"
        :summary="detail.attachment_summary || {}"
        :can-edit="canEdit"
        :can-delete="isAdmin"
        required-label="证照/营业执照"
        @refresh="refreshDetail"
        @notify="notify"
      />
      </div>

      <div class="detail-section">
        <div class="section-title-row">
          <div>
            <div class="section-label">跟进时间线</div>
            <h4>客户互动</h4>
          </div>
          <div class="btn-row">
            <button class="btn btn-compact" @click="exportDetailFollowUps">导出跟进</button>
            <button v-if="canEdit" class="btn btn-compact" @click="openCreateFollowUp">新增跟进</button>
          </div>
        </div>
        <div v-if="(detail.follow_ups || []).length" class="follow-up-timeline">
          <div v-for="followUp in detail.follow_ups" :key="followUp.id" class="follow-up-item">
            <div class="follow-up-head">
              <div>
                <span class="badge info">{{ followUp.follow_up_type || '其他' }}</span>
                <strong>{{ formatDateTime(followUp.occurred_at) }}</strong>
              </div>
              <span class="badge" :class="followUpStatusClass(followUp.status)">{{ followUpStatusLabel(followUp.status) }}</span>
            </div>
            <p>{{ followUp.content }}</p>
            <div class="follow-up-meta">
              <span>下一步：{{ followUp.next_action || '--' }}</span>
              <span>下次：{{ formatDate(followUp.next_follow_up_date) }}</span>
              <span>负责人：{{ followUp.owner || '--' }}</span>
              <span v-if="followUp.contract_no">合同：{{ followUp.contract_no }}</span>
            </div>
            <div v-if="canEdit" class="follow-up-actions">
              <button class="btn btn-compact" @click="openEditFollowUp(followUp)">编辑</button>
              <button v-if="followUp.status !== 'done'" class="btn btn-compact" @click="completeFollowUp(followUp)">完成</button>
              <button class="btn btn-compact btn-danger" @click="requestDeleteFollowUp(followUp)">删除</button>
            </div>
          </div>
        </div>
        <div v-else class="compact-empty">暂无跟进记录</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-header">
          <div>
            <div class="section-label">关联合同</div>
            <h4>租赁关系</h4>
          </div>
        </div>
        <div class="table-wrapper" style="margin-top:12px">
          <table class="data-table" style="min-width:900px">
            <thead>
              <tr>
                <th>合同编号</th>
                <th>房源</th>
                <th>签约类型</th>
                <th>租期</th>
                <th class="right">面积</th>
                <th class="right">月租金</th>
                <th class="right">押金未收</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="contract in detail.contracts || []" :key="contract.id">
                <td>
                  <button class="link-button" type="button" @click="emit('open-contract', contract.id)">{{ contract.contract_no }}</button>
                </td>
                <td>{{ contract.room_no || '--' }}</td>
                <td>{{ contract.lease_type || '--' }}</td>
                <td>{{ formatDate(contract.start_date) }} - {{ formatDate(contract.end_date) }}</td>
                <td class="right">{{ formatArea(contract.area) }}㎡</td>
                <td class="right">{{ formatCurrencyFixed(contract.monthly_rent) }}</td>
                <td class="right">{{ formatCurrencyFixed(contract.deposit_summary?.unpaid_amount || 0) }}</td>
                <td><StatusBadge :status="contract.status" /></td>
              </tr>
              <tr v-if="!(detail.contracts || []).length">
                <td colspan="8" class="list-empty">暂无关联合同</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">{{ closeLabel }}</button>
      <button v-if="showEditAction && canEdit && detail" class="btn btn-primary" @click="emit('edit-customer', detail)">编辑</button>
    </template>
  </BaseModal>

  <BaseModal :layer-index="layerIndex + 1" :open="followUpFormOpen" :title="followUpEditingId ? '编辑跟进' : '新增跟进'" @close="followUpFormOpen = false">
    <div class="form-grid">
      <label class="form-field">
        <span class="section-label">跟进类型</span>
        <select v-model="followUpForm.follow_up_type" class="select">
          <option v-for="type in followUpTypes" :key="type" :value="type">{{ type }}</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">状态</span>
        <select v-model="followUpForm.status" class="select">
          <option v-for="item in followUpStatuses" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">跟进时间</span>
        <input v-model="followUpForm.occurred_at" class="input" type="datetime-local" />
      </label>
      <label class="form-field">
        <span class="section-label">负责人</span>
        <input v-model="followUpForm.owner" class="input" />
      </label>
      <label class="form-field">
        <span class="section-label">关联合同</span>
        <select v-model="followUpForm.contract_id" class="select">
          <option value="">不关联合同</option>
          <option v-for="contract in detail?.contracts || []" :key="contract.id" :value="contract.id">
            {{ contract.contract_no }} / {{ contract.room_no || '--' }}
          </option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">下次跟进日期</span>
        <input v-model="followUpForm.next_follow_up_date" class="input" type="date" />
      </label>
      <label class="form-field full">
        <span class="section-label">跟进内容</span>
        <textarea v-model="followUpForm.content" class="textarea" data-testid="follow-up-content-input" />
      </label>
      <label class="form-field full">
        <span class="section-label">下一步动作</span>
        <textarea v-model="followUpForm.next_action" class="textarea customer-next-action-input" data-testid="follow-up-next-action-input" />
      </label>
    </div>

    <template #footer>
      <button class="btn" @click="followUpFormOpen = false">取消</button>
      <button class="btn btn-primary" :disabled="followUpSaving || !followUpForm.content.trim()" @click="saveFollowUp">保存</button>
    </template>
  </BaseModal>

  <BaseModal :layer-index="layerIndex + 1" :open="Boolean(followUpDeleteTarget)" title="删除跟进记录" size="small" @close="closeFollowUpDeleteConfirm">
    <div class="compact-empty">
      确定删除这条跟进记录吗？删除后无法恢复。
    </div>

    <template #footer>
      <button class="btn" :disabled="followUpDeleting" @click="closeFollowUpDeleteConfirm">取消</button>
      <button class="btn btn-danger" :disabled="followUpDeleting" @click="confirmDeleteFollowUp">确认删除</button>
    </template>
  </BaseModal>
</template>
