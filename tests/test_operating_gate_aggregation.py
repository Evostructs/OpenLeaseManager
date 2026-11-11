from __future__ import annotations

import calendar
from datetime import datetime

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
    Portfolio,
    SourceRecord,
    User,
)
from app.services.property_scope import SelectedPropertyScope
from app.services.metric_definitions import ensure_ops_v1_definitions
from app.services.published_operating_snapshot import build_published_operating_snapshot
from app.services.snapshot_evidence import build_snapshot_evidence
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


def _actor(db) -> User:
    actor = User(
        username="gate-aggregation-actor",
        display_name="开闸聚合测试人",
        password_hash="test-only",
        role="admin",
        is_active=True,
    )
    db.add(actor)
    db.flush()
    ensure_ops_v1_definitions(db, publisher=actor)
    return actor


def _append_gate(
    db,
    *,
    building: Building,
    actor: User,
    presentation_enabled: bool,
    contribution_enabled: bool,
    reason: str,
) -> None:
    previous = (
        db.query(BuildingOperatingGateEvent)
        .filter(BuildingOperatingGateEvent.building_id == building.id)
        .order_by(BuildingOperatingGateEvent.revision.desc())
        .first()
    )
    assert previous is not None
    db.add(
        BuildingOperatingGateEvent(
            building_id=building.id,
            revision=int(previous.revision) + 1,
            presentation_enabled=presentation_enabled,
            contribution_enabled=contribution_enabled,
            reason=reason,
            actor_id=actor.id,
            origin="user",
            previous_event_id=previous.id,
            created_at=datetime.now(),
        )
    )
    db.flush()


def _add_published_occupancy(
    db,
    *,
    building: Building,
    actor: User,
    numerator: float,
    denominator: float,
    suffix: str,
) -> None:
    target = today_in_timezone()
    now = datetime.now()
    source = DataSource(
        building_id=building.id,
        source_type="manual_snapshot",
        name=f"开闸聚合来源-{suffix}",
        owner="fixture",
        cadence="monthly",
        freshness_rule="月度确认",
        is_active=True,
    )
    db.add(source)
    db.flush()
    batch = ImportBatch(
        source_id=source.id,
        external_batch_no=f"GATE-{suffix}",
        period=target.strftime("%Y-%m"),
        as_of_date=target,
        source_observed_at=now,
        received_at=now,
        started_at=now,
        finished_at=now,
        ingested_at=now,
        published_at=now,
        file_checksum=f"gate-checksum-{suffix}",
        parser_version="gate-aggregation-fixture-v1",
        actor_id=actor.id,
        row_count=1,
        accepted_count=1,
        rejected_count=0,
        status="published",
        error_summary="",
        reviewed_by=actor.id,
        reviewed_at=now,
    )
    db.add(batch)
    db.flush()
    record = SourceRecord(
        batch_id=batch.id,
        row_no=1,
        record_checksum=f"gate-record-{suffix}",
        status="accepted",
        source_ref="row:1",
    )
    db.add(record)
    db.flush()
    snapshot = BuildingOperatingSnapshot(
        building_id=building.id,
        period_start=target.replace(day=1),
        period_end=target.replace(day=calendar.monthrange(target.year, target.month)[1]),
        as_of_date=target,
        revision=1,
        validation_status="valid",
        publication_status="published",
        is_current=True,
        submitted_by=actor.id,
        submitted_at=now,
        reviewed_by=actor.id,
        reviewed_at=now,
        published_by=actor.id,
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(snapshot)
    db.flush()
    definition = (
        db.query(MetricDefinition)
        .filter(MetricDefinition.code == "occupancy.area_rate", MetricDefinition.version == 1)
        .one()
    )
    fact = BuildingMetricFact(
        snapshot_id=snapshot.id,
        metric_definition_id=definition.id,
        value=numerator / denominator * 100,
        numerator=numerator,
        denominator=denominator,
        source_id=source.id,
        batch_id=batch.id,
        source_record_id=record.id,
        eligible_buildings=1,
        included_buildings=1,
        covered_area_sqm=denominator,
        eligible_area_sqm=denominator,
        covered_days=1,
        eligible_days=1,
        estimate_share=0,
        estimate_method="",
        data_depth="snapshot",
        completeness_status="complete",
        freshness_status="current",
        validation_status="valid",
        publication_status="published",
        calculation_version="gate-aggregation-fixture-v1",
        created_at=now,
    )
    db.add(fact)
    db.flush()
    record.business_object_type = "building_metric_fact"
    record.business_object_id = fact.id


def test_gate_keeps_all_contributions_but_separates_presentation_and_aggregation() -> None:
    with SessionLocal() as db:
        actor = _actor(db)
        portfolio = (
            db.query(Portfolio)
            .filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO")
            .one()
        )
        buildings = {
            item.code: item
            for item in db.query(Building)
            .filter(Building.code.in_(("DEMO_B", "DEMO_C", "DEMO_D")))
            .all()
        }
        assert set(buildings) == {"DEMO_B", "DEMO_C", "DEMO_D"}

        _append_gate(
            db,
            building=buildings["DEMO_B"],
            actor=actor,
            presentation_enabled=True,
            contribution_enabled=True,
            reason="已通过模拟数据对账，开放展示和合计",
        )
        # DEMO_C keeps the migration seed: visible for observation, excluded from totals.
        _append_gate(
            db,
            building=buildings["DEMO_D"],
            actor=actor,
            presentation_enabled=False,
            contribution_enabled=True,
            reason="反例：可合计但暂不对前端展示",
        )
        _add_published_occupancy(
            db,
            building=buildings["DEMO_B"],
            actor=actor,
            numerator=80,
            denominator=100,
            suffix="GJJT",
        )
        _add_published_occupancy(
            db,
            building=buildings["DEMO_C"],
            actor=actor,
            numerator=50,
            denominator=100,
            suffix="XSHGJ",
        )
        _add_published_occupancy(
            db,
            building=buildings["DEMO_D"],
            actor=actor,
            numerator=10,
            denominator=20,
            suffix="GZJY",
        )
        db.commit()

        ordered = tuple(buildings[code] for code in ("DEMO_B", "DEMO_C", "DEMO_D"))
        scope = SelectedPropertyScope(
            user_id=actor.id,
            building_ids=tuple(item.id for item in ordered),
            building_codes=tuple(item.code for item in ordered),
            scope_type="portfolio",
            scope_code=portfolio.code,
            portfolio_id=portfolio.id,
            access_level="view",
            authorization_version="gate-aggregation-fixture",
        )
        result = build_published_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today_in_timezone(),
        )
        unified_result = build_unified_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today_in_timezone(),
        )

    metric = result["metrics"]["occupancy.area_rate"]
    assert metric["numerator"] == 90
    assert metric["denominator"] == 120
    assert metric["value"] == pytest.approx(75)
    assert metric["coverage"]["authorized_buildings"] == 3
    assert metric["coverage"]["presentation_enabled_buildings"] == 2
    assert metric["coverage"]["contribution_enabled_buildings"] == 2
    assert metric["coverage"]["eligible_buildings"] == 2
    assert metric["coverage"]["included_buildings"] == 2
    assert metric["coverage"]["excluded_buildings"] == [
        {
            "building_code": "DEMO_C",
            "reason": "楼宇开闸策略未允许参与板块合计（状态：observe）",
        }
    ]

    by_code = {
        item["building"]["code"]: item
        for item in result["contributions"]
    }
    assert set(by_code) == {"DEMO_B", "DEMO_C", "DEMO_D"}
    assert by_code["DEMO_C"]["metrics"]["occupancy.area_rate"]["value"] == 50
    assert by_code["DEMO_C"]["contribution_enabled"] is False
    assert by_code["DEMO_C"]["operating_gate"]["state"] == "observe"
    assert by_code["DEMO_C"]["metrics"]["occupancy.area_rate"]["aggregation_eligible"] is False
    assert "开闸策略" in by_code["DEMO_C"]["reason"]
    assert by_code["DEMO_D"]["presentation_enabled"] is False
    assert by_code["DEMO_D"]["contribution_enabled"] is True

    presented_codes = {
        item["building"]["code"]
        for item in result["presentation"]["contributions"]
    }
    assert presented_codes == {"DEMO_B", "DEMO_C"}
    assert result["metrics"]["portfolio.building_count"]["value"] == 3
    assert result["coverage"]["presentation_enabled_buildings"] == 2
    assert result["coverage"]["contribution_enabled_buildings"] == 2
    assert result["data_classification"] == "simulated"
    assert result["presentation"]["data_classification"] == "simulated"
    assert result["gate_policy_version"] == result["rollout"]["policy_version"]
    assert len(result["rollout"]["decisions"]) == 3
    assert result["rollout"]["items"] == result["rollout"]["decisions"]
    adapters_by_code = {
        item["building"]["code"]: item
        for item in unified_result["adapters"]
    }
    assert adapters_by_code["DEMO_B"]["included"] is True
    assert adapters_by_code["DEMO_C"]["included"] is False
    assert "未允许参与板块合计" in adapters_by_code["DEMO_C"]["reason"]
    assert adapters_by_code["DEMO_D"]["included"] is True
    unified_profiles = {
        item["building"]["code"]: item["building"]["operations_profile"]
        for item in unified_result["contributions"]
    }
    assert unified_profiles["DEMO_B"]["space_structure"]["type"] == (
        "main_building_with_podium"
    )
    assert unified_profiles["DEMO_C"]["space_structure"]["zones"] == []
    assert "不虚构楼层或房号" in (
        unified_profiles["DEMO_C"]["space_structure"]["detail_unavailable_reason"]
    )
    assert "available_tabs" not in unified_profiles["DEMO_B"]


def test_live_demo_a_keeps_legacy_payload_and_adds_simulated_gate_metadata() -> None:
    with SessionLocal() as db:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        result = build_unified_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                user_id=1,
                building_ids=(demo_a.id,),
                building_codes=(demo_a.code,),
                scope_type="building",
                scope_code=demo_a.code,
                portfolio_id=None,
                access_level="view",
                authorization_version="demo_a-gate-fixture",
            ),
            as_of_date=today_in_timezone(),
            include_trend=False,
        )

    assert result["scope"]["building"]["code"] == "DEMO_A"
    assert result["legacy_contract_stats"] is not None
    assert result["legacy_operations"] is not None
    assert result["data_classification"] == "simulated"
    assert result["gate_policy_version"] == result["rollout"]["policy_version"]
    assert result["rollout"]["presentation_enabled_buildings"] == 1
    assert result["rollout"]["contribution_enabled_buildings"] == 1
    assert result["rollout"]["items"] == result["rollout"]["decisions"]
    contribution = result["contributions"][0]
    assert contribution["building"]["code"] == "DEMO_A"
    assert result["scope"]["operations_profile"]["building_code"] == "DEMO_A"
    assert contribution["building"]["operations_profile"] == (
        result["scope"]["operations_profile"]
    )
    assert contribution["presentation_enabled"] is True
    assert contribution["contribution_enabled"] is True
    assert contribution["operating_gate"]["state"] == "open"
    assert contribution["data_classification"] == "simulated"
    assert result["presentation"]["contributions"] == [contribution]


def test_live_demo_a_single_building_remains_included_when_portfolio_gate_is_observe() -> None:
    with SessionLocal() as db:
        actor = _actor(db)
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        _append_gate(
            db,
            building=demo_a,
            actor=actor,
            presentation_enabled=True,
            contribution_enabled=False,
            reason="单楼仍可查看，暂不参加板块合计",
        )
        db.commit()
        result = build_unified_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                user_id=actor.id,
                building_ids=(demo_a.id,),
                building_codes=(demo_a.code,),
                scope_type="building",
                scope_code=demo_a.code,
                portfolio_id=None,
                access_level="view",
                authorization_version="demo_a-observe-fixture",
            ),
            as_of_date=today_in_timezone(),
            include_trend=False,
        )

    assert result["adapters"][0]["included"] is True
    assert result["contributions"][0]["contribution_enabled"] is False
    assert result["legacy_operations"]["projection"]["detail_lists_available"] is True
    assert result["legacy_contract_stats"] is not None


def test_observe_building_without_snapshot_keeps_both_missing_and_gate_reasons() -> None:
    with SessionLocal() as db:
        actor = _actor(db)
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        result = build_published_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                user_id=actor.id,
                building_ids=(building.id,),
                building_codes=(building.code,),
                scope_type="portfolio",
                scope_code=portfolio.code,
                portfolio_id=portfolio.id,
                access_level="view",
                authorization_version="observe-missing-fixture",
            ),
            as_of_date=today_in_timezone(),
        )

    contribution = result["contributions"][0]
    assert contribution["publication_status"] == "missing"
    assert contribution["operating_gate"]["state"] == "observe"
    assert "未允许参与板块合计" in contribution["reason"]
    assert "没有当前已发布快照" in contribution["reason"]


def test_contribution_gate_excludes_portfolio_total_but_not_the_buildings_own_metrics() -> None:
    with SessionLocal() as db:
        actor = _actor(db)
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        _add_published_occupancy(
            db,
            building=building,
            actor=actor,
            numerator=45,
            denominator=50,
            suffix="OBSERVE-SINGLE",
        )
        db.commit()
        common = {
            "user_id": actor.id,
            "building_ids": (building.id,),
            "building_codes": (building.code,),
            "access_level": "view",
            "authorization_version": "observe-single-fixture",
        }
        own_snapshot = build_published_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                **common,
                scope_type="building",
                scope_code=building.code,
                portfolio_id=None,
            ),
            as_of_date=today_in_timezone(),
        )
        portfolio_snapshot = build_published_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                **common,
                scope_type="portfolio",
                scope_code=portfolio.code,
                portfolio_id=portfolio.id,
            ),
            as_of_date=today_in_timezone(),
        )
        own_evidence = build_snapshot_evidence(
            db,
            scope=SelectedPropertyScope(
                **common,
                scope_type="building",
                scope_code=building.code,
                portfolio_id=None,
            ),
            metric_code="occupancy.area_rate",
            as_of_date=today_in_timezone(),
        )

    own_metric = own_snapshot["metrics"]["occupancy.area_rate"]
    assert own_metric["value"] == pytest.approx(90)
    assert own_metric["coverage"]["included_buildings"] == 1
    assert own_metric["coverage"]["contribution_enabled_buildings"] == 0
    own_contribution = own_snapshot["contributions"][0]
    assert own_contribution["contribution_enabled"] is False
    assert own_contribution["metrics"]["occupancy.area_rate"]["aggregation_eligible"] is True
    own_evidence_item = own_evidence["items"][0]
    assert own_evidence_item["status"] == "included"
    assert own_evidence_item["included"] is True
    assert "单楼指标可用" in own_evidence_item["reason"]
    assert "板块合计开关不适用于单楼范围" in own_evidence_item["reason"]

    portfolio_metric = portfolio_snapshot["metrics"]["occupancy.area_rate"]
    assert portfolio_metric["value"] is None
    assert portfolio_metric["coverage"]["included_buildings"] == 0
    assert portfolio_snapshot["contributions"][0]["metrics"]["occupancy.area_rate"]["aggregation_eligible"] is False


def test_hidden_building_keeps_own_metrics_but_server_presentation_projection_is_empty() -> None:
    with SessionLocal() as db:
        actor = _actor(db)
        building = db.query(Building).filter(Building.code == "DEMO_D").one()
        _append_gate(
            db,
            building=building,
            actor=actor,
            presentation_enabled=False,
            contribution_enabled=True,
            reason="只允许板块合计，不开放单楼演示大屏",
        )
        _add_published_occupancy(
            db,
            building=building,
            actor=actor,
            numerator=18,
            denominator=20,
            suffix="HIDDEN-SINGLE",
        )
        db.commit()
        result = build_published_operating_snapshot(
            db,
            scope=SelectedPropertyScope(
                user_id=actor.id,
                building_ids=(building.id,),
                building_codes=(building.code,),
                scope_type="building",
                scope_code=building.code,
                portfolio_id=None,
                access_level="view",
                authorization_version="hidden-single-fixture",
            ),
            as_of_date=today_in_timezone(),
        )

    assert result["metrics"]["occupancy.area_rate"]["value"] == pytest.approx(90)
    assert result["contributions"][0]["presentation_enabled"] is False
    assert result["presentation"]["contributions"] == []


def test_minimal_contract_summary_preserves_mixed_published_and_live_portfolio(monkeypatch) -> None:
    from app.services import building_operating_gate, operating_snapshot

    target = today_in_timezone()
    stamp = datetime.now()
    monkeypatch.setattr(building_operating_gate, "now_in_timezone", lambda: stamp)
    monkeypatch.setattr(operating_snapshot, "now_in_timezone", lambda: stamp)
    with SessionLocal() as db:
        actor = _actor(db)
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        published = db.query(Building).filter(Building.code == "DEMO_B").one()
        _append_gate(
            db, building=published, actor=actor,
            presentation_enabled=True, contribution_enabled=True,
            reason="最小合同概况混合来源验证",
        )
        _add_published_occupancy(
            db, building=published, actor=actor,
            numerator=80.123456, denominator=120.654321, suffix="CONTRACT-SUMMARY",
        )
        scope = SelectedPropertyScope(
            user_id=actor.id, building_ids=(demo_a.id, published.id),
            building_codes=(demo_a.code, published.code), scope_type="portfolio",
            scope_code=portfolio.code, portfolio_id=portfolio.id,
            access_level="view", authorization_version="contract-summary-mixed",
        )
        published_id = published.id
        db.commit()

    with monkeypatch.context() as patch:
        patch.setattr(operating_snapshot, "get_contract_summary", operating_snapshot.get_contract_stats)
        with SessionLocal() as db:
            expected = build_unified_operating_snapshot(db, scope=scope, as_of_date=target)
    with SessionLocal() as db:
        actual = build_unified_operating_snapshot(db, scope=scope, as_of_date=target)
    assert actual == expected
    assert actual["coverage"]["live_adapter_buildings"] == 1
    published_contribution = next(
        item for item in actual["contributions"] if item["building"]["id"] == published_id
    )
    assert published_contribution["publication_status"] == "published"
    assert published_contribution["snapshot_id"] is not None
    metric = published_contribution["metrics"]["occupancy.area_rate"]
    assert metric["numerator"] == 80.123456
    assert metric["denominator"] == 120.654321
    assert metric["coverage"]["batch_ids"]
    assert metric["coverage"]["source_record_ids"]
    declared = {item["id"] for item in actual["sources"]}
    assert "demo-a-detail-live" in declared
    assert set(metric["coverage"]["source_ids"]).issubset(declared)
