from __future__ import annotations

import calendar
from datetime import datetime

from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    SourceRecord,
    User,
)
from app.services.building_operating_gate import (
    record_building_operating_gate,
    resolve_building_operating_gates,
)
from app.services.property_scope import SelectedPropertyScope
from app.services.snapshot_evidence import build_snapshot_evidence


def _register_admin(client: TestClient) -> int:
    assert client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "管理员"},
    ).status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    ).status_code == 200
    return next(
        int(item["id"])
        for item in client.get("/api/auth/users").json()
        if item["username"] == "admin"
    )


def _create_viewer_for_building(admin: TestClient, *, building_code: str) -> None:
    assert admin.post(
        "/api/auth/create-user",
        json={
            "username": "viewer",
            "password": "Viewer123",
            "display_name": "只读用户",
            "role": "viewer",
        },
    ).status_code == 200
    viewer_id = next(
        int(item["id"])
        for item in admin.get("/api/auth/users").json()
        if item["username"] == "viewer"
    )
    current = admin.get(f"/api/auth/authorizations/users/{viewer_id}")
    assert current.status_code == 200
    replaced = admin.put(
        f"/api/auth/authorizations/users/{viewer_id}",
        json={
            "expected_authorization_version": current.json()["authorization_version"],
            "grants": [
                {
                    "scope_type": "building",
                    "scope_code": building_code,
                    "access_level": "view",
                }
            ],
            "capabilities": [],
        },
    )
    assert replaced.status_code == 200, replaced.text


def _insert_metric_fact(
    *,
    building_code: str,
    actor_id: int,
    metric_code: str = "occupancy.area_rate",
    numerator: float = 80,
    denominator: float = 100,
    freshness_status: str = "current",
    published: bool = True,
    suffix: str,
) -> int:
    target = today_in_timezone()
    now = datetime.combine(target, datetime.min.time()).replace(hour=10)
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == building_code).one()
        gate = resolve_building_operating_gates(db, (building.id,)).by_building_id[building.id]
        if not gate.contribution_enabled:
            record_building_operating_gate(
                db,
                building_code,
                db.get(User, actor_id),
                presentation_enabled=True,
                contribution_enabled=True,
                reason="测试模拟快照已核验，允许参与合计",
                expected_revision=gate.revision,
            )
        definition = (
            db.query(MetricDefinition)
            .filter(MetricDefinition.code == metric_code, MetricDefinition.version == 1)
            .one()
        )
        source = DataSource(
            building_id=building.id,
            source_type="excel",
            name=f"经营月报-{suffix}",
            owner="楼宇数据负责人",
            cadence="monthly",
            freshness_rule="月末后五个工作日内确认",
            is_active=True,
        )
        db.add(source)
        db.flush()
        batch = ImportBatch(
            source_id=source.id,
            source_file_name=f"经营月报-{suffix}.xlsx",
            external_batch_no=f"BATCH-{suffix}",
            period=target.strftime("%Y-%m"),
            as_of_date=target,
            source_observed_at=now,
            source_updated_at=now,
            received_at=now,
            started_at=now,
            finished_at=now,
            ingested_at=now,
            published_at=now if published else None,
            file_checksum=f"0123456789abcdef-{suffix}",
            parser_version="fixture-normalized-v1",
            actor_id=actor_id,
            row_count=1,
            accepted_count=1,
            rejected_count=0,
            status="published" if published else "received",
            error_summary="",
            reviewed_by=actor_id if published else None,
            reviewed_at=now if published else None,
        )
        db.add(batch)
        db.flush()
        record = SourceRecord(
            batch_id=batch.id,
            row_no=1,
            record_checksum=f"normalized-record-{suffix}",
            status="accepted",
            source_ref="sheet:经营数据,row:2",
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
            publication_status="published" if published else "draft",
            is_current=published,
            submitted_by=actor_id,
            submitted_at=now,
            reviewed_by=actor_id if published else None,
            reviewed_at=now if published else None,
            published_by=actor_id if published else None,
            published_at=now if published else None,
            created_at=now,
            updated_at=now,
        )
        db.add(snapshot)
        db.flush()
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
            freshness_status=freshness_status,
            validation_status="valid",
            publication_status="published" if published else "draft",
            calculation_version="fixture-normalized-v1",
            created_at=now,
        )
        db.add(fact)
        db.flush()
        record.business_object_type = "building_metric_fact"
        record.business_object_id = fact.id
        snapshot_id = int(snapshot.id)
        db.commit()
        return snapshot_id


def test_viewer_reads_published_lineage_without_workflow_permission_or_cross_building_leak() -> None:
    with TestClient(app) as admin, TestClient(app) as viewer, TestClient(app) as anonymous:
        admin_id = _register_admin(admin)
        _create_viewer_for_building(admin, building_code="DEMO_B")
        snapshot_id = _insert_metric_fact(
            building_code="DEMO_B",
            actor_id=admin_id,
            suffix="VIEWER",
        )
        assert viewer.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "Viewer123"},
        ).status_code == 200

        response = viewer.get(
            "/api/operations/snapshot-evidence",
            params={
                "building": "DEMO_B",
                "metric_code": "occupancy.area_rate",
                "as_of_date": today_in_timezone().isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["contract_version"] == "snapshot-evidence-v1"
        assert result["scope"]["building_codes"] == ["DEMO_B"]
        assert result["metric"]["aggregate"]["numerator"] == 80
        assert result["metric"]["aggregate"]["denominator"] == 100
        assert result["metric"]["aggregate"]["value"] == 80
        assert result["limitations"]
        item = result["items"][0]
        assert item["status"] == "included"
        assert item["included"] is True
        assert "单楼指标可用" in item["reason"]
        assert item["snapshot"]["id"] == snapshot_id
        assert item["fact"]["numerator"] == 80
        assert item["fact"]["denominator"] == 100
        assert item["source"]["name"] == "经营月报-VIEWER"
        assert item["batch"]["source_file_name"] == "经营月报-VIEWER.xlsx"
        assert item["batch"]["parser_version"] == "fixture-normalized-v1"
        assert item["batch"]["actor"]["name"] == "管理员"
        assert item["batch"]["reviewed_by"]["name"] == "管理员"
        assert item["record"]["row_no"] == 1
        assert item["record"]["source_ref"] == "sheet:经营数据,row:2"
        assert item["snapshot"]["submitted_by"]["name"] == "管理员"
        assert item["snapshot"]["reviewed_by"]["name"] == "管理员"
        assert item["snapshot"]["published_by"]["name"] == "管理员"

        workflow_detail = viewer.get(
            f"/api/operations/snapshot-submissions/{snapshot_id}",
            params={"building": "DEMO_B"},
        )
        assert workflow_detail.status_code == 403
        assert viewer.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_D", "metric_code": "occupancy.area_rate"},
        ).status_code == 404
        assert viewer.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_B", "metric_code": "unknown.metric"},
        ).status_code == 422
        assert anonymous.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_B", "metric_code": "occupancy.area_rate"},
        ).status_code == 401


def test_evidence_keeps_stale_fact_visible_but_excluded_and_does_not_expose_draft() -> None:
    with TestClient(app) as client:
        actor_id = _register_admin(client)
        _insert_metric_fact(
            building_code="DEMO_B",
            actor_id=actor_id,
            freshness_status="stale",
            suffix="STALE",
        )
        _insert_metric_fact(
            building_code="DEMO_D",
            actor_id=actor_id,
            published=False,
            suffix="DRAFT",
        )
        with SessionLocal() as db:
            buildings = {
                item.code: item
                for item in db.query(Building)
                .filter(Building.code.in_(("DEMO_B", "DEMO_D")))
                .all()
            }
            scope = SelectedPropertyScope(
                user_id=actor_id,
                building_ids=(buildings["DEMO_B"].id, buildings["DEMO_D"].id),
                building_codes=("DEMO_B", "DEMO_D"),
                scope_type="portfolio",
                scope_code="EVIDENCE_FIXTURE",
                portfolio_id=None,
                access_level="view",
                authorization_version="fixture",
            )
            result = build_snapshot_evidence(
                db,
                scope=scope,
                metric_code="occupancy.area_rate",
                as_of_date=today_in_timezone(),
            )

        assert result["metric"]["aggregate"]["value"] is None
        items = {item["building"]["code"]: item for item in result["items"]}
        assert items["DEMO_B"]["status"] == "excluded"
        assert items["DEMO_B"]["fact"]["value"] == 80
        assert items["DEMO_B"]["reason"] == "指标已过期"
        assert items["DEMO_D"]["status"] == "missing"
        assert items["DEMO_D"]["snapshot"] is None
        assert items["DEMO_D"]["fact"] is None
        assert items["DEMO_D"]["batch"] is None


def test_demo_a_live_evidence_preserves_real_zero_and_declares_missing_batch_and_business_time() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        response = client.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_A", "metric_code": "risk.open_items"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        item = result["items"][0]
        assert item["status"] == "live_detail_limited"
        assert item["included"] is True
        assert "单楼指标可用" in item["reason"]
        assert item["fact"]["value"] == 0
        assert item["fact"]["freshness_status"] == "unknown"
        assert item["snapshot"] is None
        assert item["batch"] is None
        assert item["record"] is None
        assert item["limitations"]
        assert "业务观测时间" in item["limitations"][0]


def test_demo_a_single_building_evidence_remains_available_when_portfolio_contribution_is_off() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        gates_response = client.get("/api/buildings/operating-gates")
        assert gates_response.status_code == 200, gates_response.text
        demo_a_gate = next(
            item
            for item in gates_response.json()["items"]
            if item["building_code"] == "DEMO_A"
        )
        update_response = client.put(
            "/api/buildings/DEMO_A/operating-gate",
            json={
                "presentation_enabled": True,
                "contribution_enabled": False,
                "reason": "验证单楼指标与板块合计开关相互独立",
                "expected_revision": demo_a_gate["revision"],
            },
        )
        assert update_response.status_code == 200, update_response.text

        response = client.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_A", "metric_code": "risk.open_items"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        item = result["items"][0]
        assert item["status"] == "live_detail_limited"
        assert item["included"] is True
        assert item["operating_gate"]["contribution_enabled"] is False
        assert "单楼指标可用" in item["reason"]
        assert result["metric"]["aggregate"]["coverage"]["included_buildings"] == 1
        assert result["metric"]["aggregate"]["coverage"]["contribution_enabled_buildings"] == 0


def test_demo_a_evidence_aggregate_exactly_matches_the_unified_snapshot_branch() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        displayed = client.get(
            "/api/operations/snapshot",
            params={"building": "DEMO_A", "include_trend": False, "queue_limit": 0, "risk_limit": 0},
        )
        assert displayed.status_code == 200, displayed.text
        displayed_metrics = displayed.json()["metrics"]

        for metric_code in ("risk.open_items", "occupancy.area_rate", "expiry.area_pressure_90d"):
            response = client.get(
                "/api/operations/snapshot-evidence",
                params={"building": "DEMO_A", "metric_code": metric_code},
            )
            assert response.status_code == 200, response.text
            aggregate = response.json()["metric"]["aggregate"]
            expected = displayed_metrics[metric_code]
            for field in (
                "value",
                "numerator",
                "denominator",
                "period_start",
                "period_end",
                "as_of_date",
                "calculation_version",
                "coverage",
                "unavailable_reason",
            ):
                assert aggregate[field] == expected[field]


def test_demo_a_unimplemented_metric_is_an_honest_missing_result_not_an_api_error() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        response = client.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_A", "metric_code": "expiry.area_pressure_180d"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["metric"]["aggregate"]["value"] is None
        assert result["metric"]["aggregate"]["calculation_version"] is None
        assert result["metric"]["aggregate"]["unavailable_reason"] == "当前明细适配器尚未提供该指标"
        item = result["items"][0]
        assert item["status"] == "live_detail_limited"
        assert item["included"] is False
        assert item["fact"] is None
        assert item["reason"] == "当前明细适配器尚未提供该指标"
        option = next(
            row for row in result["available_metrics"] if row["code"] == "expiry.area_pressure_180d"
        )
        assert option["scope_status"] == "not_provided"
        assert option["scope_note"] == "当前明细适配器尚未提供该指标"


def test_evidence_response_contract_has_stable_metric_and_item_fields() -> None:
    with TestClient(app) as client:
        _register_admin(client)
        result = client.get(
            "/api/operations/snapshot-evidence",
            params={"building": "DEMO_A", "metric_code": "risk.open_items"},
        ).json()
        assert set(result) == {
            "contract_version",
            "definition_version",
            "data_classification",
            "gate_policy_version",
            "rollout",
            "as_of_date",
            "scope",
            "metric",
            "available_metrics",
            "limitations",
            "items",
        }
        assert {
            "code",
            "name",
            "unit",
            "formula",
            "definition_hash",
            "definition_version",
            "numerator_rule",
            "denominator_rule",
            "inclusion_rule",
            "exclusion_rule",
            "aggregation_rule",
            "coverage_rule",
            "estimation_policy",
            "freshness_policy",
            "owner",
            "aggregate",
        } == set(result["metric"])
        assert {
            "building",
            "status",
            "included",
            "reason",
            "snapshot",
            "fact",
            "source",
            "batch",
            "record",
            "limitations",
            "operating_gate",
            "data_classification",
        } == set(result["items"][0])
