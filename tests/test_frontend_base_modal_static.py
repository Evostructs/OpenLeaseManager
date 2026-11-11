from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
BASE_MODAL = ROOT / "frontend" / "src" / "components" / "BaseModal.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _template(source: str) -> str:
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def test_base_modal_uses_accessible_dialog_title_and_close_button() -> None:
    source = _source(BASE_MODAL)
    template = _template(source)

    assert 'role="dialog"' in template
    assert 'aria-modal="true"' in template
    assert ':aria-labelledby="modalTitleId"' in template
    assert ':id="modalTitleId"' in template
    assert 'aria-label="关闭"' in template
    assert 'data-testid="base-modal-backdrop"' in template
    assert 'data-testid="base-modal-panel"' in template
    assert 'data-testid="base-modal-close"' in template

    assert "useId" in source
    assert "section-label" not in template
    assert "弹窗" not in template
    assert "aria-label=\"title" not in template


def test_base_modal_focuses_panel_without_replacing_close_semantics() -> None:
    source = _source(BASE_MODAL)
    template = _template(source)

    assert "const panelRef = ref(null)" in source
    assert "function focusModalPanel()" in source
    assert "nextTick(" in source
    assert "activateModalFocus(panel, effectiveLayer.value, opener)" in source
    assert "focusSession?.release()" in source
    assert "focusModalPanel()" in source

    assert '@click.self="closeModal"' in template
    assert '@keydown.esc.stop.prevent="closeModal"' in template
    assert '@click="closeModal"' in template
    assert "emit('close')" in source


def test_base_modal_keeps_counted_scroll_lock_for_nested_modals() -> None:
    source = _source(BASE_MODAL)

    assert "MODAL_LOCK_STATE_KEY" in source
    assert "state.count += 1" in source
    assert "state.count = Math.max(0, state.count - 1)" in source
    assert "if (state.count === 0)" in source
    assert "document.documentElement.classList.add(MODAL_SCROLL_LOCK_CLASS)" in source
    assert "document.documentElement.classList.remove(MODAL_SCROLL_LOCK_CLASS)" in source
    assert "let userMoved = false" in source
    assert "const restoreUnlessUserMoved = () =>" in source
    assert "window.addEventListener('wheel', markUserMoved" in source
    assert "window.addEventListener('pointerdown', markUserMoved" in source
    assert "state.restoreToken = Number(state.restoreToken || 0) + 1" in source
    assert "state.restoreToken === restoreToken" in source
    assert "window.requestAnimationFrame(finishRestore)" in source
    assert "window.setTimeout(restoreUnlessUserMoved" not in source
    assert "cleanup()" in source
    assert "onBeforeUnmount(() => { focusSession?.release(); unlockBodyScroll() })" in source


def test_base_modal_mobile_bounds_and_footer_wrap_are_global() -> None:
    style = load_style_source()

    assert ".modal-backdrop" in style
    assert "overflow: auto" in style
    assert "max-width: calc(100vw - 48px)" in style
    assert "max-height: min(90vh, calc(100dvh - 48px))" in style
    assert ".modal-footer" in style
    assert "flex-wrap: wrap" in style
    assert "@media (max-width: 640px)" in style
    assert "max-height: calc(100dvh - 24px)" in style
    assert ".modal-footer .btn" in style
    assert "flex: 1 1 140px" in style


def test_base_modal_template_avoids_engineering_copy() -> None:
    template = _template(_source(BASE_MODAL)).lower()

    for forbidden in [
        "prompt",
        "token",
        "provider",
        "model",
        "api",
        "debug",
        "json",
        "接口",
        "调试",
    ]:
        assert forbidden not in template
