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
            print("警告: 无法启用 WAL 模式，SQLite 并发性能将下降")
        # Money/closing records should sync the WAL before acknowledging a
        # commit. NORMAL can lose recent commits after OS/power failure.
        cursor.execute("PRAGMA synchronous=FULL")
        # SQLite 默认不开外键约束；不开的话硬删除会留悬挂引用（孤儿数据）
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
