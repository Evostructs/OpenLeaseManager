from __future__ import annotations

from datetime import date, timedelta
import io

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import Building, OperationLog, Room, User, UserScopeGrant, Zone


PILOT_CODE = "DEMO_C"
PILOT_ZONE_CODE = "WHOLE_BUILDING"
PILOT_ZONE_NAME = "整楼"


def _register_admin_with_grants(client: TestClient, *building_codes: str) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert registered.status_code == 200, registered.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").one()
        buildings = db.query(Building).filter(Building.code.in_(building_codes)).all()
        assert {item.code for item in buildings} == set(building_codes)
        db.add_all(
            [
                UserScopeGrant(
                    user_id=user.id,
                    scope_type="building",
                    building_id=building.id,
                    access_level="edit",
                    include_future_buildings=False,
                    valid_from=today_in_timezone(),
                )
                for building in buildings
            ]
        )
        db.commit()
    finally:
        db.close()

    logged_in = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert logged_in.status_code == 200, logged_in.text


def _activate_pilot_with_room() -> tuple[int, int]:
    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == PILOT_CODE).one()
        building.ingestion_mode = "detail"
        zone = Zone(
            building_id=building.id,
            code=PILOT_ZONE_CODE,
            name=PILOT_ZONE_NAME,
            zone_type="whole_building",
            valid_from=date(2026, 8, 24),
            is_active=True,
        )
        db.add(zone)
        db.flush()
        room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower=PILOT_ZONE_NAME,
            floor="",
            room_no="P4-FIN-01",
            room_type="办公",
            area=120,
            is_leasable=True,
            is_deleted=False,
        )
        db.add(room)
        db.commit()
        return int(building.id), int(room.id)
    finally:
        db.close()


def _workbook_values(response) -> set[str]:
    assert response.status_code == 200, response.text
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True, data_only=True)
    return {
        str(cell.value)
        for worksheet in workbook.worksheets
        for row in worksheet.iter_rows()
        for cell in row
        if cell.value not in (None, "")
    }


def test_snapshot_buildings_reject_direct_detail_page_requests() -> None:
    snapshot_code = "DEMO_D"
    with TestClient(app) as client:
        _register_admin_with_grants(client, snapshot_code)
        requests = (
            client.get("/api/contracts", params={"building": snapshot_code}),
            client.get("/api/customers", params={"building": snapshot_code}),
            client.get("/api/rooms", params={"building": snapshot_code}),
            client.get("/api/rent-analytics", params={"building": snapshot_code}),
            client.get("/api/risks", params={"building": snapshot_code}),
            client.get("/api/dashboard/operations", params={"building": snapshot_code}),
            client.get("/api/dashboard/operations/v2", params={"building": snapshot_code}),
            client.get("/api/dashboard/operations/trend", params={"building": snapshot_code}),
            client.get("/api/reports/operations-daily", params={"building": snapshot_code}),
            client.get(
                "/api/attachments",
                params={"building": snapshot_code, "entity_type": "contract", "entity_id": 1},
            ),
        )
        assert all(response.status_code == 422 for response in requests)

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        profile = next(
            item["operations_profile"]
            for item in me.json()["scope"]["items"]
            if item["code"] == snapshot_code
        )
        assert profile["data_adapter"] == "published_snapshot"
        assert set(profile["available_tabs"]) == {"dashboard", "evidence", "exchange"}


def test_generic_detail_profile_exposes_registered_zones_to_forms() -> None:
    db = SessionLocal()
    try:
        building = Building(
            code="P4_GENERIC_FORMS",
            name="Phase 4 通用表单楼",
            ingestion_mode="detail",
            is_active=True,
        )
        db.add(building)
        db.flush()
        db.add_all(
            [
                Zone(
                    building_id=building.id,
                    code="MARKET_OFFICE",
                    name="市场化办公区",
                    zone_type="office",
                    sort_order=10,
                    valid_from=date(2026, 8, 24),
                    is_active=True,
                ),
                Zone(
                    building_id=building.id,
                    code="PODIUM_ORIGINAL",
                    name="南塔裙楼",
                    zone_type="commercial",
                    sort_order=20,
                    valid_from=date(2026, 8, 24),
                    is_active=True,
                ),
                Zone(
                    building_id=building.id,
                    code="COMMERCIAL_SUPPORT",
                    name="商业配套",
                    zone_type="commercial",
                    sort_order=30,
                    valid_from=date(2026, 8, 24),
                    is_active=True,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        _register_admin_with_grants(client, "P4_GENERIC_FORMS")
        me = client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        profile = next(
            item["operations_profile"]
            for item in me.json()["scope"]["items"]
            if item["code"] == "P4_GENERIC_FORMS"
        )
        assert profile["data_adapter"] == "live_detail"
        assert profile["space_structure"]["zones"] == [
            {"code": "MARKET_OFFICE", "name": "市场化办公区", "kind": "office"},
            {"code": "PODIUM_ORIGINAL", "name": "南塔裙楼", "kind": "commercial"},
            {"code": "COMMERCIAL_SUPPORT", "name": "商业配套", "kind": "commercial"},
        ]
        room_template = _workbook_values(
            client.get(
                "/api/rooms/export/template",
                params={"building": "P4_GENERIC_FORMS"},
            )
        )
        contract_template = _workbook_values(
            client.get(
                "/api/contracts/export/template",
                params={"building": "P4_GENERIC_FORMS"},
            )
        )
        for zone_name in ("市场化办公区", "南塔裙楼", "商业配套"):
            assert zone_name in room_template
            assert zone_name in contract_template
        assert f"P4_GENERIC_FORMS-{today_in_timezone().year}-001" in contract_template

        created_room_ids = {}
        for tower, room_no in (("南塔裙楼", "P4-POD-01"), ("商业配套", "P4-COM-01")):
            response = client.post(
                "/api/rooms",
                params={"building": "P4_GENERIC_FORMS"},
                json={"tower": tower, "room_no": room_no, "area": 80},
            )
            assert response.status_code == 200, response.text
            created_room_ids[tower] = int(response.json()["id"])

        for tower, room_no in (("南塔裙楼", "P4-POD-01"), ("商业配套", "P4-COM-01")):
            filtered = client.get(
                "/api/rooms",
                params={"building": "P4_GENERIC_FORMS", "tower": tower},
            )
            assert filtered.status_code == 200, filtered.text
            assert [(item["tower"], item["room_no"]) for item in filtered.json()] == [(tower, room_no)]

        contract = client.post(
            "/api/contracts",
            params={"building": "P4_GENERIC_FORMS"},
            json={
                "contract_no": f"P4_GENERIC_FORMS-{today_in_timezone().year}-501",
                "tower": "南塔裙楼",
                "room_no": "P4-POD-01",
                "tenant_name": "通用分区原名客户",
                "area": 80,
                "unit_price": 4,
                "start_date": f"{today_in_timezone().year}-01-01",
                "end_date": f"{today_in_timezone().year}-12-31",
                "room_items": [
                    {
                        "room_id": created_room_ids["南塔裙楼"],
                        "tower": "南塔裙楼",
                        "room_no": "P4-POD-01",
                        "area": 80,
                        "unit_price": 4,
                    }
                ],
            },
        )
        assert contract.status_code == 200, contract.text
        assert contract.json()["tower"] == "南塔裙楼"
        assert contract.json()["room_items"][0]["tower"] == "南塔裙楼"

        stats = client.get(
            "/api/contracts/stats",
            params={"building": "P4_GENERIC_FORMS"},
        )
        assert stats.status_code == 200, stats.text
        assert {"南塔裙楼", "商业配套"}.issubset(stats.json()["tower_data"])
        assert "南塔办公" not in stats.json()["tower_data"]

        decision = client.get(
            f"/api/contracts/{contract.json()['id']}/decision-card",
            params={"building": "P4_GENERIC_FORMS"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["rooms"][0]["tower"] == "南塔裙楼"

        room_export = _workbook_values(
            client.get("/api/rooms/export/excel", params={"building": "P4_GENERIC_FORMS"})
        )
        contract_export = _workbook_values(
            client.get("/api/contracts/export/excel", params={"building": "P4_GENERIC_FORMS"})
        )
        assert {"南塔裙楼", "商业配套"}.issubset(room_export)
        assert "南塔裙楼" in contract_export
        assert "商业配套" not in contract_export


def test_non_demo_a_detail_customer_contract_deposit_risk_and_templates_are_building_scoped() -> None:
    building_id, room_id = _activate_pilot_with_room()
    today = today_in_timezone()
    query = {"building": PILOT_CODE}

    with TestClient(app) as client:
        _register_admin_with_grants(client, PILOT_CODE)
        customer = client.post(
            "/api/customers",
            params=query,
            json={"name": "Phase 4 整楼押金客户"},
        )
        assert customer.status_code == 200, customer.text

        contract = client.post(
            "/api/contracts",
            params=query,
            json={
                "contract_no": "DEMO_C-2026-501",
                "tower": PILOT_ZONE_NAME,
                "room_no": "P4-FIN-01",
                "tenant_name": "Phase 4 整楼押金客户",
                "customer_id": customer.json()["id"],
                "area": 120,
                "unit_price": 4,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "room_items": [
                    {
                        "room_id": room_id,
                        "tower": PILOT_ZONE_NAME,
                        "room_no": "P4-FIN-01",
                        "area": 120,
                        "unit_price": 4,
                        "start_date": "2026-01-01",
                        "end_date": "2026-12-31",
                    }
                ],
            },
        )
        assert contract.status_code == 200, contract.text
        contract_id = int(contract.json()["id"])

        created = client.post(
            f"/api/contracts/{contract_id}/deposits",
            params=query,
            json={
                "deposit_type": "租赁押金",
                "expected_amount": 30000,
                "due_date": (today - timedelta(days=1)).isoformat(),
            },
        )
        assert created.status_code == 200, created.text
        deposit_id = int(created.json()["id"])

        edited = client.put(
            f"/api/contracts/{contract_id}/deposits/{deposit_id}",
            params=query,
            json={"expected_amount": 36000, "notes": "Phase 4 整楼押金"},
        )
        assert edited.status_code == 200, edited.text
        received = client.post(
            f"/api/contracts/{contract_id}/deposits/{deposit_id}/receive",
            params=query,
            json={"amount": 10000, "action_date": today.isoformat()},
        )
        assert received.status_code == 200, received.text
        refunded = client.post(
            f"/api/contracts/{contract_id}/deposits/{deposit_id}/refund",
            params=query,
            json={"amount": 2000, "action_date": today.isoformat()},
        )
        assert refunded.status_code == 200, refunded.text
        offset = client.post(
            f"/api/contracts/{contract_id}/deposits/{deposit_id}/offset",
            params=query,
            json={"amount": 3000},
        )
        assert offset.status_code == 200, offset.text

        risks = client.get(
            "/api/risks",
            params={**query, "risk_type": "deposit_unpaid"},
        )
        assert risks.status_code == 200, risks.text
        assert any(item["contract_id"] == contract_id for item in risks.json()["items"])

        room_export = _workbook_values(client.get("/api/rooms/export/excel", params=query))
        contract_export = _workbook_values(client.get("/api/contracts/export/excel", params=query))
        deposit_export = _workbook_values(client.get("/api/contracts/deposits/export", params=query))
        risk_export = _workbook_values(client.get("/api/risks/export", params=query))
        for values in (room_export, contract_export, deposit_export, risk_export):
            assert PILOT_ZONE_NAME in values
            assert not {"南塔办公", "北塔办公", "商业配套"}.intersection(values)
        assert any("示例单栋中心租赁情况明细表" in value for value in contract_export)
        assert not any("示例双塔中心" in value for value in contract_export)

        room_template = _workbook_values(client.get("/api/rooms/export/template", params=query))
        contract_template = _workbook_values(client.get("/api/contracts/export/template", params=query))
        follow_template = _workbook_values(
            client.get("/api/customers/follow-ups/import/template", params=query)
        )
        deposit_template = _workbook_values(
            client.get("/api/contracts/deposits/import/template", params=query)
        )
        assert PILOT_ZONE_NAME in room_template
        assert "DEMO_C-2026-001" in contract_template
        assert "DEMO_C-2026-001" in follow_template
        assert "DEMO_C-2026-001" in deposit_template
        assert not any(
            "DEMO_A" in value or "南塔" in value or "北塔" in value
            for values in (room_template, contract_template, follow_template, deposit_template)
            for value in values
        )

    db = SessionLocal()
    try:
        actions = {
            "deposit_create",
            "deposit_update",
            "deposit_receive",
            "deposit_refund",
            "deposit_offset",
        }
        logs = db.query(OperationLog).filter(OperationLog.action.in_(actions)).all()
        assert {row.action for row in logs} == actions
        assert all(
            row.scope_type == "building" and row.building_id == building_id
            for row in logs
        )
    finally:
        db.close()
