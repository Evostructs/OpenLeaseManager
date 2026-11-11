from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.core.account_name_policy import (
    AccountNamePolicyError,
    validate_new_account_names,
)
from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models import User


@pytest.mark.parametrize(
    "username",
    (
        "abc",
        "john.doe",
        "user_name-01",
        "upper.case",
        "root",
        "sysadmin",
    ),
)
def test_account_name_policy_accepts_normal_login_names(username: str) -> None:
    result = validate_new_account_names(username, "  正常员工  ")

    assert result.username == username
    assert result.display_name == "正常员工"
    assert result.policy_version


@pytest.mark.parametrize(
    "username",
    (
        "",
        "ab",
        "a" * 33,
        " leading",
        "trailing ",
        "_leading",
        "trailing_",
        "中文用户",
        "abc/def",
        "ａｄｍｉｎ",
        "Upper.Case",
    ),
)
def test_account_name_policy_rejects_ambiguous_login_names(username: str) -> None:
    with pytest.raises(AccountNamePolicyError) as captured:
        validate_new_account_names(username, "正常员工")

    assert captured.value.code == "username_format"


@pytest.mark.parametrize(
    ("username", "display_name"),
    (
        ("falun-gong", "正常员工"),
        ("fa1un-gong", "正常员工"),
        ("f4lun-g0ng", "正常员工"),
        ("taiwan-independence", "正常员工"),
        ("normal-user", "法·轮·功"),
        ("normal-user", "法🟡輪🟡功"),
        ("normal-user", "法x轮功"),
        ("normal-user", "fаlungong"),
        ("normal-user", "fαlungong"),
        ("normal-user", "falungοng"),
        ("normal-user", "fǎlúngōng"),
        ("normal-user", "法2轮功"),
        ("normal-user", "中央人民政府官方"),
        ("normal-user", "官方账号"),
        ("normal-user", "DEMO_A官方账号"),
        ("normal-user", "公安部客服"),
        ("normal-user", "国家税务总局客服"),
        ("normal-user", "上海市公安局客服"),
        ("normal-user", "上海市人民政府客服"),
        ("normal-user", "DEMO_A客服"),
        ("normal-user", "DEMO_A账号"),
        ("normal-user", "官方客服"),
        ("normal-user", "博彩-平台客服"),
        ("normal-user", "法轮功总部"),
        ("normal-user", "台独组织"),
        ("normal-user", "台独支持"),
        ("normal-user", "台独支持者"),
        ("normal-user", "台独分子"),
        ("normal-user", "台独势力"),
        ("normal-user", "台独联盟"),
        ("normal-user", "台独主张"),
        ("normal-user", "代开发票平台"),
        ("normal-user", "博彩平台客服中心"),
    ),
)
def test_account_name_policy_rejects_restricted_and_obfuscated_names(
    username: str,
    display_name: str,
) -> None:
    with pytest.raises(AccountNamePolicyError) as captured:
        validate_new_account_names(username, display_name)

    assert captured.value.code == "restricted_content"
    assert str(captured.value) == "该账号名称不可用，请更换"
    assert display_name not in str(captured.value)


@pytest.mark.parametrize(
    "display_name",
    (
        "台湾项目组",
        "国务院业务联络员",
        "浦东新区招商服务",
        "系统运维组",
        "反洗钱服务组",
        "博彩平台风险研究",
        "代开发票审核员",
        "法轮功能测试",
        "平台独立运行",
        "后台独立运行",
        "前台独立账号",
        "平台独家运营",
        "α测试组",
    ),
)
def test_account_name_policy_keeps_legitimate_near_matches(display_name: str) -> None:
    result = validate_new_account_names("normal-user", display_name)

    assert result.display_name == display_name


def test_account_name_policy_falls_back_to_username_for_blank_display_name() -> None:
    result = validate_new_account_names("normal-user", "   ")

    assert result.display_name == "normal-user"


def test_account_name_policy_exposes_versioned_rules_digest() -> None:
    result = validate_new_account_names("normal-user", "正常员工")

    assert re.fullmatch(r"2026-08-22\.4\+account-name-v2\+[0-9a-f]{12}", result.policy_version)


@pytest.mark.parametrize(
    "payload",
    (
        [],
        {"version": "test", "rules": []},
        {"version": "test", "rules": [{"id": "broken", "mode": "regex", "terms": ["("]}]},
    ),
)
def test_account_name_policy_fails_closed_for_invalid_rule_tables(tmp_path, monkeypatch, payload) -> None:
    import app.core.account_name_policy as policy

    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(policy, "_RULES_PATH", rules_path)
    policy._load_rules.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            policy.load_account_name_policy()
    finally:
        policy._load_rules.cache_clear()


def test_account_name_policy_rejects_hidden_display_characters() -> None:
    for display_name in ("张\u200b三", "张\u200d三", "张\ufe0f三"):
        with pytest.raises(AccountNamePolicyError) as captured:
            validate_new_account_names("normal-user", display_name)

        assert captured.value.code == "display_name_format"


def test_initial_admin_registration_uses_account_name_policy() -> None:
    with TestClient(app) as client:
        rejected = client.post(
            "/api/auth/register",
            json={
                "username": "admin",
                "password": "Admin123",
                "display_name": "法-轮-功",
            },
        )

        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "该账号名称不可用，请更换"
        assert client.get("/api/auth/status").json()["need_init"] is True

        accepted = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "管理员"},
        )
        assert accepted.status_code == 200


def test_platform_admin_creation_uses_same_account_name_policy() -> None:
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "管理员"},
        ).status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin123"},
        ).status_code == 200

        for payload in (
            {
                "username": "falun-gong",
                "password": "Viewer123",
                "display_name": "正常员工",
                "role": "viewer",
            },
            {
                "username": "normal-user",
                "password": "Viewer123",
                "display_name": "天安门-事件",
                "role": "viewer",
            },
        ):
            rejected = client.post("/api/auth/create-user", json=payload)
            assert rejected.status_code == 422
            assert rejected.json()["detail"] == "该账号名称不可用，请更换"

        accepted = client.post(
            "/api/auth/create-user",
            json={
                "username": "normal-user",
                "password": "Viewer123",
                "display_name": "  正常员工  ",
                "role": "viewer",
            },
        )
        assert accepted.status_code == 200

        users = client.get("/api/auth/users").json()
        created = next(item for item in users if item["username"] == "normal-user")
        assert created["display_name"] == "正常员工"


@pytest.mark.parametrize("legacy_username", ("Viewer ", "Ｖｉｅｗｅｒ"))
def test_new_account_cannot_visually_duplicate_a_legacy_username(legacy_username: str) -> None:
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "管理员"},
        ).status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin123"},
        ).status_code == 200
        with SessionLocal() as db:
            db.add(
                User(
                    username=legacy_username,
                    password_hash=hash_password("Legacy123"),
                    display_name="历史账号",
                    role="viewer",
                    is_active=True,
                )
            )
            db.commit()

        response = client.post(
            "/api/auth/create-user",
            json={
                "username": "viewer",
                "password": "Viewer123",
                "display_name": "新账号",
                "role": "viewer",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "用户名已存在"


def test_policy_updates_do_not_lock_out_existing_legacy_accounts() -> None:
    with SessionLocal() as db:
        db.add(
            User(
                username="legacy user",
                password_hash=hash_password("Legacy123"),
                display_name="历史账号",
                role="viewer",
                is_active=True,
            )
        )
        db.commit()

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "legacy user", "password": "Legacy123"},
        )

    assert response.status_code == 200
