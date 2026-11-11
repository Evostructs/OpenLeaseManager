from __future__ import annotations

from datetime import date
import io

from fastapi import HTTPException, UploadFile
import pytest
from starlette.datastructures import Headers

from app.core.config import get_settings
from app.core.crypto import encrypted_name, is_encrypted_file, public_name
from app.database import SessionLocal
from app.models import Building, Contract, ContractRoom, OperationLog, Room, Zone
from app.services import contract_files
from app.services.property_scope import SelectedPropertyScope


PDF_BYTES = b"%PDF-1.4\ntransaction-test\n%%EOF"


def _upload(filename: str, content: bytes = PDF_BYTES) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def _demo_a(db) -> Building:
    return db.query(Building).filter(Building.code == "DEMO_A").one()


def _edit_scope(db) -> SelectedPropertyScope:
    building = _demo_a(db)
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


def _building_edit_scope(db, code: str) -> SelectedPropertyScope:
    building = db.query(Building).filter(Building.code == code).one()
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


def _unmatched_source(filename: str) -> object:
    return get_settings().upload_dir / contract_files._scoped_unmatched_name("DEMO_A", filename)


def _seed_contract(
    db,
    contract_no: str,
    *,
    scope: str = "demo_a",
    pdf_path: str = "",
) -> Contract:
    demo_a = _demo_a(db)
    building_id = demo_a.id
    if scope == "other":
        other = db.query(Building).filter(Building.code == "PDF_SCOPE_OTHER").one_or_none()
        if other is None:
            other = Building(
                code="PDF_SCOPE_OTHER",
                name="PDF 范围测试乙楼",
                timezone="Asia/Shanghai",
                currency="CNY",
                ingestion_mode="detail",
                is_provisional=False,
                is_active=True,
            )
            db.add(other)
            db.flush()
        building_id = other.id
        zone = (
            db.query(Zone)
            .filter(Zone.building_id == building_id, Zone.code == "SOUTH_OFFICE")
            .one_or_none()
        )
        if zone is None:
            zone = Zone(
                building_id=building_id,
                code="SOUTH_OFFICE",
                name="PDF 范围测试乙楼南塔",
                zone_type="office",
                valid_from=date(2026, 1, 1),
                is_active=True,
            )
            db.add(zone)
            db.flush()
    else:
        zone = (
            db.query(Zone)
            .filter(Zone.building_id == building_id, Zone.code == "SOUTH_OFFICE")
            .one()
        )

    room = Room(
        building_id=building_id,
        zone_id=zone.id,
        tower="南塔",
        floor="",
        room_no=f"R-{contract_no}",
        room_type="办公",
        area=100,
        is_leasable=True,
        is_deleted=False,
    )
    db.add(room)
    db.flush()

    contract = Contract(
        contract_no=contract_no,
        building_id=building_id,
        tower="南塔",
        room_no=f"R-{contract_no}",
        tenant_name=f"客户-{contract_no}",
        area=100,
        unit_price=4,
        monthly_rent=12000,
        start_date=date(2027, 1, 1),
        end_date=date(2027, 12, 31),
        pdf_path=pdf_path,
        is_deleted=False,
    )
    contract.room_items.append(
        ContractRoom(
            building_id=building_id,
            room_id=room.id,
            tower="南塔",
            room_no=f"R-{contract_no}",
            area=100,
            unit_price=4,
            monthly_rent=12000,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
        )
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


def _stored_name(filename: str) -> str:
    return encrypted_name(filename) if get_settings().data_encryption_enabled else filename


def _assert_no_transaction_artifacts() -> None:
    upload_dir = get_settings().upload_dir
    assert not list(upload_dir.glob(".pdf-stage-*"))
    assert not list(upload_dir.glob(".pdf-rollback-*"))


def _assert_no_new_contract_storage() -> None:
    assert not list(
        get_settings().upload_dir.glob(
            f"{contract_files.CONTRACT_PDF_STORAGE_PREFIX}*"
        )
    )


def test_save_rejects_null_legacy_scope_without_writing() -> None:
    db = SessionLocal()
    try:
        contract = _seed_contract(db, "PDF-NULL-SCOPE")
        stored_building_id = int(contract.building_id)
        contract.building_id = None

        with pytest.raises(HTTPException) as exc_info:
            contract_files.save_contract_pdf(
                db,
                contract,
                _upload("PDF-NULL-SCOPE.pdf"),
                "tester",
                scope=_edit_scope(db),
            )

        db.rollback()
        db.expire_all()
        stored = db.get(Contract, contract.id)
        relation = (
            db.query(ContractRoom)
            .filter(ContractRoom.contract_id == contract.id)
            .one()
        )
        assert exc_info.value.status_code == 409
        assert stored.building_id == stored_building_id
        assert relation.building_id == stored_building_id
        assert db.query(OperationLog).count() == 0
        assert stored.pdf_path in (None, "")
        assert not list(get_settings().upload_dir.iterdir())
        _assert_no_transaction_artifacts()
    finally:
        db.close()


def test_save_rejects_other_building_before_any_file_write(monkeypatch) -> None:
    db = SessionLocal()
    writer_calls = 0
    try:
        contract = _seed_contract(db, "PDF-OTHER-SCOPE", scope="other")

        def unexpected_writer(*_args, **_kwargs):
            nonlocal writer_calls
            writer_calls += 1
            raise AssertionError("scope preflight must run before file writing")

        monkeypatch.setattr(contract_files, "_write_limited_upload", unexpected_writer)
        with pytest.raises(HTTPException):
            contract_files.save_contract_pdf(
                db,
                contract,
                _upload("PDF-OTHER-SCOPE.pdf"),
                "tester",
                scope=_edit_scope(db),
            )

        assert writer_calls == 0
        db.expire_all()
        assert db.get(Contract, contract.id).pdf_path in (None, "")
        assert db.query(OperationLog).count() == 0
        assert not list(get_settings().upload_dir.iterdir())
    finally:
        db.close()


def test_batch_treats_other_building_contract_number_like_an_ordinary_unmatched_file() -> None:
    db = SessionLocal()
    try:
        other_contract = _seed_contract(db, "PDF-BATCH-OTHER", scope="other")
        result = contract_files.batch_upload_pdfs(
            db,
            [
                _upload("PDF-BATCH-OTHER.pdf"),
                _upload("PDF-ORDINARY-UNMATCHED.pdf"),
            ],
            "tester",
            scope=_edit_scope(db),
        )

        assert result["matched_count"] == 0
        assert result["unmatched_count"] == 2
        assert {item["filename"] for item in result["unmatched"]} == {
            "PDF-BATCH-OTHER.pdf",
            "PDF-ORDINARY-UNMATCHED.pdf",
        }
        db.expire_all()
        assert db.get(Contract, other_contract.id).pdf_path in (None, "")
        assert db.query(OperationLog).count() == 0
        assert {
            item["filename"]
            for item in contract_files.list_unmatched_pdfs(scope=_edit_scope(db))
        } == {"PDF-BATCH-OTHER.pdf", "PDF-ORDINARY-UNMATCHED.pdf"}
        assert len(list(get_settings().upload_dir.iterdir())) == 2
        assert all(
            path.name.startswith(contract_files._unmatched_scope_prefix("DEMO_A"))
            for path in get_settings().upload_dir.iterdir()
        )
    finally:
        db.close()


def test_other_detail_building_supports_single_batch_and_manual_pdf_writes() -> None:
    db = SessionLocal()
    try:
        contract = _seed_contract(db, "PDF-DETAIL-OTHER", scope="other")
        scope = _building_edit_scope(db, "PDF_SCOPE_OTHER")

        saved = contract_files.save_contract_pdf(
            db,
            contract,
            _upload("PDF-DETAIL-OTHER-single.pdf"),
            "tester",
            scope=scope,
        )
        assert saved["message"] == "上传成功"

        batch = contract_files.batch_upload_pdfs(
            db,
            [
                _upload("PDF-DETAIL-OTHER-batch.pdf"),
                _upload("PDF-DETAIL-MANUAL.pdf"),
            ],
            "tester",
            scope=scope,
        )
        assert batch["matched_count"] == 1
        assert batch["unmatched_count"] == 1

        matched = contract_files.manual_match_pdf(
            db,
            "PDF-DETAIL-MANUAL.pdf",
            contract,
            "tester",
            scope=scope,
        )
        assert matched["contract_no"] == contract.contract_no

        db.expire_all()
        stored = db.get(Contract, contract.id)
        assert stored.pdf_path
        logs = (
            db.query(OperationLog)
            .filter(OperationLog.contract_id == contract.id, OperationLog.action == "update")
            .all()
        )
        assert len(logs) == 3
        assert all(
            row.scope_type == "building"
            and row.building_id == contract.building_id
            and row.portfolio_id is None
            for row in logs
        )
        assert not contract_files.list_unmatched_pdfs(scope=scope)
        _assert_no_transaction_artifacts()
    finally:
        db.close()


def test_batch_rejects_duplicate_filenames_before_writing() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc_info:
            contract_files.batch_upload_pdfs(
                db,
                [_upload("DUPLICATE.pdf", b"first"), _upload("duplicate.PDF", b"second")],
                "tester",
                scope=_edit_scope(db),
            )

        assert exc_info.value.status_code == 400
        assert not list(get_settings().upload_dir.iterdir())
        assert db.query(OperationLog).count() == 0
    finally:
        db.close()


def test_batch_rejects_names_that_collapse_to_the_same_safe_filename() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc_info:
            contract_files.batch_upload_pdfs(
                db,
                [_upload("same name.pdf", b"first"), _upload("same@name.pdf", b"second")],
                "tester",
                scope=_edit_scope(db),
            )

        assert exc_info.value.status_code == 400
        assert not list(get_settings().upload_dir.iterdir())
        assert db.query(OperationLog).count() == 0
    finally:
        db.close()


def test_same_display_filename_gets_distinct_contract_storage_keys() -> None:
    db = SessionLocal()
    try:
        first = _seed_contract(db, "PDF-SAME-NAME-1")
        second = _seed_contract(db, "PDF-SAME-NAME-2")

        first_result = contract_files.save_contract_pdf(
            db,
            first,
            _upload("signed-copy.pdf", b"first-contract"),
            "tester",
            scope=_edit_scope(db),
        )
        second_result = contract_files.save_contract_pdf(
            db,
            second,
            _upload("signed-copy.pdf", b"second-contract"),
            "tester",
            scope=_edit_scope(db),
        )

        db.expire_all()
        stored_first = db.get(Contract, first.id)
        stored_second = db.get(Contract, second.id)
        assert first_result["filename"] == second_result["filename"] == "signed-copy.pdf"
        assert stored_first.pdf_path != stored_second.pdf_path
        assert contract_files._contract_pdf_display_name(stored_first.pdf_path) == "signed-copy.pdf"
        assert contract_files._contract_pdf_display_name(stored_second.pdf_path) == "signed-copy.pdf"
        assert f"__c{first.id}__" in public_name(stored_first.pdf_path)
        assert f"__c{second.id}__" in public_name(stored_second.pdf_path)
        assert (get_settings().upload_dir / stored_first.pdf_path).is_file()
        assert (get_settings().upload_dir / stored_second.pdf_path).is_file()
    finally:
        db.close()


def test_same_filename_storage_allocation_is_isolated_across_buildings() -> None:
    db = SessionLocal()
    try:
        demo_a_contract = _seed_contract(db, "PDF-CROSS-BUILDING-DEMO_A")
        other_contract = _seed_contract(db, "PDF-CROSS-BUILDING-OTHER", scope="other")

        demo_a_name, demo_a_path, demo_a_display = contract_files._allocate_contract_pdf_storage(
            db,
            building_id=int(demo_a_contract.building_id),
            contract_id=int(demo_a_contract.id),
            filename="same-signed-copy.pdf",
        )
        other_name, other_path, other_display = contract_files._allocate_contract_pdf_storage(
            db,
            building_id=int(other_contract.building_id),
            contract_id=int(other_contract.id),
            filename="same-signed-copy.pdf",
        )

        assert demo_a_display == other_display == "same-signed-copy.pdf"
        assert (demo_a_name, demo_a_path) != (other_name, other_path)
        assert f"__b{demo_a_contract.building_id}__c{demo_a_contract.id}__" in public_name(demo_a_name)
        assert f"__b{other_contract.building_id}__c{other_contract.id}__" in public_name(other_name)
    finally:
        db.close()


def test_contract_storage_allocator_rejects_a_path_referenced_by_another_contract(
    monkeypatch,
) -> None:
    db = SessionLocal()
    try:
        target_contract = _seed_contract(db, "PDF-STORAGE-TARGET")
        other_contract = _seed_contract(db, "PDF-STORAGE-OTHER")
        fixed_hex = "a" * 32
        display_name = "same-signed-copy.pdf"
        occupied_leaf = (
            f"{contract_files.CONTRACT_PDF_STORAGE_PREFIX}"
            f"b{target_contract.building_id}__c{target_contract.id}__{fixed_hex}__{display_name}"
        )
        occupied_name = _stored_name(occupied_leaf)
        other_contract.pdf_path = occupied_name
        db.commit()

        class FixedUuid:
            hex = fixed_hex

        monkeypatch.setattr(contract_files, "uuid4", lambda: FixedUuid())
        with pytest.raises(HTTPException) as exc_info:
            contract_files._allocate_contract_pdf_storage(
                db,
                building_id=int(target_contract.building_id),
                contract_id=int(target_contract.id),
                filename=display_name,
            )

        assert exc_info.value.status_code == 409
        assert db.get(Contract, other_contract.id).pdf_path == occupied_name
        assert not (get_settings().upload_dir / occupied_name).exists()
    finally:
        db.close()


def test_save_log_failure_keeps_database_and_existing_pdf_unchanged(monkeypatch) -> None:
    db = SessionLocal()
    filename = "PDF-SAVE-LOG.pdf"
    stored_name = _stored_name(filename)
    target = get_settings().upload_dir / stored_name
    old_bytes = b"existing-save-pdf"
    try:
        contract = _seed_contract(db, "PDF-SAVE-LOG", pdf_path=stored_name)
        target.write_bytes(old_bytes)

        def fail_log(*_args, **_kwargs):
            raise RuntimeError("injected log failure")

        monkeypatch.setattr(contract_files, "log_action", fail_log)
        with pytest.raises(RuntimeError, match="injected log failure"):
            contract_files.save_contract_pdf(
                db,
                contract,
                _upload(filename),
                "tester",
                scope=_edit_scope(db),
            )

        db.expire_all()
        assert db.get(Contract, contract.id).pdf_path == stored_name
        assert db.query(Room).filter(Room.room_no == "R-PDF-SAVE-LOG").count() == 1
        assert db.query(OperationLog).count() == 0
        assert target.read_bytes() == old_bytes
        _assert_no_new_contract_storage()
        _assert_no_transaction_artifacts()
    finally:
        db.close()


def test_save_commit_failure_restores_existing_pdf_and_database(monkeypatch) -> None:
    db = SessionLocal()
    filename = "PDF-SAVE-COMMIT.pdf"
    stored_name = _stored_name(filename)
    target = get_settings().upload_dir / stored_name
    old_bytes = b"existing-save-before-commit"
    try:
        contract = _seed_contract(db, "PDF-SAVE-COMMIT", pdf_path=stored_name)
        target.write_bytes(old_bytes)

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            contract_files.save_contract_pdf(
                db,
                contract,
                _upload(filename),
                "tester",
                scope=_edit_scope(db),
            )

        db.expire_all()
        assert db.get(Contract, contract.id).pdf_path == stored_name
        assert db.query(Room).filter(Room.room_no == "R-PDF-SAVE-COMMIT").count() == 1
        assert db.query(OperationLog).count() == 0
        assert target.read_bytes() == old_bytes
        _assert_no_new_contract_storage()
        _assert_no_transaction_artifacts()
    finally:
        db.close()


@pytest.mark.parametrize("failure_mode", ("log", "commit"))
def test_batch_failure_keeps_all_database_rows_and_existing_pdfs(
    monkeypatch,
    failure_mode: str,
) -> None:
    db = SessionLocal()
    filenames = ("PDF-BATCH-TX-1.pdf", "PDF-BATCH-TX-2.pdf")
    stored_names = tuple(_stored_name(name) for name in filenames)
    targets = tuple(get_settings().upload_dir / name for name in stored_names)
    old_contents = (b"old-batch-one", b"old-batch-two")
    try:
        contracts = (
            _seed_contract(db, "PDF-BATCH-TX-1", pdf_path=stored_names[0]),
            _seed_contract(db, "PDF-BATCH-TX-2", pdf_path=stored_names[1]),
        )
        for target, content in zip(targets, old_contents):
            target.write_bytes(content)

        if failure_mode == "log":
            original_log_action = contract_files.log_action
            calls = 0

            def fail_second_log(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("injected batch log failure")
                return original_log_action(*args, **kwargs)

            monkeypatch.setattr(contract_files, "log_action", fail_second_log)
            expected_error = "injected batch log failure"
        else:
            def fail_commit():
                raise RuntimeError("injected batch commit failure")

            monkeypatch.setattr(db, "commit", fail_commit)
            expected_error = "injected batch commit failure"

        with pytest.raises(RuntimeError, match=expected_error):
            contract_files.batch_upload_pdfs(
                db,
                [_upload(filenames[0]), _upload(filenames[1])],
                "tester",
                scope=_edit_scope(db),
            )

        db.expire_all()
        assert [db.get(Contract, item.id).pdf_path for item in contracts] == list(stored_names)
        assert (
            db.query(Room)
            .filter(Room.room_no.in_(("R-PDF-BATCH-TX-1", "R-PDF-BATCH-TX-2")))
            .count()
            == 2
        )
        assert db.query(OperationLog).count() == 0
        assert [target.read_bytes() for target in targets] == list(old_contents)
        _assert_no_new_contract_storage()
        _assert_no_transaction_artifacts()
    finally:
        db.close()


@pytest.mark.parametrize("failure_mode", ("log", "commit"))
def test_manual_match_failure_restores_unmatched_source_and_existing_pdf(
    monkeypatch,
    failure_mode: str,
) -> None:
    db = SessionLocal()
    filename = "PDF-MANUAL-TX.pdf"
    stored_name = _stored_name(filename)
    source = _unmatched_source(filename)
    target = get_settings().upload_dir / stored_name
    source_bytes = b"unmatched-source-pdf"
    target_bytes = b"existing-manual-pdf"
    try:
        contract = _seed_contract(db, "PDF-MANUAL-TX", pdf_path=stored_name)
        source.write_bytes(source_bytes)
        target.write_bytes(target_bytes)

        if failure_mode == "log":
            def fail_log(*_args, **_kwargs):
                raise RuntimeError("injected manual log failure")

            monkeypatch.setattr(contract_files, "log_action", fail_log)
            expected_error = "injected manual log failure"
        else:
            def fail_commit():
                raise RuntimeError("injected manual commit failure")

            monkeypatch.setattr(db, "commit", fail_commit)
            expected_error = "injected manual commit failure"

        with pytest.raises(RuntimeError, match=expected_error):
            contract_files.manual_match_pdf(
                db,
                filename,
                contract,
                "tester",
                scope=_edit_scope(db),
            )

        db.expire_all()
        assert db.get(Contract, contract.id).pdf_path == stored_name
        assert db.query(Room).filter(Room.room_no == "R-PDF-MANUAL-TX").count() == 1
        assert db.query(OperationLog).count() == 0
        assert source.read_bytes() == source_bytes
        assert target.read_bytes() == target_bytes
        _assert_no_new_contract_storage()
        _assert_no_transaction_artifacts()
    finally:
        db.close()


def test_manual_match_success_moves_source_and_commits_scoped_log() -> None:
    db = SessionLocal()
    filename = "PDF-MANUAL-SUCCESS.pdf"
    source = _unmatched_source(filename)
    try:
        contract = _seed_contract(db, "PDF-MANUAL-SUCCESS", scope="demo_a")
        source.write_bytes(PDF_BYTES)

        result = contract_files.manual_match_pdf(
            db,
            filename,
            contract,
            "tester",
            scope=_edit_scope(db),
        )

        db.expire_all()
        stored = db.get(Contract, contract.id)
        target = get_settings().upload_dir / stored.pdf_path
        log = (
            db.query(OperationLog)
            .filter(OperationLog.contract_id == contract.id, OperationLog.action == "update")
            .one()
        )
        assert result["contract_no"] == "PDF-MANUAL-SUCCESS"
        assert stored.building_id == _demo_a(db).id
        assert (log.scope_type, log.building_id) == ("building", _demo_a(db).id)
        assert not source.exists()
        assert target.exists()
        assert is_encrypted_file(target)
        _assert_no_transaction_artifacts()
    finally:
        db.close()


def test_manual_match_rejects_other_building_before_copying_or_moving(monkeypatch) -> None:
    db = SessionLocal()
    filename = "PDF-MANUAL-OTHER.pdf"
    source = _unmatched_source(filename)
    source_bytes = b"manual-other-source"
    encryption_calls = 0
    try:
        contract = _seed_contract(db, "PDF-MANUAL-OTHER", scope="other")
        source.write_bytes(source_bytes)

        def unexpected_encrypt(*_args, **_kwargs):
            nonlocal encryption_calls
            encryption_calls += 1
            raise AssertionError("scope preflight must run before file conversion")

        monkeypatch.setattr(contract_files, "encrypt_file", unexpected_encrypt)
        with pytest.raises(HTTPException):
            contract_files.manual_match_pdf(
                db,
                filename,
                contract,
                "tester",
                scope=_edit_scope(db),
            )

        assert encryption_calls == 0
        db.expire_all()
        assert db.get(Contract, contract.id).pdf_path in (None, "")
        assert db.query(OperationLog).count() == 0
        assert source.read_bytes() == source_bytes
        assert not (get_settings().upload_dir / _stored_name(filename)).exists()
        _assert_no_transaction_artifacts()
    finally:
        db.close()
