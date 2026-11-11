from __future__ import annotations

from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_VIEW = ROOT / "frontend" / "src" / "views" / "ContractsView.vue"
CONTRACT_DETAIL_MODAL = ROOT / "frontend" / "src" / "components" / "ContractDetailModal.vue"
CONTEXT_CONTRACT_DETAIL_MODAL = ROOT / "frontend" / "src" / "components" / "ContextContractDetailModal.vue"
CUSTOMER_DETAIL_MODAL = ROOT / "frontend" / "src" / "components" / "CustomerDetailModal.vue"
TERMINATE_MODAL = ROOT / "frontend" / "src" / "components" / "ContractTerminateModal.vue"
CHANGE_PREVIEW_MODAL = ROOT / "frontend" / "src" / "components" / "ContractChangePreviewModal.vue"
RENT_TERM_MODAL = ROOT / "frontend" / "src" / "components" / "ContractRentTermModal.vue"
APP_VIEW = ROOT / "frontend" / "src" / "App.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_rent_term_form_accepts_explicit_zero_base_rent() -> None:
    source = _source(RENT_TERM_MODAL)

    assert "numberMissing(form.monthly_amount) || Number(form.monthly_amount) < 0" in source
    assert "numberMissing(form.unit_price)" in source
    assert "Number(form.unit_price) < 0" in source
    assert "term.monthly_amount != null" in source


def _terminate_flow_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function terminateContract")
    end = source.index("function batchDelete")
    return source[start:end]


def _delete_flow_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function removeContract")
    end = source.index("async function importExcel")
    return source[start:end]


def _batch_delete_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function batchDelete")
    end = source.index("async function importExcel")
    return source[start:end]


def _rent_term_delete_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function deleteRentTerm")
    end = source.index("function openDepositAction")
    return source[start:end]


def _change_preview_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("async function saveContract")
    end = source.index("async function refreshContractDetail")
    return source[start:end]


def _contracts_fetch_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("async function fetchContracts")
    end = source.index("function fetchContractsFromFirstPage")
    return source[start:end]


def _contracts_filter_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function setStatusFilter")
    end = source.index("async function fetchContracts")
    return source[start:end]


def _contracts_sort_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("function toggleContractSort")
    end = source.index("function contractSortIndicator")
    return source[start:end]


def _contracts_mounted_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("onMounted(async")
    end = source.index("</script>")
    return source[start:end]


def _contract_detail_refresh_source() -> str:
    source = _source(CONTRACTS_VIEW)
    start = source.index("async function refreshContractDetail")
    end = source.index("async function openDetail")
    return source[start:end]


def _app_modal_source(function_name: str) -> str:
    source = _source(APP_VIEW)
    start = source.index(f"function {function_name}")
    next_function = source.find("\nfunction ", start + 1)
    if next_function == -1:
        next_function = source.index("\nonMounted", start)
    return source[start:next_function]


def _app_template_source() -> str:
    source = _source(APP_VIEW)
    return source[source.index("<template>"):]


def test_contracts_decision_workspace_uses_business_selectors_and_copy() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    detail_modal_source = _source(CONTRACT_DETAIL_MODAL)
    customer_modal_source = _source(CUSTOMER_DETAIL_MODAL)

    for selector in [
        'data-testid="contracts-workspace-summary"',
        'data-testid="contracts-lifecycle-visual"',
        'data-testid="contracts-lifecycle-summary"',
        'data-testid="contracts-status-chips"',
        'data-testid="contracts-next-action-strip"',
        'data-testid="contracts-table"',
    ]:
        assert selector in contracts_source
    assert 'data-testid="contract-decision-card"' in detail_modal_source
    assert 'data-testid="contract-detail-lifecycle-strip"' in detail_modal_source
    assert 'data-testid="contract-detail-lifecycle-action"' in detail_modal_source
    assert 'data-testid="customer-detail-modal"' in customer_modal_source

    assert "合同处理台" in contracts_source
    assert "行动队列" in contracts_source
    assert "需要先看的合同" in contracts_source
    assert "contractWorkspaceLead" in contracts_source
    assert "contractActionQueue" in contracts_source
    assert "contract-command-panel" in contracts_source
    assert "contract-action-queue" in contracts_source
    assert "contractLifecycleStages" in detail_modal_source
    assert "contractLifecyclePrimaryAction" in detail_modal_source
    assert ".contract-detail-lifecycle-strip" in load_style_source()


def test_contracts_lifecycle_summary_replaces_metric_card_row() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    style_source = load_style_source()

    assert "MetricTile" not in contracts_source
    assert "contractSummaryCards" not in contracts_source
    assert "contract-summary-card" not in contracts_source
    assert "contract-summary-card" not in style_source
    assert "lifecycleTotal" in contracts_source
    assert "contractLifecycleRows" in contracts_source
    assert ":data-testid=\"`contracts-lifecycle-segment-${item.key}`\"" in contracts_source
    assert ":aria-pressed=\"String(item.active)\"" in contracts_source
    assert "contracts-pressure-strip" not in contracts_source
    assert "contractPressureLanes" not in contracts_source
    assert ".contract-lifecycle-overview" in style_source
    assert ".contract-pressure-strip" not in style_source


def test_contracts_page_owns_pdf_and_integrity_tools() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    style_source = load_style_source()

    for selector in [
        'data-testid="contracts-document-panel"',
        'data-testid="contracts-pdf-panel"',
        'data-testid="contracts-integrity-panel"',
        'data-testid="contracts-pdf-upload-button"',
        'data-testid="contracts-integrity-refresh"',
        '`contracts-integrity-process-${issue.type}`',
    ]:
        assert selector in contracts_source

    for api_path in [
        "/api/contracts/batch-upload-pdf",
        "/api/contracts/unmatched-pdfs",
        "/api/contracts/missing-pdfs",
        "/api/system/integrity-check",
    ]:
        assert api_path in contracts_source

    assert "PDF 归档" in contracts_source
    assert "完整性检查" in contracts_source
    assert "refreshContractDocumentPanel" in contracts_source
    assert ".contract-document-panel" in style_source
    assert ".contract-document-card" in style_source
    assert ".contract-document-list" in style_source


def test_contracts_list_workspace_does_not_fetch_decision_card() -> None:
    assert "/decision-card" not in _contracts_fetch_source()
    assert "/decision-card" not in _contracts_filter_source()
    assert "/decision-card" not in _contracts_sort_source()
    assert "/decision-card" not in _contracts_mounted_source()
    assert "api(`/api/contracts/${contractId}/decision-card`)" in _contract_detail_refresh_source()


def test_context_contract_modal_fetches_decision_card_only_when_open_and_preserves_tab() -> None:
    context_source = _source(CONTEXT_CONTRACT_DETAIL_MODAL)
    watch_source = context_source[context_source.index("watch("):context_source.index("</script>")]
    refresh_source = context_source[
        context_source.index("async function refreshContractDetail"):
        context_source.index("function refreshCurrent")
    ]

    assert "if (open && contractId)" in watch_source
    assert "refreshContractDetail(contractId, { reset: true, closeOnError: true })" in watch_source
    assert "/decision-card" not in watch_source
    assert "api(`/api/contracts/${id}/decision-card`)" in refresh_source
    assert "activeTab.value" not in _app_modal_source("openCustomerDetail")
    assert "activeTab.value" not in _app_modal_source("openContractDetail")


def test_context_detail_modal_uses_return_history_instead_of_replacing_peer_modal() -> None:
    app_source = _source(APP_VIEW)
    customer_open_source = _app_modal_source("openCustomerDetail")
    contract_open_source = _app_modal_source("openContractDetail")
    close_source = _app_modal_source("closeContextDetail")
    template_source = _app_template_source()

    assert "contextDetailHistory = ref([])" in app_source
    assert "contextDetailCloseLabel" in app_source
    assert "'返回上一级'" in app_source
    assert "contextContractOpen.value = false" not in customer_open_source
    assert "contextCustomerOpen.value = false" not in contract_open_source
    assert "contextDetailHistory.value.push(currentDetail)" in customer_open_source
    assert "contextDetailHistory.value.push(currentDetail)" in contract_open_source
    assert "const previous = contextDetailHistory.value.pop()" in close_source
    assert "restoreContextDetail(previous)" in close_source
    assert '@close="closeContextDetail"' in template_source
    assert ':close-label="contextDetailCloseLabel"' in template_source
    assert ':layer-index="contextActiveDetail === \'customer\' ? 2 : 1"' in template_source
    assert ':layer-index="contextActiveDetail === \'contract\' ? 2 : 1"' in template_source


def test_contract_detail_decision_card_is_scannable_and_business_facing() -> None:
    detail_modal_source = _source(CONTRACT_DETAIL_MODAL)

    assert "decisionPrimarySignal" in detail_modal_source
    assert "decisionMoneyPressure" in detail_modal_source
    assert "decisionSupportItems" in detail_modal_source
    assert "主信号" in detail_modal_source
    assert "资金压力" in detail_modal_source
    assert "优先检查" in detail_modal_source
    assert "合同正文" in detail_modal_source
    assert "押金" in detail_modal_source
    assert "跟进" in detail_modal_source
    assert "风险" in detail_modal_source
    assert "riskSeverityLabel" in detail_modal_source
    assert "risk.severity || '--'" not in detail_modal_source


def test_contracts_viewer_role_keeps_edit_controls_hidden() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    detail_modal_source = _source(CONTRACT_DETAIL_MODAL)

    assert 'v-if="canEdit || canExport"' in contracts_source
    assert 'v-if="canEdit" class="contract-row-more-actions"' in contracts_source
    assert 'v-if="canEdit" class="btn btn-primary" @click="openCreate"' in contracts_source
    assert 'v-if="canEdit" class="contract-batch-section"' in contracts_source
    assert 'v-if="canExport" class="contract-batch-section"' in contracts_source
    assert '编辑合同' in contracts_source
    assert '删除合同' in contracts_source
    assert 'v-if="props.user?.role !== \'viewer\'" class="btn" @click="emit(\'select-tab\', \'receivables\')"' in contracts_source
    assert ':show-logs="canEdit"' in contracts_source
    assert ':can-edit="showLogs"' in detail_modal_source


def test_contracts_low_frequency_actions_are_collapsed_into_menus() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    style_source = load_style_source()
    toolbar_start = contracts_source.index('class="action-cluster contract-action-cluster"')
    toolbar_end = contracts_source.index('<div v-if="importResult"', toolbar_start)
    toolbar_source = contracts_source[toolbar_start:toolbar_end]
    row_start = contracts_source.index('class="actions-cell"')
    row_end = contracts_source.index('<tr v-if="!state.list.length', row_start)
    row_source = contracts_source[row_start:row_end]

    assert 'data-testid="contract-batch-trigger"' in toolbar_source
    assert "批量处理" in toolbar_source
    assert "合同资料" in toolbar_source
    assert "押金" in toolbar_source
    assert "选中项" in toolbar_source
    assert '{{ $t(" 删除选中 ") }}{{ $t(state.selected.length) }}{{ $t(" 条 ") }}' in toolbar_source
    assert ':disabled="!state.selected.length"' in toolbar_source
    assert 'class="contract-batch-popover"' in toolbar_source

    assert "下载模板" not in toolbar_source.split('data-testid="contract-batch-trigger"')[0]
    assert "导入 Excel" not in toolbar_source.split('data-testid="contract-batch-trigger"')[0]
    assert "导出数据" not in toolbar_source.split('data-testid="contract-batch-trigger"')[0]
    assert "导出押金" not in toolbar_source.split('data-testid="contract-batch-trigger"')[0]

    assert '>{{ $t("详情") }}</button>' in row_source
    assert '>{{ $t("客户") }}</button>' in row_source
    assert '>{{ $t("应收") }}</button>' in row_source
    assert ':title="$t(&quot;更多&quot;)"' in row_source
    assert "更多" in row_source
    assert "合同流转" in row_source
    assert "资料维护" in row_source
    assert "危险操作" in row_source
    for label in ["生成续签", "生成变更签", "标记退租", "编辑合同", "合同 PDF", "删除合同"]:
        assert label in row_source
    assert 'data-testid="contract-terminate-action"' in row_source
    assert 'data-testid="contract-delete-action"' in row_source

    assert ".contract-batch-actions" in style_source
    assert ".contract-batch-popover" in style_source
    assert ".contract-row-more-actions" in style_source
    assert ".contract-row-more-popover" in style_source
    assert "width: min(320px, calc(100vw - 48px));" in style_source
    assert "width: 226px;" in style_source
    assert "min-width: 226px;" in style_source


def test_contract_row_receivables_action_uses_context_modal() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    detail_modal_source = _source(CONTRACT_DETAIL_MODAL)
    context_modal_source = _source(CONTEXT_CONTRACT_DETAIL_MODAL)
    style_source = load_style_source()
    row_action_start = contracts_source.index(':title="$t(&quot;查看应收&quot;)"')
    row_action_end = contracts_source.index('class="contract-row-more-actions"', row_action_start)
    row_action_source = contracts_source[row_action_start:row_action_end]

    assert "function openReceivablesDetail(row)" in contracts_source
    assert "openDetail(row, { initialSection: 'receivables' })" in contracts_source
    assert '@click="openReceivablesDetail(row)"' in row_action_source
    assert "emit('select-tab', 'receivables')" not in row_action_source
    assert ':title="$t(&quot;查看月度应收&quot;)"' not in row_action_source
    assert 'v-if="props.user?.role !== \'viewer\'" class="btn" @click="emit(\'select-tab\', \'receivables\')"' in contracts_source
    assert ':initial-section="detailInitialSection"' in contracts_source
    assert "initialSection" in detail_modal_source
    assert 'data-testid="contract-receivables-context"' in detail_modal_source
    assert "receivablesSection.value?.scrollIntoView" in detail_modal_source
    assert "emit('open-contract', { id: contractId, initialSection: 'receivables' })" in detail_modal_source
    assert "emit('open-contract', typeof target === 'object' ? { ...target, id } : id)" in context_modal_source
    assert 'size="wide"' in detail_modal_source
    assert 'class="split-grid contract-detail-grid"' in detail_modal_source
    assert ".modal-panel.wide" in style_source
    assert ".contract-detail-grid" in style_source
    assert ".risk-flag-list.compact .risk-flag-row" in style_source
    assert "grid-template-areas:" in style_source


def test_contract_terminate_flow_uses_app_native_modal() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    modal_source = _source(TERMINATE_MODAL)
    terminate_source = _terminate_flow_source()

    assert "window.prompt" not in terminate_source
    assert "window.confirm" not in terminate_source
    assert "Number(row.days_to_expiry) < 0 && row.end_date ? row.end_date : todayInputValue()" in terminate_source
    assert "ContractTerminateModal" in contracts_source
    assert 'data-testid="contract-terminate-action"' in contracts_source
    assert 'data-testid="contract-terminate-modal"' in modal_source
    assert 'data-testid="contract-terminate-date"' in modal_source
    assert "留空则仅标记为已退租，不调整合同终止日；填写日期会同步截断房源明细截止日。" in modal_source
    assert "确认后合同处置将变为“已退租”，并刷新合同列表与仪表盘。" in modal_source


def test_contract_terminate_call_keeps_endpoint_and_payload_shape() -> None:
    terminate_source = _terminate_flow_source()

    assert "`/api/contracts/${row.id}/terminate`" in terminate_source
    assert "JSON.stringify({ end_date: payload.end_date || null })" in terminate_source
    assert "notes" not in terminate_source


def test_contract_delete_flows_use_app_native_modals() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    delete_source = _delete_flow_source()
    batch_delete_source = _batch_delete_source()

    assert "window.confirm" not in delete_source
    assert "window.confirm" not in batch_delete_source
    assert "BaseModal" in contracts_source
    assert 'data-testid="contract-delete-action"' in contracts_source
    assert 'data-testid="contract-delete-confirm-modal"' in contracts_source
    assert 'data-testid="contract-delete-cancel"' in contracts_source
    assert 'data-testid="contract-delete-confirm"' in contracts_source
    assert 'data-testid="contract-batch-delete-action"' in contracts_source
    assert 'data-testid="contract-batch-delete-confirm-modal"' in contracts_source
    assert 'data-testid="contract-batch-delete-cancel"' in contracts_source
    assert 'data-testid="contract-batch-delete-confirm"' in contracts_source


def test_contract_delete_calls_keep_endpoint_and_payload_shape() -> None:
    delete_source = _delete_flow_source()

    assert "`/api/contracts/${row.id}`" in delete_source
    assert "'/api/contracts/batch-delete'" in delete_source
    assert "body: JSON.stringify(ids)" in delete_source
    assert "pendingBatchDeleteIds.value = [...state.selected]" in delete_source


def test_contract_change_preview_flow_uses_app_native_modal() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    modal_source = _source(CHANGE_PREVIEW_MODAL)
    change_source = _change_preview_source()

    assert "window.confirm" not in change_source
    assert "changePreviewMessage" not in contracts_source
    assert "ContractChangePreviewModal" in contracts_source
    assert ':open="changePreviewOpen"' in contracts_source
    assert ':title="$t(&quot;确认合同变更&quot;)"' in modal_source
    assert 'data-testid="contract-change-preview-modal"' in modal_source
    assert 'data-testid="contract-change-preview-cancel"' in modal_source
    assert 'data-testid="contract-change-preview-confirm"' in modal_source


def test_contract_change_preview_calls_keep_endpoint_and_payload_shape() -> None:
    change_source = _change_preview_source()

    assert "'/api/contracts/change-preview'" in change_source
    assert "body: JSON.stringify(payload)" in change_source
    assert "changePreview.value = preview" in change_source
    assert "pendingChangePayload.value = payload" in change_source
    assert "'/api/contracts/change-confirm'" in change_source
    assert "body: JSON.stringify({ contract: pendingChangePayload.value })" in change_source
    assert "if (!changePreview.value || !pendingChangePayload.value || changeConfirmSubmitting.value) return" in change_source


def test_rent_term_delete_flow_uses_app_native_modal() -> None:
    contracts_source = _source(CONTRACTS_VIEW)
    detail_modal_source = _source(CONTRACT_DETAIL_MODAL)
    delete_source = _rent_term_delete_source()

    assert "window.confirm" not in delete_source
    assert "const rentTermDeleteConfirm = reactive({ contractId: null, term: null, deleting: false })" in contracts_source
    assert "function deleteRentTerm(term)" in delete_source
    assert "const contractId = detail.value?.id || term?.contract_id" in delete_source
    assert "function closeRentTermDeleteConfirm()" in delete_source
    assert "async function confirmDeleteRentTerm()" in delete_source
    assert '@delete-rent-term="deleteRentTerm"' in contracts_source
    assert ':title="$t(&quot;删除租金条款&quot;)"' in contracts_source
    assert 'data-testid="rent-term-delete-confirm-modal"' in contracts_source
    assert 'data-testid="rent-term-delete-cancel"' in contracts_source
    assert 'data-testid="rent-term-delete-confirm"' in contracts_source
    assert ':data-testid="`rent-term-delete-action-${term.id}`"' in detail_modal_source
    assert '@click="emit(\'delete-rent-term\', term)"' in detail_modal_source


def test_rent_term_delete_call_keeps_endpoint_and_refresh_shape() -> None:
    delete_source = _rent_term_delete_source()

    assert "await api(`/api/contracts/${contractId}/rent-terms/${term.id}`, { method: 'DELETE' })" in delete_source
    assert "`/api/contracts/${contractId}/rent-terms/${term.id}`" in delete_source
    assert "{ method: 'DELETE' }" in delete_source
    assert "body:" not in delete_source
    assert "emit('notify', { text: '租金条款已删除' })" in delete_source
    assert "await fetchContracts({ force: true })" in delete_source
    assert "if (detailOpen.value && detail.value?.id === contractId) await refreshContractDetail(contractId)" in delete_source
    assert "emit('refresh-dashboard')" in delete_source
    assert "emit('notify', { type: 'error', text: error.message })" in delete_source
