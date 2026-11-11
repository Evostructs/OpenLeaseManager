from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    OperationLog,
    SourceRecord,
    User,
)
from app.ops_v1 import OPS_V1_DEFINITIONS, SUBMITTABLE_CODES
from app.services.building_operating_gate import (
    record_building_operating_gate,
    resolve_building_operating_gates,
)
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.metric_definitions import ensure_ops_v1_definitions
from app.services.property_scope import resolve_user_scope


DEMO_MARKER = "DEMO-SIMULATED"
DEMO_CALCULATION_VERSION = f"{DEMO_MARKER}-OPS-V1-v1"
DEMO_BUILDING_CODES = ("DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E")
DEMO_METRIC_CODES = tuple(
    item["code"] for item in OPS_V1_DEFINITIONS if item["code"] in SUBMITTABLE_CODES
)

_SIX_PLACES = Decimal("0.000001")
_DEMO_SOURCE_OWNER = f"{DEMO_MARKER} 模拟数据生成器"
_DEMO_FRESHNESS_RULE = f"{DEMO_MARKER} 模拟数据；仅供本地演示与开闸验收"
_DEMO_BATCH_NOTE = f"{DEMO_MARKER} 模拟数据；非正式经营数据"


class PortfolioOperatingDemoConflict(RuntimeError):
    """Raised when safe, append-only demo generation cannot proceed."""


@dataclass(frozen=True)
class _DemoProfile:
    rentable_sqm: Decimal
    leased_sqm: Decimal
    effective_daily_rent: Decimal
    receivable_month: Decimal
    received_month: Decimal
    arrears_balance: Decimal
    expiry_contract_count_90d: Decimal
    expiry_area_90d: Decimal
    expiry_area_180d: Decimal
    expiry_area_365d: Decimal
    expiry_rent_12m: Decimal
    risk_open_items: Decimal


@dataclass(frozen=True)
class _ExistingArtifact:
    snapshot_id: int
    source_id: int
    batch_id: int


@dataclass(frozen=True)
class _Inspection:
    marker_detected: bool
    artifact: _ExistingArtifact | None
    issue: str | None


@dataclass(frozen=True)
class _Plan:
    building: Building
    source: DataSource | None
    status: str
    artifact: _ExistingArtifact | None


def _d(value: str | int) -> Decimal:
    return Decimal(str(value))


_PROFILES = {
    "DEMO_B": _DemoProfile(
        rentable_sqm=_d(52_000),
        leased_sqm=_d(49_400),
        effective_daily_rent=_d("5.80"),
        receivable_month=_d(8_600_000),
        received_month=_d(8_256_000),
        arrears_balance=_d(120_000),
        expiry_contract_count_90d=_d(3),
        expiry_area_90d=_d(1_200),
        expiry_area_180d=_d(2_600),
        expiry_area_365d=_d(6_200),
        expiry_rent_12m=_d(18_000_000),
        risk_open_items=_d(2),
    ),
    "DEMO_C": _DemoProfile(
        rentable_sqm=_d(48_000),
        leased_sqm=_d(45_600),
        effective_daily_rent=_d("5.35"),
        receivable_month=_d(7_400_000),
        received_month=_d(7_178_000),
        arrears_balance=_d(80_000),
        expiry_contract_count_90d=_d(2),
        expiry_area_90d=_d(900),
        expiry_area_180d=_d(2_100),
        expiry_area_365d=_d(5_400),
        expiry_rent_12m=_d(15_000_000),
        risk_open_items=_d(1),
    ),
    "DEMO_D": _DemoProfile(
        rentable_sqm=_d(36_000),
        leased_sqm=_d(34_200),
        effective_daily_rent=_d("4.95"),
        receivable_month=_d(5_200_000),
        received_month=_d(5_044_000),
        arrears_balance=_d(60_000),
        expiry_contract_count_90d=_d(2),
        expiry_area_90d=_d(700),
        expiry_area_180d=_d(1_600),
        expiry_area_365d=_d(4_100),
        expiry_rent_12m=_d(11_000_000),
        risk_open_items=_d(1),
    ),
    "DEMO_E": _DemoProfile(
        rentable_sqm=_d(30_000),
        leased_sqm=_d(27_000),
        effective_daily_rent=_d("6.20"),
        receivable_month=_d(6_100_000),
        received_month=_d(5_795_000),
        arrears_balance=_d(150_000),
        expiry_contract_count_90d=_d(4),
        expiry_area_90d=_d(1_000),
        expiry_area_180d=_d(2_500),
        expiry_area_365d=_d(6_500),
        expiry_rent_12m=_d(20_000_000),
        risk_open_items=_d(3),
    ),
}


def _q6(value: Decimal) -> Decimal:
    return value.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(_q6(value))


def _decimal_equal(actual: object, expected: Decimal | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    try:
        return _q6(Decimal(str(actual))) == _q6(expected)
    except Exception:
        return False


def _canonical_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, (date, datetime)) else str(value),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _period(as_of_date: date) -> tuple[date, date]:
    return (
        as_of_date.replace(day=1),
        as_of_date.replace(day=calendar.monthrange(as_of_date.year, as_of_date.month)[1]),
    )


def _source_name(building_code: str) -> str:
    return f"{DEMO_MARKER} 模拟经营快照（{building_code}）"


def _batch_number(building_code: str, as_of_date: date) -> str:
    return f"{DEMO_MARKER}-OPS-V1-{as_of_date:%Y%m%d}-{building_code}"


def _source_file_name(building_code: str, as_of_date: date) -> str:
    return f"{DEMO_MARKER}-{building_code}-{as_of_date:%Y-%m}.json"


def _external_record_id(metric_code: str) -> str:
    return f"{DEMO_MARKER}:{metric_code}"


def _source_ref(building_code: str, as_of_date: date, metric_code: str) -> str:
    return f"{DEMO_MARKER}:模拟:{building_code}:{as_of_date.isoformat()}:{metric_code}"


def _metric_rows(building_code: str, as_of_date: date) -> dict[str, dict[str, Decimal | None | int]]:
    profile = _PROFILES[building_code]
    covered_days = as_of_date.day

    def summed(value: Decimal) -> dict[str, Decimal | None | int]:
        return {
            "value": _q6(value),
            "numerator": _q6(value),
            "denominator": None,
            "covered_area_sqm": _q6(profile.rentable_sqm),
            "eligible_area_sqm": _q6(profile.rentable_sqm),
            "covered_days": covered_days,
            "eligible_days": covered_days,
        }

    def ratio(
        numerator: Decimal,
        denominator: Decimal,
        *,
        percent: bool,
    ) -> dict[str, Decimal | None | int]:
        value = numerator / denominator
        if percent:
            value *= _d(100)
        return {
            "value": _q6(value),
            "numerator": _q6(numerator),
            "denominator": _q6(denominator),
            "covered_area_sqm": _q6(profile.rentable_sqm),
            "eligible_area_sqm": _q6(profile.rentable_sqm),
            "covered_days": covered_days,
            "eligible_days": covered_days,
        }

    rentable_area_days = profile.leased_sqm * _d(covered_days)
    rows = {
        "area.rentable_sqm": summed(profile.rentable_sqm),
        "area.leased_sqm": summed(profile.leased_sqm),
        "occupancy.area_rate": ratio(
            profile.leased_sqm,
            profile.rentable_sqm,
            percent=True,
        ),
        "vacancy.area_sqm": summed(profile.rentable_sqm - profile.leased_sqm),
        "rent.effective_daily": ratio(
            profile.effective_daily_rent * rentable_area_days,
            rentable_area_days,
            percent=False,
        ),
        "billing.receivable_month": summed(profile.receivable_month),
        "collection.received_month": summed(profile.received_month),
        "collection.rate_month": ratio(
            profile.received_month,
            profile.receivable_month,
            percent=True,
        ),
        "arrears.balance": summed(profile.arrears_balance),
        "expiry.contract_count_90d": summed(profile.expiry_contract_count_90d),
        "expiry.area_pressure_90d": ratio(
            profile.expiry_area_90d,
            profile.leased_sqm,
            percent=True,
        ),
        "expiry.area_pressure_180d": ratio(
            profile.expiry_area_180d,
            profile.leased_sqm,
            percent=True,
        ),
        "expiry.area_pressure_365d": ratio(
            profile.expiry_area_365d,
            profile.leased_sqm,
            percent=True,
        ),
        "expiry.rent_12m": summed(profile.expiry_rent_12m),
        "risk.open_items": summed(profile.risk_open_items),
    }
    if set(rows) != set(SUBMITTABLE_CODES):
        raise RuntimeError("DEMO 模拟指标集合与 OPS_V1 可提交集合不一致")
    return rows


def _file_checksum(building_code: str, as_of_date: date) -> str:
    rows = _metric_rows(building_code, as_of_date)
    return _canonical_hash(
        {
            "marker": DEMO_MARKER,
            "data_classification": "simulated",
            "building_code": building_code,
            "as_of_date": as_of_date,
            "calculation_version": DEMO_CALCULATION_VERSION,
            "facts": [
                {
                    "code": code,
                    "value": rows[code]["value"],
                    "numerator": rows[code]["numerator"],
                    "denominator": rows[code]["denominator"],
                }
                for code in DEMO_METRIC_CODES
            ],
        }
    )


def _record_checksum(
    building_code: str,
    as_of_date: date,
    metric_code: str,
    row: dict[str, Decimal | None | int],
) -> str:
    return _canonical_hash(
        {
            "marker": DEMO_MARKER,
            "data_classification": "simulated",
            "building_code": building_code,
            "as_of_date": as_of_date,
            "metric_code": metric_code,
            **row,
        }
    )


def _validate_request_date(as_of_date: date, *, simulation_today: date | None = None) -> None:
    if not isinstance(as_of_date, date):
        raise ValueError("as_of_date 必须是日期")
    # Explicit reference date affects only labelled DEMO generation.
    today = simulation_today or today_in_timezone()
    if as_of_date > today:
        raise ValueError("DEMO 模拟经营快照统计日不能晚于今天")
    if (as_of_date.year, as_of_date.month) != (today.year, today.month):
        raise ValueError("DEMO 模拟经营快照只能生成当前月份数据")


def _load_actors(
    db: Session,
    *,
    submitter_id: int,
    reviewer_id: int,
    publisher_id: int,
) -> tuple[User, User, User]:
    ids = (int(submitter_id), int(reviewer_id), int(publisher_id))
    if len(set(ids)) != 3:
        raise ValueError("模拟快照的提交人、复核人、发布人必须是三名不同用户")
    actors = (
        db.query(User)
        .filter(User.id.in_(ids), User.is_active.is_(True))
        .all()
    )
    by_id = {int(actor.id): actor for actor in actors}
    if set(by_id) != set(ids):
        raise ValueError("模拟快照的三名责任人必须均已存在且处于启用状态")
    return by_id[ids[0]], by_id[ids[1]], by_id[ids[2]]


def _validate_actor_capabilities(
    db: Session,
    *,
    submitter: User,
    reviewer: User,
    publisher: User,
    buildings: tuple[Building, ...],
    open_gates: bool,
) -> None:
    requirements = (
        (submitter, "提交人", {"data_import_submit"}),
        (reviewer, "复核人", {"snapshot_review"}),
        (
            publisher,
            "发布人",
            {"snapshot_publish"} | ({"platform_admin"} if open_gates else set()),
        ),
    )
    required_building_ids = {int(building.id) for building in buildings}
    building_codes_by_id = {int(building.id): str(building.code) for building in buildings}
    for actor, label, required in requirements:
        if str(actor.role or "") not in {"admin", "editor"}:
            raise ValueError(f"模拟快照{label} {actor.username} 必须是管理员或编辑角色")
        resolved = resolve_user_scope(db, actor)
        effective = set(resolved.effective_capabilities)
        missing = sorted(required - effective)
        if missing:
            raise ValueError(
                f"模拟快照{label} {actor.username} 缺少权限：{','.join(missing)}"
            )
        missing_edit_ids = required_building_ids - set(resolved.edit_building_ids)
        if missing_edit_ids:
            missing_codes = ",".join(
                building_codes_by_id[building_id]
                for building_id in sorted(missing_edit_ids)
            )
            raise ValueError(
                f"模拟快照{label} {actor.username} 缺少目标楼宇编辑授权：{missing_codes}"
            )


def _load_buildings(db: Session) -> tuple[Building, ...]:
    rows = (
        db.query(Building)
        .filter(
            Building.code.in_(DEMO_BUILDING_CODES),
            Building.is_active.is_(True),
            Building.ingestion_mode == "snapshot",
        )
        .all()
    )
    by_code = {str(item.code): item for item in rows}
    if set(by_code) != set(DEMO_BUILDING_CODES):
        missing = ",".join(sorted(set(DEMO_BUILDING_CODES) - set(by_code)))
        raise PortfolioOperatingDemoConflict(f"四栋快照楼宇不完整或模式不正确：{missing}")
    return tuple(by_code[code] for code in DEMO_BUILDING_CODES)


def _validate_source(source: DataSource, *, building_code: str) -> str | None:
    expected = {
        "source_type": "manual_snapshot",
        "name": _source_name(building_code),
        "owner": _DEMO_SOURCE_OWNER,
        "cadence": "monthly",
        "freshness_rule": _DEMO_FRESHNESS_RULE,
        "is_active": True,
    }
    for field, value in expected.items():
        if getattr(source, field) != value:
            return f"模拟来源字段 {field} 与生成器固化值不一致"
    return None


def _inspect_snapshot(
    db: Session,
    *,
    building: Building,
    snapshot: BuildingOperatingSnapshot,
    as_of_date: date,
    submitter: User,
    reviewer: User,
    publisher: User,
) -> _Inspection:
    rows = (
        db.query(BuildingMetricFact, MetricDefinition)
        .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
        .filter(BuildingMetricFact.snapshot_id == snapshot.id)
        .all()
    )
    facts_by_code = {definition.code: fact for fact, definition in rows}
    source_ids = {int(fact.source_id) for fact, _definition in rows if fact.source_id is not None}
    batch_ids = {int(fact.batch_id) for fact, _definition in rows if fact.batch_id is not None}
    sources = (
        db.query(DataSource).filter(DataSource.id.in_(tuple(source_ids))).all()
        if source_ids
        else []
    )
    batches = (
        db.query(ImportBatch).filter(ImportBatch.id.in_(tuple(batch_ids))).all()
        if batch_ids
        else []
    )
    marker_fields = [
        *(source.name for source in sources),
        *(source.owner for source in sources),
        *(batch.external_batch_no or "" for batch in batches),
        *(batch.source_file_name or "" for batch in batches),
        *(batch.parser_version for batch in batches),
        *(fact.calculation_version for fact, _definition in rows),
    ]
    marker_detected = any(DEMO_MARKER in str(value) for value in marker_fields)
    if not marker_detected:
        return _Inspection(False, None, "快照没有 DEMO 模拟追溯标记")

    if snapshot.building_id != building.id:
        return _Inspection(True, None, "快照楼宇归属不一致")
    if snapshot.validation_status != "valid" or snapshot.publication_status != "published":
        return _Inspection(True, None, "DEMO 快照不是有效已发布状态")
    if not bool(snapshot.is_current):
        return _Inspection(True, None, "DEMO 快照不是当前版本")
    actors = (snapshot.submitted_by, snapshot.reviewed_by, snapshot.published_by)
    if None in actors or len(set(actors)) != 3:
        return _Inspection(True, None, "DEMO 快照没有保留三人独立责任链")
    expected_actor_ids = (submitter.id, reviewer.id, publisher.id)
    if actors != expected_actor_ids:
        return _Inspection(True, None, "DEMO 快照责任人与本次指定责任人不一致")
    if snapshot.submitted_at is None or snapshot.reviewed_at is None or snapshot.published_at is None:
        return _Inspection(True, None, "DEMO 快照责任链时间不完整")
    if set(facts_by_code) != set(SUBMITTABLE_CODES) or len(rows) != len(SUBMITTABLE_CODES):
        return _Inspection(True, None, "DEMO 快照未包含完整 OPS_V1 可提交指标")
    if len(source_ids) != 1 or len(batch_ids) != 1 or len(sources) != 1 or len(batches) != 1:
        return _Inspection(True, None, "DEMO 快照来源或批次不唯一")

    source = sources[0]
    batch = batches[0]
    source_issue = _validate_source(source, building_code=building.code)
    if source_issue is not None or source.building_id != building.id:
        return _Inspection(True, None, source_issue or "DEMO 来源楼宇归属不一致")
    expected_batch = {
        "source_id": source.id,
        "source_file_name": _source_file_name(building.code, as_of_date),
        "external_batch_no": _batch_number(building.code, as_of_date),
        "period": as_of_date.strftime("%Y-%m"),
        "as_of_date": as_of_date,
        "file_checksum": _file_checksum(building.code, as_of_date),
        "parser_version": DEMO_CALCULATION_VERSION,
        "actor_id": snapshot.submitted_by,
        "row_count": len(SUBMITTABLE_CODES),
        "accepted_count": len(SUBMITTABLE_CODES),
        "rejected_count": 0,
        "status": "published",
        "error_summary": _DEMO_BATCH_NOTE,
        "reviewed_by": snapshot.reviewed_by,
    }
    for field, value in expected_batch.items():
        if getattr(batch, field) != value:
            return _Inspection(True, None, f"DEMO 批次字段 {field} 与固化值不一致")
    if (
        batch.source_observed_at is None
        or batch.source_updated_at is None
        or batch.received_at is None
        or batch.started_at is None
        or batch.finished_at is None
        or batch.ingested_at is None
        or batch.published_at is None
        or batch.reviewed_at is None
    ):
        return _Inspection(True, None, "DEMO 批次处理与复核时间不完整")

    records = db.query(SourceRecord).filter(SourceRecord.batch_id == batch.id).all()
    records_by_id = {int(record.id): record for record in records}
    if len(records) != len(SUBMITTABLE_CODES):
        return _Inspection(True, None, "DEMO 批次来源行不完整")
    expected_rows = _metric_rows(building.code, as_of_date)
    for row_no, code in enumerate(DEMO_METRIC_CODES, start=1):
        fact = facts_by_code[code]
        expected = expected_rows[code]
        if fact.source_id != source.id or fact.batch_id != batch.id:
            return _Inspection(True, None, f"DEMO 指标 {code} 来源链不一致")
        record = records_by_id.get(int(fact.source_record_id or 0))
        if record is None:
            return _Inspection(True, None, f"DEMO 指标 {code} 缺少来源行")
        expected_record = {
            "external_record_id": _external_record_id(code),
            "row_no": row_no,
            "record_checksum": _record_checksum(building.code, as_of_date, code, expected),
            "status": "accepted",
            "business_object_type": "building_metric_fact",
            "business_object_id": fact.id,
            "source_ref": _source_ref(building.code, as_of_date, code),
        }
        for field, value in expected_record.items():
            if getattr(record, field) != value:
                return _Inspection(True, None, f"DEMO 指标 {code} 的来源行 {field} 不一致")
        for field in ("value", "numerator", "denominator"):
            if not _decimal_equal(getattr(fact, field), expected[field]):
                return _Inspection(True, None, f"DEMO 指标 {code} 的 {field} 不一致")
        if not _decimal_equal(fact.covered_area_sqm, expected["covered_area_sqm"]):
            return _Inspection(True, None, f"DEMO 指标 {code} 的覆盖面积不一致")
        if not _decimal_equal(fact.eligible_area_sqm, expected["eligible_area_sqm"]):
            return _Inspection(True, None, f"DEMO 指标 {code} 的具备数据面积不一致")
        expected_fact_state = {
            "eligible_buildings": 1,
            "included_buildings": 1,
            "covered_days": expected["covered_days"],
            "eligible_days": expected["eligible_days"],
            "estimate_method": "",
            "data_depth": "snapshot",
            "completeness_status": "complete",
            "freshness_status": "current",
            "validation_status": "valid",
            "publication_status": "published",
            "calculation_version": DEMO_CALCULATION_VERSION,
        }
        for field, value in expected_fact_state.items():
            if getattr(fact, field) != value:
                return _Inspection(True, None, f"DEMO 指标 {code} 的状态字段 {field} 不一致")
        if not _decimal_equal(fact.estimate_share, Decimal(0)):
            return _Inspection(True, None, f"DEMO 指标 {code} 的估算比例不为零")

    audit_rows = (
        db.query(OperationLog)
        .filter(
            OperationLog.target_type == "operating_snapshot",
            OperationLog.target_id == snapshot.id,
        )
        .all()
    )
    expected_audit_operators = {
        "snapshot_submit": submitter.username,
        "snapshot_review": reviewer.username,
        "snapshot_publish": publisher.username,
    }
    if len(audit_rows) != len(expected_audit_operators):
        return _Inspection(True, None, "DEMO 快照三步审计记录不完整")
    audit_by_action = {item.action: item for item in audit_rows}
    if set(audit_by_action) != set(expected_audit_operators):
        return _Inspection(True, None, "DEMO 快照三步审计动作不完整或重复")
    for action, expected_operator in expected_audit_operators.items():
        audit = audit_by_action[action]
        if (
            audit.operator != expected_operator
            or audit.scope_type != "building"
            or audit.building_id != building.id
            or audit.portfolio_id is not None
            or DEMO_MARKER not in str(audit.changes or "")
        ):
            return _Inspection(True, None, f"DEMO 快照审计记录 {action} 不一致")
    if f"批次 {batch.id}" not in audit_by_action["snapshot_submit"].changes:
        return _Inspection(True, None, "DEMO 快照提交审计没有对应批次")
    if "复核意见" not in audit_by_action["snapshot_review"].changes:
        return _Inspection(True, None, "DEMO 快照复核审计没有复核意见")
    if "仅供本地演示与开闸验收" not in audit_by_action["snapshot_publish"].changes:
        return _Inspection(True, None, "DEMO 快照发布审计没有模拟用途说明")

    return _Inspection(
        True,
        _ExistingArtifact(
            snapshot_id=int(snapshot.id),
            source_id=int(source.id),
            batch_id=int(batch.id),
        ),
        None,
    )


def _preflight_plans(
    db: Session,
    *,
    buildings: tuple[Building, ...],
    as_of_date: date,
    submitter: User,
    reviewer: User,
    publisher: User,
) -> tuple[_Plan, ...]:
    period_start, period_end = _period(as_of_date)
    plans: list[_Plan] = []
    for building in buildings:
        source = (
            db.query(DataSource)
            .filter(
                DataSource.building_id == building.id,
                DataSource.name == _source_name(building.code),
            )
            .one_or_none()
        )
        if source is not None:
            source_issue = _validate_source(source, building_code=building.code)
            if source_issue is not None:
                raise PortfolioOperatingDemoConflict(f"{building.code}：{source_issue}，拒绝覆盖")

        candidate_batches = (
            db.query(ImportBatch)
            .join(DataSource, DataSource.id == ImportBatch.source_id)
            .filter(
                DataSource.building_id == building.id,
                or_(
                    ImportBatch.external_batch_no == _batch_number(building.code, as_of_date),
                    ImportBatch.file_checksum == _file_checksum(building.code, as_of_date),
                ),
            )
            .all()
        )
        snapshots = (
            db.query(BuildingOperatingSnapshot)
            .filter(
                BuildingOperatingSnapshot.building_id == building.id,
                BuildingOperatingSnapshot.period_start == period_start,
                BuildingOperatingSnapshot.period_end == period_end,
                BuildingOperatingSnapshot.as_of_date == as_of_date,
            )
            .all()
        )
        current = [
            item
            for item in snapshots
            if item.publication_status == "published" and bool(item.is_current)
        ]
        if len(current) > 1:
            raise PortfolioOperatingDemoConflict(f"{building.code}：同统计日存在多个 current 发布版")
        if current:
            inspection = _inspect_snapshot(
                db,
                building=building,
                snapshot=current[0],
                as_of_date=as_of_date,
                submitter=submitter,
                reviewer=reviewer,
                publisher=publisher,
            )
            if inspection.artifact is not None:
                if len(candidate_batches) != 1 or candidate_batches[0].id != inspection.artifact.batch_id:
                    raise PortfolioOperatingDemoConflict(
                        f"{building.code}：同一 DEMO 批次追溯不唯一，拒绝追加"
                    )
                plans.append(
                    _Plan(
                        building=building,
                        source=source,
                        status="skipped",
                        artifact=inspection.artifact,
                    )
                )
                continue
            if inspection.marker_detected or candidate_batches:
                raise PortfolioOperatingDemoConflict(
                    f"{building.code}：已有 DEMO current 发布版但不完整（{inspection.issue}），拒绝覆盖"
                )
            raise PortfolioOperatingDemoConflict(
                f"{building.code}：同统计日已有非 DEMO current published snapshot，零写入退出"
            )

        if snapshots:
            raise PortfolioOperatingDemoConflict(
                f"{building.code}：同统计日已有非 current 快照，拒绝追加或覆盖"
            )
        if candidate_batches:
            raise PortfolioOperatingDemoConflict(
                f"{building.code}：已有同名 DEMO 批次但没有完整 current 发布版，拒绝追加"
            )
        plans.append(_Plan(building=building, source=source, status="create", artifact=None))
    return tuple(plans)


def _preflight_gates(db: Session, buildings: tuple[Building, ...]) -> None:
    resolution = resolve_building_operating_gates(db, tuple(item.id for item in buildings))
    invalid = [
        f"{decision.building_code}={decision.state}"
        for decision in resolution.decisions
        if decision.state not in {"observe", "open"}
    ]
    if invalid:
        raise PortfolioOperatingDemoConflict(
            "模拟数据开闸只允许从 observe 进入 open：" + ",".join(invalid)
        )


def _create_source(db: Session, building: Building) -> DataSource:
    source = DataSource(
        building_id=building.id,
        source_type="manual_snapshot",
        name=_source_name(building.code),
        owner=_DEMO_SOURCE_OWNER,
        cadence="monthly",
        freshness_rule=_DEMO_FRESHNESS_RULE,
        is_active=True,
    )
    db.add(source)
    db.flush()
    return source


def _create_artifact(
    db: Session,
    *,
    building: Building,
    source: DataSource,
    definitions: dict[str, MetricDefinition],
    as_of_date: date,
    submitter: User,
    reviewer: User,
    publisher: User,
) -> _ExistingArtifact:
    created_at = now_in_timezone()
    source_updated_at = datetime.combine(as_of_date, time.min)
    metric_rows = _metric_rows(building.code, as_of_date)
    batch = ImportBatch(
        source_id=source.id,
        source_file_name=_source_file_name(building.code, as_of_date),
        external_batch_no=_batch_number(building.code, as_of_date),
        period=as_of_date.strftime("%Y-%m"),
        as_of_date=as_of_date,
        source_observed_at=created_at,
        source_updated_at=source_updated_at,
        received_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        ingested_at=created_at,
        published_at=created_at,
        file_checksum=_file_checksum(building.code, as_of_date),
        parser_version=DEMO_CALCULATION_VERSION,
        actor_id=submitter.id,
        row_count=len(DEMO_METRIC_CODES),
        accepted_count=len(DEMO_METRIC_CODES),
        rejected_count=0,
        status="published",
        error_summary=_DEMO_BATCH_NOTE,
        reviewed_by=reviewer.id,
        reviewed_at=created_at,
        created_at=created_at,
    )
    db.add(batch)
    db.flush()

    period_start, period_end = _period(as_of_date)
    snapshot = BuildingOperatingSnapshot(
        building_id=building.id,
        period_start=period_start,
        period_end=period_end,
        as_of_date=as_of_date,
        revision=1,
        validation_status="valid",
        publication_status="published",
        is_current=True,
        submitted_by=submitter.id,
        submitted_at=created_at,
        reviewed_by=reviewer.id,
        reviewed_at=created_at,
        published_by=publisher.id,
        published_at=created_at,
        supersedes_id=None,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(snapshot)
    db.flush()

    for row_no, code in enumerate(DEMO_METRIC_CODES, start=1):
        values = metric_rows[code]
        record = SourceRecord(
            batch_id=batch.id,
            external_record_id=_external_record_id(code),
            row_no=row_no,
            record_checksum=_record_checksum(building.code, as_of_date, code, values),
            status="accepted",
            source_ref=_source_ref(building.code, as_of_date, code),
            created_at=created_at,
        )
        db.add(record)
        db.flush()
        fact = BuildingMetricFact(
            snapshot_id=snapshot.id,
            metric_definition_id=definitions[code].id,
            value=_number(values["value"]),
            numerator=_number(values["numerator"]),
            denominator=_number(values["denominator"]),
            source_id=source.id,
            batch_id=batch.id,
            source_record_id=record.id,
            eligible_buildings=1,
            included_buildings=1,
            covered_area_sqm=_number(values["covered_area_sqm"]),
            eligible_area_sqm=_number(values["eligible_area_sqm"]),
            covered_days=int(values["covered_days"]),
            eligible_days=int(values["eligible_days"]),
            estimate_share=0,
            estimate_method="",
            data_depth="snapshot",
            completeness_status="complete",
            freshness_status="current",
            validation_status="valid",
            publication_status="published",
            calculation_version=DEMO_CALCULATION_VERSION,
            created_at=created_at,
        )
        db.add(fact)
        db.flush()
        record.business_object_type = "building_metric_fact"
        record.business_object_id = fact.id
    audit_scope = ObjectScope(scope_type="building", building_id=int(building.id))
    log_action(
        db,
        None,
        "snapshot_submit",
        (
            f"{DEMO_MARKER} 模拟数据提交；批次 {batch.id}；"
            f"修订 {snapshot.revision}；指标 {len(DEMO_METRIC_CODES)} 项"
        ),
        submitter.username,
        commit=False,
        target_type="operating_snapshot",
        target_id=int(snapshot.id),
        object_scope=audit_scope,
    )
    log_action(
        db,
        None,
        "snapshot_review",
        f"{DEMO_MARKER} 模拟数据复核通过；复核意见：固化口径与内部一致性校验通过",
        reviewer.username,
        commit=False,
        target_type="operating_snapshot",
        target_id=int(snapshot.id),
        object_scope=audit_scope,
    )
    log_action(
        db,
        None,
        "snapshot_publish",
        f"{DEMO_MARKER} 模拟数据发布；仅供本地演示与开闸验收；修订 {snapshot.revision}",
        publisher.username,
        commit=False,
        target_type="operating_snapshot",
        target_id=int(snapshot.id),
        object_scope=audit_scope,
    )
    db.flush()
    return _ExistingArtifact(
        snapshot_id=int(snapshot.id),
        source_id=int(source.id),
        batch_id=int(batch.id),
    )


def _open_validated_gates(
    db: Session,
    *,
    buildings: tuple[Building, ...],
    publisher: User,
) -> None:
    for building in buildings:
        decision = resolve_building_operating_gates(db, (building.id,)).decisions[0]
        if decision.state == "open":
            continue
        if decision.state != "observe":
            raise PortfolioOperatingDemoConflict(
                f"{building.code} 开闸前状态已变为 {decision.state}，停止后续开闸"
            )
        record_building_operating_gate(
            db,
            building_code=building.code,
            actor=publisher,
            presentation_enabled=True,
            contribution_enabled=True,
            reason=f"模拟数据已核验；{building.name} 开放展示并参与板块合计",
            expected_revision=decision.revision,
            commit=False,
        )


def seed_portfolio_operating_demo(
    db: Session,
    *,
    as_of_date: date,
    submitter_id: int,
    reviewer_id: int,
    publisher_id: int,
    open_gates: bool = True,
    simulation_today: date | None = None,
) -> dict:
    """Append one fully traceable simulated OPS_V1 snapshot for each snapshot building.

    Exact artifacts are skipped. Any ambiguous or non-demo artifact causes a
    preflight conflict before this function writes data.
    """

    _validate_request_date(as_of_date, simulation_today=simulation_today)
    submitter, reviewer, publisher = _load_actors(
        db,
        submitter_id=submitter_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
    )
    buildings = _load_buildings(db)
    _validate_actor_capabilities(
        db,
        submitter=submitter,
        reviewer=reviewer,
        publisher=publisher,
        buildings=buildings,
        open_gates=open_gates,
    )
    if open_gates:
        _preflight_gates(db, buildings)
    plans = _preflight_plans(
        db,
        buildings=buildings,
        as_of_date=as_of_date,
        submitter=submitter,
        reviewer=reviewer,
        publisher=publisher,
    )

    artifacts: dict[str, _ExistingArtifact] = {
        plan.building.code: plan.artifact
        for plan in plans
        if plan.artifact is not None
    }
    create_plans = tuple(plan for plan in plans if plan.status == "create")
    try:
        if create_plans:
            definitions = {
                item.code: item
                for item in ensure_ops_v1_definitions(db, publisher=publisher)
            }
            for plan in create_plans:
                source = plan.source or _create_source(db, plan.building)
                artifacts[plan.building.code] = _create_artifact(
                    db,
                    building=plan.building,
                    source=source,
                    definitions=definitions,
                    as_of_date=as_of_date,
                    submitter=submitter,
                    reviewer=reviewer,
                    publisher=publisher,
                )

            for plan in plans:
                artifact = artifacts[plan.building.code]
                snapshot = db.get(BuildingOperatingSnapshot, artifact.snapshot_id)
                if snapshot is None:
                    raise RuntimeError(f"{plan.building.code} 模拟快照写入后不可见")
                inspection = _inspect_snapshot(
                    db,
                    building=plan.building,
                    snapshot=snapshot,
                    as_of_date=as_of_date,
                    submitter=submitter,
                    reviewer=reviewer,
                    publisher=publisher,
                )
                if inspection.artifact is None:
                    raise RuntimeError(
                        f"{plan.building.code} 模拟快照写入后校验失败：{inspection.issue}"
                    )
        if open_gates:
            _open_validated_gates(db, buildings=buildings, publisher=publisher)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PortfolioOperatingDemoConflict(
            "模拟快照或开闸写入期间出现并发或唯一性冲突，本次新增内容已整体回滚"
        ) from exc
    except Exception:
        db.rollback()
        raise

    final_gates = resolve_building_operating_gates(db, tuple(item.id for item in buildings))
    gates_by_id = final_gates.by_building_id
    status_by_code = {plan.building.code: plan.status for plan in plans}
    result_rows = []
    for building in buildings:
        artifact = artifacts[building.code]
        result_rows.append(
            {
                "building_code": building.code,
                "building_name": building.name,
                "status": "created" if status_by_code[building.code] == "create" else "skipped",
                "snapshot_id": artifact.snapshot_id,
                "source_id": artifact.source_id,
                "batch_id": artifact.batch_id,
                "gate_state": gates_by_id[int(building.id)].state,
            }
        )
    return {
        "data_classification": "simulated",
        "demo_marker": DEMO_MARKER,
        "definition_version": "OPS_V1",
        "calculation_version": DEMO_CALCULATION_VERSION,
        "as_of_date": as_of_date.isoformat(),
        "simulation_reference_date": simulation_today.isoformat() if simulation_today else None,
        "open_gates": bool(open_gates),
        "gate_policy_version": final_gates.policy_version,
        "buildings": result_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成四栋楼的 DEMO 模拟 OPS_V1 经营快照")
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=today_in_timezone())
    parser.add_argument("--simulation-today", type=date.fromisoformat, help="显式模拟参考日，仅影响 DEMO 快照，不改变系统时间")
    parser.add_argument("--submitter-id", type=int, required=True)
    parser.add_argument("--reviewer-id", type=int, required=True)
    parser.add_argument("--publisher-id", type=int, required=True)
    parser.add_argument(
        "--keep-gates-observe",
        action="store_true",
        help="只生成并核验数据，不把 observe 开为 open",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        result = seed_portfolio_operating_demo(
            db,
            as_of_date=args.as_of_date,
            simulation_today=args.simulation_today,
            submitter_id=args.submitter_id,
            reviewer_id=args.reviewer_id,
            publisher_id=args.publisher_id,
            open_gates=not args.keep_gates_observe,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
