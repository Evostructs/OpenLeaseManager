from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import io
import math
import re
from typing import Iterable, Mapping
from urllib.parse import quote

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from app.core.excel_safety import sanitize_workbook
from app.core.money import round_money
from app.services.calculation_context import reuse_read_calculations
from app.core.time import today_in_timezone
from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.building_operations_profile import (
    BuildingOperationsProfile,
    DataAdapterType,
    OperationsGroup,
    DEMO_A_PROFILE,
    resolve_building_operations_profile,
)
from app.services.common import contract_room_items, customer_display_name
from app.services.contract_rent import RENT_TERM_LABELS, _free_rent_end, rent_day_breakdown, rent_constant_periods
from app.services.towers import operations_group_codes


RENT_ANALYTICS_VERSION = "rent-analytics-v1"
MAX_ANALYTICS_YEARS = 50
MAX_ANALYTICS_DAYS = MAX_ANALYTICS_YEARS * 366
SEGMENT_DEFINITIONS = (
    {"key": "overall", "label": "整体", "color": "#1f6feb"},
    {"key": "south_office", "label": "南塔办公", "color": "#d97706"},
    {"key": "north_office", "label": "北塔办公", "color": "#0f766e"},
    {"key": "commercial", "label": "商业配套", "color": "#7c3aed"},
)
SEGMENT_KEYS = tuple(item["key"] for item in SEGMENT_DEFINITIONS)
GROUP_CODE_TO_SEGMENT = {
    "WHOLE_BUILDING": "overall",
    "SOUTH_OFFICE": "south_office",
    "NORTH_OFFICE": "north_office",
    "COMMERCIAL_SUPPORT": "commercial",
}
SEGMENT_COLORS = (
    "#1f6feb",
    "#d97706",
    "#0f766e",
    "#7c3aed",
    "#be185d",
    "#4d7c0f",
)


@dataclass(frozen=True)
class RentAnalyticsGrouping:
    groups: tuple[OperationsGroup, ...]
    definitions: tuple[dict, ...]
    segment_by_group_code: Mapping[str, str]

    @property
    def segment_keys(self) -> tuple[str, ...]:
        return tuple(item["key"] for item in self.definitions)


def _segment_key(group_code: str) -> str:
    normalized = str(group_code or "").strip().upper()
    if normalized in GROUP_CODE_TO_SEGMENT:
        return GROUP_CODE_TO_SEGMENT[normalized]
    candidate = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    return candidate or "group"


def _rent_grouping(profile: BuildingOperationsProfile) -> RentAnalyticsGrouping:
    groups = tuple(item for item in profile.analysis_groups if item.available)
    if not groups or not any(
        item.code.strip().upper() == "WHOLE_BUILDING" for item in groups
    ):
        raise HTTPException(422, "当前楼宇缺少可用的整楼经营分析分组")
    segment_by_group_code = {item.code: _segment_key(item.code) for item in groups}
    if len(segment_by_group_code) != len(set(segment_by_group_code.values())):
        raise HTTPException(422, "当前楼宇经营分析分组标识冲突")

    legacy_codes = (
        "WHOLE_BUILDING",
        "SOUTH_OFFICE",
        "NORTH_OFFICE",
        "COMMERCIAL_SUPPORT",
    )
    if tuple(item.code for item in groups) == legacy_codes:
        definitions = SEGMENT_DEFINITIONS
    else:
        definitions = tuple(
            {
                "key": segment_by_group_code[item.code],
                "label": item.name,
                "color": SEGMENT_COLORS[index % len(SEGMENT_COLORS)],
            }
            for index, item in enumerate(groups)
        )
    return RentAnalyticsGrouping(
        groups=groups,
        definitions=definitions,
        segment_by_group_code=segment_by_group_code,
    )


def _resolve_rent_grouping(
    db: Session,
    building_ids: tuple[int, ...] | None,
) -> RentAnalyticsGrouping:
    if building_ids is None or not building_ids:
        return _rent_grouping(DEMO_A_PROFILE)
    if len(building_ids) != 1:
        raise HTTPException(422, "租金时间分析仅支持明确的单楼范围")
    building = db.get(Building, building_ids[0])
    if building is None or not building.is_active:
        raise HTTPException(422, "租金时间分析的楼宇不存在或未启用")
    profile = resolve_building_operations_profile(building)
    if profile.data_adapter is not DataAdapterType.LIVE_DETAIL:
        reason = profile.adapter_unavailable_reason or "当前楼宇未启用明细数据 Adapter"
        raise HTTPException(422, f"当前楼宇不能使用租金时间分析：{reason}")
    return _rent_grouping(profile)


def parse_segments(value: str | None) -> list[str]:
    if not value:
        return list(SEGMENT_KEYS)
    requested = list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if not requested:
        raise HTTPException(422, "至少选择一个分析分区")
    return requested


def _number(value: object) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _overlaps(first_start: date | None, first_end: date | None, second_start: date, second_end: date) -> bool:
    return bool(first_start and first_end and first_start <= second_end and first_end >= second_start)


def _iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _empty_segments(grouping: RentAnalyticsGrouping) -> dict[str, dict]:
    return {
        key: {"rent": 0.0, "area": 0.0, "contract_count": 0}
        for key in grouping.segment_keys
    }


def _active_room_allocations(
    contract: Contract,
    target: date,
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
) -> list[dict]:
    return [
        item
        for item in _contract_allocation_profile(
            contract,
            rooms_by_id,
            rooms_by_no,
            zone_code_by_id,
            grouping,
        )
        if item["start_date"] <= target <= item["end_date"]
    ]


def _contract_allocation_profile(
    contract: Contract,
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
) -> list[dict]:
    allocations = []
    for item in contract_room_items(contract):
        area = _number(item.get("area"))
        item_start = item.get("start_date")
        item_end = item.get("end_date")
        if area <= 0 or not item_start or not item_end or item_end < item_start:
            continue
        room = rooms_by_id.get(item.get("room_id")) or rooms_by_no.get(item.get("room_no") or "")
        group_codes = operations_group_codes(
            grouping.groups,
            zone_code=zone_code_by_id.get(int(room.zone_id)) if room and room.zone_id else None,
            tower=item.get("tower") or contract.tower,
            floor=room.floor if room else None,
            room_type=room.room_type if room else None,
            room_no=item.get("room_no"),
        )
        segments = tuple(
            grouping.segment_by_group_code[code]
            for code in group_codes
            if grouping.segment_by_group_code.get(code) not in {None, "overall"}
        )
        allocations.append(
            {
                "contract_room_id": item.get("id"),
                "room_no": item.get("room_no") or "",
                "area": area,
                "monthly_rent": max(_number(item.get("monthly_rent")), 0),
                "start_date": item_start,
                "end_date": item_end,
                "segments": segments,
                "group_codes": group_codes,
            }
        )
    return allocations


def _allocate_component_rent(
    breakdown: dict,
    allocations: list[dict],
    grouping: RentAnalyticsGrouping,
) -> dict[str, float]:
    result = {key: 0.0 for key in grouping.segment_keys if key != "overall"}
    if not allocations:
        return result

    for component in breakdown.get("components") or []:
        amount = max(_number(component.get("rent")), 0)
        if amount <= 0:
            continue
        contract_room_id = component.get("contract_room_id")
        room_no = component.get("room_no") or ""
        matches = []
        if contract_room_id:
            matches = [item for item in allocations if item["contract_room_id"] == contract_room_id]
        elif room_no:
            matches = [item for item in allocations if item["room_no"] == room_no]
        if not matches:
            matches = allocations
        total_area = sum(item["area"] for item in matches)
        if total_area <= 0:
            continue
        for item in matches:
            for segment in item["segments"]:
                result[segment] += amount * item["area"] / total_area

    allocated = sum(result.values())
    net_rent = max(_number(breakdown.get("rent")), 0)
    if not (breakdown.get("components") or []) and net_rent > 0:
        total_area = sum(item["area"] for item in allocations)
        for item in allocations:
            for segment in item["segments"]:
                if total_area > 0:
                    result[segment] += net_rent * item["area"] / total_area
    elif allocations and abs(allocated - net_rent) > 0.000001:
        # Unscoped rounding residue follows active area so the three known
        # groups still reconcile to the overall numerator.
        known = [item for item in allocations if item["segments"]]
        known_area = sum(item["area"] for item in known)
        residue = net_rent - allocated
        if known_area > 0 and residue > 0:
            for item in known:
                for segment in item["segments"]:
                    result[segment] += residue * item["area"] / known_area
    return result


def _contract_day_contribution(
    contract: Contract,
    target: date,
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
    allocation_profile: list[dict] | None = None,
) -> dict[str, dict]:
    result = _empty_segments(grouping)
    if not contract.start_date or not contract.end_date or not (contract.start_date <= target <= contract.end_date):
        return result
    profile = allocation_profile or _contract_allocation_profile(
        contract,
        rooms_by_id,
        rooms_by_no,
        zone_code_by_id,
        grouping,
    )
    allocations = [
        item for item in profile if item["start_date"] <= target <= item["end_date"]
    ]
    total_area = sum(item["area"] for item in allocations)
    if total_area <= 0:
        return result

    if not getattr(contract, "rent_terms", None):
        month_days = calendar.monthrange(target.year, target.month)[1]
        free_end = _free_rent_end(contract)
        rent_free = bool(free_end and target <= free_end)
        for item in allocations:
            item_rent = 0.0 if rent_free else item["monthly_rent"] / month_days
            result["overall"]["rent"] += item_rent
            result["overall"]["area"] += item["area"]
            result["overall"]["contract_count"] = 1
            for segment in item["segments"]:
                result[segment]["rent"] += item_rent
                result[segment]["area"] += item["area"]
                result[segment]["contract_count"] = 1
        return result

    breakdown = rent_day_breakdown(contract, target)
    net_rent = max(_number(breakdown.get("rent")), 0)
    result["overall"]["rent"] = net_rent
    result["overall"]["area"] = total_area
    result["overall"]["contract_count"] = 1

    grouped_rent = _allocate_component_rent(breakdown, allocations, grouping)
    for item in allocations:
        for segment in item["segments"]:
            result[segment]["area"] += item["area"]
            result[segment]["contract_count"] = 1
    for segment, amount in grouped_rent.items():
        result[segment]["rent"] = amount
    return result


def _contract_has_rent_basis(contract: Contract, start: date, end: date) -> bool:
    for item in contract_room_items(contract):
        if _number(item.get("monthly_rent")) > 0:
            return True
        if _number(item.get("area")) > 0 and _number(item.get("unit_price")) > 0:
            return True
    for term in getattr(contract, "rent_terms", []) or []:
        if term.rule_type not in {"area_unit_price", "fixed_monthly", "stepped_rent"}:
            continue
        has_area = _number(term.area) > 0 or any(
            _number(item.get("area")) > 0 for item in contract_room_items(contract)
        )
        if term.rule_type == "fixed_monthly" and term.monthly_amount is not None:
            return _number(term.monthly_amount) >= 0
        if term.rule_type == "area_unit_price" and term.unit_price is not None and has_area:
            return _number(term.unit_price) >= 0
        if term.rule_type == "stepped_rent" and (
            (term.monthly_amount is not None and _number(term.monthly_amount) >= 0)
            or (term.unit_price is not None and _number(term.unit_price) >= 0 and has_area)
        ):
            return True
    if _number(contract.monthly_rent) > 0 or (
        _number(contract.area) > 0 and _number(contract.unit_price) > 0
    ):
        return True

    # Legacy numeric columns use 0 both for "not supplied" and for a real
    # zero-rent value. Only accept zero when an explicit rent-free window
    # proves that every active day in the requested overlap is intentionally
    # free; otherwise keep the contract in the exclusion list rather than
    # guessing.
    if contract.start_date and contract.end_date:
        overlap_start = max(contract.start_date, start)
        overlap_end = min(contract.end_date, end)
        free_end = _free_rent_end(contract)
        if overlap_start <= overlap_end and free_end and overlap_end <= free_end:
            return True
    return False


def _contract_issues(contract: Contract, start: date, end: date) -> list[str]:
    issues = []
    if not contract.start_date or not contract.end_date:
        issues.append("缺少合同起止日期")
        return issues
    if contract.end_date < contract.start_date:
        issues.append("合同结束日期早于起始日期")
        return issues
    active_items = contract_room_items(contract, start, end)
    if not any(_number(item.get("area")) > 0 for item in active_items):
        issues.append("所选期间缺少有效出租面积")
    if not _contract_has_rent_basis(contract, start, end):
        issues.append("缺少可计算的租金依据")
    if contract.disposal == "已退租" and contract.end_date > today_in_timezone():
        issues.append("已退租但未登记实际退租日期")
    return issues


def _normalize_building_ids(building_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if building_ids is None:
        return None
    return tuple(dict.fromkeys(int(item) for item in building_ids))


def _load_context(
    db: Session,
    building_ids: tuple[int, ...] | None = None,
) -> tuple[list[Contract], dict[int, Room], dict[str, Room], dict[int, str]]:
    contract_query = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items),
            selectinload(Contract.rent_terms),
        )
        .filter(Contract.is_deleted.is_(False))
    )
    room_query = db.query(Room)
    zone_query = db.query(Zone)
    if building_ids is not None:
        contract_query = contract_query.options(
            with_loader_criteria(
                ContractRoom,
                ContractRoom.building_id.in_(building_ids or (-1,)),
                include_aliases=True,
            )
        ).filter(Contract.building_id.in_(building_ids or (-1,)))
        contract_query = contract_query.populate_existing()
        room_query = room_query.filter(Room.building_id.in_(building_ids or (-1,)))
        zone_query = zone_query.filter(Zone.building_id.in_(building_ids or (-1,)))
    contracts = contract_query.order_by(Contract.id).all()
    rooms = room_query.order_by(Room.id).all()
    rooms_by_no = {}
    for room in sorted(rooms, key=lambda item: (bool(item.is_deleted), item.id)):
        if room.room_no and room.room_no not in rooms_by_no:
            rooms_by_no[room.room_no] = room
    return (
        contracts,
        {room.id: room for room in rooms},
        rooms_by_no,
        {int(zone.id): str(zone.code) for zone in zone_query.order_by(Zone.id).all()},
    )


def _resolve_bounds(
    contracts: list[Contract],
    requested_start: date | None,
    requested_end: date | None,
) -> tuple[dict, date, date]:
    today = today_in_timezone()
    valid_starts = [
        contract.start_date
        for contract in contracts
        if contract.start_date and contract.end_date and contract.end_date >= contract.start_date
    ]
    valid_ends = [
        contract.end_date
        for contract in contracts
        if contract.start_date and contract.end_date and contract.end_date >= contract.start_date
    ]
    source_min = min(valid_starts, default=today)
    source_max = max(valid_ends, default=today)
    trailing_start = today + relativedelta(years=-1) + timedelta(days=1)
    has_signed_future = any(end_date > today for end_date in valid_ends)
    future_end = today + relativedelta(years=1) if has_signed_future else today
    min_date = min(source_min, trailing_start)
    max_date = max(source_max + timedelta(days=1), future_end, today)
    start = requested_start or min_date
    end = requested_end or max_date
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if (end - start).days + 1 > MAX_ANALYTICS_DAYS:
        raise HTTPException(422, f"时间范围不能超过 {MAX_ANALYTICS_YEARS} 年")
    default_start = trailing_start
    return (
        {
            "min_date": min_date.isoformat(),
            "max_date": max_date.isoformat(),
            "source_min_date": source_min.isoformat(),
            "source_max_date": source_max.isoformat(),
            "today": today.isoformat(),
            "default_start": default_start.isoformat(),
            "default_end": today.isoformat(),
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
        },
        start,
        end,
    )


def _eligible_contracts(
    contracts: list[Contract],
    start: date,
    end: date,
) -> tuple[list[Contract], dict]:
    candidates = []
    included = []
    excluded = []
    total_contract_days = 0
    included_contract_days = 0

    for contract in contracts:
        invalid_period = not contract.start_date or not contract.end_date or (
            contract.start_date and contract.end_date and contract.end_date < contract.start_date
        )
        if not invalid_period and not _overlaps(contract.start_date, contract.end_date, start, end):
            continue
        candidates.append(contract)
        overlap_start = max(contract.start_date, start) if not invalid_period else None
        overlap_end = min(contract.end_date, end) if not invalid_period else None
        if overlap_start and overlap_end:
            total_contract_days += (overlap_end - overlap_start).days + 1
        issues = _contract_issues(contract, start, end)
        if issues:
            excluded.append(
                {
                    "contract_id": contract.id,
                    "contract_no": contract.contract_no or "",
                    "tenant_name": customer_display_name(contract),
                    "start_date": contract.start_date.isoformat() if contract.start_date else None,
                    "end_date": contract.end_date.isoformat() if contract.end_date else None,
                    "reasons": issues,
                }
            )
            continue
        included.append(contract)
        intervals = []
        for item in contract_room_items(contract, overlap_start, overlap_end):
            if _number(item.get("area")) <= 0:
                continue
            item_start = max(item.get("start_date") or contract.start_date, overlap_start)
            item_end = min(item.get("end_date") or contract.end_date, overlap_end)
            if item_start <= item_end:
                intervals.append((item_start, item_end))
        if intervals:
            merged_start, merged_end = sorted(intervals)[0]
            for interval_start, interval_end in sorted(intervals)[1:]:
                if interval_start <= merged_end + timedelta(days=1):
                    merged_end = max(merged_end, interval_end)
                else:
                    included_contract_days += (merged_end - merged_start).days + 1
                    merged_start, merged_end = interval_start, interval_end
            included_contract_days += (merged_end - merged_start).days + 1

    total = len(candidates)
    included_count = len(included)
    coverage_rate = round(included_count / total * 100, 1) if total else 100.0
    day_rate = round(included_contract_days / total_contract_days * 100, 1) if total_contract_days else 100.0
    return included, {
        "total_contracts": total,
        "included_contracts": included_count,
        "excluded_contracts": total - included_count,
        "coverage_rate": coverage_rate,
        "total_contract_days": total_contract_days,
        "included_contract_days": included_contract_days,
        "contract_day_coverage_rate": day_rate,
        "excluded": excluded,
    }


def _calculate_daily(
    contracts: list[Contract],
    start: date,
    end: date,
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
) -> dict[date, dict[str, dict]]:
    day_count = (end - start).days + 1
    segment_keys = grouping.segment_keys
    segment_indexes = {segment: index for index, segment in enumerate(segment_keys)}
    rent_diffs = [[0.0] * (day_count + 1) for _ in segment_keys]
    area_diffs = [[0.0] * (day_count + 1) for _ in segment_keys]
    count_diffs = [[0] * (day_count + 1) for _ in segment_keys]

    def add_range(values: list, range_start: date, range_end: date, amount: float | int) -> None:
        if not amount or range_end < start or range_start > end:
            return
        left = (max(range_start, start) - start).days
        right = (min(range_end, end) - start).days + 1
        values[left] += amount
        values[right] -= amount

    def merged_intervals(items: list[dict]) -> list[tuple[date, date]]:
        intervals = sorted(
            (max(item["start_date"], start), min(item["end_date"], end))
            for item in items
            if item["start_date"] <= end and item["end_date"] >= start
        )
        if not intervals:
            return []
        result = [intervals[0]]
        for interval_start, interval_end in intervals[1:]:
            merged_start, merged_end = result[-1]
            if interval_start <= merged_end + timedelta(days=1):
                result[-1] = (merged_start, max(merged_end, interval_end))
            else:
                result.append((interval_start, interval_end))
        return result

    for contract in contracts:
        if not _overlaps(contract.start_date, contract.end_date, start, end):
            continue
        profile = [
            {
                **item,
                "start_date": max(item["start_date"], contract.start_date),
                "end_date": min(item["end_date"], contract.end_date),
            }
            for item in _contract_allocation_profile(
                contract,
                rooms_by_id,
                rooms_by_no,
                zone_code_by_id,
                grouping,
            )
            if item["start_date"] <= contract.end_date
            and item["end_date"] >= contract.start_date
        ]
        if not profile:
            continue

        if not getattr(contract, "rent_terms", None):
            free_end = _free_rent_end(contract)
            count_items = {"overall": profile}
            for segment in segment_keys:
                if segment == "overall":
                    continue
                count_items[segment] = [
                    item for item in profile if segment in item["segments"]
                ]
            for segment, items in count_items.items():
                count_diff = count_diffs[segment_indexes[segment]]
                for interval_start, interval_end in merged_intervals(items):
                    add_range(count_diff, interval_start, interval_end, 1)

            for item in profile:
                item_start = max(item["start_date"], start)
                item_end = min(item["end_date"], end)
                if item_start > item_end:
                    continue
                targets = ["overall"]
                targets.extend(item["segments"])
                for segment in targets:
                    add_range(
                        area_diffs[segment_indexes[segment]],
                        item_start,
                        item_end,
                        item["area"],
                    )

                billed_start = item_start
                if free_end and billed_start <= free_end:
                    billed_start = free_end + timedelta(days=1)
                current = billed_start
                while current <= item_end:
                    month_days = calendar.monthrange(current.year, current.month)[1]
                    period_end = min(
                        item_end,
                        date(current.year, current.month, month_days),
                    )
                    daily_rent = item["monthly_rent"] / month_days
                    for segment in targets:
                        add_range(
                            rent_diffs[segment_indexes[segment]],
                            current,
                            period_end,
                            daily_rent,
                        )
                    current = period_end + timedelta(days=1)
            continue

        overlap_start = max(contract.start_date, start)
        overlap_end = min(contract.end_date, end)
        for span_start, span_end in rent_constant_periods(contract, overlap_start, overlap_end):
            contribution = _contract_day_contribution(
                contract,
                span_start,
                rooms_by_id,
                rooms_by_no,
                zone_code_by_id,
                grouping,
                profile,
            )
            for segment in segment_keys:
                values = contribution[segment]
                segment_index = segment_indexes[segment]
                add_range(
                    rent_diffs[segment_index],
                    span_start,
                    span_end,
                    values["rent"],
                )
                add_range(
                    area_diffs[segment_index],
                    span_start,
                    span_end,
                    values["area"],
                )
                add_range(
                    count_diffs[segment_index],
                    span_start,
                    span_end,
                    values["contract_count"],
                )

    rows = {}
    running_rent = [0.0] * len(segment_keys)
    running_area = [0.0] * len(segment_keys)
    running_count = [0] * len(segment_keys)
    for offset, target in enumerate(_iter_days(start, end)):
        values = {}
        for segment_index, segment in enumerate(segment_keys):
            running_rent[segment_index] += rent_diffs[segment_index][offset]
            running_area[segment_index] += area_diffs[segment_index][offset]
            running_count[segment_index] += count_diffs[segment_index][offset]
            rent = running_rent[segment_index]
            area = running_area[segment_index]
            values[segment] = {
                "rent": 0.0 if abs(rent) < 0.000000001 else rent,
                "area": 0.0 if abs(area) < 0.000000001 else area,
                "contract_count": running_count[segment_index],
            }
        rows[target] = values
    return rows


def _serialize_daily(rows: dict[date, dict[str, dict]], segments: list[str], today: date) -> list[dict]:
    result = []
    for target, values in rows.items():
        serialized_segments = {}
        for segment in segments:
            rent = max(_number(values[segment]["rent"]), 0)
            area = max(_number(values[segment]["area"]), 0)
            serialized_segments[segment] = {
                "rent": round(rent, 6),
                "area": round(area, 4),
                "effective_daily_rent": round(rent / area, 6) if area > 0 else None,
                "contract_count": int(values[segment]["contract_count"]),
            }
        result.append(
            {
                "date": target.isoformat(),
                "phase": "history" if target <= today else "forecast",
                "segments": serialized_segments,
            }
        )
    return result


def _metric_for_rows(
    rows: dict[date, dict[str, dict]],
    segment: str,
    start: date,
    end: date,
    phase: str | None = None,
    today: date | None = None,
) -> dict | None:
    current_date = today or today_in_timezone()
    selected = [
        (target, values[segment])
        for target, values in rows.items()
        if start <= target <= end
        and (
            phase is None
            or (phase == "history" and target <= current_date)
            or (phase == "forecast" and target > current_date)
        )
    ]
    if not selected:
        return None
    rent_by_month: dict[tuple[int, int], float] = defaultdict(float)
    area_days = 0.0
    active_days = 0
    contract_ids_peak = 0
    for target, value in selected:
        rent_by_month[(target.year, target.month)] += _number(value["rent"])
        area = max(_number(value["area"]), 0)
        area_days += area
        if area > 0:
            active_days += 1
        contract_ids_peak = max(contract_ids_peak, int(value.get("contract_count") or 0))
    rent = sum(round_money(value) for value in rent_by_month.values())
    calendar_days = len(selected)
    return {
        "rent": round_money(rent),
        "area_days": round(area_days, 4),
        "average_leased_area": round(area_days / calendar_days, 4) if calendar_days else 0,
        "effective_daily_rent": round(rent / area_days, 6) if area_days > 0 else None,
        "calendar_days": calendar_days,
        "active_days": active_days,
        "peak_contract_count": contract_ids_peak,
    }


def _summary_for_rows(
    rows: dict[date, dict[str, dict]],
    start: date,
    end: date,
    segments: list[str],
) -> dict:
    today = today_in_timezone()
    if end <= today:
        phase = "history"
    elif start > today:
        phase = "forecast"
    else:
        phase = "mixed"
    result = {}
    for segment in segments:
        result[segment] = {
            "combined": _metric_for_rows(rows, segment, start, end, today=today),
            "history": _metric_for_rows(rows, segment, start, end, "history", today=today),
            "forecast": _metric_for_rows(rows, segment, start, end, "forecast", today=today),
        }
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "phase": phase,
        "split_history_forecast": phase == "mixed",
        "segments": result,
    }


def _rate_delta(current: dict | None, baseline: dict | None) -> dict:
    current_rate = current.get("effective_daily_rent") if current else None
    baseline_rate = baseline.get("effective_daily_rent") if baseline else None
    if current_rate is None or baseline_rate is None:
        return {"effective_daily_rent_delta": None, "effective_daily_rent_delta_pct": None}
    delta = current_rate - baseline_rate
    return {
        "effective_daily_rent_delta": round(delta, 6),
        "effective_daily_rent_delta_pct": round(delta / baseline_rate * 100, 2) if baseline_rate else None,
    }


def _comparison(
    contracts: list[Contract],
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
    current_summary: dict,
    start: date,
    end: date,
    compare_start: date,
    compare_end: date,
    segments: list[str],
    source_min: date,
    source_max: date,
) -> dict:
    if compare_start < source_min or compare_end > source_max:
        return {
            "available": False,
            "start_date": compare_start.isoformat(),
            "end_date": compare_end.isoformat(),
            "reason": "对比区间超出已有合同数据范围",
        }
    compare_contracts, compare_coverage = _eligible_contracts(contracts, compare_start, compare_end)
    if compare_coverage["coverage_rate"] < 100:
        return {
            "available": False,
            "start_date": compare_start.isoformat(),
            "end_date": compare_end.isoformat(),
            "reason": "对比区间存在无法计算的合同",
            "coverage": compare_coverage,
        }
    compare_rows = _calculate_daily(
        compare_contracts,
        compare_start,
        compare_end,
        rooms_by_id,
        rooms_by_no,
        zone_code_by_id,
        grouping,
    )
    compare_summary = _summary_for_rows(compare_rows, compare_start, compare_end, segments)
    deltas = {}
    for segment in segments:
        current_metric = current_summary["segments"][segment]["combined"]
        baseline_metric = compare_summary["segments"][segment]["combined"]
        deltas[segment] = {
            "current": current_metric,
            "baseline": baseline_metric,
            **_rate_delta(current_metric, baseline_metric),
        }
    return {
        "available": True,
        "start_date": compare_start.isoformat(),
        "end_date": compare_end.isoformat(),
        "coverage": compare_coverage,
        "segments": deltas,
    }


def _event_label(term_type: str, ending: bool = False) -> tuple[str, str]:
    labels = {
        "rent_free": ("rent_free_end" if ending else "rent_free_start", "免租结束" if ending else "免租开始"),
        "temporary_adjustment": (
            "temporary_adjustment_end" if ending else "temporary_adjustment_start",
            "临时租金调整结束" if ending else "临时租金调整开始",
        ),
        "escalation": ("escalation_end" if ending else "escalation", "递增租金结束" if ending else "递增租金生效"),
    }
    if term_type in labels:
        return labels[term_type]
    label = RENT_TERM_LABELS.get(term_type, "租金条款")
    return ("rent_term_end" if ending else "rent_term_start", f"{label}{'结束' if ending else '生效'}")


def _event_specs(contract: Contract, start: date, end: date) -> list[dict]:
    grouped: dict[date, dict] = {}

    def add(effect_date: date | None, event_type: str, label: str, source_date: date | None = None) -> None:
        if not effect_date or not (start <= effect_date <= end):
            return
        item = grouped.setdefault(effect_date, {"types": [], "labels": [], "source_dates": []})
        if event_type not in item["types"]:
            item["types"].append(event_type)
        if label not in item["labels"]:
            item["labels"].append(label)
        if source_date and source_date.isoformat() not in item["source_dates"]:
            item["source_dates"].append(source_date.isoformat())

    add(contract.start_date, "contract_start", "合同起租", contract.start_date)
    if contract.end_date:
        add(contract.end_date + timedelta(days=1), "contract_end", "合同到期退出", contract.end_date)
    free_end = _free_rent_end(contract)
    if free_end:
        add(free_end + timedelta(days=1), "rent_free_end", "合同免租期结束", free_end)

    for room in getattr(contract, "room_items", []) or []:
        room_start = room.start_date or contract.start_date
        room_end = room.end_date or contract.end_date
        add(room_start, "room_start", f"房源 {room.room_no or ''} 起租".strip(), room_start)
        if room_end:
            add(
                room_end + timedelta(days=1),
                "room_end",
                f"房源 {room.room_no or ''} 退出".strip(),
                room_end,
            )

    for term in getattr(contract, "rent_terms", []) or []:
        event_type, label = _event_label(term.rule_type, False)
        add(term.start_date, event_type, label, term.start_date)
        end_type, end_label = _event_label(term.rule_type, True)
        add(term.end_date + timedelta(days=1), end_type, end_label, term.end_date)
        if term.rule_type == "escalation" and (term.frequency or "once") == "annual":
            anniversary = term.start_date + relativedelta(years=1)
            while anniversary <= term.end_date:
                add(anniversary, "escalation", "年度递增租金生效", anniversary)
                anniversary += relativedelta(years=1)

    return [
        {
            "date": effect_date,
            "type": "+".join(item["types"]),
            "label": "；".join(item["labels"]),
            "source_dates": item["source_dates"],
        }
        for effect_date, item in grouped.items()
    ]


def _event_rate_impact(total: dict, current: dict, previous: dict) -> float | None:
    total_area = _number(total.get("area"))
    current_area = _number(current.get("area"))
    previous_area = _number(previous.get("area"))
    if total_area <= 0:
        return None
    current_rate = _number(total.get("rent")) / total_area
    baseline_area = total_area - current_area + previous_area
    if baseline_area <= 0:
        return None
    baseline_rent = _number(total.get("rent")) - _number(current.get("rent")) + _number(previous.get("rent"))
    return round(current_rate - baseline_rent / baseline_area, 6)


def _build_events(
    contracts: list[Contract],
    rows: dict[date, dict[str, dict]],
    start: date,
    end: date,
    segments: list[str],
    rooms_by_id: dict[int, Room],
    rooms_by_no: dict[str, Room],
    zone_code_by_id: Mapping[int, str],
    grouping: RentAnalyticsGrouping,
) -> list[dict]:
    events = []
    for contract in contracts:
        profile = _contract_allocation_profile(
            contract,
            rooms_by_id,
            rooms_by_no,
            zone_code_by_id,
            grouping,
        )
        for spec in _event_specs(contract, start, end):
            target = spec["date"]
            current = _contract_day_contribution(
                contract,
                target,
                rooms_by_id,
                rooms_by_no,
                zone_code_by_id,
                grouping,
                profile,
            )
            previous = _contract_day_contribution(
                contract,
                target - timedelta(days=1),
                rooms_by_id,
                rooms_by_no,
                zone_code_by_id,
                grouping,
                profile,
            )
            impact_segments = list(dict.fromkeys(["overall", *segments]))
            all_impacts = {}
            for segment in impact_segments:
                all_impacts[segment] = {
                    "rent_delta": round(
                        _number(current[segment]["rent"]) - _number(previous[segment]["rent"]),
                        6,
                    ),
                    "area_delta": round(
                        _number(current[segment]["area"]) - _number(previous[segment]["area"]),
                        4,
                    ),
                    "effective_daily_rent_impact": _event_rate_impact(
                        rows[target][segment],
                        current[segment],
                        previous[segment],
                    ),
                }
            overall = all_impacts["overall"]
            events.append(
                {
                    "date": target.isoformat(),
                    "source_dates": spec["source_dates"],
                    "type": spec["type"],
                    "contract_id": contract.id,
                    "contract_no": contract.contract_no or "",
                    "tenant_name": customer_display_name(contract),
                    "label": spec["label"],
                    "rent_delta": overall["rent_delta"],
                    "area_delta": overall["area_delta"],
                    "effective_daily_rent_impact": overall["effective_daily_rent_impact"],
                    "segments": {segment: all_impacts[segment] for segment in segments},
                }
            )
    events.sort(
        key=lambda item: (
            item["date"],
            -abs(item["effective_daily_rent_impact"] or 0),
            -abs(item["rent_delta"]),
            item["contract_no"],
        )
    )
    return events


@dataclass(frozen=True)
class _PreparedRentSeries:
    grouping: RentAnalyticsGrouping
    selected_segments: list[str]
    contracts: list[Contract]
    rooms_by_id: dict[int, Room]
    rooms_by_no: dict[str, Room]
    zone_code_by_id: dict[int, str]
    bounds: dict
    start: date
    end: date
    included: list[Contract]
    coverage: dict
    rows: dict[date, dict[str, dict]]


def _prepare_rent_series(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    segments: list[str] | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> _PreparedRentSeries:
    scoped_building_ids = _normalize_building_ids(building_ids)
    grouping = _resolve_rent_grouping(db, scoped_building_ids)
    if not segments or (
        tuple(segments) == SEGMENT_KEYS and grouping.segment_keys != SEGMENT_KEYS
    ):
        selected_segments = list(grouping.segment_keys)
    else:
        selected_segments = list(dict.fromkeys(segments))
    invalid_segments = [item for item in selected_segments if item not in grouping.segment_keys]
    if invalid_segments:
        raise HTTPException(422, f"不支持的分析分区：{', '.join(invalid_segments)}")

    contracts, rooms_by_id, rooms_by_no, zone_code_by_id = _load_context(
        db,
        scoped_building_ids,
    )
    bounds, start, end = _resolve_bounds(contracts, start_date, end_date)
    included, coverage = _eligible_contracts(contracts, start, end)
    rows = _calculate_daily(
        included,
        start,
        end,
        rooms_by_id,
        rooms_by_no,
        zone_code_by_id,
        grouping,
    )
    return _PreparedRentSeries(
        grouping=grouping,
        selected_segments=selected_segments,
        contracts=contracts,
        rooms_by_id=rooms_by_id,
        rooms_by_no=rooms_by_no,
        zone_code_by_id=zone_code_by_id,
        bounds=bounds,
        start=start,
        end=end,
        included=included,
        coverage=coverage,
        rows=rows,
    )


@reuse_read_calculations
def build_snapshot_rent_projection(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    segments: list[str] | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    """Read the rent fields consumed by operating snapshots, within this call only."""
    prepared = _prepare_rent_series(
        db, start_date, end_date, segments, building_ids=building_ids,
    )
    return {
        "segments": [
            item for item in prepared.grouping.definitions
            if item["key"] in prepared.selected_segments
        ],
        "coverage": prepared.coverage,
        # Price trends sum these rounded daily values; keep the full endpoint's
        # serialization before the snapshot performs any monthly aggregation.
        "daily": _serialize_daily(prepared.rows, prepared.selected_segments, today_in_timezone()),
    }


@reuse_read_calculations
def build_rent_analytics(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    segments: list[str] | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    prepared = _prepare_rent_series(
        db, start_date, end_date, segments, building_ids=building_ids,
    )
    grouping = prepared.grouping
    selected_segments = prepared.selected_segments
    contracts = prepared.contracts
    rooms_by_id = prepared.rooms_by_id
    rooms_by_no = prepared.rooms_by_no
    zone_code_by_id = prepared.zone_code_by_id
    bounds, start, end = prepared.bounds, prepared.start, prepared.end
    included, coverage, rows = prepared.included, prepared.coverage, prepared.rows
    summary = _summary_for_rows(rows, start, end, selected_segments)

    source_min = date.fromisoformat(bounds["source_min_date"])
    source_max = date.fromisoformat(bounds["source_max_date"])
    window_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)
    comparisons = {
        "previous_period": _comparison(
            contracts,
            rooms_by_id,
            rooms_by_no,
            zone_code_by_id,
            grouping,
            summary,
            start,
            end,
            previous_start,
            previous_end,
            selected_segments,
            source_min,
            source_max,
        )
    }
    if window_days <= 366:
        yoy_start = start + relativedelta(years=-1)
        yoy_end = end + relativedelta(years=-1)
        comparisons["year_over_year"] = _comparison(
            contracts,
            rooms_by_id,
            rooms_by_no,
            zone_code_by_id,
            grouping,
            summary,
            start,
            end,
            yoy_start,
            yoy_end,
            selected_segments,
            source_min,
            source_max,
        )
    else:
        comparisons["year_over_year"] = {
            "available": False,
            "reason": "所选时间超过一年，不提供同比",
        }

    return {
        "contract_version": RENT_ANALYTICS_VERSION,
        "bounds": bounds,
        "segments": [
            item for item in grouping.definitions if item["key"] in selected_segments
        ],
        "coverage": coverage,
        "summary": summary,
        "comparisons": comparisons,
        "daily": _serialize_daily(rows, selected_segments, today_in_timezone()),
        "events": _build_events(
            included,
            rows,
            start,
            end,
            selected_segments,
            rooms_by_id,
            rooms_by_no,
            zone_code_by_id,
            grouping,
        ),
        "methodology": {
            "metric": "有效平均日租（元/㎡/天）",
            "formula": "所选期间应计有效租金 ÷ 在租面积天数",
            "rent_scope": "基础租金、阶梯租金、免租及临时租金调整；不含物业费、押金、佣金、税费和回款",
            "daily_accrual": "固定月租按所在自然月天数逐日摊分，与现有月度租金计划一致",
            "vacancy": "空置面积不进入平均租金；有在租面积且处于免租期时租金按零计入",
            "forecast": "今天以后仅按现有已签合同及其到期日计算，不假设续租或再出租",
            "group_allocation": "合同级租金按当日各分区有效出租面积比例分摊",
        },
    }


def _style_workbook(workbook: openpyxl.Workbook) -> None:
    header_fill = PatternFill("solid", fgColor="DCEBFA")
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
        for column in range(1, sheet.max_column + 1):
            max_length = 0
            for row in range(1, min(sheet.max_row, 500) + 1):
                value = sheet.cell(row, column).value
                max_length = max(max_length, len(str(value or "")))
            sheet.column_dimensions[get_column_letter(column)].width = min(max(max_length + 2, 12), 42)


def export_rent_analytics_excel(analytics: dict, segments: list[str]) -> StreamingResponse:
    segment_labels = {
        item["key"]: item["label"]
        for item in analytics.get("segments") or []
    }
    if tuple(segments) == SEGMENT_KEYS and tuple(segment_labels) != SEGMENT_KEYS:
        selected_segments = list(segment_labels)
    else:
        selected_segments = segments or list(segment_labels)
    workbook = openpyxl.Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "分析摘要"
    summary_sheet.append(["租金时间分析", "值"])
    bounds = analytics["bounds"]
    coverage = analytics["coverage"]
    summary_sheet.append(["开始日期", bounds["requested_start"]])
    summary_sheet.append(["结束日期", bounds["requested_end"]])
    summary_sheet.append(
        ["所选分区", "、".join(segment_labels.get(item, item) for item in selected_segments)]
    )
    summary_sheet.append(["数据覆盖率", f"{coverage['included_contracts']}/{coverage['total_contracts']}（{coverage['coverage_rate']}%）"])
    summary_sheet.append(["计算公式", analytics["methodology"]["formula"]])
    for segment in selected_segments:
        segment_summary = analytics["summary"]["segments"][segment]
        for phase_key, phase_label in (("history", "历史"), ("forecast", "已签合同预测")):
            metric = segment_summary.get(phase_key)
            if not metric:
                continue
            summary_sheet.append(
                [
                    f"{segment_labels.get(segment, segment)}-{phase_label}",
                    f"有效平均日租 {metric['effective_daily_rent'] if metric['effective_daily_rent'] is not None else '--'}；"
                    f"应计租金 {metric['rent']}；面积天数 {metric['area_days']}",
                ]
            )

    series_sheet = workbook.create_sheet("时间序列")
    series_sheet.append(["日期", "阶段", "分区", "应计租金", "出租面积", "有效平均日租"])
    for row in analytics["daily"]:
        for segment in selected_segments:
            metric = row["segments"][segment]
            series_sheet.append(
                [
                    row["date"],
                    "历史" if row["phase"] == "history" else "已签合同预测",
                    segment_labels.get(segment, segment),
                    metric["rent"],
                    metric["area"],
                    metric["effective_daily_rent"],
                ]
            )

    driver_sheet = workbook.create_sheet("变化驱动")
    driver_sheet.append(
        ["生效日期", "依据日期", "分区", "类型", "合同编号", "客户", "原因", "租金变化", "面积变化", "平均日租影响"]
    )
    for event in analytics["events"]:
        for segment in selected_segments:
            impact = event["segments"][segment]
            if (
                segment != "overall"
                and impact["rent_delta"] == 0
                and impact["area_delta"] == 0
                and impact["effective_daily_rent_impact"] in {None, 0}
            ):
                continue
            driver_sheet.append(
                [
                    event["date"],
                    "、".join(event["source_dates"]),
                    segment_labels.get(segment, segment),
                    event["type"],
                    event["contract_no"],
                    event["tenant_name"],
                    event["label"],
                    impact["rent_delta"],
                    impact["area_delta"],
                    impact["effective_daily_rent_impact"],
                ]
            )

    exclusion_sheet = workbook.create_sheet("排除记录")
    exclusion_sheet.append(["合同编号", "客户", "排除原因"])
    for item in coverage["excluded"]:
        exclusion_sheet.append(
            [
                item["contract_no"],
                item["tenant_name"],
                "；".join(item["reasons"]),
            ]
        )

    method_sheet = workbook.create_sheet("口径说明")
    method_sheet.append(["项目", "说明"])
    for key, value in analytics["methodology"].items():
        method_sheet.append([key, value])

    _style_workbook(workbook)
    sanitize_workbook(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = f"租金时间分析_{bounds['requested_start']}_{bounds['requested_end']}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
