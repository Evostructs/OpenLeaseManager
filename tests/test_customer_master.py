from __future__ import annotations

from datetime import timedelta
import io

from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient
import openpyxl

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


def _contract_payload(contract_no: str, room_no: str, **overrides) -> dict:
    payload = {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"旧客户{contract_no}",
        "area": 100,
        "unit_price": 4,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    payload.update(overrides)
    return payload


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _month_start(offset: int = 0):
    return today_in_timezone().replace(day=1) + relativedelta(months=offset)


def test_customer_crud_search_detail_and_logs():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        created = client.post(
            "/api/customers",
            json={
                "name": "客户主档A",
                "short_name": "主档A",
                "credit_code": "91310000TESTA",
                "customer_type": "企业",
                "industry": "科技服务",
                "contact_name": "张三",
                "contact_phone": "13800000000",
            },
        )
        assert created.status_code == 200
        customer = created.json()
        assert customer["name"] == "客户主档A"
        assert customer["status"] == "active"

        searched = client.get("/api/customers", params={"keyword": "91310000TESTA"})
        assert searched.status_code == 200
        assert searched.json()["total"] == 1

        updated = client.put(
            f"/api/customers/{customer['id']}",
            json={"short_name": "主档A更新", "status": "inactive", "contact_email": "customer-a@example.com"},
        )
        assert updated.status_code == 200
        assert updated.json()["short_name"] == "主档A更新"
        assert updated.json()["status"] == "inactive"

        detail = client.get(f"/api/customers/{customer['id']}")
        assert detail.status_code == 200
        assert detail.json()["contracts"] == []

        logs = client.get("/api/audit/logs").json()["items"]
        actions = {item["action"] for item in logs}
        assert "customer_profile_create" in actions

        db = SessionLocal()
        try:
            master_logs = (
                db.query(OperationLog)
                .filter(
                    OperationLog.target_type == "customer",
                    OperationLog.target_id == customer["id"],
                    OperationLog.action.in_(["customer_create", "customer_update"]),
                )
                .all()
            )
            assert {item.action for item in master_logs} == {
                "customer_create",
                "customer_update",
            }
            assert all(item.scope_type in {"portfolio", "global"} for item in master_logs)
            assert all(item.building_id is None for item in master_logs)
        finally:
            db.close()


def test_contract_customer_fallback_and_master_name_priority():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        fallback = client.post("/api/contracts", json=_contract_payload("DEMO_A-CUST-FALLBACK", "CUST-FB", tenant_name="旧字段客户"))
        assert fallback.status_code == 200
        fallback_id = fallback.json()["id"]

        customer = client.post("/api/customers", json={"name": "主档客户", "short_name": "主档"}).json()
        linked = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-CUST-LINKED",
                "CUST-LINK",
                tenant_name="合同旧客户名",
                customer_id=customer["id"],
            ),
        )
        assert linked.status_code == 200
        linked_id = linked.json()["id"]

        fallback_detail = client.get(f"/api/contracts/{fallback_id}").json()
        assert fallback_detail["customer_id"] is None
        assert fallback_detail["tenant_name"] == "旧字段客户"
        assert fallback_detail["legacy_tenant_name"] == "旧字段客户"

        linked_detail = client.get(f"/api/contracts/{linked_id}").json()
        assert linked_detail["customer_id"] == customer["id"]
        assert linked_detail["tenant_name"] == "主档客户"
        assert linked_detail["legacy_tenant_name"] == "合同旧客户名"
        assert linked_detail["customer"]["short_name"] == "主档"

        other_customer = client.post("/api/customers", json={"name": "新主档客户"}).json()
        updated = client.put(f"/api/contracts/{fallback_id}", json={"customer_id": other_customer["id"]})
        assert updated.status_code == 200
        assert updated.json()["tenant_name"] == "新主档客户"

        logs = client.get(f"/api/contracts/{fallback_id}/logs").json()
        assert any(log["action"] == "update" and "customer_id" in log["changes"] for log in logs)


def test_customer_overview_groups_by_customer_id_and_legacy_name():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "聚合主档客户"}).json()
        for contract_no, room_no, legacy_name, area in [
            ("DEMO_A-CUST-GROUP-1", "CUST-G1", "主档旧名一", 120),
            ("DEMO_A-CUST-GROUP-2", "CUST-G2", "主档旧名二", 80),
        ]:
            response = client.post(
                "/api/contracts",
                json=_contract_payload(contract_no, room_no, tenant_name=legacy_name, area=area, customer_id=customer["id"]),
            )
            assert response.status_code == 200

        for contract_no, room_no in [
            ("DEMO_A-CUST-LEGACY-1", "CUST-L1"),
            ("DEMO_A-CUST-LEGACY-2", "CUST-L2"),
        ]:
            response = client.post(
                "/api/contracts",
                json=_contract_payload(contract_no, room_no, tenant_name="未关联同名客户", area=60),
            )
            assert response.status_code == 200

        stats = client.get("/api/contracts/stats").json()
        rows = {item["tenant_name"]: item for item in stats["customer_data"]}

        assert rows["聚合主档客户"]["customer_id"] == customer["id"]
        assert rows["聚合主档客户"]["count"] == 2
        assert rows["聚合主档客户"]["area"] == 200
        assert rows["未关联同名客户"]["customer_id"] is None
        assert rows["未关联同名客户"]["count"] == 2


def test_import_exact_customer_name_links_customer_master():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "导入匹配客户"}).json()
        content = _workbook_bytes(
            ["合同编号", "楼塔", "室号", "客户名称", "出租面积", "租金单价", "签约类型", "起租日", "终止日"],
            [["DEMO_A-CUST-IMPORT", "北塔", "CUST-IMP", "导入匹配客户", 90, 5, "新签", "2026-01-01", "2026-12-31"]],
        )
        imported = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert imported.status_code == 200
        assert imported.json()["imported"] == 1

        listed = client.get("/api/contracts", params={"keyword": "DEMO_A-CUST-IMPORT"})
        assert listed.status_code == 200
        contract_id = listed.json()["items"][0]["id"]
        detail = client.get(f"/api/contracts/{contract_id}").json()
        assert detail["customer_id"] == customer["id"]
        assert detail["tenant_name"] == "导入匹配客户"


def test_customer_detail_returns_operating_summary_and_action_cues():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        today = today_in_timezone()
        customer = client.post("/api/customers", json={"name": "经营卡片客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-CUST-ACTION-SUMMARY",
                "CUST-ACTION-1",
                tenant_name="旧合同客户名",
                customer_id=customer["id"],
                area=100,
                unit_price=4,
                start_date=_month_start(-2).isoformat(),
                end_date=(today + timedelta(days=60)).isoformat(),
            ),
        )
        assert contract.status_code == 200

        detail = client.get(f"/api/customers/{customer['id']}")
        assert detail.status_code == 200
        payload = detail.json()
        summary = payload["operating_summary"]

        assert payload["contracts"][0]["contract_no"] == "DEMO_A-CUST-ACTION-SUMMARY"
        assert summary["contract_count"] == 1
        assert summary["current_leased_area"] == 100
        assert summary["current_monthly_rent"] > 0
        assert summary["current_month_receivable"] > 0
        assert summary["unpaid_balance"] >= summary["current_month_receivable"]
        assert summary["overdue_balance"] > 0
        assert summary["nearest_expiring_contract"]["contract_no"] == "DEMO_A-CUST-ACTION-SUMMARY"
        assert summary["nearest_expiry_days"] == 60
        cue_types = {item["type"] for item in summary["action_cues"]}
        assert {"overdue", "expiry"} <= cue_types


def test_customer_follow_up_crud_due_query_and_logs():
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        today = today_in_timezone()
        customer = client.post("/api/customers", json={"name": "跟进闭环客户", "contact_name": "王五"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload(
                "DEMO_A-CUST-FOLLOW",
                "CUST-FOLLOW",
                customer_id=customer["id"],
                start_date=_month_start(-1).isoformat(),
                end_date=(today + timedelta(days=180)).isoformat(),
            ),
        ).json()

        created = client.post(
            f"/api/customers/{customer['id']}/follow-ups",
            json={
                "contract_id": contract["id"],
                "follow_up_type": "微信",
                "occurred_at": f"{today.isoformat()}T09:30:00",
                "content": "确认本月付款安排",
                "next_action": "明天再次确认付款凭证",
                "next_follow_up_date": (today - timedelta(days=1)).isoformat(),
                "owner": "Alice",
            },
        )
        assert created.status_code == 200
        follow_up = created.json()
        assert follow_up["status"] == "open"
        assert follow_up["contract_no"] == "DEMO_A-CUST-FOLLOW"

        detail = client.get(f"/api/customers/{customer['id']}").json()
        assert len(detail["follow_ups"]) == 1
        assert detail["operating_summary"]["last_follow_up_time"].startswith(today.isoformat())
        assert detail["operating_summary"]["next_follow_up_date"] == (today - timedelta(days=1)).isoformat()
        assert any(item["type"] == "follow_up_overdue" for item in detail["operating_summary"]["action_cues"])

        due = client.get("/api/customers/follow-ups/due")
        assert due.status_code == 200
        due_payload = due.json()
        assert due_payload["total"] == 1
        assert due_payload["items"][0]["customer_id"] == customer["id"]
        assert due_payload["items"][0]["open_follow_up_count"] == 1

        updated = client.put(
            f"/api/customers/{customer['id']}/follow-ups/{follow_up['id']}",
            json={"content": "客户承诺今日付款", "next_action": "下午检查到账", "owner": "Bob"},
        )
        assert updated.status_code == 200
        assert updated.json()["owner"] == "Bob"

        completed = client.post(f"/api/customers/{customer['id']}/follow-ups/{follow_up['id']}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "done"
        assert client.get("/api/customers/follow-ups/due").json()["total"] == 0

        deleted = client.delete(f"/api/customers/{customer['id']}/follow-ups/{follow_up['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert client.get(f"/api/customers/{customer['id']}").json()["follow_ups"] == []

        logs = client.get("/api/audit/logs").json()["items"]
        actions = {item["action"] for item in logs}
        assert {
            "customer_followup_create",
            "customer_followup_update",
            "customer_followup_done",
            "customer_followup_delete",
        } <= actions
