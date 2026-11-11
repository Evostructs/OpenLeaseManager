from __future__ import annotations

import calendar
from datetime import datetime
import app.scripts.seed_portfolio_operating_demo as demo_module

import pytest

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingGateEvent,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    OperationLog,
    Portfolio,
    SourceRecord,
    User,
    UserScopeGrant,
)
from app.ops_v1 import SUBMITTABLE_CODES
from app.scripts.seed_portfolio_operating_demo import (
    DEMO_BUILDING_CODES,
    DEMO_CALCULATION_VERSION,
    DEMO_MARKER,
    PortfolioOperatingDemoConflict,
    seed_portfolio_operating_demo,
)
from app.services.building_operating_gate import resolve_building_operating_gates
from app.services.capabilities import grant_capabilities
from app.services.property_scope import SelectedPropertyScope
from app.services.published_operating_snapshot import build_published_operating_snapshot


def _actors(
    db,
    *,
    grant_permissions: bool = True,
    grant_scope: bool = True,
) -> tuple[User, User, User]:
    actors = (
        User(
            username="portfolio-demo-submitter",
            display_name="模拟数据提交人",
            password_hash="test-only",
            role="editor",
            is_active=True,
        ),
        User(
            username="portfolio-demo-reviewer",
            display_name="模拟数据复核人",
            password_hash="test-only",
            role="editor",
            is_active=True,
        ),
        User(
            username="portfolio-demo-publisher",
            display_name="模拟数据发布人",
            password_hash="test-only",
            role="admin",
            is_active=True,
        ),
    )
    db.add_all(actors)
    db.flush()
    if grant_permissions:
        grant_capabilities(db, actors[0], {"data_import_submit"})
        grant_capabilities(db, actors[1], {"snapshot_review"})
        grant_capabilities(db, actors[2], {"snapshot_publish", "platform_admin"})
    if grant_scope:
        portfolio = db.query(Portfolio).filter(
            Portfolio.code == "DEMO_OFFICE_PORTFOLIO"
        ).one()
        db.add_all(
            UserScopeGrant(
                user_id=actor.id,
                scope_type="portfolio",
                portfolio_id=portfolio.id,
                building_id=None,
                access_level="edit",
                include_future_buildings=True,
                valid_from=today_in_timezone(),
                valid_to=None,
                granted_by=None,
                granted_at=datetime.now(),
            )
            for actor in actors
        )
    if grant_permissions or grant_scope:
        db.flush()
    return actors


def _table_counts(db) -> dict[str, int]:
    return {
        "sources": db.query(DataSource).count(),
        "batches": db.query(ImportBatch).count(),
        "records": db.query(SourceRecord).count(),
        "snapshots": db.query(BuildingOperatingSnapshot).count(),
        "facts": db.query(BuildingMetricFact).count(),
        "definitions": db.query(MetricDefinition).count(),
        "gate_events": db.query(BuildingOperatingGateEvent).count(),
        "operation_logs": db.query(OperationLog).count(),
    }


def _facts_by_code(db, snapshot_id: int) -> dict[str, BuildingMetricFact]:
    rows = (
        db.query(BuildingMetricFact, MetricDefinition)
        .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
        .filter(BuildingMetricFact.snapshot_id == snapshot_id)
        .all()
    )
    return {definition.code: fact for fact, definition in rows}


def _assert_internal_consistency(facts: dict[str, BuildingMetricFact]) -> None:
    assert set(facts) == set(SUBMITTABLE_CODES)

    rentable = facts["area.rentable_sqm"]
    leased = facts["area.leased_sqm"]
    vacancy = facts["vacancy.area_sqm"]
    occupancy = facts["occupancy.area_rate"]
    assert vacancy.value == pytest.approx(rentable.value - leased.value)
    assert occupancy.numerator == pytest.approx(leased.value)
    assert occupancy.denominator == pytest.approx(rentable.value)
    assert occupancy.value == pytest.approx(leased.value / rentable.value * 100)

    receivable = facts["billing.receivable_month"]
    received = facts["collection.received_month"]
    collection_rate = facts["collection.rate_month"]
    arrears = facts["arrears.balance"]
    assert collection_rate.numerator == pytest.approx(received.value)
    assert collection_rate.denominator == pytest.approx(receivable.value)
    assert collection_rate.value == pytest.approx(received.value / receivable.value * 100)
    assert 0 <= arrears.value <= receivable.value - received.value

    rent = facts["rent.effective_daily"]
    assert rent.denominator > 0
    assert rent.numerator == pytest.approx(rent.value * rent.denominator)

    expiry_90 = facts["expiry.area_pressure_90d"]
    expiry_180 = facts["expiry.area_pressure_180d"]
    expiry_365 = facts["expiry.area_pressure_365d"]
    assert expiry_90.denominator == pytest.approx(leased.value)
    assert expiry_180.denominator == pytest.approx(leased.value)
    assert expiry_365.denominator == pytest.approx(leased.value)
    assert 0 <= expiry_90.numerator <= expiry_180.numerator <= expiry_365.numerator <= leased.value
    assert expiry_90.value == pytest.approx(expiry_90.numerator / leased.value * 100)
    assert expiry_180.value == pytest.approx(expiry_180.numerator / leased.value * 100)
    assert expiry_365.value == pytest.approx(expiry_365.numerator / leased.value * 100)

    for fact in facts.values():
        assert fact.included_buildings == 1
        assert fact.eligible_buildings == 1
        assert fact.completeness_status == "complete"
        assert fact.freshness_status == "current"
        assert fact.validation_status == "valid"
        assert fact.publication_status == "published"
        assert fact.data_depth == "snapshot"
        assert fact.estimate_share == pytest.approx(0)
        assert fact.estimate_method == ""
        assert fact.calculation_version == DEMO_CALCULATION_VERSION


def test_demo_generator_creates_consistent_traceable_snapshots_and_is_idempotent() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        target = today_in_timezone()

        result = seed_portfolio_operating_demo(
            db,
            as_of_date=target,
            submitter_id=submitter.id,
            reviewer_id=reviewer.id,
            publisher_id=publisher.id,
            open_gates=False,
        )

        assert result["data_classification"] == "simulated"
        assert result["as_of_date"] == target.isoformat()
        assert {item["building_code"] for item in result["buildings"]} == set(DEMO_BUILDING_CODES)
        assert {item["status"] for item in result["buildings"]} == {"created"}

        buildings = {
            item.code: item
            for item in db.query(Building).filter(Building.code.in_(DEMO_BUILDING_CODES)).all()
        }
        snapshots = (
            db.query(BuildingOperatingSnapshot)
            .filter(
                BuildingOperatingSnapshot.building_id.in_(tuple(item.id for item in buildings.values())),
                BuildingOperatingSnapshot.as_of_date == target,
                BuildingOperatingSnapshot.publication_status == "published",
                BuildingOperatingSnapshot.is_current.is_(True),
            )
            .all()
        )
        assert len(snapshots) == 4
        for snapshot in snapshots:
            assert snapshot.submitted_by == submitter.id
            assert snapshot.reviewed_by == reviewer.id
            assert snapshot.published_by == publisher.id
            assert len({snapshot.submitted_by, snapshot.reviewed_by, snapshot.published_by}) == 3
            assert snapshot.validation_status == "valid"
            assert snapshot.period_start == target.replace(day=1)
            assert snapshot.period_end == target.replace(
                day=calendar.monthrange(target.year, target.month)[1]
            )

            facts = _facts_by_code(db, snapshot.id)
            _assert_internal_consistency(facts)
            source_ids = {fact.source_id for fact in facts.values()}
            batch_ids = {fact.batch_id for fact in facts.values()}
            assert len(source_ids) == 1
            assert len(batch_ids) == 1
            source = db.get(DataSource, next(iter(source_ids)))
            batch = db.get(ImportBatch, next(iter(batch_ids)))
            assert source is not None and batch is not None
            assert DEMO_MARKER in source.name
            assert "模拟" in source.name
            assert "模拟" in source.owner
            assert "模拟" in source.freshness_rule
            assert DEMO_MARKER in batch.external_batch_no
            assert DEMO_MARKER in batch.source_file_name
            assert DEMO_MARKER in batch.parser_version
            assert "模拟" in batch.error_summary
            assert batch.actor_id == submitter.id
            assert batch.reviewed_by == reviewer.id
            assert batch.status == "published"
            assert batch.row_count == len(SUBMITTABLE_CODES)
            assert batch.accepted_count == len(SUBMITTABLE_CODES)
            assert batch.rejected_count == 0
            records = db.query(SourceRecord).filter(SourceRecord.batch_id == batch.id).all()
            assert len(records) == len(SUBMITTABLE_CODES)
            assert all(DEMO_MARKER in (record.external_record_id or "") for record in records)
            assert all(DEMO_MARKER in (record.source_ref or "") for record in records)
            assert all(record.business_object_type == "building_metric_fact" for record in records)
            audit_rows = (
                db.query(OperationLog)
                .filter(
                    OperationLog.target_type == "operating_snapshot",
                    OperationLog.target_id == snapshot.id,
                )
                .order_by(OperationLog.id.asc())
                .all()
            )
            assert [item.action for item in audit_rows] == [
                "snapshot_submit",
                "snapshot_review",
                "snapshot_publish",
            ]
            assert [item.operator for item in audit_rows] == [
                submitter.username,
                reviewer.username,
                publisher.username,
            ]
            assert all(item.scope_type == "building" for item in audit_rows)
            assert all(item.building_id == snapshot.building_id for item in audit_rows)
            assert all(DEMO_MARKER in item.changes for item in audit_rows)
            assert "复核意见" in audit_rows[1].changes

        assert db.query(OperationLog).filter(
            OperationLog.target_type == "operating_snapshot"
        ).count() == 12

        counts_after_first = _table_counts(db)
        repeated = seed_portfolio_operating_demo(
            db,
            as_of_date=target,
            submitter_id=submitter.id,
            reviewer_id=reviewer.id,
            publisher_id=publisher.id,
            open_gates=False,
        )
        assert {item["status"] for item in repeated["buildings"]} == {"skipped"}
        assert _table_counts(db) == counts_after_first


def test_demo_generator_rejects_unqualified_attribution_before_writing() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db, grant_permissions=False)
        before = _table_counts(db)

        with pytest.raises(ValueError, match="data_import_submit"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=today_in_timezone(),
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=False,
            )

        assert _table_counts(db) == before


def test_demo_generator_rejects_actor_without_target_building_edit_scope() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db, grant_scope=False)
        before = _table_counts(db)

        with pytest.raises(ValueError, match="目标楼宇编辑授权"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=today_in_timezone(),
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=False,
            )

        assert _table_counts(db) == before


def test_demo_generator_rejects_viewer_even_with_capability_and_edit_grant() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        submitter.role = "viewer"
        db.flush()
        before = _table_counts(db)

        with pytest.raises(ValueError, match="管理员或编辑角色"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=today_in_timezone(),
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=False,
            )

        assert _table_counts(db) == before


def test_demo_generator_rejects_incomplete_audit_chain_without_repairing_history() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        target = today_in_timezone()
        created = seed_portfolio_operating_demo(
            db,
            as_of_date=target,
            submitter_id=submitter.id,
            reviewer_id=reviewer.id,
            publisher_id=publisher.id,
            open_gates=False,
        )
        first_snapshot_id = int(created["buildings"][0]["snapshot_id"])
        review_log = (
            db.query(OperationLog)
            .filter(
                OperationLog.target_type == "operating_snapshot",
                OperationLog.target_id == first_snapshot_id,
                OperationLog.action == "snapshot_review",
            )
            .one()
        )
        review_log.changes = "审计链完整性反例"
        db.commit()
        before = _table_counts(db)

        with pytest.raises(PortfolioOperatingDemoConflict, match="审计记录"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=target,
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=False,
            )

        assert _table_counts(db) == before
        assert review_log.changes == "审计链完整性反例"


def test_non_demo_current_snapshot_conflict_has_zero_writes() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        target = today_in_timezone()
        building = db.query(Building).filter(Building.code == DEMO_BUILDING_CODES[0]).one()
        now = datetime.now()
        existing = BuildingOperatingSnapshot(
            building_id=building.id,
            period_start=target.replace(day=1),
            period_end=target.replace(day=calendar.monthrange(target.year, target.month)[1]),
            as_of_date=target,
            revision=1,
            validation_status="valid",
            publication_status="published",
            is_current=True,
            submitted_by=submitter.id,
            submitted_at=now,
            reviewed_by=reviewer.id,
            reviewed_at=now,
            published_by=publisher.id,
            published_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        db.commit()
        before = _table_counts(db)

        with pytest.raises(PortfolioOperatingDemoConflict, match="非 DEMO"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=target,
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=True,
            )

        assert _table_counts(db) == before
        assert db.get(BuildingOperatingSnapshot, existing.id).is_current is True


def test_demo_generator_opens_gates_after_validation_and_aggregates_all_four() -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        target = today_in_timezone()

        result = seed_portfolio_operating_demo(
            db,
            as_of_date=target,
            submitter_id=submitter.id,
            reviewer_id=reviewer.id,
            publisher_id=publisher.id,
            open_gates=True,
        )

        buildings = tuple(
            db.query(Building)
            .filter(Building.code.in_(DEMO_BUILDING_CODES))
            .order_by(Building.id.asc())
            .all()
        )
        gates = resolve_building_operating_gates(db, tuple(item.id for item in buildings))
        assert {decision.state for decision in gates.decisions} == {"open"}
        assert all("模拟数据已核验" in decision.reason for decision in gates.decisions)
        assert all(decision.actor_id == publisher.id for decision in gates.decisions)
        assert {item["gate_state"] for item in result["buildings"]} == {"open"}

        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        scope = SelectedPropertyScope(
            user_id=publisher.id,
            building_ids=tuple(item.id for item in buildings),
            building_codes=tuple(item.code for item in buildings),
            scope_type="portfolio",
            scope_code=portfolio.code,
            portfolio_id=portfolio.id,
            access_level="view",
            authorization_version="portfolio-demo-test",
        )
        aggregate = build_published_operating_snapshot(db, scope=scope, as_of_date=target)

        assert aggregate["data_classification"] == "simulated"
        assert aggregate["metrics"]["portfolio.building_count"]["value"] == 4
        assert aggregate["metrics"]["area.rentable_sqm"]["value"] == pytest.approx(166_000)
        assert aggregate["metrics"]["area.leased_sqm"]["value"] == pytest.approx(156_200)
        occupancy = aggregate["metrics"]["occupancy.area_rate"]
        assert occupancy["numerator"] == pytest.approx(156_200)
        assert occupancy["denominator"] == pytest.approx(166_000)
        assert occupancy["value"] == pytest.approx(156_200 / 166_000 * 100)
        assert occupancy["coverage"]["included_buildings"] == 4
        assert {
            item["building"]["code"] for item in aggregate["presentation"]["contributions"]
        } == set(DEMO_BUILDING_CODES)

        gate_event_count = db.query(BuildingOperatingGateEvent).count()
        repeated = seed_portfolio_operating_demo(
            db,
            as_of_date=target,
            submitter_id=submitter.id,
            reviewer_id=reviewer.id,
            publisher_id=publisher.id,
            open_gates=True,
        )
        assert {item["status"] for item in repeated["buildings"]} == {"skipped"}
        assert {item["gate_state"] for item in repeated["buildings"]} == {"open"}
        assert db.query(BuildingOperatingGateEvent).count() == gate_event_count


def test_gate_failure_rolls_back_new_demo_snapshots_and_prior_gate_events(monkeypatch) -> None:
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        target = today_in_timezone()
        before = _table_counts(db)
        original_record = demo_module.record_building_operating_gate
        call_count = 0

        def fail_on_second_gate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("injected gate failure")
            return original_record(*args, **kwargs)

        monkeypatch.setattr(demo_module, "record_building_operating_gate", fail_on_second_gate)

        with pytest.raises(RuntimeError, match="injected gate failure"):
            seed_portfolio_operating_demo(
                db,
                as_of_date=target,
                submitter_id=submitter.id,
                reviewer_id=reviewer.id,
                publisher_id=publisher.id,
                open_gates=True,
            )

        assert call_count == 2
        assert _table_counts(db) == before
        buildings = tuple(
            db.query(Building)
            .filter(Building.code.in_(DEMO_BUILDING_CODES))
            .order_by(Building.id.asc())
            .all()
        )
        gates = resolve_building_operating_gates(db, tuple(item.id for item in buildings))
        assert {decision.state for decision in gates.decisions} == {"observe"}
