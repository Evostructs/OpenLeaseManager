from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import OperationLog


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


def _contract_payload(contract_no: str, customer_id: int, **overrides) -> dict:
    today = today_in_timezone()
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    payload = {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔裙楼",
        "room_no": "P0201",
        "tenant_name": "决策卡测试客户",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "internal_contact_person": "ops-owner",
        "contact_person": "customer-owner",
        "start_date": previous_month_end.replace(day=1).isoformat(),
        "end_date": (today + timedelta(days=45)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _operation_log_count() -> int:
    db = SessionLocal()
    try:
        return db.query(OperationLog).count()
    finally:
        db.close()


def test_decision_card_requires_login_and_returns_404() -> None:
    with _client() as client:
        assert client.get("/api/contracts/1/decision-card").status_code == 401

        _ensure_admin(client)
        _login(client)
        assert client.get("/api/contracts/9999/decision-card").status_code == 404


def test_decision_card_sections_signals_checks_and_read_only_behavior() -> None:
    today = today_in_timezone()
    current_month = today.strftime("%Y-%m")

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post(
            "/api/customers",
            json={"name": "决策卡客户", "short_name": "决策卡", "contact_name": "客户负责人"},
        ).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-DECISION-001", customer["id"]),
        ).json()

        deposit = client.post(
            f"/api/contracts/{contract['id']}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 30000, "due_date": today.isoformat()},
        ).json()
        assert client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/receive",
            json={"amount": 10000, "action_date": today.isoformat()},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/receivables/monthly/{contract['id']}/payments",
            json={"month": current_month, "amount": 5000, "payment_date": today.isoformat(), "allocation_type": "收款"},
        ).status_code == 200
        assert client.post(
            f"/api/customers/{customer['id']}/follow-ups",
            json={
                "contract_id": contract["id"],
                "follow_up_type": "电话",
                "content": "确认续租意向",
                "next_action": "联系客户负责人",
                "next_follow_up_date": (today + timedelta(days=3)).isoformat(),
                "owner": "ops-owner",
            },
        ).status_code == 200

        before_logs = _operation_log_count()
        response = client.get(f"/api/contracts/{contract['id']}/decision-card")
        after_logs = _operation_log_count()

        assert response.status_code == 200
        assert after_logs == before_logs
        data = response.json()

        assert data["contract"]["contract_no"] == "DEMO_A-DECISION-001"
        assert data["contract"]["days_to_expiry"] == 45
        assert data["customer"]["customer_id"] == customer["id"]
        assert data["customer"]["contact_name"] == "客户负责人"
        assert data["rooms"][0]["room_no"] == "P0201"
        assert data["rooms"][0]["tower"] in {"南塔办公", "北塔办公", "商业配套"}
        assert "裙楼" not in data["rooms"][0]["tower"]

        receivables = data["receivables"]
        assert receivables["receivable"] > 0
        assert receivables["received"] == 5000
        assert receivables["unpaid_balance"] > 0
        assert receivables["overdue_balance"] > 0
        assert receivables["unpaid_balance"] != receivables["overdue_balance"]
        assert receivables["collection_rate"] > 0

        assert data["deposits"]["expected_amount"] == 30000
        assert data["deposits"]["received_amount"] == 10000
        assert data["deposits"]["unpaid_amount"] == 20000
        assert data["attachments"]["has_contract_body"] is False
        assert data["follow_up"]["latest"]["owner"] == "ops-owner"
        assert data["follow_up"]["next"]["next_action"] == "联系客户负责人"
        assert data["risks"]

        assert {"collection_blocker", "attachment_gap", "deposit_gap", "high_risk", "expiring_soon"} <= set(data["decision_signals"])
        checks = set(data["suggested_checks"])
        assert {"先确认历史逾期", "补合同正文附件", "确认押金未收", "安排续租沟通"} <= checks
