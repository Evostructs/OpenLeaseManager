<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, AUTH_EXPIRED_EVENT, AUTH_EXPIRED_MESSAGE, readAuthExpiredBroadcast } from './lib/api'
import SidebarNav from './components/SidebarNav.vue'
import TopBar from './components/TopBar.vue'
import ChangePasswordModal from './components/ChangePasswordModal.vue'
import LoginView from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import ContractsView from './views/ContractsView.vue'
import RoomsView from './views/RoomsView.vue'
import ToolsView from './views/ToolsView.vue'
import IntegrationsView from './views/IntegrationsView.vue'

const sidebarOpen = ref(false)
const activeTab = ref('dashboard')
const currentUser = ref(null)
const needInit = ref(false)
const authBusy = ref(false)
const authError = ref('')
const stats = ref({})
const toasts = ref([])
const passwordModalOpen = ref(false)
const passwordBusy = ref(false)

const navItems = computed(() => {
  const base = [
    { key: 'dashboard', label: '首页概览' },
    { key: 'contracts', label: '合同管理' },
    { key: 'rooms', label: '房间管理' }
  ]
  if (currentUser.value?.role !== 'viewer') {
    base.push({ key: 'receivables', label: '月度应收' })
  }
  if (currentUser.value?.role === 'admin') {
    base.push({ key: 'audit', label: '行为记录' })
    base.push({ key: 'integrations', label: '集成接口' })
  }
  if (currentUser.value?.role !== 'viewer') {
    base.push({ key: 'tools', label: '工具中心' })
  }
  return base
})

const tabTitle = computed(() => {
  switch (activeTab.value) {
    case 'contracts':
      return { title: '合同管理', subtitle: '合同查询、导入导出与 PDF 附件管理。' }
    case 'rooms':
      return { title: '房间管理', subtitle: '房态热力图、空置盘点与房间台账。' }
    case 'receivables':
      return { title: '月度应收', subtitle: '查看租金、物业费与月度应收明细。' }
    case 'audit':
      return { title: '行为记录', subtitle: '查看系统关键操作轨迹与导出日志。' }
    case 'integrations':
      return { title: '集成接口', subtitle: '配置 Webhook、SaaS、MCP 及企业系统预留连接器。' }
    case 'tools':
      return { title: '工具中心', subtitle: '常用业务工具与系统维护入口。' }
    default:
      return { title: '经营概览', subtitle: '租赁运营的关键指标、预警和收入趋势。' }
  }
})

function normalizeToastText(text) {
  if (text == null) return '操作失败'
  if (typeof text === 'string') return text
  if (typeof text === 'object') {
    if (typeof text.message === 'string') return text.message
    if (typeof text.detail === 'string') return text.detail
    try {
      return JSON.stringify(text)
    } catch {
      return '操作失败'
    }
  }
  return String(text)
}

function notify({ text, type = 'info' }) {
  const id = `${Date.now()}-${Math.random()}`
  toasts.value.push({ id, text: normalizeToastText(text), type })
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }, 4000)
}

async function fetchAuthState() {
  try {
    currentUser.value = await api('/api/auth/me', { skipAuthRedirect: true })
    authError.value = ''
  } catch {
    currentUser.value = null
  }
  const status = await api('/api/auth/status')
  needInit.value = status.need_init
}

async function fetchDashboard() {
  if (!currentUser.value) return
  try {
    stats.value = await api('/api/contracts/stats')
  } catch (error) {
    notify({ type: 'error', text: error.message })
  }
}

async function login(payload) {
  authBusy.value = true
  authError.value = ''
  try {
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify(payload), skipAuthRedirect: true })
    await fetchAuthState()
    await fetchDashboard()
    notify({ text: '登录成功' })
  } catch (error) {
    authError.value = error.message
  } finally {
    authBusy.value = false
  }
}

async function register(payload) {
  authBusy.value = true
  authError.value = ''
  try {
    await api('/api/auth/register', { method: 'POST', body: JSON.stringify(payload) })
    notify({ text: '管理员初始化成功，请登录' })
    await fetchAuthState()
  } catch (error) {
    authError.value = error.message
  } finally {
    authBusy.value = false
  }
}

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  currentUser.value = null
  activeTab.value = 'dashboard'
}

async function changeOwnPassword(payload) {
  passwordBusy.value = true
  try {
    const result = await api('/api/auth/change-password', { method: 'POST', body: JSON.stringify(payload) })
    passwordModalOpen.value = false
    notify({ text: result.message || '密码修改成功' })
  } catch (error) {
    notify({ type: 'error', text: error.message })
  } finally {
    passwordBusy.value = false
  }
}

function resetToLogin(message) {
  currentUser.value = null
  activeTab.value = 'dashboard'
  authBusy.value = false
  stats.value = {}
  authError.value = message || AUTH_EXPIRED_MESSAGE
  notify({ type: 'error', text: authError.value })
}

function handleAuthExpired(event) {
  resetToLogin(event.detail?.message || AUTH_EXPIRED_MESSAGE)
}

function handleAuthExpiredStorage(event) {
  const message = readAuthExpiredBroadcast(event)
  if (!message) return
  resetToLogin(message)
}

function selectTab(key) {
  activeTab.value = key
  sidebarOpen.value = false
}

onMounted(async () => {
  window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  window.addEventListener('storage', handleAuthExpiredStorage)
  await fetchAuthState()
  if (currentUser.value) {
    await fetchDashboard()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  window.removeEventListener('storage', handleAuthExpiredStorage)
})
</script>

<template>
  <LoginView
    v-if="!currentUser"
    :need-init="needInit"
    :busy="authBusy"
    :error="authError"
    @login="login"
    @register="register"
  />

  <div v-else class="app-shell">
    <SidebarNav :open="sidebarOpen" :items="navItems" :current-tab="activeTab" @select="selectTab" />
    <button v-if="sidebarOpen" class="sidebar-scrim" aria-label="关闭导航" @click="sidebarOpen = false" />

    <main class="main-area">
      <TopBar
        :title="tabTitle.title"
        :subtitle="tabTitle.subtitle"
        :user="currentUser"
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @change-password="passwordModalOpen = true"
        @logout="logout"
      />

      <DashboardView v-if="activeTab === 'dashboard'" :stats="stats" />
      <ContractsView
        v-else-if="activeTab === 'contracts'"
        :user="currentUser"
        @notify="notify"
        @refresh-dashboard="fetchDashboard"
      />
      <RoomsView v-else-if="activeTab === 'rooms'" :key="'rooms'" :user="currentUser" @notify="notify" />
      <ToolsView
        v-else-if="activeTab === 'receivables'"
        :key="'receivables'"
        section="receivables"
        :user="currentUser"
        @notify="notify"
      />
      <ToolsView
        v-else-if="activeTab === 'audit'"
        :key="'audit'"
        section="audit"
        :user="currentUser"
        @notify="notify"
      />
      <IntegrationsView
        v-else-if="activeTab === 'integrations'"
        :key="'integrations'"
        @notify="notify"
      />
      <ToolsView
        v-else-if="activeTab === 'tools'"
        :key="'tools'"
        section="tools"
        :user="currentUser"
        @notify="notify"
      />
    </main>

    <div class="toast-stack">
      <div v-for="toast in toasts" :key="toast.id" class="toast" :class="{ error: toast.type === 'error' }">
        {{ toast.text }}
      </div>
    </div>

    <ChangePasswordModal
      :open="passwordModalOpen"
      :busy="passwordBusy"
      @close="passwordModalOpen = false"
      @submit="changeOwnPassword"
    />
  </div>
</template>
