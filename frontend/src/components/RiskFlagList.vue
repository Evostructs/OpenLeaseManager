<script setup>
import { formatCurrency, formatDate } from '../lib/format'

const props = defineProps({
  risks: {
    type: Array,
    default: () => []
  },
  loading: Boolean,
  emptyText: {
    type: String,
    default: '暂无风险'
  },
  compact: Boolean,
  explainable: Boolean,
  selectable: Boolean,
  selectedKey: {
    type: [String, Number],
    default: ''
  },
  rowClickable: {
    type: Boolean,
    default: true
  },
  actionsDisabled: Boolean
})

const emit = defineEmits(['open-risk', 'explain-risk', 'select-risk'])

const typeLabels = {
  overdue_receivable: '历史逾期',
  contract_expiring: '90天内到期',
  follow_up_overdue: '跟进逾期',
  deposit_unpaid: '押金缺口',
  attachment_missing: '附件缺失',
  large_adjustment: '大额减免'
}

const impactLabels = {
  overdue_receivable: '历史回款',
  deposit_unpaid: '押金待补',
  contract_expiring: '影响续租',
  follow_up_overdue: '影响跟进',
  attachment_missing: '影响资料',
  large_adjustment: '需要复核'
}

const statusLabels = {
  open: '未处理',
  pending: '跟进中',
  resolved: '已处理',
  done: '已完成',
  closed: '已关闭'
}

function severityClass(severity) {
  if (severity === 'high') return 'error'
  if (severity === 'medium') return 'warning'
  return 'info'
}

function severityLabel(severity) {
  return { high: '高', medium: '中', low: '低' }[severity] || severity || '--'
}

function typeLabel(type) {
  return typeLabels[type] || type || '--'
}

function impactLabel(type) {
  return impactLabels[type] || '需要跟进'
}

function statusLabel(status) {
  return statusLabels[status] || status || '未处理'
}

function statusClass(status) {
  if (['resolved', 'done', 'closed'].includes(status)) return 'success'
  if (status === 'open' || status === 'pending' || !status) return 'warning'
  return 'info'
}

function rowClasses(risk) {
  return [
    `severity-${risk.severity || 'low'}`,
    {
      'not-clickable': !props.rowClickable && !props.selectable,
      selected: props.selectedKey && riskKey(risk) === props.selectedKey,
      'is-blocking': risk.severity === 'high',
      'is-overdue': isOverdueRisk(risk)
    }
  ]
}

function riskMeta(risk) {
  const parts = []
  if (risk.contract_no) parts.push(risk.contract_no)
  if (risk.customer_name) parts.push(risk.customer_name)
  if (risk.room_no) parts.push(risk.room_no)
  if (risk.amount != null) parts.push(formatCurrency(risk.amount))
  if (risk.due_date) parts.push(formatDate(risk.due_date))
  return parts.join(' / ')
}

function riskMetric(risk) {
  if (risk.amount != null) return formatCurrency(risk.amount)
  if (risk.due_date) return formatDate(risk.due_date)
  if (risk.occurred_on) return formatDate(risk.occurred_on)
  return ''
}

function dateSignalLabel(risk) {
  if (risk.risk_type === 'contract_expiring') return '到期'
  if (risk.risk_type === 'follow_up_overdue') return '跟进'
  if (risk.risk_type === 'large_adjustment') return '发生'
  return '日期'
}

function isOverdueRisk(risk) {
  return (
    risk.risk_type === 'overdue_receivable' ||
    risk.risk_type === 'follow_up_overdue' ||
    Number(risk.meta?.overdue_days || 0) > 0
  )
}

function signalTone(signal) {
  return signal.tone || 'info'
}

function riskSignals(risk) {
  const signals = []
  if (risk.customer_name) signals.push({ label: '客户', value: risk.customer_name, tone: 'info' })
  if (risk.contract_no) signals.push({ label: '合同', value: risk.contract_no, tone: 'info' })
  if (risk.room_no || risk.tower) signals.push({ label: '房间', value: [risk.tower, risk.room_no].filter(Boolean).join(' / '), tone: 'info' })
  if (risk.meta?.owner) signals.push({ label: '负责人', value: risk.meta.owner, tone: 'info' })
  if (risk.meta?.follow_up_id) signals.push({ label: '跟进', value: `#${risk.meta.follow_up_id}`, tone: 'warning' })
  return signals.slice(0, 4)
}

function riskTimeline(risk) {
  const timeline = []
  if (risk.meta?.overdue_days != null) timeline.push({ label: risk.risk_type === 'overdue_receivable' ? '历史逾期' : '逾期', value: `${risk.meta.overdue_days} 天`, tone: 'danger' })
  if (risk.meta?.days_left != null) timeline.push({ label: '剩余', value: `${risk.meta.days_left} 天`, tone: risk.meta.days_left <= 30 ? 'danger' : 'warning' })
  if (risk.due_date) timeline.push({ label: dateSignalLabel(risk), value: formatDate(risk.due_date), tone: isOverdueRisk(risk) ? 'danger' : 'warning' })
  if (risk.occurred_on) timeline.push({ label: '发生', value: formatDate(risk.occurred_on), tone: 'info' })
  if (risk.created_at) timeline.push({ label: '创建', value: formatDate(risk.created_at), tone: 'info' })
  return timeline.slice(0, 2)
}

function entityLabel(risk) {
  return {
    contract: '合同',
    customer: '客户',
    receivable: '应收',
    deposit: '押金',
    room: '房间'
  }[risk.entity_type] || '对象'
}

function riskKey(risk) {
  return risk.stable_key || risk.id || ''
}

function handleRowSelect(risk) {
  if (props.rowClickable) {
    emit('open-risk', risk)
    return
  }
  if (props.selectable) {
    emit('select-risk', risk)
  }
}
</script>

<template>
  <div class="risk-flag-list" :class="{ compact }" data-testid="risk-flag-list">
    <div v-if="loading" class="risk-flag-loading" data-testid="risk-flag-skeleton" aria-hidden="true">
      <div v-for="row in compact ? 2 : 3" :key="`risk-skeleton-${row}`" class="risk-flag-row risk-flag-skeleton-row">
        <span class="risk-flag-rail muted"></span>
        <div class="risk-flag-priority">
          <span class="skeleton-line skeleton-line-xs"></span>
          <small class="skeleton-line skeleton-line-sm"></small>
        </div>
        <div class="risk-flag-main">
          <span class="skeleton-line skeleton-line-title"></span>
          <div class="risk-flag-signals">
            <span v-for="item in 4" :key="`risk-signal-skeleton-${row}-${item}`" class="skeleton-line skeleton-line-sm"></span>
          </div>
          <span v-if="!compact" class="skeleton-line skeleton-line-md"></span>
        </div>
        <div class="risk-flag-timeline">
          <span class="skeleton-line skeleton-line-sm"></span>
          <span class="skeleton-line skeleton-line-xs"></span>
        </div>
        <div class="risk-flag-actions">
          <span class="skeleton-line skeleton-line-sm"></span>
        </div>
      </div>
    </div>
    <template v-else>
      <div
        v-for="risk in risks"
        :key="risk.stable_key || risk.id"
        :role="rowClickable ? 'button' : selectable ? 'option' : undefined"
        :aria-selected="selectable ? riskKey(risk) === selectedKey : undefined"
        :tabindex="rowClickable || selectable ? 0 : undefined"
        class="risk-flag-row"
        :class="rowClasses(risk)"
        @click="handleRowSelect(risk)"
        @keydown.enter="handleRowSelect(risk)"
      >
        <span class="risk-flag-rail" :class="severityClass(risk.severity)" aria-hidden="true"></span>
        <div class="risk-flag-priority">
          <span class="badge compact" :class="severityClass(risk.severity)">{{ $t(severityLabel(risk.severity)) }}</span>
          <small>{{ $t(impactLabel(risk.risk_type)) }}</small>
        </div>
        <div class="risk-flag-main">
          <div class="risk-flag-title">
            <strong>{{ $t(risk.title) }}</strong>
            <span class="badge compact muted">{{ $t(typeLabel(risk.risk_type)) }}</span>
            <span class="badge compact" :class="statusClass(risk.status)">{{ $t(statusLabel(risk.status)) }}</span>
          </div>
          <div class="risk-flag-signals">
            <span class="risk-flag-signal entity">
              <small>{{ $t("对象") }}</small>
              <b>{{ $t(entityLabel(risk)) }}</b>
            </span>
            <span
              v-for="signal in riskSignals(risk)"
              :key="`${riskKey(risk)}-${signal.label}-${signal.value}`"
              class="risk-flag-signal"
              :class="signalTone(signal)"
            >
              <small>{{ $t(signal.label) }}</small>
              <b>{{ $t(signal.value) }}</b>
            </span>
          </div>
          <p v-if="!compact">{{ $t(risk.description) }}</p>
        </div>
        <div class="risk-flag-timeline">
          <span
            v-for="item in riskTimeline(risk)"
            :key="`${riskKey(risk)}-${item.label}-${item.value}`"
            class="risk-flag-time"
            :class="item.tone"
          >
            <small>{{ $t(item.label) }}</small>
            <b>{{ $t(item.value) }}</b>
          </span>
          <span v-if="!riskTimeline(risk).length && riskMeta(risk)" class="risk-flag-time info">
            <small>{{ $t("关联") }}</small>
            <b>{{ $t(riskMeta(risk)) }}</b>
          </span>
        </div>
        <div class="risk-flag-actions">
          <span v-if="riskMetric(risk)" class="risk-flag-metric">{{ $t(riskMetric(risk)) }}</span>
          <button
            v-if="explainable"
            type="button"
            class="btn btn-compact"
            data-testid="risk-ai-explain-button"
            :disabled="actionsDisabled"
            @click.stop="emit('explain-risk', risk)"
          >{{ $t(" 解释风险 ") }}</button>
          <button
            type="button"
            class="risk-flag-action"
            data-testid="risk-target-open-button"
            :disabled="actionsDisabled"
            @click.stop="emit('open-risk', risk)"
          >
            {{ $t(risk.action_label || '查看') }}
          </button>
        </div>
      </div>
    </template>
    <div v-if="!loading && !risks.length" class="risk-flag-empty">{{ $t(emptyText) }}</div>
  </div>
</template>
