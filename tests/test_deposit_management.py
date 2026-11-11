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


def _contract_payload(contract_no: str, room_no: str, **overrides) -> dict:
    today = today_in_timezone()
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"押金测试客户{contract_no}",
        "area": 100,
        "unit_price": 4,
        "start_date": (today - timedelta(days=10)).isoformat(),
        "end_date": (today + timedelta(days=180)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_deposit_crud_actions_contract_summary_customer_cues_and_logs() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "押金主档客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-DEPOSIT-FOUNDATION", "DEP-101", customer_id=customer["id"]),
        ).json()

        created = client.post(
            f"/api/contracts/{contract['id']}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 30000, "due_date": today_in_timezone().isoformat()},
        )
        assert created.status_code == 200
        deposit = created.json()
        assert deposit["status"] == "unpaid"
        assert deposit["unpaid_amount"] == 30000

        edited = client.put(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}",
            json={"expected_amount": 36000, "notes": "合同约定三个月押金"},
        )
        assert edited.status_code == 200
        deposit = edited.json()
        assert deposit["expected_amount"] == 36000
        assert deposit["status"] == "unpaid"

        received = client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/receive",
            json={"amount": 10000, "action_date": today_in_timezone().isoformat(), "notes": "首笔到账"},
        )
        assert received.status_code == 200
        deposit = received.json()
        assert deposit["received_amount"] == 10000
        assert deposit["unpaid_amount"] == 26000
        assert deposit["status"] == "partial"

        detail = client.get(f"/api/contracts/{contract['id']}").json()
        assert detail["deposit_summary"]["expected_amount"] == 36000
        assert detail["deposit_summary"]["received_amount"] == 10000
        assert detail["deposit_summary"]["unpaid_amount"] == 26000
        assert detail["deposits"][0]["id"] == deposit["id"]

        customer_detail = client.get(f"/api/customers/{customer['id']}").json()
        summary = customer_detail["operating_summary"]
        assert summary["deposit_unpaid_amount"] == 26000
        assert any(cue["type"] == "deposit_unpaid" for cue in summary["action_cues"])

        assert client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/refund",
            json={"amount": 12000},
        ).status_code == 400

        refunded = client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/refund",
            json={"amount": 2000, "action_date": today_in_timezone().isoformat()},
        )
        assert refunded.status_code == 200
        deposit = refunded.json()
        assert deposit["refunded_amount"] == 2000
        assert deposit["available_amount"] == 8000

        assert client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/offset",
            json={"amount": 9000},
        ).status_code == 400

        offset = client.post(
            f"/api/contracts/{contract['id']}/deposits/{deposit['id']}/offset",
            json={"amount": 3000, "notes": "轻量记录冲抵历史逾期，不改月度应收"},
        )
        assert offset.status_code == 200
        deposit = offset.json()
        assert deposit["offset_amount"] == 3000
        assert deposit["available_amount"] == 5000

        logs = client.get(f"/api/contracts/{contract['id']}/logs").json()
        actions = {item["action"] for item in logs}
        assert {"deposit_create", "deposit_update", "deposit_receive", "deposit_refund", "deposit_offset"} <= actions


def test_deposit_paid_status_negative_validation_and_customer_refund_hint() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        today = today_in_timezone()
        customer = client.post("/api/customers", json={"name": "押金退还客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-DEPOSIT-REFUND",
                "DEP-201",
                customer_id=customer["id"],
                start_date=(today - timedelta(days=60)).isoformat(),
                end_date=(today - timedelta(days=1)).isoformat(),
            ),
        ).json()

        negative = client.post(
            f"/api/contracts/{contract['id']}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": -1},
        )
        assert negative.status_code == 400

        created = client.post(
            f"/api/contracts/{contract['id']}/deposits",
            json={"deposit_type": "履约保证金", "expected_amount": 5000},
        ).json()
        paid = client.post(
            f"/api/contracts/{contract['id']}/deposits/{created['id']}/receive",
            json={"amount": 5000},
        )
        assert paid.status_code == 200
        assert paid.json()["status"] == "paid"

        detail = client.get(f"/api/customers/{customer['id']}").json()
        summary = detail["operating_summary"]
        assert summary["deposit_refund_pending_amount"] == 5000
        assert any(cue["type"] == "deposit_refund_pending" for cue in summary["action_cues"])
