from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.time import today_in_timezone
from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.building_operations_profile import (
    OPERATIONS_TABS,
    BuildingOperationsProfile,
    DataAdapterType,
    resolve_building_operations_profile,
)
from app.services.legacy_demo_a_scope import (
    LEGACY_BUILDING_CODE,
    prepare_legacy_contract_record,
    prepare_legacy_contract_write,
    prepare_legacy_room_write,
)
from app.services.property_scope import SelectedPropertyScope


def _zone_text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class DetailBuildingScope:
    building_id: int
    building_code: str
    profile: BuildingOperationsProfile


def _attach_active_operations_zones(
    db: Session,
    buildings: tuple[Building, ...] | list[Building],
) -> None:
    building_ids = tuple(int(item.id) for item in buildings)
    if not building_ids:
        return
    today = today_in_timezone()
    zones = (
        db.query(Zone)
        .filter(
            Zone.building_id.in_(building_ids),
            Zone.is_active.is_(True),
            Zone.valid_from <= today,
            or_(Zone.valid_to.is_(None), Zone.valid_to >= today),
        )
        .order_by(Zone.building_id.asc(), Zone.sort_order.asc(), Zone.id.asc())
        .all()
    )
    by_building_id: dict[int, list[tuple[str, str, str]]] = {}
    for zone in zones:
        by_building_id.setdefault(int(zone.building_id), []).append(
            (str(zone.code), str(zone.name), str(zone.zone_type))
        )
    for building in buildings:
        building.operations_zones = tuple(by_building_id.get(int(building.id), ()))


def _selected_pairs(scope: SelectedPropertyScope, *, for_write: bool) -> tuple[tuple[int, str], ...]:
    if (
        scope.scope_type not in {"building", "portfolio"}
        or scope.access_level not in {"view", "edit"}
        or not str(scope.scope_code or "").strip()
        or not scope.building_ids
        or len(scope.building_ids) != len(scope.building_codes)
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    try:
        pairs = tuple(
            (int(building_id), str(building_code or "").strip())
            for building_id, building_code in zip(scope.building_ids, scope.building_codes)
        )
        portfolio_id = int(scope.portfolio_id) if scope.portfolio_id is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "当前楼宇范围无效") from exc

    ids = [building_id for building_id, _code in pairs]
    codes = [code.casefold() for _building_id, code in pairs]
    if (
        any(building_id <= 0 or not code for building_id, code in pairs)
        or len(set(ids)) != len(ids)
        or len(set(codes)) != len(codes)
        or (
            scope.scope_type == "building"
            and (
                len(pairs) != 1
                or portfolio_id is not None
                or str(scope.scope_code).strip().casefold() != pairs[0][1].casefold()
            )
        )
        or (scope.scope_type == "portfolio" and (portfolio_id is None or portfolio_id <= 0))
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    if for_write:
        if scope.access_level != "edit":
            raise HTTPException(403, "当前楼宇仅有查看权限")
        if scope.scope_type != "building" or len(pairs) != 1:
            raise HTTPException(422, "明细写入必须选择单栋楼宇")
    return pairs


def _require_live_detail_profile(building: Building, tab: str) -> BuildingOperationsProfile:
    if tab not in OPERATIONS_TABS:
        raise HTTPException(422, "未知的楼宇运营页面")
    profile = resolve_building_operations_profile(building)
    if profile.data_adapter is not DataAdapterType.LIVE_DETAIL or tab not in profile.available_tabs:
        raise HTTPException(422, f"楼宇 {building.code} 未启用 {tab} 明细能力")
    return profile


def require_detail_building(
    db: Session,
    building_id: int,
    tab: str,
) -> DetailBuildingScope:
    try:
        normalized_id = int(building_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "当前楼宇范围无效") from exc
    building = db.get(Building, normalized_id)
    if building is None or not building.is_active:
        raise HTTPException(404, "楼宇不存在或无权访问")
    _attach_active_operations_zones(db, [building])
    profile = _require_live_detail_profile(building, tab)
    return DetailBuildingScope(
        building_id=normalized_id,
        building_code=str(building.code),
        profile=profile,
    )


def require_detail_page_scope(
    db: Session,
    scope: SelectedPropertyScope,
    tab: str,
    *,
    for_write: bool = False,
) -> tuple[DetailBuildingScope, ...]:
    pairs = _selected_pairs(scope, for_write=for_write)
    buildings = {
        int(item.id): item
        for item in db.query(Building).filter(Building.id.in_(tuple(item[0] for item in pairs))).all()
    }
    _attach_active_operations_zones(db, list(buildings.values()))
    resolved: list[DetailBuildingScope] = []
    for building_id, selected_code in pairs:
        building = buildings.get(building_id)
        if (
            building is None
            or not building.is_active
            or str(building.code).strip().casefold() != selected_code.casefold()
        ):
            raise HTTPException(403, "当前楼宇范围无效")
        profile = _require_live_detail_profile(building, tab)
        resolved.append(
            DetailBuildingScope(
                building_id=building_id,
                building_code=str(building.code),
                profile=profile,
            )
        )
    return tuple(resolved)


def require_detail_write_scope(
    db: Session,
    scope: SelectedPropertyScope,
    tab: str,
) -> DetailBuildingScope:
    return require_detail_page_scope(db, scope, tab, for_write=True)[0]


def _active_profile_zones(
    db: Session,
    detail_scope: DetailBuildingScope,
) -> dict[int, Zone]:
    expected_codes = {item.code.casefold() for item in detail_scope.profile.space_structure.zones}
    today = today_in_timezone()
    zones = (
        db.query(Zone)
        .filter(
            Zone.building_id == detail_scope.building_id,
            Zone.is_active.is_(True),
            Zone.valid_from <= today,
            or_(Zone.valid_to.is_(None), Zone.valid_to >= today),
        )
        .all()
    )
    matched = {
        int(zone.id): zone
        for zone in zones
        if not expected_codes or str(zone.code).casefold() in expected_codes
    }
    if not matched:
        raise HTTPException(503, "楼宇明细分区尚未初始化，当前写入已停止")
    if expected_codes and {str(zone.code).casefold() for zone in matched.values()} != expected_codes:
        raise HTTPException(503, "楼宇明细分区尚未完整初始化，当前写入已停止")
    return matched


def prepare_detail_room_write(
    db: Session,
    data: dict,
    *,
    building_id: int | None = None,
    existing: Room | None = None,
) -> dict:
    if building_id is None:
        return prepare_legacy_room_write(db, data, existing=existing)
    detail_scope = require_detail_building(db, int(building_id), "rooms")
    if detail_scope.building_code.casefold() == LEGACY_BUILDING_CODE.casefold():
        prepared = dict(data)
        prepared["building_id"] = detail_scope.building_id
        return prepare_legacy_room_write(db, prepared, existing=existing)

    if existing is not None and existing.building_id != detail_scope.building_id:
        raise HTTPException(404, "房间不存在或无权访问")
    prepared = dict(data)
    requested_building_id = prepared.get(
        "building_id",
        existing.building_id if existing is not None else None,
    )
    if requested_building_id not in (None, detail_scope.building_id):
        raise HTTPException(400, "房源不属于当前选择楼宇")

    zones_by_id = _active_profile_zones(db, detail_scope)
    requested_zone_id = prepared.get("zone_id", existing.zone_id if existing is not None else None)
    tower = _zone_text(prepared.get("tower", existing.tower if existing is not None else ""))
    if requested_zone_id is not None:
        try:
            zone = zones_by_id.get(int(requested_zone_id))
        except (TypeError, ValueError):
            zone = None
        if zone is None:
            raise HTTPException(400, "房源分区不属于当前选择楼宇")
        accepted_towers = {str(zone.name).casefold(), str(zone.code).casefold()}
        if existing is not None and existing.zone_id == zone.id:
            accepted_towers.add(_zone_text(existing.tower).casefold())
        if tower and tower.casefold() not in accepted_towers:
            raise HTTPException(400, "房源分区与楼宇区域不一致")
    else:
        matches = [
            zone
            for zone in zones_by_id.values()
            if tower.casefold() in {str(zone.name).casefold(), str(zone.code).casefold()}
        ]
        if not tower and len(zones_by_id) == 1:
            matches = list(zones_by_id.values())
        if len(matches) != 1:
            raise HTTPException(400, "房源必须选择当前楼宇已登记的分区")
        zone = matches[0]

    prepared["building_id"] = detail_scope.building_id
    prepared["zone_id"] = int(zone.id)
    prepared["tower"] = (
        _zone_text(existing.tower)
        if existing is not None and existing.zone_id == zone.id and _zone_text(existing.tower)
        else str(zone.name)
    )
    return prepared


def prepare_detail_contract_write(
    db: Session,
    data: dict,
    room_items: list[dict],
    *,
    existing: Contract | None = None,
) -> tuple[dict, list[dict]]:
    prepared_data = dict(data)
    requested_building_id = prepared_data.get(
        "building_id",
        existing.building_id if existing is not None else None,
    )
    if requested_building_id is None:
        return prepare_legacy_contract_write(db, prepared_data, room_items, existing=existing)

    detail_scope = require_detail_building(db, int(requested_building_id), "contracts")
    if detail_scope.building_code.casefold() == LEGACY_BUILDING_CODE.casefold():
        return prepare_legacy_contract_write(db, prepared_data, room_items, existing=existing)
    if existing is not None and existing.building_id != detail_scope.building_id:
        raise HTTPException(404, "合同不存在或无权访问")

    zones_by_id = _active_profile_zones(db, detail_scope)
    resolved: list[dict] = []
    seen_room_ids: set[int] = set()
    seen_room_numbers: set[str] = set()
    for raw_item in room_items:
        item = dict(raw_item)
        requested_item_building_id = item.get("building_id")
        if requested_item_building_id not in (None, detail_scope.building_id):
            raise HTTPException(400, "合同房源关系不属于当前选择楼宇")
        room_no = str(item.get("room_no") or "").strip()
        if not room_no:
            raise HTTPException(400, "合同房源室号不能为空")
        if room_no in seen_room_numbers:
            raise HTTPException(400, f"房源明细室号 {room_no} 重复")
        seen_room_numbers.add(room_no)

        requested_room_id = item.get("room_id")
        room = None
        if requested_room_id is not None:
            try:
                room = db.get(Room, int(requested_room_id))
            except (TypeError, ValueError):
                room = None
            if (
                room is None
                or room.building_id != detail_scope.building_id
                or str(room.room_no) != room_no
            ):
                raise HTTPException(400, f"房源 {room_no} 与所选房源标识不一致或不属于当前楼宇")
        else:
            room = (
                db.query(Room)
                .filter(
                    Room.building_id == detail_scope.building_id,
                    Room.room_no == room_no,
                    Room.is_deleted.is_(False),
                )
                .one_or_none()
            )
        if room is None:
            raise HTTPException(400, f"房源 {room_no} 必须先在当前楼宇房源台账中创建")
        if room.is_deleted:
            raise HTTPException(400, f"房源 {room_no} 已在回收站，不能新增合同关系")
        if not room.is_leasable:
            raise HTTPException(400, f"房源 {room_no} 未标记为可出租，不能新增合同关系")
        if room.zone_id is None or int(room.zone_id) not in zones_by_id:
            raise HTTPException(409, f"房源 {room_no} 的楼宇分区无效，当前写入已停止")
        if room.id in seen_room_ids:
            raise HTTPException(400, f"房源明细室号 {room_no} 重复")
        seen_room_ids.add(int(room.id))

        requested_tower = _zone_text(item.get("tower"))
        zone = zones_by_id[int(room.zone_id)]
        allowed_towers = {
            _zone_text(room.tower).casefold(),
            str(zone.name).casefold(),
            str(zone.code).casefold(),
        }
        if requested_tower and requested_tower.casefold() not in allowed_towers:
            raise HTTPException(400, f"房源 {room_no} 的分区与房源台账不一致")
        item.update(
            {
                "building_id": detail_scope.building_id,
                "room_id": int(room.id),
                "tower": _zone_text(room.tower) or str(zone.name),
                "room_no": str(room.room_no),
            }
        )
        resolved.append(item)

    prepared_data["building_id"] = detail_scope.building_id
    return prepared_data, resolved


def prepare_detail_contract_record(db: Session, contract: Contract) -> None:
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    detail_scope = require_detail_building(db, int(contract.building_id), "contracts")
    if detail_scope.building_code.casefold() == LEGACY_BUILDING_CODE.casefold():
        prepare_legacy_contract_record(db, contract)
        return
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
    prepared_contract, prepared_items = prepare_detail_contract_write(
        db,
        {"building_id": contract.building_id},
        raw_items,
        existing=contract,
    )
    contract.building_id = int(prepared_contract["building_id"])
    stored_by_id = {item.id: item for item in contract.room_items or [] if item.id is not None}
    for prepared in prepared_items:
        stored = stored_by_id.get(prepared.get("id"))
        if stored is None:
            raise HTTPException(409, "合同房源关系尚未持久化，当前写入已停止")
        stored.building_id = prepared["building_id"]
        stored.room_id = prepared["room_id"]
        stored.tower = prepared["tower"]
        stored.room_no = prepared["room_no"]
