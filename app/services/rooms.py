from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from typing import Iterable
from urllib.parse import quote

import openpyxl
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl.styles import Font
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from app.core.time import now_in_timezone, today_in_timezone
from app.core.excel_safety import sanitize_workbook
from app.core.workbook_units import normalize_import_workbook
from app.core.workbook_localization import localize_workbook
from app.core.money import round_money
from app.core.localization import bilingual_aliases, import_value, import_demo_tower
from app.database import SessionLocal
from app.models import Attachment, Building, Contract, ContractRoom, CustomerFollowUp, ReceivablePayment, ReceivableRecord, Room
from app.services.common import contract_room_items, contract_room_summary, contract_tower_value, customer_display_name, periods_overlap
from app.services.detail_operations_scope import prepare_detail_room_write, require_detail_building
from app.services.property_scope import LEGACY_DEFAULT_BUILDING_CODE
from app.services.towers import normalize_tower, normalize_tower_for_building


ROOM_IMPORT_HEADERS = {
    "南/北塔": "tower",
    "南北塔": "tower",
    "楼塔": "tower",
    "塔楼": "tower",
    "楼层": "floor",
    "层": "floor",
    "室号": "room_no",
    "房号": "room_no",
    "房间号": "room_no",
    "类型": "room_type",
    "房间类型": "room_type",
    "物业类型": "room_type",
    "用途": "room_type",
    "面积": "area",
    "建筑面积": "area",
    "出租面积": "area",
    "面积(㎡)": "area",
    "面积（㎡）": "area",
    "备注": "notes",
    "说明": "notes",
}


ROOM_IMPORT_HEADERS = bilingual_aliases(ROOM_IMPORT_HEADERS)


def _resolve_occupancy_window(start_date: date | None = None, end_date: date | None = None) -> tuple[date | None, date | None]:
    if not start_date and not end_date:
        return None, None
    window_start = start_date or end_date
    window_end = end_date or start_date
    if window_start and window_end and window_end < window_start:
        raise HTTPException(400, "查询终止日不能早于起始日")
    return window_start, window_end


def _normalize_building_ids(building_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if building_ids is None:
        return None
    return tuple(dict.fromkeys(int(item) for item in building_ids))


def _active_contract_query(db: Session, building_ids: tuple[int, ...] | None = None):
    today = today_in_timezone()
    query = db.query(Contract).options(
        selectinload(Contract.customer),
        selectinload(Contract.room_items),
        selectinload(Contract.rent_terms),
    ).filter(
        Contract.is_deleted.is_(False),
        or_(
            (Contract.start_date <= today) & (Contract.end_date >= today),
            (Contract.end_date < today) & (Contract.disposal == "逾期占用"),
        ),
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
    return query


def _month_keys(start: date, end: date) -> list[str]:
    current = start.replace(day=1)
    last = end.replace(day=1)
    keys = []
    while current <= last:
        keys.append(current.strftime("%Y-%m"))
        next_month = current.month % 12 + 1
        next_year = current.year + (1 if current.month == 12 else 0)
        current = date(next_year, next_month, 1)
    return keys


def _occupying_contracts(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    exclude_contract_id: int | None = None,
    building_ids: tuple[int, ...] | None = None,
) -> tuple[list[Contract], bool]:
    window_start, window_end = _resolve_occupancy_window(start_date, end_date)
    if not window_start or not window_end:
        query = _active_contract_query(db, building_ids)
        if exclude_contract_id:
            query = query.filter(Contract.id != exclude_contract_id)
        return query.all(), False

    query = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items),
            selectinload(Contract.rent_terms),
        )
        .filter(
            Contract.is_deleted.is_(False),
            Contract.start_date.isnot(None),
            Contract.end_date.isnot(None),
            Contract.start_date <= window_end,
            Contract.end_date >= window_start,
        )
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
    if exclude_contract_id:
        query = query.filter(Contract.id != exclude_contract_id)
    return query.order_by(Contract.start_date.asc(), Contract.end_date.asc(), Contract.id.asc()).all(), True


def list_rooms(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    exclude_contract_id: int | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    include_operations: bool = False,
    *,
    building_ids: Iterable[int] | None = None,
) -> list[dict]:
    scoped_building_ids = _normalize_building_ids(building_ids)
    room_query = db.query(Room).filter(Room.is_deleted.is_(False))
    if scoped_building_ids is not None:
        room_query = room_query.filter(Room.building_id.in_(scoped_building_ids or (-1,)))
    rooms = room_query.order_by(Room.tower, Room.room_no).all()
    code_lookup_ids = (
        scoped_building_ids
        if scoped_building_ids is not None
        else tuple({int(room.building_id) for room in rooms})
    )
    building_code_by_id = {
        int(building_id): str(code)
        for building_id, code in db.query(Building.id, Building.code)
        .filter(Building.id.in_(code_lookup_ids or (-1,)))
        .all()
    }
    legacy_demo_a = {
        code.casefold() for code in building_code_by_id.values()
    } == {LEGACY_DEFAULT_BUILDING_CODE.casefold()}
    window_start, window_end = _resolve_occupancy_window(start_date, end_date)
    active_contracts, range_mode = _occupying_contracts(
        db,
        start_date,
        end_date,
        exclude_contract_id,
        scoped_building_ids,
    )
    needs_operation_context = include_operations or str(status or "").strip() in {"历史逾期", "资料缺失"}
    operation_context = (
        _contract_operation_context(db, active_contracts, building_ids=scoped_building_ids)
        if needs_operation_context
        else _empty_operation_context()
    )
    occupied_rooms = {}
    for contract in active_contracts:
        if not range_mode and contract.status not in {"在租", "即将到期", "逾期占用"}:
            continue
        room_items = (
            contract_room_items(contract, window_start, window_end)
            if range_mode
            else [item for item in contract_room_items(contract) if _room_item_currently_occupies(contract, item)]
        )
        for room_item in room_items:
            room_no = room_item["room_no"]
            if not room_no:
                continue
            occupancy_key = (contract.building_id, room_no)
            operation = _contract_operation_summary(contract, room_item, operation_context)
            if occupancy_key in occupied_rooms:
                occupied_rooms[occupancy_key]["conflict_count"] += 1
                continue
            occupied_rooms[occupancy_key] = {
                "contract_id": contract.id,
                "customer_id": contract.customer_id,
                "contract_no": contract.contract_no,
                "tenant_name": customer_display_name(contract),
                "start_date": room_item.get("start_date").isoformat() if room_item.get("start_date") else None,
                "end_date": room_item.get("end_date").isoformat() if room_item.get("end_date") else None,
                "status": contract.status,
                "availability_status": "已占用" if range_mode else contract.status,
                "area": room_item["area"] or 0,
                "unit_price": room_item.get("unit_price") or 0,
                "monthly_rent": room_item.get("monthly_rent") or 0,
                "days_to_expiry": contract.days_to_expiry,
                "overdue_balance": operation["receivable"]["overdue_balance"],
                "unpaid_balance": operation["receivable"]["unpaid_balance"],
                "missing_contract_body": operation["attachment_summary"]["missing_contract_body"],
                "next_follow_up_date": operation["follow_up"]["next_follow_up_date"] if operation["follow_up"] else "",
                "signals": operation["signals"],
                "conflict_count": 1,
            }

    items = []
    for room in rooms:
        contract = occupied_rooms.get((room.building_id, room.room_no))
        signals = _room_signals(contract)
        items.append(
            {
                "id": room.id,
                "tower": normalize_tower_for_building(
                    room.tower,
                    building_code_by_id.get(int(room.building_id)),
                ),
                "floor": room.floor or "",
                "room_no": room.room_no,
                "room_type": room.room_type or "办公",
                "area": room.area or 0,
                "is_leasable": room.is_leasable,
                "notes": room.notes or "",
                "occupied": contract is not None,
                "availability_mode": "range" if range_mode else "current",
                "contract": contract,
                "signals": signals,
                "operating_statuses": _room_operating_statuses(contract, signals, range_mode),
            }
        )
    return _filter_room_items(
        items,
        tower,
        status,
        min_area,
        max_area,
        floor,
        legacy_demo_a=legacy_demo_a,
    )


def _empty_receivable_summary() -> dict:
    return {
        "unpaid_balance": 0,
        "overdue_balance": 0,
        "unpaid_months": 0,
        "overdue_months": 0,
        "max_aging_days": 0,
        "latest_month": "",
        "dunning_status": "",
        "dunning_owner": "",
        "next_follow_up_date": "",
    }


def _contract_receivable_summaries(db: Session, contracts: list[Contract]) -> dict[int, dict]:
    contract_ids = {contract.id for contract in contracts if contract.id}
    summaries = {contract_id: _empty_receivable_summary() for contract_id in contract_ids}
    start_dates = [contract.start_date for contract in contracts if contract.id in contract_ids and contract.start_date]
    if not contract_ids or not start_dates:
        return summaries

    from app.services.contracts import _month_bounds, _month_rent_detail, _payment_summary

    today = today_in_timezone()
    months = _month_keys(min(start_dates), today)
    records_by_key = {
        (record.contract_id, record.period_month): record
        for record in db.query(ReceivableRecord)
        .filter(
            ReceivableRecord.contract_id.in_(contract_ids),
            ReceivableRecord.period_month.in_(months),
        )
        .all()
    }
    payments_by_key: dict[tuple[int, str], list[ReceivablePayment]] = defaultdict(list)
    for payment in (
        db.query(ReceivablePayment)
        .filter(
            ReceivablePayment.contract_id.in_(contract_ids),
            ReceivablePayment.period_month.in_(months),
        )
        .all()
    ):
        payments_by_key[(payment.contract_id, payment.period_month)].append(payment)

    for month in months:
        target = date.fromisoformat(f"{month}-01")
        month_start, month_end = _month_bounds(target)
        for contract in contracts:
            contract_id = contract.id
            if contract_id not in summaries or not contract.start_date or not contract.end_date:
                continue
            if contract.start_date > month_end or contract.end_date < month_start:
                continue
            # 与 get_monthly_receivables 同口径：已退租合同截断前的月份仍计入欠款。
            room_summary = contract_room_summary(contract, month_start, month_end)
            if not room_summary["room_no"]:
                continue
            detail = _month_rent_detail(contract, target)
            receivable = round_money(float(detail["rent"] or 0))
            record = records_by_key.get((contract_id, month))
            payments = payments_by_key.get((contract_id, month), [])
            payment_summary = _payment_summary(payments, record)
            balance = round(max(receivable - float(payment_summary["settlement_amount"] or 0), 0), 2)
            if balance <= 0:
                continue
            summary = summaries[contract_id]
            summary["unpaid_balance"] = round_money(summary["unpaid_balance"] + balance)
            summary["unpaid_months"] += 1
            summary["latest_month"] = month
            due_date = month_end
            if due_date < today:
                overdue_balance = balance
                aging_days = (today - due_date).days
                summary["overdue_balance"] = round_money(summary["overdue_balance"] + overdue_balance)
                summary["overdue_months"] += 1
                summary["max_aging_days"] = max(summary["max_aging_days"], aging_days)
                summary["dunning_status"] = record.dunning_status if record else ""
                summary["dunning_owner"] = (record.dunning_owner if record else "") or contract.internal_contact_person or ""
                summary["next_follow_up_date"] = record.next_follow_up_date.isoformat() if record and record.next_follow_up_date else ""
    return summaries


def _contract_missing_body_map(db: Session, contracts: list[Contract]) -> dict[int, bool]:
    contract_pairs: set[tuple[int, int]] = set()
    for contract in contracts:
        if contract.id is None:
            continue
        if contract.building_id is None:
            raise HTTPException(409, "合同尚未明确楼宇归属")
        contract_pairs.add((int(contract.id), int(contract.building_id)))
    if not contract_pairs:
        return {}
    rows = (
        db.query(Attachment.entity_id, Attachment.building_id)
        .filter(
            Attachment.entity_type == "contract",
            Attachment.entity_id.in_({item[0] for item in contract_pairs}),
            Attachment.scope_type == "building",
            Attachment.building_id.in_({item[1] for item in contract_pairs}),
            Attachment.portfolio_id.is_(None),
            Attachment.category == "合同正文",
            Attachment.deleted_at.is_(None),
        )
        .all()
    )
    has_body = {(int(row[0]), int(row[1])) for row in rows if row[1] is not None}
    return {
        contract_id: (contract_id, building_id) not in has_body
        for contract_id, building_id in contract_pairs
    }


def _serialize_follow_up_summary(follow_up: CustomerFollowUp | None) -> dict | None:
    if not follow_up:
        return None
    today = today_in_timezone()
    overdue_days = 0
    if follow_up.next_follow_up_date and follow_up.next_follow_up_date < today:
        overdue_days = (today - follow_up.next_follow_up_date).days
    return {
        "id": follow_up.id,
        "customer_id": follow_up.customer_id,
        "contract_id": follow_up.contract_id,
        "follow_up_type": follow_up.follow_up_type or "",
        "content": follow_up.content or "",
        "next_action": follow_up.next_action or "",
        "next_follow_up_date": follow_up.next_follow_up_date.isoformat() if follow_up.next_follow_up_date else "",
        "owner": follow_up.owner or "",
        "status": follow_up.status or "open",
        "overdue_days": overdue_days,
    }


def _contract_follow_up_summaries(
    db: Session,
    contracts: list[Contract],
    *,
    building_ids: tuple[int, ...] | None = None,
) -> dict[int, dict | None]:
    contract_ids = {contract.id for contract in contracts if contract.id}
    customer_ids = {contract.customer_id for contract in contracts if contract.customer_id}
    if not contract_ids and not customer_ids:
        return {}
    query = db.query(CustomerFollowUp).filter(
            CustomerFollowUp.next_follow_up_date.isnot(None),
            or_(CustomerFollowUp.status.is_(None), CustomerFollowUp.status != "done"),
            or_(CustomerFollowUp.contract_id.in_(contract_ids or {0}), CustomerFollowUp.customer_id.in_(customer_ids or {0})),
        )
    if building_ids is not None:
        query = query.filter(CustomerFollowUp.building_id.in_(building_ids or (-1,)))
    rows = (
        query
        .order_by(CustomerFollowUp.next_follow_up_date.asc(), CustomerFollowUp.id.asc())
        .all()
    )
    linked_contract_ids = {int(row.contract_id) for row in rows if row.contract_id is not None}
    linked_contract_buildings = (
        {
            int(contract_row[0]): int(contract_row[1])
            for contract_row in db.query(Contract.id, Contract.building_id)
            .filter(Contract.id.in_(linked_contract_ids))
            .all()
            if contract_row[1] is not None
        }
        if linked_contract_ids
        else {}
    )
    for row in rows:
        if row.contract_id is None:
            continue
        expected_building_id = linked_contract_buildings.get(int(row.contract_id))
        if (
            expected_building_id is None
            or row.building_id is None
            or expected_building_id != int(row.building_id)
        ):
            raise HTTPException(409, "客户跟进与关联合同楼宇不一致")
    by_contract: dict[int, CustomerFollowUp] = {}
    by_customer: dict[int, CustomerFollowUp] = {}
    for row in rows:
        if row.contract_id in contract_ids and row.contract_id not in by_contract:
            by_contract[row.contract_id] = row
        if row.customer_id in customer_ids and row.customer_id not in by_customer:
            by_customer[row.customer_id] = row

    summaries: dict[int, dict | None] = {}
    for contract in contracts:
        follow_up = by_contract.get(contract.id) or by_customer.get(contract.customer_id)
        summaries[contract.id] = _serialize_follow_up_summary(follow_up)
    return summaries


def _contract_operation_context(
    db: Session,
    contracts: list[Contract],
    *,
    building_ids: tuple[int, ...] | None = None,
) -> dict:
    return {
        "receivables": _contract_receivable_summaries(db, contracts),
        "missing_body": _contract_missing_body_map(db, contracts),
        "follow_ups": _contract_follow_up_summaries(db, contracts, building_ids=building_ids),
    }


def _empty_operation_context() -> dict:
    return {"receivables": {}, "missing_body": {}, "follow_ups": {}}


def _contract_signals(contract: Contract, receivable: dict, missing_body: bool) -> dict:
    days = contract.days_to_expiry
    expiring = days is not None and 0 <= days <= 90 and contract.status in {"在租", "即将到期", "逾期占用"}
    return {
        "vacant": False,
        "rented": True,
        "expiring_90": expiring,
        "overdue": float(receivable.get("overdue_balance") or 0) > 0,
        "missing_attachment": missing_body,
    }


def _contract_operation_summary(contract: Contract, room_item: dict, context: dict) -> dict:
    receivable = context.get("receivables", {}).get(contract.id, _empty_receivable_summary())
    missing_body = bool(context.get("missing_body", {}).get(contract.id, False))
    follow_up = context.get("follow_ups", {}).get(contract.id)
    signals = _contract_signals(contract, receivable, missing_body)
    customer = getattr(contract, "customer", None)
    return {
        "current_contract": {
            "id": contract.id,
            "contract_no": contract.contract_no or "",
            "customer_id": contract.customer_id,
            "tenant_name": customer_display_name(contract),
            "status": contract.status,
            "start_date": contract.start_date.isoformat() if contract.start_date else "",
            "end_date": contract.end_date.isoformat() if contract.end_date else "",
            "days_to_expiry": contract.days_to_expiry,
            "area": room_item.get("area") or 0,
            "unit_price": room_item.get("unit_price") or 0,
            "monthly_rent": room_item.get("monthly_rent") or 0,
            "internal_contact_person": contract.internal_contact_person or "",
        },
        "customer": {
            "id": customer.id if customer else contract.customer_id,
            "name": customer.name if customer else customer_display_name(contract),
            "contact_name": customer.contact_name if customer else contract.contact_person or "",
            "contact_phone": customer.contact_phone if customer else contract.contact_phone or "",
        } if (customer or contract.customer_id) else None,
        "receivable": receivable,
        "follow_up": follow_up,
        "attachment_summary": {
            "missing_contract_body": missing_body,
            "missing_required": ["合同正文"] if missing_body else [],
        },
        "signals": signals,
    }


def _room_signals(contract: dict | None) -> dict:
    if not contract:
        return {
            "vacant": True,
            "rented": False,
            "expiring_90": False,
            "overdue": False,
            "missing_attachment": False,
        }
    signals = dict(contract.get("signals") or {})
    signals["vacant"] = False
    signals["rented"] = True
    return signals


def _room_operating_statuses(contract: dict | None, signals: dict, range_mode: bool) -> list[str]:
    statuses = []
    if signals.get("vacant"):
        statuses.append("空置")
        return statuses
    statuses.append("已占用" if range_mode else "在租")
    contract_status = contract.get("status") if contract else ""
    if contract_status and contract_status not in statuses:
        statuses.append(contract_status)
    if signals.get("expiring_90") and "即将到期" not in statuses:
        statuses.append("即将到期")
    if signals.get("overdue"):
        statuses.append("历史逾期")
    if signals.get("missing_attachment"):
        statuses.append("资料缺失")
    return statuses


def _filter_room_items(
    items: list[dict],
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    *,
    legacy_demo_a: bool = True,
) -> list[dict]:
    tower_text = str(tower or "").strip()
    accepted_towers = {tower_text} if tower_text else set()
    if tower_text and legacy_demo_a:
        accepted_towers.add(normalize_tower(tower_text))
    normalized_status = str(status or "").strip()
    normalized_floor = str(floor or "").strip()
    filtered = []
    for item in items:
        if accepted_towers and item["tower"] not in accepted_towers:
            continue
        if normalized_floor and str(item.get("floor") or "").strip() != normalized_floor:
            continue
        area = float(item.get("area") or 0)
        if min_area is not None and area < min_area:
            continue
        if max_area is not None and area > max_area:
            continue
        if normalized_status:
            statuses = set(item.get("operating_statuses") or [])
            if normalized_status == "已出租" and item.get("occupied"):
                pass
            elif normalized_status not in statuses:
                continue
        filtered.append(item)
    return filtered


def _periods_overlap(first_start: date | None, first_end: date | None, second_start: date | None, second_end: date | None) -> bool:
    return periods_overlap(first_start, first_end, second_start, second_end)


def _room_item_currently_occupies(contract: Contract, room_item: dict) -> bool:
    today = today_in_timezone()
    item_start = room_item.get("start_date")
    item_end = room_item.get("end_date")
    if _periods_overlap(item_start, item_end, today, today):
        return True
    return contract.disposal == "逾期占用" and item_end == contract.end_date and bool(item_end and item_end < today)


def _contract_relation(contract: Contract, start_date: date | None, end_date: date | None) -> str:
    today = today_in_timezone()
    if not start_date or not end_date:
        return "unknown"
    if end_date < today:
        return "holdover" if contract.disposal == "逾期占用" else "history"
    if start_date > today:
        return "future"
    return "current"


def _room_contract_row(contract: Contract, room_item: dict, window_start: date | None, window_end: date | None) -> dict:
    item_start = room_item.get("start_date")
    item_end = room_item.get("end_date")
    range_overlap = _periods_overlap(item_start, item_end, window_start, window_end)
    return {
        "contract_id": contract.id,
        "contract_no": contract.contract_no or "",
        "tenant_name": customer_display_name(contract),
        "tower": contract_tower_value(contract, room_item.get("tower") or contract.tower),
        "room_no": room_item.get("room_no") or "",
        "area": room_item.get("area") or 0,
        "unit_price": room_item.get("unit_price") or 0,
        "monthly_rent": room_item.get("monthly_rent") or 0,
        "start_date": item_start.isoformat() if item_start else "",
        "end_date": item_end.isoformat() if item_end else "",
        "status": contract.status,
        "relation": _contract_relation(contract, item_start, item_end),
        "range_overlap": range_overlap,
        "disposal": contract.disposal or "",
        "internal_contact_person": contract.internal_contact_person or "",
    }


def room_contract_history(
    db: Session,
    room_no: str,
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    room_no = str(room_no or "").strip()
    if not room_no:
        raise HTTPException(400, "室号不能为空")

    window_start, window_end = _resolve_occupancy_window(start_date, end_date)
    scoped_building_ids = _normalize_building_ids(building_ids)
    room_query = db.query(Room).filter(Room.room_no == room_no, Room.is_deleted.is_(False))
    if scoped_building_ids is not None:
        room_query = room_query.filter(Room.building_id.in_(scoped_building_ids or (-1,)))
    matching_rooms = room_query.order_by(Room.id.asc()).all()
    if len(matching_rooms) > 1:
        raise HTTPException(422, "该房号在多栋楼宇中存在，请明确选择单栋楼宇")

    contract_query = (
        db.query(Contract)
        .outerjoin(ContractRoom, ContractRoom.contract_id == Contract.id)
        .filter(Contract.is_deleted.is_(False))
    )
    if scoped_building_ids is None:
        contract_query = contract_query.filter(
            or_(ContractRoom.room_no == room_no, and_(~Contract.room_items.any(), Contract.room_no == room_no))
        )
    else:
        contract_query = contract_query.filter(
            Contract.building_id.in_(scoped_building_ids or (-1,)),
            or_(
                and_(
                    ContractRoom.building_id.in_(scoped_building_ids or (-1,)),
                    ContractRoom.room_no == room_no,
                ),
                and_(~Contract.room_items.any(), Contract.room_no == room_no),
            ),
        )
    contracts = (
        contract_query.distinct()
        .order_by(Contract.start_date.asc(), Contract.end_date.asc(), Contract.id.asc())
        .all()
    )

    matched_building_ids = {room.building_id for room in matching_rooms if room.building_id is not None}
    matched_building_ids.update(contract.building_id for contract in contracts if contract.building_id is not None)
    if scoped_building_ids is not None and len(matched_building_ids) > 1:
        raise HTTPException(422, "该房号在多栋楼宇中存在，请明确选择单栋楼宇")
    matched_building_id = next(iter(matched_building_ids), None)
    matched_building_code = None
    if matched_building_id is not None:
        matched_building_code = db.query(Building.code).filter(Building.id == matched_building_id).scalar()
    room = matching_rooms[0] if matching_rooms else None

    rows = []
    for contract in contracts:
        stored_building_by_id = {
            item.id: item.building_id
            for item in contract.room_items
            if item.id is not None
        }
        matched_items = []
        for item in contract_room_items(contract):
            if item.get("room_no") != room_no:
                continue
            item_building_id = (
                stored_building_by_id.get(item.get("id"))
                if item.get("id") is not None
                else contract.building_id
            )
            if scoped_building_ids is not None and item_building_id not in scoped_building_ids:
                continue
            if matched_building_id is not None and item_building_id != matched_building_id:
                continue
            matched_items.append(item)
        for item in matched_items:
            rows.append(_room_contract_row(contract, item, window_start, window_end))

    if not room and not rows:
        raise HTTPException(404, "房间不存在")

    current_rows = [row for row in rows if row["relation"] in {"current", "holdover"}]
    future_rows = [row for row in rows if row["relation"] == "future"]
    range_conflicts = [row for row in rows if row["range_overlap"]]
    operation = None
    if current_rows:
        current_row = current_rows[0]
        current_contract = next((contract for contract in contracts if contract.id == current_row["contract_id"]), None)
        if current_contract:
            context = _contract_operation_context(
                db,
                [current_contract],
                building_ids=scoped_building_ids,
            )
            operation = _contract_operation_summary(current_contract, current_row, context)
    return {
        "room": {
            "id": room.id if room else None,
            "tower": (
                normalize_tower_for_building(room.tower, matched_building_code)
                if room
                else (rows[0]["tower"] if rows else "")
            ),
            "floor": room.floor if room else "",
            "room_no": room_no,
            "room_type": room.room_type if room else "",
            "area": room.area if room else (rows[0]["area"] if rows else 0),
            "is_leasable": room.is_leasable if room else True,
            "notes": room.notes if room else "",
        },
        "contracts": rows,
        "current_contract": current_rows[0] if current_rows else None,
        "next_contract": future_rows[0] if future_rows else None,
        "operation": operation,
        "range_conflicts": range_conflicts,
        "selected_range": {
            "start_date": window_start.isoformat() if window_start else "",
            "end_date": window_end.isoformat() if window_end else "",
        },
    }


def room_stats(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    include_operations: bool = True,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    _resolve_occupancy_window(start_date, end_date)
    range_mode = bool(start_date or end_date)
    rooms = [
        item
        for item in list_rooms(
            db,
            start_date,
            end_date,
            tower=tower,
            status=status,
            min_area=min_area,
            max_area=max_area,
            floor=floor,
            include_operations=include_operations,
            building_ids=building_ids,
        )
        if item.get("is_leasable", True)
    ]
    total_rooms = len(rooms)
    total_area = round(sum(float(room.get("area") or 0) for room in rooms), 2)
    occupied_rooms = sum(1 for room in rooms if room.get("occupied"))
    occupied_area = round(sum(float(room.get("area") or 0) for room in rooms if room.get("occupied")), 2)

    type_stats = {}
    for room in rooms:
        key = room.get("room_type") or "办公"
        entry = type_stats.setdefault(key, {"total": 0, "area": 0, "occupied": 0, "occupied_area": 0})
        entry["total"] += 1
        entry["area"] += room.get("area") or 0
        if room.get("occupied"):
            entry["occupied"] += 1
            entry["occupied_area"] += room.get("area") or 0
    for entry in type_stats.values():
        entry["area"] = round_money(entry["area"])
        entry["occupied_area"] = round_money(entry["occupied_area"])

    return {
        "total_rooms": total_rooms,
        "total_area": round_money(total_area),
        "occupied_rooms": occupied_rooms,
        "rented_rooms": occupied_rooms,
        "occupied_area": round_money(occupied_area),
        "vacancy_rooms": total_rooms - occupied_rooms,
        "vacancy_area": round_money(total_area - occupied_area),
        "expiring_90_rooms": sum(1 for room in rooms if room.get("signals", {}).get("expiring_90")),
        "overdue_rooms": sum(1 for room in rooms if room.get("signals", {}).get("overdue")),
        "missing_attachment_rooms": sum(1 for room in rooms if room.get("signals", {}).get("missing_attachment")),
        "occupancy_rate": round(occupied_area / total_area * 100, 1) if total_area > 0 else 0,
        "availability_mode": "range" if range_mode else "current",
        "start_date": (start_date or end_date).isoformat() if (start_date or end_date) else "",
        "end_date": (end_date or start_date).isoformat() if (start_date or end_date) else "",
        "type_stats": type_stats,
    }


def _resolve_room_write_building_id(db: Session, building_id: int | None) -> int:
    if building_id is None:
        building = (
            db.query(Building)
            .filter(Building.code == "DEMO_A", Building.is_active.is_(True))
            .one_or_none()
        )
    else:
        building = db.get(Building, int(building_id))
    if building is None:
        raise HTTPException(404, "楼宇不存在或无权访问")
    return require_detail_building(db, int(building.id), "rooms").building_id


def _prepare_selected_room_write(
    db: Session,
    data: dict,
    *,
    building_id: int | None,
    existing: Room | None = None,
) -> dict:
    selected_building_id = _resolve_room_write_building_id(db, building_id)
    if existing is not None and existing.building_id != selected_building_id:
        raise HTTPException(404, "房间不存在或无权访问")
    prepared = dict(data)
    prepared["building_id"] = selected_building_id
    return prepare_detail_room_write(
        db,
        prepared,
        building_id=selected_building_id,
        existing=existing,
    )


def _room_number_conflict(
    db: Session,
    *,
    building_id: int,
    room_no: str,
    exclude_id: int | None = None,
) -> None:
    query = db.query(Room).filter(Room.building_id == building_id, Room.room_no == room_no)
    if exclude_id is not None:
        query = query.filter(Room.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(400, f"室号 {room_no} 已存在")


def validate_room_contract_building_graph(
    db: Session,
    room: Room,
) -> list[ContractRoom]:
    """Fail closed before a room write/delete can cross a building boundary."""
    if room.id is None or room.building_id is None:
        raise HTTPException(409, "房源关联关系楼宇不一致，当前操作已停止")
    room_building_id = int(room.building_id)
    linked_items = db.query(ContractRoom).filter(ContractRoom.room_id == room.id).all()
    contract_ids = {int(item.contract_id) for item in linked_items}
    contracts_by_id = (
        {
            int(contract.id): contract
            for contract in db.query(Contract).filter(Contract.id.in_(tuple(contract_ids))).all()
        }
        if contract_ids
        else {}
    )
    for item in linked_items:
        contract = contracts_by_id.get(int(item.contract_id))
        if (
            item.building_id is None
            or int(item.building_id) != room_building_id
            or contract is None
            or contract.building_id is None
            or int(contract.building_id) != room_building_id
        ):
            raise HTTPException(409, "房源关联关系楼宇不一致，当前操作已停止")
    return linked_items


def create_room(db: Session, data: dict, *, building_id: int | None = None) -> Room:
    prepared = dict(data)
    prepared["room_no"] = str(prepared.get("room_no") or "").strip()
    if not prepared["room_no"]:
        raise HTTPException(400, "室号不能为空")
    prepared["tower"] = str(prepared.get("tower") or "").strip()
    prepared = _prepare_selected_room_write(db, prepared, building_id=building_id)
    _room_number_conflict(
        db,
        building_id=int(prepared["building_id"]),
        room_no=prepared["room_no"],
    )
    room = Room(**prepared)
    db.add(room)
    db.flush()
    return room


def update_room(
    room: Room,
    payload: dict,
    db: Session,
    *,
    building_id: int | None = None,
) -> Room:
    linked_items = validate_room_contract_building_graph(db, room)
    prepared = dict(payload)
    if "room_no" in prepared:
        prepared["room_no"] = str(prepared.get("room_no") or "").strip()
        if not prepared["room_no"]:
            raise HTTPException(400, "室号不能为空")
    if "tower" in prepared:
        prepared["tower"] = str(prepared.get("tower") or "").strip()
    prepared = _prepare_selected_room_write(
        db,
        prepared,
        building_id=building_id,
        existing=room,
    )
    new_room_no = prepared.get("room_no")
    if new_room_no and new_room_no != room.room_no:
        _room_number_conflict(
            db,
            building_id=int(prepared["building_id"]),
            room_no=new_room_no,
            exclude_id=int(room.id),
        )

    old_room_no = room.room_no
    old_tower = str(room.tower or "").strip()
    for key, value in prepared.items():
        setattr(room, key, value)

    if room.room_no != old_room_no or str(room.tower or "").strip() != old_tower:
        affected_contracts = {}
        for item in linked_items:
            item.building_id = room.building_id
            item.room_no = room.room_no
            item.tower = room.tower
            affected_contracts[item.contract_id] = item.contract
        for contract in affected_contracts.values():
            summary = contract_room_summary(contract)
            contract.building_id = room.building_id
            contract.tower = summary["tower"]
            contract.room_no = summary["room_no"]
            contract.area = summary["area"]
            contract.unit_price = summary["unit_price"]
            contract.monthly_rent = summary["monthly_rent"]
            contract.updated_at = now_in_timezone()

    room.updated_at = now_in_timezone()
    db.flush()
    return room


def import_rooms_excel(content: bytes, db: Session, *, building_id: int | None = None) -> dict:
    selected_building_id = _resolve_room_write_building_id(db, building_id)
    selected_building = db.get(Building, selected_building_id)
    wb = normalize_import_workbook(openpyxl.load_workbook(io.BytesIO(content)))
    ws = wb.active

    header_row = None
    headers = {}
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=False), start=1):
        matched = 0
        candidate = {}
        for cell in row:
            raw = str(cell.value).strip().replace(" ", "").casefold() if cell.value else ""
            if raw in ROOM_IMPORT_HEADERS:
                matched += 1
                candidate[cell.column] = ROOM_IMPORT_HEADERS[raw]
        if matched >= 2:
            header_row = row_idx
            headers = candidate
            break

    if not header_row:
        raise HTTPException(400, "未找到有效表头。需要包含室号、面积等列。")

    skipped, errors, fatal_errors = 0, [], []
    pending_rooms: list[Room] = []
    seen_room_nos: set[str] = set()
    for row in ws.iter_rows(min_row=header_row + 1, values_only=False):
        payload = {}
        for cell in row:
            if cell.column in headers:
                payload[headers[cell.column]] = import_value(headers[cell.column], cell.value)

        room_no = str(payload.get("room_no") or "").strip()
        if not room_no:
            continue
        if room_no in seen_room_nos:
            skipped += 1
            errors.append(f"室号 {room_no} 在导入文件中重复，跳过")
            continue
        seen_room_nos.add(room_no)
        if db.query(Room).filter(Room.building_id == selected_building_id, Room.room_no == room_no).first():
            skipped += 1
            errors.append(f"室号 {room_no} 已存在，跳过")
            continue

        try:
            area = float(payload.get("area") or 0)
            if area < 0:
                raise ValueError("面积不能为负数")
            room_data = _prepare_selected_room_write(
                db,
                {
                    "tower": str(import_demo_tower(payload.get("tower"), selected_building.code) or "").strip(),
                    "floor": str(payload.get("floor") or ""),
                    "room_no": room_no,
                    "room_type": str(payload.get("room_type") or "办公"),
                    "area": area,
                    "notes": str(payload.get("notes") or ""),
                },
                building_id=selected_building_id,
            )
            _room_number_conflict(
                db,
                building_id=selected_building_id,
                room_no=room_no,
            )
            pending_rooms.append(Room(**room_data))
        except HTTPException as exc:
            if exc.status_code == 422:
                raise
            fatal_errors.append(f"室号 {room_no}: {exc.detail}")
        except Exception as exc:
            fatal_errors.append(f"室号 {room_no}: {exc}")

    if fatal_errors:
        raise HTTPException(400, "Excel 存在无法导入的数据，未写入任何新房间：" + "；".join(fatal_errors[:10]))

    try:
        for room in pending_rooms:
            db.add(room)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Excel 导入失败，未写入任何新房间：{exc}") from exc

    return {"added": len(pending_rooms), "skipped": skipped, "errors": errors}


def export_rooms_excel(
    db: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> StreamingResponse:
    rooms = list_rooms(db, start_date, end_date, building_ids=building_ids)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "房间清单"

    headers = ["楼塔", "楼层", "室号", "类型", "面积(㎡)", "出租状态", "客户名称", "合同编号", "合同到期日", "备注"]
    ws.append(headers)
    for idx, header in enumerate(headers, 1):
        ws.cell(1, idx).font = Font(bold=True)

    for room in rooms:
        contract = room.get("contract") or {}
        ws.append(
            [
                room["tower"],
                room["floor"],
                room["room_no"],
                room["room_type"],
                room["area"],
                "已出租" if room["occupied"] else "空置",
                contract.get("tenant_name", ""),
                contract.get("contract_no", ""),
                contract.get("end_date", ""),
                room["notes"],
            ]
        )

    for idx, width in enumerate([8, 6, 10, 8, 10, 10, 16, 16, 14, 20], 1):
        ws.column_dimensions[chr(64 + idx)].width = width

    localize_workbook(wb)

    sanitize_workbook(wb)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"房间清单_{today_in_timezone().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def export_room_operations_excel_scoped(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    building_ids: Iterable[int] | None = None,
) -> StreamingResponse:
    """Keep the established operations export format while applying property scope."""
    from app.services.import_export import (
        NO_DATA_MESSAGE,
        _add_export_metadata,
        _date_text,
        _export_filename,
        _make_workbook,
        _tower_export_group,
        _xlsx_response,
    )

    rooms = list_rooms(
        db,
        start_date,
        end_date,
        tower=tower,
        status=status,
        min_area=min_area,
        max_area=max_area,
        floor=floor,
        include_operations=True,
        building_ids=building_ids,
    )
    normalized_building_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) if building_ids is not None else None
    selected_building_codes = (
        {
            str(code).casefold()
            for (code,) in db.query(Building.code)
            .filter(Building.id.in_(normalized_building_ids or (-1,)))
            .all()
        }
        if normalized_building_ids is not None
        else {LEGACY_DEFAULT_BUILDING_CODE.casefold()}
    )
    legacy_demo_a = selected_building_codes == {LEGACY_DEFAULT_BUILDING_CODE.casefold()}
    rows = []
    for room in rooms:
        contract = room.get("contract") or {}
        rows.append(
            [
                _tower_export_group(
                    room.get("tower", ""),
                    floor=room.get("floor", ""),
                    room_type=room.get("room_type", ""),
                    room_no=room.get("room_no", ""),
                    legacy_demo_a=legacy_demo_a,
                ),
                room.get("floor", ""),
                room.get("room_no", ""),
                room.get("room_type", ""),
                room.get("area", 0),
                "、".join(room.get("operating_statuses") or []),
                contract.get("tenant_name", ""),
                contract.get("contract_no", ""),
                contract.get("start_date", ""),
                contract.get("end_date", ""),
                contract.get("monthly_rent", 0),
                contract.get("overdue_balance", 0),
                contract.get("unpaid_balance", 0),
                contract.get("next_follow_up_date", ""),
                "缺合同正文" if contract.get("missing_contract_body") else "",
                room.get("notes", ""),
            ]
        )
    workbook = _make_workbook(
        "房源经营清单",
        [
            "塔楼",
            "楼层",
            "室号",
            "类型",
            "面积",
            "经营状态",
            "客户名称",
            "合同编号",
            "起租日",
            "到期日",
            "月租金",
            "历史逾期金额",
            "累计未收",
            "下次跟进",
            "资料缺失",
            "备注",
        ],
        rows,
        title=f"房源经营清单 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("start_date", "开始日期", _date_text(start_date) if start_date else None),
        ("end_date", "结束日期", _date_text(end_date) if end_date else None),
        ("tower", "塔楼", _tower_export_group(tower, legacy_demo_a=legacy_demo_a) if tower else None),
        ("status", "经营状态", status),
        ("min_area", "最小面积", min_area),
        ("max_area", "最大面积", max_area),
        ("floor", "楼层", floor),
    ]
    _add_export_metadata(workbook, export_type="房源经营清单", filters=filters, row_count=len(rows))
    return _xlsx_response(
        workbook,
        _export_filename(
            "room-operations",
            [
                ("start", start_date),
                ("end", end_date),
                ("tower", _tower_export_group(tower, legacy_demo_a=legacy_demo_a) if tower else None),
                ("status", status),
                ("floor", floor),
            ],
        ),
    )


def room_template(
    *,
    db: Session | None = None,
    building_id: int | None = None,
) -> StreamingResponse:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        selected_building_id = _resolve_room_write_building_id(session, building_id)
        detail_scope = require_detail_building(session, selected_building_id, "rooms")
        if detail_scope.building_code.casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold():
            samples = [
                ["南塔", "16", "1601", "办公", 800, "示例数据"],
                ["北塔", "5", "501", "办公", 200, ""],
                ["商业配套", "1", "B101", "商铺", 300, "底商"],
            ]
            filename = "房间导入模板.xlsx"
        else:
            zones = detail_scope.profile.space_structure.zones
            sample_zone = zones[0].name if zones else ""
            samples = [
                [sample_zone, "", "示例单元-01", "办公", 100, "示例行仅说明格式"],
            ]
            filename = f"{detail_scope.building_code}-房源导入模板.xlsx"

        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "房间导入模板"
        workbook._demo_tower_values = detail_scope.building_code.casefold() == "demo_a"
        headers = ["楼塔", "楼层", "室号", "类型", "面积(㎡)", "备注"]
        worksheet.append(headers)
        for index, header in enumerate(headers, 1):
            worksheet.cell(1, index).font = Font(bold=True)
        for sample in samples:
            worksheet.append(sample)

        if detail_scope.building_code.casefold() != LEGACY_DEFAULT_BUILDING_CODE.casefold():
            zones_worksheet = workbook.create_sheet("可用分区")
            zones_worksheet.append(["分区代码", "分区名称", "分区类型"])
            for cell in zones_worksheet[1]:
                cell.font = Font(bold=True)
            for zone in zones:
                zones_worksheet.append([zone.code, zone.name, zone.kind])

        localize_workbook(workbook)

        sanitize_workbook(workbook)
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    finally:
        if owns_session:
            session.close()
