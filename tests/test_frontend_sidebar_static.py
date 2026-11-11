from __future__ import annotations

from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.vue"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "SidebarNav.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sidebar_keeps_groups_open_and_marks_current_context() -> None:
    source = _source(SIDEBAR)

    assert "next[group.key] = true" in source
    assert "if (groupContainsCurrent(group))" in source
    assert ':data-active="groupContainsCurrent(group) ? \'true\' : \'false\'"' in source
    assert 'class="nav-group-title"' in source
    assert 'class="nav-group-active-dot"' in source
    assert ':aria-current="currentTab === item.key ? \'page\' : undefined"' in source
    assert 'data-testid="sidebar-workspace-label"' in source
    assert 'data-testid="sidebar-scope-label"' in source
    assert "DEMO_A 运营后台" not in source


def test_sidebar_hierarchy_is_visual_not_extra_copy() -> None:
    source = _source(SIDEBAR)
    style = load_style_source()

    assert "sidebar-current" not in source
    assert ".nav-group.active" in style
    assert ".nav-button.active::before" in style
    assert "font-size: 13.5px" in style
    assert "font-size: 12.5px" in style
    assert "min-height: 34px" in style
    assert "min-height: 36px" in style


def test_sidebar_labels_stay_single_line_on_narrow_widths() -> None:
    style = load_style_source()

    for selector in [".nav-group-title", ".nav-button-label"]:
        block_start = style.index(selector)
        block_end = style.index("}", block_start)
        block = style[block_start:block_end]
        assert "min-width: 0" in block
        assert "overflow: hidden" in block
        assert "text-overflow: ellipsis" in block
        assert "white-space: nowrap" in block


def test_sidebar_authorization_filtered_entries_stay_in_app_navigation() -> None:
    source = _source(APP)

    assert "{ key: 'receivables', label: '月度应收' }" in source
    assert "items: group.items.filter((item) => allowed.has(item.key))" in source
    assert "const isPlatformAdmin = computed" in source
    assert "effective_capabilities.includes('platform_admin')" in source
    assert "if (isPlatformAdmin.value)" in source
    assert "key: 'system'" in source
    assert "key: 'tools'" in source
    assert "key: 'audit'" in source


def test_sidebar_brand_comes_from_the_selected_operations_profile() -> None:
    app_source = _source(APP)
    sidebar_source = _source(SIDEBAR)

    assert "building?.operations_profile?.brand" in app_source
    assert ':brand="sidebarBrand"' in app_source
    assert 'v-if="brand.logo_url"' in sidebar_source
    assert 'data-testid="sidebar-brand-monogram"' in sidebar_source
    assert '<img src="/lease-manager-mark.svg"' not in sidebar_source
