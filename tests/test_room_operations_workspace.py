from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
    assert response.status_code == 200


def _room_payload(room_no: str, **overrides) -> dict:
    payload = {"tower": "南塔", "floor": "9", "room_no": room_no, "room_type": "办公", "area": 100}
    payload.update(overrides)
    return payload


def _contract_payload(contract_no: str, room_no: str, customer_id: int, start_date, end_date, **overrides) -> dict:
    payload = {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"{contract_no}客户",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "internal_contact_person": "运营负责人",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    payload.update(overrides)
    return payload


def _upload_contract_body(client: TestClient, contract_id: int) -> None:
    response = client.post(
        "/api/attachments",
        data={"entity_type": "contract", "entity_id": str(contract_id), "category": "合同正文", "notes": ""},
        files={"file": ("contract.pdf", b"contract body", "application/pdf")},
    )
    assert response.status_code == 200


def test_room_operations_stats_filters_and_detail_reuse_existing_business_data() -> None:
    today = today_in_timezone()
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        overdue_customer = client.post("/api/customers", json={"name": "房源历史逾期客户"}).json()
        expiring_customer = client.post("/api/customers", json={"name": "房源到期客户"}).json()

        assert client.post("/api/rooms", json=_room_payload("OPS-OD")).status_code == 200
        assert client.post("/api/rooms", json=_room_payload("OPS-EXP")).status_code == 200
        assert client.post("/api/rooms", json=_room_payload("OPS-VAC", tower="北塔", floor="12", area=80)).status_code == 200

        overdue_contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-OD",
                "OPS-OD",
                overdue_customer["id"],
                previous_month_start,
                today + timedelta(days=180),
            ),
        )
        assert overdue_contract.status_code == 200
        overdue_contract_id = overdue_contract.json()["id"]

        expiring_contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-EXP",
                "OPS-EXP",
                expiring_customer["id"],
                today,
                today + timedelta(days=45),
            ),
        )
        assert expiring_contract.status_code == 200
        expiring_contract_id = expiring_contract.json()["id"]
        _upload_contract_body(client, expiring_contract_id)

        follow_up = client.post(
            f"/api/customers/{overdue_customer['id']}/follow-ups",
            json={
                "contract_id": overdue_contract_id,
                "follow_up_type": "催收",
                "content": "确认历史逾期支付计划",
                "next_action": "本周电话跟进",
                "next_follow_up_date": today.isoformat(),
                "owner": "运营负责人",
            },
        )
        assert follow_up.status_code == 200

        stats = client.get("/api/rooms/stats").json()
        assert stats["total_rooms"] == 3
        assert stats["rented_rooms"] == 2
        assert stats["vacancy_rooms"] == 1
        assert stats["vacancy_area"] == 80
        assert stats["expiring_90_rooms"] == 1
        assert stats["overdue_rooms"] == 1
        assert stats["missing_attachment_rooms"] == 1

        assert [room["room_no"] for room in client.get("/api/rooms", params={"status": "空置"}).json()] == ["OPS-VAC"]
        assert [room["room_no"] for room in client.get("/api/rooms", params={"status": "即将到期"}).json()] == ["OPS-EXP"]
        assert [room["room_no"] for room in client.get("/api/rooms", params={"status": "历史逾期"}).json()] == ["OPS-OD"]
        assert [room["room_no"] for room in client.get("/api/rooms", params={"status": "资料缺失"}).json()] == ["OPS-OD"]
        assert [room["room_no"] for room in client.get("/api/rooms", params={"tower": "北塔"}).json()] == ["OPS-VAC"]
        assert {room["room_no"] for room in client.get("/api/rooms", params={"min_area": 90}).json()} == {"OPS-EXP", "OPS-OD"}

        operation_rooms = client.get("/api/rooms", params={"include_operations": "true"}).json()
        rooms_by_no = {room["room_no"]: room for room in operation_rooms}
        overdue_room = rooms_by_no["OPS-OD"]
        vacant_room = rooms_by_no["OPS-VAC"]
        assert overdue_room["contract"]["contract_no"] == "DEMO_A-ROOM-OD"
        assert overdue_room["contract"]["tenant_name"] == "房源历史逾期客户"
        assert overdue_room["contract"]["overdue_balance"] > 0
        assert overdue_room["contract"]["missing_contract_body"] is True
        assert overdue_room["contract"]["next_follow_up_date"] == today.isoformat()
        assert overdue_room["signals"]["overdue"] is True
        assert overdue_room["signals"]["missing_attachment"] is True
        assert "历史逾期" in overdue_room["operating_statuses"]
        assert "资料缺失" in overdue_room["operating_statuses"]
        assert vacant_room["contract"] is None
        assert vacant_room["signals"]["vacant"] is True
        assert vacant_room["operating_statuses"] == ["空置"]

        detail = client.get("/api/rooms/OPS-OD/contracts").json()
        operation = detail["operation"]
        assert operation["current_contract"]["contract_no"] == "DEMO_A-ROOM-OD"
        assert operation["customer"]["name"] == "房源历史逾期客户"
        assert operation["receivable"]["overdue_balance"] > 0
        assert operation["attachment_summary"]["missing_required"] == ["合同正文"]
        assert operation["follow_up"]["next_action"] == "本周电话跟进"
        assert operation["signals"]["overdue"] is True
        assert operation["signals"]["missing_attachment"] is True
