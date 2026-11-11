from __future__ import annotations

import calendar
from datetime import date
from typing import Protocol, Sequence

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.services.calculation_context import reuse_read_calculations
from app.core.time import now_in_timezone, today_in_timezone
from app.models import Building, Room, Zone
from app.ops_v1 import OPS_V1_BY_CODE, definition_hash
from app.services.building_operations_profile import (
    BuildingOperationsProfile,
    DataAdapterType,
    resolve_building_operations_profile,
)
from app.services.contracts import get_contract_stats, get_contract_summary
from app.services.dashboard import get_operations_dashboard
from app.services.rent_analytics import SEGMENT_KEYS, build_snapshot_rent_projection
from app.services.rooms import list_rooms
from app.services.towers import operations_group_codes


SNAPSHOT_CONTRACT_VERSION = "operating-snapshot-v1"
DEFINITION_VERSION = "OPS_V1"
CALCULATION_VERSION = "operating-snapshot-v1"


class BuildingScope(Protocol):
    building_ids: Sequence[int]


def _single_building_id(scope: BuildingScope) -> int:
    raw_ids = getattr(scope, "building_ids", None)
    if raw_ids is None:
        raise HTTPException(422, "统一经营快照需要明确的单楼范围")
    try:
        building_ids = tuple(raw_ids)
    except TypeError as exc:
        raise HTTPException(422, "统一经营快照的楼宇范围格式无效") from exc
    if len(building_ids) != 1 or isinstance(building_ids[0], bool):
        raise HTTPException(422, "当前统一经营快照仅支持单楼范围")
    try:
        return int(building_ids[0])
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "统一经营快照的楼宇标识无效") from exc


def _require_supported_building(
    db: Session,
    building_id: int,
) -> tuple[Building, BuildingOperationsProfile]:
    building = db.get(Building, building_id)
    if building is None or not building.is_active:
        raise HTTPException(422, "统一经营快照的楼宇不存在或未启用")
    profile = resolve_building_operations_profile(building)
    if profile.data_adapter is not DataAdapterType.LIVE_DETAIL:
        reason = profile.adapter_unavailable_reason or "当前楼宇未启用明细数据 Adapter"
        raise HTTPException(422, f"当前楼宇不能使用明细经营快照：{reason}")
    return building, profile


def _detail_source(building: Building, profile: BuildingOperationsProfile) -> dict:
    source_code = str(building.code or "").strip().casefold().replace("_", "-")
    return {
        "id": f"{source_code}-detail-live",
        "name": f"{profile.brand.short_name} 现有租赁明细库",
        "source_type": "detail",
        "source_observed_at": None,
        "source_updated_at": None,
    }


def _ratio(numerator: float, denominator: float, reason: str) -> tuple[float | None, str | None]:
    if denominator <= 0:
        return None, reason
    return round(numerator / denominator * 100, 1), None


def _coverage(
    *,
    available: bool,
    source_id: str,
    covered_area_sqm: float | None = None,
    known_total_area_sqm: float | None = None,
    covered_days: int | None = 1,
    eligible_days: int | None = 1,
    completeness_status: str | None = None,
) -> dict:
    return {
        "portfolio_buildings": 1,
        "authorized_buildings": 1,
        "eligible_buildings": 1 if available else 0,
        "included_buildings": 1 if available else 0,
        "covered_area_sqm": covered_area_sqm,
        "known_total_area_sqm": known_total_area_sqm,
        "covered_days": covered_days,
        "eligible_days": eligible_days,
        "source_mix": ["detail"] if available else [],
        "source_ids": [source_id] if available else [],
        "data_depth": "detail",
        "completeness_status": completeness_status or ("complete" if available else "missing"),
        # The legacy detail store has no unified business-observation timestamp/SLA.
        # Calculation time must not be presented as source freshness.
        "freshness_status": "unknown",
        "validation_status": "valid" if available else "pending",
        "publication_status": "draft",
    }


def _metric(
    *,
    code: str,
    name: str,
    value: float | int | None,
    numerator: float | int | None,
    denominator: float | int | None,
    unit: str,
    formula: str,
    period_start: date,
    period_end: date,
    as_of_date: date,
    source_id: str,
    unavailable_reason: str | None = None,
    coverage: dict | None = None,
) -> dict:
    available = unavailable_reason is None
    return {
        "code": code,
        "name": name,
        "value": value if available else None,
        "numerator": numerator,
        "denominator": denominator,
        "unit": unit,
        "formula": formula,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "definition_version": DEFINITION_VERSION,
        "definition_status": "published",
        "definition_hash": definition_hash(OPS_V1_BY_CODE[code]),
        "calculation_version": CALCULATION_VERSION,
        "coverage": coverage or _coverage(available=available, source_id=source_id),
        "unavailable_reason": unavailable_reason,
    }


def _presentation_towers(
    db: Session,
    building_ids: tuple[int, ...],
    profile: BuildingOperationsProfile,
) -> list[dict]:
    available_groups = tuple(item for item in profile.dashboard_groups if item.available)
    non_whole_groups = tuple(
        item for item in available_groups if item.code.strip().upper() != "WHOLE_BUILDING"
    )
    display_groups = non_whole_groups or tuple(
        item for item in available_groups if item.code.strip().upper() == "WHOLE_BUILDING"
    )
    if not display_groups:
        return []

    room_zone_ids = {
        int(room_id): int(zone_id)
        for room_id, zone_id in db.query(Room.id, Room.zone_id)
        .filter(Room.building_id.in_(building_ids))
        .all()
    }
    zone_codes = {
        int(zone_id): str(code)
        for zone_id, code in db.query(Zone.id, Zone.code)
        .filter(Zone.building_id.in_(building_ids))
        .all()
    }
    display_group_codes = {item.code for item in display_groups}
    summary: dict[str, dict[str, float]] = {}
    for room in list_rooms(db, include_operations=False, building_ids=building_ids):
        group_codes = operations_group_codes(
            available_groups,
            zone_code=zone_codes.get(room_zone_ids.get(int(room["id"]))),
            tower=room.get("tower"),
            floor=room.get("floor"),
            room_type=room.get("room_type"),
            room_no=room.get("room_no"),
        )
        area = float(room.get("area") or 0)
        for group_code in group_codes:
            if group_code not in display_group_codes:
                continue
            entry = summary.setdefault(group_code, {"total": 0.0, "leased": 0.0})
            entry["total"] += area
            if room.get("occupied"):
                entry["leased"] += area
    rows = []
    for group in display_groups:
        if group.code not in summary:
            continue
        total = summary[group.code]["total"]
        leased = summary[group.code]["leased"]
        rows.append(
            {
                "name": group.name,
                "total": round_money(total),
                "leased": round_money(leased),
                "rate": round(leased / total * 100, 10) if total > 0 else None,
            }
        )
    return rows


def _presentation_price(rent_analytics: dict) -> dict:
    segment_definitions = tuple(rent_analytics.get("segments") or [])
    segment_keys = tuple(item["key"] for item in segment_definitions)
    trend_segments = tuple(item for item in segment_keys if item != "overall") or segment_keys
    daily = rent_analytics.get("daily") or []
    last = daily[-1] if daily else {}
    current = {
        key: (last.get("segments") or {}).get(key, {}).get("effective_daily_rent")
        for key in segment_keys
    }
    buckets: dict[str, dict[str, dict[str, float]]] = {}
    for row in daily:
        month = str(row.get("date") or "")[:7]
        if not month:
            continue
        month_bucket = buckets.setdefault(month, {})
        for key in trend_segments:
            segment = (row.get("segments") or {}).get(key)
            if segment is None:
                continue
            bucket = month_bucket.setdefault(key, {"rent": 0.0, "area": 0.0})
            bucket["rent"] += float(segment.get("rent") or 0)
            bucket["area"] += float(segment.get("area") or 0)
    months = sorted(buckets)
    by_segment = {
        key: [
            (
                round(buckets[month][key]["rent"] / buckets[month][key]["area"], 2)
                if key in buckets[month] and buckets[month][key]["area"] > 0
                else None
            )
            for month in months
        ]
        for key in trend_segments
    }
    return {
        "segments": list(segment_definitions),
        "current_by_segment": current,
        "average_unit_price": current.get("overall"),
        "months": months,
        "trend_by_segment": by_segment,
        "coverage": rent_analytics.get("coverage") or {},
    }


@reuse_read_calculations
def build_operating_snapshot(
    db: Session,
    *,
    scope: BuildingScope,
    as_of_date: date | None = None,
    include_trend: bool = True,
    queue_limit: int = 0,
    risk_limit: int = 8,
    _contract_summary_only: bool = False,
) -> dict:
    request_today = today_in_timezone()
    effective_date = as_of_date or request_today
    if effective_date != request_today:
        raise HTTPException(422, "现有明细引擎仅支持当天快照，不能可靠回算指定历史日期")

    building_id = _single_building_id(scope)
    building, profile = _require_supported_building(db, building_id)
    building_ids = (building.id,)
    source = _detail_source(building, profile)
    source_id = source["id"]

    # Portfolio aggregation consumes only contract presence and rebuilds its
    # compatibility statistics. Direct single-building reads keep full detail.
    contract_stats_reader = get_contract_summary if _contract_summary_only else get_contract_stats
    legacy_contract_stats = contract_stats_reader(db, building_ids=building_ids)
    legacy_operations = get_operations_dashboard(
        db,
        include_trend=include_trend,
        queue_limit=queue_limit,
        risk_limit=risk_limit,
        building_ids=building_ids,
    )
    price_start = (effective_date + relativedelta(months=-6)).replace(day=1)
    rent_analytics = build_snapshot_rent_projection(
        db,
        start_date=price_start,
        end_date=effective_date,
        segments=list(SEGMENT_KEYS),
        building_ids=building_ids,
    )

    month_start = effective_date.replace(day=1)
    month_end = effective_date.replace(day=calendar.monthrange(effective_date.year, effective_date.month)[1])
    elapsed_days = (effective_date - month_start).days + 1
    month_days = (month_end - month_start).days + 1

    vacancy = legacy_operations.get("vacancy") or {}
    total_area = float(vacancy.get("total_area") or 0)
    has_room_inventory = int(vacancy.get("total_rooms") or 0) > 0
    has_verifiable_room_area = total_area > 0
    is_legacy_demo_a = str(building.code or "").strip().casefold() == "demo_a"
    has_any_detail = (
        is_legacy_demo_a
        or has_room_inventory
        or int(legacy_contract_stats.get("total") or 0) > 0
    )
    room_inventory_reason = (
        None
        if has_verifiable_room_area
        else "当前楼宇房源面积均未登记，面积类指标暂不可用"
        if has_room_inventory
        else "当前楼宇尚无可核验的房源面积明细"
    )
    detail_inventory_reason = None if has_any_detail else "当前楼宇明细源为空，经营指标暂不可用"
    detail_dashboard_available = is_legacy_demo_a or has_verifiable_room_area
    detail_dashboard_reason = (
        None
        if detail_dashboard_available
        else room_inventory_reason or detail_inventory_reason
    )
    vacancy_area = float(vacancy.get("area") or 0)
    leased_area = round_money(max(total_area - vacancy_area, 0))
    occupancy_rate, occupancy_reason = _ratio(
        leased_area,
        total_area,
        "可出租面积分母为 0，面积出租率不可计算",
    )
    if room_inventory_reason is not None:
        occupancy_reason = room_inventory_reason

    current_month = legacy_operations.get("current_month") or {}
    receivable = float(current_month.get("receivable") or 0)
    received = float(current_month.get("received") or 0)
    collection_rate, collection_reason = _ratio(
        received,
        receivable,
        "本月应收分母为 0，收缴率不可计算",
    )
    if detail_inventory_reason is not None:
        collection_reason = detail_inventory_reason

    today_row = next(
        (row for row in rent_analytics.get("daily") or [] if row.get("date") == effective_date.isoformat()),
        None,
    )
    rent_overall = ((today_row or {}).get("segments") or {}).get("overall") or {}
    rent_amount = rent_overall.get("rent")
    rent_area = rent_overall.get("area")
    effective_daily = rent_overall.get("effective_daily_rent")
    rent_reason = None
    if rent_area is None or float(rent_area or 0) <= 0 or effective_daily is None:
        rent_reason = "当天没有可用的在租面积日分母，有效平均日租不可计算"

    metrics = {
        "area.rentable_sqm": _metric(
            code="area.rentable_sqm",
            name="可出租面积",
            value=round_money(total_area) if has_verifiable_room_area else None,
            numerator=round_money(total_area) if has_verifiable_room_area else None,
            denominator=None,
            unit="sqm",
            formula="可出租房源面积合计",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=room_inventory_reason,
            coverage=_coverage(
                available=has_verifiable_room_area,
                source_id=source_id,
                covered_area_sqm=round_money(total_area),
                known_total_area_sqm=round_money(total_area),
            ),
        ),
        "area.leased_sqm": _metric(
            code="area.leased_sqm",
            name="在租面积",
            value=leased_area if has_verifiable_room_area else None,
            numerator=leased_area if has_verifiable_room_area else None,
            denominator=None,
            unit="sqm",
            formula="统计日有效占用房源面积去重合计",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=room_inventory_reason,
            coverage=_coverage(
                available=has_verifiable_room_area,
                source_id=source_id,
                covered_area_sqm=round_money(total_area),
                known_total_area_sqm=round_money(total_area),
            ),
        ),
        "occupancy.area_rate": _metric(
            code="occupancy.area_rate",
            name="面积出租率",
            value=occupancy_rate,
            numerator=leased_area,
            denominator=round_money(total_area),
            unit="percent",
            formula="在租面积 / 可出租面积",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=occupancy_reason,
            coverage=_coverage(
                available=occupancy_reason is None,
                source_id=source_id,
                covered_area_sqm=round_money(total_area),
                known_total_area_sqm=round_money(total_area),
            ),
        ),
        "vacancy.area_sqm": _metric(
            code="vacancy.area_sqm",
            name="空置面积",
            value=round_money(vacancy_area) if has_verifiable_room_area else None,
            numerator=round_money(vacancy_area) if has_verifiable_room_area else None,
            denominator=None,
            unit="sqm",
            formula="可出租面积 - 在租面积",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=room_inventory_reason,
        ),
        "billing.receivable_month": _metric(
            code="billing.receivable_month",
            name="本月应收",
            value=round_money(receivable) if has_any_detail else None,
            numerator=round_money(receivable) if has_any_detail else None,
            denominator=None,
            unit="CNY",
            formula="固化账单优先，未固化月份按合同条款计算",
            period_start=month_start,
            period_end=month_end,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=detail_inventory_reason,
            coverage=_coverage(
                available=has_any_detail,
                source_id=source_id,
                covered_days=elapsed_days,
                eligible_days=month_days,
                completeness_status=(
                    "missing"
                    if not has_any_detail
                    else "complete" if elapsed_days == month_days
                    else "partial"
                ),
            ),
        ),
        "collection.received_month": _metric(
            code="collection.received_month",
            name="本月已收",
            value=round_money(received) if has_any_detail else None,
            numerator=round_money(received) if has_any_detail else None,
            denominator=None,
            unit="CNY",
            formula="归属本月应收的有效回款净额",
            period_start=month_start,
            period_end=month_end,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=detail_inventory_reason,
            coverage=_coverage(
                available=has_any_detail,
                source_id=source_id,
                covered_days=elapsed_days,
                eligible_days=month_days,
                completeness_status=(
                    "missing"
                    if not has_any_detail
                    else "complete" if elapsed_days == month_days
                    else "partial"
                ),
            ),
        ),
        "collection.rate_month": _metric(
            code="collection.rate_month",
            name="本月收缴率",
            value=collection_rate,
            numerator=round_money(received),
            denominator=round_money(receivable),
            unit="percent",
            formula="本月已收 / 本月应收",
            period_start=month_start,
            period_end=month_end,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=collection_reason,
            coverage=_coverage(
                available=collection_reason is None,
                source_id=source_id,
                covered_days=elapsed_days,
                eligible_days=month_days,
                completeness_status=(
                    "complete" if collection_reason is None and elapsed_days == month_days
                    else "partial" if collection_reason is None
                    else "missing"
                ),
            ),
        ),
        "arrears.balance": _metric(
            code="arrears.balance",
            name="逾期余额",
            value=(
                legacy_operations.get("metrics", {}).get("overdue_balance", 0)
                if has_any_detail
                else None
            ),
            numerator=(
                legacy_operations.get("metrics", {}).get("overdue_balance", 0)
                if has_any_detail
                else None
            ),
            denominator=None,
            unit="CNY",
            formula="到期日早于统计日且尚未结清的有效应收余额",
            period_start=month_start,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=detail_inventory_reason,
        ),
        "rent.effective_daily": _metric(
            code="rent.effective_daily",
            name="有效平均日租",
            value=effective_daily,
            numerator=rent_amount,
            denominator=rent_area,
            unit="CNY/sqm/day",
            formula="当天有效租金 / 当天在租面积日",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=rent_reason,
            coverage=_coverage(
                available=rent_reason is None,
                source_id=source_id,
                covered_area_sqm=float(rent_area or 0),
                known_total_area_sqm=round_money(total_area),
            ),
        ),
        "expiry.contract_count_90d": _metric(
            code="expiry.contract_count_90d",
            name="90 天内到期合同数",
            value=(
                legacy_operations.get("metrics", {}).get("expiring_contract_count_90", 0)
                if has_any_detail
                else None
            ),
            numerator=(
                legacy_operations.get("metrics", {}).get("expiring_contract_count_90", 0)
                if has_any_detail
                else None
            ),
            denominator=None,
            unit="contract",
            formula="统计日起 0 至 90 天内到期的当前有效合同数",
            period_start=effective_date,
            period_end=effective_date + relativedelta(days=90),
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=detail_inventory_reason,
        ),
        "expiry.area_pressure_90d": _metric(
            code="expiry.area_pressure_90d",
            name="90 天到期面积压力",
            value=None,
            numerator=None,
            denominator=leased_area,
            unit="percent",
            formula="90 天内到期在租面积 / 当前在租面积",
            period_start=effective_date,
            period_end=effective_date + relativedelta(days=90),
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason="现有引擎仅提供到期合同份数，尚无去重到期面积分子",
            coverage=_coverage(
                available=False,
                source_id=source_id,
                covered_area_sqm=None,
                known_total_area_sqm=round_money(total_area),
            ),
        ),
        "risk.open_items": _metric(
            code="risk.open_items",
            name="未关闭风险事项",
            value=(
                legacy_operations.get("metrics", {}).get("risk_count", 0)
                if has_any_detail
                else None
            ),
            numerator=(
                legacy_operations.get("metrics", {}).get("risk_count", 0)
                if has_any_detail
                else None
            ),
            denominator=None,
            unit="item",
            formula="统一风险规则产生且仍为 open 的事项数",
            period_start=effective_date,
            period_end=effective_date,
            as_of_date=effective_date,
            source_id=source_id,
            unavailable_reason=detail_inventory_reason,
        ),
    }

    calculated_at = now_in_timezone().isoformat()
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "definition_version": DEFINITION_VERSION,
        "definition_status": "published",
        "as_of_date": effective_date.isoformat(),
        "period": {
            "month": effective_date.strftime("%Y-%m"),
            "month_start": month_start.isoformat(),
            "month_end": month_end.isoformat(),
        },
        "scope": {
            "scope_type": "building",
            "building_ids": [building.id],
            "building": {
                "id": building.id,
                "code": building.code,
                "name": building.name,
                "timezone": building.timezone,
                "currency": building.currency,
                "ingestion_mode": building.ingestion_mode,
            },
        },
        "timestamps": {
            "source_observed_at": None,
            "source_updated_at": None,
            "received_at": None,
            "ingested_at": None,
            "published_at": None,
            "calculated_at": calculated_at,
            "reason": "现有明细库未记录统一来源观测时间，不能用计算时间替代业务更新时间",
        },
        "sources": [source],
        "detail_source_state": {
            "dashboard_facts_available": detail_dashboard_available,
            "dashboard_facts_unavailable_reason": detail_dashboard_reason,
        },
        "metrics": metrics,
        "legacy_contract_stats": legacy_contract_stats,
        "legacy_operations": legacy_operations,
        "presentation": {
            "tower": _presentation_towers(db, building_ids, profile),
            "price": _presentation_price(rent_analytics),
            "trend": legacy_operations.get("trend") or [],
        },
    }
