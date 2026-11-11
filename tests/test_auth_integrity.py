from __future__ import annotations

import calendar
from datetime import date, timedelta
import importlib
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from dateutil.relativedelta import relativedelta
import openpyxl
import pytest

from app.core.config import get_settings
from app.core.crypto import decrypt_file, is_encrypted_file
from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    Building,
    Contract,
    ContractDeposit,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    OperationLog,
    RentTerm,
    Room,
    SystemConfig,
    Zone,
)
from app.services.backup import backup_type_from_filename, create_backup_archive


def _client() -> TestClient:
    return TestClient(app)


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert response.status_code == 200


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        _register_admin(client)


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
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


def _contract_payload(contract_no: str, room_no: str, **overrides) -> dict:
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"测试客户{contract_no}",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "internal_contact_person": "李四",
        "internal_contact_feishu_id": "ou_demo_lisi",
        "property_fee_rate": 28,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    payload.update(overrides)
    return payload


def _room_payload(room_no: str, **overrides) -> dict:
    payload = {"tower": "南塔", "floor": "9", "room_no": room_no, "room_type": "办公", "area": 100}
    payload.update(overrides)
    return payload


def _expected_monthly_rent(area: float = 100, unit_price: float = 4) -> float:
    return round(area * unit_price * 365 / 12, 2)


def _rent_plan_row(client: TestClient, contract_id: int, month: str) -> tuple[dict, dict]:
    response = client.get(f"/api/contracts/{contract_id}/rent-plan")
    assert response.status_code == 200
    data = response.json()
    return next(item for item in data["plan"] if item["month"] == month), data["summary"]


def _monthly_receivable_row(client: TestClient, contract_no: str, month: str) -> dict:
    response = client.get("/api/contracts/receivables/monthly", params={"month": month})
    assert response.status_code == 200
    return next(item for item in response.json()["rows"] if item["contract_no"] == contract_no)


def _expected_broker_fee(area: float = 100, unit_price: float = 4, months: float = 2.5) -> float:
    # 与应用同口径：财务四舍五入（round_money），而非内置银行家舍入
    from app.core.money import round_money

    return round_money(_expected_monthly_rent(area, unit_price) * months)


def test_auth_permissions_and_session_invalidation() -> None:
    with _client() as admin_client:
        status = admin_client.get("/api/auth/status").json()
        assert "need_init" in status
        _ensure_admin(admin_client)
        _login(admin_client, "admin", "Admin123")

        response = admin_client.post(
            "/api/auth/create-user",
            json={"username": "viewer_auth", "password": "Viewer123", "display_name": "Viewer", "role": "viewer"},
        )
        assert response.status_code == 200

        with _client() as viewer_client:
            _login(viewer_client, "viewer_auth", "Viewer123")
            assert viewer_client.get("/api/contracts").status_code == 200
            assert viewer_client.get("/api/auth/users").status_code == 403

            users = admin_client.get("/api/auth/users").json()
            viewer_id = next(user["id"] for user in users if user["username"] == "viewer_auth")
            response = admin_client.post(
                "/api/auth/reset-password",
                json={"user_id": viewer_id, "new_password": "Viewer456"},
            )
            assert response.status_code == 200

            assert viewer_client.get("/api/auth/me").status_code == 401
            _login(viewer_client, "viewer_auth", "Viewer456")


def test_system_version_endpoint_reports_configured_version(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("APP_VERSION", "test-version")
    get_settings.cache_clear()
    try:
        with _client() as client:
            public_response = client.get("/api/version")
            assert public_response.status_code == 200
            assert public_response.json()["version"] == "test-version"

            _ensure_admin(client)
            _login(client, "admin", "Admin123")
            response = client.get("/api/system/version")
            assert response.status_code == 200
            assert response.json()["version"] == "test-version"
    finally:
        get_settings.cache_clear()


def test_contract_crud_update_delete_and_restore() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post("/api/contracts", json=_contract_payload("DEMO_A-CRUD-001", "CRUD01"))
        assert created.status_code == 200
        contract_id = created.json()["id"]
        assert created.json()["internal_contact_person"] == "李四"
        assert created.json()["broker_fee"] == _expected_broker_fee()
        assert created.json()["lease_type"] == "新签"

        manual_broker_fee = _expected_broker_fee(months=2.9)
        updated = client.put(
            f"/api/contracts/{contract_id}",
            json={"tenant_name": "更新客户", "internal_contact_person": "王五", "lease_type": "续租", "broker_fee": manual_broker_fee},
        )
        assert updated.status_code == 200
        assert updated.json()["tenant_name"] == "更新客户"
        assert updated.json()["internal_contact_person"] == "王五"
        assert updated.json()["lease_type"] == "续签"
        assert updated.json()["broker_fee"] == manual_broker_fee

        detail = client.get(f"/api/contracts/{contract_id}")
        assert detail.status_code == 200
        assert detail.json()["tenant_name"] == "更新客户"
        assert detail.json()["internal_contact_person"] == "王五"
        assert detail.json()["lease_type"] == "续签"
        assert detail.json()["broker_fee"] == manual_broker_fee

        deleted = client.delete(f"/api/contracts/{contract_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/contracts/{contract_id}").status_code == 404

        restored = client.post(f"/api/contracts/recycle/restore/{contract_id}")
        assert restored.status_code == 200
        assert client.get(f"/api/contracts/{contract_id}").status_code == 200


def test_contract_successor_draft_prefills_lifecycle_fields() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post("/api/contracts", json=_contract_payload("DEMO_A-LIFECYCLE-OLD", "LIFE01"))
        assert created.status_code == 200
        contract_id = created.json()["id"]

        draft = client.get(f"/api/contracts/{contract_id}/successor-draft", params={"lease_type": "变更"})
        assert draft.status_code == 200
        data = draft.json()
        assert data["id"] is None
        assert data["contract_no"].startswith(f"DEMO_A-{today_in_timezone().year}-")
        assert data["lease_type"] == "变更签"
        assert data["prev_contract_no"] == "DEMO_A-LIFECYCLE-OLD"
        assert data["start_date"] == "2027-01-01"
        assert data["end_date"] == "2027-12-31"
        assert data["disposal"] == ""
        assert data["pdf_path"] == ""
        assert data["broker_fee"] == 0
        assert [item["room_no"] for item in data["room_items"]] == ["LIFE01"]


def test_contract_detail_exposes_lifecycle_links_and_room_summary() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        old = client.post("/api/contracts", json=_contract_payload("DEMO_A-LIFECYCLE-LINK-OLD", "LIFE04"))
        assert old.status_code == 200
        old_id = old.json()["id"]

        new = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-LIFECYCLE-LINK-NEW",
                "LIFE04",
                lease_type="续签",
                start_date="2027-01-01",
                end_date="2027-12-31",
                prev_contract_no="DEMO_A-LIFECYCLE-LINK-OLD",
            ),
        )
        assert new.status_code == 200
        new_id = new.json()["id"]

        old_detail = client.get(f"/api/contracts/{old_id}")
        assert old_detail.status_code == 200
        old_data = old_detail.json()
        assert old_data["previous_contract"] is None
        assert old_data["room_no"] == "LIFE04"
        assert [item["room_no"] for item in old_data["room_items"]] == ["LIFE04"]
        assert [item["contract_no"] for item in old_data["next_contracts"]] == ["DEMO_A-LIFECYCLE-LINK-NEW"]
        assert old_data["next_contracts"][0]["room_no"] == "LIFE04"
        assert old_data["next_contracts"][0]["lease_type"] == "续签"

        new_detail = client.get(f"/api/contracts/{new_id}")
        assert new_detail.status_code == 200
        new_data = new_detail.json()
        assert new_data["previous_contract"]["contract_no"] == "DEMO_A-LIFECYCLE-LINK-OLD"
        assert new_data["previous_contract"]["room_no"] == "LIFE04"
        assert new_data["next_contracts"] == []
        assert new_data["room_no"] == "LIFE04"
        assert [item["room_no"] for item in new_data["room_items"]] == ["LIFE04"]


def test_contract_change_confirm_previews_receivables_and_truncates_previous_contract() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        old = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-LIFECYCLE-CHANGE-OLD", "LIFE05"),
        )
        assert old.status_code == 200
        old_id = old.json()["id"]

        payment = client.post(
            f"/api/contracts/receivables/monthly/{old_id}/payments",
            json={"month": "2026-07", "amount": 1200, "allocation_type": "收款", "notes": "预收款"},
        )
        assert payment.status_code == 200

        payload = _contract_payload(
            "DEMO_A-LIFECYCLE-CHANGE-NEW",
            "LIFE05",
            lease_type="变更签",
            start_date="2026-07-01",
            end_date="2026-12-31",
            unit_price=5,
            prev_contract_no="DEMO_A-LIFECYCLE-CHANGE-OLD",
        )
        preview = client.post("/api/contracts/change-preview", json=payload)
        assert preview.status_code == 200
        preview_data = preview.json()
        assert preview_data["previous_contract"]["contract_no"] == "DEMO_A-LIFECYCLE-CHANGE-OLD"
        assert preview_data["new_contract"]["contract_no"] == "DEMO_A-LIFECYCLE-CHANGE-NEW"
        assert preview_data["change_effective_date"] == "2026-07-01"
        assert preview_data["previous_end_date_before"] == "2026-12-31"
        assert preview_data["previous_end_date_after"] == "2026-06-30"
        assert "不会自动生成冲抵" in preview_data["receivable_policy"]
        assert preview_data["receivable_impacts"] == [
            {
                "month": "2026-07",
                "has_record": True,
                "payment_count": 1,
                "received_amount": 1200.0,
                "adjustment_amount": 0.0,
                "settlement_amount": 1200.0,
            }
        ]

        plain_create = client.post("/api/contracts", json=payload)
        assert plain_create.status_code == 400
        assert "已关联合同" in plain_create.json()["detail"]

        confirmed = client.post("/api/contracts/change-confirm", json={"contract": payload})
        assert confirmed.status_code == 200
        new_data = confirmed.json()
        assert new_data["contract_no"] == "DEMO_A-LIFECYCLE-CHANGE-NEW"
        assert new_data["prev_contract_no"] == "DEMO_A-LIFECYCLE-CHANGE-OLD"
        assert new_data["room_no"] == "LIFE05"

        old_detail = client.get(f"/api/contracts/{old_id}")
        assert old_detail.status_code == 200
        old_data = old_detail.json()
        assert old_data["end_date"] == "2026-06-30"
        assert old_data["disposal"] == "已变更"
        assert [item["contract_no"] for item in old_data["next_contracts"]] == ["DEMO_A-LIFECYCLE-CHANGE-NEW"]

        logs = client.get(f"/api/contracts/{old_id}/logs")
        assert logs.status_code == 200
        assert any(log["action"] == "change_confirm" and "DEMO_A-LIFECYCLE-CHANGE-NEW" in log["changes"] for log in logs.json())


def test_contract_terminate_workflow_updates_locked_active_contract() -> None:
    today = today_in_timezone()
    start_date = today - timedelta(days=120)
    end_date = today + timedelta(days=160)
    terminate_date = today - timedelta(days=1)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-LIFECYCLE-TERM",
                "LIFE02",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        assert created.json()["status"] == "在租"

        locked = client.put(f"/api/contracts/{contract_id}", json={"end_date": terminate_date.isoformat()})
        assert locked.status_code == 400
        assert "续租/变更流程" in locked.json()["detail"]

        terminated = client.post(
            f"/api/contracts/{contract_id}/terminate",
            json={"end_date": terminate_date.isoformat(), "notes": "客户提前退租"},
        )
        assert terminated.status_code == 200
        assert terminated.json()["end_date"] == terminate_date.isoformat()
        assert terminated.json()["disposal"] == "已退租"
        assert terminated.json()["status"] == "已退租"

        logs = client.get(f"/api/contracts/{contract_id}/logs")
        assert logs.status_code == 200
        assert any(log["action"] == "terminate" and "客户提前退租" in log["changes"] for log in logs.json())

        invalid = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-LIFECYCLE-BADTERM",
                "LIFE03",
                start_date=today.isoformat(),
                end_date=(today + timedelta(days=30)).isoformat(),
            ),
        )
        assert invalid.status_code == 200
        rejected = client.post(
            f"/api/contracts/{invalid.json()['id']}/terminate",
            json={"end_date": (today - timedelta(days=1)).isoformat()},
        )
        assert rejected.status_code == 400
        assert "退租日期不能早于起租日" in rejected.json()["detail"]


def test_integrity_reports_active_contracts_without_pdf() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-T-001",
                "tower": "南塔",
                "room_no": "S0101",
                "tenant_name": "测试客户",
                "area": 100,
                "unit_price": 4,
                "monthly_rent": 12000,
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 200

        # 新入口会自动补齐房源台账和合同房源关系；另造一条历史坏记录，
        # 继续验证完整性检查仍能发现旧数据缺口。
        db = SessionLocal()
        try:
            building = db.query(Building).filter(Building.code == "DEMO_A").one()
            db.add(
                Contract(
                    contract_no="DEMO_A-T-LEGACY-MISSING-ROOM",
                    building_id=building.id,
                    tower="南塔",
                    room_no="S0199",
                    tenant_name="历史缺房源关系",
                    area=100,
                    unit_price=4,
                    monthly_rent=12000,
                    property_fee_rate=28,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                )
            )
            db.commit()
        finally:
            db.close()

        report = client.get("/api/system/integrity-check")
        assert report.status_code == 200
        issue_types = {issue["type"] for issue in report.json()["issues"]}
        assert "missing_contract_pdfs" in issue_types
        assert "missing_room_reference" in issue_types


def test_contract_list_sorts_before_pagination() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        end_dates = ["2026-09-30", "2026-12-31", "2026-06-30"]
        for index, unit_price in enumerate([1, 3, 2], start=1):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"DEMO_A-SORT-{index}",
                    "tower": "南塔",
                    "room_no": f"S{index:04d}",
                    "tenant_name": f"排序客户{index}",
                    "area": 100,
                    "unit_price": unit_price,
                    "monthly_rent": 1,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": end_dates[index - 1],
                },
            )
            assert response.status_code == 200

        response = client.get("/api/contracts?page=1&page_size=2&keyword=DEMO_A-SORT&sort_by=monthly_rent&sort_dir=desc")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [row["monthly_rent"] for row in rows] == [_expected_monthly_rent(unit_price=3), _expected_monthly_rent(unit_price=2)]

        response = client.get("/api/contracts?page=1&page_size=0&keyword=DEMO_A-SORT&sort_by=monthly_rent&sort_dir=desc")
        assert response.status_code == 200
        assert response.json()["total"] == 3
        assert len(response.json()["items"]) == 3

        response = client.get("/api/contracts?page=1&page_size=3&keyword=DEMO_A-SORT&sort_by=end_date&sort_dir=asc")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [row["contract_no"] for row in rows] == ["DEMO_A-SORT-3", "DEMO_A-SORT-1", "DEMO_A-SORT-2"]


def test_contract_rejects_negative_financial_fields() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        base_payload = {
            "contract_no": "DEMO_A-NEGATIVE-001",
            "tower": "南塔",
            "room_no": "S9901",
            "tenant_name": "负数校验客户",
            "area": 100,
            "unit_price": 4,
            "monthly_rent": 12000,
            "property_fee_rate": 28,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        }
        for field in ["unit_price", "broker_fee", "deposit", "property_fee_rate", "free_period"]:
            payload = {**base_payload, "contract_no": f"DEMO_A-NEGATIVE-{field}", field: -1}
            response = client.post("/api/contracts", json=payload)
            assert response.status_code == 400


def test_contract_rejects_overlapping_room_lease_periods() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        base_payload = {
            "tower": "南塔",
            "room_no": "S9902",
            "tenant_name": "重叠校验客户",
            "area": 100,
            "unit_price": 4,
            "monthly_rent": 12000,
            "property_fee_rate": 28,
        }
        response = client.post(
            "/api/contracts",
            json={
                **base_payload,
                "contract_no": "DEMO_A-OVERLAP-001",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 200

        response = client.post(
            "/api/contracts",
            json={
                **base_payload,
                "contract_no": "DEMO_A-OVERLAP-002",
                "tenant_name": "重叠校验客户二",
                "start_date": "2026-06-01",
                "end_date": "2027-05-31",
            },
        )
        assert response.status_code == 400


def test_contract_room_without_dates_inherits_contract_period() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post("/api/contracts", json=_contract_payload("DEMO_A-ROOM-PERIOD-INHERIT", "PERIOD-INHERIT"))
        assert response.status_code == 200
        contract_id = response.json()["id"]

        detail = client.get(f"/api/contracts/{contract_id}")
        assert detail.status_code == 200
        room_item = detail.json()["room_items"][0]
        assert room_item["start_date"] == "2026-01-01"
        assert room_item["end_date"] == "2026-12-31"

        db = SessionLocal()
        try:
            stored = db.query(ContractRoom).filter(ContractRoom.contract_id == contract_id).one()
            assert stored.start_date is None
            assert stored.end_date is None
        finally:
            db.close()


def test_room_status_uses_contract_room_effective_period() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        room = client.post("/api/rooms", json={"tower": "南塔", "floor": "9", "room_no": "PERIOD-ACTIVE", "room_type": "办公", "area": 100})
        assert room.status_code == 200
        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-ACTIVE",
                "PERIOD-ACTIVE",
                room_items=[
                    {
                        "tower": "南塔",
                        "room_no": "PERIOD-ACTIVE",
                        "area": 100,
                        "unit_price": 4,
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-31",
                    }
                ],
            ),
        )
        assert created.status_code == 200

        february = client.get("/api/rooms", params={"start_date": "2026-02-01", "end_date": "2026-02-28"})
        assert february.status_code == 200
        assert next(item for item in february.json() if item["room_no"] == "PERIOD-ACTIVE")["occupied"] is False

        march = client.get("/api/rooms", params={"start_date": "2026-03-15", "end_date": "2026-03-15"})
        assert march.status_code == 200
        room_row = next(item for item in march.json() if item["room_no"] == "PERIOD-ACTIVE")
        assert room_row["occupied"] is True
        assert room_row["contract"]["start_date"] == "2026-03-01"
        assert room_row["contract"]["end_date"] == "2026-03-31"


def test_same_room_can_be_reused_across_non_overlapping_room_periods() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        first = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-SEQ-1",
                "PERIOD-SEQ",
                room_items=[
                    {"tower": "南塔", "room_no": "PERIOD-SEQ", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-06-30"}
                ],
            ),
        )
        assert first.status_code == 200

        second = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-SEQ-2",
                "PERIOD-SEQ",
                tenant_name="承接客户",
                room_items=[
                    {"tower": "南塔", "room_no": "PERIOD-SEQ", "area": 100, "unit_price": 4, "start_date": "2026-07-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert second.status_code == 200


def test_same_room_overlapping_contract_room_periods_are_rejected() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        first = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-OVERLAP-1",
                "PERIOD-OVERLAP",
                room_items=[
                    {"tower": "南塔", "room_no": "PERIOD-OVERLAP", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-06-30"}
                ],
            ),
        )
        assert first.status_code == 200

        second = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-OVERLAP-2",
                "PERIOD-OVERLAP",
                tenant_name="重叠客户",
                room_items=[
                    {"tower": "南塔", "room_no": "PERIOD-OVERLAP", "area": 100, "unit_price": 4, "start_date": "2026-06-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert second.status_code == 400


def test_room_contract_history_displays_contract_room_period() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        room = client.post("/api/rooms", json={"tower": "北塔", "floor": "10", "room_no": "PERIOD-HISTORY", "room_type": "办公", "area": 88})
        assert room.status_code == 200
        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-HISTORY",
                "PERIOD-HISTORY",
                room_items=[
                    {
                        "tower": "北塔",
                        "room_no": "PERIOD-HISTORY",
                        "area": 88,
                        "unit_price": 5,
                        "start_date": "2026-04-01",
                        "end_date": "2026-05-31",
                    }
                ],
            ),
        )
        assert created.status_code == 200

        history = client.get("/api/rooms/PERIOD-HISTORY/contracts", params={"start_date": "2026-03-01", "end_date": "2026-03-31"})
        assert history.status_code == 200
        row = history.json()["contracts"][0]
        assert row["start_date"] == "2026-04-01"
        assert row["end_date"] == "2026-05-31"
        assert row["range_overlap"] is False


def test_monthly_receivables_only_show_effective_room_items_for_month() -> None:
    first_rent = _expected_monthly_rent(area=100, unit_price=4)
    second_rent = _expected_monthly_rent(area=80, unit_price=5)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ROOM-PERIOD-RECEIVABLE",
                "PERIOD-JAN、PERIOD-FEB",
                room_items=[
                    {"tower": "南塔", "room_no": "PERIOD-JAN", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                    {"tower": "南塔", "room_no": "PERIOD-FEB", "area": 80, "unit_price": 5, "start_date": "2026-02-01", "end_date": "2026-02-28"},
                ],
            ),
        )
        assert created.status_code == 200

        january = client.get("/api/contracts/receivables/monthly", params={"month": "2026-01"})
        assert january.status_code == 200
        january_row = next(item for item in january.json()["rows"] if item["contract_no"] == "DEMO_A-ROOM-PERIOD-RECEIVABLE")
        assert january_row["room_no"] == "PERIOD-JAN"
        assert january_row["area"] == 100
        assert january_row["rent"] == first_rent

        february = client.get("/api/contracts/receivables/monthly", params={"month": "2026-02"})
        assert february.status_code == 200
        february_row = next(item for item in february.json()["rows"] if item["contract_no"] == "DEMO_A-ROOM-PERIOD-RECEIVABLE")
        assert february_row["room_no"] == "PERIOD-FEB"
        assert february_row["area"] == 80
        assert february_row["rent"] == second_rent


def test_rent_terms_fallback_keeps_legacy_contract_calculation() -> None:
    expected = _expected_monthly_rent(area=100, unit_price=4)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post("/api/contracts", json=_contract_payload("DEMO_A-RENTTERM-FALLBACK", "RENTTERM-FALLBACK"))
        assert created.status_code == 200
        contract_id = created.json()["id"]

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "legacy"
        assert plan_row["rent_source"] == "legacy"
        assert plan_row["rent"] == expected

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-FALLBACK", "2026-01")
        assert monthly_row["rent_source"] == "legacy"
        assert monthly_row["rent"] == expected
        assert monthly_row["total"] == expected


def test_area_unit_price_rent_term_prorates_by_calendar_days() -> None:
    # 条款覆盖 1/15-1/31（17 天），未覆盖的 1/1-1/14（14 天）按合同房源月租旧口径回退
    expected = round(
        _expected_monthly_rent(area=100, unit_price=6) * 17 / 31 + _expected_monthly_rent() * 14 / 31,
        2,
    )

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-AREA", "RENTTERM-AREA", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        term = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "area_unit_price",
                "start_date": "2026-01-15",
                "end_date": "2026-01-31",
                "area": 100,
                "unit_price": 6,
            },
        )
        assert term.status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["rent_source"] == "rent_terms"
        assert plan_row["rent"] == expected
        assert plan_row["gross_rent"] == expected

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-AREA", "2026-01")
        assert monthly_row["rent_source"] == "rent_terms"
        assert monthly_row["rent"] == expected
        assert monthly_row["total"] == expected


def test_fixed_monthly_rent_term_prorates_by_calendar_days() -> None:
    # 条款覆盖 1/10-1/20（11 天，31000/31*11=11000），其余 20 天按旧口径回退
    expected = round(11000 + _expected_monthly_rent() * 20 / 31, 2)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-FIXED", "RENTTERM-FIXED", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        term = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "fixed_monthly",
                "start_date": "2026-01-10",
                "end_date": "2026-01-20",
                "monthly_amount": 31000,
            },
        )
        assert term.status_code == 200

        plan_row, _summary = _rent_plan_row(client, contract_id, "2026-01")
        assert plan_row["rent_source"] == "rent_terms"
        assert plan_row["rent"] == expected

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-FIXED", "2026-01")
        assert monthly_row["rent"] == expected
        assert monthly_row["total"] == expected


def test_rent_free_term_deducts_overlapping_natural_days() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-FREE", "RENTTERM-FREE", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        fixed = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "fixed_monthly",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "monthly_amount": 31000,
            },
        )
        assert fixed.status_code == 200
        free = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "rent_free", "start_date": "2026-01-10", "end_date": "2026-01-20"},
        )
        assert free.status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 31000
        assert plan_row["rent_free_amount"] == 11000
        assert plan_row["rent"] == 20000

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-FREE", "2026-01")
        assert monthly_row["gross_rent"] == 31000
        assert monthly_row["rent_free_amount"] == 11000
        assert monthly_row["rent"] == 20000
        assert monthly_row["total"] == 20000


def test_room_scoped_rent_term_only_applies_to_room_effective_period() -> None:
    expected_january = _expected_monthly_rent(area=100, unit_price=6)
    # 2 月房源 B 无任何条款覆盖，按其房源月租旧口径回退计租
    expected_february = _expected_monthly_rent(area=80, unit_price=5)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RENTTERM-ROOM",
                "RENTTERM-ROOM-A、RENTTERM-ROOM-B",
                start_date="2026-01-01",
                end_date="2026-02-28",
                room_items=[
                    {"tower": "南塔", "room_no": "RENTTERM-ROOM-A", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                    {"tower": "南塔", "room_no": "RENTTERM-ROOM-B", "area": 80, "unit_price": 5, "start_date": "2026-02-01", "end_date": "2026-02-28"},
                ],
            ),
        )
        assert created.status_code == 200
        contract = created.json()
        contract_id = contract["id"]
        january_room_id = next(item["id"] for item in contract["room_items"] if item["room_no"] == "RENTTERM-ROOM-A")

        term = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "contract_room_id": january_room_id,
                "rule_type": "area_unit_price",
                "start_date": "2026-01-01",
                "end_date": "2026-02-28",
                "area": 100,
                "unit_price": 6,
            },
        )
        assert term.status_code == 200
        assert term.json()["room_no"] == "RENTTERM-ROOM-A"

        january_plan, summary = _rent_plan_row(client, contract_id, "2026-01")
        february_plan, _summary = _rent_plan_row(client, contract_id, "2026-02")
        assert summary["rent_source"] == "rent_terms"
        assert january_plan["rent"] == expected_january
        assert february_plan["rent"] == expected_february

        january_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-ROOM", "2026-01")
        february_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-ROOM", "2026-02")
        assert january_receivable["rent"] == january_plan["rent"]
        assert february_receivable["rent"] == february_plan["rent"]
        assert january_receivable["rent_source"] == "rent_terms"
        assert february_receivable["rent_source"] == "rent_terms"


def test_stepped_rent_terms_support_fixed_and_area_unit_segments() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-STEPPED", "RENTTERM-STEPPED", start_date="2026-01-01", end_date="2026-02-28"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        january = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "stepped_rent", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        )
        assert january.status_code == 200
        february = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "stepped_rent", "start_date": "2026-02-01", "end_date": "2026-02-28", "area": 100, "unit_price": 6},
        )
        assert february.status_code == 200

        january_plan, summary = _rent_plan_row(client, contract_id, "2026-01")
        february_plan, _summary = _rent_plan_row(client, contract_id, "2026-02")
        assert summary["rent_source"] == "rent_terms"
        assert january_plan["rent"] == 31000
        assert february_plan["rent"] == _expected_monthly_rent(area=100, unit_price=6)

        january_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-STEPPED", "2026-01")
        february_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-STEPPED", "2026-02")
        assert january_receivable["rent"] == january_plan["rent"]
        assert february_receivable["rent"] == february_plan["rent"]


def test_stepped_rent_cross_month_segment_prorates_by_natural_days() -> None:
    expected_february = round(31000 * 10 / 28 + 56000 * 18 / 28, 2)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-STEP-CROSS", "RENTTERM-STEP-CROSS", start_date="2026-01-01", end_date="2026-02-28"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        first = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "stepped_rent", "start_date": "2026-01-01", "end_date": "2026-02-10", "monthly_amount": 31000},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "stepped_rent", "start_date": "2026-02-11", "end_date": "2026-02-28", "monthly_amount": 56000},
        )
        assert second.status_code == 200

        january_plan, summary = _rent_plan_row(client, contract_id, "2026-01")
        february_plan, _summary = _rent_plan_row(client, contract_id, "2026-02")
        assert summary["rent_source"] == "rent_terms"
        assert january_plan["rent"] == 31000
        assert february_plan["rent"] == expected_february

        february_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-STEP-CROSS", "2026-02")
        assert february_receivable["rent"] == february_plan["rent"]
        assert february_receivable["total"] == february_plan["rent"]


def test_annual_percentage_escalation_compounds_from_effective_date() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-ESC-ANNUAL", "RENTTERM-ESC-ANNUAL", start_date="2026-01-01", end_date="2028-12-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        base = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2028-12-31", "monthly_amount": 30000},
        )
        assert base.status_code == 200
        escalation = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "escalation",
                "start_date": "2027-01-01",
                "end_date": "2028-12-31",
                "adjustment_type": "percent",
                "adjustment_value": 10,
                "frequency": "annual",
            },
        )
        assert escalation.status_code == 200

        first_year, summary = _rent_plan_row(client, contract_id, "2026-01")
        second_year, _summary = _rent_plan_row(client, contract_id, "2027-01")
        third_year, _summary = _rent_plan_row(client, contract_id, "2028-01")
        assert summary["rent_source"] == "rent_terms"
        assert first_year["rent"] == 30000
        assert second_year["rent"] == 33000
        assert third_year["rent"] == 36300

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-ESC-ANNUAL", "2028-01")
        assert monthly_row["rent"] == third_year["rent"]


def test_fixed_amount_escalation_from_specific_date_splits_month() -> None:
    expected = 29400

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-ESC-DATE", "RENTTERM-ESC-DATE", start_date="2026-02-01", end_date="2026-02-28"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        base = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-02-01", "end_date": "2026-02-28", "monthly_amount": 28000},
        )
        assert base.status_code == 200
        escalation = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "escalation",
                "start_date": "2026-02-15",
                "end_date": "2026-02-28",
                "adjustment_type": "fixed_amount",
                "adjustment_value": 2800,
                "frequency": "once",
            },
        )
        assert escalation.status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-02")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["rent"] == expected
        assert plan_row["gross_rent"] == expected

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-ESC-DATE", "2026-02")
        assert monthly_row["rent"] == plan_row["rent"]
        assert monthly_row["total"] == plan_row["rent"]


def test_rent_free_deducts_escalated_daily_rent_overlap() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-FREE-ESC", "RENTTERM-FREE-ESC", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "escalation", "start_date": "2026-01-16", "end_date": "2026-01-31", "adjustment_type": "percent", "adjustment_value": 10, "frequency": "once"},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "rent_free", "start_date": "2026-01-20", "end_date": "2026-01-24"},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 32600
        assert plan_row["rent_free_amount"] == 5500
        assert plan_row["rent"] == 27100

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-FREE-ESC", "2026-01")
        assert monthly_row["gross_rent"] == plan_row["gross_rent"]
        assert monthly_row["rent_free_amount"] == plan_row["rent_free_amount"]
        assert monthly_row["rent"] == plan_row["rent"]


def test_room_scoped_stepped_rent_and_escalation_only_apply_to_room() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RENTTERM-ROOM-ESC",
                "RENTTERM-ROOM-ESC-A、RENTTERM-ROOM-ESC-B",
                start_date="2026-01-01",
                end_date="2026-01-31",
                room_items=[
                    {"tower": "南塔", "room_no": "RENTTERM-ROOM-ESC-A", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                    {"tower": "南塔", "room_no": "RENTTERM-ROOM-ESC-B", "area": 80, "unit_price": 5, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                ],
            ),
        )
        assert created.status_code == 200
        contract = created.json()
        contract_id = contract["id"]
        room_a_id = next(item["id"] for item in contract["room_items"] if item["room_no"] == "RENTTERM-ROOM-ESC-A")
        room_b_id = next(item["id"] for item in contract["room_items"] if item["room_no"] == "RENTTERM-ROOM-ESC-B")

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_a_id, "rule_type": "stepped_rent", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_b_id, "rule_type": "stepped_rent", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 28000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_a_id, "rule_type": "escalation", "start_date": "2026-01-16", "end_date": "2026-01-31", "adjustment_type": "percent", "adjustment_value": 10, "frequency": "once"},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["rent"] == 60600

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-ROOM-ESC", "2026-01")
        assert monthly_row["rent"] == plan_row["rent"]
        assert monthly_row["total"] == plan_row["rent"]


def test_temporary_adjustment_fixed_amount_prorates_by_natural_days_and_matches_receivables() -> None:
    expected_january_adjustment = 1600
    expected_february_adjustment = round(3100 * 15 / 28, 2)

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-TEMP-CROSS", "RENTTERM-TEMP-CROSS", start_date="2026-01-01", end_date="2026-02-28"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-02-28", "monthly_amount": 31000},
        ).status_code == 200
        adjustment = client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "temporary_adjustment",
                "start_date": "2026-01-16",
                "end_date": "2026-02-15",
                "adjustment_type": "fixed_amount",
                "adjustment_value": 3100,
            },
        )
        assert adjustment.status_code == 200

        january_plan, summary = _rent_plan_row(client, contract_id, "2026-01")
        february_plan, _summary = _rent_plan_row(client, contract_id, "2026-02")
        assert summary["rent_source"] == "rent_terms"
        assert january_plan["gross_rent"] == 31000
        assert january_plan["temporary_adjustment_amount"] == expected_january_adjustment
        assert january_plan["rent"] == 29400
        assert february_plan["temporary_adjustment_amount"] == expected_february_adjustment
        assert february_plan["rent"] == round(31000 - expected_february_adjustment, 2)

        january_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-CROSS", "2026-01")
        february_receivable = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-CROSS", "2026-02")
        assert january_receivable["temporary_adjustment_amount"] == january_plan["temporary_adjustment_amount"]
        assert february_receivable["temporary_adjustment_amount"] == february_plan["temporary_adjustment_amount"]
        assert january_receivable["rent"] == january_plan["rent"]
        assert february_receivable["rent"] == february_plan["rent"]


def test_temporary_adjustment_never_makes_monthly_rent_negative() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-TEMP-ZERO", "RENTTERM-TEMP-ZERO", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 1000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "temporary_adjustment", "start_date": "2026-01-01", "end_date": "2026-01-31", "adjustment_type": "fixed_amount", "adjustment_value": 5000},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 1000
        assert plan_row["temporary_adjustment_amount"] == 1000
        assert plan_row["rent"] == 0
        assert plan_row["temporary_adjustment_attention"] is True

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-ZERO", "2026-01")
        assert monthly_row["temporary_adjustment_amount"] == plan_row["temporary_adjustment_amount"]
        assert monthly_row["rent"] == 0
        assert monthly_row["total"] == 0


def test_temporary_adjustment_stacks_with_rent_free() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-TEMP-FREE", "RENTTERM-TEMP-FREE", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "rent_free", "start_date": "2026-01-01", "end_date": "2026-01-05"},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "temporary_adjustment", "start_date": "2026-01-10", "end_date": "2026-01-20", "adjustment_type": "fixed_amount", "adjustment_value": 3100},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 31000
        assert plan_row["rent_free_amount"] == 5000
        assert plan_row["temporary_adjustment_amount"] == 1100
        assert plan_row["rent"] == 24900

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-FREE", "2026-01")
        assert monthly_row["rent_free_amount"] == plan_row["rent_free_amount"]
        assert monthly_row["temporary_adjustment_amount"] == plan_row["temporary_adjustment_amount"]
        assert monthly_row["rent"] == plan_row["rent"]


def test_rent_plan_preview_explains_composite_rent_terms() -> None:
    anchor = today_in_timezone().replace(day=1)
    end_month = anchor + relativedelta(months=2)
    end_date = end_month.replace(day=calendar.monthrange(end_month.year, end_month.month)[1])

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RENTTERM-PREVIEW",
                "RENTTERM-PREVIEW",
                start_date=anchor.isoformat(),
                end_date=end_date.isoformat(),
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": anchor.isoformat(), "end_date": end_date.isoformat(), "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "rent_free", "start_date": anchor.isoformat(), "end_date": anchor.replace(day=3).isoformat()},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={
                "rule_type": "temporary_adjustment",
                "start_date": anchor.replace(day=10).isoformat(),
                "end_date": anchor.replace(day=20).isoformat(),
                "adjustment_type": "fixed_amount",
                "adjustment_value": 3100,
            },
        ).status_code == 200

        response = client.get(f"/api/contracts/{contract_id}/rent-plan")
        assert response.status_code == 200
        data = response.json()
        preview = data["preview"]
        assert len(preview) == 3
        assert preview[0]["month"] == anchor.strftime("%Y-%m")
        assert data["summary"]["preview_months"] == 3

        first = preview[0]
        effect_types = [item["type"] for item in first["term_effects"]]
        assert effect_types == ["base", "rent_free", "temporary_adjustment"]
        assert "固定月租" in first["term_summary"]
        assert "自然日免租" in first["term_summary"]
        assert "临时减免" in first["term_summary"]
        assert first["term_effects"][0]["amount"] == first["gross_rent"]
        assert first["term_effects"][1]["amount"] == -first["rent_free_amount"]
        assert first["term_effects"][2]["amount"] == -first["temporary_adjustment_amount"]

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-PREVIEW", anchor.strftime("%Y-%m"))
        assert monthly_row["term_summary"] == first["term_summary"]
        assert monthly_row["term_effects"] == first["term_effects"]
        assert monthly_row["rent"] == first["rent"]


def test_temporary_adjustment_stacks_with_stepped_rent() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-TEMP-STEP", "RENTTERM-TEMP-STEP", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "stepped_rent", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "temporary_adjustment", "start_date": "2026-01-10", "end_date": "2026-01-20", "adjustment_type": "fixed_amount", "adjustment_value": 3100},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 31000
        assert plan_row["temporary_adjustment_amount"] == 1100
        assert plan_row["rent"] == 29900

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-STEP", "2026-01")
        assert monthly_row["temporary_adjustment_amount"] == plan_row["temporary_adjustment_amount"]
        assert monthly_row["rent"] == plan_row["rent"]


def test_temporary_adjustment_stacks_with_escalation() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-RENTTERM-TEMP-ESC", "RENTTERM-TEMP-ESC", start_date="2026-01-01", end_date="2026-01-31"),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "escalation", "start_date": "2026-01-16", "end_date": "2026-01-31", "adjustment_type": "percent", "adjustment_value": 10, "frequency": "once"},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"rule_type": "temporary_adjustment", "start_date": "2026-01-16", "end_date": "2026-01-31", "adjustment_type": "fixed_amount", "adjustment_value": 3100},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 32600
        assert plan_row["temporary_adjustment_amount"] == 1600
        assert plan_row["rent"] == 31000

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-ESC", "2026-01")
        assert monthly_row["gross_rent"] == plan_row["gross_rent"]
        assert monthly_row["temporary_adjustment_amount"] == plan_row["temporary_adjustment_amount"]
        assert monthly_row["rent"] == plan_row["rent"]


def test_room_scoped_temporary_adjustment_only_applies_to_matching_room() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RENTTERM-TEMP-ROOM",
                "RENTTERM-TEMP-ROOM-A、RENTTERM-TEMP-ROOM-B",
                start_date="2026-01-01",
                end_date="2026-01-31",
                room_items=[
                    {"tower": "南塔", "room_no": "RENTTERM-TEMP-ROOM-A", "area": 100, "unit_price": 4, "monthly_rent": 31000, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                    {"tower": "南塔", "room_no": "RENTTERM-TEMP-ROOM-B", "area": 80, "unit_price": 5, "monthly_rent": 28000, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                ],
            ),
        )
        assert created.status_code == 200
        contract = created.json()
        contract_id = contract["id"]
        room_a_id = next(item["id"] for item in contract["room_items"] if item["room_no"] == "RENTTERM-TEMP-ROOM-A")
        room_b_id = next(item["id"] for item in contract["room_items"] if item["room_no"] == "RENTTERM-TEMP-ROOM-B")

        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_a_id, "rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 31000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_b_id, "rule_type": "fixed_monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "monthly_amount": 28000},
        ).status_code == 200
        assert client.post(
            f"/api/contracts/{contract_id}/rent-terms",
            json={"contract_room_id": room_a_id, "rule_type": "temporary_adjustment", "start_date": "2026-01-01", "end_date": "2026-01-31", "adjustment_type": "fixed_amount", "adjustment_value": 3100},
        ).status_code == 200

        plan_row, summary = _rent_plan_row(client, contract_id, "2026-01")
        assert summary["rent_source"] == "rent_terms"
        assert plan_row["gross_rent"] == 59000
        assert plan_row["temporary_adjustment_amount"] == 3100
        assert plan_row["rent"] == 55900

        monthly_row = _monthly_receivable_row(client, "DEMO_A-RENTTERM-TEMP-ROOM", "2026-01")
        assert monthly_row["temporary_adjustment_amount"] == plan_row["temporary_adjustment_amount"]
        assert monthly_row["rent"] == plan_row["rent"]
        assert monthly_row["total"] == plan_row["rent"]


def test_expand_room_action_adds_contract_room_and_log() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")
        assert client.post("/api/rooms", json=_room_payload("ACTION-EXPAND-BASE")).status_code == 200
        assert client.post("/api/rooms", json=_room_payload("ACTION-EXPAND-NEW", area=80)).status_code == 200

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-EXPAND",
                "ACTION-EXPAND-BASE",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-EXPAND-BASE", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-06-30"}
                ],
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        expanded = client.post(
            f"/api/contracts/{contract_id}/rooms/expand",
            json={"tower": "南塔", "room_no": "ACTION-EXPAND-NEW", "area": 80, "unit_price": 5, "start_date": "2026-07-01", "end_date": "2026-12-31"},
        )
        assert expanded.status_code == 200
        rooms = sorted(expanded.json()["room_items"], key=lambda item: item["room_no"])
        assert [item["room_no"] for item in rooms] == ["ACTION-EXPAND-BASE", "ACTION-EXPAND-NEW"]
        assert next(item for item in rooms if item["room_no"] == "ACTION-EXPAND-NEW")["start_date"] == "2026-07-01"

        logs = client.get(f"/api/contracts/{contract_id}/logs")
        assert logs.status_code == 200
        assert any(log["action"] == "room_expand" for log in logs.json())


def test_reduce_room_action_truncates_contract_room_end_date() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-REDUCE",
                "ACTION-REDUCE",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-REDUCE", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        room_item_id = created.json()["room_items"][0]["id"]

        reduced = client.post(f"/api/contracts/{contract_id}/rooms/{room_item_id}/reduce", json={"end_date": "2026-05-31"})
        assert reduced.status_code == 200
        room_item = reduced.json()["room_items"][0]
        assert room_item["room_no"] == "ACTION-REDUCE"
        assert room_item["end_date"] == "2026-05-31"
        assert reduced.json()["end_date"] == "2026-12-31"


def test_swap_room_action_truncates_old_room_and_adds_new_room() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")
        assert client.post("/api/rooms", json=_room_payload("ACTION-SWAP-OLD")).status_code == 200
        assert client.post("/api/rooms", json=_room_payload("ACTION-SWAP-NEW", area=90)).status_code == 200

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-SWAP",
                "ACTION-SWAP-OLD",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-SWAP-OLD", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        room_item_id = created.json()["room_items"][0]["id"]

        swapped = client.post(
            f"/api/contracts/{contract_id}/rooms/{room_item_id}/swap",
            json={
                "end_date": "2026-06-30",
                "new_room": {"tower": "南塔", "room_no": "ACTION-SWAP-NEW", "area": 90, "unit_price": 6, "start_date": "2026-07-01", "end_date": "2026-12-31"},
            },
        )
        assert swapped.status_code == 200
        rooms = {item["room_no"]: item for item in swapped.json()["room_items"]}
        assert rooms["ACTION-SWAP-OLD"]["end_date"] == "2026-06-30"
        assert rooms["ACTION-SWAP-NEW"]["start_date"] == "2026-07-01"


def test_room_lifecycle_action_rejects_overlapping_occupancy() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        occupied = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-OCCUPIED",
                "ACTION-CONFLICT",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-CONFLICT", "area": 100, "unit_price": 4, "start_date": "2026-07-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert occupied.status_code == 200
        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-OVERLAP",
                "ACTION-OVERLAP-BASE",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-OVERLAP-BASE", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert created.status_code == 200

        rejected = client.post(
            f"/api/contracts/{created.json()['id']}/rooms/expand",
            json={"tower": "南塔", "room_no": "ACTION-CONFLICT", "area": 100, "unit_price": 5, "start_date": "2026-08-01", "end_date": "2026-08-31"},
        )
        assert rejected.status_code == 400


def test_room_lifecycle_actions_update_room_status_and_history() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")
        assert client.post("/api/rooms", json=_room_payload("ACTION-HISTORY-OLD")).status_code == 200
        assert client.post("/api/rooms", json=_room_payload("ACTION-HISTORY-NEW", area=120)).status_code == 200

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-ACTION-HISTORY",
                "ACTION-HISTORY-OLD",
                room_items=[
                    {"tower": "南塔", "room_no": "ACTION-HISTORY-OLD", "area": 100, "unit_price": 4, "start_date": "2026-01-01", "end_date": "2026-12-31"}
                ],
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        room_item_id = created.json()["room_items"][0]["id"]

        swapped = client.post(
            f"/api/contracts/{contract_id}/rooms/{room_item_id}/swap",
            json={
                "end_date": "2026-06-30",
                "new_room": {"tower": "南塔", "room_no": "ACTION-HISTORY-NEW", "area": 120, "unit_price": 5, "start_date": "2026-07-01", "end_date": "2026-12-31"},
            },
        )
        assert swapped.status_code == 200

        august_rooms = client.get("/api/rooms", params={"start_date": "2026-08-01", "end_date": "2026-08-31"})
        assert august_rooms.status_code == 200
        by_room = {item["room_no"]: item for item in august_rooms.json()}
        assert by_room["ACTION-HISTORY-OLD"]["occupied"] is False
        assert by_room["ACTION-HISTORY-NEW"]["occupied"] is True
        assert by_room["ACTION-HISTORY-NEW"]["contract"]["contract_no"] == "DEMO_A-ACTION-HISTORY"

        history = client.get("/api/rooms/ACTION-HISTORY-NEW/contracts")
        assert history.status_code == 200
        row = history.json()["contracts"][0]
        assert row["contract_no"] == "DEMO_A-ACTION-HISTORY"
        assert row["start_date"] == "2026-07-01"
        assert row["end_date"] == "2026-12-31"


def test_dashboard_stats_exclude_retired_contracts_from_active_metrics() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        for contract_no, room_no, disposal in [
            ("DEMO_A-STATS-ACTIVE", "S9905", ""),
            ("DEMO_A-STATS-RETIRED", "S9906", "已退租"),
        ]:
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": contract_no,
                    "tower": "南塔",
                    "room_no": room_no,
                    "tenant_name": "统计口径客户",
                    "area": 100,
                    "unit_price": 4,
                    "monthly_rent": 12000,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "disposal": disposal,
                },
            )
            assert response.status_code == 200

        response = client.get("/api/contracts/stats")
        assert response.status_code == 200
        metrics = response.json()["metrics"]
        assert metrics["total_area"] == 100
        assert metrics["total_monthly_rent"] == _expected_monthly_rent()


def test_dashboard_stats_include_expiry_alerts_within_180_days() -> None:
    today = today_in_timezone()
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        within_180 = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-EXPIRY-180",
                "EXP180",
                start_date=(today - timedelta(days=30)).isoformat(),
                end_date=(today + timedelta(days=150)).isoformat(),
            ),
        )
        assert within_180.status_code == 200

        outside_180 = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-EXPIRY-181",
                "EXP181",
                start_date=(today - timedelta(days=30)).isoformat(),
                end_date=(today + timedelta(days=181)).isoformat(),
            ),
        )
        assert outside_180.status_code == 200

        response = client.get("/api/contracts/stats")
        assert response.status_code == 200
        rows = response.json()["expiring_soon"]
        assert any(row["contract_no"] == "DEMO_A-EXPIRY-180" and row["days_left"] == 150 for row in rows)
        assert all(row["contract_no"] != "DEMO_A-EXPIRY-181" for row in rows)


def test_batch_delete_hides_all_selected_contracts() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        ids = []
        for index in range(2):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"DEMO_A-BATCH-DELETE-{index}",
                    "tower": "南塔",
                    "room_no": f"SBD{index}",
                    "tenant_name": f"批量删除客户{index}",
                    "area": 100,
                    "unit_price": 4,
                    "monthly_rent": 12000,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
            )
            assert response.status_code == 200
            ids.append(response.json()["id"])

        response = client.post("/api/contracts/batch-delete", json=ids)
        assert response.status_code == 200
        assert response.json()["deleted"] == 2

        response = client.get("/api/contracts?keyword=DEMO_A-BATCH-DELETE&page=1&page_size=20")
        assert response.status_code == 200
        assert response.json()["total"] == 0


def test_batch_delete_rolls_back_when_logging_fails(monkeypatch) -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        ids = []
        for index in range(2):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"DEMO_A-BATCH-ROLLBACK-{index}",
                    "tower": "南塔",
                    "room_no": f"SBR{index}",
                    "tenant_name": f"批量回滚客户{index}",
                    "area": 100,
                    "unit_price": 4,
                    "monthly_rent": 12000,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
            )
            assert response.status_code == 200
            ids.append(response.json()["id"])

        def fail_log_action(*_args, **_kwargs):
            raise RuntimeError("forced log failure")

        from app.services import contracts as contracts_service

        monkeypatch.setattr(contracts_service, "log_action", fail_log_action)
        failing_client = TestClient(app, raise_server_exceptions=False)
        try:
            response = failing_client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
            assert response.status_code == 200
            response = failing_client.post("/api/contracts/batch-delete", json=ids)
            assert response.status_code == 500
        finally:
            failing_client.close()

        rows = client.get("/api/contracts?keyword=DEMO_A-BATCH-ROLLBACK&page=1&page_size=20").json()
        assert rows["total"] == 2

        from app.database import SessionLocal
        from app.models import Contract

        db = SessionLocal()
        try:
            stored = db.query(Contract).filter(Contract.contract_no.like("DEMO_A-BATCH-ROLLBACK-%")).all()
            assert len(stored) == 2
            assert all(not item.is_deleted for item in stored)
        finally:
            db.close()


def test_contract_import_rejects_invalid_file_without_partial_write() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        content = _workbook_bytes(
            ["合同编号", "塔楼", "室号", "客户", "面积", "月租金", "起租日", "终止日"],
            [
                ["DEMO_A-IMPORT-OK", "南塔", "S8801", "导入客户", 100, 10000, "2026-01-01", "2026-12-31"],
                ["DEMO_A-IMPORT-BAD", "南塔", "S8802", "错误客户", "bad-area", 10000, "2026-01-01", "2026-12-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rows = client.get("/api/contracts?keyword=DEMO_A-IMPORT&page=1&page_size=20").json()
        assert rows["total"] == 0


def test_contract_import_rejects_overlapping_rows_without_partial_write() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        content = _workbook_bytes(
            ["合同编号", "塔楼", "室号", "客户", "面积", "月租金", "起租日", "终止日"],
            [
                ["DEMO_A-IMPORT-OVERLAP-001", "南塔", "S8803", "导入客户一", 100, 10000, "2026-01-01", "2026-12-31"],
                ["DEMO_A-IMPORT-OVERLAP-002", "南塔", "S8803", "导入客户二", 100, 10000, "2026-06-01", "2027-05-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rows = client.get("/api/contracts?keyword=DEMO_A-IMPORT-OVERLAP&page=1&page_size=20").json()
        assert rows["total"] == 0


def test_contract_template_explains_room_inventory_import() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.get("/api/contracts/export/template")
        assert response.status_code == 200

        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        assert "导入模板" in workbook.sheetnames
        assert "导入说明" in workbook.sheetnames
        sheet = workbook["导入模板"]
        assert sheet["C3"].value == "IMP1801、IMPC101"
        assert "多房源写在同一格" in sheet["S3"].value
        assert "面积以房源清单为准" in sheet["E1"].comment.text
        assert "继承合同" in sheet["M1"].comment.text

        notes = list(workbook["导入说明"].iter_rows(values_only=True))
        assert any("多个室号可写在同一个单元格" in str(row[1]) for row in notes)
        assert any("房源起止日可留空继承合同期间" in str(row[1]) for row in notes)


def test_contract_import_reports_skipped_rows() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        existing = client.post("/api/contracts", json=_contract_payload("DEMO_A-IMPORT-SKIP-OLD", "SKIPOLD01"))
        assert existing.status_code == 200

        content = _workbook_bytes(
            ["合同编号", "楼塔", "室号", "客户", "面积", "租金单价", "起租日", "终止日"],
            [
                ["DEMO_A-IMPORT-SKIP-OLD", "南塔", "SKIPOLD01", "已存在客户", 100, 4, "2027-01-01", "2027-12-31"],
                ["DEMO_A-IMPORT-SKIP-NEW", "南塔", "SKIPNEW01", "新增客户", 120, 5, "2027-01-01", "2027-12-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["imported"] == 1
        assert result["skipped"] == 1
        assert result["skipped_rows"] == [
            {"row": 2, "contract_no": "DEMO_A-IMPORT-SKIP-OLD", "reason": "合同编号已存在，跳过"}
        ]
        assert result["errors"] == ["第2行 合同 DEMO_A-IMPORT-SKIP-OLD: 合同编号已存在，跳过"]


def test_contract_import_uses_room_inventory_for_contract_rooms() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        first_room = client.post("/api/rooms", json={"tower": "北塔", "floor": "18", "room_no": "IMP1801", "room_type": "办公", "area": 120})
        second_room = client.post("/api/rooms", json={"tower": "商业配套", "floor": "1", "room_no": "IMPC101", "room_type": "商铺", "area": 80})
        assert first_room.status_code == 200
        assert second_room.status_code == 200

        content = _workbook_bytes(
            ["合同编号", "楼塔", "室号", "客户", "面积", "租金单价", "起租日", "终止日"],
            [
                ["DEMO_A-IMPORT-ROOMBASE", "南塔", "IMP1801、IMPC101", "房源底座导入客户", 1, 5, "2027-01-01", "2027-12-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        assert response.json()["imported"] == 1

        rows = client.get("/api/contracts?keyword=DEMO_A-IMPORT-ROOMBASE&page=1&page_size=20").json()
        assert rows["total"] == 1
        contract = rows["items"][0]
        assert contract["tower"] == "多房源"
        assert contract["room_no"] == "IMP1801、IMPC101"
        assert contract["area"] == 200
        assert contract["monthly_rent"] == _expected_monthly_rent(area=200, unit_price=5)

        db = SessionLocal()
        try:
            stored = db.query(Contract).filter(Contract.contract_no == "DEMO_A-IMPORT-ROOMBASE").one()
            items = db.query(ContractRoom).filter(ContractRoom.contract_id == stored.id).order_by(ContractRoom.room_no).all()
            assert [(item.room_no, item.tower, item.area, item.room_id is not None) for item in items] == [
                ("IMP1801", "北塔", 120, True),
                ("IMPC101", "商业配套", 80, True),
            ]
        finally:
            db.close()


def test_contract_import_reports_room_conflict_row() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        room = client.post("/api/rooms", json={"tower": "南塔", "floor": "19", "room_no": "IMPCONF1901", "room_type": "办公", "area": 100})
        assert room.status_code == 200
        existing = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-IMPORT-CONFLICT-OLD",
                "IMPCONF1901",
                tenant_name="既有客户",
                start_date="2027-01-01",
                end_date="2027-12-31",
            ),
        )
        assert existing.status_code == 200

        content = _workbook_bytes(
            ["合同编号", "楼塔", "室号", "客户", "面积", "租金单价", "起租日", "终止日"],
            [
                ["DEMO_A-IMPORT-CONFLICT-NEW", "南塔", "IMPCONF1901", "冲突导入客户", 1, 6, "2027-06-01", "2027-06-30"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["message"] == "Excel 存在无法导入的数据，未写入任何新合同"
        assert detail["fatal_errors"] == [
            {
                "row": 2,
                "contract_no": "DEMO_A-IMPORT-CONFLICT-NEW",
                "reason": "室号 IMPCONF1901 在该租期内已关联合同 DEMO_A-IMPORT-CONFLICT-OLD",
            }
        ]
        assert detail["errors"] == [
            "第2行 合同 DEMO_A-IMPORT-CONFLICT-NEW: 室号 IMPCONF1901 在该租期内已关联合同 DEMO_A-IMPORT-CONFLICT-OLD"
        ]

        rows = client.get("/api/contracts?keyword=DEMO_A-IMPORT-CONFLICT-NEW&page=1&page_size=20").json()
        assert rows["total"] == 0


def test_pdf_download_rejects_unsafe_stored_path() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-PDF-PATH",
                "tower": "南塔",
                "room_no": "S9903",
                "tenant_name": "路径校验客户",
                "area": 100,
                "unit_price": 4,
                "monthly_rent": 12000,
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 200
        contract_id = response.json()["id"]

        from app.database import SessionLocal
        from app.models import Contract

        db = SessionLocal()
        try:
            contract = db.query(Contract).filter(Contract.id == contract_id).first()
            contract.pdf_path = "../escape.pdf"
            db.commit()
        finally:
            db.close()

        response = client.get(f"/api/contracts/{contract_id}/pdf")
        assert response.status_code == 400


def test_pdf_upload_rejects_oversized_file(monkeypatch) -> None:
    from app.services import contracts as contracts_service

    monkeypatch.setattr(contracts_service, "MAX_PDF_FILE_SIZE", 8)
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-PDF-SIZE",
                "tower": "南塔",
                "room_no": "S9904",
                "tenant_name": "大小校验客户",
                "area": 100,
                "unit_price": 4,
                "monthly_rent": 12000,
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 200
        contract_id = response.json()["id"]

        response = client.post(
            f"/api/contracts/{contract_id}/upload-pdf",
            files={"file": ("contract.pdf", b"123456789", "application/pdf")},
        )
        assert response.status_code == 413


def test_missing_pdf_endpoint_returns_all_matching_contracts() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        for index in range(105):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"DEMO_A-MISSING-PDF-{index:03d}",
                    "tower": "南塔",
                    "room_no": f"SMP{index:03d}",
                    "tenant_name": f"缺失PDF客户{index}",
                    "area": 100,
                    "unit_price": 4,
                    "monthly_rent": 12000,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
            )
            assert response.status_code == 200

        response = client.get("/api/contracts/missing-pdfs")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 105


def test_monthly_receivables_tracks_rent_and_lists_property_fee_separately() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RECEIVABLE-001",
                "REC01",
                area=100,
                monthly_rent=12000,
                property_fee_rate=28,
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert response.status_code == 200

        response = client.get("/api/contracts/receivables/monthly?month=2026-04")
        assert response.status_code == 200
        data = response.json()
        expected_rent = _expected_monthly_rent()
        assert data["total_rent"] == expected_rent
        assert data["total_property"] == 2800
        assert data["total"] == expected_rent
        assert data["rows"][0]["lease_days"] == 30
        assert data["rows"][0]["month_days"] == 30
        assert data["rows"][0]["property_fee"] == 2800
        assert data["rows"][0]["total"] == expected_rent


def test_monthly_receivables_include_linked_customer_id_when_available() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        customer = client.post("/api/customers", json={"name": "Receivable Linked Customer"})
        assert customer.status_code == 200
        customer_id = customer.json()["id"]

        linked = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RECEIVABLE-CUSTOMER-001",
                "RECCUST01",
                customer_id=customer_id,
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert linked.status_code == 200
        legacy = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RECEIVABLE-CUSTOMER-002",
                "RECCUST02",
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert legacy.status_code == 200

        response = client.get("/api/contracts/receivables/monthly?month=2026-04")
        assert response.status_code == 200
        rows = {row["contract_no"]: row for row in response.json()["rows"]}
        assert rows["DEMO_A-RECEIVABLE-CUSTOMER-001"]["customer_id"] == customer_id
        assert rows["DEMO_A-RECEIVABLE-CUSTOMER-002"]["customer_id"] is None


def test_contract_can_cover_multiple_room_items() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        for room_no, area in [("MR01", 100), ("MR02", 80)]:
            response = client.post("/api/rooms", json={"tower": "南塔", "floor": "12", "room_no": room_no, "room_type": "办公", "area": area})
            assert response.status_code == 200

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-MULTI-ROOM",
                "tenant_name": "多房源客户",
                "lease_type": "新签",
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "room_items": [
                    {"tower": "南塔", "room_no": "MR01", "area": 100, "unit_price": 4},
                    {"tower": "南塔", "room_no": "MR02", "area": 80, "unit_price": 5},
                ],
            },
        )
        assert response.status_code == 200
        contract = response.json()
        assert contract["room_no"] == "MR01、MR02"
        assert contract["area"] == 180
        assert contract["monthly_rent"] == _expected_monthly_rent(area=100, unit_price=4) + _expected_monthly_rent(area=80, unit_price=5)
        assert [item["room_no"] for item in contract["room_items"]] == ["MR01", "MR02"]

        rooms = client.get("/api/rooms").json()
        occupied = {row["room_no"] for row in rooms if row["occupied"]}
        assert {"MR01", "MR02"}.issubset(occupied)

        exported = client.get("/api/contracts/export/excel?keyword=DEMO_A-MULTI-ROOM")
        assert exported.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(exported.content))
        assert "房源明细" in workbook.sheetnames
        detail_sheet = workbook["房源明细"]
        detail_rows = list(detail_sheet.iter_rows(min_row=2, values_only=True))
        assert [row[3] for row in detail_rows] == ["MR01", "MR02"]
        assert [row[4] for row in detail_rows] == [100, 80]

        overlap = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-MULTI-OVERLAP",
                "tenant_name": "重叠客户",
                "start_date": "2026-06-01",
                "end_date": "2026-12-31",
                "room_items": [{"tower": "南塔", "room_no": "MR02", "area": 80, "unit_price": 5}],
            },
        )
        assert overlap.status_code == 400


def test_rooms_can_be_checked_by_future_lease_window() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post("/api/rooms", json={"tower": "南塔", "floor": "18", "room_no": "AV1801", "room_type": "办公", "area": 120})
        assert response.status_code == 200

        response = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-AVAILABILITY-001",
                "AV1801",
                tenant_name="未来占用客户",
                area=120,
                start_date="2027-01-01",
                end_date="2027-12-31",
            ),
        )
        assert response.status_code == 200
        contract_id = response.json()["id"]

        current_rooms = client.get("/api/rooms")
        assert current_rooms.status_code == 200
        current_room = next(row for row in current_rooms.json() if row["room_no"] == "AV1801")
        assert current_room["occupied"] is False
        assert current_room["availability_mode"] == "current"

        future_rooms = client.get("/api/rooms", params={"start_date": "2027-06-01", "end_date": "2027-06-30"})
        assert future_rooms.status_code == 200
        future_room = next(row for row in future_rooms.json() if row["room_no"] == "AV1801")
        assert future_room["occupied"] is True
        assert future_room["availability_mode"] == "range"
        assert future_room["contract"]["contract_no"] == "DEMO_A-AVAILABILITY-001"
        assert future_room["contract"]["availability_status"] == "已占用"

        edit_rooms = client.get(
            "/api/rooms",
            params={
                "start_date": "2027-06-01",
                "end_date": "2027-06-30",
                "exclude_contract_id": contract_id,
            },
        )
        assert edit_rooms.status_code == 200
        edit_room = next(row for row in edit_rooms.json() if row["room_no"] == "AV1801")
        assert edit_room["occupied"] is False
        assert edit_room["availability_mode"] == "range"

        stats = client.get("/api/rooms/stats", params={"start_date": "2027-06-01", "end_date": "2027-06-30"})
        assert stats.status_code == 200
        assert stats.json()["availability_mode"] == "range"
        assert stats.json()["occupied_rooms"] == 1
        assert stats.json()["vacancy_rooms"] == 0

        invalid = client.get("/api/rooms", params={"start_date": "2027-06-30", "end_date": "2027-06-01"})
        assert invalid.status_code == 400


def test_room_contract_history_lists_past_current_future_and_range_conflicts() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post("/api/rooms", json={"tower": "南塔", "floor": "20", "room_no": "RH2001", "room_type": "办公", "area": 100})
        assert response.status_code == 200

        for contract_no, tenant_name, start_date, end_date, unit_price in [
            ("DEMO_A-ROOM-HISTORY-PAST", "历史客户", "2025-01-01", "2025-12-31", 3),
            ("DEMO_A-ROOM-HISTORY-CURRENT", "当前客户", "2026-01-01", "2026-12-31", 4),
            ("DEMO_A-ROOM-HISTORY-FUTURE", "未来客户", "2027-01-01", "2027-12-31", 5),
        ]:
            created = client.post(
                "/api/contracts",
                json=_contract_payload(
                    contract_no,
                    "RH2001",
                    tenant_name=tenant_name,
                    area=100,
                    unit_price=unit_price,
                    start_date=start_date,
                    end_date=end_date,
                ),
            )
            assert created.status_code == 200

        db = SessionLocal()
        try:
            demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
            legacy = Contract(
                contract_no="DEMO_A-ROOM-HISTORY-LEGACY",
                building_id=demo_a.id,
                tower="南塔",
                room_no="RH2001",
                tenant_name="旧字段客户",
                area=100,
                unit_price=6,
                monthly_rent=_expected_monthly_rent(area=100, unit_price=6),
                start_date=date(2028, 1, 1),
                end_date=date(2028, 12, 31),
            )
            db.add(legacy)
            db.commit()
        finally:
            db.close()

        response = client.get(
            "/api/rooms/RH2001/contracts",
            params={"start_date": "2027-06-01", "end_date": "2027-06-30"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["room"]["room_no"] == "RH2001"
        assert data["current_contract"]["contract_no"] == "DEMO_A-ROOM-HISTORY-CURRENT"
        assert data["next_contract"]["contract_no"] == "DEMO_A-ROOM-HISTORY-FUTURE"
        assert [row["contract_no"] for row in data["contracts"]] == [
            "DEMO_A-ROOM-HISTORY-PAST",
            "DEMO_A-ROOM-HISTORY-CURRENT",
            "DEMO_A-ROOM-HISTORY-FUTURE",
            "DEMO_A-ROOM-HISTORY-LEGACY",
        ]
        assert [row["relation"] for row in data["contracts"]] == ["history", "current", "future", "future"]
        assert [row["contract_no"] for row in data["range_conflicts"]] == ["DEMO_A-ROOM-HISTORY-FUTURE"]
        assert data["range_conflicts"][0]["range_overlap"] is True


def test_receivable_record_tracks_collection_balance_invoice_and_dunning() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-RECEIPT-001",
                "RECEIPT01",
                area=100,
                unit_price=4,
                property_fee_rate=28,
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        report = client.get("/api/contracts/receivables/monthly?month=2026-04").json()
        receivable_total = report["rows"][0]["total"]
        assert receivable_total == _expected_monthly_rent()
        assert report["rows"][0]["property_fee"] == 2800
        assert report["rows"][0]["dunning_owner"] == "李四"
        response = client.post(
            f"/api/contracts/receivables/monthly/{contract_id}",
            json={
                "month": "2026-04",
                "received_amount": 10000,
                "received_date": "2026-04-15",
                "invoice_status": "已开票",
                "dunning_status": "催收中",
                "dunning_owner": "赵六",
                "notes": "首笔到账",
            },
        )
        assert response.status_code == 200
        data = response.json()
        row = data["rows"][0]
        assert data["total_received"] == 10000
        assert data["total_balance"] == round(receivable_total - 10000, 2)
        assert data["total_unpaid_balance"] == round(receivable_total - 10000, 2)
        assert data["total_overdue_balance"] == round(receivable_total - 10000, 2)
        assert data["total_not_due_balance"] == 0
        assert data["unpaid_count"] == 1
        assert data["overdue_count"] == 1
        assert row["balance"] == round(receivable_total - 10000, 2)
        assert row["unpaid_balance"] == round(receivable_total - 10000, 2)
        assert row["overdue_balance"] == round(receivable_total - 10000, 2)
        assert row["not_due_balance"] == 0
        assert row["due_date"] == "2026-04-30"
        assert row["is_overdue"] is True
        assert row["payment_status"] == "部分收款"
        assert row["received_date"] == "2026-04-15"
        assert row["invoice_status"] == "已开票"
        assert row["dunning_status"] == "催收中"
        assert row["dunning_owner"] == "赵六"
        assert sum(bucket["amount"] for bucket in data["aging_buckets"].values()) == round(receivable_total - 10000, 2)
        assert data["key_debtors"][0]["tenant_name"] == "测试客户DEMO_A-RECEIPT-001"
        assert data["key_debtors"][0]["owner"] == "赵六"
        assert data["key_debtors"][0]["overdue_balance"] == round(receivable_total - 10000, 2)
        assert row["receivable_notes"] == "首笔到账"
        assert row["payments"][0]["source"] == "legacy_summary"

        deleted = client.delete(f"/api/contracts/receivables/payments/{row['payments'][0]['id']}")
        assert deleted.status_code == 200
        deleted_row = deleted.json()["rows"][0]
        assert deleted_row["received_amount"] == 0
        assert deleted_row["payment_count"] == 0


def test_receivable_payments_support_multiple_allocations_adjustments_and_deletion() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-PAYMENT-ALLOC-001",
                "PAYALLOC01",
                area=100,
                unit_price=4,
                start_date="2026-04-01",
                end_date="2026-12-31",
                internal_contact_person="周八",
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        expected_rent = _expected_monthly_rent()

        meta = client.patch(
            f"/api/contracts/receivables/monthly/{contract_id}",
            json={
                "month": "2026-04",
                "invoice_status": "红冲/作废",
                "invoice_no": "INV-202604-001",
                "invoice_date": "2026-04-18",
                "invoice_amount": 11000,
                "invoice_tax_rate": 0.06,
                "invoice_notes": "红冲后重开",
                "dunning_status": "已升级",
                "dunning_owner": "周八",
                "last_dunning_date": "2026-04-21",
                "next_follow_up_date": "2026-04-26",
                "dunning_notes": "需财务复核",
                "notes": "对账中",
            },
        )
        assert meta.status_code == 200

        for payload in [
            {"month": "2026-04", "amount": 6000, "payment_date": "2026-04-05", "allocation_type": "收款", "payment_method": "转账", "reference_no": "BANK-001"},
            {"month": "2026-04", "amount": 4000, "payment_date": "2026-04-20", "allocation_type": "收款", "payment_method": "支票", "reference_no": "CHK-002"},
            {"month": "2026-04", "amount": 1000, "payment_date": "2026-04-25", "allocation_type": "减免", "notes": "临时减免"},
        ]:
            response = client.post(f"/api/contracts/receivables/monthly/{contract_id}/payments", json=payload)
            assert response.status_code == 200

        data = client.get("/api/contracts/receivables/monthly?month=2026-04").json()
        row = data["rows"][0]
        assert row["invoice_status"] == "红冲/作废"
        assert row["invoice_no"] == "INV-202604-001"
        assert row["invoice_date"] == "2026-04-18"
        assert row["invoice_amount"] == 11000
        assert row["invoice_tax_rate"] == 0.06
        assert row["invoice_notes"] == "红冲后重开"
        assert row["dunning_status"] == "已升级"
        assert row["last_dunning_date"] == "2026-04-21"
        assert row["next_follow_up_date"] == "2026-04-26"
        assert row["dunning_notes"] == "需财务复核"
        assert row["received_amount"] == 10000
        assert row["adjustment_amount"] == 1000
        assert row["settlement_amount"] == 11000
        assert row["balance"] == round(expected_rent - 11000, 2)
        assert row["payment_status"] == "部分收款"
        assert row["payment_count"] == 3
        assert row["received_date"] == "2026-04-20"
        assert data["total_received"] == 10000
        assert data["total_adjustment"] == 1000
        assert data["total_settled"] == 11000
        assert data["total_balance"] == round(expected_rent - 11000, 2)
        assert data["dunning_alerts"][0]["contract_no"] == "DEMO_A-PAYMENT-ALLOC-001"
        assert data["dunning_alerts"][0]["follow_up_due"] is True
        assert {payment["allocation_type"] for payment in row["payments"]} == {"收款", "减免"}

        filtered = client.get("/api/contracts/receivables/monthly", params={"month": "2026-04", "invoice_status": "红冲/作废"})
        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1
        empty = client.get("/api/contracts/receivables/monthly", params={"month": "2026-04", "invoice_status": "未开票"})
        assert empty.status_code == 200
        assert empty.json()["count"] == 0

        deleted_payment = next(payment for payment in row["payments"] if payment["reference_no"] == "CHK-002")
        deleted = client.delete(f"/api/contracts/receivables/payments/{deleted_payment['id']}")
        assert deleted.status_code == 200
        row_after_delete = deleted.json()["rows"][0]
        assert row_after_delete["received_amount"] == 6000
        assert row_after_delete["adjustment_amount"] == 1000
        assert row_after_delete["payment_count"] == 2


def test_document_customer_name_prefers_customer_then_contract_fallback() -> None:
    from app.services.documents import _customer_name

    assert _customer_name(SimpleNamespace(customer=SimpleNamespace(name="Acme Customer"), tenant_name="Legacy Tenant")) == "Acme Customer"
    assert _customer_name(SimpleNamespace(customer=None, tenant_name="Legacy Tenant")) == "Legacy Tenant"


def test_payment_notice_dunning_notice_and_receipt_documents_generate_html_and_logs() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-DOC-001",
                "DOC01",
                tenant_name="Acme Retail",
                area=100,
                unit_price=4,
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        payment = client.post(
            f"/api/contracts/receivables/monthly/{contract_id}/payments",
            json={
                "month": "2026-04",
                "amount": 5000,
                "payment_date": "2026-04-10",
                "allocation_type": "收款",
                "payment_method": "转账",
                "reference_no": "BANK-DOC-001",
            },
        )
        assert payment.status_code == 200
        payment_row = payment.json()["rows"][0]["payments"][0]
        expected_total = _expected_monthly_rent()
        expected_balance = round(expected_total - 5000, 2)

        notice = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "payment_notice", "contract_id": contract_id, "month": "2026-04", "notice_date": "2026-05-08"},
        )
        assert notice.status_code == 200
        notice_html = notice.json()["html"]
        assert "缴款通知书" in notice_html
        assert "Acme Retail" in notice_html
        assert "DEMO_A-DOC-001" in notice_html
        assert "2026-04" in notice_html
        assert f"{expected_total:,.2f}" in notice_html

        dunning = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "dunning_notice", "contract_id": contract_id, "month": "2026-04", "notice_date": "2026-05-08"},
        )
        assert dunning.status_code == 200
        dunning_html = dunning.json()["html"]
        assert "催收通知" in dunning_html
        assert f"{expected_balance:,.2f}" in dunning_html
        assert "逾期天数" in dunning_html

        receipt = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "receipt", "payment_id": payment_row["id"], "notice_date": "2026-05-08"},
        )
        assert receipt.status_code == 200
        receipt_html = receipt.json()["html"]
        assert "收据" in receipt_html
        assert "5,000.00" in receipt_html
        assert "2026-04-10" in receipt_html

        db = SessionLocal()
        try:
            logs = db.query(OperationLog).filter(OperationLog.action == "document_generate").order_by(OperationLog.id).all()
            assert len(logs) == 3
            assert {log.contract_id for log in logs} == {contract_id}
            assert "payment_notice" in logs[0].changes
            assert "dunning_notice" in logs[1].changes
            assert "receipt" in logs[2].changes
        finally:
            db.close()


def test_invoice_and_dunning_ledgers_filter_overdue_and_export_excel() -> None:
    # 动态锚定月份，避免硬编码日期随时间推移腐化：
    # 上个月 = 已逾期月（月末到期日已过），当月 = 未逾期月（到期日未到）
    today = today_in_timezone()
    overdue_start = today.replace(day=1) + relativedelta(months=-1)
    not_due_start = today.replace(day=1)
    overdue_month = overdue_start.strftime("%Y-%m")
    not_due_month = not_due_start.strftime("%Y-%m")
    far_end = (today.replace(day=1) + relativedelta(months=9, days=-1)).isoformat()

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        overdue = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-LEDGER-OVERDUE",
                "LEDGER01",
                tenant_name="台账逾期客户",
                area=100,
                unit_price=4,
                start_date=overdue_start.isoformat(),
                end_date=far_end,
                internal_contact_person="王催",
            ),
        )
        assert overdue.status_code == 200
        overdue_id = overdue.json()["id"]
        not_due = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-LEDGER-NOTDUE",
                "LEDGER02",
                tenant_name="台账未逾期客户",
                area=100,
                unit_price=4,
                start_date=not_due_start.isoformat(),
                end_date=far_end,
                internal_contact_person="钱七",
            ),
        )
        assert not_due.status_code == 200
        not_due_id = not_due.json()["id"]

        overdue_meta = client.patch(
            f"/api/contracts/receivables/monthly/{overdue_id}",
            json={
                "month": overdue_month,
                "invoice_status": "已开票",
                "invoice_no": "INV-LEDGER-001",
                "invoice_date": overdue_start.replace(day=20).isoformat(),
                "invoice_amount": 10000,
                "invoice_tax_rate": 0.06,
                "invoice_notes": "已开普票",
                "dunning_status": "催收中",
                "dunning_owner": "赵六",
                "last_dunning_date": overdue_start.replace(day=25).isoformat(),
                "next_follow_up_date": overdue_start.replace(day=28).isoformat(),
                "dunning_notes": "承诺月底前补齐",
            },
        )
        assert overdue_meta.status_code == 200
        payment = client.post(
            f"/api/contracts/receivables/monthly/{overdue_id}/payments",
            json={
                "month": overdue_month,
                "amount": 5000,
                "payment_date": overdue_start.replace(day=10).isoformat(),
                "allocation_type": "收款",
            },
        )
        assert payment.status_code == 200

        not_due_meta = client.patch(
            f"/api/contracts/receivables/monthly/{not_due_id}",
            json={
                "month": not_due_month,
                "invoice_status": "待开票",
                "invoice_no": "INV-LEDGER-002",
                "dunning_status": "未催收",
                "dunning_owner": "钱七",
                "next_follow_up_date": not_due_start.replace(day=20).isoformat(),
            },
        )
        assert not_due_meta.status_code == 200

        invoices = client.get(
            "/api/contracts/receivables/invoices",
            params={"month": overdue_month, "invoice_status": "已开票", "keyword": "INV-LEDGER-001"},
        )
        assert invoices.status_code == 200
        invoice_data = invoices.json()
        assert invoice_data["count"] == 1
        invoice_row = invoice_data["rows"][0]
        assert invoice_row["contract_no"] == "DEMO_A-LEDGER-OVERDUE"
        assert invoice_row["invoice_no"] == "INV-LEDGER-001"
        assert invoice_row["invoice_amount"] == 10000
        assert invoice_row["balance"] == round(_expected_monthly_rent() - 5000, 2)

        invoice_room_filter = client.get(
            "/api/contracts/receivables/invoices", params={"month": not_due_month, "keyword": "LEDGER02"}
        )
        assert invoice_room_filter.status_code == 200
        assert [row["contract_no"] for row in invoice_room_filter.json()["rows"]] == ["DEMO_A-LEDGER-NOTDUE"]

        dunning = client.get(
            "/api/contracts/receivables/dunning",
            params={"month": overdue_month, "dunning_status": "催收中", "dunning_owner": "赵六", "overdue_only": "true"},
        )
        assert dunning.status_code == 200
        dunning_data = dunning.json()
        assert dunning_data["count"] == 1
        dunning_row = dunning_data["rows"][0]
        assert dunning_row["contract_no"] == "DEMO_A-LEDGER-OVERDUE"
        assert dunning_row["is_overdue"] is True
        assert dunning_row["overdue_balance"] == round(_expected_monthly_rent() - 5000, 2)
        assert dunning_row["aging_days"] > 0

        current_overdue = client.get(
            "/api/contracts/receivables/dunning", params={"month": not_due_month, "overdue_only": "true"}
        )
        assert current_overdue.status_code == 200
        assert current_overdue.json()["count"] == 0
        current_not_overdue = client.get(
            "/api/contracts/receivables/dunning",
            params={"month": not_due_month, "overdue_only": "false", "dunning_owner": "钱七"},
        )
        assert current_not_overdue.status_code == 200
        assert current_not_overdue.json()["rows"][0]["contract_no"] == "DEMO_A-LEDGER-NOTDUE"
        assert current_not_overdue.json()["rows"][0]["overdue_balance"] == 0

        invoice_export = client.get(
            "/api/contracts/receivables/invoices/export", params={"month": overdue_month, "invoice_status": "已开票"}
        )
        assert invoice_export.status_code == 200
        invoice_book = openpyxl.load_workbook(io.BytesIO(invoice_export.content), data_only=True)
        assert invoice_book.active.title == "发票台账"
        assert invoice_book.active.cell(row=2, column=1).value == "合同号"
        assert invoice_book.active.cell(row=3, column=10).value == "INV-LEDGER-001"

        dunning_export = client.get(
            "/api/contracts/receivables/dunning/export", params={"month": overdue_month, "overdue_only": "true"}
        )
        assert dunning_export.status_code == 200
        dunning_book = openpyxl.load_workbook(io.BytesIO(dunning_export.content), data_only=True)
        assert dunning_book.active.title == "催收台账"
        assert dunning_book.active.cell(row=2, column=1).value == "合同号"
        assert dunning_book.active.cell(row=3, column=1).value == "DEMO_A-LEDGER-OVERDUE"


def test_receivable_payment_marks_overpayment_without_creating_debt() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-OVERPAY-001",
                "OVERPAY01",
                area=100,
                unit_price=4,
                start_date="2026-04-01",
                end_date="2026-12-31",
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]
        expected_rent = _expected_monthly_rent()

        response = client.post(
            f"/api/contracts/receivables/monthly/{contract_id}/payments",
            json={"month": "2026-04", "amount": expected_rent + 500, "payment_date": "2026-04-10", "allocation_type": "收款"},
        )
        assert response.status_code == 200
        row = response.json()["rows"][0]
        assert row["balance"] == 0
        assert row["overpaid_amount"] == 500
        assert row["payment_status"] == "超收"
        assert response.json()["total_overpaid"] == 500


def test_dashboard_stats_include_current_month_receivable_health() -> None:
    today = today_in_timezone()
    month = today.strftime("%Y-%m")
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-DASH-RECEIVABLE",
                "DASHREC01",
                tenant_name="首页历史逾期客户",
                area=100,
                unit_price=4,
                start_date=month_start.isoformat(),
                end_date=month_end.isoformat(),
                internal_contact_person="钱七",
            ),
        )
        assert created.status_code == 200
        contract_id = created.json()["id"]

        response = client.post(
            f"/api/contracts/receivables/monthly/{contract_id}",
            json={
                "month": month,
                "received_amount": 5000,
                "received_date": today.isoformat(),
                "invoice_status": "待开票",
                "dunning_status": "已提醒",
            },
        )
        assert response.status_code == 200

        stats = client.get("/api/contracts/stats")
        assert stats.status_code == 200
        receivables = stats.json()["receivables"]
        expected_rent = _expected_monthly_rent()
        assert receivables["month"] == month
        assert receivables["total_rent"] == expected_rent
        assert receivables["total_received"] == 5000
        assert receivables["total_balance"] == round(expected_rent - 5000, 2)
        assert receivables["total_unpaid_balance"] == round(expected_rent - 5000, 2)
        assert receivables["total_not_due_balance"] == round(expected_rent - 5000, 2)
        assert receivables["total_overdue_balance"] == 0
        assert receivables["unpaid_count"] == 1
        assert receivables["overdue_count"] == 0
        assert receivables["key_debtors"][0]["tenant_name"] == "首页历史逾期客户"
        assert receivables["key_debtors"][0]["owner"] == "钱七"
        assert receivables["key_debtors"][0]["overdue_balance"] == 0
        assert receivables["dunning_alerts"] == []
        assert len(receivables["trend"]) == 6
        assert receivables["trend"][-1]["month"] == month
        assert receivables["trend"][-1]["total_received"] == 5000
        assert receivables["trend"][-1]["total_overdue_balance"] == 0


def test_partial_month_rent_is_prorated_by_calendar_days() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-PRORATE-001",
                "PRORATE01",
                area=100,
                unit_price=3.6,
                monthly_rent=1,
                property_fee_rate=28,
                start_date="2026-04-15",
                end_date="2026-05-14",
            ),
        )
        assert response.status_code == 200
        contract = response.json()
        base_rent = _expected_monthly_rent(unit_price=3.6)
        assert contract["monthly_rent"] == base_rent

        april = client.get("/api/contracts/receivables/monthly?month=2026-04").json()
        assert april["total_rent"] == round(base_rent * 16 / 30, 2)
        assert april["rows"][0]["lease_days"] == 16
        assert april["rows"][0]["month_days"] == 30

        may = client.get("/api/contracts/receivables/monthly?month=2026-05").json()
        assert may["total_rent"] == round(base_rent * 14 / 31, 2)
        assert may["rows"][0]["lease_days"] == 14
        assert may["rows"][0]["month_days"] == 31

        plan = client.get(f"/api/contracts/{contract['id']}/rent-plan").json()["plan"]
        assert [row["lease_days"] for row in plan] == [16, 14]
        assert [row["rent"] for row in plan] == [round(base_rent * 16 / 30, 2), round(base_rent * 14 / 31, 2)]


def test_feishu_expiry_reminder_mentions_internal_contact(monkeypatch) -> None:
    from app.tasks import check_expiring_contracts

    posts = []

    class FakeResponse:
        status_code = 200

    def fake_post(url, json, timeout):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("app.tasks.httpx.post", fake_post)

    db = SessionLocal()
    try:
        demo_a_id = db.query(Building).filter(Building.code == "DEMO_A").one().id
        db.add(SystemConfig(key="feishu_webhook", value="https://example.test/feishu"))
        db.add(
            Contract(
                building_id=demo_a_id,
                contract_no="DEMO_A-FEISHU-OWNER",
                tower="南塔",
                room_no="FS01",
                tenant_name="飞书提醒客户",
                area=100,
                unit_price=4,
                monthly_rent=12000,
                property_fee_rate=28,
                start_date=date.today() - timedelta(days=300),
                end_date=date.today() + timedelta(days=30),
                internal_contact_person="王五",
                internal_contact_feishu_id="ou_owner_001",
                is_deleted=False,
            )
        )
        db.commit()
    finally:
        db.close()

    check_expiring_contracts()

    assert len(posts) == 1
    content = posts[0]["json"]["card"]["elements"][0]["text"]["content"]
    assert "<at id=ou_owner_001></at>" in content
    assert "DEMO_A-FEISHU-OWNER" in content
    assert "飞书提醒客户" in content


def test_batch_pdf_upload_matches_contract_and_updates_missing_list() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post("/api/contracts", json=_contract_payload("DEMO_A-PDF-MATCH-001", "PDF01"))
        assert response.status_code == 200
        contract_id = response.json()["id"]

        response = client.post(
            "/api/contracts/batch-upload-pdf",
            files=[("files", ("DEMO_A-PDF-MATCH-001.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))],
        )
        assert response.status_code == 200
        assert response.json()["matched_count"] == 1

        detail = client.get(f"/api/contracts/{contract_id}")
        assert detail.status_code == 200
        assert detail.json()["pdf_path"].endswith(".pdf.enc")
        stored_path = get_settings().upload_dir / detail.json()["pdf_path"]
        assert stored_path.exists()
        assert is_encrypted_file(stored_path)
        assert b"%PDF" not in stored_path.read_bytes()
        downloaded = client.get(f"/api/contracts/{contract_id}/pdf")
        assert downloaded.status_code == 200
        assert downloaded.content == b"%PDF-1.4\n%%EOF"

        missing = client.get("/api/contracts/missing-pdfs")
        assert missing.status_code == 200
        assert all(item["contract_no"] != "DEMO_A-PDF-MATCH-001" for item in missing.json())


def test_startup_migrates_legacy_plaintext_pdf_to_encrypted_storage() -> None:
    settings = get_settings()
    legacy_name = "legacy-startup.pdf"
    (settings.upload_dir / legacy_name).write_bytes(b"%PDF-1.4\nlegacy\n%%EOF")

    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        contract = Contract(
            contract_no="DEMO_A-LEGACY-PDF",
            building_id=building.id,
            tower="南塔",
            room_no="LEGACY01",
            tenant_name="历史PDF客户",
            area=100,
            unit_price=4,
            monthly_rent=12000,
            property_fee_rate=28,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            pdf_path=legacy_name,
        )
        db.add(contract)
        db.commit()
        contract_id = contract.id
    finally:
        db.close()

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        detail = client.get(f"/api/contracts/{contract_id}")
        assert detail.status_code == 200
        migrated_name = detail.json()["pdf_path"]
        assert migrated_name == "legacy-startup.pdf.enc"
        assert not (settings.upload_dir / legacy_name).exists()
        assert is_encrypted_file(settings.upload_dir / migrated_name)

        downloaded = client.get(f"/api/contracts/{contract_id}/pdf")
        assert downloaded.status_code == 200
        assert downloaded.content == b"%PDF-1.4\nlegacy\n%%EOF"


def test_integrity_report_can_export_excel() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.get("/api/system/integrity-check/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        assert "完整性问题" in workbook.sheetnames
        assert "数据库概览" in workbook.sheetnames


def test_room_import_rejects_invalid_file_without_partial_write() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        content = _workbook_bytes(
            ["塔楼", "楼层", "室号", "类型", "面积"],
            [
                ["北塔", "20", "NIMPORT01", "办公", 100],
                ["北塔", "20", "NIMPORT02", "办公", "bad-area"],
            ],
        )
        response = client.post(
            "/api/rooms/import",
            files={"file": ("rooms.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rooms = client.get("/api/rooms").json()
        assert all(room["room_no"] != "NIMPORT01" for room in rooms)


def test_demo_seed_rooms_match_real_tower_floor_plan() -> None:
    from app.scripts.seed_demo_data import build_room_seeds

    south = "\u5357\u5854"
    north = "\u5317\u5854"
    commercial_support = "\u5546\u4e1a\u914d\u5957"

    rooms = build_room_seeds()
    south_floors = {room.floor_number for room in rooms if room.tower == south}
    north_floors = {room.floor_number for room in rooms if room.tower == north}
    room_nos = {room.room_no for room in rooms}

    assert south_floors == set(range(1, 37)) - {4, 13, 14, 24, 25}
    assert north_floors == set(range(1, 23)) - {4, 13, 14}
    assert all(room.tower != commercial_support for room in rooms)
    assert {"N0101", "N0201", "S0101", "P0301"}.issubset(room_nos)


def test_demo_seed_can_target_fixed_demo_date(monkeypatch) -> None:
    import app.scripts.seed_demo_data as seed_demo_data

    monkeypatch.setenv("DEMO_TODAY", "2026-05-19")
    reloaded = importlib.reload(seed_demo_data)
    try:
        rooms = reloaded.build_room_seeds()
        contracts = reloaded.build_contract_seeds(rooms)

        assert reloaded.TODAY == date(2026, 5, 19)
        assert reloaded.summarize_contracts(contracts) == {
            "待生效": 18,
            "即将到期": 18,
            "已退租": 24,
            "逾期占用": 4,
            "在租": 112,
        }
    finally:
        monkeypatch.delenv("DEMO_TODAY", raising=False)
        importlib.reload(seed_demo_data)


def test_demo_seed_inserts_contract_room_items() -> None:
    from app.scripts.seed_demo_data import (
        build_contract_seeds,
        build_room_seeds,
        insert_contracts,
        insert_rooms,
        reset_demo_tables,
    )

    rooms = build_room_seeds()
    contracts = build_contract_seeds(rooms)

    reset_demo_tables()
    insert_rooms(rooms)
    insert_contracts(contracts)

    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        assert db.query(Contract).count() == len(contracts)
        assert db.query(ContractRoom).count() == len(contracts)
        assert db.query(Room).filter(Room.building_id == building.id, Room.zone_id.isnot(None)).count() == len(rooms)
        first_contract = db.query(Contract).filter(Contract.contract_no == contracts[0].contract_no).one()
        first_item = db.query(ContractRoom).filter(ContractRoom.contract_id == first_contract.id).one()
        linked_room = db.get(Room, first_item.room_id)
        assert first_contract.building_id == building.id
        assert first_item.building_id == building.id
        assert linked_room is not None
        assert linked_room.building_id == building.id
        assert first_item.room_no == contracts[0].room_no
        assert first_item.area == contracts[0].area
    finally:
        db.close()


def test_demo_seed_operational_data_is_mostly_normal_with_risk_samples() -> None:
    from app.scripts.seed_demo_data import (
        DEMO_OVERDUE_CONTRACTS,
        DEMO_OVERDUE_MONTHS,
        build_contract_seeds,
        build_room_seeds,
        insert_contracts,
        insert_rooms,
        _current_contracts,
        reset_demo_tables,
        seed_demo_operational_data,
    )
    from app.services.contracts import get_monthly_receivables
    from app.services.rooms import _contract_receivable_summaries

    rooms = build_room_seeds()
    contracts = build_contract_seeds(rooms)

    reset_demo_tables()
    insert_rooms(rooms)
    insert_contracts(contracts)
    operational_summary = seed_demo_operational_data()

    db = SessionLocal()
    try:
        current_contracts = _current_contracts(db)
        current_count = len(current_contracts)
        overdue_contracts = current_contracts[:DEMO_OVERDUE_CONTRACTS]
        overdue_contract_ids = {contract.id for contract in overdue_contracts}
        receivable_summaries = _contract_receivable_summaries(db, current_contracts)
        normal_summaries = [
            receivable_summaries[contract.id]
            for contract in current_contracts
            if contract.id not in overdue_contract_ids
        ]

        assert db.query(Customer).count() == len(contracts)
        assert operational_summary["current_contracts"] == current_count
        assert (
            db.query(Attachment)
            .filter(Attachment.entity_type == "contract", Attachment.category == "合同正文")
            .count()
            >= current_count - 5
        )
        assert (
            db.query(Attachment)
            .filter(Attachment.entity_type == "customer", Attachment.category == "证照")
            .count()
            >= current_count - 4
        )
        assert all((get_settings().attachment_dir / item.storage_path).is_file() for item in db.query(Attachment).all())
        assert db.query(ContractDeposit).count() == current_count
        assert db.query(ContractDeposit).filter(ContractDeposit.status == "paid").count() >= current_count - 4
        assert (
            db.query(CustomerFollowUp)
            .filter(CustomerFollowUp.next_follow_up_date < today_in_timezone())
            .count()
            == 4
        )
        assert db.query(RentTerm).filter(RentTerm.rule_type == "temporary_adjustment").count() == 2

        assert normal_summaries
        assert all(summary["overdue_balance"] == 0 for summary in normal_summaries)
        assert all(summary["overdue_months"] == 0 for summary in normal_summaries)
        assert all(summary["unpaid_months"] <= 1 for summary in normal_summaries)

        for contract in overdue_contracts:
            summary = receivable_summaries[contract.id]
            assert 0 < summary["overdue_months"] <= DEMO_OVERDUE_MONTHS
            assert 0 < summary["overdue_balance"] <= round(float(contract.monthly_rent or 0) * DEMO_OVERDUE_MONTHS, 2)
            assert summary["unpaid_months"] <= DEMO_OVERDUE_MONTHS + 1

        current_month = today_in_timezone().strftime("%Y-%m")
        current_report = get_monthly_receivables(
            db,
            current_month,
            contract_ids=[contract.id for contract in current_contracts if contract.id not in overdue_contract_ids],
        )
        assert current_report["total_received"] > 0
        assert 60 <= current_report["received_rate"] <= 90
        not_due_rows = [
            row
            for row in current_report["rows"]
            if row["total"] > 0 and row["unpaid_balance"] > 0 and row["overdue_balance"] == 0
        ]
        assert not_due_rows
        assert all(row["not_due_balance"] == row["unpaid_balance"] for row in not_due_rows)
    finally:
        db.close()

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")
        data = client.get("/api/risks", params={"limit": 500}).json()

    risk_types = {item["risk_type"] for item in data["items"]}
    assert {
        "overdue_receivable",
        "contract_expiring",
        "follow_up_overdue",
        "deposit_unpaid",
        "attachment_missing",
        "large_adjustment",
    } <= risk_types
    # One contract can intentionally contribute more than one risk item, so the
    # number of risk rows is not directly comparable with the contract count.
    # Keep the useful invariants explicit and only require the seeded sample to
    # remain a minority of the active portfolio.
    assert data["summary"]["total"] == sum(data["summary"]["by_type"].values())
    assert data["summary"]["total"] < current_count // 2


def test_legacy_podium_tower_is_renamed_to_commercial_support() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/rooms",
            json={"tower": "南塔裙楼", "floor": "2", "room_no": "P0201", "room_type": "餐饮", "area": 520},
        )
        assert response.status_code == 200

        response = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-PODIUM-001", "P0201", tower="南塔裙楼", tenant_name="商业配套客户"),
        )
        assert response.status_code == 200
        assert response.json()["tower"] == "商业配套"

        rooms = client.get("/api/rooms").json()
        assert next(room for room in rooms if room["room_no"] == "P0201")["tower"] == "商业配套"

        contracts = client.get("/api/contracts", params={"tower": "商业配套"}).json()
        assert contracts["total"] == 1
        assert contracts["items"][0]["tower"] == "商业配套"


def test_dashboard_tower_performance_groups_office_and_commercial() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        rooms = [
            {"tower": "南塔", "floor": "8", "room_no": "S0801", "room_type": "办公", "area": 100},
            {"tower": "北塔", "floor": "12", "room_no": "N1201", "room_type": "办公", "area": 80},
            {"tower": "南塔", "floor": "3", "room_no": "S0301", "room_type": "餐饮", "area": 60},
            {"tower": "北塔", "floor": "1", "room_no": "N0101", "room_type": "办公", "area": 50},
        ]
        for room in rooms:
            response = client.post("/api/rooms", json=room)
            assert response.status_code == 200

        start_date = (date.today() - timedelta(days=10)).isoformat()
        end_date = (date.today() + timedelta(days=120)).isoformat()
        contracts = [
            ("DEMO_A-DASH-SOUTH", "S0801", "南塔", 100),
            ("DEMO_A-DASH-NORTH", "N1201", "北塔", 80),
            ("DEMO_A-DASH-RETAIL-S", "S0301", "南塔", 60),
            ("DEMO_A-DASH-RETAIL-N", "N0101", "北塔", 50),
        ]
        for contract_no, room_no, tower, area in contracts:
            response = client.post(
                "/api/contracts",
                json=_contract_payload(
                    contract_no,
                    room_no,
                    tower=tower,
                    area=area,
                    start_date=start_date,
                    end_date=end_date,
                ),
            )
            assert response.status_code == 200

        stats = client.get("/api/contracts/stats")
        assert stats.status_code == 200
        tower_data = stats.json()["tower_data"]

        assert tower_data["南塔办公"]["count"] == 1
        assert tower_data["南塔办公"]["area"] == 100
        assert tower_data["南塔办公"]["room_area"] == 100
        assert tower_data["北塔办公"]["count"] == 1
        assert tower_data["北塔办公"]["area"] == 80
        assert tower_data["北塔办公"]["room_area"] == 80
        assert tower_data["商业配套"]["count"] == 2
        assert tower_data["商业配套"]["area"] == 110
        assert tower_data["商业配套"]["room_area"] == 110


def test_dashboard_customer_overview_merges_customer_contract_area() -> None:
    today = today_in_timezone()
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        contracts = [
            ("DEMO_A-CUSTOMER-001", "CUST01", "重点客户A", 120),
            ("DEMO_A-CUSTOMER-002", "CUST02", "重点客户A", 100),
            ("DEMO_A-CUSTOMER-003", "CUST03", "普通客户B", 90),
        ]
        for contract_no, room_no, tenant_name, area in contracts:
            response = client.post(
                "/api/contracts",
                json=_contract_payload(contract_no, room_no, tenant_name=tenant_name, area=area),
            )
            assert response.status_code == 200

        stats = client.get("/api/contracts/stats")
        assert stats.status_code == 200
        customer_data = stats.json()["customer_data"]

        assert customer_data[0]["tenant_name"] == "重点客户A"
        assert customer_data[0]["count"] == 2
        assert customer_data[0]["room_count"] == 2
        assert customer_data[0]["area"] == 220
        assert customer_data[0]["rent"] == _expected_monthly_rent(area=120) + _expected_monthly_rent(area=100)
        assert customer_data[0]["rooms"] == ["CUST01", "CUST02"]
        assert customer_data[1]["tenant_name"] == "普通客户B"

        expired = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-CUSTOMER-HOLDOVER",
                "CUST04",
                tenant_name="\u8fc7\u671f\u5360\u7528\u5ba2\u6237",
                start_date=(today - timedelta(days=365)).isoformat(),
                end_date=(today - timedelta(days=1)).isoformat(),
                disposal="\u903e\u671f\u5360\u7528",
            ),
        )
        assert expired.status_code == 200

        stats = client.get("/api/contracts/stats")
        assert stats.status_code == 200
        holdover = next(item for item in stats.json()["customer_data"] if item["tenant_name"] == "\u8fc7\u671f\u5360\u7528\u5ba2\u6237")
        assert holdover["nearest_end_date"] is None


def test_contract_room_details_drive_contract_outputs_and_receivables() -> None:
    first_rent = _expected_monthly_rent(area=100, unit_price=4)
    second_rent = _expected_monthly_rent(area=80, unit_price=5)
    expected_rent = round(first_rent + second_rent, 2)
    expected_unit_price = round(expected_rent * 12 / 365 / 180, 2)

    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        zones_by_code = {
            zone.code: zone
            for zone in (
                db.query(Zone)
                .filter(
                    Zone.building_id == building.id,
                    Zone.code.in_(("SOUTH_OFFICE", "NORTH_OFFICE")),
                )
                .all()
            )
        }
        south_room = Room(
            building_id=building.id,
            zone_id=zones_by_code["SOUTH_OFFICE"].id,
            tower="南塔",
            floor="10",
            room_no="S1001",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=False,
        )
        north_room = Room(
            building_id=building.id,
            zone_id=zones_by_code["NORTH_OFFICE"].id,
            tower="北塔",
            floor="12",
            room_no="N1201",
            room_type="办公",
            area=80,
            is_leasable=True,
            is_deleted=False,
        )
        db.add_all([south_room, north_room])
        db.flush()
        contract = Contract(
            contract_no="DEMO_A-ROOM-SUMMARY",
            building_id=building.id,
            tower="南塔",
            room_no="STALE-ROOM",
            tenant_name="明细优先客户",
            area=1,
            unit_price=1,
            monthly_rent=1,
            property_fee_rate=28,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            is_deleted=False,
        )
        db.add(contract)
        db.flush()
        db.add_all(
            [
                ContractRoom(
                    contract_id=contract.id,
                    building_id=building.id,
                    room_id=south_room.id,
                    tower="南塔",
                    room_no="S1001",
                    area=100,
                    unit_price=4,
                    monthly_rent=first_rent,
                ),
                ContractRoom(
                    contract_id=contract.id,
                    building_id=building.id,
                    room_id=north_room.id,
                    tower="北塔",
                    room_no="N1201",
                    area=80,
                    unit_price=5,
                    monthly_rent=second_rent,
                ),
            ]
        )
        db.commit()
        contract_id = contract.id
    finally:
        db.close()

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        listed = client.get("/api/contracts", params={"keyword": "DEMO_A-ROOM-SUMMARY", "page": 1, "page_size": 20})
        detail = client.get(f"/api/contracts/{contract_id}")
        receivables = client.get("/api/contracts/receivables/monthly", params={"month": "2026-05"})
        stats = client.get("/api/contracts/stats")
        exported = client.get("/api/contracts/export/excel", params={"keyword": "DEMO_A-ROOM-SUMMARY"})

    assert listed.status_code == 200
    listed_row = listed.json()["items"][0]
    assert listed_row["room_no"] == "S1001、N1201"
    assert listed_row["tower"] == "多房源"
    assert listed_row["area"] == 180
    assert listed_row["monthly_rent"] == expected_rent
    assert listed_row["unit_price"] == expected_unit_price

    assert detail.status_code == 200
    detail_row = detail.json()
    assert detail_row["room_no"] == "S1001、N1201"
    assert detail_row["area"] == 180
    assert detail_row["monthly_rent"] == expected_rent
    assert [item["room_no"] for item in detail_row["room_items"]] == ["S1001", "N1201"]

    assert receivables.status_code == 200
    receivable_row = next(item for item in receivables.json()["rows"] if item["contract_no"] == "DEMO_A-ROOM-SUMMARY")
    assert receivable_row["room_no"] == "S1001、N1201"
    assert receivable_row["tower"] == "多房源"
    assert receivable_row["area"] == 180
    assert receivable_row["rent"] == expected_rent
    assert receivable_row["total"] == expected_rent

    assert stats.status_code == 200
    customer = next(item for item in stats.json()["customer_data"] if item["tenant_name"] == "明细优先客户")
    assert customer["area"] == 180
    assert customer["rent"] == expected_rent
    assert customer["rooms"] == ["N1201", "S1001"]

    assert exported.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
    sheet = workbook["租赁合同"]
    assert sheet.cell(row=3, column=3).value == "多房源"
    assert sheet.cell(row=3, column=4).value == "S1001、N1201"
    assert sheet.cell(row=3, column=6).value == 180
    assert sheet.cell(row=3, column=7).value == expected_unit_price
    assert sheet.cell(row=3, column=10).value == expected_rent


def test_backup_archive_is_encrypted_and_contains_readable_sqlite_after_decrypt() -> None:
    with _client() as client:
        _ensure_admin(client)

    result = create_backup_archive()
    assert result["backup_type"] == "manual"
    assert result["type_label"] == "手动立即备份"
    archive_path = get_settings().backup_dir / result["filename"]
    assert archive_path.exists()
    assert archive_path.name.endswith(".zip.enc")
    assert is_encrypted_file(archive_path)

    with tempfile.TemporaryDirectory() as temp_dir:
        decrypted = Path(temp_dir) / "backup.zip"
        decrypt_file(archive_path, decrypted, get_settings().data_key)
        with zipfile.ZipFile(decrypted) as archive:
            archive.extract("lease.db", temp_dir)

        db_path = f"{temp_dir}/lease.db"
        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        finally:
            connection.close()

    assert {"users", "contracts", "rooms"}.issubset(tables)


def test_backup_type_labels_legacy_auto_and_safety_names() -> None:
    assert backup_type_from_filename("backup_20260507_020000_005083.zip.enc") == "auto"
    assert backup_type_from_filename("backup_20260507_130000_005083.zip.enc") == "manual"
    assert backup_type_from_filename("auto_backup_20260507_020000_005083.zip.enc") == "auto"
    assert backup_type_from_filename("safety_backup_20260507_130000_005083.zip.enc") == "safety"


def test_backup_restore_reverts_database_and_pdfs() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post("/api/contracts", json=_contract_payload("DEMO_A-BACKUP-KEEP", "BKP01"))
        assert created.status_code == 200
        contract_id = created.json()["id"]
        uploaded = client.post(
            f"/api/contracts/{contract_id}/upload-pdf",
            files={"file": ("keep.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        )
        assert uploaded.status_code == 200

        backup = client.post("/api/backups")
        assert backup.status_code == 200
        filename = backup.json()["filename"]

        extra = client.post("/api/contracts", json=_contract_payload("DEMO_A-BACKUP-DROP", "BKP02"))
        assert extra.status_code == 200

        restored = client.post(f"/api/backups/{filename}/restore")
        assert restored.status_code == 200
        assert "safety_backup" in restored.json()
        assert restored.json()["safety_backup"].startswith("safety_backup_")

        kept = client.get("/api/contracts?keyword=DEMO_A-BACKUP-KEEP&page=1&page_size=20")
        dropped = client.get("/api/contracts?keyword=DEMO_A-BACKUP-DROP&page=1&page_size=20")
        assert kept.status_code == 200
        assert dropped.status_code == 200
        assert kept.json()["total"] == 1
        assert dropped.json()["total"] == 0

        restored_contract_id = kept.json()["items"][0]["id"]
        assert client.get(f"/api/contracts/{restored_contract_id}/pdf").status_code == 200


def test_encrypted_backup_restore_rejects_wrong_key(monkeypatch) -> None:
    from dataclasses import replace
    from app.services import backup as backup_service

    with _client() as client:
        _ensure_admin(client)

    result = create_backup_archive()
    assert result["filename"].endswith(".zip.enc")

    wrong_settings = replace(
        get_settings(),
        data_encryption_key="QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo5ODc2NTQ=",
    )
    monkeypatch.setattr(backup_service, "get_settings", lambda: wrong_settings)
    with pytest.raises(HTTPException) as exc_info:
        backup_service.restore_backup_archive(result["filename"])
    assert exc_info.value.status_code == 400
