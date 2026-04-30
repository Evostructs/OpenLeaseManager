from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.database import get_db
from app.models import Room, User
from app.schemas import RoomPayload
from app.services.common import get_operator, log_action
from app.services.integrations import enqueue_event
from app.services.rooms import create_room, export_rooms_excel, import_rooms_excel, list_rooms, room_stats, room_template, update_room


router = APIRouter(prefix="/api/rooms", tags=["rooms"], dependencies=[Depends(get_current_user)])


def _room_payload(room: Room) -> dict:
    return {
        "id": room.id,
        "room_no": room.room_no,
        "tower": room.tower or "",
        "floor": room.floor or "",
        "room_type": room.room_type or "",
        "area": room.area or 0,
        "is_leasable": bool(room.is_leasable),
        "notes": room.notes or "",
        "is_deleted": bool(room.is_deleted),
    }


@router.get("")
def rooms(db: Session = Depends(get_db)):
    return list_rooms(db)


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return room_stats(db)


@router.get("/export/template")
def export_template(_editor: User = Depends(require_editor)):
    return room_template()


@router.get("/export/excel")
def export_excel(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return export_rooms_excel(db)


@router.post("")
def create(payload: RoomPayload, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    room = create_room(db, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    log_action(db, None, "room_create", f"添加房间 {room.room_no} ({room.tower} {room.room_type} {room.area}㎡)", get_operator(request))
    enqueue_event(db, "room", "room.created", room.id, _room_payload(room))
    return {"message": "添加成功", "id": room.id}


@router.post("/batch")
def batch_create(payload: list[dict], _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    added, skipped = 0, 0
    for item in payload:
        room_no = str(item.get("room_no") or "").strip()
        if not room_no:
            continue
        if db.query(Room).filter(Room.room_no == room_no).first():
            skipped += 1
            continue
        room = Room(
            tower=str(item.get("tower") or ""),
            floor=str(item.get("floor") or ""),
            room_no=room_no,
            room_type=str(item.get("room_type") or "办公"),
            area=float(item.get("area") or 0),
            is_leasable=bool(item.get("is_leasable", True)),
            notes=str(item.get("notes") or ""),
        )
        db.add(room)
        db.commit()
        enqueue_event(db, "room", "room.created", room.id, _room_payload(room))
        added += 1
    return {"added": added, "skipped": skipped}


@router.post("/import")
async def import_excel(file: UploadFile = File(...), _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return import_rooms_excel(await file.read(), db)


@router.put("/{room_id}")
def update(room_id: int, payload: RoomPayload, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(False)).first()
    if not room:
        raise HTTPException(404, "房间不存在")
    room = update_room(room, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), db)
    log_action(db, None, "room_update", f"修改房间 {room.room_no}", get_operator(request))
    enqueue_event(db, "room", "room.updated", room.id, _room_payload(room))
    return {"message": "更新成功"}


@router.post("/batch-delete")
def batch_delete(ids: list[int], request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    deleted = 0
    deleted_payloads = []
    for room_id in ids:
        room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(False)).first()
        if room:
            room.is_deleted = True
            room.updated_at = datetime.now()
            deleted += 1
            deleted_payloads.append((room.id, _room_payload(room)))
    db.commit()
    log_action(db, None, "room_delete", f"批量删除{deleted}个房间", get_operator(request))
    for room_id, payload in deleted_payloads:
        enqueue_event(db, "room", "room.deleted", room_id, payload)
    return {"message": f"已删除 {deleted} 个房间", "deleted": deleted}


@router.delete("/{room_id}")
def delete(room_id: int, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(False)).first()
    if not room:
        raise HTTPException(404, "房间不存在")
    room.is_deleted = True
    room.updated_at = datetime.now()
    db.commit()
    log_action(db, None, "room_delete", f"删除房间 {room.room_no} ({room.tower} {room.room_type} {room.area}㎡)", get_operator(request))
    enqueue_event(db, "room", "room.deleted", room.id, _room_payload(room))
    return {"message": "已删除"}


@router.get("/recycle/list")
def recycle_list(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    items = db.query(Room).filter(Room.is_deleted.is_(True)).order_by(Room.updated_at.desc()).all()
    return [
        {
            "id": room.id,
            "tower": room.tower or "",
            "room_no": room.room_no,
            "room_type": room.room_type or "",
            "area": room.area or 0,
            "floor": room.floor or "",
            "updated_at": room.updated_at.isoformat() if room.updated_at else "",
        }
        for room in items
    ]


@router.post("/recycle/restore/{room_id}")
def recycle_restore(room_id: int, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(True)).first()
    if not room:
        raise HTTPException(404, "房间不存在或未删除")
    room.is_deleted = False
    room.updated_at = datetime.now()
    db.commit()
    log_action(db, None, "room_restore", f"恢复房间 {room.room_no}", get_operator(request))
    return {"message": f"已恢复房间 {room.room_no}"}


@router.post("/recycle/batch-restore")
def recycle_batch_restore(ids: list[int], request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    restored = 0
    for room_id in ids:
        room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(True)).first()
        if room:
            room.is_deleted = False
            room.updated_at = datetime.now()
            restored += 1
            log_action(db, None, "room_restore", f"批量恢复房间 {room.room_no}", get_operator(request))
    db.commit()
    return {"message": f"已恢复 {restored} 个房间", "restored": restored}


@router.delete("/recycle/purge/{room_id}")
def recycle_purge(room_id: int, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id, Room.is_deleted.is_(True)).first()
    if not room:
        raise HTTPException(404, "房间不存在")
    db.delete(room)
    db.commit()
    return {"message": "已永久删除"}


@router.post("/recycle/clear")
def recycle_clear(request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    deleted = db.query(Room).filter(Room.is_deleted.is_(True)).all()
    count = len(deleted)
    for room in deleted:
        db.delete(room)
    db.commit()
    log_action(db, None, "room_clear", f"清空房间回收站 ({count} 个)", get_operator(request))
    return {"message": f"已清空 {count} 个房间"}
