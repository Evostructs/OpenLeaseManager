from __future__ import annotations

from datetime import date
import io

from fastapi import HTTPException, UploadFile
import pytest
from starlette.datastructures import Headers

from app.database import SessionLocal
from app.models import Attachment, Building, Contract, ContractDeposit, ReceivableStatement
from app.schemas import ContractCreate
from app.services import contracts as contracts_service
from app.services.attachments import _attachment_path, create_attachment
from app.services.contracts import clear_recycle_bin, create_contract, purge_contract, soft_delete_contract
from app.services.property_scope import SelectedPropertyScope


def _edit_scope(db) -> SelectedPropertyScope:
    building = db.query(Building).filter(Building.code == "DEMO_A").one()
    return SelectedPropertyScope(
        user_id=0,
        building_ids=(building.id,),
        building_codes=(building.code,),
        scope_type="building",
        scope_code=building.code,
        portfolio_id=None,
        access_level="edit",
        authorization_version="test",
    )


def _create_contract_with_attachment(db, suffix: str, *, soft_deleted: bool = True) -> tuple[Contract, Attachment]:
    room_no = f"PURGE-{suffix}"
    result = create_contract(
        db,
        ContractCreate(
            contract_no=f"PURGE-{suffix}",
            tower="南塔",
            room_no=room_no,
            tenant_name=f"清理测试-{suffix}",
            area=80,
            unit_price=4,
            start_date=date(2028, 1, 1),
            end_date=date(2028, 12, 31),
            room_items=[
                {
                    "tower": "南塔",
                    "room_no": room_no,
                    "area": 80,
                    "unit_price": 4,
                    "start_date": "2028-01-01",
                    "end_date": "2028-12-31",
                }
            ],
        ),
        "tester",
        scope=_edit_scope(db),
    )
    contract = db.get(Contract, result["id"])
    upload = UploadFile(
        file=io.BytesIO(b"purge-safety"),
        filename=f"{suffix}.txt",
        headers=Headers({"content-type": "text/plain"}),
    )
    attachment_result = create_attachment(
        db,
        scope=_edit_scope(db),
        entity_type="contract",
        entity_id=contract.id,
        upload=upload,
        category="其他",
        notes="",
        operator="tester",
    )
    attachment = db.get(Attachment, attachment_result["id"])
    if soft_deleted:
        soft_delete_contract(db, contract, "tester", scope=_edit_scope(db))
    return contract, attachment


def test_active_contract_cannot_bypass_recycle_bin() -> None:
    db = SessionLocal()
    try:
        contract, attachment = _create_contract_with_attachment(db, "ACTIVE", soft_deleted=False)
        file_path = _attachment_path(attachment.storage_path)

        with pytest.raises(HTTPException) as exc_info:
            purge_contract(db, contract, scope=_edit_scope(db))

        assert exc_info.value.status_code == 409
        assert db.get(Contract, contract.id) is not None
        assert db.get(Attachment, attachment.id) is not None
        assert file_path.exists()
    finally:
        db.close()


def test_purge_commit_failure_keeps_database_and_file(monkeypatch) -> None:
    db = SessionLocal()
    try:
        contract, attachment = _create_contract_with_attachment(db, "COMMIT-FAIL")
        contract_id = contract.id
        attachment_id = attachment.id
        file_path = _attachment_path(attachment.storage_path)
        assert file_path.exists()

        def fail_after_flush() -> None:
            db.flush()
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(db, "commit", fail_after_flush)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            purge_contract(db, contract, scope=_edit_scope(db))

        assert db.get(Contract, contract_id) is not None
        assert db.get(Attachment, attachment_id) is not None
        assert file_path.exists()
    finally:
        db.close()


def test_clear_recycle_scope_failure_is_atomic_and_keeps_all_files() -> None:
    db = SessionLocal()
    try:
        first_contract, first_attachment = _create_contract_with_attachment(db, "SCOPE-GOOD")
        second_contract, second_attachment = _create_contract_with_attachment(db, "SCOPE-BAD")
        first_path = _attachment_path(first_attachment.storage_path)
        second_path = _attachment_path(second_attachment.storage_path)

        second_attachment.scope_type = "portfolio"
        second_attachment.portfolio_id = None
        second_attachment.building_id = None
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            clear_recycle_bin(db, scope=_edit_scope(db))
        assert exc_info.value.status_code == 400
        assert db.get(Contract, first_contract.id) is not None
        assert db.get(Contract, second_contract.id) is not None
        assert db.get(Attachment, first_attachment.id) is not None
        assert db.get(Attachment, second_attachment.id) is not None
        assert first_path.exists()
        assert second_path.exists()
    finally:
        db.close()


def test_purge_reports_post_commit_file_cleanup_failure(monkeypatch) -> None:
    db = SessionLocal()
    try:
        contract, attachment = _create_contract_with_attachment(db, "FILE-FAIL")
        contract_id = contract.id
        attachment_id = attachment.id
        file_path = _attachment_path(attachment.storage_path)
        monkeypatch.setattr(contracts_service, "_unlink_committed_contract_files", lambda _paths: 1)

        result = purge_contract(db, contract, scope=_edit_scope(db))

        assert "cleanup_warning" in result
        assert db.get(Contract, contract_id) is None
        assert db.get(Attachment, attachment_id) is None
        assert file_path.exists()
    finally:
        db.close()


def test_purge_removes_deposit_attachment_without_orphan() -> None:
    db = SessionLocal()
    try:
        contract, _contract_attachment = _create_contract_with_attachment(db, "DEPOSIT", soft_deleted=False)
        deposit = ContractDeposit(
            contract_id=contract.id,
            customer_id=contract.customer_id,
            deposit_type="租赁保证金",
            expected_amount=1000,
            status="unpaid",
        )
        db.add(deposit)
        db.commit()
        upload = UploadFile(
            file=io.BytesIO(b"deposit-proof"),
            filename="deposit.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        result = create_attachment(
            db,
            scope=_edit_scope(db),
            entity_type="deposit",
            entity_id=deposit.id,
            upload=upload,
            category="押金凭证",
            notes="",
            operator="tester",
        )
        deposit_attachment = db.get(Attachment, result["id"])
        deposit_path = _attachment_path(deposit_attachment.storage_path)
        soft_delete_contract(db, contract, "tester", scope=_edit_scope(db))

        purge_contract(db, contract, scope=_edit_scope(db))

        assert db.get(Attachment, deposit_attachment.id) is None
        assert db.get(ContractDeposit, deposit.id) is None
        assert not deposit_path.exists()
    finally:
        db.close()


def test_frozen_statement_blocks_permanent_delete_and_keeps_files() -> None:
    db = SessionLocal()
    try:
        contract, attachment = _create_contract_with_attachment(db, "STATEMENT", soft_deleted=False)
        statement = ReceivableStatement(
            contract_id=contract.id,
            period_month="2028-01",
            rent=1000,
            property_fee=100,
            total_amount=1100,
            source="manual",
            operator="tester",
        )
        db.add(statement)
        db.commit()
        file_path = _attachment_path(attachment.storage_path)
        soft_delete_contract(db, contract, "tester", scope=_edit_scope(db))

        with pytest.raises(HTTPException) as exc_info:
            purge_contract(db, contract, scope=_edit_scope(db))

        assert exc_info.value.status_code == 409
        assert db.get(Contract, contract.id) is not None
        assert db.get(ReceivableStatement, statement.id) is not None
        assert db.get(Attachment, attachment.id) is not None
        assert file_path.exists()
    finally:
        db.close()
