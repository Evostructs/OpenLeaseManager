from __future__ import annotations

from datetime import date
import io
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
import openpyxl
import pytest

from app.core.config import get_settings
from app.core.crypto import decrypt_file, is_encrypted_file
from app.database import SessionLocal
from app.main import app
from app.models import Contract
from app.services.backup import create_backup_archive


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
        "tower": "A座",
        "room_no": room_no,
        "tenant_name": f"测试客户{contract_no}",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "property_fee_rate": 28,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    payload.update(overrides)
    return payload


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

        created = client.post("/api/contracts", json=_contract_payload("LEASE-CRUD-001", "CRUD01"))
        assert created.status_code == 200
        contract_id = created.json()["id"]

        updated = client.put(f"/api/contracts/{contract_id}", json={"tenant_name": "更新客户"})
        assert updated.status_code == 200
        assert updated.json()["tenant_name"] == "更新客户"

        detail = client.get(f"/api/contracts/{contract_id}")
        assert detail.status_code == 200
        assert detail.json()["tenant_name"] == "更新客户"

        deleted = client.delete(f"/api/contracts/{contract_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/contracts/{contract_id}").status_code == 404

        restored = client.post(f"/api/contracts/recycle/restore/{contract_id}")
        assert restored.status_code == 200
        assert client.get(f"/api/contracts/{contract_id}").status_code == 200


def test_integrity_reports_active_contracts_without_pdf() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "LEASE-T-001",
                "tower": "A座",
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

        report = client.get("/api/system/integrity-check")
        assert report.status_code == 200
        issue_types = {issue["type"] for issue in report.json()["issues"]}
        assert "missing_contract_pdfs" in issue_types
        assert "missing_room_reference" in issue_types


def test_contract_list_sorts_before_pagination() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        for index, rent in enumerate([1000, 3000, 2000], start=1):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"LEASE-SORT-{index}",
                    "tower": "A座",
                    "room_no": f"S{index:04d}",
                    "tenant_name": f"排序客户{index}",
                    "area": 100,
                    "unit_price": 4,
                    "monthly_rent": rent,
                    "property_fee_rate": 28,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
            )
            assert response.status_code == 200

        response = client.get("/api/contracts?page=1&page_size=2&keyword=LEASE-SORT&sort_by=monthly_rent&sort_dir=desc")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [row["monthly_rent"] for row in rows] == [3000, 2000]


def test_contract_rejects_negative_financial_fields() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        base_payload = {
            "contract_no": "LEASE-NEGATIVE-001",
            "tower": "A座",
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
            payload = {**base_payload, "contract_no": f"LEASE-NEGATIVE-{field}", field: -1}
            response = client.post("/api/contracts", json=payload)
            assert response.status_code == 400


def test_contract_rejects_overlapping_room_lease_periods() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        base_payload = {
            "tower": "A座",
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
                "contract_no": "LEASE-OVERLAP-001",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert response.status_code == 200

        response = client.post(
            "/api/contracts",
            json={
                **base_payload,
                "contract_no": "LEASE-OVERLAP-002",
                "tenant_name": "重叠校验客户二",
                "start_date": "2026-06-01",
                "end_date": "2027-05-31",
            },
        )
        assert response.status_code == 400


def test_dashboard_stats_exclude_retired_contracts_from_active_metrics() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        for contract_no, room_no, disposal in [
            ("LEASE-STATS-ACTIVE", "S9905", ""),
            ("LEASE-STATS-RETIRED", "S9906", "已退租"),
        ]:
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": contract_no,
                    "tower": "A座",
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
        assert metrics["total_monthly_rent"] == 12000


def test_batch_delete_hides_all_selected_contracts() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        ids = []
        for index in range(2):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"LEASE-BATCH-DELETE-{index}",
                    "tower": "A座",
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

        response = client.get("/api/contracts?keyword=LEASE-BATCH-DELETE&page=1&page_size=20")
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
                    "contract_no": f"LEASE-BATCH-ROLLBACK-{index}",
                    "tower": "A座",
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

        rows = client.get("/api/contracts?keyword=LEASE-BATCH-ROLLBACK&page=1&page_size=20").json()
        assert rows["total"] == 2

        from app.database import SessionLocal
        from app.models import Contract

        db = SessionLocal()
        try:
            stored = db.query(Contract).filter(Contract.contract_no.like("LEASE-BATCH-ROLLBACK-%")).all()
            assert len(stored) == 2
            assert all(not item.is_deleted for item in stored)
        finally:
            db.close()


def test_contract_import_rejects_invalid_file_without_partial_write() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        content = _workbook_bytes(
            ["合同编号", "楼栋/区域", "室号", "客户", "面积", "月租金", "起租日", "终止日"],
            [
                ["LEASE-IMPORT-OK", "A座", "S8801", "导入客户", 100, 10000, "2026-01-01", "2026-12-31"],
                ["LEASE-IMPORT-BAD", "A座", "S8802", "错误客户", "bad-area", 10000, "2026-01-01", "2026-12-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rows = client.get("/api/contracts?keyword=LEASE-IMPORT&page=1&page_size=20").json()
        assert rows["total"] == 0


def test_contract_import_rejects_overlapping_rows_without_partial_write() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        content = _workbook_bytes(
            ["合同编号", "楼栋/区域", "室号", "客户", "面积", "月租金", "起租日", "终止日"],
            [
                ["LEASE-IMPORT-OVERLAP-001", "A座", "S8803", "导入客户一", 100, 10000, "2026-01-01", "2026-12-31"],
                ["LEASE-IMPORT-OVERLAP-002", "A座", "S8803", "导入客户二", 100, 10000, "2026-06-01", "2027-05-31"],
            ],
        )
        response = client.post(
            "/api/contracts/import",
            files={"file": ("contracts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rows = client.get("/api/contracts?keyword=LEASE-IMPORT-OVERLAP&page=1&page_size=20").json()
        assert rows["total"] == 0


def test_pdf_download_rejects_unsafe_stored_path() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "LEASE-PDF-PATH",
                "tower": "A座",
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
                "contract_no": "LEASE-PDF-SIZE",
                "tower": "A座",
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
                    "contract_no": f"LEASE-MISSING-PDF-{index:03d}",
                    "tower": "A座",
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


def test_monthly_receivables_returns_rent_and_property_fee() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post(
            "/api/contracts",
            json=_contract_payload(
                "LEASE-RECEIVABLE-001",
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
        assert data["total_rent"] == 12000
        assert data["total_property"] == 2800
        assert data["total"] == 14800
        assert data["rows"][0]["property_fee"] == 2800


def test_batch_pdf_upload_matches_contract_and_updates_missing_list() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        response = client.post("/api/contracts", json=_contract_payload("LEASE-PDF-MATCH-001", "PDF01"))
        assert response.status_code == 200
        contract_id = response.json()["id"]

        response = client.post(
            "/api/contracts/batch-upload-pdf",
            files=[("files", ("LEASE-PDF-MATCH-001.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))],
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
        assert all(item["contract_no"] != "LEASE-PDF-MATCH-001" for item in missing.json())


def test_startup_migrates_legacy_plaintext_pdf_to_encrypted_storage() -> None:
    settings = get_settings()
    legacy_name = "legacy-startup.pdf"
    (settings.upload_dir / legacy_name).write_bytes(b"%PDF-1.4\nlegacy\n%%EOF")

    db = SessionLocal()
    try:
        contract = Contract(
            contract_no="LEASE-LEGACY-PDF",
            tower="A座",
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
            ["楼栋/区域", "楼层", "室号", "类型", "面积"],
            [
                ["B座", "20", "NIMPORT01", "办公", 100],
                ["B座", "20", "NIMPORT02", "办公", "bad-area"],
            ],
        )
        response = client.post(
            "/api/rooms/import",
            files={"file": ("rooms.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 400

        rooms = client.get("/api/rooms").json()
        assert all(room["room_no"] != "NIMPORT01" for room in rooms)


def test_backup_archive_is_encrypted_and_contains_readable_sqlite_after_decrypt() -> None:
    with _client() as client:
        _ensure_admin(client)

    result = create_backup_archive()
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


def test_backup_restore_reverts_database_and_pdfs() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        created = client.post("/api/contracts", json=_contract_payload("LEASE-BACKUP-KEEP", "BKP01"))
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

        extra = client.post("/api/contracts", json=_contract_payload("LEASE-BACKUP-DROP", "BKP02"))
        assert extra.status_code == 200

        restored = client.post(f"/api/backups/{filename}/restore")
        assert restored.status_code == 200
        assert "safety_backup" in restored.json()

        kept = client.get("/api/contracts?keyword=LEASE-BACKUP-KEEP&page=1&page_size=20")
        dropped = client.get("/api/contracts?keyword=LEASE-BACKUP-DROP&page=1&page_size=20")
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
