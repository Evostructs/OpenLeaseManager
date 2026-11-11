from __future__ import annotations

import hashlib
import json

from collections import defaultdict
from datetime import date
from typing import Iterable

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.money import round_money
from app.core.time import now_in_timezone, today_in_timezone
from app.models import Contract, ReceivablePayment, ReceivablePaymentOperation, ReceivableRecord, ReceivableStatement
from app.schemas import ReceivableMetadataPayload, ReceivablePaymentPayload, ReceivablePaymentReceipt, ReceivableRecordPayload
from app.services.common import (
    contract_monthly_property_fee,
    contract_room_items,
    contract_room_summary,
    customer_display_name,
    log_action,
)
from app.services.contract_rent import (
    RENT_BASE_TERM_TYPES,
    RENT_TERM_LABELS,
    _month_bounds,
    _month_rent_detail,
    _rent_term_effects_for_month,
)
from app.services.legacy_demo_a_scope import ObjectScope


INVOICE_STATUSES = {"未开票", "待开票", "已开票", "无需开票", "红冲/作废"}


DUNNING_STATUSES = {"未催收", "已提醒", "催收中", "已升级", "无需催收"}


PAYMENT_ALLOCATION_TYPES = {"收款", "冲抵", "减免", "坏账"}


CASH_ALLOCATION_TYPES = {"收款"}


LEGACY_PAYMENT_SOURCE = "legacy_summary"


DUNNING_BALANCE_THRESHOLD = 10000


DUNNING_AGING_THRESHOLD_DAYS = 30


AGING_BUCKETS = [
    ("current", "未到期未收", 0, 0),
    ("1_30", "1-30天", 1, 30),
    ("31_60", "31-60天", 31, 60),
    ("61_90", "61-90天", 61, 90),
    ("over_90", "90天以上", 91, None),
]


def _normalize_receivable_building_ids(building_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if building_ids is None:
        return None
    return tuple(dict.fromkeys(int(item) for item in building_ids))


def _aging_bucket_key(aging_days: int) -> str:
    for key, _label, start, end in AGING_BUCKETS:
        if aging_days >= start and (end is None or aging_days <= end):
            return key
    return "current"


def _empty_aging_buckets() -> dict:
    return {key: {"label": label, "amount": 0, "count": 0} for key, label, _start, _end in AGING_BUCKETS}


def _parse_receivable_month(month: str | None) -> tuple[str, date]:
    if not month:
        month = today_in_timezone().strftime("%Y-%m")
    try:
        target = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(400, "月份格式错误，应为 YYYY-MM") from exc
    return month, target


def _receivable_due_date(contract: Contract, month_start: date, month_end: date) -> date:
    """应收到期日：合同约定付款日（每月 X 日，月末自动收口），未约定按月末。"""
    due_day = getattr(contract, "payment_due_day", None)
    if due_day:
        return month_start.replace(day=min(int(due_day), month_end.day))
    return month_end


def _ensure_month_within_contract(contract: Contract, month: str, target: date) -> None:
    """核销/应收记录月份必须落在合同租期内。

    租期外的月份不会出现在任何台账与报表中，写入的金额会"沉没"，
    商用前必须拦截这类录入错误。
    """
    if not contract.start_date or not contract.end_date:
        raise HTTPException(400, "合同缺少起止日期，无法登记该月份的应收/核销")
    month_start, month_end = _month_bounds(target)
    if contract.start_date > month_end or contract.end_date < month_start:
        raise HTTPException(
            400,
            f"月份 {month} 不在合同租期（{contract.start_date.isoformat()} ~ {contract.end_date.isoformat()}）内，"
            "请确认月份或先通过变更/续租流程调整合同租期",
        )


def _ensure_no_base_term_overlap(contract: Contract, data: dict, exclude_term_id: int | None = None) -> None:
    """基础租金条款（固定月租/面积单价/阶梯）时间重叠会按天叠加双重计费，必须拦截。

    判定口径：合同级条款与任何条款同期即重叠；房源级条款仅与同房源或合同级条款比较。
    """
    if data.get("rule_type") not in RENT_BASE_TERM_TYPES:
        return
    new_room_id = data.get("contract_room_id")
    new_room_no = str(data.get("room_no") or "").strip()
    new_contract_scope = not new_room_id and not new_room_no
    for term in contract.rent_terms or []:
        if exclude_term_id is not None and term.id == exclude_term_id:
            continue
        if term.rule_type not in RENT_BASE_TERM_TYPES:
            continue
        if not term.start_date or not term.end_date:
            continue
        if not (term.start_date <= data["end_date"] and term.end_date >= data["start_date"]):
            continue
        term_room_no = str(term.room_no or "").strip()
        term_contract_scope = not term.contract_room_id and not term_room_no
        same_scope = (
            new_contract_scope
            or term_contract_scope
            or bool(new_room_id and term.contract_room_id == new_room_id)
            or bool(new_room_no and term_room_no == new_room_no)
        )
        if same_scope:
            label = RENT_TERM_LABELS.get(term.rule_type, term.rule_type or "基础租金")
            raise HTTPException(
                400,
                f"与现有{label}条款（{term.start_date} ~ {term.end_date}）时间重叠，"
                "基础租金条款重叠会导致同期双重计费，请先调整既有条款",
            )


def _settled_month_receivables(db: Session, contract: Contract) -> dict[str, float]:
    """返回当前口径下已结清/超收月份的应收额 {month: receivable}。"""
    payments_by_month: dict[str, list[ReceivablePayment]] = defaultdict(list)
    for payment in db.query(ReceivablePayment).filter(ReceivablePayment.contract_id == contract.id).all():
        payments_by_month[payment.period_month].append(payment)
    if not payments_by_month:
        return {}
    records_by_month = {
        record.period_month: record
        for record in db.query(ReceivableRecord).filter(ReceivableRecord.contract_id == contract.id).all()
    }
    frozen_months = {
        stmt.period_month
        for stmt in db.query(ReceivableStatement.period_month)
        .filter(ReceivableStatement.contract_id == contract.id)
        .all()
    }
    settled = {}
    for month, payments in payments_by_month.items():
        if month in frozen_months:
            continue  # 已出账月份金额已固化，推导口径变化不影响账单
        try:
            anchor = date.fromisoformat(f"{month}-01")
        except ValueError:
            continue
        receivable = round_money(_month_rent_detail(contract, anchor)["rent"])
        if receivable <= 0:
            continue
        summary = _payment_summary(payments, records_by_month.get(month))
        if float(summary["settlement_amount"] or 0) >= receivable - 0.005:
            settled[month] = receivable
    return settled


def _ensure_settled_months_unchanged(db: Session, contract: Contract, before: dict[str, float]) -> None:
    """已结清/超收月份的应收是历史账目，推导口径变化必须显式拦截。

    调用前提：本次变更已 flush 进当前事务；本函数会重新推导这些月份的应收，
    发现金额变化时回滚整个事务并抛 409。
    """
    if not before:
        return
    db.flush()
    db.expire(contract, ["rent_terms", "room_items"])
    changed = []
    for month, old_amount in before.items():
        anchor = date.fromisoformat(f"{month}-01")
        new_amount = round_money(_month_rent_detail(contract, anchor)["rent"])
        if abs(new_amount - old_amount) > 0.005:
            changed.append((month, old_amount, new_amount))
    if changed:
        db.rollback()
        detail = "；".join(f"{month}：{old:.2f} → {new:.2f}" for month, old, new in sorted(changed)[:5])
        raise HTTPException(
            409,
            f"该操作会改变已结清月份的应收（{detail}），为保护历史账目已拦截；"
            "如确需调整，请先在月度应收中处理对应月份的收款/核销记录",
        )


def _validate_receivable_metadata(
    invoice_status: str | None,
    dunning_status: str | None,
    invoice_amount: float | None = None,
    invoice_tax_rate: float | None = None,
) -> None:
    if invoice_status and invoice_status not in INVOICE_STATUSES:
        raise HTTPException(400, "发票状态无效")
    if dunning_status and dunning_status not in DUNNING_STATUSES:
        raise HTTPException(400, "催收状态无效")
    if invoice_amount is not None and float(invoice_amount or 0) < 0:
        raise HTTPException(400, "开票金额不能为负数")
    if invoice_tax_rate is not None and float(invoice_tax_rate or 0) < 0:
        raise HTTPException(400, "税率不能为负数")


def _receivable_metadata_changes(contract: Contract, payload) -> dict:
    return {
        "invoice_status": payload.invoice_status or "未开票",
        "invoice_no": payload.invoice_no or "",
        "invoice_date": payload.invoice_date,
        "invoice_amount": round_money(float(payload.invoice_amount or 0)),
        "invoice_tax_rate": round(float(payload.invoice_tax_rate or 0), 4),
        "invoice_notes": payload.invoice_notes or "",
        "dunning_status": payload.dunning_status or "未催收",
        "dunning_owner": payload.dunning_owner or contract.internal_contact_person or "",
        "last_dunning_date": payload.last_dunning_date,
        "next_follow_up_date": payload.next_follow_up_date,
        "dunning_notes": payload.dunning_notes or "",
        "notes": payload.notes or "",
    }


def _dunning_risk(balance: float, aging_days: int, next_follow_up_date: date | None) -> dict:
    today = today_in_timezone()
    follow_up_due = bool(next_follow_up_date and next_follow_up_date <= today)
    is_overdue = balance > 0 and aging_days > 0
    requires = (is_overdue and (balance >= DUNNING_BALANCE_THRESHOLD or aging_days >= DUNNING_AGING_THRESHOLD_DAYS)) or follow_up_due
    if not requires:
        return {"requires_dunning": False, "dunning_priority": "", "follow_up_due": follow_up_due}
    if aging_days >= 90 or (is_overdue and balance >= 50000):
        priority = "高"
    elif aging_days >= DUNNING_AGING_THRESHOLD_DAYS or (is_overdue and balance >= DUNNING_BALANCE_THRESHOLD) or follow_up_due:
        priority = "中"
    else:
        priority = "低"
    return {"requires_dunning": True, "dunning_priority": priority, "follow_up_due": follow_up_due}


def _ensure_receivable_record(db: Session, contract: Contract, month: str, operator: str) -> ReceivableRecord:
    record = (
        db.query(ReceivableRecord)
        .filter(ReceivableRecord.contract_id == contract.id, ReceivableRecord.period_month == month)
        .first()
    )
    if record:
        return record
    record = ReceivableRecord(
        contract_id=contract.id,
        period_month=month,
        dunning_owner=contract.internal_contact_person or "",
        operator=operator,
    )
    db.add(record)
    return record


def _receivable_payment_out(payment: ReceivablePayment) -> dict:
    return {
        "id": payment.id,
        "contract_id": payment.contract_id,
        "month": payment.period_month,
        "payment_date": payment.payment_date.isoformat() if payment.payment_date else "",
        "amount": round_money(payment.amount or 0),
        "allocation_type": payment.allocation_type or "收款",
        "payment_method": payment.payment_method or "",
        "reference_no": payment.reference_no or "",
        "notes": payment.notes or "",
        "operator": payment.operator or "",
        "source": payment.source or "manual",
        "created_at": payment.created_at.isoformat() if payment.created_at else "",
    }


def _legacy_receivable_payment_out(record: ReceivableRecord) -> dict:
    return {
        "id": None,
        "contract_id": record.contract_id,
        "month": record.period_month,
        "payment_date": record.received_date.isoformat() if record.received_date else "",
        "amount": round_money(record.received_amount or 0),
        "allocation_type": "收款",
        "payment_method": "",
        "reference_no": "",
        "notes": record.notes or "",
        "operator": record.operator or "",
        "source": LEGACY_PAYMENT_SOURCE,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }


def _payment_summary(payments: list[ReceivablePayment], record: ReceivableRecord | None) -> dict:
    rows = [_receivable_payment_out(payment) for payment in payments]
    if not rows and record and (record.received_amount or 0) > 0:
        rows.append(_legacy_receivable_payment_out(record))

    cash_received = 0.0
    adjustment_amount = 0.0
    settlement_amount = 0.0
    cash_dates: list[str] = []
    for row in rows:
        amount = float(row.get("amount") or 0)
        allocation_type = row.get("allocation_type") or "收款"
        settlement_amount += amount
        if allocation_type in CASH_ALLOCATION_TYPES:
            cash_received += amount
            if row.get("payment_date"):
                cash_dates.append(row["payment_date"])
        else:
            adjustment_amount += amount

    rows.sort(key=lambda item: (item.get("payment_date") or "", item.get("id") or 0), reverse=True)
    return {
        "payments": rows,
        "payment_count": len(rows),
        "received_amount": round_money(cash_received),
        "adjustment_amount": round_money(adjustment_amount),
        "settlement_amount": round_money(settlement_amount),
        "received_date": max(cash_dates) if cash_dates else "",
    }


def _rank_debtors(rows: list[dict], limit: int = 8) -> list[dict]:
    debtors = {}
    for row in rows:
        balance = float(row.get("balance") or 0)
        if balance <= 0:
            continue
        tenant = (row.get("tenant_name") or "").strip() or "未标注客户"
        entry = debtors.setdefault(
            tenant,
            {
                "tenant_name": tenant,
                "balance": 0,
                "overdue_balance": 0,
                "not_due_balance": 0,
                "receivable": 0,
                "received": 0,
                "contracts": set(),
                "rooms": set(),
                "owners": set(),
                "max_aging_days": 0,
                "dunning_statuses": set(),
                "invoice_statuses": set(),
            },
        )
        entry["balance"] += balance
        entry["overdue_balance"] += float(row.get("overdue_balance") or 0)
        entry["not_due_balance"] += float(row.get("not_due_balance") or 0)
        entry["receivable"] += float(row.get("total") or 0)
        entry["received"] += float(row.get("received_amount") or 0)
        if row.get("contract_no"):
            entry["contracts"].add(row["contract_no"])
        if row.get("room_no"):
            entry["rooms"].update(part for part in str(row["room_no"]).split("、") if part)
        if row.get("dunning_owner"):
            entry["owners"].add(row["dunning_owner"])
        if row.get("dunning_status"):
            entry["dunning_statuses"].add(row["dunning_status"])
        if row.get("invoice_status"):
            entry["invoice_statuses"].add(row["invoice_status"])
        entry["max_aging_days"] = max(entry["max_aging_days"], int(row.get("aging_days") or 0))

    ranked = []
    for entry in debtors.values():
        ranked.append(
            {
                "tenant_name": entry["tenant_name"],
                "balance": round_money(entry["balance"]),
                "overdue_balance": round_money(entry["overdue_balance"]),
                "not_due_balance": round_money(entry["not_due_balance"]),
                "receivable": round_money(entry["receivable"]),
                "received": round_money(entry["received"]),
                "contract_count": len(entry["contracts"]),
                "contracts": sorted(entry["contracts"]),
                "rooms": sorted(entry["rooms"]),
                "owners": sorted(entry["owners"]),
                "owner": "、".join(sorted(entry["owners"])) or "",
                "max_aging_days": entry["max_aging_days"],
                "dunning_statuses": sorted(entry["dunning_statuses"]),
                "invoice_statuses": sorted(entry["invoice_statuses"]),
            }
        )
    ranked.sort(key=lambda item: (-item["overdue_balance"], -item["balance"], -item["max_aging_days"], item["tenant_name"]))
    return ranked[:limit]


def _rank_dunning_alerts(rows: list[dict], limit: int = 8) -> list[dict]:
    priority_weight = {"高": 3, "中": 2, "低": 1}
    alerts = [
        {
            "contract_id": row.get("contract_id"),
            "contract_no": row.get("contract_no"),
            "tenant_name": row.get("tenant_name"),
            "room_no": row.get("room_no"),
            "balance": row.get("balance") or 0,
            "overdue_balance": row.get("overdue_balance") or 0,
            "aging_days": row.get("aging_days") or 0,
            "dunning_status": row.get("dunning_status") or "未催收",
            "dunning_owner": row.get("dunning_owner") or "",
            "dunning_priority": row.get("dunning_priority") or "",
            "next_follow_up_date": row.get("next_follow_up_date") or "",
            "follow_up_due": bool(row.get("follow_up_due")),
        }
        for row in rows
        if row.get("requires_dunning")
    ]
    alerts.sort(
        key=lambda item: (
            -priority_weight.get(item["dunning_priority"], 0),
            -float(item["balance"] or 0),
            -int(item["aging_days"] or 0),
            item["tenant_name"] or "",
        )
    )
    return alerts[:limit]


def build_rent_plan(contract: Contract, *, include_term_effects: bool = True, financial_only: bool = False) -> dict:
    from app.services.contract_rent import rent_plan_month_details

    if not contract.start_date or not contract.end_date:
        return {"plan": [], "summary": {}}

    total_rent = 0
    total_property = 0
    plan = []

    for current, month_end, detail in rent_plan_month_details(contract):
        month_start = current
        if financial_only:
            # Keep the full lease and the same billing engine for audit evidence,
            # without formatting UI-only fields or calculating unused totals.
            property_fee = 0.0
            if contract.property_fee_rate:
                summary = contract_room_summary(contract, month_start, month_end)
                property_fee = round_money(contract_monthly_property_fee(contract, summary))
            plan.append({
                "month": f"{current.year:04d}-{current.month:02d}",
                **{key: detail.get(key, 0) for key in (
                    "rent", "rent_ratio", "lease_days", "month_days", "rent_free_amount",
                    "temporary_adjustment_amount", "rent_source", "gross_rent",
                )},
                "property_fee": property_fee,
            })
            continue
        summary = contract_room_summary(contract, month_start, month_end)
        month_idx = detail["month_idx"]
        rent_free = detail["rent_free"]
        rent = detail["rent"]
        term_summary, term_effects = (
            _rent_term_effects_for_month(contract, month_start, month_end, detail)
            if include_term_effects else ("", [])
        )
        property_fee = contract_monthly_property_fee(contract, summary)
        total_rent += rent
        total_property += property_fee
        plan.append(
            {
                "month": current.strftime("%Y-%m"),
                "month_idx": month_idx,
                "lease_days": detail["lease_days"],
                "month_days": detail["month_days"],
                "rent_ratio": detail["rent_ratio"],
                "rent": rent,
                "rent_free": rent_free,
                "rent_free_amount": detail.get("rent_free_amount", 0),
                "temporary_adjustment": detail.get("temporary_adjustment", False),
                "temporary_adjustment_amount": detail.get("temporary_adjustment_amount", 0),
                "temporary_adjustment_attention": detail.get("temporary_adjustment_attention", False),
                "gross_rent": detail.get("gross_rent", rent),
                "rent_source": detail.get("rent_source", "legacy"),
                "term_summary": term_summary,
                "term_effects": term_effects,
                "property_fee": round_money(property_fee),
                "property_free": False,
                "total": round_money(rent + property_fee),
            }
        )

    if financial_only or not include_term_effects:
        # Financial snapshots use the amounts and dates, not UI descriptions or
        # the upcoming-twelve-month preview. Public plan APIs retain the default.
        return {"plan": plan}
    preview_start = max(contract.start_date.replace(day=1), today_in_timezone().replace(day=1))
    preview = [row for row in plan if date.fromisoformat(f"{row['month']}-01") >= preview_start][:12]
    return {
        "plan": plan,
        "preview": preview,
        "summary": {
            "total_months": len(plan),
            "free_months": contract.free_period or 0,
            "preview_months": len(preview),
            "prop_free_months": 0,
            "total_rent": round_money(total_rent),
            "total_property": round_money(total_property),
            "total_all": round_money(total_rent + total_property),
            "rent_source": "rent_terms" if getattr(contract, "rent_terms", None) else "legacy",
        },
    }


def _build_receivable_batch_context(
    db: Session,
    months: list[str],
    *,
    contract_ids: list[int] | None = None,
    contracts: list[Contract] | None = None,
    building_ids: Iterable[int] | None = None,
    use_statements: bool = True,
) -> dict:
    """Load shared receivable inputs once for a multi-month calculation."""
    normalized_months = sorted({_parse_receivable_month(month)[0] for month in months})
    requested_ids = set(contract_ids) if contract_ids is not None else None
    selected_building_ids = _normalize_receivable_building_ids(building_ids)

    if contracts is None:
        contract_query = (
            db.query(Contract)
            .options(
                selectinload(Contract.customer),
                selectinload(Contract.room_items),
                selectinload(Contract.rent_terms),
            )
            .filter(Contract.is_deleted.is_(False))
        )
        if requested_ids is not None:
            contract_query = contract_query.filter(Contract.id.in_(requested_ids or {-1}))
        if selected_building_ids is not None:
            contract_query = contract_query.filter(Contract.building_id.in_(selected_building_ids or (-1,)))
        loaded_contracts = contract_query.all()
    else:
        loaded_contracts = [
            contract
            for contract in contracts
            if not contract.is_deleted
            and (requested_ids is None or contract.id in requested_ids)
            and (selected_building_ids is None or contract.building_id in selected_building_ids)
        ]

    loaded_ids = [contract.id for contract in loaded_contracts]
    statements_by_month: dict[str, dict[int, ReceivableStatement]] = defaultdict(dict)
    records_by_month: dict[str, dict[int, ReceivableRecord]] = defaultdict(dict)
    payments_by_month: dict[str, dict[int, list[ReceivablePayment]]] = defaultdict(lambda: defaultdict(list))

    if normalized_months and loaded_ids:
        if use_statements:
            for statement in (
                db.query(ReceivableStatement)
                .filter(
                    ReceivableStatement.period_month.in_(normalized_months),
                    ReceivableStatement.contract_id.in_(loaded_ids),
                )
                .all()
            ):
                statements_by_month[statement.period_month][statement.contract_id] = statement

        for record in (
            db.query(ReceivableRecord)
            .filter(
                ReceivableRecord.period_month.in_(normalized_months),
                ReceivableRecord.contract_id.in_(loaded_ids),
            )
            .all()
        ):
            records_by_month[record.period_month][record.contract_id] = record

        for payment in (
            db.query(ReceivablePayment)
            .filter(
                ReceivablePayment.period_month.in_(normalized_months),
                ReceivablePayment.contract_id.in_(loaded_ids),
            )
            .all()
        ):
            payments_by_month[payment.period_month][payment.contract_id].append(payment)

    return {
        "contracts": loaded_contracts,
        "statements_by_month": statements_by_month,
        "records_by_month": records_by_month,
        "payments_by_month": payments_by_month,
    }


def get_monthly_receivables_batch(
    db: Session,
    months: list[str],
    *,
    contract_ids: list[int] | None = None,
    contracts: list[Contract] | None = None,
    building_ids: Iterable[int] | None = None,
    use_statements: bool = True,
    billing_only: bool = False,
) -> dict[str, dict]:
    """Calculate several months while reusing the same ORM query results."""
    normalized_months = list(dict.fromkeys(_parse_receivable_month(month)[0] for month in months))
    context = _build_receivable_batch_context(
        db,
        normalized_months,
        contract_ids=contract_ids,
        contracts=contracts,
        building_ids=building_ids,
        use_statements=use_statements,
    )
    return {
        month: get_monthly_receivables(
            db,
            month,
            contract_ids=contract_ids,
            building_ids=building_ids,
            use_statements=use_statements,
            _batch_context=context,
            _billing_only=billing_only,
        )
        for month in normalized_months
    }


def get_monthly_receivables(
    db: Session,
    month: str | None,
    invoice_status: str | None = None,
    contract_ids: list[int] | None = None,
    use_statements: bool = True,
    _batch_context: dict | None = None,
    building_ids: Iterable[int] | None = None,
    _billing_only: bool = False,
) -> dict:
    month, target = _parse_receivable_month(month)
    if invoice_status and invoice_status not in INVOICE_STATUSES:
        raise HTTPException(400, "发票状态无效")
    month_start, month_end = _month_bounds(target)
    rows = []
    total_rent = 0
    total_property = 0
    total_received = 0
    total_adjustment = 0
    total_settled = 0
    total_balance = 0
    total_overdue_balance = 0
    total_not_due_balance = 0
    total_overpaid = 0
    unpaid_count = 0
    overdue_count = 0
    aging_buckets = _empty_aging_buckets()
    requested_contract_ids = set(contract_ids) if contract_ids is not None else None
    selected_building_ids = _normalize_receivable_building_ids(building_ids)
    if _batch_context is not None:
        statements_by_contract_id = (
            dict(_batch_context["statements_by_month"].get(month, {})) if use_statements else {}
        )
        contracts = [
            contract
            for contract in _batch_context["contracts"]
            if (requested_contract_ids is None or contract.id in requested_contract_ids)
            and (selected_building_ids is None or contract.building_id in selected_building_ids)
            and (
                (contract.start_date and contract.end_date and contract.start_date <= month_end and contract.end_date >= month_start)
                or contract.id in statements_by_contract_id
            )
        ]
    else:
        query = (
            db.query(Contract)
            .options(
                selectinload(Contract.customer),
                selectinload(Contract.room_items),
                selectinload(Contract.rent_terms),
            )
            .filter(
                Contract.is_deleted.is_(False),
                Contract.start_date <= month_end,
                Contract.end_date >= month_start,
            )
        )
        if contract_ids is not None:
            query = query.filter(Contract.id.in_(contract_ids or [-1]))
        if selected_building_ids is not None:
            query = query.filter(Contract.building_id.in_(selected_building_ids or (-1,)))
        contracts = query.all()

        statements_by_contract_id: dict[int, ReceivableStatement] = {}
        if use_statements:
            stmt_query = db.query(ReceivableStatement).filter(ReceivableStatement.period_month == month)
            if contract_ids is not None:
                stmt_query = stmt_query.filter(ReceivableStatement.contract_id.in_(contract_ids or [-1]))
            if selected_building_ids is not None:
                stmt_query = stmt_query.join(Contract, Contract.id == ReceivableStatement.contract_id).filter(
                    Contract.building_id.in_(selected_building_ids or (-1,))
                )
            statements_by_contract_id = {stmt.contract_id: stmt for stmt in stmt_query.all()}
            # 已固化但因退租截断等不再落在租期窗口内的合同，仍按固化账单展示
            loaded_ids = {contract.id for contract in contracts}
            missing_ids = [cid for cid in statements_by_contract_id if cid not in loaded_ids]
            if missing_ids:
                extra = (
                    db.query(Contract)
                    .options(
                        selectinload(Contract.customer),
                        selectinload(Contract.room_items),
                        selectinload(Contract.rent_terms),
                    )
                    .filter(Contract.is_deleted.is_(False), Contract.id.in_(missing_ids))
                    .all()
                )
                if selected_building_ids is not None:
                    extra = [contract for contract in extra if contract.building_id in selected_building_ids]
                contracts = contracts + extra

    contract_ids = [contract.id for contract in contracts]
    records_by_contract_id = {}
    payments_by_contract_id: dict[int, list[ReceivablePayment]] = defaultdict(list)
    if contract_ids:
        if _batch_context is not None:
            records_by_contract_id = {
                row_contract_id: record
                for row_contract_id, record in _batch_context["records_by_month"].get(month, {}).items()
                if row_contract_id in contract_ids
            }
            for row_contract_id, payments in _batch_context["payments_by_month"].get(month, {}).items():
                if row_contract_id in contract_ids:
                    payments_by_contract_id[row_contract_id].extend(payments)
        else:
            records_by_contract_id = {
                record.contract_id: record
                for record in db.query(ReceivableRecord)
                .filter(
                    ReceivableRecord.period_month == month,
                    ReceivableRecord.contract_id.in_(contract_ids),
                )
                .all()
            }
            for payment in (
                db.query(ReceivablePayment)
                .filter(
                    ReceivablePayment.period_month == month,
                    ReceivablePayment.contract_id.in_(contract_ids),
                )
                .all()
            ):
                payments_by_contract_id[payment.contract_id].append(payment)

    for contract in contracts:
        if not contract.start_date or not contract.end_date:
            continue
        # 不按"已退租"状态跳过：退租只截断 end_date，截断前的历史月份
        # （含未收余额）必须继续出现在台账与风险统计中。
        summary = contract_room_summary(contract, month_start, month_end)
        statement = statements_by_contract_id.get(contract.id)
        if not summary["room_no"] and statement is None:
            continue
        detail = _month_rent_detail(contract, target)
        rent = detail["rent"]
        property_fee = contract_monthly_property_fee(contract, summary)
        if statement is not None:
            # 已出账月份：金额以固化账单为准，条款/合同变更不再回溯影响
            rent = statement.rent
            property_fee = statement.property_fee
        if _billing_only:
            # Forecasts need billing amounts and the frozen-statement flag, not
            # invoice/dunning UI rows, aging calculations or debtor rankings.
            rows.append({'contract_id': contract.id, 'rent': round_money(rent),
                         'property_fee': round_money(property_fee),
                         'statement_frozen': statement is not None})
            continue
        term_summary, term_effects = _rent_term_effects_for_month(contract, month_start, month_end, detail)
        receivable = round_money(rent)
        record = records_by_contract_id.get(contract.id)
        payments = payments_by_contract_id.get(contract.id, [])
        payment_summary = _payment_summary(payments, record)
        received = payment_summary["received_amount"]
        adjustment = payment_summary["adjustment_amount"]
        settled = payment_summary["settlement_amount"]
        balance = round_money(max(receivable - settled, 0))
        overpaid = round_money(max(settled - receivable, 0))
        today = today_in_timezone()
        due_date = _receivable_due_date(contract, month_start, month_end)
        is_overdue = balance > 0 and due_date < today
        aging_days = (today - due_date).days if is_overdue else 0
        overdue_balance = balance if is_overdue else 0
        not_due_balance = balance if balance > 0 and not is_overdue else 0
        dunning_owner = record.dunning_owner if record and record.dunning_owner else (contract.internal_contact_person or "")
        invoice_status_value = record.invoice_status if record else "未开票"
        if invoice_status and invoice_status_value != invoice_status:
            continue
        if receivable <= 0:
            payment_status = "无应收"
        elif overpaid > 0:
            payment_status = "超收"
        elif balance <= 0:
            payment_status = "已结清"
        elif settled > 0 and received > 0:
            payment_status = "部分收款"
        elif settled > 0:
            payment_status = "部分核销"
        else:
            payment_status = "未收款"
        bucket = aging_buckets[_aging_bucket_key(aging_days)]
        if balance > 0:
            bucket["amount"] = round_money(bucket["amount"] + balance)
            bucket["count"] += 1
            unpaid_count += 1
        if is_overdue:
            overdue_count += 1
            total_overdue_balance += overdue_balance

        total_rent += rent
        total_property += property_fee
        total_received += received
        total_adjustment += adjustment
        total_settled += settled
        total_balance += balance
        total_not_due_balance += not_due_balance
        total_overpaid += overpaid

        next_follow_up_date = record.next_follow_up_date if record else None
        dunning_risk = _dunning_risk(balance, aging_days, next_follow_up_date)
        rows.append(
            {
                "contract_id": contract.id,
                "customer_id": contract.customer_id,
                "contract_no": contract.contract_no,
                "tenant_name": customer_display_name(contract),
                "room_no": summary["room_no"],
                "room_items": contract_room_items(contract, month_start, month_end),
                "tower": summary["tower"],
                "area": summary["area"],
                "lease_days": detail["lease_days"],
                "month_days": detail["month_days"],
                "rent_ratio": detail["rent_ratio"],
                "rent": round_money(rent),
                "rent_free": detail["rent_free"],
                "rent_free_amount": detail.get("rent_free_amount", 0),
                "temporary_adjustment": detail.get("temporary_adjustment", False),
                "temporary_adjustment_amount": detail.get("temporary_adjustment_amount", 0),
                "temporary_adjustment_attention": detail.get("temporary_adjustment_attention", False),
                "gross_rent": detail.get("gross_rent", rent),
                "rent_source": "statement" if statement is not None else detail.get("rent_source", "legacy"),
                "statement_frozen": statement is not None,
                "statement_generated_at": statement.generated_at.isoformat() if statement is not None and statement.generated_at else "",
                "term_summary": term_summary,
                "term_effects": term_effects,
                "property_fee": round_money(property_fee),
                "prop_free": False,
                "total": receivable,
                "received_amount": received,
                "received_date": payment_summary["received_date"],
                "adjustment_amount": round_money(adjustment),
                "settlement_amount": round_money(settled),
                "balance": balance,
                "unpaid_balance": balance,
                "overdue_balance": round_money(overdue_balance),
                "not_due_balance": round_money(not_due_balance),
                "overpaid_amount": overpaid,
                "payment_status": payment_status,
                "payment_count": payment_summary["payment_count"],
                "payments": payment_summary["payments"],
                "due_date": due_date.isoformat(),
                "is_overdue": is_overdue,
                "aging_days": aging_days,
                "aging_bucket": bucket["label"],
                "invoice_status": invoice_status_value,
                "invoice_no": record.invoice_no if record else "",
                "invoice_date": record.invoice_date.isoformat() if record and record.invoice_date else "",
                "invoice_amount": round_money(record.invoice_amount or 0) if record else 0,
                "invoice_tax_rate": round(record.invoice_tax_rate or 0, 4) if record else 0,
                "invoice_notes": record.invoice_notes if record else "",
                "dunning_status": record.dunning_status if record else "未催收",
                "dunning_owner": dunning_owner,
                "last_dunning_date": record.last_dunning_date.isoformat() if record and record.last_dunning_date else "",
                "next_follow_up_date": record.next_follow_up_date.isoformat() if record and record.next_follow_up_date else "",
                "dunning_notes": record.dunning_notes if record else "",
                **dunning_risk,
                "internal_contact_person": contract.internal_contact_person or "",
                "receivable_notes": record.notes if record else "",
                "start_date": contract.start_date.isoformat(),
                "end_date": contract.end_date.isoformat(),
            }
        )

    if _billing_only:
        return {'month': month, 'rows': rows}
    rows.sort(key=lambda item: (item["balance"], item["total"]), reverse=True)
    receivable_total = round_money(total_rent)
    return {
        "month": month,
        "invoice_status": invoice_status or "",
        "rows": rows,
        "total_rent": round_money(total_rent),
        "total_property": round_money(total_property),
        "total": receivable_total,
        "total_received": round_money(total_received),
        "total_adjustment": round_money(total_adjustment),
        "total_settled": round_money(total_settled),
        "total_balance": round_money(total_balance),
        "total_unpaid_balance": round_money(total_balance),
        "total_overdue_balance": round_money(total_overdue_balance),
        "total_not_due_balance": round_money(total_not_due_balance),
        "total_overpaid": round_money(total_overpaid),
        "received_rate": round(total_received / receivable_total * 100, 1) if receivable_total > 0 else 0,
        "unpaid_count": unpaid_count,
        "overdue_count": overdue_count,
        "aging_buckets": aging_buckets,
        "key_debtors": _rank_debtors(rows),
        "dunning_alerts": _rank_dunning_alerts(rows),
        "count": len(rows),
    }


def _matches_receivable_keyword(row: dict, keyword: str | None, fields: list[str]) -> bool:
    if not keyword:
        return True
    needle = keyword.strip().lower()
    if not needle:
        return True
    return any(needle in str(row.get(field) or "").lower() for field in fields)


def get_invoice_ledger(
    db: Session,
    month: str | None,
    invoice_status: str | None = None,
    keyword: str | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    report = get_monthly_receivables(db, month, invoice_status, building_ids=building_ids)
    rows = []
    for row in report["rows"]:
        if not _matches_receivable_keyword(row, keyword, ["contract_no", "tenant_name", "room_no", "invoice_no"]):
            continue
        rows.append(
            {
                "contract_id": row["contract_id"],
                "contract_no": row["contract_no"],
                "tenant_name": row["tenant_name"],
                "room_no": row["room_no"],
                "tower": row["tower"],
                "period_month": report["month"],
                "rent": row["rent"],
                "total": row["total"],
                "received_amount": row["received_amount"],
                "received_date": row["received_date"],
                "adjustment_amount": row["adjustment_amount"],
                "settlement_amount": row["settlement_amount"],
                "balance": row["balance"],
                "overpaid_amount": row["overpaid_amount"],
                "payment_count": row["payment_count"],
                "payments": row["payments"],
                "invoice_status": row["invoice_status"],
                "invoice_no": row["invoice_no"],
                "invoice_date": row["invoice_date"],
                "invoice_amount": row["invoice_amount"],
                "invoice_tax_rate": row["invoice_tax_rate"],
                "invoice_notes": row["invoice_notes"],
                "dunning_status": row["dunning_status"],
                "dunning_owner": row["dunning_owner"],
                "last_dunning_date": row["last_dunning_date"],
                "next_follow_up_date": row["next_follow_up_date"],
                "dunning_notes": row["dunning_notes"],
                "internal_contact_person": row["internal_contact_person"],
                "receivable_notes": row["receivable_notes"],
            }
        )

    rows.sort(key=lambda item: (item["invoice_status"] == "已开票", item["balance"], item["rent"]), reverse=False)
    return {
        "month": report["month"],
        "invoice_status": invoice_status or "",
        "keyword": keyword or "",
        "rows": rows,
        "count": len(rows),
        "total_rent": round_money(sum(row["rent"] for row in rows)),
        "total_received": round_money(sum(row["received_amount"] for row in rows)),
        "total_balance": round_money(sum(row["balance"] for row in rows)),
        "total_invoice_amount": round_money(sum(row["invoice_amount"] for row in rows)),
    }


def _follow_up_soon(next_follow_up_date: str | None, days: int = 7) -> bool:
    if not next_follow_up_date:
        return False
    try:
        follow_up = date.fromisoformat(str(next_follow_up_date)[:10])
    except ValueError:
        return False
    today = today_in_timezone()
    return today <= follow_up <= today + relativedelta(days=days)


def get_dunning_ledger(
    db: Session,
    month: str | None,
    dunning_status: str | None = None,
    dunning_owner: str | None = None,
    overdue_only: bool | None = None,
    focus: bool = True,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    if dunning_status and dunning_status not in DUNNING_STATUSES:
        raise HTTPException(400, "催收状态无效")
    report = get_monthly_receivables(db, month, building_ids=building_ids)
    owner_keyword = (dunning_owner or "").strip().lower()
    rows = []
    for row in report["rows"]:
        if dunning_status and row["dunning_status"] != dunning_status:
            continue
        if owner_keyword and owner_keyword not in str(row.get("dunning_owner") or "").lower():
            continue
        is_overdue = bool(row.get("is_overdue"))
        if overdue_only is True and not is_overdue:
            continue
        if overdue_only is False and is_overdue:
            continue
        follow_up_soon = _follow_up_soon(row.get("next_follow_up_date"))
        if focus and not (row["balance"] > 0 or row["overdue_balance"] > 0 or follow_up_soon or row.get("follow_up_due")):
            continue
        rows.append(
            {
                "contract_id": row["contract_id"],
                "contract_no": row["contract_no"],
                "tenant_name": row["tenant_name"],
                "room_no": row["room_no"],
                "tower": row["tower"],
                "period_month": report["month"],
                "rent": row["rent"],
                "total": row["total"],
                "received_amount": row["received_amount"],
                "received_date": row["received_date"],
                "adjustment_amount": row["adjustment_amount"],
                "settlement_amount": row["settlement_amount"],
                "balance": row["balance"],
                "overpaid_amount": row["overpaid_amount"],
                "payment_count": row["payment_count"],
                "payments": row["payments"],
                "overdue_balance": row["overdue_balance"],
                "aging_days": row["aging_days"],
                "is_overdue": is_overdue,
                "invoice_status": row["invoice_status"],
                "invoice_no": row["invoice_no"],
                "invoice_date": row["invoice_date"],
                "invoice_amount": row["invoice_amount"],
                "invoice_tax_rate": row["invoice_tax_rate"],
                "invoice_notes": row["invoice_notes"],
                "dunning_status": row["dunning_status"],
                "dunning_owner": row["dunning_owner"],
                "last_dunning_date": row["last_dunning_date"],
                "next_follow_up_date": row["next_follow_up_date"],
                "dunning_notes": row["dunning_notes"],
                "requires_dunning": row["requires_dunning"],
                "dunning_priority": row["dunning_priority"],
                "follow_up_due": row["follow_up_due"],
                "follow_up_soon": follow_up_soon,
                "internal_contact_person": row["internal_contact_person"],
                "receivable_notes": row["receivable_notes"],
            }
        )

    rows.sort(key=lambda item: (item["is_overdue"], item["follow_up_due"], item["follow_up_soon"], item["balance"]), reverse=True)
    return {
        "month": report["month"],
        "dunning_status": dunning_status or "",
        "dunning_owner": dunning_owner or "",
        "overdue_only": overdue_only,
        "focus": focus,
        "rows": rows,
        "count": len(rows),
        "total_rent": round_money(sum(row["rent"] for row in rows)),
        "total_received": round_money(sum(row["received_amount"] for row in rows)),
        "total_balance": round_money(sum(row["balance"] for row in rows)),
        "total_overdue_balance": round_money(sum(row["overdue_balance"] for row in rows)),
        "overdue_count": sum(1 for row in rows if row["is_overdue"]),
        "follow_up_due_count": sum(1 for row in rows if row["follow_up_due"] or row["follow_up_soon"]),
    }


def upsert_receivable_record(db: Session, contract: Contract, payload: ReceivableRecordPayload, operator: str) -> dict:
    month, target = _parse_receivable_month(payload.month)
    _ensure_month_within_contract(contract, month, target)
    _validate_receivable_metadata(payload.invoice_status, payload.dunning_status, payload.invoice_amount, payload.invoice_tax_rate)

    amount = round_money(float(payload.received_amount or 0))
    if amount < 0:
        raise HTTPException(400, "实收金额不能为负数")

    record = _ensure_receivable_record(db, contract, month, operator)

    changes = {
        "period_month": month,
        "received_amount": amount,
        "received_date": payload.received_date,
        **_receivable_metadata_changes(contract, payload),
    }
    for key, value in changes.items():
        setattr(record, key, value)
    record.operator = operator
    record.updated_at = now_in_timezone()

    legacy_payment = (
        db.query(ReceivablePayment)
        .filter(
            ReceivablePayment.contract_id == contract.id,
            ReceivablePayment.period_month == month,
            ReceivablePayment.source == LEGACY_PAYMENT_SOURCE,
        )
        .first()
    )
    if amount > 0:
        if not legacy_payment:
            legacy_payment = ReceivablePayment(
                contract_id=contract.id,
                period_month=month,
                source=LEGACY_PAYMENT_SOURCE,
                allocation_type="收款",
            )
            db.add(legacy_payment)
        legacy_payment.payment_date = payload.received_date
        legacy_payment.amount = amount
        legacy_payment.notes = payload.notes or ""
        legacy_payment.operator = operator
        legacy_payment.updated_at = now_in_timezone()
    elif legacy_payment:
        db.delete(legacy_payment)

    try:
        log_action(
            db,
            contract.id,
            "receivable_update",
            f"{month} 租金收款更新: 实收 {amount}",
            operator,
            commit=False,
            object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_monthly_receivables(
        db,
        target.strftime("%Y-%m"),
        building_ids=[int(contract.building_id)],
    )


def update_receivable_metadata(db: Session, contract: Contract, payload: ReceivableMetadataPayload, operator: str) -> dict:
    month, target = _parse_receivable_month(payload.month)
    _ensure_month_within_contract(contract, month, target)
    _validate_receivable_metadata(payload.invoice_status, payload.dunning_status, payload.invoice_amount, payload.invoice_tax_rate)
    record = _ensure_receivable_record(db, contract, month, operator)
    for key, value in _receivable_metadata_changes(contract, payload).items():
        setattr(record, key, value)
    record.operator = operator
    record.updated_at = now_in_timezone()
    try:
        log_action(
            db,
            contract.id,
            "receivable_update",
            f"{month} 发票催收状态更新",
            operator,
            commit=False,
            object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_monthly_receivables(
        db,
        target.strftime("%Y-%m"),
        building_ids=[int(contract.building_id)],
    )


def _payment_receipt(operation: ReceivablePaymentOperation, *, replayed: bool) -> dict:
    return ReceivablePaymentReceipt(
        operation_id=operation.operation_id,
        contract_id=operation.contract_id,
        payment_id=operation.payment_id,
        status="recorded" if operation.payment_id is not None else "deleted",
        replayed=replayed,
    ).model_dump()


def get_receivable_payment_receipt(db: Session, contract: Contract, operation_id: str) -> dict:
    operation = db.get(ReceivablePaymentOperation, operation_id)
    if operation is None or operation.contract_id != contract.id:
        raise HTTPException(404, "尚未查到本次收款回执，可使用原操作重试")
    return _payment_receipt(operation, replayed=True)


def _replay_payment(operation: ReceivablePaymentOperation, contract_id: int, request_hash: str) -> dict:
    if operation.contract_id != contract_id or operation.request_hash != request_hash:
        raise HTTPException(409, "本次操作标识已用于其他收款内容，请核对收款明细")
    return _payment_receipt(operation, replayed=True)


def add_receivable_payment(db: Session, contract: Contract, payload: ReceivablePaymentPayload, operator: str) -> dict:
    month, target = _parse_receivable_month(payload.month)
    allocation_type = payload.allocation_type or "收款"
    if allocation_type not in PAYMENT_ALLOCATION_TYPES:
        raise HTTPException(400, "核销类型无效")
    amount = round_money(float(payload.amount or 0))
    if amount <= 0:
        raise HTTPException(400, "核销金额必须大于 0")
    metadata = payload.metadata
    if metadata is not None:
        if not payload.operation_id or metadata.month != month:
            raise HTTPException(400, "联合保存需提供操作标识，且发票催收月份必须与收款月份一致")
        _validate_receivable_metadata(metadata.invoice_status, metadata.dunning_status, metadata.invoice_amount, metadata.invoice_tax_rate)
    values = dict(
        contract_id=contract.id, period_month=month, payment_date=payload.payment_date,
        amount=amount, allocation_type=allocation_type, payment_method=payload.payment_method or "",
        reference_no=payload.reference_no or "", notes=payload.notes or "",
    )
    canonical = {
        **values, "amount": f"{amount:.2f}",
        "payment_date": payload.payment_date.isoformat() if payload.payment_date else None,
        "metadata": metadata.model_dump(mode="json") if metadata else None,
    }
    request_hash = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    operation = None
    contract_id = contract.id
    if payload.operation_id:
        existing = db.get(ReceivablePaymentOperation, payload.operation_id)
        if existing is not None:
            return _replay_payment(existing, contract_id, request_hash)
    # A previously committed operation is replayable even if the lease dates changed later.
    _ensure_month_within_contract(contract, month, target)
    try:
        if payload.operation_id:
            operation = ReceivablePaymentOperation(
                operation_id=payload.operation_id, contract_id=contract_id, request_hash=request_hash,
            )
            db.add(operation)
            # Claim the unique operation before any other writes. Concurrent retries
            # lose this insert and replay the winner after rolling back their transaction.
            db.flush()
        record = _ensure_receivable_record(db, contract, month, operator)
        if metadata is not None:
            for key, value in _receivable_metadata_changes(contract, metadata).items():
                setattr(record, key, value)
            record.operator = operator
            record.updated_at = now_in_timezone()
            log_action(db, contract_id, "receivable_update", f"{month} 发票催收状态更新", operator,
                       commit=False, object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)))
        payment = ReceivablePayment(**values, operator=operator, source="manual")
        db.add(payment)
        db.flush()
        if operation is not None:
            operation.payment_id = payment.id
        log_action(
            db, contract_id, "receivable_payment", f"{month} 新增{allocation_type}: {amount}", operator,
            commit=False, target_type="receivable_payment", target_id=payment.id,
            object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
        )
        # Construct the receipt before commit; no report reads can turn a committed
        # keyed write into an apparent save failure.
        receipt = _payment_receipt(operation, replayed=False) if operation is not None else None
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(ReceivablePaymentOperation, payload.operation_id) if payload.operation_id else None
        if existing is not None:
            return _replay_payment(existing, contract_id, request_hash)
        raise
    except Exception:
        db.rollback()
        raise
    if receipt is not None:
        return receipt
    # Compatibility for callers that have not yet adopted operation IDs.
    return get_monthly_receivables(db, target.strftime("%Y-%m"), building_ids=[int(contract.building_id)])


def delete_receivable_payment(
    db: Session,
    payment_id: int,
    operator: str,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    selected_building_ids = _normalize_receivable_building_ids(building_ids)
    query = (
        db.query(ReceivablePayment)
        .join(Contract, Contract.id == ReceivablePayment.contract_id)
        .filter(
            ReceivablePayment.id == payment_id,
            Contract.is_deleted.is_(False),
        )
    )
    if selected_building_ids is not None:
        query = query.filter(Contract.building_id.in_(selected_building_ids or (-1,)))
    payment = query.first()
    if not payment:
        raise HTTPException(404, "收款核销记录不存在")
    contract_id = payment.contract_id
    contract = db.get(Contract, contract_id)
    if contract is None or contract.building_id is None:
        raise HTTPException(404, "收款核销记录不存在")
    period_month = payment.period_month
    amount = round_money(payment.amount or 0)
    allocation_type = payment.allocation_type or "收款"
    target_id = payment.id
    if payment.source == LEGACY_PAYMENT_SOURCE:
        record = (
            db.query(ReceivableRecord)
            .filter(ReceivableRecord.contract_id == contract_id, ReceivableRecord.period_month == period_month)
            .first()
        )
        if record:
            record.received_amount = 0
            record.received_date = None
            record.updated_at = now_in_timezone()
    # Explicitly keep the tombstone in the same deletion transaction, including
    # legacy database connections where FK enforcement may not be enabled.
    db.query(ReceivablePaymentOperation).filter_by(payment_id=payment.id).update(
        {"payment_id": None}, synchronize_session=False,
    )
    db.delete(payment)
    log_action(
        db,
        contract_id,
        "receivable_payment_delete",
        f"{period_month} 删除{allocation_type}: {amount}",
        operator,
        commit=False,
        target_type="receivable_payment",
        target_id=target_id,
        object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
    )
    db.commit()
    return get_monthly_receivables(
        db,
        period_month,
        building_ids=[int(contract.building_id)],
    )


def generate_receivable_statements(
    db: Session,
    month: str | None,
    operator: str = "system",
    regenerate: bool = False,
    *,
    building_ids: Iterable[int] | None = None,
    object_scope: ObjectScope | None = None,
) -> dict:
    """月度出账：把指定月份各合同的应收金额固化到 receivable_statements。

    已出账的合同默认跳过（regenerate=True 时覆盖重出）；
    固化后该月金额以账单为准，合同/条款变更不再回溯影响。
    """
    month, target = _parse_receivable_month(month)
    selected_building_ids = _normalize_receivable_building_ids(building_ids)
    report = get_monthly_receivables(
        db,
        month,
        building_ids=selected_building_ids,
        use_statements=False,
    )
    statement_query = (
        db.query(ReceivableStatement)
        .join(Contract, Contract.id == ReceivableStatement.contract_id)
        .filter(ReceivableStatement.period_month == month)
    )
    if selected_building_ids is not None:
        statement_query = statement_query.filter(Contract.building_id.in_(selected_building_ids or (-1,)))
    existing = {stmt.contract_id: stmt for stmt in statement_query.all()}
    created = 0
    updated = 0
    skipped = 0
    try:
        for row in report["rows"]:
            contract_id = row.get("contract_id")
            stmt = existing.get(contract_id)
            if stmt is not None and not regenerate:
                skipped += 1
                continue
            if stmt is None:
                stmt = ReceivableStatement(contract_id=contract_id, period_month=month)
                db.add(stmt)
                created += 1
            else:
                updated += 1
            stmt.rent = round_money(row.get("rent"))
            stmt.property_fee = round_money(row.get("property_fee"))
            stmt.total_amount = round_money(stmt.rent + stmt.property_fee)
            stmt.source = "manual" if operator != "system" else "auto"
            stmt.operator = operator
            stmt.generated_at = now_in_timezone()
        log_action(
            db,
            None,
            "statement_generate",
            f"{month} 出账：新增 {created}，覆盖 {updated}，跳过 {skipped}",
            operator,
            commit=False,
            target_type="statement",
            object_scope=object_scope,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": f"{month} 出账完成", "month": month, "created": created, "updated": updated, "skipped": skipped}


def get_receivable_trend(db: Session, anchor: date, months: int = 6) -> list[dict]:
    trend = []
    start = anchor.replace(day=1) + relativedelta(months=-(months - 1))
    for offset in range(months):
        current = start + relativedelta(months=offset)
        report = get_monthly_receivables(db, current.strftime("%Y-%m"))
        trend.append(
            {
                "month": report["month"],
                "total_rent": report["total_rent"],
                "total_received": report["total_received"],
                "total_adjustment": report["total_adjustment"],
                "total_balance": report["total_balance"],
                "total_unpaid_balance": report["total_unpaid_balance"],
                "total_overdue_balance": report["total_overdue_balance"],
                "total_not_due_balance": report["total_not_due_balance"],
                "received_rate": report["received_rate"],
                "unpaid_count": report["unpaid_count"],
                "overdue_count": report["overdue_count"],
            }
        )
    return trend
