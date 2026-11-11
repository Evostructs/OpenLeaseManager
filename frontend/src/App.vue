<script setup>
import { t } from './i18n/index.js'
import { startOperation, trackReadyWork } from './lib/operationTelemetry'
import UnifiedSearch from './components/UnifiedSearch.vue'
import { readViewLink, navigationViewQuery } from './lib/savedViews'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, AUTH_EXPIRED_EVENT, AUTH_EXPIRED_MESSAGE, readAuthExpiredBroadcast } from './lib/api'
import SidebarNav from './components/SidebarNav.vue'
import TopBar from './components/TopBar.vue'
import ChangePasswordModal from './components/ChangePasswordModal.vue'
import ControlHelpPanel from './components/ControlHelpPanel.vue'
import ContextContractDetailModal from './components/ContextContractDetailModal.vue'
import CustomerDetailModal from './components/CustomerDetailModal.vue'
import PageSkeleton from './components/PageSkeleton.vue'
import LoginView from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import UnifiedCockpit from './components/UnifiedCockpit.vue'
import { planningDrafts, planningIdentity } from './lib/planningDrafts.js'
import { confirmPlanningLeaveWithoutEditor } from './lib/planningLeaveGuard.js'
import { helpForTab } from './lib/controlHelp'
import { currentDateValue, currentMonthValue } from './lib/dates'
import { markRouteTransition, startPerformanceTelemetry } from './lib/performanceTelemetry'
import { pathForTab, tabForPath } from './router'
import { cachedApi, invalidateApiCache, prefetchApi } from './lib/prefetchCache'
import {
  WORKSPACE_LEADERSHIP,
  WORKSPACE_OPERATIONS,
  allowedTabsForUser,
  allowedTabsForWorkspace,
  availableWorkspacesForUser,
  overviewRouteQuery,
  portfolioOverviewRequestPath,
  resolveEntryWorkspace,
  resolveWorkspaceTab
} from './lib/navigationPolicy'
import {
  buildAuthorizedPortfolioOverviewOptions,
  buildAuthorizedPropertyOptions,
  getPropertyScopeContext,
  propertyScopeNamespace,
  resetPropertyScopeContext,
  resolveAuthorizedPortfolioOverviewSelection,
  resolveAuthorizedPropertySelection,
  setPropertyScopeContext
} from './lib/propertyScope'
import {
  buildLeadershipPresentationState,
  selectPortfolioOverviewSnapshot
} from './lib/presentationDomain'
import { THEME_CHANGE_EVENT, THEME_STORAGE_KEY, currentTheme } from './lib/theme'
import { readWorkspacePreference, writeWorkspacePreference } from './lib/workspacePreference'

const pageLoaders = {
  overview: () => import('./views/PortfolioOverviewView.vue'),
  operations: () => import('./views/OperationsWorkbenchView.vue'),
  analytics: () => import('./views/OperationsAnalyticsView.vue'),
  evidence: () => import('./views/EvidenceView.vue'),
  exchange: () => import('./views/SnapshotExchangeWorkbenchView.vue'),
  'ai-assist': () => import('./views/AIAssistView.vue'),
  risks: () => import('./views/RiskCenterView.vue'),
  contracts: () => import('./views/ContractsView.vue'),
  customers: () => import('./views/CustomersView.vue'),
  rooms: () => import('./views/RoomsView.vue'),
  receivables: () => import('./views/ReceivablesLedgerView.vue'),
  audit: () => import('./views/AuditLogView.vue'),
  tools: () => import('./views/ToolsCenterView.vue')
}

const primaryRoutePreloadKeys = ['overview', 'operations', 'exchange', 'contracts', 'customers', 'rooms', 'receivables', 'audit', 'tools', 'risks', 'analytics', 'evidence', 'ai-assist']

const tabDataPreloadPaths = {
  contracts: ['/api/contracts?page=1&page_size=25'],
  customers: [
    '/api/customers?page=1&page_size=25',
    '/api/customers/follow-ups/due?limit=8',
    '/api/contracts/stats',
    '/api/dashboard/operations?include_trend=false&queue_limit=8&risk_limit=0',
    '/api/attachments/completeness-tasks?target_type=customer&limit=12'
  ],
  rooms: ['/api/rooms?include_operations=true'],
  receivables: [
    `/api/contracts/receivables/monthly?month=${currentMonthValue()}`,
    `/api/contracts/receivables/invoices?month=${currentMonthValue()}`,
    `/api/contracts/receivables/dunning?month=${currentMonthValue()}&focus=true`
  ],
  risks: ['/api/risks?limit=100', '/api/attachments/completeness-tasks?limit=20']
}
const TAB_DATA_PRELOAD_GRACE_MS = 140
const CHART_RUNTIME_PRELOAD_DELAY_MS = 3500
const PRESENTATION_REFRESH_INTERVAL_MS = 30000
const PRESENTATION_FULLSCREEN_COMMIT_GRACE_MS = 250
const chartRuntimePreloadBlockedTabs = new Set(['rooms', 'risks'])

const loadPage = (loader) => defineAsyncComponent({
  loader: () => trackReadyWork('component', loader),
  loadingComponent: PageSkeleton,
  delay: 120,
  timeout: 30000
})

const OperationsWorkbenchView = loadPage(pageLoaders.operations)
const PortfolioOverviewView = loadPage(pageLoaders.overview)
const OperationsAnalyticsView = loadPage(pageLoaders.analytics)
const EvidenceView = loadPage(pageLoaders.evidence)
const SnapshotExchangeWorkbenchView = loadPage(pageLoaders.exchange)
const AIAssistView = loadPage(pageLoaders['ai-assist'])
const RiskCenterView = loadPage(pageLoaders.risks)
const ContractsView = loadPage(pageLoaders.contracts)
const CustomersView = loadPage(pageLoaders.customers)
const RoomsView = loadPage(pageLoaders.rooms)
const ReceivablesLedgerView = loadPage(pageLoaders.receivables)
const AuditLogView = loadPage(pageLoaders.audit)
const ToolsCenterView = loadPage(pageLoaders.tools)

const route = useRoute()
const router = useRouter()
const sidebarOpen = ref(false)
const activeTab = ref('dashboard')
const activeWorkspace = ref(WORKSPACE_OPERATIONS)
function initialWorkspaceLastTabs() {
  return {
    [WORKSPACE_LEADERSHIP]: 'overview',
    [WORKSPACE_OPERATIONS]: 'dashboard'
  }
}
const workspaceLastTabs = ref(initialWorkspaceLastTabs())
const currentUser = ref(null)
const cockpitRef = ref(null)
watch(() => planningIdentity(currentUser.value), identity => planningDrafts.identify(identity), { immediate: true, flush: 'sync' })
const needInit = ref(false)
const authBusy = ref(false)
const authError = ref('')
const stats = ref({})
const operationsDashboard = ref({})
const operatingSnapshot = ref({})
const selectedPropertyScope = ref({ type: '', code: '' })
const selectedOverviewPortfolio = ref('')
const selectedOverviewBuilding = ref('')
const portfolioOverviewSnapshot = ref({})
const portfolioOverviewLoading = ref(false)
const portfolioOverviewError = ref('')
const scopeRevision = ref(0)
const dashboardLoading = ref(false)
const operationsLoading = ref(false)
const toasts = ref([])
const unifiedSearchOpen = ref(false)
const passwordModalOpen = ref(false)
const isHelpOpen = ref(false)
const theme = ref(currentTheme())
let systemThemeMedia = null

function applyTheme(value, persist = true) {
  theme.value = value
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = value
  }
  if (persist && typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, value)
    } catch {}
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: { theme: value } }))
  }
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

function handleSystemThemeChange(event) {
  let stored = null
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY)
  } catch {}
  if (stored === 'dark' || stored === 'light') return
  applyTheme(event.matches ? 'dark' : 'light', false)
}
const passwordBusy = ref(false)
const customerFocusId = ref(null)
const customerFocusToken = ref(0)
const customerFollowUpPrefill = ref(null)
const customerFollowUpPrefillToken = ref(0)
const contextCustomerOpen = ref(false)
const contextCustomerId = ref(null)
const contextCustomerFollowUpPrefill = ref(null)
const contextCustomerFollowUpPrefillToken = ref(0)
const contextContractOpen = ref(false)
const contextContractId = ref(null)
const contextContractInitialSection = ref('')
const contextActiveDetail = ref(null)
const contextDetailHistory = ref([])
const contextDetailScrollY = ref(0)
const drilldownIntent = ref(null)
const drilldownToken = ref(0)
const tabScrollPositions = ref({})
const authBooting = ref(true)
let routePreloadScheduled = false
let chartRuntimePreloadScheduled = false
const tabDataPreloadPromises = new Map()
let dashboardRequestRevision = 0
let portfolioOverviewRequestRevision = 0
let portfolioOverviewInFlight = null
let authSessionGeneration = 0
const DEFAULT_EVIDENCE_METRIC = 'occupancy.area_rate'

const isLeadershipWorkspace = computed(() => activeWorkspace.value === WORKSPACE_LEADERSHIP)
const allowedTabKeys = computed(() => allowedTabsForWorkspace(
  currentUser.value || {},
  activeWorkspace.value,
  selectedPropertyScope.value
))
const workspaceKeys = computed(() => availableWorkspacesForUser(currentUser.value || {}))
const workspaceOptions = computed(() => workspaceKeys.value.map((value) => (
  value === WORKSPACE_LEADERSHIP
    ? { value, label: '资产组合概况', description: '五栋楼概况 · 只读' }
    : { value, label: '楼宇运营端', description: '按楼宇业务授权' }
)))
const scopeOptions = computed(() => {
  const summary = currentUser.value?.scope || {}
  if (isLeadershipWorkspace.value) return buildAuthorizedPortfolioOverviewOptions(summary)
  return buildAuthorizedPropertyOptions(summary)
})
const selectedScopeValue = computed(() => (
  isLeadershipWorkspace.value && selectedOverviewPortfolio.value
    ? `portfolio_overview:${selectedOverviewPortfolio.value}`
    : selectedPropertyScope.value.type && selectedPropertyScope.value.code
    ? `${selectedPropertyScope.value.type}:${selectedPropertyScope.value.code}`
    : ''
))
const currentScopeLabel = computed(() => (
  scopeOptions.value.find((option) => option.value === selectedScopeValue.value)?.label
  || (isLeadershipWorkspace.value ? '五栋楼概况' : '当前授权楼宇')
))
const selectedBuildingOperationsProfile = computed(() => {
  const summary = currentUser.value?.scope || {}
  const selectedCode = String(selectedPropertyScope.value.code || '').trim().toLowerCase()
  if (selectedPropertyScope.value.type === 'building') {
    const building = (summary.items || []).find(
      (item) => String(item?.code || '').trim().toLowerCase() === selectedCode
    )
    return building?.operations_profile || {}
  }
  if (selectedPropertyScope.value.type !== 'portfolio') return {}

  const portfolio = (summary.portfolio?.items || []).find(
    (item) => String(item?.code || '').trim().toLowerCase() === selectedCode
  )
  const memberCodes = new Set(
    (portfolio?.building_codes || []).map((code) => String(code || '').trim().toLowerCase())
  )
  const memberProfiles = (summary.items || [])
    .filter((item) => memberCodes.has(String(item?.code || '').trim().toLowerCase()))
    .map((item) => item?.operations_profile)
    .filter((profile) => profile?.data_adapter === 'live_detail')
  if (!memberProfiles.length || memberProfiles.length !== memberCodes.size) return {}

  const zonesByName = new Map()
  for (const profile of memberProfiles) {
    for (const zone of profile?.space_structure?.zones || []) {
      const name = String(zone?.name || '').trim()
      if (!name) continue
      const key = name.toLowerCase()
      if (!zonesByName.has(key)) {
        zonesByName.set(key, {
          code: `PORTFOLIO_ZONE_${zonesByName.size + 1}`,
          name,
          kind: String(zone?.kind || '').trim() || 'zone'
        })
      }
    }
  }
  const zones = [...zonesByName.values()]
  if (!zones.length) return {}
  return {
    building_code: `PORTFOLIO:${portfolio?.code || selectedPropertyScope.value.code}`,
    data_adapter: 'live_detail',
    data_granularity: ['building', 'zone', 'leasable_unit'],
    space_structure: { type: 'portfolio_registered_zones', zones },
    analysis_groups: zones.map((zone) => ({
      code: zone.code,
      name: zone.name,
      zone_codes: [zone.code],
      available: true
    })),
    portfolio_read_only: true
  }
})
const sidebarBrand = computed(() => {
  const summary = currentUser.value?.scope || {}
  if (isLeadershipWorkspace.value) {
    return {
      short_name: '组合概况',
      operations_title: currentScopeLabel.value,
      logo_url: null
    }
  }
  if (selectedPropertyScope.value.type === 'building') {
    const selectedCode = String(selectedPropertyScope.value.code || '').trim().toLowerCase()
    const building = (summary.items || []).find(
      (item) => String(item?.code || '').trim().toLowerCase() === selectedCode
    )
    if (building?.operations_profile?.brand) return building.operations_profile.brand
  }
  return {
    short_name: selectedPropertyScope.value.type === 'portfolio' ? '资产组合' : '楼宇',
    operations_title: currentScopeLabel.value,
    logo_url: null
  }
})
const currentWorkspaceLabel = computed(() => (
  workspaceOptions.value.find((option) => option.value === activeWorkspace.value)?.label
  || '楼宇运营端'
))
const presentationActionLabel = computed(() => (
  isLeadershipWorkspace.value
    ? '进入五栋楼大屏'
    : `进入${currentScopeLabel.value}大屏`
))
const scopedPageKey = computed(() => `${scopeRevision.value}:${currentPage.value.key}`)
const isPlatformAdmin = computed(() => (
  Array.isArray(currentUser.value?.scope?.effective_capabilities)
  && currentUser.value.scope.effective_capabilities.includes('platform_admin')
))

function resetModalScrollLock() {
  if (typeof document === 'undefined') return
  document.documentElement.classList.remove('modal-scroll-locked')
  document.body.style.overflow = ''
  if (typeof window !== 'undefined' && window.__leaseManagerModalLockState) {
    window.__leaseManagerModalLockState.count = 0
  }
}

const navGroups = computed(() => {
  if (isLeadershipWorkspace.value) {
    return [{
      key: 'leadership',
      label: '资产组合概况',
      items: [{ key: 'overview', label: '五栋楼概况' }]
    }]
  }

  const groups = [
    {
      key: 'overview',
      label: '楼宇运营',
      items: [
        { key: 'dashboard', label: '今日看板' },
        { key: 'operations', label: '待办队列' },
        { key: 'exchange', label: '文件交换' },
        { key: 'analytics', label: '经营分析' },
        { key: 'evidence', label: '数据依据' }
      ]
    },
    {
      key: 'business',
      label: '业务管理',
      items: [
        { key: 'contracts', label: '合同管理' },
        { key: 'customers', label: '客户管理' },
        { key: 'rooms', label: '房间管理' }
      ]
    },
    {
      key: 'finance-risk',
      label: '财务与风控',
      items: [
        { key: 'receivables', label: '月度应收' },
        { key: 'risks', label: '风险中心' }
      ]
    },
    {
      key: 'ai-assist',
      label: '经营研判',
      items: [
        { key: 'ai-assist', label: '研判台' }
      ]
    }
  ]

  if (isPlatformAdmin.value) {
    groups.push({
      key: 'system',
      label: '管理工具',
      items: [
        { key: 'audit', label: '操作日志' },
        { key: 'tools', label: '工具中心' }
      ]
    })
  }
  const allowed = new Set(allowedTabKeys.value)
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => allowed.has(item.key))
    }))
    .filter((group) => group.items.length)
})

const tabTitle = computed(() => {
  switch (activeTab.value) {
    case 'overview':
      return '五栋楼概况'
    case 'operations':
      return '待办队列'
    case 'analytics':
      return '经营分析'
    case 'exchange':
      return '标准文件交换'
    case 'evidence':
      return '数据依据'
    case 'risks':
      return '风险中心'
    case 'contracts':
      return '合同管理'
    case 'customers':
      return '客户管理'
    case 'rooms':
      return '房间管理'
    case 'receivables':
      return '月度应收'
    case 'ai-assist':
      return '经营研判'
    case 'audit':
      return '操作日志'
    case 'tools':
      return '工具中心'
    default:
      return '今日看板'
  }
})

const currentControlHelp = computed(() => helpForTab(activeTab.value, tabTitle.value))
const contextDetailCloseLabel = computed(() => (contextDetailHistory.value.length ? '返回上一级' : '关闭'))
const currentPage = computed(() => {
  switch (activeTab.value) {
    case 'overview':
      return {
        key: 'overview',
        component: PortfolioOverviewView,
        props: {
          snapshot: portfolioOverviewSnapshot.value,
          buildingCode: selectedOverviewBuilding.value,
          loading: portfolioOverviewLoading.value,
          error: portfolioOverviewError.value
        },
        listeners: {
          'select-building': selectOverviewBuilding,
          'back-overview': clearOverviewBuilding,
          refresh: () => fetchPortfolioOverview({ force: true })
        }
      }
    case 'operations':
      return {
        key: 'operations',
        component: OperationsWorkbenchView,
        props: { operations: operationsDashboard.value, user: currentUser.value },
        listeners: {
          notify,
          'open-contract': openContractDetail,
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
    case 'analytics':
      return {
        key: 'analytics',
        component: OperationsAnalyticsView,
        props: {
          stats: stats.value,
          snapshot: operatingSnapshot.value,
          user: currentUser.value,
          operationsProfile: selectedBuildingOperationsProfile.value
        },
        listeners: {
          notify,
          drilldown: openDrilldown,
          'open-contract': openContractDetail,
          'select-tab': selectTab
        }
      }
    case 'evidence':
      return {
        key: 'evidence',
        component: EvidenceView,
        props: {
          user: currentUser.value,
          metricCode: scopeQueryText(route.query.metric) || DEFAULT_EVIDENCE_METRIC,
          asOfDate: scopeQueryText(route.query.as_of_date) || operatingSnapshot.value?.as_of_date || currentDateValue()
        },
        listeners: {
          notify,
          'change-query': updateEvidenceQuery
        }
      }
    case 'exchange':
      return {
        key: 'exchange',
        component: SnapshotExchangeWorkbenchView,
        props: { user: currentUser.value },
        listeners: { notify }
      }
    case 'risks':
      return {
        key: 'risks',
        component: RiskCenterView,
        props: { user: currentUser.value },
        listeners: {
          notify,
          'open-contract': openContractDetail,
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
    case 'ai-assist':
      return {
        key: 'ai-assist',
        component: AIAssistView,
        props: { user: currentUser.value },
        listeners: {
          notify,
          'open-contract': openContractDetail,
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
    case 'contracts':
      return {
        key: 'contracts',
        component: ContractsView,
        props: {
          user: currentUser.value,
          stats: stats.value,
          operationsProfile: selectedBuildingOperationsProfile.value,
          propertyScope: selectedPropertyScope.value,
          drilldownIntent: drilldownIntent.value,
          drilldownToken: drilldownToken.value
        },
        listeners: {
          notify,
          'refresh-dashboard': () => fetchDashboard({ force: true }),
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
    case 'customers':
      return {
        key: 'customers',
        component: CustomersView,
        props: {
          user: currentUser.value,
          propertyScope: selectedPropertyScope.value,
          focusCustomerId: customerFocusId.value,
          focusToken: customerFocusToken.value,
          focusFollowUpPrefill: customerFollowUpPrefill.value,
          focusFollowUpPrefillToken: customerFollowUpPrefillToken.value,
          drilldownIntent: drilldownIntent.value,
          drilldownToken: drilldownToken.value
        },
        listeners: { notify, 'open-contract': openContractDetail }
      }
    case 'rooms':
      return {
        key: 'rooms',
        component: RoomsView,
        props: {
          user: currentUser.value,
          operationsProfile: selectedBuildingOperationsProfile.value,
          propertyScope: selectedPropertyScope.value,
          drilldownIntent: drilldownIntent.value,
          drilldownToken: drilldownToken.value
        },
        listeners: {
          notify,
          'open-customer': openCustomerDetail,
          'open-contract': openContractDetail
        }
      }
    case 'receivables':
      return {
        key: 'receivables',
        component: ReceivablesLedgerView,
        props: {
          user: currentUser.value,
          propertyScope: selectedPropertyScope.value,
          drilldownIntent: drilldownIntent.value,
          drilldownToken: drilldownToken.value
        },
        listeners: {
          notify,
          'open-contract': openContractDetail,
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
    case 'audit':
      return {
        key: 'audit',
        component: AuditLogView,
        props: { user: currentUser.value },
        listeners: { notify }
      }
    case 'tools':
      return {
        key: 'tools',
        component: ToolsCenterView,
        props: { user: currentUser.value },
        listeners: { notify, 'operating-gate-change': refreshOperatingGateContext }
      }
    default:
      return {
        key: 'dashboard',
        component: DashboardView,
        props: {
          stats: stats.value,
          operations: operationsDashboard.value,
          snapshot: operatingSnapshot.value,
          operationsProfile: selectedBuildingOperationsProfile.value,
          availableTabs: allowedTabKeys.value,
          user: currentUser.value,
          loading: dashboardLoading.value,
          operationsLoading: operationsLoading.value
        },
        listeners: {
          'open-contract': openContractDetail,
          'open-customer': openCustomerDetail,
          'select-tab': selectTab
        }
      }
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

function runWhenIdle(callback) {
  if (typeof window === 'undefined') return
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(callback, { timeout: 1500 })
    return
  }
  window.setTimeout(callback, 160)
}

function scheduleChartRuntimePreload() {
  if (chartRuntimePreloadScheduled || !currentUser.value) return
  chartRuntimePreloadScheduled = true
  runWhenIdle(() => {
    window.setTimeout(() => {
      if (!currentUser.value) return
      if (chartRuntimePreloadBlockedTabs.has(activeTab.value)) {
        chartRuntimePreloadScheduled = false
        return
      }
      runWhenIdle(() => {
        import('./lib/chartRuntime')
          .then(({ preloadChartRuntime }) => preloadChartRuntime())
          .catch(() => {})
      })
    }, CHART_RUNTIME_PRELOAD_DELAY_MS)
  })
}

function schedulePrimaryRoutePreload() {
  if (routePreloadScheduled || !currentUser.value) return
  routePreloadScheduled = true
  const allowed = new Set(allowedTabsForUser(currentUser.value, selectedPropertyScope.value))
  const keys = primaryRoutePreloadKeys.filter((key) => allowed.has(key))

  let index = 0
  const preloadNext = () => {
    const key = keys[index]
    index += 1
    if (!key) {
      scheduleChartRuntimePreload()
      return
    }
    const loader = pageLoaders[key]
    if (!loader) {
      preloadNext()
      return
    }
    loader()
      .catch(() => {})
      .finally(() => {
        runWhenIdle(preloadNext)
      })
  }

  runWhenIdle(preloadNext)
}

function waitForTabDataPreloadGrace() {
  return new Promise((resolve) => {
    window.setTimeout(resolve, TAB_DATA_PRELOAD_GRACE_MS)
  })
}

function preloadNavigationData(key) {
  if (!allowedTabKeys.value.includes(key)) return Promise.resolve()
  const paths = tabDataPreloadPaths[key]
  if (!currentUser.value || !paths?.length) return Promise.resolve()

  const existing = tabDataPreloadPromises.get(key)
  if (existing) return existing

  const request = Promise.allSettled(paths.map((path) => prefetchApi(path)))
    .finally(() => {
      if (tabDataPreloadPromises.get(key) === request) tabDataPreloadPromises.delete(key)
    })
  tabDataPreloadPromises.set(key, request)
  return request
}

async function activateTabWithPreload(key) {
  key = resolveWorkspaceTab(
    currentUser.value || {},
    activeWorkspace.value,
    key,
    selectedPropertyScope.value
  )
  if (!key || activeTab.value === key) {
    sidebarOpen.value = false
    return
  }

  const preload = preloadNavigationData(key)
  if (tabDataPreloadPaths[key]?.length) {
    await Promise.race([preload, waitForTabDataPreloadGrace()]).catch(() => {})
  }
  activateTab(key)
}

// 会话标记：登录成功才置位。启动时无标记直接走未登录路径，
// 避免未登录访问时 /api/auth/me 的 401 控制台资源级报错（JS 无法静默浏览器网络层红字）；
// 标记在但 cookie 已过期时仍会调 /me 并走 catch 清标记，行为正确且此时 401 属真实过期信号。
const HAS_SESSION_KEY = 'lease_has_session'
function readHasSession() {
  try {
    return window.localStorage.getItem(HAS_SESSION_KEY) === '1'
  } catch {
    return false
  }
}
function writeHasSession(value) {
  try {
    if (value) window.localStorage.setItem(HAS_SESSION_KEY, '1')
    else window.localStorage.removeItem(HAS_SESSION_KEY)
  } catch {}
}

function isDefaultEntryRequest() {
  return route.path === '/'
    && !scopeQueryText(route.query.building)
    && !scopeQueryText(route.query.portfolio)
}

function resolveAuthenticatedEntry(user) {
  const requestedTab = tabForPath(route.path)
  const preference = readWorkspacePreference(user)
  const propertyScope = resolveAuthorizedPropertySelection(user?.scope || {}, {
    building: scopeQueryText(route.query.building),
    portfolio: scopeQueryText(route.query.portfolio)
  })
  const entry = resolveEntryWorkspace(user, requestedTab, {
    defaultEntry: isDefaultEntryRequest(),
    ...(preference || {}),
    propertyScope
  })
  return {
    requestedTab,
    entry,
    lastTabs: {
      ...initialWorkspaceLastTabs(),
      ...(preference?.preferredLastTabs || {}),
      [entry.workspace]: entry.tab
    }
  }
}

function persistWorkspacePreference(user = currentUser.value) {
  if (!user) return false
  return writeWorkspacePreference(user, {
    activeWorkspace: activeWorkspace.value,
    lastTabs: workspaceLastTabs.value
  })
}

function replaceWithCleanEntryUrl() {
  return router.replace({ path: pathForTab('dashboard'), query: {}, hash: '' }).catch(() => {})
}

// 前端运行时错误上报：写入操作日志（action=client_error），审计日志页可见；
// 每分钟最多 5 条，避免异常风暴刷爆日志；上报失败静默，绝不影响业务。
let clientErrorReported = 0
let clientErrorWindowStart = 0
function reportClientError(payload) {
  const now = Date.now()
  if (now - clientErrorWindowStart > 60000) {
    clientErrorReported = 0
    clientErrorWindowStart = now
  }
  if (clientErrorReported >= 5) return
  clientErrorReported += 1
  api('/api/audit/client-error', {
    method: 'POST',
    body: JSON.stringify(payload),
    skipAuthRedirect: true
  }).catch(() => {})
}
function onGlobalError(event) {
  if (event.target && event.target !== window) return // 资源加载错误（img/script 等）不上报
  reportClientError({
    message: String(event.message || 'script error').slice(0, 300),
    source: `${event.filename || ''}:${event.lineno || 0}`.slice(0, 200),
    detail: String((event.error && event.error.stack) || '').slice(0, 500)
  })
}
function onUnhandledRejection(event) {
  const reason = event.reason
  reportClientError({
    message: String((reason && reason.message) || reason || 'unhandled rejection').slice(0, 300),
    source: 'unhandledrejection',
    detail: String((reason && reason.stack) || '').slice(0, 500)
  })
}

// 演示驾驶舱模式（会议室大屏）：全屏渲染专用驾驶舱视图 PresentationView
// （16:9 一屏横向栅格 + 深色大屏配色），ESC 或退出浏览器原生全屏时收尾还原。
const presentationMode = ref(false)
const presentationWorkspace = ref('')
let presentationPrevTheme = ''
let presentationRefreshTimer = 0
let presentationRefreshGeneration = 0
let presentationEntryGeneration = 0
let presentationEntryPending = false
let presentationNativeFullscreenCommitted = false
let presentationNativeFullscreenEnteredAt = 0

const leadershipPresentationSnapshot = computed(() => selectPortfolioOverviewSnapshot(
  portfolioOverviewSnapshot.value,
  selectedOverviewBuilding.value
))
const leadershipPresentationState = computed(() => (
  buildLeadershipPresentationState(leadershipPresentationSnapshot.value)
))
const activePresentationSnapshot = computed(() => (
  presentationWorkspace.value === WORKSPACE_LEADERSHIP
    ? leadershipPresentationSnapshot.value
    : operatingSnapshot.value
))
const activePresentationOperationsProfile = computed(() => (
  presentationWorkspace.value === WORKSPACE_LEADERSHIP
    ? activePresentationSnapshot.value?.scope?.operations_profile || {}
    : selectedBuildingOperationsProfile.value
))
const activePresentationStats = computed(() => (
  presentationWorkspace.value === WORKSPACE_LEADERSHIP
    ? leadershipPresentationState.value.stats
    : stats.value
))
const activePresentationOperations = computed(() => (
  presentationWorkspace.value === WORKSPACE_LEADERSHIP
    ? leadershipPresentationState.value.operations
    : operationsDashboard.value
))

function currentScopeAllowsPresentation() {
  const snapshot = operatingSnapshot.value || {}
  if (snapshot?.scope?.scope_type === 'portfolio') return true
  if (snapshot?.scope?.scope_type !== 'building') return false
  const buildingCode = String(
    snapshot?.scope?.building?.code
    || snapshot?.scope?.scope_code
    || snapshot?.scope?.building_codes?.[0]
    || ''
  ).trim().toLowerCase()
  const decisions = Array.isArray(snapshot?.rollout?.items) ? snapshot.rollout.items : []
  const decision = decisions.find((item) => (
    String(item?.building_code || '').trim().toLowerCase() === buildingCode
  ))
  return decision?.presentation_enabled === true
}

async function enterPresentation() {
  if (presentationMode.value || presentationEntryPending) return
  const entryGeneration = ++presentationEntryGeneration
  const targetWorkspace = activeWorkspace.value
  const targetUserId = String(currentUser.value?.id || '')
  presentationEntryPending = true
  try {
    try {
      await document.documentElement.requestFullscreen()
    } catch {}
    if (document.fullscreenElement && !presentationNativeFullscreenEnteredAt) {
      presentationNativeFullscreenEnteredAt = Date.now()
    }

    if (!isPresentationEntryCurrent(entryGeneration, targetWorkspace, targetUserId)) {
      if (!presentationMode.value && !presentationEntryPending) closeNativePresentationFullscreen()
      return
    }

    const stillCurrent = () => (
      isPresentationEntryCurrent(entryGeneration, targetWorkspace, targetUserId)
    )
    const refreshed = targetWorkspace === WORKSPACE_LEADERSHIP
      ? await refreshLeadershipPresentationContext({ stillCurrent })
      : await refreshOperatingGateContext({ stillCurrent })
    if (!isPresentationEntryCurrent(entryGeneration, targetWorkspace, targetUserId)) {
      if (!presentationMode.value && !presentationEntryPending) closeNativePresentationFullscreen()
      return
    }
    if (!refreshed) {
      closeNativePresentationFullscreen()
      return
    }

    if (targetWorkspace === WORKSPACE_LEADERSHIP) {
      selectedOverviewBuilding.value = ''
      syncOverviewRoute({ replace: true })
    } else if (!currentScopeAllowsPresentation()) {
      notify({ type: 'error', text: '该楼宇当前未开放演示大屏，业务页面仍可正常查看' })
      closeNativePresentationFullscreen()
      return
    }

    presentationWorkspace.value = targetWorkspace
    presentationPrevTheme = theme.value
    presentationMode.value = true
    document.body.classList.add('presentation-mode')
    applyTheme(theme.value, false)
    presentationNativeFullscreenCommitted = Boolean(document.fullscreenElement)
    presentationNativeFullscreenEnteredAt = 0
    presentationRefreshGeneration += 1
    schedulePresentationRefresh(presentationRefreshGeneration)
  } finally {
    if (entryGeneration === presentationEntryGeneration) presentationEntryPending = false
  }
}

function isPresentationEntryCurrent(generation, workspace, userId) {
  return generation === presentationEntryGeneration
    && workspace === activeWorkspace.value
    && userId === String(currentUser.value?.id || '')
}

function closeNativePresentationFullscreen() {
  presentationNativeFullscreenCommitted = false
  presentationNativeFullscreenEnteredAt = 0
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
}

function schedulePresentationRefresh(generation) {
  window.clearTimeout(presentationRefreshTimer)
  presentationRefreshTimer = window.setTimeout(
    () => refreshPresentationGate(generation),
    PRESENTATION_REFRESH_INTERVAL_MS
  )
}

async function refreshPresentationGate(generation) {
  if (!presentationMode.value || generation !== presentationRefreshGeneration) return
  const targetWorkspace = presentationWorkspace.value
  const stillCurrent = () => (
    presentationMode.value
    && generation === presentationRefreshGeneration
    && targetWorkspace === presentationWorkspace.value
  )
  // Continue checking session/permissions while hidden; refresh the expensive
  // business snapshot as soon as this tab is visible again.
  const authorizationOnly = document.hidden
  const current = targetWorkspace === WORKSPACE_LEADERSHIP
    ? await refreshLeadershipPresentationContext({ stillCurrent, authorizationOnly })
    : await refreshOperatingGateContext({ stillCurrent, authorizationOnly })
  if (!presentationMode.value || generation !== presentationRefreshGeneration) return
  if (!current) {
    exitPresentation()
    return
  }
  if (
    presentationWorkspace.value === WORKSPACE_OPERATIONS
    && !currentScopeAllowsPresentation()
  ) {
    notify({ type: 'error', text: '该楼宇演示大屏已被关闭，已退出演示模式' })
    exitPresentation()
    return
  }
  schedulePresentationRefresh(generation)
}

function onPresentationVisibilityChange() {
  if (document.hidden || !presentationMode.value) return
  window.clearTimeout(presentationRefreshTimer)
  presentationRefreshGeneration += 1
  void refreshPresentationGate(presentationRefreshGeneration)
}

function exitPresentation() {
  presentationEntryGeneration += 1
  presentationEntryPending = false
  if (!presentationMode.value) {
    closeNativePresentationFullscreen()
    return
  }
  presentationRefreshGeneration += 1
  window.clearTimeout(presentationRefreshTimer)
  presentationRefreshTimer = 0
  presentationMode.value = false
  presentationWorkspace.value = ''
  document.body.classList.remove('presentation-mode')
  const previousTheme = presentationPrevTheme
  presentationPrevTheme = ''
  if (previousTheme) applyTheme(previousTheme, false)
  closeNativePresentationFullscreen()
}

async function requestPresentationLeave(label, options) {
  if (!presentationMode.value || !currentUser.value) return true
  return cockpitRef.value ? cockpitRef.value.requestLeave(label, options) : confirmPlanningLeaveWithoutEditor(options)
}
async function requestPresentationExit() {
  if (await requestPresentationLeave('退出驾驶舱')) exitPresentation()
}

function onPresentationKeydown(event) {
  if (event.key === 'Escape' && (presentationMode.value || presentationEntryPending)) {
    void requestPresentationExit()
  }
}

function onPresentationFullscreenChange() {
  const isFullscreen = Boolean(document.fullscreenElement)
  if (isFullscreen) {
    if (presentationMode.value) presentationNativeFullscreenCommitted = true
    else if (presentationEntryPending) presentationNativeFullscreenEnteredAt = Date.now()
    return
  }
  if (presentationEntryPending && presentationNativeFullscreenEnteredAt) {
    const fullscreenDuration = Date.now() - presentationNativeFullscreenEnteredAt
    presentationNativeFullscreenEnteredAt = 0
    if (fullscreenDuration >= PRESENTATION_FULLSCREEN_COMMIT_GRACE_MS) {
      presentationEntryGeneration += 1
      presentationEntryPending = false
    }
    return
  }
  if (!isFullscreen && presentationNativeFullscreenCommitted && presentationMode.value) {
    presentationNativeFullscreenCommitted = false
    void requestPresentationExit()
  }
}

function scopeQueryText(value) {
  const first = Array.isArray(value) ? value[0] : value
  return first == null ? '' : String(first).trim()
}

function clearPortfolioOverviewState({ resetSelection = false } = {}) {
  portfolioOverviewRequestRevision += 1
  portfolioOverviewSnapshot.value = {}
  portfolioOverviewLoading.value = false
  portfolioOverviewError.value = ''
  selectedOverviewBuilding.value = ''
  if (resetSelection) selectedOverviewPortfolio.value = ''
}

function configurePortfolioOverview(user, { honorRoute = true, syncUrl = false } = {}) {
  const summary = user?.scope || {}
  const requestedPortfolio = honorRoute ? scopeQueryText(route.query.overview_portfolio) : ''
  const selection = resolveAuthorizedPortfolioOverviewSelection(summary, {
    overview_portfolio: requestedPortfolio
  })
  const previousPortfolio = selectedOverviewPortfolio.value
  const nextPortfolio = selection.type === 'portfolio_overview' ? selection.code : ''
  const changed = previousPortfolio !== nextPortfolio
  if (changed) clearPortfolioOverviewState()
  selectedOverviewPortfolio.value = nextPortfolio
  selectedOverviewBuilding.value = honorRoute
    ? scopeQueryText(route.query.overview_building)
    : ''

  if (syncUrl && nextPortfolio) {
    router.replace({
      path: pathForTab('overview'),
      query: overviewRouteQuery(nextPortfolio, selectedOverviewBuilding.value),
      hash: route.hash
    }).catch(() => {})
  }
  return changed
}

function resolvedOverviewBuilding(snapshot, requestedCode) {
  const selected = selectPortfolioOverviewSnapshot(snapshot, requestedCode)
  return selected?.scope?.scope_type === 'building'
    ? scopeQueryText(selected?.scope?.scope_code)
    : ''
}

function syncOverviewRoute({ replace = false } = {}) {
  const portfolioCode = selectedOverviewPortfolio.value
  if (!portfolioCode) return
  const target = {
    path: pathForTab('overview'),
    query: overviewRouteQuery(portfolioCode, selectedOverviewBuilding.value),
    hash: route.hash
  }
  const navigation = replace ? router.replace(target) : router.push(target)
  navigation.catch(() => {})
}

function selectOverviewBuilding(code) {
  const selectedCode = resolvedOverviewBuilding(portfolioOverviewSnapshot.value, code)
  if (!selectedCode) return
  selectedOverviewBuilding.value = selectedCode
  syncOverviewRoute()
}

function clearOverviewBuilding() {
  if (!selectedOverviewBuilding.value) return
  selectedOverviewBuilding.value = ''
  syncOverviewRoute()
}

function chooseOverviewPortfolio(value) {
  const [type, ...parts] = String(value || '').split(':')
  const code = parts.join(':').trim()
  if (type !== 'portfolio_overview' || !code) return
  const selection = resolveAuthorizedPortfolioOverviewSelection(currentUser.value?.scope || {}, {
    overview_portfolio: code
  })
  if (selection.type !== 'portfolio_overview' || !selection.code) return
  if (selection.code === selectedOverviewPortfolio.value && !selectedOverviewBuilding.value) return
  clearPortfolioOverviewState()
  selectedOverviewPortfolio.value = selection.code
  syncOverviewRoute()
}

function chooseCurrentScope(value) {
  if (isLeadershipWorkspace.value) {
    chooseOverviewPortfolio(value)
    return
  }
  choosePropertyScope(value)
}

function fetchPortfolioOverview(options = {}) {
  const user = options.user || currentUser.value
  const portfolioCode = options.portfolioCode || selectedOverviewPortfolio.value
  const owner = JSON.stringify([
    authSessionGeneration, user?.id || '', user?.scope?.authorization_version || '',
    user?.scope?.gate_policy_version || '', portfolioCode
  ])
  // Publishing new permissions also wakes the authorization watcher. Both
  // callers may share this one current request, but never a completed result
  // or an in-flight request from another session, grant version or portfolio.
  if (portfolioOverviewInFlight?.owner === owner
    && portfolioOverviewInFlight.revision === portfolioOverviewRequestRevision) {
    return portfolioOverviewInFlight.promise
  }
  const request = requestPortfolioOverview(options)
  const entry = { owner, revision: portfolioOverviewRequestRevision, promise: null }
  entry.promise = request.finally(() => {
    if (portfolioOverviewInFlight === entry) portfolioOverviewInFlight = null
  })
  portfolioOverviewInFlight = entry
  return entry.promise
}

async function requestPortfolioOverview(options = {}) {
  const user = options.user || currentUser.value
  const portfolioCode = options.portfolioCode || selectedOverviewPortfolio.value
  if (!user || !portfolioCode) {
    portfolioOverviewSnapshot.value = {}
    portfolioOverviewError.value = '当前账号没有可查看的板块概况'
    return false
  }

  const revision = ++portfolioOverviewRequestRevision
  const authorizationVersion = scopeQueryText(user?.scope?.authorization_version)
  const requestOwner = `${user?.id || ''}:${authorizationVersion}:${portfolioCode}`
  portfolioOverviewLoading.value = true
  portfolioOverviewError.value = ''
  try {
    const payload = await api(portfolioOverviewRequestPath(portfolioCode), { includeScope: false })
    const currentOwner = `${user?.id || ''}:${scopeQueryText(user?.scope?.authorization_version)}:${selectedOverviewPortfolio.value}`
    if (revision !== portfolioOverviewRequestRevision || currentOwner !== requestOwner) return false
    if (payload?.contract_version !== 'portfolio-overview-v1') {
      throw new Error('板块概况格式不受支持')
    }
    portfolioOverviewSnapshot.value = payload || {}
    selectedOverviewBuilding.value = resolvedOverviewBuilding(
      portfolioOverviewSnapshot.value,
      selectedOverviewBuilding.value
    )
    if (route.path === pathForTab('overview')) syncOverviewRoute({ replace: true })
    return true
  } catch (error) {
    if (revision !== portfolioOverviewRequestRevision) return false
    portfolioOverviewSnapshot.value = {}
    portfolioOverviewError.value = error.message || '板块概况读取失败'
    return false
  } finally {
    if (revision === portfolioOverviewRequestRevision) portfolioOverviewLoading.value = false
  }
}

function presentationRefreshIsCurrent(options = {}) {
  return typeof options.stillCurrent !== 'function' || options.stillCurrent()
}

function refreshedSessionStillActive(user, sessionGeneration) {
  return sessionGeneration === authSessionGeneration
    && readHasSession()
    && String(currentUser.value?.id || '') === String(user?.id || '')
}

async function refreshLeadershipPresentationContext(options = {}) {
  if (!presentationRefreshIsCurrent(options)) return false
  const sessionGeneration = authSessionGeneration
  try {
    const user = await api('/api/auth/me')
    if (
      !presentationRefreshIsCurrent(options)
      || !refreshedSessionStillActive(user, sessionGeneration)
    ) return false
    if (!availableWorkspacesForUser(user).includes(WORKSPACE_LEADERSHIP)) {
      clearPortfolioOverviewState({ resetSelection: true })
      currentUser.value = user
      if (activeWorkspace.value === WORKSPACE_LEADERSHIP) {
        await changeWorkspace(availableWorkspacesForUser(user)[0] || WORKSPACE_OPERATIONS)
      }
      notify({ type: 'error', text: '当前账号已没有资产组合概况查看权限' })
      return false
    }
    configurePortfolioOverview(user, { honorRoute: true })
    currentUser.value = user
    if (options.authorizationOnly) return true
    const refreshed = await fetchPortfolioOverview({ user, force: true })
    if (
      !presentationRefreshIsCurrent(options)
      || !refreshedSessionStillActive(user, sessionGeneration)
    ) return false
    currentUser.value = user
    return refreshed
  } catch (error) {
    notify({ type: 'error', text: error.message || '五栋楼概况刷新失败，请重试' })
    return false
  }
}

function clearScopeBoundState() {
  dashboardRequestRevision += 1
  stats.value = {}
  operationsDashboard.value = {}
  operatingSnapshot.value = {}
  customerFocusId.value = null
  contextCustomerOpen.value = false
  contextCustomerId.value = null
  contextContractOpen.value = false
  contextContractId.value = null
  contextActiveDetail.value = null
  contextDetailHistory.value = []
  drilldownIntent.value = null
  tabScrollPositions.value = {}
  tabDataPreloadPromises.clear()
  dashboardLoading.value = false
  operationsLoading.value = false
  scopeRevision.value += 1
  resetModalScrollLock()
}

function closeBusinessContextForWorkspaceChange() {
  customerFocusId.value = null
  contextCustomerOpen.value = false
  contextCustomerId.value = null
  contextContractOpen.value = false
  contextContractId.value = null
  contextActiveDetail.value = null
  contextDetailHistory.value = []
  drilldownIntent.value = null
  resetModalScrollLock()
}

async function changeWorkspace(workspace, { force = false } = {}) {
  if (!currentUser.value || workspace === activeWorkspace.value) return
  if (!force && !await requestPresentationLeave('切换工作区')) return
  if (!workspaceKeys.value.includes(workspace)) return

  const previousWorkspace = activeWorkspace.value
  workspaceLastTabs.value = {
    ...workspaceLastTabs.value,
    [previousWorkspace]: activeTab.value
  }
  rememberActiveTabScroll()
  exitPresentation()
  if (workspace === WORKSPACE_LEADERSHIP) closeBusinessContextForWorkspaceChange()

  activeWorkspace.value = workspace
  const targetTab = resolveWorkspaceTab(
    currentUser.value,
    workspace,
    workspaceLastTabs.value[workspace],
    selectedPropertyScope.value
  )
  workspaceLastTabs.value = {
    ...workspaceLastTabs.value,
    [workspace]: targetTab
  }
  persistWorkspacePreference()
  markRouteTransition(targetTab)
  activeTab.value = targetTab
  sidebarOpen.value = false
  restoreTabScroll(targetTab)

  if (workspace === WORKSPACE_LEADERSHIP) {
    configurePortfolioOverview(currentUser.value, { honorRoute: false })
    syncTabToUrl(targetTab)
    if (!portfolioOverviewSnapshot.value?.contract_version) {
      await fetchPortfolioOverview({ force: true })
    }
    return
  }

  configurePropertyScope(currentUser.value, { syncUrl: false })
  syncTabToUrl(targetTab)
  if (!Object.keys(operatingSnapshot.value || {}).length) await fetchDashboard()
}

function configurePropertyScope(user, { syncUrl = true } = {}) {
  const summary = user?.scope || {}
  const requestedBuilding = scopeQueryText(route.query.building)
  const requestedPortfolio = scopeQueryText(route.query.portfolio)
  const { type, code } = resolveAuthorizedPropertySelection(summary, {
    building: requestedBuilding,
    portfolio: requestedPortfolio
  })

  const previous = getPropertyScopeContext()
  if (type && code) {
    setPropertyScopeContext({
      userId: user?.id,
      authorizationVersion: summary.authorization_version,
      gatePolicyVersion: summary.gate_policy_version,
      scopeType: type,
      scopeCode: code,
      metricDefinition: 'OPS_V1'
    })
  } else {
    resetPropertyScopeContext()
  }
  selectedPropertyScope.value = { type, code }
  const current = getPropertyScopeContext()
  const changed = JSON.stringify(previous) !== JSON.stringify(current)
  if (changed) clearScopeBoundState()

  if (syncUrl && type && code) {
    const nextQuery = { ...route.query }
    if (type === 'building') {
      nextQuery.building = code
      delete nextQuery.portfolio
    } else {
      nextQuery.portfolio = code
      delete nextQuery.building
    }
    if (
      scopeQueryText(route.query.building) !== scopeQueryText(nextQuery.building)
      || scopeQueryText(route.query.portfolio) !== scopeQueryText(nextQuery.portfolio)
    ) {
      router.replace({ path: route.path, query: nextQuery, hash: route.hash }).catch(() => {})
    }
  }
  return changed
}

function choosePropertyScope(value) {
  const [type, ...parts] = String(value || '').split(':')
  const code = parts.join(':').trim()
  if (!['building', 'portfolio'].includes(type) || !code) return
  if (type === selectedPropertyScope.value.type && code === selectedPropertyScope.value.code) return
  const timing = startOperation('building-switch', activeTab.value)
  const query = navigationViewQuery(route.query, route.path, route.path, { type, code })
  if (type === 'building') {
    query.building = code
    delete query.portfolio
  } else {
    query.portfolio = code
    delete query.building
  }
  router.push({ path: route.path, query, hash: route.hash }).then(result => timing.finish(result ? 'superseded' : 'ok'), () => timing.finish('error'))
}

function openOperationsPresentationBuilding(code) {
  if (!code) return
  choosePropertyScope(`building:${code}`)
}

function handlePresentationBuilding(code) {
  if (presentationWorkspace.value === WORKSPACE_LEADERSHIP) {
    selectOverviewBuilding(code)
    return
  }
  openOperationsPresentationBuilding(code)
}

function handlePresentationBackOverview() {
  if (presentationWorkspace.value !== WORKSPACE_LEADERSHIP) return
  clearOverviewBuilding()
}

function openPresentationTarget(payload = {}) {
  const tab = typeof payload === 'string' ? payload : payload?.tab
  const buildingCode = typeof payload === 'object' ? payload?.buildingCode : ''
  const targetScope = buildingCode
    ? { type: 'building', code: buildingCode }
    : selectedPropertyScope.value
  if (!tab) return
  if (
    presentationWorkspace.value !== WORKSPACE_OPERATIONS
    || resolveWorkspaceTab(
      currentUser.value || {},
      WORKSPACE_OPERATIONS,
      tab,
      targetScope
    ) !== tab
  ) return

  exitPresentation()
  commitTabSelection(tab, WORKSPACE_OPERATIONS)

  const query = { ...route.query }
  if (tab === 'evidence') {
    query.metric = scopeQueryText(payload?.metric) || DEFAULT_EVIDENCE_METRIC
    query.as_of_date = scopeQueryText(payload?.asOfDate) || operatingSnapshot.value?.as_of_date || currentDateValue()
  } else {
    delete query.metric
    delete query.as_of_date
  }
  if (buildingCode) {
    query.building = buildingCode
    delete query.portfolio
  }
  router.push({ path: pathForTab(tab), query, hash: route.hash }).catch(() => {})
}

function updateEvidenceQuery(payload = {}) {
  const query = { ...route.query }
  query.metric = scopeQueryText(payload.metricCode) || DEFAULT_EVIDENCE_METRIC
  query.as_of_date = scopeQueryText(payload.asOfDate) || operatingSnapshot.value?.as_of_date || currentDateValue()
  router.replace({ path: pathForTab('evidence'), query, hash: route.hash }).catch(() => {})
}

function commitTabSelection(key, workspace = activeWorkspace.value) {
  if (activeTab.value !== key) {
    markRouteTransition(key)
    rememberActiveTabScroll()
    activeTab.value = key
    restoreTabScroll(key)
    scheduleChartRuntimePreload()
  }
  workspaceLastTabs.value = {
    ...workspaceLastTabs.value,
    [workspace]: key
  }
  persistWorkspacePreference()
  sidebarOpen.value = false
}

async function fetchAuthState(options = {}) {
  const { deferUser = false } = options
  let user = null
  if (readHasSession()) {
    try {
      user = await api('/api/auth/me', { skipAuthRedirect: true })
      const { requestedTab, entry, lastTabs } = resolveAuthenticatedEntry(user)
      activeWorkspace.value = entry.workspace
      workspaceLastTabs.value = lastTabs
      configurePropertyScope(user, {
        syncUrl: !deferUser && entry.workspace === WORKSPACE_OPERATIONS
      })
      configurePortfolioOverview(user, {
        honorRoute: requestedTab === 'overview' && entry.workspace === WORKSPACE_LEADERSHIP
      })
      if (!deferUser) currentUser.value = user
      authError.value = ''
    } catch {
      authSessionGeneration += 1
      writeHasSession(false)
      resetPropertyScopeContext()
      selectedPropertyScope.value = { type: '', code: '' }
      clearScopeBoundState()
      clearPortfolioOverviewState({ resetSelection: true })
      if (!deferUser) currentUser.value = null
      replaceWithCleanEntryUrl()
    }
  }
  const status = await api('/api/auth/status')
  needInit.value = status.need_init
  return user
}

async function fetchDashboard(options = {}) {
  if (!currentUser.value && !options.allowWithoutUser) return
  const requestRevision = ++dashboardRequestRevision
  const requestScopeNamespace = propertyScopeNamespace()
  const ownsRequest = () => (
    requestRevision === dashboardRequestRevision
    && requestScopeNamespace === propertyScopeNamespace()
  )
  const isCurrentRequest = () => ownsRequest() && presentationRefreshIsCurrent(options)
  dashboardLoading.value = true
  operationsLoading.value = true
  let loaded = false
  const snapshotRequest = cachedApi('/api/operations/snapshot?include_trend=true&queue_limit=3&risk_limit=3', {
    force: !!options.force
  })
    .then((snapshot) => {
      if (!isCurrentRequest()) return
      operatingSnapshot.value = snapshot || {}
      stats.value = snapshot?.legacy_contract_stats || {}
      operationsDashboard.value = snapshot?.legacy_operations || {}
      loaded = true
    })
    .catch((error) => {
      if (!isCurrentRequest()) return
      operatingSnapshot.value = {}
      stats.value = {}
      operationsDashboard.value = {}
      notify({ type: 'error', text: error.message })
    })
    .finally(() => {
      if (!ownsRequest()) return
      dashboardLoading.value = false
      operationsLoading.value = false
    })
  await snapshotRequest.catch(() => {})
  return loaded
}

async function refreshOperatingGateContext(options = {}) {
  if (!presentationRefreshIsCurrent(options)) return false
  const sessionGeneration = authSessionGeneration
  try {
    invalidateApiCache('/api/buildings')
    invalidateApiCache('/api/operations/snapshot')
    invalidateApiCache('/api/operations/snapshot-evidence')
    const user = await api('/api/auth/me')
    if (
      !presentationRefreshIsCurrent(options)
      || !refreshedSessionStillActive(user, sessionGeneration)
    ) return false
    if (!availableWorkspacesForUser(user).includes(WORKSPACE_OPERATIONS)) {
      resetPropertyScopeContext()
      selectedPropertyScope.value = { type: '', code: '' }
      clearScopeBoundState()
      currentUser.value = user
      if (activeWorkspace.value === WORKSPACE_OPERATIONS) {
        await changeWorkspace(availableWorkspacesForUser(user)[0] || WORKSPACE_LEADERSHIP)
      }
      notify({ type: 'error', text: '当前账号已没有楼宇运营端查看权限' })
      return false
    }
    configurePropertyScope(user)
    currentUser.value = user
    if (options.authorizationOnly) return true
    const refreshed = await fetchDashboard({ force: true, stillCurrent: options.stillCurrent })
    if (
      !presentationRefreshIsCurrent(options)
      || !refreshedSessionStillActive(user, sessionGeneration)
    ) return false
    currentUser.value = user
    return refreshed
  } catch (error) {
    notify({ type: 'error', text: error.message || '楼宇开放状态刷新失败，请重试' })
    return false
  }
}

async function enterAuthenticatedApplication(user) {
  const { requestedTab, entry, lastTabs } = resolveAuthenticatedEntry(user)
  const targetTab = entry.tab
  activeWorkspace.value = entry.workspace
  workspaceLastTabs.value = {
    ...lastTabs,
    [entry.workspace]: targetTab
  }
  if (targetTab === 'dashboard') markRouteTransition('dashboard')
  else markRouteTransition(targetTab)
  activeTab.value = targetTab

  if (targetTab === 'overview') {
    configurePortfolioOverview(user, {
      honorRoute: requestedTab === 'overview'
    })
    await fetchPortfolioOverview({ user })
  } else {
    await fetchDashboard({ allowWithoutUser: true })
  }

  currentUser.value = user
  persistWorkspacePreference(user)
  syncTabToUrl(targetTab, {
    replace: targetTab !== requestedTab || targetTab === 'overview'
  })
  schedulePrimaryRoutePreload()
}

async function login(payload) {
  const timing = startOperation('login', tabForPath(route.path))
  let outcome = 'error'
  authBusy.value = true
  authError.value = ''
  try {
    resetPropertyScopeContext()
    selectedPropertyScope.value = { type: '', code: '' }
    clearScopeBoundState()
    clearPortfolioOverviewState({ resetSelection: true })
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify(payload), skipAuthRedirect: true })
    // 登录接口成功即证明会话已建立；先置标记再拉用户，
    // 否则 fetchAuthState 的标记门控会跳过 /me 拿不到 user（登出后再登录死锁）
    authSessionGeneration += 1
    writeHasSession(true)
    const user = await fetchAuthState({ deferUser: true })
    if (user) {
      await enterAuthenticatedApplication(user)
      schedulePrimaryRoutePreload()
    }
    outcome = 'ok'
    notify({ text: '登录成功' })
  } catch (error) {
    authError.value = error.message
  } finally {
    authBusy.value = false
    timing.finish(outcome)
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
  const parked = planningDrafts.has(planningIdentity(currentUser.value))
  if (presentationMode.value) {
    if (!await requestPresentationLeave('退出登录', { allowSave: false, force: parked })) return
  } else if (parked && !window.confirm(t('退出登录会清除本页所有暂存财务草稿。确定放弃草稿并退出吗？'))) return
  authBooting.value = true
  authSessionGeneration += 1
  exitPresentation()
  try {
    await api('/api/auth/logout', { method: 'POST' })
    resetPropertyScopeContext()
    selectedPropertyScope.value = { type: '', code: '' }
    clearScopeBoundState()
    clearPortfolioOverviewState({ resetSelection: true })
    currentUser.value = null
    writeHasSession(false)
    activeWorkspace.value = WORKSPACE_OPERATIONS
    workspaceLastTabs.value = initialWorkspaceLastTabs()
    activeTab.value = 'dashboard'
    await replaceWithCleanEntryUrl()
    tabScrollPositions.value = {}
    routePreloadScheduled = false
    chartRuntimePreloadScheduled = false
    tabDataPreloadPromises.clear()
    operatingSnapshot.value = {}
    portfolioOverviewSnapshot.value = {}
    stats.value = {}
    operationsDashboard.value = {}
    isHelpOpen.value = false
  } finally {
    authBooting.value = false
  }
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
  authSessionGeneration += 1
  exitPresentation()
  resetPropertyScopeContext()
  selectedPropertyScope.value = { type: '', code: '' }
  clearScopeBoundState()
  clearPortfolioOverviewState({ resetSelection: true })
  currentUser.value = null
  writeHasSession(false)
  activeWorkspace.value = WORKSPACE_OPERATIONS
  workspaceLastTabs.value = initialWorkspaceLastTabs()
  activeTab.value = 'dashboard'
  replaceWithCleanEntryUrl()
  isHelpOpen.value = false
  authBusy.value = false
  stats.value = {}
  operationsDashboard.value = {}
  operatingSnapshot.value = {}
  portfolioOverviewSnapshot.value = {}
  tabScrollPositions.value = {}
  routePreloadScheduled = false
  chartRuntimePreloadScheduled = false
  tabDataPreloadPromises.clear()
  dashboardLoading.value = false
  operationsLoading.value = false
  authError.value = message || AUTH_EXPIRED_MESSAGE
  notify({ type: 'error', text: authError.value })
}

function handleAuthExpired(event) {
  resetToLogin(event.detail?.message || AUTH_EXPIRED_MESSAGE)
}

function handleAuthExpiredStorage(event) {
  if (
    event.key === HAS_SESSION_KEY
    && event.oldValue === '1'
    && event.newValue === null
  ) {
    if (currentUser.value) resetToLogin('已在其他页面退出，请重新登录')
    return
  }
  const message = readAuthExpiredBroadcast(event)
  if (!message) return
  resetToLogin(message)
}

function rememberActiveTabScroll() {
  if (typeof window === 'undefined') return
  tabScrollPositions.value = {
    ...tabScrollPositions.value,
    [activeTab.value]: window.scrollY || 0
  }
}

function trackActiveTabScroll() {
  if (typeof window === 'undefined' || !currentUser.value) return
  tabScrollPositions.value = {
    ...tabScrollPositions.value,
    [activeTab.value]: window.scrollY || 0
  }
}

function restoreTabScroll(key) {
  if (typeof window === 'undefined') return
  const top = tabScrollPositions.value[key] || 0
  nextTick(() => {
    const restore = () => window.scrollTo({ top, left: 0, behavior: 'auto' })
    const restoreIfUnchanged = () => {
      if (Math.abs((window.scrollY || 0) - top) <= 4) restore()
    }
    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(() => {
        restore()
        window.requestAnimationFrame(restoreIfUnchanged)
      })
      return
    }
    restore()
  })
}

function activateTab(key) {
  key = resolveWorkspaceTab(
    currentUser.value || {},
    activeWorkspace.value,
    key,
    selectedPropertyScope.value
  )
  if (!key) return
  commitTabSelection(key)
  syncTabToUrl(key)
}

// URL ↔ 页签同步：切页签时写 URL；浏览器前进/后退或深链接时由下方 watch 回切页签
function syncTabToUrl(key, { replace = false } = {}) {
  const path = pathForTab(key)
  let query
  if (key === 'overview') {
    query = overviewRouteQuery(selectedOverviewPortfolio.value, selectedOverviewBuilding.value)
  } else {
    query = navigationViewQuery(route.query, route.path, path, selectedPropertyScope.value)
    delete query.overview_portfolio
    delete query.overview_building
    delete query.building
    delete query.portfolio
    if (selectedPropertyScope.value.type === 'building' && selectedPropertyScope.value.code) {
      query.building = selectedPropertyScope.value.code
    } else if (selectedPropertyScope.value.type === 'portfolio' && selectedPropertyScope.value.code) {
      query.portfolio = selectedPropertyScope.value.code
    }
    if (key === 'evidence') {
      query.metric = scopeQueryText(query.metric) || DEFAULT_EVIDENCE_METRIC
      query.as_of_date = scopeQueryText(query.as_of_date) || operatingSnapshot.value?.as_of_date || currentDateValue()
    } else {
      delete query.metric
      delete query.as_of_date
    }
  }
  const queryChanged = JSON.stringify(route.query) !== JSON.stringify(query)
  if (route.path !== path || queryChanged) {
    if (replace) router.replace({ path, query, hash: route.hash }).catch(() => {})
    else router.push({ path, query, hash: route.hash }).catch(() => {})
  }
}

watch(
  () => route.path,
  async (path) => {
    if (!currentUser.value) return
    const requested = tabForPath(path)
    const propertyScope = resolveAuthorizedPropertySelection(currentUser.value?.scope || {}, {
      building: scopeQueryText(route.query.building),
      portfolio: scopeQueryText(route.query.portfolio)
    })
    const entry = resolveEntryWorkspace(currentUser.value, requested, { propertyScope })
    if (entry.workspace !== activeWorkspace.value) {
      exitPresentation()
      activeWorkspace.value = entry.workspace
      if (entry.workspace === WORKSPACE_LEADERSHIP) closeBusinessContextForWorkspaceChange()
    }
    if (entry.workspace === WORKSPACE_OPERATIONS) {
      configurePropertyScope(currentUser.value, { syncUrl: false })
    }
    if (entry.tab !== requested) {
      // Commit the safe target before any await.  Otherwise the old detail
      // component can remount under the newly selected snapshot-only scope.
      if (entry.tab !== activeTab.value) commitTabSelection(entry.tab)
      syncTabToUrl(entry.tab, { replace: true })
      if (!Object.keys(operatingSnapshot.value || {}).length) await fetchDashboard()
      return
    }
    if (entry.tab !== activeTab.value) await selectTab(entry.tab, { fromRoute: true })
  }
)

watch(
  () => [scopeQueryText(route.query.building), scopeQueryText(route.query.portfolio)],
  async () => {
    if (!currentUser.value || route.path === pathForTab('overview')) return
    const changed = configurePropertyScope(currentUser.value)
    const target = resolveWorkspaceTab(
      currentUser.value,
      WORKSPACE_OPERATIONS,
      activeTab.value,
      selectedPropertyScope.value
    )
    if (target !== activeTab.value) {
      exitPresentation()
      // Scope-bound state was just cleared.  Switch away from a detail page
      // synchronously before the new scope can trigger component requests.
      commitTabSelection(target)
      syncTabToUrl(target, { replace: true })
      if (!Object.keys(operatingSnapshot.value || {}).length) await fetchDashboard()
      return
    }
    if (changed) await fetchDashboard({ force: true })
  }
)

watch(
  () => [
    scopeQueryText(route.query.overview_portfolio),
    scopeQueryText(route.query.overview_building),
    scopeQueryText(route.query.building),
    scopeQueryText(route.query.portfolio),
    scopeQueryText(route.query.metric),
    scopeQueryText(route.query.as_of_date)
  ],
  async () => {
    if (!currentUser.value || route.path !== pathForTab('overview')) return
    const changed = configurePortfolioOverview(currentUser.value, { honorRoute: true })
    if (changed || !portfolioOverviewSnapshot.value?.contract_version) {
      await fetchPortfolioOverview({ force: true })
      return
    }
    const validatedBuilding = resolvedOverviewBuilding(
      portfolioOverviewSnapshot.value,
      selectedOverviewBuilding.value
    )
    if (validatedBuilding !== selectedOverviewBuilding.value) {
      selectedOverviewBuilding.value = validatedBuilding
    }
    syncOverviewRoute({ replace: true })
  }
)

// Observe the authorization fields individually. /auth/me returns a new user
// object even when these values are unchanged; that must not start a competing
// snapshot request and invalidate a presentation entry already in progress.
watch(
  [
    () => currentUser.value?.id || '',
    () => currentUser.value?.scope?.authorization_version || '',
    () => currentUser.value?.scope?.gate_policy_version || ''
  ],
  async (_current, previous = []) => {
    if (!currentUser.value) return
    if (!previous[0]) return
    if (document.hidden && presentationMode.value) {
      configurePropertyScope(currentUser.value, { syncUrl: false })
      configurePortfolioOverview(currentUser.value, { honorRoute: true })
      return
    }
    const available = availableWorkspacesForUser(currentUser.value)
    if (!available.includes(activeWorkspace.value)) {
      await changeWorkspace(available[0] || WORKSPACE_OPERATIONS, { force: true })
      return
    }
    if (isLeadershipWorkspace.value) {
      configurePropertyScope(currentUser.value, { syncUrl: false })
      configurePortfolioOverview(currentUser.value, { honorRoute: true })
      await fetchPortfolioOverview({ force: true })
      return
    }
    const changed = configurePropertyScope(currentUser.value)
    if (changed) await fetchDashboard({ force: true })
  }
)

async function selectTab(key, { fromRoute = false } = {}) {
  if (!fromRoute && !await requestPresentationLeave('切换页面')) return
  drilldownIntent.value = null
  const target = resolveWorkspaceTab(
    currentUser.value || {},
    activeWorkspace.value,
    key,
    selectedPropertyScope.value
  )
  if (target === 'overview') {
    const changed = configurePortfolioOverview(currentUser.value, { honorRoute: fromRoute })
    if (changed || !portfolioOverviewSnapshot.value?.contract_version) {
      await fetchPortfolioOverview({ force: true })
    }
  } else if (!Object.keys(operatingSnapshot.value || {}).length) {
    await fetchDashboard()
  }
  workspaceLastTabs.value = {
    ...workspaceLastTabs.value,
    [activeWorkspace.value]: target
  }
  await activateTabWithPreload(target)
}

function openSearchResult({ type, item }) {
  if (type === 'contract' && allowedTabKeys.value.includes('contracts')) openContractDetail(item)
  else if (type === 'customer' && allowedTabKeys.value.includes('customers')) openCustomerDetail(item)
  else if (type === 'room' && allowedTabKeys.value.includes('rooms')) openDrilldown({ target: 'rooms', filters: { keyword: item.room_no, tower: item.tower }, message: '已定位所选房源' })
}

let appliedViewLink = ''
watch(() => [route.fullPath, currentUser.value?.id, authBooting.value, selectedScopeValue.value, allowedTabKeys.value.join(',')], async () => {
  if (!currentUser.value || authBooting.value) { appliedViewLink = ''; return }
  await nextTick()
  const requestedScope = scopeQueryText(route.query[selectedPropertyScope.value.type])
  if (!requestedScope || requestedScope !== selectedPropertyScope.value.code || isLeadershipWorkspace.value) return
  const target = tabForPath(route.path)
  if (!allowedTabKeys.value.includes(target)) return
  const key = `${currentUser.value.id}:${route.fullPath}:${selectedScopeValue.value}`
  if (appliedViewLink === key) return
  appliedViewLink = key
  const context = readViewLink(route.query)
  if (Object.keys(context.filters).length && ['contracts','receivables','rooms'].includes(target)) {
    openDrilldown({ target, filters: context.filters, message: '已恢复链接中的筛选条件' })
  }
  if (context.detail?.type === 'contract' && allowedTabKeys.value.includes('contracts')) openContractDetail(context.detail.id)
  else if (context.detail?.type === 'customer' && allowedTabKeys.value.includes('customers')) openCustomerDetail(context.detail.id)
}, { flush: 'post' })

function openDrilldown(intent) {
  if (!intent?.target) return
  drilldownIntent.value = { ...intent }
  drilldownToken.value += 1
  activateTab(intent.target)
}

function currentContextDetail() {
  if (contextActiveDetail.value === 'customer' && contextCustomerOpen.value && contextCustomerId.value) {
    return {
      type: 'customer',
      customerId: contextCustomerId.value,
      followUpPrefill: contextCustomerFollowUpPrefill.value,
      followUpPrefillToken: contextCustomerFollowUpPrefillToken.value
    }
  }
  if (contextActiveDetail.value === 'contract' && contextContractOpen.value && contextContractId.value) {
    return {
      type: 'contract',
      contractId: contextContractId.value,
      initialSection: contextContractInitialSection.value
    }
  }
  return null
}

function restoreContextDetail(entry) {
  if (!entry) return
  if (entry.type === 'customer') {
    contextCustomerId.value = entry.customerId
    contextCustomerFollowUpPrefill.value = entry.followUpPrefill || null
    contextCustomerFollowUpPrefillToken.value = entry.followUpPrefillToken || 0
    contextCustomerOpen.value = true
    contextActiveDetail.value = 'customer'
    return
  }
  if (entry.type === 'contract') {
    contextContractId.value = entry.contractId
    contextContractInitialSection.value = entry.initialSection || ''
    contextContractOpen.value = true
    contextActiveDetail.value = 'contract'
  }
}

function rememberContextDetailScroll() {
  if (typeof window === 'undefined') return
  contextDetailScrollY.value = window.scrollY || 0
}

let contextDetailScrollRestoreToken = 0

function cancelContextDetailScrollRestore() {
  contextDetailScrollRestoreToken += 1
}

function restoreContextDetailScroll() {
  if (typeof window === 'undefined') return
  const token = ++contextDetailScrollRestoreToken
  const top = contextDetailScrollY.value || 0
  const restore = () => {
    if (token !== contextDetailScrollRestoreToken || currentContextDetail()) return
    window.scrollTo({ top, left: 0, behavior: 'auto' })
  }
  if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) {
    document.activeElement.blur()
  }
  restore()
  nextTick(() => {
    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(restore)
      return
    }
    restore()
  })
}

function closeContextDetail() {
  const closingType = contextActiveDetail.value
  if (closingType === 'customer') {
    contextCustomerOpen.value = false
  } else if (closingType === 'contract') {
    contextContractOpen.value = false
  }

  const previous = contextDetailHistory.value.pop()
  if (previous) {
    restoreContextDetail(previous)
    return
  }

  contextActiveDetail.value = null
  contextCustomerOpen.value = false
  contextContractOpen.value = false
  if (!unifiedSearchOpen.value) restoreContextDetailScroll()
}

function openCustomerDetail(customer) {
  const customerId = typeof customer === 'object' ? (customer?.id || customer?.customer_id) : customer
  if (!customerId) return
  cancelContextDetailScrollRestore()
  const currentDetail = currentContextDetail()
  if (!currentDetail) rememberContextDetailScroll()
  if (currentDetail && currentDetail.type !== 'customer') contextDetailHistory.value.push(currentDetail)
  const followUpPrefill = typeof customer === 'object' ? customer?.followUpPrefill : null
  contextCustomerId.value = customerId
  contextCustomerOpen.value = true
  contextActiveDetail.value = 'customer'
  if (followUpPrefill) {
    contextCustomerFollowUpPrefill.value = followUpPrefill
    contextCustomerFollowUpPrefillToken.value += 1
  } else {
    contextCustomerFollowUpPrefill.value = null
  }
  sidebarOpen.value = false
}

function openContractDetail(contract) {
  const contractId = typeof contract === 'object' ? (contract?.id || contract?.contract_id) : contract
  if (!contractId) return
  cancelContextDetailScrollRestore()
  const currentDetail = currentContextDetail()
  if (!currentDetail) rememberContextDetailScroll()
  if (currentDetail && currentDetail.type !== 'contract') contextDetailHistory.value.push(currentDetail)
  contextContractInitialSection.value = typeof contract === 'object' ? (contract?.initialSection || '') : ''
  contextContractId.value = contractId
  contextContractOpen.value = true
  contextActiveDetail.value = 'contract'
  sidebarOpen.value = false
}

const removePlanningRouteGuard = router.beforeEach(async (to, from) => {
  if (to.fullPath === from.fullPath) return true
  return requestPresentationLeave('离开当前页面')
})

onMounted(async () => {
  resetModalScrollLock()
  startPerformanceTelemetry()
  window.addEventListener('error', onGlobalError)
  window.addEventListener('unhandledrejection', onUnhandledRejection)
  window.addEventListener('keydown', onPresentationKeydown)
  document.addEventListener('fullscreenchange', onPresentationFullscreenChange)
  document.addEventListener('visibilitychange', onPresentationVisibilityChange)
  window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  window.addEventListener('storage', handleAuthExpiredStorage)
  window.addEventListener('scroll', trackActiveTabScroll, { passive: true })
  if (typeof window.matchMedia === 'function') {
    systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)')
    if (typeof systemThemeMedia.addEventListener === 'function') {
      systemThemeMedia.addEventListener('change', handleSystemThemeChange)
    }
  }
  const timing = startOperation('session-restore', tabForPath(route.path))
  const user = await fetchAuthState({ deferUser: true })
  if (user) await enterAuthenticatedApplication(user)
  authBooting.value = false
  timing.finish(user ? 'ok' : 'superseded')
})

onBeforeUnmount(() => {
  removePlanningRouteGuard()
  presentationRefreshGeneration += 1
  window.clearTimeout(presentationRefreshTimer)
  document.removeEventListener('visibilitychange', onPresentationVisibilityChange)
  window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  window.removeEventListener('storage', handleAuthExpiredStorage)
  window.removeEventListener('scroll', trackActiveTabScroll)
  if (systemThemeMedia && typeof systemThemeMedia.removeEventListener === 'function') {
    systemThemeMedia.removeEventListener('change', handleSystemThemeChange)
  }
})
</script>

<template>
  <PageSkeleton v-if="authBooting" />

  <LoginView
    v-else-if="!currentUser"
    :need-init="needInit"
    :busy="authBusy"
    :error="authError"
    @login="login"
    @register="register"
  />

  <div v-else class="app-shell">
    <SidebarNav
      :open="sidebarOpen"
      :groups="navGroups"
      :current-tab="activeTab"
      :workspace-label="currentWorkspaceLabel"
      :scope-label="currentScopeLabel"
      :brand="sidebarBrand"
      @select="selectTab"
      @preload="preloadNavigationData"
    />
    <button v-if="sidebarOpen" class="sidebar-scrim" :aria-label="$t(&quot;关闭导航&quot;)" @click="sidebarOpen = false" />

    <main class="main-area">
      <TopBar
        :title="$t(tabTitle)"
        :show-search="!isLeadershipWorkspace && allowedTabKeys.some(key => ['contracts','customers','rooms'].includes(key))"
        @open-search="unifiedSearchOpen = true"
        :user="currentUser"
        :theme="theme"
        :scope-options="scopeOptions"
        :scope-value="selectedScopeValue"
        :scope-label="currentScopeLabel"
        :workspace-options="workspaceOptions"
        :workspace-value="activeWorkspace"
        :presentation-label="presentationActionLabel"
        show-presentation
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @change-workspace="changeWorkspace"
        @change-password="passwordModalOpen = true"
        @open-help="isHelpOpen = true"
        @toggle-theme="toggleTheme"
        @presentation="enterPresentation"
        @change-scope="chooseCurrentScope"
        @logout="logout"
      />

      <div class="page-switcher" data-testid="app-page-shell">
        <KeepAlive :key="scopeRevision" :max="10">
          <component
            :is="currentPage.component"
            :key="scopedPageKey"
            v-bind="currentPage.props"
            v-on="currentPage.listeners"
          />
        </KeepAlive>
      </div>
    </main>

    <UnifiedCockpit
      ref="cockpitRef"
      v-if="presentationMode"
      :stats="activePresentationStats"
      :operations="activePresentationOperations"
      :snapshot="activePresentationSnapshot"
      :operations-profile="activePresentationOperationsProfile"
      :overview-only="presentationWorkspace === WORKSPACE_LEADERSHIP"
      :available-tabs="allowedTabKeys"
      :user="currentUser"
      :portfolio-snapshot="portfolioOverviewSnapshot"
      :initial-building-code="presentationWorkspace === WORKSPACE_LEADERSHIP ? selectedOverviewBuilding : (selectedPropertyScope.type === 'building' ? selectedPropertyScope.code : '')"
      :scope-label="currentScopeLabel"
      @exit="exitPresentation"
      @notify="notify"
    />
    
        <div class="toast-stack" aria-live="polite" aria-atomic="false">
      <div v-for="toast in toasts" :key="toast.id" class="toast" :class="{ error: toast.type === 'error' }">
        <span>{{ $t(toast.text) }}</span>
        <button type="button" class="toast-dismiss" :aria-label="$t(&quot;关闭提示&quot;)" @click="toasts = toasts.filter(item => item.id !== toast.id)">×</button>
      </div>
    </div>

    <UnifiedSearch :open="unifiedSearchOpen" :scope-key="`${currentUser.id}:${selectedScopeValue}`" :scope-label="currentScopeLabel" :allowed-types="isLeadershipWorkspace ? [] : allowedTabKeys" @close="unifiedSearchOpen = false" @select="openSearchResult" />

    <ChangePasswordModal
      :open="passwordModalOpen"
      :busy="passwordBusy"
      @close="passwordModalOpen = false"
      @submit="changeOwnPassword"
    />
    <ControlHelpPanel
      :open="isHelpOpen"
      :title="$t(tabTitle)"
      :content="currentControlHelp"
      @close="isHelpOpen = false"
    />
    <CustomerDetailModal
      :open="contextCustomerOpen"
      :customer-id="contextCustomerId"
      :user="currentUser"
      :property-scope="selectedPropertyScope"
      :follow-up-prefill="contextCustomerFollowUpPrefill"
      :follow-up-prefill-token="contextCustomerFollowUpPrefillToken"
      :show-edit-action="false"
      :close-label="contextDetailCloseLabel"
      :layer-index="contextActiveDetail === 'customer' ? 2 : 1"
      @close="closeContextDetail"
      @notify="notify"
      @open-contract="openContractDetail"
      @updated="fetchDashboard"
    />
    <ContextContractDetailModal
      :open="contextContractOpen"
      :contract-id="contextContractId"
      :initial-section="contextContractInitialSection"
      :user="currentUser"
      :close-label="contextDetailCloseLabel"
      :layer-index="contextActiveDetail === 'contract' ? 2 : 1"
      @close="closeContextDetail"
      @notify="notify"
      @open-contract="openContractDetail"
      @open-customer="openCustomerDetail"
    />
  </div>
</template>
