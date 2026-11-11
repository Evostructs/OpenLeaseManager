from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "OperationsWorkbenchView.vue"
APP = ROOT / "frontend" / "src" / "App.vue"
DASHBOARD_VIEW = ROOT / "frontend" / "src" / "views" / "DashboardView.vue"
ANALYTICS_VIEW = ROOT / "frontend" / "src" / "views" / "OperationsAnalyticsView.vue"
CUSTOMERS_VIEW = ROOT / "frontend" / "src" / "views" / "CustomersView.vue"


def _view_source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _template_source() -> str:
    source = _view_source()
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def _style_source() -> str:
    return load_style_source()


def test_operations_workbench_exposes_command_board_and_lanes() -> None:
    source = _view_source()
    for selector in [
        "operations-page",
        "operation-filter-strip",
        "operation-priority-row-${operationRowRank(groupIndex, itemIndex) - 1}",
        "operation-priority-action-${operationRowRank(groupIndex, itemIndex) - 1}",
        "operation-command-board",
        "today-priority-map",
        "today-action-queue",
        "operation-pending-strip",
        "today-priority-lane-${lane.key}",
    ]:
        assert selector in source
    for queue_key in [
        "overdue_contracts",
        "due_follow_up_customers",
        "expiring_contracts",
        "unpaid_deposits",
        "missing_contract_body",
    ]:
        assert queue_key in source
    for lane_key in [
        "'payment'",
        "'expiring'",
        "'materials'",
    ]:
        assert lane_key in source


def test_operations_workbench_preserves_context_and_action_clicks() -> None:
    source = _view_source()
    template = _template_source()
    assert '@click="openOperationItem(item)"' in source
    assert '@click="openOperationAction(item)"' in source
    assert '@click="navigateOperationItem(item)"' in source
    assert '@click="focusOperationLane(lane.key)"' in source
    assert '@click="openOperationItem(entry.item)"' in source
    assert '@click="openOperationAction(entry.item)"' in source
    assert "today-action-row-${index}" in source
    assert "operation-secondary-action-${operationRowRank(groupIndex, itemIndex) - 1}" in source
    assert "/api/dashboard/operations?include_trend=false&queue_limit=0&risk_limit=0" in source
    assert "/api/dashboard/operations/v2?include_trend=false&queue_limit=0&risk_limit=0" in source
    assert "/api/dashboard/operations/trend" in source
    assert 'data-testid="operation-target-context-modal"' in template


def test_operations_workbench_does_not_add_dashboard_requests() -> None:
    source = _view_source()
    requests = re.findall(r"(?:cachedApi|api)\('([^']+)'\)", source)
    assert "/api/dashboard/operations/v2?include_trend=false&queue_limit=0&risk_limit=0" in requests
    assert "/api/dashboard/operations?include_trend=false&queue_limit=0&risk_limit=0" in requests
    assert "/api/dashboard/operations/trend" in requests
    assert requests == [
        "/api/dashboard/operations?include_trend=false&queue_limit=0&risk_limit=0",
        "/api/dashboard/operations/v2?include_trend=false&queue_limit=0&risk_limit=0",
        "/api/dashboard/operations/trend",
    ]


def test_operations_workbench_health_cards_prefer_v2_metrics_with_v1_fallback() -> None:
    source = _view_source()
    assert "const operationsV2Data = ref(null)" in source
    assert "operationsV2Data.value?.metrics" in source
    assert "return v2Metrics.map(formatOperationsV2Metric)" in source
    assert "function formatOperationsV2Metric(metric) {" in source
    assert "metric.raw_value ?? metric.value ?? 0" in source
    assert "operationsV2Data.value = null" in source

    health_cards = re.search(r"const operationHealthCards = computed\(\(\) => \{(?P<body>.*?)\n\}\)", source, re.S)
    assert health_cards is not None
    body = health_cards.group("body")
    assert body.index("operationsV2Data.value?.metrics") < body.index("const m = metrics.value")
    assert body.index("return v2Metrics.map(formatOperationsV2Metric)") < body.index("const m = metrics.value")
    assert "current_month_unpaid_balance" in body
    assert "vacancy.occupancy_rate" in body
    assert "m.vacancy_room_count" in body


def test_operations_workbench_focus_lanes_prefer_v2_counts_and_tones() -> None:
    source = _view_source()
    assert "const operationsV2LaneMap = computed(() => {" in source
    assert "operationsV2Data.value?.lanes" in source
    assert "function operationV2LaneCount(laneKey, fallbackCount)" in source
    assert "function operationV2LaneTone(laneKey, fallbackTone = 'success')" in source

    focus_lanes = re.search(
        r"const operationFocusLanes = computed\(\(\) => \{(?P<body>.*?)\n\}\)\n\nconst operationPendingItems",
        source,
        re.S,
    )
    assert focus_lanes is not None
    body = focus_lanes.group("body")

    for lane_key, fallback_group in [
        ("expiring", "expiringGroup"),
    ]:
        assert f"operationV2LaneTone('{lane_key}', {fallback_group}?.tone || 'success')" in body
        assert f"operationV2LaneCount('{lane_key}', {fallback_group}?.totalCount || 0)" in body

    for fallback in [
        "operationFirstItem(['overdue_contracts', 'unpaid_deposits'])",
        "expiringGroup?.items[0] || null",
        "materialGroup?.items[0] || null",
    ]:
        assert fallback in body

    assert "const paymentFallbackCount = metricNumber(paymentGroup?.totalCount) + metricNumber(depositGroup?.totalCount)" in body
    assert "const paymentCount = operationV2LaneCount('payment', paymentFallbackCount)" in body
    assert "const materialCount = operationV2LaneCount('materials', materialGroup?.totalCount || 0)" in body
    assert "operationV2LaneTone('materials', materialGroup?.tone || 'success')" in body
    assert "totalCount: paymentCount" in body
    assert "totalCount: materialCount" in body
    assert "operationV2LaneCount('risk'" not in body


def test_operations_workbench_shows_clickable_today_action_queue() -> None:
    source = _view_source()
    template = _template_source()
    assert "const operationTodayActionQueue = computed(() => {" in source
    assert "overdue_contracts: 0" in source
    assert "expiring_contracts: 1" in source
    assert ".slice(0, 5)" in source

    strip = re.search(
        r'<section\s+v-if="operationTodayActionQueue\.length"(?P<body>.*?)</section>',
        template,
        re.S,
    )
    assert strip is not None
    body = strip.group("body")
    assert 'data-testid="today-action-queue"' in body
    assert ':aria-label="$t(&quot;今日紧急队列&quot;)"' in body
    assert 'v-for="(entry, index) in operationTodayActionQueue"' in body
    assert '@click="openOperationItem(entry.item)"' in body
    assert '@click="openOperationAction(entry.item)"' in body


def test_dashboard_customers_and_analytics_do_not_reference_operations_v2() -> None:
    for path in [APP, DASHBOARD_VIEW, ANALYTICS_VIEW, CUSTOMERS_VIEW]:
        assert "/api/dashboard/operations/v2" not in path.read_text(encoding="utf-8")


def test_operations_workbench_shows_priority_and_pending_language() -> None:
    source = _view_source()
    for text in [
        "今日处理主线",
        "今日紧急队列",
        "收款",
        "到期",
        "资料",
        "处理队列",
        "关键缺口",
        "高优先级",
        "合同正文缺失",
        "押金未收/不足",
        "续租退租",
    ]:
        assert text in source


def test_operations_workbench_template_uses_business_language() -> None:
    template = _template_source().lower()
    for forbidden in [
        "payload",
        "debug",
        "provider",
        "prompt",
        "token",
        "api",
        "\u63a5\u53e3",
        "\u6a21\u578b",
        "\u8c03\u8bd5",
        "接口",
        "模型",
        "调试",
        "只读辅助",
    ]:
        assert forbidden not in template


def test_operations_workbench_filter_wraps_on_mobile() -> None:
    style = _style_source()
    filter_block = re.search(r"\.operation-filter-strip\s*\{(?P<body>.*?)\}", style, re.S)
    assert filter_block is not None
    assert "flex-wrap: wrap" in filter_block.group("body")
    assert "overflow-x: auto" not in filter_block.group("body")


def test_operations_workbench_actions_are_context_first() -> None:
    source = _view_source()
    open_item = re.search(r"function openOperationItem\(item\) \{(?P<body>.*?)\n\}", source, re.S)
    assert open_item is not None
    assert "emit('open-customer', item.customer_id)" in open_item.group("body")
    assert "emit('open-contract'" in open_item.group("body")
    assert "openOperationTarget(item)" in open_item.group("body")
    assert "emit('select-tab'" not in open_item.group("body")

    open_action = re.search(r"function openOperationAction\(item\) \{(?P<body>.*?)\n\}", source, re.S)
    assert open_action is not None
    assert "openOperationItem(item)" in open_action.group("body")
    assert "emit('select-tab'" not in open_action.group("body")

    assert "return props.user?.role === 'viewer' ? '' : '前往月度应收'" in source
    assert "return '前往客户管理'" in source
    assert "return '前往房间管理'" in source
    assert "operationContractInitialSection(item)" in source
    assert "initialSection" in source
