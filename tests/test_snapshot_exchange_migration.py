from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.models import UserCapability
from app.services.authorization_admin import ALLOWED_CAPABILITIES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BINDING_TABLE = "snapshot_exchange_source_bindings"
RECEIPT_TABLE = "snapshot_exchange_receipts"
REVISION = "20260806_0028"
PREVIOUS_REVISION = "20260805_0027"
CURRENT_HEAD = "20261001_0033"
CAPABILITY = "snapshot_exchange_receive"


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


def _table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
    )


def _columns(db: sqlite3.Connection, table_name: str) -> dict[str, dict[str, object]]:
    return {
        str(row[1]): {
            "type": str(row[2]).upper(),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key": bool(row[5]),
        }
        for row in db.execute(f"PRAGMA table_info({table_name})")
    }


def _foreign_keys(db: sqlite3.Connection, table_name: str) -> set[tuple[str, str, str, str]]:
    return {
        (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
        for row in db.execute(f"PRAGMA foreign_key_list({table_name})")
    }


def _index_sql(db: sqlite3.Connection, table_name: str) -> dict[str, str]:
    return {
        str(name): str(sql or "")
        for name, sql in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table_name,),
        )
    }


def _unique_column_sets(db: sqlite3.Connection, table_name: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in db.execute(f"PRAGMA index_list({table_name})"):
        if not bool(row[2]):
            continue
        columns = tuple(
            str(column[2])
            for column in db.execute(f"PRAGMA index_info('{str(row[1])}')")
            if column[2] is not None
        )
        if columns:
            result.add(columns)
    return result


def _create_source(
    db: sqlite3.Connection,
    *,
    name: str = "DEMO_A snapshot exchange source",
) -> int:
    cursor = db.execute(
        """
        INSERT INTO data_sources (
            building_id, source_type, name, owner, cadence, freshness_rule,
            is_active, created_at, updated_at
        ) VALUES (
            (SELECT id FROM buildings WHERE code='DEMO_A'),
            'external_api', ?, 'operations', 'monthly', 'same day',
            1, '2026-08-06 09:00:00', '2026-08-06 09:00:00'
        )
        """,
        (name,),
    )
    return int(cursor.lastrowid)


def _create_binding(
    db: sqlite3.Connection,
    *,
    source_id: int,
    source_system_code: str = "DEMO_A-OPS",
) -> int:
    cursor = db.execute(
        f"""
        INSERT INTO {BINDING_TABLE} (
            source_id, source_system_code, is_active, created_at, updated_at
        ) VALUES (?, ?, 1, '2026-08-06 09:00:00', '2026-08-06 09:00:00')
        """,
        (source_id, source_system_code),
    )
    return int(cursor.lastrowid)


def _create_actor(db: sqlite3.Connection, *, username: str = "exchange-operator") -> int:
    cursor = db.execute(
        "INSERT INTO users (username, password_hash, role, is_active) VALUES (?, 'x', 'editor', 1)",
        (username,),
    )
    return int(cursor.lastrowid)


def _create_candidate(
    db: sqlite3.Connection,
    *,
    source_id: int,
    actor_id: int,
    suffix: str,
    revision: int,
    as_of_date: str = "2026-08-06",
) -> tuple[int, int]:
    batch = db.execute(
        """
        INSERT INTO import_batches (
            source_id, source_file_name, external_batch_no, period, as_of_date,
            received_at, parser_version, actor_id, row_count, accepted_count,
            rejected_count, status, error_summary, created_at
        ) VALUES (
            ?, NULL, ?, '2026-08', ?,
            '2026-08-06 09:00:00', 'OPS_EXCHANGE_V1', ?, 1, 1,
            0, 'reviewed', '', '2026-08-06 09:00:00'
        )
        """,
        (source_id, f"batch-{suffix}", as_of_date, actor_id),
    )
    snapshot = db.execute(
        """
        INSERT INTO building_operating_snapshots (
            building_id, period_start, period_end, as_of_date, revision,
            validation_status, publication_status, is_current,
            submitted_by, submitted_at, created_at, updated_at
        ) VALUES (
            (SELECT id FROM buildings WHERE code='DEMO_A'),
            '2026-08-01', '2026-08-31', ?, ?,
            'valid', 'draft', 0, ?, '2026-08-06 09:00:00',
            '2026-08-06 09:00:00', '2026-08-06 09:00:00'
        )
        """,
        (as_of_date, revision, actor_id),
    )
    return int(snapshot.lastrowid), int(batch.lastrowid)


def _receipt_values(
    *,
    binding_id: int,
    actor_id: int,
    receipt_no: str,
    delivery_id: str,
    content_digest: str,
    status: str,
    source_publication_revision: int,
    original_receipt_id: int | None = None,
    correction_of_receipt_id: int | None = None,
    candidate_snapshot_id: int | None = None,
    candidate_batch_id: int | None = None,
    candidate_ref: str | None = None,
    as_of_date: str = "2026-08-06",
) -> tuple[object, ...]:
    return (
        receipt_no,
        binding_id,
        delivery_id,
        "OPS_EXCHANGE_V1",
        "OPS_V1",
        "OPS_CANONICAL_V1",
        "sha-256",
        content_digest,
        status,
        "2026-08-01",
        "2026-08-31",
        as_of_date,
        source_publication_revision,
        f"publication-{source_publication_revision}",
        correction_of_receipt_id,
        original_receipt_id,
        candidate_snapshot_id,
        candidate_batch_id,
        candidate_ref,
        '{"building_code":"DEMO_A"}',
        "[]",
        actor_id,
        "exchange-operator",
        "2026-08-06 09:00:00",
    )


def _insert_receipt(db: sqlite3.Connection, values: tuple[object, ...]) -> int:
    cursor = db.execute(
        f"""
        INSERT INTO {RECEIPT_TABLE} (
            receipt_no, binding_id, delivery_id, exchange_version,
            metric_definition_version, digest_profile, digest_algorithm,
            content_digest, status, period_start, period_end, as_of_date,
            source_publication_revision, source_publication_ref,
            correction_of_receipt_id, original_receipt_id,
            candidate_snapshot_id, candidate_batch_id, candidate_ref,
            normalized_business_json, errors_json, actor_id, actor_username,
            received_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        values,
    )
    return int(cursor.lastrowid)


def _seed_accepted_receipt(db: sqlite3.Connection) -> dict[str, int]:
    source_id = _create_source(db)
    binding_id = _create_binding(db, source_id=source_id)
    actor_id = _create_actor(db)
    snapshot_id, batch_id = _create_candidate(
        db,
        source_id=source_id,
        actor_id=actor_id,
        suffix="accepted-1",
        revision=1,
    )
    receipt_id = _insert_receipt(
        db,
        _receipt_values(
            binding_id=binding_id,
            actor_id=actor_id,
            receipt_no="receipt-accepted-1",
            delivery_id="delivery-1",
            content_digest="a" * 64,
            status="accepted",
            source_publication_revision=1,
            candidate_snapshot_id=snapshot_id,
            candidate_batch_id=batch_id,
            candidate_ref="candidate-1",
        ),
    )
    return {
        "source_id": source_id,
        "binding_id": binding_id,
        "actor_id": actor_id,
        "snapshot_id": snapshot_id,
        "batch_id": batch_id,
        "receipt_id": receipt_id,
    }


def test_current_head_is_the_only_alembic_head(tmp_path: Path) -> None:
    result = _run_alembic(tmp_path / "heads", "heads")
    heads = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert heads == [f"{CURRENT_HEAD} (head)"]


def test_0028_upgrade_creates_source_binding_receipt_and_capability_contract(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "structure"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == CURRENT_HEAD
        assert _table_exists(db, BINDING_TABLE)
        assert _table_exists(db, RECEIPT_TABLE)

        binding_columns = _columns(db, BINDING_TABLE)
        assert set(binding_columns) == {
            "id",
            "source_id",
            "source_system_code",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert binding_columns["id"]["primary_key"]
        for name in ("source_id", "source_system_code", "is_active", "created_at", "updated_at"):
            assert binding_columns[name]["not_null"]
        assert _foreign_keys(db, BINDING_TABLE) == {
            ("data_sources", "source_id", "id", "RESTRICT")
        }
        assert ("source_id",) in _unique_column_sets(db, BINDING_TABLE)
        binding_indexes = _index_sql(db, BINDING_TABLE)
        assert any(
            "lower(trim(source_system_code))" in sql.replace(" ", "").lower()
            for sql in binding_indexes.values()
        )

        receipt_columns = _columns(db, RECEIPT_TABLE)
        assert set(receipt_columns) == {
            "id",
            "receipt_no",
            "binding_id",
            "delivery_id",
            "exchange_version",
            "metric_definition_version",
            "digest_profile",
            "digest_algorithm",
            "content_digest",
            "status",
            "period_start",
            "period_end",
            "as_of_date",
            "source_publication_revision",
            "source_publication_ref",
            "correction_of_receipt_id",
            "original_receipt_id",
            "candidate_snapshot_id",
            "candidate_batch_id",
            "candidate_ref",
            "normalized_business_json",
            "errors_json",
            "actor_id",
            "actor_username",
            "received_at",
        }
        assert receipt_columns["id"]["primary_key"]
        for name in (
            "receipt_no",
            "binding_id",
            "delivery_id",
            "exchange_version",
            "metric_definition_version",
            "digest_profile",
            "digest_algorithm",
            "content_digest",
            "status",
            "period_start",
            "period_end",
            "as_of_date",
            "source_publication_revision",
            "source_publication_ref",
            "normalized_business_json",
            "errors_json",
            "actor_username",
            "received_at",
        ):
            assert receipt_columns[name]["not_null"]
        assert _foreign_keys(db, RECEIPT_TABLE) == {
            (BINDING_TABLE, "binding_id", "id", "RESTRICT"),
            (RECEIPT_TABLE, "correction_of_receipt_id", "id", "RESTRICT"),
            (RECEIPT_TABLE, "original_receipt_id", "id", "RESTRICT"),
            ("building_operating_snapshots", "candidate_snapshot_id", "id", "RESTRICT"),
            ("import_batches", "candidate_batch_id", "id", "RESTRICT"),
            ("users", "actor_id", "id", "SET NULL"),
        }
        unique_columns = _unique_column_sets(db, RECEIPT_TABLE)
        assert ("receipt_no",) in unique_columns
        assert ("binding_id", "delivery_id", "content_digest", "status") in unique_columns
        receipt_index_sql = "\n".join(_index_sql(db, RECEIPT_TABLE).values()).lower()
        assert "status in ('accepted', 'duplicate', 'rejected')" in receipt_index_sql
        assert "where status = 'accepted'" in receipt_index_sql
        assert "status = 'accepted' and correction_of_receipt_id is null" in receipt_index_sql
        assert "candidate_snapshot_id" in receipt_index_sql
        assert "candidate_batch_id" in receipt_index_sql
        assert "candidate_ref" in receipt_index_sql

        capability_sql = str(
            db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_capabilities'"
            ).fetchone()[0]
        )
        assert CAPABILITY in capability_sql
        db.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES ('receiver', 'x', 'viewer', 1)"
        )
        db.execute(
            """
            INSERT INTO user_capabilities (
                user_id, capability, valid_from, valid_to, granted_by, granted_at
            ) VALUES (
                (SELECT id FROM users WHERE username='receiver'), ?,
                '2026-08-06', NULL, NULL, '2026-08-06 09:00:00'
            )
            """,
            (CAPABILITY,),
        )
        db.commit()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_0028_models_and_authorization_use_the_new_explicit_capability() -> None:
    assert CAPABILITY in ALLOWED_CAPABILITIES
    capability_constraint = next(
        constraint
        for constraint in UserCapability.__table__.constraints
        if getattr(constraint, "name", None) == "ck_user_capabilities_name"
    )
    assert CAPABILITY in str(capability_constraint.sqltext)


def test_0028_upgrade_preserves_existing_capability_grants(tmp_path: Path) -> None:
    data_dir = tmp_path / "existing-capabilities"
    _run_alembic(data_dir, "upgrade", PREVIOUS_REVISION)

    with _connect(data_dir) as db:
        actor_id = _create_actor(db, username="existing-reviewer")
        cursor = db.execute(
            """
            INSERT INTO user_capabilities (
                user_id, capability, valid_from, valid_to, granted_by, granted_at
            ) VALUES (?, 'snapshot_review', '2026-08-05', NULL, NULL, '2026-08-05 09:00:00')
            """,
            (actor_id,),
        )
        capability_id = int(cursor.lastrowid)
        db.commit()

    _run_alembic(data_dir, "upgrade", "head")
    with _connect(data_dir) as db:
        assert db.execute(
            """
            SELECT id, user_id, capability, valid_from, valid_to, granted_by, granted_at
            FROM user_capabilities
            WHERE id = ?
            """,
            (capability_id,),
        ).fetchone() == (
            capability_id,
            actor_id,
            "snapshot_review",
            "2026-08-05",
            None,
            None,
            "2026-08-05 09:00:00",
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_0028_binding_codes_are_unique_after_normalization_and_keys_are_nonempty(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "binding-constraints"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        source_a = _create_source(db, name="source-a")
        source_b = _create_source(db, name="source-b")
        _create_binding(db, source_id=source_a, source_system_code="DEMO_A-OPS")
        with pytest.raises(sqlite3.IntegrityError):
            _create_binding(db, source_id=source_b, source_system_code=" demo_a-ops ")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"""
                INSERT INTO {BINDING_TABLE} (
                    source_id, source_system_code, is_active, created_at, updated_at
                ) VALUES (?, '   ', 1, '2026-08-06 09:00:00', '2026-08-06 09:00:00')
                """,
                (source_b,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _create_binding(db, source_id=source_a, source_system_code="another-code")


def test_0028_receipt_status_shapes_fail_closed(tmp_path: Path) -> None:
    data_dir = tmp_path / "receipt-shapes"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        seeded = _seed_accepted_receipt(db)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="accepted-without-candidate",
                    delivery_id="delivery-accepted-invalid",
                    content_digest="b" * 64,
                    status="accepted",
                    source_publication_revision=2,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="duplicate-without-original",
                    delivery_id="delivery-duplicate-invalid",
                    content_digest="a" * 64,
                    status="duplicate",
                    source_publication_revision=1,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="conflict-without-original",
                    delivery_id="delivery-conflict-invalid",
                    content_digest="c" * 64,
                    status="conflict",
                    source_publication_revision=1,
                ),
            )
        snapshot_id, batch_id = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="rejected-invalid",
            revision=2,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="rejected-with-candidate",
                    delivery_id="delivery-rejected-invalid",
                    content_digest="d" * 64,
                    status="rejected",
                    source_publication_revision=2,
                    candidate_snapshot_id=snapshot_id,
                    candidate_batch_id=batch_id,
                    candidate_ref="candidate-invalid",
                ),
            )

        duplicate_id = _insert_receipt(
            db,
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-duplicate-valid",
                delivery_id="delivery-duplicate-valid",
                content_digest="a" * 64,
                status="duplicate",
                source_publication_revision=1,
                original_receipt_id=seeded["receipt_id"],
            ),
        )
        conflict_id = _insert_receipt(
            db,
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-conflict-valid",
                delivery_id="delivery-1",
                content_digest="e" * 64,
                status="conflict",
                source_publication_revision=1,
                original_receipt_id=seeded["receipt_id"],
            ),
        )
        rejected_id = _insert_receipt(
            db,
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-rejected-valid",
                delivery_id="delivery-rejected-valid",
                content_digest="f" * 64,
                status="rejected",
                source_publication_revision=2,
            ),
        )
        assert duplicate_id > 0
        assert conflict_id > 0
        assert rejected_id > 0

        invalid_digest = list(
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-invalid-digest",
                delivery_id="delivery-invalid-digest",
                content_digest="g" * 64,
                status="rejected",
                source_publication_revision=3,
            )
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(db, tuple(invalid_digest))


def test_0028_exchange_receipts_are_append_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "receipt-immutability"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        seeded = _seed_accepted_receipt(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"UPDATE {RECEIPT_TABLE} SET actor_username='rewritten' WHERE id=?",
                (seeded["receipt_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"DELETE FROM {RECEIPT_TABLE} WHERE id=?",
                (seeded["receipt_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"UPDATE {BINDING_TABLE} SET source_system_code='renamed' WHERE id=?",
                (seeded["binding_id"],),
            )
        db.execute(
            f"UPDATE {BINDING_TABLE} SET is_active=0 WHERE id=?",
            (seeded["binding_id"],),
        )


def test_0028_receipt_uniqueness_separates_canonical_delivery_content_and_revision(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "receipt-uniqueness"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        seeded = _seed_accepted_receipt(db)

        second_root_snapshot, second_root_batch = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="second-root",
            revision=8,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-second-root",
                    delivery_id="delivery-second-root",
                    content_digest="2" * 64,
                    status="accepted",
                    source_publication_revision=8,
                    candidate_snapshot_id=second_root_snapshot,
                    candidate_batch_id=second_root_batch,
                    candidate_ref="candidate-second-root",
                ),
            )

        snapshot_2, batch_2 = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="same-delivery",
            revision=2,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-same-delivery",
                    delivery_id="delivery-1",
                    content_digest="b" * 64,
                    status="accepted",
                    source_publication_revision=2,
                    candidate_snapshot_id=snapshot_2,
                    candidate_batch_id=batch_2,
                    candidate_ref="candidate-same-delivery",
                ),
            )

        snapshot_3, batch_3 = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="same-content",
            revision=3,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-same-content",
                    delivery_id="delivery-same-content",
                    content_digest="a" * 64,
                    status="accepted",
                    source_publication_revision=3,
                    candidate_snapshot_id=snapshot_3,
                    candidate_batch_id=batch_3,
                    candidate_ref="candidate-same-content",
                ),
            )

        snapshot_4, batch_4 = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="same-revision",
            revision=4,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-same-revision",
                    delivery_id="delivery-same-revision",
                    content_digest="c" * 64,
                    status="accepted",
                    source_publication_revision=1,
                    candidate_snapshot_id=snapshot_4,
                    candidate_batch_id=batch_4,
                    candidate_ref="candidate-same-revision",
                ),
            )

        conflict_values = _receipt_values(
            binding_id=seeded["binding_id"],
            actor_id=seeded["actor_id"],
            receipt_no="receipt-conflict-1",
            delivery_id="delivery-1",
            content_digest="d" * 64,
            status="conflict",
            source_publication_revision=1,
            original_receipt_id=seeded["receipt_id"],
        )
        _insert_receipt(db, conflict_values)
        duplicate_conflict = list(conflict_values)
        duplicate_conflict[0] = "receipt-conflict-2"
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(db, tuple(duplicate_conflict))

        snapshot_5, batch_5 = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="revision-2",
            revision=5,
        )
        accepted_revision_2 = _insert_receipt(
            db,
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-accepted-2",
                delivery_id="delivery-2",
                content_digest="e" * 64,
                status="accepted",
                source_publication_revision=2,
                correction_of_receipt_id=seeded["receipt_id"],
                candidate_snapshot_id=snapshot_5,
                candidate_batch_id=batch_5,
                candidate_ref="candidate-2",
            ),
        )
        assert accepted_revision_2 > 0

        branch_snapshot, branch_batch = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="competing-branch",
            revision=7,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-competing-branch",
                    delivery_id="delivery-competing-branch",
                    content_digest="1" * 64,
                    status="accepted",
                    source_publication_revision=3,
                    correction_of_receipt_id=seeded["receipt_id"],
                    candidate_snapshot_id=branch_snapshot,
                    candidate_batch_id=branch_batch,
                    candidate_ref="candidate-competing-branch",
                ),
            )

        snapshot_6, batch_6 = _create_candidate(
            db,
            source_id=seeded["source_id"],
            actor_id=seeded["actor_id"],
            suffix="same-revision-next-day",
            revision=6,
            as_of_date="2026-08-07",
        )
        same_revision_next_day = _insert_receipt(
            db,
            _receipt_values(
                binding_id=seeded["binding_id"],
                actor_id=seeded["actor_id"],
                receipt_no="receipt-revision-1-next-day",
                delivery_id="delivery-revision-1-next-day",
                content_digest="f" * 64,
                status="accepted",
                source_publication_revision=1,
                candidate_snapshot_id=snapshot_6,
                candidate_batch_id=batch_6,
                candidate_ref="candidate-revision-1-next-day",
                as_of_date="2026-08-07",
            ),
        )
        assert same_revision_next_day > 0

        with pytest.raises(sqlite3.IntegrityError):
            _insert_receipt(
                db,
                _receipt_values(
                    binding_id=seeded["binding_id"],
                    actor_id=seeded["actor_id"],
                    receipt_no="receipt-reused-candidate",
                    delivery_id="delivery-reused-candidate",
                    content_digest="0" * 64,
                    status="accepted",
                    source_publication_revision=3,
                    correction_of_receipt_id=accepted_revision_2,
                    candidate_snapshot_id=snapshot_5,
                    candidate_batch_id=batch_5,
                    candidate_ref="candidate-2",
                ),
            )


@pytest.mark.parametrize("guard_case", ["binding", "receipt", "capability"])
def test_0028_refuses_downgrade_before_losing_exchange_state(
    tmp_path: Path,
    guard_case: str,
) -> None:
    data_dir = tmp_path / f"downgrade-{guard_case}"
    _run_alembic(data_dir, "upgrade", "head")

    with _connect(data_dir) as db:
        if guard_case == "binding":
            source_id = _create_source(db)
            _create_binding(db, source_id=source_id)
        elif guard_case == "receipt":
            _seed_accepted_receipt(db)
        else:
            actor_id = _create_actor(db)
            db.execute(
                """
                INSERT INTO user_capabilities (
                    user_id, capability, valid_from, valid_to, granted_by, granted_at
                ) VALUES (?, ?, '2026-08-06', NULL, NULL, '2026-08-06 09:00:00')
                """,
                (actor_id, CAPABILITY),
            )
        db.commit()

    result = _run_alembic(
        data_dir,
        "downgrade",
        PREVIOUS_REVISION,
        expect_success=False,
    )
    output = result.stdout + result.stderr
    assert "阶段3F-1降级已停止" in output

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
        assert _table_exists(db, BINDING_TABLE)
        assert _table_exists(db, RECEIPT_TABLE)
        capability_sql = str(
            db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_capabilities'"
            ).fetchone()[0]
        )
        assert CAPABILITY in capability_sql
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_0028_empty_exchange_state_can_downgrade_and_reupgrade(tmp_path: Path) -> None:
    data_dir = tmp_path / "roundtrip"
    _run_alembic(data_dir, "upgrade", "head")
    _run_alembic(data_dir, "downgrade", PREVIOUS_REVISION)

    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PREVIOUS_REVISION
        assert not _table_exists(db, RECEIPT_TABLE)
        assert not _table_exists(db, BINDING_TABLE)
        capability_sql = str(
            db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_capabilities'"
            ).fetchone()[0]
        )
        assert CAPABILITY not in capability_sql
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    _run_alembic(data_dir, "upgrade", "head")
    with _connect(data_dir) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == CURRENT_HEAD
        assert _table_exists(db, BINDING_TABLE)
        assert _table_exists(db, RECEIPT_TABLE)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
