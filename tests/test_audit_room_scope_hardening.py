from __future__ import annotations

import json
from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    CustomerBuildingProfile,
    OperationLog,
    Portfolio,
    PortfolioBuilding,
    Room,
    User,
    UserScopeGrant,
    Zone,
)
from app.routers.ai import get_ai_provider
from app.services.ai_assist import FakeAiAssistProvider
from app.core.time import today_in_timezone


PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
OTHER_CODE = "AUDIT_ROOM_B"


def _login_admin(client: TestClient) -> None:
    assert client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    ).status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    ).status_code == 200


def _seed_second_building() -> tuple[int, int, int]:
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        portfolio = db.query(Portfolio).filter(Portfolio.code == PORTFOLIO_CODE).one()
        building = Building(
            code=OTHER_CODE,
            name="审计房源乙楼",
            is_active=True,
            ingestion_mode="detail",
        )
        db.add(building)
        db.flush()
        zone = Zone(
            building_id=building.id,
            code=f"{OTHER_CODE}_OFFICE",
            name="乙楼办公",
            zone_type="office",
            valid_from=date(2026, 1, 1),
            is_active=True,
        )
        db.add(zone)
        db.flush()
        active = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="乙塔",
            floor="8",
            room_no="B-801",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=False,
        )
        deleted = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="乙塔",
            floor="9",
            room_no="B-901",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=True,
        )
        db.add_all(
            [
                active,
                deleted,
                PortfolioBuilding(
                    portfolio_id=portfolio.id,
                    building_id=building.id,
                    valid_from=date(2026, 1, 1),
                ),
                UserScopeGrant(
                    user_id=admin.id,
                    scope_type="portfolio",
                    portfolio_id=portfolio.id,
                    building_id=None,
                    access_level="edit",
                    include_future_buildings=False,
                    valid_from=date(2026, 1, 1),
                ),
            ]
        )
        db.commit()
        return int(building.id), int(active.id), int(deleted.id)


def _seed_portfolio_contracts(other_building_id: int) -> tuple[int, int]:
    today = today_in_timezone()
    with SessionLocal() as db:
        demo_a_id = int(db.query(Building.id).filter(Building.code == "DEMO_A").scalar())
        db.add_all(
            [
                Contract(
                    building_id=demo_a_id,
                    contract_no="AUDIT-EXPORT-DEMO_A-1",
                    tenant_name="甲楼客户一",
                    area=100,
                    unit_price=4,
                    monthly_rent=12000,
                    start_date=today - timedelta(days=30),
                    end_date=today + timedelta(days=180),
                    is_deleted=False,
                ),
                Contract(
                    building_id=demo_a_id,
                    contract_no="AUDIT-EXPORT-DEMO_A-2",
                    tenant_name="甲楼客户二",
                    area=80,
                    unit_price=4,
                    monthly_rent=9600,
                    start_date=today - timedelta(days=30),
                    end_date=today + timedelta(days=180),
                    is_deleted=False,
                ),
                Contract(
                    building_id=other_building_id,
                    contract_no="AUDIT-EXPORT-B-1",
                    tenant_name="乙楼客户一",
                    area=60,
                    unit_price=4,
                    monthly_rent=7200,
                    start_date=today - timedelta(days=30),
                    end_date=today + timedelta(days=180),
                    is_deleted=False,
                ),
            ]
        )
        db.commit()
        return demo_a_id, other_building_id


def test_portfolio_business_exports_freeze_one_audit_row_per_building() -> None:
    with TestClient(app) as client:
        _login_admin(client)
        other_building_id, _active_id, _deleted_id = _seed_second_building()
        demo_a_id, _ = _seed_portfolio_contracts(other_building_id)

        assert client.get(
            "/api/contracts/export/excel",
            params={"portfolio": PORTFOLIO_CODE},
        ).status_code == 200
        for building_code in ("DEMO_A", OTHER_CODE):
            assert client.get(
                "/api/rent-analytics/export",
                params={"building": building_code},
            ).status_code == 200
        assert client.get(
            "/api/reports/operations-daily",
            params={"portfolio": PORTFOLIO_CODE},
        ).status_code == 200

        with SessionLocal() as db:
            for action in ("export", "rent_analytics_export", "daily_report_export"):
                rows = db.query(OperationLog).filter(OperationLog.action == action).all()
                assert {(row.scope_type, row.building_id, row.portfolio_id) for row in rows} == {
                    ("building", demo_a_id, None),
                    ("building", other_building_id, None),
                }

            contract_export_changes = {
                int(row.building_id): row.changes
                for row in db.query(OperationLog).filter(OperationLog.action == "export").all()
            }
            assert contract_export_changes == {
                demo_a_id: "导出合同 Excel（2 条）",
                other_building_id: "导出合同 Excel（1 条）",
            }

            report_changes = {
                int(row.building_id): json.loads(row.changes or "{}")
                for row in db.query(OperationLog).filter(OperationLog.action == "daily_report_export").all()
            }
            assert set(report_changes) == {demo_a_id, other_building_id}
            assert all(set(payload) == {"date", "month"} for payload in report_changes.values())
            assert all("queue_counts" not in payload for payload in report_changes.values())
            assert all("risk_count" not in payload for payload in report_changes.values())

        visible = client.get(
            "/api/audit/logs",
            params={"portfolio": PORTFOLIO_CODE, "page_size": 100},
        )
        assert visible.status_code == 200, visible.text
        visible_actions = {row["action"] for row in visible.json()["items"]}
        assert {"export", "rent_analytics_export", "daily_report_export"} <= visible_actions


def test_portfolio_ai_audit_is_written_only_to_customer_related_buildings() -> None:
    provider = FakeAiAssistProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            _login_admin(client)
            customer = client.post("/api/customers", json={"name": "审计归属客户"}).json()
            other_building_id, _active_id, _deleted_id = _seed_second_building()

            response = client.post(
                "/api/ai/customer-summary",
                params={"portfolio": PORTFOLIO_CODE},
                json={"customer_id": customer["id"]},
            )
            assert response.status_code == 200, response.text

            with SessionLocal() as db:
                demo_a_id = int(db.query(Building.id).filter(Building.code == "DEMO_A").scalar())
                related_ids = {
                    int(value)
                    for (value,) in db.query(CustomerBuildingProfile.building_id)
                    .filter(CustomerBuildingProfile.customer_id == customer["id"])
                    .all()
                }
                assert related_ids == {demo_a_id}
                rows = db.query(OperationLog).filter(OperationLog.action == "ai_customer_summary").all()
                assert [
                    (row.building_id, row.target_type, row.target_id)
                    for row in rows
                ] == [(demo_a_id, "customer", customer["id"])]
                assert all(row.building_id != other_building_id for row in rows)
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)


def test_room_ids_outside_selected_building_are_indistinguishable_from_missing_ids() -> None:
    payload = {
        "tower": "南塔",
        "floor": "1",
        "room_no": "S-101",
        "room_type": "办公",
        "area": 100,
        "is_leasable": True,
        "notes": "",
    }
    with TestClient(app) as client:
        _login_admin(client)
        _other_building_id, active_id, deleted_id = _seed_second_building()

        pairs = (
            (
                client.put(f"/api/rooms/{active_id}", json=payload),
                client.put("/api/rooms/99999999", json=payload),
            ),
            (
                client.delete(f"/api/rooms/{active_id}"),
                client.delete("/api/rooms/99999999"),
            ),
            (
                client.post(f"/api/rooms/recycle/restore/{deleted_id}"),
                client.post("/api/rooms/recycle/restore/99999999"),
            ),
            (
                client.delete(f"/api/rooms/recycle/purge/{deleted_id}"),
                client.delete("/api/rooms/recycle/purge/99999999"),
            ),
        )
        for hidden, missing in pairs:
            assert hidden.status_code == missing.status_code == 404
            assert hidden.json() == missing.json() == {"detail": "房间不存在或无权访问"}
