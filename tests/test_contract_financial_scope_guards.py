from __future__ import annotations

from datetime import date
import io

from fastapi.testclient import TestClient
import openpyxl

from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    ContractDeposit,
    ContractRoom,
    OperationLog,
    ReceivablePayment,
    ReceivableStatement,
    Room,
    User,
    UserScopeGrant,
    Zone,
)
from app.services.contracts import get_dunning_ledger, get_invoice_ledger, get_monthly_receivables


MONTH = "2026-08"


def _login_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    ).status_code == 200


def _contract_payload(contract_no: str, room_no: str) -> dict:
    return {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"客户-{contract_no}",
        "area": 100,
        "unit_price": 4,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }


def _seed_other_building_financials() -> tuple[int, int, int]:
    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "FINANCE_B").first()
        if building is None:
            building = Building(
                code="FINANCE_B",
                name="财务范围乙楼",
                is_active=True,
                ingestion_mode="detail",
            )
            db.add(building)
            db.flush()
        admin = db.query(User).filter(User.username == "admin").one()
        db.add(
            UserScopeGrant(
                user_id=admin.id,
                scope_type="building",
                building_id=building.id,
                portfolio_id=None,
                access_level="edit",
                include_future_buildings=False,
                valid_from=date(2026, 1, 1),
            )
        )
        zone = Zone(
            building_id=building.id,
            code="FINANCE_B_OFFICE",
            name="乙楼办公",
            zone_type="office",
            valid_from=date(2026, 1, 1),
            is_active=True,
        )
        db.add(zone)
        db.flush()
        room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="乙塔",
            floor="12",
            room_no="FB-1201",
            room_type="办公",
            area=100,
            is_leasable=True,
        )
        db.add(room)
        db.flush()
        contract = Contract(
            contract_no="FINANCE-B-001",
            building_id=building.id,
            tower="乙塔",
            room_no="FB-1201",
            tenant_name="乙楼财务客户",
            area=100,
            unit_price=4,
            monthly_rent=12166.67,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            is_deleted=False,
        )
        contract.room_items.append(
            ContractRoom(
                building_id=building.id,
                room_id=room.id,
                tower="乙塔",
                room_no="FB-1201",
                area=100,
                unit_price=4,
                monthly_rent=12166.67,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
        )
        db.add(contract)
        db.flush()
        payment = ReceivablePayment(
            contract_id=contract.id,
            period_month=MONTH,
            payment_date=date(2026, 8, 2),
            amount=2222,
            allocation_type="收款",
            payment_method="转账",
            reference_no="FIN-B-PAY",
            source="manual",
            operator="seed",
        )
        deposit = ContractDeposit(
            contract_id=contract.id,
            customer_id=None,
            deposit_type="租赁押金",
            expected_amount=22000,
            received_amount=0,
            refunded_amount=0,
            offset_amount=0,
            status="unpaid",
            due_date=date(2026, 8, 1),
            notes="乙楼押金",
        )
        db.add_all([payment, deposit])
        db.commit()
        return int(contract.id), int(building.id), int(payment.id)
    finally:
        db.close()


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["合同编号", "押金类型", "应收金额"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _workbook_values(response) -> set[str]:
    assert response.status_code == 200, response.text
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)
    return {
        str(value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if value not in (None, "")
    }


def test_contract_financial_document_and_deposit_routes_are_building_scoped() -> None:
    with TestClient(app) as client:
        _login_admin(client)
        demo_a_response = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-FINANCE-001", "FIN-1201"),
        )
        assert demo_a_response.status_code == 200, demo_a_response.text
        demo_a_id = int(demo_a_response.json()["id"])
        other_id, other_building_id, other_payment_id = _seed_other_building_financials()

        demo_a_payment = client.post(
            f"/api/contracts/receivables/monthly/{demo_a_id}/payments",
            json={
                "month": MONTH,
                "amount": 1111,
                "payment_date": "2026-08-01",
                "allocation_type": "收款",
                "reference_no": "DEMO_A-PAY",
            },
        )
        assert demo_a_payment.status_code == 200, demo_a_payment.text
        assert {row["contract_id"] for row in demo_a_payment.json()["rows"]} == {demo_a_id}
        assert client.post(
            f"/api/contracts/{demo_a_id}/deposits",
            json={"deposit_type": "租赁押金", "expected_amount": 11000},
        ).status_code == 200

        default_monthly = client.get(
            "/api/contracts/receivables/monthly",
            params={"month": MONTH},
        ).json()
        other_monthly = client.get(
            "/api/contracts/receivables/monthly",
            params={"month": MONTH, "building": "FINANCE_B"},
        ).json()
        assert {row["contract_id"] for row in default_monthly["rows"]} == {demo_a_id}
        assert {row["contract_id"] for row in other_monthly["rows"]} == {other_id}

        default_invoices = client.get(
            "/api/contracts/receivables/invoices",
            params={"month": MONTH},
        ).json()
        other_invoices = client.get(
            "/api/contracts/receivables/invoices",
            params={"month": MONTH, "building": "FINANCE_B"},
        ).json()
        assert {row["contract_id"] for row in default_invoices["rows"]} == {demo_a_id}
        assert {row["contract_id"] for row in other_invoices["rows"]} == {other_id}

        default_dunning = client.get(
            "/api/contracts/receivables/dunning",
            params={"month": MONTH, "focus": "false"},
        ).json()
        other_dunning = client.get(
            "/api/contracts/receivables/dunning",
            params={"month": MONTH, "focus": "false", "building": "FINANCE_B"},
        ).json()
        assert {row["contract_id"] for row in default_dunning["rows"]} == {demo_a_id}
        assert {row["contract_id"] for row in other_dunning["rows"]} == {other_id}

        assert "DEMO_A-FINANCE-001" in _workbook_values(
            client.get("/api/contracts/receivables/invoices/export", params={"month": MONTH})
        )
        other_invoice_values = _workbook_values(
            client.get(
                "/api/contracts/receivables/invoices/export",
                params={"month": MONTH, "building": "FINANCE_B"},
            )
        )
        assert "FINANCE-B-001" in other_invoice_values
        assert "DEMO_A-FINANCE-001" not in other_invoice_values
        assert "DEMO_A-FINANCE-001" in _workbook_values(
            client.get(
                "/api/contracts/receivables/dunning/export",
                params={"month": MONTH, "focus": "false"},
            )
        )
        other_dunning_values = _workbook_values(
            client.get(
                "/api/contracts/receivables/dunning/export",
                params={"month": MONTH, "focus": "false", "building": "FINANCE_B"},
            )
        )
        assert "FINANCE-B-001" in other_dunning_values
        assert "DEMO_A-FINANCE-001" not in other_dunning_values

        generated = client.post(f"/api/contracts/receivables/statements/{MONTH}/generate")
        assert generated.status_code == 200, generated.text
        other_generated = client.post(
            f"/api/contracts/receivables/statements/{MONTH}/generate",
            params={"building": "FINANCE_B"},
        )
        assert other_generated.status_code == 200, other_generated.text
        db = SessionLocal()
        try:
            statement_contract_ids = {
                row.contract_id
                for row in db.query(ReceivableStatement).filter(ReceivableStatement.period_month == MONTH).all()
            }
            assert statement_contract_ids == {demo_a_id, other_id}
        finally:
            db.close()

        assert client.delete(f"/api/contracts/receivables/payments/{other_payment_id}").status_code == 404
        db = SessionLocal()
        try:
            assert db.get(ReceivablePayment, other_payment_id) is not None
        finally:
            db.close()

        valid_document = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "payment_notice", "contract_id": demo_a_id, "month": MONTH},
        )
        assert valid_document.status_code == 200, valid_document.text
        assert client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "payment_notice", "contract_id": other_id, "month": MONTH},
        ).status_code == 404
        hidden_payment_document = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "receipt", "payment_id": other_payment_id},
        )
        missing_payment_document = client.post(
            "/api/contracts/documents/generate",
            json={"document_type": "receipt", "payment_id": 999999999},
        )
        assert hidden_payment_document.status_code == 404
        assert missing_payment_document.status_code == 404
        assert hidden_payment_document.json() == missing_payment_document.json()
        other_document = client.post(
            "/api/contracts/documents/generate",
            params={"building": "FINANCE_B"},
            json={"document_type": "payment_notice", "contract_id": other_id, "month": MONTH},
        )
        assert other_document.status_code == 200, other_document.text

        default_deposits = _workbook_values(client.get("/api/contracts/deposits/export"))
        other_deposits = _workbook_values(
            client.get("/api/contracts/deposits/export", params={"building": "FINANCE_B"})
        )
        assert "DEMO_A-FINANCE-001" in default_deposits
        assert "FINANCE-B-001" not in default_deposits
        assert "FINANCE-B-001" in other_deposits
        assert "DEMO_A-FINANCE-001" not in other_deposits

        default_contracts = _workbook_values(client.get("/api/contracts/export/excel"))
        other_contracts = _workbook_values(
            client.get("/api/contracts/export/excel", params={"building": "FINANCE_B"})
        )
        assert "DEMO_A-FINANCE-001" in default_contracts
        assert "FINANCE-B-001" not in default_contracts
        assert "FINANCE-B-001" in other_contracts
        assert "DEMO_A-FINANCE-001" not in other_contracts

        assert any(
            "DEMO_A" in value
            for value in _workbook_values(client.get("/api/contracts/export/template"))
        )
        other_contract_template = client.get(
            "/api/contracts/export/template",
            params={"building": "FINANCE_B"},
        )
        assert other_contract_template.status_code == 200, other_contract_template.text
        other_contract_template_values = _workbook_values(other_contract_template)
        assert "FINANCE_B-2026-001" in other_contract_template_values
        assert not any("DEMO_A" in value for value in other_contract_template_values)
        assert any(
            "DEMO_A" in value
            for value in _workbook_values(client.get("/api/contracts/deposits/import/template"))
        )
        other_deposit_template = client.get(
            "/api/contracts/deposits/import/template",
            params={"building": "FINANCE_B"},
        )
        assert other_deposit_template.status_code == 200, other_deposit_template.text
        other_deposit_template_values = _workbook_values(other_deposit_template)
        assert "FINANCE_B-2026-001" in other_deposit_template_values
        assert not any("DEMO_A" in value for value in other_deposit_template_values)

        db = SessionLocal()
        try:
            before_import_count = db.query(ContractDeposit).count()
        finally:
            db.close()
        mixed_import = client.post(
            "/api/contracts/deposits/import",
            files={
                "file": (
                    "mixed.xlsx",
                    _workbook_bytes(
                        [
                            ["DEMO_A-FINANCE-001", "租赁押金", 1000],
                            ["FINANCE-B-001", "租赁押金", 2000],
                        ]
                    ),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert mixed_import.status_code == 400
        missing_import = client.post(
            "/api/contracts/deposits/import",
            files={
                "file": (
                    "missing.xlsx",
                    _workbook_bytes(
                        [
                            ["DEMO_A-FINANCE-001", "租赁押金", 1000],
                            ["NO-SUCH-CONTRACT", "租赁押金", 2000],
                        ]
                    ),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert missing_import.status_code == 400
        db = SessionLocal()
        try:
            assert db.query(ContractDeposit).count() == before_import_count
        finally:
            db.close()

        valid_import = client.post(
            "/api/contracts/deposits/import",
            files={
                "file": (
                    "valid.xlsx",
                    _workbook_bytes([["DEMO_A-FINANCE-001", "租赁押金", 33000]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert valid_import.status_code == 200, valid_import.text
        assert valid_import.json()["imported"] == 1
        other_import = client.post(
            "/api/contracts/deposits/import",
            params={"building": "FINANCE_B"},
            files={
                "file": (
                    "other.xlsx",
                    _workbook_bytes([["FINANCE-B-001", "租赁押金", 44000]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert other_import.status_code == 200, other_import.text
        assert other_import.json()["imported"] == 1

        db = SessionLocal()
        try:
            assert get_monthly_receivables(db, MONTH, building_ids=[])["count"] == 0
            assert get_invoice_ledger(db, MONTH, building_ids=[])["count"] == 0
            assert get_dunning_ledger(db, MONTH, focus=False, building_ids=[])["count"] == 0
            demo_a_building_id = int(db.query(Building).filter(Building.code == "DEMO_A").one().id)
            statement_logs = db.query(OperationLog).filter(OperationLog.action == "statement_generate").all()
            document_logs = db.query(OperationLog).filter(OperationLog.action == "document_generate").all()
            deposit_logs = db.query(OperationLog).filter(OperationLog.action == "deposit_import").all()
            assert {row.building_id for row in statement_logs} == {demo_a_building_id, other_building_id}
            assert {row.building_id for row in document_logs} == {demo_a_building_id, other_building_id}
            assert {row.building_id for row in deposit_logs} == {demo_a_building_id, other_building_id}
            export_logs = db.query(OperationLog).filter(OperationLog.action == "export").all()
            assert export_logs
            assert all(log.scope_type == "building" and log.building_id is not None for log in export_logs)
            assert {log.building_id for log in export_logs} == {demo_a_building_id, other_building_id}
        finally:
            db.close()
