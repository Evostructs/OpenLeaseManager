from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.vue"
TOPBAR = ROOT / "frontend" / "src" / "components" / "TopBar.vue"
HELP_PANEL = ROOT / "frontend" / "src" / "components" / "ControlHelpPanel.vue"
HELP_COPY = ROOT / "frontend" / "src" / "lib" / "controlHelp.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _template(source: str) -> str:
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def test_topbar_exposes_help_toggle_without_replacing_account_actions() -> None:
    source = _source(TOPBAR)

    assert "openHelp" in source
    assert 'data-testid="topbar-help-toggle"' in source
    assert "$emit('openHelp')" in source
    assert "$emit('changePassword')" in source
    assert "$emit('logout')" in source
    assert "$emit('toggleSidebar')" in source


def test_topbar_exposes_explicit_workspace_scope_and_presentation_actions() -> None:
    source = _source(TOPBAR)
    app_source = _source(APP)

    assert "changeWorkspace" in source
    assert 'data-testid="current-workspace"' in source
    assert 'data-testid="current-scope"' in source
    assert ':data-testid="`workspace-switch-${option.value}`"' in source
    assert 'v-if="scopeOptions.length > 1"' in source
    assert ':aria-label="presentationLabel"' in source
    assert "{{ presentationLabel }}" in source
    assert '@change-workspace="changeWorkspace"' in app_source
    assert ':presentation-label="presentationActionLabel"' in app_source
    assert "max-width: 220px" in source
    assert ".topbar-presentation-button span" in source


def test_app_wires_help_panel_to_current_tab_without_business_views() -> None:
    source = _source(APP)

    assert "const isHelpOpen = ref(false)" in source
    assert "const currentControlHelp = computed(() => helpForTab(activeTab.value, tabTitle.value))" in source
    assert '@open-help="isHelpOpen = true"' in source
    assert ':content="currentControlHelp"' in source
    assert '@close="isHelpOpen = false"' in source
    topbar_block = source[source.index("<TopBar") : source.index("/>", source.index("<TopBar"))]
    assert "activeTab.value =" not in topbar_block


def test_help_panel_has_required_selectors_and_close_paths() -> None:
    source = _source(HELP_PANEL)

    for selector in [
        'data-testid="control-help-panel"',
        'data-testid="control-help-current-page"',
        'data-testid="control-help-close"',
    ]:
        assert selector in source

    assert "keydown" in source
    assert '@keydown.esc.stop.prevent="close"' in source
    assert '@click.self="close"' in source


def test_help_panel_and_copy_do_not_request_backend_or_use_engineering_words() -> None:
    combined = "\n".join([_template(_source(HELP_PANEL)), _source(HELP_COPY)])

    assert "api(" not in combined
    assert "/api/" not in combined

    for forbidden in [
        "payload",
        "debug",
        "provider",
        "prompt",
        "token",
        "API",
        "接口",
        "模型",
        "调试",
        "只读辅助",
    ]:
        assert forbidden not in combined


def test_help_panel_mobile_width_is_bounded() -> None:
    source = _source(HELP_PANEL)

    assert "calc(100vw - 48px)" in source
    assert "calc(100vw - 24px)" in source
    assert "word-break: break-word" in source
