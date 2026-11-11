from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NEW_TABLES = {
    "area_measurements",
    "authorization_control",
    "building_metric_facts",
    "building_operating_gate_events",
    "building_operating_snapshots",
    "buildings",
    "customer_building_profiles",
    "data_sources",
    "import_batches",
    "import_errors",
    "metric_definitions",
    "migration_0023_follow_up_backfills",
    "portfolio_buildings",
    "portfolios",
    "source_records",
    "user_capabilities",
    "user_authorization_revisions",
    "user_scope_grants",
    "zones",
}


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


def _connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _tower_for(sequence: int) -> str:
    return ("南塔", "北塔", "商业配套")[(sequence - 1) % 3]


def _seed_verified_legacy_baseline(db_path: Path, *, existing_room_link: bool = False) -> None:
    with _connect(db_path) as db:
        db.executemany(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, 1)",
            (
                ("legacy_admin", "test-hash", "admin"),
                ("legacy_editor", "test-hash", "editor"),
                ("legacy_viewer", "test-hash", "viewer"),
            ),
        )
        db.executemany(
            "INSERT INTO customers (name, status) VALUES (?, 'active')",
            ((f"测试客户 {sequence:03d}",) for sequence in range(1, 13)),
        )
        db.executemany(
            """
            INSERT INTO rooms (tower, floor, room_no, area)
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    _tower_for(sequence),
                    str((sequence - 1) // 10 + 1),
                    f"R{sequence:04d}",
                    100.0 + sequence,
                )
                for sequence in range(1, 25)
            ),
        )
        db.executemany(
            """
            INSERT INTO contracts
                (contract_no, customer_id, tower, room_no, tenant_name, area)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    f"LEGACY-{sequence:04d}",
                    sequence,
                    _tower_for(sequence),
                    f"R{sequence:04d}",
                    f"测试客户 {sequence:03d}",
                    100.0 + sequence,
                )
                for sequence in range(1, 13)
            ),
        )
        db.executemany(
            """
            INSERT INTO contract_rooms
                (contract_id, room_id, tower, room_no, area)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    sequence,
                    sequence if existing_room_link and sequence == 1 else None,
                    _tower_for(sequence),
                    f"R{sequence:04d}",
                    100.0 + sequence,
                )
                for sequence in range(1, 13)
            ),
        )
        db.executemany(
            """
            INSERT INTO customer_follow_ups
                (customer_id, contract_id, occurred_at, content)
            VALUES (?, ?, '2026-08-04 12:00:00', '')
            """,
            (
                (1, 1),
                (2, 2),
                (3, 3),
                # 无合同跟进不猜楼，应保留空范围等待人工分类。
                (4, None),
            ),
        )
        db.executemany(
            """
            INSERT INTO attachments
                (entity_type, entity_id, file_name, storage_path)
            VALUES ('contract', ?, ?, ?)
            """,
            (
                (
                    ((sequence - 1) % 12) + 1,
                    f"legacy-{sequence:03d}.pdf",
                    f"legacy/{sequence:03d}.pdf",
                )
                for sequence in range(1, 19)
            ),
        )
        db.executemany(
            "INSERT INTO operation_logs (contract_id, action, changes) VALUES (?, 'create', '')",
            ((((sequence - 1) % 12) + 1,) for sequence in range(1, 17)),
        )
        # 旧表的 batch 扩展必须在存在下游外键数据时仍安全。
        db.execute(
            """
            INSERT INTO receivable_records
                (contract_id, period_month, received_amount)
            VALUES (1, '2026-01', 100.00)
            """
        )
        db.execute(
            """
            INSERT INTO receivable_payments
                (contract_id, period_month, amount)
            VALUES (1, '2026-01', 100.00)
            """
        )
        db.execute(
            """
            INSERT INTO receivable_statements
                (contract_id, period_month, rent, property_fee, total_amount)
            VALUES (1, '2026-01', 80.00, 20.00, 100.00)
            """
        )
        db.execute(
            """
            INSERT INTO rent_terms
                (contract_id, contract_room_id, room_no, rule_type, start_date, end_date)
            VALUES (1, 1, 'R0001', 'fixed', '2026-01-01', '2026-12-31')
            """
        )
        db.execute(
            """
            INSERT INTO contract_deposits
                (contract_id, customer_id, expected_amount, status)
            VALUES (1, 1, 1000.00, 'unpaid')
            """
        )


def _business_snapshot(db: sqlite3.Connection) -> dict[str, float | int]:
    return {
        "rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "room_area": db.execute("SELECT SUM(area) FROM rooms").fetchone()[0],
        "contracts": db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],
        "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "contract_rooms": db.execute("SELECT COUNT(*) FROM contract_rooms").fetchone()[0],
        "contract_room_area": db.execute("SELECT SUM(area) FROM contract_rooms").fetchone()[0],
        "follow_ups": db.execute("SELECT COUNT(*) FROM customer_follow_ups").fetchone()[0],
        "attachments": db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
        "operation_logs": db.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0],
        "receivable_records": db.execute("SELECT COUNT(*) FROM receivable_records").fetchone()[0],
        "receivable_payments": db.execute("SELECT COUNT(*) FROM receivable_payments").fetchone()[0],
        "receivable_statements": db.execute("SELECT COUNT(*) FROM receivable_statements").fetchone()[0],
        "rent_terms": db.execute("SELECT COUNT(*) FROM rent_terms").fetchone()[0],
        "contract_deposits": db.execute("SELECT COUNT(*) FROM contract_deposits").fetchone()[0],
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
    }


def _schema_fingerprint(db_path: Path) -> dict[str, dict[str, object]]:
    """Capture the legacy-facing structure without relying on raw SQLite DDL text."""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        inspector = inspect(engine)
        fingerprint: dict[str, dict[str, object]] = {}
        for table_name in sorted(inspector.get_table_names()):
            if table_name == "alembic_version":
                continue
            fingerprint[table_name] = {
                "columns": {
                    (
                        column["name"],
                        str(column["type"]),
                        bool(column["nullable"]),
                        column.get("default"),
                        int(column.get("primary_key", 0)),
                    )
                    for column in inspector.get_columns(table_name)
                },
                "primary_key": tuple(
                    inspector.get_pk_constraint(table_name).get("constrained_columns")
                    or ()
                ),
                "indexes": {
                    (
                        index.get("name"),
                        tuple(index.get("column_names") or ()),
                        bool(index.get("unique")),
                    )
                    for index in inspector.get_indexes(table_name)
                },
                "unique_constraints": {
                    (
                        constraint.get("name"),
                        tuple(constraint.get("column_names") or ()),
                    )
                    for constraint in inspector.get_unique_constraints(table_name)
                },
                "foreign_keys": {
                    (
                        foreign_key.get("name"),
                        tuple(foreign_key.get("constrained_columns") or ()),
                        foreign_key.get("referred_table"),
                        tuple(foreign_key.get("referred_columns") or ()),
                        tuple(sorted((foreign_key.get("options") or {}).items())),
                    )
                    for foreign_key in inspector.get_foreign_keys(table_name)
                },
                "check_constraints": {
                    (
                        constraint.get("name"),
                        " ".join((constraint.get("sqltext") or "").split()),
                    )
                    for constraint in inspector.get_check_constraints(table_name)
                },
            }
        return fingerprint
    finally:
        engine.dispose()


def _assert_legacy_schema_preserved(
    legacy: dict[str, dict[str, object]],
    candidate: dict[str, dict[str, object]],
) -> None:
    assert legacy.keys() <= candidate.keys()
    for table_name, expected in legacy.items():
        actual = candidate[table_name]
        actual_columns = {column[0]: column for column in actual["columns"]}
        for expected_column in expected["columns"]:
            name, type_name, nullable, default, primary_key = expected_column
            assert name in actual_columns
            actual_column = actual_columns[name]
            assert actual_column[1] == type_name
            # 从可空收紧为非空属于兼容性强化；反向放宽则不允许静默通过。
            assert nullable or not actual_column[2]
            assert actual_column[3:] == (default, primary_key)
        assert expected["primary_key"] == actual["primary_key"]
        for expected_index in expected["indexes"]:
            if table_name == "rooms" and expected_index[0] == "ix_rooms_room_no":
                assert any(
                    index[:2] == expected_index[:2]
                    for index in actual["indexes"]
                )
            else:
                assert expected_index in actual["indexes"]
        assert expected["unique_constraints"] <= actual["unique_constraints"]
        assert expected["foreign_keys"] <= actual["foreign_keys"]
        assert expected["check_constraints"] <= actual["check_constraints"]


def _assert_database_ok(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def _assert_gate_head(
    db: sqlite3.Connection,
    *,
    upgrade_started_at: datetime | None = None,
    upgrade_finished_at: datetime | None = None,
) -> None:
    rows = db.execute(
        """
        SELECT b.code, g.revision, g.presentation_enabled, g.contribution_enabled,
               g.origin, g.actor_id, g.previous_event_id, g.created_at
        FROM building_operating_gate_events g
        JOIN buildings b ON b.id=g.building_id
        ORDER BY b.code
        """
    ).fetchall()
    assert [row[:7] for row in rows] == [
        ("DEMO_A", 1, 1, 1, "migration", None, None),
        ("DEMO_B", 1, 1, 0, "migration", None, None),
        ("DEMO_C", 1, 1, 0, "migration", None, None),
        ("DEMO_D", 1, 1, 0, "migration", None, None),
        ("DEMO_E", 1, 1, 0, "migration", None, None),
    ]
    if upgrade_started_at is not None and upgrade_finished_at is not None:
        # The migration deliberately stores second-precision timestamps, so the
        # lower bound must use the same precision when the migration finishes
        # within the start second.
        upgrade_started_at = upgrade_started_at.replace(microsecond=0)
        assert all(
            upgrade_started_at <= datetime.fromisoformat(row[7]) <= upgrade_finished_at
            for row in rows
        )
    assert {
        (("building_id",), "buildings", ("id",)),
        (("actor_id",), "users", ("id",)),
        (
            ("building_id", "previous_event_id"),
            "building_operating_gate_events",
            ("building_id", "id"),
        ),
    } <= _foreign_key_shapes(db, "building_operating_gate_events")
    assert {
        ("buildings", "building_id", "id", "RESTRICT"),
        ("users", "actor_id", "id", "RESTRICT"),
        ("building_operating_gate_events", "building_id", "building_id", "RESTRICT"),
        ("building_operating_gate_events", "previous_event_id", "id", "RESTRICT"),
    } <= {
        (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
        for row in db.execute("PRAGMA foreign_key_list(building_operating_gate_events)")
    }


def test_verified_demo_a_baseline_upgrades_downgrades_and_reupgrades(tmp_path: Path) -> None:
    data_dir = tmp_path / "verified-upgrade"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    _seed_verified_legacy_baseline(db_path)
    legacy_schema = _schema_fingerprint(db_path)

    with _connect(db_path) as db:
        before = _business_snapshot(db)
        assert before == {
            "rooms": 24,
            "room_area": 2700.0,
            "contracts": 12,
            "customers": 12,
            "contract_rooms": 12,
            "contract_room_area": 1278.0,
            "follow_ups": 4,
            "attachments": 18,
            "operation_logs": 16,
            "receivable_records": 1,
            "receivable_payments": 1,
            "receivable_statements": 1,
            "rent_terms": 1,
            "contract_deposits": 1,
            "users": 3,
        }
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NOT NULL"
        ).fetchone()[0] == 0

    upgrade_started_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    # Preserve the legacy-name baseline; 0033 has its own migration checks.
    _run_alembic(data_dir, "upgrade", "20260928_0032")
    upgrade_finished_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    _assert_legacy_schema_preserved(legacy_schema, _schema_fingerprint(db_path))

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        assert _business_snapshot(db) == before
        assert db.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 1
        assert db.execute(
            "SELECT name FROM portfolios WHERE code='DEMO_OFFICE_PORTFOLIO'"
        ).fetchone()[0] == "示例资产组合（写字楼，暂定）"
        assert db.execute(
            """
            SELECT code, name, ingestion_mode, is_provisional
            FROM buildings
            ORDER BY code
            """
        ).fetchall() == [
            ("DEMO_A", "示例双塔中心", "detail", 0),
            ("DEMO_B", "示例主裙楼中心", "snapshot", 0),
            ("DEMO_C", "示例单栋中心", "snapshot", 0),
            ("DEMO_D", "示例办公中心", "snapshot", 0),
            ("DEMO_E", "示例商务中心", "snapshot", 0),
        ]
        assert db.execute(
            "SELECT COUNT(*) FROM portfolio_buildings WHERE valid_to IS NULL"
        ).fetchone()[0] == 5
        building_id = db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM zones WHERE building_id=?", (building_id,)).fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM rooms WHERE building_id=? AND zone_id IS NOT NULL", (building_id,)).fetchone()[0] == 24
        assert db.execute("SELECT COUNT(*) FROM contracts WHERE building_id=?", (building_id,)).fetchone()[0] == 12
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN contracts c ON c.id = cr.contract_id
            JOIN rooms r ON r.id = cr.room_id
            WHERE cr.building_id = ?
              AND c.building_id = cr.building_id
              AND r.building_id = cr.building_id
              AND r.room_no = cr.room_no
            """,
            (building_id,),
        ).fetchone()[0] == 12
        assert db.execute(
            "SELECT COUNT(*) FROM customer_follow_ups WHERE building_id=?",
            (building_id,),
        ).fetchone()[0] == 4
        assert db.execute(
            "SELECT COUNT(*) FROM customer_follow_ups WHERE building_id IS NULL"
        ).fetchone()[0] == 0
        for table_name, required_columns in {
            "contracts": {"building_id"},
            "rooms": {"building_id", "zone_id", "room_no"},
            "contract_rooms": {"building_id", "room_id", "room_no"},
            "customer_follow_ups": {"building_id"},
        }.items():
            column_nullability = {
                row[1]: bool(row[3])
                for row in db.execute(f"PRAGMA table_info({table_name})")
            }
            assert all(column_nullability[column] for column in required_columns)
        assert db.execute(
            "SELECT COUNT(*) FROM attachments WHERE scope_type IS NULL AND portfolio_id IS NULL AND building_id IS NULL"
        ).fetchone()[0] == 18
        assert db.execute(
            "SELECT COUNT(*) FROM operation_logs WHERE scope_type IS NULL AND portfolio_id IS NULL AND building_id IS NULL"
        ).fetchone()[0] == 16
        assert db.execute("SELECT COUNT(*) FROM user_scope_grants").fetchone()[0] == 3
        assert dict(
            db.execute(
                "SELECT access_level, COUNT(*) FROM user_scope_grants GROUP BY access_level"
            ).fetchall()
        ) == {"edit": 2, "view": 1}
        assert db.execute("SELECT COUNT(*) FROM user_capabilities").fetchone()[0] == 2
        assert {
            row[0]
            for row in db.execute(
                """
                SELECT capability
                FROM user_capabilities
                WHERE user_id=(SELECT id FROM users WHERE username='legacy_admin')
                """
            )
        } == {"platform_admin", "customer_master_manage"}
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert EXPECTED_NEW_TABLES <= tables
        _assert_gate_head(
            db,
            upgrade_started_at=upgrade_started_at,
            upgrade_finished_at=upgrade_finished_at,
        )
        indexes = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {
            "ix_attachments_entity",
            "ux_attachments_storage_path",
            "ux_buildings_code_normalized",
            "ux_portfolios_code_normalized",
        } <= indexes
        control_updated_at = datetime.fromisoformat(db.execute(
            "SELECT updated_at FROM authorization_control WHERE id=1"
        ).fetchone()[0])
        assert upgrade_started_at.replace(microsecond=0) <= control_updated_at <= upgrade_finished_at
        assert db.execute(
            "SELECT global_revision FROM authorization_control WHERE id=1"
        ).fetchone()[0] == 0
        assert "authorization_revision" in {
            row[1] for row in db.execute("PRAGMA table_info(users)")
        }
        revision_foreign_keys = {
            row[3]: row[6]
            for row in db.execute("PRAGMA foreign_key_list(user_authorization_revisions)")
        }
        assert revision_foreign_keys == {
            "operator_user_id": "RESTRICT",
            "target_user_id": "RESTRICT",
        }
        _assert_database_ok(db)

    _run_alembic(data_dir, "downgrade", "20260610_0018")

    assert _schema_fingerprint(db_path) == legacy_schema

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260610_0018"
        assert _business_snapshot(db) == before
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NOT NULL"
        ).fetchone()[0] == 0
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert EXPECTED_NEW_TABLES.isdisjoint(tables)
        assert "building_id" not in {
            row[1] for row in db.execute("PRAGMA table_info(contracts)")
        }
        assert "zone_id" not in {
            row[1] for row in db.execute("PRAGMA table_info(rooms)")
        }
        indexes = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"ix_attachments_entity", "ux_attachments_storage_path"} <= indexes
        _assert_database_ok(db)

    _run_alembic(data_dir, "upgrade", "20260928_0032")
    _assert_legacy_schema_preserved(legacy_schema, _schema_fingerprint(db_path))
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        assert _business_snapshot(db) == before
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NOT NULL"
        ).fetchone()[0] == 12
        _assert_gate_head(db)
        _assert_database_ok(db)


def test_changed_fixture_counts_stop_before_seed_or_backfill(tmp_path: Path) -> None:
    data_dir = tmp_path / "changed-counts"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES ('only-user', 'test-hash', 'viewer', 1)"
        )

    result = _run_alembic(data_dir, "upgrade", "head", expect_success=False)
    assert "DEMO_A 迁移样例基线不匹配" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0019"
        assert db.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM user_scope_grants").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        _assert_database_ok(db)


def test_0022_stops_before_creating_normalized_code_indexes_on_case_conflict(tmp_path: Path) -> None:
    data_dir = tmp_path / "authorization-code-conflict"
    _run_alembic(data_dir, "upgrade", "20260805_0021")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            """
            INSERT INTO buildings
                (code, name, timezone, currency, ingestion_mode,
                 is_provisional, is_active, created_at, updated_at)
            VALUES
                ('demo_a', '大小写冲突楼', 'Asia/Shanghai', 'CNY', 'detail',
                 1, 1, '2026-08-05 01:00:00', '2026-08-05 01:00:00')
            """
        )

    result = _run_alembic(data_dir, "upgrade", "head", expect_success=False)
    assert "稳定代码存在忽略大小写/空格后的冲突" in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0021"
        assert "authorization_revision" not in {
            row[1] for row in db.execute("PRAGMA table_info(users)")
        }
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='authorization_control'"
        ).fetchone()[0] == 0
        _assert_database_ok(db)


def test_existing_room_links_stop_before_seed_or_backfill(tmp_path: Path) -> None:
    data_dir = tmp_path / "existing-links"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    _seed_verified_legacy_baseline(db_path, existing_room_link=True)

    result = _run_alembic(data_dir, "upgrade", "head", expect_success=False)
    assert "existing_room_link=1" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0019"
        assert db.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM contracts WHERE building_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE building_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NOT NULL"
        ).fetchone()[0] == 1
        _assert_database_ok(db)


@pytest.mark.parametrize(
    ("mutation", "error_key"),
    (
        ("UPDATE contract_rooms SET room_no='MISSING' WHERE id=1", "no_match=1"),
        ("UPDATE contract_rooms SET tower='北塔' WHERE id=1", "tower_conflict=1"),
        ("UPDATE contract_rooms SET area=area+1 WHERE id=1", "area_conflict=1"),
        (
            "UPDATE contract_rooms SET room_no='   ' WHERE id=1",
            "empty_relation_room_number=1",
        ),
        (
            "UPDATE rooms SET tower='未知分区' WHERE id=24",
            "unknown_room_tower=1",
        ),
    ),
)
def test_relationship_anomalies_stop_the_migration_before_dml(
    tmp_path: Path,
    mutation: str,
    error_key: str,
) -> None:
    data_dir = tmp_path / f"anomaly-{error_key.split('=')[0]}"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    _seed_verified_legacy_baseline(db_path)
    with _connect(db_path) as db:
        db.execute(mutation)

    result = _run_alembic(data_dir, "upgrade", "head", expect_success=False)
    assert error_key in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0019"
        assert db.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM contracts WHERE building_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM contract_rooms WHERE building_id IS NOT NULL"
        ).fetchone()[0] == 0
        _assert_database_ok(db)


def test_0020_downgrade_stops_before_clearing_stage1c_scope_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "downgrade-stage1c-guard"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            """
            INSERT INTO operation_logs (scope_type, action, changes, operator)
            VALUES ('global', 'stage1c_test', '', 'test')
            """
        )

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260610_0018",
        expect_success=False,
    )
    assert "DEMO_A 安全降级已停止" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0020"
        assert db.execute(
            "SELECT COUNT(*) FROM operation_logs WHERE action='stage1c_test'"
        ).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM buildings WHERE code='DEMO_A'").fetchone()[0] == 1
        _assert_database_ok(db)


def test_0019_downgrade_stops_before_dropping_nonempty_scope_columns(tmp_path: Path) -> None:
    data_dir = tmp_path / "downgrade-foundation-guard"
    _run_alembic(data_dir, "upgrade", "20260804_0019")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            """
            INSERT INTO operation_logs (scope_type, action, changes, operator)
            VALUES ('global', 'foundation_test', '', 'test')
            """
        )

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260610_0018",
        expect_success=False,
    )
    assert "多楼宇降级已停止" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0019"
        assert db.execute(
            "SELECT COUNT(*) FROM operation_logs WHERE action='foundation_test'"
        ).fetchone()[0] == 1
        assert "scope_type" in {
            row[1] for row in db.execute("PRAGMA table_info(operation_logs)")
        }
        _assert_database_ok(db)


@pytest.mark.parametrize(
    ("mutation", "preserved_query"),
    (
        (
            "UPDATE portfolios SET code='CHANGED_PORTFOLIO'",
            "SELECT COUNT(*) FROM portfolios WHERE code='CHANGED_PORTFOLIO'",
        ),
        (
            "UPDATE portfolios SET name='changed name'",
            "SELECT COUNT(*) FROM portfolios WHERE name='changed name'",
        ),
        (
            "UPDATE buildings SET is_active=0 WHERE code='DEMO_A'",
            "SELECT COUNT(*) FROM buildings WHERE code='DEMO_A' AND is_active=0",
        ),
        (
            "UPDATE zones SET valid_from='2026-08-05' WHERE code='SOUTH_OFFICE'",
            "SELECT COUNT(*) FROM zones WHERE code='SOUTH_OFFICE' AND valid_from='2026-08-05'",
        ),
        (
            "UPDATE portfolio_buildings SET valid_to='2026-08-05'",
            "SELECT COUNT(*) FROM portfolio_buildings WHERE valid_to='2026-08-05'",
        ),
    ),
)
def test_0020_downgrade_stops_before_changed_seed_structure(
    tmp_path: Path,
    mutation: str,
    preserved_query: str,
) -> None:
    data_dir = tmp_path / f"changed-seed-{abs(hash(mutation))}"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(mutation)

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260610_0018",
        expect_success=False,
    )
    assert "DEMO_A 安全降级已停止" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0020"
        assert db.execute(preserved_query).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 3
        _assert_database_ok(db)


def test_0020_downgrade_stops_before_changed_seed_grant(tmp_path: Path) -> None:
    data_dir = tmp_path / "changed-seed-grant"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    _seed_verified_legacy_baseline(db_path)
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    with _connect(db_path) as db:
        db.execute(
            """
            UPDATE user_scope_grants
            SET access_level='view'
            WHERE user_id=(SELECT id FROM users WHERE username='legacy_admin')
            """
        )

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260610_0018",
        expect_success=False,
    )
    assert "DEMO_A 安全降级已停止" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0020"
        assert db.execute(
            """
            SELECT access_level
            FROM user_scope_grants
            WHERE user_id=(SELECT id FROM users WHERE username='legacy_admin')
            """
        ).fetchone()[0] == "view"
        assert db.execute("SELECT COUNT(*) FROM contracts WHERE building_id IS NOT NULL").fetchone()[0] == 12
        assert db.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 3
        _assert_database_ok(db)


@pytest.mark.parametrize(
    ("mutation", "preserved_query"),
    (
        (
            """
            UPDATE rooms
            SET zone_id=(SELECT id FROM zones WHERE code='NORTH_OFFICE')
            WHERE id=1
            """,
            """
            SELECT COUNT(*) FROM rooms r JOIN zones z ON z.id=r.zone_id
            WHERE r.id=1 AND z.code='NORTH_OFFICE'
            """,
        ),
        (
            "UPDATE contracts SET building_id=NULL WHERE id=1",
            "SELECT COUNT(*) FROM contracts WHERE id=1 AND building_id IS NULL",
        ),
        (
            "UPDATE contract_rooms SET room_id=NULL WHERE id=1",
            "SELECT COUNT(*) FROM contract_rooms WHERE id=1 AND room_id IS NULL",
        ),
        (
            """
            UPDATE customer_follow_ups
            SET building_id=(SELECT id FROM buildings WHERE code='DEMO_A')
            WHERE contract_id IS NULL
            """,
            """
            SELECT COUNT(*) FROM customer_follow_ups
            WHERE contract_id IS NULL AND building_id IS NOT NULL
            """,
        ),
    ),
)
def test_0020_downgrade_stops_before_changed_legacy_scope(
    tmp_path: Path,
    mutation: str,
    preserved_query: str,
) -> None:
    data_dir = tmp_path / f"changed-legacy-scope-{abs(hash(mutation))}"
    _run_alembic(data_dir, "upgrade", "20260610_0018")
    db_path = data_dir / "lease.db"
    _seed_verified_legacy_baseline(db_path)
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    _run_alembic(data_dir, "downgrade", "20260805_0022")
    with _connect(db_path) as db:
        db.execute(mutation)

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260610_0018",
        expect_success=False,
    )
    assert "DEMO_A 安全降级已停止" in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260804_0020"
        assert db.execute(preserved_query).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM buildings WHERE code='DEMO_A'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 3
        _assert_database_ok(db)


def _insert_test_building_and_zone(
    db: sqlite3.Connection,
    *,
    building_code: str,
    zone_code: str,
) -> tuple[int, int]:
    now = "2026-08-05 02:00:00"
    cursor = db.execute(
        """
        INSERT INTO buildings
            (code, name, timezone, currency, ingestion_mode,
             is_provisional, is_active, created_at, updated_at)
        VALUES (?, ?, 'Asia/Shanghai', 'CNY', 'detail', 0, 1, ?, ?)
        """,
        (building_code, f"测试楼宇 {building_code}", now, now),
    )
    building_id = int(cursor.lastrowid)
    cursor = db.execute(
        """
        INSERT INTO zones
            (building_id, code, name, zone_type, sort_order,
             valid_from, valid_to, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 'office', 0, '2026-08-05', NULL, 1, ?, ?)
        """,
        (building_id, zone_code, f"测试分区 {zone_code}", now, now),
    )
    return building_id, int(cursor.lastrowid)


def _foreign_key_shapes(db: sqlite3.Connection, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    grouped: dict[int, list[tuple[object, ...]]] = {}
    for row in db.execute(f"PRAGMA foreign_key_list({table_name})"):
        grouped.setdefault(int(row[0]), []).append(row)
    return {
        (
            tuple(str(row[3]) for row in sorted(rows, key=lambda item: int(item[1]))),
            str(rows[0][2]),
            tuple(str(row[4]) for row in sorted(rows, key=lambda item: int(item[1]))),
        )
        for rows in grouped.values()
    }


def test_0023_database_enforces_property_relationships_and_scoped_room_numbers(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "strict-property-relations"
    _run_alembic(data_dir, "upgrade", "20260805_0023")
    db_path = data_dir / "lease.db"

    with _connect(db_path) as db:
        demo_a_building_id = int(
            db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        )
        demo_a_zone_id = int(
            db.execute(
                "SELECT id FROM zones WHERE building_id=? ORDER BY id LIMIT 1",
                (demo_a_building_id,),
            ).fetchone()[0]
        )
        second_building_id, second_zone_id = _insert_test_building_and_zone(
            db,
            building_code="TEST_B2",
            zone_code="TEST_B2_OFFICE",
        )

        room_1 = int(
            db.execute(
                "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'A-101')",
                (demo_a_building_id, demo_a_zone_id),
            ).lastrowid
        )
        room_2 = int(
            db.execute(
                "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'A-101')",
                (second_building_id, second_zone_id),
            ).lastrowid
        )

        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'A-101')",
                (demo_a_building_id, demo_a_zone_id),
            )
        for invalid_values in (
            (None, demo_a_zone_id, "A-102"),
            (demo_a_building_id, None, "A-102"),
            (demo_a_building_id, demo_a_zone_id, None),
            (demo_a_building_id, demo_a_zone_id, "   "),
            (demo_a_building_id, second_zone_id, "A-102"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, ?)",
                    invalid_values,
                )

        contract_1 = int(
            db.execute(
                "INSERT INTO contracts (contract_no, building_id) VALUES ('STRICT-C1', ?)",
                (demo_a_building_id,),
            ).lastrowid
        )
        contract_2 = int(
            db.execute(
                "INSERT INTO contracts (contract_no, building_id) VALUES ('STRICT-C2', ?)",
                (second_building_id,),
            ).lastrowid
        )
        db.execute(
            """
            INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
            VALUES (?, ?, ?, 'A-101')
            """,
            (contract_1, demo_a_building_id, room_1),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
                VALUES (?, ?, ?, 'A-101')
                """,
                (contract_2, demo_a_building_id, room_1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
                VALUES (?, ?, ?, 'A-101')
                """,
                (contract_1, demo_a_building_id, room_2),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
                VALUES (?, ?, ?, 'WRONG')
                """,
                (contract_1, demo_a_building_id, room_1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
                VALUES (?, NULL, ?, 'A-101')
                """,
                (contract_1, room_1),
            )

        db.execute("UPDATE rooms SET room_no='A-101-RENAMED' WHERE id=?", (room_1,))
        assert db.execute(
            "SELECT room_no FROM contract_rooms WHERE contract_id=?",
            (contract_1,),
        ).fetchone()[0] == "A-101-RENAMED"
        db.execute("UPDATE rooms SET room_no='A-101' WHERE id=?", (room_1,))
        assert db.execute(
            "SELECT room_no FROM contract_rooms WHERE contract_id=?",
            (contract_1,),
        ).fetchone()[0] == "A-101"

        customer_id = int(
            db.execute("INSERT INTO customers (name) VALUES ('严格约束测试客户')").lastrowid
        )
        db.execute(
            """
            INSERT INTO customer_follow_ups
                (customer_id, contract_id, building_id, occurred_at)
            VALUES (?, ?, ?, '2026-08-05 02:00:00')
            """,
            (customer_id, contract_1, demo_a_building_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO customer_follow_ups
                    (customer_id, contract_id, building_id, occurred_at)
                VALUES (?, ?, ?, '2026-08-05 02:00:00')
                """,
                (customer_id, contract_1, second_building_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO customer_follow_ups
                    (customer_id, contract_id, building_id, occurred_at)
                VALUES (?, NULL, NULL, '2026-08-05 02:00:00')
                """,
                (customer_id,),
            )

        assert (
            (("building_id", "zone_id"), "zones", ("building_id", "id"))
            in _foreign_key_shapes(db, "rooms")
        )
        assert {
            (("building_id", "contract_id"), "contracts", ("building_id", "id")),
            (
                ("building_id", "room_id", "room_no"),
                "rooms",
                ("building_id", "id", "room_no"),
            ),
        } <= _foreign_key_shapes(db, "contract_rooms")
        assert any(
            row[2] == "rooms" and row[5] == "CASCADE"
            for row in db.execute("PRAGMA foreign_key_list(contract_rooms)")
        )
        _assert_database_ok(db)

    # 0022 无法表示跨楼重复房号；降级必须停住，不能删行或改房号蒙混通过。
    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260805_0022",
        expect_success=False,
    )
    assert "duplicate_room_no=1" in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0023"
        assert db.execute("SELECT COUNT(*) FROM rooms WHERE room_no='A-101'").fetchone()[0] == 2
        _assert_database_ok(db)


def test_0023_preflight_reports_all_anomalies_before_structural_change(tmp_path: Path) -> None:
    data_dir = tmp_path / "strict-preflight"
    _run_alembic(data_dir, "upgrade", "20260805_0022")
    db_path = data_dir / "lease.db"

    with _connect(db_path) as db:
        demo_a_building_id = int(
            db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        )
        demo_a_zone_id = int(
            db.execute(
                "SELECT id FROM zones WHERE building_id=? ORDER BY id LIMIT 1",
                (demo_a_building_id,),
            ).fetchone()[0]
        )
        second_building_id, second_zone_id = _insert_test_building_and_zone(
            db,
            building_code="ANOMALY_B2",
            zone_code="ANOMALY_B2_OFFICE",
        )
        db.execute("DROP INDEX ix_rooms_room_no")
        db.execute(
            "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'DUP-1')",
            (demo_a_building_id, demo_a_zone_id),
        )
        duplicate_room_id = int(
            db.execute(
                "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'DUP-1')",
                (demo_a_building_id, demo_a_zone_id),
            ).lastrowid
        )
        db.execute(
            "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, ?, 'CROSS-ZONE')",
            (demo_a_building_id, second_zone_id),
        )
        db.execute(
            "INSERT INTO rooms (building_id, zone_id, room_no) VALUES (?, NULL, 'NULL-ZONE')",
            (demo_a_building_id,),
        )
        db.execute(
            "INSERT INTO contracts (contract_no, building_id) VALUES ('NULL-BUILDING', NULL)"
        )
        second_contract_id = int(
            db.execute(
                "INSERT INTO contracts (contract_no, building_id) VALUES ('CROSS-CONTRACT', ?)",
                (second_building_id,),
            ).lastrowid
        )
        db.execute(
            """
            INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
            VALUES (?, ?, ?, 'DUP-1')
            """,
            (second_contract_id, demo_a_building_id, duplicate_room_id),
        )
        customer_id = int(
            db.execute("INSERT INTO customers (name) VALUES ('预检异常测试客户')").lastrowid
        )
        db.execute(
            """
            INSERT INTO customer_follow_ups
                (customer_id, contract_id, building_id, occurred_at)
            VALUES (?, ?, ?, '2026-08-05 02:00:00')
            """,
            (customer_id, second_contract_id, demo_a_building_id),
        )

    result = _run_alembic(data_dir, "upgrade", "head", expect_success=False)
    for expected_count in (
        "contracts_null_building=1",
        "rooms_null_zone=1",
        "rooms_duplicate_per_building=1",
        "rooms_cross_building_zone=1",
        "contract_rooms_cross_building_contract=1",
        "follow_ups_cross_building_contract=1",
    ):
        assert expected_count in result.stderr

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0022"
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table' AND name='migration_0023_follow_up_backfills'
            """
        ).fetchone()[0] == 0
        assert {
            row[1]: bool(row[3])
            for row in db.execute("PRAGMA table_info(contracts)")
        }["building_id"] is False
        _assert_database_ok(db)
