from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RISK_CENTER_VIEW = ROOT / "frontend" / "src" / "views" / "RiskCenterView.vue"
RISK_FLAG_LIST = ROOT / "frontend" / "src" / "components" / "RiskFlagList.vue"
APP_VIEW = ROOT / "frontend" / "src" / "App.vue"
CONTEXT_CONTRACT_MODAL = ROOT / "frontend" / "src" / "components" / "ContextContractDetailModal.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _risk_flag_list_usage() -> str:
    source = _source(RISK_CENTER_VIEW)
    start = source.index("<RiskFlagList")
    end = source.index("/>", start)
    return source[start : end + 2]


def _function_source(name: str) -> str:
    source = _source(RISK_CENTER_VIEW)
    start = source.index(f"function {name}")
    end = source.index("\n}\n", start)
    return source[start : end + 3]


def test_risk_center_has_selected_risk_command_board() -> None:
    source = _source(RISK_CENTER_VIEW)

    assert 'data-testid="risk-center-page"' in source
    assert 'data-testid="risk-matrix-overview"' in source
    assert 'data-testid="risk-matrix"' in source
    assert 'data-testid="urgent-risk-queue"' in source
    assert 'data-testid="risk-filter-bar"' in source
    assert 'data-testid="risk-search-input"' in source
    assert 'data-testid="risk-more-trigger"' in source
    assert 'data-testid="risk-more-panel"' in source
    assert 'data-testid="risk-export-button"' in source
    assert 'data-testid="attachment-completeness-card"' in source
    assert 'data-testid="risk-selected-inspector"' in source
    assert 'data-testid="risk-detail-action-primary"' in source
    assert "const selectedRisk = ref(null)" in source
    assert "const selectedRiskFacts = computed(() => riskDetailFacts(selectedRisk.value))" in source
    assert "function riskDetailFacts(risk)" in source
    assert "const urgentRisks = computed" in source
    assert "function riskUrgencyScore(risk)" in source


def test_matrix_segment_filters_and_selects_first_matching_risk() -> None:
    source = _source(RISK_CENTER_VIEW)
    segment_source = _function_source("selectRiskSegment")

    assert '@click="selectRiskSegment(row.key, severity)"' in source
    assert 'risk-matrix-cell-${row.key}-${severity}' in source
    assert "riskFilters.value.risk_type = type" in segment_source
    assert "riskFilters.value.severity = severity" in segment_source
    assert "await fetchRiskFlags()" in segment_source
    assert "selectFirstVisibleRisk()" in segment_source
    assert "watch(visibleRisks" in source


def test_risk_center_filter_toolbar_keeps_export_permission_boundary() -> None:
    source = _source(RISK_CENTER_VIEW)
    export_index = source.index('data-testid="risk-export-button"')
    export_context = source[export_index - 180 : export_index + 220]
    export_source = _function_source("exportRiskFlags")

    assert "const riskMoreOpen = ref(false)" in source
    assert "const hasRiskFilters = computed" in source
    assert "const severityFilterChips = computed" in source
    assert "function selectRiskSeverity" in source
    assert 'data-testid="risk-type-select"' in source
    assert 'data-testid="risk-severity-select"' in source
    assert source.count('class="visually-hidden"') >= 2
    assert 'data-testid="risk-type-select"\n              @change=' not in source
    assert 'data-testid="risk-severity-select"\n              @change=' not in source
    assert 'v-if="canExportRisks"' in export_context
    assert '@click="exportRiskFlags"' in export_context
    assert "if (!canExportRisks.value) return" in export_source
    assert "params.set('risk_type', riskFilters.value.risk_type)" in export_source
    assert "params.set('severity', riskFilters.value.severity)" in export_source
    assert "riskFilters.value.query" not in export_source


def test_risk_list_rows_select_instead_of_navigating() -> None:
    usage = _risk_flag_list_usage()
    risk_list_source = _source(RISK_FLAG_LIST)

    assert ':row-clickable="false"' in usage
    assert "selectable" in usage
    assert ':selected-key="selectedRiskKey"' in usage
    assert '@select-risk="selectRisk"' in usage
    assert '@open-risk="openRiskItem"' in usage
    assert "selectable: Boolean" in risk_list_source
    assert "selectedKey:" in risk_list_source
    assert "rowClickable:" in risk_list_source
    assert "emit('select-risk', risk)" in risk_list_source
    assert 'data-testid="risk-target-open-button"' in risk_list_source
    assert 'data-testid="risk-flag-list"' in risk_list_source


def test_risk_center_keeps_backend_endpoint_boundary() -> None:
    source = _source(RISK_CENTER_VIEW)

    assert source.count("/api/") == 4
    assert "cachedApi(`/api/risks?${params}`" in source
    assert "cachedApi('/api/attachments/completeness-tasks?limit=20'" in source
    assert "fetchRiskFlags({ force: true })" in source
    assert "refreshAttachmentTasks" in source
    assert "api('/api/ai/risk-explanation'" in source
    assert "openDownload(`/api/risks/export" in source


def test_risk_targets_stay_in_context_before_workspace_navigation() -> None:
    source = _source(RISK_CENTER_VIEW)
    app_source = _source(APP_VIEW)
    context_modal_source = _source(CONTEXT_CONTRACT_MODAL)
    open_risk_source = _function_source("openRiskItem")

    assert "const riskTargetOpen = ref(false)" in source
    assert 'data-testid="risk-target-context-modal"' in source
    assert "function openRiskTargetContext(risk, mode = '')" in source
    assert "function openReceivableContext(risk)" in source
    assert "emit('open-contract', { id: contractId, initialSection: 'receivables' })" in source
    assert "openRiskTargetContext(risk, 'room')" in open_risk_source
    assert "openReceivableContext(risk)" in open_risk_source
    assert "emit('select-tab', 'rooms')" not in open_risk_source
    assert "emit('select-tab', props.user.role === 'viewer' ? 'contracts' : 'receivables')" not in open_risk_source
    assert 'data-testid="risk-target-room-tab-button"' in source
    assert 'data-testid="risk-target-receivables-tab-button"' in source

    assert "const contextContractInitialSection = ref('')" in app_source
    assert "contextContractInitialSection.value = typeof contract === 'object' ? (contract?.initialSection || '') : ''" in app_source
    assert ':initial-section="contextContractInitialSection"' in app_source
    assert "initialSection:" in context_modal_source
    assert ':initial-section="initialSection"' in context_modal_source
