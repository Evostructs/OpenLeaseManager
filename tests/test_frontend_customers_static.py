from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS_VIEW = ROOT / "frontend" / "src" / "views" / "CustomersView.vue"
CUSTOMER_DETAIL_MODAL = ROOT / "frontend" / "src" / "components" / "CustomerDetailModal.vue"


def _source() -> str:
    return CUSTOMERS_VIEW.read_text(encoding="utf-8")


def _modal_source() -> str:
    return CUSTOMER_DETAIL_MODAL.read_text(encoding="utf-8")


def _style_source() -> str:
    return load_style_source()


def test_customer_visuals_use_existing_chart_and_signal_navigation() -> None:
    source = _source()

    assert "import ChartCard from '../components/ChartCard.vue'" in source
    assert "<ChartCard" in source
    assert 'data-testid="customer-value-risk-view"' in source
    assert 'data-testid="customer-concentration-chart"' in source
    assert 'data-testid="customer-risk-matrix"' in source
    assert '@click="openCustomerSignal(row)"' in source
    assert '@click="openCustomerSignal(cell.leader)"' in source


def test_customer_dashboard_copy_is_business_facing_and_count_matches_preview() -> None:
    source = _source()
    template = source.split("<template>", maxsplit=1)[1]

    assert "历史逾期客户" in template
    assert '{{ $t(overdueCustomerRows.length) }}{{ $t(" 个客户") }}' in template
    assert "overdueCustomerRows.slice(0, 4)" in template
    assert "客户待办分布" in template
    assert "租金贡献 × 待办类型" in template
    assert "收款 / 跟进 / 附件" not in template
    assert "欠款 / 逾期" not in template
    assert "异常提示" not in template

    visible_text = "\n".join(re.findall(r">([^<>{}]+)<", template))
    for forbidden in ["逾期暴露", "敞口", "主档", "操作风险", "高占比", "风险信号", "客户风险矩阵"]:
        assert forbidden not in visible_text


def test_customer_visuals_do_not_add_ai_or_new_literal_fetch_endpoints() -> None:
    source = _source()

    assert "/api/ai" not in source

    literal_fetches = {
        next(group for group in match if group)
        for match in re.findall(r"(?:api|cachedApi)\((?:'([^']+)'|\"([^\"]+)\"|`([^`]+)`)", source)
        if any(group.startswith("/api/") for group in match)
    }
    assert literal_fetches == {
        "/api/customers?${params}",
        "/api/customers/follow-ups/due?limit=8",
        "/api/contracts/stats",
        "/api/dashboard/operations?include_trend=false&queue_limit=8&risk_limit=0",
        "/api/attachments/completeness-tasks?target_type=customer&limit=12",
        "/api/customers/import",
        "/api/customers/follow-ups/import",
    }


def test_customer_bulk_actions_are_folded_behind_menu() -> None:
    source = _source()

    assert 'data-testid="customer-batch-actions"' in source
    assert 'data-testid="customer-batch-trigger"' in source
    assert 'data-testid="customer-batch-panel"' in source

    actions_block = source.split('<div class="action-cluster customer-action-cluster">', maxsplit=1)[1]
    actions_block = actions_block.split('<input ref="customerImportInput"', maxsplit=1)[0]
    before_panel = actions_block.split('data-testid="customer-batch-panel"', maxsplit=1)[0]
    panel_block = actions_block.split('data-testid="customer-batch-panel"', maxsplit=1)[1]

    assert "新增客户" in before_panel
    assert "批量处理" in before_panel

    for hidden_action in [
        "downloadCustomerTemplate",
        "triggerCustomerPreviewFromBatch",
        "triggerCustomerImportFromBatch",
        "downloadFollowUpTemplate",
        "triggerFollowUpPreviewFromBatch",
        "triggerFollowUpImportFromBatch",
        "exportCustomersFromBatch",
        "exportFollowUpsFromBatch",
    ]:
        assert hidden_action not in before_panel
        assert hidden_action in panel_block

    assert '@click="exportCustomers"' not in actions_block
    assert '@click="exportAllFollowUps"' not in actions_block
    assert "客户模板" not in before_panel
    assert "跟进模板" not in before_panel


def test_customer_import_result_copy_is_business_facing() -> None:
    source = _source()
    template = source.split("<template>", maxsplit=1)[1]

    assert "校验通过，可导入。" in template
    assert "预检未发现错误，可以继续使用正式导入按钮写入数据" not in template

    visible_text = "\n".join(re.findall(r">([^<>{}]+)<", template))
    for forbidden in ["prompt", "token", "provider", "model", "接口", "调试", "JSON", "写入数据"]:
        assert forbidden not in visible_text


def test_customer_bulk_action_styles_guard_mobile_overflow() -> None:
    style = _style_source()

    for selector in [
        ".customer-batch-actions",
        ".customer-batch-trigger",
        ".customer-batch-popover",
        ".customer-batch-grid",
        ".customer-batch-buttons",
    ]:
        assert selector in style

    assert re.search(r"\.customer-batch-popover\s*\{[^}]*width:\s*min\(460px,\s*calc\(100vw - 48px\)\)", style, re.DOTALL)
    assert re.search(r"@media \(max-width:\s*1024px\).*?\.customer-batch-grid\s*\{[^}]*grid-template-columns:\s*1fr", style, re.DOTALL)
    assert re.search(r"@media \(max-width:\s*640px\).*?\.customer-batch-trigger\s*\{[^}]*width:\s*100%", style, re.DOTALL)


def test_customer_detail_operator_panel_keeps_required_selectors() -> None:
    source = _modal_source()

    for selector in [
        'data-testid="customer-detail-modal"',
        'data-testid="customer-detail-operating-panel"',
        'data-testid="customer-detail-signal-band"',
        'data-testid="customer-detail-next-actions"',
        'data-testid="customer-ai-assist"',
        'data-testid="customer-ai-summary-button"',
        'data-testid="customer-ai-suggestion-button"',
        'data-testid="follow-up-content-input"',
        'data-testid="follow-up-next-action-input"',
    ]:
        assert selector in source

    assert "@open-risk=\"openRisk\"" in source
    assert "emit('open-contract', { id: contractId, initialSection: 'receivables' })" in source
    assert "@click=\"emit('open-contract', contract.id)\"" in source
    assert "经营摘要" in source
    assert 'InfoHint :text="$t(&quot;生成后可复制或带入跟进。&quot;)"' in source
    assert "辅助分析" not in source


def test_customer_detail_ai_stays_explicit_click_only() -> None:
    source = _modal_source()

    assert source.count("runCustomerAi(") == 3
    assert '@click="runCustomerAi(\'summary\')"' in source
    assert '@click="runCustomerAi(\'suggestion\')"' in source
    assert re.search(r"async function runCustomerAi\(kind\).*?/api/ai/\$\{endpoint\}", source, re.DOTALL)
    assert not re.search(r"watch\([^)]*runCustomerAi", source, re.DOTALL)
    assert not re.search(r"loadDetail\([^)]*runCustomerAi", source, re.DOTALL)
    assert not re.search(r"refreshDetail\([^)]*runCustomerAi", source, re.DOTALL)


def test_customer_list_uses_compact_value_risk_rows() -> None:
    source = _source()

    for selector in [
        'data-testid="customer-compact-row"',
        'data-testid="customer-risk-bar"',
        'data-testid="customer-row-action-strip"',
        'data-testid="customer-row-primary-action"',
        'data-testid="customer-view-button"',
    ]:
        assert selector in source

    assert source.count('data-testid="customer-view-button"') == 1
    assert 'customerRiskClass(customer)' in source
    assert 'customerValueBarStyle(customer)' in source
    assert 'customerRiskBarStyle(customer)' in source
    assert 'customerNextAction(customer)' in source

    primary = re.search(r'<div class="customer-row-primary-action"[^>]*>.*?</div>', source, re.DOTALL)
    assert primary is not None
    assert 'data-testid="customer-view-button"' in primary.group(0)
    assert '@click="openDetail(customer)"' in primary.group(0)

    row_block = source.split('data-testid="customer-compact-row"', maxsplit=1)[1]
    row_block = row_block.split('<div v-if="!state.list.length"', maxsplit=1)[0]
    assert 'customerContactLine(customer)' not in row_block
    assert 'customerCreditLine(customer)' not in row_block
    assert 'data-testid="customer-row-secondary-actions"' not in row_block
    assert '@click="openFollowUpFromList(customer)"' not in row_block
    assert '@click="openEdit(customer)"' not in row_block


def test_customer_list_styles_keep_mobile_wrapping_guards() -> None:
    style = _style_source()

    for selector in [
        ".customer-compact-row",
        ".customer-row-risk-rail",
        ".customer-mini-track",
        ".customer-risk-bar",
        ".customer-row-action-strip",
        ".customer-row-primary-action",
    ]:
        assert selector in style

    assert re.search(r"\.customer-work-row\s*\{[^}]*grid-template-columns:\s*5px minmax\(220px,\s*0\.9fr\) minmax\(420px,\s*1\.55fr\) minmax\(88px,\s*118px\)", style, re.DOTALL)
    assert re.search(r"\.customer-work-meta\s*\{[^}]*grid-template-columns:\s*minmax\(132px,\s*0\.95fr\) minmax\(122px,\s*0\.9fr\) minmax\(150px,\s*1fr\) minmax\(150px,\s*1fr\)", style, re.DOTALL)
    assert re.search(r"@media \(max-width:\s*1100px\).*?\.customer-work-row\s*\{[^}]*grid-template-columns:\s*5px minmax\(0,\s*1fr\)", style, re.DOTALL)
    assert re.search(r"@media \(max-width:\s*1024px\).*?\.customer-work-meta\s*\{[^}]*grid-template-columns:\s*1fr", style, re.DOTALL)


def test_customer_detail_does_not_add_customer_endpoint_literals() -> None:
    source = _modal_source()

    customer_literals = set(re.findall(r"""['"`](/api/customers[^'"`]+)['"`]""", source))
    assert customer_literals == {
        "/api/customers/${id}",
        "/api/customers/${detail.value.id}/follow-ups/${followUpEditingId.value}",
        "/api/customers/${detail.value.id}/follow-ups",
        "/api/customers/${detail.value.id}/follow-ups/${followUp.id}/complete",
        "/api/customers/${target.customerId}/follow-ups/${target.id}",
        "/api/customers/follow-ups/export?customer_id=${detail.value.id}",
    }


def test_customer_detail_operator_panel_uses_business_facing_copy() -> None:
    source = _modal_source()
    template = source.split("<template>", maxsplit=1)[1]

    for text in ["客户运营面板", "下一步", "应收", "押金", "跟进", "到期", "风险提示", "核对历史逾期"]:
        assert text in template or text in source

    for forbidden in ["/api/", "operating_summary", "action_target", "follow-up-suggestion"]:
        assert forbidden not in template

    visible_text = "\n".join(re.findall(r">([^<>{}]+)<", template))
    for forbidden in ["provider", "model", "JSON", "模型", "供应商"]:
        assert forbidden not in visible_text
