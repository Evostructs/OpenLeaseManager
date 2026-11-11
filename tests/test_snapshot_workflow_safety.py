from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier, local

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    ImportBatch,
    ImportError,
    OperationLog,
    User,
)
from app.services.property_scope import SelectedPropertyScope
import app.services.snapshot_workflow as snapshot_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "submitter", "password": "Submit123", "display_name": "提交人"},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/auth/login",
        json={"username": "submitter", "password": "Submit123"},
    )
    assert response.status_code == 200, response.text
    _grant(
        client,
        _user_id(client, "submitter"),
        [
            "platform_admin",
            "data_import_submit",
            "snapshot_review",
            "snapshot_publish",
        ],
    )
    response = client.post(
        "/api/auth/login",
        json={"username": "submitter", "password": "Submit123"},
    )
    assert response.status_code == 200, response.text


def _user_id(admin: TestClient, username: str) -> int:
    response = admin.get("/api/auth/users")
    assert response.status_code == 200, response.text
    return next(int(item["id"]) for item in response.json() if item["username"] == username)


def _create_user(admin: TestClient, username: str, password: str) -> int:
    response = admin.post(
        "/api/auth/create-user",
        json={
            "username": username,
            "password": password,
            "display_name": username,
            "role": "editor",
        },
    )
    assert response.status_code == 200, response.text
    return _user_id(admin, username)


def _grant(admin: TestClient, user_id: int, capabilities: list[str]) -> None:
    current = admin.get(f"/api/auth/authorizations/users/{user_id}")
    assert current.status_code == 200, current.text
    response = admin.put(
        f"/api/auth/authorizations/users/{user_id}",
        json={
            "expected_authorization_version": current.json()["authorization_version"],
            "grants": [
                {"scope_type": "building", "scope_code": "DEMO_A", "access_level": "edit"}
            ],
            "capabilities": capabilities,
        },
    )
    assert response.status_code == 200, response.text


def _source(client: TestClient, name: str) -> int:
    response = client.post(
        "/api/operations/snapshot-sources",
        params={"building": "DEMO_A"},
        json={
            "source_type": "manual_snapshot",
            "name": name,
            "owner": "安全测试",
            "cadence": "daily",
            "freshness_rule": "来源业务时间不得早于统计日",
        },
    )
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


def _payload(
    source_id: int,
    *,
    observed_at: datetime,
    updated_at: datetime | None = None,
    claimed_freshness: str = "current",
) -> dict:
    target = today_in_timezone()
    return {
        "source_id": source_id,
        "period_start": target.replace(day=1).isoformat(),
        "period_end": target.replace(
            day=calendar.monthrange(target.year, target.month)[1]
        ).isoformat(),
        "as_of_date": target.isoformat(),
        "source_observed_at": observed_at.isoformat(),
        "source_updated_at": updated_at.isoformat() if updated_at is not None else None,
        "parser_version": "ops-v1-safety-test",
        "facts": [
            {
                "code": "area.rentable_sqm",
                "numerator": 100,
                "covered_area_sqm": 100,
                "eligible_area_sqm": 100,
                "covered_days": 1,
                "eligible_days": 1,
                "completeness_status": "complete",
                "freshness_status": claimed_freshness,
                "calculation_version": "safety-v1",
            }
        ],
    }


def _workflow_clients() -> tuple[TestClient, TestClient, TestClient]:
    submitter = TestClient(app)
    reviewer = TestClient(app)
    publisher = TestClient(app)
    _register_admin(submitter)
    reviewer_id = _create_user(submitter, "reviewer", "Review123")
    publisher_id = _create_user(submitter, "publisher", "Publish123")
    _grant(submitter, reviewer_id, ["snapshot_review"])
    _grant(submitter, publisher_id, ["snapshot_publish"])
    assert reviewer.post(
        "/api/auth/login", json={"username": "reviewer", "password": "Review123"}
    ).status_code == 200
    assert publisher.post(
        "/api/auth/login", json={"username": "publisher", "password": "Publish123"}
    ).status_code == 200
    return submitter, reviewer, publisher


def test_server_recomputes_freshness_and_rejects_contradictory_source_times() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        source_id = _source(client, "服务端新鲜度来源")
        target = today_in_timezone()

        caller_claim_is_overwritten = client.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_payload(
                source_id,
                observed_at=datetime.combine(target, datetime.min.time()),
                claimed_freshness="stale",
            ),
        )
        assert caller_claim_is_overwritten.status_code == 200, caller_claim_is_overwritten.text
        fact = caller_claim_is_overwritten.json()["facts"][0]
        assert fact["freshness_status"] == "current"

        future_observation = client.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_payload(
                source_id,
                observed_at=now_in_timezone() + timedelta(days=1),
            ),
        )
        assert future_observation.status_code == 422, future_observation.text

        observed = now_in_timezone() - timedelta(minutes=5)
        updated_after_observed = client.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_payload(
                source_id,
                observed_at=observed,
                updated_at=observed + timedelta(minutes=1),
            ),
        )
        assert updated_after_observed.status_code == 422, updated_after_observed.text

    with SessionLocal() as db:
        error_codes = {row.error_code for row in db.query(ImportError).all()}
        assert "future_source_observed_at" in error_codes
        assert "source_updated_after_observed" in error_codes


def test_old_observation_can_be_published_but_is_stale_and_excluded_from_total() -> None:
    submitter, reviewer, publisher = _workflow_clients()
    try:
        source_id = _source(submitter, "旧观测保留来源")
        old_observation = datetime.combine(
            today_in_timezone() - timedelta(days=1),
            datetime.min.time(),
        )
        submitted = submitter.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_payload(
                source_id,
                observed_at=old_observation,
                claimed_freshness="current",
            ),
        )
        assert submitted.status_code == 200, submitted.text
        snapshot_id = int(submitted.json()["id"])
        assert submitted.json()["facts"][0]["freshness_status"] == "stale"

        reviewed = reviewer.post(
            f"/api/operations/snapshot-submissions/{snapshot_id}/review",
            params={"building": "DEMO_A"},
            json={"approved": True, "notes": "旧观测仅保留为证据"},
        )
        assert reviewed.status_code == 200, reviewed.text
        published = publisher.post(
            f"/api/operations/snapshot-submissions/{snapshot_id}/publish",
            params={"building": "DEMO_A"},
            json={"notes": "发布历史观察值"},
        )
        assert published.status_code == 200, published.text

        aggregate = publisher.get(
            "/api/operations/published-snapshot",
            params={"building": "DEMO_A", "as_of_date": today_in_timezone().isoformat()},
        )
        assert aggregate.status_code == 200, aggregate.text
        metric = aggregate.json()["metrics"]["area.rentable_sqm"]
        assert metric["value"] is None
        assert metric["coverage"]["eligible_buildings"] == 1
        assert metric["coverage"]["included_buildings"] == 0
        assert metric["coverage"]["freshness_status"] == "stale"
        assert metric["coverage"]["excluded_buildings"] == [
            {"building_code": "DEMO_A", "reason": "指标已过期"}
        ]

        with SessionLocal() as db:
            fact = (
                db.query(BuildingMetricFact)
                .filter(BuildingMetricFact.snapshot_id == snapshot_id)
                .one()
            )
            assert fact.publication_status == "published"
            assert fact.freshness_status == "stale"
            assert fact.included_buildings == 0
    finally:
        submitter.close()
        reviewer.close()
        publisher.close()


def test_review_compare_and_swap_allows_exactly_one_reviewer_without_half_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app) as admin:
        _register_admin(admin)
        reviewer_a_id = _create_user(admin, "reviewer_a", "ReviewA123")
        reviewer_b_id = _create_user(admin, "reviewer_b", "ReviewB123")
        source_id = _source(admin, "并发复核来源")
        submitted = admin.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_payload(
                source_id,
                observed_at=datetime.combine(today_in_timezone(), datetime.min.time()),
            ),
        )
        assert submitted.status_code == 200, submitted.text
        snapshot_id = int(submitted.json()["id"])

    with SessionLocal() as db:
        building_id = int(db.query(Building.id).filter(Building.code == "DEMO_A").scalar())
    scope = SelectedPropertyScope(
        user_id=0,
        building_ids=(building_id,),
        building_codes=("DEMO_A",),
        scope_type="building",
        scope_code="DEMO_A",
        portfolio_id=None,
        access_level="edit",
        authorization_version="safety-test",
    )
    barrier = Barrier(2)
    thread_state = local()
    original_snapshot_in_scope = snapshot_workflow._snapshot_in_scope

    def synchronized_snapshot_in_scope(db, requested_id, *, scope):
        snapshot = original_snapshot_in_scope(db, requested_id, scope=scope)
        if not getattr(thread_state, "initial_lookup_done", False):
            thread_state.initial_lookup_done = True
            barrier.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(
        snapshot_workflow,
        "_snapshot_in_scope",
        synchronized_snapshot_in_scope,
    )

    def review(actor_id: int) -> tuple[int, int | None]:
        with SessionLocal() as db:
            actor = db.get(User, actor_id)
            try:
                result = snapshot_workflow.review_operating_snapshot(
                    db,
                    snapshot_id,
                    scope=scope,
                    actor=actor,
                    approved=True,
                    notes=f"reviewer-{actor_id}",
                )
                return 200, int(result["reviewed_by"])
            except HTTPException as exc:
                return int(exc.status_code), None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, (reviewer_a_id, reviewer_b_id)))
    assert sorted(status for status, _reviewer in results) == [200, 409]
    winner = next(reviewer for status, reviewer in results if status == 200)

    with SessionLocal() as db:
        snapshot = db.get(BuildingOperatingSnapshot, snapshot_id)
        fact = (
            db.query(BuildingMetricFact)
            .filter(BuildingMetricFact.snapshot_id == snapshot_id)
            .one()
        )
        batch = db.get(ImportBatch, fact.batch_id)
        logs = (
            db.query(OperationLog)
            .filter(
                OperationLog.target_type == "operating_snapshot",
                OperationLog.target_id == snapshot_id,
                OperationLog.action == "snapshot_review",
            )
            .all()
        )
        assert snapshot.publication_status == "reviewed"
        assert snapshot.validation_status == "valid"
        assert snapshot.reviewed_by == winner
        assert batch.status == "reviewed"
        assert batch.reviewed_by == winner
        assert fact.validation_status == "valid"
        assert len(logs) == 1
        assert logs[0].operator in {"reviewer_a", "reviewer_b"}


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


def _upgrade_with_seeded_definitions(data_dir: Path) -> Path:
    _run_alembic(data_dir, "upgrade", "20260805_0023")
    db_path = data_dir / "lease.db"
    with _connect(db_path) as db:
        db.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active)
            VALUES ('migration_actor', 'test-hash', 'admin', 1)
            """
        )
    # 0024 history guards are checked against the legacy display-name baseline.
    _run_alembic(data_dir, "upgrade", "20260928_0032")
    return db_path


def _insert_downgrade_history(db_path: Path, history_kind: str) -> None:
    with _connect(db_path) as db:
        actor_id = int(db.execute("SELECT id FROM users WHERE username='migration_actor'").fetchone()[0])
        building_id = int(db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0])
        snapshot_status = "published" if history_kind == "snapshot" else "draft"
        snapshot_id = int(
            db.execute(
                """
                INSERT INTO building_operating_snapshots (
                    building_id, period_start, period_end, as_of_date, revision,
                    validation_status, publication_status, is_current,
                    submitted_by, submitted_at, reviewed_by, reviewed_at,
                    published_by, published_at, created_at, updated_at
                ) VALUES (
                    ?, '2026-08-01', '2026-08-31', '2026-08-05', 1,
                    'valid', ?, ?, ?, '2026-08-05 09:00:00',
                    ?, ?, ?, ?,
                    '2026-08-05 09:00:00', '2026-08-05 11:00:00'
                )
                """,
                (
                    building_id,
                    snapshot_status,
                    1 if snapshot_status == "published" else 0,
                    actor_id,
                    actor_id if snapshot_status == "published" else None,
                    "2026-08-05 10:00:00" if snapshot_status == "published" else None,
                    actor_id if snapshot_status == "published" else None,
                    "2026-08-05 11:00:00" if snapshot_status == "published" else None,
                ),
            ).lastrowid
        )
        if history_kind == "fact":
            source_id = int(
                db.execute(
                    """
                    INSERT INTO data_sources (
                        building_id, source_type, name, owner, cadence,
                        freshness_rule, is_active, created_at, updated_at
                    ) VALUES (?, 'manual_snapshot', 'migration-source', '', 'monthly',
                              'monthly', 1, '2026-08-05 09:00:00', '2026-08-05 09:00:00')
                    """,
                    (building_id,),
                ).lastrowid
            )
            definition_id = int(
                db.execute(
                    """
                    SELECT id FROM metric_definitions
                    WHERE code='area.rentable_sqm' AND version=1
                    """
                ).fetchone()[0]
            )
            db.execute(
                """
                INSERT INTO building_metric_facts (
                    snapshot_id, metric_definition_id, value, numerator, denominator,
                    source_id, eligible_buildings, included_buildings,
                    covered_days, eligible_days, estimate_method, data_depth,
                    completeness_status, freshness_status, validation_status,
                    publication_status, calculation_version, created_at
                ) VALUES (?, ?, 100, 100, NULL, ?, 1, 1, 1, 1, '', 'snapshot',
                          'complete', 'current', 'valid', 'published', 'migration-test',
                          '2026-08-05 09:00:00')
                """,
                (snapshot_id, definition_id, source_id),
            )


@pytest.mark.parametrize(
    ("history_kind", "expected_snapshots", "expected_facts"),
    (("snapshot", 1, 0), ("fact", 0, 1)),
)
def test_0024_downgrade_refuses_history_before_removing_guards(
    tmp_path: Path,
    history_kind: str,
    expected_snapshots: int,
    expected_facts: int,
) -> None:
    data_dir = tmp_path / f"downgrade-guard-{history_kind}"
    db_path = _upgrade_with_seeded_definitions(data_dir)
    _insert_downgrade_history(db_path, history_kind)

    result = _run_alembic(
        data_dir,
        "downgrade",
        "20260805_0023",
        expect_success=False,
    )
    assert f"published_or_superseded_snapshots={expected_snapshots}" in result.stderr
    assert f"published_or_superseded_facts={expected_facts}" in result.stderr
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0024"
        trigger_count = int(
            db.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='trigger' AND name LIKE 'trg_%published%'
                """
            ).fetchone()[0]
        )
        assert trigger_count == 6


def test_0024_without_publication_history_can_downgrade_and_reupgrade(tmp_path: Path) -> None:
    data_dir = tmp_path / "downgrade-empty"
    db_path = _upgrade_with_seeded_definitions(data_dir)
    _run_alembic(data_dir, "downgrade", "20260805_0023")
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260805_0023"
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%published%'"
        ).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM metric_definitions WHERE version=1").fetchone()[0] == 0

    _run_alembic(data_dir, "upgrade", "20260928_0032")
    with _connect(db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260928_0032"
        assert db.execute("SELECT COUNT(*) FROM metric_definitions WHERE version=1").fetchone()[0] > 0
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%published%'"
        ).fetchone()[0] == 6
