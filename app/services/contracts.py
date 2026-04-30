from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import io
from pathlib import Path
import tempfile
from urllib.parse import quote

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
import openpyxl
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.crypto import (
    CryptoError,
    decrypt_file,
    encrypt_file,
    encrypt_stream_to_file,
    encrypted_name,
    is_encrypted_file,
    public_name,
)
from app.core.files import ensure_child_path, ensure_pdf_filename, sanitize_stem
from app.models import Contract
from app.schemas import ContractCreate, ContractUpdate
from app.services.common import dump_changes, log_action, parse_date, serialize_contract
from app.services.integrations import enqueue_event


SETTINGS = get_settings()
PDF_CHUNK_SIZE = 1024 * 1024
MAX_PDF_FILE_SIZE = 50 * 1024 * 1024
MAX_BATCH_PDF_COUNT = 20
MAX_BATCH_PDF_TOTAL_SIZE = 200 * 1024 * 1024
ACTIVE_CONTRACT_STATUSES = {"在租", "即将到期", "逾期占用"}


EXPORT_HEADERS = [
    "序号",
    "合同编号",
    "楼栋/区域",
    "室号",
    "客户名称",
    "出租面积",
    "租金单价",
    "新租/续租",
    "免租期",
    "月租金",
    "中介佣金",
    "净租金",
    "起租日",
    "终止日",
    "合同状态",
    "到期处置",
    "联系人",
    "联系电话",
    "押金",
    "押金状态",
    "物业费单价",
    "月物业费",
    "关联上份合同",
    "备注",
]

FIELD_MAP: dict[str, str | None] = {}
_ALIAS_LIST = [
    (None, ["序号", "编号", "No", "NO", "no"]),
    ("contract_no", ["合同编号", "合同号", "合同编码", "合同No", "contract"]),
    ("tower", ["楼栋/区域", "楼栋", "区域", "楼塔", "塔楼", "塔", "栋"]),
    ("room_no", ["室号", "房号", "房间号", "房间", "室", "单元号", "单元"]),
    ("tenant_name", ["客户名称", "客户", "租户", "租户名称", "承租方", "承租人", "公司名称", "租赁方"]),
    ("area", ["出租面积", "面积", "租赁面积", "建筑面积", "使用面积", "面积(㎡)", "面积（㎡）", "面积㎡"]),
    ("unit_price", ["租金单价", "单价", "租金/单价", "单价(元)", "单价（元）"]),
    ("lease_type", ["新租/续租", "新租续租", "租赁类型", "类型", "新/续租", "新续租", "租赁方式"]),
    ("free_period", ["免租期", "免租期(月)", "免租期（月）", "免租月数", "免租"]),
    ("monthly_rent", ["月租金", "月租金1", "月租", "租金", "月租金(元)", "月租金（元）"]),
    (None, ["月租金2", "月租金3"]),
    ("broker_fee", ["中介佣金", "佣金", "中介费", "中介费用", "佣金(元)", "佣金（元）"]),
    (None, ["净租金", "净租金(元)", "净租金（元）"]),
    ("start_date", ["起租日", "起租日期", "起始日", "起始日期", "开始日期", "租赁起始日", "合同开始日"]),
    ("end_date", ["终止日", "终止日期", "到期日", "到期日期", "结束日期", "租赁终止日", "合同结束日", "截止日", "截止日期"]),
    ("notes", ["备注", "说明", "备注信息", "注", "其他"]),
    (None, ["合同状态", "状态"]),
    ("disposal", ["到期处置", "处置", "处置状态", "到期状态"]),
    ("contact_person", ["联系人", "联系人姓名", "对接人"]),
    ("contact_phone", ["联系电话", "电话", "手机", "手机号"]),
    ("contact_email", ["邮箱", "电子邮箱", "email", "Email"]),
    ("deposit", ["押金", "保证金", "押金金额"]),
    ("deposit_status", ["押金状态", "保证金状态"]),
    ("property_fee_rate", ["物业费单价", "物业单价", "物业费", "管理费单价", "管理费"]),
    ("property_fee_free", ["物业费免费期", "物业免费期", "物业费减免期", "管理费免费期"]),
    ("prev_contract_no", ["关联合同", "上份合同", "关联上份合同", "原合同编号"]),
]
for field, aliases in _ALIAS_LIST:
    for alias in aliases:
        FIELD_MAP[alias] = field


def _apply_status_filter(query, status: str | None):
    if not status:
        return query

    today = date.today()
    soon = today + relativedelta(days=90)
    if status == "信息不全":
        return query.filter(or_(Contract.start_date.is_(None), Contract.end_date.is_(None)))
    if status == "待生效":
        return query.filter(Contract.start_date > today)
    if status in {"已续租", "已退租", "逾期占用"}:
        return query.filter(Contract.end_date < today, Contract.disposal == status)
    if status == "已到期":
        return query.filter(
            Contract.end_date < today,
            or_(Contract.disposal.is_(None), ~Contract.disposal.in_(["已续租", "已退租", "逾期占用"])),
        )
    if status == "即将到期":
        return query.filter(
            Contract.start_date <= today,
            Contract.end_date >= today,
            Contract.end_date <= soon,
        )
    if status == "在租":
        return query.filter(
            Contract.start_date <= today,
            Contract.end_date > soon,
        )
    return query


def next_contract_no(db: Session) -> dict:
    year = date.today().year
    prefix = f"{SETTINGS.contract_no_prefix}-{year}-"
    existing = db.query(Contract).filter(Contract.contract_no.like(f"{prefix}%")).all()
    max_seq = 0
    for contract in existing:
        try:
            max_seq = max(max_seq, int(contract.contract_no.replace(prefix, "")))
        except Exception:
            continue
    return {"next_no": f"{prefix}{max_seq + 1:03d}"}


def get_contract(db: Session, contract_id: int, include_deleted: bool = False) -> Contract:
    query = db.query(Contract).filter(Contract.id == contract_id)
    if not include_deleted:
        query = query.filter(Contract.is_deleted.is_(False))
    contract = query.first()
    if not contract:
        raise HTTPException(404, "合同不存在")
    return contract


def list_contracts(
    db: Session,
    page: int,
    page_size: int,
    keyword: str | None,
    tower: str | None,
    status: str | None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict:
    query = db.query(Contract).filter(Contract.is_deleted.is_(False))
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(Contract.contract_no.like(like), Contract.room_no.like(like), Contract.tenant_name.like(like)))
    if tower:
        query = query.filter(Contract.tower == tower)
    query = _apply_status_filter(query, status)

    sort_key = sort_by or "end_date"
    reverse = sort_dir == "desc"
    sortable_columns = {
        "contract_no": Contract.contract_no,
        "tower": Contract.tower,
        "room_no": Contract.room_no,
        "tenant_name": Contract.tenant_name,
        "area": Contract.area,
        "monthly_rent": Contract.monthly_rent,
        "end_date": Contract.end_date,
    }

    if sort_key in sortable_columns:
        column = sortable_columns[sort_key]
        query = query.order_by(column.desc() if reverse else column.asc())
        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return {"total": total, "page": page, "page_size": page_size, "items": [serialize_contract(item) for item in items]}

    items = query.all()

    if sort_key == "contract_no":
        items.sort(key=lambda item: item.contract_no or "", reverse=reverse)
    elif sort_key == "tower":
        items.sort(key=lambda item: item.tower or "", reverse=reverse)
    elif sort_key == "room_no":
        items.sort(key=lambda item: item.room_no or "", reverse=reverse)
    elif sort_key == "tenant_name":
        items.sort(key=lambda item: item.tenant_name or "", reverse=reverse)
    elif sort_key == "area":
        items.sort(key=lambda item: item.area or 0, reverse=reverse)
    elif sort_key == "monthly_rent":
        items.sort(key=lambda item: item.monthly_rent or 0, reverse=reverse)
    elif sort_key == "status":
        items.sort(key=lambda item: item.status or "", reverse=reverse)
    else:
        items.sort(key=lambda item: item.end_date or date.max, reverse=reverse)

    total = len(items)
    sliced = items[(page - 1) * page_size : page * page_size]
    return {"total": total, "page": page, "page_size": page_size, "items": [serialize_contract(item) for item in sliced]}


def _month_bounds(anchor: date) -> tuple[date, date]:
    month_start = anchor.replace(day=1)
    month_end = month_start + relativedelta(months=1, days=-1)
    return month_start, month_end


def _validate_contract_values(data: dict) -> None:
    if data.get("start_date") and data.get("end_date") and data["end_date"] <= data["start_date"]:
        raise HTTPException(400, "合同结束日期必须晚于开始日期")
    if (data.get("monthly_rent") or 0) < 0:
        raise HTTPException(400, "月租金不能为负数")
    if (data.get("area") or 0) < 0:
        raise HTTPException(400, "面积不能为负数")
    if (data.get("unit_price") or 0) < 0:
        raise HTTPException(400, "日租单价不能为负数")
    if (data.get("broker_fee") or 0) < 0:
        raise HTTPException(400, "佣金不能为负数")
    if (data.get("deposit") or 0) < 0:
        raise HTTPException(400, "保证金不能为负数")
    if (data.get("property_fee_rate") or 0) < 0:
        raise HTTPException(400, "物业费单价不能为负数")
    if (data.get("free_period") or 0) < 0:
        raise HTTPException(400, "免租期不能为负数")


def _find_overlapping_contract(
    db: Session,
    *,
    room_no: str,
    start_date: date | None,
    end_date: date | None,
    exclude_id: int | None = None,
) -> Contract | None:
    if not room_no or not start_date or not end_date:
        return None
    query = db.query(Contract).filter(
        Contract.is_deleted.is_(False),
        Contract.room_no == room_no,
        Contract.start_date.isnot(None),
        Contract.end_date.isnot(None),
        Contract.start_date <= end_date,
        Contract.end_date >= start_date,
    )
    if exclude_id is not None:
        query = query.filter(Contract.id != exclude_id)
    return query.first()


def _periods_overlap(first_start: date | None, first_end: date | None, second_start: date | None, second_end: date | None) -> bool:
    if not first_start or not first_end or not second_start or not second_end:
        return False
    return first_start <= second_end and first_end >= second_start


def _is_currently_active(contract: Contract) -> bool:
    return contract.status in ACTIVE_CONTRACT_STATUSES and contract.disposal != "已退租"


def _safe_upload_path(filename: str) -> Path:
    return ensure_child_path(get_settings().upload_dir, filename)


def _stored_pdf_name(filename: str) -> str:
    settings = get_settings()
    return encrypted_name(filename) if settings.data_encryption_enabled else filename


def _ensure_pdf_upload_name(upload: UploadFile) -> str:
    raw_name = upload.filename or ""
    if Path(raw_name).suffix.lower() != ".pdf":
        raise HTTPException(400, "请上传 PDF 文件")
    return raw_name


def _write_limited_upload(upload: UploadFile, target: Path, *, max_size: int | None = None) -> int:
    max_size = max_size or MAX_PDF_FILE_SIZE
    try:
        upload.file.seek(0)
    except Exception:
        pass

    settings = get_settings()
    if settings.data_encryption_enabled:
        try:
            return encrypt_stream_to_file(upload.file, target, settings.data_key, max_size=max_size)
        except CryptoError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(413, f"PDF 文件不能超过 {max_size // 1024 // 1024}MB") from exc

    total = 0
    with target.open("wb") as handle:
        while True:
            chunk = upload.file.read(PDF_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"PDF 文件不能超过 {max_size // 1024 // 1024}MB")
            handle.write(chunk)
    return total


def build_rent_plan(contract: Contract) -> dict:
    if not contract.start_date or not contract.end_date:
        return {"plan": [], "summary": {}}

    current = contract.start_date.replace(day=1)
    end = contract.end_date
    month_idx = 0
    total_rent = 0
    total_property = 0
    plan = []

    while current <= end:
        month_idx += 1
        rent_free = month_idx <= (contract.free_period or 0)
        rent = 0 if rent_free else (contract.monthly_rent or 0)
        property_fee = contract.monthly_property_fee
        total_rent += rent
        total_property += property_fee
        plan.append(
            {
                "month": current.strftime("%Y-%m"),
                "month_idx": month_idx,
                "rent": rent,
                "rent_free": rent_free,
                "property_fee": round(property_fee, 2),
                "property_free": False,
                "total": round(rent + property_fee, 2),
            }
        )
        current += relativedelta(months=1)

    return {
        "plan": plan,
        "summary": {
            "total_months": len(plan),
            "free_months": contract.free_period or 0,
            "prop_free_months": 0,
            "total_rent": round(total_rent, 2),
            "total_property": round(total_property, 2),
            "total_all": round(total_rent + total_property, 2),
        },
    }


def get_monthly_receivables(db: Session, month: str | None) -> dict:
    if not month:
        month = date.today().strftime("%Y-%m")
    try:
        target = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(400, "月份格式错误，应为 YYYY-MM") from exc

    month_start, month_end = _month_bounds(target)
    rows = []
    total_rent = 0
    total_property = 0
    contracts = (
        db.query(Contract)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.start_date <= month_end,
            Contract.end_date >= month_start,
        )
        .all()
    )
    for contract in contracts:
        if not contract.start_date or not contract.end_date:
            continue
        if contract.status in {"已退租"}:
            continue
        month_idx = (target.year - contract.start_date.year) * 12 + (target.month - contract.start_date.month) + 1
        rent = 0 if month_idx <= (contract.free_period or 0) else (contract.monthly_rent or 0)
        property_fee = contract.monthly_property_fee
        total_rent += rent
        total_property += property_fee
        rows.append(
            {
                "contract_no": contract.contract_no,
                "tenant_name": contract.tenant_name or "",
                "room_no": contract.room_no or "",
                "tower": contract.tower or "",
                "area": contract.area or 0,
                "rent": round(rent, 2),
                "rent_free": month_idx <= (contract.free_period or 0),
                "property_fee": round(property_fee, 2),
                "prop_free": False,
                "total": round(rent + property_fee, 2),
                "start_date": contract.start_date.isoformat(),
                "end_date": contract.end_date.isoformat(),
            }
        )

    rows.sort(key=lambda item: item["total"], reverse=True)
    return {
        "month": month,
        "rows": rows,
        "total_rent": round(total_rent, 2),
        "total_property": round(total_property, 2),
        "total": round(total_rent + total_property, 2),
        "count": len(rows),
    }


def get_contract_stats(db: Session) -> dict:
    contracts = db.query(Contract).filter(Contract.is_deleted.is_(False)).all()
    active = [item for item in contracts if _is_currently_active(item)]

    statuses = {}
    expiring_soon = []
    for contract in contracts:
        statuses[contract.status] = statuses.get(contract.status, 0) + 1
        if contract.days_to_expiry is not None and 0 < contract.days_to_expiry <= 90:
            expiring_soon.append(
                {
                    "id": contract.id,
                    "contract_no": contract.contract_no,
                    "room_no": contract.room_no,
                    "tenant_name": contract.tenant_name,
                    "end_date": contract.end_date.isoformat() if contract.end_date else None,
                    "days_left": contract.days_to_expiry,
                }
            )
    expiring_soon.sort(key=lambda item: item["days_left"])

    total_area = sum(item.area or 0 for item in active)
    total_monthly_rent = sum(item.monthly_rent or 0 for item in active)
    total_net_rent = sum(item.net_rent or 0 for item in active)
    avg_price = round(total_monthly_rent / total_area, 2) if total_area > 0 else 0

    tower_data = {}
    for contract in active:
        key = contract.tower or "未标注"
        entry = tower_data.setdefault(key, {"count": 0, "area": 0, "rent": 0})
        entry["count"] += 1
        entry["area"] += contract.area or 0
        entry["rent"] += contract.monthly_rent or 0

    lease_types = {}
    for contract in active:
        key = contract.lease_type or "未标注"
        lease_types[key] = lease_types.get(key, 0) + 1

    today = date.today()
    expiry_months = []
    for offset in range(12):
        current = today + relativedelta(months=offset)
        label = current.strftime("%Y-%m")
        count = sum(
            1
            for contract in contracts
            if contract.end_date and contract.end_date.year == current.year and contract.end_date.month == current.month and contract.status != "已到期"
        )
        expiry_months.append({"month": label, "count": count})

    total_deposit = sum(item.deposit or 0 for item in contracts if item.deposit_status in {"已收", ""})
    disposals = {}
    for contract in contracts:
        if contract.status in {"已到期", "已续租", "已退租", "逾期占用"}:
            key = contract.disposal or "待处理"
            disposals[key] = disposals.get(key, 0) + 1

    room_total_area = 0
    try:
        from app.models import Room

        rooms = db.query(Room).filter(Room.is_leasable.is_(True), Room.is_deleted.is_(False)).all()
        room_total_area = sum(room.area or 0 for room in rooms)
    except Exception:
        rooms = []
    occupancy_rate = round(total_area / room_total_area * 100, 1) if room_total_area > 0 else 0

    total_property_fee = sum(item.monthly_property_fee for item in active)
    total_net_property_fee = sum(item.net_property_fee for item in active)

    def monthly_snapshot(anchor: date) -> dict:
        month_start, month_end = _month_bounds(anchor)
        snapshot_contracts = [
            item
            for item in contracts
            if item.start_date
            and item.end_date
            and item.start_date <= month_end
            and item.end_date >= month_start
            and _is_currently_active(item)
        ]
        snapshot_area = sum(item.area or 0 for item in snapshot_contracts)
        snapshot_rent = sum(item.monthly_rent or 0 for item in snapshot_contracts)
        snapshot_rate = round(snapshot_area / room_total_area * 100, 1) if room_total_area > 0 else 0
        return {
            "area": round(snapshot_area, 2),
            "rent": round(snapshot_rent, 2),
            "occupancy_rate": snapshot_rate,
        }

    def rate_change(current_value: float, base_value: float) -> float:
        if not base_value:
            return 0
        return round((current_value - base_value) / base_value * 100, 1)

    current_snapshot = monthly_snapshot(today)
    previous_snapshot = monthly_snapshot(today + relativedelta(months=-1))
    year_ago_snapshot = monthly_snapshot(today + relativedelta(years=-1))

    term_dist = {"1年以内": 0, "1-2年": 0, "2-3年": 0, "3年以上": 0}
    for contract in contracts:
        if contract.start_date and contract.end_date:
            months = contract.total_months
            if months < 12:
                term_dist["1年以内"] += 1
            elif months < 24:
                term_dist["1-2年"] += 1
            elif months < 36:
                term_dist["2-3年"] += 1
            else:
                term_dist["3年以上"] += 1

    price_by_month = defaultdict(list)
    for contract in contracts:
        if contract.start_date and contract.unit_price and contract.unit_price > 0:
            price_by_month[contract.start_date.strftime("%Y-%m")].append(contract.unit_price)
    price_trend = [
        {"month": month_key, "avg_price": round(sum(values) / len(values), 2), "count": len(values)}
        for month_key, values in sorted(price_by_month.items())
    ]

    income_forecast = []
    for offset in range(12):
        current = today + relativedelta(months=offset)
        month_start, month_end = _month_bounds(current)
        monthly_income = 0
        for contract in contracts:
            if contract.start_date and contract.end_date and contract.start_date <= month_end and contract.end_date >= month_start:
                if contract.disposal != "已退租":
                    monthly_income += contract.monthly_rent or 0
        income_forecast.append({"month": current.strftime("%Y-%m"), "income": round(monthly_income, 2)})

    return {
        "total": len(contracts),
        "active_count": len(active),
        "statuses": statuses,
        "expiring_soon": expiring_soon,
        "metrics": {
            "total_area": round(total_area, 2),
            "total_monthly_rent": round(total_monthly_rent, 2),
            "total_net_rent": round(total_net_rent, 2),
            "avg_price": avg_price,
            "total_deposit": round(total_deposit, 2),
            "room_total_area": round(room_total_area, 2),
            "occupancy_rate": occupancy_rate,
        },
        "metrics_trends": {
            "area": {
                "mom": rate_change(current_snapshot["area"], previous_snapshot["area"]),
                "yoy": rate_change(current_snapshot["area"], year_ago_snapshot["area"]),
            },
            "rent": {
                "mom": rate_change(current_snapshot["rent"], previous_snapshot["rent"]),
                "yoy": rate_change(current_snapshot["rent"], year_ago_snapshot["rent"]),
            },
            "occupancy_rate": {
                "mom": round(current_snapshot["occupancy_rate"] - previous_snapshot["occupancy_rate"], 1),
                "yoy": round(current_snapshot["occupancy_rate"] - year_ago_snapshot["occupancy_rate"], 1),
            },
        },
        "disposals": disposals,
        "tower_data": tower_data,
        "lease_types": lease_types,
        "expiry_months": expiry_months,
        "metrics_extra": {
            "total_property_fee": round(total_property_fee, 2),
            "total_net_property_fee": round(total_net_property_fee, 2),
        },
        "term_dist": term_dist,
        "price_trend": price_trend,
        "income_forecast": income_forecast,
    }


def create_contract(db: Session, payload: ContractCreate, operator: str) -> dict:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    contract_no = (data.get("contract_no") or "").strip()
    if not contract_no:
        raise HTTPException(400, "合同编号不能为空")
    _validate_contract_values(data)
    if db.query(Contract).filter(Contract.contract_no == contract_no).first():
        raise HTTPException(400, f"合同编号 {contract_no} 已存在")

    data["contract_no"] = contract_no
    data["property_fee_free"] = 0
    overlapping = _find_overlapping_contract(
        db,
        room_no=str(data.get("room_no") or "").strip(),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    )
    if overlapping:
        raise HTTPException(400, f"室号 {data.get('room_no')} 在该租期内已关联合同 {overlapping.contract_no}")
    contract = Contract(**data)
    db.add(contract)
    db.commit()
    db.refresh(contract)

    if payload.prev_contract_no:
        previous = db.query(Contract).filter(Contract.contract_no == payload.prev_contract_no, Contract.is_deleted.is_(False)).first()
        if previous and previous.disposal not in {"已续租", "已退租"}:
            previous.disposal = "已续租"
            previous.updated_at = datetime.now()
            db.commit()
            log_action(db, previous.id, "update", f"因新合同 {contract.contract_no} 生效，自动标记为已续租", operator)

    log_action(db, contract.id, "create", dump_changes(data), operator)
    enqueue_event(db, "contract", "contract.created", contract.id, serialize_contract(contract))
    return serialize_contract(contract)


def update_contract(db: Session, contract: Contract, payload: ContractUpdate, operator: str) -> dict:
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    if payload.last_updated:
        last_updated = contract.updated_at.isoformat() if contract.updated_at else ""
        if payload.last_updated != last_updated:
            raise HTTPException(409, "该合同已被其他用户修改，请刷新后重试")

    if contract.status in {"在租", "即将到期"}:
        locked_fields = {"start_date": "起租日", "end_date": "终止日", "monthly_rent": "月租金", "area": "面积", "unit_price": "单价", "broker_fee": "佣金"}
        for field, label in locked_fields.items():
            if field in data and getattr(contract, field) is not None and str(data[field]) != str(getattr(contract, field)):
                raise HTTPException(400, f"合同已生效，{label}不可修改。如需变更请通过续租/变更流程新建合同。")

    if "contract_no" in data:
        new_contract_no = (data["contract_no"] or "").strip()
        if not new_contract_no:
            raise HTTPException(400, "合同编号不能为空")
        conflict = db.query(Contract).filter(Contract.contract_no == new_contract_no, Contract.id != contract.id).first()
        if conflict:
            raise HTTPException(400, f"合同编号 {new_contract_no} 已存在")
        data["contract_no"] = new_contract_no

    if "property_fee_free" in data:
        data["property_fee_free"] = 0

    candidate = {
        "start_date": data.get("start_date", contract.start_date),
        "end_date": data.get("end_date", contract.end_date),
        "room_no": data.get("room_no", contract.room_no),
        "monthly_rent": data.get("monthly_rent", contract.monthly_rent),
        "area": data.get("area", contract.area),
        "unit_price": data.get("unit_price", contract.unit_price),
        "broker_fee": data.get("broker_fee", contract.broker_fee),
        "deposit": data.get("deposit", contract.deposit),
        "property_fee_rate": data.get("property_fee_rate", contract.property_fee_rate),
        "free_period": data.get("free_period", contract.free_period),
    }
    _validate_contract_values(candidate)
    overlapping = _find_overlapping_contract(
        db,
        room_no=str(candidate.get("room_no") or "").strip(),
        start_date=candidate.get("start_date"),
        end_date=candidate.get("end_date"),
        exclude_id=contract.id,
    )
    if overlapping:
        raise HTTPException(400, f"室号 {candidate.get('room_no')} 在该租期内已关联合同 {overlapping.contract_no}")

    changes = {}
    for key, value in data.items():
        if key in {"last_updated", "status"}:
            continue
        old_value = getattr(contract, key)
        if str(old_value) != str(value):
            changes[key] = {"old": str(old_value), "new": str(value)}
            setattr(contract, key, value)

    contract.updated_at = datetime.now()
    db.commit()
    db.refresh(contract)
    if changes:
        log_action(db, contract.id, "update", dump_changes(changes), operator)
        enqueue_event(db, "contract", "contract.updated", contract.id, serialize_contract(contract))
    return serialize_contract(contract)


def soft_delete_contract(db: Session, contract: Contract, operator: str) -> dict:
    contract.is_deleted = True
    contract.updated_at = datetime.now()
    db.commit()
    log_action(db, contract.id, "delete", f"软删除合同 {contract.contract_no}", operator)
    enqueue_event(db, "contract", "contract.deleted", contract.id, {"id": contract.id, "contract_no": contract.contract_no})
    return {"message": "已删除"}


def batch_delete_contracts(db: Session, ids: list[int], operator: str) -> dict:
    contracts = db.query(Contract).filter(Contract.id.in_(ids), Contract.is_deleted.is_(False)).all() if ids else []
    try:
        for contract in contracts:
            contract.is_deleted = True
            contract.updated_at = datetime.now()
            log_action(db, contract.id, "delete", f"批量删除合同 {contract.contract_no}", operator, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    deleted = len(contracts)
    for contract in contracts:
        enqueue_event(db, "contract", "contract.deleted", contract.id, {"id": contract.id, "contract_no": contract.contract_no})
    return {"message": f"已删除 {deleted} 条", "deleted": deleted}


def restore_contract(db: Session, contract: Contract, operator: str) -> dict:
    conflict = db.query(Contract).filter(Contract.contract_no == contract.contract_no, Contract.is_deleted.is_(False)).first()
    if conflict:
        raise HTTPException(400, f"合同编号 {contract.contract_no} 已被使用，无法恢复")
    contract.is_deleted = False
    contract.updated_at = datetime.now()
    db.commit()
    log_action(db, contract.id, "restore", f"从回收站恢复合同 {contract.contract_no}", operator)
    return {"message": f"已恢复合同 {contract.contract_no}"}


def batch_restore_contracts(db: Session, ids: list[int], operator: str) -> dict:
    restored = 0
    for contract_id in ids:
        contract = db.query(Contract).filter(Contract.id == contract_id, Contract.is_deleted.is_(True)).first()
        if not contract:
            continue
        conflict = db.query(Contract).filter(Contract.contract_no == contract.contract_no, Contract.is_deleted.is_(False)).first()
        if conflict:
            continue
        contract.is_deleted = False
        contract.updated_at = datetime.now()
        restored += 1
        log_action(db, contract.id, "restore", f"批量恢复合同 {contract.contract_no}", operator)
    db.commit()
    return {"message": f"已恢复 {restored} 条合同", "restored": restored}


def purge_contract(db: Session, contract: Contract) -> dict:
    if contract.pdf_path:
        pdf_file = _safe_upload_path(contract.pdf_path)
        if pdf_file.exists():
            pdf_file.unlink()
    db.delete(contract)
    db.commit()
    return {"message": f"已永久删除合同 {contract.contract_no}"}


def clear_recycle_bin(db: Session) -> dict:
    deleted = db.query(Contract).filter(Contract.is_deleted.is_(True)).all()
    count = len(deleted)
    for contract in deleted:
        if contract.pdf_path:
            pdf_file = _safe_upload_path(contract.pdf_path)
            if pdf_file.exists():
                pdf_file.unlink()
        db.delete(contract)
    db.commit()
    return {"message": f"已清空回收站（{count} 条）"}


def save_contract_pdf(db: Session, contract: Contract, upload: UploadFile, operator: str) -> dict:
    raw_name = _ensure_pdf_upload_name(upload)
    filename = ensure_pdf_filename(raw_name, sanitize_stem(contract.contract_no, "contract"))
    stored_name = _stored_pdf_name(filename)
    target = _safe_upload_path(stored_name)
    _write_limited_upload(upload, target)
    contract.pdf_path = stored_name
    contract.updated_at = datetime.now()
    db.commit()
    log_action(db, contract.id, "update", f"上传PDF: {filename}", operator)
    enqueue_event(db, "pdf", "contract.pdf_uploaded", contract.id, serialize_contract(contract))
    return {"message": "上传成功", "filename": public_name(stored_name)}


def download_contract_pdf(contract: Contract) -> FileResponse:
    if not contract.pdf_path:
        raise HTTPException(404, "PDF不存在")
    file_path = _safe_upload_path(contract.pdf_path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")

    if is_encrypted_file(file_path):
        settings = get_settings()
        if not settings.data_encryption_enabled:
            raise HTTPException(500, "PDF 已加密，但当前未启用数据加密配置")
        temp_dir = settings.data_dir / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp = tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=".pdf")
        temp_path = Path(temp.name)
        temp.close()
        try:
            decrypt_file(file_path, temp_path, settings.data_key)
        except CryptoError as exc:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(500, "PDF 解密失败，请检查数据加密密钥") from exc
        return FileResponse(
            temp_path,
            filename=public_name(contract.pdf_path),
            media_type="application/pdf",
            background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), temp_path),
        )

    return FileResponse(file_path, filename=public_name(contract.pdf_path), media_type="application/pdf")


def batch_upload_pdfs(db: Session, uploads: list[UploadFile], operator: str) -> dict:
    if len(uploads) > MAX_BATCH_PDF_COUNT:
        raise HTTPException(413, f"单次最多上传 {MAX_BATCH_PDF_COUNT} 个 PDF")
    for upload in uploads:
        if upload.filename:
            _ensure_pdf_upload_name(upload)

    contracts = db.query(Contract).filter(Contract.is_deleted.is_(False)).all()
    matched = []
    matched_contracts: list[Contract] = []
    unmatched = []
    total_bytes = 0
    written_paths: list[Path] = []

    for upload in uploads:
        if not upload.filename:
            continue
        filename = _ensure_pdf_upload_name(upload)
        basename = Path(filename).stem
        best_match = None
        best_score = 0
        for contract in contracts:
            if contract.tenant_name and contract.tenant_name.strip() in basename:
                score = len(contract.tenant_name.strip())
                if score > best_score:
                    best_match = contract
                    best_score = score
            if contract.contract_no and contract.contract_no in basename:
                score = len(contract.contract_no) + 1000
                if score > best_score:
                    best_match = contract
                    best_score = score
            if contract.room_no and contract.room_no in basename and len(contract.room_no) >= 3:
                score = len(contract.room_no)
                if score > best_score:
                    best_match = contract
                    best_score = score

        if best_match:
            save_name = _stored_pdf_name(ensure_pdf_filename(filename, sanitize_stem(best_match.contract_no, "contract")))
            target = _safe_upload_path(save_name)
            written = _write_limited_upload(upload, target)
            written_paths.append(target)
            if total_bytes + written > MAX_BATCH_PDF_TOTAL_SIZE:
                db.rollback()
                for path in written_paths:
                    path.unlink(missing_ok=True)
                raise HTTPException(413, f"单次上传总大小不能超过 {MAX_BATCH_PDF_TOTAL_SIZE // 1024 // 1024}MB")
            total_bytes += written
            best_match.pdf_path = save_name
            best_match.updated_at = datetime.now()
            log_action(db, best_match.id, "update", f"批量上传PDF匹配: {filename} → {best_match.contract_no}", operator, commit=False)
            matched.append({"filename": filename, "contract_no": best_match.contract_no, "tenant_name": best_match.tenant_name, "room_no": best_match.room_no})
            matched_contracts.append(best_match)
        else:
            temp_name = ensure_pdf_filename(filename, f"_unmatched_{sanitize_stem(basename, 'pdf')}")
            temp_path = _safe_upload_path(_stored_pdf_name(f"_unmatched_{temp_name}"))
            written = _write_limited_upload(upload, temp_path)
            written_paths.append(temp_path)
            if total_bytes + written > MAX_BATCH_PDF_TOTAL_SIZE:
                db.rollback()
                for path in written_paths:
                    path.unlink(missing_ok=True)
                raise HTTPException(413, f"单次上传总大小不能超过 {MAX_BATCH_PDF_TOTAL_SIZE // 1024 // 1024}MB")
            total_bytes += written
            unmatched.append({"filename": filename})

    try:
        db.commit()
    except Exception:
        db.rollback()
        for path in written_paths:
            path.unlink(missing_ok=True)
        raise
    for contract in matched_contracts:
        enqueue_event(db, "pdf", "contract.pdf_uploaded", contract.id, serialize_contract(contract))

    return {
        "total": len(uploads),
        "matched": matched,
        "matched_count": len(matched),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
    }


def manual_match_pdf(db: Session, filename: str, contract: Contract, operator: str) -> dict:
    safe_name = ensure_pdf_filename(filename, Path(filename).stem or "pdf")
    temp_path = _safe_upload_path(f"_unmatched_{safe_name}")
    encrypted_temp_path = _safe_upload_path(encrypted_name(f"_unmatched_{safe_name}"))
    if not temp_path.exists() and encrypted_temp_path.exists():
        temp_path = encrypted_temp_path
    if not temp_path.exists():
        raise HTTPException(404, "未匹配文件不存在")

    final_name = _stored_pdf_name(ensure_pdf_filename(filename, sanitize_stem(contract.contract_no, "contract")))
    final_path = _safe_upload_path(final_name)
    settings = get_settings()
    if settings.data_encryption_enabled and not is_encrypted_file(temp_path):
        encrypt_file(temp_path, final_path, settings.data_key, max_size=MAX_PDF_FILE_SIZE)
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(final_path)
    contract.pdf_path = final_name
    contract.updated_at = datetime.now()
    db.commit()
    log_action(db, contract.id, "update", f"手动匹配PDF: {filename} → {contract.contract_no}", operator)
    enqueue_event(db, "pdf", "contract.pdf_matched", contract.id, serialize_contract(contract))
    return {"message": "匹配成功", "contract_no": contract.contract_no}


def list_unmatched_pdfs() -> list[dict]:
    items = []
    seen = set()
    for path in sorted(get_settings().upload_dir.glob("_unmatched_*.pdf*")):
        filename = public_name(path.name.replace("_unmatched_", "", 1))
        if filename in seen:
            continue
        seen.add(filename)
        items.append({"filename": filename, "size": path.stat().st_size})
    return items


def list_missing_contract_pdfs(db: Session) -> list[dict]:
    today = date.today()
    contracts = (
        db.query(Contract)
        .filter(
            Contract.is_deleted.is_(False),
            or_(Contract.pdf_path.is_(None), Contract.pdf_path == ""),
            or_(
                Contract.start_date > today,
                and_(Contract.start_date <= today, Contract.end_date >= today),
                and_(Contract.end_date < today, Contract.disposal == "逾期占用"),
            ),
        )
        .order_by(Contract.end_date.asc())
        .all()
    )
    return [serialize_contract(contract) for contract in contracts]


def _normalize_header(value) -> str:
    if not value:
        return ""
    return str(value).strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")


def _match_header(value) -> tuple[str | None, bool]:
    normalized = _normalize_header(value)
    if not normalized:
        return None, False
    if normalized in FIELD_MAP:
        return FIELD_MAP[normalized], True
    for key, field in FIELD_MAP.items():
        if key.lower() == normalized.lower():
            return field, True
    for key, field in FIELD_MAP.items():
        if key in normalized or normalized in key:
            return field, True
    return None, False


def import_contracts_excel(content: bytes, db: Session, operator: str) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active

    header_row = None
    headers = {}
    matched_fields = []
    unmatched_headers = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=False), start=1):
        matched_count = 0
        candidate = {}
        for cell in row:
            normalized = _normalize_header(cell.value)
            if not normalized:
                continue
            field, matched = _match_header(normalized)
            if matched:
                matched_count += 1
                if field:
                    candidate[cell.column] = field
        if matched_count >= 3:
            header_row = row_idx
            headers = candidate
            for cell in row:
                normalized = _normalize_header(cell.value)
                if not normalized:
                    continue
                field, matched = _match_header(normalized)
                if matched and field:
                    matched_fields.append(f"{normalized} → {field}")
                elif matched:
                    matched_fields.append(f"{normalized} → (跳过, 自动计算)")
                else:
                    unmatched_headers.append(normalized)
            break

    if not header_row or not headers:
        raise HTTPException(400, "未找到有效表头。请确认 Excel 中包含合同编号、室号等列名。")

    skipped = 0
    errors = []
    fatal_errors = []
    pending_contracts: list[Contract] = []
    seen_contract_nos: set[str] = set()
    for row in ws.iter_rows(min_row=header_row + 1, values_only=False):
        row_data = {}
        for cell in row:
            if cell.column in headers:
                row_data[headers[cell.column]] = cell.value

        contract_no = str(row_data.get("contract_no") or "").strip()
        if not contract_no:
            continue
        if contract_no in seen_contract_nos:
            skipped += 1
            errors.append(f"合同 {contract_no} 在导入文件中重复，跳过")
            continue
        seen_contract_nos.add(contract_no)
        if db.query(Contract).filter(Contract.contract_no == contract_no).first():
            skipped += 1
            errors.append(f"合同 {contract_no} 已存在，跳过")
            continue

        try:
            data = {
                "contract_no": contract_no,
                "tower": str(row_data.get("tower") or ""),
                "room_no": str(row_data.get("room_no") or ""),
                "tenant_name": str(row_data.get("tenant_name") or ""),
                "area": float(row_data.get("area") or 0),
                "unit_price": float(row_data.get("unit_price") or 0),
                "lease_type": str(row_data.get("lease_type") or ""),
                "free_period": int(float(row_data.get("free_period") or 0)),
                "monthly_rent": float(row_data.get("monthly_rent") or 0),
                "broker_fee": float(row_data.get("broker_fee") or 0),
                "start_date": parse_date(row_data.get("start_date")),
                "end_date": parse_date(row_data.get("end_date")),
                "disposal": str(row_data.get("disposal") or ""),
                "contact_person": str(row_data.get("contact_person") or ""),
                "contact_phone": str(row_data.get("contact_phone") or ""),
                "contact_email": str(row_data.get("contact_email") or ""),
                "deposit": float(row_data.get("deposit") or 0),
                "deposit_status": str(row_data.get("deposit_status") or ""),
                "property_fee_rate": float(row_data.get("property_fee_rate") or 0),
                "property_fee_free": 0,
                "prev_contract_no": str(row_data.get("prev_contract_no") or ""),
                "notes": str(row_data.get("notes") or ""),
            }
            _validate_contract_values(data)
            overlapping = _find_overlapping_contract(
                db,
                room_no=str(data.get("room_no") or "").strip(),
                start_date=data.get("start_date"),
                end_date=data.get("end_date"),
            )
            if overlapping:
                raise HTTPException(400, f"室号 {data.get('room_no')} 在该租期内已关联合同 {overlapping.contract_no}")
            for pending in pending_contracts:
                if pending.room_no == data.get("room_no") and _periods_overlap(
                    pending.start_date,
                    pending.end_date,
                    data.get("start_date"),
                    data.get("end_date"),
                ):
                    raise HTTPException(400, f"室号 {data.get('room_no')} 在导入文件中存在租期重叠")
            pending_contracts.append(Contract(**data))
        except HTTPException as exc:
            fatal_errors.append(f"合同 {contract_no}: {exc.detail}")
        except Exception as exc:
            fatal_errors.append(f"合同 {contract_no}: {exc}")

    if fatal_errors:
        raise HTTPException(400, "Excel 存在无法导入的数据，未写入任何新合同：" + "；".join(fatal_errors[:10]))

    try:
        for contract in pending_contracts:
            db.add(contract)
        db.flush()
        for contract in pending_contracts:
            log_action(db, contract.id, "import", f"Excel导入: {contract.contract_no}", operator, commit=False)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Excel 导入失败，未写入任何新合同：{exc}") from exc

    imported = len(pending_contracts)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "matched_fields": matched_fields,
        "unmatched_headers": unmatched_headers,
        "header_row": header_row,
    }


def export_contracts_excel(contracts: list[Contract], keyword: str | None, tower: str | None, status: str | None) -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "租赁合同"

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(EXPORT_HEADERS))
    title = ws.cell(row=1, column=1, value=f"租赁合同明细表（{date.today().strftime('%Y年%m月%d日')}）")
    title.font = Font(bold=True, size=14)
    title.alignment = Alignment(horizontal="center")

    header_fill = PatternFill("solid", fgColor="3B82F6")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

    for col_idx, header in enumerate(EXPORT_HEADERS, 1):
        cell = ws.cell(row=2, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    for idx, contract in enumerate(contracts, 1):
        row_idx = idx + 2
        values = [
            idx,
            contract.contract_no,
            contract.tower,
            contract.room_no,
            contract.tenant_name,
            contract.area,
            contract.unit_price,
            contract.lease_type,
            contract.free_period,
            contract.monthly_rent,
            contract.broker_fee,
            contract.net_rent,
            contract.start_date.strftime("%Y-%m-%d") if contract.start_date else "",
            contract.end_date.strftime("%Y-%m-%d") if contract.end_date else "",
            contract.status,
            contract.disposal or "",
            contract.contact_person or "",
            contract.contact_phone or "",
            contract.deposit or 0,
            contract.deposit_status or "",
            contract.property_fee_rate or 0,
            contract.monthly_property_fee,
            contract.prev_contract_no or "",
            contract.notes or "",
        ]
        for col_idx, value in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = [6, 15, 8, 10, 15, 10, 10, 10, 8, 12, 12, 12, 12, 12, 10, 10, 10, 14, 12, 10, 10, 10, 15, 20]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    parts = ["租赁合同"]
    if tower:
        parts.append(tower)
    if status:
        parts.append(status)
    if keyword:
        parts.append(keyword)
    parts.append(date.today().strftime("%Y%m%d"))
    filename = "_".join(parts) + ".xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def export_contract_template() -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "导入模板"
    headers = ["合同编号", "楼栋/区域", "室号", "客户名称", "出租面积", "租金单价", "新租/续租", "免租期", "月租金", "中介佣金", "起租日", "终止日", "备注"]
    for idx, header in enumerate(headers, 1):
        ws.cell(row=1, column=idx, value=header).font = Font(bold=True)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"},
    )
