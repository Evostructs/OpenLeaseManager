from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
APP = ROOT / "frontend" / "src" / "App.vue"
API = ROOT / "frontend" / "src" / "lib" / "api.js"
PREFETCH_CACHE = ROOT / "frontend" / "src" / "lib" / "prefetchCache.js"
PROPERTY_SCOPE = ROOT / "frontend" / "src" / "lib" / "propertyScope.js"
PRESENTATION = ROOT / "frontend" / "src" / "components" / "PresentationView.vue"
PRESENTATION_DIR = ROOT / "frontend" / "src" / "components" / "presentation"
PRESENTATION_DOMAIN = ROOT / "frontend" / "src" / "lib" / "presentationDomain.js"
COCKPIT = ROOT / "frontend" / "src" / "components" / "UnifiedCockpit.vue"
COCKPIT_DOMAIN = ROOT / "frontend" / "src" / "lib" / "cockpitDomain.js"
EVIDENCE_DOMAIN = ROOT / "frontend" / "src" / "lib" / "evidenceDomain.js"
EVIDENCE_VIEW = ROOT / "frontend" / "src" / "views" / "EvidenceView.vue"
ROUTER = ROOT / "frontend" / "src" / "router.js"
TOP_BAR = ROOT / "frontend" / "src" / "components" / "TopBar.vue"
TOOLS = ROOT / "frontend" / "src" / "views" / "ToolsCenterView.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_all_frontend_requests_and_downloads_share_the_property_scope_boundary() -> None:
    source = _source(API)
    assert "fetch(buildScopedApiUrl(path), config)" in source
    assert "export function openDownload(url, options = {})" in source
    assert "window.open(buildScopedDownloadUrl(url, options)" in source


def test_components_do_not_bypass_the_shared_network_and_download_boundary() -> None:
    direct_fetch = []
    direct_api_windows = []
    direct_api_links = []
    direct_api_navigation = []
    unbound_api_calls = []
    unbound_cached_api_calls = []
    unbound_download_calls = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".js", ".vue"} or path == API:
            continue
        source = _source(path)
        if re.search(r"\bfetch\s*\(", source):
            direct_fetch.append(path.relative_to(ROOT).as_posix())
        relative = path.relative_to(ROOT).as_posix()
        if re.search(r"\bapi\s*\(", source) and not re.search(r"from\s+['\"][^'\"]*(?:lib/api|\./api)", source):
            unbound_api_calls.append(relative)
        if (
            path != PREFETCH_CACHE
            and re.search(r"\bcachedApi\s*\(", source)
            and not re.search(r"from\s+['\"][^'\"]*lib/prefetchCache", source)
        ):
            unbound_cached_api_calls.append(relative)
        if re.search(r"\bopenDownload\s*\(", source) and not re.search(r"from\s+['\"][^'\"]*(?:lib/api|\./api)", source):
            unbound_download_calls.append(relative)
        if re.search(r"\bwindow\.open\s*\([^)]*/api/", source, flags=re.DOTALL):
            direct_api_windows.append(path.relative_to(ROOT).as_posix())
        if re.search(r"\b(?:window\.)?location(?:\.href|\.assign|\.replace)\s*[^;\n]*/api/", source):
            direct_api_navigation.append(path.relative_to(ROOT).as_posix())
        for line_number, line in enumerate(source.splitlines(), start=1):
            if "/api/" in line and ("href =" in line or 'href="' in line or ":href=" in line):
                direct_api_links.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}")
    assert direct_fetch == []
    assert direct_api_windows == []
    assert direct_api_links == []
    assert direct_api_navigation == []
    assert unbound_api_calls == []
    assert unbound_cached_api_calls == []
    assert unbound_download_calls == []


def test_platform_control_plane_paths_are_explicitly_scope_exempt() -> None:
    source = _source(PROPERTY_SCOPE)
    for path in (
        "/api/auth/",
        "/api/audit/platform/",
        "/api/buildings",
        "/api/system/",
        "/api/settings/",
        "/api/backups/",
    ):
        assert path in source


def test_tools_refreshes_the_platform_audit_stream_without_a_building_scope() -> None:
    source = _source(TOOLS)
    assert source.count("/api/audit/platform/logs") == 3
    assert "/api/audit/logs" not in source


def test_tools_exposes_server_side_building_presentation_and_aggregation_gates() -> None:
    tools_source = _source(TOOLS)
    app_source = _source(APP)
    presentation_source = _source(PRESENTATION_DOMAIN)
    assert "/api/buildings/operating-gates" in tools_source
    assert "/api/buildings/${encodeURIComponent(item.building_code)}/operating-gate" in tools_source
    assert "开放经营大屏展示（含单楼与板块卡片）" in tools_source
    assert "计入板块合计" in tools_source
    assert "所有经营数字当前均为模拟数据" in tools_source
    assert "expected_revision: Number(item.revision || 0)" in tools_source
    assert "emit('operating-gate-change')" in tools_source
    assert "'operating-gate-change': refreshOperatingGateContext" in app_source
    assert "import { cachedApi, invalidateApiCache, prefetchApi } from './lib/prefetchCache'" in app_source
    assert "effective_capabilities.includes('platform_admin')" in app_source
    assert "effective_capabilities.includes('platform_admin')" in tools_source
    assert "return []" in presentation_source
    assert "await refreshOperatingGateContext({ stillCurrent })" in app_source
    refresh_block = _between(
        app_source,
        "async function refreshPresentationGate(generation)",
        "function exitPresentation()",
    )
    assert "if (!current)" in refresh_block
    assert "presentationWorkspace.value === WORKSPACE_OPERATIONS" in refresh_block
    assert "!currentScopeAllowsPresentation()" in refresh_block
    assert "generation !== presentationRefreshGeneration" in refresh_block
    assert "schedulePresentationRefresh(generation)" in refresh_block
    assert "window.setTimeout(" in app_source
    assert "exitPresentation()" in _between(
        app_source,
        "async function logout()",
        "async function changeOwnPassword",
    )
    assert "exitPresentation()" in _between(
        app_source,
        "function resetToLogin(message)",
        "function handleAuthExpired",
    )
    assert "该楼宇当前未开放演示大屏，业务页面仍可正常查看" in app_source


def test_app_uses_one_operating_snapshot_and_scope_namespaced_page_state() -> None:
    source = _source(APP)
    assert "/api/operations/snapshot?include_trend=true&queue_limit=3&risk_limit=3" in source
    assert "snapshot?.legacy_contract_stats" in source
    assert "snapshot?.legacy_operations" in source
    assert ":key=\"scopedPageKey\"" in source
    assert '<KeepAlive :key="scopeRevision" :max="10">' in source
    assert "resetPropertyScopeContext()" in source
    assert "resolveAuthorizedPropertySelection" in source
    assert "authorizationVersion: summary.authorization_version" in source
    assert "gatePolicyVersion: summary.gate_policy_version" in source
    assert "metricDefinition: 'OPS_V1'" in source
    assert "router.push({ path, query, hash: route.hash })" in source
    assert "delete query.metric" in source
    assert "delete query.as_of_date" in source
    assert "requestScopeNamespace = propertyScopeNamespace()" in source
    assert "requestRevision === dashboardRequestRevision" in source
    assert "requestScopeNamespace === propertyScopeNamespace()" in source
    assert "operatingSnapshot.value = {}" in source


def test_session_scope_changes_drop_cached_pages_and_stale_snapshot_state() -> None:
    source = _source(APP)
    login = _between(source, "async function login", "async function register")
    logout = _between(source, "async function logout", "async function changeOwnPassword")
    auth_expired = _between(source, "function resetToLogin", "function handleAuthExpired")
    clear_scope = _between(source, "function clearScopeBoundState", "function configurePropertyScope")
    fetch_dashboard = _between(source, "async function fetchDashboard", "async function login")

    for block in (login, logout, auth_expired):
        assert "resetPropertyScopeContext()" in block
        assert "clearScopeBoundState()" in block
    assert "activeWorkspace.value = WORKSPACE_OPERATIONS" in auth_expired
    assert "workspaceLastTabs.value = initialWorkspaceLastTabs()" in auth_expired
    assert "replaceWithCleanEntryUrl()" in auth_expired
    for state_reset in (
        "stats.value = {}",
        "operationsDashboard.value = {}",
        "operatingSnapshot.value = {}",
        "tabDataPreloadPromises.clear()",
        "scopeRevision.value += 1",
    ):
        assert state_reset in clear_scope
    assert "currentUser.value?.scope?.authorization_version" in source
    assert "currentUser.value?.scope?.gate_policy_version" in source
    assert "requestScopeNamespace = propertyScopeNamespace()" in fetch_dashboard
    assert "if (!isCurrentRequest()) return" in fetch_dashboard
    assert "operatingSnapshot.value = {}" in fetch_dashboard


def test_presentation_entry_is_cancelable_and_theme_restore_is_one_shot() -> None:
    source = _source(APP)
    enter = _between(source, "async function enterPresentation()", "function isPresentationEntryCurrent")
    exit_block = _between(source, "function exitPresentation()", "function onPresentationKeydown")
    fullscreen_change = _between(
        source,
        "function onPresentationFullscreenChange()",
        "function scopeQueryText",
    )

    assert "const entryGeneration = ++presentationEntryGeneration" in enter
    assert "isPresentationEntryCurrent(entryGeneration, targetWorkspace, targetUserId)" in enter
    assert "presentationEntryGeneration += 1" in exit_block
    assert "if (!presentationMode.value)" in exit_block
    assert "presentationPrevTheme = ''" in exit_block
    assert "presentationNativeFullscreenCommitted" in fullscreen_change
    assert "presentationEntryPending && presentationNativeFullscreenEnteredAt" in fullscreen_change
    assert "requestPresentationExit()" in fullscreen_change
    assert "await requestPresentationLeave(" in source

    leadership_refresh = _between(
        source,
        "async function refreshLeadershipPresentationContext(options = {})",
        "function clearScopeBoundState",
    )
    operations_refresh = _between(
        source,
        "async function refreshOperatingGateContext(options = {})",
        "async function enterAuthenticatedApplication",
    )
    for refresh_block in (leadership_refresh, operations_refresh):
        assert "presentationRefreshIsCurrent(options)" in refresh_block
        assert "const sessionGeneration = authSessionGeneration" in refresh_block
        assert "refreshedSessionStillActive(user, sessionGeneration)" in refresh_block
    assert "sessionGeneration === authSessionGeneration" in source


def test_business_scope_query_watcher_uses_route_domain_during_workspace_changes() -> None:
    source = _source(APP)
    path_watcher = _between(
        source,
        "() => route.path,",
        "() => [scopeQueryText(route.query.building), scopeQueryText(route.query.portfolio)]",
    )
    watcher = _between(
        source,
        "() => [scopeQueryText(route.query.building), scopeQueryText(route.query.portfolio)]",
        "watch(\n  () => [\n    scopeQueryText(route.query.overview_portfolio)",
    )
    assert "configurePropertyScope(currentUser.value, { syncUrl: false })" in path_watcher
    assert "route.path === pathForTab('overview')" in watcher
    assert "activeTab.value === 'overview'" not in watcher


def test_presentation_is_a_pure_consumer_of_the_shared_snapshot() -> None:
    source = _source(PRESENTATION)
    assert "snapshot:" in source
    assert "watch(() => props.snapshot, applySnapshot" in source
    assert "{{ scopeTitle }} 租赁经营总览" in source
    assert "DEMO_A 租赁经营总览" not in source
    assert "同日快照 ${included}/${authorized} 栋" in source
    assert "number > 0 && number <= 1" not in source
    assert "rows.map((row) => optionalNumber(row.receivable ?? row.total_rent))" in source
    assert "const total = optionalNumber(vacancy.value.total_area)" in source
    assert "return { available: false, leased: null, vacant: null }" in source
    assert "item.publication_status === 'missing' ? '缺少同日数据' : '已有同日快照'" in source
    presentation_sources = [source, _source(PRESENTATION_DOMAIN)] + [
        _source(path) for path in PRESENTATION_DIR.rglob("*.vue")
    ]
    for presentation_source in presentation_sources:
        assert "cachedApi" not in presentation_source
        assert "fetch(" not in presentation_source
        for legacy_path in (
            "/api/rooms",
            "/api/rent-analytics",
            "/api/dashboard/operations/trend",
        ):
            assert legacy_path not in presentation_source


def test_portfolio_presentation_uses_authorized_scope_and_explicit_drilldowns() -> None:
    app_source = _source(APP)
    property_scope_source = _source(PROPERTY_SCOPE)
    presentation_source = _source(PRESENTATION)
    portfolio_source = _source(PRESENTATION_DIR / "PortfolioPresentationView.vue")
    grid_source = _source(PRESENTATION_DIR / "PortfolioBuildingGrid.vue")
    domain_source = _source(PRESENTATION_DOMAIN)
    cockpit_source = _source(COCKPIT)
    cockpit_domain = _source(COCKPIT_DOMAIN)
    cockpit_binding = _between(app_source, "<UnifiedCockpit", "/>")

    assert "buildAuthorizedPropertyOptions(summary)" in app_source
    assert "summary?.portfolio?.items" in property_scope_source
    assert "value: `portfolio:${normalizedText(item.code)}`" in property_scope_source
    assert 'presentationWorkspace.value === WORKSPACE_LEADERSHIP' in app_source
    assert ':snapshot="activePresentationSnapshot"' in cockpit_binding
    assert ':portfolio-snapshot="portfolioOverviewSnapshot"' in cockpit_binding
    assert ':overview-only="presentationWorkspace === WORKSPACE_LEADERSHIP"' in cockpit_binding
    assert ':user="currentUser"' in cockpit_binding
    assert '@exit="exitPresentation"' in cockpit_binding
    assert "@navigate=" not in cockpit_binding
    assert "@select-building=" not in cockpit_binding

    # The shared cockpit switches only its local selected building. It must
    # retain the server presentation boundary and explicit live-detail grant.
    assert "selectPortfolioOverviewSnapshot(source.value, selectedCode.value)" in cockpit_source
    assert "cockpitDetailAccess(props.user, selectedCode.value)" in cockpit_source
    assert "list(user?.scope?.items).find" in cockpit_domain
    assert "if (!building || !code) return { allowed: false" in cockpit_domain
    assert "profile.data_adapter === 'live_detail' && tabs.includes('analytics')" in cockpit_domain
    assert "presented.has(row.code)" in cockpit_domain
    choose_building = _between(cockpit_source, "function chooseBuilding", "function toggleTour")
    assert "row.code === code && row.selectable" in choose_building
    assert "selectedCode.value = code" in choose_building
    assert "router." not in choose_building
    assert "choosePropertyScope" not in choose_building
    detail_request = _between(cockpit_source, "async function loadSelectedDetail", "async function loadRooms")
    assert "if (!access.value.allowed || !selectedBuilding.value?.selectable) return" in detail_request
    assert "cockpitBuildingPath('/api/operations/snapshot', buildingCode)" in detail_request
    assert "includeScope: false" in detail_request
    assert 'data-testid="cockpit-detail"' in cockpit_source
    assert '@open-contract="openContract"' in cockpit_source
    assert "access.value.tabs.includes('contracts')" in cockpit_source
    assert "v-if=\"isPortfolio\"" in presentation_source
    assert 'data-scope-type="portfolio"' in portfolio_source
    assert "buildPortfolioBuildingCards" in portfolio_source
    assert "emit('select-building', card.code)" in grid_source
    assert "buildingCode: card.code" in grid_source
    assert "value === null ? '--'" in domain_source
    assert "const serverPresentation = snapshot?.presentation?.contributions" in domain_source
    assert "if (Array.isArray(serverPresentation)) return serverPresentation" in domain_source
    assert ".filter((item) => item?.presentation_enabled" not in domain_source

    for candidate_name in (
        "示例办公中心",
        "示例单栋中心",
        "示例主裙楼中心",
        "示例商务中心",
    ):
        assert candidate_name not in portfolio_source
        assert candidate_name not in grid_source
        assert candidate_name not in domain_source


def test_top_bar_exposes_the_current_authorized_building() -> None:
    source = _source(TOP_BAR)
    assert 'data-testid="property-scope-selector"' in source
    assert ':value="scopeValue"' in source
    assert "$emit('changeScope', $event.target.value)" in source


def test_evidence_page_uses_the_shared_scope_and_keeps_presentation_navigation_explicit() -> None:
    app_source = _source(APP)
    router_source = _source(ROUTER)
    evidence_source = _source(EVIDENCE_VIEW)
    evidence_domain = _source(EVIDENCE_DOMAIN)
    portfolio_source = _source(PRESENTATION_DIR / "PortfolioPresentationView.vue")
    grid_source = _source(PRESENTATION_DIR / "PortfolioBuildingGrid.vue")

    assert "['evidence', '/evidence']" in router_source
    assert "evidence: () => import('./views/EvidenceView.vue')" in app_source
    assert "{ key: 'evidence', label: '数据依据' }" in app_source
    assert "query.metric" in app_source
    assert "query.as_of_date" in app_source
    assert "delete query.metric" in app_source
    assert "delete query.as_of_date" in app_source
    assert "@click=\"openEvidence\"" in portfolio_source
    assert 'data-testid="portfolio-data-quality-evidence"' in portfolio_source
    assert 'class="portfolio-building-metric-row"' in grid_source
    assert 'class="portfolio-building-evidence"' in grid_source
    assert "buildingCode: card.code" in grid_source
    assert "metric: metric.key" in grid_source
    assert "asOfDate: card.asOfDate" in grid_source

    assert "api(buildRequestPath())" in evidence_source
    assert "/api/operations/snapshot-evidence" in evidence_source
    assert "metric_code" in evidence_source
    assert "as_of_date" in evidence_source
    assert "fetch(" not in evidence_source
    assert "value === null || value === undefined || value === ''" in evidence_domain
    assert "return '--'" in evidence_domain


def test_phase2_profile_filters_navigation_and_snapshot_dashboard_fails_closed() -> None:
    app_source = _source(APP)
    dashboard_source = _source(FRONTEND_SRC / "views" / "DashboardView.vue")
    portfolio_source = _source(PRESENTATION_DIR / "PortfolioPresentationView.vue")

    assert "items: group.items.filter((item) => allowed.has(item.key))" in app_source
    assert ':available-tabs="allowedTabKeys"' in app_source
    assert "selectedPropertyScope.value" in app_source
    assert 'data-testid="snapshot-dashboard-summary"' in dashboard_source
    assert "detail_facts_available !== false" in dashboard_source
    assert "detail_facts_available !== false" in _source(PRESENTATION)
    assert "不把未知数据当作 0" in dashboard_source
    assert "DEMO_A · PORTFOLIO" not in portfolio_source
