from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from app.services.calculation_context import reuse_read_calculations
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from app.core.time import now_in_timezone
from app.models import Contract, ContractRoom, ReceivablePayment, ReceivableRecord, ReceivableStatement, RentTerm
from app.services.common import contract_room_items, customer_display_name
from app.services.contract_receivables import (
    CASH_ALLOCATION_TYPES,
    _payment_summary,
    build_rent_plan,
    get_monthly_receivables_batch,
)


SCHEMA_VERSION = "financial-planning-v1"
# Inputs are yuan; all public forecast amounts are ten-thousand yuan. Keeping
# integer fen internally preserves the existing billing engine's cent boundary.
FEN_PER_WAN = 1_000_000
MAX_LEASE_MONTHS = 600
BASE_METRICS = (
    "rent_income",
    "rent_receivable",
    "property_fee_income",
    "property_fee_receivable",
    "actual_cash_received",
    "forecast_cash_received",
    "opex",
    "capex",
    "actual_opex_paid",
    "forecast_opex_paid",
    "actual_capex_paid",
    "forecast_capex_paid",
)
DERIVED_METRICS = (
    "revenue", "operating_cost", "ebitda", "opex_cash_paid",
    "capex_cash_paid", "operating_cash_flow", "net_cash_flow",
)
METRICS = BASE_METRICS + DERIVED_METRICS


def _value(item: object, key: str, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _decimal(value: object, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise HTTPException(422, f"{label}不能为空")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, f"{label}必须是有效数字") from None
    if not result.is_finite():
        raise HTTPException(422, f"{label}必须是有限数字")
    return result


def _yuan_fen(value: object, label: str = "金额") -> int:
    return int((_decimal(value, label) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _wan_fen(value: object, label: str = "金额") -> int:
    return int((_decimal(value, label) * FEN_PER_WAN).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _wan(value: int) -> float:
    return value / FEN_PER_WAN


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    number = _decimal(value, label)
    if number != number.to_integral_value() or not minimum <= number <= maximum:
        raise HTTPException(422, f"{label}须在{minimum}至{maximum}之间")
    return int(number)


def _month(value: object, label: str) -> str:
    raw = str(value or "")
    try:
        parsed = date.fromisoformat(f"{raw}-01")
    except ValueError:
        raise HTTPException(422, f"{label}须为YYYY-MM") from None
    if raw != parsed.strftime("%Y-%m"):
        raise HTTPException(422, f"{label}须为YYYY-MM")
    return raw


def _date(value: object, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(422, f"{label}须为有效日期") from None


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _month_keys(year: int) -> list[str]:
    return [f"{year:04d}-{month:02d}" for month in range(1, 13)]


def _row_period(row: Mapping, year: int) -> str:
    value = row.get("month")
    if isinstance(value, str) and len(value) == 7:
        period = _month(value, "快照月份")
        if not period.startswith(f"{year:04d}-"):
            raise HTTPException(422, "快照月份与年度不一致")
        return period
    return f"{year:04d}-{_integer(value, '快照月份', 1, 12):02d}"


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _cutoff(year: int, closed: int) -> date:
    if closed == 0:
        return date(year - 1, 12, 31)
    return date(year, closed, calendar.monthrange(year, closed)[1])


def _empty_rows(months: list[str]) -> dict[str, dict[str, int]]:
    return {month: dict.fromkeys(BASE_METRICS, 0) for month in months}


def _derived(row: Mapping[str, int]) -> dict[str, int]:
    result = {metric: int(row.get(metric, 0)) for metric in BASE_METRICS}
    result["revenue"] = result["rent_income"] + result["property_fee_income"]
    result["operating_cost"] = result["opex"]
    result["ebitda"] = result["revenue"] - result["opex"]
    result["opex_cash_paid"] = result["actual_opex_paid"] + result["forecast_opex_paid"]
    result["capex_cash_paid"] = result["actual_capex_paid"] + result["forecast_capex_paid"]
    result["operating_cash_flow"] = (
        result["actual_cash_received"] + result["forecast_cash_received"] - result["opex_cash_paid"]
    )
    result["net_cash_flow"] = result["operating_cash_flow"] - result["capex_cash_paid"]
    return result


class _CalculatedSourceMonths(list):
    """Private, request-local exact fen totals; serialized as an ordinary list.

    Saved/external snapshots remain plain lists and use the validated parsing
    path. This avoids converting our own integers to yuan and back to Decimal
    for every metric on every contract month.
    """


def _public_rows(rows: Mapping[str, Mapping[str, int]], closed: int) -> list[dict]:
    derived = [(month, _derived(row)) for month, row in rows.items()]
    result = _CalculatedSourceMonths([
        {
            "month": int(month[-2:]), "period": month,
            "phase": "management_actual" if int(month[-2:]) <= closed else "forecast",
            **{key: _wan(amount) for key, amount in row.items()},
        }
        for month, row in derived
    ])
    result._fen_rows = [(month, tuple(row.get(key, 0) for key in METRICS)) for month, row in derived]
    return result


def _straight_income(plan: list[dict]) -> dict[str, int]:
    """Use the full existing rent plan, including free rent and known changes.

    The effective-month denominator is the same rent_ratio used by
    contract_net_rent. Cumulative allocation keeps the full-lease total exact.
    """
    total = sum(_yuan_fen(row.get("rent", 0)) for row in plan)
    weights = [_decimal(row.get("rent_ratio", 0), "有效租期") for row in plan]
    denominator = sum(weights, Decimal(0))
    if denominator <= 0:
        return {row["month"]: 0 for row in plan}
    cumulative = Decimal(0)
    allocated = 0
    result = {}
    for row, weight in zip(plan, weights):
        cumulative += weight
        through = int((Decimal(total) * cumulative / denominator).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        result[row["month"]] = through - allocated
        allocated = through
    return result


def _normalize_adjustments(items: Iterable[object], contract_ids: set[int]) -> dict[int, dict]:
    result = {}
    for item in items:
        contract_id = _integer(_value(item, "contract_id"), "合同编号", 1, 2**63 - 1)
        if contract_id not in contract_ids:
            raise HTTPException(404, "测算合同不存在或不属于当前项目")
        if contract_id in result:
            raise HTTPException(422, "同一合同只能设置一项测算调整")
        shift = _integer(_value(item, "start_shift_months", 0), "起租偏移月数", -24, 24)
        percent = _decimal(_value(item, "rent_change_percent", 0), "租金调整比例")
        if not Decimal(-100) <= percent <= Decimal(500):
            raise HTTPException(422, "租金调整比例须在-100%至500%之间")
        result[contract_id] = {"start_shift_months": shift, "rent_change_percent": float(percent)}
    return result


def _shifted_contract(contract: Contract, months: int) -> Contract:
    """A detached calculation object: never mutate or attach live ORM rows."""
    clone = Contract(
        id=contract.id, building_id=contract.building_id, contract_no=contract.contract_no,
        tower=contract.tower, room_no=contract.room_no, area=contract.area,
        unit_price=contract.unit_price, monthly_rent=contract.monthly_rent,
        free_period=contract.free_period, property_fee_rate=contract.property_fee_rate,
        property_fee_free=contract.property_fee_free,
        start_date=contract.start_date + relativedelta(months=months),
        end_date=contract.end_date + relativedelta(months=months),
    )
    clone.room_items = [
        ContractRoom(
            id=item.id, building_id=item.building_id, contract_id=contract.id,
            room_id=item.room_id, tower=item.tower, room_no=item.room_no,
            area=item.area, unit_price=item.unit_price, monthly_rent=item.monthly_rent,
            start_date=item.start_date + relativedelta(months=months) if item.start_date else None,
            end_date=item.end_date + relativedelta(months=months) if item.end_date else None,
        )
        for item in contract.room_items
    ]
    clone.rent_terms = [
        RentTerm(
            id=item.id, contract_id=contract.id, contract_room_id=item.contract_room_id,
            room_no=item.room_no, rule_type=item.rule_type,
            start_date=item.start_date + relativedelta(months=months),
            end_date=item.end_date + relativedelta(months=months),
            area=item.area, unit_price=item.unit_price, monthly_amount=item.monthly_amount,
            adjustment_type=item.adjustment_type, adjustment_value=item.adjustment_value,
            frequency=item.frequency,
        )
        for item in contract.rent_terms
    ]
    return clone


def _adjusted_plan(contract: Contract, adjustment: Mapping, original: list[dict]) -> list[dict]:
    shift = adjustment.get("start_shift_months", 0)
    plan = build_rent_plan(_shifted_contract(contract, shift), financial_only=True)["plan"] if shift else deepcopy(original)
    factor = Decimal(1) + _decimal(adjustment.get("rent_change_percent", 0), "租金调整比例") / 100
    for row in plan:
        for key in ("rent", "gross_rent", "rent_free_amount", "temporary_adjustment_amount"):
            row[key] = float((_decimal(row.get(key, 0), "租金") * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        row["total"] = float(Decimal(str(row["rent"])) + Decimal(str(row.get("property_fee", 0))))
    return plan


def _contract_snapshot(contract: Contract, plan: list[dict], adjustment: Mapping) -> dict:
    return {
        "contract": {
            "id": contract.id, "building_id": contract.building_id,
            "contract_no": contract.contract_no or "", "room_no": contract.room_no or "",
            "area": float(contract.area or 0), "monthly_rent": float(contract.monthly_rent or 0),
            "unit_price": float(contract.unit_price or 0), "free_period": contract.free_period or 0,
            "start_date": _iso(contract.start_date), "end_date": _iso(contract.end_date),
            "property_fee_rate": float(contract.property_fee_rate or 0),
            "property_fee_free": contract.property_fee_free or 0,
            "updated_at": _iso(contract.updated_at),
        },
        "room_items": [
            {key: (_iso(value) if isinstance(value, date) else value)
             for key, value in item.items() if key != "notes"}
            for item in contract_room_items(contract)
        ],
        "rent_terms": [
            {
                "id": item.id, "contract_room_id": item.contract_room_id,
                "room_no": item.room_no, "rule_type": item.rule_type,
                "start_date": _iso(item.start_date), "end_date": _iso(item.end_date),
                "area": item.area, "unit_price": item.unit_price, "monthly_amount": item.monthly_amount,
                "adjustment_type": item.adjustment_type, "adjustment_value": item.adjustment_value,
                "frequency": item.frequency,
            }
            for item in contract.rent_terms
        ],
        "rent_plan": [
            {key: row.get(key) for key in (
                "month", "rent", "property_fee", "rent_ratio", "lease_days", "month_days",
                "rent_free_amount", "temporary_adjustment_amount", "rent_source",
            )}
            for row in plan
        ],
        "adjustment": dict(adjustment),
    }


def _payment_context(db: Session, contract_ids: list[int]) -> dict[int, dict[str, dict]]:
    payments = defaultdict(lambda: defaultdict(list))
    records = defaultdict(dict)
    if not contract_ids:
        return {}
    # Select whole account periods before the legacy fallback. Filtering by date
    # first can resurrect an old summary already replaced by payment details.
    for payment in db.query(ReceivablePayment).filter(ReceivablePayment.contract_id.in_(contract_ids)).all():
        payments[payment.contract_id][payment.period_month].append(payment)
    for record in db.query(ReceivableRecord).filter(ReceivableRecord.contract_id.in_(contract_ids)).all():
        records[record.contract_id][record.period_month] = record
    return {
        contract_id: {
            month: _payment_summary(payments[contract_id].get(month, []), records[contract_id].get(month))
            for month in set(payments[contract_id]) | set(records[contract_id])
        }
        for contract_id in contract_ids
    }


def _expense_source(item: object, months: list[str], cutoff: date, closed: int, coverage: dict) -> dict | None:
    if _value(item, "is_active", True) is False:
        return None
    if _value(item, "detail") or _value(item, "payments"):
        from app.services.finance_forecast import expense_source
        return expense_source(item, months, cutoff, closed, coverage)
    source_id = _value(item, "id")
    if source_id is None:
        raise HTTPException(422, "费用记录缺少稳定编号")
    category = _value(item, "category")
    if category not in {"opex", "capex"}:
        raise HTTPException(422, "费用性质须为opex或capex")
    amount = _yuan_fen(_value(item, "amount_yuan"), "费用金额")
    if amount < 0:
        raise HTTPException(422, "费用金额不能小于零")
    occurrence = _month(_value(item, "occurrence_month"), "费用发生月")
    payment_month = _month(_value(item, "payment_month"), "费用计划支付月")
    paid_on = _date(_value(item, "paid_on"), "费用实际支付日")
    rows = _empty_rows(months)
    if occurrence in rows:
        rows[occurrence][category] = amount
    payment_status = "paid" if paid_on and paid_on <= cutoff else "unpaid"
    effective_payment_month = None
    if paid_on and paid_on <= cutoff:
        effective_payment_month = paid_on.strftime("%Y-%m")
        if effective_payment_month in rows:
            rows[effective_payment_month][f"actual_{category}_paid"] = amount
    else:
        effective_payment_month = payment_month
        if effective_payment_month <= cutoff.strftime("%Y-%m"):
            coverage["expense_payable_yuan"] += amount / 100
            effective_payment_month = months[closed] if closed < 12 else None
            coverage["carried_expenses"].append({
                "expense_id": source_id, "amount_yuan": amount / 100,
                "planned_payment_month": payment_month, "forecast_payment_month": effective_payment_month,
            })
        if effective_payment_month in rows:
            rows[effective_payment_month][f"forecast_{category}_paid"] = amount
    return {
        "source_key": f"expense:{source_id}", "source_type": "expense", "source_id": source_id,
        "expense_id": source_id, "label": str(_value(item, "name", "") or "未命名费用"),
        "months": _public_rows(rows, closed),
        "source_snapshot": {
            "id": source_id, "name": str(_value(item, "name", "") or ""), "category": category,
            "amount_yuan": amount / 100, "occurrence_month": occurrence,
            "payment_month": payment_month, "paid_on": _iso(paid_on),
            "payment_status": payment_status, "effective_payment_month": effective_payment_month,
            "revision": _value(item, "revision"),
        },
    }


def _contract_source(
    contract: Contract,
    *,
    months: list[str],
    closed: int,
    cutoff: date,
    adjustment: dict,
    receivables: Mapping[str, dict],
    statements: Mapping[str, ReceivableStatement],
    payments: Mapping[str, dict],
    coverage: dict,
) -> dict:
    rows = _empty_rows(months)
    valid_period = bool(contract.start_date and contract.end_date and contract.end_date >= contract.start_date)
    if valid_period:
        lease_months = (contract.end_date.year - contract.start_date.year) * 12 + contract.end_date.month - contract.start_date.month + 1
        valid_period = lease_months <= MAX_LEASE_MONTHS
    if valid_period:
        plan = build_rent_plan(contract, financial_only=True)["plan"]
        shifted = _adjusted_plan(contract, adjustment, plan) if adjustment else plan
    else:
        plan = shifted = []
        coverage["excluded_contracts"].append({
            "contract_id": contract.id, "contract_no": contract.contract_no or "",
            "reason": "租期不完整或超出50年测算范围",
        })
    original_by_month = {row["month"]: row for row in plan}
    shifted_by_month = {row["month"]: row for row in shifted}
    original_income = _straight_income(plan)
    shifted_income = original_income if shifted is plan else _straight_income(shifted)
    original_settled = defaultdict(int)
    shifted_settled = defaultdict(int)
    payment_snapshot = []
    shift = adjustment.get("start_shift_months", 0)
    for period, summary in payments.items():
        for payment in summary["payments"]:
            amount = _yuan_fen(payment.get("amount", 0))
            paid_on = _date(payment.get("payment_date"), "收款日期")
            allocation_type = payment.get("allocation_type") or "收款"
            is_cash = allocation_type in CASH_ALLOCATION_TYPES
            payment_snapshot.append({
                "id": payment.get("id"), "period_month": period,
                "payment_date": _iso(paid_on), "amount_yuan": amount / 100,
                "allocation_type": allocation_type, "source": payment.get("source"),
            })
            if paid_on is None:
                key = "undated_cash_yuan" if is_cash else "undated_settlement_yuan"
                coverage[key] += amount / 100
                coverage["unresolved_payments"].append({
                    "contract_id": contract.id, "payment_id": payment.get("id"),
                    "period_month": period, "amount_yuan": amount / 100,
                    "allocation_type": allocation_type, "reason": "缺少实际日期",
                })
                continue
            if paid_on > cutoff:
                continue
            original_settled[period] += amount
            shifted_period = (date.fromisoformat(f"{period}-01") + relativedelta(months=shift)).strftime("%Y-%m") if shift else period
            shifted_settled[shifted_period] += amount
            received_month = paid_on.strftime("%Y-%m")
            if is_cash and received_month in rows:
                rows[received_month]["actual_cash_received"] += amount

    for month, row in rows.items():
        is_closed = int(month[-2:]) <= closed
        lease_row = (original_by_month if is_closed else shifted_by_month).get(month, {})
        billing = receivables.get(month, {})
        use_billing = bool(billing) and (is_closed or not adjustment)
        rent = _yuan_fen(billing.get("rent", 0) if use_billing else lease_row.get("rent", 0))
        fee = _yuan_fen(billing.get("property_fee", 0) if use_billing else lease_row.get("property_fee", 0))
        row["rent_income"] = (original_income if is_closed else shifted_income).get(month, 0)
        row["rent_receivable"] = rent
        row["property_fee_income"] = fee
        row["property_fee_receivable"] = fee
        if not is_closed:
            row["forecast_cash_received"] = max(rent - shifted_settled[month], 0)
            coverage["overpaid_future_rent_yuan"] += max(shifted_settled[month] - rent, 0) / 100
        if billing.get("statement_frozen"):
            coverage["frozen_receivable_months"] += 1

    outstanding = 0
    all_periods = set(original_by_month) | set(statements)
    for period in all_periods:
        if period > cutoff.strftime("%Y-%m"):
            continue
        statement = statements.get(period)
        due = _yuan_fen(statement.rent if statement is not None else original_by_month.get(period, {}).get("rent", 0))
        outstanding += max(due - original_settled[period], 0)
    coverage["past_due_rent_balance_yuan"] += outstanding / 100
    source_snapshot = _contract_snapshot(contract, plan, adjustment)
    source_snapshot["payments"] = sorted(payment_snapshot, key=lambda item: (item["period_month"], item["payment_date"] or "", item["id"] or 0))
    source_snapshot["receivable_statements"] = [
        {"id": item.id, "month": period, "rent_yuan": float(item.rent or 0),
         "property_fee_yuan": float(item.property_fee or 0), "generated_at": _iso(item.generated_at)}
        for period, item in sorted(statements.items())
    ]
    source_snapshot["past_due_rent_balance_yuan"] = outstanding / 100
    source_snapshot["income_method"] = "full_lease_fixed_rent_effective_month_straight_line"
    room_no = "、".join(item.get("room_no", "") for item in contract_room_items(contract)) or contract.room_no or ""
    return {
        "source_key": f"contract:{contract.id}", "source_type": "contract", "source_id": contract.id,
        "contract_id": contract.id, "contract_no": contract.contract_no or "",
        "tenant_name": customer_display_name(contract), "room_no": room_no,
        "start_date": _iso(contract.start_date), "end_date": _iso(contract.end_date),
        "label": contract.contract_no or f"合同{contract.id}",
        "months": _public_rows(rows, closed), "source_snapshot": source_snapshot,
    }


def _indexed_sources(snapshot: Mapping) -> dict[str, dict]:
    result = {}
    for source in snapshot.get("sources", []):
        key = source.get("source_key")
        if not key or key in result:
            raise HTTPException(422, "财务快照来源编号缺失或重复")
        result[key] = source
    return result


def _row_metric(row: Mapping, metric: str) -> int:
    if metric in row:
        return _wan_fen(row[metric], metric)
    if metric == "ebitda":
        return _row_metric(row, "revenue") - _row_metric(row, "operating_cost")
    if metric == "revenue":
        return _row_metric(row, "rent_income") + _row_metric(row, "property_fee_income")
    if metric == "operating_cost":
        return _row_metric(row, "opex")
    return 0


def _aggregate_sources(sources: list[dict], months: list[str], closed: int) -> tuple[list[dict], dict]:
    rows = {month: dict.fromkeys(METRICS, 0) for month in months}
    year = int(months[0][:4])
    for source in sources:
        exact = getattr(source.get('months'), '_fen_rows', None)
        if exact is not None:
            for period, values in exact:
                if period in rows:
                    for metric, amount in zip(METRICS, values):
                        rows[period][metric] += amount
            continue
        for row in source.get("months", []):
            period = _row_period(row, year)
            if period in rows:
                for metric in METRICS:
                    rows[period][metric] += _row_metric(row, metric)
    public = [
        {"month": int(month[-2:]), "period": month, "phase": "management_actual" if int(month[-2:]) <= closed else "forecast",
         **{metric: _wan(row[metric]) for metric in METRICS}}
        for month, row in rows.items()
    ]
    totals = {metric: _wan(sum(row[metric] for row in rows.values())) for metric in METRICS}
    return public, totals


def _freeze_sources(sources: list[dict], previous: Mapping, year: int, closed: int) -> tuple[list[dict], int]:
    through = min(closed, _integer(previous.get("closed_through_month", 0), "原快照截止月份", 0, 12))
    if through == 0:
        return sources, 0
    months = _month_keys(year)
    old_by_key = _indexed_sources(previous)
    current_by_key = {source["source_key"]: source for source in sources}
    result = []
    for key in sorted(set(old_by_key) | set(current_by_key)):
        old = old_by_key.get(key)
        current = current_by_key.get(key)
        source = deepcopy(current or old)
        old_rows = {_row_period(row, year): row for row in old.get("months", [])} if old else {}
        current_rows = {_row_period(row, year): row for row in current.get("months", [])} if current else {}
        source["months"] = []
        for index, month in enumerate(months):
            candidate = (old_rows if index < through else current_rows).get(month, {})
            source["months"].append({
                "month": index + 1, "period": month, "phase": "management_actual" if index < closed else "forecast",
                **{metric: _wan(_row_metric(candidate, metric)) for metric in METRICS},
            })
        snapshot = deepcopy(source.get("source_snapshot", {}))
        snapshot.pop("frozen_periods", None)
        if old:
            old_snapshot = deepcopy(old.get("source_snapshot", {}))
            periods = old_snapshot.pop("frozen_periods", [])
            preserved = []
            for period in periods:
                start = int(period.get("start_month", 1))
                end = min(int(period.get("end_month", through)), through)
                if start <= end:
                    preserved.append({**period, "start_month": start, "end_month": end})
            last_end = max((period["end_month"] for period in preserved), default=0)
            if last_end < through:
                preserved.append({"start_month": last_end + 1, "end_month": through, "source_snapshot": old_snapshot})
            snapshot["frozen_periods"] = preserved
        source["source_snapshot"] = snapshot
        result.append(source)
    return result, through


@reuse_read_calculations
def build_financial_forecast(
    db: Session,
    *,
    building_id: int,
    year: int,
    closed_through_month: int = 0,
    expenses: Iterable[object] = (),
    contract_adjustments: Iterable[object] = (),
    frozen_snapshot: Mapping | None = None,
    _period_month: str | None = None,
) -> dict:
    """Read business records and produce a reproducible management forecast.

    Nothing is inserted, changed or committed. Income is the fixed contracted
    consideration spread over the full effective term, rather than an assertion
    that an accounting ledger has been closed.
    """
    year = _integer(year, "财务年度", 1900, 9998)
    closed = _integer(closed_through_month, "已发生截止月", 0, 12)
    building_id = _integer(building_id, "项目编号", 1, 2**63 - 1)
    months = _month_keys(year)
    source_months = months
    if _period_month is not None:
        # Monthly closing uses the same sources and full-lease evidence, but
        # needs only one month of contract billing and public metric rows.
        period = _month(_period_month, "交接月份")
        if period != f"{year:04d}-{closed:02d}" or frozen_snapshot is not None:
            raise HTTPException(422, "月度计算须与截止月份一致且不使用年度冻结版本")
        source_months = [period]
    cutoff = _cutoff(year, closed)
    contracts = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer), selectinload(Contract.room_items), selectinload(Contract.rent_terms),
            with_loader_criteria(ContractRoom, ContractRoom.building_id == building_id, include_aliases=True),
        )
        .filter(Contract.building_id == building_id, Contract.is_deleted.is_(False))
        .order_by(Contract.id)
        .populate_existing()
        .all()
    )
    contract_ids = [contract.id for contract in contracts]
    adjustments = _normalize_adjustments(contract_adjustments, set(contract_ids))
    if _period_month is not None and adjustments:
        raise HTTPException(422, "月度交接不能使用模拟合同调整")
    reports = get_monthly_receivables_batch(db, source_months, contracts=contracts, building_ids=[building_id], billing_only=True)
    report_rows = defaultdict(dict)
    for month, report in reports.items():
        for row in report["rows"]:
            report_rows[row["contract_id"]][month] = row
    payment_context = _payment_context(db, contract_ids)
    statements = defaultdict(dict)
    if contract_ids:
        for item in db.query(ReceivableStatement).filter(ReceivableStatement.contract_id.in_(contract_ids)).all():
            statements[item.contract_id][item.period_month] = item
    coverage = {
        "income_basis": "合同管理口径",
        "income_method": "全租期固定租金按有效月数直线分摊",
        "cash_cutoff_date": cutoff.isoformat(),
        "cash_forecast_basis": "剩余租金应收于对应未来账期收回；历史欠款另列",
        "property_fee_cash_included": False,
        "expense_basis": "已录费用按发生月计入，实际支付按paid_on；未付计划单列",
        "contract_count": len(contracts), "excluded_contracts": [], "frozen_receivable_months": 0,
        "undated_cash_yuan": 0.0, "undated_settlement_yuan": 0.0, "unresolved_payments": [],
        "past_due_rent_balance_yuan": 0.0, "overpaid_future_rent_yuan": 0.0,
        "expense_payable_yuan": 0.0, "carried_expenses": [], "expense_count": 0,
        "frozen_through_month": 0,
    }
    sources = [
        _contract_source(
            contract, months=source_months, closed=closed, cutoff=cutoff,
            adjustment=adjustments.get(contract.id, {}), receivables=report_rows[contract.id],
            statements=statements[contract.id], payments=payment_context.get(contract.id, {}), coverage=coverage,
        )
        for contract in contracts
    ]
    for expense in expenses:
        expense_building = _value(expense, "building_id", building_id)
        if expense_building != building_id:
            raise HTTPException(404, "费用不存在或不属于当前项目")
        source = _expense_source(expense, months, cutoff, closed, coverage)
        if source:
            sources.append(source)
            coverage["expense_count"] += 1
    from app.services.finance_forecast import fee_sources
    sources.extend(fee_sources(db, building_id, months, cutoff, closed, coverage))
    _indexed_sources({"sources": sources})
    if frozen_snapshot is not None:
        if frozen_snapshot.get("year") != year or frozen_snapshot.get("building_id") != building_id:
            raise HTTPException(422, "已发生期间快照须属于同一项目和年度")
        if any(source.get("source_type") == "budget_adjustment" for source in frozen_snapshot.get("sources", [])):
            raise HTTPException(422, "预算编制调整不能作为已发生期间基准")
        sources, coverage["frozen_through_month"] = _freeze_sources(sources, frozen_snapshot, year, closed)
    for key in ("undated_cash_yuan", "undated_settlement_yuan", "past_due_rent_balance_yuan", "overpaid_future_rent_yuan", "expense_payable_yuan"):
        coverage[key] = _yuan_fen(coverage[key]) / 100
    public_months, totals = _aggregate_sources(sources, source_months, closed)
    return _json_safe({
        "schema_version": SCHEMA_VERSION, "building_id": building_id,
        "year": year, "closed_through_month": closed, "currency": "CNY", "unit": "万元",
        "generated_at": now_in_timezone().isoformat(), "months": public_months,
        "totals": totals, "sources": sources, "coverage": coverage,
    })


def _comparison_values(snapshot: Mapping, metric: str, months: list[str]) -> tuple[int, dict[str, int]]:
    year = int(months[0][:4])
    by_month = {_row_period(row, year): _row_metric(row, metric) for row in snapshot.get("months", [])}
    values = {month: by_month.get(month, 0) for month in months}
    calculated = sum(values.values())
    total = _row_metric(snapshot.get("totals", {}), metric) if metric in snapshot.get("totals", {}) else calculated
    if total != calculated:
        raise HTTPException(422, "财务快照月度金额与年度合计不一致")
    return total, values


def _comparison_reason(source_type: str, before: dict | None, after: dict | None, metric: str) -> str:
    if source_type == "budget_adjustment":
        return "预算编制调整"
    if source_type == "expense":
        return "费用变化"
    if source_type == "contract":
        if before is None:
            return "合同新增"
        if after is None:
            return "合同退出"
        return "合同收款及计划变化" if "cash" in metric else "合同条款变化"
    return "其他差异"


def compare_financial_snapshots(baseline: Mapping, current: Mapping, metric: str = "ebitda") -> dict:
    if metric not in METRICS:
        raise HTTPException(422, "不支持的财务比较指标")
    if any(baseline.get(key) != current.get(key) for key in ("building_id", "year", "currency", "unit")):
        raise HTTPException(422, "仅可比较同一项目、年度及金额口径的财务快照")
    months = _month_keys(_integer(current.get("year"), "财务年度", 1900, 9998))
    year = int(months[0][:4])
    before_total, before_months = _comparison_values(baseline, metric, months)
    after_total, after_months = _comparison_values(current, metric, months)
    before_sources, after_sources = _indexed_sources(baseline), _indexed_sources(current)
    source_before_months = dict.fromkeys(months, 0)
    source_after_months = dict.fromkeys(months, 0)
    items = []
    all_sources = []
    for key in sorted(set(before_sources) | set(after_sources)):
        before, after = before_sources.get(key), after_sources.get(key)
        source = after or before
        old_rows = {_row_period(row, year): row for row in before.get("months", [])} if before else {}
        new_rows = {_row_period(row, year): row for row in after.get("months", [])} if after else {}
        monthly = []
        for month in months:
            old = _row_metric(old_rows.get(month, {}), metric)
            new = _row_metric(new_rows.get(month, {}), metric)
            source_before_months[month] += old
            source_after_months[month] += new
            monthly.append({"month": int(month[-2:]), "period": month, "baseline_value": _wan(old), "current_value": _wan(new), "delta": _wan(new - old)})
        old_total = sum(_wan_fen(row["baseline_value"]) for row in monthly)
        new_total = sum(_wan_fen(row["current_value"]) for row in monthly)
        changed = any(row["delta"] for row in monthly)
        if not any(row["baseline_value"] or row["current_value"] for row in monthly):
            continue
        entry = {
            "source_key": key, "source_type": source.get("source_type", "other"), "source_id": source.get("source_id"),
            "label": source.get("label", key), "contract_id": source.get("contract_id"),
            "expense_id": source.get("expense_id"),
            "reason": _comparison_reason(source.get("source_type", "other"), before, after, metric) if changed else "金额未变",
            "baseline_value": _wan(old_total), "current_value": _wan(new_total),
            "delta": _wan(new_total - old_total), "months": monthly,
        }
        all_sources.append(entry)
        if changed:
            items.append(entry)
    residual_months = [
        {"month": int(month[-2:]), "period": month,
         "baseline_value": _wan(before_months[month] - source_before_months[month]),
         "current_value": _wan(after_months[month] - source_after_months[month]),
         "delta": _wan((after_months[month] - source_after_months[month]) - (before_months[month] - source_before_months[month]))}
        for month in months
    ]
    if any(row["baseline_value"] or row["current_value"] for row in residual_months):
        old = sum(_wan_fen(row["baseline_value"]) for row in residual_months)
        new = sum(_wan_fen(row["current_value"]) for row in residual_months)
        changed = any(row["delta"] for row in residual_months)
        entry = {
            "source_key": "other:unattributed", "source_type": "other", "source_id": None,
            "label": "其他未分配差异", "reason": "其他差异" if changed else "金额未变", "baseline_value": _wan(old),
            "current_value": _wan(new), "delta": _wan(new - old), "months": residual_months,
        }
        all_sources.append(entry)
        if changed:
            items.append(entry)
    items.sort(key=lambda item: (-abs(_wan_fen(item["delta"])), item["source_key"]))
    if sum(_wan_fen(item["delta"]) for item in items) != after_total - before_total:
        raise HTTPException(422, "财务差异明细无法与总差额核对")
    if (
        sum(_wan_fen(item["baseline_value"]) for item in all_sources) != before_total
        or sum(_wan_fen(item["current_value"]) for item in all_sources) != after_total
    ):
        raise HTTPException(422, "财务来源明细无法与年度合计核对")
    for index, period in enumerate(months):
        if (
            sum(_wan_fen(item["months"][index]["baseline_value"]) for item in all_sources) != before_months[period]
            or sum(_wan_fen(item["months"][index]["current_value"]) for item in all_sources) != after_months[period]
        ):
            raise HTTPException(422, "财务来源明细无法与月度合计核对")
    return {
        "metric": metric, "baseline_total": _wan(before_total), "current_total": _wan(after_total),
        "delta": _wan(after_total - before_total), "items": items, "all_sources": all_sources,
    }
