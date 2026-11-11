from __future__ import annotations

import io

import openpyxl
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Building, OperationLog, Portfolio, User, UserScopeGrant


def _register_and_login(client: TestClient) -> tuple[int, int]:
    if client.get("/api/auth/status").json()["need_init"]:
        assert client.post(
            "/api/auth/register",
            json={"username": "audit_admin", "password": "Admin123", "display_name": "Audit Admin"},
        ).status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "audit_admin", "password": "Admin123"},
    ).status_code == 200

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "audit_admin").one()
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = db.query(Building).filter(Building.code == "AUDIT_OTHER").one_or_none()
        if other is None:
            other = Building(
                code="AUDIT_OTHER",
                name="审计反例楼",
                timezone="Asia/Shanghai",
                currency="CNY",
                ingestion_mode="detail",
                is_provisional=False,
                is_active=True,
            )
            db.add(other)
            db.flush()
        db.add(
            UserScopeGrant(
                user_id=user.id,
                scope_type="building",
                building_id=other.id,
                access_level="view",
                include_future_buildings=False,
                valid_from=demo_a.created_at.date(),
            )
        )
        db.add_all(
            [
                OperationLog(
                    scope_type="building",
                    building_id=demo_a.id,
                    target_type="room",
                    target_id=101,
                    action="room_update",
                    operator="demo_a-user",
                    changes="DEMO_A only",
                ),
                OperationLog(
                    scope_type="building",
                    building_id=other.id,
                    target_type="room",
                    target_id=202,
                    action="room_delete",
                    operator="other-user",
                    changes="OTHER only",
                ),
                OperationLog(
                    scope_type=None,
                    building_id=None,
                    target_type="room",
                    target_id=303,
                    action="room_create",
                    operator="unresolved-user",
                    changes="unresolved",
                ),
            ]
        )
        db.commit()
        return demo_a.id, other.id


def test_audit_list_filters_rows_and_filter_options_before_pagination() -> None:
    with TestClient(app) as client:
        _register_and_login(client)

        demo_a = client.get("/api/audit/logs", params={"building": "DEMO_A"})
        assert demo_a.status_code == 200
        payload = demo_a.json()
        assert payload["total"] == 1
        assert [item["target_id"] for item in payload["items"]] == [101]
        assert payload["operators"] == ["demo_a-user"]
        assert [item["value"] for item in payload["actions"]] == ["room_update"]

        other = client.get("/api/audit/logs", params={"building": "AUDIT_OTHER"})
        assert other.status_code == 200
        assert other.json()["total"] == 1
        assert other.json()["items"][0]["target_id"] == 202

        hidden = client.get(
            "/api/audit/logs",
            params={"building": "DEMO_A", "target_id": 202},
        )
        assert hidden.status_code == 200
        assert hidden.json()["total"] == 0
        assert "other-user" not in hidden.json()["operators"]


def test_audit_export_contains_only_the_selected_building() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        response = client.get("/api/audit/export", params={"building": "DEMO_A"})
        assert response.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        assert len(rows) == 2
        assert rows[1][1] == "demo_a-user"
        assert all("other-user" not in tuple(str(value) for value in row) for row in rows)
        assert all("unresolved-user" not in tuple(str(value) for value in row) for row in rows)


def test_direct_building_grants_do_not_create_portfolio_audit_access() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        catalog = client.get("/api/buildings")
        assert catalog.status_code == 200
        assert catalog.json()["portfolio"]["items"] == []

        response = client.get(
            "/api/audit/logs",
            params={"portfolio": "DEMO_OFFICE_PORTFOLIO"},
        )
        assert response.status_code == 404


def test_portfolio_selection_exposes_member_building_logs_not_unversioned_portfolio_logs() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "audit_admin").one()
            demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
            portfolio = (
                db.query(Portfolio)
                .filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO")
                .one()
            )
            db.add(
                UserScopeGrant(
                    user_id=user.id,
                    scope_type="portfolio",
                    portfolio_id=portfolio.id,
                    access_level="view",
                    include_future_buildings=True,
                    valid_from=demo_a.created_at.date(),
                )
            )
            db.add(
                OperationLog(
                    scope_type="portfolio",
                    portfolio_id=portfolio.id,
                    target_type="portfolio_report",
                    target_id=909,
                    action="export",
                    operator="portfolio-only",
                    changes="membership snapshot unavailable",
                )
            )
            db.commit()

        response = client.get(
            "/api/audit/logs",
            params={"portfolio": "DEMO_OFFICE_PORTFOLIO", "page_size": 100},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert 101 in {item["target_id"] for item in payload["items"]}
        assert 909 not in {item["target_id"] for item in payload["items"]}
        assert "portfolio-only" not in payload["operators"]
