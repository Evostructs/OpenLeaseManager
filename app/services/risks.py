from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from app.core.time import today_in_timezone
from app.core.money import round_money
from app.models import Attachment, Contract, ContractDeposit, ContractRoom, CustomerFollowUp, RentTerm
from app.services.common import contract_room_summary, customer_display_name, serialize_deposit
from app.services.contracts import TEMPORARY_ADJUSTMENT_TERM_TYPE, _is_currently_active
from app.services.receivable_history import OVERDUE_LOOKBACK_MONTHS, collect_overdue_history, month_keys_back


CONTRACT_BODY_CATEGORY = "合同正文"
CUSTOMER_LICENSE_CATEGORY = "证照"
DONE_FOLLOW_UP_STATUS = "done"

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
LARGE_ADJUSTMENT_FIXED_FLOOR = 50000
LARGE_ADJUSTMENT_RATIO = 0.5
EXPIRING_DAYS = 90


def _money(value: float | int | None) -> float:
    return round_money(float(value or 0))


def _date_text(value: date | str | None) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def _contract_context(contract: Contract | None, fallback: dict | None = None) -> dict:
    fallback = fallback or {}
    summary = contract_room_summary(contract) if contract else {}
    return {
        "contract_id": contract.id if contract else fallback.get("contract_id"),
        "contract_no": contract.contract_no if contract else fallback.get("contract_no", ""),
        "customer_id": contract.customer_id if contract else fallback.get("customer_id"),
        "customer_name": customer_display_name(contract) if contract else fallback.get("tenant_name", ""),
        "room_no": summary.get("room_no") or fallback.get("room_no", ""),
        "tower": summary.get("tower") or fallback.get("tower", ""),
    }


def _risk(
    stable_key: str,
    risk_type: str,
    severity: str,
    title: str,
    description: str,
    entity_type: str,
    entity_id: int | None,
    action_label: str,
    action_target: dict,
    *,
    amount: float | int | None = None,
    due_date: date | str | None = None,
    occurred_on: date | str | None = None,
    context: dict | None = None,
    meta: dict | None = None,
) -> dict:
    item = {
        "id": stable_key,
        "stable_key": stable_key,
        "risk_type": risk_type,
        "severity": severity,
        "title": title,
        "description": description,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "amount": _money(amount) if amount is not None else None,
        "due_date": _date_text(due_date),
        "occurred_on": _date_text(occurred_on),
        "action_label": action_label,
        "action_target": action_target,
        "status": "open",
        "meta": meta or {},
    }
    item.update(context or {})
    return item


def _overdue_receivable_risks(
    db: Session,
    contracts_by_id: dict[int, Contract],
    anchor: date,
    contract_id: int | None,
    customer_id: int | None,
    monthly_reports: dict[str, dict] | None,
    report_contract_ids: list[int] | None = None,
) -> list[dict]:
    history = collect_overdue_history(
        db,
        months=month_keys_back(anchor, OVERDUE_LOOKBACK_MONTHS),
        contract_ids=report_contract_ids,
        monthly_reports=monthly_reports,
        contracts=list(contracts_by_id.values()),
    )

    risks = []
    for row_contract_id, entry in history["overdue_by_contract"].items():
        if contract_id and row_contract_id != contract_id:
            continue
        contract = contracts_by_id.get(row_contract_id)
        row_customer_id = contract.customer_id if contract else None
        if customer_id and row_customer_id != customer_id:
            continue
        context = _contract_context(contract, entry["sample_row"])
        amount = entry["overdue_balance"]
        days = entry["max_aging_days"]
        severity = entry["severity"]
        risks.append(
            _risk(
                f"overdue_receivable:{row_contract_id}",
                "overdue_receivable",
                severity,
                f"{context.get('customer_name') or context.get('contract_no') or '合同'}存在历史逾期",
                f"历史逾期 {entry['overdue_months']} 个月，最长账龄 {days} 天，历史逾期金额 {amount:.2f}。",
                "receivable",
                row_contract_id,
                "查看应收",
                {"type": "receivable", "contract_id": row_contract_id, "month": entry["latest_month"]},
                amount=amount,
                due_date=entry["due_date"],
                context=context,
                meta={"overdue_months": entry["overdue_months"], "max_aging_days": days, "latest_month": entry["latest_month"]},
            )
        )
    return risks


def _contract_expiring_risks(contracts: list[Contract], contract_id: int | None, customer_id: int | None) -> list[dict]:
    risks = []
    for contract in contracts:
        if contract_id and contract.id != contract_id:
            continue
        if customer_id and contract.customer_id != customer_id:
            continue
        days = contract.days_to_expiry
        if days is None or days < 0 or days > EXPIRING_DAYS:
            continue
        if not _is_currently_active(contract):
            continue
        context = _contract_context(contract)
        severity = "high" if days <= 30 else "medium"
        risks.append(
            _risk(
                f"contract_expiring:{contract.id}",
                "contract_expiring",
                severity,
                f"合同 {contract.contract_no or contract.id} 将在 {days} 天内到期",
                "按 90 天到期预警口径提示，需确认续租、退租或变更安排。",
                "contract",
                contract.id,
                "查看合同",
                {"type": "contract", "contract_id": contract.id},
                due_date=contract.end_date,
                context=context,
                meta={"days_left": days},
            )
        )
    return risks


def _follow_up_overdue_risks(
    db: Session,
    anchor: date,
    contract_id: int | None,
    customer_id: int | None,
    building_ids: tuple[int, ...] | None = None,
) -> list[dict]:
    query = db.query(CustomerFollowUp).filter(
        CustomerFollowUp.next_follow_up_date.isnot(None),
        CustomerFollowUp.next_follow_up_date < anchor,
        or_(CustomerFollowUp.status.is_(None), CustomerFollowUp.status != DONE_FOLLOW_UP_STATUS),
    )
    query = query.options(
        selectinload(CustomerFollowUp.customer),
        selectinload(CustomerFollowUp.contract).selectinload(Contract.room_items),
    )
    if building_ids is not None:
        query = query.options(
            with_loader_criteria(
                ContractRoom,
                ContractRoom.building_id.in_(building_ids or (-1,)),
                include_aliases=True,
            )
        ).filter(CustomerFollowUp.building_id.in_(building_ids or (-1,)))
        query = query.populate_existing()
    if contract_id:
        query = query.filter(CustomerFollowUp.contract_id == contract_id)
    if customer_id:
        query = query.filter(CustomerFollowUp.customer_id == customer_id)
    rows = query.order_by(CustomerFollowUp.next_follow_up_date.asc(), CustomerFollowUp.id.asc()).all()

    risks = []
    for row in rows:
        customer = getattr(row, "customer", None)
        contract = getattr(row, "contract", None)
        if row.contract_id is not None and (
            contract is None
            or contract.building_id is None
            or row.building_id is None
            or int(contract.building_id) != int(row.building_id)
        ):
            raise HTTPException(409, "客户跟进与关联合同楼宇不一致")
        context = _contract_context(contract)
        context.update(
            {
                "customer_id": row.customer_id,
                "customer_name": customer.name if customer else context.get("customer_name", ""),
            }
        )
        overdue_days = (anchor - row.next_follow_up_date).days if row.next_follow_up_date else 0
        severity = "high" if overdue_days >= 7 else "medium"
        risks.append(
            _risk(
                f"follow_up_overdue:{row.id}",
                "follow_up_overdue",
                severity,
                f"{context.get('customer_name') or '客户'}跟进已逾期",
                f"计划跟进日已过去 {overdue_days} 天；下一步：{row.next_action or row.content or '补充跟进动作'}。",
                "customer",
                row.customer_id,
                "查看客户",
                {"type": "customer", "customer_id": row.customer_id},
                due_date=row.next_follow_up_date,
                context=context,
                meta={"follow_up_id": row.id, "overdue_days": overdue_days, "owner": row.owner or ""},
            )
        )
    return risks


def _deposit_unpaid_risks(
    db: Session,
    anchor: date,
    contracts_by_id: dict[int, Contract],
    contract_id: int | None,
    customer_id: int | None,
    building_ids: tuple[int, ...] | None = None,
) -> list[dict]:
    query = db.query(ContractDeposit).join(Contract).filter(Contract.is_deleted.is_(False))
    query = query.options(
        selectinload(ContractDeposit.contract).selectinload(Contract.customer),
        selectinload(ContractDeposit.contract).selectinload(Contract.room_items),
    )
    if building_ids is not None:
        query = query.options(
            with_loader_criteria(
                ContractRoom,
                ContractRoom.building_id.in_(building_ids or (-1,)),
                include_aliases=True,
            )
        ).filter(Contract.building_id.in_(building_ids or (-1,)))
        query = query.populate_existing()
    if contract_id:
        query = query.filter(ContractDeposit.contract_id == contract_id)
    if customer_id:
        query = query.filter(ContractDeposit.customer_id == customer_id)
    deposits = query.order_by(ContractDeposit.due_date.asc(), ContractDeposit.id.asc()).all()

    risks = []
    for deposit in deposits:
        contract = contracts_by_id.get(deposit.contract_id) or getattr(deposit, "contract", None)
        if contract and not _is_currently_active(contract):
            continue
        row = serialize_deposit(deposit)
        unpaid_amount = _money(row.get("unpaid_amount"))
        available_amount = _money(row.get("available_amount"))
        expected_amount = _money(row.get("expected_amount"))
        insufficient_amount = _money(max(expected_amount - available_amount, 0)) if expected_amount > 0 and _money(row.get("received_amount")) >= expected_amount else 0
        amount = unpaid_amount if unpaid_amount > 0 else insufficient_amount
        if amount <= 0:
            continue
        context = _contract_context(contract)
        severity = "high" if amount >= 50000 or (deposit.due_date and deposit.due_date < anchor) else "medium"
        title_suffix = "未收" if unpaid_amount > 0 else "可用余额不足"
        risks.append(
            _risk(
                f"deposit_unpaid:{deposit.id}",
                "deposit_unpaid",
                severity,
                f"押金{title_suffix}",
                f"{row.get('deposit_type') or '押金'}应收 {expected_amount:.2f}，已收 {row.get('received_amount'):.2f}，当前需关注 {amount:.2f}。",
                "deposit",
                deposit.id,
                "查看合同押金",
                {"type": "contract", "contract_id": deposit.contract_id},
                amount=amount,
                due_date=deposit.due_date,
                context=context,
                meta={"deposit_id": deposit.id, "deposit_type": row.get("deposit_type"), "unpaid_amount": unpaid_amount, "insufficient_amount": insufficient_amount},
            )
        )
    return risks


def _attachment_missing_risks(
    db: Session,
    contracts: list[Contract],
    contract_id: int | None,
    customer_id: int | None,
) -> list[dict]:
    active_contracts = [contract for contract in contracts if _is_currently_active(contract)]
    if contract_id:
        active_contracts = [contract for contract in active_contracts if contract.id == contract_id]
    if customer_id:
        active_contracts = [contract for contract in active_contracts if contract.customer_id == customer_id]

    risks = []
    contract_pairs: set[tuple[int, int]] = set()
    for contract in active_contracts:
        if contract.id is None:
            continue
        if contract.building_id is None:
            raise HTTPException(409, "合同尚未明确楼宇归属")
        contract_pairs.add((int(contract.id), int(contract.building_id)))
    if contract_pairs:
        attached_contract_pairs = {
            (int(row[0]), int(row[1]))
            for row in db.query(Attachment.entity_id, Attachment.building_id)
            .filter(
                Attachment.entity_type == "contract",
                Attachment.entity_id.in_({item[0] for item in contract_pairs}),
                Attachment.scope_type == "building",
                Attachment.building_id.in_({item[1] for item in contract_pairs}),
                Attachment.portfolio_id.is_(None),
                Attachment.category == CONTRACT_BODY_CATEGORY,
                Attachment.deleted_at.is_(None),
            )
            .all()
            if row[1] is not None
        }
        for contract in active_contracts:
            contract_pair = (int(contract.id), int(contract.building_id))
            if contract_pair in attached_contract_pairs:
                continue
            context = _contract_context(contract)
            risks.append(
                _risk(
                    f"attachment_missing:contract:{contract.id}:body",
                    "attachment_missing",
                    "medium",
                    "缺合同正文附件",
                    "当前有效合同未归档合同正文，建议补齐附件。",
                    "contract",
                    contract.id,
                    "查看合同附件",
                    {"type": "contract", "contract_id": contract.id},
                    context=context,
                    meta={"missing_category": CONTRACT_BODY_CATEGORY},
                )
            )

    customers = {
        (int(contract.customer_id), int(contract.building_id)): getattr(contract, "customer", None)
        for contract in active_contracts
        if contract.customer_id and contract.building_id is not None and getattr(contract, "customer", None)
    }
    if customers:
        customer_ids = {item[0] for item in customers}
        customer_building_ids = {item[1] for item in customers}
        attached_customer_pairs = {
            (int(row[0]), int(row[1]))
            for row in db.query(Attachment.entity_id, Attachment.building_id)
            .filter(
                Attachment.entity_type == "customer",
                Attachment.entity_id.in_(customer_ids),
                Attachment.scope_type == "building",
                Attachment.building_id.in_(customer_building_ids),
                Attachment.portfolio_id.is_(None),
                Attachment.category == CUSTOMER_LICENSE_CATEGORY,
                Attachment.deleted_at.is_(None),
            )
            .all()
            if row[1] is not None
        }
        for (row_customer_id, row_building_id), customer in customers.items():
            if (row_customer_id, row_building_id) in attached_customer_pairs:
                continue
            risks.append(
                _risk(
                    f"attachment_missing:customer:{row_customer_id}:building:{row_building_id}:license",
                    "attachment_missing",
                    "medium",
                    "缺客户证照附件",
                    "客户有关联合同但未归档证照或营业执照，建议补齐附件。",
                    "customer",
                    row_customer_id,
                    "查看客户附件",
                    {"type": "customer", "customer_id": row_customer_id},
                    context={
                        "customer_id": row_customer_id,
                        "customer_name": customer.name or "",
                        "building_id": row_building_id,
                    },
                    meta={
                        "missing_category": CUSTOMER_LICENSE_CATEGORY,
                        "building_id": row_building_id,
                    },
                )
            )
    return risks


def _term_base_amount(contract: Contract, term: RentTerm) -> float:
    if term.contract_room_id:
        for room in getattr(contract, "room_items", []) or []:
            if room.id == term.contract_room_id:
                return _money(room.monthly_rent)
    summary = contract_room_summary(contract)
    return _money(summary.get("monthly_rent") or contract.monthly_rent)


def _large_adjustment_risks(
    db: Session,
    anchor: date,
    contracts_by_id: dict[int, Contract],
    contract_id: int | None,
    customer_id: int | None,
    building_ids: tuple[int, ...] | None = None,
) -> list[dict]:
    query = db.query(RentTerm).join(Contract).filter(
        Contract.is_deleted.is_(False),
        RentTerm.rule_type == TEMPORARY_ADJUSTMENT_TERM_TYPE,
        RentTerm.end_date >= anchor,
    )
    if building_ids is not None:
        query = query.filter(Contract.building_id.in_(building_ids or (-1,)))
    if contract_id:
        query = query.filter(RentTerm.contract_id == contract_id)
    if customer_id:
        query = query.filter(Contract.customer_id == customer_id)
    terms = query.order_by(RentTerm.start_date.asc(), RentTerm.id.asc()).all()

    risks = []
    for term in terms:
        contract = contracts_by_id.get(term.contract_id) or getattr(term, "contract", None)
        if not contract:
            continue
        value = _money(term.adjustment_value)
        if value <= 0:
            continue
        base = _term_base_amount(contract, term)
        is_percent = (term.adjustment_type or "") == "percent"
        is_large = value >= 50 if is_percent else ((base > 0 and value >= base * LARGE_ADJUSTMENT_RATIO) or value >= LARGE_ADJUSTMENT_FIXED_FLOOR)
        if not is_large:
            continue
        context = _contract_context(contract)
        unit = "%" if is_percent else "元/月"
        risks.append(
            _risk(
                f"large_adjustment:{term.id}",
                "large_adjustment",
                "high",
                "临时减免幅度较大",
                f"临时减免 {value:g}{unit}，仅提示复核，不阻断保存。",
                "contract",
                contract.id,
                "查看租金条款",
                {"type": "contract", "contract_id": contract.id},
                amount=None if is_percent else value,
                due_date=term.end_date,
                occurred_on=term.start_date,
                context=context,
                meta={"rent_term_id": term.id, "adjustment_type": term.adjustment_type or "", "adjustment_value": value, "base_monthly_rent": base},
            )
        )
    return risks


def _sort_risks(items: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        due = item.get("due_date") or "9999-99-99"
        amount = item.get("amount") or 0
        return (SEVERITY_ORDER.get(item.get("severity"), 9), due, -float(amount), item.get("stable_key") or "")

    return sorted(items, key=key)


def _summary(items: list[dict]) -> dict:
    by_type = Counter(item["risk_type"] for item in items)
    by_severity = Counter(item["severity"] for item in items)
    return {
        "total": len(items),
        "by_type": dict(sorted(by_type.items())),
        "by_severity": {key: by_severity.get(key, 0) for key in ("high", "medium", "low")},
    }


def list_risks(
    db: Session,
    *,
    risk_type: str | None = None,
    severity: str | None = None,
    entity_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 100,
    monthly_reports: dict[str, dict] | None = None,
    contracts: list[Contract] | None = None,
    building_ids: Iterable[int] | None = None,
) -> dict:
    anchor = today_in_timezone()
    scoped_building_ids = (
        None
        if building_ids is None
        else tuple(dict.fromkeys(int(item) for item in building_ids))
    )
    if contracts is not None:
        contracts = [
            contract
            for contract in contracts
            if scoped_building_ids is None or contract.building_id in scoped_building_ids
        ]
    else:
        contract_query = (
            db.query(Contract)
            .options(
                selectinload(Contract.customer),
                selectinload(Contract.room_items),
                selectinload(Contract.rent_terms),
            )
            .filter(Contract.is_deleted.is_(False))
        )
        if scoped_building_ids is not None:
            contract_query = contract_query.options(
                with_loader_criteria(
                    ContractRoom,
                    ContractRoom.building_id.in_(scoped_building_ids or (-1,)),
                    include_aliases=True,
                )
            ).filter(Contract.building_id.in_(scoped_building_ids or (-1,)))
            contract_query = contract_query.populate_existing()
        contracts = contract_query.all()
    contracts_by_id = {contract.id: contract for contract in contracts}

    report_contract_ids = None
    if contract_id:
        report_contract_ids = [contract_id]
    elif customer_id:
        report_contract_ids = [item.id for item in contracts if item.customer_id == customer_id]
    elif scoped_building_ids is not None:
        report_contract_ids = list(contracts_by_id)

    items: list[dict] = []
    items.extend(
        _overdue_receivable_risks(
            db,
            contracts_by_id,
            anchor,
            contract_id,
            customer_id,
            monthly_reports,
            report_contract_ids,
        )
    )
    items.extend(_contract_expiring_risks(contracts, contract_id, customer_id))
    items.extend(
        _follow_up_overdue_risks(
            db,
            anchor,
            contract_id,
            customer_id,
            scoped_building_ids,
        )
    )
    items.extend(
        _deposit_unpaid_risks(
            db,
            anchor,
            contracts_by_id,
            contract_id,
            customer_id,
            scoped_building_ids,
        )
    )
    items.extend(_attachment_missing_risks(db, contracts, contract_id, customer_id))
    items.extend(
        _large_adjustment_risks(
            db,
            anchor,
            contracts_by_id,
            contract_id,
            customer_id,
            scoped_building_ids,
        )
    )

    if risk_type:
        items = [item for item in items if item["risk_type"] == risk_type]
    if severity:
        items = [item for item in items if item["severity"] == severity]
    if entity_type:
        items = [item for item in items if item["entity_type"] == entity_type]

    items = _sort_risks(items)
    summary = _summary(items)
    return {
        "date": anchor.isoformat(),
        "filters": {
            "risk_type": risk_type or "",
            "severity": severity or "",
            "entity_type": entity_type or "",
            "contract_id": contract_id,
            "customer_id": customer_id,
            "limit": limit,
        },
        "summary": summary,
        "items": items[:limit],
    }
