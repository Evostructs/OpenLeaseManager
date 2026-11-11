from __future__ import annotations

import io

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import OperationLog


def _client() -> TestClient:
    return TestClient(app)


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert response.status_code in {200, 403}


def _login(client: TestClient, username: str = "admin", password: str = "Admin123") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _ensure_admin(client: TestClient) -> None:
    _register_admin(client)
    _login(client)


def _create_editor(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/create-user",
        json={"username": "editor", "password": "Editor123", "display_name": "Editor", "role": "editor"},
    )
    assert response.status_code == 200


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _room_payload(room_no: str = "PERM-1801") -> dict:
    return {"tower": "南塔", "floor": "18", "room_no": room_no, "room_type": "办公", "area": 100}


def _contract_payload(contract_no: str = "DEMO_A-PERM-001", room_no: str = "PERM-1801") -> dict:
    return {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": "权限审计客户",
        "area": 100,
        "unit_price": 5,
        "lease_type": "新签",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }


def test_anonymous_and_editor_admin_action_permissions() -> None:
    with _client() as admin_client:
        _ensure_admin(admin_client)
        _create_editor(admin_client)

    with _client() as anonymous_client:
        response = anonymous_client.get("/api/audit/logs")
        assert response.status_code == 401
        assert response.json()["detail"]

    with _client() as editor_client:
        _login(editor_client, "editor", "Editor123")
        response = editor_client.post("/api/contracts", json=_contract_payload("DEMO_A-EDITOR-DENIED"))
        assert response.status_code == 403
        assert "管理员" in response.json()["detail"]


def test_admin_import_and_contract_write_audit_query_filters() -> None:
    with _client() as client:
        _ensure_admin(client)

        customer_book = _workbook_bytes(
            ["客户名称", "统一社会信用代码", "联系人", "联系电话", "邮箱"],
            [["权限导入客户", "91310000PERMAUDIT", "张三", "13800000000", "audit@example.com"]],
        )
        import_response = client.post(
            "/api/customers/import",
            files={"file": ("customers.xlsx", customer_book, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert import_response.status_code == 200
        assert import_response.json()["imported"] == 1

        assert client.post("/api/rooms", json=_room_payload()).status_code == 200
        contract_response = client.post("/api/contracts", json=_contract_payload())
        assert contract_response.status_code == 200
        contract_id = contract_response.json()["id"]

        today = today_in_timezone().isoformat()
        query_response = client.get(
            "/api/audit/logs",
            params={
                "action": "create",
                "target_type": "contract",
                "target_id": contract_id,
                "operator": "admin",
                "start_date": today,
                "end_date": today,
                "page": 1,
                "page_size": 1,
            },
        )
        assert query_response.status_code == 200
        payload = query_response.json()
        assert payload["total"] == 1
        assert payload["page"] == 1
        assert payload["page_size"] == 1
        assert payload["items"][0]["target_type"] == "contract"
        assert payload["items"][0]["target_id"] == contract_id
        assert payload["items"][0]["operator"] == "admin"

        import_query = client.get(
            "/api/audit/logs",
                params={
                    "building": "DEMO_A",
                "action": "customer_import",
                "target_type": "customer",
            },
        )
        assert import_query.status_code == 200
        assert import_query.json()["total"] == 1

        db = SessionLocal()
        try:
            contract_log = db.query(OperationLog).filter(OperationLog.action == "create").one()
            assert contract_log.target_type == "contract"
            assert contract_log.target_id == contract_id
            assert "password" not in (contract_log.changes or "").lower()
        finally:
            db.close()
