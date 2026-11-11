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


def _month_key(anchor: date) -> str:
    return anchor.strftime("%Y-%m")


def _create_contract(client: TestClient, contract_no: str, start: date, end: date, **overrides) -> int:
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": f"B{abs(hash(contract_no)) % 900 + 100}",
        "tenant_name": f"{contract_no} 租户",
        "area": 120,
        "unit_price": 2,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    payload.update(overrides)
    response = client.post("/api/contracts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_payment_month_must_be_within_contract_period():
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(3))
    contract_id = _create_contract(client, "DEMO_A-BOUND-MONTH", start, end)

    outside_month = _month_key(_month_start(-6))
    response = client.post(
        f"/api/contracts/receivables/monthly/{contract_id}/payments",
        json={"month": outside_month, "amount": 1000, "allocation_type": "收款"},
    )
    assert response.status_code == 400
    assert "租期" in response.json()["detail"]

    meta = client.patch(
        f"/api/contracts/receivables/monthly/{contract_id}",
        json={"month": outside_month, "dunning_status": "催收中"},
    )
    assert meta.status_code == 400

    inside = client.post(
        f"/api/contracts/receivables/monthly/{contract_id}/payments",
        json={"month": _month_key(_month_start(-1)), "amount": 1000, "allocation_type": "收款"},
    )
    assert inside.status_code == 200


def test_overlapping_base_rent_terms_rejected():
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(5))
    contract_id = _create_contract(client, "DEMO_A-BOUND-OVERLAP", start, end)

    first = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": _month_end(_month_start(0)).isoformat(),
            "monthly_amount": 10000,
        },
    )
    assert first.status_code == 200, first.text

    # 同期再加一条基础条款 → 双重计费，拦截
    overlap = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "area_unit_price",
            "start_date": _month_start(0).isoformat(),
            "end_date": _month_end(_month_start(2)).isoformat(),
            "area": 120,
            "unit_price": 3,
        },
    )
    assert overlap.status_code == 400
    assert "重叠" in overlap.json()["detail"]

    # 顺序衔接的阶梯条款不受影响
    sequential = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "stepped_rent",
            "start_date": _month_start(1).isoformat(),
            "end_date": _month_end(_month_start(3)).isoformat(),
            "monthly_amount": 11000,
        },
    )
    assert sequential.status_code == 200, sequential.text

    # 非基础条款（免租/临时减免）允许与基础条款同期
    rent_free = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "rent_free",
            "start_date": _month_start(0).isoformat(),
            "end_date": _month_end(_month_start(0)).isoformat(),
        },
    )
    assert rent_free.status_code == 200, rent_free.text


def test_explicit_zero_base_rent_terms_are_allowed_and_override_legacy_rent():
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(0)
    end = _month_end(start)
    payloads = (
        ("FIXED", {"rule_type": "fixed_monthly", "monthly_amount": 0}),
        ("AREA", {"rule_type": "area_unit_price", "area": 120, "unit_price": 0}),
        ("STEP", {"rule_type": "stepped_rent", "monthly_amount": 0}),
    )
    contract_ids = {}
    for suffix, term_payload in payloads:
        contract_id = _create_contract(
            client,
            f"DEMO_A-ZERO-{suffix}",
            start,
            end,
            room_no=f"ZERO-{suffix}",
        )
        response = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                **term_payload,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        contract_ids[suffix] = contract_id

    missing = _create_contract(
        client,
        "DEMO_A-ZERO-MISSING",
        start,
        end,
        room_no="ZERO-MISSING",
    )
    rejected = client.post(
        f"/api/contracts/{missing}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert rejected.status_code == 400

    for contract_id in contract_ids.values():
        plan = client.get(f"/api/contracts/{contract_id}/rent-plan").json()
        row = next(item for item in plan["plan"] if item["month"] == _month_key(start))
        assert row["rent"] == 0
        assert row["rent_source"] == "rent_terms"


def test_settled_month_protected_from_term_changes():
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(3))
    contract_id = _create_contract(client, "DEMO_A-BOUND-SETTLED", start, end)

    settled_month = _month_key(_month_start(-2))
    paid = client.post(
        f"/api/contracts/receivables/monthly/{contract_id}/payments",
        json={"month": settled_month, "amount": MONTHLY, "allocation_type": "收款"},
    )
    assert paid.status_code == 200

    # 覆盖已结清月份的条款会改写其应收 → 409 拦截
    blocked = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": _month_end(_month_start(-1)).isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert blocked.status_code == 409
    assert "已结清" in blocked.json()["detail"]

    # 只影响未结清月份的条款可以正常添加和删除
    allowed = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": _month_start(1).isoformat(),
            "end_date": _month_end(_month_start(2)).isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert allowed.status_code == 200, allowed.text
    term_id = allowed.json()["id"]
    deleted = client.delete(f"/api/contracts/{contract_id}/rent-terms/{term_id}")
    assert deleted.status_code == 200

    # 拦截后事务已回滚：条款列表中不应残留被拒的条款
    terms = client.get(f"/api/contracts/{contract_id}/rent-terms").json()
    assert all(term["monthly_amount"] != 13000 for term in terms)


def test_settled_month_protected_from_contract_update():
    client = _client()
    _ensure_admin(client)
    _login(client)

    # 已到期合同（不受"在租锁定"约束）仍不能改动已结清月份的应收
    start = _month_start(-5)
    end = _month_end(_month_start(-2))
    contract_id = _create_contract(client, "DEMO_A-BOUND-EXPIRED", start, end)

    settled_month = _month_key(_month_start(-4))
    paid = client.post(
        f"/api/contracts/receivables/monthly/{contract_id}/payments",
        json={"month": settled_month, "amount": MONTHLY, "allocation_type": "收款"},
    )
    assert paid.status_code == 200

    blocked = client.put(f"/api/contracts/{contract_id}", json={"area": 120, "unit_price": 3})
    assert blocked.status_code == 409
    assert "已结清" in blocked.json()["detail"]

    # 不影响应收的字段仍可修改
    ok = client.put(f"/api/contracts/{contract_id}", json={"notes": "已到期合同备注"})
    assert ok.status_code == 200, ok.text
