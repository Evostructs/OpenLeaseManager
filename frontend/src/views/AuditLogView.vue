<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import InfoHint from '../components/InfoHint.vue'
import MetricSkeleton from '../components/MetricSkeleton.vue'
import TableSkeleton from '../components/TableSkeleton.vue'
import { openDownload } from '../lib/api'
import { cachedApi } from '../lib/prefetchCache'
import {
  formatAuditAction,
  formatAuditOperator,
  formatDateTime
} from '../lib/format'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify'])

const canViewAudit = computed(() => (
  Array.isArray(props.user?.scope?.effective_capabilities)
  && props.user.scope.effective_capabilities.includes('platform_admin')
))
const showAudit = computed(() => canViewAudit.value)

const audit = ref({ items: [], total: 0, operators: [], actions: [], target_types: [], page: 1, page_size: 50, pages: 1 })
const auditLoading = ref(false)
const auditLoaded = ref(false)
const auditError = ref('')

const filters = reactive({ operator: '', action: '', target_type: '', target_id: '', start_date: '', end_date: '', page: 1 })

const auditQuickActions = [
  { label: '导入', actions: ['import', 'customer_import', 'customer_profile_import', 'customer_master_import', 'followup_import'] },
  { label: '智能协助', actions: ['ai_customer_summary', 'ai_follow_suggestion', 'ai_risk_explain'] },
  { label: '资料', actions: ['attachment_upload', 'attachment_update', 'attachment_delete'] },
  { label: '合同', actions: ['create', 'update', 'terminate', 'change_confirm', 'room_expand', 'room_reduce', 'room_swap'] },
  { label: '收款', actions: ['receivable_update', 'receivable_payment', 'receivable_payment_delete'] },
  { label: '押金', actions: ['deposit_create', 'deposit_update', 'deposit_receive', 'deposit_refund', 'deposit_offset'] },
  { label: '文档报表', actions: ['document_generate', 'daily_report_export'] },
  { label: '配置', actions: ['config_update'] }
]
const sensitiveAuditKeyPattern = /(password|token|cookie|secret|authorization|api[_-]?key|ai[_-]?key|webhook|prompt|output|completion|response)/i
const businessAuditActionLabels = {
  ai_customer_summary: '客户摘要',
  ai_follow_suggestion: '跟进建议',
  ai_risk_explain: '风险说明'
}
const auditFieldLabels = {
  action: '事项',
  amount: '金额',
  area: '面积',
  attachment_id: '附件记录',
  changed_fields: '变更字段',
  changes: '变更',
  contract_id: '合同记录',
  contract_no: '合同',
  customer_id: '客户记录',
  date: '日期',
  deposit_id: '押金记录',
  deposit_type: '押金类型',
  document_label: '文档',
  document_type: '文档类型',
  end_date: '结束',
  expected_amount: '应收押金',
  failed: '失败',
  filename: '文件',
  follow_up_id: '跟进记录',
  import_type: '导入类型',
  monthly_rent: '月租',
  month: '月份',
  name: '客户',
  old: '原值',
  new: '新值',
  payment_id: '收款记录',
  period_month: '账期',
  prev_contract_no: '原合同',
  queue_counts: '待办',
  rent: '租金',
  risk_count: '风险数',
  room_no: '房源',
  rooms: '房源',
  start_date: '开始',
  status: '状态',
  succeeded: '成功',
  tenant_name: '客户',
  tower: '楼栋',
  type: '类型',
  unit_price: '单价'
}
const hiddenAuditSummaryKeys = new Set(['provider', 'model', 'base_url', 'api_key', 'prompt', 'messages', 'output', 'completion', 'response', 'token', 'tokens'])

const auditInitialLoading = computed(() => showAudit.value && auditLoading.value && !auditLoaded.value && !(audit.value.items || []).length)
const auditRefreshing = computed(() => showAudit.value && auditLoading.value && (auditLoaded.value || (audit.value.items || []).length > 0))

const auditActiveFilterCount = computed(() => (
  [filters.operator, filters.action, filters.target_type, filters.target_id, filters.start_date, filters.end_date]
    .filter(Boolean).length
))
const auditLatestItem = computed(() => (audit.value.items || [])[0] || null)
const auditLatestTargetTitle = computed(() => {
  const item = auditLatestItem.value
  if (!item) return '--'
  return item.contract_no || `${formatAuditTargetType(item.target_type)} #${item.target_id || '--'}`
})
const auditLatestTargetMeta = computed(() => {
  const item = auditLatestItem.value
  if (!item) return '暂无对象'
  return item.tenant_name || formatAuditTargetType(item.target_type)
})
const auditVisibleQuickActions = computed(() => {
  const available = new Set((audit.value.actions || []).map((item) => item.value))
  return auditQuickActions
    .map((group) => ({
      ...group,
      availableActions: group.actions.filter((action) => available.has(action))
    }))
    .filter((group) => group.availableActions.length)
})
const auditActiveQuickAction = computed(() => (
  auditVisibleQuickActions.value.find((group) => group.availableActions.includes(filters.action))?.label || ''
))

function auditItemTargetLabel(item) {
  if (!item) return '--'
  const businessLabel = [item.contract_no, item.tenant_name].filter(Boolean).join(' / ')
  if (businessLabel) return businessLabel
  return `${formatAuditTargetType(item.target_type)} #${item.target_id || '--'}`
}

function formatAuditTargetType(targetType) {
  switch (targetType) {
    case 'contract':
      return '合同'
    case 'customer':
      return '客户'
    case 'room':
      return '房间'
    case 'deposit':
      return '押金'
    case 'attachment':
      return '附件'
    case 'document':
      return '文档'
    case 'report':
      return '报表'
    case 'system_config':
      return '配置'
    case 'receivable_payment':
      return '收款'
    case 'rent_term':
      return '租金条款'
    case 'contract_room':
      return '合同房源'
    case 'follow_up':
      return '跟进'
    default:
      return targetType || '--'
  }
}

function auditActionTone(action) {
  if (['delete', 'room_delete', 'receivable_payment_delete', 'attachment_delete', 'customer_followup_delete', 'rent_term_delete'].includes(action)) return 'danger'
  if (String(action || '').startsWith('ai_')) return 'info'
  if (String(action || '').includes('import') || action === 'document_generate') return 'success'
  if (String(action || '').includes('config') || String(action || '').includes('restore')) return 'warning'
  return 'neutral'
}

function businessAuditActionLabel(action, fallbackLabel = '') {
  if (businessAuditActionLabels[action]) return businessAuditActionLabels[action]
  const formatted = formatAuditAction(action)
  const fallback = String(fallbackLabel || '').trim()
  if (fallback && fallback !== action && !fallback.includes('_')) return fallback
  if (formatted && !String(formatted).includes('_')) return formatted
  return '业务操作'
}

function auditActionOptionLabel(actionOption) {
  return businessAuditActionLabel(actionOption?.value, actionOption?.label)
}

function auditFieldLabel(key) {
  return auditFieldLabels[key] || '记录'
}

function isHiddenAuditSummaryKey(key) {
  const normalized = String(key || '').toLowerCase()
  return hiddenAuditSummaryKeys.has(normalized) || sensitiveAuditKeyPattern.test(normalized)
}

function formatAuditFieldList(fields) {
  return fields.filter((field) => !isHiddenAuditSummaryKey(field)).map((field) => auditFieldLabel(field)).filter(Boolean).join('、') || '--'
}

function formatAiAssistTask(type) {
  switch (type) {
    case 'customer_summary':
      return '客户摘要'
    case 'follow_up_suggestion':
      return '跟进建议'
    case 'risk_explanation':
      return '风险说明'
    default:
      return '智能协助'
  }
}

function redactAuditText(value, fallback = '--') {
  const text = String(value ?? '').trim()
  if (!text) return fallback
  if (sensitiveAuditKeyPattern.test(text)) return '敏感内容已隐藏'
  return text.length > 140 ? `${text.slice(0, 140)}...` : text
}

function sanitizeAuditValue(key, value, depth = 0) {
  if (sensitiveAuditKeyPattern.test(String(key || ''))) return '已隐藏'
  if (value == null || value === '') return value
  if (typeof value === 'string') return redactAuditText(value, '')
  if (typeof value !== 'object') return value
  if (depth >= 2) return '已折叠'
  if (Array.isArray(value)) {
    return value.slice(0, 4).map((entry, index) => sanitizeAuditValue(`${key}_${index}`, entry, depth + 1))
  }
  return Object.fromEntries(
    Object.entries(value).slice(0, 8).map(([childKey, childValue]) => [
      childKey,
      sanitizeAuditValue(childKey, childValue, depth + 1)
    ])
  )
}

function compactAuditValue(key, value) {
  if (value == null || value === '') return '--'
  if (Array.isArray(value)) return value.map((entry) => compactAuditValue(key, entry)).join('、') || '--'
  if (typeof value === 'object') {
    const keys = Object.keys(value)
    if (!keys.length) return '--'
    if (value.old !== undefined || value.new !== undefined) {
      return `${compactAuditValue('old', value.old)} -> ${compactAuditValue('new', value.new)}`
    }
    return formatAuditFieldList(keys)
  }
  return String(value)
}

function formatAuditChanges(changes, action = '') {
  if (!changes) return '--'
  if (action === 'config_update') return '通知配置已更新'
  if (typeof changes === 'string' && !changes.trim().startsWith('{') && !changes.trim().startsWith('[')) {
    return redactAuditText(changes)
  }
  try {
    const parsed = typeof changes === 'string' ? JSON.parse(changes) : changes
    const safe = sanitizeAuditValue('', parsed)
    if (safe && typeof safe === 'object' && !Array.isArray(safe) && (safe.import_type || safe.succeeded !== undefined || safe.failed !== undefined)) {
      return `导入 ${safe.import_type || '--'}：成功 ${safe.succeeded ?? 0}，失败 ${safe.failed ?? 0}`
    }
    if (safe && typeof safe === 'object' && !Array.isArray(safe) && (safe.provider || String(action || '').startsWith('ai_'))) {
      return formatAiAssistTask(safe.type)
    }
    if (safe && typeof safe === 'object' && !Array.isArray(safe) && safe.changed_fields?.length) {
      return `变更：${formatAuditFieldList(safe.changed_fields)}`
    }
    if (safe && typeof safe === 'object' && !Array.isArray(safe) && safe.changes && typeof safe.changes === 'object') {
      return `变更：${formatAuditFieldList(Object.keys(safe.changes))}`
    }
    if (safe && typeof safe === 'object' && !Array.isArray(safe)) {
      return Object.entries(safe)
        .filter(([key]) => !isHiddenAuditSummaryKey(key))
        .slice(0, 4)
        .map(([key, value]) => `${auditFieldLabel(key)}：${compactAuditValue(key, value)}`)
        .join('；') || '--'
    }
    return redactAuditText(JSON.stringify(safe))
  } catch {
    return redactAuditText(changes)
  }
}

async function fetchAudit() {
  if (!canViewAudit.value) return
  auditLoading.value = true
  const params = new URLSearchParams({ page: String(filters.page || 1), page_size: '50' })
  if (filters.operator) params.set('operator', filters.operator)
  if (filters.action) params.set('action', filters.action)
  if (filters.target_type) params.set('target_type', filters.target_type)
  if (filters.target_id) params.set('target_id', filters.target_id)
  if (filters.start_date) params.set('start_date', filters.start_date)
  if (filters.end_date) params.set('end_date', filters.end_date)
  try {
    audit.value = await cachedApi(`/api/audit/logs?${params}`)
    auditLoaded.value = true
    auditError.value = ''
    filters.page = audit.value.page || 1
  } catch (error) {
    auditError.value = error.message || '操作日志读取失败，请稍后重试'
  } finally {
    auditLoading.value = false
  }
}

async function applyAuditFilters() {
  filters.page = 1
  await fetchAudit()
}

async function clearAuditFilters() {
  filters.operator = ''
  filters.action = ''
  filters.target_type = ''
  filters.target_id = ''
  filters.start_date = ''
  filters.end_date = ''
  filters.page = 1
  await fetchAudit()
}

async function setAuditQuickAction(group) {
  filters.action = auditActiveQuickAction.value === group.label ? '' : group.availableActions[0]
  filters.page = 1
  await fetchAudit()
}

async function changeAuditPage(delta) {
  const totalPages = audit.value.pages || 1
  filters.page = Math.min(Math.max(1, filters.page + delta), totalPages)
  await fetchAudit()
}

onMounted(async () => {
  if (showAudit.value) {
    await fetchAudit()
  }
})
</script>

<template>
  <section class="content">
    <article v-if="showAudit" class="card tool-report-card" data-testid="audit-page">
      <div class="card-header compact">
        <div>
            <h3 class="card-title">操作日志</h3>
        </div>
        <div class="audit-header-actions">
          <span data-testid="audit-help-trigger">
            <InfoHint text="查看合同、客户、收款等关键记录。" />
          </span>
          <span v-if="auditRefreshing" class="metric-pill info refreshing-indicator">同步中</span>
          <button class="btn" data-testid="audit-export-button" @click="openDownload('/api/audit/export')">导出日志</button>
        </div>
      </div>

      <div v-if="auditInitialLoading" class="audit-loading-layout" data-testid="audit-skeleton">
        <MetricSkeleton :count="4" />
        <div class="audit-insight-grid">
          <TableSkeleton :rows="4" :columns="3" />
          <TableSkeleton :rows="4" :columns="3" />
          <TableSkeleton :rows="4" :columns="2" />
        </div>
        <TableSkeleton :rows="8" :columns="7" />
      </div>
      <template v-else>
      <div v-if="auditError" class="inline-status-row danger" data-testid="audit-load-error">{{ auditError }}</div>
      <div class="audit-activity-strip" data-testid="audit-activity-strip">
        <div class="audit-activity-item primary" data-testid="audit-result-card">
          <span>日志总数</span>
          <strong>{{ audit.total || 0 }}</strong>
          <small>{{ auditActiveFilterCount ? `已启用 ${auditActiveFilterCount} 个筛选` : '全部日志' }}</small>
        </div>
        <div class="audit-activity-item" data-testid="audit-latest-action-card">
          <span>最新动作</span>
          <strong>{{ auditLatestItem ? businessAuditActionLabel(auditLatestItem.action, auditLatestItem.action_label) : '--' }}</strong>
          <small>{{ auditLatestItem ? formatDateTime(auditLatestItem.timestamp) : '暂无记录' }}</small>
        </div>
        <div class="audit-activity-item" data-testid="audit-latest-operator-card">
          <span>最新操作人</span>
          <strong>{{ auditLatestItem ? (auditLatestItem.operator_label || formatAuditOperator(auditLatestItem.operator)) : '--' }}</strong>
          <small>{{ auditLatestItem ? formatDateTime(auditLatestItem.timestamp) : '暂无记录' }}</small>
        </div>
        <div class="audit-activity-item" data-testid="audit-latest-target-card">
          <span>最新对象</span>
          <strong>{{ auditLatestTargetTitle }}</strong>
          <small>{{ auditLatestTargetMeta }}</small>
        </div>
      </div>

      <div v-if="auditVisibleQuickActions.length" class="audit-action-strip" data-testid="audit-action-strip">
        <span class="section-label">快速筛选</span>
        <button
          v-for="group in auditVisibleQuickActions"
          :key="group.label"
          type="button"
          class="audit-chip"
          :class="{ active: auditActiveQuickAction === group.label }"
          @click="setAuditQuickAction(group)"
        >
          {{ group.label }}
        </button>
      </div>

      <details class="demo-secondary-details audit-filter-details">
        <summary>
          <span>筛选工具</span>
          <strong>按人员 / 动作 / 对象查找</strong>
        </summary>
      <div class="audit-filter-panel" data-testid="audit-filter-panel">
        <label>
          <span>操作人</span>
          <select v-model="filters.operator" class="select" data-testid="audit-operator-filter">
            <option value="">全部操作人</option>
            <option v-for="operator in audit.operators || []" :key="operator" :value="operator">
              {{ formatAuditOperator(operator) }}
            </option>
          </select>
        </label>
        <label>
          <span>事项</span>
          <select v-model="filters.action" class="select" data-testid="audit-action-filter">
            <option value="">全部事项</option>
            <option v-for="action in audit.actions || []" :key="action.value" :value="action.value">
              {{ auditActionOptionLabel(action) }}
            </option>
          </select>
        </label>
        <label>
          <span>对象</span>
          <select v-model="filters.target_type" class="select" data-testid="audit-target-filter">
            <option value="">全部对象</option>
            <option v-for="targetType in audit.target_types || []" :key="targetType" :value="targetType">
              {{ formatAuditTargetType(targetType) }}
            </option>
          </select>
        </label>
        <label>
          <span>记录编号</span>
          <input v-model="filters.target_id" class="input" data-testid="audit-target-id-filter" placeholder="编号" @keyup.enter="applyAuditFilters" />
        </label>
        <label>
          <span>开始</span>
          <input v-model="filters.start_date" class="input" type="date" />
        </label>
        <label>
          <span>结束</span>
          <input v-model="filters.end_date" class="input" type="date" />
        </label>
        <div class="audit-filter-actions">
          <button class="btn btn-primary" data-testid="audit-filter-submit" @click="applyAuditFilters">筛选</button>
          <button class="btn" data-testid="audit-filter-clear" @click="clearAuditFilters">清空</button>
          <button class="btn" data-testid="audit-refresh" @click="fetchAudit">刷新</button>
        </div>
      </div>
      </details>

      <div class="table-wrapper compact-table audit-table-wrapper">
        <table class="data-table audit-table" data-testid="audit-log-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>事项</th>
              <th>对象</th>
              <th>记录</th>
              <th>合同 / 客户</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in audit.items || []" :key="item.id">
              <td class="audit-time-cell">{{ formatDateTime(item.timestamp) }}</td>
              <td class="audit-operator-cell">{{ item.operator_label || formatAuditOperator(item.operator) }}</td>
              <td>
                <span class="audit-action-badge" :class="auditActionTone(item.action)">
                  {{ businessAuditActionLabel(item.action, item.action_label) }}
                </span>
              </td>
              <td>{{ formatAuditTargetType(item.target_type) }}</td>
              <td>{{ item.target_id || '--' }}</td>
              <td>
                <div class="audit-business-cell">
                  <strong>{{ item.contract_no || '--' }}</strong>
                  <span>{{ item.tenant_name || '--' }}</span>
                </div>
              </td>
              <td class="audit-summary-cell">{{ formatAuditChanges(item.changes, item.action) }}</td>
            </tr>
            <tr v-if="!(audit.items || []).length">
              <td colspan="7" class="list-empty">
                暂无记录
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-footer">
        <span class="helper-text">共 {{ audit.total || 0 }} 条 · 第 {{ audit.page || 1 }} / {{ audit.pages || 1 }} 页</span>
        <div class="spacer" />
        <button class="btn" :disabled="(audit.page || 1) <= 1" @click="changeAuditPage(-1)">上一页</button>
        <button class="btn" :disabled="(audit.page || 1) >= (audit.pages || 1)" @click="changeAuditPage(1)">下一页</button>
      </div>
      </template>
    </article>
  </section>
</template>
