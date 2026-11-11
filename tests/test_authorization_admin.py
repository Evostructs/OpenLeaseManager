from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    OperationLog,
    Portfolio,
    PortfolioBuilding,
    User,
    UserAuthorizationRevision,
    UserCapability,
)


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert response.status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin123"}
    ).status_code == 200


def _user_id(client: TestClient, username: str) -> int:
    response = client.get("/api/auth/users")
    assert response.status_code == 200
    return next(int(item["id"]) for item in response.json() if item["username"] == username)


def _add_other_building() -> None:
    with SessionLocal() as db:
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        building = Building(
            code="OTHER_AUTH",
            name="授权测试楼",
            ingestion_mode="snapshot",
            is_active=True,
        )
        db.add(building)
        db.flush()
        db.add(
            PortfolioBuilding(
                portfolio_id=portfolio.id,
                building_id=building.id,
                valid_from=today_in_timezone(),
            )
        )
        db.commit()


def test_platform_admin_replaces_current_scope_with_stable_codes_and_invalidates_target_session() -> None:
    with TestClient(app) as client, TestClient(app) as stale_viewer:
        _register_admin(client)
        assert client.post(
            "/api/auth/create-user",
            json={
                "username": "viewer",
                "password": "Viewer123",
                "display_name": "Viewer",
                "role": "viewer",
            },
        ).status_code == 200
        viewer_id = _user_id(client, "viewer")
        assert stale_viewer.post(
            "/api/auth/login", json={"username": "viewer", "password": "Viewer123"}
        ).status_code == 200
        assert stale_viewer.get("/api/buildings").status_code == 200
        _add_other_building()
        current_authorization = client.get(
            f"/api/auth/authorizations/users/{viewer_id}"
        ).json()

        response = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": current_authorization["authorization_version"],
                "grants": [
                    {
                        "scope_type": "building",
                        "scope_code": "demo_a",
                        "access_level": "view",
                    },
                    {
                        "scope_type": "building",
                        "scope_code": "OTHER_AUTH",
                        "access_level": "view",
                    },
                ],
                "capabilities": [],
            },
        )
        assert response.status_code == 200
        assert {item["code"] for item in response.json()["scope"]["items"]} == {
            "DEMO_A",
            "OTHER_AUTH",
        }
        stale_retry = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": current_authorization["authorization_version"],
                "grants": [
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_A",
                        "access_level": "view",
                    }
                ],
                "capabilities": [],
            },
        )
        assert stale_retry.status_code == 409
        platform_audit = client.get(
            "/api/audit/platform/logs", params={"action": "authorization_replace"}
        )
        assert platform_audit.status_code == 200
        assert platform_audit.json()["total"] == 1
        assert platform_audit.json()["items"][0]["target_id"] == viewer_id
        assert stale_viewer.get("/api/buildings").status_code == 401
        assert client.post(
            "/api/auth/login", json={"username": "viewer", "password": "Viewer123"}
        ).status_code == 200
        catalog = client.get("/api/buildings")
        assert catalog.status_code == 200
        assert {item["code"] for item in catalog.json()["items"]} == {"DEMO_A", "OTHER_AUTH"}
        assert client.get(f"/api/auth/authorizations/users/{viewer_id}").status_code == 403
        assert client.get("/api/audit/platform/logs").status_code == 403

    with SessionLocal() as db:
        audit = (
            db.query(OperationLog)
            .filter(
                OperationLog.action == "authorization_replace",
                OperationLog.target_type == "user_authorization",
                OperationLog.target_id == viewer_id,
            )
            .one()
        )
        assert audit.scope_type == "global"
        assert audit.building_id is None
        assert audit.portfolio_id is None
        history = (
            db.query(UserAuthorizationRevision)
            .filter(UserAuthorizationRevision.target_user_id == viewer_id)
            .one()
        )
        assert history.revision == 1
        assert json.loads(history.before_state)["grants"][0]["scope_code"] == "DEMO_A"
        assert {item["scope_code"] for item in json.loads(history.after_state)["grants"]} == {
            "DEMO_A",
            "OTHER_AUTH",
        }


def test_platform_admin_can_revoke_all_scopes_and_invalidate_target_session() -> None:
    with TestClient(app) as admin, TestClient(app) as stale_viewer:
        _register_admin(admin)
        created = admin.post(
            "/api/auth/create-user",
            json={
                "username": "revoked_viewer",
                "password": "Viewer123",
                "display_name": "Revoked Viewer",
                "role": "viewer",
            },
        )
        assert created.status_code == 200, created.text
        viewer_id = _user_id(admin, "revoked_viewer")
        login = stale_viewer.post(
            "/api/auth/login",
            json={"username": "revoked_viewer", "password": "Viewer123"},
        )
        assert login.status_code == 200, login.text
        assert stale_viewer.get("/api/buildings").status_code == 200

        current = admin.get(f"/api/auth/authorizations/users/{viewer_id}")
        assert current.status_code == 200, current.text
        revoked = admin.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": current.json()["authorization_version"],
                "grants": [],
                "capabilities": [],
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["grants"] == []
        assert revoked.json()["scope"]["items"] == []
        assert revoked.json()["scope"]["portfolio"]["items"] == []
        assert revoked.json()["scope"]["portfolio_overview"]["items"] == []
        assert stale_viewer.get("/api/auth/me").status_code == 401

        relogin = stale_viewer.post(
            "/api/auth/login",
            json={"username": "revoked_viewer", "password": "Viewer123"},
        )
        assert relogin.status_code == 200, relogin.text
        refreshed = stale_viewer.get("/api/auth/me")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["scope"]["items"] == []
        assert refreshed.json()["scope"]["portfolio"]["items"] == []
        assert refreshed.json()["scope"]["portfolio_overview"]["items"] == []


def test_authorization_rejects_numeric_codes_edit_for_viewer_and_last_platform_admin_removal() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        assert client.post(
            "/api/auth/create-user",
            json={
                "username": "viewer",
                "password": "Viewer123",
                "display_name": "Viewer",
                "role": "viewer",
            },
        ).status_code == 200
        viewer_id = _user_id(client, "viewer")
        admin_id = _user_id(client, "admin")
        viewer_version = client.get(
            f"/api/auth/authorizations/users/{viewer_id}"
        ).json()["authorization_version"]
        admin_version = client.get(
            f"/api/auth/authorizations/users/{admin_id}"
        ).json()["authorization_version"]

        numeric = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": viewer_version,
                "grants": [
                    {"scope_type": "building", "scope_code": "1", "access_level": "view"}
                ],
                "capabilities": [],
            },
        )
        assert numeric.status_code == 422

        wildcard = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": viewer_version,
                "grants": [
                    {"scope_type": "building", "scope_code": "%", "access_level": "view"}
                ],
                "capabilities": [],
            },
        )
        assert wildcard.status_code == 404

        padded_code = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": viewer_version,
                "grants": [
                    {"scope_type": "building", "scope_code": " DEMO_A ", "access_level": "view"}
                ],
                "capabilities": [],
            },
        )
        assert padded_code.status_code == 422

        viewer_edit = client.put(
            f"/api/auth/authorizations/users/{viewer_id}",
            json={
                "expected_authorization_version": viewer_version,
                "grants": [
                    {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                ],
                "capabilities": [],
            },
        )
        assert viewer_edit.status_code == 422

        remove_last_admin = client.put(
            f"/api/auth/authorizations/users/{admin_id}",
            json={
                "expected_authorization_version": admin_version,
                "grants": [
                    {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                ],
                "capabilities": ["customer_master_manage"],
            },
        )
        assert remove_last_admin.status_code == 409
        current = client.get("/api/auth/me")
        assert current.status_code == 200
        assert "platform_admin" in current.json()["scope"]["effective_capabilities"]


def test_raw_include_future_change_increments_revision_and_rejects_stale_compare_and_swap() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        assert client.post(
            "/api/auth/create-user",
            json={
                "username": "portfolio_viewer",
                "password": "Viewer123",
                "display_name": "Portfolio Viewer",
                "role": "viewer",
            },
        ).status_code == 200
        user_id = _user_id(client, "portfolio_viewer")
        initial = client.get(f"/api/auth/authorizations/users/{user_id}").json()

        first = client.put(
            f"/api/auth/authorizations/users/{user_id}",
            json={
                "expected_authorization_version": initial["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio",
                        "scope_code": "DEMO_OFFICE_PORTFOLIO",
                        "access_level": "view",
                        "include_future_buildings": False,
                    }
                ],
                "capabilities": [],
            },
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["authorization_revision"] == 1

        second = client.put(
            f"/api/auth/authorizations/users/{user_id}",
            json={
                "expected_authorization_version": first_body["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio",
                        "scope_code": "DEMO_OFFICE_PORTFOLIO",
                        "access_level": "view",
                        "include_future_buildings": True,
                    }
                ],
                "capabilities": [],
            },
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["authorization_revision"] == 2
        assert second_body["authorization_version"] != first_body["authorization_version"]

        stale = client.put(
            f"/api/auth/authorizations/users/{user_id}",
            json={
                "expected_authorization_version": first_body["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio",
                        "scope_code": "DEMO_OFFICE_PORTFOLIO",
                        "access_level": "view",
                        "include_future_buildings": False,
                    }
                ],
                "capabilities": [],
            },
        )
        assert stale.status_code == 409

    with SessionLocal() as db:
        history = (
            db.query(UserAuthorizationRevision)
            .filter(UserAuthorizationRevision.target_user_id == user_id)
            .order_by(UserAuthorizationRevision.revision.asc())
            .all()
        )
        assert [item.revision for item in history] == [1, 2]
        assert json.loads(history[0].after_state)["grants"][0]["include_future_buildings"] is False
        assert json.loads(history[1].after_state)["grants"][0]["include_future_buildings"] is True
        history[0].after_state = "{}"
        with pytest.raises(IntegrityError, match="authorization revision history is immutable"):
            db.commit()
        db.rollback()
        first_history = (
            db.query(UserAuthorizationRevision)
            .filter(
                UserAuthorizationRevision.target_user_id == user_id,
                UserAuthorizationRevision.revision == 1,
            )
            .one()
        )
        db.delete(first_history)
        with pytest.raises(IntegrityError, match="authorization revision history is immutable"):
            db.commit()
        db.rollback()


def test_concurrent_cross_revocation_serializes_and_preserves_one_platform_admin(monkeypatch) -> None:
    import app.services.authorization_admin as authorization_service

    with TestClient(app) as admin_a, TestClient(app) as admin_b:
        _register_admin(admin_a)
        assert admin_a.post(
            "/api/auth/create-user",
            json={
                "username": "admin_b",
                "password": "AdminB123",
                "display_name": "Admin B",
                "role": "admin",
            },
        ).status_code == 200
        admin_a_id = _user_id(admin_a, "admin")
        admin_b_id = _user_id(admin_a, "admin_b")

        admin_b_initial = admin_a.get(
            f"/api/auth/authorizations/users/{admin_b_id}"
        ).json()
        grant_admin_b = admin_a.put(
            f"/api/auth/authorizations/users/{admin_b_id}",
            json={
                "expected_authorization_version": admin_b_initial["authorization_version"],
                "grants": [
                    {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                ],
                "capabilities": ["platform_admin", "customer_master_manage"],
            },
        )
        assert grant_admin_b.status_code == 200, grant_admin_b.text
        assert admin_b.post(
            "/api/auth/login", json={"username": "admin_b", "password": "AdminB123"}
        ).status_code == 200

        admin_a_version = admin_a.get(
            f"/api/auth/authorizations/users/{admin_a_id}"
        ).json()["authorization_version"]
        admin_b_version = admin_a.get(
            f"/api/auth/authorizations/users/{admin_b_id}"
        ).json()["authorization_version"]

        original_acquire = authorization_service._acquire_authorization_control
        both_ready = threading.Barrier(2)

        def synchronized_acquire(db):
            both_ready.wait(timeout=10)
            return original_acquire(db)

        monkeypatch.setattr(authorization_service, "_acquire_authorization_control", synchronized_acquire)

        def remove_admin_b():
            return admin_a.put(
                f"/api/auth/authorizations/users/{admin_b_id}",
                json={
                    "expected_authorization_version": admin_b_version,
                    "grants": [
                        {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                    ],
                    "capabilities": ["customer_master_manage"],
                },
            )

        def remove_admin_a():
            return admin_b.put(
                f"/api/auth/authorizations/users/{admin_a_id}",
                json={
                    "expected_authorization_version": admin_a_version,
                    "grants": [
                        {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                    ],
                    "capabilities": ["customer_master_manage"],
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = [
                executor.submit(remove_admin_b),
                executor.submit(remove_admin_a),
            ]
            statuses = sorted(future.result(timeout=20).status_code for future in responses)
        assert statuses == [200, 409]
        monkeypatch.setattr(authorization_service, "_acquire_authorization_control", original_acquire)

        with SessionLocal() as db:
            active_admin_ids = {
                int(user_id)
                for (user_id,) in (
                    db.query(UserCapability.user_id)
                    .join(User, User.id == UserCapability.user_id)
                    .filter(
                        UserCapability.capability == "platform_admin",
                        UserCapability.valid_from <= today_in_timezone(),
                        (UserCapability.valid_to.is_(None))
                        | (UserCapability.valid_to >= today_in_timezone()),
                        User.is_active.is_(True),
                    )
                    .all()
                )
            }
        assert len(active_admin_ids) == 1
        surviving_id = next(iter(active_admin_ids))
        surviving_client = admin_a if surviving_id == admin_a_id else admin_b
        current = surviving_client.get(
            f"/api/auth/authorizations/users/{surviving_id}"
        )
        assert current.status_code == 200
        remove_last = surviving_client.put(
            f"/api/auth/authorizations/users/{surviving_id}",
            json={
                "expected_authorization_version": current.json()["authorization_version"],
                "grants": [
                    {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
                ],
                "capabilities": ["customer_master_manage"],
            },
        )
        assert remove_last.status_code == 409
