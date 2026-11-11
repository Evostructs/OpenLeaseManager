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


def _room_payload(room_no: str) -> dict:
    return {"tower": "南塔", "floor": "18", "room_no": room_no, "room_type": "办公", "area": 100}


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
        "tenant_name": f"{contract_no} tenant",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "internal_contact_person": "risk-owner",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    payload.update(overrides)
    return payload


def _create_customer(client: TestClient, name: str) -> dict:
    response = client.post("/api/customers", json={"name": name})
    assert response.status_code == 200
    return response.json()


def _create_contract(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/contracts", json=payload)
    assert response.status_code == 200
    return response.json()


def _upload_contract_body(client: TestClient, contract_id: int) -> None:
    response = client.post(
        "/api/attachments",
        data={"entity_type": "contract", "entity_id": str(contract_id), "category": "合同正文", "notes": ""},
        files={"file": ("contract.pdf", b"contract body", "application/pdf")},
    )
    assert response.status_code == 200


def test_risk_flags_cover_core_types_filters_and_detail_embeds() -> None:
    today = today_in_timezone()
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        for room_no in ("RISK-OD", "RISK-EXP", "RISK-FU", "RISK-DEP", "RISK-ADJ"):
            assert client.post("/api/rooms", json=_room_payload(room_no)).status_code == 200

        overdue_customer = _create_customer(client, "Risk overdue customer")
        expiring_customer = _create_customer(client, "Risk expiring customer")
        follow_customer = _create_customer(client, "Risk follow customer")
        deposit_customer = _create_customer(client, "Risk deposit customer")
        adjustment_customer = _create_customer(client, "Risk adjustment customer")

        overdue_contract = _create_contract(
            client,
            _contract_payload(
                "DEMO_A-RISK-OD",
                "RISK-OD",
                overdue_customer["id"],
                previous_month_start,
                today + timedelta(days=180),
            ),
        )
        _upload_contract_body(client, overdue_contract["id"])

        expiring_contract = _create_contract(
            client,
            _contract_payload("DEMO_A-RISK-EXP", "RISK-EXP", expiring_customer["id"], today, today + timedelta(days=45)),
        )

        follow_contract = _create_contract(
            client,
            _contract_payload("DEMO_A-RISK-FU", "RISK-FU", follow_customer["id"], today, today + timedelta(days=180)),
        )
        _upload_contract_body(client, follow_contract["id"])
        follow_up = client.post(
            f"/api/customers/{follow_customer['id']}/follow-ups",
            json={
                "contract_id": follow_contract["id"],
                "follow_up_type": "电话",
                "content": "Confirm overdue action",
                "next_action": "Call contact",
                "next_follow_up_date": (today - timedelta(days=8)).isoformat(),
                "owner": "risk-owner",
            },
        )
        assert follow_up.status_code == 200

        deposit_contract = _create_contract(
            client,
            _contract_payload("DEMO_A-RISK-DEP", "RISK-DEP", deposit_customer["id"], today, today + timedelta(days=180)),
        )
        _upload_contract_body(client, deposit_contract["id"])
        deposit = client.post(
            f"/api/contracts/{deposit_contract['id']}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 30000, "due_date": (today - timedelta(days=1)).isoformat()},
        )
        assert deposit.status_code == 200
        received = client.post(
            f"/api/contracts/{deposit_contract['id']}/deposits/{deposit.json()['id']}/receive",
            json={"amount": 10000, "action_date": today.isoformat()},
        )
        assert received.status_code == 200

        adjustment_contract = _create_contract(
            client,
            _contract_payload("DEMO_A-RISK-ADJ", "RISK-ADJ", adjustment_customer["id"], today, today + timedelta(days=180)),
        )
        _upload_contract_body(client, adjustment_contract["id"])
        adjustment = client.post(
            f"/api/contracts/{adjustment_contract['id']}/rent-terms",
            json={
                "rule_type": "temporary_adjustment",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
                "adjustment_type": "fixed_amount",
                "adjustment_value": 7000,
            },
        )
        assert adjustment.status_code == 200

        response = client.get("/api/risks", params={"limit": 100})
        assert response.status_code == 200
        data = response.json()
        risk_types = {item["risk_type"] for item in data["items"]}
        assert {
            "overdue_receivable",
            "contract_expiring",
            "follow_up_overdue",
            "deposit_unpaid",
            "attachment_missing",
            "large_adjustment",
        } <= risk_types
        assert data["summary"]["total"] >= 6
        assert data["summary"]["by_severity"]["high"] >= 2

        deposit_only = client.get("/api/risks", params={"risk_type": "deposit_unpaid"}).json()
        assert deposit_only["items"]
        assert {item["risk_type"] for item in deposit_only["items"]} == {"deposit_unpaid"}

        high_only = client.get("/api/risks", params={"severity": "high"}).json()
        assert high_only["items"]
        assert {item["severity"] for item in high_only["items"]} == {"high"}

        contract_detail = client.get(f"/api/contracts/{adjustment_contract['id']}")
        assert contract_detail.status_code == 200
        assert any(item["risk_type"] == "large_adjustment" for item in contract_detail.json()["risks"])

        customer_detail = client.get(f"/api/customers/{follow_customer['id']}")
        assert customer_detail.status_code == 200
        assert any(item["risk_type"] == "follow_up_overdue" for item in customer_detail.json()["risks"])

        dashboard = client.get("/api/dashboard/operations")
        assert dashboard.status_code == 200
        operations = dashboard.json()
        assert operations["metrics"]["risk_count"] >= data["summary"]["total"]
        assert operations["metrics"]["high_risk_count"] >= 2
        assert operations["risks"]["top"]
