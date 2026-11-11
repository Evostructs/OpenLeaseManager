from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
INFO_HINT = ROOT / "frontend" / "src" / "components" / "InfoHint.vue"
FRONTEND_SRC = ROOT / "frontend" / "src"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _info_hint_source() -> str:
    return _source(INFO_HINT)


def _template(source: str) -> str:
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def _style(source: str) -> str:
    match = re.search(r"<style scoped>(.*)</style>", source, re.S)
    assert match is not None
    return match.group(1)


def test_info_hint_preserves_public_api_and_accessible_pairing() -> None:
    source = _info_hint_source()
    template = _template(source)

    assert "text:" in source
    assert "label:" in source
    assert "align:" in source
    assert "default: '查看说明'" in source
    assert "鏌ョ湅璇存槑" not in source
    assert 'class="info-hint-button"' in template
    assert 'class="info-hint-popover"' in template
    assert ':class="[`align-${align}`, { \'is-open\': isOpen }]"' in template
    assert ':aria-describedby="tooltipId"' in template
    assert ':aria-expanded="isOpen ? \'true\' : \'false\'"' in template
    assert ':id="tooltipId"' in template
    assert 'role="tooltip"' in template


def test_info_hint_has_click_focus_escape_blur_and_outside_close_paths() -> None:
    source = _info_hint_source()
    template = _template(source)

    assert '@focus="openTooltip"' in template
    assert '@mouseenter="openTooltip"' in template
    assert '@mouseleave="closeTooltipIfUnfocused"' in template
    assert '@pointerdown="handleButtonPointerDown"' in template
    assert '@click.stop="handleButtonClick"' in template
    assert '@keydown.esc.stop.prevent="closeTooltip"' in template
    assert '@focusout="handleFocusOut"' in template
    assert "document.addEventListener('pointerdown', handleDocumentPointerDown)" in source
    assert "document.removeEventListener('pointerdown', handleDocumentPointerDown)" in source
    assert "rootRef.value?.contains(event.target)" in source
    assert "rootRef.value?.contains(nextTarget)" in source


def test_info_hint_mobile_bounds_are_component_scoped() -> None:
    source = _info_hint_source()
    style = _style(source)
    global_style = load_style_source()

    assert "position: fixed" in style
    assert "width: min(300px, calc(100vw - 24px))" in style
    assert "max-width: calc(100vw - 24px)" in style
    assert "max-height: min(220px, calc(100vh - 24px))" in style
    assert "overflow-wrap: anywhere" in style
    assert "word-break: break-word" in style
    assert ".info-hint-popover.is-open" in style
    assert ".info-hint:hover .info-hint-popover" not in global_style
    assert ".info-hint:focus-within .info-hint-popover" not in global_style


def test_info_hint_does_not_add_requests_or_engineering_copy() -> None:
    source = _info_hint_source()
    template = _template(source)
    hint_texts: list[str] = []

    for path in FRONTEND_SRC.rglob("*.vue"):
        page_source = _source(path)
        hint_texts.extend(re.findall(r"<InfoHint[^>]*\btext=\"([^\"]*)\"", page_source))

    combined_copy = "\n".join([template, *hint_texts]).lower()

    assert "api(" not in source
    assert "/api/" not in source
    for forbidden in [
        "payload",
        "debug",
        "provider",
        "prompt",
        "token",
        "api",
        "接口",
        "模型",
        "调试",
        "只读辅助",
    ]:
        assert forbidden not in combined_copy
