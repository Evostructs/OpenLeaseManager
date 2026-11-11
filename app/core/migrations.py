from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import get_settings
from app.core.crypto import encrypt_file, is_encrypted_file
from app.core.file_lock import exclusive_file_lock
from app.core.time import now_in_timezone


def _pending_migrations(config: Config, db_path: Path) -> bool:
    if not db_path.exists():
        return True
    script = ScriptDirectory.from_config(config)
    heads = set(script.get_heads())
    conn = sqlite3.connect(db_path)
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return True
    finally:
        conn.close()
    return not row or row[0] not in heads


def _pre_migrate_snapshot(db_path: Path) -> None:
    """SQLite 的 DDL 非事务，迁移中途失败会留下半截 schema；
    升级前先做一份库快照，失败时可手工回滚。"""
    if not db_path.exists():
        return
    settings = get_settings()
    snapshot_path = settings.backup_dir / f"pre_migrate_{now_in_timezone().strftime('%Y%m%d_%H%M%S')}.db"
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(snapshot_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    final_path = snapshot_path
    if settings.data_encryption_enabled:
        encrypted_path = snapshot_path.with_suffix(".db.enc")
        encrypt_file(snapshot_path, encrypted_path, settings.data_key)
        if not is_encrypted_file(encrypted_path):
            raise RuntimeError("Encrypted migration snapshot verification failed.")
        snapshot_path.unlink(missing_ok=True)
        final_path = encrypted_path

    if settings.backup_mirror_dir:
        try:
            settings.backup_mirror_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_path, settings.backup_mirror_dir / final_path.name)
        except OSError as exc:
            print(f"警告: 迁移快照无法同步到镜像目录: {exc}")


def run_migrations() -> None:
    project_root = Path(__file__).resolve().parents[2]
    alembic_ini = project_root / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(project_root / "alembic"))

    settings = get_settings()
    db_path = settings.data_dir / "lease.db"
    lock_path = settings.data_dir / ".migrate.lock"
    # 文件锁串行化迁移：多 worker / 多副本挂同一数据卷时不并发执行 alembic
    with exclusive_file_lock(lock_path):
        if _pending_migrations(config, db_path):
            _pre_migrate_snapshot(db_path)
        command.upgrade(config, "head")
