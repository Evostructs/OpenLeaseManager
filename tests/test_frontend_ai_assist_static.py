from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "AIAssistView.vue"
APP = ROOT / "frontend" / "src" / "App.vue"


def _view_source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _template_source() -> str:
    match = re.search(r"<template>(.*)</template>", _view_source(), re.S)
    assert match is not None
    return match.group(1)


def test_ai_assist_keeps_required_test_selectors() -> None:
    source = _view_source()
    for selector in [
        "ai-assist-page",
        "ai-customer-search-input",
        "ai-workbench-summary-button",
        "ai-workbench-suggestion-button",
        "ai-risk-load-button",
        "ai-risk-workbench-result",
        "ai-workbench-risk-copy",
        "ai-context-signal-band",
        "ai-dual-workbench-grid",
        "ai-unified-flow",
        "ai-result-summary",
        "ai-result-evidence",
        "ai-result-next-actions",
        "ai-mode-customer",
        "ai-mode-risk",
        "ai-output-decision-card",
        "ai-next-action-strip",
    ]:
        assert selector in source


def test_ai_assist_uses_business_facing_structure_and_language() -> None:
    template = _template_source()
    for expected in [
        "经营研判台",
        "经营研判流程",
        "对象选择",
        "经营信号",
        "客户对象",
        "风险对象",
        "结论",
        "依据",
        "下一步",
        "读取风险",
        "生成经营摘要",
        "生成跟进建议",
    ]:
        assert expected in template

    assert "ai-task-switch" not in template
    assert "activeTask" not in template

    lowered = template.lower()
    for forbidden in [
        "payload",
        "debug",
        "provider",
        "prompt",
        "token",
        "接口",
        "模型",
        "调试",
        "json",
        "只读辅助",
        "不会自动",
        "人工确认",
        "请核对",
        "保存前请核对",
        "api",
        "先把",
        "会在这里",
        "先定位",
        "等待风险队列",
        "ai 工作台",
        "ai工作台",
        "生成结果",
        "辅助分析",
    ]:
        assert forbidden not in lowered


def test_ai_assist_moves_safety_copy_into_nearby_hints() -> None:
    template = _template_source()

    assert 'text="选定客户后，可生成经营摘要或跟进建议。"' in template
    assert 'text="选择风险后生成原因、影响和处理方向。"' in template
    assert "ai-workbench-command" in template
    assert "ai-dual-workbench-grid" in template
    assert "ai-unified-flow" in template
    assert '<p>摘要和跟进建议' not in template
    assert '<p>{{ riskState.loaded' not in template
    assert "人工确认" not in template
    assert "保存前请核对" not in template


def test_ai_assist_keeps_manual_read_boundaries() -> None:
    source = _view_source()
    template = _template_source()

    assert "onMounted" not in source
    assert '@click="runCustomerAi(\'summary\')"' in template
    assert '@click="runCustomerAi(\'suggestion\')"' in template
    assert '@click="fetchRisks"' in template
    assert "@click=\"explainRisk(riskState.selected)\"" in template


def test_ai_nav_uses_operating_judgement_language() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "经营研判" in source
    assert "研判台" in source
    assert "智能助理" not in source
    assert "辅助分析" not in source
