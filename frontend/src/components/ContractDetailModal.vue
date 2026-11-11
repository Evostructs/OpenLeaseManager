<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import ExecutionActions from './ExecutionActions.vue'
import AttachmentPanel from './AttachmentPanel.vue'
import BaseModal from './BaseModal.vue'
import RiskFlagList from './RiskFlagList.vue'
import StatusBadge from './StatusBadge.vue'
import { openDownload } from '../lib/api'
import { formatArea, formatAuditAction, formatAuditOperator, formatCurrency, formatDate } from '../lib/format'

const props = defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  },
  loading: Boolean,
  logs: {
    type: Array,
    default: () => []
  },
  rentPlan: {
    type: Array,
    default: () => []
  },
  rentPreview: {
    type: Array,
    default: () => []
  },
  rentSummary: {
    type: Object,
    default: () => ({})
  },
  decisionCard: {
    type: Object,
    default: null
  },
  decisionLoading: Boolean,
  decisionError: {
    type: String,
    default: ''
  },
  initialSection: {
    type: String,
    default: ''
  },
  showLogs: Boolean,
  detailLink: String,
  closeLabel: {
    type: String,
    default: '关闭'
  },
  showCloseAction: {
    type: Boolean,
    default: false
  },
  layerIndex: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['close', 'refresh', 'refresh-decision-card', 'open-contract', 'open-customer', 'room-action', 'rent-term-action', 'delete-rent-term', 'deposit-action', 'generate-document', 'notify'])

const hasLifecycle = computed(() => Boolean(props.contract?.previous_contract || props.contract?.next_contracts?.length))
const rentPreviewRows = computed(() => (props.rentPreview?.length ? props.rentPreview : props.rentPlan.slice(0, 12)))
const depositSummary = computed(() => props.contract?.deposit_summary || {})
const decisionSignals = computed(() => props.decisionCard?.decision_signals || [])
const decisionChecks = computed(() => props.decisionCard?.suggested_checks || [])
const decisionRisks = computed(() => props.decisionCard?.risks || [])
const decisionRooms = computed(() => props.decisionCard?.rooms || [])
const decisionPrimarySignal = computed(() => (decisionSignals.value.length ? decisionSignalLabel(decisionSignals.value[0]) : '状态平稳'))
const decisionMoneyPressure = computed(() => {
  const receivables = props.decisionCard?.receivables || {}
  const deposits = props.decisionCard?.deposits || {}
  return Number(receivables.unpaid_balance || 0) + Number(deposits.unpaid_amount || 0)
})
const decisionMoneyPressureText = computed(() => {
  const receivables = props.decisionCard?.receivables || {}
  const deposits = props.decisionCard?.deposits || {}
  return `逾期 ${formatCurrency(receivables.overdue_balance || 0)} · 押金未收 ${formatCurrency(deposits.unpaid_amount || 0)}`
})
const decisionSupportItems = computed(() => {
  const card = props.decisionCard || {}
  const deposits = card.deposits || {}
  const latestFollowUp = card.follow_up?.latest
  const nextFollowUp = card.follow_up?.next
  const highRisk = decisionRisks.value.some((risk) => risk.severity === 'high')
  return [
    {
      label: '合同正文',
      value: card.attachments?.has_contract_body ? '已归档' : '缺失',
      helper: card.attachments?.has_contract_body ? '附件已可追溯' : '先补合同正文',
      tone: card.attachments?.has_contract_body ? 'success' : 'warning',
    },
    {
      label: '押金',
      value: deposits.unpaid_amount ? `未收 ${formatCurrency(deposits.unpaid_amount)}` : '已平衡',
      helper: `应收 ${formatCurrency(deposits.expected_amount || 0)} · 已收 ${formatCurrency(deposits.received_amount || 0)}`,
      tone: Number(deposits.unpaid_amount || 0) > 0 ? 'warning' : 'success',
    },
    {
      label: '跟进',
      value: nextFollowUp?.next_follow_up_date ? formatDate(nextFollowUp.next_follow_up_date) : '暂无下次跟进',
      helper: latestFollowUp?.owner ? `最近 ${latestFollowUp.owner}` : '需确认责任人',
      tone: nextFollowUp?.next_follow_up_date ? 'info' : 'warning',
    },
    {
      label: '风险',
      value: decisionRisks.value.length ? `${decisionRisks.value.length} 条` : '暂无',
      helper: highRisk ? '含高风险提示' : '继续例行核对',
      tone: highRisk ? 'error' : (decisionRisks.value.length ? 'warning' : 'success'),
    },
  ]
})
const contractLifecycleStages = computed(() => {
  const status = props.contract?.status || ''
  const disposal = props.contract?.disposal || ''
  const days = props.contract?.days_to_expiry
  const finalDisposals = new Set(['已续租', '已退租', '已变更'])
  let activeKey = 'active'
  if (status === '待生效') activeKey = 'pending'
  else if (finalDisposals.has(disposal) || ['已续租', '已退租', '已变更'].includes(status)) activeKey = 'closed'
  else if (status === '已到期' || status === '逾期占用' || (typeof days === 'number' && days < 0)) activeKey = 'disposal'
  else if (status === '即将到期' || (typeof days === 'number' && days <= 90)) activeKey = 'expiring'

  const order = ['pending', 'active', 'expiring', 'disposal', 'closed']
  const activeIndex = order.indexOf(activeKey)
  const rangeCopy = `${formatDate(props.contract?.start_date)} - ${formatDate(props.contract?.end_date)}`
  return [
    {
      key: 'pending',
      label: '待生效',
      helper: `起租 ${formatDate(props.contract?.start_date)}`,
      tone: 'info'
    },
    {
      key: 'active',
      label: '租赁中',
      helper: rangeCopy,
      tone: 'success'
    },
    {
      key: 'expiring',
      label: '到期预排',
      helper: typeof days === 'number' ? `${Math.max(days, 0)} 天` : '到期日待核',
      tone: 'warning'
    },
    {
      key: 'disposal',
      label: '到期处置',
      helper: status === '逾期占用' ? '逾期占用' : (disposal || '续租/退租'),
      tone: 'warning'
    },
    {
      key: 'closed',
      label: '已结束',
      helper: disposal || status || '归档',
      tone: 'info'
    }
  ].map((stage, index) => ({
    ...stage,
    active: stage.key === activeKey,
    done: index < activeIndex,
    muted: index > activeIndex
  }))
})
const contractLifecyclePrimaryAction = computed(() => {
  const signals = decisionSignals.value
  if (signals.includes('collection_blocker')) {
    return { title: '核对历史逾期', helper: '查看本合同应收与收款记录', action: '查看应收', target: 'receivables', tone: 'error' }
  }
  if (signals.includes('attachment_gap')) {
    return { title: '补齐合同正文', helper: '先把合同附件补完整', action: '查看附件', target: 'attachments', tone: 'warning' }
  }
  if (signals.includes('deposit_gap')) {
    return { title: '处理押金缺口', helper: '核对应收、已收和未收押金', action: '看押金', target: 'deposits', tone: 'warning' }
  }
  if (signals.includes('expiring_soon')) {
    return { title: '确认到期安排', helper: '明确续租、退租或招商排期', action: '看房源', target: 'rooms', tone: 'warning' }
  }
  return { title: '保持合同跟进', helper: '当前无高优先级动作', action: '查看客户', target: 'customer', tone: 'success' }
})
const documentMonth = ref(currentMonthValue())
const receivablesSection = ref(null)
const attachmentsSection = ref(null)
const depositsSection = ref(null)
const roomsSection = ref(null)

watch(
  () => [props.open, props.loading, props.initialSection, props.contract?.id],
  async ([open, loading, section]) => {
    if (!open || loading || section !== 'receivables') return
    await nextTick()
    receivablesSection.value?.scrollIntoView({ block: 'start', inline: 'nearest' })
  },
  { immediate: true }
)

function currentMonthValue() {
  const today = new Date()
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
}

function openLifecycleContract(contract) {
  if (contract?.id) emit('open-contract', contract)
}

function openRisk(risk) {
  const target = risk?.action_target || {}
  const contractId = target.contract_id || risk?.contract_id || props.contract?.id
  if ((target.type === 'receivable' || risk?.entity_type === 'receivable') && contractId) {
    emit('open-contract', { id: contractId, initialSection: 'receivables' })
    return
  }
  if (target.type === 'customer' && target.customer_id) {
    emit('open-customer', target.customer_id)
    return
  }
  if (contractId && contractId !== props.contract?.id) {
    emit('open-contract', { id: contractId })
  }
}

async function runContractLifecycleAction(action) {
  if (action?.target === 'receivables' && props.contract?.id) {
    emit('open-contract', { id: props.contract.id, initialSection: 'receivables' })
    return
  }
  if (action?.target === 'customer' && props.contract?.customer_id) {
    emit('open-customer', props.contract.customer_id)
    return
  }
  const target = {
    attachments: attachmentsSection,
    deposits: depositsSection,
    rooms: roomsSection
  }[action?.target]
  if (target?.value) {
    await nextTick()
    target.value.scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'nearest' })
  }
}

function rentRuleLabel(ruleType) {
  return {
    area_unit_price: '面积单价',
    fixed_monthly: '固定月租',
    stepped_rent: '阶梯租金',
    escalation: '递增租金',
    rent_free: '自然日免租',
    temporary_adjustment: '临时减免'
  }[ruleType] || ruleType || '--'
}

function rentSourceLabel(source) {
  return source === 'rent_terms' ? '租金条款' : '合同基础租金'
}

function rentTermScopeLevel(term) {
  return term?.contract_room_id || term?.room_no ? '房源级' : '合同级'
}

function rentTermScope(term) {
  if (!term) return '--'
  if (term.contract_room_id) {
    const room = (props.contract?.room_items || []).find((item) => Number(item.id) === Number(term.contract_room_id))
    return room?.room_no || term.room_no || '--'
  }
  return term.room_no || '合同整体'
}

function rentTermAmount(term) {
  if (!term) return '--'
  if (term.rule_type === 'area_unit_price') {
    return `${formatArea(term.area)} ㎡ × ${formatCurrency(term.unit_price)} / 日`
  }
  if (term.rule_type === 'fixed_monthly') {
    return `${formatCurrency(term.monthly_amount)} / 月`
  }
  if (term.rule_type === 'stepped_rent') {
    if (term.monthly_amount != null) return `${formatCurrency(term.monthly_amount)} / 月`
    return `${formatArea(term.area)} ㎡ × ${formatCurrency(term.unit_price)} / 日`
  }
  if (term.rule_type === 'escalation') {
    const frequency = term.frequency === 'annual' ? '年度' : '指定日期'
    const amount = term.adjustment_type === 'fixed_amount' ? `${formatCurrency(term.adjustment_value)} / 月` : `${term.adjustment_value || 0}%`
    return `${frequency} · ${amount}`
  }
  if (term.rule_type === 'rent_free') {
    return '按自然日抵减'
  }
  if (term.rule_type === 'temporary_adjustment') {
    const amount = term.adjustment_type === 'percent' ? `${term.adjustment_value || 0}%` : `${formatCurrency(term.adjustment_value)} / 月`
    return `临时抵减 ${amount}`
  }
  return '--'
}

function rentTermAdjustment(term) {
  if (!term) return '--'
  if (['area_unit_price', 'fixed_monthly', 'stepped_rent'].includes(term.rule_type)) return '基础租金'
  if (term.rule_type === 'rent_free') return '自然日免租'
  if (term.rule_type === 'escalation') {
    const mode = term.adjustment_type === 'fixed_amount' ? '固定金额递增' : '百分比递增'
    const frequency = term.frequency === 'annual' ? '年度' : '指定日期'
    return `${frequency} · ${mode}`
  }
  if (term.rule_type === 'temporary_adjustment') {
    return term.adjustment_type === 'percent' ? '百分比抵减' : '固定金额抵减'
  }
  return '--'
}

function temporaryAdjustmentNeedsAttention(term) {
  if (term?.rule_type !== 'temporary_adjustment') return false
  const value = Number(term.adjustment_value || 0)
  if (!value) return false
  if (term.adjustment_type === 'percent') return value >= 50
  const room = (props.contract?.room_items || []).find((item) => Number(item.id) === Number(term.contract_room_id))
  const base = Number(room?.monthly_rent || props.contract?.monthly_rent || 0)
  return base > 0 ? value >= base * 0.5 : value >= 50000
}

function effectAmountText(effect) {
  if (!effect || effect.amount === null || effect.amount === undefined) return ''
  const amount = Number(effect.amount || 0)
  const prefix = amount > 0 ? '+' : ''
  return `${prefix}${formatCurrency(amount)}`
}

function effectText(effect) {
  if (!effect) return ''
  const amount = effectAmountText(effect)
  const description = effect.description ? ` · ${effect.description}` : ''
  return `${effect.label || ''}${description}${amount ? ` ${amount}` : ''}`
}

function rowEffects(row) {
  return row?.term_effects?.length ? row.term_effects : []
}

function depositStatusLabel(status) {
  return {
    unpaid: '未收',
    partial: '部分收取',
    paid: '已收齐',
    refunded: '已退还',
    offset: '已冲抵',
    closed: '已关闭'
  }[status] || status || '--'
}

function depositStatusClass(status) {
  if (status === 'paid') return 'success'
  if (['partial', 'offset'].includes(status)) return 'warning'
  if (['refunded', 'closed'].includes(status)) return 'info'
  return 'error'
}

function decisionSignalLabel(signal) {
  return {
    collection_blocker: '历史逾期',
    attachment_gap: '附件缺口',
    deposit_gap: '押金缺口',
    high_risk: '高风险',
    expiring_soon: '90天内到期',
    good_standing: '状态平稳'
  }[signal] || '经营信号'
}

function decisionSignalClass(signal) {
  if (['collection_blocker', 'high_risk'].includes(signal)) return 'error'
  if (['attachment_gap', 'deposit_gap', 'expiring_soon'].includes(signal)) return 'warning'
  if (signal === 'good_standing') return 'success'
  return 'info'
}

function riskSeverityClass(severity) {
  if (severity === 'high') return 'error'
  if (severity === 'medium') return 'warning'
  return 'info'
}

function riskSeverityLabel(severity) {
  return {
    high: '高',
    medium: '中',
    low: '低'
  }[severity] || '待评估'
}

function riskTypeLabel(type) {
  return {
    overdue: '逾期风险',
    contract: '合同风险',
    customer: '客户风险',
    deposit: '押金风险',
    attachment: '附件风险'
  }[type] || '经营风险'
}

function decisionCardTitle(card) {
  const days = card?.contract?.days_to_expiry
  if (typeof days === 'number' && days >= 0 && days <= 90) return '续租/退租决策卡'
  return '经营决策卡'
}

function expiryHint(card) {
  const days = card?.contract?.days_to_expiry
  if (typeof days !== 'number') return '未标注到期日'
  if (days < 0) return `已到期 ${Math.abs(days)} 天`
  if (days <= 90) return `距离到期 ${days} 天`
  return `距离到期 ${days} 天，暂非临期`
}

function exportContractDeposits() {
  if (!props.contract?.id) return
  openDownload(`/api/contracts/deposits/export?contract_id=${props.contract.id}`)
}
</script>

<template>
  <BaseModal :open="open" :title="$t(&quot;合同详情&quot;)" size="wide" :layer-index="layerIndex" @close="emit('close')">
    <div v-if="loading" class="compact-empty">{{ $t("正在读取合同详情...") }}</div>
    <div v-else-if="contract" class="split-grid contract-detail-grid">
      <section class="card" style="padding:0;border:none;box-shadow:none">
        <ExecutionActions :contract-id="contract.id" :contract-end="contract.end_date" compact @open-contract="emit('open-contract', $event)" />
        <div class="detail-list">
          <div>
            <div class="section-label">{{ $t("合同编号") }}</div>
            <h3 class="card-title" style="margin-top:6px">{{ contract.contract_no }}</h3>
            <a v-if="detailLink" class="btn btn-compact" :href="detailLink" target="_blank" rel="noopener">{{ $t("打开此详情链接") }}</a>
          </div>
          <div class="stat-grid">
            <div>
              <div class="section-label">{{ $t("状态") }}</div>
              <StatusBadge :status="contract.status" />
            </div>
            <div>
              <div class="section-label">{{ $t("客户") }}</div>
              <div>{{ contract.tenant_name || '--' }}</div>
              <button
                v-if="contract.customer_id"
                type="button"
                class="btn btn-compact"
                style="margin-top:8px"
                @click="emit('open-customer', contract.customer_id)"
              >{{ $t(" 查看客户资料 ") }}</button>
              <div v-if="contract.customer?.short_name || contract.customer?.credit_code" class="table-cell-subtext">
                {{ contract.customer.short_name || '无简称' }} · {{ contract.customer.credit_code || '无信用代码' }}
              </div>
            </div>
            <div>
              <div class="section-label">{{ $t("客户方对接人") }}</div>
              <div>{{ contract.contact_person || '--' }}</div>
            </div>
            <div>
              <div class="section-label">{{ $t("我方对接人") }}</div>
              <div>{{ $t(contract.internal_contact_person || '--') }}</div>
            </div>
            <div>
              <div class="section-label">{{ $t("起止日期") }}</div>
              <div>{{ $t(formatDate(contract.start_date)) }} - {{ $t(formatDate(contract.end_date)) }}</div>
            </div>
            <div>
              <div class="section-label">{{ $t("月租金") }}</div>
              <div>{{ $t(formatCurrency(contract.monthly_rent)) }}</div>
            </div>
            <div>
              <div class="section-label">{{ $t("净租金") }}</div>
              <div>{{ $t(formatCurrency(contract.net_rent)) }}</div>
            </div>
            <div>
              <div class="section-label">{{ $t("物业费/月") }}</div>
              <div>{{ $t(formatCurrency(contract.monthly_property_fee)) }}</div>
            </div>
          </div>
          <div>
            <div class="section-label">{{ $t("备注") }}</div>
            <p>{{ contract.notes || '暂无备注' }}</p>
          </div>
          <section class="contract-detail-lifecycle-strip" data-testid="contract-detail-lifecycle-strip">
            <div class="contract-detail-lifecycle-head">
              <div>
                <div class="section-label">{{ $t("合同生命周期") }}</div>
                <h3>{{ $t(contractLifecyclePrimaryAction.title) }}</h3>
                <small>{{ $t(contractLifecyclePrimaryAction.helper) }}</small>
              </div>
              <button
                type="button"
                class="btn btn-compact"
                :class="contractLifecyclePrimaryAction.tone === 'error' ? 'btn-danger' : ''"
                data-testid="contract-detail-lifecycle-action"
                @click="runContractLifecycleAction(contractLifecyclePrimaryAction)"
              >
                {{ $t(contractLifecyclePrimaryAction.action) }}
              </button>
            </div>
            <div class="contract-detail-stage-row">
              <span
                v-for="stage in contractLifecycleStages"
                :key="stage.key"
                class="contract-detail-stage"
                :class="[stage.tone, { active: stage.active, done: stage.done, muted: stage.muted }]"
              >
                <b>{{ $t(stage.label) }}</b>
                <small>{{ $t(stage.helper) }}</small>
              </span>
            </div>
          </section>
          <div v-if="(contract.risks || []).length">
            <div class="section-label">{{ $t("相关风险") }}</div>
            <RiskFlagList :risks="contract.risks || []" compact @open-risk="openRisk" />
          </div>
          <section class="decision-card" data-testid="contract-decision-card">
            <div class="section-title-row">
              <div>
                <div class="section-label">{{ $t(decisionCardTitle(decisionCard)) }}</div>
                <h3 class="decision-card-heading">{{ $t(expiryHint(decisionCard)) }}</h3>
              </div>
              <button type="button" class="btn btn-compact" @click="emit('refresh-decision-card')">{{ $t("刷新") }}</button>
            </div>
            <div v-if="decisionLoading" class="compact-empty slim">{{ $t("正在整理决策信息...") }}</div>
            <div v-else-if="decisionError" class="compact-empty slim">{{ $t(decisionError) }}</div>
            <div v-else-if="decisionCard" class="decision-card-body">
              <div class="decision-brief-grid">
                <div class="decision-brief-item" :class="decisionSignalClass(decisionSignals[0])">
                  <span>{{ $t("主信号") }}</span>
                  <strong>{{ $t(decisionPrimarySignal) }}</strong>
                  <small>{{ $t(expiryHint(decisionCard)) }}</small>
                </div>
                <div class="decision-brief-item" :class="decisionMoneyPressure > 0 ? 'warning' : 'success'">
                  <span>{{ $t("资金压力") }}</span>
                  <strong>{{ $t(formatCurrency(decisionMoneyPressure)) }}</strong>
                  <small>{{ $t(decisionMoneyPressureText) }}</small>
                </div>
              </div>
              <div class="decision-signal-row">
                <span
                  v-for="signal in decisionSignals"
                  :key="signal"
                  class="badge compact"
                  :class="decisionSignalClass(signal)"
                >
                  {{ $t(decisionSignalLabel(signal)) }}
                </span>
              </div>
              <div class="decision-support-grid">
                <div v-for="item in decisionSupportItems" :key="item.label" class="decision-support-item" :class="item.tone">
                  <span>{{ $t(item.label) }}</span>
                  <strong>{{ $t(item.value) }}</strong>
                  <small>{{ $t(item.helper) }}</small>
                </div>
              </div>
              <div class="decision-metric-grid">
                <div>
                  <span>{{ $t("本月应收") }}</span>
                  <strong>{{ $t(formatCurrency(decisionCard.receivables?.receivable || 0)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("本月已收") }}</span>
                  <strong>{{ $t(formatCurrency(decisionCard.receivables?.received || 0)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("本月未收") }}</span>
                  <strong>{{ $t(formatCurrency(decisionCard.receivables?.unpaid_balance || 0)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("历史逾期") }}</span>
                  <strong>{{ $t(formatCurrency(decisionCard.receivables?.overdue_balance || 0)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("押金未收") }}</span>
                  <strong>{{ $t(formatCurrency(decisionCard.deposits?.unpaid_amount || 0)) }}</strong>
                </div>
                <div>
                  <span>{{ $t("回款率") }}</span>
                  <strong>{{ $t(decisionCard.receivables?.collection_rate || 0) }}%</strong>
                </div>
              </div>
              <div class="decision-subgrid">
                <div>
                  <div class="section-label">{{ $t("优先检查") }}</div>
                  <ul class="decision-list">
                    <li v-for="check in decisionChecks" :key="check">{{ $t(check) }}</li>
                    <li v-if="!decisionChecks.length">{{ $t("暂无确定性检查项") }}</li>
                  </ul>
                </div>
                <div>
                  <div class="section-label">{{ $t("房源 / 租金") }}</div>
                  <div class="decision-room-list">
                    <div v-for="room in decisionRooms" :key="`${room.room_no}-${room.tower}`">
                      <strong>{{ room.room_no || '--' }}</strong>
                      <span>{{ $t(room.tower || '--') }} · {{ $t(formatArea(room.area || 0)) }} {{ $t("㎡") }} · {{ $t(formatCurrency(room.monthly_rent || 0)) }}{{ $t("/月") }}</span>
                    </div>
                    <div v-if="!decisionRooms.length" class="helper-text">{{ $t("暂无房源明细") }}</div>
                  </div>
                </div>
              </div>
              <div class="decision-subgrid">
                <div>
                  <div class="section-label">{{ $t("附件 / 押金") }}</div>
                  <div class="decision-line">
                    <span>{{ $t("合同正文") }}</span>
                    <span class="badge compact" :class="decisionCard.attachments?.has_contract_body ? 'success' : 'warning'">
                      {{ $t(decisionCard.attachments?.has_contract_body ? '已归档' : '缺失') }}
                    </span>
                  </div>
                  <div class="decision-line">
                    <span>{{ $t("应收 / 已收 / 冲抵 / 退还") }}</span>
                    <span>{{ $t(formatCurrency(decisionCard.deposits?.expected_amount || 0)) }} / {{ $t(formatCurrency(decisionCard.deposits?.received_amount || 0)) }} / {{ $t(formatCurrency(decisionCard.deposits?.offset_amount || 0)) }} / {{ $t(formatCurrency(decisionCard.deposits?.refunded_amount || 0)) }}</span>
                  </div>
                </div>
                <div>
                  <div class="section-label">{{ $t("跟进") }}</div>
                  <div class="decision-line">
                    <span>{{ $t("最近") }}</span>
                    <span>{{ $t(formatDate(decisionCard.follow_up?.latest?.occurred_at)) }} · {{ $t(decisionCard.follow_up?.latest?.owner || '--') }}</span>
                  </div>
                  <div class="decision-line">
                    <span>{{ $t("下一次") }}</span>
                    <span>{{ $t(formatDate(decisionCard.follow_up?.next?.next_follow_up_date)) }} · {{ $t(decisionCard.follow_up?.next?.next_action || '--') }}</span>
                  </div>
                </div>
              </div>
              <div>
                <div class="section-label">{{ $t("风险") }}</div>
                <div class="decision-risk-list">
                  <div v-for="risk in decisionRisks.slice(0, 4)" :key="`${risk.type}-${risk.title}`">
                    <span class="badge compact" :class="riskSeverityClass(risk.severity)">{{ $t(riskSeverityLabel(risk.severity)) }}</span>
                    <strong>{{ $t(risk.title || riskTypeLabel(risk.type)) }}</strong>
                  </div>
                  <div v-if="!decisionRisks.length" class="helper-text">{{ $t("暂无相关风险") }}</div>
                </div>
              </div>
            </div>
          </section>
          <div ref="attachmentsSection">
          <AttachmentPanel
            entity-type="contract"
            :entity-id="contract.id"
            :attachments="contract.attachments || []"
            :summary="contract.attachment_summary || {}"
            :can-edit="showLogs"
            :can-delete="showLogs"
            required-label="合同正文"
            @refresh="emit('refresh')"
            @notify="emit('notify', $event)"
          />
          </div>
          <div ref="depositsSection">
            <div class="section-title-row">
              <div class="section-label">{{ $t("押金台账") }}</div>
              <div class="btn-row">
                <button type="button" class="btn btn-compact" @click="exportContractDeposits">{{ $t("导出押金") }}</button>
                <button v-if="showLogs" type="button" class="btn btn-compact" @click="openDownload('/api/contracts/deposits/import/template')">{{ $t("押金模板") }}</button>
                <button v-if="showLogs" type="button" class="btn btn-compact" @click="emit('deposit-action', { mode: 'create', contract })">{{ $t("登记应收") }}</button>
              </div>
            </div>
            <div class="stat-grid compact-stats" style="margin-top:10px">
              <div>
                <div class="section-label">{{ $t("应收押金") }}</div>
                <div>{{ $t(formatCurrency(depositSummary.expected_amount || 0)) }}</div>
              </div>
              <div>
                <div class="section-label">{{ $t("已收") }}</div>
                <div>{{ $t(formatCurrency(depositSummary.received_amount || 0)) }}</div>
              </div>
              <div>
                <div class="section-label">{{ $t("未收") }}</div>
                <div>{{ $t(formatCurrency(depositSummary.unpaid_amount || 0)) }}</div>
              </div>
              <div>
                <div class="section-label">{{ $t("已退 / 已冲抵") }}</div>
                <div>{{ $t(formatCurrency(depositSummary.refunded_amount || 0)) }} / {{ $t(formatCurrency(depositSummary.offset_amount || 0)) }}</div>
              </div>
              <div>
                <div class="section-label">{{ $t("可用押金") }}</div>
                <div>{{ $t(formatCurrency(depositSummary.available_amount || 0)) }}</div>
              </div>
            </div>
            <div class="table-wrapper" style="margin-top:12px">
              <table class="data-table" style="min-width:980px">
                <thead>
                  <tr>
                    <th>{{ $t("类型") }}</th>
                    <th>{{ $t("状态") }}</th>
                    <th class="right">{{ $t("应收") }}</th>
                    <th class="right">{{ $t("已收") }}</th>
                    <th class="right">{{ $t("未收") }}</th>
                    <th class="right">{{ $t("已退") }}</th>
                    <th class="right">{{ $t("已冲抵") }}</th>
                    <th class="right">{{ $t("可用") }}</th>
                    <th>{{ $t("日期") }}</th>
                    <th>{{ $t("备注") }}</th>
                    <th v-if="showLogs">{{ $t("操作") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="deposit in contract.deposits || []" :key="deposit.id">
                    <td>{{ $t(deposit.deposit_type || '--') }}</td>
                    <td><span class="badge" :class="depositStatusClass(deposit.status)">{{ $t(depositStatusLabel(deposit.status)) }}</span></td>
                    <td class="right">{{ $t(formatCurrency(deposit.expected_amount || 0)) }}</td>
                    <td class="right">{{ $t(formatCurrency(deposit.received_amount || 0)) }}</td>
                    <td class="right">{{ $t(formatCurrency(deposit.unpaid_amount || 0)) }}</td>
                    <td class="right">{{ $t(formatCurrency(deposit.refunded_amount || 0)) }}</td>
                    <td class="right">{{ $t(formatCurrency(deposit.offset_amount || 0)) }}</td>
                    <td class="right">{{ $t(formatCurrency(deposit.available_amount || 0)) }}</td>
                    <td>
                      <div>{{ $t("应收 ") }}{{ $t(formatDate(deposit.due_date)) }}</div>
                      <div class="table-cell-subtext">{{ $t("收 ") }}{{ $t(formatDate(deposit.received_date)) }}{{ $t(" / 退 ") }}{{ $t(formatDate(deposit.refund_date)) }}</div>
                    </td>
                    <td>{{ deposit.notes || '--' }}</td>
                    <td v-if="showLogs" class="actions-cell">
                      <button type="button" class="icon-btn" :title="$t(&quot;编辑押金&quot;)" @click="emit('deposit-action', { mode: 'edit', contract, deposit })">{{ $t("编辑") }}</button>
                      <button type="button" class="icon-btn success" :title="$t(&quot;登记收取&quot;)" @click="emit('deposit-action', { mode: 'receive', contract, deposit })">{{ $t("收取") }}</button>
                      <button type="button" class="icon-btn warning" :title="$t(&quot;登记退还&quot;)" @click="emit('deposit-action', { mode: 'refund', contract, deposit })">{{ $t("退还") }}</button>
                      <button type="button" class="icon-btn lifecycle" :title="$t(&quot;登记冲抵&quot;)" @click="emit('deposit-action', { mode: 'offset', contract, deposit })">{{ $t("冲抵") }}</button>
                    </td>
                  </tr>
                  <tr v-if="!(contract.deposits || []).length">
                    <td :colspan="showLogs ? 11 : 10" class="list-empty">{{ $t("暂无押金台账") }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-if="showLogs" class="document-action-row">
            <div>
              <div class="section-label">{{ $t("通知账期") }}</div>
              <input v-model="documentMonth" type="month" class="input" />
            </div>
            <button
              type="button"
              class="btn btn-compact"
              @click="emit('generate-document', { document_type: 'payment_notice', contract, month: documentMonth })"
            >{{ $t(" 缴款通知 ") }}</button>
            <button
              type="button"
              class="btn btn-compact"
              @click="emit('generate-document', { document_type: 'dunning_notice', contract, month: documentMonth })"
            >{{ $t(" 催收通知 ") }}</button>
          </div>
          <div v-if="hasLifecycle">
            <div class="section-label">{{ $t("生命周期") }}</div>
            <div class="contract-lifecycle">
              <button
                v-if="contract.previous_contract"
                type="button"
                class="lifecycle-link"
                @click="openLifecycleContract(contract.previous_contract)"
              >
                <span class="lifecycle-label">{{ $t("上一份合同") }}</span>
                <strong>{{ contract.previous_contract.contract_no }}</strong>
                <span>{{ contract.previous_contract.tenant_name || '--' }}</span>
                <span>{{ $t(contract.previous_contract.lease_type || '--') }} · {{ $t(formatDate(contract.previous_contract.start_date)) }} - {{ $t(formatDate(contract.previous_contract.end_date)) }}</span>
                <span>{{ $t(contract.previous_contract.status || '--') }}{{ $t(contract.previous_contract.disposal ? ` / ${contract.previous_contract.disposal}` : '') }}</span>
              </button>
              <div v-if="contract.next_contracts?.length" class="lifecycle-group">
                <div class="lifecycle-label">{{ $t("后续合同") }}</div>
                <button
                  v-for="item in contract.next_contracts"
                  :key="item.id"
                  type="button"
                  class="lifecycle-link"
                  @click="openLifecycleContract(item)"
                >
                  <strong>{{ item.contract_no }}</strong>
                  <span>{{ item.tenant_name || '--' }}</span>
                  <span>{{ $t(item.lease_type || '--') }} · {{ $t(formatDate(item.start_date)) }} - {{ $t(formatDate(item.end_date)) }}</span>
                  <span>{{ $t(item.status || '--') }}{{ $t(item.disposal ? ` / ${item.disposal}` : '') }}</span>
                </button>
              </div>
            </div>
          </div>
          <div ref="roomsSection">
            <div class="section-label">{{ $t("房源明细") }}</div>
            <button v-if="showLogs" type="button" class="btn btn-compact" style="margin-top:8px" @click="emit('room-action', { mode: 'expand', contract })">{{ $t("扩租") }}</button>
            <div class="table-wrapper" style="margin-top:12px">
              <table class="data-table" style="min-width:860px">
                <thead>
                  <tr>
                    <th>{{ $t("楼塔") }}</th>
                    <th>{{ $t("室号") }}</th>
                    <th>{{ $t("有效期") }}</th>
                    <th class="right">{{ $t("面积") }}</th>
                    <th class="right">{{ $t("日租单价") }}</th>
                    <th class="right">{{ $t("月租金") }}</th>
                    <th v-if="showLogs">{{ $t("操作") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in contract.room_items || []" :key="`${item.room_no}-${item.id || ''}`">
                    <td>{{ $t(item.tower || '--') }}</td>
                    <td>{{ item.room_no || '--' }}</td>
                    <td>{{ $t(formatDate(item.start_date)) }} - {{ $t(formatDate(item.end_date)) }}</td>
                    <td class="right">{{ $t(formatArea(item.area)) }} {{ $t("㎡") }}</td>
                    <td class="right">{{ $t(formatCurrency(item.unit_price)) }}</td>
                    <td class="right">{{ $t(formatCurrency(item.monthly_rent)) }}</td>
                    <td v-if="showLogs" class="actions-cell">
                      <button v-if="item.id" type="button" class="icon-btn warning" :title="$t(&quot;减租&quot;)" @click="emit('room-action', { mode: 'reduce', contract, roomItem: item })">{{ $t("减租") }}</button>
                      <button v-if="item.id" type="button" class="icon-btn lifecycle" :title="$t(&quot;换房&quot;)" @click="emit('room-action', { mode: 'swap', contract, roomItem: item })">{{ $t("换房") }}</button>
                      <span v-if="!item.id" class="helper-text">--</span>
                    </td>
                  </tr>
                  <tr v-if="!(contract.room_items || []).length">
                    <td :colspan="showLogs ? 7 : 6" class="list-empty">{{ $t("暂无房源明细") }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <div class="section-label">{{ $t("租金条款") }}</div>
            <button v-if="showLogs" type="button" class="btn btn-compact" style="margin-top:8px" @click="emit('rent-term-action', { contract })">{{ $t("新增条款") }}</button>
            <div class="table-wrapper" style="margin-top:12px">
              <table class="data-table" style="min-width:860px">
                <thead>
                  <tr>
                    <th>{{ $t("类型") }}</th>
                    <th>{{ $t("作用范围") }}</th>
                    <th>{{ $t("有效期") }}</th>
                    <th>{{ $t("金额参数") }}</th>
                    <th>{{ $t("调整方式") }}</th>
                    <th>{{ $t("备注") }}</th>
                    <th v-if="showLogs">{{ $t("操作") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="term in contract.rent_terms || []" :key="term.id">
                    <td>{{ $t(rentRuleLabel(term.rule_type)) }}</td>
                    <td>
                      <span class="badge info compact">{{ $t(rentTermScopeLevel(term)) }}</span>
                      <div>{{ $t(rentTermScope(term)) }}</div>
                    </td>
                    <td>{{ $t(formatDate(term.start_date)) }} - {{ $t(formatDate(term.end_date)) }}</td>
                    <td>
                      {{ $t(rentTermAmount(term)) }}
                      <span v-if="temporaryAdjustmentNeedsAttention(term)" class="badge warning">{{ $t("需关注") }}</span>
                    </td>
                    <td>{{ $t(rentTermAdjustment(term)) }}</td>
                    <td>{{ term.notes || '--' }}</td>
                    <td v-if="showLogs" class="actions-cell">
                      <button type="button" class="icon-btn" :title="$t(&quot;编辑条款&quot;)" @click="emit('rent-term-action', { contract, term })">{{ $t("编辑") }}</button>
                      <button type="button" class="icon-btn danger" :title="$t(&quot;删除条款&quot;)" :data-testid="`rent-term-delete-action-${term.id}`" @click="emit('delete-rent-term', term)">{{ $t("删除") }}</button>
                    </td>
                  </tr>
                  <tr v-if="!(contract.rent_terms || []).length">
                    <td :colspan="showLogs ? 7 : 6" class="list-empty">{{ $t("暂无租金条款，继续使用合同基础租金") }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <section
        ref="receivablesSection"
        class="card contract-receivables-context"
        :class="{ focused: initialSection === 'receivables' }"
        data-testid="contract-receivables-context"
        style="padding:0;border:none;box-shadow:none"
      >
        <div class="section-label">{{ $t("应收计划预览") }}</div>
        <p class="helper-text">{{ $t("预览 ") }}{{ $t(rentSummary.preview_months ?? rentPreviewRows.length) }}{{ $t(" 个月，总月数 ") }}{{ $t(rentSummary.total_months || 0) }}{{ $t("，总租金 ") }}{{ $t(formatCurrency(rentSummary.total_rent || 0)) }}{{ $t("，来源 ") }}{{ $t(rentSourceLabel(rentSummary.rent_source)) }}</p>
        <div class="table-wrapper" style="margin-top:16px">
          <table class="data-table" style="min-width:1080px">
            <thead>
              <tr>
                <th>{{ $t("月份") }}</th>
                <th>{{ $t("计租天数") }}</th>
                <th>{{ $t("计算来源") }}</th>
                <th class="right">{{ $t("毛租金") }}</th>
                <th class="right">{{ $t("免租") }}</th>
                <th class="right">{{ $t("临时减免") }}</th>
                <th>{{ $t("租金") }}</th>
                <th>{{ $t("物业费") }}</th>
                <th>{{ $t("合计") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in rentPreviewRows" :key="item.month">
                <td>{{ $t(item.month) }}</td>
                <td>{{ $t(item.lease_days) }}/{{ $t(item.month_days) }}</td>
                <td>
                  <div>{{ $t(item.term_summary || rentSourceLabel(item.rent_source)) }}</div>
                  <div v-if="rowEffects(item).length" class="rent-effect-list">
                    <span
                      v-for="effect in rowEffects(item)"
                      :key="`${item.month}-${effect.type}-${effect.label}`"
                      class="rent-effect-chip"
                      :class="{ warning: effect.needs_attention }"
                    >
                      {{ $t(effectText(effect)) }}
                    </span>
                  </div>
                </td>
                <td class="right">{{ $t(formatCurrency(item.gross_rent ?? item.rent)) }}</td>
                <td class="right">{{ $t(item.rent_free_amount ? `-${formatCurrency(item.rent_free_amount)}` : '--') }}</td>
                <td class="right">{{ $t(item.temporary_adjustment_amount ? `-${formatCurrency(item.temporary_adjustment_amount)}` : '--') }}</td>
                <td>{{ $t(formatCurrency(item.rent)) }}</td>
                <td>{{ $t(formatCurrency(item.property_fee)) }}</td>
                <td>{{ $t(formatCurrency(item.total)) }}</td>
              </tr>
              <tr v-if="!rentPreviewRows.length">
                <td colspan="9" class="list-empty">{{ $t("暂无未来应收预览") }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
    <div v-else class="compact-empty">{{ $t("暂无合同详情") }}</div>

    <section v-if="contract && showLogs" class="card" style="margin-top:16px">
      <div class="section-label">{{ $t("操作记录") }}</div>
      <div class="table-wrapper" style="margin-top:16px">
        <table class="data-table" style="min-width:680px">
          <thead>
            <tr>
              <th>{{ $t("时间") }}</th>
              <th>{{ $t("操作人") }}</th>
              <th>{{ $t("动作") }}</th>
              <th>{{ $t("内容") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id">
              <td>{{ $t(formatDate(log.timestamp)) }}</td>
              <td>{{ log.operator_label || formatAuditOperator(log.operator) }}</td>
              <td>{{ $t(log.action_label || formatAuditAction(log.action)) }}</td>
              <td>{{ $t(log.changes) }}</td>
            </tr>
            <tr v-if="!logs.length">
              <td colspan="4" class="list-empty">{{ $t("暂无操作记录") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <template v-if="showCloseAction" #footer>
      <button class="btn" type="button" @click="emit('close')">{{ $t(closeLabel) }}</button>
    </template>
  </BaseModal>
</template>
