from __future__ import annotations

from datetime import date, timedelta
import io

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.crypto import is_encrypted_file
from app.database import SessionLocal
from app.main import app
from app.models import Contract, Room


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


def _active_period() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=365), today + timedelta(days=365)


def _seed_rooms(count: int, *, prefix: str = "STR", tower: str = "A座") -> None:
    db = SessionLocal()
    try:
        rooms = [
            Room(
                tower=tower,
                floor=str(index // 10 + 1),
                room_no=f"{prefix}{index:04d}",
                room_type="办公",
                area=120 + index % 60,
                is_leasable=True,
            )
            for index in range(count)
        ]
        db.bulk_save_objects(rooms)
        db.commit()
    finally:
        db.close()


def _seed_contracts(
    count: int,
    *,
    prefix: str = "LEASE-STRESS",
    room_prefix: str = "STR",
    with_pdf_every: int | None = None,
    start_rent: float = 10000,
) -> None:
    start_date, end_date = _active_period()
    db = SessionLocal()
    try:
        contracts = []
        for index in range(count):
            has_pdf = with_pdf_every is not None and index % with_pdf_every == 0
            contracts.append(
                Contract(
                    contract_no=f"{prefix}-{index:04d}",
                    tower="A座",
                    room_no=f"{room_prefix}{index:04d}",
                    tenant_name=f"压力测试客户{index:04d}",
                    area=100 + index % 80,
                    unit_price=3.5 + (index % 20) / 10,
                    monthly_rent=start_rent + index * 37,
                    property_fee_rate=28,
                    start_date=start_date,
                    end_date=end_date,
                    pdf_path=f"{prefix}-{index:04d}.pdf" if has_pdf else "",
                    is_deleted=False,
                )
            )
        db.bulk_save_objects(contracts)
        db.commit()
    finally:
        db.close()


def _contract_pdf_paths(prefix: str) -> list[str]:
    db = SessionLocal()
    try:
        return [
            row.pdf_path or ""
            for row in db.query(Contract).filter(Contract.contract_no.like(f"{prefix}-%")).order_by(Contract.contract_no).all()
        ]
    finally:
        db.close()


def test_large_contract_sorting_is_global_before_pagination() -> None:
    _seed_rooms(260, prefix="GS")
    _seed_contracts(260, prefix="LEASE-GLOBAL-SORT", room_prefix="GS", start_rent=20000)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        response = client.get(
            "/api/contracts",
            params={
                "status": "在租",
                "sort_by": "monthly_rent",
                "sort_dir": "desc",
                "page": 2,
                "page_size": 25,
            },
        )
        assert response.status_code == 200
        data = response.json()
        rents = [item["monthly_rent"] for item in data["items"]]

    assert data["total"] == 260
    assert rents == sorted(rents, reverse=True)
    assert rents[0] == 20000 + (260 - 26) * 37


def test_missing_pdf_and_integrity_checks_handle_more_than_frontend_page_limit() -> None:
    _seed_rooms(650, prefix="MP")
    _seed_contracts(650, prefix="LEASE-MISSING-STRESS", room_prefix="MP", with_pdf_every=None)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        missing = client.get("/api/contracts/missing-pdfs")
        report = client.get("/api/system/integrity-check")

    assert missing.status_code == 200
    assert len(missing.json()) == 650
    assert report.status_code == 200
    missing_issue = next(issue for issue in report.json()["issues"] if issue["type"] == "missing_contract_pdfs")
    assert "650" in missing_issue["detail"]


def test_batch_pdf_upload_count_limit_short_circuits_without_writing_files(monkeypatch) -> None:
    from app.services import contracts as contracts_service

    monkeypatch.setattr(contracts_service, "MAX_BATCH_PDF_COUNT", 3)
    _seed_rooms(4, prefix="BC")
    _seed_contracts(4, prefix="LEASE-BATCH-COUNT", room_prefix="BC")

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        files = [
            ("files", (f"LEASE-BATCH-COUNT-{index:04d}.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))
            for index in range(4)
        ]
        response = client.post("/api/contracts/batch-upload-pdf", files=files)

    assert response.status_code == 413
    assert all(not path for path in _contract_pdf_paths("LEASE-BATCH-COUNT"))
    assert not list(get_settings().upload_dir.glob("LEASE-BATCH-COUNT-*.pdf"))


def test_batch_pdf_upload_total_size_limit_rolls_back_all_matches(monkeypatch) -> None:
    from app.services import contracts as contracts_service

    monkeypatch.setattr(contracts_service, "MAX_BATCH_PDF_TOTAL_SIZE", 20)
    _seed_rooms(2, prefix="BT")
    _seed_contracts(2, prefix="LEASE-BATCH-TOTAL", room_prefix="BT")

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        files = [
            ("files", ("LEASE-BATCH-TOTAL-0000.pdf", b"123456789012", "application/pdf")),
            ("files", ("LEASE-BATCH-TOTAL-0001.pdf", b"123456789012", "application/pdf")),
        ]
        response = client.post("/api/contracts/batch-upload-pdf", files=files)

    assert response.status_code == 413
    assert all(not path for path in _contract_pdf_paths("LEASE-BATCH-TOTAL"))
    assert not list(get_settings().upload_dir.glob("LEASE-BATCH-TOTAL-*.pdf"))


def test_rooms_and_stats_remain_consistent_under_large_room_inventory() -> None:
    _seed_rooms(420, prefix="RM")
    _seed_contracts(294, prefix="LEASE-ROOM-STRESS", room_prefix="RM", with_pdf_every=1)

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        rooms = client.get("/api/rooms")
        stats = client.get("/api/rooms/stats")

    assert rooms.status_code == 200
    assert stats.status_code == 200
    rows = rooms.json()
    data = stats.json()
    assert len(rows) == 420
    assert sum(1 for row in rows if row["occupied"]) == 294
    assert all(row["contract"]["status"] == "在租" for row in rows if row["occupied"])
    assert data["total_rooms"] == 420
    assert data["occupied_rooms"] == 294


def test_backup_restore_reverts_large_dataset_snapshot() -> None:
    _seed_rooms(180, prefix="BK")
    _seed_contracts(150, prefix="LEASE-BACKUP-STRESS", room_prefix="BK", with_pdf_every=3)
    settings = get_settings()
    for index in range(0, 150, 3):
        (settings.upload_dir / f"LEASE-BACKUP-STRESS-{index:04d}.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        backup = client.post("/api/backups")
        assert backup.status_code == 200
        filename = backup.json()["filename"]

        _seed_contracts(25, prefix="LEASE-BACKUP-EXTRA", room_prefix="BKX")
        before_restore = client.get("/api/contracts?keyword=LEASE-BACKUP-EXTRA&page=1&page_size=100")
        assert before_restore.status_code == 200
        assert before_restore.json()["total"] == 25

        restored = client.post(f"/api/backups/{filename}/restore")
        assert restored.status_code == 200

        kept = client.get("/api/contracts?keyword=LEASE-BACKUP-STRESS&page=1&page_size=100")
        dropped = client.get("/api/contracts?keyword=LEASE-BACKUP-EXTRA&page=1&page_size=100")

    assert kept.status_code == 200
    assert dropped.status_code == 200
    assert kept.json()["total"] == 150
    assert dropped.json()["total"] == 0
    restored_pdf = settings.upload_dir / "LEASE-BACKUP-STRESS-0000.pdf.enc"
    assert restored_pdf.exists()
    assert is_encrypted_file(restored_pdf)


def test_integrity_export_handles_large_issue_report() -> None:
    _seed_contracts(300, prefix="LEASE-INTEGRITY-EXPORT", room_prefix="NO-ROOM")

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        response = client.get("/api/system/integrity-check/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(response.content) > 10_000
    assert io.BytesIO(response.content).getbuffer().nbytes == len(response.content)
