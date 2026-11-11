from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260910_0029"
PREVIOUS_REVISION = "20260806_0028"
TABLES = {"financial_expenses", "financial_plan_versions", "financial_planning_states"}


def _run_alembic(data_dir: Path, *arguments: str, expect_success: bool = True):
    data_dir.mkdir(parents=True, exist_ok=True)
    process_env = os.environ.copy()
    process_env["DATA_DIR"] = str(data_dir)
    process_env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT, env=process_env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90, check=False,
    )
    if expect_success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
    return result


def _connect(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(data_dir / "lease.db")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _schema(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (kind, name): sql or ""
        for kind, name, sql in db.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def _business_rows(db: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [name for (kind, name) in _schema(db) if kind == "table" and name != "alembic_version"]
    return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in tables}


def _insert_expense(db: sqlite3.Connection) -> int:
    year = date.today().year
    cursor = db.execute(
        """INSERT INTO financial_expenses
           (building_id, name, category, amount_yuan, occurrence_month, payment_month,
            created_by, updated_by, created_at, updated_at)
           VALUES ((SELECT id FROM buildings WHERE code='DEMO_A'), '保留费用历史', 'opex',
                   20000, ?, ?, 'migration-test', 'migration-test', ?, ?)""",
        (f"{year}-01", f"{year}-02", date.today().isoformat(), date.today().isoformat()),
    )
    return int(cursor.lastrowid)


def _insert_version(db: sqlite3.Connection) -> int:
    cursor = db.execute(
        """INSERT INTO financial_plan_versions
           (building_id, year, revision, kind, name, client_state_json, snapshot_json,
            source_digest, created_by, created_at)
           VALUES ((SELECT id FROM buildings WHERE code='DEMO_A'), ?, 1, 'budget',
                   '保留预算历史', '{}', '{}', ?, 'migration-test', ?)""",
        (date.today().year, "a" * 64, date.today().isoformat()),
    )
    return int(cursor.lastrowid)


def test_0029_upgrade_adds_only_three_tables_and_preserves_existing_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "upgrade-old-database"
    _run_alembic(data_dir, "upgrade", PREVIOUS_REVISION)
    with _connect(data_dir) as db:
        db.execute("INSERT INTO system_config (key, value) VALUES ('planning-sentinel', 'unchanged')")
        db.commit()
        old_schema = _schema(db)
        old_rows = _business_rows(db)
    _run_alembic(data_dir, "upgrade", REVISION)
    with _connect(data_dir) as db:
        new_schema = _schema(db)
        assert {name for kind, name in new_schema.keys() - old_schema.keys() if kind == "table"} == TABLES
        for key, definition in old_schema.items():
            assert new_schema[key] == definition
        for table, rows in old_rows.items():
            assert db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() == rows
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0029_new_schema_links_versions_and_preserves_business_constraints(tmp_path: Path) -> None:
    data_dir = tmp_path / "schema-constraints"
    _run_alembic(data_dir, "upgrade", REVISION)
    with _connect(data_dir) as db:
        expense_id = _insert_expense(db)
        version_id = _insert_version(db)
        building_id = db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        db.execute(
            """INSERT INTO financial_planning_states
               (building_id, year, revision, active_budget_id, updated_at)
               VALUES (?, ?, 1, ?, ?)""",
            (building_id, date.today().year, version_id, date.today().isoformat()),
        )
        db.commit()
        assert db.execute("SELECT amount_yuan, paid_on, is_active, revision FROM financial_expenses WHERE id=?",
                          (expense_id,)).fetchone() == (20000, None, 1, 1)
        for sql in (
            "UPDATE financial_expenses SET amount_yuan=-1",
            "UPDATE financial_expenses SET category='unknown'",
            "UPDATE financial_expenses SET revision=0",
            "UPDATE financial_plan_versions SET kind='unknown'",
            "UPDATE financial_plan_versions SET closed_through_month=13",
            "UPDATE financial_plan_versions SET revision=0",
            "UPDATE financial_planning_states SET revision=-1",
            "UPDATE financial_planning_states SET active_budget_id=999999",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_version(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO financial_planning_states (building_id, year, updated_at) VALUES (?, ?, ?)",
                (building_id, date.today().year, date.today().isoformat()),
            )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("populated", sorted(TABLES))
def test_0029_downgrade_refuses_any_populated_planning_table(tmp_path: Path, populated: str) -> None:
    data_dir = tmp_path / populated
    _run_alembic(data_dir, "upgrade", REVISION)
    with _connect(data_dir) as db:
        if populated == "financial_expenses":
            _insert_expense(db)
        elif populated == "financial_plan_versions":
            _insert_version(db)
        else:
            db.execute(
                """INSERT INTO financial_planning_states (building_id, year, updated_at)
                   VALUES ((SELECT id FROM buildings WHERE code='DEMO_A'), ?, ?)""",
                (date.today().year, date.today().isoformat()),
            )
        db.commit()
        before_schema, before_rows = _schema(db), _business_rows(db)
    result = _run_alembic(data_dir, "downgrade", PREVIOUS_REVISION, expect_success=False)
    assert "拒绝删除业务历史" in result.stdout + result.stderr
    with _connect(data_dir) as db:
        assert _schema(db) == before_schema
        assert _business_rows(db) == before_rows
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0029_empty_downgrade_and_reupgrade_are_lossless(tmp_path: Path) -> None:
    data_dir = tmp_path / "empty-roundtrip"
    _run_alembic(data_dir, "upgrade", PREVIOUS_REVISION)
    with _connect(data_dir) as db:
        before_schema, before_rows = _schema(db), _business_rows(db)
    _run_alembic(data_dir, "upgrade", REVISION)
    _run_alembic(data_dir, "downgrade", PREVIOUS_REVISION)
    with _connect(data_dir) as db:
        assert _schema(db) == before_schema
        assert _business_rows(db) == before_rows
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PREVIOUS_REVISION
    _run_alembic(data_dir, "upgrade", REVISION)
    with _connect(data_dir) as db:
        assert TABLES <= {name for kind, name in _schema(db) if kind == "table"}
        assert all(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in TABLES)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
