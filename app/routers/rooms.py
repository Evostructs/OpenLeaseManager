from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_admin, require_editor
from app.core.uploads import read_import_upload
from app.core.time import now_in_timezone
from app.database import get_db
from app.models import Building, Contract, ContractRoom, Room, User
from app.schemas import RoomPayload
from app.services.common import get_operator, log_action
from app.services.detail_operations_access import detail_scope_dependency
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import (
    SelectedPropertyScope,
    assert_selected_object_building,
    require_single_selected_building,
)
from app.services.rooms import (
    create_room,
    export_room_operations_excel_scoped,
    import_rooms_excel,
    list_rooms,
    room_contract_history,
    room_stats,
    room_template,
    update_room,
    validate_room_contract_building_graph,
)
from app.services.towers import normalize_tower_for_building

get_selected_view_scope = detail_scope_dependency("rooms", for_write=False)
get_selected_edit_scope = detail_scope_dependency("rooms", for_write=True)


def _ensure_room_not_occupied(db: Session, room) -> None:
    """在租/即将到期/逾期占用合同关联的房间不允许删除，防止房态与台账断链。"""
    from app.core.time import today_in_timezone

    validate_room_contract_building_graph(db, room)
    today = today_in_timezone()
    room_start = func.coalesce(ContractRoom.start_date, Contract.start_date)
    room_end = func.coalesce(ContractRoom.end_date, Contract.end_date)
    overlapping = (
        db.query(Contract)
        .join(ContractRoom, ContractRoom.contract_id == Contract.id)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.building_id == room.building_id,
            ContractRoom.building_id == room.building_id,
            ContractRoom.room_no == room.room_no,
            room_start.isnot(None),
            room_end.isnot(None),
            room_start <= today,
            room_end >= today,
        )
        .first()
    )
    if overlapping is None:
        overlapping = (
            db.query(Contract)
            .filter(
                Contract.is_deleted.is_(False),
                Contract.building_id == room.building_id,
                Contract.room_no == room.room_no,
                ~Contract.room_items.any(),
                Contract.start_date.isnot(None),
                Contract.end_date.isnot(None),
                Contract.start_date <= today,
                Contract.end_date >= today,
            )
            .first()
        )
    if overlapping:
        raise HTTPException(400, f"房间 {room.room_no} 当前被合同 {overlapping.contract_no} 占用，不能删除")


def _rooms_for_batch(
    db: Session,
    ids: list[int],
    *,
    is_deleted: bool,
    scope: SelectedPropertyScope,
) -> list[Room]:
    """整批验证存在性、状态和当前请求范围后，再返回任何 ORM 行。"""
    ordered_ids = list(dict.fromkeys(ids))
    if not ordered_ids:
        return []
    rows = (
        db.query(Room)
        .filter(Room.id.in_(ordered_ids), Room.is_deleted.is_(is_deleted))
        .all()
    )
    rows_by_id = {room.id: room for room in rows}
    if len(rows_by_id) != len(ordered_ids):
        raise HTTPException(404, "房间不存在或无权访问")
    ordered_rooms = [rows_by_id[room_id] for room_id in ordered_ids]
    for room in ordered_rooms:
        assert_selected_object_building(scope, room.building_id)
    return ordered_rooms


def _ensure_rooms_have_no_contract_references(db: Session, rooms: list[Room]) -> None:
    """合同房源仍引用台账房源时禁止永久删除，绝不退回文本关联。"""
    room_ids = [room.id for room in rooms if room.id is not None]
    if not room_ids:
        return
    referenced_room_ids = {
        room_id
        for (room_id,) in (
            db.query(ContractRoom.room_id)
            .filter(ContractRoom.room_id.in_(room_ids))
            .distinct()
            .all()
        )
        if room_id is not None
    }
    if not referenced_room_ids:
        return
    room_numbers = [room.room_no for room in rooms if room.id in referenced_room_ids]
    raise HTTPException(
        400,
        f"房间 {'、'.join(room_numbers)} 仍有关联合同房源，不能永久删除",
    )


def _building_log_scope(building_id: int) -> ObjectScope:
    return ObjectScope(scope_type="building", building_id=building_id)


router = APIRouter(prefix="/api/rooms", tags=["rooms"], dependencies=[Depends(get_current_user)])


@router.get("/search")
def search_rooms(
    keyword: str = Query(..., min_length=1, max_length=160),
    page_size: int = Query(6, ge=1, le=20),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    term = keyword.strip()
    if not term:
        return {"items": [], "total": 0}
    query = db.query(Room).filter(
        Room.is_deleted.is_(False), Room.building_id.in_(scope.building_ids or (-1,)),
        or_(Room.room_no.contains(term, autoescape=True), Room.tower.contains(term, autoescape=True)),
    )
    total = query.count()
    rows = query.order_by(Room.tower, Room.room_no, Room.id).limit(page_size).all()
    return {"total": total, "items": [
        {"id": room.id, "room_no": room.room_no, "tower": room.tower, "building_id": room.building_id}
        for room in rows
    ]}


@router.get("")
def rooms(
    start_date: date | None = None,
    end_date: date | None = None,
    exclude_contract_id: int | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    include_operations: bool = False,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_rooms(
        db,
        start_date,
        end_date,
        exclude_contract_id,
        tower,
        status,
        min_area,
        max_area,
        floor,
        include_operations,
        building_ids=scope.building_ids,
    )


@router.get("/stats")
def stats(
    start_date: date | None = None,
    end_date: date | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return room_stats(
        db,
        start_date,
        end_date,
        tower,
        status,
        min_area,
        max_area,
        floor,
        building_ids=scope.building_ids,
    )


@router.get("/export/template")
def export_template(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    return room_template(db=db, building_id=building_id)


@router.get("/export/excel")
def export_excel(
    start_date: date | None = None,
    end_date: date | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return export_room_operations_excel_scoped(
        db,
        start_date=start_date,
        end_date=end_date,
        tower=tower,
        status=status,
        min_area=min_area,
        max_area=max_area,
        floor=floor,
        building_ids=scope.building_ids,
    )


@router.get("/{room_no}/contracts")
def contracts_for_room(
    room_no: str,
    start_date: date | None = None,
    end_date: date | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return room_contract_history(
        db,
        room_no,
        start_date,
        end_date,
        building_ids=scope.building_ids,
    )


@router.post("")
def create(
    payload: RoomPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    try:
        room = create_room(
            db,
            payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
            building_id=building_id,
        )
        log_action(
            db,
            None,
            "room_create",
            f"添加房间 {room.room_no} ({room.tower} {room.room_type} {room.area}㎡)",
            get_operator(request),
            commit=False,
            target_type="room",
            target_id=room.id,
            object_scope=_building_log_scope(building_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "添加成功", "id": room.id}


@router.post("/batch")
def batch_create(
    payload: list[dict],
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    added, skipped = 0, 0
    try:
        for item in payload:
            room_no = str(item.get("room_no") or "").strip()
            if not room_no:
                continue
            if db.query(Room).filter(Room.building_id == building_id, Room.room_no == room_no).first():
                skipped += 1
                continue
            create_room(
                db,
                {
                    "tower": str(item.get("tower") or "").strip(),
                    "floor": str(item.get("floor") or ""),
                    "room_no": room_no,
                    "room_type": str(item.get("room_type") or "办公"),
                    "area": float(item.get("area") or 0),
                    "is_leasable": bool(item.get("is_leasable", True)),
                    "notes": str(item.get("notes") or ""),
                },
                building_id=building_id,
            )
            added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"added": added, "skipped": skipped}


@router.post("/import")
async def import_excel(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return import_rooms_excel(await read_import_upload(file), db, building_id=building_id)


@router.put("/{room_id}")
def update(
    room_id: int,
    payload: RoomPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    room = (
        db.query(Room)
        .filter(
            Room.id == room_id,
            Room.building_id == building_id,
            Room.is_deleted.is_(False),
        )
        .first()
    )
    if not room:
        raise HTTPException(404, "房间不存在或无权访问")
    try:
        room = update_room(
            room,
            payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
            db,
            building_id=building_id,
        )
        log_action(
            db,
            None,
            "room_update",
            f"修改房间 {room.room_no}",
            get_operator(request),
            commit=False,
            target_type="room",
            target_id=room.id,
            object_scope=_building_log_scope(building_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "更新成功"}


@router.post("/batch-delete")
def batch_delete(
    ids: list[int],
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    try:
        rooms_to_delete = _rooms_for_batch(db, ids, is_deleted=False, scope=scope)
        operator = get_operator(request)
        for room in rooms_to_delete:
            room.is_deleted = True
            room.updated_at = now_in_timezone()
            log_action(
                db,
                None,
                "room_delete",
                f"批量删除房间 {room.room_no}",
                operator,
                commit=False,
                target_type="room",
                target_id=room.id,
                object_scope=_building_log_scope(building_id),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    deleted = len(rooms_to_delete)
    return {"message": f"已删除 {deleted} 个房间", "deleted": deleted}


@router.delete("/{room_id}")
def delete(
    room_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    room = (
        db.query(Room)
        .filter(
            Room.id == room_id,
            Room.building_id == building_id,
            Room.is_deleted.is_(False),
        )
        .first()
    )
    if not room:
        raise HTTPException(404, "房间不存在或无权访问")
    try:
        _ensure_room_not_occupied(db, room)
        room.is_deleted = True
        room.updated_at = now_in_timezone()
        log_action(
            db,
            None,
            "room_delete",
            f"删除房间 {room.room_no} ({room.tower} {room.room_type} {room.area}㎡)",
            get_operator(request),
            commit=False,
            target_type="room",
            target_id=room.id,
            object_scope=_building_log_scope(building_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "已删除"}


@router.get("/recycle/list")
def recycle_list(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    items = (
        db.query(Room)
        .filter(
            Room.is_deleted.is_(True),
            Room.building_id.in_(scope.building_ids or (-1,)),
        )
        .order_by(Room.updated_at.desc())
        .all()
    )
    building_code_by_id = {
        int(building_id): str(code)
        for building_id, code in db.query(Building.id, Building.code)
        .filter(Building.id.in_({int(room.building_id) for room in items} or {-1}))
        .all()
    }
    return [
        {
            "id": room.id,
            "tower": normalize_tower_for_building(
                room.tower,
                building_code_by_id.get(int(room.building_id)),
            ),
            "room_no": room.room_no,
            "room_type": room.room_type or "",
            "area": room.area or 0,
            "floor": room.floor or "",
            "updated_at": room.updated_at.isoformat() if room.updated_at else "",
        }
        for room in items
    ]


@router.post("/recycle/restore/{room_id}")
def recycle_restore(
    room_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    room = (
        db.query(Room)
        .filter(
            Room.id == room_id,
            Room.building_id == building_id,
            Room.is_deleted.is_(True),
        )
        .first()
    )
    if not room:
        raise HTTPException(404, "房间不存在或无权访问")
    try:
        room.is_deleted = False
        room.updated_at = now_in_timezone()
        log_action(
            db,
            None,
            "room_restore",
            f"恢复房间 {room.room_no}",
            get_operator(request),
            commit=False,
            target_type="room",
            target_id=room.id,
            object_scope=_building_log_scope(building_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": f"已恢复房间 {room.room_no}"}


@router.post("/recycle/batch-restore")
def recycle_batch_restore(
    ids: list[int],
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    try:
        rooms_to_restore = _rooms_for_batch(db, ids, is_deleted=True, scope=scope)
        operator = get_operator(request)
        for room in rooms_to_restore:
            room.is_deleted = False
            room.updated_at = now_in_timezone()
            log_action(
                db,
                None,
                "room_restore",
                f"批量恢复房间 {room.room_no}",
                operator,
                commit=False,
                target_type="room",
                target_id=room.id,
                object_scope=_building_log_scope(building_id),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    restored = len(rooms_to_restore)
    return {"message": f"已恢复 {restored} 个房间", "restored": restored}


@router.delete("/recycle/purge/{room_id}")
def recycle_purge(
    room_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    room = (
        db.query(Room)
        .filter(
            Room.id == room_id,
            Room.building_id == building_id,
            Room.is_deleted.is_(True),
        )
        .first()
    )
    if not room:
        raise HTTPException(404, "房间不存在或无权访问")
    try:
        _ensure_rooms_have_no_contract_references(db, [room])
        log_action(
            db,
            None,
            "room_purge",
            f"永久删除房间 {room.room_no}",
            get_operator(request),
            commit=False,
            target_type="room",
            target_id=room.id,
            object_scope=_building_log_scope(building_id),
        )
        db.delete(room)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "已永久删除"}


@router.post("/recycle/clear")
def recycle_clear(
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_single_selected_building(scope)
    try:
        rooms_to_delete = (
            db.query(Room)
            .filter(
                Room.is_deleted.is_(True),
                Room.building_id == building_id,
            )
            .order_by(Room.id)
            .all()
        )
        _ensure_rooms_have_no_contract_references(db, rooms_to_delete)
        operator = get_operator(request)
        for room in rooms_to_delete:
            log_action(
                db,
                None,
                "room_clear",
                f"清空回收站房间 {room.room_no}",
                operator,
                commit=False,
                target_type="room",
                target_id=room.id,
                object_scope=_building_log_scope(building_id),
            )
            db.delete(room)
        db.commit()
    except Exception:
        db.rollback()
        raise
    count = len(rooms_to_delete)
    return {"message": f"已清空 {count} 个房间"}
