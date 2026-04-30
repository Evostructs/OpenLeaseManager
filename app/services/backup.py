from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
import zipfile

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.crypto import CryptoError, decrypt_file, encrypt_file, is_encrypted_file
from app.core.files import ensure_safe_backup_name
from app.database import engine


def create_backup_archive() -> dict:
    settings = get_settings()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_name = f"backup_{timestamp}"
    staging_dir = settings.backup_dir / backup_name
    staging_dir.mkdir(parents=True, exist_ok=True)
    zip_path: Path | None = None

    try:
        db_path = settings.data_dir / "lease.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            try:
                target_db = staging_dir / "lease.db"
                backup_conn = sqlite3.connect(target_db)
                try:
                    conn.backup(backup_conn)
                finally:
                    backup_conn.close()
            finally:
                conn.close()

        if settings.upload_dir.exists():
            shutil.copytree(settings.upload_dir, staging_dir / "pdfs", dirs_exist_ok=True)

        zip_path = Path(shutil.make_archive(str(staging_dir), "zip", staging_dir))
        final_path = zip_path
        if settings.data_encryption_enabled:
            encrypted_path = zip_path.with_name(f"{zip_path.name}.enc")
            try:
                encrypt_file(zip_path, encrypted_path, settings.data_key)
            finally:
                zip_path.unlink(missing_ok=True)
            final_path = encrypted_path

        return {
            "message": "备份成功",
            "filename": final_path.name,
            "size": os.path.getsize(final_path),
            "encrypted": final_path.name.endswith(".enc"),
        }
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def list_backup_archives() -> list[dict]:
    settings = get_settings()
    if not settings.backup_dir.exists():
        return []

    files = []
    candidates = list(settings.backup_dir.glob("*.zip")) + list(settings.backup_dir.glob("*.zip.enc"))
    for item in sorted(candidates, key=lambda path: path.stat().st_ctime, reverse=True):
        files.append(
            {
                "filename": item.name,
                "size": item.stat().st_size,
                "created": datetime.fromtimestamp(item.stat().st_ctime).isoformat(),
                "encrypted": item.name.endswith(".enc"),
            }
        )
    return files


def get_backup_file(name: str) -> Path:
    settings = get_settings()
    safe_name = ensure_safe_backup_name(name)
    target = settings.backup_dir / safe_name
    if not target.exists() or not (target.name.endswith(".zip") or target.name.endswith(".zip.enc")):
        raise FileNotFoundError(safe_name)
    return target


def _validate_archive_member(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(400, "备份文件包含非法路径")


def _restore_source_zip(archive_path: Path, temp_path: Path) -> Path:
    if archive_path.name.endswith(".zip.enc") or is_encrypted_file(archive_path):
        settings = get_settings()
        if not settings.data_encryption_enabled:
            raise HTTPException(500, "备份已加密，但当前未启用数据加密配置")
        decrypted = temp_path / "backup.zip"
        try:
            decrypt_file(archive_path, decrypted, settings.data_key)
        except CryptoError as exc:
            raise HTTPException(400, "备份解密失败，请确认数据加密密钥正确") from exc
        return decrypted
    return archive_path


def restore_backup_archive(name: str) -> dict:
    settings = get_settings()
    archive_path = get_backup_file(name)
    safety_backup = create_backup_archive()

    with tempfile.TemporaryDirectory(dir=settings.backup_dir) as temp_dir:
        temp_path = Path(temp_dir)
        source_zip = _restore_source_zip(archive_path, temp_path)
        if not zipfile.is_zipfile(source_zip):
            raise HTTPException(400, "备份文件格式不正确")

        with zipfile.ZipFile(source_zip) as archive:
            for member in archive.namelist():
                _validate_archive_member(member)
            archive.extractall(temp_path)

        source_db = temp_path / "lease.db"
        if not source_db.exists():
            raise HTTPException(400, "备份文件缺少 lease.db")

        target_db = settings.data_dir / "lease.db"
        engine.dispose()
        source_conn = sqlite3.connect(source_db)
        try:
            target_conn = sqlite3.connect(target_db)
            try:
                target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                source_conn.backup(target_conn)
                target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                target_conn.close()
        finally:
            source_conn.close()

        restored_pdfs = temp_path / "pdfs"
        if settings.upload_dir.exists():
            shutil.rmtree(settings.upload_dir)
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        if restored_pdfs.exists():
            shutil.copytree(restored_pdfs, settings.upload_dir, dirs_exist_ok=True)

    if settings.data_encryption_enabled:
        from app.services.encryption_migration import encrypt_plaintext_pdfs_without_backup

        encrypt_plaintext_pdfs_without_backup()

    engine.dispose()
    return {
        "message": "备份恢复成功",
        "filename": archive_path.name,
        "safety_backup": safety_backup["filename"],
    }
