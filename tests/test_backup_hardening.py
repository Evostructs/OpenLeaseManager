from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.maintenance import maintenance_mode
from app.main import app
from app.services.backup import create_backup_archive, prune_backup_archives, verify_backup_archive


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def test_backup_is_verified_after_creation():
    result = create_backup_archive("manual")
    settings = get_settings()
    archive = settings.backup_dir / result["filename"]
    assert archive.exists()
    # 再显式校验一次（创建时已自动校验）
    verify_backup_archive(archive)


def test_corrupted_backup_fails_verification():
    result = create_backup_archive("manual")
    settings = get_settings()
    archive = settings.backup_dir / result["filename"]
    archive.write_bytes(b"corrupted-not-a-backup")
    try:
        verify_backup_archive(archive)
        raised = False
    except Exception:
        raised = True
    assert raised, "损坏的备份必须校验失败"


def test_prune_keeps_recent_and_minimum_count():
    settings = get_settings()
    old_stamp = time.time() - (settings.backup_retention_days + 10) * 86400
    # 构造 min_keep + 3 个过期自动备份：应只清掉超出 min_keep 的 3 个
    total = settings.backup_min_keep + 3
    for index in range(total):
        path = settings.backup_dir / f"auto_backup_20250101_0000{index:02d}.zip"
        path.write_bytes(b"x")
        os.utime(path, (old_stamp - index, old_stamp - index))

    removed = prune_backup_archives()
    remaining = sorted(p.name for p in settings.backup_dir.glob("auto_backup_20250101_*.zip"))
    assert len(removed) == 3
    assert len(remaining) == settings.backup_min_keep

    # 新鲜的自动备份不会被清；它挤占 min_keep 名额后最老的过期备份继续出清
    fresh = settings.backup_dir / "auto_backup_20991231_000000.zip"
    fresh.write_bytes(b"x")
    removed_again = prune_backup_archives()
    assert fresh.exists()
    assert all(name.startswith("auto_backup_20250101_") for name in removed_again)


def test_maintenance_mode_returns_503_for_business_api():
    with _client() as client:
        _ensure_admin(client)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200
        with maintenance_mode():
            blocked = client.get("/api/contracts")
            assert blocked.status_code == 503
            assert "维护" in blocked.json()["detail"]
            # 健康/版本探活不受影响
            assert client.get("/api/version").status_code == 200
        # 退出维护模式后恢复正常
        assert client.get("/api/contracts").status_code == 200


def test_restore_round_trip_under_maintenance():
    with _client() as client:
        _ensure_admin(client)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200
        created = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-RESTORE-RT",
                "tower": "南塔",
                "room_no": "RT01",
                "tenant_name": "恢复演练租户",
                "area": 100,
                "unit_price": 4,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert created.status_code == 200, created.text

        backup = client.post("/api/backups")
        assert backup.status_code == 200, backup.text
        filename = backup.json()["filename"]

        restored = client.post(f"/api/backups/{filename}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["safety_backup"]

        # 恢复后数据可读，合同仍在
        rows = client.get("/api/contracts", params={"keyword": "DEMO_A-RESTORE-RT"}).json()["items"]
        assert rows and rows[0]["contract_no"] == "DEMO_A-RESTORE-RT"
