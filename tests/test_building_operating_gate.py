from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal
from app.core.time import now_in_timezone
from app.main import app
from app.models import Building, BuildingOperatingGateEvent, OperationLog, User
from app.services.building_operating_gate import (
    record_building_operating_gate,
    resolve_building_operating_gates,
)


def test_missing_events_use_safe_defaults_and_a_stable_scope_version() -> None:
    with SessionLocal() as db:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = db.query(Building).filter(Building.code == "DEMO_B").one()
        before_any_seed = datetime(2000, 1, 1, 0, 0, 0)

        first = resolve_building_operating_gates(
            db,
            (demo_a.id, other.id),
            evaluated_at=before_any_seed,
        )
        reordered = resolve_building_operating_gates(
            db,
            (other.id, demo_a.id),
            evaluated_at=before_any_seed,
        )

    assert first.by_building_id[demo_a.id].state == "open"
    assert first.by_building_id[demo_a.id].presentation_enabled is True
    assert first.by_building_id[demo_a.id].contribution_enabled is True
    assert first.by_building_id[other.id].state == "closed"
    assert first.by_building_id[other.id].presentation_enabled is False
    assert first.by_building_id[other.id].contribution_enabled is False
    assert first.policy_version == reordered.policy_version
    assert first.policy_version.startswith("building-gates-v1-")


def test_record_appends_audited_decision_and_changes_the_policy_version() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        actor = User(
            username="gate-admin",
            display_name="开闸管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        before = resolve_building_operating_gates(db, (building.id,))

        decision = record_building_operating_gate(
            db,
            "DEMO_B",
            actor,
            presentation_enabled=True,
            contribution_enabled=True,
            reason="模拟经营数据已完成板块合计验收",
            expected_revision=1,
        )
        after = resolve_building_operating_gates(db, (building.id,))
        replayed_before_change = resolve_building_operating_gates(
            db,
            (building.id,),
            evaluated_at=before.evaluated_at,
        )
        audit = (
            db.query(OperationLog)
            .filter(
                OperationLog.building_id == building.id,
                OperationLog.target_type == "building_operating_gate",
                OperationLog.target_id == decision.event_id,
            )
            .one()
        )
        stored = db.get(BuildingOperatingGateEvent, decision.event_id)

    assert decision.revision == 2
    assert decision.previous_event_id is not None
    assert decision.state == "open"
    assert decision.presentation_enabled is True
    assert decision.contribution_enabled is True
    assert stored is not None
    assert stored.reason == "模拟经营数据已完成板块合计验收"
    assert audit.action == "gate_change"
    assert audit.operator == "gate-admin"
    assert "模拟经营数据已完成板块合计验收" in audit.changes
    assert after.by_building_id[building.id] == decision
    assert after.policy_version != before.policy_version
    assert replayed_before_change.by_building_id[building.id].revision == 1
    assert replayed_before_change.policy_version == before.policy_version


def test_resolution_exposes_all_four_independent_gate_states() -> None:
    with SessionLocal() as db:
        buildings = {
            item.code: item
            for item in db.query(Building)
            .filter(Building.code.in_(("DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D")))
            .all()
        }
        actor = User(
            username="four-state-admin",
            display_name="四态管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        record_building_operating_gate(
            db,
            "DEMO_C",
            actor,
            presentation_enabled=False,
            contribution_enabled=True,
            reason="仅保留板块合计贡献",
            expected_revision=1,
        )
        record_building_operating_gate(
            db,
            "DEMO_D",
            actor,
            presentation_enabled=False,
            contribution_enabled=False,
            reason="模拟数据重新校验期间完全撤闸",
            expected_revision=1,
        )

        resolved = resolve_building_operating_gates(
            db,
            tuple(item.id for item in buildings.values()),
        )

    states = {
        code: resolved.by_building_id[building.id].state
        for code, building in buildings.items()
    }
    assert states == {
        "DEMO_A": "open",
        "DEMO_B": "observe",
        "DEMO_C": "aggregate_only",
        "DEMO_D": "closed",
    }


def test_record_rejects_noop_and_stale_revision_without_writing_history() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        actor = User(
            username="cas-admin",
            display_name="并发管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        event_count = db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count()
        log_count = db.query(OperationLog).filter_by(building_id=building.id).count()

        with pytest.raises(HTTPException) as noop:
            record_building_operating_gate(
                db,
                "DEMO_B",
                actor,
                presentation_enabled=True,
                contribution_enabled=False,
                reason="重复提交观察态",
                expected_revision=1,
            )
        with pytest.raises(HTTPException) as stale:
            record_building_operating_gate(
                db,
                "DEMO_B",
                actor,
                presentation_enabled=True,
                contribution_enabled=True,
                reason="使用过期修订号尝试开闸",
                expected_revision=0,
            )

        assert db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count() == event_count
        assert db.query(OperationLog).filter_by(building_id=building.id).count() == log_count

    assert noop.value.status_code == 409
    assert stale.value.status_code == 409


def test_record_rejects_string_booleans_without_writing_history() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        actor = User(
            username="gate-string-bool-admin",
            display_name="布尔值校验管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        event_count = db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count()

        with pytest.raises(HTTPException) as invalid:
            record_building_operating_gate(
                db,
                building.code,
                actor,
                presentation_enabled="false",
                contribution_enabled="true",
                reason="字符串不得被当作布尔值",
                expected_revision=1,
            )

        assert db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count() == event_count

    assert invalid.value.status_code == 422


def test_resolution_and_writes_fail_closed_when_history_chain_is_broken() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        seed = (
            db.query(BuildingOperatingGateEvent)
            .filter_by(building_id=building.id, revision=1)
            .one()
        )
        actor = User(
            username="broken-chain-admin",
            display_name="断链测试管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.flush()
        recorded_at = now_in_timezone() - timedelta(seconds=10)
        revision_two = BuildingOperatingGateEvent(
            building_id=building.id,
            revision=2,
            presentation_enabled=True,
            contribution_enabled=True,
            reason="正确承接第一版",
            actor_id=actor.id,
            origin="user",
            previous_event_id=seed.id,
            created_at=recorded_at,
        )
        db.add(revision_two)
        db.flush()
        db.add(
            BuildingOperatingGateEvent(
                building_id=building.id,
                revision=3,
                presentation_enabled=False,
                contribution_enabled=False,
                reason="故意绕过第二版形成断链",
                actor_id=actor.id,
                origin="user",
                previous_event_id=seed.id,
                created_at=recorded_at + timedelta(seconds=1),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as resolved:
            resolve_building_operating_gates(db, (building.id,))
        with pytest.raises(HTTPException) as written:
            record_building_operating_gate(
                db,
                building.code,
                actor,
                presentation_enabled=True,
                contribution_enabled=True,
                reason="断链时不得继续追加",
                expected_revision=3,
            )

    assert resolved.value.status_code == 409
    assert written.value.status_code == 409


def test_gate_event_and_audit_log_commit_or_roll_back_together() -> None:
    trigger_name = "trg_test_reject_gate_change_log"
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        actor = User(
            username="rollback-admin",
            display_name="回滚管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        initial_events = db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count()
        initial_logs = db.query(OperationLog).filter_by(building_id=building.id).count()
        db.execute(
            text(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON operation_logs
                WHEN NEW.action = 'gate_change'
                BEGIN
                    SELECT RAISE(ABORT, 'test audit failure');
                END
                """
            )
        )
        db.commit()
        try:
            with pytest.raises(HTTPException) as failed:
                record_building_operating_gate(
                    db,
                    "DEMO_B",
                    actor,
                    presentation_enabled=True,
                    contribution_enabled=True,
                    reason="审计日志失败时不得留下半条开闸历史",
                    expected_revision=1,
                )

            assert failed.value.status_code == 409
            assert (
                db.query(BuildingOperatingGateEvent).filter_by(building_id=building.id).count()
                == initial_events
            )
            assert db.query(OperationLog).filter_by(building_id=building.id).count() == initial_logs
        finally:
            db.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
            db.commit()


def test_two_writers_with_one_expected_revision_produce_one_decision() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        actor = User(
            username="race-admin",
            display_name="并发开闸管理员",
            password_hash="not-used",
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        actor_id = int(actor.id)
        building_id = int(building.id)

    start = Barrier(2)

    def write(reason: str):
        with SessionLocal() as worker_db:
            worker_actor = worker_db.get(User, actor_id)
            start.wait(timeout=10)
            try:
                return record_building_operating_gate(
                    worker_db,
                    "DEMO_B",
                    worker_actor,
                    presentation_enabled=True,
                    contribution_enabled=True,
                    reason=reason,
                    expected_revision=1,
                )
            except HTTPException as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ("并发开闸甲", "并发开闸乙")))

    successes = [item for item in outcomes if not isinstance(item, HTTPException)]
    conflicts = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    with SessionLocal() as db:
        assert (
            db.query(BuildingOperatingGateEvent).filter_by(building_id=building_id).count()
            == 2
        )
        assert (
            db.query(OperationLog)
            .filter_by(building_id=building_id, action="gate_change")
            .count()
            == 1
        )


def test_platform_admin_gate_api_updates_scope_version_and_rejects_stale_writes() -> None:
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/register",
            json={"username": "gate-api-admin", "password": "Admin123", "display_name": "开闸管理员"},
        ).status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"username": "gate-api-admin", "password": "Admin123"},
        ).status_code == 200

        before = client.get("/api/buildings/operating-gates")
        assert before.status_code == 200
        before_body = before.json()
        target = next(item for item in before_body["items"] if item["building_code"] == "DEMO_B")
        assert target["state"] == "observe"
        before_version = before_body["gate_policy_version"]

        changed = client.put(
            "/api/buildings/DEMO_B/operating-gate",
            json={
                "presentation_enabled": True,
                "contribution_enabled": True,
                "reason": "模拟经营数据已完成接口验收",
                "expected_revision": target["revision"],
            },
        )
        assert changed.status_code == 200
        assert changed.json()["decision"]["state"] == "open"
        assert changed.json()["gate_policy_version"] != before_version

        scope_from_me = client.get("/api/auth/me").json()["scope"]
        scope_from_buildings = client.get("/api/buildings").json()
        assert scope_from_me["gate_policy_version"] == scope_from_buildings["gate_policy_version"]
        assert changed.json()["gate_policy_version"] != before_version
        assert scope_from_me["data_classification"] == "simulated"
        assert scope_from_me["items"][0]["operating_gate"]["state"] == "open"

        stale = client.put(
            "/api/buildings/DEMO_B/operating-gate",
            json={
                "presentation_enabled": False,
                "contribution_enabled": False,
                "reason": "使用旧修订号应被拒绝",
                "expected_revision": target["revision"],
            },
        )
        assert stale.status_code == 409


def test_non_platform_admin_cannot_read_or_change_operating_gates() -> None:
    with TestClient(app) as admin, TestClient(app) as viewer:
        assert admin.post(
            "/api/auth/register",
            json={"username": "gate-owner", "password": "Admin123", "display_name": "管理员"},
        ).status_code == 200
        assert admin.post(
            "/api/auth/login",
            json={"username": "gate-owner", "password": "Admin123"},
        ).status_code == 200
        assert admin.post(
            "/api/auth/create-user",
            json={
                "username": "gate-viewer",
                "password": "Viewer123",
                "display_name": "查看人",
                "role": "viewer",
            },
        ).status_code == 200
        assert viewer.post(
            "/api/auth/login",
            json={"username": "gate-viewer", "password": "Viewer123"},
        ).status_code == 200

        assert viewer.get("/api/buildings/operating-gates").status_code == 403
        assert viewer.put(
            "/api/buildings/DEMO_B/operating-gate",
            json={
                "presentation_enabled": True,
                "contribution_enabled": True,
                "reason": "无权操作",
                "expected_revision": 1,
            },
        ).status_code == 403
