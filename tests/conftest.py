from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

import pytest


TEST_DATA_DIR = tempfile.mkdtemp(prefix="lease-manager-test-")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["JWT_SECRET"] = "test-secret-not-for-production"
os.environ["COOKIE_SECURE"] = "false"
os.environ["TOKEN_EXPIRE_HOURS"] = "2"
os.environ["DATA_ENCRYPTION_ENABLED"] = "true"
os.environ["DATA_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture(autouse=True)
def isolated_database():
    from app.core.config import get_settings
    from app.core.migrations import run_migrations
    from app.database import SessionLocal
    from app.models import Contract, IntegrationConnection, IntegrationEvent, IntegrationSyncRun, OperationLog, Room, SystemConfig, User

    run_migrations()
    settings = get_settings()

    def cleanup() -> None:
        db = SessionLocal()
        try:
            for model in (IntegrationEvent, IntegrationSyncRun, IntegrationConnection, OperationLog, Contract, Room, User, SystemConfig):
                db.query(model).delete()
            db.commit()
        finally:
            db.close()
        for directory in (settings.upload_dir, settings.backup_dir):
            if directory.exists():
                shutil.rmtree(directory)
            Path(directory).mkdir(parents=True, exist_ok=True)

    cleanup()
    yield
    cleanup()
