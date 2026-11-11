from __future__ import annotations

import calendar
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.main import app

# 系统口径：房源月租 = area * unit_price * 365 / 12（payload 中的 monthly_rent 不参与）。
# area=120, unit_price=2 -> 月租恰好 7300。
MONTHLY = 7300.0


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


def _month_start(offset: int) -> date:
    return today_in_timezone().replace(day=1) + relativedelta(months=offset)


def _month_end(anchor: date) -> date:
    return date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])


def _month_key(anchor: date) -> str:
    return anchor.strftime("%Y-%m")


def _contract_payload(contract_no: str, start: date, end: date, **overrides) -> dict:
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": "1801",
        "tenant_name": f"{contract_no} 租户",
        "area": 120,
        "unit_price": 2,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    payload.update(overrides)
    return payload


def _create_contract(client: TestClient, payload: dict) -> int:
    response = client.post("/api/contracts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _rent_by_month(client: TestClient, contract_id: int) -> dict[str, float]:
    response = client.get(f"/api/contracts/{contract_id}/rent-plan")
    assert response.status_code == 200, response.text
    return {row["month"]: row["rent"] for row in response.json()["plan"]}


def _receivable_row(client: TestClient, month: str, contract_no: str) -> dict | None:
    response = client.get("/api/contracts/receivables/monthly", params={"month": month})
    assert response.status_code == 200, response.text
    for row in response.json()["rows"]:
        if row["contract_no"] == contract_no:
            return row
    return None


def test_terminated_contract_keeps_historical_receivables():
    """已退租合同截断 end_date 后，截断前月份的应收（含欠款）不能从台账消失。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-4)
    end = _month_end(_month_start(7))
    contract_id = _create_contract(client, _contract_payload("DEMO_A-TERM-KEEP", start, end))

    terminate_end = _month_start(-2).replace(day=15)
    response = client.post(
        f"/api/contracts/{contract_id}/terminate",
        json={"end_date": terminate_end.isoformat(), "notes": "回归测试退租"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "已退租"

    # 退租前的完整历史月份：应收必须仍然存在且为整月租金
    history_month = _month_key(_month_start(-3))
    row = _receivable_row(client, history_month, "DEMO_A-TERM-KEEP")
    assert row is not None, "已退租合同的历史应收从台账中消失"
    assert row["rent"] == MONTHLY
    assert row["balance"] == MONTHLY
    assert row["is_overdue"] is True

    # 退租当月：按 1 日至退租日折算
    terminate_month_days = calendar.monthrange(terminate_end.year, terminate_end.month)[1]
    expected = round(MONTHLY * 15 / terminate_month_days, 2)
    row = _receivable_row(client, _month_key(_month_start(-2)), "DEMO_A-TERM-KEEP")
    assert row is not None
    assert abs(row["rent"] - expected) < 0.02

    # 退租之后的月份：不再产生应收
    assert _receivable_row(client, _month_key(_month_start(-1)), "DEMO_A-TERM-KEEP") is None


def test_rent_terms_uncovered_months_fall_back_to_legacy():
    """配置部分租金条款后，未被条款覆盖的时段按合同月租回退计算，不能归零。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(1))
    contract_id = _create_contract(
        client, _contract_payload("DEMO_A-TERM-FALLBACK", start, end, room_no="1802")
    )

    # fixed_monthly 条款只覆盖第 1 个月
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": _month_end(start).isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert response.status_code == 200, response.text

    rents = _rent_by_month(client, contract_id)
    assert abs(rents[_month_key(start)] - 13000) < 0.02
    # 未覆盖月份回退到合同月租，而不是 0
    assert abs(rents[_month_key(_month_start(-1))] - MONTHLY) < 0.02
    assert abs(rents[_month_key(_month_start(0))] - MONTHLY) < 0.02


def test_rent_terms_mid_month_boundary_prorates_both_sides():
    """条款月中结束时，覆盖段按条款、未覆盖段按旧口径，同月各按天数折算。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(1))
    contract_id = _create_contract(
        client, _contract_payload("DEMO_A-TERM-MIDMONTH", start, end, room_no="1803")
    )

    boundary_month = _month_start(-1)
    term_end = boundary_month.replace(day=15)
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": term_end.isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert response.status_code == 200, response.text

    month_days = calendar.monthrange(boundary_month.year, boundary_month.month)[1]
    expected = round(13000 * 15 / month_days + MONTHLY * (month_days - 15) / month_days, 2)
    rents = _rent_by_month(client, contract_id)
    assert abs(rents[_month_key(boundary_month)] - expected) < 0.02
    assert abs(rents[_month_key(_month_start(0))] - MONTHLY) < 0.02


def test_only_adjustment_terms_do_not_zero_out_rent():
    """只配置临时减免条款时，基础租金仍按旧口径产生，免租期不受影响。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-3)
    end = _month_end(_month_start(2))
    contract_id = _create_contract(
        client,
        _contract_payload("DEMO_A-TERM-ADJONLY", start, end, room_no="1804", free_period=1),
    )

    # 临时减免：第 3 个月减 2000
    adj_month = _month_start(-1)
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "temporary_adjustment",
            "start_date": adj_month.isoformat(),
            "end_date": _month_end(adj_month).isoformat(),
            "adjustment_type": "fixed_amount",
            "adjustment_value": 2000,
        },
    )
    assert response.status_code == 200, response.text

    rents = _rent_by_month(client, contract_id)
    # 免租期仍然生效（第 1 个月免租）
    assert rents[_month_key(start)] == 0
    # 非免租、无条款月份按合同月租计
    assert abs(rents[_month_key(_month_start(-2))] - MONTHLY) < 0.02
    # 减免月按条款抵减后计租
    assert abs(rents[_month_key(adj_month)] - (MONTHLY - 2000)) < 0.02


def test_rent_free_term_only_zeroes_covered_month():
    """只配置免租条款时，免租月归零、其余月份保持合同月租。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(1))
    contract_id = _create_contract(
        client, _contract_payload("DEMO_A-TERM-FREEONLY", start, end, room_no="1805")
    )

    free_month = _month_start(-1)
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "rent_free",
            "start_date": free_month.isoformat(),
            "end_date": _month_end(free_month).isoformat(),
        },
    )
    assert response.status_code == 200, response.text

    rents = _rent_by_month(client, contract_id)
    assert abs(rents[_month_key(start)] - MONTHLY) < 0.02
    assert rents[_month_key(free_month)] == 0
    assert abs(rents[_month_key(_month_start(0))] - MONTHLY) < 0.02


def test_multi_room_mid_month_room_start_is_prorated():
    """多房源合同中，房源月中起租时该月只按房源实际天数折算，不整月计费。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(1))
    room_b_month = _month_start(-1)
    room_b_start = room_b_month.replace(day=15)
    payload = _contract_payload("DEMO_A-MULTIROOM-PRORATE", start, end)
    # 房源月租同样由 area*unit_price 推导：A=60*4 -> 7300, B=30*4 -> 3650
    payload["room_items"] = [
        {"tower": "南塔", "room_no": "1806", "area": 60, "unit_price": 4},
        {
            "tower": "南塔",
            "room_no": "1807",
            "area": 30,
            "unit_price": 4,
            "start_date": room_b_start.isoformat(),
        },
    ]
    contract_id = _create_contract(client, payload)

    month_days = calendar.monthrange(room_b_month.year, room_b_month.month)[1]
    room_b_days = month_days - 15 + 1
    expected = round(7300 + 3650 * room_b_days / month_days, 2)

    rents = _rent_by_month(client, contract_id)
    assert abs(rents[_month_key(start)] - 7300) < 0.02
    assert abs(rents[_month_key(room_b_month)] - expected) < 0.02
    assert abs(rents[_month_key(_month_start(0))] - 10950) < 0.02

    row = _receivable_row(client, _month_key(room_b_month), "DEMO_A-MULTIROOM-PRORATE")
    assert row is not None
    assert abs(row["rent"] - expected) < 0.02


def test_rolling_free_period_spans_from_start_date():
    """免租 N 个月 = 自起租日起连续 N 个自然月：月中起租时免租跨月按天折算。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-3).replace(day=15)
    end = _month_end(_month_start(2))
    contract_id = _create_contract(
        client,
        _contract_payload("DEMO_A-FREE-ROLLING", start, end, room_no="1808", free_period=1),
    )

    rents = _rent_by_month(client, contract_id)
    first_month = _month_start(-3)
    second_month = _month_start(-2)
    second_days = calendar.monthrange(second_month.year, second_month.month)[1]
    # 免租窗口 [15日, 次月14日]：首月全免，次月 15 日起计租
    assert rents[_month_key(first_month)] == 0
    expected_second = round(MONTHLY * (second_days - 14) / second_days, 2)
    assert abs(rents[_month_key(second_month)] - expected_second) < 0.02
    assert abs(rents[_month_key(_month_start(-1))] - MONTHLY) < 0.02


def test_rolling_free_period_with_terms_fallback():
    """滚动免租与租金条款回退共存：免租窗口内不回退计租，窗口外正常。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-3).replace(day=10)
    end = _month_end(_month_start(2))
    contract_id = _create_contract(
        client,
        _contract_payload("DEMO_A-FREE-TERMS", start, end, room_no="1809", free_period=1),
    )

    adj_month = _month_start(-1)
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "temporary_adjustment",
            "start_date": adj_month.isoformat(),
            "end_date": _month_end(adj_month).isoformat(),
            "adjustment_type": "fixed_amount",
            "adjustment_value": 2000,
        },
    )
    assert response.status_code == 200, response.text

    rents = _rent_by_month(client, contract_id)
    first_month = _month_start(-3)
    second_month = _month_start(-2)
    second_days = calendar.monthrange(second_month.year, second_month.month)[1]
    assert rents[_month_key(first_month)] == 0
    expected_second = round(MONTHLY * (second_days - 9) / second_days, 2)
    assert abs(rents[_month_key(second_month)] - expected_second) < 0.05
    assert abs(rents[_month_key(adj_month)] - (MONTHLY - 2000)) < 0.02


def test_rolling_free_period_also_applies_to_explicit_base_term():
    """合同滚动免租必须抵减显式基础租金条款，不能只阻止旧口径回退。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-3).replace(day=10)
    end = _month_end(_month_start(2))
    contract_id = _create_contract(
        client,
        _contract_payload("DEMO_A-FREE-BASE-TERM", start, end, room_no="1811", free_period=1),
    )
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "monthly_amount": MONTHLY,
        },
    )
    assert response.status_code == 200, response.text

    rents = _rent_by_month(client, contract_id)
    first_month = _month_start(-3)
    second_month = _month_start(-2)
    second_days = calendar.monthrange(second_month.year, second_month.month)[1]
    assert rents[_month_key(first_month)] == 0
    assert abs(rents[_month_key(second_month)] - round(MONTHLY * (second_days - 9) / second_days, 2)) < 0.05


def test_net_rent_mid_month_lease_equals_monthly_rent():
    """净租金口径收敛：首尾不满月按实际占比计入分母，整 12 个月租期净租金=月租。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2).replace(day=15)
    end = start + relativedelta(months=12) - timedelta(days=1)
    contract_id = _create_contract(
        client, _contract_payload("DEMO_A-NETRENT-MID", start, end, room_no="1810")
    )

    detail = client.get(f"/api/contracts/{contract_id}").json()
    assert abs(detail["net_rent"] - MONTHLY) < 0.05


def test_net_rent_uses_rent_terms():
    """净租金支持租金条款：条款覆盖全租期时净租金=条款月租，与租金计划同口径。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-2)
    end = _month_end(_month_start(9))
    contract_id = _create_contract(
        client, _contract_payload("DEMO_A-NETRENT-TERM", start, end, room_no="1811")
    )
    response = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert response.status_code == 200, response.text

    detail = client.get(f"/api/contracts/{contract_id}").json()
    assert abs(detail["net_rent"] - 13000) < 0.05

    # 列表序列化与详情同口径
    rows = client.get("/api/contracts", params={"keyword": "DEMO_A-NETRENT-TERM"}).json()["items"]
    assert rows and abs(rows[0]["net_rent"] - 13000) < 0.05
