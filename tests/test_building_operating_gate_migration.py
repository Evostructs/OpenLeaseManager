from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def _gate_rows(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        """
        SELECT b.code, g.revision, g.presentation_enabled, g.contribution_enabled,
               g.origin, g.actor_id, g.previous_event_id
        FROM building_operating_gate_events g
        JOIN buildings b ON b.id = g.building_id
        ORDER BY b.code, g.revision
        """
    ).fetchall()


def test_0026_seeds_five_append_only_gate_decisions(tmp_path: Path) -> None:
    data_dir = tmp_path / "gate-seed"
    # Check this historical seed before 0033 intentionally changes display names.
    _run_alembic(data_dir, "upgrade", "20260928_0032")

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        assert _gate_rows(db) == [
            ("DEMO_A", 1, 1, 1, "migration", None, None),
            ("DEMO_B", 1, 1, 0, "migration", None, None),
            ("DEMO_C", 1, 1, 0, "migration", None, None),
            ("DEMO_D", 1, 1, 0, "migration", None, None),
            ("DEMO_E", 1, 1, 0, "migration", None, None),
        ]
        trigger_names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='building_operating_gate_events'"
            ).fetchall()
        }
        assert trigger_names == {
            "trg_building_operating_gate_events_no_update",
            "trg_building_operating_gate_events_no_delete",
        }
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0026_guards_history_and_refuses_lossy_downgrade(tmp_path: Path) -> None:
    data_dir = tmp_path / "gate-history"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        gate_id = int(
            db.execute(
                """
                SELECT g.id FROM building_operating_gate_events g
                JOIN buildings b ON b.id=g.building_id WHERE b.code='DEMO_B'
                """
            ).fetchone()[0]
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE building_operating_gate_events SET reason='changed' WHERE id=?",
                (gate_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM building_operating_gate_events WHERE id=?", (gate_id,))
        db.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES ('gate-admin', 'x', 'admin', 1)"
        )
        db.execute(
            """
            INSERT INTO building_operating_gate_events (
                building_id, revision, presentation_enabled, contribution_enabled,
                reason, actor_id, origin, previous_event_id, created_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'), 2, 1, 1,
                '模拟数据已核验，允许进入合计',
                (SELECT id FROM users WHERE username='gate-admin'), 'user', ?,
                '2026-08-05 16:00:00'
            )
            """,
            (gate_id,),
        )
        db.commit()

    result = _run_alembic(data_dir, "downgrade", "20260805_0025", expect_success=False)
    assert "gate history contains non-seed decisions" in result.stdout + result.stderr
    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0026"
        assert len(_gate_rows(db)) == 6
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_0026_round_trip_without_history_is_lossless(tmp_path: Path) -> None:
    data_dir = tmp_path / "gate-roundtrip"
    _run_alembic(data_dir, "upgrade", "head")
    _run_alembic(data_dir, "downgrade", "20260805_0025")

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0025"
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='building_operating_gate_events'"
        ).fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    _run_alembic(data_dir, "upgrade", "head")
    with _connect(data_dir) as db:
        assert len(_gate_rows(db)) == 5
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
