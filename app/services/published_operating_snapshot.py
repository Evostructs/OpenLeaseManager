from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    Portfolio,
    PortfolioBuilding,
    SourceRecord,
)
from app.ops_v1 import OPS_V1_DEFINITIONS
from app.services.building_operating_gate import resolve_building_operating_gates
from app.services.metric_definitions import published_ops_v1_by_code
from app.services.property_scope import SelectedPropertyScope


CALCULATION_VERSION = "ops-v1-published-aggregate-v1"
_SIX_PLACES = Decimal("0.000001")


def _float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP))


def _month_period(as_of_date: date) -> tuple[date, date]:
    return (
        as_of_date.replace(day=1),
        as_of_date.replace(day=calendar.monthrange(as_of_date.year, as_of_date.month)[1]),
    )


def _max_iso(values: list[datetime | None]) -> str | None:
    concrete = [item for item in values if item is not None]
    return max(concrete).isoformat() if concrete else None


def _portfolio_building_count(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    as_of_date: date,
) -> int:
    if scope.scope_type != "portfolio" or scope.portfolio_id is None:
        return 1
    return int(
        db.query(PortfolioBuilding.id)
        .join(Building, Building.id == PortfolioBuilding.building_id)
        .filter(
            PortfolioBuilding.portfolio_id == scope.portfolio_id,
            PortfolioBuilding.valid_from <= as_of_date,
            or_(PortfolioBuilding.valid_to.is_(None), PortfolioBuilding.valid_to >= as_of_date),
            Building.is_active.is_(True),
        )
        .count()
    )


def _aggregation_building_ids(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    as_of_date: date,
) -> tuple[int, ...]:
    """Intersect current authorization with portfolio membership on the target date.

    The request scope is resolved from the user's current grants.  Historical
    portfolio reads must additionally honor time-bounded membership, but that
    lookup must never add a building that is absent from the authorized scope.
    """

    authorized_ids = tuple(dict.fromkeys(int(item) for item in scope.building_ids))
    if scope.scope_type != "portfolio" or scope.portfolio_id is None or not authorized_ids:
        return authorized_ids
    member_ids = {
        int(building_id)
        for (building_id,) in (
            db.query(PortfolioBuilding.building_id)
            .filter(
                PortfolioBuilding.portfolio_id == scope.portfolio_id,
                PortfolioBuilding.building_id.in_(authorized_ids),
                PortfolioBuilding.valid_from <= as_of_date,
                or_(
                    PortfolioBuilding.valid_to.is_(None),
                    PortfolioBuilding.valid_to >= as_of_date,
                ),
            )
            .distinct()
            .all()
        )
    }
    return tuple(building_id for building_id in authorized_ids if building_id in member_ids)


def _excluded_reason(fact: BuildingMetricFact | None) -> str | None:
    if fact is None:
        return "该统计日没有已发布指标事实"
    if fact.validation_status != "valid":
        return "指标未通过校验"
    if fact.publication_status != "published":
        return "指标不是当前已发布版本"
    if fact.freshness_status == "stale":
        return "指标已过期"
    if fact.freshness_status != "current":
        return "指标新鲜度未知"
    if fact.completeness_status == "unknown":
        return "指标完整度未知"
    if fact.included_buildings != 1:
        return "指标覆盖状态不允许纳入"
    return None


def _gate_state_value(decision) -> str:
    state = getattr(decision, "state", "unknown")
    return str(getattr(state, "value", state))


def _gate_excluded_reason(decision) -> str:
    return f"楼宇开闸策略未允许参与板块合计（状态：{_gate_state_value(decision)}）"


def _combined_reason(*parts: str | None) -> str | None:
    unique: list[str] = []
    for part in parts:
        normalized = str(part or "").strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return "；".join(unique) or None


def _gate_decision_payload(decision, *, building: Building) -> dict:
    payload = dict(decision.as_dict())
    payload.setdefault("building_id", int(building.id))
    payload.setdefault("building_code", str(building.code))
    payload.setdefault("building_name", str(building.name))
    return payload


def _sum_optional(left: float | int | None, right: float | int | None) -> float | None:
    if left is None and right is None:
        return None
    return _float(Decimal(str(left or 0)) + Decimal(str(right or 0)))


def _metric_value(metrics: dict[str, dict], code: str) -> float | None:
    metric = metrics.get(code) or {}
    value = metric.get("value")
    return float(value) if value is not None else None


def _detail_source_ids(snapshot: dict, *, building: Building) -> tuple[object, ...]:
    """Keep a detail adapter's own source identity across portfolio projection."""

    source_ids: list[object] = []
    for source in snapshot.get("sources") or []:
        if not isinstance(source, dict) or source.get("id") is None:
            continue
        source_id = source["id"]
        if source_id not in source_ids:
            source_ids.append(source_id)
    if not source_ids:
        raise HTTPException(409, f"{building.name}明细适配器未声明数据来源")
    return tuple(source_ids)


def _detail_source_payloads(snapshot: dict, *, building: Building) -> list[dict]:
    declared: dict[object, dict] = {}
    for source in snapshot.get("sources") or []:
        if not isinstance(source, dict) or source.get("id") is None:
            continue
        payload = dict(source)
        payload["building_id"] = building.id
        payload.setdefault("source_type", "detail")
        payload.setdefault("cadence", "live")
        payload["data_classification"] = "simulated"
        declared[payload["id"]] = payload
    for source_id in _detail_source_ids(snapshot, building=building):
        if source_id not in declared:
            raise HTTPException(409, f"{building.name}明细适配器来源元数据不完整")
    return list(declared.values())


def _merge_detail_adapter_metrics(
    metrics: dict[str, dict],
    *,
    detail_snapshots: dict[int, dict],
    building_by_id: dict[int, Building],
    authorized_count: int,
) -> None:
    """Merge same-day live detail facts by raw fractions, never by displayed rates."""

    if not detail_snapshots:
        return
    for spec in OPS_V1_DEFINITIONS:
        code = spec["code"]
        if spec["aggregation_rule"] == "derived_authorized_building_count":
            continue
        aggregate = metrics[code]
        coverage = aggregate["coverage"]
        existing_included = int(coverage.get("included_buildings") or 0)
        adapter_included = 0
        adapter_eligible = 0
        adapter_num = Decimal(0)
        adapter_den = Decimal(0)
        has_adapter_denominator = False
        adapter_covered_area: float | None = None
        adapter_known_area: float | None = None
        adapter_covered_days = 0
        adapter_eligible_days = 0
        included_adapter_source_ids: list[object] = []
        excluded = [
            item
            for item in coverage.get("excluded_buildings", [])
            if (item.get("building_code") or "").casefold()
            not in {building_by_id[item].code.casefold() for item in detail_snapshots}
        ]

        for building_id, snapshot in detail_snapshots.items():
            building = building_by_id[building_id]
            detail = (snapshot.get("metrics") or {}).get(code)
            reason = None
            if detail is None:
                reason = "楼宇明细适配器尚未提供该指标"
            elif detail.get("unavailable_reason"):
                reason = str(detail["unavailable_reason"])
            elif detail.get("numerator") is None:
                reason = "楼宇明细适配器缺少原始分子"
            elif spec["aggregation_rule"] in {"ratio", "ratio_percent"} and detail.get("denominator") is None:
                reason = "楼宇明细适配器缺少原始分母"

            if detail is not None:
                detail_coverage = detail.get("coverage") or {}
                adapter_eligible += int(bool(detail_coverage.get("eligible_buildings")))
                adapter_covered_area = _sum_optional(
                    adapter_covered_area,
                    detail_coverage.get("covered_area_sqm"),
                )
                adapter_known_area = _sum_optional(
                    adapter_known_area,
                    detail_coverage.get("known_total_area_sqm"),
                )
                adapter_covered_days += int(detail_coverage.get("covered_days") or 0)
                adapter_eligible_days += int(detail_coverage.get("eligible_days") or 0)

            if reason is not None:
                excluded.append({"building_code": building.code, "reason": reason})
                continue
            adapter_included += 1
            for source_id in (
                (detail.get("coverage") or {}).get("source_ids")
                or _detail_source_ids(snapshot, building=building)
            ):
                if source_id not in included_adapter_source_ids:
                    included_adapter_source_ids.append(source_id)
            adapter_eligible = max(adapter_eligible, adapter_included)
            adapter_num += Decimal(str(detail["numerator"]))
            if detail.get("denominator") is not None:
                adapter_den += Decimal(str(detail["denominator"]))
                has_adapter_denominator = True

        if adapter_included:
            base_num = Decimal(str(aggregate.get("numerator") or 0))
            numerator = base_num + adapter_num
            denominator: Decimal | None = None
            aggregation = spec["aggregation_rule"]
            if aggregation in {"ratio", "ratio_percent"}:
                denominator = Decimal(str(aggregate.get("denominator") or 0)) + adapter_den
                value = None if denominator <= 0 else numerator / denominator
                if value is not None and aggregation == "ratio_percent":
                    value *= Decimal(100)
            else:
                value = numerator
            aggregate["numerator"] = _float(numerator)
            aggregate["denominator"] = _float(denominator)
            aggregate["value"] = _float(value)
            aggregate["unavailable_reason"] = (
                None if value is not None else "纳入楼宇的合计分母为 0，指标不可计算"
            )

        total_included = existing_included + adapter_included
        coverage["eligible_buildings"] = int(coverage.get("eligible_buildings") or 0) + adapter_eligible
        coverage["included_buildings"] = total_included
        coverage["covered_area_sqm"] = _sum_optional(
            coverage.get("covered_area_sqm"),
            adapter_covered_area,
        )
        coverage["known_total_area_sqm"] = _sum_optional(
            coverage.get("known_total_area_sqm"),
            adapter_known_area,
        )
        if coverage.get("covered_days") is not None or adapter_covered_days:
            coverage["covered_days"] = int(coverage.get("covered_days") or 0) + adapter_covered_days
        if coverage.get("eligible_days") is not None or adapter_eligible_days:
            coverage["eligible_days"] = int(coverage.get("eligible_days") or 0) + adapter_eligible_days
        if adapter_included:
            coverage["source_mix"] = sorted(set(coverage.get("source_mix") or []) | {"detail"})
            source_ids = list(coverage.get("source_ids") or [])
            for source_id in included_adapter_source_ids:
                if source_id not in source_ids:
                    source_ids.append(source_id)
            coverage["source_ids"] = source_ids
            coverage["data_depth"] = "mixed" if existing_included else "detail"
        coverage["completeness_status"] = (
            "complete" if total_included == authorized_count
            else "partial" if total_included
            else "missing"
        )
        # The current detail adapter does not expose a trustworthy business
        # observation timestamp.  Its live values may participate in today's
        # aggregate, but that must not upgrade the aggregate's evidence status
        # to ``current``.  When no adapter value is included, preserve the
        # publication-derived state (including ``stale`` for retained history).
        if adapter_included:
            coverage["freshness_status"] = "unknown"
        coverage["validation_status"] = "valid" if total_included else "pending"
        if adapter_included:
            coverage["publication_status"] = (
                "published_with_live_adapter" if existing_included else "live_adapter"
            )
        coverage["excluded_buildings"] = excluded


def _compatibility_projections(
    *,
    metrics: dict[str, dict],
    as_of_date: date,
    contributions: list[dict],
) -> tuple[dict, dict, dict]:
    """Project OPS_V1 onto legacy homepage/presentation fields during migration."""

    rentable = _metric_value(metrics, "area.rentable_sqm")
    leased = _metric_value(metrics, "area.leased_sqm")
    occupancy = _metric_value(metrics, "occupancy.area_rate")
    vacancy = _metric_value(metrics, "vacancy.area_sqm")
    receivable = _metric_value(metrics, "billing.receivable_month")
    received = _metric_value(metrics, "collection.received_month")
    collection_rate = _metric_value(metrics, "collection.rate_month")
    arrears = _metric_value(metrics, "arrears.balance")
    rent = _metric_value(metrics, "rent.effective_daily")
    expiry = _metric_value(metrics, "expiry.contract_count_90d")
    risk = _metric_value(metrics, "risk.open_items")
    unpaid = (
        max(receivable - received, 0)
        if receivable is not None and received is not None
        else None
    )
    month = as_of_date.strftime("%Y-%m")
    empty_queues = {
        "overdue_contracts": [],
        "expiring_contracts": [],
        "due_follow_up_customers": [],
        "unpaid_deposits": [],
        "missing_contract_body": [],
    }
    detail_unavailable_reason = "当前数据来源不提供合同、客户、房源和待办明细"
    queue_counts = {
        "overdue_contracts": None,
        "expiring_contracts": int(expiry) if expiry is not None else None,
        "due_follow_up_customers": None,
        "unpaid_deposits": None,
        "missing_contract_body": None,
    }
    queue_count_unavailable_reasons = {
        key: (
            (metrics.get("expiry.contract_count_90d") or {}).get("unavailable_reason")
            or detail_unavailable_reason
            if key == "expiring_contracts"
            else detail_unavailable_reason
        )
        for key, value in queue_counts.items()
        if value is None
    }
    legacy_stats = {
        "total": None,
        "active_count": None,
        "statuses": {},
        "expiring_soon": [],
        "metrics": {
            "total_area": leased,
            "total_monthly_rent": receivable,
            "total_net_rent": receivable,
            "avg_price": rent,
            "total_deposit": None,
            "room_total_area": rentable,
            "occupancy_rate": occupancy,
        },
        "metrics_trends": {},
        "disposals": {},
        "tower_data": [],
        "customer_data": [],
        "lease_types": {},
        "expiry_months": [],
        "metrics_extra": {},
        "term_dist": {},
        "price_trend": [],
        "income_forecast": [],
        "receivables": {
            "month": month,
            "total_rent": receivable,
            "total_received": received,
            "total_balance": unpaid,
            "total_unpaid_balance": unpaid,
            "total_overdue_balance": arrears,
            "total_not_due_balance": None,
            "received_rate": collection_rate,
            "unpaid_count": None,
            "overdue_count": None,
            "aging_buckets": [],
            "key_debtors": [],
            "dunning_alerts": [],
            "trend": [],
        },
        "projection": {
            "source": "OPS_V1",
            "detail_lists_available": False,
            "detail_lists_unavailable_reason": detail_unavailable_reason,
        },
    }
    legacy_operations = {
        "date": as_of_date.isoformat(),
        "month": month,
        "metrics": {
            "current_month_receivable": receivable,
            "current_month_received": received,
            "current_month_unpaid_balance": unpaid,
            "current_month_overdue_balance": arrears,
            "overdue_balance": arrears,
            "received_rate": collection_rate,
            "vacancy_area": vacancy,
            "vacancy_room_count": None,
            "expiring_contract_count_90": expiry,
            "due_follow_up_customer_count": None,
            "unpaid_deposit_amount": None,
            "unpaid_deposit_count": None,
            "missing_contract_body_count": None,
            "risk_count": risk,
            "high_risk_count": None,
        },
        "current_month": {
            "receivable": receivable,
            "received": received,
            "unpaid_balance": unpaid,
            "overdue_balance": arrears,
            "received_rate": collection_rate,
        },
        "vacancy": {
            "room_count": None,
            "area": vacancy,
            "total_rooms": None,
            "total_area": rentable,
            "occupancy_rate": occupancy,
        },
        "overdue": {"contract_count": None, "total_overdue_balance": arrears},
        "follow_ups": {"customer_count": None, "follow_up_count": None},
        "deposits": {"deposit_count": None, "unpaid_amount": None},
        "missing_contract_body": {"contract_count": None},
        "risks": {"summary": {"total": risk, "by_severity": {"high": None}}, "top": []},
        "trend": [
            {
                "month": month,
                "receivable": receivable,
                "received": received,
                "received_rate": collection_rate,
            }
        ],
        "queues": empty_queues,
        "queue_counts": queue_counts,
        "queue_count_unavailable_reasons": queue_count_unavailable_reasons,
        "todos": [],
        "projection": {
            "source": "OPS_V1",
            "detail_lists_available": False,
            "detail_lists_unavailable_reason": detail_unavailable_reason,
        },
    }
    tower = []
    for contribution in contributions:
        contribution_metrics = contribution.get("metrics") or {}
        total = (contribution_metrics.get("area.rentable_sqm") or {}).get("value")
        building_leased = (contribution_metrics.get("area.leased_sqm") or {}).get("value")
        rate = (contribution_metrics.get("occupancy.area_rate") or {}).get("value")
        if total is None and building_leased is None and rate is None:
            continue
        tower.append(
            {
                "name": (contribution.get("building") or {}).get("name") or "未命名楼宇",
                "total": total,
                "leased": building_leased,
                "rate": rate,
                "building_code": (contribution.get("building") or {}).get("code"),
            }
        )
    presentation = {
        "tower": tower,
        "price": {
            "current_by_segment": {"overall": rent},
            "average_unit_price": rent,
            "months": [month],
            "trend_by_segment": {},
            "coverage": (metrics.get("rent.effective_daily") or {}).get("coverage") or {},
        },
        "trend": legacy_operations["trend"],
        "contributions": contributions,
    }
    return legacy_stats, legacy_operations, presentation


def build_published_operating_snapshot(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    as_of_date: date,
    detail_snapshots: dict[int, dict] | None = None,
    adapter_unavailable_reasons: dict[int, str] | None = None,
) -> dict:
    authorized_scope_ids = tuple(dict.fromkeys(int(item) for item in scope.building_ids))
    if not authorized_scope_ids:
        raise HTTPException(404, "当前范围没有可访问楼宇")
    building_ids = _aggregation_building_ids(db, scope=scope, as_of_date=as_of_date)
    buildings = (
        db.query(Building)
        .filter(Building.id.in_(building_ids), Building.is_active.is_(True))
        .order_by(Building.id.asc())
        .all()
        if building_ids
        else []
    )
    if {item.id for item in buildings} != set(building_ids):
        raise HTTPException(404, "楼宇不存在或无权访问")
    building_by_id = {item.id: item for item in buildings}
    requested_adapter_unavailable_reasons = {
        int(building_id): str(reason or "").strip()
        for building_id, reason in (adapter_unavailable_reasons or {}).items()
    }
    if not set(requested_adapter_unavailable_reasons).issubset(authorized_scope_ids):
        raise HTTPException(409, "数据适配器状态包含授权范围外楼宇")
    if any(not reason for reason in requested_adapter_unavailable_reasons.values()):
        raise HTTPException(409, "不可用的数据适配器必须说明原因")
    normalized_adapter_unavailable_reasons = {
        building_id: reason
        for building_id, reason in requested_adapter_unavailable_reasons.items()
        if building_id in building_by_id
    }
    adapter_enabled_ids = tuple(
        building_id
        for building_id in building_ids
        if building_id not in normalized_adapter_unavailable_reasons
    )
    gate_resolution = resolve_building_operating_gates(
        db,
        building_ids,
    )
    gate_by_building_id = gate_resolution.by_building_id
    if set(gate_by_building_id) != set(building_ids):
        raise HTTPException(409, "楼宇开闸决策与当前授权范围不一致")
    gate_contribution_enabled_ids = tuple(
        building_id
        for building_id in building_ids
        if bool(gate_by_building_id[building_id].contribution_enabled)
    )
    aggregation_enabled_ids = (
        gate_contribution_enabled_ids
        if scope.scope_type == "portfolio"
        else building_ids
    )
    aggregation_enabled_ids = tuple(
        building_id
        for building_id in aggregation_enabled_ids
        if building_id in adapter_enabled_ids
    )
    presentation_enabled_ids = tuple(
        building_id
        for building_id in building_ids
        if bool(gate_by_building_id[building_id].presentation_enabled)
    )
    requested_detail_snapshots = {
        int(building_id): snapshot
        for building_id, snapshot in (detail_snapshots or {}).items()
    }
    if not set(requested_detail_snapshots).issubset(authorized_scope_ids):
        raise HTTPException(409, "明细适配器返回了授权范围外楼宇")
    normalized_detail_snapshots = {
        building_id: snapshot
        for building_id, snapshot in requested_detail_snapshots.items()
        if building_id in building_by_id
    }
    for building_id, snapshot in normalized_detail_snapshots.items():
        if snapshot.get("as_of_date") != as_of_date.isoformat():
            raise HTTPException(409, "明细适配器统计日与组合统计日不一致")
        if snapshot.get("definition_version") != "OPS_V1":
            raise HTTPException(409, "明细适配器未使用 OPS_V1 指标定义")
        snapshot_ids = tuple(int(item) for item in (snapshot.get("scope") or {}).get("building_ids") or [])
        if snapshot_ids != (building_id,):
            raise HTTPException(409, "明细适配器楼宇归属与组合范围不一致")
        declared_source_ids = set(
            _detail_source_ids(snapshot, building=building_by_id[building_id])
        )
        for metric in (snapshot.get("metrics") or {}).values():
            if not isinstance(metric, dict):
                continue
            referenced_source_ids = set(
                (metric.get("coverage") or {}).get("source_ids") or []
            )
            if not referenced_source_ids.issubset(declared_source_ids):
                raise HTTPException(409, "明细指标引用了未声明的数据来源")
    period_start, period_end = _month_period(as_of_date)
    snapshot_query = db.query(BuildingOperatingSnapshot).filter(
        BuildingOperatingSnapshot.building_id.in_(adapter_enabled_ids or (-1,)),
        BuildingOperatingSnapshot.period_start == period_start,
        BuildingOperatingSnapshot.period_end == period_end,
        BuildingOperatingSnapshot.as_of_date == as_of_date,
        BuildingOperatingSnapshot.validation_status == "valid",
        BuildingOperatingSnapshot.publication_status == "published",
        BuildingOperatingSnapshot.is_current.is_(True),
    )
    if normalized_detail_snapshots:
        snapshot_query = snapshot_query.filter(
            BuildingOperatingSnapshot.building_id.notin_(tuple(normalized_detail_snapshots))
        )
    snapshots = (
        snapshot_query
        .all()
    )
    snapshot_by_building = {item.building_id: item for item in snapshots}
    snapshot_ids = tuple(item.id for item in snapshots)
    definitions = published_ops_v1_by_code(db)

    rows = []
    if snapshot_ids:
        rows = (
            db.query(
                BuildingMetricFact,
                MetricDefinition,
                DataSource,
                ImportBatch,
                SourceRecord,
            )
            .join(MetricDefinition, MetricDefinition.id == BuildingMetricFact.metric_definition_id)
            .join(DataSource, DataSource.id == BuildingMetricFact.source_id)
            .outerjoin(ImportBatch, ImportBatch.id == BuildingMetricFact.batch_id)
            .outerjoin(SourceRecord, SourceRecord.id == BuildingMetricFact.source_record_id)
            .filter(BuildingMetricFact.snapshot_id.in_(snapshot_ids))
            .all()
        )
    snapshot_building_by_id = {item.id: item.building_id for item in snapshots}
    fact_by_code_building: dict[str, dict[int, tuple]] = defaultdict(dict)
    all_sources: dict[int, DataSource] = {}
    all_batches: dict[int, ImportBatch] = {}
    published_times: list[datetime | None] = []
    observed_times: list[datetime | None] = []
    updated_times: list[datetime | None] = []
    received_times: list[datetime | None] = []
    ingested_times: list[datetime | None] = []
    for fact, definition, source, batch, record in rows:
        building_id = snapshot_building_by_id.get(fact.snapshot_id)
        if building_id is None or source.building_id != building_id:
            raise HTTPException(409, "已发布指标的楼宇来源关系不一致")
        expected_definition = definitions.get(definition.code)
        if expected_definition is None or expected_definition.id != definition.id:
            raise HTTPException(409, "已发布快照引用了非 OPS_V1 指标定义")
        if source.source_type in {"manual_snapshot", "excel"}:
            if (
                batch is None
                or batch.source_id != source.id
                or batch.as_of_date != as_of_date
                or batch.status != "published"
                or record is None
                or record.batch_id != batch.id
                or record.status != "accepted"
            ):
                raise HTTPException(409, "已发布指标的来源批次链不完整")
        fact_by_code_building[definition.code][building_id] = (
            fact,
            source,
            batch,
            record,
        )
        all_sources[source.id] = source
        if batch is not None:
            all_batches[batch.id] = batch
            observed_times.append(batch.source_observed_at)
            updated_times.append(batch.source_updated_at)
            received_times.append(batch.received_at)
            ingested_times.append(batch.ingested_at)
            published_times.append(batch.published_at)

    authorized_count = len(building_ids)
    contribution_enabled_count = len(aggregation_enabled_ids)
    gate_contribution_enabled_count = len(gate_contribution_enabled_ids)
    presentation_enabled_count = len(presentation_enabled_ids)
    portfolio_count = _portfolio_building_count(db, scope=scope, as_of_date=as_of_date)
    metrics: dict[str, dict] = {}
    for spec in OPS_V1_DEFINITIONS:
        code = spec["code"]
        definition = definitions[code]
        if spec["aggregation_rule"] == "derived_authorized_building_count":
            metrics[code] = {
                "code": code,
                "name": definition.name,
                "value": authorized_count,
                "numerator": authorized_count,
                "denominator": None,
                "unit": definition.unit,
                "formula": definition.business_definition,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "as_of_date": as_of_date.isoformat(),
                "definition_version": "OPS_V1",
                "definition_hash": definition.definition_hash,
                "calculation_version": CALCULATION_VERSION,
                "coverage": {
                    "portfolio_buildings": portfolio_count,
                    "authorized_buildings": authorized_count,
                    "presentation_enabled_buildings": presentation_enabled_count,
                    "contribution_enabled_buildings": gate_contribution_enabled_count,
                    "eligible_buildings": authorized_count,
                    "included_buildings": authorized_count,
                    "covered_area_sqm": None,
                    "known_total_area_sqm": None,
                    "covered_days": None,
                    "eligible_days": None,
                    "source_mix": ["property_scope"],
                    "source_ids": [],
                    "batch_ids": [],
                    "source_record_ids": [],
                    "data_depth": "master_data",
                    "completeness_status": "complete",
                    "freshness_status": "current",
                    "validation_status": "valid",
                    "publication_status": "published",
                    "estimate_share": 0,
                    "excluded_buildings": [],
                },
                "unavailable_reason": None,
            }
            continue

        available = fact_by_code_building.get(code, {})
        eligible: list[tuple[int, BuildingMetricFact, DataSource, ImportBatch | None, SourceRecord | None]] = []
        included: list[tuple[int, BuildingMetricFact, DataSource, ImportBatch | None, SourceRecord | None]] = []
        excluded: list[dict] = []
        for building_id in building_ids:
            row = available.get(building_id)
            fact = row[0] if row is not None else None
            decision = gate_by_building_id[building_id]
            aggregation_enabled = building_id in aggregation_enabled_ids
            if fact is not None and aggregation_enabled:
                eligible.append((building_id, *row))
            adapter_unavailable_reason = normalized_adapter_unavailable_reasons.get(building_id)
            reason = (
                adapter_unavailable_reason
                or (_excluded_reason(fact) if aggregation_enabled else _gate_excluded_reason(decision))
            )
            if reason is None and row is not None:
                included.append((building_id, *row))
            else:
                excluded.append(
                    {
                        "building_code": building_by_id[building_id].code,
                        "reason": reason,
                    }
                )
        numerators = [Decimal(str(item[1].numerator)) for item in included if item[1].numerator is not None]
        denominators = [Decimal(str(item[1].denominator)) for item in included if item[1].denominator is not None]
        aggregation = spec["aggregation_rule"]
        value: Decimal | None = None
        numerator: Decimal | None = None
        denominator: Decimal | None = None
        unavailable_reason = None
        if included:
            if len(numerators) != len(included):
                raise HTTPException(409, f"已发布指标 {code} 缺少原始分子")
            numerator = sum(numerators, Decimal(0))
            if aggregation in {"ratio", "ratio_percent"}:
                if len(denominators) != len(included):
                    raise HTTPException(409, f"已发布指标 {code} 缺少原始分母")
                denominator = sum(denominators, Decimal(0))
                if denominator > 0:
                    value = numerator / denominator
                    if aggregation == "ratio_percent":
                        value *= Decimal(100)
                else:
                    unavailable_reason = "纳入楼宇的合计分母为 0，指标不可计算"
            else:
                value = numerator
        else:
            unavailable_reason = _combined_reason(
                *normalized_adapter_unavailable_reasons.values()
            ) if normalized_adapter_unavailable_reasons and not adapter_enabled_ids else (
                "当前开闸策略下没有楼宇允许参与板块合计"
                if contribution_enabled_count == 0
                else "授权范围内没有同日、有效且未过期的已发布指标"
            )

        covered_area = [Decimal(str(item[1].covered_area_sqm)) for item in included if item[1].covered_area_sqm is not None]
        eligible_area = [Decimal(str(item[1].eligible_area_sqm)) for item in eligible if item[1].eligible_area_sqm is not None]
        covered_days = sum(int(item[1].covered_days or 0) for item in included)
        eligible_days = sum(int(item[1].eligible_days or 0) for item in eligible)
        source_types = sorted({item[2].source_type for item in included})
        source_ids = sorted({int(item[2].id) for item in included})
        batch_ids = sorted({int(item[3].id) for item in included if item[3] is not None})
        record_ids = sorted({int(item[4].id) for item in included if item[4] is not None})
        depths = sorted({item[1].data_depth for item in included})
        estimate_weights = []
        for _building_id, fact, _source, _batch, _record in included:
            share = Decimal(str(fact.estimate_share or 0))
            weight = Decimal(str(fact.numerator or 0))
            estimate_weights.append((share, weight))
        total_weight = sum((weight for _share, weight in estimate_weights), Decimal(0))
        estimate_share = (
            sum((share * weight for share, weight in estimate_weights), Decimal(0)) / total_weight
            if total_weight > 0
            else max((share for share, _weight in estimate_weights), default=Decimal(0))
        )
        completeness = (
            "complete" if len(included) == authorized_count
            else "partial" if included
            else "missing"
        )
        metrics[code] = {
            "code": code,
            "name": definition.name,
            "value": _float(value),
            "numerator": _float(numerator),
            "denominator": _float(denominator),
            "unit": definition.unit,
            "formula": definition.business_definition,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "definition_version": "OPS_V1",
            "definition_hash": definition.definition_hash,
            "calculation_version": CALCULATION_VERSION,
            "coverage": {
                "portfolio_buildings": portfolio_count,
                "authorized_buildings": authorized_count,
                "presentation_enabled_buildings": presentation_enabled_count,
                "contribution_enabled_buildings": gate_contribution_enabled_count,
                "eligible_buildings": len(eligible),
                "included_buildings": len(included),
                "covered_area_sqm": _float(sum(covered_area, Decimal(0))) if covered_area else None,
                "known_total_area_sqm": _float(sum(eligible_area, Decimal(0))) if eligible_area else None,
                "covered_days": covered_days if eligible_days else None,
                "eligible_days": eligible_days if eligible_days else None,
                "source_mix": source_types,
                "source_ids": source_ids,
                "batch_ids": batch_ids,
                "source_record_ids": record_ids,
                "data_depth": depths[0] if len(depths) == 1 else "mixed" if depths else "unknown",
                "completeness_status": completeness,
                "freshness_status": "current" if included else "stale" if eligible else "unknown",
                "validation_status": "valid" if included else "pending",
                "publication_status": "published" if included else "draft",
                "estimate_share": _float(estimate_share),
                "excluded_buildings": excluded,
            },
            "unavailable_reason": unavailable_reason,
        }

    _merge_detail_adapter_metrics(
        metrics,
        detail_snapshots={
            building_id: snapshot
            for building_id, snapshot in normalized_detail_snapshots.items()
            if building_id in aggregation_enabled_ids
        },
        building_by_id=building_by_id,
        authorized_count=authorized_count,
    )

    contributions = []
    for building_id in building_ids:
        building = building_by_id[building_id]
        gate_decision = gate_by_building_id[building_id]
        contribution_enabled = bool(gate_decision.contribution_enabled)
        aggregation_enabled = building_id in aggregation_enabled_ids
        presentation_enabled = bool(gate_decision.presentation_enabled)
        gate_excluded_reason = (
            _gate_excluded_reason(gate_decision)
            if scope.scope_type == "portfolio" and not contribution_enabled
            else None
        )
        adapter_unavailable_reason = normalized_adapter_unavailable_reasons.get(building_id)
        aggregation_excluded_reason = _combined_reason(
            adapter_unavailable_reason,
            gate_excluded_reason,
        )
        snapshot = snapshot_by_building.get(building_id)
        detail_snapshot = normalized_detail_snapshots.get(building_id)
        operating_gate = _gate_decision_payload(gate_decision, building=building)
        contribution_metrics: dict[str, dict] = {}
        for spec in OPS_V1_DEFINITIONS:
            if spec["aggregation_rule"] == "derived_authorized_building_count":
                continue
            code = spec["code"]
            if detail_snapshot is not None:
                detail_metric = (detail_snapshot.get("metrics") or {}).get(code)
                if detail_metric is not None:
                    contribution_metrics[code] = {
                        "value": detail_metric.get("value"),
                        "numerator": detail_metric.get("numerator"),
                        "denominator": detail_metric.get("denominator"),
                        "calculation_version": detail_metric.get("calculation_version"),
                        "coverage": detail_metric.get("coverage") or {},
                        "unavailable_reason": detail_metric.get("unavailable_reason"),
                        "aggregation_eligible": aggregation_enabled,
                        "aggregation_excluded_reason": aggregation_excluded_reason,
                    }
                continue
            row = fact_by_code_building.get(code, {}).get(building_id)
            if row is not None:
                fact = row[0]
                contribution_metrics[code] = {
                    "value": _float(fact.value),
                    "numerator": _float(fact.numerator),
                    "denominator": _float(fact.denominator),
                    "calculation_version": fact.calculation_version,
                    "coverage": {
                        "eligible_buildings": fact.eligible_buildings,
                        "included_buildings": fact.included_buildings,
                        "covered_area_sqm": _float(fact.covered_area_sqm),
                        "known_total_area_sqm": _float(fact.eligible_area_sqm),
                        "covered_days": fact.covered_days,
                        "eligible_days": fact.eligible_days,
                        "source_ids": [fact.source_id],
                        "batch_ids": [fact.batch_id] if fact.batch_id is not None else [],
                        "source_record_ids": (
                            [fact.source_record_id] if fact.source_record_id is not None else []
                        ),
                        "data_depth": fact.data_depth,
                        "completeness_status": fact.completeness_status,
                        "freshness_status": fact.freshness_status,
                        "validation_status": fact.validation_status,
                        "publication_status": fact.publication_status,
                        "estimate_share": _float(fact.estimate_share),
                    },
                    "unavailable_reason": _excluded_reason(fact),
                    "aggregation_eligible": aggregation_enabled,
                    "aggregation_excluded_reason": aggregation_excluded_reason,
                }
        contributions.append(
            {
                "building": {"id": building.id, "code": building.code, "name": building.name},
                "snapshot_id": snapshot.id if snapshot else None,
                "revision": snapshot.revision if snapshot else None,
                "publication_status": (
                    "adapter_unavailable" if adapter_unavailable_reason
                    else "live_adapter" if detail_snapshot is not None
                    else snapshot.publication_status if snapshot else "missing"
                ),
                "published_at": snapshot.published_at.isoformat() if snapshot and snapshot.published_at else None,
                "reason": _combined_reason(
                    adapter_unavailable_reason,
                    gate_excluded_reason if not adapter_unavailable_reason else None,
                    f"当天{building.name}明细按 OPS_V1 实时适配"
                    if detail_snapshot is not None
                    else None if snapshot
                    else "该统计日没有当前已发布快照",
                ),
                "presentation_enabled": presentation_enabled,
                "contribution_enabled": contribution_enabled,
                "gate_state": _gate_state_value(gate_decision),
                "operating_gate": operating_gate,
                "gate": operating_gate,
                "data_classification": "simulated",
                "metrics": contribution_metrics,
            }
        )

    presented_contributions = [
        item for item in contributions if bool(item.get("presentation_enabled"))
    ]
    legacy_stats, legacy_operations, presentation = _compatibility_projections(
        metrics=metrics,
        as_of_date=as_of_date,
        contributions=presented_contributions,
    )
    presentation["data_classification"] = "simulated"
    presentation["gate_policy_version"] = gate_resolution.policy_version
    portfolio = db.get(Portfolio, scope.portfolio_id) if scope.portfolio_id is not None else None
    display_name = (
        portfolio.name
        if portfolio is not None
        else buildings[0].name if len(buildings) == 1
        else scope.scope_code
    )
    sources_payload = [
        {
            "id": source.id,
            "building_id": source.building_id,
            "name": source.name,
            "source_type": source.source_type,
            "cadence": source.cadence,
            "data_classification": "simulated",
        }
        for source in sorted(all_sources.values(), key=lambda item: item.id)
    ]
    detail_source_building_by_id: dict[object, int] = {}
    for building_id, detail_snapshot in normalized_detail_snapshots.items():
        building = building_by_id[building_id]
        for source_payload in _detail_source_payloads(detail_snapshot, building=building):
            source_id = source_payload["id"]
            previous_building_id = detail_source_building_by_id.setdefault(
                source_id,
                building_id,
            )
            if previous_building_id != building_id:
                raise HTTPException(409, "不同明细适配器返回了重复来源标识")
            if not any(item.get("id") == source_id for item in sources_payload):
                sources_payload.append(source_payload)
    rollout_decisions = [
        _gate_decision_payload(
            gate_by_building_id[building_id],
            building=building_by_id[building_id],
        )
        for building_id in building_ids
    ]
    adapters_payload = []
    for building_id in normalized_detail_snapshots:
        decision = gate_by_building_id[building_id]
        aggregation_enabled = building_id in aggregation_enabled_ids
        adapters_payload.append(
            {
                "building": {
                    "id": building_id,
                    "code": building_by_id[building_id].code,
                    "name": building_by_id[building_id].name,
                },
                "adapter_type": "live_detail",
                "included": aggregation_enabled,
                "reason": (
                    f"当天{building_by_id[building_id].name}明细按 OPS_V1 实时适配"
                    if aggregation_enabled
                    else _gate_excluded_reason(decision)
                ),
                "operating_gate": _gate_decision_payload(
                    decision,
                    building=building_by_id[building_id],
                ),
                "data_classification": "simulated",
            }
        )

    return {
        "contract_version": "operating-snapshot-v1",
        "definition_version": "OPS_V1",
        "definition_status": "published",
        "data_classification": "simulated",
        "gate_policy_version": gate_resolution.policy_version,
        "as_of_date": as_of_date.isoformat(),
        "period": {
            "month": as_of_date.strftime("%Y-%m"),
            "month_start": period_start.isoformat(),
            "month_end": period_end.isoformat(),
        },
        "scope": {
            "scope_type": scope.scope_type,
            "scope_code": scope.scope_code,
            "display_name": display_name,
            "building_ids": list(building_ids),
            "building_codes": [building_by_id[item].code for item in building_ids],
        },
        "coverage": {
            "portfolio_buildings": portfolio_count,
            "authorized_buildings": authorized_count,
            "presentation_enabled_buildings": presentation_enabled_count,
            "contribution_enabled_buildings": gate_contribution_enabled_count,
            "published_same_day_buildings": len(snapshot_by_building),
            "live_adapter_buildings": len(normalized_detail_snapshots),
            "same_day_snapshot_buildings": len(snapshot_by_building) + len(normalized_detail_snapshots),
            "included_same_day_buildings": len(snapshot_by_building) + len(normalized_detail_snapshots),
        },
        "timestamps": {
            "source_observed_at": _max_iso(observed_times),
            "source_updated_at": _max_iso(updated_times),
            "received_at": _max_iso(received_times),
            "ingested_at": _max_iso(ingested_times),
            "published_at": _max_iso(published_times),
        },
        "sources": sources_payload,
        "metrics": metrics,
        "contributions": contributions,
        "rollout": {
            "policy_version": gate_resolution.policy_version,
            "evaluated_at": gate_resolution.evaluated_at.isoformat(),
            "data_classification": "simulated",
            "authorized_buildings": authorized_count,
            "presentation_enabled_buildings": presentation_enabled_count,
            "contribution_enabled_buildings": gate_contribution_enabled_count,
            "items": rollout_decisions,
            "decisions": rollout_decisions,
        },
        "adapters": adapters_payload,
        "legacy_contract_stats": legacy_stats,
        "legacy_operations": legacy_operations,
        "presentation": presentation,
    }
