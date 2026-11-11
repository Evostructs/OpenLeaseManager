from __future__ import annotations

import calendar
from datetime import date
from datetime import datetime
import json
from typing import Iterable, Mapping

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.core.money import round_money
from app.models import Attachment, Contract, ContractDeposit, ContractRoom, Customer, OperationLog, RentTerm
from app.services.legacy_demo_a_scope import ObjectScope, legacy_operation_log_scope
from app.services.lease_types import normalize_signing_type
from app.services.calculation_context import memoize_contract_calculation
from app.services.towers import normalize_tower_for_building


def _calculated_monthly_rent(area: float | None, unit_price: float | None) -> float:
    return round_money((area or 0) * (unit_price or 0) * 365 / 12)


def contract_tower_value(contract: Contract, value: object) -> str:
    try:
        building_code = getattr(getattr(contract, "building", None), "code", None)
    except Exception:
        building_code = None
    return normalize_tower_for_building(value, building_code)


def periods_overlap(first_start: date | None, first_end: date | None, second_start: date | None, second_end: date | None) -> bool:
    if not first_start or not first_end or not second_start or not second_end:
        return False
    return first_start <= second_end and first_end >= second_start


def contract_room_effective_period(contract: Contract, item: ContractRoom | dict) -> tuple[date | None, date | None]:
    if isinstance(item, dict):
        item_start = item.get("start_date")
        item_end = item.get("end_date")
    else:
        item_start = item.start_date
        item_end = item.end_date
    return item_start or contract.start_date, item_end or contract.end_date


def contract_room_active_in_window(
    contract: Contract,
    item: ContractRoom | dict,
    window_start: date | None = None,
    window_end: date | None = None,
) -> bool:
    if not window_start and not window_end:
        return True
    effective_start, effective_end = contract_room_effective_period(contract, item)
    query_start = window_start or window_end
    query_end = window_end or window_start
    return periods_overlap(effective_start, effective_end, query_start, query_end)


@memoize_contract_calculation
def contract_room_items(
    contract: Contract,
    window_start: date | None = None,
    window_end: date | None = None,
) -> list[dict]:
    stored_room_items = [item for item in getattr(contract, "room_items", []) or [] if item.room_no]
    items = [
        {
            "id": item.id,
            "room_id": item.room_id,
            "tower": contract_tower_value(contract, item.tower),
            "room_no": item.room_no or "",
            "area": item.area or 0,
            "unit_price": item.unit_price or 0,
            "monthly_rent": item.monthly_rent or _calculated_monthly_rent(item.area, item.unit_price),
            "start_date": contract_room_effective_period(contract, item)[0],
            "end_date": contract_room_effective_period(contract, item)[1],
            "notes": item.notes or "",
        }
        for item in stored_room_items
        if contract_room_active_in_window(contract, item, window_start, window_end)
    ]
    if items:
        return items
    if stored_room_items:
        return []
    if contract.room_no and contract_room_active_in_window(
        contract,
        {"start_date": contract.start_date, "end_date": contract.end_date},
        window_start,
        window_end,
    ):
        return [
            {
                "id": None,
                "room_id": None,
                "tower": contract_tower_value(contract, contract.tower),
                "room_no": contract.room_no or "",
                "area": contract.area or 0,
                "unit_price": contract.unit_price or 0,
                "monthly_rent": contract.monthly_rent or 0,
                "start_date": contract.start_date,
                "end_date": contract.end_date,
                "notes": "",
            }
        ]
    return []


def contract_room_summary(
    contract: Contract,
    window_start: date | None = None,
    window_end: date | None = None,
) -> dict:
    stored_items = [item for item in getattr(contract, "room_items", []) or [] if item.room_no]
    if not stored_items:
        return {
            "tower": contract_tower_value(contract, contract.tower),
            "room_no": contract.room_no or "",
            "area": contract.area or 0,
            "unit_price": contract.unit_price or 0,
            "monthly_rent": contract.monthly_rent or 0,
        }

    items = contract_room_items(contract, window_start, window_end)
    if not items:
        return {
            "tower": "",
            "room_no": "",
            "area": 0,
            "unit_price": 0,
            "monthly_rent": 0,
        }

    total_area = round(sum(item.get("area") or 0 for item in items), 2)
    total_monthly_rent = round(sum(item.get("monthly_rent") or 0 for item in items), 2)
    towers = [contract_tower_value(contract, item.get("tower")) for item in items if item.get("tower")]
    unique_towers = sorted({tower for tower in towers if tower})
    if len(unique_towers) == 1:
        tower = unique_towers[0]
    elif len(unique_towers) > 1:
        tower = "多房源"
    else:
        tower = contract_tower_value(contract, contract.tower)

    return {
        "tower": tower,
        "room_no": "、".join(item.get("room_no") or "" for item in items if item.get("room_no")),
        "area": total_area,
        "unit_price": round_money(total_monthly_rent * 12 / 365 / total_area) if total_area > 0 else 0,
        "monthly_rent": total_monthly_rent,
    }


def contract_monthly_property_fee(contract: Contract, summary: dict | None = None) -> float:
    effective = summary or contract_room_summary(contract)
    return round((effective.get("area") or 0) * (contract.property_fee_rate or 0), 2)


def contract_effective_net_rent(contract: Contract) -> float:
    # 单一口径：委托租金引擎（运行时引入避免模块级循环依赖）
    from app.services.contracts import contract_net_rent

    return contract_net_rent(contract)


def log_action(
    db: Session,
    contract_id: int | None,
    action: str,
    changes: str = "",
    operator: str = "system",
    commit: bool = True,
    *,
    target_type: str | None = None,
    target_id: int | None = None,
    object_scope: ObjectScope | None = None,
) -> None:
    resolved_target_type = target_type or ("contract" if contract_id else "")
    resolved_target_id = target_id if target_id is not None else contract_id
    scope = object_scope
    if scope is None:
        scope = legacy_operation_log_scope(
            db,
            contract_id=contract_id,
            target_type=resolved_target_type,
            target_id=resolved_target_id,
        )
    elif scope.scope_type == "building":
        if scope.building_id is None or scope.portfolio_id is not None:
            raise ValueError("building log scope requires only building_id")
    elif scope.scope_type == "portfolio":
        if scope.portfolio_id is None or scope.building_id is not None:
            raise ValueError("portfolio log scope requires only portfolio_id")
    elif scope.scope_type == "global":
        if scope.building_id is not None or scope.portfolio_id is not None:
            raise ValueError("global log scope cannot include property ids")
    else:
        raise ValueError("operation log scope must be building, portfolio, or global")
    log = OperationLog(
        contract_id=contract_id,
        **scope.as_columns(),
        target_type=resolved_target_type,
        target_id=resolved_target_id,
        action=action,
        changes=changes,
        operator=operator,
    )
    db.add(log)
    if commit:
        db.commit()


def log_action_for_buildings(
    db: Session,
    building_ids: Iterable[int],
    action: str,
    changes: str = "",
    operator: str = "system",
    commit: bool = True,
    *,
    target_type: str | None = None,
    target_id: int | None = None,
    changes_by_building: Mapping[int, str] | None = None,
) -> None:
    """Persist one immutable audit row per affected building.

    Portfolio membership is time-varying and OperationLog intentionally has no
    membership snapshot.  Business actions spanning several buildings are
    therefore frozen at the stable building boundary instead of being written
    as a later-ambiguous portfolio row.
    """
    normalized_ids = tuple(dict.fromkeys(int(item) for item in building_ids))
    if not normalized_ids:
        raise ValueError("building-scoped audit requires at least one building")
    normalized_changes = (
        {int(building_id): value for building_id, value in changes_by_building.items()}
        if changes_by_building is not None
        else None
    )
    if normalized_changes is not None and set(normalized_changes) != set(normalized_ids):
        raise ValueError("building-scoped audit changes must match affected buildings")
    for building_id in normalized_ids:
        log_action(
            db,
            None,
            action,
            normalized_changes[building_id] if normalized_changes is not None else changes,
            operator,
            commit=False,
            target_type=target_type,
            target_id=target_id,
            object_scope=ObjectScope(scope_type="building", building_id=building_id),
        )
    if commit:
        db.commit()


def get_operator(request: Request | None) -> str:
    if request is None:
        return "system"
    token = request.cookies.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return "system"
    try:
        payload = decode_access_token(token)
        return payload.get("username", "system")
    except Exception:
        return "system"


def serialize_rent_term(term: RentTerm) -> dict:
    return {
        "id": term.id,
        "contract_id": term.contract_id,
        "contract_room_id": term.contract_room_id,
        "room_no": term.room_no or "",
        "rule_type": term.rule_type or "",
        "start_date": term.start_date,
        "end_date": term.end_date,
        "area": term.area,
        "unit_price": term.unit_price,
        "monthly_amount": term.monthly_amount,
        "adjustment_type": term.adjustment_type,
        "adjustment_value": term.adjustment_value,
        "frequency": term.frequency,
        "notes": term.notes or "",
    }


def deposit_status(expected_amount: float | None, received_amount: float | None, refunded_amount: float | None, offset_amount: float | None) -> str:
    expected = round_money(float(expected_amount or 0))
    received = round_money(float(received_amount or 0))
    refunded = round_money(float(refunded_amount or 0))
    offset = round_money(float(offset_amount or 0))
    settled = round_money(refunded + offset)
    available = round_money(max(received - settled, 0))

    if received <= 0:
        return "unpaid"
    if settled >= received:
        if refunded > 0 and offset > 0:
            return "closed"
        if refunded > 0:
            return "refunded"
        if offset > 0:
            return "offset"
        return "closed"
    if expected > 0 and received < expected:
        return "partial"
    if offset > 0:
        return "offset"
    if refunded > 0:
        return "refunded"
    return "paid"


def serialize_deposit(deposit: ContractDeposit) -> dict:
    expected = round_money(float(deposit.expected_amount or 0))
    received = round_money(float(deposit.received_amount or 0))
    refunded = round_money(float(deposit.refunded_amount or 0))
    offset = round_money(float(deposit.offset_amount or 0))
    available = round_money(max(received - refunded - offset, 0))
    return {
        "id": deposit.id,
        "contract_id": deposit.contract_id,
        "customer_id": deposit.customer_id,
        "deposit_type": deposit.deposit_type or "租赁押金",
        "expected_amount": expected,
        "received_amount": received,
        "refunded_amount": refunded,
        "offset_amount": offset,
        "unpaid_amount": round_money(max(expected - received, 0)),
        "available_amount": available,
        "status": deposit.status or deposit_status(expected, received, refunded, offset),
        "due_date": deposit.due_date,
        "received_date": deposit.received_date,
        "refund_date": deposit.refund_date,
        "notes": deposit.notes or "",
        "created_at": deposit.created_at,
        "updated_at": deposit.updated_at,
    }


def serialize_attachment(attachment: Attachment) -> dict:
    return {
        "id": attachment.id,
        "entity_type": attachment.entity_type or "",
        "entity_id": attachment.entity_id,
        "file_name": attachment.file_name or "",
        "content_type": attachment.content_type or "",
        "file_size": attachment.file_size or 0,
        "category": attachment.category or "其他",
        "notes": attachment.notes or "",
        "uploaded_by": attachment.uploaded_by or "",
        "created_at": attachment.created_at,
        "deleted_at": attachment.deleted_at,
    }


def list_entity_attachments(db: Session, entity_type: str, entity_id: int) -> list[dict]:
    rows = (
        db.query(Attachment)
        .filter(
            Attachment.entity_type == entity_type,
            Attachment.entity_id == entity_id,
            Attachment.deleted_at.is_(None),
        )
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        .all()
    )
    return [serialize_attachment(item) for item in rows]


def attachment_summary(attachments: list[dict], entity_type: str) -> dict:
    categories: dict[str, int] = {}
    for item in attachments:
        category = item.get("category") or "其他"
        categories[category] = categories.get(category, 0) + 1
    missing_required = []
    if entity_type == "contract" and categories.get("合同正文", 0) == 0:
        missing_required.append("合同正文")
    if entity_type == "customer" and categories.get("证照", 0) == 0:
        missing_required.append("证照")
    return {"total": len(attachments), "categories": categories, "missing_required": missing_required}


def deposit_summary(deposits: list[ContractDeposit]) -> dict:
    rows = [serialize_deposit(item) for item in deposits]
    expected = round_money(sum(row["expected_amount"] for row in rows))
    received = round_money(sum(row["received_amount"] for row in rows))
    refunded = round_money(sum(row["refunded_amount"] for row in rows))
    offset = round_money(sum(row["offset_amount"] for row in rows))
    available = round_money(sum(row["available_amount"] for row in rows))
    unpaid = round_money(sum(row["unpaid_amount"] for row in rows))
    return {
        "expected_amount": expected,
        "received_amount": received,
        "refunded_amount": refunded,
        "offset_amount": offset,
        "unpaid_amount": unpaid,
        "available_amount": available,
        "insufficient_amount": round_money(max(expected - available, 0)),
        "count": len(rows),
        "unpaid_count": sum(1 for row in rows if row["unpaid_amount"] > 0),
        "available_count": sum(1 for row in rows if row["available_amount"] > 0),
    }


def customer_display_name(contract: Contract | None) -> str:
    if not contract:
        return ""
    customer = getattr(contract, "customer", None)
    if customer and customer.name:
        return customer.name
    return contract.tenant_name or ""


def serialize_customer_summary(customer: Customer | None) -> dict | None:
    if not customer:
        return None
    return {
        "id": customer.id,
        "name": customer.name or "",
        "short_name": customer.short_name or "",
        "credit_code": customer.credit_code or "",
        "customer_type": customer.customer_type or "",
        "industry": customer.industry or "",
        "contact_name": customer.contact_name or "",
        "contact_phone": customer.contact_phone or "",
        "contact_email": customer.contact_email or "",
        "status": customer.status or "active",
    }


def serialize_contract(contract: Contract) -> dict:
    rooms = contract_room_items(contract)
    summary = contract_room_summary(contract)
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "customer_id": contract.customer_id,
        "customer": serialize_customer_summary(getattr(contract, "customer", None)),
        "tower": summary["tower"],
        "room_no": summary["room_no"],
        "tenant_name": customer_display_name(contract),
        "legacy_tenant_name": contract.tenant_name or "",
        "area": summary["area"],
        "unit_price": summary["unit_price"],
        "lease_type": normalize_signing_type(contract.lease_type),
        "free_period": contract.free_period or 0,
        "monthly_rent": summary["monthly_rent"],
        "broker_fee": contract.broker_fee or 0,
        "net_rent": contract_effective_net_rent(contract),
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "status": contract.status,
        "days_to_expiry": contract.days_to_expiry,
        "disposal": contract.disposal or "",
        "contact_person": contract.contact_person or "",
        "contact_phone": contract.contact_phone or "",
        "contact_email": contract.contact_email or "",
        "internal_contact_person": contract.internal_contact_person or "",
        "internal_contact_feishu_id": contract.internal_contact_feishu_id or "",
        "deposit": contract.deposit or 0,
        "deposit_status": contract.deposit_status or "",
        "property_fee_rate": contract.property_fee_rate or 0,
        "monthly_property_fee": contract_monthly_property_fee(contract, summary),
        "prev_contract_no": contract.prev_contract_no or "",
        "payment_due_day": contract.payment_due_day,
        "notes": contract.notes or "",
        "pdf_path": contract.pdf_path or "",
        "created_at": contract.created_at,
        "updated_at": contract.updated_at,
        "room_items": rooms,
        "rent_terms": [serialize_rent_term(term) for term in getattr(contract, "rent_terms", []) or []],
        "deposits": [serialize_deposit(deposit) for deposit in getattr(contract, "deposits", []) or []],
        "deposit_summary": deposit_summary(list(getattr(contract, "deposits", []) or [])),
    }


def serialize_contract_lifecycle_item(contract: Contract) -> dict:
    summary = contract_room_summary(contract)
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "customer_id": contract.customer_id,
        "tenant_name": customer_display_name(contract),
        "lease_type": normalize_signing_type(contract.lease_type),
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "status": contract.status,
        "disposal": contract.disposal or "",
        "room_no": summary["room_no"],
    }


def serialize_contract_with_lifecycle(db: Session, contract: Contract) -> dict:
    data = serialize_contract(contract)
    attachments = list_entity_attachments(db, "contract", contract.id)
    data["attachments"] = attachments
    data["attachment_summary"] = attachment_summary(attachments, "contract")
    from app.services.risks import list_risks

    data["risks"] = list_risks(db, contract_id=contract.id, limit=20)["items"]
    prev_contract_no = (contract.prev_contract_no or "").strip()
    previous = None
    if prev_contract_no:
        previous = (
            db.query(Contract)
            .filter(Contract.contract_no == prev_contract_no, Contract.is_deleted.is_(False))
            .first()
        )

    next_contracts = []
    contract_no = (contract.contract_no or "").strip()
    if contract_no:
        next_contracts = (
            db.query(Contract)
            .filter(Contract.prev_contract_no == contract_no, Contract.is_deleted.is_(False))
            .order_by(Contract.start_date.asc(), Contract.id.asc())
            .all()
        )

    data["previous_contract"] = serialize_contract_lifecycle_item(previous) if previous else None
    data["next_contracts"] = [serialize_contract_lifecycle_item(item) for item in next_contracts]
    return data


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def dump_changes(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
