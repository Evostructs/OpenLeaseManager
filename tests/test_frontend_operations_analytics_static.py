from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "OperationsAnalyticsView.vue"
RENT_TIMELINE = ROOT / "frontend" / "src" / "components" / "RentTimelineAnalytics.vue"


def _view_source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _template_source() -> str:
    source = _view_source()
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def test_operations_analytics_exposes_decision_signals() -> None:
    source = _view_source()
    for selector in [
        "analytics-page",
        "analytics-insights",
        "analytics-status-chart",
        "analytics-customer-rank",
        "analytics-tower-summary",
        "analytics-tower-mix-chart",
        "analytics-action-${item.key}",
        "analytics-customer-drilldown-${index}",
        "analytics-command-board",
        "analytics-signal-cash",
        "analytics-signal-vacancy",
        "analytics-signal-expiry",
    ]:
        assert selector in source
    assert "<RentTimelineAnalytics" in source
    assert '@open-contract="emit(\'open-contract\', $event)"' in source


def test_operations_analytics_template_uses_business_language() -> None:
    template = _template_source().lower()
    for forbidden in [
        "payload",
        "debug",
        "provider",
        "prompt",
        "token",
        "接口",
        "模型",
        "调试",
        "只读辅助",
        "下钻",
        "查看",
        "决策工作台",
        "暂无合同状态分布数据",
        "暂无客户聚合数据",
        "暂无未来收入预测",
    ]:
        assert forbidden not in template

    source = _view_source()
    for expected in [
        "经营驾驶舱",
        "工作区入口",
        "进入应收",
        "进入房源",
        "进入合同",
        "进入客户",
        "暂无趋势",
        "暂无客户排行",
        "分区结构",
    ]:
        assert expected in source

    for removed in ["待办归集", "进入风险", "analytics-signal-risk"]:
        assert removed not in source


def test_rent_timeline_replaces_the_old_inconsistent_rent_charts() -> None:
    view_source = _view_source()
    rent_source = RENT_TIMELINE.read_text(encoding="utf-8")

    for removed in [
        "avgDailyRent",
        "rent / area / 30",
        "towerRentOption",
        "forecastOption",
        "analytics-tower-rent-chart",
        "analytics-forecast-chart",
    ]:
        assert removed not in view_source

    for capability in [
        'data-testid="analytics-rent-timeline"',
        'data-testid="rent-timeline-chart"',
        'data-testid="rent-timeline-export-png"',
        'data-testid="rent-timeline-export-excel"',
        "@datazoom=\"handleDataZoom\"",
        'type="date"',
        "selectedSegments",
        "已签合同预测",
        "空置面积不进入分母",
    ]:
        assert capability in rent_source

    assert "['admin', 'editor'].includes(props.user?.role)" in rent_source


def test_operations_analytics_keeps_dense_values_single_line() -> None:
    source = _view_source()

    assert "class=\"analytics-line-ellipsis\"" in source
    assert "class=\"customer-name-ellipsis\"" in source

    stylesheet = load_style_source()
    assert ".analytics-line-ellipsis" in stylesheet
    assert "text-overflow: ellipsis;" in stylesheet
    assert "white-space: nowrap;" in stylesheet


def test_operations_analytics_lead_facts_do_not_truncate_numbers() -> None:
    source = _view_source()
    stylesheet = load_style_source()

    assert "compactMoney(collectionSummary.value.received)" in source
    assert "formatSignedShortPoints(portfolioTrendSummary.value.occupancyMom)" in source
    assert ':title="fact.fullValue || fact.value"' in source

    lead_fact_block = re.search(r"\.analytics-lead-facts strong \{([^}]+)\}", stylesheet)
    assert lead_fact_block is not None
    assert "text-overflow: ellipsis" not in lead_fact_block.group(1)
    assert "white-space: normal" in lead_fact_block.group(1)
