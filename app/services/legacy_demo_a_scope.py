from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.time import today_in_timezone
from app.models import (
    Attachment,
    Building,
    Contract,
    ContractDeposit,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    Portfolio,
    PortfolioBuilding,
    Room,
    User,
    UserScopeGrant,
    Zone,
)
from app.services.towers import normalize_tower


LEGACY_BUILDING_CODE = "DEMO_A"
TOWER_ZONE_CODES = {
    "南塔": "SOUTH_OFFICE",
    "北塔": "NORTH_OFFICE",
    "商业配套": "COMMERCIAL_SUPPORT",
}
LEGACY_CONTRACT_TOWER_ALIASES = {
    "南塔办公": "南塔",
    "北塔办公": "北塔",
    "S": "南塔",
    "N": "北塔",
}
GLOBAL_LOG_TARGET_TYPES = {"audit", "backup", "frontend", "system_config"}
BUILDING_COLLECTION_LOG_TARGET_TYPES = {
    "contract",
    "room",
    "receivable",
    "deposit",
    "follow_up",
    "report",
    "rent_analytics",
    "risk",
    "statement",
}


@dataclass(frozen=True)
class LegacyDemoABaseScope:
    portfolio_id: int
    building_id: int


@dataclass(frozen=True)
class LegacyDemoAScope:
    portfolio_id: int
    building_id: int
    zone_ids_by_tower: Mapping[str, int]


@dataclass(frozen=True)
class ObjectScope:
    scope_type: str | None
    portfolio_id: int | None = None
    building_id: int | None = None

    def as_columns(self) -> dict[str, int | str | None]:
        return {
            "scope_type": self.scope_type,
            "portfolio_id": self.portfolio_id,
            "building_id": self.building_id,
        }


def _contract_zone_tower(value: object) -> str:
    normalized = normalize_tower(value)
    return LEGACY_CONTRACT_TOWER_ALIASES.get(normalized, normalized)


def _new_contract_room_zone_tower(tower: object, room_no: str) -> str:
    zone_tower = _contract_zone_tower(tower)
    if zone_tower in TOWER_ZONE_CODES:
        return zone_tower
    room_prefix = str(room_no or "").strip().upper()[:1]
    inferred = {"S": "南塔", "N": "北塔", "P": "商业配套"}.get(room_prefix)
    if inferred:
        return inferred
    raise HTTPException(400, f"房源 {room_no} 无法确定 DEMO_A 分区，请先补充南塔、北塔或商业配套")


def require_legacy_demo_a_base_scope(db: Session) -> LegacyDemoABaseScope:
    """Resolve the one effective DEMO_A building/portfolio pair without requiring room zones."""
    today = today_in_timezone()
    building = (
        db.query(Building)
        .filter(Building.code == LEGACY_BUILDING_CODE, Building.is_active.is_(True))
        .one_or_none()
    )
    if building is None:
        raise HTTPException(503, "DEMO_A 楼宇范围尚未初始化，当前写入已停止")

    memberships = (
        db.query(PortfolioBuilding, Portfolio)
        .join(Portfolio, Portfolio.id == PortfolioBuilding.portfolio_id)
        .filter(
            PortfolioBuilding.building_id == building.id,
            PortfolioBuilding.valid_from <= today,
            or_(PortfolioBuilding.valid_to.is_(None), PortfolioBuilding.valid_to >= today),
            Portfolio.is_active.is_(True),
        )
        .all()
    )
    if not memberships:
        raise HTTPException(503, "DEMO_A 板块关系尚未初始化，当前写入已停止")
    if len(memberships) != 1:
        raise HTTPException(503, "DEMO_A 当前存在多条有效板块关系，当前写入已停止")
    membership, portfolio = memberships[0]
    return LegacyDemoABaseScope(portfolio_id=portfolio.id, building_id=building.id)


def require_legacy_demo_a_scope(db: Session) -> LegacyDemoAScope:
    """Resolve the DEMO_A base scope plus the three effective room zones."""
    base = require_legacy_demo_a_base_scope(db)
    today = today_in_timezone()

    zones = (
        db.query(Zone)
        .filter(
            Zone.building_id == base.building_id,
            Zone.code.in_(tuple(TOWER_ZONE_CODES.values())),
            Zone.is_active.is_(True),
            Zone.valid_from <= today,
            or_(Zone.valid_to.is_(None), Zone.valid_to >= today),
        )
        .all()
    )
    zone_ids_by_code = {zone.code: zone.id for zone in zones}
    missing_codes = set(TOWER_ZONE_CODES.values()) - zone_ids_by_code.keys()
    if missing_codes:
        raise HTTPException(503, "DEMO_A 分区尚未完整初始化，当前写入已停止")

    return LegacyDemoAScope(
        portfolio_id=base.portfolio_id,
        building_id=base.building_id,
        zone_ids_by_tower={
            tower: zone_ids_by_code[zone_code]
            for tower, zone_code in TOWER_ZONE_CODES.items()
        },
    )


def grant_legacy_demo_a_access(
    db: Session,
    user: User,
    *,
    granted_by: int | None = None,
) -> UserScopeGrant:
    """Give a newly created legacy user the same DEMO_A access their role had before scopes existed."""
    if user.id is None:
        raise RuntimeError("创建楼宇权限前必须先保存用户")

    scope = require_legacy_demo_a_base_scope(db)
    today = today_in_timezone()
    existing = (
        db.query(UserScopeGrant)
        .filter(
            UserScopeGrant.user_id == user.id,
            UserScopeGrant.scope_type == "building",
            UserScopeGrant.building_id == scope.building_id,
            UserScopeGrant.valid_from <= today,
            or_(UserScopeGrant.valid_to.is_(None), UserScopeGrant.valid_to >= today),
        )
        .order_by(UserScopeGrant.valid_from.desc(), UserScopeGrant.id.desc())
        .first()
    )
    access_level = "edit" if user.role in {"admin", "editor"} else "view"
    if existing is not None:
        existing.access_level = access_level
        return existing

    grant = UserScopeGrant(
        user_id=user.id,
        scope_type="building",
        building_id=scope.building_id,
        access_level=access_level,
        include_future_buildings=False,
        valid_from=today,
        granted_by=granted_by,
    )
    db.add(grant)
    return grant


def prepare_legacy_room_write(
    db: Session,
    data: dict,
    *,
    existing: Room | None = None,
) -> dict:
    """Return a DEMO_A-scoped room payload without mutating the caller or ORM row."""
    scope = require_legacy_demo_a_scope(db)
    prepared = dict(data)
    tower = normalize_tower(prepared.get("tower", existing.tower if existing else ""))
    if tower not in scope.zone_ids_by_tower:
        raise HTTPException(400, "楼塔必须是南塔、北塔或商业配套")

    requested_building_id = prepared.get(
        "building_id",
        existing.building_id if existing is not None else None,
    )
    if requested_building_id not in (None, scope.building_id):
        raise HTTPException(400, "旧版写入入口仅允许处理 DEMO_A 数据")

    expected_zone_id = scope.zone_ids_by_tower[tower]
    requested_zone_id = prepared.get("zone_id")
    if requested_zone_id not in (None, expected_zone_id):
        raise HTTPException(400, "房源分区与楼塔不一致")

    prepared["tower"] = tower
    prepared["building_id"] = scope.building_id
    prepared["zone_id"] = expected_zone_id
    return prepared


def prepare_legacy_contract_write(
    db: Session,
    data: dict,
    room_items: list[dict],
    *,
    existing: Contract | None = None,
) -> tuple[dict, list[dict]]:
    """Resolve every legacy contract room to one DEMO_A room before any DML occurs."""
    scope = require_legacy_demo_a_base_scope(db)
    prepared_data = dict(data)
    requested_building_id = prepared_data.get(
        "building_id",
        existing.building_id if existing is not None else None,
    )
    if requested_building_id not in (None, scope.building_id):
        raise HTTPException(400, "旧版写入入口仅允许处理 DEMO_A 合同")

    resolved: list[tuple[dict, Room | None, dict | None]] = []
    seen_room_ids: set[int] = set()
    seen_room_numbers: set[str] = set()
    for raw_item in room_items:
        item = dict(raw_item)
        requested_item_building_id = item.get("building_id")
        if requested_item_building_id not in (None, scope.building_id):
            raise HTTPException(400, "合同房源关系不属于 DEMO_A")
        room_no = str(item.get("room_no") or "").strip()
        if not room_no:
            raise HTTPException(400, "合同房源室号不能为空")
        if room_no in seen_room_numbers:
            raise HTTPException(400, f"房源明细室号 {room_no} 重复")
        seen_room_numbers.add(room_no)
        requested_room_id = item.get("room_id")
        room = db.get(Room, int(requested_room_id)) if requested_room_id is not None else None
        if room is not None and room.building_id != scope.building_id:
            raise HTTPException(400, f"房源 {room_no} 不属于 DEMO_A")
        if room is not None and room.room_no != room_no:
            raise HTTPException(400, f"房源 {room_no} 与所选房源标识不一致")
        if room is None:
            room = (
                db.query(Room)
                .filter(
                    Room.building_id == scope.building_id,
                    Room.room_no == room_no,
                    Room.is_deleted.is_(False),
                )
                .one_or_none()
            )
        if room is None:
            if requested_room_id is not None:
                raise HTTPException(400, f"房源 {room_no} 与所选房源标识不一致")
            tower = normalize_tower(item.get("tower"))
            if not tower:
                raise HTTPException(400, f"房源 {room_no} 的楼塔无效")
            zone_tower = _new_contract_room_zone_tower(tower, room_no)
            room_scope = require_legacy_demo_a_scope(db)
            room = Room(
                building_id=scope.building_id,
                zone_id=room_scope.zone_ids_by_tower[zone_tower],
                tower=zone_tower,
                floor="",
                room_no=room_no,
                room_type="商业" if zone_tower == "商业配套" else "办公",
                area=float(item.get("area") or 0),
                is_leasable=True,
                notes="由合同旧版入口自动补入房源台账，楼层和类型待复核",
            )
            db.add(room)
            db.flush()
        if room.id in seen_room_ids:
            raise HTTPException(400, f"房源明细室号 {room_no} 重复")
        seen_room_ids.add(room.id)
        if room.is_deleted and item.get("id") is None:
            raise HTTPException(400, f"房源 {room_no} 已在回收站，不能新增合同关系")

        scoped_room = prepare_legacy_room_write(db, {}, existing=room)
        if scoped_room["building_id"] != scope.building_id:
            raise HTTPException(400, f"房源 {room_no} 不属于 DEMO_A")

        if requested_room_id not in (None, room.id):
            raise HTTPException(400, f"房源 {room_no} 与所选房源标识不一致")
        requested_tower = normalize_tower(item.get("tower"))
        if requested_tower and _contract_zone_tower(requested_tower) != scoped_room["tower"]:
            raise HTTPException(400, f"房源 {room_no} 的楼塔与房源台账不一致")

        item.update(
            {
                "building_id": scope.building_id,
                "room_id": room.id,
                "tower": scoped_room["tower"],
                "room_no": room.room_no,
            }
        )
        resolved.append((item, room, scoped_room))

    # Validation above is deliberately complete before adopting any still-unscoped
    # legacy room, so a later bad item cannot leave a partial in-session backfill.
    for _item, room, scoped_room in resolved:
        if room is None or scoped_room is None:
            continue
        room.building_id = int(scoped_room["building_id"])
        room.zone_id = int(scoped_room["zone_id"])
        room.tower = str(scoped_room["tower"])

    prepared_data["building_id"] = scope.building_id
    return prepared_data, [item for item, _room, _scope in resolved]


def prepare_legacy_contract_record(db: Session, contract: Contract) -> None:
    """Solidify one existing contract and all of its room relations before any legacy mutation."""
    raw_items = [
        {
            "id": item.id,
            "building_id": item.building_id,
            "room_id": item.room_id,
            "tower": item.tower,
            "room_no": item.room_no,
            "area": item.area,
            "unit_price": item.unit_price,
            "monthly_rent": item.monthly_rent,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "notes": item.notes,
        }
        for item in contract.room_items or []
        if item.room_no
    ]
    prepared_contract, prepared_items = prepare_legacy_contract_write(
        db,
        {"building_id": contract.building_id},
        raw_items,
        existing=contract,
    )
    contract.building_id = prepared_contract["building_id"]
    stored_by_id = {item.id: item for item in contract.room_items or [] if item.id is not None}
    for prepared in prepared_items:
        stored = stored_by_id.get(prepared.get("id"))
        if stored is None:
            raise HTTPException(409, "合同房源关系尚未持久化，当前写入已停止")
        stored.building_id = prepared["building_id"]
        stored.room_id = prepared["room_id"]
        stored.tower = prepared["tower"]
        stored.room_no = prepared["room_no"]


def prepare_legacy_follow_up_write(
    db: Session,
    data: dict,
    *,
    existing: CustomerFollowUp | None = None,
) -> dict:
    prepared = dict(data)
    scope = require_legacy_demo_a_base_scope(db)
    contract_id = prepared.get(
        "contract_id",
        existing.contract_id if existing is not None else None,
    )
    building_id = scope.building_id
    if contract_id is not None:
        contract = db.get(Contract, int(contract_id))
        if contract is None:
            raise HTTPException(404, "关联合同不存在")
        if contract.building_id not in (None, scope.building_id):
            raise HTTPException(400, "关联合同不属于 DEMO_A")
        building_id = contract.building_id or scope.building_id

    requested_building_id = prepared.get(
        "building_id",
        existing.building_id if existing is not None else None,
    )
    if requested_building_id not in (None, building_id):
        raise HTTPException(400, "跟进记录与关联合同的楼宇不一致")
    prepared["building_id"] = building_id
    return prepared


def _preserved_attachment_scope(
    base: LegacyDemoABaseScope,
    attachment: Attachment,
) -> ObjectScope | None:
    values = (attachment.scope_type, attachment.portfolio_id, attachment.building_id)
    if values == (None, None, None):
        # 旧附件仅添加可空范围字段，必须按类型确认归属。
        # 编辑或删除时原样保留“待分类”，不能借旧版 DEMO_A 页面静默改写归属。
        return ObjectScope(scope_type=None)
    if attachment.scope_type == "global" and attachment.portfolio_id is None and attachment.building_id is None:
        return ObjectScope(scope_type="global")
    if (
        attachment.scope_type == "portfolio"
        and attachment.portfolio_id == base.portfolio_id
        and attachment.building_id is None
    ):
        return ObjectScope(scope_type="portfolio", portfolio_id=base.portfolio_id)
    if (
        attachment.scope_type == "building"
        and attachment.portfolio_id is None
        and attachment.building_id == base.building_id
    ):
        return ObjectScope(scope_type="building", building_id=base.building_id)
    raise HTTPException(400, "附件范围不属于旧版 DEMO_A 入口或范围字段不完整")


def legacy_attachment_scope(
    db: Session,
    entity_type: str,
    entity_id: int,
    *,
    existing: Attachment | None = None,
) -> ObjectScope:
    """Scope new legacy uploads to the DEMO_A page from which they were created."""
    scope = require_legacy_demo_a_base_scope(db)
    if existing is not None:
        preserved = _preserved_attachment_scope(scope, existing)
        if preserved is not None:
            return preserved
    building_id: int | None = None
    if entity_type == "contract":
        contract = db.get(Contract, entity_id)
        building_id = contract.building_id if contract else None
    elif entity_type == "deposit":
        deposit = db.get(ContractDeposit, entity_id)
        contract = db.get(Contract, deposit.contract_id) if deposit else None
        building_id = contract.building_id if contract else None
    elif entity_type == "follow_up":
        follow_up = db.get(CustomerFollowUp, entity_id)
        building_id = follow_up.building_id if follow_up else None
    elif entity_type == "customer":
        # The legacy customer page is currently DEMO_A-only. Stage 2 may explicitly
        # reclassify shared legal-customer files as portfolio scoped.
        building_id = scope.building_id

    if building_id not in (None, scope.building_id):
        raise HTTPException(400, "附件对象不属于 DEMO_A")
    return ObjectScope(scope_type="building", building_id=building_id or scope.building_id)


def legacy_operation_log_scope(
    db: Session,
    *,
    contract_id: int | None,
    target_type: str,
    target_id: int | None,
) -> ObjectScope:
    if target_type in GLOBAL_LOG_TARGET_TYPES:
        return ObjectScope(scope_type="global")

    scope = require_legacy_demo_a_base_scope(db)
    if target_type == "attachment" and target_id is not None:
        attachment = db.get(Attachment, int(target_id))
        if attachment is None:
            raise HTTPException(404, "操作日志附件目标不存在")
        # 附件可能是板块级或仍待分类，必须以附件自身不可变范围为准；
        # contract_id 仅是业务上下文，不能覆盖附件范围。
        return legacy_attachment_scope(
            db,
            attachment.entity_type,
            attachment.entity_id,
            existing=attachment,
        )
    if contract_id is not None:
        contract = db.get(Contract, int(contract_id))
        if contract is None:
            raise HTTPException(404, "操作日志关联合同不存在")
        if contract.building_id is None:
            raise HTTPException(409, "操作日志关联合同尚未固化楼宇范围")
        if contract.building_id != scope.building_id:
            raise HTTPException(400, "操作日志关联合同不属于 DEMO_A")
        return ObjectScope(scope_type="building", building_id=contract.building_id)

    if target_type == "customer":
        if target_id is not None and db.get(Customer, int(target_id)) is None:
            raise HTTPException(404, "操作日志客户目标不存在")
        return ObjectScope(scope_type="portfolio", portfolio_id=scope.portfolio_id)

    if target_type == "contract" and target_id is not None:
        contract = db.get(Contract, int(target_id))
        if contract is None:
            raise HTTPException(404, "操作日志合同目标不存在")
        building_id = contract.building_id
    elif target_type == "room" and target_id is not None:
        room = db.get(Room, int(target_id))
        if room is None:
            raise HTTPException(404, "操作日志房源目标不存在")
        building_id = room.building_id
    elif target_type == "follow_up" and target_id is not None:
        follow_up = db.get(CustomerFollowUp, int(target_id))
        if follow_up is None:
            raise HTTPException(404, "操作日志跟进目标不存在")
        building_id = follow_up.building_id
    elif target_type == "deposit" and target_id is not None:
        deposit = db.get(ContractDeposit, int(target_id))
        if deposit is None:
            raise HTTPException(404, "操作日志押金目标不存在")
        contract = db.get(Contract, deposit.contract_id)
        if contract is None:
            raise HTTPException(404, "操作日志押金关联合同不存在")
        building_id = contract.building_id
    elif target_type == "contract_room" and target_id is not None:
        room_item = db.get(ContractRoom, int(target_id))
        if room_item is None:
            raise HTTPException(404, "操作日志合同房源目标不存在")
        building_id = room_item.building_id
    elif target_type in BUILDING_COLLECTION_LOG_TARGET_TYPES and target_id is None:
        building_id = scope.building_id
    else:
        raise HTTPException(400, "操作日志范围无法从目标对象确定")

    if building_id is None:
        raise HTTPException(409, "操作日志目标尚未固化楼宇范围")
    if building_id != scope.building_id:
        raise HTTPException(400, "操作日志目标不属于 DEMO_A")
    return ObjectScope(scope_type="building", building_id=building_id)
