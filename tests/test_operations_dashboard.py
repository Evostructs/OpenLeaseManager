from __future__ import annotations

from datetime import date, timedelta

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


def _room_payload(room_no: str, area: float = 100) -> dict:
    return {"tower": "南塔", "floor": "12", "room_no": room_no, "room_type": "办公", "area": area}


def _contract_payload(
    contract_no: str,
    room_no: str,
    customer_id: int,
    start_date: date,
    end_date: date,
    **overrides,
) -> dict:
    payload = {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"{contract_no} customer",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "internal_contact_person": "ops-owner",
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


def _assert_v2_target_is_navigable(target: dict) -> None:
    assert isinstance(target, dict)
    assert target.get("target_type") in {"contract", "customer"}
    assert any(target.get(key) for key in ("contract_id", "customer_id", "room_no"))
    assert any(target.get(key) for key in ("contract_no", "customer_name", "room_no"))


def test_operations_dashboard_metrics_trends_and_action_queues() -> None:
    today = today_in_timezone()
    current_month = today.strftime("%Y-%m")
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        for room_no, area in [
            ("OPS-OD", 100),
            ("OPS-PAID", 100),
            ("OPS-EXP", 100),
            ("OPS-MISS", 100),
            ("OPS-VAC", 80),
        ]:
            assert client.post("/api/rooms", json=_room_payload(room_no, area)).status_code == 200

        overdue_customer = client.post("/api/customers", json={"name": "Ops overdue customer"}).json()
        paid_customer = client.post("/api/customers", json={"name": "Ops paid customer"}).json()
        expiring_customer = client.post("/api/customers", json={"name": "Ops expiring customer"}).json()
        missing_customer = client.post("/api/customers", json={"name": "Ops missing attachment customer"}).json()

        overdue_contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-OPS-OD",
                "OPS-OD",
                overdue_customer["id"],
                previous_month_start,
                today + timedelta(days=180),
            ),
        )
        assert overdue_contract.status_code == 200
        overdue_id = overdue_contract.json()["id"]
        _upload_contract_body(client, overdue_id)

        paid_contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-OPS-PAID", "OPS-PAID", paid_customer["id"], today, today + timedelta(days=180)),
        )
        assert paid_contract.status_code == 200
        paid_id = paid_contract.json()["id"]
        _upload_contract_body(client, paid_id)

        expiring_contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-OPS-EXP", "OPS-EXP", expiring_customer["id"], today, today + timedelta(days=45)),
        )
        assert expiring_contract.status_code == 200
        expiring_id = expiring_contract.json()["id"]
        _upload_contract_body(client, expiring_id)

        missing_contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-OPS-MISS", "OPS-MISS", missing_customer["id"], today, today + timedelta(days=180)),
        )
        assert missing_contract.status_code == 200
        missing_id = missing_contract.json()["id"]

        payment = client.post(
            f"/api/contracts/receivables/monthly/{paid_id}/payments",
            json={"month": current_month, "amount": 5000, "payment_date": today.isoformat(), "allocation_type": "收款"},
        )
        assert payment.status_code == 200

        deposit = client.post(
            f"/api/contracts/{paid_id}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 30000, "due_date": today.isoformat()},
        )
        assert deposit.status_code == 200
        received = client.post(
            f"/api/contracts/{paid_id}/deposits/{deposit.json()['id']}/receive",
            json={"amount": 10000, "action_date": today.isoformat()},
        )
        assert received.status_code == 200

        follow_up = client.post(
            f"/api/customers/{paid_customer['id']}/follow-ups",
            json={
                "contract_id": paid_id,
                "follow_up_type": "电话",
                "content": "Confirm renewal decision",
                "next_action": "Call finance contact",
                "next_follow_up_date": today.isoformat(),
                "owner": "ops-owner",
            },
        )
        assert follow_up.status_code == 200

        current_report = client.get("/api/contracts/receivables/monthly", params={"month": current_month}).json()
        previous_report = client.get("/api/contracts/receivables/monthly", params={"month": previous_month_start.strftime("%Y-%m")}).json()

        response = client.get("/api/dashboard/operations")
        assert response.status_code == 200
        data = response.json()

        assert data["month"] == current_month
        assert data["current_month"]["receivable"] == current_report["total_rent"]
        assert data["current_month"]["received"] == current_report["total_received"]
        assert data["current_month"]["unpaid_balance"] == current_report["total_unpaid_balance"]
        assert data["current_month"]["overdue_balance"] == current_report["total_overdue_balance"]
        assert data["metrics"]["current_month_receivable"] == current_report["total_rent"]
        assert data["metrics"]["current_month_received"] == 5000
        assert data["metrics"]["current_month_unpaid_balance"] == current_report["total_unpaid_balance"]
        assert data["metrics"]["current_month_overdue_balance"] == current_report["total_overdue_balance"]

        assert data["overdue"]["total_overdue_balance"] == previous_report["total_overdue_balance"]
        assert data["overdue"]["contract_count"] == 1
        assert data["queue_counts"]["overdue_contracts"] == 1
        assert data["queues"]["overdue_contracts"][0]["contract_id"] == overdue_id
        assert data["queues"]["overdue_contracts"][0]["contract_no"] == "DEMO_A-OPS-OD"
        assert data["queues"]["overdue_contracts"][0]["target_type"] == "contract"

        assert data["vacancy"]["room_count"] == 1
        assert data["vacancy"]["area"] == 80
        assert data["metrics"]["vacancy_room_count"] == 1
        assert data["metrics"]["vacancy_area"] == 80

        assert data["metrics"]["expiring_contract_count_90"] == 1
        assert data["queue_counts"]["expiring_contracts"] == 1
        assert data["queues"]["expiring_contracts"][0]["contract_id"] == expiring_id
        assert data["queues"]["expiring_contracts"][0]["days_left"] == 45

        assert data["metrics"]["due_follow_up_customer_count"] == 1
        assert data["queue_counts"]["due_follow_up_customers"] == 1
        assert data["queues"]["due_follow_up_customers"][0]["customer_id"] == paid_customer["id"]
        assert data["queues"]["due_follow_up_customers"][0]["target_type"] == "customer"

        assert data["metrics"]["unpaid_deposit_amount"] == 20000
        assert data["metrics"]["unpaid_deposit_count"] == 1
        assert data["queue_counts"]["unpaid_deposits"] == 1
        assert data["queues"]["unpaid_deposits"][0]["contract_id"] == paid_id
        assert data["queues"]["unpaid_deposits"][0]["unpaid_amount"] == 20000

        assert data["metrics"]["missing_contract_body_count"] == 1
        assert data["queue_counts"]["missing_contract_body"] == 1
        assert data["queues"]["missing_contract_body"][0]["contract_id"] == missing_id
        assert data["queues"]["missing_contract_body"][0]["missing_category"] == "合同正文"

        assert len(data["trend"]) == 6
        assert data["trend"][-1]["month"] == current_month
        assert data["trend"][-1]["receivable"] == current_report["total_rent"]
        assert {item["type"] for item in data["todos"]} >= {
            "overdue_contract",
            "expiring_contract",
            "due_follow_up_customer",
            "unpaid_deposit",
            "missing_contract_body",
        }

        lite_response = client.get("/api/dashboard/operations", params={"include_trend": "false", "queue_limit": 1, "risk_limit": 1})
        assert lite_response.status_code == 200
        lite = lite_response.json()
        assert lite["trend"] == []
        assert all(len(items) <= 1 for items in lite["queues"].values())
        assert lite["queue_counts"] == data["queue_counts"]
        assert len(lite["risks"]["top"]) <= 1
        assert lite["metrics"]["risk_count"] == data["metrics"]["risk_count"]
        assert lite["metrics"]["high_risk_count"] == data["metrics"]["high_risk_count"]
        assert {"date", "month", "metrics", "current_month", "vacancy", "risks", "queues", "queue_counts", "todos"} <= set(lite)

        v2_response = client.get("/api/dashboard/operations/v2", params={"include_trend": "false", "queue_limit": 1, "risk_limit": 1})
        assert v2_response.status_code == 200
        v2 = v2_response.json()
        assert {
            "contract_version",
            "facts",
            "metrics",
            "signals",
            "lanes",
            "next_actions",
            "drilldown_intents",
        } <= set(v2)
        assert v2["contract_version"] == "operations-hci-v2"
        assert v2["month"] == current_month
        assert v2["facts"]["current_month"] == lite["current_month"]
        assert v2["facts"]["queue_counts"] == lite["queue_counts"]
        assert v2["facts"]["vacancy"] == lite["vacancy"]
        assert v2["facts"]["risk_summary"] == lite["risks"]["summary"]
        assert {lane["key"] for lane in v2["lanes"]} >= {"payment", "follow_up", "expiring", "materials", "risk"}
        assert v2_response.text.find("contact_phone") == -1

        v2_metrics = {item["key"]: item for item in v2["metrics"]}
        for metric in v2["metrics"]:
            assert {"key", "value", "raw_value", "unit", "tone", "intent_key"} <= set(metric)
            assert metric["key"]
            assert metric["intent_key"]
        assert {item["label"] for item in v2["metrics"]} >= {
            "本月应收",
            "本月已收",
            "本月未收",
            "本月逾期",
            "回款率",
            "空置房间",
            "风险事项",
        }
        assert v2_metrics["vacancy_room_count"]["unit"] == "间"
        assert "空置面积" in v2_metrics["vacancy_room_count"]["helper"]
        assert v2_metrics["risk_count"]["unit"] == "项"
        assert "高风险" in v2_metrics["risk_count"]["helper"]
        assert {lane["label"] for lane in v2["lanes"]} >= {"收款", "跟进", "到期", "资料", "风险"}
        assert {signal["label"] for signal in v2["signals"]} >= {"回款状态", "待办压力"}
        assert "Current month" not in v2_response.text
        assert "Vacant rooms" not in v2_response.text
        assert "Open risks" not in v2_response.text
        assert "sqm vacant" not in v2_response.text
        assert v2_metrics["current_month_receivable"]["raw_value"] == lite["metrics"]["current_month_receivable"]
        assert v2_metrics["current_month_received"]["raw_value"] == lite["metrics"]["current_month_received"]
        assert v2_metrics["current_month_unpaid_balance"]["raw_value"] == lite["metrics"]["current_month_unpaid_balance"]
        assert v2_metrics["current_month_overdue_balance"]["raw_value"] == lite["metrics"]["current_month_overdue_balance"]

        v2_lanes = {lane["key"]: lane for lane in v2["lanes"]}
        for lane in v2["lanes"]:
            assert {"key", "count", "items"} <= set(lane)
            assert isinstance(lane["items"], list)
            for item in lane["items"]:
                _assert_v2_target_is_navigable(item["target"])
        assert v2_lanes["payment"]["count"] == lite["queue_counts"]["overdue_contracts"] + lite["queue_counts"]["unpaid_deposits"]
        assert v2_lanes["follow_up"]["count"] == lite["queue_counts"]["due_follow_up_customers"]
        assert v2_lanes["expiring"]["count"] == lite["queue_counts"]["expiring_contracts"]
        assert v2_lanes["materials"]["count"] == lite["queue_counts"]["missing_contract_body"]
        assert v2_lanes["risk"]["count"] == lite["metrics"]["risk_count"]
        assert {item["type"] for item in v2["next_actions"]} >= {
            "overdue_contract",
            "expiring_contract",
            "due_follow_up_customer",
            "unpaid_deposit",
            "missing_contract_body",
        }
        for action in v2["next_actions"]:
            assert {"id", "title", "target", "intent_key"} <= set(action)
            _assert_v2_target_is_navigable(action["target"])

        trend_response = client.get("/api/dashboard/operations/trend")
        assert trend_response.status_code == 200
        trend_data = trend_response.json()
        assert len(trend_data["trend"]) == 6
        assert trend_data["trend"][-1]["month"] == current_month
