from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased

from app.core.time import app_timezone, now_in_timezone
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    SnapshotExchangeReceipt,
    SnapshotExchangeSourceBinding,
    SourceRecord,
    User,
)
from app.ops_v1 import SUBMITTABLE_CODES
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.metric_definitions import published_ops_v1_by_code
from app.services.snapshot_workflow import _prepare_fact
from app.snapshot_exchange_schemas import (
    SnapshotExchangePackageV1,
    SnapshotExchangeReceiptOut,
    canonical_business_json,
    compute_content_digest,
)


@dataclass(frozen=True)
class ReceiveResult:
    receipt: SnapshotExchangeReceiptOut
    exact_replay: bool


class SnapshotExchangeIdentityError(ValueError):
    pass


class SnapshotExchangeUnavailable(RuntimeError):
    pass


_MAX_RECEIPT_ERRORS = 100
_RECEIVE_MAX_ATTEMPTS = 5


def _bounded_receipt_errors(errors: list[dict]) -> list[dict]:
    if len(errors) <= _MAX_RECEIPT_ERRORS:
        return errors
    retained = errors[: _MAX_RECEIPT_ERRORS - 1]
    retained.append(
        {
            "code": "too_many_errors",
            "field": None,
            "row": None,
            "message": (
                f"共发现 {len(errors)} 个问题；回执保留前 "
                f"{_MAX_RECEIPT_ERRORS - 1} 项，其余已汇总"
            ),
        }
    )
    return retained


def snapshot_exchange_candidate_latest_clause(snapshot_id_column):
    """Return a DB predicate that is true for non-exchange or latest exchange candidates."""
    candidate = aliased(SnapshotExchangeReceipt)
    newer = aliased(SnapshotExchangeReceipt)
    candidate_for_snapshot = and_(
        candidate.candidate_snapshot_id == snapshot_id_column,
        candidate.status == "accepted",
    )
    candidate_exists = exists().where(candidate_for_snapshot)
    newer_exists = exists().where(
        candidate_for_snapshot,
        newer.binding_id == candidate.binding_id,
        newer.period_start == candidate.period_start,
        newer.period_end == candidate.period_end,
        newer.as_of_date == candidate.as_of_date,
        newer.status == "accepted",
        or_(
            newer.source_publication_revision > candidate.source_publication_revision,
            and_(
                newer.source_publication_revision == candidate.source_publication_revision,
                newer.id > candidate.id,
            ),
        ),
    )
    return or_(~candidate_exists, ~newer_exists)


def assert_snapshot_exchange_candidate_latest(db: Session, *, snapshot_id: int) -> None:
    candidate = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.candidate_snapshot_id == snapshot_id,
            SnapshotExchangeReceipt.status == "accepted",
        )
        .one_or_none()
    )
    if candidate is None:
        return
    source_head = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.binding_id == candidate.binding_id,
            SnapshotExchangeReceipt.period_start == candidate.period_start,
            SnapshotExchangeReceipt.period_end == candidate.period_end,
            SnapshotExchangeReceipt.as_of_date == candidate.as_of_date,
            SnapshotExchangeReceipt.status == "accepted",
        )
        .order_by(
            SnapshotExchangeReceipt.source_publication_revision.desc(),
            SnapshotExchangeReceipt.id.desc(),
        )
        .first()
    )
    if source_head is None or source_head.id != candidate.id:
        raise HTTPException(409, "该候选已被更新的来源修订替代，不能继续复核或发布")


def _utc_text(value: datetime) -> str:
    aware = value.replace(tzinfo=app_timezone()).astimezone(timezone.utc)
    return aware.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source_time(value: str) -> datetime:
    utc_value = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return utc_value.astimezone(app_timezone()).replace(tzinfo=None)


def _source_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _new_public_ref(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _binding_for_package(
    db: Session,
    package: SnapshotExchangePackageV1,
) -> tuple[SnapshotExchangeSourceBinding, DataSource, Building]:
    row = (
        db.query(SnapshotExchangeSourceBinding, DataSource, Building)
        .join(DataSource, DataSource.id == SnapshotExchangeSourceBinding.source_id)
        .join(Building, Building.id == DataSource.building_id)
        .filter(
            SnapshotExchangeSourceBinding.source_system_code == package.source_system_code,
            SnapshotExchangeSourceBinding.is_active.is_(True),
            DataSource.is_active.is_(True),
            DataSource.source_type == "external_api",
            Building.is_active.is_(True),
            Building.code == package.building_code,
        )
        .one_or_none()
    )
    if row is None:
        raise SnapshotExchangeIdentityError("来源系统不存在、已停用或未绑定到声明楼宇")
    return row


def _serialize_receipt(
    db: Session,
    receipt: SnapshotExchangeReceipt,
) -> SnapshotExchangeReceiptOut:
    binding = db.get(SnapshotExchangeSourceBinding, receipt.binding_id)
    if binding is None:
        raise RuntimeError("交换回执缺少来源绑定")
    source = db.get(DataSource, binding.source_id)
    building = db.get(Building, source.building_id) if source is not None else None
    if source is None or building is None:
        raise RuntimeError("交换回执来源链不完整")
    business = json.loads(receipt.normalized_business_json)
    original = db.get(SnapshotExchangeReceipt, receipt.original_receipt_id) if receipt.original_receipt_id else None
    correction_of = (
        db.get(SnapshotExchangeReceipt, receipt.correction_of_receipt_id)
        if receipt.correction_of_receipt_id
        else None
    )
    errors = json.loads(receipt.errors_json or "[]")
    candidate_ref = receipt.candidate_ref
    if candidate_ref is None and original is not None:
        candidate_ref = original.candidate_ref
    return SnapshotExchangeReceiptOut.model_validate(
        {
            "receipt_version": "OPS_EXCHANGE_RECEIPT_V1",
            "receipt_no": receipt.receipt_no,
            "exchange_version": receipt.exchange_version,
            "source_system_code": business["source_system_code"],
            "building_code": business["building_code"],
            "delivery_id": receipt.delivery_id,
            "digest_profile": receipt.digest_profile,
            "digest_algorithm": receipt.digest_algorithm,
            "server_content_digest": receipt.content_digest,
            "source_publication_revision": receipt.source_publication_revision,
            "status": receipt.status,
            "first_received_at": _utc_text(receipt.received_at),
            "original_receipt_no": original.receipt_no if original is not None else None,
            "correction_of_receipt_no": (
                correction_of.receipt_no if correction_of is not None else None
            ),
            "candidate_batch_ref": candidate_ref,
            "center_status": "pending_review" if receipt.status == "accepted" else "none",
            "errors": errors,
        }
    )


def get_snapshot_exchange_receipt(
    db: Session,
    *,
    receipt_no: str,
) -> SnapshotExchangeReceiptOut | None:
    receipt = (
        db.query(SnapshotExchangeReceipt)
        .filter(SnapshotExchangeReceipt.receipt_no == receipt_no)
        .one_or_none()
    )
    return _serialize_receipt(db, receipt) if receipt is not None else None


def get_snapshot_exchange_review_evidence(
    db: Session,
    *,
    snapshot_id: int,
) -> dict | None:
    receipt = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.candidate_snapshot_id == int(snapshot_id),
            SnapshotExchangeReceipt.status == "accepted",
        )
        .one_or_none()
    )
    if receipt is None:
        return None
    business = json.loads(receipt.normalized_business_json)
    return {
        "exchange_version": business["exchange_version"],
        "source_system_code": business["source_system_code"],
        "delivery_id": receipt.delivery_id,
        "source_publication_revision": receipt.source_publication_revision,
        "source_publication_ref": receipt.source_publication_ref,
        "correction_of_delivery_id": business.get("correction_of_delivery_id"),
        "correction_reason": business.get("correction_reason"),
        "source_observed_at": business["source_observed_at"],
        "source_updated_at": business.get("source_updated_at"),
        "source_published_at": business["source_published_at"],
        "workflow": business["workflow"],
    }


def _metric_checksum(metric_payload: dict) -> str:
    raw = json.dumps(
        metric_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unknown_fact(metric, definition) -> dict:
    return {
        "metric_definition_id": definition.id,
        "value": None,
        "numerator": None,
        "denominator": None,
        "eligible_buildings": 1,
        "included_buildings": 0,
        "covered_area_sqm": (
            float(metric.covered_area_sqm) if metric.covered_area_sqm is not None else None
        ),
        "eligible_area_sqm": (
            float(metric.eligible_area_sqm) if metric.eligible_area_sqm is not None else None
        ),
        "covered_days": metric.covered_days,
        "eligible_days": metric.eligible_days,
        "estimate_share": None,
        "estimate_method": "",
        "data_depth": "external",
        "completeness_status": "unknown",
        "freshness_status": "unknown",
        "validation_status": "valid",
        "publication_status": "draft",
        "calculation_version": metric.calculation_version,
    }


def _record_nonaccepted_receipt(
    db: Session,
    *,
    binding: SnapshotExchangeSourceBinding,
    building: Building,
    package: SnapshotExchangePackageV1,
    actor: User,
    server_digest: str,
    status: str,
    errors: list[dict],
    original: SnapshotExchangeReceipt | None = None,
) -> ReceiveResult:
    received_at = now_in_timezone()
    receipt_errors = _bounded_receipt_errors(errors)
    receipt = SnapshotExchangeReceipt(
        receipt_no=_new_public_ref("RX"),
        binding_id=binding.id,
        delivery_id=package.delivery_id,
        exchange_version=package.exchange_version,
        metric_definition_version=package.metric_definition_version,
        digest_profile=package.digest_profile,
        digest_algorithm=package.digest_algorithm,
        content_digest=server_digest,
        status=status,
        period_start=package.period_start,
        period_end=package.period_end,
        as_of_date=package.as_of_date,
        source_publication_revision=package.source_publication_revision,
        source_publication_ref=package.source_publication_ref,
        correction_of_receipt_id=None,
        original_receipt_id=original.id if original is not None else None,
        candidate_snapshot_id=None,
        candidate_batch_id=None,
        candidate_ref=None,
        normalized_business_json=canonical_business_json(package),
        errors_json=json.dumps(
            receipt_errors,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        actor_id=actor.id,
        actor_username=actor.username,
        received_at=received_at,
    )
    db.add(receipt)
    db.flush()
    serialized_receipt = _serialize_receipt(db, receipt)
    action = {
        "rejected": "snapshot_exchange_reject",
        "conflict": "snapshot_exchange_conflict",
        "duplicate": "snapshot_exchange_duplicate",
    }[status]
    log_action(
        db,
        None,
        action,
        f"模拟经营快照交换包{status}；来源 {binding.source_system_code}；投递 {package.delivery_id}",
        actor.username,
        commit=False,
        target_type="snapshot_exchange_receipt",
        target_id=receipt.id,
        object_scope=ObjectScope(scope_type="building", building_id=building.id),
    )
    db.commit()
    return ReceiveResult(receipt=serialized_receipt, exact_replay=False)


def _receive_snapshot_package_once(
    db: Session,
    *,
    package: SnapshotExchangePackageV1,
    actor: User,
) -> ReceiveResult:
    binding, source, building = _binding_for_package(db, package)
    server_digest = compute_content_digest(package)
    if not hmac.compare_digest(package.content_digest, server_digest):
        prior_mismatch = (
            db.query(SnapshotExchangeReceipt)
            .filter(
                SnapshotExchangeReceipt.binding_id == binding.id,
                SnapshotExchangeReceipt.delivery_id == package.delivery_id,
                SnapshotExchangeReceipt.content_digest == server_digest,
                SnapshotExchangeReceipt.status.in_(("rejected", "conflict")),
            )
            .order_by(SnapshotExchangeReceipt.id.asc())
            .first()
        )
        if prior_mismatch is not None:
            return ReceiveResult(
                receipt=_serialize_receipt(db, prior_mismatch),
                exact_replay=True,
            )
        canonical = (
            db.query(SnapshotExchangeReceipt)
            .filter(
                SnapshotExchangeReceipt.binding_id == binding.id,
                SnapshotExchangeReceipt.delivery_id == package.delivery_id,
                SnapshotExchangeReceipt.status.in_(("accepted", "duplicate", "rejected")),
            )
            .order_by(SnapshotExchangeReceipt.id.asc())
            .first()
        )
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="conflict" if canonical is not None else "rejected",
            original=canonical,
            errors=[
                {
                    "code": "content_digest_mismatch",
                    "field": "content_digest",
                    "row": None,
                    "message": "客户端摘要与服务端重算结果不一致",
                }
            ],
        )
    exact = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.binding_id == binding.id,
            SnapshotExchangeReceipt.delivery_id == package.delivery_id,
            SnapshotExchangeReceipt.content_digest == server_digest,
        )
        .order_by(SnapshotExchangeReceipt.id.asc())
        .first()
    )
    if exact is not None:
        return ReceiveResult(receipt=_serialize_receipt(db, exact), exact_replay=True)

    canonical_delivery = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.binding_id == binding.id,
            SnapshotExchangeReceipt.delivery_id == package.delivery_id,
            SnapshotExchangeReceipt.status.in_(("accepted", "duplicate", "rejected")),
        )
        .order_by(SnapshotExchangeReceipt.id.asc())
        .first()
    )
    if canonical_delivery is not None:
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="conflict",
            original=canonical_delivery,
            errors=[
                {
                    "code": "delivery_id_conflict",
                    "field": "delivery_id",
                    "row": None,
                    "message": "同一投递编号已绑定到不同业务内容",
                }
            ],
        )

    original_content = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.binding_id == binding.id,
            SnapshotExchangeReceipt.content_digest == server_digest,
            SnapshotExchangeReceipt.status == "accepted",
        )
        .order_by(SnapshotExchangeReceipt.id.asc())
        .first()
    )
    if original_content is not None:
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="duplicate",
            original=original_content,
            errors=[],
        )

    source_head = (
        db.query(SnapshotExchangeReceipt)
        .filter(
            SnapshotExchangeReceipt.binding_id == binding.id,
            SnapshotExchangeReceipt.period_start == package.period_start,
            SnapshotExchangeReceipt.period_end == package.period_end,
            SnapshotExchangeReceipt.as_of_date == package.as_of_date,
            SnapshotExchangeReceipt.status == "accepted",
        )
        .order_by(
            SnapshotExchangeReceipt.source_publication_revision.desc(),
            SnapshotExchangeReceipt.id.desc(),
        )
        .first()
    )
    if source_head is None and package.correction_of_delivery_id is not None:
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="rejected",
            errors=[
                {
                    "code": "correction_target_missing",
                    "field": "correction_of_delivery_id",
                    "row": None,
                    "message": "当前统计期间没有可被更正的已接收来源修订",
                }
            ],
        )
    if source_head is not None:
        if package.correction_of_delivery_id is None:
            return _record_nonaccepted_receipt(
                db,
                binding=binding,
                building=building,
                package=package,
                actor=actor,
                server_digest=server_digest,
                status="conflict",
                original=source_head,
                errors=[
                    {
                        "code": "correction_required",
                        "field": "correction_of_delivery_id",
                        "row": None,
                        "message": "该统计期间已有来源修订，新内容必须显式更正当前修订",
                    }
                ],
            )
        if package.correction_of_delivery_id != source_head.delivery_id:
            return _record_nonaccepted_receipt(
                db,
                binding=binding,
                building=building,
                package=package,
                actor=actor,
                server_digest=server_digest,
                status="conflict",
                original=source_head,
                errors=[
                    {
                        "code": "correction_target_not_current",
                        "field": "correction_of_delivery_id",
                        "row": None,
                        "message": "更正引用不是当前最新来源修订",
                    }
                ],
            )
        if package.source_publication_revision <= source_head.source_publication_revision:
            return _record_nonaccepted_receipt(
                db,
                binding=binding,
                building=building,
                package=package,
                actor=actor,
                server_digest=server_digest,
                status="conflict",
                original=source_head,
                errors=[
                    {
                        "code": "source_revision_not_newer",
                        "field": "source_publication_revision",
                        "row": None,
                        "message": "更正的来源发布修订必须严格高于当前修订",
                    }
                ],
            )

    issues: list[dict] = []
    received_clock = now_in_timezone().replace(tzinfo=app_timezone()).astimezone(timezone.utc)
    source_times = (
        ("source_observed_at", package.source_observed_at),
        ("source_updated_at", package.source_updated_at),
        ("source_published_at", package.source_published_at),
        ("workflow.submitted.occurred_at", package.workflow.submitted.occurred_at),
        ("workflow.reviewed.occurred_at", package.workflow.reviewed.occurred_at),
        ("workflow.published.occurred_at", package.workflow.published.occurred_at),
    )
    for field_name, raw_time in source_times:
        if raw_time is not None and _source_utc(raw_time) > received_clock:
            issues.append(
                {
                    "code": "source_time_future",
                    "field": field_name,
                    "row": None,
                    "message": "来源时间不能晚于中心接收时间",
                }
            )

    submitted_codes = {metric.code for metric in package.metrics}
    for code in sorted(SUBMITTABLE_CODES - submitted_codes):
        issues.append(
            {
                "code": "missing_required_metric",
                "field": f"metrics.{code}",
                "row": None,
                "message": f"交换包缺少必须指标 {code}；未知值也必须显式提交为空",
            }
        )

    definitions = published_ops_v1_by_code(db)
    source_observed_at = _source_time(package.source_observed_at)
    source_updated_at = _source_time(package.source_updated_at) if package.source_updated_at else None
    business_time = source_updated_at or source_observed_at
    authoritative_freshness = "current" if business_time.date() >= package.as_of_date else "stale"

    prepared: list[tuple[object, dict]] = []
    for row_no, metric in enumerate(package.metrics, start=1):
        definition = definitions.get(metric.code)
        if metric.code not in SUBMITTABLE_CODES or definition is None:
            issues.append(
                {
                    "code": "unknown_metric",
                    "field": "metrics.code",
                    "row": row_no,
                    "message": f"指标 {metric.code} 不属于已发布的 OPS_V1 可提交集合",
                }
            )
            continue
        if metric.numerator is None:
            prepared.append((metric, _unknown_fact(metric, definition)))
            continue
        if metric.freshness_status != authoritative_freshness:
            issues.append(
                {
                    "code": "freshness_mismatch",
                    "field": "metrics.freshness_status",
                    "row": row_no,
                    "message": "指标时效状态与来源时间和统计日的中心判定不一致",
                }
            )
        try:
            values = _prepare_fact(
                metric.model_dump(mode="python"),
                definition,
                freshness_status=authoritative_freshness,
                require_exact_value=True,
            )
        except (TypeError, ValueError) as exc:
            issues.append(
                {
                    "code": "invalid_metric_fact",
                    "field": None,
                    "row": row_no,
                    "message": str(exc),
                }
            )
            continue
        values["data_depth"] = "external"
        prepared.append((metric, values))

    if issues:
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="rejected",
            errors=issues,
        )

    current = (
        db.query(BuildingOperatingSnapshot)
        .filter(
            BuildingOperatingSnapshot.building_id == building.id,
            BuildingOperatingSnapshot.period_start == package.period_start,
            BuildingOperatingSnapshot.period_end == package.period_end,
            BuildingOperatingSnapshot.as_of_date == package.as_of_date,
            BuildingOperatingSnapshot.publication_status == "published",
            BuildingOperatingSnapshot.is_current.is_(True),
        )
        .one_or_none()
    )
    if current is not None and source_head is None:
        return _record_nonaccepted_receipt(
            db,
            binding=binding,
            building=building,
            package=package,
            actor=actor,
            server_digest=server_digest,
            status="rejected",
            errors=[
                {
                    "code": "center_correction_reference_required",
                    "field": "correction_of_delivery_id",
                    "row": None,
                    "message": (
                        "中心已存在当前发布版；现行交换契约无法安全引用该版本，"
                        "首个来源投递不得直接替换"
                    ),
                }
            ],
        )
    max_revision = (
        db.query(BuildingOperatingSnapshot.revision)
        .filter(
            BuildingOperatingSnapshot.building_id == building.id,
            BuildingOperatingSnapshot.period_start == package.period_start,
            BuildingOperatingSnapshot.period_end == package.period_end,
            BuildingOperatingSnapshot.as_of_date == package.as_of_date,
        )
        .order_by(BuildingOperatingSnapshot.revision.desc())
        .first()
    )
    received_at = now_in_timezone()
    batch = ImportBatch(
        source_id=source.id,
        source_file_name=None,
        external_batch_no=package.delivery_id,
        period=package.period_start.strftime("%Y-%m"),
        as_of_date=package.as_of_date,
        source_observed_at=source_observed_at,
        source_updated_at=source_updated_at,
        received_at=received_at,
        started_at=received_at,
        file_checksum=server_digest,
        parser_version="OPS_EXCHANGE_V1/OPS_CANONICAL_V1",
        actor_id=actor.id,
        row_count=len(prepared),
        accepted_count=0,
        rejected_count=0,
        status="validating",
        error_summary="",
        supersedes_id=(source_head.candidate_batch_id if source_head is not None else None),
        correction_reason=package.correction_reason,
    )
    db.add(batch)
    db.flush()

    snapshot = BuildingOperatingSnapshot(
        building_id=building.id,
        period_start=package.period_start,
        period_end=package.period_end,
        as_of_date=package.as_of_date,
        revision=(int(max_revision[0]) + 1) if max_revision else 1,
        validation_status="valid",
        publication_status="draft",
        is_current=False,
        submitted_by=actor.id,
        submitted_at=received_at,
        supersedes_id=current.id if current is not None else None,
        created_at=received_at,
        updated_at=received_at,
    )
    db.add(snapshot)
    db.flush()

    for row_no, (metric, values) in enumerate(prepared, start=1):
        metric_payload = metric.model_dump(mode="json")
        source_ref = json.dumps(
            {
                "source_batch_ref": metric.source_batch_ref,
                "source_record_refs": metric.source_record_refs,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        record = SourceRecord(
            batch_id=batch.id,
            external_record_id=metric.code,
            row_no=row_no,
            record_checksum=_metric_checksum(metric_payload),
            status="accepted",
            source_ref=source_ref,
        )
        db.add(record)
        db.flush()
        fact = BuildingMetricFact(
            snapshot_id=snapshot.id,
            source_id=source.id,
            batch_id=batch.id,
            source_record_id=record.id,
            **values,
        )
        db.add(fact)
        db.flush()
        record.business_object_type = "building_metric_fact"
        record.business_object_id = fact.id

    batch.accepted_count = len(prepared)
    batch.status = "received"
    batch.ingested_at = received_at
    batch.finished_at = received_at
    candidate_ref = _new_public_ref("CAND")
    receipt = SnapshotExchangeReceipt(
        receipt_no=_new_public_ref("RX"),
        binding_id=binding.id,
        delivery_id=package.delivery_id,
        exchange_version=package.exchange_version,
        metric_definition_version=package.metric_definition_version,
        digest_profile=package.digest_profile,
        digest_algorithm=package.digest_algorithm,
        content_digest=server_digest,
        status="accepted",
        period_start=package.period_start,
        period_end=package.period_end,
        as_of_date=package.as_of_date,
        source_publication_revision=package.source_publication_revision,
        source_publication_ref=package.source_publication_ref,
        correction_of_receipt_id=(source_head.id if source_head is not None else None),
        original_receipt_id=None,
        candidate_snapshot_id=snapshot.id,
        candidate_batch_id=batch.id,
        candidate_ref=candidate_ref,
        normalized_business_json=canonical_business_json(package),
        errors_json="[]",
        actor_id=actor.id,
        actor_username=actor.username,
        received_at=received_at,
    )
    db.add(receipt)
    db.flush()
    log_action(
        db,
        None,
        "snapshot_submit",
        f"提交交换经营快照；修订 {snapshot.revision}；指标 {len(prepared)} 项",
        actor.username,
        commit=False,
        target_type="operating_snapshot",
        target_id=snapshot.id,
        object_scope=ObjectScope(scope_type="building", building_id=building.id),
    )
    log_action(
        db,
        None,
        "snapshot_exchange_receive",
        f"接收模拟经营快照交换包；来源 {binding.source_system_code}；投递 {package.delivery_id}",
        actor.username,
        commit=False,
        target_type="snapshot_exchange_receipt",
        target_id=receipt.id,
        object_scope=ObjectScope(scope_type="building", building_id=building.id),
    )
    db.commit()
    return ReceiveResult(receipt=_serialize_receipt(db, receipt), exact_replay=False)


def _retry_after_contention(
    db: Session,
    *,
    package: SnapshotExchangePackageV1,
    actor: User,
) -> ReceiveResult:
    last_error: Exception | None = None
    for attempt in range(_RECEIVE_MAX_ATTEMPTS - 1):
        time.sleep(min(0.01 * (attempt + 1), 0.05))
        try:
            return _receive_snapshot_package_once(db, package=package, actor=actor)
        except IntegrityError as exc:
            db.rollback()
            last_error = exc
            if "unique" not in str(exc).lower():
                raise
        except OperationalError as exc:
            db.rollback()
            detail = str(exc).lower()
            if "locked" not in detail and "busy" not in detail:
                raise
            raise SnapshotExchangeUnavailable(
                "快照交换接收发生持续并发争用"
            ) from exc
    raise SnapshotExchangeUnavailable("快照交换接收发生持续并发争用") from last_error


def receive_snapshot_package(
    db: Session,
    *,
    package: SnapshotExchangePackageV1,
    actor: User,
) -> ReceiveResult:
    from app.core.presentation_units import require_snapshot_book_currency
    require_snapshot_book_currency(db)
    try:
        return _receive_snapshot_package_once(db, package=package, actor=actor)
    except IntegrityError as exc:
        db.rollback()
        if "unique" not in str(exc).lower():
            raise
        return _retry_after_contention(db, package=package, actor=actor)
    except OperationalError as exc:
        db.rollback()
        detail = str(exc).lower()
        if "locked" not in detail and "busy" not in detail:
            raise
        raise SnapshotExchangeUnavailable(
            "快照交换接收发生持续并发争用"
        ) from exc
    except Exception:
        db.rollback()
        raise
