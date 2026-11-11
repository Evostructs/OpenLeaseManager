from __future__ import annotations

from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest

from app.core.security import hash_password
from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import Building, Portfolio, PortfolioBuilding, User, UserPortfolioOverviewGrant
from app.services.property_scope import resolve_user_scope, select_property_scope


PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
SENTINEL_CONTRACT_NO = "OVERVIEW-LEAK-CONTRACT-2026"
SENTINEL_CUSTOMER_NAME = "概况泄漏哨兵客户"
SENTINEL_ROOM_NO = "OVERVIEW-LEAK-ROOM-2601"


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "系统管理员"},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert response.status_code == 200, response.text


def _create_overview_leader(admin: TestClient, leader: TestClient) -> int:
    response = admin.post(
        "/api/auth/create-user",
        json={
            "username": "portfolio_leader",
            "password": "Leader123",
            "display_name": "板块概况账号",
            "role": "viewer",
            "initial_scope": "none",
        },
    )
    assert response.status_code == 200, response.text
    users = admin.get("/api/auth/users")
    assert users.status_code == 200, users.text
    leader_id = next(
        int(item["id"])
        for item in users.json()
        if item["username"] == "portfolio_leader"
    )
    current = admin.get(f"/api/auth/authorizations/users/{leader_id}")
    assert current.status_code == 200, current.text
    assert current.json()["grants"] == []
    assert current.json()["scope"]["items"] == []
    response = admin.put(
        f"/api/auth/authorizations/users/{leader_id}",
        json={
            "expected_authorization_version": current.json()["authorization_version"],
            "grants": [
                {
                    "scope_type": "portfolio_overview",
                    "scope_code": PORTFOLIO_CODE,
                    "access_level": "view",
                    "include_future_buildings": False,
                }
            ],
            "capabilities": [],
        },
    )
    assert response.status_code == 200, response.text
    response = leader.post(
        "/api/auth/login",
        json={"username": "portfolio_leader", "password": "Leader123"},
    )
    assert response.status_code == 200, response.text
    return leader_id


def _seed_business_leak_sentinels(admin: TestClient) -> tuple[int, int]:
    selected_building = {"building": "DEMO_A"}
    customer = admin.post(
        "/api/customers",
        params=selected_building,
        json={"name": SENTINEL_CUSTOMER_NAME},
    )
    assert customer.status_code == 200, customer.text
    customer_id = int(customer.json()["id"])

    room = admin.post(
        "/api/rooms",
        params=selected_building,
        json={
            "tower": "南塔",
            "floor": "26",
            "room_no": SENTINEL_ROOM_NO,
            "room_type": "办公",
            "area": 100,
        },
    )
    assert room.status_code == 200, room.text

    start_date = today_in_timezone()
    contract = admin.post(
        "/api/contracts",
        params=selected_building,
        json={
            "contract_no": SENTINEL_CONTRACT_NO,
            "customer_id": customer_id,
            "tenant_name": SENTINEL_CUSTOMER_NAME,
            "tower": "南塔",
            "room_no": SENTINEL_ROOM_NO,
            "area": 100,
            "unit_price": 4,
            "monthly_rent": 12000,
            "lease_type": "新签",
            "start_date": start_date.isoformat(),
            "end_date": (start_date + timedelta(days=364)).isoformat(),
        },
    )
    assert contract.status_code == 200, contract.text
    return customer_id, int(contract.json()["id"])


def _assert_overview_only_denial_matrix(
    leader: TestClient,
    *,
    leader_id: int,
    customer_id: int,
    contract_id: int,
) -> None:
    portfolio_params = {"portfolio": PORTFOLIO_CODE}
    as_of_date = today_in_timezone().isoformat()
    cases = (
        # Ordinary business detail APIs.
        ("明细-房源列表", "GET", "/api/rooms", {"params": portfolio_params}),
        (
            "明细-房源合同历史",
            "GET",
            f"/api/rooms/{SENTINEL_ROOM_NO}/contracts",
            {"params": portfolio_params},
        ),
        ("明细-合同列表", "GET", "/api/contracts", {"params": portfolio_params}),
        (
            "明细-合同详情",
            "GET",
            f"/api/contracts/{contract_id}",
            {"params": portfolio_params},
        ),
        ("明细-客户列表", "GET", "/api/customers", {"params": portfolio_params}),
        (
            "明细-客户详情",
            "GET",
            f"/api/customers/{customer_id}",
            {"params": portfolio_params},
        ),
        (
            "明细-经营看板",
            "GET",
            "/api/dashboard/operations",
            {"params": {**portfolio_params, "queue_limit": 0, "risk_limit": 8}},
        ),
        (
            "明细-附件完整性任务",
            "GET",
            "/api/attachments/completeness-tasks",
            {"params": {**portfolio_params, "limit": 100}},
        ),
        (
            "明细-风险列表",
            "GET",
            "/api/risks",
            {"params": {**portfolio_params, "limit": 100}},
        ),
        (
            "明细-经营日报",
            "GET",
            "/api/reports/operations-daily",
            {"params": portfolio_params},
        ),
        # Downloads and exports.
        ("下载-房源台账", "GET", "/api/rooms/export/excel", {"params": portfolio_params}),
        ("下载-合同台账", "GET", "/api/contracts/export/excel", {"params": portfolio_params}),
        ("下载-客户台账", "GET", "/api/customers/export", {"params": portfolio_params}),
        (
            "下载-租金分析",
            "GET",
            "/api/rent-analytics/export",
            {
                "params": {
                    **portfolio_params,
                    "start_date": as_of_date[:8] + "01",
                    "end_date": as_of_date,
                }
            },
        ),
        ("下载-风险台账", "GET", "/api/risks/export", {"params": portfolio_params}),
        ("下载-操作日志", "GET", "/api/audit/export", {"params": portfolio_params}),
        # Snapshot data and workflow APIs.
        (
            "流程-经营快照",
            "GET",
            "/api/operations/snapshot",
            {"params": {**portfolio_params, "queue_limit": 0, "risk_limit": 8}},
        ),
        (
            "流程-已发布快照",
            "GET",
            "/api/operations/published-snapshot",
            {"params": {**portfolio_params, "as_of_date": as_of_date}},
        ),
        (
            "流程-快照证据",
            "GET",
            "/api/operations/snapshot-evidence",
            {
                "params": {
                    **portfolio_params,
                    "metric_code": "occupancy.area_rate",
                    "as_of_date": as_of_date,
                }
            },
        ),
        (
            "流程-数据源",
            "GET",
            "/api/operations/snapshot-sources",
            {"params": portfolio_params},
        ),
        (
            "流程-提交详情",
            "GET",
            "/api/operations/snapshot-submissions/1",
            {"params": portfolio_params},
        ),
        (
            "流程-复核",
            "POST",
            "/api/operations/snapshot-submissions/1/review",
            {
                "params": portfolio_params,
                "json": {"approved": True, "notes": "概况账号拒绝矩阵"},
            },
        ),
        (
            "流程-发布",
            "POST",
            "/api/operations/snapshot-submissions/1/publish",
            {
                "params": portfolio_params,
                "json": {"notes": "概况账号拒绝矩阵"},
            },
        ),
        # Platform and authorization management APIs.
        ("管理-用户列表", "GET", "/api/auth/users", {}),
        (
            "管理-用户授权",
            "GET",
            f"/api/auth/authorizations/users/{leader_id}",
            {},
        ),
        ("管理-楼宇开闸", "GET", "/api/buildings/operating-gates", {}),
        (
            "管理-平台审计",
            "GET",
            "/api/audit/platform/logs",
            {"params": {"page": 1, "page_size": 50}},
        ),
        ("管理-备份", "GET", "/api/backups", {}),
        ("管理-飞书设置", "GET", "/api/settings/feishu", {}),
        ("管理-完整性检查", "GET", "/api/system/integrity-check", {}),
    )

    for label, method, path, kwargs in cases:
        response = leader.request(method, path, **kwargs)
        assert response.status_code != 422, f"{label} 缺少合法必填参数：{response.text}"
        assert response.status_code in {403, 404}, (
            f"{label} 应拒绝概况专用账号，实际 {response.status_code}: {response.text}"
        )


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_overview_projection_never_returns_hidden_building_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import portfolio_overview as overview_service
    from app.services.property_scope import SelectedPropertyScope

    def metric(value: float) -> dict:
        return {
            "value": value,
            "unit": "",
            "as_of_date": today_in_timezone().isoformat(),
            "unavailable_reason": None,
            "coverage": {},
        }

    raw = {
        "definition_version": "OPS_V1",
        "definition_status": "published",
        "gate_policy_version": "test-policy",
        "as_of_date": today_in_timezone().isoformat(),
        "period": {
            "month": today_in_timezone().strftime("%Y-%m"),
            "month_start": today_in_timezone().replace(day=1).isoformat(),
            "month_end": today_in_timezone().isoformat(),
        },
        "coverage": {
            "portfolio_buildings": 2,
            "authorized_buildings": 2,
            "presentation_enabled_buildings": 1,
            "contribution_enabled_buildings": 2,
        },
        "timestamps": {},
        "metrics": {},
        "contributions": [
            {
                "building": {"code": "DEMO_A", "name": "示例双塔中心"},
                "publication_status": "live_adapter",
                "presentation_enabled": True,
                "contribution_enabled": True,
                "gate_state": "open",
                "operating_gate": {
                    "state": "open",
                    "presentation_enabled": True,
                    "contribution_enabled": True,
                },
                "metrics": {"risk.open_items": metric(1)},
            },
            {
                "building": {"code": "DEMO_D", "name": "示例办公中心"},
                "publication_status": "published",
                "presentation_enabled": False,
                "contribution_enabled": True,
                "gate_state": "aggregate_only",
                "operating_gate": {
                    "state": "aggregate_only",
                    "presentation_enabled": False,
                    "contribution_enabled": True,
                },
                "metrics": {"risk.open_items": metric(987654321)},
            },
        ],
        "rollout": {
            "policy_version": "test-policy",
            "evaluated_at": now_in_timezone().isoformat(),
            "authorized_buildings": 2,
            "presentation_enabled_buildings": 1,
            "contribution_enabled_buildings": 2,
            "items": [
                {
                    "building_code": "DEMO_A",
                    "building_name": "示例双塔中心",
                    "state": "open",
                    "presentation_enabled": True,
                    "contribution_enabled": True,
                },
                {
                    "building_code": "DEMO_D",
                    "building_name": "示例办公中心",
                    "state": "aggregate_only",
                    "presentation_enabled": False,
                    "contribution_enabled": True,
                },
            ],
        },
        "presentation": {"trend": [], "contributions": []},
    }
    monkeypatch.setattr(
        overview_service,
        "build_unified_operating_snapshot",
        lambda *_args, **_kwargs: raw,
    )

    db = SessionLocal()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.code == PORTFOLIO_CODE).one()
        buildings = {
            item.code: item
            for item in db.query(Building)
            .filter(Building.code.in_(("DEMO_A", "DEMO_D")))
            .all()
        }
        scope = SelectedPropertyScope(
            user_id=1,
            building_ids=(int(buildings["DEMO_A"].id), int(buildings["DEMO_D"].id)),
            building_codes=("DEMO_A", "DEMO_D"),
            scope_type="portfolio",
            scope_code=PORTFOLIO_CODE,
            portfolio_id=int(portfolio.id),
            access_level="overview",
            authorization_version="overview-projection-test",
        )
        projected = overview_service.build_portfolio_overview(db, scope=scope)
    finally:
        db.close()

    assert [item["building"]["code"] for item in projected["contributions"]] == [
        "DEMO_A"
    ]
    assert [
        item["building"]["code"]
        for item in projected["presentation"]["contributions"]
    ] == ["DEMO_A"]
    assert {item["building_code"] for item in projected["rollout"]["items"]} == {
        "DEMO_A",
        "DEMO_D",
    }
    visible_metric = projected["contributions"][0]["metrics"]["risk.open_items"]
    assert visible_metric["name"] == "未关闭风险事项"
    assert visible_metric["unit"] == "item"
    assert "987654321" not in json.dumps(projected, ensure_ascii=False, sort_keys=True)


def test_overview_only_leader_can_read_strict_portfolio_projection_without_business_scope() -> None:
    with TestClient(app) as admin, TestClient(app) as leader:
        _register_admin(admin)
        customer_id, contract_id = _seed_business_leak_sentinels(admin)
        leader_id = _create_overview_leader(admin, leader)

        me = leader.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.headers["cache-control"] == "private, no-store"
        scope = me.json()["scope"]
        assert scope["items"] == []
        assert scope["portfolio"]["items"] == []
        assert [item["code"] for item in scope["portfolio_overview"]["items"]] == [
            PORTFOLIO_CODE
        ]
        assert scope["effective_capabilities"] == []

        overview = leader.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": PORTFOLIO_CODE},
        )
        assert overview.status_code == 200, overview.text
        assert overview.headers["cache-control"] == "private, no-store"
        payload = overview.json()
        assert payload["contract_version"] == "portfolio-overview-v1"
        assert payload["scope"]["scope_type"] == "portfolio"
        assert payload["scope"]["scope_code"] == PORTFOLIO_CODE
        assert payload["coverage"]["authorized_buildings"] == 5
        assert len(payload["contributions"]) == 5
        assert payload["data_classification"] == "simulated"
        profiles = {
            item["building"]["code"]: item["building"]["operations_profile"]
            for item in payload["contributions"]
        }
        assert profiles["DEMO_B"]["space_structure"]["type"] == (
            "main_building_with_podium"
        )
        assert profiles["DEMO_C"]["space_structure"]["zones"] == []
        assert "available_tabs" not in profiles["DEMO_B"]
        assert "contract_number_rule" not in profiles["DEMO_B"]
        assert "import_rule" not in profiles["DEMO_B"]
        forbidden_keys = {
            "id",
            "building_id",
            "snapshot_id",
            "revision",
            "event_id",
            "actor_id",
            "previous_event_id",
            "source_id",
            "source_ids",
            "batch_id",
            "batch_ids",
            "source_record_id",
            "source_record_ids",
            "source_file_name",
            "external_batch_no",
            "file_checksum",
            "submitted_by",
            "reviewed_by",
            "published_by",
            "legacy_contract_stats",
            "legacy_operations",
            "sources",
            "adapters",
            "queues",
            "todos",
        }
        assert forbidden_keys.isdisjoint(set(_walk_keys(payload)))
        serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for sentinel in (
            SENTINEL_CONTRACT_NO,
            SENTINEL_CUSTOMER_NAME,
            SENTINEL_ROOM_NO,
        ):
            assert sentinel not in serialized_payload

        for rejected_query in (
            {"portfolio": "1"},
            {"portfolio": PORTFOLIO_CODE, "building": "DEMO_A"},
            {"portfolio": PORTFOLIO_CODE, "queue_limit": "1"},
        ):
            rejected = leader.get(
                "/api/operations/portfolio-overview",
                params=rejected_query,
            )
            assert rejected.status_code == 422, rejected.text

        for duplicate_query in (
            [("portfolio", PORTFOLIO_CODE), ("portfolio", PORTFOLIO_CODE)],
            [
                ("portfolio", PORTFOLIO_CODE),
                ("as_of_date", today_in_timezone().isoformat()),
                ("as_of_date", today_in_timezone().isoformat()),
            ],
        ):
            rejected = leader.get(
                "/api/operations/portfolio-overview",
                params=duplicate_query,
            )
            assert rejected.status_code == 422, rejected.text

        not_granted = leader.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": "NOT_GRANTED_PORTFOLIO"},
        )
        assert not_granted.status_code == 404, not_granted.text

        _assert_overview_only_denial_matrix(
            leader,
            leader_id=leader_id,
            customer_id=customer_id,
            contract_id=contract_id,
        )


def test_hybrid_account_keeps_overview_and_demo_a_business_grants_separate() -> None:
    with TestClient(app) as admin, TestClient(app) as hybrid:
        _register_admin(admin)
        _seed_business_leak_sentinels(admin)
        created = admin.post(
            "/api/auth/create-user",
            json={
                "username": "hybrid_workspace",
                "password": "Hybrid123",
                "display_name": "概况兼楼宇运营",
                "role": "editor",
                "initial_scope": "none",
            },
        )
        assert created.status_code == 200, created.text
        users = admin.get("/api/auth/users")
        assert users.status_code == 200, users.text
        hybrid_id = next(
            int(item["id"])
            for item in users.json()
            if item["username"] == "hybrid_workspace"
        )
        current = admin.get(f"/api/auth/authorizations/users/{hybrid_id}")
        assert current.status_code == 200, current.text
        authorized = admin.put(
            f"/api/auth/authorizations/users/{hybrid_id}",
            json={
                "expected_authorization_version": current.json()["authorization_version"],
                "grants": [
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_A",
                        "access_level": "edit",
                        "include_future_buildings": False,
                    },
                    {
                        "scope_type": "portfolio_overview",
                        "scope_code": PORTFOLIO_CODE,
                        "access_level": "view",
                        "include_future_buildings": False,
                    },
                ],
                "capabilities": [],
            },
        )
        assert authorized.status_code == 200, authorized.text
        logged_in = hybrid.post(
            "/api/auth/login",
            json={"username": "hybrid_workspace", "password": "Hybrid123"},
        )
        assert logged_in.status_code == 200, logged_in.text

        me = hybrid.get("/api/auth/me")
        assert me.status_code == 200, me.text
        scope = me.json()["scope"]
        assert [item["code"] for item in scope["items"]] == ["DEMO_A"]
        assert scope["portfolio"]["items"] == []
        assert [item["code"] for item in scope["portfolio_overview"]["items"]] == [
            PORTFOLIO_CODE
        ]

        overview = hybrid.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": PORTFOLIO_CODE},
        )
        assert overview.status_code == 200, overview.text
        serialized_overview = json.dumps(overview.json(), ensure_ascii=False, sort_keys=True)
        assert SENTINEL_CONTRACT_NO not in serialized_overview
        assert SENTINEL_CUSTOMER_NAME not in serialized_overview
        assert SENTINEL_ROOM_NO not in serialized_overview

        demo_a_contracts = hybrid.get("/api/contracts", params={"building": "DEMO_A"})
        assert demo_a_contracts.status_code == 200, demo_a_contracts.text
        assert SENTINEL_CONTRACT_NO in demo_a_contracts.text
        other_building_contracts = hybrid.get(
            "/api/contracts",
            params={"building": "DEMO_B"},
        )
        assert other_building_contracts.status_code == 404, other_building_contracts.text


def test_overview_grant_never_becomes_business_scope_or_silently_adds_future_buildings() -> None:
    db = SessionLocal()
    try:
        today = today_in_timezone()
        granted_at = now_in_timezone()
        portfolio = db.query(Portfolio).filter(Portfolio.code == PORTFOLIO_CODE).one()
        leader = User(
            username="scope_only_leader",
            display_name="仅概况账号",
            password_hash=hash_password("Leader123"),
            role="viewer",
            is_active=True,
        )
        db.add(leader)
        db.flush()
        db.add(
            UserPortfolioOverviewGrant(
                user_id=int(leader.id),
                portfolio_id=int(portfolio.id),
                include_future_buildings=False,
                valid_from=today,
                valid_to=None,
                granted_at=granted_at,
            )
        )
        future = Building(
            code="OVERVIEW_FUTURE",
            name="后来加入的楼宇",
            timezone="Asia/Shanghai",
            currency="CNY",
            ingestion_mode="snapshot",
            is_active=True,
            created_at=granted_at + timedelta(minutes=1),
        )
        db.add(future)
        db.flush()
        db.add(
            PortfolioBuilding(
                portfolio_id=int(portfolio.id),
                building_id=int(future.id),
                valid_from=today,
                valid_to=None,
                created_at=granted_at + timedelta(minutes=1),
            )
        )
        db.commit()

        resolved = resolve_user_scope(db, leader, as_of_date=today)
        assert resolved.view_building_codes == ()
        assert resolved.edit_building_codes == ()
        assert resolved.portfolios == ()
        assert len(resolved.portfolio_overviews) == 1
        assert "OVERVIEW_FUTURE" not in resolved.portfolio_overviews[0].building_codes

        try:
            select_property_scope(resolved, portfolio=PORTFOLIO_CODE)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("概况授权不得被普通板块选择器接受")
    finally:
        db.close()


def test_replacing_an_unchanged_frozen_overview_grant_keeps_its_original_membership_boundary() -> None:
    with TestClient(app) as admin, TestClient(app) as leader:
        _register_admin(admin)
        leader_id = _create_overview_leader(admin, leader)

        db = SessionLocal()
        try:
            portfolio = db.query(Portfolio).filter(Portfolio.code == PORTFOLIO_CODE).one()
            grant = (
                db.query(UserPortfolioOverviewGrant)
                .filter(UserPortfolioOverviewGrant.user_id == leader_id)
                .one()
            )
            original_granted_at = grant.granted_at
            assert original_granted_at is not None
            late_building = Building(
                code="OVERVIEW_LATE_REPLACE",
                name="授权后加入的楼宇",
                timezone="Asia/Shanghai",
                currency="CNY",
                ingestion_mode="snapshot",
                is_active=True,
                created_at=original_granted_at + timedelta(microseconds=1),
            )
            db.add(late_building)
            db.flush()
            db.add(
                PortfolioBuilding(
                    portfolio_id=int(portfolio.id),
                    building_id=int(late_building.id),
                    valid_from=today_in_timezone(),
                    valid_to=None,
                    created_at=original_granted_at + timedelta(microseconds=1),
                )
            )
            db.commit()
        finally:
            db.close()

        current = admin.get(f"/api/auth/authorizations/users/{leader_id}")
        assert current.status_code == 200, current.text
        before_codes = current.json()["scope"]["portfolio_overview"]["items"][0][
            "building_codes"
        ]
        assert "OVERVIEW_LATE_REPLACE" not in before_codes

        replaced = admin.put(
            f"/api/auth/authorizations/users/{leader_id}",
            json={
                "expected_authorization_version": current.json()["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio_overview",
                        "scope_code": PORTFOLIO_CODE,
                        "access_level": "view",
                        "include_future_buildings": False,
                    }
                ],
                "capabilities": [],
            },
        )
        assert replaced.status_code == 200, replaced.text
        after_codes = replaced.json()["scope"]["portfolio_overview"]["items"][0][
            "building_codes"
        ]
        assert after_codes == before_codes
        assert "OVERVIEW_LATE_REPLACE" not in after_codes

        relogin = leader.post(
            "/api/auth/login",
            json={"username": "portfolio_leader", "password": "Leader123"},
        )
        assert relogin.status_code == 200, relogin.text
        overview = leader.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": PORTFOLIO_CODE},
        )
        assert overview.status_code == 200, overview.text
        assert overview.json()["scope"]["building_codes"] == before_codes
        assert overview.json()["coverage"]["authorized_buildings"] == len(before_codes)
        assert overview.json()["coverage"]["portfolio_buildings"] == len(before_codes)


def test_overview_authorization_change_invalidates_existing_session_and_keeps_history() -> None:
    with TestClient(app) as admin, TestClient(app) as leader:
        _register_admin(admin)
        leader_id = _create_overview_leader(admin, leader)
        before_me = leader.get("/api/auth/me")
        assert before_me.status_code == 200, before_me.text
        before_version = before_me.json()["scope"]["authorization_version"]

        current = admin.get(f"/api/auth/authorizations/users/{leader_id}")
        assert current.status_code == 200, current.text
        updated = admin.put(
            f"/api/auth/authorizations/users/{leader_id}",
            json={
                "expected_authorization_version": current.json()["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio_overview",
                        "scope_code": PORTFOLIO_CODE,
                        "access_level": "view",
                        "include_future_buildings": True,
                    }
                ],
                "capabilities": [],
            },
        )
        assert updated.status_code == 200, updated.text
        assert len(updated.json()["grants"]) == 1
        updated_grant = updated.json()["grants"][0]
        assert updated_grant["scope_type"] == "portfolio_overview"
        assert updated_grant["scope_code"] == PORTFOLIO_CODE
        assert updated_grant["access_level"] == "view"
        assert updated_grant["include_future_buildings"] is True
        assert leader.get("/api/auth/me").status_code == 401

        relogin = leader.post(
            "/api/auth/login",
            json={"username": "portfolio_leader", "password": "Leader123"},
        )
        assert relogin.status_code == 200, relogin.text
        after_me = leader.get("/api/auth/me")
        assert after_me.status_code == 200, after_me.text
        assert after_me.json()["scope"]["authorization_version"] != before_version

        db = SessionLocal()
        try:
            from app.models import UserAuthorizationRevision

            history = (
                db.query(UserAuthorizationRevision)
                .filter(UserAuthorizationRevision.target_user_id == leader_id)
                .order_by(UserAuthorizationRevision.revision.asc())
                .all()
            )
            assert [item.revision for item in history] == [1, 2]
            assert all("portfolio_overview" in item.after_state for item in history)
        finally:
            db.close()
