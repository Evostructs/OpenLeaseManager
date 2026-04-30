<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, openDownload } from '../lib/api'
import {
  formatAuditAction,
  formatAuditOperator,
  formatIssueType,
  formatRole,
  formatCurrencyFixed,
  formatDateTime
} from '../lib/format'
import { includesFilter, sortTableRows } from '../lib/table'

const props = defineProps({
  user: {
    type: Object,
    required: true
  },
  section: {
    type: String,
    default: 'tools'
  }
})

const emit = defineEmits(['notify'])

const isAdmin = computed(() => props.user.role === 'admin')
const canUseTools = computed(() => props.user.role !== 'viewer')
const canViewReceivables = computed(() => props.user.role !== 'viewer')
const canViewAudit = computed(() => props.user.role === 'admin')

const showReceivables = computed(() => props.section === 'receivables' && canViewReceivables.value)
const showAudit = computed(() => props.section === 'audit' && canViewAudit.value)
const showTools = computed(() => props.section === 'tools' && canUseTools.value)

const receivables = ref(null)
const month = ref(new Date().toISOString().slice(0, 7))
const receivableFilters = reactive({
  contract_no: '',
  tenant_name: '',
  room_no: '',
  rent: '',
  property_fee: '',
  total: ''
})
const receivableSort = reactive({
  key: '',
  direction: ''
})

const backups = ref([])
const feishu = ref({ webhook_url: '' })
const integrity = ref(null)
const users = ref([])

const batchResult = ref(null)
const unmatchedOptions = ref([])
const orphanPdfs = ref([])
const missingContractPdfs = ref([])
const pdfInput = ref(null)
const pdfPanel = ref(null)
const pdfFocusTarget = ref('')

const audit = ref({ items: [], total: 0, operators: [], actions: [] })
const filters = reactive({ operator: '', action: '' })

const userForm = reactive({ username: '', password: '', display_name: '', role: 'viewer' })
const pwdForm = reactive({ user_id: null, new_password: '' })

const matchedRows = computed(() => batchResult.value?.matched || [])
const unmatchedRows = computed(() => batchResult.value?.unmatched || [])
const totalPendingPdfs = computed(() => orphanPdfs.value.length + unmatchedRows.value.length + missingContractPdfs.value.length)
const displayedIntegrityIssues = computed(() => {
  const issues = [...(integrity.value?.issues || [])]
  const hasMissingPdfIssue = issues.some((issue) => issue.type === 'missing_contract_pdfs')

  if (missingContractPdfs.value.length && !hasMissingPdfIssue) {
    const sample = missingContractPdfs.value
      .slice(0, 5)
      .map((item) => item.contract_no)
      .filter(Boolean)
      .join('、')
    issues.unshift({
      type: 'missing_contract_pdfs',
      contract_no: '',
      detail: `存在 ${missingContractPdfs.value.length} 份执行中合同未上传 PDF${sample ? `，如 ${sample} 等` : ''}`
    })
  }

  return issues
})
const displayedIntegrityIssueCount = computed(() => displayedIntegrityIssues.value.length)

function formatBackupPrimary(item) {
  const filename = String(item?.filename || '')
  const encryptedLabel = item?.encrypted || filename.endsWith('.enc') ? ' · 已加密' : ''
  const match = filename.match(/^(auto_backup|backup)_(\d{8})_(\d{6})(?:_\d+)?\.zip(?:\.enc)?$/i)
  if (match) {
    const [, prefix, ymd, hms] = match
    const typeLabel = prefix.toLowerCase() === 'auto_backup' ? '自动备份' : '手动备份'
    const formatted = `${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)} ${hms.slice(0, 2)}:${hms.slice(2, 4)}`
    return `${formatted} · ${typeLabel}${encryptedLabel}`
  }
  return `${formatDateTime(item?.created)} · 备份文件${encryptedLabel}`
}

function formatAuditChanges(changes) {
  if (!changes) return '--'
  if (typeof changes === 'string') return changes
  try {
    return JSON.stringify(changes)
  } catch {
    return String(changes)
  }
}

const receivableRows = computed(() => {
  const rows = (receivables.value?.rows || []).filter((row) => {
    if (!includesFilter(row.contract_no, receivableFilters.contract_no)) return false
    if (!includesFilter(row.tenant_name, receivableFilters.tenant_name)) return false
    if (!includesFilter(row.room_no, receivableFilters.room_no)) return false
    if (!includesFilter(row.rent, receivableFilters.rent)) return false
    if (!includesFilter(row.property_fee, receivableFilters.property_fee)) return false
    if (!includesFilter(row.total, receivableFilters.total)) return false
    return true
  })

  if (!receivableSort.key || !receivableSort.direction) return rows

  const accessors = {
    contract_no: (row) => row.contract_no,
    tenant_name: (row) => row.tenant_name,
    room_no: (row) => row.room_no,
    rent: (row) => row.rent,
    property_fee: (row) => row.property_fee,
    total: (row) => row.total
  }

  return sortTableRows(rows, accessors[receivableSort.key], receivableSort.direction)
})

async function fetchReceivables() {
  if (!canViewReceivables.value) return
  try {
    receivables.value = await api(`/api/contracts/receivables/monthly?month=${month.value}`)
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function fetchBackups() {
  if (!isAdmin.value) return
  backups.value = await api('/api/backups')
}

async function fetchUnmatchedPdfs() {
  if (!canUseTools.value) return
  orphanPdfs.value = await api('/api/contracts/unmatched-pdfs')
}

async function fetchMissingContractPdfs() {
  if (!canUseTools.value) return
  missingContractPdfs.value = await api('/api/contracts/missing-pdfs')
}

async function fetchIntegrity() {
  if (!isAdmin.value) return
  integrity.value = await api('/api/system/integrity-check')
}

async function refreshIntegrityPanel() {
  if (!showTools.value) return
  const tasks = [fetchUnmatchedPdfs(), fetchMissingContractPdfs()]
  if (isAdmin.value) tasks.push(fetchIntegrity())
  const results = await Promise.allSettled(tasks)
  const failed = results.find((result) => result.status === 'rejected')
  if (failed) {
    emit('notify', { type: 'error', text: failed.reason?.message || '完整性检查刷新失败' })
  }
}

async function fetchAudit() {
  if (!canViewAudit.value) return
  const params = new URLSearchParams({ page: '1', page_size: '50' })
  if (filters.operator) params.set('operator', filters.operator)
  if (filters.action) params.set('action', filters.action)
  audit.value = await api(`/api/audit/logs?${params}`)
}

async function fetchUsers() {
  if (!isAdmin.value) return
  users.value = await api('/api/auth/users')
}

async function fetchFeishu() {
  if (!isAdmin.value) return
  feishu.value = await api('/api/settings/feishu')
}

async function createBackup() {
  try {
    const result = await api('/api/backups', { method: 'POST' })
    emit('notify', { text: `${result.message}：${result.filename}` })
    await fetchBackups()
    await fetchAudit()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function restoreBackup(item) {
  if (!window.confirm(`确定恢复备份 ${formatBackupPrimary(item)} 吗？当前数据会先自动生成一份安全备份。`)) return
  try {
    const result = await api(`/api/backups/${item.filename}/restore`, { method: 'POST' })
    emit('notify', { text: `${result.message}，安全备份：${result.safety_backup}` })
    await Promise.all([fetchBackups(), refreshIntegrityPanel(), fetchAudit()])
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function saveFeishu() {
  try {
    await api('/api/settings/feishu', { method: 'POST', body: JSON.stringify(feishu.value) })
    emit('notify', { text: '飞书 Webhook 已保存' })
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function testFeishu() {
  try {
    const result = await api('/api/settings/feishu/test', { method: 'POST' })
    emit('notify', { text: result.message })
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function triggerFeishuCheck() {
  try {
    const result = await api('/api/settings/feishu/trigger', { method: 'POST' })
    emit('notify', { text: result.message })
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function uploadBatchPdf(event) {
  const files = event.target.files
  if (!files?.length) return

  const payload = new FormData()
  Array.from(files).forEach((file) => payload.append('files', file))

  try {
    batchResult.value = await api('/api/contracts/batch-upload-pdf', { method: 'POST', body: payload })
    emit('notify', {
      text: `上传完成：已匹配 ${batchResult.value.matched_count} 个，待处理 ${batchResult.value.unmatched_count} 个`
    })
    if (batchResult.value.unmatched_count) {
      unmatchedOptions.value = await api('/api/contracts/missing-pdfs')
    }
    await Promise.all([fetchUnmatchedPdfs(), fetchMissingContractPdfs(), fetchIntegrity(), fetchAudit()])
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    event.target.value = ''
  }
}

async function matchPdf(item) {
  try {
    await api(`/api/contracts/match-pdf?filename=${encodeURIComponent(item.filename)}&contract_id=${item.match_id}`, {
      method: 'POST'
    })
    emit('notify', { text: `${item.filename} 已完成关联` })
    if (batchResult.value?.unmatched) {
      batchResult.value.unmatched = batchResult.value.unmatched.filter((entry) => entry.filename !== item.filename)
    }
    orphanPdfs.value = orphanPdfs.value.filter((entry) => entry.filename !== item.filename)
    await Promise.all([fetchUnmatchedPdfs(), fetchMissingContractPdfs(), fetchIntegrity(), fetchAudit()])
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function createUser() {
  try {
    await api('/api/auth/create-user', { method: 'POST', body: JSON.stringify(userForm) })
    emit('notify', { text: '用户创建成功' })
    userForm.username = ''
    userForm.password = ''
    userForm.display_name = ''
    userForm.role = 'viewer'
    await fetchUsers()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function resetPassword() {
  try {
    await api('/api/auth/reset-password', { method: 'POST', body: JSON.stringify(pwdForm) })
    emit('notify', { text: '密码重置成功' })
    pwdForm.user_id = null
    pwdForm.new_password = ''
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function triggerPdfUpload() {
  pdfInput.value?.click()
}

function focusPdfPanel(issueType = 'orphan_pdfs') {
  const target = pdfPanel.value
  pdfFocusTarget.value = issueType === 'missing_contract_pdfs' ? 'missing' : 'panel'
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.clearTimeout(focusPdfPanel._timer)
  focusPdfPanel._timer = window.setTimeout(() => {
    pdfFocusTarget.value = ''
  }, 1800)
}

function toggleReceivableSort(key) {
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

onMounted(async () => {
  if (showReceivables.value) {
    await fetchReceivables()
    return
  }

  if (showAudit.value) {
    await fetchAudit()
    return
  }

  if (showTools.value) {
    await refreshIntegrityPanel()
    if (isAdmin.value) {
      await Promise.all([fetchBackups(), fetchUsers(), fetchFeishu()])
    }
  }
})
</script>

<template>
  <section class="content">
    <article v-if="showReceivables" class="card tool-report-card">
      <div class="card-header compact">
        <div>
          <h3 class="card-title">月度应收报表</h3>
          <p class="helper-text">按月份查看租金与物业费应收，并支持导出合同清单。</p>
        </div>
      </div>

      <div class="toolbar tool-toolbar">
        <input v-model="month" type="month" class="input month-input" />
        <button class="btn btn-primary" data-testid="receivables-query" @click="fetchReceivables">查询</button>
        <button v-if="receivables?.rows?.length" class="btn" @click="openDownload('/api/contracts/export/excel')">导出合同表</button>
      </div>

      <div v-if="receivables" class="tool-summary-grid compact-stats report-summary-grid">
        <div class="tool-summary-card">
          <span>租金</span>
          <strong>{{ formatCurrencyFixed(receivables.total_rent) }}</strong>
        </div>
        <div class="tool-summary-card">
          <span>物业费</span>
          <strong>{{ formatCurrencyFixed(receivables.total_property) }}</strong>
        </div>
        <div class="tool-summary-card">
          <span>合计</span>
          <strong>{{ formatCurrencyFixed(receivables.total) }}</strong>
        </div>
      </div>

      <div v-if="receivableRows.length" class="table-wrapper compact-table">
        <table class="data-table" data-testid="receivables-table" style="min-width: 760px">
          <thead>
            <tr>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'contract_no' }" @click="toggleReceivableSort('contract_no')">合同编号 {{ receivableSortIndicator('contract_no') }}</button></th>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'tenant_name' }" @click="toggleReceivableSort('tenant_name')">客户 {{ receivableSortIndicator('tenant_name') }}</button></th>
              <th><button class="table-sort-btn" :class="{ active: receivableSort.key === 'room_no' }" @click="toggleReceivableSort('room_no')">室号 {{ receivableSortIndicator('room_no') }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'rent' }" @click="toggleReceivableSort('rent')">租金 {{ receivableSortIndicator('rent') }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'property_fee' }" @click="toggleReceivableSort('property_fee')">物业费 {{ receivableSortIndicator('property_fee') }}</button></th>
              <th class="right"><button class="table-sort-btn right" :class="{ active: receivableSort.key === 'total' }" @click="toggleReceivableSort('total')">合计 {{ receivableSortIndicator('total') }}</button></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in receivableRows" :key="row.contract_no">
              <td>{{ row.contract_no }}</td>
              <td>{{ row.tenant_name }}</td>
              <td>{{ row.room_no }}</td>
              <td class="right">{{ formatCurrencyFixed(row.rent) }}</td>
              <td class="right">{{ formatCurrencyFixed(row.property_fee) }}</td>
              <td class="right">{{ formatCurrencyFixed(row.total) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="compact-empty">当前月份暂无应收数据</div>
    </article>

    <article v-else-if="showAudit" class="card tool-report-card">
      <div class="card-header compact">
        <div>
          <h3 class="card-title">行为记录</h3>
          <p class="helper-text">查看合同、房间与系统配置的关键操作记录。</p>
        </div>
        <button class="btn" @click="openDownload('/api/audit/export')">导出日志</button>
      </div>

      <div class="toolbar tool-toolbar">
        <select v-model="filters.operator" class="select" style="max-width: 180px">
          <option value="">全部操作人</option>
          <option v-for="operator in audit.operators || []" :key="operator" :value="operator">
            {{ formatAuditOperator(operator) }}
          </option>
        </select>
        <select v-model="filters.action" class="select" style="max-width: 220px">
          <option value="">全部动作</option>
          <option v-for="action in audit.actions || []" :key="action.value" :value="action.value">
            {{ action.label }}
          </option>
        </select>
        <button class="btn btn-primary" @click="fetchAudit">筛选</button>
      </div>

      <div class="table-wrapper compact-table">
        <table class="data-table" style="min-width: 900px">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>动作</th>
              <th>合同编号</th>
              <th>客户</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in audit.items || []" :key="item.id">
              <td>{{ formatDateTime(item.timestamp) }}</td>
              <td>{{ item.operator_label || formatAuditOperator(item.operator) }}</td>
              <td>{{ item.action_label || formatAuditAction(item.action) }}</td>
              <td>{{ item.contract_no || '--' }}</td>
              <td>{{ item.tenant_name || '--' }}</td>
              <td>{{ formatAuditChanges(item.changes) }}</td>
            </tr>
            <tr v-if="!(audit.items || []).length">
              <td colspan="6" class="list-empty">暂无行为记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <section v-else-if="showTools" class="tool-hub-grid" :class="{ admin: isAdmin, editor: !isAdmin }">
      <div v-if="!isAdmin" class="permission-notice">
        当前账号为编辑权限，仅开放合同 PDF 工具。数据备份、完整性检查、飞书通知和用户管理需使用管理员账号。
      </div>

      <article
        ref="pdfPanel"
        class="card compact-card tool-card tool-card-pdf"
        :class="{ 'focus-target': pdfFocusTarget === 'panel' || pdfFocusTarget === 'missing' }"
      >
        <div class="card-header compact">
          <h3 class="card-title">合同 PDF</h3>
          <button class="btn btn-primary" data-testid="pdf-upload-button" @click="triggerPdfUpload">上传 PDF</button>
          <input ref="pdfInput" class="visually-hidden" type="file" multiple accept=".pdf" @change="uploadBatchPdf" />
        </div>

        <div class="pdf-toolbar-inline">
          <p class="helper-text">支持批量上传后自动匹配，未匹配文件可在下方继续处理。</p>
          <div class="pill-group">
            <span class="metric-pill info">待处理 {{ totalPendingPdfs }}</span>
            <span v-if="orphanPdfs.length" class="metric-pill warning">孤立 PDF {{ orphanPdfs.length }}</span>
          </div>
        </div>

        <div v-if="batchResult" class="upload-summary compact-stats">
          <div class="mini-stat">
            <span>总文件</span>
            <strong>{{ batchResult.total }}</strong>
          </div>
          <div class="mini-stat">
            <span>已匹配</span>
            <strong>{{ batchResult.matched_count }}</strong>
          </div>
          <div class="mini-stat">
            <span>待处理</span>
            <strong>{{ batchResult.unmatched_count }}</strong>
          </div>
        </div>

        <div v-if="orphanPdfs.length" class="detail-list">
          <div class="section-label">未关联合同文件</div>
          <div v-for="item in orphanPdfs" :key="`orphan-${item.filename}`" class="manual-match-card">
            <strong>{{ item.filename }}</strong>
            <div class="toolbar stacked-mobile">
              <span class="helper-text">该文件未自动匹配到合同，需要手动指定。</span>
              <select v-model="item.match_id" class="select">
                <option value="">选择合同</option>
                <option v-for="contract in unmatchedOptions" :key="contract.id" :value="contract.id">
                  {{ contract.contract_no }} - {{ contract.tenant_name }}
                </option>
              </select>
              <button class="btn btn-primary" :disabled="!item.match_id" @click="matchPdf(item)">关联</button>
            </div>
          </div>
        </div>

        <div v-if="matchedRows.length" class="detail-list">
          <div v-for="item in matchedRows" :key="item.filename" class="compact-list-item">
            <div>
              <strong>{{ item.filename }}</strong>
              <p>{{ item.contract_no }} / {{ item.tenant_name }}</p>
            </div>
            <span class="badge success">已匹配</span>
          </div>
        </div>

        <div v-if="unmatchedRows.length" class="detail-list">
          <div v-for="item in unmatchedRows" :key="item.filename" class="manual-match-card">
            <strong>{{ item.filename }}</strong>
            <div class="toolbar stacked-mobile">
              <select v-model="item.match_id" class="select">
                <option value="">选择合同</option>
                <option v-for="contract in unmatchedOptions" :key="contract.id" :value="contract.id">
                  {{ contract.contract_no }} - {{ contract.tenant_name }}
                </option>
              </select>
              <button class="btn btn-primary" :disabled="!item.match_id" @click="matchPdf(item)">关联</button>
            </div>
          </div>
        </div>

        <div v-if="!orphanPdfs.length && !missingContractPdfs.length && !matchedRows.length && !unmatchedRows.length" class="compact-empty compact-empty-tight">
          当前没有待处理 PDF
        </div>
      </article>

      <article v-if="isAdmin" class="card compact-card tool-card tool-card-backup">
        <div class="card-header compact">
          <h3 class="card-title">数据备份</h3>
          <button class="btn btn-primary" data-testid="backup-create-button" @click="createBackup">立即备份</button>
        </div>

        <div class="backup-list scroll-list scroll-list-backup" data-testid="backup-list">
          <div v-for="item in backups" :key="item.filename" class="compact-list-item">
            <div>
              <strong>{{ formatBackupPrimary(item) }}</strong>
              <p>{{ item.filename }}</p>
            </div>
            <div class="backup-actions">
              <button class="btn" :data-testid="`backup-restore-${item.filename}`" @click="restoreBackup(item)">恢复</button>
              <button class="btn" @click="openDownload(`/api/backups/${item.filename}`)">下载</button>
            </div>
          </div>
          <div v-if="!backups.length" class="compact-empty">暂无备份文件</div>
        </div>
      </article>

      <article v-if="isAdmin" class="card compact-card tool-card tool-card-integrity">
        <div class="card-header compact">
          <h3 class="card-title">完整性检查</h3>
          <button class="btn" @click="refreshIntegrityPanel">重新检查</button>
        </div>

        <div v-if="integrity" class="integrity-summary">
          <div class="tool-summary-card">
            <span>问题数</span>
            <strong>{{ displayedIntegrityIssueCount }}</strong>
          </div>
        </div>

        <div v-if="displayedIntegrityIssues.length" class="detail-list">
          <article v-for="issue in displayedIntegrityIssues" :key="`${issue.type}-${issue.contract_no}-${issue.detail}`" class="integrity-card">
            <div class="integrity-card-top">
              <span class="badge warning">{{ formatIssueType(issue.type) }}</span>
              <button
                v-if="['orphan_pdfs', 'missing_contract_pdfs'].includes(issue.type)"
                class="btn"
                :data-testid="`integrity-process-${issue.type}`"
                @click="focusPdfPanel(issue.type)"
              >
                去处理
              </button>
            </div>
            <strong>{{ issue.contract_no || '未关联合同' }}</strong>
            <p>{{ issue.detail }}</p>
          </article>
        </div>
        <div v-else class="compact-empty compact-empty-tight">
          <div class="integrity-ok">
            <span class="badge success">状态正常</span>
            <span>没有发现明显问题</span>
          </div>
        </div>
      </article>

      <article v-if="isAdmin" class="card compact-card tool-card tool-card-feishu">
        <div class="card-header compact">
          <h3 class="card-title">飞书通知</h3>
        </div>

        <label class="form-field">
          <span class="section-label">Webhook 地址</span>
          <textarea v-model="feishu.webhook_url" class="textarea compact-textarea" />
        </label>

        <div class="btn-row">
          <button class="btn btn-primary" @click="saveFeishu">保存</button>
          <button class="btn" @click="testFeishu">发送测试</button>
          <button class="btn" @click="triggerFeishuCheck">立即检查到期</button>
        </div>
      </article>

      <article v-if="isAdmin" class="card compact-card tool-card tool-card-users">
        <div class="card-header compact">
          <h3 class="card-title">用户管理</h3>
        </div>

        <div class="user-role-list">
          <div v-for="userItem in users" :key="userItem.id" class="user-role-item">
            <div>
              <strong>{{ userItem.display_name || userItem.username }}</strong>
              <span>{{ userItem.username }}</span>
            </div>
            <span class="badge info">{{ formatRole(userItem.role) }}</span>
          </div>
        </div>

        <div class="user-admin-stack">
          <div class="admin-subsection">
            <div class="section-label">新建账号</div>
            <div class="form-grid">
              <label class="form-field">
                <span class="section-label">用户名</span>
                <input v-model="userForm.username" class="input" />
              </label>
              <label class="form-field">
                <span class="section-label">显示名称</span>
                <input v-model="userForm.display_name" class="input" />
              </label>
              <label class="form-field">
                <span class="section-label">密码</span>
                <input v-model="userForm.password" type="password" class="input" />
              </label>
              <label class="form-field">
                <span class="section-label">角色</span>
                <select v-model="userForm.role" class="select">
                  <option value="viewer">只读</option>
                  <option value="editor">编辑</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
            </div>
            <div class="btn-row">
              <button class="btn btn-primary" @click="createUser">提交创建</button>
            </div>
          </div>

          <div class="admin-subsection">
            <div class="section-label">密码重置</div>
            <div class="toolbar stacked-mobile">
              <select v-model="pwdForm.user_id" class="select">
                <option :value="null">选择用户</option>
                <option v-for="userItem in users" :key="userItem.id" :value="userItem.id">
                  {{ userItem.display_name }}（{{ formatRole(userItem.role) }}）
                </option>
              </select>
              <input v-model="pwdForm.new_password" type="password" class="input" placeholder="新密码" />
              <button class="btn" @click="resetPassword">确认重置</button>
            </div>
          </div>
        </div>
      </article>
    </section>
  </section>
</template>
