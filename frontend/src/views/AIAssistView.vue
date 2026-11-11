<script setup>
import { computed, reactive } from 'vue'
import InfoHint from '../components/InfoHint.vue'
import RiskFlagList from '../components/RiskFlagList.vue'
import { api } from '../lib/api'
import { cachedApi } from '../lib/prefetchCache'
import {
  aiHighlights,
  aiMainText,
  aiResultText,
  aiSuggestedActions,
  followUpPrefillFromSuggestion,
  hasAiResultText,
  writeTextToClipboard
} from '../lib/aiAssistText'
import { formatCurrencyFixed, formatDate } from '../lib/format'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify', 'open-contract', 'open-customer', 'select-tab'])

const customerState = reactive({
  keyword: '',
  loading: false,
  detailLoading: false,
  searched: false,
  list: [],
  selected: null,
  error: '',
  summaryLoading: false,
  suggestionLoading: false,
  summaryError: '',
  suggestionError: '',
  summary: null,
  suggestion: null
})

const riskState = reactive({
  loading: false,
  loaded: false,
  keyword: '',
  severity: 'all',
  items: [],
  selected: null,
  explanationLoading: false,
  error: '',
  explanation: null
})

const riskTypeLabels = {
  overdue_receivable: '历史逾期',
  contract_expiring: '合同到期',
  follow_up_overdue: '跟进逾期',
  deposit_unpaid: '押金缺口',
  attachment_missing: '附件缺失',
  large_adjustment: '大额减免'
}

const riskStatusLabels = {
  open: '未处理',
  pending: '跟进中',
  resolved: '已处理',
  done: '已完成',
  closed: '已关闭'
}

const selectedSummary = computed(() => customerState.selected?.operating_summary || {})
const selectedCustomerName = computed(() => customerState.selected?.name || '当前客户')
const selectedCustomerSignals = computed(() => {
  const summary = selectedSummary.value
  const cues = Array.isArray(summary.action_cues) ? summary.action_cues : []
  const overdue = Number(summary.overdue_balance || 0)
  const unpaid = Number(summary.unpaid_balance || 0)
  const depositUnpaid = Number(summary.deposit_unpaid_amount || 0)
  const depositInsufficient = Number(summary.deposit_insufficient_amount || 0)
  const hasDangerCue = cues.some((cue) => cue?.level === 'danger')
  return [
    {
      label: '合同',
      value: `${summary.contract_count || 0} 份`,
      helper: summary.current_monthly_rent ? `${formatCurrencyFixed(summary.current_monthly_rent)}/月` : '暂无有效合同',
      tone: Number(summary.contract_count || 0) > 0 ? 'info' : 'muted'
    },
    {
      label: '应收',
      value: formatCurrencyFixed(summary.current_month_receivable || 0),
      helper: summary.current_month || '本月',
      tone: Number(summary.current_month_receivable || 0) > 0 ? 'info' : 'muted'
    },
    {
      label: '累计未收',
      value: formatCurrencyFixed(unpaid),
      helper: `历史逾期 ${formatCurrencyFixed(overdue)}`,
      tone: overdue > 0 ? 'danger' : unpaid > 0 ? 'warning' : 'success'
    },
    {
      label: '押金',
      value: depositUnpaid > 0 ? `未收 ${formatCurrencyFixed(depositUnpaid)}` : '已平衡',
      helper: depositInsufficient > 0 ? `不足 ${formatCurrencyFixed(depositInsufficient)}` : `可用 ${formatCurrencyFixed(summary.deposit_available_amount || 0)}`,
      tone: depositUnpaid > 0 || depositInsufficient > 0 ? 'warning' : 'success'
    },
    {
      label: '风险',
      value: cues.length ? `${cues.length} 个提醒` : '平稳',
      helper: cues[0]?.text || '暂无重点风险',
      tone: hasDangerCue ? 'danger' : cues.length ? 'warning' : 'success'
    },
    {
      label: '跟进',
      value: summary.next_follow_up_date ? formatDate(summary.next_follow_up_date) : '待安排',
      helper: summary.last_follow_up_time ? `上次 ${formatDate(summary.last_follow_up_time)}` : '暂无跟进记录',
      tone: summary.next_follow_up_date ? 'info' : 'warning'
    }
  ]
})
const hasCustomerOutputState = computed(() =>
  Boolean(
    customerState.selected ||
      customerState.summaryLoading ||
      customerState.suggestionLoading ||
      customerState.summaryError ||
      customerState.suggestionError ||
      customerState.summary ||
      customerState.suggestion
  )
)
const customerSummaryText = computed(() => aiMainText(customerState.summary, 'summary'))
const customerSuggestionText = computed(() => aiMainText(customerState.suggestion, 'suggestion'))
const customerEvidenceItems = computed(() => [
  ...aiHighlights(customerState.summary, 'summary'),
  ...aiHighlights(customerState.suggestion, 'suggestion')
])
const customerNextActionItems = computed(() => [
  ...aiSuggestedActions(customerState.summary, 'summary'),
  ...aiSuggestedActions(customerState.suggestion, 'suggestion')
])
const canEdit = computed(() => ['admin', 'editor'].includes(props.user?.role))
const selectedRiskSignals = computed(() => {
  const risk = riskState.selected
  if (!risk) return []
  const hasAmount = risk.amount != null
  const primaryDate = risk.due_date || risk.occurred_on
  const isResolved = ['resolved', 'done', 'closed'].includes(risk.status)
  return [
    {
      label: '等级',
      value: riskSeverityLabel(risk.severity),
      helper: riskTypeLabel(risk.risk_type),
      tone: risk.severity === 'high' ? 'danger' : risk.severity === 'medium' ? 'warning' : 'info'
    },
    {
      label: '对象',
      value: risk.customer_name || risk.contract_no || risk.room_no || '--',
      helper: [risk.contract_no, risk.room_no].filter(Boolean).join(' / ') || '查看关联目标',
      tone: 'info'
    },
    {
      label: hasAmount ? '金额' : '日期',
      value: hasAmount ? formatCurrencyFixed(risk.amount) : formatDate(primaryDate),
      helper: hasAmount && primaryDate ? `日期 ${formatDate(primaryDate)}` : '关注处理时点',
      tone: hasAmount ? 'warning' : 'info'
    },
    {
      label: '状态',
      value: riskStatusLabel(risk.status),
      helper: risk.action_label || '查看目标后处理',
      tone: isResolved ? 'success' : 'warning'
    }
  ]
})
const filteredRisks = computed(() => {
  const keyword = riskState.keyword.trim().toLowerCase()
  return (riskState.items || []).filter((risk) => {
    const severityMatch = riskState.severity === 'all' || risk.severity === riskState.severity
    if (!severityMatch) return false
    if (!keyword) return true
    return [
      risk.title,
      risk.description,
      risk.risk_type,
      risk.customer_name,
      risk.contract_no,
      risk.room_no
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const selectedRiskKey = computed(() => riskKey(riskState.selected))
const riskExplanationText = computed(() => aiMainText(riskState.explanation, 'explanation'))
const riskEvidenceItems = computed(() => aiHighlights(riskState.explanation, 'explanation'))
const riskNextActionItems = computed(() => aiSuggestedActions(riskState.explanation, 'explanation'))

function riskKey(risk) {
  return risk?.stable_key || risk?.id || ''
}

function riskSeverityLabel(severity) {
  return {
    high: '高风险',
    medium: '中风险',
    low: '低风险'
  }[severity] || '未分级'
}

function riskTypeLabel(type) {
  return riskTypeLabels[type] || type || '--'
}

function riskStatusLabel(status) {
  return riskStatusLabels[status] || status || '未处理'
}

function clearCustomerAiResults() {
  customerState.summaryError = ''
  customerState.suggestionError = ''
  customerState.summary = null
  customerState.suggestion = null
}

async function searchCustomers() {
  if (customerState.loading) return
  customerState.loading = true
  customerState.searched = true
  customerState.error = ''
  try {
    const params = new URLSearchParams({
      page: '1',
      page_size: '10'
    })
    if (customerState.keyword.trim()) params.set('keyword', customerState.keyword.trim())
    const data = await cachedApi(`/api/customers?${params}`)
    customerState.list = data.items || []
  } catch (error) {
    customerState.error = error.message || '客户搜索失败'
    customerState.list = []
  } finally {
    customerState.loading = false
  }
}

async function selectCustomer(customer) {
  if (!customer?.id || customerState.detailLoading) return
  clearCustomerAiResults()
  customerState.detailLoading = true
  customerState.error = ''
  try {
    customerState.selected = await cachedApi(`/api/customers/${customer.id}`)
  } catch (error) {
    customerState.error = error.message || '客户详情读取失败'
    customerState.selected = null
  } finally {
    customerState.detailLoading = false
  }
}

async function runCustomerAi(kind) {
  if (!customerState.selected?.id) return
  const endpoint = kind === 'summary' ? 'customer-summary' : 'follow-up-suggestion'
  const resultField = kind === 'summary' ? 'summary' : 'suggestion'
  const loadingField = kind === 'summary' ? 'summaryLoading' : 'suggestionLoading'
  const errorField = kind === 'summary' ? 'summaryError' : 'suggestionError'
  if (customerState[loadingField]) return
  customerState[loadingField] = true
  customerState[errorField] = ''
  customerState[resultField] = null
  try {
    customerState[resultField] = await api(`/api/ai/${endpoint}`, {
      method: 'POST',
      body: JSON.stringify({ customer_id: customerState.selected.id })
    })
  } catch (error) {
    customerState[errorField] = '生成失败，请稍后重试。'
  } finally {
    customerState[loadingField] = false
  }
}

async function fetchRisks() {
  if (riskState.loading) return
  riskState.loading = true
  riskState.loaded = true
  riskState.error = ''
  try {
    const data = await cachedApi('/api/risks?limit=50')
    riskState.items = data.items || []
  } catch (error) {
    riskState.error = error.message || '风险项读取失败'
    riskState.items = []
  } finally {
    riskState.loading = false
  }
}

function selectRisk(risk) {
  if (!risk) return
  if (riskKey(riskState.selected) !== riskKey(risk)) {
    riskState.explanation = null
  }
  riskState.selected = risk
  riskState.error = ''
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
  if (!risk || riskState.explanationLoading) return
  if (riskKey(riskState.selected) !== riskKey(risk)) {
    riskState.explanation = null
  }
  riskState.selected = risk
  riskState.error = ''
  riskState.explanationLoading = true
  try {
    riskState.explanation = await api('/api/ai/risk-explanation', {
      method: 'POST',
      body: JSON.stringify(riskAiPayload(risk))
    })
  } catch (error) {
    riskState.error = '生成失败，请稍后重试。'
  } finally {
    riskState.explanationLoading = false
  }
}

async function copyAiResult(result, field, title) {
  const text = aiResultText(result, field, title)
  if (!text.trim()) return
  try {
    await writeTextToClipboard(text)
    emit('notify', { text: '结果已复制' })
  } catch {
    emit('notify', { type: 'error', text: '复制失败，请手动选择文本复制' })
  }
}

function bringSuggestionToFollowUp() {
  if (!canEdit.value || !customerState.selected?.id || !hasAiResultText(customerState.suggestion, 'suggestion')) return
  emit('open-customer', {
    id: customerState.selected.id,
    followUpPrefill: followUpPrefillFromSuggestion(customerState.suggestion)
  })
}

function openRiskTarget(risk) {
  const target = risk?.action_target || {}
  if (target.type === 'customer' && target.customer_id) {
    emit('open-customer', target.customer_id)
    return
  }
  if (target.type === 'contract' && target.contract_id) {
    emit('open-contract', target.contract_id)
    return
  }
  if (target.type === 'receivable') {
    if (props.user?.role === 'viewer') {
      if (target.contract_id) {
        emit('open-contract', target.contract_id)
      } else {
        emit('select-tab', 'contracts')
      }
      return
    }
    emit('select-tab', 'receivables')
    return
  }
  if (target.type === 'room') {
    emit('select-tab', 'rooms')
    return
  }
  emit('notify', { type: 'error', text: '暂不支持打开该风险目标' })
}
</script>

<template>
  <section class="content ai-assist-page" data-testid="ai-assist-page">
    <article class="card ai-assist-card ai-workbench-shell">
      <div class="card-header compact ai-workbench-command">
        <div>
          <div class="section-label">{{ $t("经营研判台") }}</div>
          <h3 class="card-title">{{ $t("经营研判流程") }}</h3>
        </div>
        <div class="ai-command-actions">
          <InfoHint :text="$t(&quot;选定对象后生成结论、依据和下一步。&quot;)" />
          <button class="btn btn-compact" @click="emit('select-tab', 'operations')">{{ $t("待办队列") }}</button>
          <button class="btn btn-compact" @click="emit('select-tab', 'risks')">{{ $t("风险中心") }}</button>
        </div>
      </div>

      <div class="ai-flow-strip" data-testid="ai-unified-flow" :aria-label="$t(&quot;研判流程&quot;)">
        <span>{{ $t("选对象") }}</span>
        <span>{{ $t("生成结论") }}</span>
        <span>{{ $t("看依据") }}</span>
        <span>{{ $t("定下一步") }}</span>
      </div>

      <div class="ai-dual-workbench-grid">
      <section class="ai-workbench-pane ai-customer-workbench" data-testid="ai-mode-customer">
        <div class="ai-pane-head">
          <div>
            <div class="section-label">{{ $t("客户对象") }}</div>
            <strong>{{ $t("客户经营摘要") }}</strong>
          </div>
          <InfoHint :text="$t(&quot;选定客户后，可生成经营摘要或跟进建议。&quot;)" />
        </div>
      <div class="ai-workbench-grid">
        <div class="ai-control-column">
          <div class="ai-picker-panel">
            <div class="ai-panel-heading">
              <strong>{{ $t("对象选择") }}</strong>
              <span class="badge compact info">{{ $t("客户") }}</span>
            </div>
            <div class="filter-cluster ai-search-controls">
              <input
                v-model="customerState.keyword"
                class="input"
                data-testid="ai-customer-search-input"
                :placeholder="$t(&quot;客户名称 / 简称 / 信用代码&quot;)"
                @keyup.enter="searchCustomers"
              />
              <button class="btn" data-testid="ai-customer-search-button" :disabled="customerState.loading" @click="searchCustomers">
                {{ $t(customerState.loading ? '搜索中...' : '搜索客户') }}
              </button>
            </div>
            <p v-if="customerState.error" class="form-error">{{ $t(customerState.error) }}</p>
            <div class="ai-search-list" data-testid="ai-customer-search-list">
              <div v-if="customerState.loading && !customerState.list.length" class="ai-list-skeleton" data-testid="ai-customer-search-skeleton" aria-hidden="true">
                <div v-for="row in 3" :key="`customer-search-skeleton-${row}`" class="ai-list-skeleton-row">
                  <span class="skeleton-line skeleton-line-title"></span>
                  <span class="skeleton-line skeleton-line-sm"></span>
                  <span class="skeleton-line skeleton-line-xs"></span>
                </div>
              </div>
              <button
                v-for="customer in customerState.list"
                :key="customer.id"
                type="button"
                class="ai-search-row"
                :class="{ active: customerState.selected?.id === customer.id }"
                @click="selectCustomer(customer)"
              >
                <strong>{{ customer.name }}</strong>
                <span>{{ customer.short_name || customer.contact_name || '--' }}</span>
                <small>{{ $t(customer.contract_count || 0) }}{{ $t(" 个合同") }}</small>
              </button>
              <div v-if="!customerState.searched && !customerState.loading" class="ai-ready-state ai-search-ready">
                <span class="ai-ready-kicker">{{ $t("客户") }}</span>
                <strong>{{ $t("选择客户") }}</strong>
                <small>{{ $t("输入名称或信用代码") }}</small>
              </div>
              <div v-else-if="customerState.searched && !customerState.list.length && !customerState.loading" class="compact-empty compact-empty-tight">{{ $t(" 未找到匹配客户 ") }}</div>
            </div>
          </div>

          <div class="ai-context-panel">
            <div class="ai-panel-heading">
              <strong>{{ $t("经营信号") }}</strong>
              <span>{{ $t("合同 / 应收 / 押金 / 跟进") }}</span>
            </div>
            <div v-if="customerState.detailLoading" class="ai-panel-skeleton ai-context-loading" data-testid="ai-customer-detail-skeleton" aria-hidden="true">
              <span class="skeleton-line skeleton-line-title"></span>
              <span class="skeleton-line skeleton-line-md"></span>
              <span class="skeleton-line skeleton-line-sm"></span>
              <div class="ai-skeleton-signal-band">
                <span v-for="item in 6" :key="`customer-signal-skeleton-${item}`" class="skeleton-line skeleton-line-sm"></span>
              </div>
            </div>
            <template v-else-if="customerState.selected">
              <div class="section-title-row">
                <div>
                  <div class="section-label">{{ $t("当前客户") }}</div>
                  <h4>{{ customerState.selected.name }}</h4>
                </div>
                <button class="btn btn-compact" @click="emit('open-customer', customerState.selected.id)">{{ $t("查看客户详情") }}</button>
              </div>
              <div class="ai-context-signal-band" data-testid="ai-context-signal-band">
                <div
                  v-for="signal in selectedCustomerSignals"
                  :key="signal.label"
                  class="ai-signal-card"
                  :class="`tone-${signal.tone}`"
                >
                  <span>{{ $t(signal.label) }}</span>
                  <strong>{{ $t(signal.value) }}</strong>
                  <small>{{ $t(signal.helper) }}</small>
                </div>
              </div>
              <div class="ai-action-grid">
                <button
                  class="btn btn-primary"
                  data-testid="ai-workbench-summary-button"
                  :disabled="customerState.summaryLoading"
                  @click="runCustomerAi('summary')"
                >
                  {{ $t(customerState.summaryLoading ? '生成中...' : '生成经营摘要') }}
                </button>
                <button
                  class="btn"
                  data-testid="ai-workbench-suggestion-button"
                  :disabled="customerState.suggestionLoading"
                  @click="runCustomerAi('suggestion')"
                >
                  {{ $t(customerState.suggestionLoading ? '生成中...' : '生成跟进建议') }}
                </button>
              </div>
            </template>
            <div v-else class="ai-ready-state ai-context-ready">
              <span class="ai-ready-kicker">{{ $t("经营信号") }}</span>
              <strong>{{ $t("选择客户") }}</strong>
              <small>{{ $t("合同、应收、押金、风险、跟进") }}</small>
            </div>
          </div>
        </div>

        <div class="ai-result-panel ai-output-stack" data-testid="ai-customer-result-panel">
          <div class="ai-panel-heading">
            <strong>{{ $t("结果") }}</strong>
          </div>

          <div v-if="!hasCustomerOutputState" class="ai-ready-panel ai-result-ready">
            <span class="ai-ready-kicker">{{ $t("下一步") }}</span>
            <strong>{{ $t("选择客户") }}</strong>
            <div class="ai-ready-steps">
              <span>{{ $t("搜客户") }}</span>
              <span>{{ $t("看信号") }}</span>
              <span>{{ $t("整理摘要") }}</span>
            </div>
          </div>

          <template v-else>
            <p v-if="customerState.summaryError" class="form-error" data-testid="ai-workbench-error">{{ $t(customerState.summaryError) }}</p>
            <p v-if="customerState.suggestionError" class="form-error" data-testid="ai-workbench-suggestion-error">{{ $t(customerState.suggestionError) }}</p>
            <div
              v-if="customerState.summaryLoading"
              class="ai-panel-skeleton ai-result-loading-skeleton"
              data-testid="ai-summary-skeleton"
              aria-hidden="true"
            >
              <span class="skeleton-line skeleton-line-title"></span>
              <span class="skeleton-line skeleton-line-md"></span>
              <span class="skeleton-line skeleton-line-sm"></span>
              <span class="skeleton-line skeleton-line-md"></span>
            </div>
            <div
              v-else-if="customerState.suggestionLoading"
              class="ai-panel-skeleton ai-result-loading-skeleton"
              data-testid="ai-suggestion-skeleton"
              aria-hidden="true"
            >
              <span class="skeleton-line skeleton-line-title"></span>
              <span class="skeleton-line skeleton-line-md"></span>
              <span class="skeleton-line skeleton-line-sm"></span>
              <span class="skeleton-line skeleton-line-md"></span>
            </div>
            <div v-else class="ai-result-block ai-review-card ai-output-decision-card" data-testid="ai-output-decision-card">
              <section class="ai-review-section" data-testid="ai-result-summary">
                <strong>{{ $t("结论") }}</strong>
                <div v-if="customerSummaryText || customerSuggestionText" class="ai-result-lines">
                  <p v-if="customerSummaryText" data-testid="ai-customer-summary-result">{{ $t(customerSummaryText) }}</p>
                  <p v-if="customerSuggestionText" data-testid="ai-customer-suggestion-result">{{ $t(customerSuggestionText) }}</p>
                </div>
                <p v-else class="helper-text">{{ $t("选择客户后整理摘要或跟进建议。") }}</p>
              </section>
              <section v-if="customerEvidenceItems.length" class="ai-review-section" data-testid="ai-result-evidence">
                <strong>{{ $t("依据") }}</strong>
                <div class="ai-chip-row">
                  <span v-for="item in customerEvidenceItems" :key="item" class="badge info">{{ $t(item) }}</span>
                </div>
              </section>
              <section
                v-if="customerNextActionItems.length || hasAiResultText(customerState.summary, 'summary') || hasAiResultText(customerState.suggestion, 'suggestion')"
                class="ai-review-section"
                data-testid="ai-result-next-actions"
              >
                <strong>{{ $t("下一步") }}</strong>
                <ul v-if="customerNextActionItems.length" class="ai-action-list">
                  <li v-for="item in customerNextActionItems" :key="item">{{ $t(item) }}</li>
                </ul>
                <div
                  v-if="hasAiResultText(customerState.summary, 'summary') || hasAiResultText(customerState.suggestion, 'suggestion')"
                  class="ai-result-actions ai-next-action-strip"
                  data-testid="ai-next-action-strip"
                >
                  <button
                    v-if="hasAiResultText(customerState.summary, 'summary')"
                    class="btn btn-compact"
                    data-testid="ai-workbench-summary-copy"
                    @click="copyAiResult(customerState.summary, 'summary', '客户经营摘要')"
                  >{{ $t(" 复制摘要 ") }}</button>
                  <button
                    v-if="hasAiResultText(customerState.suggestion, 'suggestion')"
                    class="btn btn-compact"
                    data-testid="ai-workbench-suggestion-copy"
                    @click="copyAiResult(customerState.suggestion, 'suggestion', '跟进建议')"
                  >{{ $t(" 复制建议 ") }}</button>
                  <button
                    v-if="canEdit && hasAiResultText(customerState.suggestion, 'suggestion')"
                    class="btn btn-compact"
                    data-testid="ai-workbench-suggestion-to-follow-up"
                    @click="bringSuggestionToFollowUp"
                  >{{ $t(" 带入跟进 ") }}</button>
                </div>
              </section>
            </div>
          </template>
        </div>
      </div>
      </section>

      <section class="ai-workbench-pane ai-risk-workbench" data-testid="ai-mode-risk">
        <div class="ai-pane-head">
          <div>
            <div class="section-label">{{ $t("风险对象") }}</div>
            <strong>{{ $t("风险处理方向") }}</strong>
          </div>
          <div class="module-header-actions">
            <InfoHint :text="$t(&quot;选择风险后生成原因、影响和处理方向。&quot;)" />
            <button class="btn btn-compact" data-testid="ai-risk-load-button" :disabled="riskState.loading" @click="fetchRisks">
              {{ $t(riskState.loading ? '读取中...' : '读取风险') }}
            </button>
          </div>
        </div>
      <div class="ai-risk-workspace-grid">
        <div class="ai-picker-panel">
          <div class="ai-panel-heading">
            <strong>{{ $t("对象选择") }}</strong>
            <span class="badge compact warning">{{ $t("风险项") }}</span>
          </div>
          <div class="ai-risk-toolbar">
            <input
              v-model="riskState.keyword"
              class="input"
              :placeholder="$t(&quot;筛选风险标题 / 客户 / 合同 / 房间&quot;)"
            />
            <select v-model="riskState.severity" class="select">
              <option value="all">{{ $t("全部等级") }}</option>
              <option value="high">{{ $t("高风险") }}</option>
              <option value="medium">{{ $t("中风险") }}</option>
              <option value="low">{{ $t("低风险") }}</option>
            </select>
          </div>
          <RiskFlagList
            v-if="riskState.loaded || riskState.loading"
            :risks="filteredRisks"
            :loading="riskState.loading"
            :row-clickable="false"
            selectable
            :selected-key="selectedRiskKey"
            :actions-disabled="riskState.explanationLoading"
            :empty-text="$t(riskState.items.length ? '当前筛选没有匹配风险' : '当前没有可解释风险')"
            explainable
            @open-risk="openRiskTarget"
            @select-risk="selectRisk"
            @explain-risk="explainRisk"
          />
          <div v-else class="ai-ready-panel ai-risk-list-ready">
            <span class="ai-ready-kicker">{{ $t("下一步") }}</span>
            <strong>{{ $t("读取风险") }}</strong>
          </div>
        </div>

        <div class="ai-result-panel" data-testid="ai-risk-result-panel">
          <div v-if="riskState.selected" class="risk-ai-target">
            <div class="ai-panel-heading">
              <strong>{{ $t("经营信号") }}</strong>
              <span>{{ $t("等级 / 对象 / 金额 / 状态") }}</span>
            </div>
            <div class="ai-context-signal-band ai-risk-signal-band">
              <div
                v-for="signal in selectedRiskSignals"
                :key="signal.label"
                class="ai-signal-card"
                :class="`tone-${signal.tone}`"
              >
                <span>{{ $t(signal.label) }}</span>
                <strong>{{ $t(signal.value) }}</strong>
                <small>{{ $t(signal.helper) }}</small>
              </div>
            </div>
            <div class="risk-ai-target-head">
              <span class="badge" :class="riskState.selected.severity === 'high' ? 'error' : 'warning'">{{ $t(riskSeverityLabel(riskState.selected.severity)) }}</span>
              <strong>{{ $t(riskState.selected.title || riskState.selected.risk_type) }}</strong>
            </div>
            <p>{{ $t(riskState.selected.description || '') }}</p>
            <div class="ai-action-grid">
              <button class="btn btn-primary" :disabled="riskState.explanationLoading" @click="explainRisk(riskState.selected)">
                {{ $t(riskState.explanationLoading ? '生成中...' : '生成风险分析') }}
              </button>
              <button class="btn" :disabled="riskState.explanationLoading" @click="openRiskTarget(riskState.selected)">{{ $t("查看目标") }}</button>
            </div>
          </div>
          <p v-if="riskState.error" class="form-error" data-testid="ai-risk-workbench-error">{{ $t(riskState.error) }}</p>
          <div v-if="riskState.explanationLoading" class="ai-panel-skeleton ai-result-loading-skeleton" data-testid="ai-risk-explanation-skeleton" aria-hidden="true">
            <span class="skeleton-line skeleton-line-title"></span>
            <span class="skeleton-line skeleton-line-md"></span>
            <span class="skeleton-line skeleton-line-sm"></span>
            <span class="skeleton-line skeleton-line-md"></span>
          </div>
          <div v-else-if="riskState.explanation" class="ai-result-block ai-review-card" data-testid="ai-risk-workbench-result">
            <section class="ai-review-section" data-testid="ai-result-summary">
              <strong>{{ $t("结论") }}</strong>
              <p>{{ $t(riskExplanationText || '暂未形成有效解释，请稍后重试。') }}</p>
            </section>
            <section v-if="riskEvidenceItems.length" class="ai-review-section" data-testid="ai-result-evidence">
              <strong>{{ $t("依据") }}</strong>
              <div class="ai-chip-row">
                <span v-for="item in riskEvidenceItems" :key="item" class="badge info">{{ $t(item) }}</span>
              </div>
            </section>
            <section
              v-if="riskNextActionItems.length || hasAiResultText(riskState.explanation, 'explanation')"
              class="ai-review-section"
              data-testid="ai-result-next-actions"
            >
              <strong>{{ $t("下一步") }}</strong>
              <ul v-if="riskNextActionItems.length" class="ai-action-list">
                <li v-for="item in riskNextActionItems" :key="item">{{ $t(item) }}</li>
              </ul>
              <div v-if="hasAiResultText(riskState.explanation, 'explanation')" class="ai-result-actions ai-next-action-strip">
                <button
                  class="btn btn-compact"
                  data-testid="ai-workbench-risk-copy"
                  @click="copyAiResult(riskState.explanation, 'explanation', '风险原因、影响和处理方向')"
                >{{ $t(" 复制解释 ") }}</button>
              </div>
            </section>
          </div>
          <div v-else class="ai-ready-panel ai-result-ready">
            <span class="ai-ready-kicker">{{ $t("下一步") }}</span>
            <strong>{{ $t(riskState.loaded ? '选择风险' : '读取风险') }}</strong>
            <div class="ai-ready-steps">
              <span>{{ $t("读风险") }}</span>
              <span>{{ $t("选对象") }}</span>
              <span>{{ $t("看处理") }}</span>
            </div>
          </div>
        </div>
      </div>
      </section>
      </div>
    </article>
  </section>
</template>
