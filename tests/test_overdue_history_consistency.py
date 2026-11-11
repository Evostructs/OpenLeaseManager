from __future__ import annotations

import calendar
from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.main import app

MONTHLY = 7300.0  # area=120, unit_price=2


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
    assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200


def _month_start(offset: int) -> date:
    return today_in_timezone().replace(day=1) + relativedelta(months=offset)


def _month_end(anchor: date) -> date:
    return date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])


def test_overdue_history_consistent_across_views():
    """风险中心、运营看板、决策卡、客户摘要的历史逾期金额必须同源一致。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    customer = client.post("/api/customers", json={"name": "口径一致性客户"}).json()
    start = _month_start(-3)
    end = _month_end(_month_start(8))
    created = client.post(
        "/api/contracts",
        json={
            "contract_no": "DEMO_A-OVERDUE-CONSIST",
            "customer_id": customer["id"],
            "tower": "南塔",
            "room_no": "1812",
            "tenant_name": "口径一致性客户",
            "area": 120,
            "unit_price": 2,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    contract_id = created.json()["id"]

    # 三个完整历史月（-3/-2/-1）均未收款 → 逾期 3 * 7300 = 21900
    expected_overdue = round(MONTHLY * 3, 2)

    # 1. 风险中心
    risks = client.get("/api/risks", params={"contract_id": contract_id}).json()["items"]
    overdue_risks = [item for item in risks if item["risk_type"] == "overdue_receivable"]
    assert len(overdue_risks) == 1
    assert abs(overdue_risks[0]["amount"] - expected_overdue) < 0.05
    assert overdue_risks[0]["severity"] == "high"  # >= 10000
    assert overdue_risks[0]["meta"]["overdue_months"] == 3

    # 2. 运营看板
    operations = client.get("/api/dashboard/operations").json()
    queue_rows = [
        row
        for row in operations.get("queues", {}).get("overdue_contracts", [])
        if row.get("contract_id") == contract_id
    ]
    assert len(queue_rows) == 1
    assert abs(queue_rows[0]["overdue_balance"] - expected_overdue) < 0.05
    assert queue_rows[0]["severity"] == "danger"  # high 映射到看板词汇
    assert queue_rows[0]["overdue_months"] == 3

    # 3. 合同决策卡
    card = client.get(f"/api/contracts/{contract_id}/decision-card").json()
    assert abs(card["receivables"]["overdue_balance"] - expected_overdue) < 0.05

    # 4. 客户经营摘要（全历史窗口，本例与 12 个月窗口重合）
    detail = client.get(f"/api/customers/{customer['id']}").json()
    summary = detail["operating_summary"]
    assert abs(summary["overdue_balance"] - expected_overdue) < 0.05
    # 未收余额 = 3 个逾期月 + 当月未到期
    assert abs(summary["unpaid_balance"] - round(MONTHLY * 4, 2)) < 0.05
