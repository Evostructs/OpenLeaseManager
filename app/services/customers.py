from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session, selectinload

from app.core.time import now_in_timezone, today_in_timezone
from app.core.money import round_money
from app.models import (
    Contract,
    Customer,
    CustomerBuildingProfile,
    CustomerFollowUp,
    PortfolioBuilding,
)
from app.schemas import CustomerCreate, CustomerFollowUpCreate, CustomerFollowUpUpdate, CustomerUpdate
from app.services.common import attachment_summary, contract_room_summary, customer_display_name, deposit_summary, dump_changes, list_entity_attachments, log_action, serialize_deposit
from app.services.legacy_demo_a_scope import ObjectScope, prepare_legacy_follow_up_write
from app.services.lease_types import normalize_signing_type
from app.services.property_scope import (
    SelectedPropertyScope,
    assert_selected_object_building,
    require_single_selected_building,
)


CUSTOMER_STATUSES = {"active", "inactive"}
FOLLOW_UP_TYPES = {"电话", "微信", "拜访", "邮件", "催收", "续租", "其他"}
FOLLOW_UP_STATUSES = {"open", "done"}
ACTIVE_CONTRACT_STATUSES = {"在租", "即将到期", "逾期占用"}
CUSTOMER_PROFILE_FIELDS = frozenset({"local_contact", "local_notes", "owner"})
CUSTOMER_MASTER_FIELDS = frozenset(
    {
        "name",
        "short_name",
        "credit_code",
        "customer_type",
        "industry",
        "contact_name",
        "contact_phone",
        "contact_email",
        "address",
        "notes",
        "status",
    }
)


def _clean_text(value) -> str:
    return str(value or "").strip()


def _customer_payload_data(payload: CustomerCreate | CustomerUpdate, *, partial: bool) -> dict:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(exclude_unset=partial)
    else:
        data = payload.dict(exclude_unset=partial)
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""
        elif isinstance(value, str):
            data[key] = value.strip()
    if "status" in data:
        data["status"] = data.get("status") or "active"
        if data["status"] not in CUSTOMER_STATUSES:
            raise HTTPException(400, "客户状态无效")
    return data


def _split_customer_payload(data: dict) -> tuple[dict, dict]:
    unknown = set(data) - CUSTOMER_MASTER_FIELDS - CUSTOMER_PROFILE_FIELDS
    if unknown:
        raise HTTPException(400, "客户资料包含未识别字段")
    master = {key: value for key, value in data.items() if key in CUSTOMER_MASTER_FIELDS}
    profile = {key: value for key, value in data.items() if key in CUSTOMER_PROFILE_FIELDS}
    return master, profile


def _follow_up_payload_data(payload: CustomerFollowUpCreate | CustomerFollowUpUpdate, *, partial: bool) -> dict:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(exclude_unset=partial)
    else:
        data = payload.dict(exclude_unset=partial)
    for key, value in list(data.items()):
        if isinstance(value, str):
            data[key] = value.strip()
    if data.get("contract_id") in ("", 0):
        data["contract_id"] = None
    if "follow_up_type" in data:
        data["follow_up_type"] = data.get("follow_up_type") or "其他"
        if data["follow_up_type"] not in FOLLOW_UP_TYPES:
            raise HTTPException(400, "跟进类型无效")
    if "status" in data:
        data["status"] = data.get("status") or "open"
        if data["status"] not in FOLLOW_UP_STATUSES:
            raise HTTPException(400, "跟进状态无效")
    if "content" in data and not _clean_text(data.get("content")):
        raise HTTPException(400, "跟进内容不能为空")
    return data


def _ensure_unique_name(db: Session, name: str, customer_id: int | None = None) -> None:
    query = db.query(Customer).filter(Customer.name == name)
    if customer_id is not None:
        query = query.filter(Customer.id != customer_id)
    if query.first():
        raise HTTPException(400, f"客户名称 {name} 已存在")


def _customer_building_predicate(building_ids: Sequence[int]):
    normalized_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) or (-1,)
    today = today_in_timezone()
    profile_exists = exists().where(
        CustomerBuildingProfile.customer_id == Customer.id,
        CustomerBuildingProfile.building_id.in_(normalized_ids),
        CustomerBuildingProfile.valid_from <= today,
        or_(CustomerBuildingProfile.valid_to.is_(None), CustomerBuildingProfile.valid_to >= today),
    )
    return or_(
        Customer.contracts.any(
            and_(Contract.is_deleted.is_(False), Contract.building_id.in_(normalized_ids))
        ),
        Customer.follow_ups.any(CustomerFollowUp.building_id.in_(normalized_ids)),
        profile_exists,
    )


def find_customer_by_exact_name(
    db: Session,
    name: str | None,
    *,
    building_ids: Sequence[int] | None = None,
) -> Customer | None:
    customer_name = "" if name is None else str(name)
    if not customer_name.strip():
        return None
    query = db.query(Customer).filter(Customer.name == customer_name)
    if building_ids is not None:
        query = query.filter(_customer_building_predicate(building_ids))
    return query.first()


def resolve_contract_customer(
    db: Session,
    data: dict,
    *,
    match_name: bool = False,
    building_ids: Sequence[int] | None = None,
) -> Customer | None:
    raw_customer_id = data.get("customer_id")
    if raw_customer_id in ("", 0):
        raw_customer_id = None
    if raw_customer_id is not None:
        query = db.query(Customer).filter(Customer.id == int(raw_customer_id))
        if building_ids is not None:
            query = query.filter(_customer_building_predicate(building_ids))
        customer = query.first()
        if not customer:
            raise HTTPException(404, "客户不存在或无权访问")
        data["customer_id"] = customer.id
        if not _clean_text(data.get("tenant_name")):
            data["tenant_name"] = customer.name
        return customer

    if "customer_id" in data:
        data["customer_id"] = None

    if match_name:
        customer = find_customer_by_exact_name(
            db,
            data.get("tenant_name"),
            building_ids=building_ids,
        )
        if customer:
            data["customer_id"] = customer.id
            return customer
        # Keep the legacy free-text tenant import compatible.  A name that is
        # not visible in the selected building remains unlinked regardless of
        # whether it is absent globally or belongs to another building; this
        # avoids both implicit cross-building linkage and a name oracle.
    return None


def serialize_customer_follow_up(follow_up: CustomerFollowUp) -> dict:
    contract = getattr(follow_up, "contract", None)
    return {
        "id": follow_up.id,
        "customer_id": follow_up.customer_id,
        "contract_id": follow_up.contract_id,
        "contract_no": contract.contract_no if contract else "",
        "follow_up_type": follow_up.follow_up_type or "",
        "occurred_at": follow_up.occurred_at,
        "content": follow_up.content or "",
        "next_action": follow_up.next_action or "",
        "next_follow_up_date": follow_up.next_follow_up_date,
        "owner": follow_up.owner or "",
        "status": follow_up.status or "open",
        "created_at": follow_up.created_at,
        "updated_at": follow_up.updated_at,
    }


def build_customer_operating_summary(
    db: Session,
    customer: Customer,
    contracts: list[Contract],
    *,
    follow_ups: list[CustomerFollowUp] | None = None,
) -> dict:
    today = today_in_timezone()
    contract_ids = {contract.id for contract in contracts}
    contract_id_list = list(contract_ids)
    active_contracts = [
        contract
        for contract in contracts
        if contract.status in ACTIVE_CONTRACT_STATUSES and contract.disposal != "已退租"
    ]
    active_summaries = [contract_room_summary(contract, today, today) for contract in active_contracts]
    current_leased_area = round_money(sum(summary["area"] for summary in active_summaries))
    current_monthly_rent = round_money(sum(summary["monthly_rent"] for summary in active_summaries))

    current_month = today.strftime("%Y-%m")
    current_month_receivable = 0.0
    unpaid_balance = 0.0
    overdue_balance = 0.0
    start_dates = [contract.start_date for contract in contracts if contract.start_date]
    if contract_ids and start_dates:
        from app.services.receivable_history import collect_overdue_history, month_keys_between

        # 客户摘要看全历史余额（自最早合同起租月起），窗口与风险中心/看板的
        # 12 个月回看不同，但逐月聚合逻辑同源单一口径
        history = collect_overdue_history(
            db,
            months=month_keys_between(min(start_dates), today),
            contract_ids=contract_id_list,
        )
        for entry in history["by_contract"].values():
            unpaid_balance += entry["unpaid_balance"]
            overdue_balance += entry["overdue_balance"]
        current_month_receivable = history["month_totals"].get(current_month, {}).get("receivable", 0.0)

    future_contracts = [
        contract
        for contract in contracts
        if contract.end_date and contract.end_date >= today and contract.disposal != "已退租"
    ]
    nearest = min(future_contracts, key=lambda item: (item.end_date, item.id), default=None)
    nearest_expiring_contract = None
    nearest_expiry_days = None
    if nearest:
        nearest_expiry_days = (nearest.end_date - today).days if nearest.end_date else None
        nearest_expiring_contract = {
            "id": nearest.id,
            "contract_no": nearest.contract_no or "",
            "end_date": nearest.end_date,
            "days_left": nearest_expiry_days,
            "status": nearest.status,
            "room_no": contract_room_summary(nearest)["room_no"],
        }

    follow_ups = list(getattr(customer, "follow_ups", []) or []) if follow_ups is None else list(follow_ups)
    last_follow_up = max(follow_ups, key=lambda item: (item.occurred_at or datetime.min, item.id or 0), default=None)
    open_follow_ups = [
        item
        for item in follow_ups
        if (item.status or "open") != "done" and item.next_follow_up_date
    ]
    next_follow_up = min(open_follow_ups, key=lambda item: (item.next_follow_up_date, item.id or 0), default=None)

    deposit_entries = []
    deposit_rows = []
    active_contract_ids = {contract.id for contract in active_contracts}
    for contract in contracts:
        for deposit in getattr(contract, "deposits", []) or []:
            deposit_entries.append(deposit)
            deposit_rows.append((contract, serialize_deposit(deposit)))
    deposit_totals = deposit_summary(deposit_entries)
    deposit_refund_pending_amount = 0.0
    deposit_refund_pending_count = 0
    deposit_insufficient_amount = 0.0
    deposit_insufficient_count = 0
    for contract, row in deposit_rows:
        available = float(row.get("available_amount") or 0)
        expected = float(row.get("expected_amount") or 0)
        received = float(row.get("received_amount") or 0)
        if available > 0 and ((contract.end_date and contract.end_date < today) or contract.disposal == "已退租"):
            deposit_refund_pending_amount += available
            deposit_refund_pending_count += 1
        if contract.id in active_contract_ids and expected > 0 and received >= expected and available < expected:
            deposit_insufficient_amount += expected - available
            deposit_insufficient_count += 1

    action_cues = []
    if overdue_balance > 0:
        action_cues.append({"type": "overdue", "level": "danger", "text": f"历史逾期 {round_money(overdue_balance)}"})
    if nearest_expiring_contract and nearest_expiry_days is not None and nearest_expiry_days <= 90:
        action_cues.append({"type": "expiry", "level": "warning", "text": f"合同 {nearest_expiring_contract['contract_no']} {nearest_expiry_days} 天后到期"})
    if next_follow_up and next_follow_up.next_follow_up_date:
        if next_follow_up.next_follow_up_date < today:
            days = (today - next_follow_up.next_follow_up_date).days
            action_cues.append({"type": "follow_up_overdue", "level": "danger", "text": f"下次跟进已逾期 {days} 天"})
        elif next_follow_up.next_follow_up_date == today:
            action_cues.append({"type": "follow_up_due", "level": "warning", "text": "今日需要跟进"})
    if deposit_totals["unpaid_amount"] > 0:
        action_cues.append({"type": "deposit_unpaid", "level": "warning", "text": f"押金未收 {round_money(deposit_totals['unpaid_amount'])}"})
    if deposit_refund_pending_amount > 0:
        action_cues.append({"type": "deposit_refund_pending", "level": "info", "text": f"押金待退 {round_money(deposit_refund_pending_amount)}"})
    if deposit_insufficient_amount > 0:
        action_cues.append({"type": "deposit_insufficient", "level": "warning", "text": f"押金不足 {round_money(deposit_insufficient_amount)}"})

    return {
        "current_leased_area": current_leased_area,
        "current_monthly_rent": current_monthly_rent,
        "current_month": current_month,
        "current_month_receivable": round_money(current_month_receivable),
        "unpaid_balance": round_money(unpaid_balance),
        "overdue_balance": round_money(overdue_balance),
        "contract_count": len(contracts),
        "nearest_expiring_contract": nearest_expiring_contract,
        "nearest_expiry_days": nearest_expiry_days,
        "last_follow_up_time": last_follow_up.occurred_at if last_follow_up else None,
        "next_follow_up_date": next_follow_up.next_follow_up_date if next_follow_up else None,
        "deposit_expected_amount": deposit_totals["expected_amount"],
        "deposit_received_amount": deposit_totals["received_amount"],
        "deposit_unpaid_amount": deposit_totals["unpaid_amount"],
        "deposit_refunded_amount": deposit_totals["refunded_amount"],
        "deposit_offset_amount": deposit_totals["offset_amount"],
        "deposit_available_amount": deposit_totals["available_amount"],
        "deposit_refund_pending_amount": round_money(deposit_refund_pending_amount),
        "deposit_insufficient_amount": round_money(deposit_insufficient_amount),
        "deposit_unpaid_count": deposit_totals["unpaid_count"],
        "deposit_refund_pending_count": deposit_refund_pending_count,
        "deposit_insufficient_count": deposit_insufficient_count,
        "action_cues": action_cues,
    }


def _scope_ids(scope: SelectedPropertyScope) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(item) for item in scope.building_ids))


def selected_customer_predicate(scope: SelectedPropertyScope):
    return _customer_building_predicate(_scope_ids(scope))


def selected_customer_contracts(
    customer: Customer,
    scope: SelectedPropertyScope | None,
) -> list[Contract]:
    allowed_ids = set(_scope_ids(scope)) if scope is not None else None
    return [
        contract
        for contract in customer.contracts or []
        if not contract.is_deleted
        and (allowed_ids is None or contract.building_id in allowed_ids)
    ]


def assert_customer_follow_up_scope(
    follow_up: CustomerFollowUp,
    scope: SelectedPropertyScope,
) -> int:
    building_id = assert_selected_object_building(scope, follow_up.building_id)
    contract = getattr(follow_up, "contract", None)
    if follow_up.contract_id is not None:
        if contract is None or contract.is_deleted:
            raise HTTPException(409, "跟进记录的关联合同不存在")
        if contract.customer_id != follow_up.customer_id or contract.building_id != building_id:
            raise HTTPException(409, "跟进记录与关联合同楼宇不一致")
    return building_id


def selected_customer_follow_ups(
    customer: Customer,
    scope: SelectedPropertyScope | None,
) -> list[CustomerFollowUp]:
    rows = list(getattr(customer, "follow_ups", []) or [])
    if scope is None:
        return rows
    allowed_ids = set(_scope_ids(scope))
    scoped = [item for item in rows if item.building_id in allowed_ids]
    for item in scoped:
        assert_customer_follow_up_scope(item, scope)
    return scoped


def _selected_log_scope(scope: SelectedPropertyScope | None, *, building_id: int | None = None) -> ObjectScope | None:
    if scope is None:
        return None
    if building_id is not None:
        assert_selected_object_building(scope, building_id)
        return ObjectScope(scope_type="building", building_id=int(building_id))
    if scope.scope_type == "building":
        return ObjectScope(scope_type="building", building_id=require_single_selected_building(scope))
    if scope.scope_type == "portfolio" and scope.portfolio_id is not None:
        return ObjectScope(scope_type="portfolio", portfolio_id=int(scope.portfolio_id))
    raise HTTPException(409, "当前客户操作范围不完整")


def active_customer_profiles(
    db: Session,
    *,
    customer_ids: Sequence[int],
    building_ids: Sequence[int],
) -> dict[int, list[CustomerBuildingProfile]]:
    normalized_customer_ids = tuple(dict.fromkeys(int(item) for item in customer_ids))
    normalized_building_ids = tuple(dict.fromkeys(int(item) for item in building_ids))
    if not normalized_customer_ids or not normalized_building_ids:
        return {}
    today = today_in_timezone()
    rows = (
        db.query(CustomerBuildingProfile)
        .filter(
            CustomerBuildingProfile.customer_id.in_(normalized_customer_ids),
            CustomerBuildingProfile.building_id.in_(normalized_building_ids),
            CustomerBuildingProfile.valid_from <= today,
            or_(
                CustomerBuildingProfile.valid_to.is_(None),
                CustomerBuildingProfile.valid_to >= today,
            ),
        )
        .order_by(
            CustomerBuildingProfile.customer_id,
            CustomerBuildingProfile.building_id,
            CustomerBuildingProfile.valid_from.desc(),
            CustomerBuildingProfile.id.desc(),
        )
        .all()
    )
    by_pair: dict[tuple[int, int], CustomerBuildingProfile] = {}
    by_customer: dict[int, list[CustomerBuildingProfile]] = {}
    for row in rows:
        pair = (int(row.customer_id), int(row.building_id))
        if pair in by_pair:
            raise HTTPException(409, "客户楼宇资料存在重叠有效版本，请先完成数据复核")
        by_pair[pair] = row
        by_customer.setdefault(int(row.customer_id), []).append(row)
    return by_customer


def _profile_payload(
    profile: CustomerBuildingProfile | None,
    *,
    building_id: int,
    building_code: str,
) -> dict:
    return {
        "building_code": building_code,
        "local_contact": profile.local_contact or "" if profile is not None else "",
        "local_notes": profile.local_notes or "" if profile is not None else "",
        "owner": profile.owner or "" if profile is not None else "",
        "valid_from": profile.valid_from if profile is not None else None,
        "valid_to": profile.valid_to if profile is not None else None,
        "has_profile": profile is not None,
        "_building_id": int(building_id),
    }


def customer_master_log_scope(
    db: Session,
    *,
    customer_ids: Sequence[int] = (),
    selected_building_ids: Sequence[int] = (),
) -> ObjectScope:
    """Shared master changes are portfolio-scoped only when every affected building agrees."""
    building_ids = {int(item) for item in selected_building_ids}
    normalized_customer_ids = tuple(dict.fromkeys(int(item) for item in customer_ids))
    today = today_in_timezone()
    if normalized_customer_ids:
        building_ids.update(
            int(value)
            for (value,) in db.query(CustomerBuildingProfile.building_id)
            .filter(
                CustomerBuildingProfile.customer_id.in_(normalized_customer_ids),
                CustomerBuildingProfile.valid_from <= today,
                or_(
                    CustomerBuildingProfile.valid_to.is_(None),
                    CustomerBuildingProfile.valid_to >= today,
                ),
            )
            .distinct()
            .all()
        )
        building_ids.update(
            int(value)
            for (value,) in db.query(Contract.building_id)
            .filter(
                Contract.customer_id.in_(normalized_customer_ids),
                Contract.is_deleted.is_(False),
            )
            .distinct()
            .all()
            if value is not None
        )
        building_ids.update(
            int(value)
            for (value,) in db.query(CustomerFollowUp.building_id)
            .filter(CustomerFollowUp.customer_id.in_(normalized_customer_ids))
            .distinct()
            .all()
            if value is not None
        )
    if not building_ids:
        return ObjectScope(scope_type="global")

    rows = (
        db.query(PortfolioBuilding.building_id, PortfolioBuilding.portfolio_id)
        .filter(
            PortfolioBuilding.building_id.in_(tuple(sorted(building_ids))),
            PortfolioBuilding.valid_from <= today,
            or_(PortfolioBuilding.valid_to.is_(None), PortfolioBuilding.valid_to >= today),
        )
        .all()
    )
    portfolios_by_building: dict[int, set[int]] = {building_id: set() for building_id in building_ids}
    for building_id, portfolio_id in rows:
        portfolios_by_building[int(building_id)].add(int(portfolio_id))
    if any(len(portfolio_ids) != 1 for portfolio_ids in portfolios_by_building.values()):
        return ObjectScope(scope_type="global")
    portfolio_ids = {next(iter(values)) for values in portfolios_by_building.values()}
    if len(portfolio_ids) != 1:
        return ObjectScope(scope_type="global")
    return ObjectScope(scope_type="portfolio", portfolio_id=next(iter(portfolio_ids)))


def ensure_customer_building_profile(
    db: Session,
    customer_id: int,
    building_id: int,
) -> CustomerBuildingProfile:
    today = today_in_timezone()
    profiles = (
        db.query(CustomerBuildingProfile)
        .filter(
            CustomerBuildingProfile.customer_id == int(customer_id),
            CustomerBuildingProfile.building_id == int(building_id),
            CustomerBuildingProfile.valid_from <= today,
            or_(CustomerBuildingProfile.valid_to.is_(None), CustomerBuildingProfile.valid_to >= today),
        )
        .order_by(CustomerBuildingProfile.valid_from.desc(), CustomerBuildingProfile.id.desc())
        .all()
    )
    if len(profiles) > 1:
        raise HTTPException(409, "客户楼宇资料存在重叠有效版本，请先完成数据复核")
    if profiles:
        return profiles[0]
    profile = CustomerBuildingProfile(
        customer_id=int(customer_id),
        building_id=int(building_id),
        local_contact="",
        local_notes="",
        owner="",
        valid_from=today,
    )
    db.add(profile)
    return profile


def serialize_customer(
    customer: Customer,
    *,
    include_contracts: bool = False,
    db: Session | None = None,
    scope: SelectedPropertyScope | None = None,
    profiles: Sequence[CustomerBuildingProfile] | None = None,
) -> dict:
    contracts = selected_customer_contracts(customer, scope)
    profile_rows = list(profiles or [])
    if profiles is None and db is not None and scope is not None:
        profile_rows = active_customer_profiles(
            db,
            customer_ids=(int(customer.id),),
            building_ids=scope.building_ids,
        ).get(int(customer.id), [])
    profiles_by_building = {int(item.building_id): item for item in profile_rows}
    building_codes = {
        int(building_id): str(building_code)
        for building_id, building_code in zip(
            scope.building_ids if scope is not None else (),
            scope.building_codes if scope is not None else (),
        )
    }
    profile_payloads = [
        _profile_payload(
            profiles_by_building.get(building_id),
            building_id=building_id,
            building_code=building_code,
        )
        for building_id, building_code in building_codes.items()
        if building_id in profiles_by_building
    ]
    selected_profile = None
    if scope is not None and scope.scope_type == "building" and len(building_codes) == 1:
        building_id, building_code = next(iter(building_codes.items()))
        selected_profile = _profile_payload(
            profiles_by_building.get(building_id),
            building_id=building_id,
            building_code=building_code,
        )
    public_profile_payloads = [
        {key: value for key, value in item.items() if key != "_building_id"}
        for item in profile_payloads
    ]
    public_selected_profile = (
        {key: value for key, value in selected_profile.items() if key != "_building_id"}
        if selected_profile is not None
        else None
    )
    data = {
        "id": customer.id,
        "name": customer.name or "",
        "short_name": customer.short_name or "",
        "credit_code": customer.credit_code or "",
        "customer_type": customer.customer_type or "",
        "industry": customer.industry or "",
        "contact_name": customer.contact_name or "",
        "contact_phone": customer.contact_phone or "",
        "contact_email": customer.contact_email or "",
        "address": customer.address or "",
        "notes": customer.notes or "",
        "status": customer.status or "active",
        "contract_count": len(contracts),
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
        "local_contact": public_selected_profile["local_contact"] if public_selected_profile else "",
        "local_notes": public_selected_profile["local_notes"] if public_selected_profile else "",
        "owner": public_selected_profile["owner"] if public_selected_profile else "",
        "building_profile": public_selected_profile,
        "building_profiles": public_profile_payloads,
        "shared_master": {
            "name": customer.name or "",
            "short_name": customer.short_name or "",
            "credit_code": customer.credit_code or "",
            "customer_type": customer.customer_type or "",
            "industry": customer.industry or "",
            "contact_name": customer.contact_name or "",
            "contact_phone": customer.contact_phone or "",
            "contact_email": customer.contact_email or "",
            "address": customer.address or "",
            "notes": customer.notes or "",
            "status": customer.status or "active",
        },
    }
    if include_contracts:
        follow_ups = selected_customer_follow_ups(customer, scope)
        data["contracts"] = [serialize_customer_contract(contract) for contract in sorted(contracts, key=lambda item: item.contract_no or "")]
        data["follow_ups"] = [
            serialize_customer_follow_up(item)
            for item in sorted(
                follow_ups,
                key=lambda item: (item.occurred_at or datetime.min, item.id or 0),
                reverse=True,
            )
        ]
        data["operating_summary"] = (
            build_customer_operating_summary(db, customer, contracts, follow_ups=follow_ups)
            if db
            else {}
        )
        if db and scope is not None:
            # Runtime import avoids the attachments -> common -> customers module cycle.
            from app.services.attachments import list_attachments

            attachments = list_attachments(db, "customer", customer.id, scope=scope)
        else:
            attachments = list_entity_attachments(db, "customer", customer.id) if db else []
        data["attachments"] = attachments
        data["attachment_summary"] = attachment_summary(attachments, entity_type="customer")
        if db:
            from app.services.risks import list_risks

            data["risks"] = list_risks(
                db,
                customer_id=customer.id,
                limit=20,
                contracts=contracts,
                building_ids=scope.building_ids if scope is not None else None,
            )["items"]
    return data


def serialize_customer_contract(contract: Contract) -> dict:
    summary = contract_room_summary(contract)
    deposits = list(getattr(contract, "deposits", []) or [])
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "tenant_name": customer_display_name(contract),
        "legacy_tenant_name": contract.tenant_name or "",
        "room_no": summary["room_no"],
        "tower": summary["tower"],
        "area": summary["area"],
        "monthly_rent": summary["monthly_rent"],
        "lease_type": normalize_signing_type(contract.lease_type),
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "days_to_expiry": contract.days_to_expiry,
        "status": contract.status,
        "disposal": contract.disposal or "",
        "deposit_summary": deposit_summary(deposits),
    }


def list_customers(
    db: Session,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    query = db.query(Customer).options(selectinload(Customer.contracts))
    if scope is not None:
        query = query.filter(selected_customer_predicate(scope))
    if keyword:
        like = f"%{keyword.strip()}%"
        profile_match = False
        if scope is not None:
            today = today_in_timezone()
            profile_match = exists().where(
                CustomerBuildingProfile.customer_id == Customer.id,
                CustomerBuildingProfile.building_id.in_(_scope_ids(scope) or (-1,)),
                CustomerBuildingProfile.valid_from <= today,
                or_(
                    CustomerBuildingProfile.valid_to.is_(None),
                    CustomerBuildingProfile.valid_to >= today,
                ),
                or_(
                    CustomerBuildingProfile.local_contact.like(like),
                    CustomerBuildingProfile.local_notes.like(like),
                    CustomerBuildingProfile.owner.like(like),
                ),
            )
        query = query.filter(
            or_(
                Customer.name.like(like),
                Customer.short_name.like(like),
                Customer.credit_code.like(like),
                profile_match,
            )
        )
    if status:
        query = query.filter(Customer.status == status)
    query = query.order_by(Customer.status.asc(), Customer.name.asc(), Customer.id.asc())
    total = query.count()
    items = query.all() if page_size == 0 else query.offset((page - 1) * page_size).limit(page_size).all()
    profiles_by_customer = (
        active_customer_profiles(
            db,
            customer_ids=[int(item.id) for item in items],
            building_ids=scope.building_ids,
        )
        if scope is not None
        else {}
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            serialize_customer(
                item,
                scope=scope,
                profiles=profiles_by_customer.get(int(item.id), []),
            )
            for item in items
        ],
    }


def get_customer(
    db: Session,
    customer_id: int,
    *,
    scope: SelectedPropertyScope | None = None,
) -> Customer:
    query = (
        db.query(Customer)
        .options(
            selectinload(Customer.contracts).selectinload(Contract.room_items),
            selectinload(Customer.contracts).selectinload(Contract.rent_terms),
            selectinload(Customer.contracts).selectinload(Contract.deposits),
            selectinload(Customer.follow_ups).selectinload(CustomerFollowUp.contract),
        )
        .filter(Customer.id == customer_id)
    )
    if scope is not None:
        query = query.filter(selected_customer_predicate(scope))
    customer = query.first()
    if not customer:
        raise HTTPException(404, "客户不存在或无权访问")
    return customer


def create_customer(
    db: Session,
    payload: CustomerCreate,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    selected_building_id = require_single_selected_building(scope) if scope is not None else None
    data = _customer_payload_data(payload, partial=False)
    master_data, profile_data = _split_customer_payload(data)
    name = _clean_text(master_data.get("name"))
    if not name:
        raise HTTPException(400, "客户名称不能为空")
    master_data["name"] = name
    master_data["status"] = master_data.get("status") or "active"
    if master_data["status"] not in CUSTOMER_STATUSES:
        raise HTTPException(400, "客户状态无效")
    _ensure_unique_name(db, name)
    customer = Customer(**master_data)
    db.add(customer)
    db.flush()
    profile = None
    if selected_building_id is not None:
        profile = ensure_customer_building_profile(db, customer.id, selected_building_id)
        for key, value in profile_data.items():
            setattr(profile, key, value)
    log_action(
        db,
        None,
        "customer_create",
        dump_changes({"customer_id": customer.id, "name": customer.name}),
        operator,
        commit=False,
        target_type="customer",
        target_id=customer.id,
        object_scope=customer_master_log_scope(
            db,
            customer_ids=(int(customer.id),),
            selected_building_ids=(selected_building_id,) if selected_building_id is not None else (),
        ),
    )
    if profile is not None:
        log_action(
            db,
            None,
            "customer_profile_create",
            dump_changes(
                {
                    "customer_id": customer.id,
                    "changes": {
                        key: {"old": "", "new": str(getattr(profile, key) or "")}
                        for key in CUSTOMER_PROFILE_FIELDS
                        if getattr(profile, key) or ""
                    },
                }
            ),
            operator,
            commit=False,
            target_type="customer",
            target_id=customer.id,
            object_scope=ObjectScope(scope_type="building", building_id=selected_building_id),
        )
    db.commit()
    db.refresh(customer)
    return serialize_customer(customer, db=db, scope=scope)


def update_customer(
    db: Session,
    customer: Customer,
    payload: CustomerUpdate,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
    can_manage_master: bool = False,
) -> dict:
    data = _customer_payload_data(payload, partial=True)
    master_data, profile_data = _split_customer_payload(data)
    if "name" in master_data:
        name = _clean_text(master_data.get("name"))
        if not name:
            raise HTTPException(400, "客户名称不能为空")
        _ensure_unique_name(db, name, customer.id)
        master_data["name"] = name

    master_changes = {}
    for key, value in master_data.items():
        old_value = getattr(customer, key)
        if str(old_value or "") != str(value or ""):
            master_changes[key] = {"old": str(old_value or ""), "new": str(value or "")}

    if master_changes and not can_manage_master:
        raise HTTPException(403, "修改共享客户主档需要客户主档管理权限")
    for key, value in master_data.items():
        if key in master_changes:
            setattr(customer, key, value)

    profile = None
    profile_changes = {}
    profile_is_new = False
    selected_building_id = None
    if profile_data:
        if scope is None:
            raise HTTPException(422, "楼宇客户资料必须明确选择一栋楼宇")
        selected_building_id = require_single_selected_building(scope)
        profile = ensure_customer_building_profile(db, customer.id, selected_building_id)
        profile_is_new = profile.id is None
        for key, value in profile_data.items():
            old_value = getattr(profile, key)
            if str(old_value or "") != str(value or ""):
                profile_changes[key] = {
                    "old": str(old_value or ""),
                    "new": str(value or ""),
                }
                setattr(profile, key, value)
        if profile_is_new and not profile_changes:
            profile_changes["profile"] = {"old": "absent", "new": "created"}

    # A building-local edit must not make the shared master look modified to
    # users of another building.  The profile carries its own updated_at.
    if master_changes:
        customer.updated_at = now_in_timezone()
    if master_changes:
        log_action(
            db,
            None,
            "customer_update",
            dump_changes({"customer_id": customer.id, "changes": master_changes}),
            operator,
            commit=False,
            target_type="customer",
            target_id=customer.id,
            object_scope=customer_master_log_scope(
                db,
                customer_ids=(int(customer.id),),
                selected_building_ids=scope.building_ids if scope is not None else (),
            ),
        )
    if profile_changes and selected_building_id is not None:
        profile.updated_at = now_in_timezone()
        log_action(
            db,
            None,
            "customer_profile_create" if profile_is_new else "customer_profile_update",
            dump_changes({"customer_id": customer.id, "changes": profile_changes}),
            operator,
            commit=False,
            target_type="customer",
            target_id=customer.id,
            object_scope=ObjectScope(scope_type="building", building_id=selected_building_id),
        )
    if master_changes or profile_changes:
        db.commit()
        db.refresh(customer)
    return serialize_customer(customer, db=db, scope=scope)


def _validate_follow_up_contract(
    db: Session,
    customer: Customer,
    contract_id: int | None,
    *,
    scope: SelectedPropertyScope | None = None,
) -> Contract | None:
    if not contract_id:
        return None
    contract = db.query(Contract).filter(Contract.id == int(contract_id), Contract.is_deleted.is_(False)).first()
    if not contract:
        raise HTTPException(404, "关联合同不存在")
    if scope is not None:
        assert_selected_object_building(scope, contract.building_id)
    if contract.customer_id != customer.id:
        raise HTTPException(400, "关联合同不属于当前客户")
    return contract


def get_customer_follow_up(
    db: Session,
    customer: Customer,
    follow_up_id: int,
    *,
    scope: SelectedPropertyScope | None = None,
) -> CustomerFollowUp:
    follow_up = (
        db.query(CustomerFollowUp)
        .filter(CustomerFollowUp.id == follow_up_id, CustomerFollowUp.customer_id == customer.id)
        .first()
    )
    if not follow_up:
        raise HTTPException(404, "跟进记录不存在")
    if scope is not None:
        assert_customer_follow_up_scope(follow_up, scope)
    return follow_up


def create_customer_follow_up(
    db: Session,
    customer: Customer,
    payload: CustomerFollowUpCreate,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    data = _follow_up_payload_data(payload, partial=False)
    contract = _validate_follow_up_contract(db, customer, data.get("contract_id"), scope=scope)
    data["customer_id"] = customer.id
    data["follow_up_type"] = data.get("follow_up_type") or "电话"
    data["occurred_at"] = data.get("occurred_at") or now_in_timezone()
    data["status"] = data.get("status") or "open"
    if scope is None:
        data = prepare_legacy_follow_up_write(db, data)
    else:
        building_id = (
            assert_selected_object_building(scope, contract.building_id)
            if contract is not None
            else require_single_selected_building(scope)
        )
        data["building_id"] = building_id
        if contract is None:
            ensure_customer_building_profile(db, customer.id, building_id)
    follow_up = CustomerFollowUp(**data)
    db.add(follow_up)
    db.flush()
    log_action(
        db,
        data.get("contract_id"),
        "customer_followup_create",
        dump_changes({"customer_id": customer.id, "follow_up_id": follow_up.id, "type": follow_up.follow_up_type}),
        operator,
        commit=False,
        target_type="follow_up",
        target_id=follow_up.id,
        object_scope=_selected_log_scope(scope, building_id=follow_up.building_id),
    )
    db.commit()
    db.refresh(follow_up)
    return serialize_customer_follow_up(follow_up)


def update_customer_follow_up(
    db: Session,
    customer: Customer,
    follow_up: CustomerFollowUp,
    payload: CustomerFollowUpUpdate,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    data = _follow_up_payload_data(payload, partial=True)
    if scope is None:
        if "contract_id" in data:
            _validate_follow_up_contract(db, customer, data.get("contract_id"))
        data = prepare_legacy_follow_up_write(db, data, existing=follow_up)
    else:
        assert_customer_follow_up_scope(follow_up, scope)
        resulting_contract_id = data.get("contract_id", follow_up.contract_id)
        contract = _validate_follow_up_contract(
            db,
            customer,
            resulting_contract_id,
            scope=scope,
        )
        building_id = (
            assert_selected_object_building(scope, contract.building_id)
            if contract is not None
            else require_single_selected_building(scope)
        )
        data["building_id"] = building_id
        if contract is None:
            ensure_customer_building_profile(db, customer.id, building_id)

    changes = {}
    for key, value in data.items():
        old_value = getattr(follow_up, key)
        if str(old_value or "") != str(value or ""):
            changes[key] = {"old": str(old_value or ""), "new": str(value or "")}
            setattr(follow_up, key, value)

    if changes:
        follow_up.updated_at = now_in_timezone()
        log_action(
            db,
            follow_up.contract_id,
            "customer_followup_update",
            dump_changes({"customer_id": customer.id, "follow_up_id": follow_up.id, "changes": changes}),
            operator,
            commit=False,
            target_type="follow_up",
            target_id=follow_up.id,
            object_scope=_selected_log_scope(scope, building_id=follow_up.building_id),
        )
        db.commit()
        db.refresh(follow_up)
    return serialize_customer_follow_up(follow_up)


def complete_customer_follow_up(
    db: Session,
    customer: Customer,
    follow_up: CustomerFollowUp,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    if scope is not None:
        assert_customer_follow_up_scope(follow_up, scope)
    if (follow_up.status or "open") != "done":
        if scope is None:
            scoped = prepare_legacy_follow_up_write(db, {}, existing=follow_up)
            follow_up.building_id = scoped["building_id"]
        follow_up.status = "done"
        follow_up.updated_at = now_in_timezone()
        log_action(
            db,
            follow_up.contract_id,
            "customer_followup_done",
            dump_changes({"customer_id": customer.id, "follow_up_id": follow_up.id}),
            operator,
            commit=False,
            target_type="follow_up",
            target_id=follow_up.id,
            object_scope=_selected_log_scope(scope, building_id=follow_up.building_id),
        )
        db.commit()
        db.refresh(follow_up)
    return serialize_customer_follow_up(follow_up)


def delete_customer_follow_up(
    db: Session,
    customer: Customer,
    follow_up: CustomerFollowUp,
    operator: str,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    follow_up_id = follow_up.id
    contract_id = follow_up.contract_id
    if scope is None:
        scoped = prepare_legacy_follow_up_write(db, {}, existing=follow_up)
        follow_up.building_id = scoped["building_id"]
    else:
        assert_customer_follow_up_scope(follow_up, scope)
    log_action(
        db,
        contract_id,
        "customer_followup_delete",
        dump_changes({"customer_id": customer.id, "follow_up_id": follow_up_id}),
        operator,
        commit=False,
        target_type="follow_up",
        target_id=follow_up_id,
        object_scope=_selected_log_scope(scope, building_id=follow_up.building_id),
    )
    db.delete(follow_up)
    db.commit()
    return {"ok": True, "id": follow_up_id}


def list_due_follow_up_customers(
    db: Session,
    limit: int = 20,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict:
    today = today_in_timezone()
    query = (
        db.query(CustomerFollowUp)
        .filter(
            CustomerFollowUp.next_follow_up_date.isnot(None),
            CustomerFollowUp.next_follow_up_date <= today,
            or_(CustomerFollowUp.status.is_(None), CustomerFollowUp.status != "done"),
        )
        .order_by(CustomerFollowUp.next_follow_up_date.asc(), CustomerFollowUp.id.asc())
    )
    if scope is not None:
        query = query.filter(CustomerFollowUp.building_id.in_(_scope_ids(scope) or (-1,)))
    rows = query.all()
    grouped = {}
    for row in rows:
        if scope is not None:
            assert_customer_follow_up_scope(row, scope)
        customer = getattr(row, "customer", None)
        if not customer:
            continue
        entry = grouped.setdefault(
            row.customer_id,
            {
                "customer_id": row.customer_id,
                "customer_name": customer.name or "",
                "contact_name": customer.contact_name or "",
                "contact_phone": customer.contact_phone or "",
                "next_follow_up_date": row.next_follow_up_date,
                "next_action": row.next_action or "",
                "content": row.content or "",
                "owner": row.owner or "",
                "follow_up_id": row.id,
                "open_follow_up_count": 0,
                "overdue_days": max((today - row.next_follow_up_date).days, 0) if row.next_follow_up_date else 0,
            },
        )
        entry["open_follow_up_count"] += 1
    items = list(grouped.values())[:limit]
    return {"date": today, "total": len(grouped), "items": items}
