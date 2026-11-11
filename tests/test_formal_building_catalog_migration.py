from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
FORMAL_BUILDINGS = (
    ("DEMO_A", "示例双塔中心", "detail", "2026-08-04"),
    ("DEMO_B", "示例主裙楼中心", "snapshot", "2026-08-05"),
    ("DEMO_C", "示例单栋中心", "snapshot", "2026-08-05"),
    ("DEMO_D", "示例办公中心", "snapshot", "2026-08-05"),
    ("DEMO_E", "示例商务中心", "snapshot", "2026-08-05"),
)
EMPTY_BUILDING_TABLES = (
    "area_measurements",
    "building_operating_snapshots",
    "customer_building_profiles",
    "data_sources",
    "zones",
    "contracts",
    "rooms",
)


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
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _catalog_rows(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        """
        SELECT b.code, b.name, b.ingestion_mode, b.is_provisional,
               b.is_active, b.timezone, b.currency, pb.valid_from, pb.valid_to
        FROM buildings b
        JOIN portfolio_buildings pb ON pb.building_id = b.id
        JOIN portfolios p ON p.id = pb.portfolio_id
        WHERE p.code = ?
        ORDER BY b.code
        """,
        (PORTFOLIO_CODE,),
    ).fetchall()


def _grant_rows(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        """
        SELECT user_id, scope_type, portfolio_id, building_id, access_level,
               include_future_buildings, valid_from, valid_to, granted_by, granted_at
        FROM user_scope_grants
        ORDER BY id
        """
    ).fetchall()


def test_0025_seeds_exact_formal_catalog_without_grants_or_operating_data(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "formal-catalog"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active)
            VALUES ('catalog-grant-user', 'test-hash', 'editor', 1)
            """
        )
        db.execute(
            """
            INSERT INTO user_scope_grants (
                user_id, scope_type, portfolio_id, building_id, access_level,
                include_future_buildings, valid_from, valid_to, granted_by, granted_at
            ) VALUES (
                (SELECT id FROM users WHERE username='catalog-grant-user'),
                'building', NULL, (SELECT id FROM buildings WHERE code='DEMO_A'), 'edit',
                0, '2026-08-04', NULL, NULL, '2026-08-04 22:00:00'
            )
            """
        )
        db.execute(
            """
            INSERT INTO user_scope_grants (
                user_id, scope_type, portfolio_id, building_id, access_level,
                include_future_buildings, valid_from, valid_to, granted_by, granted_at
            ) VALUES (
                (SELECT id FROM users WHERE username='catalog-grant-user'),
                'portfolio', (SELECT id FROM portfolios WHERE code=?), NULL, 'view',
                0, '2026-08-04', NULL, NULL, '2026-08-04 22:05:00'
            )
            """,
            (PORTFOLIO_CODE,),
        )
        grants_before = _grant_rows(db)
        demo_a_id = int(db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0])
        demo_a_created_before, demo_a_updated_before = db.execute(
            "SELECT created_at, updated_at FROM buildings WHERE id=?",
            (demo_a_id,),
        ).fetchone()
        demo_a_counts_before = {
            table_name: int(
                db.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE building_id=?",
                    (demo_a_id,),
                ).fetchone()[0]
            )
            for table_name in EMPTY_BUILDING_TABLES
        }

    upgrade_started_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    _run_alembic(data_dir, "upgrade", "20260805_0025")
    upgrade_finished_at = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0025"
        assert _catalog_rows(db) == sorted(
            [
                (code, name, mode, 0, 1, "Asia/Shanghai", "CNY", valid_from, None)
                for code, name, mode, valid_from in FORMAL_BUILDINGS
            ]
        )
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM portfolio_buildings").fetchone()[0] == 5
        assert _grant_rows(db) == grants_before
        assert db.execute(
            "SELECT is_provisional FROM portfolios WHERE code=?",
            (PORTFOLIO_CODE,),
        ).fetchone()[0] == 1

        demo_a_id = int(db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0])
        assert {
            table_name: int(
                db.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE building_id=?",
                    (demo_a_id,),
                ).fetchone()[0]
            )
            for table_name in EMPTY_BUILDING_TABLES
        } == demo_a_counts_before

        new_ids = tuple(
            int(row[0])
            for row in db.execute(
                "SELECT id FROM buildings WHERE code <> 'DEMO_A' ORDER BY code"
            ).fetchall()
        )
        placeholders = ",".join("?" for _ in new_ids)
        for table_name in EMPTY_BUILDING_TABLES:
            assert db.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE building_id IN ({placeholders})",
                new_ids,
            ).fetchone()[0] == 0

        building_times = {
            code: (datetime.fromisoformat(created_at), datetime.fromisoformat(updated_at))
            for code, created_at, updated_at in db.execute(
                "SELECT code, created_at, updated_at FROM buildings ORDER BY code"
            ).fetchall()
        }
        migrated_at = building_times["DEMO_B"][0]
        assert upgrade_started_at.replace(microsecond=0) <= migrated_at <= upgrade_finished_at
        assert building_times["DEMO_A"] == (
            datetime.fromisoformat(demo_a_created_before),
            migrated_at,
        )
        assert datetime.fromisoformat(demo_a_updated_before) < migrated_at
        for code, _name, _mode, _valid_from in FORMAL_BUILDINGS[1:]:
            assert building_times[code] == (migrated_at, migrated_at)
        membership_times = {
            code: datetime.fromisoformat(created_at)
            for code, created_at in db.execute(
                """
                SELECT b.code, pb.created_at
                FROM portfolio_buildings pb
                JOIN buildings b ON b.id=pb.building_id
                ORDER BY b.code
                """
            ).fetchall()
        }
        assert membership_times["DEMO_A"] == datetime.fromisoformat(demo_a_created_before)
        assert {membership_times[code] for code, _name, _mode, _date in FORMAL_BUILDINGS[1:]} == {
            migrated_at
        }
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0025_round_trip_restores_0024_and_reupgrades(tmp_path: Path) -> None:
    data_dir = tmp_path / "formal-catalog-roundtrip"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"

    _run_alembic(data_dir, "upgrade", "20260805_0025")
    _run_alembic(data_dir, "downgrade", "20260805_0024")
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0024"
        assert db.execute(
            """
            SELECT name, is_provisional, ingestion_mode, created_at, updated_at
            FROM buildings WHERE code='DEMO_A'
            """
        ).fetchone() == (
            "示例双塔中心（暂定）",
            1,
            "detail",
            "2026-08-04 21:00:00",
            "2026-08-04 21:00:00",
        )
        assert db.execute(
            "SELECT valid_from, valid_to, created_at FROM portfolio_buildings"
        ).fetchone() == ("2026-08-04", None, "2026-08-04 21:00:00")
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM portfolio_buildings").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    _run_alembic(data_dir, "upgrade", "20260805_0025")
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0025"
        assert len(_catalog_rows(db)) == 5
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize(
    ("mutation", "expected_error", "preserved_query"),
    (
        (
            "UPDATE buildings SET code='DEMO_B' WHERE code='DEMO_A'",
            "正式楼宇代码冲突",
            "SELECT COUNT(*) FROM buildings WHERE code='DEMO_B' AND is_provisional=1",
        ),
        (
            """
            INSERT INTO buildings (
                code, name, timezone, currency, ingestion_mode,
                is_provisional, is_active, created_at, updated_at
            ) VALUES (
                'NAME_CONFLICT', '示例主裙楼中心', 'Asia/Shanghai', 'CNY', 'snapshot',
                0, 1, '2026-08-05 08:00:00', '2026-08-05 08:00:00'
            )
            """,
            "正式楼宇名称冲突",
            "SELECT COUNT(*) FROM buildings WHERE code='NAME_CONFLICT'",
        ),
        (
            "UPDATE buildings SET is_active=0 WHERE code='DEMO_A'",
            "正式楼宇迁移基线已变化",
            "SELECT COUNT(*) FROM buildings WHERE code='DEMO_A' AND is_active=0",
        ),
        (
            "UPDATE portfolios SET updated_at='2026-08-05 08:00:00'",
            "正式楼宇迁移基线已变化",
            "SELECT COUNT(*) FROM portfolios WHERE updated_at='2026-08-05 08:00:00'",
        ),
        (
            "UPDATE portfolio_buildings SET valid_from='2026-08-05'",
            "正式楼宇迁移基线已变化",
            "SELECT COUNT(*) FROM portfolio_buildings WHERE valid_from='2026-08-05'",
        ),
    ),
)
def test_0025_upgrade_conflicts_stop_before_dml(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
    preserved_query: str,
) -> None:
    data_dir = tmp_path / f"upgrade-conflict-{abs(hash(mutation))}"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(mutation)

    result = _run_alembic(data_dir, "upgrade", "20260805_0025", expect_success=False)
    assert expected_error in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0024"
        assert db.execute(preserved_query).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM portfolio_buildings").fetchone()[0] == 1


def test_0025_upgrade_rejects_normalized_code_variant_before_dml(tmp_path: Path) -> None:
    data_dir = tmp_path / "upgrade-normalized-code-conflict"
    _run_alembic(data_dir, "upgrade", "20260805_0024")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute("DROP INDEX ux_buildings_code_normalized")
        db.execute(
            """
            INSERT INTO buildings (
                code, name, timezone, currency, ingestion_mode,
                is_provisional, is_active, created_at, updated_at
            ) VALUES (
                ' demo_b ', '规范化编码冲突楼', 'Asia/Shanghai', 'CNY', 'snapshot',
                0, 1, '2026-08-05 08:00:00', '2026-08-05 08:00:00'
            )
            """
        )

    result = _run_alembic(data_dir, "upgrade", "20260805_0025", expect_success=False)
    assert "正式楼宇代码冲突" in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0024"
        assert db.execute(
            "SELECT name, is_provisional FROM buildings WHERE code='DEMO_A'"
        ).fetchone() == ("示例双塔中心（暂定）", 1)
        assert db.execute("SELECT COUNT(*) FROM portfolio_buildings").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("mutation", "expected_error", "preserved_query"),
    (
        (
            "UPDATE buildings SET name='被修改的楼名' WHERE code='DEMO_B'",
            "正式楼宇安全降级已停止",
            "SELECT COUNT(*) FROM buildings WHERE code='DEMO_B' AND name='被修改的楼名'",
        ),
        (
            """
            INSERT INTO zones (
                building_id, code, name, zone_type, sort_order, valid_from,
                valid_to, is_active, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                'BLOCK_ZONE', '降级阻断分区', 'office', 0, '2026-08-05',
                NULL, 1, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            """,
            "zones:DEMO_B=1",
            "SELECT COUNT(*) FROM zones WHERE code='BLOCK_ZONE'",
        ),
        (
            """
            INSERT INTO contracts (contract_no, building_id)
            VALUES ('BLOCK-CONTRACT', (SELECT id FROM buildings WHERE code='DEMO_B'));
            """,
            "contracts:DEMO_B=1",
            "SELECT COUNT(*) FROM contracts WHERE contract_no='BLOCK-CONTRACT'",
        ),
        (
            """
            INSERT INTO zones (
                building_id, code, name, zone_type, sort_order, valid_from,
                valid_to, is_active, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                'BLOCK_ROOM_ZONE', '房源阻断分区', 'office', 0, '2026-08-05',
                NULL, 1, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            INSERT INTO rooms (building_id, zone_id, room_no)
            VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                (SELECT id FROM zones WHERE code='BLOCK_ROOM_ZONE'),
                'BLOCK-ROOM'
            );
            """,
            "rooms:DEMO_B=1",
            "SELECT COUNT(*) FROM rooms WHERE room_no='BLOCK-ROOM'",
        ),
        (
            """
            INSERT INTO zones (
                building_id, code, name, zone_type, sort_order, valid_from,
                valid_to, is_active, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                'BLOCK_CR_ZONE', '合同房源阻断分区', 'office', 0, '2026-08-05',
                NULL, 1, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            INSERT INTO rooms (building_id, zone_id, room_no)
            VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                (SELECT id FROM zones WHERE code='BLOCK_CR_ZONE'),
                'BLOCK-CR'
            );
            INSERT INTO contracts (contract_no, building_id)
            VALUES ('BLOCK-CR-CONTRACT', (SELECT id FROM buildings WHERE code='DEMO_B'));
            INSERT INTO contract_rooms (contract_id, building_id, room_id, room_no)
            VALUES (
                (SELECT id FROM contracts WHERE contract_no='BLOCK-CR-CONTRACT'),
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                (SELECT id FROM rooms WHERE room_no='BLOCK-CR'),
                'BLOCK-CR'
            );
            """,
            "contract_rooms:DEMO_B=1",
            "SELECT COUNT(*) FROM contract_rooms WHERE room_no='BLOCK-CR'",
        ),
        (
            """
            INSERT INTO customers (name, status) VALUES ('降级阻断客户', 'active');
            INSERT INTO customer_follow_ups (
                customer_id, contract_id, building_id, occurred_at, content
            ) VALUES (
                (SELECT id FROM customers WHERE name='降级阻断客户'),
                NULL, (SELECT id FROM buildings WHERE code='DEMO_B'),
                '2026-08-05 09:00:00', 'downgrade blocker'
            );
            """,
            "customer_follow_ups:DEMO_B=1",
            "SELECT COUNT(*) FROM customer_follow_ups WHERE content='downgrade blocker'",
        ),
        (
            """
            INSERT INTO attachments (
                scope_type, portfolio_id, building_id, entity_type, entity_id,
                file_name, storage_path
            ) VALUES (
                'building', NULL, (SELECT id FROM buildings WHERE code='DEMO_B'),
                'building', (SELECT id FROM buildings WHERE code='DEMO_B'),
                'downgrade-blocker.pdf', 'downgrade-blocker/attachment.pdf'
            );
            """,
            "attachments:DEMO_B=1",
            "SELECT COUNT(*) FROM attachments WHERE storage_path='downgrade-blocker/attachment.pdf'",
        ),
        (
            """
            INSERT INTO operation_logs (
                scope_type, portfolio_id, building_id, target_type, target_id,
                action, changes, operator
            ) VALUES (
                'building', NULL, (SELECT id FROM buildings WHERE code='DEMO_B'),
                'building', (SELECT id FROM buildings WHERE code='DEMO_B'),
                'block', '', 'migration-test'
            );
            """,
            "operation_logs:DEMO_B=1",
            "SELECT COUNT(*) FROM operation_logs WHERE action='block'",
        ),
        (
            """
            INSERT INTO customers (name, status) VALUES ('楼宇资料阻断客户', 'active');
            INSERT INTO customer_building_profiles (
                customer_id, building_id, local_contact, local_notes, owner,
                valid_from, valid_to, created_at, updated_at
            ) VALUES (
                (SELECT id FROM customers WHERE name='楼宇资料阻断客户'),
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                '', '', '', '2026-08-05', NULL,
                '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            """,
            "customer_building_profiles:DEMO_B=1",
            "SELECT COUNT(*) FROM customer_building_profiles",
        ),
        (
            """
            INSERT INTO users (username, password_hash, role, is_active)
            VALUES ('downgrade-grant-user', 'test-hash', 'viewer', 1);
            INSERT INTO user_scope_grants (
                user_id, scope_type, portfolio_id, building_id, access_level,
                include_future_buildings, valid_from, valid_to, granted_by, granted_at
            ) VALUES (
                (SELECT id FROM users WHERE username='downgrade-grant-user'),
                'building', NULL, (SELECT id FROM buildings WHERE code='DEMO_B'),
                'view', 0, '2026-08-05', NULL, NULL, '2026-08-05 09:00:00'
            );
            """,
            "user_scope_grants:DEMO_B=1",
            "SELECT COUNT(*) FROM user_scope_grants WHERE user_id=(SELECT id FROM users WHERE username='downgrade-grant-user')",
        ),
        (
            """
            INSERT INTO data_sources (
                building_id, source_type, name, owner, cadence, freshness_rule,
                is_active, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                'manual_snapshot', 'downgrade-blocker', '', 'monthly', 'monthly',
                1, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            )
            """,
            "data_sources:DEMO_B=1",
            "SELECT COUNT(*) FROM data_sources WHERE name='downgrade-blocker'",
        ),
        (
            """
            INSERT INTO data_sources (
                building_id, source_type, name, owner, cadence, freshness_rule,
                is_active, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_A'),
                'manual_snapshot', 'area-blocker-source', '', 'monthly', 'monthly',
                1, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            INSERT INTO import_batches (
                source_id, source_file_name, as_of_date, received_at, parser_version,
                row_count, accepted_count, rejected_count, status, error_summary, created_at
            ) VALUES (
                (SELECT id FROM data_sources WHERE name='area-blocker-source'),
                'area.csv', '2026-08-05', '2026-08-05 09:00:00', 'test-v1',
                1, 0, 0, 'received', '', '2026-08-05 09:00:00'
            );
            INSERT INTO source_records (
                batch_id, row_no, record_checksum, status, created_at
            ) VALUES (
                (SELECT id FROM import_batches WHERE source_file_name='area.csv'),
                1, 'area-record-checksum', 'pending', '2026-08-05 09:00:00'
            );
            INSERT INTO area_measurements (
                building_id, zone_id, room_id, area_type, measurement_purpose,
                area_sqm, standard, boundary_method, measured_at, valid_from,
                valid_to, version, source_record_id, evidence_ref, quality_status, created_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'), NULL, NULL,
                'rentable', 'downgrade guard', 100, 'test', 'test',
                '2026-08-05', '2026-08-05', NULL, 1,
                (SELECT id FROM source_records WHERE record_checksum='area-record-checksum'),
                '', 'draft', '2026-08-05 09:00:00'
            );
            """,
            "area_measurements:DEMO_B=1",
            "SELECT COUNT(*) FROM area_measurements WHERE measurement_purpose='downgrade guard'",
        ),
        (
            """
            INSERT INTO building_operating_snapshots (
                building_id, period_start, period_end, as_of_date, revision,
                validation_status, publication_status, is_current, created_at, updated_at
            ) VALUES (
                (SELECT id FROM buildings WHERE code='DEMO_B'),
                '2026-08-01', '2026-08-31', '2026-08-05', 1,
                'pending', 'draft', 0, '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            """,
            "building_operating_snapshots:DEMO_B=1",
            "SELECT COUNT(*) FROM building_operating_snapshots WHERE building_id=(SELECT id FROM buildings WHERE code='DEMO_B')",
        ),
        (
            """
            INSERT INTO portfolios (
                code, name, is_provisional, is_active, created_at, updated_at
            ) VALUES (
                'EXTRA_PORTFOLIO', '额外板块关系', 0, 1,
                '2026-08-05 09:00:00', '2026-08-05 09:00:00'
            );
            INSERT INTO portfolio_buildings (
                portfolio_id, building_id, valid_from, valid_to, created_at
            ) VALUES (
                (SELECT id FROM portfolios WHERE code='EXTRA_PORTFOLIO'),
                (SELECT id FROM buildings WHERE code='DEMO_E'),
                '2026-08-05', NULL, '2026-08-05 09:00:00'
            );
            """,
            "portfolio_buildings:DEMO_E=2",
            "SELECT COUNT(*) FROM portfolio_buildings WHERE portfolio_id=(SELECT id FROM portfolios WHERE code='EXTRA_PORTFOLIO')",
        ),
    ),
)
def test_0025_downgrade_refuses_changed_or_referenced_seed_before_dml(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
    preserved_query: str,
) -> None:
    data_dir = tmp_path / f"downgrade-blocker-{abs(hash(mutation))}"
    _run_alembic(data_dir, "upgrade", "20260805_0025")
    db_path = data_dir / "lease.db"
    state_before: dict[str, list[tuple]] | None = None
    with _connect(db_path) as db:
        db.executescript(mutation)
        if "EXTRA_PORTFOLIO" in mutation:
            # This blocker is deliberately attached to the last building in
            # downgrade order. The triggers prove no earlier seed row even
            # reaches DML before the complete preflight rejects the downgrade.
            db.executescript(
                """
                CREATE TRIGGER test_0025_no_building_delete
                BEFORE DELETE ON buildings
                BEGIN
                    SELECT RAISE(ABORT, 'unexpected formal catalog DML');
                END;
                CREATE TRIGGER test_0025_no_membership_delete
                BEFORE DELETE ON portfolio_buildings
                BEGIN
                    SELECT RAISE(ABORT, 'unexpected formal catalog DML');
                END;
                """
            )
            state_before = {
                table_name: db.execute(
                    f"SELECT * FROM {table_name} ORDER BY id"
                ).fetchall()
                for table_name in ("buildings", "portfolios", "portfolio_buildings")
            }

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260805_0024",
        expect_success=False,
    )
    assert expected_error in result.stderr
    assert "unexpected formal catalog DML" not in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0025"
        assert db.execute(preserved_query).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM portfolio_buildings").fetchone()[0] >= 5
        if state_before is not None:
            assert {
                table_name: db.execute(
                    f"SELECT * FROM {table_name} ORDER BY id"
                ).fetchall()
                for table_name in ("buildings", "portfolios", "portfolio_buildings")
            } == state_before
