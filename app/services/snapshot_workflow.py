from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.time import app_timezone, now_in_timezone, today_in_timezone
from app.models import (
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    ImportError,
    MetricDefinition,
    SourceRecord,
    User,
)
from app.ops_v1 import OPS_V1_BY_CODE, SUBMITTABLE_CODES
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.metric_definitions import published_ops_v1_by_code
from app.services.property_scope import SelectedPropertyScope, require_single_selected_building


_SIX_PLACES = Decimal("0.000001")
_MAX_NUMERIC_24_6 = Decimal("999999999999999999.999999")
_MAX_EXCHANGE_DECIMAL = Decimal("999999999.999999")
_ALLOWED_COMPLETENESS = frozenset({"unknown", "partial", "complete"})


def _decimal(value, *, field: str, allow_none: bool = True) -> Decimal | None:
    if value is None and allow_none:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是有效数值") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} 不是有限数值")
    return parsed


def _number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        quantized = value.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("数值超出中心存储精度") from exc
    if abs(quantized) > _MAX_NUMERIC_24_6:
        raise ValueError("数值超出中心 NUMERIC(24,6) 存储范围")
    return float(quantized)


def _canonical_checksum(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _snapshot_month(as_of_date: date) -> tuple[date, date]:
    start = as_of_date.replace(day=1)
    end = as_of_date.replace(day=calendar.monthrange(as_of_date.year, as_of_date.month)[1])
    return start, end


def _assert_month_period(period_start: date, period_end: date, as_of_date: date) -> None:
    expected_start, expected_end = _snapshot_month(as_of_date)
    if period_start != expected_start or period_end != expected_end:
        raise ValueError("OPS_V1 快照期间必须是统计日所在自然月")


def _source_in_building(
    db: Session,
    *,
    source_id: int,
    building_id: int,
    allowed_types: frozenset[str] | None = None,
) -> DataSource:
    source = (
        db.query(DataSource)
        .filter(
            DataSource.id == int(source_id),
            DataSource.building_id == building_id,
            DataSource.is_active.is_(True),
        )
        .one_or_none()
    )
    if source is None or (allowed_types is not None and source.source_type not in allowed_types):
        raise HTTPException(404, "数据来源不存在或不属于当前楼宇")
    return source


def create_snapshot_source(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    actor: User,
    payload: dict,
) -> dict:
    building_id = require_single_selected_building(scope)
    source_type = str(payload.get("source_type") or "").strip()
    if source_type not in {"manual_snapshot", "excel"}:
        raise HTTPException(422, "经营快照来源只能是人工快照或 Excel")
    name = str(payload.get("name") or "").strip()
    cadence = str(payload.get("cadence") or "").strip()
    freshness_rule = str(payload.get("freshness_rule") or "").strip()
    if not name or not cadence or not freshness_rule:
        raise HTTPException(422, "来源名称、更新频率和新鲜度规则均不能为空")
    if (
        db.query(DataSource.id)
        .filter(DataSource.building_id == building_id, DataSource.name == name)
        .first()
        is not None
    ):
        raise HTTPException(409, "当前楼宇已存在同名数据来源")
    source = DataSource(
        building_id=building_id,
        source_type=source_type,
        name=name,
        owner=str(payload.get("owner") or "").strip(),
        cadence=cadence,
        freshness_rule=freshness_rule,
        is_active=True,
    )
    db.add(source)
    db.flush()
    log_action(
        db,
        None,
        "snapshot_source_create",
        f"新增经营快照来源；类型 {source_type}；名称 {name}",
        actor.username,
        commit=False,
        target_type="data_source",
        target_id=source.id,
        object_scope=ObjectScope(scope_type="building", building_id=building_id),
    )
    db.commit()
    db.refresh(source)
    return serialize_source(source)


def list_snapshot_sources(
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> list[dict]:
    building_id = require_single_selected_building(scope)
    rows = (
        db.query(DataSource)
        .filter(
            DataSource.building_id == building_id,
            DataSource.source_type.in_(("manual_snapshot", "excel")),
        )
        .order_by(DataSource.is_active.desc(), DataSource.name.asc(), DataSource.id.asc())
        .all()
    )
    return [serialize_source(row) for row in rows]


def serialize_source(source: DataSource) -> dict:
    return {
        "id": source.id,
        "building_id": source.building_id,
        "source_type": source.source_type,
        "name": source.name,
        "owner": source.owner,
        "cadence": source.cadence,
        "freshness_rule": source.freshness_rule,
        "is_active": bool(source.is_active),
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }


def _normalize_source_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} 不是有效时间")
    if value.tzinfo is not None:
        return value.astimezone(app_timezone()).replace(tzinfo=None)
    return value


def _authoritative_freshness(
    *,
    as_of_date: date,
    source_observed_at: datetime,
    source_updated_at: datetime | None,
) -> str:
    # If the source exposes its own last-update time, it is the conservative
    # business cutoff.  A later extraction/confirmation time must not make old
    # source data look current.  Sources without that field fall back to the
    # actual observation/confirmation time.
    business_time = source_updated_at or source_observed_at
    return "current" if business_time.date() >= as_of_date else "stale"


def _prepare_fact(
    raw: dict,
    definition: MetricDefinition,
    *,
    freshness_status: str,
    require_exact_value: bool = False,
) -> dict:
    code = definition.code
    spec = OPS_V1_BY_CODE[code]
    aggregation = spec["aggregation_rule"]
    numerator = _decimal(raw.get("numerator"), field=f"{code}.numerator", allow_none=False)
    denominator = _decimal(raw.get("denominator"), field=f"{code}.denominator")
    if numerator is None or numerator < 0:
        raise ValueError(f"{code}.numerator 不能为空或为负数")
    if aggregation in {"ratio", "ratio_percent"}:
        if denominator is None or denominator <= 0:
            raise ValueError(f"{code}.denominator 必须大于 0")
        computed = numerator / denominator
        if aggregation == "ratio_percent":
            computed *= Decimal(100)
    else:
        if denominator not in (None, Decimal(0)):
            raise ValueError(f"{code} 是合计指标，不应提供分母")
        denominator = None
        computed = numerator
    supplied_value = _decimal(raw.get("value"), field=f"{code}.value")
    try:
        rounded = computed.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"{code} 数值超出中心存储精度") from exc
    if abs(rounded) > _MAX_NUMERIC_24_6:
        raise ValueError(f"{code} 数值超出中心 NUMERIC(24,6) 存储范围")
    if require_exact_value:
        if abs(rounded) > _MAX_EXCHANGE_DECIMAL:
            raise ValueError(f"{code} 重算值超出 OPS_EXCHANGE_V1 安全数值范围")
        if Decimal(str(float(rounded))) != rounded:
            raise ValueError(f"{code} 重算值不能由中心存储精确保留")
    if supplied_value is not None:
        mismatched = (
            supplied_value != rounded
            if require_exact_value
            else abs(supplied_value - rounded) > _SIX_PLACES
        )
        if mismatched:
            raise ValueError(f"{code}.value 与分子分母计算结果不一致")

    covered_area = _decimal(raw.get("covered_area_sqm"), field=f"{code}.covered_area_sqm")
    eligible_area = _decimal(raw.get("eligible_area_sqm"), field=f"{code}.eligible_area_sqm")
    if covered_area is not None and covered_area < 0:
        raise ValueError(f"{code}.covered_area_sqm 不能为负数")
    if eligible_area is not None and eligible_area < 0:
        raise ValueError(f"{code}.eligible_area_sqm 不能为负数")
    if covered_area is not None and eligible_area is not None and covered_area > eligible_area:
        raise ValueError(f"{code} 的已覆盖面积不能大于具备数据面积")

    covered_days = int(raw.get("covered_days") or 0)
    eligible_days = int(raw.get("eligible_days") or 0)
    if covered_days < 0 or eligible_days < 0 or covered_days > eligible_days:
        raise ValueError(f"{code} 的时间覆盖不合法")
    estimate_share = _decimal(raw.get("estimate_share"), field=f"{code}.estimate_share")
    estimate_method = str(raw.get("estimate_method") or "").strip()
    if estimate_share is not None and not (Decimal(0) <= estimate_share <= Decimal(1)):
        raise ValueError(f"{code}.estimate_share 必须在 0 到 1 之间")
    if estimate_share is not None and estimate_share > 0 and not estimate_method:
        raise ValueError(f"{code} 含估算时必须说明估算方法")
    if estimate_method and estimate_share in (None, Decimal(0)):
        raise ValueError(f"{code} 记录估算方法时必须同时记录估算比例")

    completeness = str(raw.get("completeness_status") or "unknown").strip()
    if completeness not in _ALLOWED_COMPLETENESS:
        raise ValueError(f"{code}.completeness_status 不合法")
    calculation_version = str(raw.get("calculation_version") or "").strip()
    if not calculation_version:
        raise ValueError(f"{code}.calculation_version 不能为空")
    return {
        "metric_definition_id": definition.id,
        "value": _number(rounded),
        "numerator": _number(numerator),
        "denominator": _number(denominator),
        "eligible_buildings": 1,
        "included_buildings": int(
            freshness_status == "current"
            and completeness in {"partial", "complete"}
            and (
                (
                    covered_area is not None
                    and eligible_area is not None
                    and eligible_area > 0
                    and covered_area > 0
                )
                or (eligible_days > 0 and covered_days > 0)
            )
        ),
        "covered_area_sqm": _number(covered_area),
        "eligible_area_sqm": _number(eligible_area),
        "covered_days": covered_days,
        "eligible_days": eligible_days,
        "estimate_share": _number(estimate_share),
        "estimate_method": estimate_method,
        "data_depth": "snapshot",
        "completeness_status": completeness,
        "freshness_status": freshness_status,
        "validation_status": "valid",
        "publication_status": "draft",
        "calculation_version": calculation_version,
    }


def submit_operating_snapshot(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    actor: User,
    payload: dict,
) -> dict:
    building_id = require_single_selected_building(scope)
    source = _source_in_building(
        db,
        source_id=int(payload["source_id"]),
        building_id=building_id,
        allowed_types=frozenset({"manual_snapshot", "excel"}),
    )
    as_of_date = payload["as_of_date"]
    period_start = payload["period_start"]
    period_end = payload["period_end"]
    raw_facts = list(payload.get("facts") or [])
    source_time_issues: list[tuple[int | None, str | None, str, str]] = []
    source_observed_at: datetime | None = None
    source_updated_at: datetime | None = None
    try:
        source_observed_at = _normalize_source_timestamp(
            payload.get("source_observed_at"),
            field="source_observed_at",
        )
    except ValueError as exc:
        source_time_issues.append(
            (None, "source_observed_at", "invalid_source_observed_at", str(exc))
        )
    if payload.get("source_updated_at") is not None:
        try:
            source_updated_at = _normalize_source_timestamp(
                payload.get("source_updated_at"),
                field="source_updated_at",
            )
        except ValueError as exc:
            source_time_issues.append(
                (None, "source_updated_at", "invalid_source_updated_at", str(exc))
            )
    source_now = now_in_timezone()
    if source_observed_at is not None and source_observed_at > source_now:
        source_time_issues.append(
            (
                None,
                "source_observed_at",
                "future_source_observed_at",
                "来源观测时间不能晚于当前时间",
            )
        )
    if source_updated_at is not None and source_updated_at > source_now:
        source_time_issues.append(
            (
                None,
                "source_updated_at",
                "future_source_updated_at",
                "来源更新时间不能晚于当前时间",
            )
        )
    if (
        source_observed_at is not None
        and source_updated_at is not None
        and source_updated_at > source_observed_at
    ):
        source_time_issues.append(
            (
                None,
                "source_updated_at",
                "source_updated_after_observed",
                "来源更新时间不能晚于来源观测时间",
            )
        )
    authoritative_freshness = (
        _authoritative_freshness(
            as_of_date=as_of_date,
            source_observed_at=source_observed_at,
            source_updated_at=source_updated_at,
        )
        if source_observed_at is not None and not source_time_issues
        else "stale"
    )
    source_file_name = str(payload.get("source_file_name") or "").strip() or None
    supplied_checksum = str(payload.get("file_checksum") or "").strip().lower() or None
    if source.source_type == "excel" and (source_file_name is None or supplied_checksum is None):
        raise HTTPException(422, "Excel 快照必须记录源文件名和文件校验值")
    checksum = supplied_checksum or _canonical_checksum(payload)
    external_batch_no = str(payload.get("external_batch_no") or "").strip() or None
    duplicate = (
        db.query(ImportBatch.id)
        .filter(
            ImportBatch.source_id == source.id,
            (ImportBatch.file_checksum == checksum)
            | (
                (ImportBatch.external_batch_no == external_batch_no)
                if external_batch_no is not None
                else False
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(409, "该来源文件或外部批次已经提交")

    batch = ImportBatch(
        source_id=source.id,
        source_file_name=source_file_name,
        external_batch_no=external_batch_no,
        period=period_start.strftime("%Y-%m"),
        as_of_date=as_of_date,
        source_observed_at=source_observed_at,
        source_updated_at=source_updated_at,
        received_at=now_in_timezone(),
        started_at=now_in_timezone(),
        file_checksum=checksum,
        parser_version=str(payload.get("parser_version") or "ops-v1-normalized-v1").strip(),
        actor_id=actor.id,
        row_count=len(raw_facts),
        accepted_count=0,
        rejected_count=0,
        status="validating",
        error_summary="",
        correction_reason=str(payload.get("correction_reason") or "").strip() or None,
    )
    db.add(batch)
    db.flush()

    issues: list[tuple[int | None, str | None, str, str]] = list(source_time_issues)
    if as_of_date > today_in_timezone():
        issues.append((None, "as_of_date", "future_as_of_date", "统计日不能晚于今天"))
    try:
        _assert_month_period(period_start, period_end, as_of_date)
    except ValueError as exc:
        issues.append((None, "period", "invalid_period", str(exc)))
    if not raw_facts:
        issues.append((None, "facts", "empty_snapshot", "经营快照至少需要一个指标事实"))

    definitions = published_ops_v1_by_code(db)
    seen_codes: set[str] = set()
    prepared: list[tuple[SourceRecord, dict]] = []
    for row_no, raw in enumerate(raw_facts, start=1):
        record = SourceRecord(
            batch_id=batch.id,
            row_no=row_no,
            record_checksum=_canonical_checksum(raw),
            status="pending",
            source_ref=f"row:{row_no}",
        )
        db.add(record)
        code = str(raw.get("code") or "").strip()
        row_issues: list[tuple[str | None, str, str]] = []
        if code in seen_codes:
            row_issues.append(("code", "duplicate_metric", "同一快照不能重复提交同一指标"))
        seen_codes.add(code)
        if code not in SUBMITTABLE_CODES or code not in definitions:
            row_issues.append(("code", "unknown_metric", "指标不属于已发布的 OPS_V1 可提交集合"))
        fact_values = None
        if not row_issues:
            try:
                fact_values = _prepare_fact(
                    raw,
                    definitions[code],
                    freshness_status=authoritative_freshness,
                )
            except (ValueError, TypeError) as exc:
                row_issues.append((None, "invalid_metric_fact", str(exc)))
        if row_issues:
            record.status = "rejected"
            for field_name, error_code, message in row_issues:
                issues.append((row_no, field_name, error_code, message))
        else:
            record.status = "accepted"
            prepared.append((record, fact_values or {}))

    if issues:
        for row_no, field_name, error_code, message in issues:
            db.add(
                ImportError(
                    batch_id=batch.id,
                    row_no=row_no,
                    field_name=field_name,
                    error_code=error_code,
                    severity="blocking",
                    message=message,
                )
            )
        batch.accepted_count = len(prepared)
        batch.rejected_count = max(len(raw_facts) - len(prepared), 0)
        batch.status = "rejected"
        batch.error_summary = f"{len(issues)} 个阻断问题，整批未进入正式快照"
        batch.finished_at = now_in_timezone()
        db.commit()
        raise HTTPException(
            422,
            {
                "message": "快照校验失败，整批未写入正式指标",
                "batch_id": batch.id,
                "error_count": len(issues),
            },
        )

    current = (
        db.query(BuildingOperatingSnapshot)
        .filter(
            BuildingOperatingSnapshot.building_id == building_id,
            BuildingOperatingSnapshot.period_start == period_start,
            BuildingOperatingSnapshot.period_end == period_end,
            BuildingOperatingSnapshot.as_of_date == as_of_date,
            BuildingOperatingSnapshot.publication_status == "published",
            BuildingOperatingSnapshot.is_current.is_(True),
        )
        .one_or_none()
    )
    correction_reason = str(payload.get("correction_reason") or "").strip()
    if current is not None and not correction_reason:
        batch.status = "rejected"
        batch.rejected_count = len(raw_facts)
        batch.error_summary = "已有发布版，更正必须说明原因"
        batch.finished_at = now_in_timezone()
        for record, _values in prepared:
            record.status = "rejected"
        db.add(
            ImportError(
                batch_id=batch.id,
                row_no=None,
                field_name="correction_reason",
                error_code="correction_reason_required",
                severity="blocking",
                message="已有发布版，更正必须说明原因",
            )
        )
        db.commit()
        raise HTTPException(422, {"message": batch.error_summary, "batch_id": batch.id})

    max_revision = (
        db.query(BuildingOperatingSnapshot.revision)
        .filter(
            BuildingOperatingSnapshot.building_id == building_id,
            BuildingOperatingSnapshot.period_start == period_start,
            BuildingOperatingSnapshot.period_end == period_end,
            BuildingOperatingSnapshot.as_of_date == as_of_date,
        )
        .order_by(BuildingOperatingSnapshot.revision.desc())
        .first()
    )
    submitted_at = now_in_timezone()
    snapshot = BuildingOperatingSnapshot(
        building_id=building_id,
        period_start=period_start,
        period_end=period_end,
        as_of_date=as_of_date,
        revision=(int(max_revision[0]) + 1) if max_revision else 1,
        validation_status="valid",
        publication_status="draft",
        is_current=False,
        submitted_by=actor.id,
        submitted_at=submitted_at,
        supersedes_id=current.id if current is not None else None,
        created_at=submitted_at,
        updated_at=submitted_at,
    )
    db.add(snapshot)
    db.flush()
    if current is not None:
        old_batch_ids = {
            int(batch_id)
            for (batch_id,) in (
                db.query(BuildingMetricFact.batch_id)
                .filter(
                    BuildingMetricFact.snapshot_id == current.id,
                    BuildingMetricFact.batch_id.is_not(None),
                )
                .distinct()
                .all()
            )
        }
        if len(old_batch_ids) == 1:
            batch.supersedes_id = next(iter(old_batch_ids))
            batch.correction_reason = correction_reason

    for record, values in prepared:
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
    batch.rejected_count = 0
    batch.status = "received"
    batch.error_summary = ""
    batch.ingested_at = now_in_timezone()
    batch.finished_at = batch.ingested_at
    log_action(
        db,
        None,
        "snapshot_submit",
        f"提交经营快照；批次 {batch.id}；修订 {snapshot.revision}；指标 {len(prepared)} 项",
        actor.username,
        commit=False,
        target_type="operating_snapshot",
        target_id=snapshot.id,
        object_scope=ObjectScope(scope_type="building", building_id=building_id),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "快照修订或来源批次发生并发冲突，请刷新后重试") from exc
    return get_snapshot_submission(db, snapshot.id, scope=scope)


def _snapshot_in_scope(
    db: Session,
    snapshot_id: int,
    *,
    scope: SelectedPropertyScope,
) -> BuildingOperatingSnapshot:
    snapshot = (
        db.query(BuildingOperatingSnapshot)
        .filter(
            BuildingOperatingSnapshot.id == int(snapshot_id),
            BuildingOperatingSnapshot.building_id.in_(tuple(scope.building_ids)),
        )
        .one_or_none()
    )
    if snapshot is None:
        raise HTTPException(404, "经营快照不存在或无权访问")
    return snapshot


def get_snapshot_submission(
    db: Session,
    snapshot_id: int,
    *,
    scope: SelectedPropertyScope,
    include_exchange_evidence: bool = False,
) -> dict:
    snapshot = _snapshot_in_scope(db, snapshot_id, scope=scope)
    facts = (
        db.query(BuildingMetricFact, MetricDefinition, DataSource, ImportBatch)
        .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
        .join(DataSource, DataSource.id == BuildingMetricFact.source_id)
        .outerjoin(ImportBatch, ImportBatch.id == BuildingMetricFact.batch_id)
        .filter(BuildingMetricFact.snapshot_id == snapshot.id)
        .order_by(MetricDefinition.code.asc())
        .all()
    )
    result = {
        "id": snapshot.id,
        "building_id": snapshot.building_id,
        "period_start": snapshot.period_start.isoformat(),
        "period_end": snapshot.period_end.isoformat(),
        "as_of_date": snapshot.as_of_date.isoformat(),
        "definition_version": "OPS_V1",
        "revision": snapshot.revision,
        "validation_status": snapshot.validation_status,
        "publication_status": snapshot.publication_status,
        "is_current": bool(snapshot.is_current),
        "submitted_by": snapshot.submitted_by,
        "submitted_at": snapshot.submitted_at.isoformat() if snapshot.submitted_at else None,
        "reviewed_by": snapshot.reviewed_by,
        "reviewed_at": snapshot.reviewed_at.isoformat() if snapshot.reviewed_at else None,
        "published_by": snapshot.published_by,
        "published_at": snapshot.published_at.isoformat() if snapshot.published_at else None,
        "supersedes_id": snapshot.supersedes_id,
        "facts": [
            {
                "id": fact.id,
                "code": definition.code,
                "name": definition.name,
                "unit": definition.unit,
                "value": fact.value,
                "numerator": fact.numerator,
                "denominator": fact.denominator,
                "source": serialize_source(source),
                "batch_id": fact.batch_id,
                "source_record_id": fact.source_record_id,
                "covered_area_sqm": fact.covered_area_sqm,
                "eligible_area_sqm": fact.eligible_area_sqm,
                "covered_days": fact.covered_days,
                "eligible_days": fact.eligible_days,
                "estimate_share": fact.estimate_share,
                "estimate_method": fact.estimate_method,
                "data_depth": fact.data_depth,
                "completeness_status": fact.completeness_status,
                "freshness_status": fact.freshness_status,
                "validation_status": fact.validation_status,
                "publication_status": fact.publication_status,
                "calculation_version": fact.calculation_version,
                "batch": (
                    {
                        "id": batch.id,
                        "status": batch.status,
                        "source_file_name": batch.source_file_name,
                        "file_checksum": batch.file_checksum,
                        "parser_version": batch.parser_version,
                    }
                    if batch is not None
                    else None
                ),
            }
            for fact, definition, source, batch in facts
        ],
    }
    if include_exchange_evidence:
        from app.services.snapshot_exchange import get_snapshot_exchange_review_evidence

        exchange_evidence = get_snapshot_exchange_review_evidence(
            db,
            snapshot_id=snapshot.id,
        )
        if exchange_evidence is not None:
            result["exchange_evidence"] = exchange_evidence
    return result


def review_operating_snapshot(
    db: Session,
    snapshot_id: int,
    *,
    scope: SelectedPropertyScope,
    actor: User,
    approved: bool,
    notes: str,
    include_exchange_evidence: bool = False,
) -> dict:
    snapshot = _snapshot_in_scope(db, snapshot_id, scope=scope)
    require_single_selected_building(scope)
    if snapshot.publication_status != "draft" or snapshot.validation_status != "valid":
        raise HTTPException(409, "只有校验通过且尚未复核的快照可以复核")
    from app.services.snapshot_exchange import (
        assert_snapshot_exchange_candidate_latest,
        snapshot_exchange_candidate_latest_clause,
    )

    assert_snapshot_exchange_candidate_latest(db, snapshot_id=snapshot.id)
    if snapshot.submitted_by == actor.id:
        raise HTTPException(409, "提交人不能复核自己的快照")
    normalized_notes = str(notes or "").strip()
    if not approved and not normalized_notes:
        raise HTTPException(422, "退回快照必须填写复核意见")
    facts = db.query(BuildingMetricFact).filter(BuildingMetricFact.snapshot_id == snapshot.id).all()
    if not facts or any(item.validation_status != "valid" for item in facts):
        raise HTTPException(409, "快照指标尚未全部通过校验")
    batch_ids = {item.batch_id for item in facts if item.batch_id is not None}
    batches = db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).all() if batch_ids else []
    reviewed_at = now_in_timezone()
    action = "snapshot_review"
    cas_values: dict = {
        BuildingOperatingSnapshot.reviewed_by: actor.id,
        BuildingOperatingSnapshot.reviewed_at: reviewed_at,
        BuildingOperatingSnapshot.updated_at: reviewed_at,
    }
    if approved:
        cas_values[BuildingOperatingSnapshot.publication_status] = "reviewed"
    else:
        cas_values[BuildingOperatingSnapshot.validation_status] = "rejected"
        action = "snapshot_reject"
    try:
        claimed = (
            db.query(BuildingOperatingSnapshot)
            .filter(
                BuildingOperatingSnapshot.id == snapshot.id,
                BuildingOperatingSnapshot.building_id == snapshot.building_id,
                BuildingOperatingSnapshot.publication_status == "draft",
                BuildingOperatingSnapshot.validation_status == "valid",
                BuildingOperatingSnapshot.reviewed_by.is_(None),
                BuildingOperatingSnapshot.reviewed_at.is_(None),
                snapshot_exchange_candidate_latest_clause(
                    BuildingOperatingSnapshot.id
                ),
            )
            .update(cas_values, synchronize_session=False)
        )
        if claimed != 1:
            db.rollback()
            raise HTTPException(409, "该快照已被其他复核人处理，请刷新后重试")
        if approved:
            for batch in batches:
                batch.reviewed_by = actor.id
                batch.reviewed_at = reviewed_at
                batch.status = "reviewed"
        else:
            for fact in facts:
                fact.validation_status = "rejected"
            for batch in batches:
                batch.reviewed_by = actor.id
                batch.reviewed_at = reviewed_at
                batch.status = "rejected"
                batch.error_summary = normalized_notes
        log_action(
            db,
            None,
            action,
            normalized_notes or "复核通过",
            actor.username,
            commit=False,
            target_type="operating_snapshot",
            target_id=snapshot.id,
            object_scope=ObjectScope(scope_type="building", building_id=snapshot.building_id),
        )
        db.commit()
    except HTTPException:
        if db.in_transaction():
            db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "复核期间出现并发冲突，未产生半复核状态，请刷新后重试") from exc
    except OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(
                409,
                "复核期间出现并发冲突，未产生半复核状态，请刷新后重试",
            ) from exc
        raise
    return get_snapshot_submission(
        db,
        snapshot.id,
        scope=scope,
        include_exchange_evidence=include_exchange_evidence,
    )


def publish_operating_snapshot(
    db: Session,
    snapshot_id: int,
    *,
    scope: SelectedPropertyScope,
    actor: User,
    notes: str,
) -> dict:
    snapshot = _snapshot_in_scope(db, snapshot_id, scope=scope)
    require_single_selected_building(scope)
    if snapshot.publication_status != "reviewed" or snapshot.validation_status != "valid":
        raise HTTPException(409, "只有复核通过的快照可以发布")
    from app.services.snapshot_exchange import (
        assert_snapshot_exchange_candidate_latest,
        snapshot_exchange_candidate_latest_clause,
    )

    assert_snapshot_exchange_candidate_latest(db, snapshot_id=snapshot.id)
    if actor.id in {snapshot.submitted_by, snapshot.reviewed_by}:
        raise HTTPException(409, "发布人必须与提交人、复核人分别独立")
    facts = db.query(BuildingMetricFact).filter(BuildingMetricFact.snapshot_id == snapshot.id).all()
    if not facts or any(
        fact.validation_status != "valid" or fact.publication_status != "draft"
        for fact in facts
    ):
        raise HTTPException(409, "快照指标状态不允许发布")
    definitions = published_ops_v1_by_code(db)
    valid_definition_ids = {item.id for item in definitions.values()}
    if any(fact.metric_definition_id not in valid_definition_ids for fact in facts):
        raise HTTPException(409, "快照包含未发布或已变更的指标定义")
    for fact in facts:
        source = _source_in_building(
            db,
            source_id=fact.source_id,
            building_id=snapshot.building_id,
        )
        if source.source_type in {"manual_snapshot", "excel"} and (
            fact.batch_id is None or fact.source_record_id is None
        ):
            raise HTTPException(409, "人工或 Excel 指标缺少批次或来源行追溯")
        if fact.batch_id is not None:
            batch = db.get(ImportBatch, fact.batch_id)
            record = db.get(SourceRecord, fact.source_record_id) if fact.source_record_id else None
            if (
                batch is None
                or batch.source_id != source.id
                or batch.as_of_date != snapshot.as_of_date
                or batch.status != "reviewed"
                or record is None
                or record.batch_id != batch.id
                or record.status != "accepted"
            ):
                raise HTTPException(409, "指标来源链不完整或与快照统计日不一致")

    try:
        # This no-op conditional UPDATE is the serialization point.  On SQLite
        # it obtains the write lock while rechecking the accepted source head,
        # so a correction cannot commit between the gate and the publish write.
        claimed = (
            db.query(BuildingOperatingSnapshot)
            .filter(
                BuildingOperatingSnapshot.id == snapshot.id,
                BuildingOperatingSnapshot.building_id == snapshot.building_id,
                BuildingOperatingSnapshot.publication_status == "reviewed",
                BuildingOperatingSnapshot.validation_status == "valid",
                snapshot_exchange_candidate_latest_clause(
                    BuildingOperatingSnapshot.id
                ),
            )
            .update(
                {
                    BuildingOperatingSnapshot.updated_at:
                        BuildingOperatingSnapshot.updated_at,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            raise HTTPException(409, "该候选已被更新的来源修订替代，不能继续发布")

        current = (
            db.query(BuildingOperatingSnapshot)
            .filter(
                BuildingOperatingSnapshot.building_id == snapshot.building_id,
                BuildingOperatingSnapshot.period_start == snapshot.period_start,
                BuildingOperatingSnapshot.period_end == snapshot.period_end,
                BuildingOperatingSnapshot.as_of_date == snapshot.as_of_date,
                BuildingOperatingSnapshot.publication_status == "published",
                BuildingOperatingSnapshot.is_current.is_(True),
                BuildingOperatingSnapshot.id != snapshot.id,
            )
            .one_or_none()
        )
        if current is not None and snapshot.supersedes_id != current.id:
            raise HTTPException(409, "待发布修订不是当前发布版的直接后继，请重新提交")
        if current is None and snapshot.supersedes_id is not None:
            raise HTTPException(409, "被更正的当前发布版已发生变化，请重新提交")

        published_at = now_in_timezone()
        if current is not None:
            current.publication_status = "superseded"
            current.is_current = False
            current.updated_at = published_at
            old_facts = (
                db.query(BuildingMetricFact)
                .filter(BuildingMetricFact.snapshot_id == current.id)
                .all()
            )
            old_batch_ids: set[int] = set()
            for fact in old_facts:
                fact.publication_status = "superseded"
                if fact.batch_id is not None:
                    old_batch_ids.add(int(fact.batch_id))
            if old_batch_ids:
                db.query(ImportBatch).filter(ImportBatch.id.in_(old_batch_ids)).update(
                    {ImportBatch.status: "superseded"},
                    synchronize_session=False,
                )

        snapshot.publication_status = "published"
        snapshot.is_current = True
        snapshot.published_by = actor.id
        snapshot.published_at = published_at
        snapshot.updated_at = published_at
        batch_ids: set[int] = set()
        for fact in facts:
            fact.publication_status = "published"
            if fact.batch_id is not None:
                batch_ids.add(int(fact.batch_id))
        if batch_ids:
            db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).update(
                {ImportBatch.status: "published", ImportBatch.published_at: published_at},
                synchronize_session=False,
            )
        log_action(
            db,
            None,
            "snapshot_publish",
            str(notes or "").strip() or f"发布经营快照修订 {snapshot.revision}",
            actor.username,
            commit=False,
            target_type="operating_snapshot",
            target_id=snapshot.id,
            object_scope=ObjectScope(scope_type="building", building_id=snapshot.building_id),
        )
        db.commit()
    except HTTPException:
        if db.in_transaction():
            db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "发布期间出现并发修订，未产生半发布状态，请刷新后重试") from exc
    except OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(
                409,
                "发布期间出现并发冲突，未产生半发布状态，请刷新后重试",
            ) from exc
        raise
    return get_snapshot_submission(db, snapshot.id, scope=scope)
