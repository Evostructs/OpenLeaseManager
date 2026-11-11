from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from threading import Barrier, Event

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal, engine
from app.main import app
from app.core.time import now_in_timezone
from app.services import snapshot_exchange as exchange_service
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    OperationLog,
    Portfolio,
    PortfolioBuilding,
    SnapshotExchangeReceipt,
    SnapshotExchangeSourceBinding,
    SourceRecord,
    User,
)
from app.ops_v1 import OPS_V1_BY_CODE, SUBMITTABLE_CODES
from app.services.snapshot_exchange import (
    get_snapshot_exchange_receipt,
    receive_snapshot_package,
)
from app.services.property_scope import SelectedPropertyScope
from app.services.portfolio_overview import build_portfolio_overview
from app.services.snapshot_workflow import (
    publish_operating_snapshot,
    review_operating_snapshot,
)
from app.snapshot_exchange_schemas import (
    SnapshotExchangePackageV1,
    compute_content_digest,
)


def _register_receiver() -> int:
    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "exchange_receiver",
                "password": "Receive123",
                "display_name": "交换接收人",
            },
        )
        assert registered.status_code == 200, registered.text
    with SessionLocal() as db:
        return int(db.query(User).filter(User.username == "exchange_receiver").one().id)


def _register_actor(username: str) -> int:
    with SessionLocal() as db:
        actor = User(
            username=username,
            display_name=username,
            password_hash="unused-direct-service-test",
            role="editor",
            is_active=True,
        )
        db.add(actor)
        db.commit()
        return int(actor.id)


def _scope(*, user_id: int, building: Building) -> SelectedPropertyScope:
    return SelectedPropertyScope(
        user_id=user_id,
        building_ids=(building.id,),
        building_codes=(building.code,),
        scope_type="building",
        scope_code=building.code,
        portfolio_id=None,
        access_level="edit",
        authorization_version="snapshot-exchange-test",
    )


def _bind_source() -> int:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        source = DataSource(
            building_id=building.id,
            source_type="external_api",
            name="示例楼宇来源系统（本地模拟）",
            owner="模拟来源负责人",
            cadence="monthly",
            freshness_rule="统计日后一个工作日内发布",
            is_active=True,
        )
        db.add(source)
        db.flush()
        binding = SnapshotExchangeSourceBinding(
            source_id=source.id,
            source_system_code="DEMO-B-OPS",
            is_active=True,
        )
        db.add(binding)
        db.commit()
        return int(binding.id)


def _package(*, delivery_id: str = "DELIVERY-20260805-001") -> SnapshotExchangePackageV1:
    raw = {
        "exchange_version": "OPS_EXCHANGE_V1",
        "delivery_id": delivery_id,
        "source_system_code": "DEMO-B-OPS",
        "building_code": "DEMO_B",
        "metric_definition_version": "OPS_V1",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "as_of_date": "2026-08-05",
        "source_observed_at": "2026-08-05T08:00:00Z",
        "source_updated_at": "2026-08-05T07:30:00Z",
        "source_published_at": "2026-08-05T09:00:00Z",
        "source_publication_revision": 1,
        "source_publication_ref": "PUB-20260805-001",
        "correction_of_delivery_id": None,
        "correction_reason": None,
        "data_classification": "simulated",
        "digest_profile": "OPS_CANONICAL_V1",
        "digest_algorithm": "sha-256",
        "content_digest": "0" * 64,
        "metrics": [_known_metric(code) for code in sorted(SUBMITTABLE_CODES)],
        "workflow": {
            "submitted": {
                "actor_ref": "user:submitter",
                "occurred_at": "2026-08-05T08:10:00Z",
            },
            "reviewed": {
                "actor_ref": "user:reviewer",
                "occurred_at": "2026-08-05T08:30:00Z",
            },
            "published": {
                "actor_ref": "user:publisher",
                "occurred_at": "2026-08-05T09:00:00Z",
            },
        },
    }
    unsigned = SnapshotExchangePackageV1.model_validate(raw)
    raw["content_digest"] = compute_content_digest(unsigned)
    return SnapshotExchangePackageV1.model_validate(raw)


def _known_metric(code: str) -> dict:
    aggregation = OPS_V1_BY_CODE[code]["aggregation_rule"]
    if aggregation == "ratio_percent":
        value, numerator, denominator = "50", "1", "2"
    elif aggregation == "ratio":
        value, numerator, denominator = "0.5", "1", "2"
    else:
        value, numerator, denominator = "1", "1", None
    return {
        "code": code,
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "covered_area_sqm": "1",
        "eligible_area_sqm": "1",
        "covered_days": 5,
        "eligible_days": 31,
        "estimate_share": "0",
        "estimate_method": "",
        "completeness_status": "partial",
        "freshness_status": "current",
        "calculation_version": "ops-source-v1",
        "source_batch_ref": "BATCH-20260805-001",
        "source_record_refs": [f"METRIC:{code}"],
    }


def _resign(raw: dict) -> SnapshotExchangePackageV1:
    raw["content_digest"] = "0" * 64
    unsigned = SnapshotExchangePackageV1.model_validate(raw)
    raw["content_digest"] = compute_content_digest(unsigned)
    return SnapshotExchangePackageV1.model_validate(raw)


def _changed_package(package: SnapshotExchangePackageV1) -> SnapshotExchangePackageV1:
    raw = package.model_dump(mode="json")
    metric = next(item for item in raw["metrics"] if item["code"] == "area.rentable_sqm")
    metric["value"] = "101"
    metric["numerator"] = "101"
    return _resign(raw)


def _correction(
    package: SnapshotExchangePackageV1,
    *,
    delivery_id: str,
    source_revision: int,
    correction_of_delivery_id: str | None,
    value: str,
) -> SnapshotExchangePackageV1:
    raw = package.model_dump(mode="json")
    raw["delivery_id"] = delivery_id
    raw["source_publication_revision"] = source_revision
    raw["source_publication_ref"] = f"PUB-20260805-{source_revision:03d}-{delivery_id[-3:]}"
    raw["correction_of_delivery_id"] = correction_of_delivery_id
    raw["correction_reason"] = (
        "来源复核后更正模拟值" if correction_of_delivery_id is not None else None
    )
    metric = next(item for item in raw["metrics"] if item["code"] == "area.rentable_sqm")
    metric["value"] = value
    metric["numerator"] = value
    return _resign(raw)


def _business_counts(db) -> dict[str, int]:
    return {
        "batches": db.query(ImportBatch).count(),
        "records": db.query(SourceRecord).count(),
        "snapshots": db.query(BuildingOperatingSnapshot).count(),
        "facts": db.query(BuildingMetricFact).count(),
    }


def _receive_concurrently(
    *,
    actor_id: int,
    packages: tuple[SnapshotExchangePackageV1, SnapshotExchangePackageV1],
) -> list[tuple[str, str, bool]]:
    start = Barrier(len(packages))

    def run(package: SnapshotExchangePackageV1) -> tuple[str, str, bool]:
        with SessionLocal() as db:
            actor = db.get(User, actor_id)
            start.wait(timeout=10)
            result = receive_snapshot_package(db, package=package, actor=actor)
            return (
                result.receipt.status,
                result.receipt.receipt_no,
                result.exact_replay,
            )

    with ThreadPoolExecutor(max_workers=len(packages)) as pool:
        return list(pool.map(run, packages))


def test_first_receive_creates_one_draft_candidate_and_exact_replay_returns_original_receipt() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        first = receive_snapshot_package(db, package=package, actor=actor)
        assert first.exact_replay is False
        assert first.receipt.status == "accepted"
        assert first.receipt.original_receipt_no is None
        assert first.receipt.candidate_batch_ref is not None
        assert first.receipt.center_status == "pending_review"
        public_receipt = first.receipt.model_dump(mode="json")
        assert not (
            {"id", "source_id", "building_id", "candidate_snapshot_id"}
            & set(public_receipt)
        )

        baseline = {
            "receipts": db.query(SnapshotExchangeReceipt).count(),
            "batches": db.query(ImportBatch).count(),
            "records": db.query(SourceRecord).count(),
            "snapshots": db.query(BuildingOperatingSnapshot).count(),
            "facts": db.query(BuildingMetricFact).count(),
            "audits": db.query(OperationLog)
            .filter(OperationLog.action == "snapshot_exchange_receive")
            .count(),
        }
        assert baseline == {
            "receipts": 1,
            "batches": 1,
            "records": len(SUBMITTABLE_CODES),
            "snapshots": 1,
            "facts": len(SUBMITTABLE_CODES),
            "audits": 1,
        }

        replay = receive_snapshot_package(db, package=package, actor=actor)
        assert replay.exact_replay is True
        assert replay.receipt == first.receipt
        assert db.query(SnapshotExchangeReceipt).count() == baseline["receipts"]
        assert db.query(ImportBatch).count() == baseline["batches"]
        assert db.query(SourceRecord).count() == baseline["records"]
        assert db.query(BuildingOperatingSnapshot).count() == baseline["snapshots"]
        assert db.query(BuildingMetricFact).count() == baseline["facts"]
        assert (
            db.query(OperationLog)
            .filter(OperationLog.action == "snapshot_exchange_receive")
            .count()
            == baseline["audits"]
        )

        snapshot = db.query(BuildingOperatingSnapshot).one()
        assert snapshot.publication_status == "draft"
        assert snapshot.is_current is False
        facts = db.query(BuildingMetricFact).all()
        assert all(fact.data_depth == "external" for fact in facts)
        assert all(fact.publication_status == "draft" for fact in facts)


def test_historical_receipt_codes_come_from_the_accepted_package_not_mutable_catalog_names() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        received = receive_snapshot_package(db, package=package, actor=actor)
        receipt_no = received.receipt.receipt_no
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        building.code = "DEMO_B_RENAMED"
        db.commit()

        historical = get_snapshot_exchange_receipt(db, receipt_no=receipt_no)
        assert historical is not None
        assert historical.source_system_code == "DEMO-B-OPS"
        assert historical.building_code == "DEMO_B"


def test_tampered_client_digest_is_rejected_with_a_stable_receipt_and_no_candidate() -> None:
    actor_id = _register_receiver()
    _bind_source()
    tampered = _package().model_copy(update={"content_digest": "f" * 64})

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        first = receive_snapshot_package(db, package=tampered, actor=actor)
        assert first.exact_replay is False
        assert first.receipt.status == "rejected"
        assert [item.code for item in first.receipt.errors] == ["content_digest_mismatch"]
        assert first.receipt.candidate_batch_ref is None
        assert first.receipt.center_status == "none"
        assert db.query(SnapshotExchangeReceipt).count() == 1
        assert db.query(ImportBatch).count() == 0
        assert db.query(SourceRecord).count() == 0
        assert db.query(BuildingOperatingSnapshot).count() == 0
        assert db.query(BuildingMetricFact).count() == 0

        replay = receive_snapshot_package(db, package=tampered, actor=actor)
        assert replay.exact_replay is True
        assert replay.receipt == first.receipt
        assert db.query(SnapshotExchangeReceipt).count() == 1
        assert db.query(ImportBatch).count() == 0


def test_same_content_under_a_new_delivery_id_is_a_stable_duplicate_without_new_business_data() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    alias = package.model_copy(update={"delivery_id": "DELIVERY-20260805-002"})

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        original = receive_snapshot_package(db, package=package, actor=actor)
        baseline = _business_counts(db)

        duplicate = receive_snapshot_package(db, package=alias, actor=actor)
        assert duplicate.exact_replay is False
        assert duplicate.receipt.status == "duplicate"
        assert duplicate.receipt.original_receipt_no == original.receipt.receipt_no
        assert duplicate.receipt.candidate_batch_ref == original.receipt.candidate_batch_ref
        assert duplicate.receipt.errors == []
        assert db.query(SnapshotExchangeReceipt).count() == 2
        assert _business_counts(db) == baseline
        assert (
            db.query(OperationLog)
            .filter(OperationLog.action == "snapshot_exchange_duplicate")
            .count()
            == 1
        )

        replay = receive_snapshot_package(db, package=alias, actor=actor)
        assert replay.exact_replay is True
        assert replay.receipt == duplicate.receipt
        assert db.query(SnapshotExchangeReceipt).count() == 2
        assert _business_counts(db) == baseline


def test_same_delivery_id_with_changed_content_is_a_stable_conflict_without_new_business_data() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    changed = _changed_package(package)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        original = receive_snapshot_package(db, package=package, actor=actor)
        baseline = _business_counts(db)

        conflict = receive_snapshot_package(db, package=changed, actor=actor)
        assert conflict.exact_replay is False
        assert conflict.receipt.status == "conflict"
        assert conflict.receipt.original_receipt_no == original.receipt.receipt_no
        assert [item.code for item in conflict.receipt.errors] == ["delivery_id_conflict"]
        assert db.query(SnapshotExchangeReceipt).count() == 2
        assert _business_counts(db) == baseline

        replay = receive_snapshot_package(db, package=changed, actor=actor)
        assert replay.exact_replay is True
        assert replay.receipt == conflict.receipt
        assert db.query(SnapshotExchangeReceipt).count() == 2
        assert _business_counts(db) == baseline


def test_missing_required_or_center_derived_metrics_reject_the_whole_package() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    raw["metrics"] = [
        item for item in raw["metrics"] if item["code"] != "risk.open_items"
    ]
    missing = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        rejected = receive_snapshot_package(db, package=missing, actor=actor)
        assert rejected.receipt.status == "rejected"
        assert "missing_required_metric" in {item.code for item in rejected.receipt.errors}
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }

    raw = _package(delivery_id="DELIVERY-20260805-002").model_dump(mode="json")
    raw["metrics"].append(_known_metric("portfolio.building_count"))
    raw["metrics"].sort(key=lambda item: item["code"])
    center_derived = _resign(raw)
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        rejected = receive_snapshot_package(db, package=center_derived, actor=actor)
        assert rejected.receipt.status == "rejected"
        assert "unknown_metric" in {item.code for item in rejected.receipt.errors}
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }


def test_inconsistent_ratio_freshness_and_future_source_time_reject_atomically() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    next(item for item in raw["metrics"] if item["code"] == "collection.rate_month")[
        "value"
    ] = "51"
    next(item for item in raw["metrics"] if item["code"] == "area.rentable_sqm")[
        "freshness_status"
    ] = "stale"
    raw["source_updated_at"] = "2099-08-05T07:30:00Z"
    raw["source_observed_at"] = "2099-08-05T08:00:00Z"
    raw["workflow"]["submitted"]["occurred_at"] = "2099-08-05T08:10:00Z"
    raw["workflow"]["reviewed"]["occurred_at"] = "2099-08-05T08:30:00Z"
    raw["workflow"]["published"]["occurred_at"] = "2099-08-05T09:00:00Z"
    raw["source_published_at"] = "2099-08-05T09:00:00Z"
    inconsistent = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        rejected = receive_snapshot_package(db, package=inconsistent, actor=actor)
        assert rejected.receipt.status == "rejected"
        codes = {item.code for item in rejected.receipt.errors}
        assert {"invalid_metric_fact", "freshness_mismatch", "source_time_future"} <= codes
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }


def test_exchange_ratio_rejects_even_one_six_decimal_unit_of_difference() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    ratio = next(
        item for item in raw["metrics"] if item["code"] == "collection.rate_month"
    )
    ratio.update(
        {
            "numerator": "1",
            "denominator": "3",
            "value": "33.333334",
        }
    )
    package = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        result = receive_snapshot_package(db, package=package, actor=actor)
        assert result.receipt.status == "rejected"
        assert "invalid_metric_fact" in {item.code for item in result.receipt.errors}
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }


def test_exchange_ratio_rejects_unsafe_computed_value_when_value_is_omitted() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    ratio = next(
        item for item in raw["metrics"] if item["code"] == "occupancy.area_rate"
    )
    ratio.update(
        {
            "numerator": "999999999.999998",
            "denominator": "0.000003",
            "value": None,
        }
    )
    package = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        result = receive_snapshot_package(db, package=package, actor=actor)
        assert result.receipt.status == "rejected"
        assert "invalid_metric_fact" in {item.code for item in result.receipt.errors}
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }


def test_rejected_receipt_bounds_large_error_sets_and_remains_replayable() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    template = raw["metrics"][0]
    raw["metrics"] = []
    for index in range(100):
        metric = dict(template)
        metric["code"] = f"unknown.metric.{index:03d}"
        metric["source_record_refs"] = [f"UNKNOWN-{index:03d}"]
        raw["metrics"].append(metric)
    raw["source_updated_at"] = "2099-08-05T07:30:00Z"
    raw["source_observed_at"] = "2099-08-05T08:00:00Z"
    raw["workflow"]["submitted"]["occurred_at"] = "2099-08-05T08:10:00Z"
    raw["workflow"]["reviewed"]["occurred_at"] = "2099-08-05T08:30:00Z"
    raw["source_published_at"] = "2099-08-05T09:00:00Z"
    raw["workflow"]["published"]["occurred_at"] = "2099-08-05T09:00:00Z"
    package = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        first = receive_snapshot_package(db, package=package, actor=actor)
        assert first.receipt.status == "rejected"
        assert len(first.receipt.errors) == 100
        assert first.receipt.errors[-1].code == "too_many_errors"
        replay = receive_snapshot_package(db, package=package, actor=actor)
        assert replay.exact_replay is True
        assert replay.receipt == first.receipt

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 1
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }


def test_explicit_unknown_stays_null_while_real_zero_stays_zero() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    unknown = next(item for item in raw["metrics"] if item["code"] == "risk.open_items")
    unknown.update(
        {
            "value": None,
            "numerator": None,
            "denominator": None,
            "covered_area_sqm": None,
            "covered_days": 0,
            "estimate_share": None,
            "completeness_status": "unknown",
            "freshness_status": "unknown",
        }
    )
    zero = next(item for item in raw["metrics"] if item["code"] == "arrears.balance")
    zero.update({"value": "0", "numerator": "0"})
    package = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        result = receive_snapshot_package(db, package=package, actor=actor)
        assert result.receipt.status == "accepted"
        facts = {
            definition.code: fact
            for fact, definition in db.query(BuildingMetricFact, MetricDefinition)
            .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
            .all()
        }
        assert facts["risk.open_items"].value is None
        assert facts["risk.open_items"].numerator is None
        assert facts["risk.open_items"].included_buildings == 0
        assert facts["risk.open_items"].freshness_status == "unknown"
        assert facts["arrears.balance"].value == 0
        assert facts["arrears.balance"].numerator == 0


def test_exchange_numeric_upper_bound_round_trips_through_center_storage() -> None:
    actor_id = _register_receiver()
    _bind_source()
    raw = _package().model_dump(mode="json")
    metric = next(item for item in raw["metrics"] if item["code"] == "area.rentable_sqm")
    metric["value"] = "999999999.999999"
    metric["numerator"] = "999999999.999999"
    package = _resign(raw)

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        result = receive_snapshot_package(db, package=package, actor=actor)
        assert result.receipt.status == "accepted"

    with SessionLocal() as db:
        fact = (
            db.query(BuildingMetricFact)
            .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
            .filter(MetricDefinition.code == "area.rentable_sqm")
            .one()
        )
        assert Decimal(str(fact.value)) == Decimal("999999999.999999")
        assert Decimal(str(fact.numerator)) == Decimal("999999999.999999")


def test_correction_must_extend_the_latest_source_revision_and_links_its_candidate_chain() -> None:
    actor_id = _register_receiver()
    _bind_source()
    original_package = _package()
    correction = _correction(
        original_package,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=original_package.delivery_id,
        value="2",
    )

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        original = receive_snapshot_package(db, package=original_package, actor=actor)
        corrected = receive_snapshot_package(db, package=correction, actor=actor)
        assert corrected.receipt.status == "accepted"
        assert corrected.receipt.correction_of_receipt_no == original.receipt.receipt_no

        receipts = (
            db.query(SnapshotExchangeReceipt)
            .filter(SnapshotExchangeReceipt.status == "accepted")
            .order_by(SnapshotExchangeReceipt.source_publication_revision.asc())
            .all()
        )
        assert len(receipts) == 2
        assert receipts[1].correction_of_receipt_id == receipts[0].id
        batches = db.query(ImportBatch).order_by(ImportBatch.id.asc()).all()
        assert batches[1].supersedes_id == batches[0].id
        assert batches[1].correction_reason == "来源复核后更正模拟值"
        snapshots = db.query(BuildingOperatingSnapshot).order_by(BuildingOperatingSnapshot.revision).all()
        assert [item.revision for item in snapshots] == [1, 2]

        baseline = _business_counts(db)
        late_branch = _correction(
            original_package,
            delivery_id="DELIVERY-20260805-003",
            source_revision=3,
            correction_of_delivery_id=original_package.delivery_id,
            value="3",
        )
        conflict = receive_snapshot_package(db, package=late_branch, actor=actor)
        assert conflict.receipt.status == "conflict"
        assert conflict.receipt.original_receipt_no == corrected.receipt.receipt_no
        assert [item.code for item in conflict.receipt.errors] == [
            "correction_target_not_current"
        ]
        assert _business_counts(db) == baseline

        nonincreasing = _correction(
            correction,
            delivery_id="DELIVERY-20260805-004",
            source_revision=2,
            correction_of_delivery_id=correction.delivery_id,
            value="4",
        )
        conflict = receive_snapshot_package(db, package=nonincreasing, actor=actor)
        assert conflict.receipt.status == "conflict"
        assert [item.code for item in conflict.receipt.errors] == [
            "source_revision_not_newer"
        ]
        assert _business_counts(db) == baseline

        unreferenced = _correction(
            correction,
            delivery_id="DELIVERY-20260805-005",
            source_revision=3,
            correction_of_delivery_id=None,
            value="5",
        )
        conflict = receive_snapshot_package(db, package=unreferenced, actor=actor)
        assert conflict.receipt.status == "conflict"
        assert [item.code for item in conflict.receipt.errors] == ["correction_required"]
        assert _business_counts(db) == baseline


def test_center_review_refuses_an_exchange_candidate_after_a_newer_source_revision_arrives() -> None:
    receiver_id = _register_receiver()
    reviewer_id = _register_actor("exchange_reviewer")
    _bind_source()
    original_package = _package()
    correction = _correction(
        original_package,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=original_package.delivery_id,
        value="2",
    )

    with SessionLocal() as db:
        receiver = db.get(User, receiver_id)
        reviewer = db.get(User, reviewer_id)
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        receive_snapshot_package(db, package=original_package, actor=receiver)
        original_snapshot = (
            db.query(BuildingOperatingSnapshot)
            .filter(BuildingOperatingSnapshot.revision == 1)
            .one()
        )
        receive_snapshot_package(db, package=correction, actor=receiver)

        with pytest.raises(HTTPException) as exc_info:
            review_operating_snapshot(
                db,
                original_snapshot.id,
                scope=_scope(user_id=reviewer_id, building=building),
                actor=reviewer,
                approved=True,
                notes="来源修订门禁测试",
            )
        assert exc_info.value.status_code == 409
        db.refresh(original_snapshot)
        assert original_snapshot.publication_status == "draft"
        assert (
            db.query(OperationLog)
            .filter(
                OperationLog.target_type == "operating_snapshot",
                OperationLog.target_id == original_snapshot.id,
                OperationLog.action == "snapshot_review",
            )
            .count()
            == 0
        )


def test_first_exchange_delivery_cannot_silently_replace_an_existing_center_publication() -> None:
    receiver_id = _register_receiver()
    publisher_id = _register_actor("existing_center_publisher")
    _bind_source()
    package = _package()

    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        now = now_in_timezone()
        db.add(
            BuildingOperatingSnapshot(
                building_id=building.id,
                period_start=package.period_start,
                period_end=package.period_end,
                as_of_date=package.as_of_date,
                revision=1,
                validation_status="valid",
                publication_status="published",
                is_current=True,
                submitted_by=publisher_id,
                submitted_at=now,
                reviewed_by=publisher_id,
                reviewed_at=now,
                published_by=publisher_id,
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    with SessionLocal() as db:
        receiver = db.get(User, receiver_id)
        result = receive_snapshot_package(db, package=package, actor=receiver)
        assert result.receipt.status == "rejected"
        assert {item.code for item in result.receipt.errors} == {
            "center_correction_reference_required"
        }

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 1
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 1,
            "facts": 0,
        }
        current = db.query(BuildingOperatingSnapshot).one()
        assert current.publication_status == "published"
        assert current.is_current is True


def test_center_publish_rechecks_source_head_and_only_the_latest_correction_can_publish() -> None:
    receiver_id = _register_receiver()
    reviewer_id = _register_actor("exchange_reviewer")
    publisher_id = _register_actor("exchange_publisher")
    _bind_source()
    original_package = _package()
    correction = _correction(
        original_package,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=original_package.delivery_id,
        value="2",
    )

    with SessionLocal() as db:
        receiver = db.get(User, receiver_id)
        reviewer = db.get(User, reviewer_id)
        publisher = db.get(User, publisher_id)
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        reviewer_scope = _scope(user_id=reviewer_id, building=building)
        publisher_scope = _scope(user_id=publisher_id, building=building)

        receive_snapshot_package(db, package=original_package, actor=receiver)
        original_snapshot = db.query(BuildingOperatingSnapshot).one()
        review_operating_snapshot(
            db,
            original_snapshot.id,
            scope=reviewer_scope,
            actor=reviewer,
            approved=True,
            notes="先复核来源修订1",
        )
        receive_snapshot_package(db, package=correction, actor=receiver)

        with pytest.raises(HTTPException) as exc_info:
            publish_operating_snapshot(
                db,
                original_snapshot.id,
                scope=publisher_scope,
                actor=publisher,
                notes="不应发布旧来源修订",
            )
        assert exc_info.value.status_code == 409
        db.refresh(original_snapshot)
        assert original_snapshot.publication_status == "reviewed"

        corrected_snapshot = (
            db.query(BuildingOperatingSnapshot)
            .filter(BuildingOperatingSnapshot.revision == 2)
            .one()
        )
        review_operating_snapshot(
            db,
            corrected_snapshot.id,
            scope=reviewer_scope,
            actor=reviewer,
            approved=True,
            notes="复核最新来源修订",
        )
        published = publish_operating_snapshot(
            db,
            corrected_snapshot.id,
            scope=publisher_scope,
            actor=publisher,
            notes="发布最新来源修订",
        )
        assert published["publication_status"] == "published"
        audit_actions = [
            item.action
            for item in db.query(OperationLog)
            .filter(
                OperationLog.target_type == "operating_snapshot",
                OperationLog.target_id == corrected_snapshot.id,
            )
            .order_by(OperationLog.id.asc())
            .all()
        ]
        assert audit_actions == ["snapshot_submit", "snapshot_review", "snapshot_publish"]


def test_publish_and_source_correction_are_serialized_at_the_latest_source_head_gate(
    monkeypatch,
) -> None:
    receiver_id = _register_receiver()
    reviewer_id = _register_actor("exchange_race_reviewer")
    publisher_id = _register_actor("exchange_race_publisher")
    _bind_source()
    original_package = _package()
    correction = _correction(
        original_package,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=original_package.delivery_id,
        value="2",
    )

    with SessionLocal() as db:
        receiver = db.get(User, receiver_id)
        reviewer = db.get(User, reviewer_id)
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        receive_snapshot_package(db, package=original_package, actor=receiver)
        original_snapshot = db.query(BuildingOperatingSnapshot).one()
        original_snapshot_id = int(original_snapshot.id)
        review_operating_snapshot(
            db,
            original_snapshot_id,
            scope=_scope(user_id=reviewer_id, building=building),
            actor=reviewer,
            approved=True,
            notes="并发发布门禁测试复核",
        )

    import app.services.snapshot_exchange as exchange_service

    original_gate = exchange_service.assert_snapshot_exchange_candidate_latest
    head_checked = Event()
    correction_committed = Event()

    def pause_after_source_head_check(db, *, snapshot_id: int) -> None:
        original_gate(db, snapshot_id=snapshot_id)
        head_checked.set()
        assert correction_committed.wait(timeout=10), "更正接收线程未按时提交"

    monkeypatch.setattr(
        exchange_service,
        "assert_snapshot_exchange_candidate_latest",
        pause_after_source_head_check,
    )

    def publish_original() -> int:
        with SessionLocal() as db:
            publisher = db.get(User, publisher_id)
            building = db.query(Building).filter(Building.code == "DEMO_B").one()
            try:
                publish_operating_snapshot(
                    db,
                    original_snapshot_id,
                    scope=_scope(user_id=publisher_id, building=building),
                    actor=publisher,
                    notes="不应越过已提交的新来源修订",
                )
            except HTTPException as exc:
                return exc.status_code
            return 200

    with ThreadPoolExecutor(max_workers=1) as pool:
        publishing = pool.submit(publish_original)
        assert head_checked.wait(timeout=10), "发布线程未按时通过前置来源头检查"
        try:
            with SessionLocal() as db:
                receiver = db.get(User, receiver_id)
                result = receive_snapshot_package(db, package=correction, actor=receiver)
                assert result.receipt.status == "accepted"
        finally:
            correction_committed.set()
        publish_status = publishing.result(timeout=10)

    assert publish_status == 409
    with SessionLocal() as db:
        snapshots = (
            db.query(BuildingOperatingSnapshot)
            .order_by(BuildingOperatingSnapshot.revision.asc())
            .all()
        )
        assert [item.publication_status for item in snapshots] == ["reviewed", "draft"]
        assert all(item.is_current is False for item in snapshots)
        assert (
            db.query(OperationLog)
            .filter(
                OperationLog.target_type == "operating_snapshot",
                OperationLog.target_id == original_snapshot_id,
                OperationLog.action == "snapshot_publish",
            )
            .count()
            == 0
        )


def test_second_fact_write_failure_rolls_back_the_entire_exchange_transaction() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    fact_inserts = 0

    def fail_second_fact(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal fact_inserts
        if statement.lstrip().upper().startswith("INSERT INTO BUILDING_METRIC_FACTS"):
            fact_inserts += 1
            if fact_inserts == 2:
                raise RuntimeError("injected second fact write failure")

    event.listen(engine, "before_cursor_execute", fail_second_fact)
    try:
        with SessionLocal() as db:
            actor = db.get(User, actor_id)
            with pytest.raises(RuntimeError, match="injected second fact write failure"):
                receive_snapshot_package(db, package=package, actor=actor)
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_fact)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert _business_counts(db) == {
            "batches": 0,
            "records": 0,
            "snapshots": 0,
            "facts": 0,
        }
        assert (
            db.query(OperationLog)
            .filter(
                OperationLog.action.in_(
                    ("snapshot_submit", "snapshot_exchange_receive")
                )
            )
            .count()
            == 0
        )


def test_concurrent_exact_replay_and_content_alias_produce_one_business_result() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()

    exact_results = _receive_concurrently(
        actor_id=actor_id,
        packages=(package, package),
    )
    assert [item[0] for item in exact_results] == ["accepted", "accepted"]
    assert len({item[1] for item in exact_results}) == 1
    assert sorted(item[2] for item in exact_results) == [False, True]
    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 1
        assert _business_counts(db) == {
            "batches": 1,
            "records": len(SUBMITTABLE_CODES),
            "snapshots": 1,
            "facts": len(SUBMITTABLE_CODES),
        }


def test_receive_contention_uses_a_small_bounded_retry_count(monkeypatch) -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    attempts = 0

    def always_locked(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(
        exchange_service,
        "_receive_snapshot_package_once",
        always_locked,
    )
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        with pytest.raises(exchange_service.SnapshotExchangeUnavailable):
            receive_snapshot_package(db, package=package, actor=actor)
    assert attempts <= 5


def test_concurrent_different_delivery_ids_for_same_content_create_one_duplicate_alias() -> None:
    actor_id = _register_receiver()
    _bind_source()
    package = _package()
    alias = package.model_copy(update={"delivery_id": "DELIVERY-20260805-002"})

    results = _receive_concurrently(actor_id=actor_id, packages=(package, alias))
    assert sorted(item[0] for item in results) == ["accepted", "duplicate"]
    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 2
        assert _business_counts(db) == {
            "batches": 1,
            "records": len(SUBMITTABLE_CODES),
            "snapshots": 1,
            "facts": len(SUBMITTABLE_CODES),
        }


def test_concurrent_different_first_deliveries_create_only_one_accepted_root() -> None:
    actor_id = _register_receiver()
    _bind_source()
    first = _package()
    competing_root = _correction(
        first,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=None,
        value="2",
    )

    results = _receive_concurrently(
        actor_id=actor_id,
        packages=(first, competing_root),
    )
    assert sorted(item[0] for item in results) == ["accepted", "conflict"]
    with SessionLocal() as db:
        accepted = db.query(SnapshotExchangeReceipt).filter(
            SnapshotExchangeReceipt.status == "accepted"
        ).all()
        assert len(accepted) == 1
        assert accepted[0].correction_of_receipt_id is None
        assert _business_counts(db) == {
            "batches": 1,
            "records": len(SUBMITTABLE_CODES),
            "snapshots": 1,
            "facts": len(SUBMITTABLE_CODES),
        }


def test_concurrent_competing_corrections_accept_only_one_source_successor() -> None:
    actor_id = _register_receiver()
    _bind_source()
    original = _package()
    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        receive_snapshot_package(db, package=original, actor=actor)

    correction_a = _correction(
        original,
        delivery_id="DELIVERY-20260805-002",
        source_revision=2,
        correction_of_delivery_id=original.delivery_id,
        value="2",
    )
    correction_b = _correction(
        original,
        delivery_id="DELIVERY-20260805-003",
        source_revision=3,
        correction_of_delivery_id=original.delivery_id,
        value="3",
    )
    results = _receive_concurrently(
        actor_id=actor_id,
        packages=(correction_a, correction_b),
    )
    assert sorted(item[0] for item in results) == ["accepted", "conflict"]
    with SessionLocal() as db:
        accepted = (
            db.query(SnapshotExchangeReceipt)
            .filter(SnapshotExchangeReceipt.status == "accepted")
            .order_by(SnapshotExchangeReceipt.source_publication_revision.asc())
            .all()
        )
        assert len(accepted) == 2
        assert accepted[1].correction_of_receipt_id == accepted[0].id
        assert accepted[1].source_publication_revision in {2, 3}
        assert _business_counts(db) == {
            "batches": 2,
            "records": 2 * len(SUBMITTABLE_CODES),
            "snapshots": 2,
            "facts": 2 * len(SUBMITTABLE_CODES),
        }


def test_leadership_overview_changes_only_after_center_publish_and_never_exposes_exchange_detail() -> None:
    receiver_id = _register_receiver()
    reviewer_id = _register_actor("exchange_reviewer")
    publisher_id = _register_actor("exchange_publisher")
    _bind_source()
    package = _package()

    with SessionLocal() as db:
        receiver = db.get(User, receiver_id)
        reviewer = db.get(User, reviewer_id)
        publisher = db.get(User, publisher_id)
        portfolio = (
            db.query(Portfolio)
            .filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO")
            .one()
        )
        buildings = (
            db.query(Building)
            .join(PortfolioBuilding, PortfolioBuilding.building_id == Building.id)
            .filter(PortfolioBuilding.portfolio_id == portfolio.id)
            .order_by(Building.code.asc())
            .all()
        )
        overview_scope = SelectedPropertyScope(
            user_id=publisher_id,
            building_ids=tuple(item.id for item in buildings),
            building_codes=tuple(item.code for item in buildings),
            scope_type="portfolio",
            scope_code=portfolio.code,
            portfolio_id=portfolio.id,
            access_level="overview",
            authorization_version="snapshot-exchange-overview-test",
        )
        building = next(item for item in buildings if item.code == "DEMO_B")
        reviewer_scope = _scope(user_id=reviewer_id, building=building)
        publisher_scope = _scope(user_id=publisher_id, building=building)

        before = build_portfolio_overview(
            db,
            scope=overview_scope,
            as_of_date=package.as_of_date,
        )

        def stable_overview(payload: dict) -> dict:
            stable = json.loads(json.dumps(payload, ensure_ascii=False))
            stable["rollout"].pop("evaluated_at", None)
            return stable

        receive_snapshot_package(db, package=package, actor=receiver)
        pending = build_portfolio_overview(
            db,
            scope=overview_scope,
            as_of_date=package.as_of_date,
        )
        assert stable_overview(pending) == stable_overview(before)

        snapshot = db.query(BuildingOperatingSnapshot).one()
        review_operating_snapshot(
            db,
            snapshot.id,
            scope=reviewer_scope,
            actor=reviewer,
            approved=True,
            notes="中心复核通过",
        )
        still_pending = build_portfolio_overview(
            db,
            scope=overview_scope,
            as_of_date=package.as_of_date,
        )
        assert stable_overview(still_pending) == stable_overview(before)
        publish_operating_snapshot(
            db,
            snapshot.id,
            scope=publisher_scope,
            actor=publisher,
            notes="中心发布通过",
        )
        published = build_portfolio_overview(
            db,
            scope=overview_scope,
            as_of_date=package.as_of_date,
        )
        contribution = next(
            item
            for item in published["contributions"]
            if item["building"]["code"] == "DEMO_B"
        )
        assert contribution["publication_status"] == "published"
        assert contribution["metrics"]["area.rentable_sqm"]["value"] == 1
        serialized = json.dumps(published, ensure_ascii=False)
        for forbidden in (
            "exchange_version",
            "delivery_id",
            "receipt_no",
            "source_system_code",
            "source_publication_ref",
            "correction_of_receipt_no",
            "workflow",
            "errors",
        ):
            assert forbidden not in serialized
