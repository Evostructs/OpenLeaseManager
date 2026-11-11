from __future__ import annotations

from datetime import date
from typing import Iterable

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from app.core.time import today_in_timezone
from app.core.money import round_money
from app.services.calculation_context import reuse_read_calculations
from app.models import Attachment, Contract, ContractDeposit, ContractRoom, CustomerFollowUp
from app.services.common import contract_room_summary, customer_display_name, serialize_deposit
from app.services.contracts import _is_currently_active, get_monthly_receivables, get_monthly_receivables_batch
from app.services.receivable_history import (
    OVERDUE_LOOKBACK_MONTHS,
    collect_overdue_history,
    month_keys_back,
    monthly_report_cache_key,
)
from app.services.rooms import room_stats


CONTRACT_BODY_CATEGORY = "合同正文"
DONE_FOLLOW_UP_STATUS = "done"


def _money(value: float | int | None) -> float:
    return round_money(float(value or 0))


def _month_keys(anchor: date, months: int) -> list[str]:
    start = anchor.replace(day=1) + relativedelta(months=-(months - 1))
    return [(start + relativedelta(months=offset)).strftime("%Y-%m") for offset in range(months)]


def _receivable_report(
    db: Session,
    month: str,
    monthly_reports: dict[str, dict],
    contract_ids: list[int] | None = None,
) -> dict:
    if month not in monthly_reports:
        monthly_reports[month] = get_monthly_receivables(db, month, contract_ids=contract_ids)
    return monthly_reports[month]


def _contract_reference(contract: Contract | None, fallback: dict | None = None) -> dict:
    fallback = fallback or {}
    summary = contract_room_summary(contract) if contract else {}
    return {
        "contract_id": contract.id if contract else fallback.get("contract_id"),
        "contract_no": contract.contract_no if contract else fallback.get("contract_no", ""),
        "customer_id": contract.customer_id if contract else fallback.get("customer_id"),
        "tenant_name": customer_display_name(contract) if contract else fallback.get("tenant_name", ""),
        "room_no": summary.get("room_no") or fallback.get("room_no", ""),
        "tower": summary.get("tower") or fallback.get("tower", ""),
        "area": _money(summary.get("area") or fallback.get("area")),
        "end_date": contract.end_date.isoformat() if contract and contract.end_date else fallback.get("end_date", ""),
        "days_left": contract.days_to_expiry if contract else fallback.get("days_left"),
        "status": contract.status if contract else fallback.get("status", ""),
        "target_type": "contract",
    }


def _current_month_payload(report: dict) -> dict:
    return {
        "month": report["month"],
        "receivable": report["total_rent"],
        "received": report["total_received"],
        "unpaid_balance": report["total_unpaid_balance"],
        "overdue_balance": report["total_overdue_balance"],
        "not_due_balance": report["total_not_due_balance"],
        "received_rate": report["received_rate"],
        "unpaid_count": report["unpaid_count"],
        "overdue_count": report["overdue_count"],
    }


def _trend_payload(
    db: Session,
    anchor: date,
    monthly_reports: dict[str, dict],
    contract_ids: list[int] | None = None,
) -> list[dict]:
    rows = []
    for month in _month_keys(anchor, 6):
        item = _receivable_report(db, month, monthly_reports, contract_ids)
        rows.append(
            {
                "month": item["month"],
                "receivable": item["total_rent"],
                "received": item["total_received"],
                "unpaid_balance": item["total_unpaid_balance"],
                "overdue_balance": item["total_overdue_balance"],
                "received_rate": item["received_rate"],
            }
        )
    return rows


def _overdue_contracts(
    db: Session,
    contracts: list[Contract],
    anchor: date,
    monthly_reports: dict[str, dict],
    months: int = OVERDUE_LOOKBACK_MONTHS,
) -> tuple[dict, list[dict]]:
    contract_map = {contract.id: contract for contract in contracts}
    history = collect_overdue_history(
        db,
        months=month_keys_back(anchor, months),
        monthly_reports=monthly_reports,
        contracts=contracts,
    )

    rows = []
    for contract_id, entry in history["overdue_by_contract"].items():
        contract = contract_map.get(contract_id)
        rows.append(
            {
                **_contract_reference(contract, entry["sample_row"]),
                "type": "overdue_contract",
                "label": "历史逾期合同",
                "overdue_balance": entry["overdue_balance"],
                "unpaid_balance": entry["unpaid_balance"],
                "overdue_months": entry["overdue_months"],
                "max_aging_days": entry["max_aging_days"],
                "latest_month": entry["latest_month"],
                "dunning_owner": entry["dunning_owner"],
                "next_follow_up_date": entry["next_follow_up_date"],
                # 与风险中心同一严重度阈值，映射到看板的展示词汇
                "severity": "danger" if entry["severity"] == "high" else "warning",
            }
        )

    rows = sorted(
        rows,
        key=lambda item: (-item["overdue_balance"], -item["max_aging_days"], item["contract_no"] or ""),
    )
    summary = {
        "contract_count": len(rows),
        "total_overdue_balance": history["total_overdue_balance"],
    }
    return summary, rows


def _expiring_contracts(contracts: list[Contract]) -> list[dict]:
    rows = []
    for contract in contracts:
        days = contract.days_to_expiry
        if days is None or days < 0 or days > 90:
            continue
        if not _is_currently_active(contract):
            continue
        rows.append(
            {
                **_contract_reference(contract),
                "type": "expiring_contract",
                "label": "90天内到期合同",
                "severity": "warning" if days > 30 else "danger",
            }
        )
    rows.sort(key=lambda item: (item["days_left"], item["contract_no"] or ""))
    return rows


def _due_follow_up_customers(
    db: Session,
    anchor: date,
    building_ids: tuple[int, ...] | None = None,
) -> tuple[dict, list[dict]]:
    query = (
        db.query(CustomerFollowUp)
        .options(
            selectinload(CustomerFollowUp.customer),
            selectinload(CustomerFollowUp.contract).selectinload(Contract.room_items),
        )
        .filter(
            CustomerFollowUp.next_follow_up_date.isnot(None),
            CustomerFollowUp.next_follow_up_date <= anchor,
            or_(CustomerFollowUp.status.is_(None), CustomerFollowUp.status != DONE_FOLLOW_UP_STATUS),
        )
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
    rows = query.order_by(CustomerFollowUp.next_follow_up_date.asc(), CustomerFollowUp.id.asc()).all()
    grouped: dict[int, dict] = {}
    for row in rows:
        customer = getattr(row, "customer", None)
        if not customer:
            continue
        contract = getattr(row, "contract", None)
        if row.contract_id is not None and (
            contract is None
            or contract.building_id is None
            or row.building_id is None
            or int(contract.building_id) != int(row.building_id)
        ):
            raise HTTPException(409, "客户跟进与关联合同楼宇不一致")
        entry = grouped.setdefault(
            row.customer_id,
            {
                "type": "due_follow_up_customer",
                "label": "待跟进客户",
                "severity": "danger" if row.next_follow_up_date and row.next_follow_up_date < anchor else "warning",
                "customer_id": row.customer_id,
                "customer_name": customer.name or "",
                "contact_name": customer.contact_name or "",
                "contact_phone": customer.contact_phone or "",
                "follow_up_id": row.id,
                "contract_id": row.contract_id,
                "contract_no": contract.contract_no if contract else "",
                "room_no": contract_room_summary(contract).get("room_no", "") if contract else "",
                "next_follow_up_date": row.next_follow_up_date.isoformat() if row.next_follow_up_date else "",
                "next_action": row.next_action or "",
                "owner": row.owner or "",
                "open_follow_up_count": 0,
                "overdue_days": max((anchor - row.next_follow_up_date).days, 0) if row.next_follow_up_date else 0,
                "target_type": "customer",
            },
        )
        entry["open_follow_up_count"] += 1
    items = list(grouped.values())
    summary = {"customer_count": len(items), "follow_up_count": len(rows)}
    return summary, items


def _unpaid_deposits(
    db: Session,
    building_ids: tuple[int, ...] | None = None,
) -> tuple[dict, list[dict]]:
    query = (
        db.query(ContractDeposit)
        .options(
            selectinload(ContractDeposit.contract).selectinload(Contract.customer),
            selectinload(ContractDeposit.contract).selectinload(Contract.room_items),
        )
        .join(Contract)
        .filter(Contract.is_deleted.is_(False))
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
    deposits = query.all()
    rows = []
    for deposit in deposits:
        data = serialize_deposit(deposit)
        unpaid_amount = _money(data["unpaid_amount"])
        if unpaid_amount <= 0:
            continue
        contract = getattr(deposit, "contract", None)
        rows.append(
            {
                **_contract_reference(contract),
                "type": "unpaid_deposit",
                "label": "未收押金",
                "severity": "warning",
                "deposit_id": deposit.id,
                "deposit_type": data["deposit_type"],
                "expected_amount": data["expected_amount"],
                "received_amount": data["received_amount"],
                "unpaid_amount": unpaid_amount,
                "due_date": deposit.due_date.isoformat() if deposit.due_date else "",
            }
        )
    rows.sort(key=lambda item: (-item["unpaid_amount"], item.get("due_date") or "9999-99-99", item["contract_no"] or ""))
    summary = {
        "deposit_count": len(rows),
        "unpaid_amount": _money(sum(item["unpaid_amount"] for item in rows)),
    }
    return summary, rows


def _missing_contract_body(db: Session, contracts: list[Contract]) -> list[dict]:
    active_contracts = [contract for contract in contracts if _is_currently_active(contract)]
    contract_pairs: set[tuple[int, int]] = set()
    for contract in active_contracts:
        if contract.id is None:
            continue
        if contract.building_id is None:
            raise HTTPException(409, "合同尚未明确楼宇归属")
        contract_pairs.add((int(contract.id), int(contract.building_id)))
    if not contract_pairs:
        return []
    attached_rows = (
        db.query(Attachment.entity_id, Attachment.building_id)
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
    )
    with_body = {(int(row[0]), int(row[1])) for row in attached_rows if row[1] is not None}
    rows = []
    for contract in active_contracts:
        contract_pair = (int(contract.id), int(contract.building_id))
        if contract_pair in with_body:
            continue
        rows.append(
            {
                **_contract_reference(contract),
                "type": "missing_contract_body",
                "label": "缺合同正文附件",
                "severity": "warning",
                "missing_category": CONTRACT_BODY_CATEGORY,
            }
        )
    rows.sort(key=lambda item: ((item["days_left"] if item["days_left"] is not None else 99999), item["contract_no"] or ""))
    return rows


def _todo_item(item: dict) -> dict:
    title = item.get("tenant_name") or item.get("customer_name") or item.get("contract_no") or item.get("label")
    return {
        "type": item.get("type"),
        "label": item.get("label"),
        "severity": item.get("severity", "info"),
        "title": title,
        "contract_id": item.get("contract_id"),
        "customer_id": item.get("customer_id"),
        "room_no": item.get("room_no", ""),
        "target_type": item.get("target_type", "contract"),
        "amount": item.get("overdue_balance") or item.get("unpaid_amount") or 0,
        "days_left": item.get("days_left"),
        "due_date": item.get("due_date") or item.get("next_follow_up_date") or item.get("end_date") or "",
        "meta": item,
    }


def _top_todos(queues: dict[str, list[dict]]) -> list[dict]:
    ordered_groups = [
        ("overdue_contracts", 4),
        ("expiring_contracts", 4),
        ("due_follow_up_customers", 4),
        ("unpaid_deposits", 4),
        ("missing_contract_body", 4),
    ]
    todos = []
    for key, limit in ordered_groups:
        todos.extend(_todo_item(item) for item in queues.get(key, [])[:limit])
    return todos


def _limit_queues(queues: dict[str, list[dict]], limit: int) -> dict[str, list[dict]]:
    if limit <= 0:
        return queues
    return {key: rows[:limit] for key, rows in queues.items()}


@reuse_read_calculations
def get_operations_dashboard(
    db: Session,
    *,
    include_trend: bool = True,
    queue_limit: int = 0,
    risk_limit: int = 8,
    building_ids: Iterable[int] | None = None,
) -> dict:
    today = today_in_timezone()
    month = today.strftime("%Y-%m")
    scoped_building_ids = (
        None
        if building_ids is None
        else tuple(dict.fromkeys(int(item) for item in building_ids))
    )
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
    scoped_contract_ids = [contract.id for contract in contracts] if scoped_building_ids is not None else None
    # Current, trend, overdue and risk widgets reuse the same twelve-month
    # receivable inputs. Loading them as one batch avoids repeating the same
    # contract/statement/payment queries for every month.
    monthly_reports = get_monthly_receivables_batch(
        db,
        month_keys_back(today, OVERDUE_LOOKBACK_MONTHS),
        contracts=contracts,
    )

    current_receivables = _receivable_report(db, month, monthly_reports, scoped_contract_ids)
    current_month = _current_month_payload(current_receivables)
    trend = _trend_payload(db, today, monthly_reports, scoped_contract_ids) if include_trend else []
    vacancy = room_stats(db, include_operations=False, building_ids=scoped_building_ids)
    overdue_summary, overdue_contracts = _overdue_contracts(db, contracts, today, monthly_reports)
    expiring_contracts = _expiring_contracts(contracts)
    follow_up_summary, follow_up_customers = _due_follow_up_customers(db, today, scoped_building_ids)
    deposit_summary, unpaid_deposits = _unpaid_deposits(db, scoped_building_ids)
    missing_body = _missing_contract_body(db, contracts)
    from app.services.risks import list_risks

    # This boundary knows the batch used exactly the same filtered contracts as
    # the risk scope. The generic history cache must not assume a bare month
    # report belongs to any requested contract scope (especially an empty one).
    risk_monthly_reports = {
        monthly_report_cache_key(report_month, scoped_contract_ids): report
        for report_month, report in monthly_reports.items()
    }
    risks = list_risks(
        db,
        limit=risk_limit,
        monthly_reports=risk_monthly_reports,
        contracts=contracts,
        building_ids=scoped_building_ids,
    )

    full_queues = {
        "overdue_contracts": overdue_contracts,
        "expiring_contracts": expiring_contracts,
        "due_follow_up_customers": follow_up_customers,
        "unpaid_deposits": unpaid_deposits,
        "missing_contract_body": missing_body,
    }
    queue_counts = {key: len(rows) for key, rows in full_queues.items()}
    queues = _limit_queues(full_queues, queue_limit)
    metrics = {
        "current_month_receivable": current_month["receivable"],
        "current_month_received": current_month["received"],
        "current_month_unpaid_balance": current_month["unpaid_balance"],
        "current_month_overdue_balance": current_month["overdue_balance"],
        "overdue_balance": overdue_summary["total_overdue_balance"],
        "received_rate": current_month["received_rate"],
        "vacancy_area": vacancy["vacancy_area"],
        "vacancy_room_count": vacancy["vacancy_rooms"],
        "expiring_contract_count_90": len(expiring_contracts),
        "due_follow_up_customer_count": follow_up_summary["customer_count"],
        "unpaid_deposit_amount": deposit_summary["unpaid_amount"],
        "unpaid_deposit_count": deposit_summary["deposit_count"],
        "missing_contract_body_count": len(missing_body),
        "risk_count": risks["summary"]["total"],
        "high_risk_count": risks["summary"]["by_severity"]["high"],
    }

    return {
        "date": today.isoformat(),
        "month": month,
        "metrics": metrics,
        "current_month": current_month,
        "vacancy": {
            "room_count": vacancy["vacancy_rooms"],
            "area": vacancy["vacancy_area"],
            "total_rooms": vacancy["total_rooms"],
            "total_area": vacancy["total_area"],
            "occupancy_rate": vacancy["occupancy_rate"],
        },
        "overdue": overdue_summary,
        "follow_ups": follow_up_summary,
        "deposits": deposit_summary,
        "missing_contract_body": {"contract_count": len(missing_body)},
        "risks": {"summary": risks["summary"], "top": risks["items"]},
        "trend": trend,
        "queues": queues,
        "queue_counts": queue_counts,
        "todos": _top_todos(full_queues),
    }


def _format_number(value: float | int | None) -> str:
    number = _money(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def _format_rate(value: float | int | None) -> str:
    return f"{_money(value)}%"


def _metric(
    key: str,
    label: str,
    raw_value: float | int | None,
    *,
    unit: str = "",
    helper: str = "",
    tone: str = "info",
    progress: float | None = None,
    intent_key: str = "",
) -> dict:
    value = _format_rate(raw_value) if unit == "%" else _format_number(raw_value)
    return {
        "key": key,
        "label": label,
        "value": value,
        "raw_value": raw_value,
        "unit": unit,
        "helper": helper,
        "tone": tone,
        "progress": progress,
        "intent_key": intent_key or key,
    }


def _action_target(item: dict) -> dict:
    return {
        "target_type": item.get("target_type", "contract"),
        "customer_id": item.get("customer_id"),
        "customer_name": item.get("customer_name") or item.get("tenant_name") or "",
        "contract_id": item.get("contract_id"),
        "contract_no": item.get("contract_no", ""),
        "room_no": item.get("room_no", ""),
    }


def _safe_lane_item(item: dict) -> dict:
    return {
        "type": item.get("type", ""),
        "label": item.get("label", ""),
        "tone": item.get("severity", "info"),
        "title": item.get("tenant_name") or item.get("customer_name") or item.get("contract_no") or item.get("label") or "",
        "subtitle": item.get("contract_no") or item.get("room_no") or "",
        "value": _action_value(item),
        "target": _action_target(item),
        "signals": _action_signals(item),
    }


def _action_value(item: dict) -> str:
    amount = item.get("overdue_balance") or item.get("unpaid_amount")
    if amount:
        return _format_number(amount)
    if item.get("days_left") is not None:
        return f"{item['days_left']} 天"
    if item.get("overdue_days") is not None:
        return f"{item['overdue_days']} 天"
    return item.get("due_date") or item.get("next_follow_up_date") or item.get("end_date") or ""


def _action_signals(item: dict) -> list[dict]:
    signals = []
    for key, label in [
        ("overdue_balance", "历史逾期"),
        ("unpaid_balance", "累计未收"),
        ("unpaid_amount", "未收押金"),
        ("days_left", "剩余天数"),
        ("overdue_days", "逾期天数"),
        ("open_follow_up_count", "待跟进"),
    ]:
        value = item.get(key)
        if value not in (None, "", 0, 0.0):
            signals.append({"key": key, "label": label, "value": value})
    return signals


def _action_priority(item: dict) -> str:
    if item.get("severity") == "danger":
        return "P1"
    if item.get("type") in {"unpaid_deposit", "missing_contract_body"}:
        return "P2"
    return "P3"


def _primary_action(item: dict) -> dict:
    target_type = item.get("target_type", "contract")
    if target_type == "customer":
        return {"type": "open_customer", "label": "查看客户", "intent_key": "open_customer"}
    return {"type": "open_contract", "label": "查看合同", "intent_key": "open_contract"}


def _next_action(item: dict, index: int) -> dict:
    action_type = item.get("type", "")
    target = _action_target(item)
    title = item.get("tenant_name") or item.get("customer_name") or item.get("contract_no") or item.get("label") or action_type
    subtitle_parts = [item.get("contract_no"), item.get("room_no"), item.get("next_action")]
    return {
        "id": f"{action_type}:{target.get('contract_id') or target.get('customer_id') or index}",
        "type": action_type,
        "priority": _action_priority(item),
        "tone": item.get("severity", "info"),
        "title": title,
        "subtitle": " / ".join(str(part) for part in subtitle_parts if part),
        "value": _action_value(item),
        "signals": _action_signals(item),
        "target": target,
        "primary_action": _primary_action(item),
        "secondary_actions": [{"type": "drilldown", "label": "查看详情", "intent_key": f"{action_type}_drilldown"}],
        "intent_key": f"{action_type}_action",
    }


def _risk_lane_items(risks: list[dict]) -> list[dict]:
    items = []
    for risk in risks:
        items.append(
            {
                "type": risk.get("risk_type", "risk"),
                "label": risk.get("title") or risk.get("risk_type") or "风险",
                "tone": risk.get("severity", "info"),
                "title": risk.get("title") or risk.get("description") or "风险",
                "subtitle": risk.get("customer_name") or risk.get("contract_no") or risk.get("room_no") or "",
                "value": _format_number(risk.get("amount")) if risk.get("amount") else "",
                "target": _action_target(risk),
                "signals": [{"key": "status", "label": "状态", "value": risk.get("status", "")}],
            }
        )
    return items


def get_operations_dashboard_v2(
    db: Session,
    *,
    include_trend: bool = True,
    queue_limit: int = 0,
    risk_limit: int = 8,
    building_ids: Iterable[int] | None = None,
) -> dict:
    v1 = get_operations_dashboard(
        db,
        include_trend=include_trend,
        queue_limit=queue_limit,
        risk_limit=risk_limit,
        building_ids=building_ids,
    )
    metrics = v1["metrics"]
    current_month = v1["current_month"]
    queue_counts = v1["queue_counts"]
    queues = v1["queues"]
    risks = v1["risks"]

    lanes = [
        {
            "key": "payment",
            "label": "收款",
            "tone": "danger" if metrics["current_month_overdue_balance"] else "warning",
            "count": queue_counts["overdue_contracts"] + queue_counts["unpaid_deposits"],
            "value": _format_number(metrics["current_month_unpaid_balance"]),
            "items": [_safe_lane_item(item) for item in queues.get("overdue_contracts", []) + queues.get("unpaid_deposits", [])],
        },
        {
            "key": "follow_up",
            "label": "跟进",
            "tone": "warning" if queue_counts["due_follow_up_customers"] else "success",
            "count": queue_counts["due_follow_up_customers"],
            "value": str(queue_counts["due_follow_up_customers"]),
            "items": [_safe_lane_item(item) for item in queues.get("due_follow_up_customers", [])],
        },
        {
            "key": "expiring",
            "label": "到期",
            "tone": "warning" if queue_counts["expiring_contracts"] else "success",
            "count": queue_counts["expiring_contracts"],
            "value": str(queue_counts["expiring_contracts"]),
            "items": [_safe_lane_item(item) for item in queues.get("expiring_contracts", [])],
        },
        {
            "key": "materials",
            "label": "资料",
            "tone": "warning" if queue_counts["missing_contract_body"] else "success",
            "count": queue_counts["missing_contract_body"],
            "value": str(queue_counts["missing_contract_body"]),
            "items": [_safe_lane_item(item) for item in queues.get("missing_contract_body", [])],
        },
        {
            "key": "risk",
            "label": "风险",
            "tone": "danger" if metrics["high_risk_count"] else "warning" if metrics["risk_count"] else "success",
            "count": metrics["risk_count"],
            "value": str(metrics["risk_count"]),
            "items": _risk_lane_items(risks.get("top", [])),
        },
    ]

    action_sources = [
        "overdue_contracts",
        "expiring_contracts",
        "due_follow_up_customers",
        "unpaid_deposits",
        "missing_contract_body",
    ]
    source_items = [item for source in action_sources for item in queues.get(source, [])]

    return {
        "contract_version": "operations-hci-v2",
        "date": v1["date"],
        "month": v1["month"],
        "facts": {
            "current_month": current_month,
            "vacancy": v1["vacancy"],
            "queue_counts": queue_counts,
            "risk_summary": risks["summary"],
        },
        "metrics": [
            _metric("current_month_receivable", "本月应收", metrics["current_month_receivable"], unit="CNY", intent_key="receivables_current_month"),
            _metric("current_month_received", "本月已收", metrics["current_month_received"], unit="CNY", tone="success", progress=current_month["received_rate"], intent_key="payments_current_month"),
            _metric("current_month_unpaid_balance", "本月未收", metrics["current_month_unpaid_balance"], unit="CNY", tone="warning", intent_key="unpaid_current_month"),
            _metric("current_month_overdue_balance", "本月逾期", metrics["current_month_overdue_balance"], unit="CNY", tone="danger", intent_key="overdue_current_month"),
            _metric("received_rate", "回款率", metrics["received_rate"], unit="%", progress=metrics["received_rate"], intent_key="received_rate"),
            _metric("vacancy_room_count", "空置房间", metrics["vacancy_room_count"], unit="间", helper=f"空置面积 {_format_number(metrics['vacancy_area'])} ㎡", intent_key="vacancy_rooms"),
            _metric("risk_count", "风险事项", metrics["risk_count"], unit="项", tone="danger" if metrics["high_risk_count"] else "warning", helper=f"高风险 {metrics['high_risk_count']} 项", intent_key="risk_center"),
        ],
        "signals": [
            {
                "key": "cash",
                "label": "回款状态",
                "tone": "danger" if current_month["overdue_balance"] else "warning",
                "items": [
                    {"key": "received_rate", "label": "回款率", "value": current_month["received_rate"]},
                    {"key": "unpaid_balance", "label": "本月未收", "value": current_month["unpaid_balance"]},
                    {"key": "overdue_balance", "label": "本月逾期", "value": current_month["overdue_balance"]},
                ],
            },
            {
                "key": "operations",
                "label": "待办压力",
                "tone": "warning" if any(queue_counts.values()) else "success",
                "items": [
                    {
                        "key": key,
                        "label": {
                            "overdue_contracts": "历史逾期合同",
                            "due_follow_up_customers": "待跟进客户",
                            "expiring_contracts": "90天内到期",
                            "unpaid_deposits": "未收押金",
                            "missing_contract_body": "附件待补",
                        }.get(key, key),
                        "value": value,
                    }
                    for key, value in queue_counts.items()
                ],
            },
        ],
        "lanes": lanes,
        "next_actions": [_next_action(item, index) for index, item in enumerate(source_items, start=1)],
        "drilldown_intents": {
            "payment": {"lane": "payment", "filters": {"queue_keys": ["overdue_contracts", "unpaid_deposits"]}},
            "follow_up": {"lane": "follow_up", "filters": {"queue_keys": ["due_follow_up_customers"]}},
            "expiring": {"lane": "expiring", "filters": {"queue_keys": ["expiring_contracts"]}},
            "materials": {"lane": "materials", "filters": {"queue_keys": ["missing_contract_body"]}},
            "risk": {"lane": "risk", "filters": {"source": "risks"}},
        },
    }


def get_operations_trend(
    db: Session,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    today = today_in_timezone()
    scoped_building_ids = (
        None
        if building_ids is None
        else tuple(dict.fromkeys(int(item) for item in building_ids))
    )
    contracts = None
    contract_ids = None
    if scoped_building_ids is not None:
        contracts = (
            db.query(Contract)
            .filter(
                Contract.is_deleted.is_(False),
                Contract.building_id.in_(scoped_building_ids or (-1,)),
            )
            .all()
        )
        contract_ids = [contract.id for contract in contracts]
    monthly_reports = get_monthly_receivables_batch(
        db,
        _month_keys(today, 6),
        **({"contracts": contracts} if contracts is not None else {}),
    )
    return {
        "date": today.isoformat(),
        "trend": _trend_payload(db, today, monthly_reports, contract_ids),
    }
