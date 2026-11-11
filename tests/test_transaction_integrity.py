from __future__ import annotations

import asyncio
from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.time import today_in_timezone
from app.main import app, integrity_error_handler


def _client(**kwargs) -> TestClient:
    return TestClient(app, **kwargs)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200


def test_integrity_error_handler_returns_409():
    error = IntegrityError("INSERT INTO contracts ...", {}, Exception("UNIQUE constraint failed: contracts.contract_no"))
    response = asyncio.new_event_loop().run_until_complete(integrity_error_handler(None, error))
    assert response.status_code == 409
    assert "并发" in bytes(response.body).decode("utf-8")


def test_integrity_error_through_endpoint_returns_409(monkeypatch):
    import app.routers.contracts as contracts_router

    def _raise_integrity(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception("UNIQUE constraint failed"))

    with _client(raise_server_exceptions=False) as client:
        _ensure_admin(client)
        _login(client)
        monkeypatch.setattr(contracts_router, "create_contract", _raise_integrity)
        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-RACE-409",
                "tower": "南塔",
                "room_no": "R409",
                "tenant_name": "并发测试",
                "area": 100,
                "unit_price": 4,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 409
        assert "请刷新后重试" in response.json()["detail"]


def test_create_and_terminate_write_audit_in_same_transaction():
    """主操作成功后审计日志必须存在（同事务提交，不再分多次 commit）。"""
    today = today_in_timezone()
    start = today.replace(day=1) + relativedelta(months=-2)
    end = today.replace(day=1) + relativedelta(months=6, days=-1)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        created = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-TXN-AUDIT",
                "tower": "南塔",
                "room_no": "TXN01",
                "tenant_name": "事务审计租户",
                "area": 100,
                "unit_price": 4,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        assert created.status_code == 200, created.text
        contract_id = created.json()["id"]

        terminated = client.post(
            f"/api/contracts/{contract_id}/terminate",
            json={"end_date": (today + relativedelta(days=-1)).isoformat()},
        )
        assert terminated.status_code == 200, terminated.text

        logs = client.get("/api/audit/logs", params={"keyword": "DEMO_A-TXN-AUDIT", "page_size": 50}).json()
        actions = [row["action"] for row in logs.get("items", logs.get("rows", []))]
        assert "create" in actions
        assert "terminate" in actions
