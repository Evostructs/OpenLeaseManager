from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.time import today_in_timezone
from app.core.money import round_money
from app.models import Attachment, Building, Contract, ContractRoom, CustomerFollowUp, Room
from app.services.common import attachment_summary, contract_effective_net_rent, contract_monthly_property_fee, contract_room_summary, customer_display_name, deposit_summary, serialize_attachment
from app.services.contracts import get_monthly_receivables
from app.services.receivable_history import OVERDUE_LOOKBACK_MONTHS, collect_overdue_history, month_keys_back
from app.services.risks import list_risks
from app.services.towers import leasing_group, normalize_tower


ALLOWED_DECISION_TOWERS = {"南塔办公", "北塔办公", "商业配套"}
CONTRACT_BODY_CATEGORY = "合同正文"
DONE_FOLLOW_UP_STATUS = "done"


def _money(value: float | int | None) -> float:
    return round_money(float(value or 0))


def _current_month() -> str:
    return today_in_timezone().strftime("%Y-%m")


def _contract_query(
    db: Session,
    contract_id: int,
    *,
    building_ids: Sequence[int] | None = None,
) -> Contract:
    query = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items).selectinload(ContractRoom.room),
            selectinload(Contract.rent_terms),
            selectinload(Contract.deposits),
        )
        .filter(Contract.id == contract_id, Contract.is_deleted.is_(False))
    )
    if building_ids is not None:
        normalized_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) or (-1,)
        query = query.filter(Contract.building_id.in_(normalized_ids))
    contract = query.first()
    if contract is None:
        from fastapi import HTTPException

        raise HTTPException(404, "合同不存在或无权访问")
    return contract


def _decision_tower(
    tower: object,
    *,
    legacy_demo_a: bool,
    room: Room | None = None,
    room_no: object = None,
) -> str:
    if not legacy_demo_a:
        return str(tower or getattr(room, "tower", None) or "").strip() or "未标注"
    value = leasing_group(
        tower,
        floor=getattr(room, "floor", None),
        room_type=getattr(room, "room_type", None),
        room_no=room_no,
    )
    if value in ALLOWED_DECISION_TOWERS:
        return value

    normalized = normalize_tower(tower)
    if normalized == "南塔":
        return "南塔办公"
    if normalized == "北塔":
        return "北塔办公"
    return "商业配套"


def _room_rows(contract: Contract, *, legacy_demo_a: bool) -> list[dict]:
    rows = []
    for item in getattr(contract, "room_items", []) or []:
        if not item.room_no:
            continue
        rows.append(
            {
                "room_no": item.room_no or "",
                "tower": _decision_tower(
                    item.tower or contract.tower,
                    legacy_demo_a=legacy_demo_a,
                    room=getattr(item, "room", None),
                    room_no=item.room_no,
                ),
                "area": _money(item.area),
                "unit_price": _money(item.unit_price),
                "monthly_rent": _money(item.monthly_rent),
            }
        )
    if rows:
        return rows
    if not contract.room_no:
        return []
    return [
        {
            "room_no": contract.room_no or "",
            "tower": _decision_tower(
                contract.tower,
                legacy_demo_a=legacy_demo_a,
                room_no=contract.room_no,
            ),
            "area": _money(contract.area),
            "unit_price": _money(contract.unit_price),
            "monthly_rent": _money(contract.monthly_rent),
        }
    ]


def _current_receivables(db: Session, contract: Contract) -> dict:
    month = _current_month()
    report = get_monthly_receivables(db, month, contract_ids=[contract.id])
    row = report["rows"][0] if report["rows"] else {}
    receivable = _money(row.get("total"))
    received = _money(row.get("received_amount"))
    unpaid = _money(row.get("unpaid_balance") if row else 0)
    return {
        "month": month,
        "receivable": receivable,
        "received": received,
        "unpaid_balance": unpaid,
        "overdue_balance": _money(row.get("overdue_balance") if row else 0),
        "not_due_balance": _money(row.get("not_due_balance") if row else 0),
        "collection_rate": round(received / receivable * 100, 1) if receivable > 0 else 0,
        "payment_status": row.get("payment_status") or "",
        "due_date": row.get("due_date") or "",
    }


def _overdue_balance(db: Session, contract: Contract) -> float:
    history = collect_overdue_history(
        db,
        months=month_keys_back(today_in_timezone(), OVERDUE_LOOKBACK_MONTHS),
        contract_ids=[contract.id],
    )
    entry = history["overdue_by_contract"].get(contract.id)
    return entry["overdue_balance"] if entry else 0.0


def _follow_up_summary(item: CustomerFollowUp | None) -> dict | None:
    if not item:
        return None
    return {
        "id": item.id,
        "customer_id": item.customer_id,
        "contract_id": item.contract_id,
        "follow_up_type": item.follow_up_type or "",
        "occurred_at": item.occurred_at.isoformat() if isinstance(item.occurred_at, datetime) else "",
        "next_action": item.next_action or "",
        "next_follow_up_date": item.next_follow_up_date.isoformat() if item.next_follow_up_date else "",
        "owner": item.owner or "",
        "status": item.status or "open",
    }


def _follow_ups(db: Session, contract: Contract) -> dict:
    filters = [CustomerFollowUp.contract_id == contract.id]
    if contract.customer_id:
        filters.append(CustomerFollowUp.customer_id == contract.customer_id)
    query = db.query(CustomerFollowUp).filter(
        CustomerFollowUp.building_id == contract.building_id,
        or_(*filters),
    )

    latest = query.order_by(CustomerFollowUp.occurred_at.desc(), CustomerFollowUp.id.desc()).first()
    next_item = (
        query.filter(
            CustomerFollowUp.next_follow_up_date.isnot(None),
            or_(CustomerFollowUp.status.is_(None), CustomerFollowUp.status != DONE_FOLLOW_UP_STATUS),
        )
        .order_by(CustomerFollowUp.next_follow_up_date.asc(), CustomerFollowUp.id.asc())
        .first()
    )
    return {"latest": _follow_up_summary(latest), "next": _follow_up_summary(next_item)}


def _attachments(db: Session, contract: Contract) -> dict:
    rows = (
        db.query(Attachment)
        .filter(
            Attachment.entity_type == "contract",
            Attachment.entity_id == contract.id,
            Attachment.scope_type == "building",
            Attachment.building_id == contract.building_id,
            Attachment.portfolio_id.is_(None),
            Attachment.deleted_at.is_(None),
        )
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        .all()
    )
    items = [serialize_attachment(item) for item in rows]
    summary = attachment_summary(items, "contract")
    has_body = CONTRACT_BODY_CATEGORY not in summary.get("missing_required", [])
    return {
        "total": len(items),
        "categories": summary.get("categories", {}),
        "missing_required": summary.get("missing_required", []),
        "has_contract_body": has_body,
    }


def _risk_rows(db: Session, contract: Contract, *, building_ids: Sequence[int] | None = None) -> list[dict]:
    risks = list_risks(
        db,
        contract_id=contract.id,
        limit=10,
        building_ids=building_ids,
    )["items"]
    return [
        {
            "type": item.get("risk_type") or "",
            "severity": item.get("severity") or "",
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "target": item.get("action_target") or {},
            "amount": item.get("amount"),
            "due_date": item.get("due_date") or "",
        }
        for item in risks
    ]


def _signals_and_checks(
    *,
    contract: Contract,
    receivables: dict,
    deposits: dict,
    attachments: dict,
    risks: list[dict],
    follow_up: dict,
) -> tuple[list[str], list[str]]:
    days = contract.days_to_expiry
    expiring_soon = days is not None and 0 <= days <= 90
    collection_blocker = receivables["overdue_balance"] > 0
    current_unpaid = receivables["unpaid_balance"] > 0
    attachment_gap = bool(attachments["missing_required"])
    deposit_gap = deposits["unpaid_amount"] > 0 or deposits.get("insufficient_amount", 0) > 0
    high_risk = any(item.get("severity") == "high" for item in risks)

    signals = []
    checks = []
    if collection_blocker:
        signals.append("collection_blocker")
        checks.append("先确认历史逾期")
    elif current_unpaid:
        checks.append("确认本月未收")
    if attachment_gap:
        signals.append("attachment_gap")
        checks.append("补合同正文附件")
    if deposit_gap:
        signals.append("deposit_gap")
        checks.append("确认押金未收")
    if high_risk:
        signals.append("high_risk")
        checks.append("复核高风险事项")
    if expiring_soon:
        signals.append("expiring_soon")
        checks.append("安排续租沟通")
        checks.append("确认续租、谈价或退租承接安排")
        if not follow_up.get("next"):
            checks.append("补充下一次跟进计划")

    if not signals and not current_unpaid:
        signals.append("good_standing")
        checks.append("定期复核经营状态")

    return signals, list(dict.fromkeys(checks))


def build_contract_decision_card(
    db: Session,
    contract_id: int,
    *,
    building_ids: Sequence[int] | None = None,
) -> dict:
    normalized_ids = (
        tuple(dict.fromkeys(int(item) for item in building_ids))
        if building_ids is not None
        else None
    )
    contract = _contract_query(db, contract_id, building_ids=normalized_ids)
    building = db.get(Building, int(contract.building_id))
    legacy_demo_a = bool(
        building is not None and str(building.code or "").strip().casefold() == "demo_a"
    )
    customer = getattr(contract, "customer", None)
    room_summary = contract_room_summary(contract)
    rooms = _room_rows(contract, legacy_demo_a=legacy_demo_a)
    current_receivables = _current_receivables(db, contract)
    current_receivables["overdue_balance"] = _overdue_balance(db, contract)
    deposits = deposit_summary(list(getattr(contract, "deposits", []) or []))
    attachments = _attachments(db, contract)
    risks = _risk_rows(db, contract, building_ids=normalized_ids)
    follow_up = _follow_ups(db, contract)
    signals, checks = _signals_and_checks(
        contract=contract,
        receivables=current_receivables,
        deposits=deposits,
        attachments=attachments,
        risks=risks,
        follow_up=follow_up,
    )

    return {
        "contract": {
            "id": contract.id,
            "contract_no": contract.contract_no or "",
            "status": contract.status,
            "start_date": contract.start_date,
            "end_date": contract.end_date,
            "days_to_expiry": contract.days_to_expiry,
            "customer_name": customer_display_name(contract),
            "internal_contact_person": contract.internal_contact_person or "",
            "contact_person": contract.contact_person or "",
        },
        "customer": {
            "customer_id": contract.customer_id,
            "name": customer.name if customer else customer_display_name(contract),
            "short_name": customer.short_name if customer else "",
            "contact_name": customer.contact_name if customer else "",
            "status": customer.status if customer else "",
        },
        "rooms": rooms,
        "rent": {
            "monthly_rent": _money(room_summary.get("monthly_rent")),
            "unit_price": _money(room_summary.get("unit_price")),
            "area": _money(room_summary.get("area")),
            "net_rent": contract_effective_net_rent(contract),
            "monthly_property_fee": contract_monthly_property_fee(contract, room_summary),
        },
        "receivables": current_receivables,
        "deposits": deposits,
        "risks": risks,
        "follow_up": follow_up,
        "attachments": attachments,
        "suggested_checks": checks,
        "decision_signals": signals,
    }
