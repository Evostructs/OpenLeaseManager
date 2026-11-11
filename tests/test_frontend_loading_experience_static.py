from __future__ import annotations

from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
APP_VIEW = ROOT / "frontend" / "src" / "App.vue"
CONTRACTS_VIEW = ROOT / "frontend" / "src" / "views" / "ContractsView.vue"
RISK_CENTER_VIEW = ROOT / "frontend" / "src" / "views" / "RiskCenterView.vue"
RECEIVABLES_VIEW = ROOT / "frontend" / "src" / "views" / "ReceivablesLedgerView.vue"
AUDIT_VIEW = ROOT / "frontend" / "src" / "views" / "AuditLogView.vue"
OPERATIONS_VIEW = ROOT / "frontend" / "src" / "views" / "OperationsWorkbenchView.vue"
CUSTOMERS_VIEW = ROOT / "frontend" / "src" / "views" / "CustomersView.vue"
ROOMS_VIEW = ROOT / "frontend" / "src" / "views" / "RoomsView.vue"
AI_ASSIST_VIEW = ROOT / "frontend" / "src" / "views" / "AIAssistView.vue"
COMPONENTS = ROOT / "frontend" / "src" / "components"
ECHART_PANEL = COMPONENTS / "EChartPanel.vue"
RISK_FLAG_LIST = COMPONENTS / "RiskFlagList.vue"
CHART_RUNTIME = ROOT / "frontend" / "src" / "lib" / "chartRuntime.js"
ECHARTS_RUNTIME = ROOT / "frontend" / "src" / "lib" / "echartsRuntime.js"
PERFORMANCE_TELEMETRY = ROOT / "frontend" / "src" / "lib" / "performanceTelemetry.js"
PREFETCH_CACHE = ROOT / "frontend" / "src" / "lib" / "prefetchCache.js"
SIDEBAR_NAV = COMPONENTS / "SidebarNav.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _style_source() -> str:
    return load_style_source()


def test_app_shell_uses_keep_alive_page_cache_and_stable_fallback() -> None:
    source = _source(APP_VIEW)

    assert "import PageSkeleton from './components/PageSkeleton.vue'" in source
    assert "loadingComponent: PageSkeleton" in source
    assert 'data-testid="app-page-shell"' in source
    assert '<KeepAlive :key="scopeRevision" :max="10">' in source
    assert ':is="currentPage.component"' in source
    assert ':key="scopedPageKey"' in source
    assert 'v-on="currentPage.listeners"' in source
    assert "tabScrollPositions = ref({})" in source
    assert "function rememberActiveTabScroll()" in source
    assert "function restoreTabScroll(key)" in source
    assert "const restoreIfUnchanged = () => {" in source
    assert "Math.abs((window.scrollY || 0) - top) <= 4" in source
    assert "activeTab.value = key" in source
    assert "scheduleChartRuntimePreload()" in source[
        source.index("function commitTabSelection"):source.index("async function fetchAuthState")
    ]
    assert "commitTabSelection(key)" in source[
        source.index("function activateTab(key)"):source.index("async function selectTab")
    ]
    assert "const pageLoaders = {" in source
    assert "const primaryRoutePreloadKeys =" in source
    assert "'analytics'" in source[source.index("const primaryRoutePreloadKeys"):source.index("const loadPage")]
    assert "'ai-assist'" in source[source.index("const primaryRoutePreloadKeys"):source.index("const loadPage")]
    assert "function schedulePrimaryRoutePreload()" in source
    assert "function scheduleChartRuntimePreload()" in source
    assert "const authBooting = ref(true)" in source
    assert "fetchAuthState({ deferUser: true })" in source
    assert source.count("= resolveAuthenticatedEntry(user)") == 2
    assert "resolveEntryWorkspace(user, requestedTab" in source
    assert "readWorkspacePreference(user)" in source
    assert "function isDefaultEntryRequest()" in source
    assert "defaultEntry: isDefaultEntryRequest()" in source
    assert "!scopeQueryText(route.query.building)" in source
    assert "!scopeQueryText(route.query.portfolio)" in source
    assert "syncUrl: !deferUser && entry.workspace === WORKSPACE_OPERATIONS" in source
    assert "commitTabSelection(tab, WORKSPACE_OPERATIONS)" in source
    assert "fetchDashboard({ allowWithoutUser: true })" in source
    assert '<PageSkeleton v-if="authBooting" />' in source
    assert "preloadChartRuntime" in source
    assert "const CHART_RUNTIME_PRELOAD_DELAY_MS = 3500" in source
    assert "const chartRuntimePreloadBlockedTabs = new Set(['rooms', 'risks'])" in source
    assert "chartRuntimePreloadBlockedTabs.has(activeTab.value)" in source
    assert "window.requestIdleCallback(callback, { timeout: 1500 })" in source
    assert "const loader = pageLoaders[key]" in source
    assert "const tabDataPreloadPaths = {" in source
    assert "'/api/contracts?page=1&page_size=25'" in source
    assert "'/api/customers?page=1&page_size=25'" in source
    assert "'/api/customers/follow-ups/due?limit=8'" in source
    assert "'/api/rooms?include_operations=true'" in source
    assert "`/api/contracts/receivables/monthly?month=${currentMonthValue()}`" in source
    assert "`/api/contracts/receivables/dunning?month=${currentMonthValue()}&focus=true`" in source
    assert "'/api/risks?limit=100'" in source
    assert "'/api/attachments/completeness-tasks?limit=20'" in source
    assert "function preloadNavigationData(key)" in source
    assert "async function activateTabWithPreload(key)" in source
    assert "Promise.race([preload, waitForTabDataPreloadGrace()])" in source
    assert '@preload="preloadNavigationData"' in source
    assert "import { markRouteTransition, startPerformanceTelemetry } from './lib/performanceTelemetry'" in source
    assert "startPerformanceTelemetry()" in source
    assert "markRouteTransition(key)" in source
    assert "markRouteTransition('dashboard')" in source
    assert "schedulePrimaryRoutePreload()" in source[source.index("async function login"):source.index("async function register")]
    assert "async-page-state" not in source


def test_skeleton_components_are_reusable_and_dimensioned() -> None:
    style = _style_source()

    for component_name in [
        "PageSkeleton.vue",
        "MetricSkeleton.vue",
        "TableSkeleton.vue",
        "ChartSkeleton.vue",
    ]:
        assert (COMPONENTS / component_name).exists()

    assert ".page-switcher" in style
    assert "min-height: calc(100vh - 60px);" in style
    assert ".metric-skeleton-grid" in style
    assert ".chart-skeleton" in style
    assert ".table-skeleton-row" in style
    assert "grid-template-columns: repeat(var(--skeleton-columns), minmax(0, 1fr));" in style
    assert "@keyframes skeleton-pulse" in style


def test_navigation_intent_prefetch_reuses_inflight_gets() -> None:
    app = _source(APP_VIEW)
    sidebar = _source(SIDEBAR_NAV)
    prefetch_cache = _source(PREFETCH_CACHE)
    rooms = _source(ROOMS_VIEW)
    risks = _source(RISK_CENTER_VIEW)
    contracts = _source(CONTRACTS_VIEW)
    customers = _source(CUSTOMERS_VIEW)
    receivables = _source(RECEIVABLES_VIEW)

    assert "defineEmits(['select', 'preload'])" in sidebar
    assert '@pointerenter="$emit(\'preload\', item.key)"' in sidebar
    assert '@pointerdown="$emit(\'preload\', item.key)"' in sidebar
    assert '@focus="$emit(\'preload\', item.key)"' in sidebar

    assert "prefetchApi } from './lib/prefetchCache'" in app
    assert "tabDataPreloadPromises = new Map()" in app
    assert "Promise.allSettled(paths.map((path) => prefetchApi(path)))" in app
    assert "tabDataPreloadPromises.clear()" in app

    assert "const cache = new Map()" in prefetch_cache
    assert "export function cachedApi(path, options = {})" in prefetch_cache
    assert "if (existing?.promise) return existing.promise" in prefetch_cache
    assert "export function prefetchApi(path, options = {})" in prefetch_cache
    assert "export function invalidateApiCache(prefix = '')" in prefetch_cache

    assert "cachedApi(`/api/rooms${suffix}`" in rooms
    assert "invalidateApiCache('/api/rooms')" in rooms
    assert "cachedApi(`/api/risks?${params}`" in risks
    assert "cachedApi('/api/attachments/completeness-tasks?limit=20'" in risks
    assert "cachedApi(`/api/contracts?${params}`" in contracts
    assert "invalidateApiCache('/api/contracts')" in contracts
    assert "cachedApi(`/api/customers?${params}`" in customers
    assert "invalidateCustomerWorkspaceCache()" in customers
    assert "cachedApi(`/api/contracts/receivables/monthly?${params}`" in receivables
    assert "invalidateReceivablesCache()" in receivables


def test_first_stage_pages_keep_old_content_and_use_initial_skeletons() -> None:
    contracts = _source(CONTRACTS_VIEW)
    risks = _source(RISK_CENTER_VIEW)
    receivables = _source(RECEIVABLES_VIEW)

    assert "const hasLoadedContracts = ref(false)" in contracts
    assert "const isContractsInitialLoading = computed" in contracts
    assert "const isContractsRefreshing = computed" in contracts
    assert "hasLoadedContracts.value = true" in contracts
    assert '<TableSkeleton\n        v-if="isContractsInitialLoading"' in contracts
    assert "state.list = []" not in contracts[contracts.index("async function fetchContracts"):contracts.index("function fetchContractsFromFirstPage")]

    assert "const riskLoaded = ref(false)" in risks
    assert "const riskInitialLoading = computed" in risks
    assert "const riskRefreshing = computed" in risks
    assert "if (!riskLoaded.value)" in risks
    assert '<MetricSkeleton v-if="riskInitialLoading"' in risks
    assert '<TableSkeleton v-if="riskInitialLoading"' in risks

    assert "const receivablesLoading = ref(false)" in receivables
    assert "const receivablesInitialLoading = computed" in receivables
    assert "const receivablesRefreshing = computed" in receivables
    assert 'data-testid="receivables-skeleton"' in receivables
    assert '<ChartSkeleton :height="230" />' in receivables
    assert '<TableSkeleton :rows="8" :columns="6" />' in receivables
    assert '<div v-else-if="!receivablesInitialLoading" class="compact-empty receivable-empty-state">' in receivables


def test_operations_workbench_uses_stable_loading_regions() -> None:
    operations = _source(OPERATIONS_VIEW)
    style = _style_source()

    assert "import MetricSkeleton from '../components/MetricSkeleton.vue'" in operations
    assert "import TableSkeleton from '../components/TableSkeleton.vue'" in operations
    assert "const operationsInitialLoading = computed" in operations
    assert "const operationsRefreshing = computed" in operations
    assert "const trendInitialLoading = computed" in operations
    assert "const trendRefreshing = computed" in operations
    assert "if (!trendRows.value.length) trendRows.value = []" in operations
    assert '<MetricSkeleton v-if="operationsInitialLoading" :count="4" />' in operations
    assert 'data-testid="today-priority-map"' in operations
    assert 'data-testid="today-action-queue"' in operations
    assert 'data-testid="operations-queue-skeleton"' in operations
    assert 'data-testid="operations-trend-skeleton"' in operations
    assert '<table v-else class="operation-trend-table">' in operations
    assert ".operation-trend-skeleton" in style
    assert ".operation-queue-skeleton" in style
    assert ".today-priority-map" in style
    assert ".today-action-queue" in style


def test_customer_and_room_pages_keep_content_during_refresh() -> None:
    customers = _source(CUSTOMERS_VIEW)
    rooms = _source(ROOMS_VIEW)
    style = _style_source()

    assert "import TableSkeleton from '../components/TableSkeleton.vue'" in customers
    assert "const hasLoadedCustomers = ref(false)" in customers
    assert "const customersInitialLoading = computed" in customers
    assert "const customersRefreshing = computed" in customers
    assert "hasLoadedCustomers.value = true" in customers
    assert '<TableSkeleton v-if="customersInitialLoading"' in customers
    customer_fetch_body = customers[customers.index("async function fetchCustomers"):customers.index("async function fetchDueFollowUps")]
    assert "state.list = []" not in customer_fetch_body
    assert 'v-if="customersRefreshing"' in customers
    assert 'v-if="state.loading" class="list-empty"' not in customers

    assert "import TableSkeleton from '../components/TableSkeleton.vue'" in rooms
    assert "const roomsLoaded = ref(false)" in rooms
    assert "const roomsInitialLoading = computed" in rooms
    assert "const roomsRefreshing = computed" in rooms
    assert "roomsLoaded.value = true" in rooms
    assert '<TableSkeleton v-if="roomsInitialLoading"' in rooms
    assert 'v-for="row in roomsInitialLoading ? 6 : 0"' in rooms
    room_fetch_body = rooms[rooms.index("async function fetchRooms"):rooms.index("function availabilityQueryString")]
    assert "rooms.value = []" not in room_fetch_body
    assert 'v-if="roomsLoading" class="compact-empty"' not in rooms

    assert ".customers-table-skeleton" in style
    assert ".room-heatmap-skeleton" in style
    assert ".room-table-skeleton-row" in style


def test_chart_panel_lazy_loads_echarts_after_shell_render() -> None:
    panel = _source(ECHART_PANEL)
    runtime = _source(CHART_RUNTIME)
    echarts_runtime = _source(ECHARTS_RUNTIME)

    assert "import * as echarts from 'echarts'" not in panel
    assert "from '../lib/chartRuntime'" in panel
    assert "const echarts = await loadChartRuntime()" in panel

    assert "chartRuntimePromise = import('./echartsRuntime')" in runtime
    assert "export function preloadChartRuntime()" in runtime
    # Queue coalescing, resize-only work and inactive charts are exercised by
    # frontend/tests/chartPerformance.test.mjs; do not require the old 32ms delay.
    assert "from 'echarts/core'" in echarts_runtime
    assert "BarChart, LineChart, PieChart" in echarts_runtime
    assert "CanvasRenderer" in echarts_runtime
    assert "from 'echarts'" not in echarts_runtime


def test_performance_telemetry_tracks_cls_long_tasks_and_interactions() -> None:
    telemetry = _source(PERFORMANCE_TELEMETRY)

    assert "layout-shift" in telemetry
    assert "longtask" in telemetry
    assert "event" in telemetry
    assert "durationThreshold: 16" in telemetry
    assert "routeSamples" in telemetry
    assert "export function markRouteTransition(routeKey)" in telemetry
    assert "window.__leaseManagerPerformance" in telemetry
    assert "snapshot: performanceSnapshot" in telemetry
    assert "sanitizeResourceName" in telemetry
    assert "url.pathname.startsWith('/api/')" in telemetry
    assert ".filter((entry) => ['script', 'fetch', 'xmlhttprequest'].includes(entry.initiatorType))" in telemetry


def test_audit_page_uses_stable_first_load_and_refresh_states() -> None:
    audit_view = _source(AUDIT_VIEW)
    style = _style_source()

    assert "const auditLoading = ref(false)" in audit_view
    assert "const auditLoaded = ref(false)" in audit_view
    assert "const auditInitialLoading = computed" in audit_view
    assert "const auditRefreshing = computed" in audit_view
    assert "auditLoading.value = true" in audit_view
    assert "auditLoaded.value = true" in audit_view
    assert "auditLoading.value = false" in audit_view
    assert 'data-testid="audit-skeleton"' in audit_view
    assert '<TableSkeleton :rows="8" :columns="7" />' in audit_view
    assert 'v-if="auditRefreshing"' in audit_view
    audit_fetch_body = audit_view[audit_view.index("async function fetchAudit"):audit_view.index("async function applyAuditFilters")]
    assert "audit.value = { items: []" not in audit_fetch_body

    assert ".audit-loading-layout" in style
    assert "min-height: 600px;" in style


def test_ai_assist_uses_skeletons_for_long_running_panels() -> None:
    ai_assist = _source(AI_ASSIST_VIEW)
    risk_list = _source(RISK_FLAG_LIST)
    style = _style_source()

    assert 'data-testid="ai-customer-search-skeleton"' in ai_assist
    assert 'data-testid="ai-customer-detail-skeleton"' in ai_assist
    assert 'data-testid="ai-summary-skeleton"' in ai_assist
    assert 'data-testid="ai-suggestion-skeleton"' in ai_assist
    assert 'data-testid="ai-risk-explanation-skeleton"' in ai_assist
    assert "客户详情读取中" not in ai_assist
    assert "正在整理客户摘要" not in ai_assist
    assert "正在拟定跟进建议" not in ai_assist
    assert "正在分析风险原因" not in ai_assist

    assert 'data-testid="risk-flag-skeleton"' in risk_list
    assert "读取中..." not in risk_list
    assert "risk-flag-skeleton-row" in risk_list

    assert ".ai-list-skeleton" in style
    assert ".ai-panel-skeleton" in style
    assert ".risk-flag-loading" in style
    assert ".risk-flag-skeleton-row" in style
