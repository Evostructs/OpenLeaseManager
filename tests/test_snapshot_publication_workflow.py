from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    Building,
    DataSource,
    ImportBatch,
    ImportError,
    MetricDefinition,
    OperationLog,
    Portfolio,
    PortfolioBuilding,
    SourceRecord,
    User,
)
from app.ops_v1 import OPS_V1_DEFINITIONS, definition_hash
from app.services.property_scope import SelectedPropertyScope
from app.services.building_operating_gate import (
    record_building_operating_gate,
    resolve_building_operating_gates,
)
from app.services.published_operating_snapshot import build_published_operating_snapshot
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


def _register_admin(client: TestClient) -> None:
    assert client.post(
        "/api/auth/register",
        json={"username": "submitter", "password": "Submit123", "display_name": "提交人"},
    ).status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "submitter", "password": "Submit123"}
    ).status_code == 200


def _user_id(admin: TestClient, username: str) -> int:
    rows = admin.get("/api/auth/users").json()
    return next(int(item["id"]) for item in rows if item["username"] == username)


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


def _workflow_clients() -> tuple[TestClient, TestClient, TestClient]:
    submitter = TestClient(app)
    reviewer = TestClient(app)
    publisher = TestClient(app)
    _register_admin(submitter)
    for username, password, display_name in (
        ("reviewer", "Review123", "复核人"),
        ("publisher", "Publish123", "发布人"),
    ):
        assert submitter.post(
            "/api/auth/create-user",
            json={
                "username": username,
                "password": password,
                "display_name": display_name,
                "role": "editor",
            },
        ).status_code == 200
    reviewer_id = _user_id(submitter, "reviewer")
    publisher_id = _user_id(submitter, "publisher")
    submitter_id = _user_id(submitter, "submitter")
    _grant(submitter, reviewer_id, ["snapshot_review", "snapshot_publish"])
    _grant(submitter, publisher_id, ["snapshot_publish"])
    _grant(
        submitter,
        submitter_id,
        [
            "platform_admin",
            "customer_master_manage",
            "data_import_submit",
            "snapshot_review",
            "snapshot_publish",
        ],
    )
    assert submitter.post(
        "/api/auth/login", json={"username": "submitter", "password": "Submit123"}
    ).status_code == 200
    assert reviewer.post(
        "/api/auth/login", json={"username": "reviewer", "password": "Review123"}
    ).status_code == 200
    assert publisher.post(
        "/api/auth/login", json={"username": "publisher", "password": "Publish123"}
    ).status_code == 200
    return submitter, reviewer, publisher


def _submission(source_id: int, *, leased: float, correction_reason: str | None = None) -> dict:
    today = today_in_timezone()
    month_end = today.replace(day=28)
    while month_end.month == today.month:
        next_day = month_end.fromordinal(month_end.toordinal() + 1)
        if next_day.month != today.month:
            break
        month_end = next_day
    return {
        "source_id": source_id,
        "period_start": today.replace(day=1).isoformat(),
        "period_end": month_end.isoformat(),
        "as_of_date": today.isoformat(),
        "source_observed_at": datetime.combine(today, datetime.min.time()).isoformat(),
        "parser_version": "ops-v1-test-v1",
        "correction_reason": correction_reason,
        "facts": [
            {
                "code": "area.rentable_sqm",
                "numerator": 100,
                "covered_area_sqm": 100,
                "eligible_area_sqm": 100,
                "covered_days": 1,
                "eligible_days": 1,
                "completeness_status": "complete",
                "freshness_status": "current",
                "calculation_version": "fixture-v1",
            },
            {
                "code": "occupancy.area_rate",
                "numerator": leased,
                "denominator": 100,
                "covered_area_sqm": 100,
                "eligible_area_sqm": 100,
                "covered_days": 1,
                "eligible_days": 1,
                "completeness_status": "complete",
                "freshness_status": "current",
                "calculation_version": "fixture-v1",
            },
        ],
    }


def test_ops_v1_workflow_separates_actors_publishes_exact_date_and_supersedes_atomically() -> None:
    submitter, reviewer, publisher = _workflow_clients()
    try:
        source = submitter.post(
            "/api/operations/snapshot-sources",
            params={"building": "DEMO_A"},
            json={
                "source_type": "manual_snapshot",
                "name": "月度人工经营快照",
                "owner": "数据负责人",
                "cadence": "monthly",
                "freshness_rule": "统计日后五个工作日内确认",
            },
        )
        assert source.status_code == 200, source.text
        source_id = int(source.json()["id"])

        submitted = submitter.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_submission(source_id, leased=80),
        )
        assert submitted.status_code == 200, submitted.text
        first_id = int(submitted.json()["id"])
        assert submitted.json()["publication_status"] == "draft"
        assert submitter.post(
            f"/api/operations/snapshot-submissions/{first_id}/review",
            params={"building": "DEMO_A"},
            json={"approved": True, "notes": "提交人自审反例"},
        ).status_code == 409
        reviewed = reviewer.post(
            f"/api/operations/snapshot-submissions/{first_id}/review",
            params={"building": "DEMO_A"},
            json={"approved": True, "notes": "来源与分子分母核对通过"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewer.post(
            f"/api/operations/snapshot-submissions/{first_id}/publish",
            params={"building": "DEMO_A"},
            json={"notes": "复核人兼发布反例"},
        ).status_code == 409
        published = publisher.post(
            f"/api/operations/snapshot-submissions/{first_id}/publish",
            params={"building": "DEMO_A"},
            json={"notes": "正式发布"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["publication_status"] == "published"

        read = publisher.get(
            "/api/operations/published-snapshot",
            params={"building": "DEMO_A", "as_of_date": today_in_timezone().isoformat()},
        )
        assert read.status_code == 200, read.text
        assert read.json()["definition_version"] == "OPS_V1"
        occupancy = read.json()["metrics"]["occupancy.area_rate"]
        assert occupancy["numerator"] == 80
        assert occupancy["denominator"] == 100
        assert occupancy["value"] == 80
        assert occupancy["coverage"]["included_buildings"] == 1

        second = submitter.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=_submission(source_id, leased=90, correction_reason="更正已复核的在租面积"),
        )
        assert second.status_code == 200, second.text
        second_id = int(second.json()["id"])
        assert second.json()["supersedes_id"] == first_id
        assert reviewer.post(
            f"/api/operations/snapshot-submissions/{second_id}/review",
            params={"building": "DEMO_A"},
            json={"approved": True, "notes": "更正复核通过"},
        ).status_code == 200
        assert publisher.post(
            f"/api/operations/snapshot-submissions/{second_id}/publish",
            params={"building": "DEMO_A"},
            json={"notes": "发布更正"},
        ).status_code == 200

        with SessionLocal() as db:
            first = db.get(BuildingOperatingSnapshot, first_id)
            current = db.get(BuildingOperatingSnapshot, second_id)
            assert first.publication_status == "superseded"
            assert first.is_current is False
            assert current.publication_status == "published"
            assert current.is_current is True
            assert db.query(ImportBatch).filter(ImportBatch.status == "published").count() == 1
            assert db.query(ImportBatch).filter(ImportBatch.status == "superseded").count() == 1
            actions = {
                row.action
                for row in db.query(OperationLog)
                .filter(OperationLog.target_type == "operating_snapshot")
                .all()
            }
            assert {"snapshot_submit", "snapshot_review", "snapshot_publish"} <= actions
            current_fact = (
                db.query(BuildingMetricFact)
                .filter(
                    BuildingMetricFact.snapshot_id == second_id,
                    BuildingMetricFact.publication_status == "published",
                )
                .first()
            )
            current_fact.value = 999
            with pytest.raises(IntegrityError, match="published metric facts are immutable"):
                db.commit()
            db.rollback()
            current = db.get(BuildingOperatingSnapshot, second_id)
            current.revision = 99
            with pytest.raises(IntegrityError, match="published operating snapshots are immutable"):
                db.commit()
            db.rollback()
            current = db.get(BuildingOperatingSnapshot, second_id)
            db.delete(current)
            with pytest.raises(IntegrityError, match="published operating snapshots are immutable"):
                db.commit()
            db.rollback()
    finally:
        submitter.close()
        reviewer.close()
        publisher.close()


def test_ops_v1_definitions_are_complete_published_and_immutable() -> None:
    with TestClient(app) as client:
        _register_admin(client)
    with SessionLocal() as db:
        rows = db.query(MetricDefinition).filter(MetricDefinition.version == 1).all()
        assert {row.code for row in rows} == {item["code"] for item in OPS_V1_DEFINITIONS}
        for row in rows:
            spec = next(item for item in OPS_V1_DEFINITIONS if item["code"] == row.code)
            assert row.publication_status == "published"
            assert row.definition_hash == definition_hash(spec)
        rows[0].name = "禁止原地改写"
        with pytest.raises(IntegrityError, match="published metric definitions are immutable"):
            db.commit()
        db.rollback()
        row = db.query(MetricDefinition).filter(MetricDefinition.code == rows[0].code).one()
        db.delete(row)
        with pytest.raises(IntegrityError, match="published metric definitions are immutable"):
            db.commit()
        db.rollback()


def test_failed_validation_keeps_rejected_evidence_without_formal_snapshot() -> None:
    submitter, reviewer, publisher = _workflow_clients()
    try:
        source = submitter.post(
            "/api/operations/snapshot-sources",
            params={"building": "DEMO_A"},
            json={
                "source_type": "manual_snapshot",
                "name": "失败批次来源",
                "owner": "测试",
                "cadence": "monthly",
                "freshness_rule": "月度确认",
            },
        )
        assert source.status_code == 200
        payload = _submission(int(source.json()["id"]), leased=80)
        payload["facts"][1]["code"] = "not.ops.v1"
        failed = submitter.post(
            "/api/operations/snapshot-submissions",
            params={"building": "DEMO_A"},
            json=payload,
        )
        assert failed.status_code == 422, failed.text
        batch_id = int(failed.json()["detail"]["batch_id"])
        with SessionLocal() as db:
            batch = db.get(ImportBatch, batch_id)
            assert batch.status == "rejected"
            assert batch.rejected_count == 1
            assert db.query(ImportError).filter(ImportError.batch_id == batch_id).count() >= 1
            assert (
                db.query(SourceRecord)
                .filter(SourceRecord.batch_id == batch_id, SourceRecord.status == "rejected")
                .count()
                == 1
            )
            assert db.query(BuildingOperatingSnapshot).count() == 0
            assert db.query(BuildingMetricFact).count() == 0
    finally:
        submitter.close()
        reviewer.close()
        publisher.close()


def _insert_published_occupancy(
    db,
    *,
    building: Building,
    actor_id: int,
    as_of_date,
    numerator: float,
    denominator: float,
    suffix: str,
) -> None:
    gate = resolve_building_operating_gates(db, (building.id,)).by_building_id[building.id]
    if not gate.contribution_enabled:
        record_building_operating_gate(
            db,
            building.code,
            db.get(User, actor_id),
            presentation_enabled=True,
            contribution_enabled=True,
            reason="测试模拟快照已核验，允许参与合计",
            expected_revision=gate.revision,
        )
    source = DataSource(
        building_id=building.id,
        source_type="manual_snapshot",
        name=f"聚合来源-{suffix}",
        owner="fixture",
        cadence="monthly",
        freshness_rule="月度确认",
        is_active=True,
    )
    db.add(source)
    db.flush()
    now = datetime.now()
    batch = ImportBatch(
        source_id=source.id,
        source_file_name=None,
        external_batch_no=f"AGG-{suffix}",
        period=as_of_date.strftime("%Y-%m"),
        as_of_date=as_of_date,
        source_observed_at=now,
        received_at=now,
        started_at=now,
        finished_at=now,
        ingested_at=now,
        published_at=now,
        file_checksum=f"checksum-{suffix}-1234567890",
        parser_version="fixture-v1",
        actor_id=actor_id,
        row_count=1,
        accepted_count=1,
        rejected_count=0,
        status="published",
        error_summary="",
        reviewed_by=actor_id,
        reviewed_at=now,
    )
    db.add(batch)
    db.flush()
    record = SourceRecord(
        batch_id=batch.id,
        row_no=1,
        record_checksum=f"record-{suffix}",
        status="accepted",
        source_ref="row:1",
    )
    db.add(record)
    db.flush()
    month_end = as_of_date.replace(day=28)
    while True:
        following = month_end.fromordinal(month_end.toordinal() + 1)
        if following.month != as_of_date.month:
            break
        month_end = following
    snapshot = BuildingOperatingSnapshot(
        building_id=building.id,
        period_start=as_of_date.replace(day=1),
        period_end=month_end,
        as_of_date=as_of_date,
        revision=1,
        validation_status="valid",
        publication_status="published",
        is_current=True,
        submitted_by=actor_id,
        submitted_at=now,
        reviewed_by=actor_id,
        reviewed_at=now,
        published_by=actor_id,
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
        calculation_version="fixture-v1",
        created_at=now,
    )
    db.add(fact)
    db.flush()
    record.business_object_type = "building_metric_fact"
    record.business_object_id = fact.id


def test_authorized_portfolio_aggregates_exact_date_by_raw_fractions_and_discloses_missing() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        admin_id = _user_id(client, "submitter")
        with SessionLocal() as db:
            portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
            buildings = []
            for code in ("AGG_A", "AGG_B", "AGG_OLD"):
                building = Building(code=code, name=code, ingestion_mode="snapshot", is_active=True)
                db.add(building)
                db.flush()
                db.add(
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=building.id,
                        valid_from=today_in_timezone(),
                    )
                )
                buildings.append(building)
            target = today_in_timezone()
            _insert_published_occupancy(
                db,
                building=buildings[0],
                actor_id=admin_id,
                as_of_date=target,
                numerator=80,
                denominator=100,
                suffix="A",
            )
            _insert_published_occupancy(
                db,
                building=buildings[1],
                actor_id=admin_id,
                as_of_date=target,
                numerator=20,
                denominator=50,
                suffix="B",
            )
            _insert_published_occupancy(
                db,
                building=buildings[2],
                actor_id=admin_id,
                as_of_date=target - timedelta(days=1),
                numerator=999,
                denominator=1000,
                suffix="OLD",
            )
            db.commit()
            scope = SelectedPropertyScope(
                user_id=admin_id,
                building_ids=tuple(item.id for item in buildings),
                building_codes=tuple(item.code for item in buildings),
                scope_type="portfolio",
                scope_code=portfolio.code,
                portfolio_id=portfolio.id,
                access_level="view",
                authorization_version="fixture",
            )
            result = build_published_operating_snapshot(db, scope=scope, as_of_date=target)
        metric = result["metrics"]["occupancy.area_rate"]
        assert metric["numerator"] == 100
        assert metric["denominator"] == 150
        assert metric["value"] == pytest.approx(66.666667)
        assert metric["coverage"]["authorized_buildings"] == 3
        assert metric["coverage"]["eligible_buildings"] == 2
        assert metric["coverage"]["included_buildings"] == 2
        assert metric["coverage"]["excluded_buildings"] == [
            {"building_code": "AGG_OLD", "reason": "该统计日没有已发布指标事实"}
        ]
        # The registered user still has only its original DEMO_A grant. Guessing
        # a new building code through the public endpoint must not reveal it.
        assert client.get(
            "/api/operations/published-snapshot",
            params={"building": "AGG_A", "as_of_date": target.isoformat()},
        ).status_code == 404


def test_historical_portfolio_excludes_building_that_joined_after_target_date() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        admin_id = _user_id(client, "submitter")
        with SessionLocal() as db:
            target = today_in_timezone() - timedelta(days=10)
            portfolio = Portfolio(
                code="HISTORICAL_MEMBERSHIP",
                name="历史成员口径测试板块",
                is_active=True,
            )
            historical_member = Building(
                code="HISTORICAL_MEMBER",
                name="统计日已在板块",
                ingestion_mode="snapshot",
                is_active=True,
            )
            later_member = Building(
                code="LATER_MEMBER",
                name="统计日后加入板块",
                ingestion_mode="detail",
                is_active=True,
            )
            db.add_all((portfolio, historical_member, later_member))
            db.flush()
            db.add_all(
                (
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=historical_member.id,
                        valid_from=target - timedelta(days=1),
                    ),
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=later_member.id,
                        valid_from=target + timedelta(days=1),
                    ),
                )
            )
            _insert_published_occupancy(
                db,
                building=historical_member,
                actor_id=admin_id,
                as_of_date=target,
                numerator=80,
                denominator=100,
                suffix="HISTORICAL-MEMBER",
            )
            _insert_published_occupancy(
                db,
                building=later_member,
                actor_id=admin_id,
                as_of_date=target,
                numerator=999,
                denominator=1000,
                suffix="LATER-MEMBER",
            )
            db.commit()
            scope = SelectedPropertyScope(
                user_id=admin_id,
                building_ids=(historical_member.id, later_member.id),
                building_codes=(historical_member.code, later_member.code),
                scope_type="portfolio",
                scope_code=portfolio.code,
                portfolio_id=portfolio.id,
                access_level="view",
                authorization_version="fixture",
            )
            result = build_unified_operating_snapshot(db, scope=scope, as_of_date=target)

        occupancy = result["metrics"]["occupancy.area_rate"]
        assert occupancy["numerator"] == 80
        assert occupancy["denominator"] == 100
        assert occupancy["value"] == 80
        assert result["scope"]["building_ids"] == [historical_member.id]
        assert result["scope"]["building_codes"] == ["HISTORICAL_MEMBER"]
        assert result["coverage"]["portfolio_buildings"] == 1
        assert result["coverage"]["authorized_buildings"] == 1
        assert [item["building"]["code"] for item in result["contributions"]] == [
            "HISTORICAL_MEMBER"
        ]


def test_historical_portfolio_membership_never_expands_current_authorization() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        admin_id = _user_id(client, "submitter")
        with SessionLocal() as db:
            target = today_in_timezone() - timedelta(days=10)
            portfolio = Portfolio(
                code="HISTORICAL_AUTH_BOUNDARY",
                name="历史授权边界测试板块",
                is_active=True,
            )
            authorized = Building(
                code="HIST_AUTHORIZED",
                name="当前仍有权限",
                ingestion_mode="snapshot",
                is_active=True,
            )
            unauthorized = Building(
                code="HIST_UNAUTHORIZED",
                name="当前无权限",
                ingestion_mode="snapshot",
                is_active=True,
            )
            db.add_all((portfolio, authorized, unauthorized))
            db.flush()
            db.add_all(
                (
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=authorized.id,
                        valid_from=target - timedelta(days=1),
                    ),
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=unauthorized.id,
                        valid_from=target - timedelta(days=1),
                    ),
                )
            )
            _insert_published_occupancy(
                db,
                building=authorized,
                actor_id=admin_id,
                as_of_date=target,
                numerator=60,
                denominator=100,
                suffix="HIST-AUTHORIZED",
            )
            _insert_published_occupancy(
                db,
                building=unauthorized,
                actor_id=admin_id,
                as_of_date=target,
                numerator=900,
                denominator=1000,
                suffix="HIST-UNAUTHORIZED",
            )
            db.commit()
            scope = SelectedPropertyScope(
                user_id=admin_id,
                building_ids=(authorized.id,),
                building_codes=(authorized.code,),
                scope_type="portfolio",
                scope_code=portfolio.code,
                portfolio_id=portfolio.id,
                access_level="view",
                authorization_version="fixture",
            )
            result = build_published_operating_snapshot(db, scope=scope, as_of_date=target)

        occupancy = result["metrics"]["occupancy.area_rate"]
        assert occupancy["numerator"] == 60
        assert occupancy["denominator"] == 100
        assert occupancy["value"] == 60
        assert result["scope"]["building_ids"] == [authorized.id]
        assert result["scope"]["building_codes"] == ["HIST_AUTHORIZED"]
        assert result["coverage"]["portfolio_buildings"] == 2
        assert result["coverage"]["authorized_buildings"] == 1
        assert [item["building"]["code"] for item in result["contributions"]] == [
            "HIST_AUTHORIZED"
        ]
        assert all(
            item["building_id"] == authorized.id
            for item in result["sources"]
            if isinstance(item["building_id"], int)
        )


def test_unified_portfolio_combines_same_day_demo_a_adapter_and_publication_with_projections() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        admin_id = _user_id(client, "submitter")
        with SessionLocal() as db:
            demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
            published_building = Building(
                code="UNIFIED_PUBLISHED",
                name="统一入口已发布楼",
                ingestion_mode="snapshot",
                is_active=True,
            )
            db.add(published_building)
            db.flush()
            target = today_in_timezone()
            _insert_published_occupancy(
                db,
                building=published_building,
                actor_id=admin_id,
                as_of_date=target,
                numerator=20,
                denominator=50,
                suffix="UNIFIED",
            )
            db.commit()
            scope = SelectedPropertyScope(
                user_id=admin_id,
                building_ids=(demo_a.id, published_building.id),
                building_codes=(demo_a.code, published_building.code),
                scope_type="portfolio",
                scope_code="UNIFIED_SCOPE",
                portfolio_id=None,
                access_level="view",
                authorization_version="fixture",
            )
            detail_snapshot = {
                "definition_version": "OPS_V1",
                "as_of_date": target.isoformat(),
                "scope": {"building_ids": [demo_a.id]},
                "sources": [
                    {
                        "id": "demo_a-detail-live",
                        "name": "DEMO_A 现有租赁明细库",
                        "source_type": "detail",
                    }
                ],
                "metrics": {
                    "occupancy.area_rate": {
                        "value": 80,
                        "numerator": 80,
                        "denominator": 100,
                        "coverage": {
                            "eligible_buildings": 1,
                            "included_buildings": 1,
                            "covered_area_sqm": 100,
                            "known_total_area_sqm": 100,
                            "covered_days": 1,
                            "eligible_days": 1,
                            "source_ids": ["demo_a-detail-live"],
                        },
                        "unavailable_reason": None,
                    },
                    "risk.open_items": {
                        "value": 3,
                        "numerator": 3,
                        "denominator": None,
                        "coverage": {
                            "eligible_buildings": 1,
                            "included_buildings": 1,
                            "data_depth": "detail",
                            "completeness_status": "complete",
                            "freshness_status": "unknown",
                            "validation_status": "valid",
                            "publication_status": "live_adapter",
                            "source_ids": ["demo_a-detail-live"],
                        },
                        "unavailable_reason": None,
                    },
                },
            }
            with pytest.raises(HTTPException) as missing_source:
                build_published_operating_snapshot(
                    db,
                    scope=scope,
                    as_of_date=target,
                    detail_snapshots={demo_a.id: {**detail_snapshot, "sources": []}},
                )
            assert missing_source.value.status_code == 409
            assert "未声明数据来源" in str(missing_source.value.detail)
            result = build_published_operating_snapshot(
                db,
                scope=scope,
                as_of_date=target,
                detail_snapshots={demo_a.id: detail_snapshot},
            )

        occupancy = result["metrics"]["occupancy.area_rate"]
        assert occupancy["numerator"] == 100
        assert occupancy["denominator"] == 150
        assert occupancy["value"] == pytest.approx(66.666667)
        assert occupancy["coverage"]["included_buildings"] == 2
        assert occupancy["coverage"]["freshness_status"] == "unknown"
        assert occupancy["coverage"]["excluded_buildings"] == []
        assert occupancy["coverage"]["source_ids"][-1] == "demo_a-detail-live"
        assert {item["id"] for item in result["sources"]} >= {
            "demo_a-detail-live"
        }
        assert all(
            source_id in {item["id"] for item in result["sources"]}
            for source_id in occupancy["coverage"]["source_ids"]
        )
        assert result["coverage"]["included_same_day_buildings"] == 2
        assert result["coverage"]["same_day_snapshot_buildings"] == 2
        assert [item["publication_status"] for item in result["contributions"]] == [
            "live_adapter",
            "published",
        ]
        live_contribution, published_contribution = result["contributions"]
        assert live_contribution["metrics"]["risk.open_items"]["value"] == 3
        assert (
            live_contribution["metrics"]["risk.open_items"]["coverage"]["freshness_status"]
            == "unknown"
        )
        published_occupancy = published_contribution["metrics"]["occupancy.area_rate"]
        assert published_occupancy["coverage"]["included_buildings"] == 1
        assert published_occupancy["coverage"]["freshness_status"] == "current"
        assert published_occupancy["coverage"]["source_ids"]
        assert result["legacy_operations"]["vacancy"]["occupancy_rate"] == pytest.approx(66.666667)
        assert result["presentation"]["contributions"] == result["contributions"]


def test_unified_non_demo_a_single_building_uses_published_projection_without_422() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        admin_id = _user_id(client, "submitter")
        with SessionLocal() as db:
            building = Building(
                code="UNIFIED_SINGLE",
                name="统一入口单楼",
                ingestion_mode="snapshot",
                is_active=True,
            )
            db.add(building)
            db.flush()
            target = today_in_timezone()
            _insert_published_occupancy(
                db,
                building=building,
                actor_id=admin_id,
                as_of_date=target,
                numerator=0.8,
                denominator=100,
                suffix="SINGLE",
            )
            db.commit()
            scope = SelectedPropertyScope(
                user_id=admin_id,
                building_ids=(building.id,),
                building_codes=(building.code,),
                scope_type="building",
                scope_code=building.code,
                portfolio_id=None,
                access_level="view",
                authorization_version="fixture",
            )
            result = build_unified_operating_snapshot(db, scope=scope, as_of_date=target)

        assert result["scope"]["scope_code"] == "UNIFIED_SINGLE"
        assert result["metrics"]["occupancy.area_rate"]["value"] == 0.8
        assert result["legacy_operations"]["vacancy"]["occupancy_rate"] == 0.8
        assert result["presentation"]["tower"] == [
            {
                "name": "统一入口单楼",
                "total": None,
                "leased": None,
                "rate": 0.8,
                "building_code": "UNIFIED_SINGLE",
            }
        ]
