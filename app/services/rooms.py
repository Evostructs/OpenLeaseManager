from __future__ import annotations

from datetime import date, datetime
import io
from typing import Iterable
from urllib.parse import quote

import openpyxl
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl.styles import Font
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Contract, Room


ROOM_IMPORT_HEADERS = {
    "楼栋/区域": "tower",
    "楼栋": "tower",
    "区域": "tower",
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


def _active_contract_query(db: Session):
    today = date.today()
    return db.query(Contract).filter(
        Contract.is_deleted.is_(False),
        Contract.room_no.isnot(None),
        or_(
            (Contract.start_date <= today) & (Contract.end_date >= today),
            (Contract.end_date < today) & (Contract.disposal == "逾期占用"),
        ),
    )


def list_rooms(db: Session) -> list[dict]:
    rooms = db.query(Room).filter(Room.is_deleted.is_(False)).order_by(Room.tower, Room.room_no).all()
    active_contracts = _active_contract_query(db).all()
    occupied_rooms = {}
    for contract in active_contracts:
        if contract.status in {"在租", "即将到期", "逾期占用"} and contract.room_no:
            occupied_rooms[contract.room_no] = {
                "contract_no": contract.contract_no,
                "tenant_name": contract.tenant_name,
                "end_date": contract.end_date.isoformat() if contract.end_date else None,
                "status": contract.status,
                "area": contract.area or 0,
            }

    items = []
    for room in rooms:
        contract = occupied_rooms.get(room.room_no)
        items.append(
            {
                "id": room.id,
                "tower": room.tower or "",
                "floor": room.floor or "",
                "room_no": room.room_no,
                "room_type": room.room_type or "办公",
                "area": room.area or 0,
                "is_leasable": room.is_leasable,
                "notes": room.notes or "",
                "occupied": contract is not None,
                "contract": contract,
            }
        )
    return items


def room_stats(db: Session) -> dict:
    room_filter = (Room.is_leasable.is_(True), Room.is_deleted.is_(False))
    total_rooms, total_area = (
        db.query(func.count(Room.id), func.coalesce(func.sum(Room.area), 0))
        .filter(*room_filter)
        .one()
    )
    occupied_room_nos = {
        room_no
        for (room_no,) in (
            _active_contract_query(db)
            .with_entities(Contract.room_no)
            .join(Room, Room.room_no == Contract.room_no)
            .filter(*room_filter)
            .distinct()
            .all()
        )
    }
    occupied_rooms = len(occupied_room_nos)
    occupied_area = (
        db.query(func.coalesce(func.sum(Room.area), 0))
        .filter(*room_filter, Room.room_no.in_(occupied_room_nos or [""]))
        .scalar()
        or 0
    )

    rooms = db.query(Room).filter(*room_filter).all()

    type_stats = {}
    for room in rooms:
        key = room.room_type or "办公"
        entry = type_stats.setdefault(key, {"total": 0, "area": 0, "occupied": 0, "occupied_area": 0})
        entry["total"] += 1
        entry["area"] += room.area or 0
        if room.room_no in occupied_room_nos:
            entry["occupied"] += 1
            entry["occupied_area"] += room.area or 0

    return {
        "total_rooms": total_rooms,
        "total_area": round(total_area, 2),
        "occupied_rooms": occupied_rooms,
        "occupied_area": round(occupied_area, 2),
        "vacancy_rooms": total_rooms - occupied_rooms,
        "vacancy_area": round(total_area - occupied_area, 2),
        "occupancy_rate": round(occupied_area / total_area * 100, 1) if total_area > 0 else 0,
        "type_stats": type_stats,
    }


def create_room(db: Session, data: dict) -> Room:
    existing = db.query(Room).filter(Room.room_no == data["room_no"]).first()
    if existing:
        raise HTTPException(400, f"室号 {data['room_no']} 已存在")
    room = Room(**data)
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def update_room(room: Room, payload: dict, db: Session) -> Room:
    new_room_no = payload.get("room_no")
    if new_room_no and new_room_no != room.room_no:
        conflict = db.query(Room).filter(Room.room_no == new_room_no, Room.id != room.id).first()
        if conflict:
            raise HTTPException(400, f"室号 {new_room_no} 已被其他房间使用")
    for key, value in payload.items():
        setattr(room, key, value)
    room.updated_at = datetime.now()
    db.commit()
    db.refresh(room)
    return room


def import_rooms_excel(content: bytes, db: Session) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active

    header_row = None
    headers = {}
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=False), start=1):
        matched = 0
        candidate = {}
        for cell in row:
            raw = str(cell.value).strip().replace(" ", "") if cell.value else ""
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
                payload[headers[cell.column]] = cell.value

        room_no = str(payload.get("room_no") or "").strip()
        if not room_no:
            continue
        if room_no in seen_room_nos:
            skipped += 1
            errors.append(f"室号 {room_no} 在导入文件中重复，跳过")
            continue
        seen_room_nos.add(room_no)
        if db.query(Room).filter(Room.room_no == room_no).first():
            skipped += 1
            errors.append(f"室号 {room_no} 已存在，跳过")
            continue

        try:
            area = float(payload.get("area") or 0)
            if area < 0:
                raise ValueError("面积不能为负数")
            pending_rooms.append(Room(
                tower=str(payload.get("tower") or ""),
                floor=str(payload.get("floor") or ""),
                room_no=room_no,
                room_type=str(payload.get("room_type") or "办公"),
                area=area,
                notes=str(payload.get("notes") or ""),
            ))
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


def export_rooms_excel(db: Session) -> StreamingResponse:
    rooms = list_rooms(db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "房间清单"

    headers = ["楼栋/区域", "楼层", "室号", "类型", "面积(㎡)", "出租状态", "客户名称", "合同编号", "合同到期日", "备注"]
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

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"房间清单_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def room_template() -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "房间导入模板"
    headers = ["楼栋/区域", "楼层", "室号", "类型", "面积(㎡)", "备注"]
    ws.append(headers)
    for idx, header in enumerate(headers, 1):
        ws.cell(1, idx).font = Font(bold=True)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E6%88%BF%E9%97%B4%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"},
    )
