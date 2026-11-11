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


def _create_contract(client: TestClient, contract_no: str, room_no: str, start: date, end: date, **overrides) -> int:
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
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


def _receivable_row(client: TestClient, month: str, contract_no: str) -> dict | None:
    response = client.get("/api/contracts/receivables/monthly", params={"month": month})
    assert response.status_code == 200, response.text
    for row in response.json()["rows"]:
        if row["contract_no"] == contract_no:
            return row
    return None


def test_payment_due_day_drives_overdue():
    """约定付款日的合同：当月超过付款日即逾期，不再一律等到月末。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    today = today_in_timezone()
    start = _month_start(-2)
    end = _month_end(_month_start(5))
    contract_id = _create_contract(
        client, "DEMO_A-DUEDAY", "D401", start, end, payment_due_day=1
    )
    detail = client.get(f"/api/contracts/{contract_id}").json()
    assert detail["payment_due_day"] == 1

    # 当月应收：付款日为 1 日，今天若已过 1 日则当月即逾期
    row = _receivable_row(client, _month_key(_month_start(0)), "DEMO_A-DUEDAY")
    assert row is not None
    assert row["due_date"].endswith("-01")
    if today.day > 1:
        assert row["is_overdue"] is True
        assert row["aging_days"] == today.day - 1
    # 未约定付款日的合同仍按月末
    plain_id = _create_contract(client, "DEMO_A-DUEDAY-PLAIN", "D402", start, end)
    plain = _receivable_row(client, _month_key(_month_start(0)), "DEMO_A-DUEDAY-PLAIN")
    assert plain["is_overdue"] is False

    # 付款日越界拒绝
    bad = client.put(f"/api/contracts/{contract_id}", json={"payment_due_day": 40})
    assert bad.status_code == 422


def test_statement_freezes_month_amount():
    """出账后该月金额固化：条款变更不再回溯影响，账单标记 frozen。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-3)
    end = _month_end(_month_start(3))
    contract_id = _create_contract(client, "DEMO_A-STMT", "D403", start, end)
    frozen_month = _month_key(_month_start(-2))

    generated = client.post(f"/api/contracts/receivables/statements/{frozen_month}/generate")
    assert generated.status_code == 200, generated.text
    assert generated.json()["created"] >= 1

    row = _receivable_row(client, frozen_month, "DEMO_A-STMT")
    assert row["statement_frozen"] is True
    assert row["rent"] == MONTHLY

    # 出账月份不再受"已结清保护"约束，可以加覆盖该月的条款……
    term = client.post(
        f"/api/contracts/{contract_id}/rent-terms",
        json={
            "rule_type": "fixed_monthly",
            "start_date": start.isoformat(),
            "end_date": _month_end(_month_start(-1)).isoformat(),
            "monthly_amount": 13000,
        },
    )
    assert term.status_code == 200, term.text

    # ……但固化月份金额不变，未固化月份按新条款
    frozen_row = _receivable_row(client, frozen_month, "DEMO_A-STMT")
    assert frozen_row["rent"] == MONTHLY
    assert frozen_row["statement_frozen"] is True
    open_row = _receivable_row(client, _month_key(_month_start(-1)), "DEMO_A-STMT")
    assert abs(open_row["rent"] - 13000) < 0.02
    assert open_row["statement_frozen"] is False

    # 重复出账默认跳过，regenerate 才覆盖
    again = client.post(f"/api/contracts/receivables/statements/{frozen_month}/generate")
    assert again.json()["created"] == 0
    assert again.json()["skipped"] >= 1


def test_statement_survives_termination_truncation():
    """已固化月份在退租截断租期后仍出现在台账（以账单为准）。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-4)
    end = _month_end(_month_start(4))
    contract_id = _create_contract(client, "DEMO_A-STMT-TERM", "D404", start, end)
    frozen_month = _month_key(_month_start(-1))
    assert client.post(f"/api/contracts/receivables/statements/{frozen_month}/generate").status_code == 200

    # 退租截断到固化月份之前
    terminate_end = _month_start(-2).replace(day=15)
    assert (
        client.post(f"/api/contracts/{contract_id}/terminate", json={"end_date": terminate_end.isoformat()}).status_code
        == 200
    )

    row = _receivable_row(client, frozen_month, "DEMO_A-STMT-TERM")
    assert row is not None, "固化月份不应因退租截断而从台账消失"
    assert row["statement_frozen"] is True
    assert row["rent"] == MONTHLY


def test_restore_contract_blocked_by_room_conflict():
    """软删合同恢复前校验房源占用：删除期间已被新合同占用则拒绝恢复。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-1)
    end = _month_end(_month_start(6))
    first_id = _create_contract(client, "DEMO_A-RESTORE-A", "D405", start, end)
    assert client.delete(f"/api/contracts/{first_id}").status_code == 200

    # 同房源同租期签了新合同
    _create_contract(client, "DEMO_A-RESTORE-B", "D405", start, end)

    restored = client.post(f"/api/contracts/recycle/restore/{first_id}")
    assert restored.status_code == 400
    assert "占用" in restored.json()["detail"] or "已关联" in restored.json()["detail"]


def test_purge_contract_cleans_attachment_records():
    """永久删除合同时清理附件记录，不留孤儿数据。"""
    client = _client()
    _ensure_admin(client)
    _login(client)

    start = _month_start(-1)
    end = _month_end(_month_start(6))
    contract_id = _create_contract(client, "DEMO_A-PURGE", "D406", start, end)
    upload = client.post(
        "/api/attachments",
        files={"file": ("note.pdf", b"%PDF-1.4 test", "application/pdf")},
        data={"entity_type": "contract", "entity_id": str(contract_id), "category": "其他"},
    )
    assert upload.status_code == 200, upload.text

    assert client.delete(f"/api/contracts/{contract_id}").status_code == 200
    assert client.delete(f"/api/contracts/recycle/purge/{contract_id}").status_code == 200

    from app.database import SessionLocal
    from app.models import Attachment

    db = SessionLocal()
    try:
        orphans = db.query(Attachment).filter(Attachment.entity_type == "contract", Attachment.entity_id == contract_id).count()
    finally:
        db.close()
    assert orphans == 0
