from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "views" / "LoginView.vue"
INDEX = ROOT / "frontend" / "index.html"


def _view_source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _template_source() -> str:
    match = re.search(r"<template>(.*)</template>", _view_source(), re.S)
    assert match is not None
    return match.group(1)


def _style_source() -> str:
    return load_style_source()


def test_login_page_has_commercial_entry_structure() -> None:
    template = _template_source()

    for selector in [
        'data-testid="login-entry"',
        'data-testid="login-brand-panel"',
        'data-testid="login-signal-band"',
        'data-testid="login-signal-contracts"',
        'data-testid="login-signal-receivables"',
        'data-testid="login-signal-risk"',
        'data-testid="login-card"',
        'data-testid="login-form"',
        'data-testid="login-mode"',
    ]:
        assert selector in template

    for expected in [
        "多楼宇租赁运营平台",
        "租赁管理系统",
        "合同台账",
        "月度应收",
        "风险中心",
    ]:
        assert expected in template

    assert "/lease-manager-mark.svg" in template
    assert "DEMO_A 运营后台" not in template
    assert "\u9886\u5bfc" not in template


def test_global_branding_is_multibuilding_neutral() -> None:
    index = INDEX.read_text(encoding="utf-8")

    assert '<title>OpenLeaseOps · Lease Management</title>' in index
    assert 'href="/lease-manager-mark.svg"' in index
    assert "portfolio analytics" in index
    assert "<title>DEMO_A " not in index
    assert "\u9886\u5bfc" not in index


def test_login_and_initialization_modes_share_existing_auth_events() -> None:
    source = _view_source()
    template = _template_source()

    assert "defineEmits(['login', 'register'])" in source
    assert "function submitAuth()" in source
    assert "emit(props.needInit ? 'register' : 'login', payload)" in source
    assert '@submit.prevent="submitAuth"' in template
    assert 'type="submit"' in template
    assert 'data-testid="register-submit"' in template
    assert 'data-testid="login-submit"' in template
    assert 'data-testid="login-confirm-password"' in template
    assert "账号登录" in template
    assert "创建管理员" in template
    assert "进入系统" in template
    assert "props.needInit" in template
    assert ':minlength="props.needInit ? 3 : undefined"' in template
    assert ':maxlength="props.needInit ? 32 : undefined"' in template
    assert "[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]" in template
    assert "3—32 位小写字母、数字、点、下划线或短横线" in template


def test_login_copy_is_user_facing_and_concise() -> None:
    template = _template_source()
    visible_text = re.sub(r"<[^>]+>", " ", template)
    lowered = visible_text.lower()

    for forbidden in [
        "token",
        "prompt",
        "provider",
        "model",
        "api",
        "接口",
        "调试",
        "json",
        "debug",
    ]:
        assert forbidden not in lowered

    for noisy_phrase in [
        "不会自动",
        "人工确认",
        "先把",
        "会在这里",
        "请联系管理员重置密码。管理员可在",
    ]:
        assert noisy_phrase not in template

    assert "联系管理员重置密码。" in template


def test_login_layout_has_mobile_overflow_guards() -> None:
    style = _style_source()

    for expected in [
        ".login-entry",
        "width: min(1120px, 100%)",
        "grid-template-columns: minmax(0, 1fr) minmax(360px, 420px)",
        ".login-signal span",
        "text-overflow: ellipsis",
        "@media (max-width: 640px)",
        "grid-template-columns: minmax(0, 1fr)",
        ".login-shell",
        "overflow: auto",
    ]:
        assert expected in style

    login_css_start = style.index(".login-shell")
    login_css_end = style.index(".visually-hidden", login_css_start)
    login_css = style[login_css_start:login_css_end]
    assert "letter-spacing: -" not in login_css
