from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.crypto import CryptoError, decrypt_file, encrypt_file, is_encrypted_file
from app.core.files import ensure_safe_backup_name
from app.core.maintenance import maintenance_mode
from app.core.time import now_in_timezone, today_in_timezone
from app.database import engine


BACKUP_PREFIXES = {
    "manual": "backup",
    "auto": "auto_backup",
    "safety": "safety_backup",
}

BACKUP_TYPE_LABELS = {
    "manual": "手动立即备份",
    "auto": "系统定时自动备份",
    "safety": "恢复前安全备份",
}


def backup_type_from_filename(filename: str) -> str:
    match = re.match(r"^(auto_backup|safety_backup|backup)_(\d{8})_(\d{6})(?:_\d+)?\.zip(?:\.enc)?$", filename, re.I)
    if not match:
        return "manual"
    prefix = match.group(1).lower()
    if prefix == "auto_backup":
        return "auto"
    if prefix == "safety_backup":
        return "safety"
    if match.group(3) == "020000":
        return "auto"
    return "manual"


def create_backup_archive(backup_type: str = "manual") -> dict:
    settings = get_settings()
    timestamp = now_in_timezone().strftime("%Y%m%d_%H%M%S_%f")
    normalized_type = backup_type if backup_type in BACKUP_PREFIXES else "manual"
    backup_name = f"{BACKUP_PREFIXES[normalized_type]}_{timestamp}"
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
        if settings.attachment_dir.exists():
            shutil.copytree(settings.attachment_dir, staging_dir / "attachments", dirs_exist_ok=True)

        zip_path = Path(shutil.make_archive(str(staging_dir), "zip", staging_dir))
        final_path = zip_path
        if settings.data_encryption_enabled:
            encrypted_path = zip_path.with_name(f"{zip_path.name}.enc")
            try:
                encrypt_file(zip_path, encrypted_path, settings.data_key)
            finally:
                zip_path.unlink(missing_ok=True)
            final_path = encrypted_path

        # 备份产物当场校验：能解密、是合法 zip、库通过 quick_check。
        # 校验失败立即删除坏档并抛错，避免"以为有备份其实早就坏了"。
        try:
            verify_backup_archive(final_path)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise

        return {
            "message": "备份成功",
            "filename": final_path.name,
            "size": os.path.getsize(final_path),
            "encrypted": final_path.name.endswith(".enc"),
            "backup_type": normalized_type,
            "type_label": BACKUP_TYPE_LABELS[normalized_type],
        }
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def verify_backup_archive(archive_path: Path) -> None:
    settings = get_settings()
    with tempfile.TemporaryDirectory(dir=settings.backup_dir) as temp_dir:
        temp_path = Path(temp_dir)
        source = archive_path
        if archive_path.name.endswith(".enc") or is_encrypted_file(archive_path):
            source = temp_path / "verify.zip"
            decrypt_file(archive_path, source, settings.data_key)
        if not zipfile.is_zipfile(source):
            raise RuntimeError(f"备份 {archive_path.name} 不是合法的 zip 文件")
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if "lease.db" not in names:
                raise RuntimeError(f"备份 {archive_path.name} 缺少 lease.db")
            archive.extract("lease.db", temp_path)
        conn = sqlite3.connect(temp_path / "lease.db")
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        if not result or result[0] != "ok":
            raise RuntimeError(f"备份 {archive_path.name} 数据库完整性校验未通过: {result}")


def prune_backup_archives(directory: Path | None = None) -> list[str]:
    """按保留策略清理自动/安全备份；手动备份不动。

    自动备份保留 backup_retention_days 天，且无论多旧至少保留
    最近 backup_min_keep 份；恢复前安全备份同策略。
    """
    settings = get_settings()
    target_dir = directory or settings.backup_dir
    if not target_dir.exists():
        return []
    cutoff = today_in_timezone().toordinal() - settings.backup_retention_days
    removed = []
    for prefix in ("auto_backup", "safety_backup", "pre_migrate"):
        candidates = sorted(
            (path for path in target_dir.iterdir() if path.is_file() and path.name.startswith(f"{prefix}_")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates[settings.backup_min_keep:]:
            if datetime.fromtimestamp(path.stat().st_mtime).toordinal() < cutoff:
                path.unlink(missing_ok=True)
                removed.append(path.name)
    return removed


def mirror_backup_archive(filename: str) -> bool:
    """把备份同步一份到镜像目录（建议挂载到另一块盘/存储池）。"""
    settings = get_settings()
    if not settings.backup_mirror_dir:
        return False
    source = settings.backup_dir / filename
    if not source.exists():
        return False
    settings.backup_mirror_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, settings.backup_mirror_dir / filename)
    prune_backup_archives(settings.backup_mirror_dir)
    return True


def list_backup_archives() -> list[dict]:
    settings = get_settings()
    if not settings.backup_dir.exists():
        return []

    files = []
    candidates = list(settings.backup_dir.glob("*.zip")) + list(settings.backup_dir.glob("*.zip.enc"))
    for item in sorted(candidates, key=lambda path: path.stat().st_ctime, reverse=True):
        backup_type = backup_type_from_filename(item.name)
        files.append(
            {
                "filename": item.name,
                "size": item.stat().st_size,
                "created": datetime.fromtimestamp(item.stat().st_ctime).isoformat(),
                "encrypted": item.name.endswith(".enc"),
                "backup_type": backup_type,
                "type_label": BACKUP_TYPE_LABELS[backup_type],
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


def _swap_directory(target: Path, new_content: Path | None) -> None:
    """用"备好新目录 → 原子改名替换"的方式恢复目录，失败时回滚旧目录。

    旧实现是先 rmtree 再 copytree，两步之间抛异常会把附件目录清空。
    """
    staging = target.with_name(f".{target.name}.restore-new")
    old = target.with_name(f".{target.name}.restore-old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    if new_content and new_content.exists():
        shutil.copytree(new_content, staging, dirs_exist_ok=True)
    if target.exists():
        target.rename(old)
    try:
        staging.rename(target)
    except Exception:
        if old.exists():
            old.rename(target)
        raise
    shutil.rmtree(old, ignore_errors=True)


def restore_backup_archive(name: str) -> dict:
    settings = get_settings()
    archive_path = get_backup_file(name)

    # 恢复全程进入维护模式：业务请求统一 503，防止恢复窗口内的
    # 并发写被覆盖或与 backup API 抢锁留下半截状态。
    with maintenance_mode():
        safety_backup = create_backup_archive("safety")

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

            _swap_directory(settings.upload_dir, temp_path / "pdfs")
            _swap_directory(settings.attachment_dir, temp_path / "attachments")

        if settings.data_encryption_enabled:
            from app.services.encryption_migration import encrypt_plaintext_pdfs_without_backup

            encrypt_plaintext_pdfs_without_backup()

        engine.dispose()
    return {
        "message": "备份恢复成功",
        "filename": archive_path.name,
        "safety_backup": safety_backup["filename"],
    }
