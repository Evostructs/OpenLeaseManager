from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
import sqlite3

from app.core.config import get_settings


settings = get_settings()
DB_PATH = settings.data_dir / "lease.db"

engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
    pool_recycle=3600,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
