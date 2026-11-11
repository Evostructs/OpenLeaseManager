from __future__ import annotations

from datetime import timedelta
import io

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import ContractDeposit, Customer, CustomerFollowUp, OperationLog


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


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _load_response_workbook(response):
    assert response.status_code == 200
    return openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)


def _disposition(response) -> str:
    return response.headers["content-disposition"]


def _headers(workbook, row: int = 2) -> list[str]:
    sheet = workbook.active
    values = [cell.value for cell in sheet[row]]
    if not any(values):
        values = [cell.value for cell in sheet[1]]
    return values


def _metadata(workbook) -> dict:
    sheet = workbook["导出说明"]
    return {row[0].value: row[1].value for row in sheet.iter_rows(min_row=1, max_col=2) if row[0].value}


def _workbook_text(workbook) -> str:
    values = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            values.extend(str(value) for value in row if value is not None)
    return "\n".join(values)


def _room_payload(room_no: str, **overrides) -> dict:
    payload = {"tower": "南塔", "floor": "18", "room_no": room_no, "room_type": "办公", "area": 100}
    payload.update(overrides)
    return payload


def _contract_payload(contract_no: str, room_no: str, customer_id: int, **overrides) -> dict:
    today = today_in_timezone()
    payload = {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": contract_no,
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=45)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_core_operations_exports_use_xlsx_chinese_headers_and_filters() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post(
            "/api/customers",
            json={"name": "导出客户A", "short_name": "客户A", "credit_code": "91310000EXPORTA", "contact_phone": "13800000000"},
        ).json()
        assert client.post("/api/rooms", json=_room_payload("EXP-1801")).status_code == 200
        contract = client.post("/api/contracts", json=_contract_payload("DEMO_A-EXPORT-001", "EXP-1801", customer["id"])).json()
        assert client.post(
            f"/api/customers/{customer['id']}/follow-ups",
            json={
                "contract_id": contract["id"],
                "follow_up_type": "电话",
                "content": "确认交接资料",
                "next_follow_up_date": today_in_timezone().isoformat(),
                "owner": "Alice",
            },
        ).status_code == 200
        deposit = client.post(
            f"/api/contracts/{contract['id']}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 30000, "due_date": today_in_timezone().isoformat()},
        )
        assert deposit.status_code == 200

        today = today_in_timezone().strftime("%Y-%m-%d")

        customer_response = client.get("/api/customers/export", params={"keyword": "91310000EXPORTA"})
        assert f'filename="lease-manager-customers-keyword-91310000exporta-{today}.xlsx"' in _disposition(customer_response)
        customer_book = _load_response_workbook(customer_response)
        assert "客户名称" in _headers(customer_book)
        assert customer_book.active["A3"].value == "导出客户A"
        customer_meta = _metadata(customer_book)
        assert customer_meta["系统"] == "lease-manager"
        assert customer_meta["导出类型"] == "客户清单"
        assert customer_meta["筛选条件"] == "关键词=91310000EXPORTA"
        assert customer_meta["数据行数"] == 1

        follow_response = client.get("/api/customers/follow-ups/export", params={"customer_id": customer["id"]})
        assert f'filename="lease-manager-customer-follow-ups-customer-{customer["id"]}-{today}.xlsx"' in _disposition(follow_response)
        follow_book = _load_response_workbook(follow_response)
        assert "跟进内容" in _headers(follow_book)
        assert follow_book.active["E3"].value == "确认交接资料"
        assert _metadata(follow_book)["筛选条件"] == f"客户ID={customer['id']}"

        deposit_response = client.get("/api/contracts/deposits/export", params={"contract_id": contract["id"]})
        assert f'filename="lease-manager-contract-deposits-contract-{contract["id"]}-{today}.xlsx"' in _disposition(deposit_response)
        deposit_book = _load_response_workbook(deposit_response)
        assert "押金状态" in _headers(deposit_book)
        assert deposit_book.active["F3"].value == 30000
        assert deposit_book.active["D3"].value == "南塔办公"
        assert _metadata(deposit_book)["筛选条件"] == f"合同ID={contract['id']}"

        risk_response = client.get("/api/risks/export", params={"risk_type": "contract_expiring"})
        assert f'filename="lease-manager-risks-type-contract-expiring-{today}.xlsx"' in _disposition(risk_response)
        risk_book = _load_response_workbook(risk_response)
        assert "风险类型" in _headers(risk_book)
        assert any(row[0].value == "90天内到期" for row in risk_book.active.iter_rows(min_row=3))
        assert _metadata(risk_book)["筛选条件"] == "风险类型=90天内到期"

        room_response = client.get("/api/rooms/export/excel", params={"tower": "南塔", "status": "在租"})
        assert f'filename="lease-manager-room-operations-tower-south-office-status-leased-{today}.xlsx"' in _disposition(room_response)
        room_book = _load_response_workbook(room_response)
        assert "经营状态" in _headers(room_book)
        assert any(row[2].value == "EXP-1801" for row in room_book.active.iter_rows(min_row=3))
        assert room_book.active["A3"].value == "南塔办公"
        assert _metadata(room_book)["筛选条件"] == "塔楼=南塔办公；经营状态=在租"

        for workbook in [customer_book, follow_book, deposit_book, risk_book, room_book]:
            text = _workbook_text(workbook)
            assert "裙楼" not in text
            for sensitive in ["Authorization", "password", "cookie", "token", "完整 prompt", "完整 AI output"]:
                assert sensitive.lower() not in text.lower()


def test_export_empty_results_are_readable_and_include_metadata() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        response = client.get("/api/customers/export", params={"status": "archived"})
        assert response.status_code == 200
        assert "lease-manager-customers-status-archived-" in _disposition(response)
        workbook = _load_response_workbook(response)
        assert workbook.active["A3"].value == "当前筛选条件下无数据"
        metadata = _metadata(workbook)
        assert metadata["导出类型"] == "客户清单"
        assert metadata["筛选条件"] == "状态=archived"
        assert metadata["数据行数"] == 0
        assert metadata["提示"] == "当前筛选条件下无数据"


def test_customer_import_reports_success_failure_rows_and_operation_log() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        content = _workbook_bytes(
            ["客户名称", "统一社会信用代码", "联系人", "联系电话", "邮箱"],
            [
                ["导入客户A", "91310000IMPORTA", "张三", "13800000000", "import-a@example.com"],
                ["导入客户B", "91310000IMPORTB", "李四", "bad-phone", "bad-email"],
            ],
        )
        response = client.post(
            "/api/customers/import",
            files={"file": ("customers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["imported"] == 1
        assert payload["failed"] == 1
        assert any(error["row"] == 3 and error["field"] == "contact_phone" for error in payload["errors"])
        assert client.get("/api/customers", params={"keyword": "IMPORTA"}).json()["total"] == 1

        db = SessionLocal()
        try:
            logs = db.query(OperationLog).filter(OperationLog.action == "customer_import").all()
            assert len(logs) == 1
            assert "succeeded" in logs[0].changes
        finally:
            db.close()


def test_customer_import_preview_reports_counts_warnings_and_stays_read_only() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        existing = client.post("/api/customers", json={"name": "Preview Existing", "credit_code": "91310000PREVIEWOLD"}).json()
        assert existing["name"] == "Preview Existing"

        content = _workbook_bytes(
            ["name", "credit_code", "contact_phone", "contact_email", "status"],
            [
                ["Preview Existing", "91310000PREVIEWOLD", "13800000000", "existing@example.com", "active"],
                ["Preview New", "91310000PREVIEWNEW", "13900000000", "new@example.com", "inactive"],
            ],
        )
        response = client.post(
            "/api/customers/import/preview",
            files={"file": ("customers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 2
        assert payload["valid_rows"] == 2
        assert payload["error_rows"] == 0
        assert payload["warning_rows"] == 1
        assert payload["warnings"][0]["row_number"] == 2
        assert payload["warnings"][0]["field"] == "name"
        assert payload["warnings"][0]["reason"]
        assert {row["action"] for row in payload["preview_rows"]} == {"update", "create"}

        db = SessionLocal()
        try:
            assert db.query(Customer).count() == 1
            assert db.query(Customer).filter(Customer.name == "Preview New").count() == 0
            assert db.query(OperationLog).filter(OperationLog.action == "customer_import").count() == 0
        finally:
            db.close()


def test_customer_import_preview_reports_duplicate_and_field_errors() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        content = _workbook_bytes(
            ["name", "credit_code", "contact_phone", "contact_email", "status"],
            [
                ["Preview Dup", "91310000PREVIEWDUP1", "13800000000", "dup1@example.com", "active"],
                ["Preview Dup", "91310000PREVIEWDUP2", "bad-phone", "bad-email", "broken"],
            ],
        )
        response = client.post(
            "/api/customers/import/preview",
            files={"file": ("customers.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 2
        assert payload["valid_rows"] == 1
        assert payload["error_rows"] == 1
        assert any(error["row_number"] == 3 and error["field"] == "name" and error["reason"] for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "contact_phone" for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "contact_email" for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "status" for error in payload["errors"])

        db = SessionLocal()
        try:
            assert db.query(Customer).filter(Customer.name == "Preview Dup").count() == 0
            assert db.query(OperationLog).filter(OperationLog.action == "customer_import").count() == 0
        finally:
            db.close()


def test_follow_up_import_matches_customer_and_reports_missing_customer() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "跟进导入客户"}).json()
        assert client.post("/api/rooms", json=_room_payload("FU-1801")).status_code == 200
        contract = client.post("/api/contracts", json=_contract_payload("DEMO_A-FU-IMPORT", "FU-1801", customer["id"])).json()
        content = _workbook_bytes(
            ["客户名称", "合同编号", "跟进类型", "发生时间", "跟进内容", "下次跟进日期", "负责人"],
            [
                ["跟进导入客户", contract["contract_no"], "电话", "2026-05-08 09:30:00", "确认基础资料", "2026-05-15", "Alice"],
                ["不存在客户", "", "电话", "2026-05-08", "无法匹配客户", "2026-05-16", "Bob"],
            ],
        )
        response = client.post(
            "/api/customers/follow-ups/import",
            files={"file": ("followups.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["imported"] == 1
        assert payload["failed"] == 1
        assert any(error["row"] == 3 and error["customer_name"] == "不存在客户" for error in payload["errors"])

        detail = client.get(f"/api/customers/{customer['id']}").json()
        assert detail["follow_ups"][0]["content"] == "确认基础资料"

        db = SessionLocal()
        try:
            assert db.query(OperationLog).filter(OperationLog.action == "followup_import").count() == 1
        finally:
            db.close()


def test_follow_up_import_preview_reports_missing_customer_date_and_required_errors_read_only() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer = client.post("/api/customers", json={"name": "Follow Preview Customer"}).json()
        assert customer["name"] == "Follow Preview Customer"

        content = _workbook_bytes(
            ["customer_name", "occurred_at", "content", "status"],
            [
                ["Follow Preview Customer", "2026-05-08 09:30:00", "valid follow up", "open"],
                ["Missing Preview Customer", "bad-date", "", "broken"],
            ],
        )
        response = client.post(
            "/api/customers/follow-ups/import/preview",
            files={"file": ("followups.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 2
        assert payload["valid_rows"] == 1
        assert payload["error_rows"] == 1
        assert payload["preview_rows"][0]["customer_name"] == "Follow Preview Customer"
        assert any(error["row_number"] == 3 and error["field"] == "customer_name" and error["reason"] for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "occurred_at" for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "content" for error in payload["errors"])
        assert any(error["row_number"] == 3 and error["field"] == "status" for error in payload["errors"])

        db = SessionLocal()
        try:
            assert db.query(CustomerFollowUp).count() == 0
            assert db.query(OperationLog).filter(OperationLog.action == "followup_import").count() == 0
        finally:
            db.close()


def test_import_templates_download_and_deposit_import_is_atomic() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer_template = _load_response_workbook(client.get("/api/customers/import/template"))
        assert "客户名称" in _headers(customer_template, row=1)

        follow_template = _load_response_workbook(client.get("/api/customers/follow-ups/import/template"))
        assert "跟进内容" in _headers(follow_template, row=1)

        deposit_template = _load_response_workbook(client.get("/api/contracts/deposits/import/template"))
        assert "应收金额" in _headers(deposit_template, row=1)

        customer = client.post("/api/customers", json={"name": "押金导入客户"}).json()
        assert client.post("/api/rooms", json=_room_payload("DEP-IMPORT-01")).status_code == 200
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-DEPOSIT-IMPORT-001", "DEP-IMPORT-01", customer["id"]),
        ).json()
        response = client.post(
            "/api/contracts/deposits/import",
            files={
                "file": (
                    "deposits.xlsx",
                    _workbook_bytes(
                        ["合同编号", "押金类型", "应收金额"],
                        [[contract["contract_no"], "租赁押金", 30000]],
                    ),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["imported"] == 1

        db = SessionLocal()
        try:
            imported = db.query(ContractDeposit).filter(ContractDeposit.contract_id == contract["id"]).one()
            assert imported.expected_amount == 30000
            audit = db.query(OperationLog).filter(OperationLog.action == "deposit_import").one()
            assert audit.scope_type == "building"
            assert audit.building_id is not None
        finally:
            db.close()
