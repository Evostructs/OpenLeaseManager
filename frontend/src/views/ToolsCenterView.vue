<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { currencyUnit, setCurrencyUnit, locale } from '../i18n/index.js'
import { currencies } from '../i18n/currency.js'
import BaseModal from '../components/BaseModal.vue'
import InfoHint from '../components/InfoHint.vue'
import { api, openDownload } from '../lib/api'
import { cachedApi, invalidateApiCache } from '../lib/prefetchCache'
import { formatDateTime } from '../lib/format'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['notify', 'operating-gate-change'])
const NEW_ACCOUNT_USERNAME_PATTERN = /^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$/

const isAdmin = computed(() => (
  Array.isArray(props.user?.scope?.effective_capabilities)
  && props.user.scope.effective_capabilities.includes('platform_admin')
))
const canUseTools = computed(() => isAdmin.value)
const canViewAudit = computed(() => isAdmin.value)
const showTools = computed(() => canUseTools.value)

const selectedCurrency = ref(currencyUnit.value)
const currencySaving = ref(false)
async function saveCurrencyUnit() {
  currencySaving.value = true
  try {
    const result = await api('/api/settings/currency', { method: 'PUT', body: JSON.stringify({ code: selectedCurrency.value }) })
    setCurrencyUnit(result.code)
    emit('notify', { text: '货币单位已保存，金额数字未改变。' })
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    currencySaving.value = false
  }
}

const backups = ref([])
const backupRestoreConfirm = reactive({ item: null, restoring: false })

const feishu = ref({ webhook_url: '' })
const users = ref([])
const operatingGates = ref([])
const operatingGatesError = ref('')
const savingGateCode = ref('')
const backupsError = ref('')
const usersError = ref('')
const feishuError = ref('')

const userForm = reactive({ username: '', password: '', display_name: '', role: 'viewer' })
const userConfirmPassword = ref('')
const userConfirmationInput = ref(null)
const userConfirmationError = ref('')
watch(() => [userForm.password, userConfirmPassword.value], () => { userConfirmationError.value = '' })
const pwdForm = reactive({ user_id: null, new_password: '' })

const latestBackup = computed(() => backups.value[0] || null)
const backupSignalCopy = computed(() => (latestBackup.value ? formatBackupPrimary(latestBackup.value) : '暂无备份'))
const feishuSignalCopy = computed(() => (feishu.value?.webhook_url ? '已配置' : '待配置'))
const accountSignalCopy = computed(() => `${users.value.length} 个账号`)
const gateSignalCopy = computed(() => {
  const openCount = operatingGates.value.filter((item) => item.contribution_enabled).length
  return `${openCount}/${operatingGates.value.length || 0} 栋计入合计`
})

function gateStateLabel(item) {
  switch (item?.state) {
    case 'open':
      return '展示并计入合计'
    case 'observe':
      return '观察态，不计入合计'
    case 'aggregate_only':
      return '不展示，但计入合计'
    default:
      return '暂不展示，也不计入合计'
  }
}

function gateChanged(item) {
  return Boolean(
    item
    && (
      Boolean(item.draft_presentation_enabled) !== Boolean(item.presentation_enabled)
      || Boolean(item.draft_contribution_enabled) !== Boolean(item.contribution_enabled)
    )
  )
}

function backupTypeLabelFromFilename(prefix, hms) {
  const normalizedPrefix = String(prefix || '').toLowerCase()
  if (normalizedPrefix === 'auto_backup') return '系统定时自动备份'
  if (normalizedPrefix === 'safety_backup') return '恢复前安全备份'
  if (hms === '020000') return '系统定时自动备份'
  return '手动立即备份'
}

function formatBackupPrimary(item) {
  const filename = String(item?.filename || '')
  const encryptedLabel = item?.encrypted || filename.endsWith('.enc') ? ' · 已加密' : ''
  const match = filename.match(/^(auto_backup|safety_backup|backup)_(\d{8})_(\d{6})(?:_\d+)?\.zip(?:\.enc)?$/i)
  if (match) {
    const [, prefix, ymd, hms] = match
    const typeLabel = item?.type_label || backupTypeLabelFromFilename(prefix, hms)
    const formatted = `${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)} ${hms.slice(0, 2)}:${hms.slice(2, 4)}`
    return `${formatted} · ${typeLabel}${encryptedLabel}`
  }
  return `${formatDateTime(item?.created)} · ${item?.type_label || '备份文件'}${encryptedLabel}`
}

function formatUserRole(role) {
  switch (role) {
    case 'admin':
      return '管理员'
    case 'editor':
      return '编辑'
    case 'viewer':
      return '查看'
    default:
      return role || '--'
  }
}

async function fetchBackups() {
  if (!isAdmin.value) return
  try {
    backups.value = await cachedApi('/api/backups')
    backupsError.value = ''
  } catch (error) {
    backupsError.value = error.message || '备份列表读取失败，请稍后重试'
  }
}

// 与拆分前的 ToolsView 行为保持一致：创建/恢复备份后会刷新一次操作日志数据。
// 工具中心不展示日志内容，这里仅保留原有的刷新请求与静默错误处理。
const audit = ref({ items: [], total: 0, operators: [], actions: [], target_types: [], page: 1, page_size: 50, pages: 1 })
const auditError = ref('')

async function fetchAudit() {
  if (!canViewAudit.value) return
  const params = new URLSearchParams({ page: '1', page_size: '50' })
  try {
    audit.value = await cachedApi(`/api/audit/platform/logs?${params}`)
    auditError.value = ''
  } catch (error) {
    auditError.value = error.message || '操作日志读取失败，请稍后重试'
  }
}

async function fetchUsers() {
  if (!isAdmin.value) return
  try {
    users.value = await cachedApi('/api/auth/users')
    usersError.value = ''
  } catch (error) {
    usersError.value = error.message || '账号列表读取失败，请稍后重试'
  }
}

async function fetchOperatingGates() {
  if (!isAdmin.value) return
  try {
    const result = await api('/api/buildings/operating-gates')
    operatingGates.value = (Array.isArray(result?.items) ? result.items : []).map((item) => ({
      ...item,
      draft_presentation_enabled: Boolean(item.presentation_enabled),
      draft_contribution_enabled: Boolean(item.contribution_enabled),
      draft_reason: ''
    }))
    operatingGatesError.value = ''
  } catch (error) {
    operatingGatesError.value = error.message || '楼宇展示与合计状态读取失败，请稍后重试'
  }
}

async function saveOperatingGate(item) {
  const reason = String(item?.draft_reason || '').trim()
  if (!gateChanged(item) || !reason || savingGateCode.value) return
  savingGateCode.value = item.building_code
  try {
    await api(`/api/buildings/${encodeURIComponent(item.building_code)}/operating-gate`, {
      method: 'PUT',
      body: JSON.stringify({
        presentation_enabled: Boolean(item.draft_presentation_enabled),
        contribution_enabled: Boolean(item.draft_contribution_enabled),
        reason,
        expected_revision: Number(item.revision || 0)
      })
    })
    invalidateApiCache('/api/buildings')
    invalidateApiCache('/api/operations/snapshot')
    invalidateApiCache('/api/operations/snapshot-evidence')
    emit('notify', { text: `${item.building_name || item.building_code}的展示与合计状态已保存` })
    emit('operating-gate-change')
    await fetchOperatingGates()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
    await fetchOperatingGates()
  } finally {
    savingGateCode.value = ''
  }
}

async function fetchFeishu() {
  if (!isAdmin.value) return
  try {
    feishu.value = await cachedApi('/api/settings/feishu')
    feishuError.value = ''
  } catch (error) {
    feishuError.value = error.message || '提醒配置读取失败，请稍后重试'
  }
}

async function createBackup() {
  try {
    const result = await api('/api/backups', { method: 'POST' })
    emit('notify', { text: `${result.message}：${result.filename}` })
    invalidateApiCache('/api/backups')
    invalidateApiCache('/api/audit/platform/logs')
    await fetchBackups()
    await fetchAudit()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

function openRestoreBackupConfirm(item) {
  if (!item?.filename || backupRestoreConfirm.restoring) return
  backupRestoreConfirm.item = item
}

function closeRestoreBackupConfirm() {
  if (backupRestoreConfirm.restoring) return
  backupRestoreConfirm.item = null
}

async function confirmRestoreBackup() {
  const target = backupRestoreConfirm.item
  if (!target?.filename || backupRestoreConfirm.restoring) return
  backupRestoreConfirm.restoring = true
  try {
    const result = await api(`/api/backups/${target.filename}/restore`, { method: 'POST' })
    emit('notify', { text: `${result.message}，安全备份：${result.safety_backup}` })
    invalidateApiCache('/api/backups')
    invalidateApiCache('/api/audit/platform/logs')
    await Promise.all([fetchBackups(), fetchAudit()])
    backupRestoreConfirm.item = null
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    backupRestoreConfirm.restoring = false
  }
}

async function saveFeishu() {
  try {
    await api('/api/settings/feishu', { method: 'POST', body: JSON.stringify(feishu.value) })
    invalidateApiCache('/api/settings/feishu')
    emit('notify', { text: '飞书通知已保存' })
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

async function createUser() {
  if (!userConfirmPassword.value || userForm.password !== userConfirmPassword.value) {
    userConfirmationError.value = userConfirmPassword.value ? '两次输入的密码不一致' : '请再次输入密码'
    userConfirmationInput.value?.focus()
    return
  }
  if (!NEW_ACCOUNT_USERNAME_PATTERN.test(userForm.username)) {
    emit('notify', {
      type: 'error',
      text: '用户名须为 3—32 位小写字母、数字、点、下划线或短横线，且首尾须为字母或数字',
    })
    return
  }
  try {
    await api('/api/auth/create-user', { method: 'POST', body: JSON.stringify(userForm) })
    emit('notify', { text: '用户创建成功' })
    userForm.username = ''
    userForm.password = ''
    userConfirmPassword.value = ''
    userForm.display_name = ''
    userForm.role = 'viewer'
    invalidateApiCache('/api/auth/users')
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

onMounted(async () => {
  if (showTools.value) {
    if (isAdmin.value) {
      await Promise.all([fetchBackups(), fetchUsers(), fetchFeishu(), fetchOperatingGates()])
    }
  }
})
</script>

<template>
  <section class="content">
    <section v-if="showTools" class="tool-console" :class="{ admin: isAdmin, editor: !isAdmin }" data-testid="tools-page">
      <div class="tool-task-grid" data-testid="tools-task-grid">
        <section v-if="isAdmin" class="tool-task-group" data-testid="tool-section-currency">
          <div class="tool-task-group-head"><h3>{{ $t("货币单位") }}</h3></div>
          <article class="tool-task-row tool-card">
            <div class="tool-task-main">
              <p>{{ $t("界面、业务报表和常用 Excel 导出使用同一货币单位。") }}</p>
              <p class="helper-text">{{ $t("仅更换金额的货币单位，已有数字不变，不进行汇率换算。") }}</p>
            </div>
            <div class="tool-task-detail">
              <label class="form-field">
                <span>{{ $t("货币单位") }}</span>
                <select v-model="selectedCurrency" class="select" data-testid="currency-unit-select">
                  <option v-for="item in currencies" :key="item.code" :value="item.code">{{ item.code }} — {{ locale === 'en' ? item.en : item.zh }}</option>
                </select>
              </label>
            </div>
            <div class="tool-task-actions">
              <button class="btn btn-primary" data-testid="currency-unit-save" :disabled="currencySaving || selectedCurrency === currencyUnit" @click="saveCurrencyUnit">{{ $t(currencySaving ? '保存中' : '保存') }}</button>
            </div>
          </article>
        </section>
        <section class="tool-task-group tool-task-group-materials" data-testid="tool-section-materials">
          <div class="tool-task-group-head">
            <h3>{{ $t("提醒与备份") }}</h3>
            <span class="tools-help-trigger" data-testid="tools-help-trigger">
              <InfoHint :text="$t(&quot;常用处理入口按日常任务归在一起。&quot;)" />
            </span>
          </div>

          <article v-if="isAdmin" class="tool-task-row tool-card tool-card-feishu" data-testid="tool-card-feishu">
            <div class="tool-task-main">
              <h4>{{ $t("到期提醒") }}</h4>
              <p>{{ $t("维护通知地址，测试提醒发送。") }}</p>
              <span class="metric-pill info">{{ $t(feishuSignalCopy) }}</span>
            </div>
            <div class="tool-task-actions">
              <button class="btn btn-primary" @click="saveFeishu">{{ $t("保存") }}</button>
              <button class="btn" data-testid="tool-reminder-test-action" @click="testFeishu">{{ $t("测试通知") }}</button>
              <button class="btn" @click="triggerFeishuCheck">{{ $t("检查到期") }}</button>
            </div>
            <div class="tool-task-detail">
              <div v-if="feishuError" class="inline-status-row danger" data-testid="feishu-load-error">{{ $t(feishuError) }}</div>
              <label class="form-field">
                <span class="section-label">{{ $t("通知地址") }}</span>
                <textarea v-model="feishu.webhook_url" class="textarea compact-textarea" />
              </label>
            </div>
          </article>

          <article class="tool-task-row tool-card tool-card-backup" data-testid="tool-card-backup">
            <div class="tool-task-main">
              <h4>{{ $t("数据备份") }}</h4>
              <p>{{ $t("创建、下载或恢复备份。") }}</p>
              <span class="metric-pill info">{{ $t(backupSignalCopy) }}</span>
            </div>
            <div class="tool-task-actions">
              <button class="btn btn-primary" data-testid="backup-create-button" @click="createBackup">{{ $t("立即备份") }}</button>
            </div>

            <div class="tool-task-detail">
              <div class="backup-list scroll-list scroll-list-backup" data-testid="backup-list">
                <div v-for="item in backups" :key="item.filename" class="compact-list-item">
                  <div>
                    <strong>{{ $t(formatBackupPrimary(item)) }}</strong>
                    <p>{{ item.filename }}</p>
                  </div>
                  <div class="backup-actions">
                    <button class="btn" :data-testid="`backup-restore-${item.filename}`" @click="openRestoreBackupConfirm(item)">{{ $t("恢复") }}</button>
                    <button class="btn" @click="openDownload(`/api/backups/${item.filename}`)">{{ $t("下载") }}</button>
                  </div>
                </div>
                <div v-if="backupsError" class="inline-status-row danger" data-testid="backup-load-error">{{ $t(backupsError) }}</div>
                <div v-else-if="!backups.length" class="inline-status-row">{{ $t("暂无备份文件") }}</div>
              </div>

              <BaseModal :open="Boolean(backupRestoreConfirm.item)" :title="$t(&quot;恢复备份&quot;)" size="small" @close="closeRestoreBackupConfirm">
                <div data-testid="backup-restore-confirm-modal" class="detail-list">
                  <p>{{ $t("确定恢复备份「") }}{{ $t(formatBackupPrimary(backupRestoreConfirm.item)) }}{{ $t("」吗？") }}</p>
                  <p class="helper-text danger">{{ $t("当前数据会先自动生成一份安全备份；确认后，当前数据库、PDF 与附件会被该备份覆盖。") }}</p>
                </div>
                <template #footer>
                  <button class="btn" data-testid="backup-restore-cancel" :disabled="backupRestoreConfirm.restoring" @click="closeRestoreBackupConfirm">{{ $t("取消") }}</button>
                  <button class="btn btn-danger" data-testid="backup-restore-confirm" :disabled="backupRestoreConfirm.restoring" @click="confirmRestoreBackup">
                    {{ $t(backupRestoreConfirm.restoring ? '恢复中' : '确认恢复') }}
                  </button>
                </template>
              </BaseModal>
            </div>
          </article>
        </section>

        <section v-if="isAdmin" class="tool-task-group tool-task-group-operating-gates" data-testid="tool-section-operating-gates">
          <div class="tool-task-group-head">
            <h3>{{ $t("经营大屏与板块合计") }}</h3>
          </div>

          <article class="tool-task-row tool-card" data-testid="tool-card-operating-gates">
            <div class="tool-task-main">
              <h4>{{ $t("逐楼展示与合计") }}</h4>
              <p>{{ $t("两项可独立调整；所有经营数字当前均为模拟数据。") }}</p>
              <span class="metric-pill warning">{{ $t(gateSignalCopy) }}</span>
            </div>

            <div class="tool-task-detail">
              <div v-if="operatingGatesError" class="inline-status-row danger" data-testid="operating-gates-load-error">
                {{ $t(operatingGatesError) }}
              </div>
              <div v-else class="user-admin-stack operating-gate-grid" data-testid="operating-gates-list">
                <details v-for="item in operatingGates" :key="item.building_code" class="admin-subsection operating-gate-item" :data-testid="`operating-gate-${item.building_code}`">
                  <summary class="operating-gate-summary">
                    <span>{{ $t(item.building_name || item.building_code) }} <small>{{ $t(item.building_code) }}</small></span>
                    <span class="badge info">{{ $t(gateStateLabel(item)) }}</span>
                  </summary>
                  <p class="helper-text">{{ $t("当前原因：") }}{{ item.reason }}</p>
                  <div class="toolbar stacked-mobile">
                    <label class="form-field">
                      <span class="section-label">{{ $t("开放经营大屏展示（含单楼与板块卡片）") }}</span>
                      <input v-model="item.draft_presentation_enabled" type="checkbox" />
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("计入板块合计") }}</span>
                      <input v-model="item.draft_contribution_enabled" type="checkbox" />
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("本次调整原因") }}</span>
                      <input v-model="item.draft_reason" class="input" maxlength="2000" :placeholder="$t(&quot;说明为什么调整&quot;)" />
                    </label>
                    <button
                      class="btn btn-primary"
                      :disabled="!gateChanged(item) || !String(item.draft_reason || '').trim() || Boolean(savingGateCode)"
                      :data-testid="`operating-gate-save-${item.building_code}`"
                      @click="saveOperatingGate(item)"
                    >
                      {{ $t(savingGateCode === item.building_code ? '保存中' : '保存调整') }}
                    </button>
                  </div>
                </details>
                <div v-if="!operatingGates.length" class="inline-status-row">{{ $t("暂无可管理楼宇") }}</div>
              </div>
            </div>
          </article>
        </section>

        <section v-if="isAdmin" class="tool-task-group tool-task-group-access" data-testid="tool-section-access">
          <div class="tool-task-group-head">
            <h3>{{ $t("账号维护") }}</h3>
          </div>

          <article class="tool-task-row tool-card tool-card-users" data-testid="tool-card-users">
            <div class="tool-task-main">
              <h4>{{ $t("账号维护") }}</h4>
              <p>{{ $t("查看账号角色，新建账号或重置密码。") }}</p>
              <span class="metric-pill info">{{ $t(accountSignalCopy) }}</span>
            </div>

            <div class="tool-task-detail">
              <div v-if="usersError" class="inline-status-row danger" data-testid="users-load-error">{{ $t(usersError) }}</div>
              <div class="user-role-list">
                <div v-for="userItem in users" :key="userItem.id" class="user-role-item">
                  <div>
                    <strong>{{ userItem.display_name || userItem.username }}</strong>
                    <span>{{ userItem.username }}</span>
                  </div>
                  <span class="badge info">{{ $t(formatUserRole(userItem.role)) }}</span>
                </div>
              </div>

              <div class="user-admin-stack">
                <div class="admin-subsection">
                  <div class="section-label">{{ $t("新建账号") }}</div>
                  <div class="form-grid">
                    <label class="form-field">
                      <span class="section-label">{{ $t("用户名") }}</span>
                      <input
                        v-model="userForm.username"
                        class="input"
                        required
                        minlength="3"
                        maxlength="32"
                        pattern="[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]"
                        autocomplete="off"
                        spellcheck="false"
                      />
                      <span class="form-field-hint">{{ $t("3—32 位小写字母、数字、点、下划线或短横线，首尾使用字母或数字") }}</span>
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("显示名称") }}</span>
                      <input v-model="userForm.display_name" class="input" maxlength="50" />
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("密码") }}</span>
                      <input v-model="userForm.password" type="password" class="input" autocomplete="new-password" />
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("确认密码") }}</span>
                      <input ref="userConfirmationInput" v-model="userConfirmPassword" type="password" class="input" autocomplete="new-password"
                        :aria-invalid="!!userConfirmationError" :aria-describedby="userConfirmationError ? 'user-confirmation-error' : undefined" />
                      <span v-if="userConfirmationError" id="user-confirmation-error" class="form-error" role="alert">{{ $t(userConfirmationError) }}</span>
                    </label>
                    <label class="form-field">
                      <span class="section-label">{{ $t("角色") }}</span>
                      <select v-model="userForm.role" class="select">
                        <option value="viewer">{{ $t("查看") }}</option>
                        <option value="editor">{{ $t("编辑") }}</option>
                        <option value="admin">{{ $t("管理员") }}</option>
                      </select>
                    </label>
                  </div>
                  <div class="btn-row">
                    <button class="btn btn-primary" @click="createUser">{{ $t("创建账号") }}</button>
                  </div>
                </div>

                <div class="admin-subsection">
                  <div class="section-label">{{ $t("密码重置") }}</div>
                  <div class="toolbar stacked-mobile">
                    <select v-model="pwdForm.user_id" class="select">
                      <option :value="null">{{ $t("选择用户") }}</option>
                      <option v-for="userItem in users" :key="userItem.id" :value="userItem.id">
                        {{ userItem.display_name }}（{{ $t(formatUserRole(userItem.role)) }}）
                      </option>
                    </select>
                    <input v-model="pwdForm.new_password" type="password" class="input" :placeholder="$t(&quot;新密码&quot;)" />
                    <button class="btn" @click="resetPassword">{{ $t("确认重置") }}</button>
                  </div>
                </div>
              </div>
            </div>
          </article>
        </section>
      </div>
    </section>
  </section>
</template>
