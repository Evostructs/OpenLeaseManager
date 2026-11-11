from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLE_NAME = "user_portfolio_overview_grants"


def _run_alembic(data_dir: Path, *arguments: str, expect_success: bool = True):
    data_dir.mkdir(parents=True, exist_ok=True)
    process_env = os.environ.copy()
    process_env["DATA_DIR"] = str(data_dir)
    process_env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if expect_success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
    return result


def _connect(data_dir: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(data_dir / "lease.db")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_exists(db: sqlite3.Connection) -> bool:
    return bool(
        db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_NAME,),
        ).fetchone()[0]
    )


def _assert_0027_structure(db: sqlite3.Connection) -> None:
    assert _table_exists(db)
    columns = {
        str(row[1]): {
            "type": str(row[2]).upper(),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key": bool(row[5]),
        }
        for row in db.execute(f"PRAGMA table_info({TABLE_NAME})")
    }
    assert set(columns) == {
        "id",
        "user_id",
        "portfolio_id",
        "include_future_buildings",
        "valid_from",
        "valid_to",
        "granted_by",
        "granted_at",
    }
    assert columns["id"]["primary_key"]
    assert columns["user_id"]["not_null"]
    assert columns["portfolio_id"]["not_null"]
    assert columns["include_future_buildings"]["not_null"]
    assert columns["include_future_buildings"]["default"] in {"0", "(0)"}
    assert columns["valid_from"]["not_null"]
    assert not columns["valid_to"]["not_null"]
    assert not columns["granted_by"]["not_null"]
    assert columns["granted_at"]["not_null"]

    assert {
        (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
        for row in db.execute(f"PRAGMA foreign_key_list({TABLE_NAME})")
    } == {
        ("users", "user_id", "id", "RESTRICT"),
        ("portfolios", "portfolio_id", "id", "RESTRICT"),
        ("users", "granted_by", "id", "SET NULL"),
    }

    indexes = {
        str(row[1]): bool(row[2])
        for row in db.execute(f"PRAGMA index_list({TABLE_NAME})")
    }
    assert {
        "ix_user_portfolio_overview_grants_id",
        "ix_user_portfolio_overview_grants_portfolio_id",
        "ix_user_portfolio_overview_grants_user_id",
    } <= set(indexes)
    unique_columns = {
        tuple(
            str(column[2])
            for column in db.execute(f"PRAGMA index_info('{index_name}')")
        )
        for index_name, unique in indexes.items()
        if unique
    }
    assert ("user_id", "portfolio_id", "valid_from") in unique_columns

    create_sql = str(
        db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_NAME,),
        ).fetchone()[0]
    )
    assert "ck_user_portfolio_overview_valid_range" in create_sql


def test_0027_upgrade_creates_isolated_overview_grants_structure(tmp_path: Path) -> None:
    data_dir = tmp_path / "overview-structure"
    # Pin the historical baseline rather than assuming it is the latest head.
    _run_alembic(data_dir, "upgrade", "20260928_0032")

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        _assert_0027_structure(db)
        assert db.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0027_refuses_downgrade_when_overview_grants_exist(tmp_path: Path) -> None:
    data_dir = tmp_path / "overview-downgrade-guard"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        db.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES ('overview-user', 'x', 'viewer', 1)"
        )
        db.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                user_id, portfolio_id, include_future_buildings,
                valid_from, valid_to, granted_by, granted_at
            ) VALUES (
                (SELECT id FROM users WHERE username='overview-user'),
                (SELECT id FROM portfolios WHERE code='DEMO_OFFICE_PORTFOLIO'),
                0, '2026-08-05', NULL,
                (SELECT id FROM users WHERE username='overview-user'),
                '2026-08-05 17:30:00'
            )
            """
        )
        db.commit()

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260805_0026",
        expect_success=False,
    )
    output = result.stdout + result.stderr
    assert "阶段3E降级已停止" in output
    assert "overview_grants=1" in output

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0027"
        _assert_0027_structure(db)
        assert db.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_0027_empty_table_can_downgrade_and_reupgrade(tmp_path: Path) -> None:
    data_dir = tmp_path / "overview-roundtrip"
    _run_alembic(data_dir, "upgrade", "20260928_0032")
    _run_alembic(data_dir, "downgrade", "20260805_0026")

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0026"
        assert not _table_exists(db)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    _run_alembic(data_dir, "upgrade", "20260928_0032")
    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        _assert_0027_structure(db)
        assert db.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
