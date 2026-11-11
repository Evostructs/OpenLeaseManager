from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
RECEIVABLES_VIEW = ROOT / "frontend" / "src" / "views" / "ReceivablesLedgerView.vue"
RECEIVABLE_PAYMENT_MODAL = ROOT / "frontend" / "src" / "components" / "ReceivablePaymentModal.vue"
AUDIT_VIEW = ROOT / "frontend" / "src" / "views" / "AuditLogView.vue"
TOOLS_VIEW = ROOT / "frontend" / "src" / "views" / "ToolsCenterView.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _style_source() -> str:
    return load_style_source()


def _receivable_payment_delete_source() -> str:
    source = _source(RECEIVABLES_VIEW)
    start = source.index("function requestDeleteReceivablePayment")
    end = source.index("function toggleReceivableSort")
    return source[start:end]


def _template_between(path: Path, start_marker: str, end_marker: str) -> str:
    source = _source(path)
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _template_from(path: Path, start_marker: str) -> str:
    source = _source(path)
    return source[source.index(start_marker):]


def test_receivable_payment_delete_uses_app_native_modal() -> None:
    source = _source(RECEIVABLES_VIEW)
    delete_source = _receivable_payment_delete_source()

    assert "window.confirm" not in delete_source
    assert "const receivablePaymentDeleteConfirm = reactive({ payment: null, deleting: false })" in source
    assert "function requestDeleteReceivablePayment(payment)" in source
    assert "function closeReceivablePaymentDeleteConfirm()" in source
    assert "async function confirmDeleteReceivablePayment()" in source
    assert 'data-testid="receivable-payment-delete-confirm-modal"' in source
    assert 'data-testid="receivable-payment-delete-cancel"' in source
    assert 'data-testid="receivable-payment-delete-confirm"' in source
    assert "`receivable-payment-delete-${payment.id}`" in _source(RECEIVABLE_PAYMENT_MODAL)


def test_receivable_payment_delete_keeps_endpoint_and_refresh_shape() -> None:
    delete_source = _receivable_payment_delete_source()

    assert "`/api/contracts/receivables/payments/${payment.id}`" in delete_source
    assert "{ method: 'DELETE' }" in delete_source
    assert "receivables.value = await api" in delete_source
    assert "body:" not in delete_source
    assert "await Promise.all([fetchInvoiceLedger({ force: true }), fetchDunningLedger({ force: true })])" in delete_source
    assert "Object.assign(receivableForm" in delete_source
    assert "row.contract_id === receivableForm.contract_id" in delete_source


def test_receivable_triage_board_is_frontend_only_and_business_facing() -> None:
    source = _source(RECEIVABLES_VIEW)
    board_start = source.index('data-testid="receivable-triage-board"')
    board_end = source.index('<div v-if="receivables" class="receivable-workspace">')
    board_template = source[board_start:board_end]

    assert "const receivableTriageSegments = computed" in source
    assert "const receivableActionSteps = computed" in source
    assert 'data-testid="receivable-triage-segment-all"' in board_template
    assert 'data-testid="receivable-action-path"' in board_template
    assert "`receivable-action-step-${step.stepKey}`" in board_template
    assert "stepKey: 'overdue'" in source
    assert "stepKey: 'follow-up'" in source
    assert "stepKey: 'dunning'" in source
    assert "stepKey: 'invoice'" in source
    assert "stepKey: 'not-due'" in source
    assert "case 'follow_up':" in source
    assert "case 'active_dunning':" in source
    assert "/api/contracts/receivables/invoices" not in board_template
    assert "/api/contracts/receivables/dunning" not in board_template
    assert "requires_dunning" not in board_template
    assert "dunning_priority" not in board_template
    assert "invoice_status" not in board_template

    board_copy = board_template.lower()
    for forbidden in ["payload", "debug", "provider", "prompt", "token", "api", "接口", "模型", "调试", "只读辅助"]:
        assert forbidden not in board_copy


def test_receivable_collection_overview_replaces_summary_card_stack() -> None:
    source = _source(RECEIVABLES_VIEW)
    overview_template = _template_between(
        RECEIVABLES_VIEW,
        'data-testid="receivable-collection-overview"',
        'data-testid="receivable-triage-board"',
    )
    secondary_source = source[
        source.index("const receivableSecondaryMetrics"):
        source.index("const receivableCollectionOption")
    ]

    for test_id in [
        'data-testid="receivable-collection-overview"',
        'data-testid="receivable-collection-segment"',
        'data-testid="receivable-secondary-metrics"',
    ]:
        assert test_id in overview_template

    for label in ["已收金额", "本月未收", "本月逾期", "更多收款明细"]:
        assert label in overview_template
    for label in ["物业费参考", "超收余额", "核销调整"]:
        assert label in secondary_source

    assert "const receivableCollectionOverviewSegments = computed" in source
    assert 'class="tool-summary-grid compact-stats report-summary-grid"' not in source


def test_receivable_flow_overview_drives_detail_ledger() -> None:
    source = _source(RECEIVABLES_VIEW)
    flow_template = _template_between(
        RECEIVABLES_VIEW,
        'data-testid="receivable-flow-overview"',
        'data-testid="receivable-triage-board"',
    )
    flow_source = source[
        source.index("const receivableFlowSteps = computed"):
        source.index("const receivableSecondaryMetrics")
    ]
    handler_source = source[
        source.index("async function applyReceivableFlowStep(step)"):
        source.index("async function clearReceivableFilters")
    ]

    assert "const receivableFlowSteps = computed" in source
    assert 'data-testid="receivable-flow-overview"' in flow_template
    assert "`receivable-flow-step-${step.key}`" in flow_template
    assert "@click=\"applyReceivableFlowStep(step)\"" in flow_template
    for key in ["key: 'due'", "key: 'received'", "key: 'unpaid'", "key: 'overdue'", "key: 'todo'"]:
        assert key in flow_source
    for label in ["应收", "已收", "本月未收", "本月逾期", "收款待办"]:
        assert label in flow_source
    assert "amount: Number(current.total_overdue_balance || 0)" in flow_source
    assert "sumReceivableAmount(actionRows, 'balance')" in flow_source
    assert "setReceivableStatus(step.status)" in handler_source
    assert "await openReceivableLedger(step.target)" in handler_source
    assert "receivableDetailLedgerOpen.value = true" in source


def test_receivable_detail_ledger_is_grouped_and_default_collapsed() -> None:
    source = _source(RECEIVABLES_VIEW)
    detail_template = _template_between(
        RECEIVABLES_VIEW,
        'data-testid="receivable-detail-ledger"',
        '<ReceivablePaymentModal',
    )

    assert "const receivableDetailLedgerOpen = ref(false)" in source
    assert "const activeReceivableLedger = ref('main')" in source
    assert "const receivableLedgerTabs = computed" in source
    assert "const receivableLedgerPresetSteps = computed" in source
    assert 'data-testid="receivable-detail-ledger-toggle"' in detail_template
    assert 'data-testid="receivable-ledger-presets"' in detail_template
    assert "`receivable-ledger-preset-${step.key}`" in detail_template
    assert "`receivable-ledger-tab-${tab.key}`" in detail_template
    assert "v-if=\"receivableDetailLedgerOpen\"" in detail_template
    assert "v-show=\"activeReceivableLedger === 'main'\"" in detail_template
    assert "v-show=\"activeReceivableLedger === 'invoice'\"" in detail_template
    assert "v-show=\"activeReceivableLedger === 'dunning'\"" in detail_template
    assert "按合同登记收款，或切换至开票、催收台账" in detail_template
    assert ".receivable-ledger-preset-row" in _style_source()
    assert ".receivables-ledger-table th:last-child" in _style_source()


def test_tools_info_hints_use_short_business_copy() -> None:
    source = "".join(_source(path) for path in (RECEIVABLES_VIEW, AUDIT_VIEW, TOOLS_VIEW))

    old_hints = [
        "物业费只做参考，不计入本页回款率；未收余额拆分为逾期与未到期未收。未收账龄越靠右，催收优先级越高；重点未收客户按未收余额排序。",
        "优先列出催收提醒、逾期余额、跟进临近和待开票未收项；未到期未收不会按逾期呈现。",
        "记录关键业务动作；敏感内容只显示处理结果。",
        "该文件未自动匹配到合同，需要手动指定。",
        "恢复前会二次确认，并先生成安全备份。",
    ]
    new_hints = [
        "查看本月应收、回款和未收分布。",
        "按本月未收、收款登记、开票查看客户。",
        "查看合同、客户、收款等关键记录。",
        "常用处理入口按日常任务归在一起。",
    ]

    for old_hint in old_hints:
        assert old_hint not in source
    for new_hint in new_hints:
        assert new_hint in source


def test_receivable_action_path_clicks_are_local_filters() -> None:
    source = _source(RECEIVABLES_VIEW)
    board_start = source.index('data-testid="receivable-action-path"')
    board_end = source.index('<div v-if="receivables" class="receivable-workspace">')
    action_template = source[board_start:board_end]
    set_status_start = source.index("function setReceivableStatus(status)")
    set_status_end = source.index("function scrollReceivableLedgerTarget")
    set_status_source = source[set_status_start:set_status_end]

    assert '@click="setReceivableStatus(step.status)"' in action_template
    assert "receivableFilters.status = status" in set_status_source
    assert "receivablePager.page = 1" in set_status_source
    assert "await " not in set_status_source
    assert "api(" not in set_status_source
    assert "refreshReceivablePage" not in set_status_source


def test_receivable_triage_overdue_amount_uses_only_overdue_balance() -> None:
    source = _source(RECEIVABLES_VIEW)
    segment_start = source.index("key: 'overdue'")
    segment_end = source.index("key: 'follow-up'")
    overdue_segment = source[segment_start:segment_end]

    assert "sumReceivableAmountWithFallback" not in source
    assert "amount: sumReceivableAmount(overdueRows, 'overdue_balance')" in overdue_segment
    assert "amount: sumReceivableAmount(overdueRows, 'balance')" not in overdue_segment


def test_ledger_flow_is_frontend_only_and_business_facing() -> None:
    source = _source(RECEIVABLES_VIEW)
    flow_template = _template_between(
        RECEIVABLES_VIEW,
        'data-testid="ledger-flow"',
        '<section ref="invoiceLedgerSection"',
    )

    assert "const ledgerFlowStatusBars = computed" in source
    assert "const ledgerFlowLanes = computed" in source
    assert "function applyLedgerFlowLane(lane)" in source
    assert "function ledgerFlowRowMeta(row, lane)" in source
    assert "invoiceLedgerSection = ref(null)" in source
    assert "dunningLedgerSection = ref(null)" in source
    assert "scrollIntoView({ behavior: 'smooth', block: 'start' })" in source
    assert 'data-testid="ledger-flow"' in flow_template
    assert "`ledger-flow-lane-${lane.key}`" in flow_template

    for label in ["未开票", "已开票未收", "本月逾期待催", "已安排跟进", "待跟进"]:
        assert label in source

    assert "/api/contracts/receivables/invoices" not in flow_template
    assert "/api/contracts/receivables/dunning" not in flow_template
    assert source.count("/api/contracts/receivables/invoices") == 2
    assert source.count("/api/contracts/receivables/dunning") == 2

    flow_copy = flow_template.lower()
    for forbidden in ["token", "prompt", "debug", "api", "接口", "调试"]:
        assert forbidden not in flow_copy


def test_ledger_flow_clicks_reuse_existing_filters_and_fetches() -> None:
    source = _source(RECEIVABLES_VIEW)
    handler_start = source.index("async function applyLedgerFlowLane(lane)")
    handler_end = source.index("async function fetchReceivables")
    handler_source = source[handler_start:handler_end]

    assert '@click="applyLedgerFlowLane(lane)"' in source
    assert "invoiceFilters.invoice_status = lane.invoiceStatus || ''" in handler_source
    assert "await fetchInvoiceLedger()" in handler_source
    assert "dunningFilters.dunning_status = lane.dunningStatus || ''" in handler_source
    assert "dunningFilters.overdue_only = lane.overdueOnly || ''" in handler_source
    assert "dunningFilters.focus = Boolean(lane.focus)" in handler_source
    assert "await fetchDunningLedger()" in handler_source
    assert "setReceivableStatus" not in handler_source
    assert "api(" not in handler_source
    assert "/api/" not in handler_source


def test_ledger_flow_overdue_lane_uses_only_overdue_balance() -> None:
    source = _source(RECEIVABLES_VIEW)
    lane_start = source.index("key: 'overdue-dunning'")
    lane_end = source.index("key: 'planned-follow'")
    overdue_lane = source[lane_start:lane_end]

    assert "amountKey: 'overdue_balance'" in overdue_lane
    assert "amountKey: 'balance'" not in overdue_lane
    assert "未到期未收" not in overdue_lane


def test_audit_console_keeps_controls_and_business_activity_strip() -> None:
    audit_template = _template_from(AUDIT_VIEW, '<article v-if="showAudit"')

    for test_id in [
        'data-testid="audit-activity-strip"',
        'data-testid="audit-result-card"',
        'data-testid="audit-latest-action-card"',
        'data-testid="audit-latest-operator-card"',
        'data-testid="audit-latest-target-card"',
        'data-testid="audit-export-button"',
        'data-testid="audit-action-strip"',
        'data-testid="audit-filter-panel"',
        'data-testid="audit-filter-submit"',
        'data-testid="audit-refresh"',
        'data-testid="audit-filter-clear"',
        'data-testid="audit-log-table"',
    ]:
        assert test_id in audit_template

    assert "日志总数" in audit_template
    assert "最新动作" in audit_template
    assert "最新操作人" in audit_template
    assert "最新对象" in audit_template
    assert "查看合同、客户、收款等关键记录。" in audit_template
    assert "审计时间线" not in audit_template
    assert "当前页动作分布" not in audit_template
    assert "最新高影响动作" not in audit_template
    assert "安全边界" not in audit_template
    assert "仅管理员" not in audit_template
    assert "导出用于复核" not in audit_template

    audit_body_copy = audit_template.lower()
    for forbidden in ["token", "secret", "cookie", "authorization", "prompt", "api key", "apikey"]:
        assert forbidden not in audit_body_copy


def test_audit_activity_strip_uses_existing_audit_endpoints() -> None:
    source = _source(AUDIT_VIEW)
    audit_template = _template_from(AUDIT_VIEW, '<article v-if="showAudit"')

    assert "const auditLatestItem = computed" in source
    assert "const auditLatestTargetTitle = computed" in source
    assert "const auditLatestTargetMeta = computed" in source
    assert "auditVisibleQuickActions" in source
    assert "openDownload('/api/audit/export')" in audit_template
    assert "/api/audit/summary" not in source
    assert source.count("/api/audit/logs") == 1
    assert source.count("/api/audit/export") == 1

    activity_template = _template_between(
        AUDIT_VIEW,
        'data-testid="audit-activity-strip"',
        'data-testid="audit-action-strip"',
    )
    assert "全量" not in activity_template
    for forbidden in ["token", "secret", "cookie", "authorization", "prompt", "api key", "apikey", "debug", "provider", "model"]:
        assert forbidden not in activity_template.lower()


def test_tools_console_groups_modules_and_keeps_required_actions() -> None:
    source = _source(TOOLS_VIEW)
    tools_template = _template_from(TOOLS_VIEW, '<section v-if="showTools"')

    for test_id in [
        'data-testid="tools-page"',
        'data-testid="tools-task-grid"',
        'data-testid="tools-help-trigger"',
        'data-testid="tool-section-materials"',
        'data-testid="tool-section-access"',
        'data-testid="tool-card-backup"',
        'data-testid="tool-card-feishu"',
        'data-testid="tool-card-users"',
        'data-testid="backup-create-button"',
        'data-testid="backup-list"',
        'data-testid="backup-restore-confirm-modal"',
        'data-testid="backup-restore-confirm"',
    ]:
        assert test_id in tools_template

    assert 'class="tool-console"' in tools_template
    assert 'data-testid="tool-hub-intro"' not in source
    assert 'data-testid="tool-ops-strip"' not in source
    assert 'class="tool-section-head"' not in tools_template
    assert '<span class="tool-group-label business">合同资料</span>' not in tools_template
    assert '<span class="tool-group-label business">资料</span>' not in tools_template
    assert '<span class="tool-group-label admin">管理员</span>' not in tools_template
    assert '<span class="tool-group-label config">通知</span>' not in tools_template
    assert "合同 PDF 与资料检查" not in tools_template
    assert "备份与恢复控制" not in tools_template
    assert "通知与用户管理" not in tools_template
    assert "备份安全" not in tools_template
    assert "放在同一控制区" not in tools_template
    assert "提醒与备份" in tools_template
    assert "合同 PDF" not in tools_template
    assert "资料完整性" not in tools_template
    assert 'data-testid="tool-section-security"' not in tools_template
    assert 'data-testid="tool-card-pdf"' not in tools_template
    assert 'data-testid="tool-card-integrity"' not in tools_template
    assert 'data-testid="pdf-upload-button"' not in tools_template
    assert '`integrity-process-${issue.type}`' not in tools_template
    assert "数据备份" in tools_template
    assert "到期提醒" in tools_template
    assert "账号维护" in tools_template
    assert 'minlength="3"' in tools_template
    assert 'maxlength="32"' in tools_template
    assert 'pattern="[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]"' in tools_template
    assert "3—32 位小写字母、数字、点、下划线或短横线" in tools_template
    assert "const NEW_ACCOUNT_USERNAME_PATTERN = /^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$/" in source
    assert "if (!NEW_ACCOUNT_USERNAME_PATTERN.test(userForm.username))" in source
    assert "用户名须为 3—32 位小写字母、数字、点、下划线或短横线" in source
    assert tools_template.index('data-testid="tool-section-materials"') < tools_template.index('data-testid="tool-section-access"')
    assert tools_template.index('data-testid="tool-card-feishu"') < tools_template.index('data-testid="tool-card-backup"')
    assert tools_template.index('data-testid="tool-card-backup"') < tools_template.index('data-testid="tool-card-users"')


def test_tools_console_uses_two_column_desktop_layout() -> None:
    source = _source(TOOLS_VIEW)
    style = _style_source()

    for class_name in [
        "tool-task-group-materials",
        "tool-task-group-access",
    ]:
        assert class_name in source
        assert f".{class_name}" in style
    assert "tool-task-group-security" not in source

    assert re.search(
        r"\.tool-console\.admin\s+\.tool-task-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(380px,\s*1fr\)",
        style,
        re.S,
    )
    assert re.search(r"\.tool-task-group-materials\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*1", style, re.S)
    assert re.search(r"\.tool-task-group-access\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*1", style, re.S)
    assert re.search(
        r"@media \(max-width:\s*1280px\).*?\.tool-console\.admin\s+\.tool-task-grid.*?grid-template-columns:\s*1fr",
        style,
        re.S,
    )
