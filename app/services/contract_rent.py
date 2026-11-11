from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache

from dateutil.relativedelta import relativedelta

from app.core.money import round_money
from app.models import Contract, RentTerm
from app.services.common import contract_room_effective_period, contract_room_items, contract_room_summary
from app.services.calculation_context import memoize_contract_calculation


DEFAULT_BROKER_FEE_MONTHS = 2.5


RENT_BASE_TERM_TYPES = {"area_unit_price", "fixed_monthly", "stepped_rent"}


TEMPORARY_ADJUSTMENT_TERM_TYPE = "temporary_adjustment"


RENT_TERM_TYPES = RENT_BASE_TERM_TYPES | {"rent_free", "escalation", TEMPORARY_ADJUSTMENT_TERM_TYPE}


RENT_ADJUSTMENT_TYPES = {"percent", "fixed_amount"}


ESCALATION_ADJUSTMENT_TYPES = RENT_ADJUSTMENT_TYPES


ESCALATION_FREQUENCIES = {"once", "annual"}


RENT_TERM_LABELS = {
    "area_unit_price": "面积单价",
    "fixed_monthly": "固定月租",
    "stepped_rent": "阶梯租金",
    "rent_free": "自然日免租",
    "escalation": "递增租金",
    TEMPORARY_ADJUSTMENT_TERM_TYPE: "临时减免",
}


def _month_bounds(anchor: date) -> tuple[date, date]:
    month_start = anchor.replace(day=1)
    month_end = date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])
    return month_start, month_end


def calculate_monthly_rent(area: float | None, unit_price: float | None) -> float:
    return round_money((area or 0) * (unit_price or 0) * 365 / 12)


def calculate_broker_fee(monthly_rent: float | None, months: float = DEFAULT_BROKER_FEE_MONTHS) -> float:
    return round_money((monthly_rent or 0) * months)


def _sync_monthly_rent(data: dict) -> None:
    data["monthly_rent"] = calculate_monthly_rent(data.get("area"), data.get("unit_price"))


def _date_overlap(first_start: date, first_end: date, second_start: date, second_end: date) -> tuple[date | None, date | None]:
    start = max(first_start, second_start)
    end = min(first_end, second_end)
    if start > end:
        return None, None
    return start, end


def _rent_term_room_key(term: RentTerm) -> str:
    if term.contract_room_id:
        return f"id:{term.contract_room_id}"
    if term.room_no:
        return f"room:{term.room_no}"
    return ""


def _rent_free_matches_component(free_component: dict, rent_component: dict) -> bool:
    free_room_id = free_component.get("contract_room_id")
    free_room_no = free_component.get("room_no")
    if not free_room_id and not free_room_no:
        return True
    if free_room_id and rent_component.get("contract_room_id") == free_room_id:
        return True
    return bool(free_room_no and rent_component.get("room_no") == free_room_no)


def _rent_term_scope_periods(contract: Contract, term: RentTerm) -> list[tuple[date, date]]:
    if not contract.start_date or not contract.end_date:
        return []
    contract_period = (contract.start_date, contract.end_date)
    if term.contract_room_id:
        room_item = next((item for item in contract.room_items or [] if item.id == term.contract_room_id), None)
        if not room_item:
            return []
        start, end = contract_room_effective_period(contract, room_item)
        if not start or not end:
            return []
        overlap_start, overlap_end = _date_overlap(contract_period[0], contract_period[1], start, end)
        return [(overlap_start, overlap_end)] if overlap_start and overlap_end else []
    if term.room_no:
        periods = []
        for room_item in contract.room_items or []:
            if room_item.room_no != term.room_no:
                continue
            start, end = contract_room_effective_period(contract, room_item)
            if not start or not end:
                continue
            overlap_start, overlap_end = _date_overlap(contract_period[0], contract_period[1], start, end)
            if overlap_start and overlap_end:
                periods.append((overlap_start, overlap_end))
        return periods
    return [contract_period]


def _rent_term_fallback_area(contract: Contract, term: RentTerm) -> float:
    if term.area is not None:
        return float(term.area or 0)
    if term.contract_room_id:
        room_item = next((item for item in contract.room_items or [] if item.id == term.contract_room_id), None)
        return float(room_item.area or 0) if room_item else 0
    if term.room_no:
        return round(sum(float(item.area or 0) for item in contract.room_items or [] if item.room_no == term.room_no), 2)
    return float(contract_room_summary(contract)["area"] or 0)


def _rent_term_monthly_base(contract: Contract, term: RentTerm) -> float:
    if term.rule_type == "fixed_monthly":
        return round_money(float(term.monthly_amount or 0))
    if term.rule_type == "stepped_rent" and term.monthly_amount is not None:
        return round_money(float(term.monthly_amount or 0))
    if term.rule_type in {"area_unit_price", "stepped_rent"}:
        return calculate_monthly_rent(_rent_term_fallback_area(contract, term), term.unit_price)
    return 0


def _rent_term_scope_component(term: RentTerm) -> dict:
    return {
        "term": term,
        "room_key": _rent_term_room_key(term),
        "contract_room_id": term.contract_room_id,
        "room_no": term.room_no or "",
    }


def _legacy_room_components_for_day(contract: Contract, target: date, month_days: int) -> list[dict]:
    components = []
    for item in contract_room_items(contract, target, target):
        item_start = item.get("start_date")
        item_end = item.get("end_date")
        if not item_start or not item_end or not (item_start <= target <= item_end):
            continue
        monthly_rent = float(item.get("monthly_rent") or 0)
        if monthly_rent <= 0:
            continue
        if item.get("id"):
            room_key = f"id:{item['id']}"
        elif item.get("room_no"):
            room_key = f"room:{item['room_no']}"
        else:
            room_key = ""
        components.append(
            {
                "term": None,
                "source": "legacy",
                "room_key": room_key,
                "contract_room_id": item.get("id"),
                "room_no": item.get("room_no") or "",
                "start": target,
                "end": target,
                "days": 1,
                "month_days": month_days,
                "monthly_base": monthly_rent,
                "amount": monthly_rent / month_days,
            }
        )
    return components


def _rent_term_active_on_day(contract: Contract, term: RentTerm, target: date) -> bool:
    for scope_start, scope_end in _rent_term_scope_periods(contract, term):
        term_start, term_end = _date_overlap(term.start_date, term.end_date, scope_start, scope_end)
        if term_start and term_end and term_start <= target <= term_end:
            return True
    return False


def _rent_term_active_in_period(contract: Contract, term: RentTerm, period_start: date, period_end: date) -> bool:
    for scope_start, scope_end in _rent_term_scope_periods(contract, term):
        term_start, term_end = _date_overlap(term.start_date, term.end_date, scope_start, scope_end)
        if not term_start or not term_end:
            continue
        overlap_start, overlap_end = _date_overlap(term_start, term_end, period_start, period_end)
        if overlap_start and overlap_end:
            return True
    return False


def _rent_term_scope_text(term: RentTerm) -> str:
    if term.contract_room_id or term.room_no:
        return f"房源级 {term.room_no or ''}".strip()
    return "合同级"


def _rent_term_effects_for_month(contract: Contract, month_start: date, month_end: date, detail: dict) -> tuple[str, list[dict]]:
    active_terms = [
        term
        for term in getattr(contract, "rent_terms", []) or []
        if _rent_term_active_in_period(contract, term, month_start, month_end)
    ]
    effects = []
    if not active_terms:
        effects.append(
            {
                "type": "legacy",
                "label": "旧口径",
                "description": "合同/房源月租按自然日比例计算",
                "amount": detail.get("rent", 0),
            }
        )
        return "旧口径", effects

    active_labels = []
    for term in active_terms:
        label = RENT_TERM_LABELS.get(term.rule_type, term.rule_type or "租金条款")
        scoped_label = f"{_rent_term_scope_text(term)}{label}"
        if scoped_label not in active_labels:
            active_labels.append(scoped_label)

    legacy_fallback_amount = round_money(detail.get("legacy_fallback_amount") or 0)
    base_labels = [
        RENT_TERM_LABELS.get(term.rule_type, term.rule_type or "基础租金")
        for term in active_terms
        if term.rule_type in RENT_BASE_TERM_TYPES
    ]
    if base_labels:
        gross_amount = detail.get("gross_rent", detail.get("rent", 0)) or 0
        effects.append(
            {
                "type": "base",
                "label": "基础租金",
                "description": "、".join(dict.fromkeys(base_labels)),
                "amount": round_money(gross_amount - legacy_fallback_amount),
            }
        )
    if legacy_fallback_amount > 0:
        effects.append(
            {
                "type": "legacy",
                "label": "旧口径计租",
                "description": "条款未覆盖时段按合同/房源月租折算",
                "amount": legacy_fallback_amount,
            }
        )
    if any(term.rule_type == "escalation" for term in active_terms):
        effects.append(
            {
                "type": "escalation",
                "label": "递增租金",
                "description": "已计入基础租金",
                "amount": None,
            }
        )
    if (detail.get("rent_free_amount") or 0) > 0:
        effects.append(
            {
                "type": "rent_free",
                "label": "自然日免租",
                "description": "按自然日抵减",
                "amount": -round_money(detail.get("rent_free_amount") or 0),
            }
        )
    if (detail.get("temporary_adjustment_amount") or 0) > 0:
        effects.append(
            {
                "type": "temporary_adjustment",
                "label": "临时减免",
                "description": "按自然日抵减",
                "amount": -round_money(detail.get("temporary_adjustment_amount") or 0),
                "needs_attention": bool(detail.get("temporary_adjustment_attention")),
            }
        )
    if not effects:
        effects.append(
            {
                "type": "rent_terms",
                "label": "租金条款",
                "description": "当月无有效应收租金",
                "amount": 0,
            }
        )
    return "；".join(active_labels), effects


def _rent_escalation_level(term: RentTerm, target: date) -> int:
    if term.rule_type != "escalation" or not term.start_date or not term.end_date:
        return 0
    if not (term.start_date <= target <= term.end_date):
        return 0
    if (term.frequency or "once") != "annual":
        return 1
    elapsed_years = target.year - term.start_date.year
    anniversary = term.start_date + relativedelta(years=elapsed_years)
    if anniversary > target:
        elapsed_years -= 1
    return max(elapsed_years, 0) + 1


def _rent_term_positive_components_for_day(contract: Contract, target: date, month_days: int) -> list[dict]:
    components = []
    for term in getattr(contract, "rent_terms", []) or []:
        if term.rule_type not in RENT_BASE_TERM_TYPES:
            continue
        monthly_base = _rent_term_monthly_base(contract, term)
        has_explicit_base = (
            (term.rule_type == "fixed_monthly" and term.monthly_amount is not None)
            or (term.rule_type == "area_unit_price" and term.unit_price is not None)
            or (
                term.rule_type == "stepped_rent"
                and (term.monthly_amount is not None or term.unit_price is not None)
            )
        )
        if monthly_base < 0 or (monthly_base == 0 and not has_explicit_base):
            continue
        for scope_start, scope_end in _rent_term_scope_periods(contract, term):
            term_start, term_end = _date_overlap(term.start_date, term.end_date, scope_start, scope_end)
            if not term_start or not term_end or not (term_start <= target <= term_end):
                continue
            component = _rent_term_scope_component(term)
            component.update(
                {
                    "start": target,
                    "end": target,
                    "days": 1,
                    "month_days": month_days,
                    "monthly_base": monthly_base,
                    "amount": monthly_base / month_days,
                }
            )
            components.append(component)
    return components


def _rent_term_escalations_for_day(contract: Contract, target: date) -> list[RentTerm]:
    terms = []
    for term in getattr(contract, "rent_terms", []) or []:
        if term.rule_type != "escalation" or not _rent_term_active_on_day(contract, term, target):
            continue
        if _rent_escalation_level(term, target) > 0:
            terms.append(term)
    return terms


def _rent_term_free_components_for_day(contract: Contract, target: date) -> list[dict]:
    return [
        _rent_term_scope_component(term)
        for term in getattr(contract, "rent_terms", []) or []
        if term.rule_type == "rent_free" and _rent_term_active_on_day(contract, term, target)
    ]


def _rent_term_temporary_adjustments_for_day(contract: Contract, target: date) -> list[RentTerm]:
    return [
        term
        for term in getattr(contract, "rent_terms", []) or []
        if term.rule_type == TEMPORARY_ADJUSTMENT_TERM_TYPE and _rent_term_active_on_day(contract, term, target)
    ]


def _apply_percentage_escalations(components: list[dict], escalations: list[RentTerm], target: date) -> None:
    for component in components:
        amount = component["amount"]
        for term in escalations:
            if (term.adjustment_type or "") != "percent":
                continue
            if not _rent_free_matches_component(_rent_term_scope_component(term), component):
                continue
            level = _rent_escalation_level(term, target)
            amount *= (1 + float(term.adjustment_value or 0) / 100) ** level
        component["amount"] = amount


def _fixed_escalation_components_for_day(escalations: list[RentTerm], positive_components: list[dict], target: date, month_days: int) -> list[dict]:
    components = []
    for term in escalations:
        if (term.adjustment_type or "") != "fixed_amount":
            continue
        scope_component = _rent_term_scope_component(term)
        if not any(_rent_free_matches_component(scope_component, component) for component in positive_components):
            continue
        level = _rent_escalation_level(term, target)
        amount = float(term.adjustment_value or 0) * level / month_days
        component = dict(scope_component)
        component.update(
            {
                "start": target,
                "end": target,
                "days": 1,
                "month_days": month_days,
                "monthly_base": float(term.adjustment_value or 0) * level,
                "amount": amount,
            }
        )
        components.append(component)
    return components


def _temporary_adjustment_breakdown_for_day(
    adjustments: list[RentTerm],
    component_nets: list[dict],
    month_days: int,
) -> tuple[float, list[dict]]:
    remaining = [
        {"component": item["component"], "amount": max(float(item.get("amount") or 0), 0)}
        for item in component_nets
    ]
    if not remaining or not month_days:
        return 0.0, remaining

    total = 0.0
    for term in adjustments:
        scope_component = _rent_term_scope_component(term)
        matches = [
            item
            for item in remaining
            if item["amount"] > 0 and _rent_free_matches_component(scope_component, item["component"])
        ]
        available = sum(item["amount"] for item in matches)
        if available <= 0:
            continue

        adjustment_type = term.adjustment_type or "fixed_amount"
        if adjustment_type == "percent":
            requested = available * float(term.adjustment_value or 0) / 100
        else:
            requested = float(term.adjustment_value or 0) / month_days
        applied = min(max(requested, 0), available)
        total += applied

        to_apply = applied
        for index, item in enumerate(matches):
            if to_apply <= 0:
                break
            if index == len(matches) - 1:
                take = min(item["amount"], to_apply)
            else:
                take = min(item["amount"], applied * item["amount"] / available)
            item["amount"] -= take
            to_apply -= take
    return total, remaining


def _temporary_adjustment_amount_for_day(adjustments: list[RentTerm], component_nets: list[dict], month_days: int) -> float:
    total, _remaining = _temporary_adjustment_breakdown_for_day(adjustments, component_nets, month_days)
    return total


def _rent_terms_day_breakdown(contract: Contract, target: date) -> dict:
    """Return the unrounded rent accrual and scoped components for one day.

    Month-level callers keep their existing rounding boundary. Analytics can
    aggregate the same raw daily accrual over any inclusive date window.
    """

    month_days = calendar.monthrange(target.year, target.month)[1]
    has_contract_period = bool(contract.start_date and contract.end_date)
    free_rent_end = _free_rent_end(contract) if has_contract_period else None

    components = _rent_term_positive_components_for_day(contract, target, month_days)
    if (
        has_contract_period
        and contract.start_date <= target <= contract.end_date
        and (free_rent_end is None or target > free_rent_end)
    ):
        contract_scoped = any(
            not component.get("contract_room_id") and not component.get("room_no")
            for component in components
        )
        if not contract_scoped:
            covered_room_ids = {
                component["contract_room_id"]
                for component in components
                if component.get("contract_room_id")
            }
            covered_room_nos = {
                component["room_no"] for component in components if component.get("room_no")
            }
            for item in _legacy_room_components_for_day(contract, target, month_days):
                if item.get("contract_room_id") and item["contract_room_id"] in covered_room_ids:
                    continue
                if item.get("room_no") and item["room_no"] in covered_room_nos:
                    continue
                components.append(item)

    escalations = _rent_term_escalations_for_day(contract, target)
    _apply_percentage_escalations(components, escalations, target)
    components.extend(_fixed_escalation_components_for_day(escalations, components, target, month_days))

    gross = sum(float(component.get("amount") or 0) for component in components)
    legacy_fallback = sum(
        float(component.get("amount") or 0)
        for component in components
        if component.get("source") == "legacy"
    )
    free_components = _rent_term_free_components_for_day(contract, target)
    # The legacy free_period is a rolling contract-wide rent-free window. It
    # must also suppress explicit base terms, not only the legacy fallback.
    if free_rent_end is not None and target <= free_rent_end:
        free_components.append({"contract_room_id": None, "room_no": ""})

    component_nets = []
    rent_free_amount = 0.0
    for component in components:
        amount = float(component.get("amount") or 0)
        if any(_rent_free_matches_component(free_component, component) for free_component in free_components):
            rent_free_amount += amount
            component_nets.append({"component": component, "amount": 0.0})
        else:
            component_nets.append({"component": component, "amount": amount})

    temporary_adjustment_amount, remaining = _temporary_adjustment_breakdown_for_day(
        _rent_term_temporary_adjustments_for_day(contract, target),
        component_nets,
        month_days,
    )
    remaining_by_component = {
        id(item["component"]): max(float(item.get("amount") or 0), 0)
        for item in remaining
    }
    scoped_components = []
    for item in component_nets:
        component = item["component"]
        amount_after_free = max(float(item.get("amount") or 0), 0)
        net_amount = remaining_by_component.get(id(component), amount_after_free)
        scoped_components.append(
            {
                "contract_room_id": component.get("contract_room_id"),
                "room_no": component.get("room_no") or "",
                "gross_rent": max(float(component.get("amount") or 0), 0),
                "rent_free_amount": max(float(component.get("amount") or 0) - amount_after_free, 0),
                "temporary_adjustment_amount": max(amount_after_free - net_amount, 0),
                "rent": net_amount,
                "source": component.get("source") or "rent_term",
            }
        )

    return {
        "rent": max(gross - rent_free_amount - temporary_adjustment_amount, 0),
        "gross_rent": max(gross, 0),
        "rent_free_amount": min(max(rent_free_amount, 0), max(gross, 0)),
        "temporary_adjustment_amount": min(
            max(temporary_adjustment_amount, 0),
            max(gross - rent_free_amount, 0),
        ),
        "legacy_fallback_amount": max(legacy_fallback, 0),
        "components": scoped_components,
        "rent_source": "rent_terms",
    }


def rent_day_breakdown(contract: Contract, target: date) -> dict:
    """Calculate one natural day's effective rent using the billing engine."""

    if not contract.start_date or not contract.end_date or not (contract.start_date <= target <= contract.end_date):
        return {
            "rent": 0.0,
            "gross_rent": 0.0,
            "rent_free_amount": 0.0,
            "temporary_adjustment_amount": 0.0,
            "legacy_fallback_amount": 0.0,
            "components": [],
            "rent_source": "none",
        }
    if getattr(contract, "rent_terms", None):
        return _rent_terms_day_breakdown(contract, target)

    month_days = calendar.monthrange(target.year, target.month)[1]
    components = _legacy_room_components_for_day(contract, target, month_days)
    gross = sum(float(component.get("amount") or 0) for component in components)
    free_rent_end = _free_rent_end(contract)
    rent_free = bool(free_rent_end and target <= free_rent_end)
    scoped_components = [
        {
            "contract_room_id": component.get("contract_room_id"),
            "room_no": component.get("room_no") or "",
            "gross_rent": max(float(component.get("amount") or 0), 0),
            "rent_free_amount": max(float(component.get("amount") or 0), 0) if rent_free else 0.0,
            "temporary_adjustment_amount": 0.0,
            "rent": 0.0 if rent_free else max(float(component.get("amount") or 0), 0),
            "source": "legacy",
        }
        for component in components
    ]
    return {
        "rent": 0.0 if rent_free else max(gross, 0),
        "gross_rent": max(gross, 0),
        "rent_free_amount": max(gross, 0) if rent_free else 0.0,
        "temporary_adjustment_amount": 0.0,
        "legacy_fallback_amount": max(gross, 0),
        "components": scoped_components,
        "rent_source": "legacy",
    }


def rent_constant_periods(contract: Contract, start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive spans on which the existing daily engine is constant.

    Accrual changes only at month denominators, lease/room/term boundaries,
    the rolling free-period end, or annual escalation anniversaries. Always
    derive anniversaries from the original date (including February 29).
    This contains no cached business data or authorization state.
    """
    if start > end:
        return []
    boundaries = {start}

    def add(value):
        if value is not None and start < value <= end:
            boundaries.add(value)

    def after(value):
        if value is not None and value < end:
            add(value + timedelta(days=1))

    current = start.replace(day=1)
    while current <= end:
        add(current)
        if current.year == 9999 and current.month == 12:
            break
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    add(contract.start_date)
    after(contract.end_date)
    after(_free_rent_end(contract))
    for room in getattr(contract, 'room_items', []) or []:
        add(room.start_date or contract.start_date)
        after(room.end_date or contract.end_date)
    for term in getattr(contract, 'rent_terms', []) or []:
        add(term.start_date)
        after(term.end_date)
        if term.rule_type == 'escalation' and (term.frequency or 'once') == 'annual' and term.start_date:
            for year in range(max(start.year, term.start_date.year),
                              min(end.year, (term.end_date or end).year) + 1):
                add(term.start_date + relativedelta(years=year - term.start_date.year))
    ordered = sorted(boundaries)
    return [(left, right - timedelta(days=1)) for left, right in zip(ordered, ordered[1:])] + [(ordered[-1], end)]


@lru_cache(maxsize=2048)
def _fixed_month_amounts(base: float, month_days: int, lease_days: int, free_days: int) -> tuple:
    # Bounded memoization of pure arithmetic only: no IDs, records, permissions
    # or dates. Preserve the old daily addition order before cent rounding.
    daily = base / month_days
    gross = deduction = 0.0
    for i in range(lease_days):
        gross += daily
        if i < free_days:
            deduction += daily
    gross = round_money(gross)
    deduction = round_money(min(gross, deduction))
    return gross, deduction, round_money(max(gross - deduction, 0))


def _simple_fixed_month_detail(contract: Contract, month_start: date, month_end: date) -> dict | None:
    terms = getattr(contract, 'rent_terms', []) or []
    if len(terms) != 1 or not contract.start_date or not contract.end_date:
        return None
    term = terms[0]
    if (term.rule_type != 'fixed_monthly' or term.contract_room_id or term.room_no
            or term.monthly_amount is None or term.monthly_amount < 0):
        return None
    active_start, active_end = _date_overlap(contract.start_date, contract.end_date, month_start, month_end)
    # A gap in this term may enable legacy fallback; leave it to the full engine.
    if not active_start or not active_end or not term.start_date or not term.end_date:
        return None
    if term.start_date > active_start or term.end_date < active_end:
        return None
    return _fixed_month_detail(
        contract.start_date, active_start, active_end, _free_rent_end(contract),
        round_money(float(term.monthly_amount)), month_start,
        calendar.monthrange(month_start.year, month_start.month)[1],
    )


def _fixed_month_detail(start: date, active_start: date, active_end: date,
                        free_end: date | None, base: float, month_start: date, month_days: int) -> dict:
    lease_days = (active_end - active_start).days + 1
    free_days = max(0, (min(free_end, active_end) - active_start).days + 1) if free_end else 0
    gross, deduction, net = _fixed_month_amounts(
        base, month_days, lease_days, free_days)
    return {
        'month_idx': (month_start.year-start.year)*12 + month_start.month-start.month+1,
        'lease_days': lease_days, 'month_days': month_days, 'rent_ratio': round(lease_days/month_days, 6),
        'rent': net, 'rent_free': deduction > 0, 'rent_free_amount': deduction,
        'temporary_adjustment': False, 'temporary_adjustment_amount': 0.0,
        'temporary_adjustment_attention': False, 'gross_rent': gross,
        'legacy_fallback_amount': 0.0, 'rent_source': 'rent_terms',
    }


def rent_plan_month_details(contract: Contract):
    """Iterate the same monthly engine, preparing a constant rule once per lease.

    This keeps no cross-request contract state. Partial/gapped/scoped terms use
    the existing generic path; only a single full-lease fixed rule qualifies.
    """
    start, end = contract.start_date, contract.end_date
    if not start or not end:
        return
    terms = getattr(contract, 'rent_terms', []) or []
    term = terms[0] if len(terms) == 1 else None
    fixed = bool(end >= start and term is not None and term.rule_type == 'fixed_monthly'
                 and not term.contract_room_id and not term.room_no
                 and term.monthly_amount is not None and term.monthly_amount >= 0
                 and term.start_date and term.end_date
                 and term.start_date <= start and term.end_date >= end)
    base = round_money(float(term.monthly_amount)) if fixed else 0
    free_end = _free_rent_end(contract) if fixed else None
    current = start.replace(day=1)
    while current <= end:
        month_days = calendar.monthrange(current.year, current.month)[1]
        month_end = date(current.year, current.month, month_days)
        detail = (_fixed_month_detail(start, max(start, current), min(end, month_end),
                                     free_end, base, current, month_days)
                  if fixed else _month_rent_detail(contract, current))
        yield current, month_end, detail
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


def _rent_terms_month_detail(contract: Contract, anchor: date) -> dict:
    month_start, month_end = _month_bounds(anchor)
    simple = _simple_fixed_month_detail(contract, month_start, month_end)
    if simple is not None:
        return simple
    month_days = calendar.monthrange(month_start.year, month_start.month)[1]
    month_idx = 0
    lease_days = 0
    if contract.start_date and contract.end_date:
        month_idx = (month_start.year - contract.start_date.year) * 12 + (month_start.month - contract.start_date.month) + 1
        active_start, active_end = _date_overlap(contract.start_date, contract.end_date, month_start, month_end)
        if active_start and active_end:
            lease_days = (active_end - active_start).days + 1

    gross = 0.0
    deduction = 0.0
    temporary_adjustment = 0.0
    legacy_fallback = 0.0
    for span_start, span_end in rent_constant_periods(contract, month_start, month_end):
        daily = _rent_terms_day_breakdown(contract, span_start)
        # Keep the original chronological floating additions and month-end
        # cent rounding exactly; only the expensive rule evaluation is reused.
        for _ in range((span_end - span_start).days + 1):
            gross += daily["gross_rent"]
            deduction += daily["rent_free_amount"]
            temporary_adjustment += daily["temporary_adjustment_amount"]
            legacy_fallback += daily["legacy_fallback_amount"]
    gross = round_money(gross)
    rent_free_amount = round_money(min(gross, deduction))
    net_after_free = max(gross - rent_free_amount, 0)
    temporary_adjustment_amount = round_money(min(net_after_free, temporary_adjustment))
    rent = round_money(max(net_after_free - temporary_adjustment_amount, 0))
    return {
        "month_idx": month_idx,
        "lease_days": lease_days,
        "month_days": month_days,
        "rent_ratio": round(lease_days / month_days, 6) if month_days else 0,
        "rent": rent,
        "rent_free": rent_free_amount > 0,
        "rent_free_amount": rent_free_amount,
        "temporary_adjustment": temporary_adjustment_amount > 0,
        "temporary_adjustment_amount": temporary_adjustment_amount,
        "temporary_adjustment_attention": gross > 0 and temporary_adjustment_amount >= gross * 0.5,
        "gross_rent": gross,
        "legacy_fallback_amount": round_money(legacy_fallback),
        "rent_source": "rent_terms",
    }


def contract_net_rent(contract: Contract) -> float:
    """全口径月均净租金：月度应收合计 / 有效月数（Σ 当月在租天数占比）。

    与租金计划、月度应收使用同一计算引擎，天然涵盖租金条款、
    滚动免租与多房源按天折算；首尾不满月按实际占比计入分母，
    不再把不满月当作整月稀释均值。models.Contract.net_rent 与
    common.contract_effective_net_rent 均委托此函数，保持单一口径。
    """
    if not contract.start_date or not contract.end_date or contract.end_date < contract.start_date:
        return 0
    total_rent = 0.0
    effective_months = 0.0
    current = contract.start_date.replace(day=1)
    while current <= contract.end_date:
        detail = _month_rent_detail(contract, current)
        total_rent += detail["rent"]
        effective_months += detail["rent_ratio"]
        current += relativedelta(months=1)
    if effective_months <= 0:
        return 0
    return round_money(max(total_rent / effective_months, 0))


def _free_rent_end(contract: Contract) -> date | None:
    """滚动免租窗口的最后一天：起租日 + 免租月数个自然月 - 1 天。

    免租 N 个月 = 自起租日起连续 N 个自然月（如 1/25 起免 1 个月即 1/25-2/24），
    而非按自然月序号整月免租。月末起租时按 relativedelta 月底收口。
    """
    free = int(contract.free_period or 0)
    if free <= 0 or not contract.start_date:
        return None
    return contract.start_date + relativedelta(months=free) - timedelta(days=1)


def _legacy_room_rent_for_span(contract: Contract, month_start: date, month_end: date, span_start: date, span_end: date, month_days: int) -> float:
    """旧口径：指定日期段内每个房源按自身生效期交集天数折算月租。"""
    total = 0.0
    for item in contract_room_items(contract, month_start, month_end):
        item_start = item.get("start_date")
        item_end = item.get("end_date")
        if not item_start or not item_end:
            continue
        overlap_start, overlap_end = _date_overlap(item_start, item_end, span_start, span_end)
        if not overlap_start or not overlap_end:
            continue
        item_days = (overlap_end - overlap_start).days + 1
        total += float(item.get("monthly_rent") or 0) * item_days / month_days
    return total


@memoize_contract_calculation
def _month_rent_detail(contract: Contract, anchor: date) -> dict:
    if getattr(contract, "rent_terms", None):
        return _rent_terms_month_detail(contract, anchor)

    month_start, month_end = _month_bounds(anchor)
    month_days = calendar.monthrange(month_start.year, month_start.month)[1]
    month_idx = 0
    lease_days = 0
    rent_free = False
    rent_free_amount = 0.0
    rent = 0.0

    if contract.start_date and contract.end_date:
        month_idx = (month_start.year - contract.start_date.year) * 12 + (month_start.month - contract.start_date.month) + 1
        active_start = max(contract.start_date, month_start)
        active_end = min(contract.end_date, month_end)
        if active_start <= active_end:
            lease_days = (active_end - active_start).days + 1
            # 免租窗口自起租日起连续滚动，在任意月份内都是活跃日的前缀段
            free_rent_end = _free_rent_end(contract)
            free_until = None
            if free_rent_end and free_rent_end >= active_start:
                free_until = min(free_rent_end, active_end)
            rent_free = free_until is not None
            billable_start = active_start if free_until is None else free_until + timedelta(days=1)
            if rent_free:
                # 尾差一致性：免租额单独舍入，计租额 = 整月额 - 免租额，
                # 保证"免租 + 计租"相加恒等于整月租金，不因独立舍入差一分
                rent_free_amount = round_money(
                    _legacy_room_rent_for_span(contract, month_start, month_end, active_start, free_until, month_days)
                )
                if billable_start <= active_end:
                    month_total = round_money(
                        _legacy_room_rent_for_span(contract, month_start, month_end, active_start, active_end, month_days)
                    )
                    rent = round_money(max(month_total - rent_free_amount, 0))
            elif billable_start <= active_end:
                rent = round_money(
                    _legacy_room_rent_for_span(contract, month_start, month_end, billable_start, active_end, month_days)
                )

    return {
        "month_idx": month_idx,
        "lease_days": lease_days,
        "month_days": month_days,
        "rent_ratio": round(lease_days / month_days, 6) if month_days else 0,
        "rent": rent,
        "rent_free": rent_free,
        "rent_free_amount": rent_free_amount,
        "gross_rent": round_money(rent + rent_free_amount),
        "rent_source": "legacy",
    }
