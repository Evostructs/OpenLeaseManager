from __future__ import annotations

from datetime import date, datetime
import io
import re
from typing import Iterable
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, selectinload

from app.core.time import now_in_timezone, today_in_timezone
from app.core.localization import bilingual_aliases, import_value
from app.core.excel_safety import sanitize_workbook
from app.core.workbook_localization import localize_workbook
from app.database import SessionLocal
from app.models import (
    Building,
    Contract,
    ContractDeposit,
    Customer,
    CustomerBuildingProfile,
    CustomerFollowUp,
)
from app.services.common import contract_room_summary, customer_display_name, deposit_status, dump_changes, log_action, serialize_deposit
from app.services.deposits import DEPOSIT_TYPES
from app.services.customers import (
    FOLLOW_UP_STATUSES,
    FOLLOW_UP_TYPES,
    active_customer_profiles,
    assert_customer_follow_up_scope,
    customer_master_log_scope,
    ensure_customer_building_profile,
    find_customer_by_exact_name,
    get_customer,
    selected_customer_contracts,
    selected_customer_predicate,
)
from app.services.detail_operations_scope import DetailBuildingScope, require_detail_write_scope
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import LEGACY_DEFAULT_BUILDING_CODE, SelectedPropertyScope
from app.services.rooms import list_rooms
from app.services.risks import list_risks
from app.services.towers import leasing_group


EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADER_FILL = PatternFill("solid", fgColor="3B82F6")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
PHONE_RE = re.compile(r"^[0-9+\-()\s]{5,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SYSTEM_NAME = "lease-manager"
NO_DATA_MESSAGE = "当前筛选条件下无数据"
FILENAME_MAX_TOKEN_LENGTH = 32
FILENAME_VALUE_ALIASES = {
    "南塔": "south-tower",
    "北塔": "north-tower",
    "南塔办公": "south-office",
    "北塔办公": "north-office",
    "商业配套": "commercial",
    "在租": "leased",
    "空置": "vacant",
    "即将到期": "expiring",
    "逾期占用": "overdue-occupancy",
    "不可用": "unavailable",
}


def _resolve_detail_write_scope(
    scope: SelectedPropertyScope,
    tab: str,
    *,
    db: Session | None = None,
) -> DetailBuildingScope:
    if db is not None:
        return require_detail_write_scope(db, scope, tab)
    session = SessionLocal()
    try:
        return require_detail_write_scope(session, scope, tab)
    finally:
        session.close()


def _require_customer_detail_write_scope(
    scope: SelectedPropertyScope,
    *,
    db: Session | None = None,
) -> tuple[int, str]:
    detail_scope = _resolve_detail_write_scope(scope, "customers", db=db)
    return detail_scope.building_id, detail_scope.building_code


def _require_deposit_detail_write_scope(
    scope: SelectedPropertyScope,
    *,
    db: Session | None = None,
) -> tuple[int, str]:
    detail_scope = _resolve_detail_write_scope(scope, "receivables", db=db)
    return detail_scope.building_id, detail_scope.building_code


def _selected_import_log_scope(db: Session, scope: SelectedPropertyScope) -> ObjectScope:
    building_id, _building_code = _require_customer_detail_write_scope(scope, db=db)
    return ObjectScope(scope_type="building", building_id=building_id)


def _sample_contract_no(detail_scope: DetailBuildingScope) -> str:
    rule = detail_scope.profile.contract_number_rule
    if not rule.supported or not rule.pattern:
        return "示例合同编号"
    try:
        return rule.pattern.format(year=2026, sequence=1)
    except (KeyError, ValueError):
        return f"{detail_scope.building_code}-2026-001"

CUSTOMER_IMPORT_ALIASES = {
    "客户名称": "name",
    "客户": "name",
    "企业名称": "name",
    "名称": "name",
    "name": "name",
    "客户简称": "short_name",
    "简称": "short_name",
    "short_name": "short_name",
    "统一社会信用代码": "credit_code",
    "信用代码": "credit_code",
    "credit_code": "credit_code",
    "客户类型": "customer_type",
    "类型": "customer_type",
    "customer_type": "customer_type",
    "行业": "industry",
    "industry": "industry",
    "联系人": "contact_name",
    "联系人姓名": "contact_name",
    "contact_name": "contact_name",
    "联系电话": "contact_phone",
    "手机号": "contact_phone",
    "电话": "contact_phone",
    "contact_phone": "contact_phone",
    "邮箱": "contact_email",
    "电子邮箱": "contact_email",
    "email": "contact_email",
    "contact_email": "contact_email",
    "地址": "address",
    "address": "address",
    "状态": "status",
    "status": "status",
    "备注": "notes",
    "notes": "notes",
    "楼宇联系人": "local_contact",
    "本地联系人": "local_contact",
    "local_contact": "local_contact",
    "楼宇备注": "local_notes",
    "本地备注": "local_notes",
    "local_notes": "local_notes",
    "楼宇负责人": "profile_owner",
    "客户负责人": "profile_owner",
    "profile_owner": "profile_owner",
    "owner": "profile_owner",
}

FOLLOW_UP_IMPORT_ALIASES = {
    "客户名称": "customer_name",
    "客户": "customer_name",
    "customer_name": "customer_name",
    "合同编号": "contract_no",
    "合同号": "contract_no",
    "contract_no": "contract_no",
    "跟进类型": "follow_up_type",
    "类型": "follow_up_type",
    "follow_up_type": "follow_up_type",
    "发生时间": "occurred_at",
    "跟进时间": "occurred_at",
    "occurred_at": "occurred_at",
    "跟进内容": "content",
    "内容": "content",
    "content": "content",
    "下一步": "next_action",
    "下一步动作": "next_action",
    "next_action": "next_action",
    "下次跟进日期": "next_follow_up_date",
    "下次跟进日": "next_follow_up_date",
    "next_follow_up_date": "next_follow_up_date",
    "负责人": "owner",
    "owner": "owner",
    "状态": "status",
    "status": "status",
}

DEPOSIT_IMPORT_ALIASES = {
    "合同编号": "contract_no",
    "合同号": "contract_no",
    "contract_no": "contract_no",
    "客户名称": "customer_name",
    "客户": "customer_name",
    "customer_name": "customer_name",
    "押金类型": "deposit_type",
    "保证金类型": "deposit_type",
    "deposit_type": "deposit_type",
    "应收金额": "expected_amount",
    "expected_amount": "expected_amount",
    "已收金额": "received_amount",
    "received_amount": "received_amount",
    "已退金额": "refunded_amount",
    "refunded_amount": "refunded_amount",
    "已冲抵金额": "offset_amount",
    "offset_amount": "offset_amount",
    "应收日期": "due_date",
    "due_date": "due_date",
    "收取日期": "received_date",
    "received_date": "received_date",
    "退还日期": "refund_date",
    "refund_date": "refund_date",
    "备注": "notes",
    "notes": "notes",
}

STATUS_LABELS = {"active": "在用", "inactive": "停用"}
FOLLOW_UP_STATUS_LABELS = {"open": "未完成", "done": "已完成"}
DEPOSIT_STATUS_LABELS = {
    "unpaid": "未收",
    "partial": "部分收取",
    "paid": "已收齐",
    "refunded": "已退还",
    "offset": "已冲抵",
    "closed": "已关闭",
}
RISK_TYPE_LABELS = {
    "overdue_receivable": "历史逾期",
    "contract_expiring": "90天内到期",
    "follow_up_overdue": "跟进逾期",
    "deposit_unpaid": "押金缺口",
    "attachment_missing": "附件缺失",
    "large_adjustment": "大额减免",
}
RISK_SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _safe_filename_token(value: object) -> str:
    text = FILENAME_VALUE_ALIASES.get(str(value or "").strip(), str(value or "").strip()).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:FILENAME_MAX_TOKEN_LENGTH].strip("-")


def _export_filename(export_key: str, filters: list[tuple[str, object]], extension: str = "xlsx") -> str:
    parts = [SYSTEM_NAME, _safe_filename_token(export_key)]
    for key, value in filters:
        if value in (None, ""):
            continue
        key_token = _safe_filename_token(key)
        value_token = _safe_filename_token(value)
        parts.append("-".join(item for item in (key_token, value_token or "filtered") if item))
    parts.append(today_in_timezone().strftime("%Y-%m-%d"))
    safe_parts = [part for part in parts if part]
    return "-".join(safe_parts) + f".{extension.lstrip('.')}"


def _content_disposition(filename: str) -> str:
    try:
        filename.encode("ascii")
        fallback = filename
    except UnicodeEncodeError:
        suffix_match = re.search(r"(\.[A-Za-z0-9]{1,8})$", filename)
        fallback = f"download{suffix_match.group(1) if suffix_match else ''}"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _xlsx_response(workbook: openpyxl.Workbook, filename: str) -> StreamingResponse:
    localize_workbook(workbook)
    sanitize_workbook(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type=EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


def _style_header_row(ws, row_idx: int = 1) -> None:
    for cell in ws[row_idx]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _autosize(ws, min_width: int = 10, max_width: int = 36) -> None:
    for column_idx, column in enumerate(ws.columns, start=1):
        values = [str(cell.value or "") for cell in column]
        width = min(max(max((len(value) for value in values), default=0) + 4, min_width), max_width)
        ws.column_dimensions[get_column_letter(column_idx)].width = width


def _make_workbook(
    sheet_name: str,
    headers: list[str],
    rows: list[list[object]],
    *,
    title: str | None = None,
    empty_message: str | None = None,
) -> openpyxl.Workbook:
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = sheet_name
    row_idx = 1
    if title:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        title_cell = ws.cell(row=1, column=1, value=title)
        title_cell.font = TITLE_FONT
        title_cell.alignment = Alignment(horizontal="center")
        row_idx = 2
    ws.append(headers)
    _style_header_row(ws, row_idx)
    for row in rows:
        ws.append(row)
    if not rows and empty_message:
        ws.append([empty_message] + [""] * max(len(headers) - 1, 0))
    _autosize(ws)
    return workbook


def _safe_metadata_value(key: str, value: object) -> str:
    text = _cell_text(value)
    if not text:
        return "全部"
    if re.search(r"(^|[_-])(authorization|bearer|cookie|password|token|secret|api_key|access_key)([_-]|$)", key, re.I):
        return "[已隐藏]"
    if re.search(r"bearer\s+|password\s*=|token\s*=|authorization\s*:", text, re.I):
        return "[已隐藏]"
    return text[:120]


def _filter_summary(filters: list[tuple[str, str, object]]) -> str:
    active = [f"{label}={_safe_metadata_value(key, value)}" for key, label, value in filters if value not in (None, "")]
    return "；".join(active) if active else "全部"


def _add_export_metadata(
    workbook: openpyxl.Workbook,
    *,
    export_type: str,
    filters: list[tuple[str, str, object]],
    row_count: int,
) -> None:
    ws = workbook.create_sheet("导出说明")
    rows = [
        ("系统", SYSTEM_NAME),
        ("导出类型", export_type),
        ("导出时间", now_in_timezone().strftime("%Y-%m-%d %H:%M:%S")),
        ("筛选条件", _filter_summary(filters)),
        ("数据行数", row_count),
    ]
    if row_count == 0:
        rows.append(("提示", NO_DATA_MESSAGE))
    for row in rows:
        ws.append(row)
    _style_header_row(ws)
    _autosize(ws, min_width=12, max_width=80)


def _tower_export_group(
    tower: object,
    *,
    floor: object = None,
    room_type: object = None,
    room_no: object = None,
    legacy_demo_a: bool = True,
) -> str:
    normalized_tower = str(tower or "").strip()
    if legacy_demo_a and normalized_tower in {"南塔", "北塔", "南塔裙楼", "商业配套"}:
        return leasing_group(
            normalized_tower,
            floor=floor,
            room_type=room_type,
            room_no=room_no,
        )
    if normalized_tower:
        return normalized_tower
    return "未标注"


def _legacy_demo_a_export_scope(db: Session, building_ids: Iterable[int] | None) -> bool:
    if building_ids is None:
        return True
    selected_ids = tuple(dict.fromkeys(int(item) for item in building_ids))
    codes = {
        str(code).casefold()
        for (code,) in db.query(Building.code).filter(Building.id.in_(selected_ids or (-1,))).all()
    }
    return codes == {LEGACY_DEFAULT_BUILDING_CODE.casefold()}


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _date_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _datetime_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d 00:00:00")
    return str(value).replace("T", " ")[:19]


def _money(value) -> float:
    return round(float(value or 0), 2)


def _normalize_header(value) -> str:
    return _cell_text(value).replace(" ", "").replace("\n", "")


def _find_header(ws, aliases: dict[str, str], *, min_matches: int = 2) -> tuple[int, dict[int, str]]:
    aliases = bilingual_aliases(aliases)
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=False), start=1):
        matched: dict[int, str] = {}
        for cell in row:
            key = _normalize_header(cell.value).casefold()
            if key in aliases:
                matched[cell.column] = aliases[key]
        if len(set(matched.values())) >= min_matches:
            return row_idx, matched
    raise HTTPException(400, "未找到有效表头，请下载模板后再导入。")


def _workbook_from_content(content: bytes):
    try:
        return openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(400, "Excel 文件无法读取，请确认文件格式为 xlsx/xls。") from exc


def _read_import_rows(content: bytes, aliases: dict[str, str], *, min_matches: int = 2) -> list[tuple[int, dict[str, object]]]:
    workbook = _workbook_from_content(content)
    ws = workbook.active
    header_row, headers = _find_header(ws, aliases, min_matches=min_matches)
    rows: list[tuple[int, dict[str, object]]] = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=False), start=header_row + 1):
        payload = {}
        has_value = False
        for cell in row:
            field = headers.get(cell.column)
            if not field:
                continue
            payload[field] = import_value(field, cell.value)
            if _cell_text(cell.value):
                has_value = True
        if has_value:
            rows.append((row_idx, payload))
    return rows


def _issue(row: int, field: str, reason: str, *, customer_name: str = "", value=None, hint: str | None = None) -> dict:
    item = {"row": row, "row_number": row, "field": field, "message": reason, "reason": reason}
    if customer_name:
        item["customer_name"] = customer_name
    if value not in (None, ""):
        item["value"] = _cell_text(value)
    if hint:
        item["hint"] = hint
    return item


def _import_preview_response(plan: dict) -> dict:
    return {
        "total_rows": plan["total_rows"],
        "valid_rows": plan["valid_rows"],
        "error_rows": plan["error_rows"],
        "warning_rows": plan["warning_rows"],
        "errors": plan["errors"],
        "warnings": plan["warnings"],
        "preview_rows": plan["preview_rows"],
    }


def _parse_date_field(value, *, row: int, field: str, errors: list[dict], required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            errors.append(_issue(row, field, "日期不能为空"))
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _cell_text(value)
    try:
        return datetime.fromisoformat(text.replace("/", "-")).date()
    except Exception:
        errors.append(_issue(row, field, f"{field} 日期格式应为 YYYY-MM-DD", value=value))
        return None


def _parse_datetime_field(value, *, row: int, field: str, errors: list[dict]) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _cell_text(value).replace("/", "-")
    try:
        return datetime.fromisoformat(text)
    except Exception:
        try:
            return datetime.fromisoformat(f"{text}T00:00:00")
        except Exception:
            errors.append(_issue(row, field, f"{field} 时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS", value=value))
            return None


def _parse_money_field(value, *, row: int, field: str, errors: list[dict]) -> float:
    if value in (None, ""):
        return 0.0
    try:
        amount = round(float(value), 2)
    except Exception:
        errors.append(_issue(row, field, f"{field} 金额格式错误", value=value))
        return 0.0
    if amount < 0:
        errors.append(_issue(row, field, f"{field} 金额不能为负数", value=value))
        return 0.0
    return amount


def _validate_phone_email(row: int, data: dict, errors: list[dict]) -> None:
    phone = _cell_text(data.get("contact_phone"))
    email = _cell_text(data.get("contact_email"))
    if phone and not PHONE_RE.match(phone):
        errors.append(_issue(row, "contact_phone", "联系电话格式错误", value=phone))
    if email and not EMAIL_RE.match(email):
        errors.append(_issue(row, "contact_email", "邮箱格式错误", value=email))


def _log_import_result(
    db: Session,
    action: str,
    import_type: str,
    succeeded: int,
    failed: int,
    operator: str,
    target_type: str,
    scope: SelectedPropertyScope,
) -> None:
    if succeeded <= 0:
        return
    log_action(
        db,
        None,
        action,
        dump_changes({"import_type": import_type, "succeeded": succeeded, "failed": failed}),
        operator,
        commit=False,
        target_type=target_type,
        object_scope=_selected_import_log_scope(db, scope),
    )


def export_customers_excel(
    db: Session,
    keyword: str | None = None,
    status: str | None = None,
    *,
    scope: SelectedPropertyScope,
) -> StreamingResponse:
    query = db.query(Customer).options(selectinload(Customer.contracts)).filter(selected_customer_predicate(scope))
    if keyword:
        like = f"%{keyword.strip()}%"
        today = today_in_timezone()
        profile_match = exists().where(
            CustomerBuildingProfile.customer_id == Customer.id,
            CustomerBuildingProfile.building_id.in_(scope.building_ids or (-1,)),
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
    customers = query.order_by(Customer.status.asc(), Customer.name.asc(), Customer.id.asc()).all()
    profiles_by_customer = active_customer_profiles(
        db,
        customer_ids=[int(customer.id) for customer in customers],
        building_ids=scope.building_ids,
    )
    selected_building_id = (
        int(scope.building_ids[0])
        if scope.scope_type == "building" and len(scope.building_ids) == 1
        else None
    )
    selected_building_code = (
        str(scope.building_codes[0])
        if selected_building_id is not None and scope.building_codes
        else scope.scope_code
    )
    rows = [
        [
            customer.name or "",
            customer.short_name or "",
            customer.credit_code or "",
            customer.customer_type or "",
            customer.industry or "",
            customer.contact_name or "",
            customer.contact_phone or "",
            customer.contact_email or "",
            customer.address or "",
            STATUS_LABELS.get(customer.status or "active", customer.status or ""),
            len(selected_customer_contracts(customer, scope)),
            _datetime_text(customer.updated_at),
            customer.notes or "",
            selected_building_code,
            next(
                (
                    profile.local_contact or ""
                    for profile in profiles_by_customer.get(int(customer.id), [])
                    if int(profile.building_id) == selected_building_id
                ),
                "",
            ),
            next(
                (
                    profile.owner or ""
                    for profile in profiles_by_customer.get(int(customer.id), [])
                    if int(profile.building_id) == selected_building_id
                ),
                "",
            ),
            next(
                (
                    profile.local_notes or ""
                    for profile in profiles_by_customer.get(int(customer.id), [])
                    if int(profile.building_id) == selected_building_id
                ),
                "",
            ),
        ]
        for customer in customers
    ]
    workbook = _make_workbook(
        "客户清单",
        [
            "客户名称",
            "简称",
            "信用代码",
            "客户类型",
            "行业",
            "联系人",
            "联系电话",
            "邮箱",
            "地址",
            "状态",
            "合同数",
            "更新时间",
            "备注",
            "楼宇范围",
            "楼宇联系人",
            "楼宇负责人",
            "楼宇备注",
        ],
        rows,
        title=f"客户清单 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("keyword", "关键词", keyword),
        ("status", "状态", STATUS_LABELS.get(status, status) if status else None),
    ]
    _add_export_metadata(workbook, export_type="客户清单", filters=filters, row_count=len(rows))
    return _xlsx_response(workbook, _export_filename("customers", [("status", status), ("keyword", keyword)]))


def export_follow_ups_excel(
    db: Session,
    *,
    customer_id: int | None = None,
    keyword: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    scope: SelectedPropertyScope,
) -> StreamingResponse:
    query = (
        db.query(CustomerFollowUp)
        .options(selectinload(CustomerFollowUp.customer), selectinload(CustomerFollowUp.contract))
        .filter(CustomerFollowUp.building_id.in_(scope.building_ids or (-1,)))
    )
    if customer_id:
        get_customer(db, int(customer_id), scope=scope)
        query = query.filter(CustomerFollowUp.customer_id == customer_id)
    if status:
        query = query.filter(CustomerFollowUp.status == status)
    if owner:
        query = query.filter(CustomerFollowUp.owner == owner)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.join(Customer).filter(
            or_(
                Customer.name.like(like),
                Customer.short_name.like(like),
                Customer.credit_code.like(like),
                CustomerFollowUp.content.like(like),
                CustomerFollowUp.next_action.like(like),
                CustomerFollowUp.owner.like(like),
            )
        )
    follow_ups = query.order_by(CustomerFollowUp.occurred_at.desc(), CustomerFollowUp.id.desc()).all()
    rows = []
    for follow_up in follow_ups:
        assert_customer_follow_up_scope(follow_up, scope)
        customer = getattr(follow_up, "customer", None)
        contract = getattr(follow_up, "contract", None)
        rows.append(
            [
                customer.name if customer else "",
                contract.contract_no if contract else "",
                follow_up.follow_up_type or "",
                _datetime_text(follow_up.occurred_at),
                follow_up.content or "",
                follow_up.next_action or "",
                _date_text(follow_up.next_follow_up_date),
                follow_up.owner or "",
                FOLLOW_UP_STATUS_LABELS.get(follow_up.status or "open", follow_up.status or ""),
                _datetime_text(follow_up.updated_at),
            ]
        )
    workbook = _make_workbook(
        "客户跟进记录",
        ["客户名称", "合同编号", "跟进类型", "发生时间", "跟进内容", "下一步", "下次跟进日期", "负责人", "状态", "更新时间"],
        rows,
        title=f"客户跟进记录 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("customer_id", "客户ID", customer_id),
        ("keyword", "关键词", keyword),
        ("status", "状态", FOLLOW_UP_STATUS_LABELS.get(status, status) if status else None),
        ("owner", "负责人", owner),
    ]
    _add_export_metadata(workbook, export_type="客户跟进记录", filters=filters, row_count=len(rows))
    return _xlsx_response(
        workbook,
        _export_filename(
            "customer-follow-ups",
            [("customer", customer_id), ("status", status), ("owner", owner), ("keyword", keyword)],
        ),
    )


def export_deposits_excel(
    db: Session,
    *,
    keyword: str | None = None,
    status: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    building_ids: Iterable[int] | None = None,
) -> StreamingResponse:
    selected_building_ids = None if building_ids is None else tuple(dict.fromkeys(int(item) for item in building_ids))
    legacy_demo_a = _legacy_demo_a_export_scope(db, selected_building_ids)
    query = (
        db.query(ContractDeposit)
        .join(Contract)
        .options(selectinload(ContractDeposit.contract).selectinload(Contract.customer), selectinload(ContractDeposit.customer))
        .filter(Contract.is_deleted.is_(False))
    )
    if selected_building_ids is not None:
        query = query.filter(Contract.building_id.in_(selected_building_ids or (-1,)))
    if contract_id:
        query = query.filter(ContractDeposit.contract_id == contract_id)
    if customer_id:
        query = query.filter(ContractDeposit.customer_id == customer_id)
    if status:
        query = query.filter(ContractDeposit.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(or_(Contract.contract_no.like(like), Contract.tenant_name.like(like), Contract.room_no.like(like), Contract.customer.has(Customer.name.like(like))))
    deposits = query.order_by(Contract.contract_no.asc(), ContractDeposit.id.asc()).all()
    rows = []
    for deposit in deposits:
        contract = getattr(deposit, "contract", None)
        row = serialize_deposit(deposit)
        summary = contract_room_summary(contract) if contract else {}
        rows.append(
            [
                contract.contract_no if contract else "",
                customer_display_name(contract) if contract else "",
                summary.get("room_no", ""),
                _tower_export_group(
                    summary.get("tower", ""),
                    room_no=summary.get("room_no", ""),
                    legacy_demo_a=legacy_demo_a,
                ),
                row["deposit_type"],
                row["expected_amount"],
                row["received_amount"],
                row["unpaid_amount"],
                row["refunded_amount"],
                row["offset_amount"],
                row["available_amount"],
                DEPOSIT_STATUS_LABELS.get(row["status"], row["status"]),
                _date_text(row["due_date"]),
                _date_text(row["received_date"]),
                _date_text(row["refund_date"]),
                row["notes"],
            ]
        )
    workbook = _make_workbook(
        "押金台账",
        ["合同编号", "客户名称", "室号", "塔楼", "押金类型", "应收金额", "已收金额", "未收金额", "已退金额", "已冲抵金额", "可用余额", "押金状态", "应收日期", "收取日期", "退还日期", "备注"],
        rows,
        title=f"押金台账 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("keyword", "关键词", keyword),
        ("status", "押金状态", DEPOSIT_STATUS_LABELS.get(status, status) if status else None),
        ("contract_id", "合同ID", contract_id),
        ("customer_id", "客户ID", customer_id),
    ]
    _add_export_metadata(workbook, export_type="押金台账", filters=filters, row_count=len(rows))
    return _xlsx_response(
        workbook,
        _export_filename(
            "contract-deposits",
            [("status", status), ("contract", contract_id), ("customer", customer_id), ("keyword", keyword)],
        ),
    )


def export_risks_excel(
    db: Session,
    *,
    risk_type: str | None = None,
    severity: str | None = None,
    entity_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    building_ids: Iterable[int] | None = None,
) -> StreamingResponse:
    legacy_demo_a = _legacy_demo_a_export_scope(db, building_ids)
    data = list_risks(
        db,
        risk_type=risk_type,
        severity=severity,
        entity_type=entity_type,
        contract_id=contract_id,
        customer_id=customer_id,
        limit=5000,
        building_ids=building_ids,
    )
    rows = [
        [
            RISK_TYPE_LABELS.get(item.get("risk_type"), item.get("risk_type", "")),
            RISK_SEVERITY_LABELS.get(item.get("severity"), item.get("severity", "")),
            item.get("title", ""),
            item.get("description", ""),
            item.get("contract_no", ""),
            item.get("customer_name", ""),
            item.get("room_no", ""),
            _tower_export_group(
                item.get("tower", ""),
                room_no=item.get("room_no", ""),
                legacy_demo_a=legacy_demo_a,
            ),
            item.get("amount"),
            item.get("due_date", ""),
            item.get("occurred_on", ""),
            item.get("action_label", ""),
            item.get("stable_key", ""),
        ]
        for item in data["items"]
    ]
    workbook = _make_workbook(
        "风险清单",
        ["风险类型", "级别", "标题", "说明", "合同编号", "客户名称", "室号", "塔楼", "金额", "到期日期", "发生日期", "建议动作", "风险键"],
        rows,
        title=f"风险清单 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("risk_type", "风险类型", RISK_TYPE_LABELS.get(risk_type, risk_type) if risk_type else None),
        ("severity", "级别", RISK_SEVERITY_LABELS.get(severity, severity) if severity else None),
        ("entity_type", "对象类型", entity_type),
        ("contract_id", "合同ID", contract_id),
        ("customer_id", "客户ID", customer_id),
    ]
    _add_export_metadata(workbook, export_type="风险清单", filters=filters, row_count=len(rows))
    return _xlsx_response(
        workbook,
        _export_filename(
            "risks",
            [("type", risk_type), ("severity", severity), ("entity", entity_type), ("contract", contract_id), ("customer", customer_id)],
        ),
    )


def export_room_operations_excel(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    tower: str | None = None,
    status: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    floor: str | None = None,
) -> StreamingResponse:
    rooms = list_rooms(db, start_date, end_date, tower=tower, status=status, min_area=min_area, max_area=max_area, floor=floor, include_operations=True)
    rows = []
    for room in rooms:
        contract = room.get("contract") or {}
        rows.append(
            [
                _tower_export_group(room.get("tower", ""), floor=room.get("floor", ""), room_type=room.get("room_type", ""), room_no=room.get("room_no", "")),
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
        ["塔楼", "楼层", "室号", "类型", "面积", "经营状态", "客户名称", "合同编号", "起租日", "到期日", "月租金", "历史逾期金额", "累计未收", "下次跟进", "资料缺失", "备注"],
        rows,
        title=f"房源经营清单 {today_in_timezone().strftime('%Y-%m-%d')}",
        empty_message=NO_DATA_MESSAGE,
    )
    filters = [
        ("start_date", "开始日期", _date_text(start_date) if start_date else None),
        ("end_date", "结束日期", _date_text(end_date) if end_date else None),
        ("tower", "塔楼", _tower_export_group(tower) if tower else None),
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
                ("tower", _tower_export_group(tower) if tower else None),
                ("status", status),
                ("floor", floor),
            ],
        ),
    )


def customer_import_template(
    *,
    scope: SelectedPropertyScope,
    db: Session | None = None,
) -> StreamingResponse:
    detail_scope = _resolve_detail_write_scope(scope, "customers", db=db)
    building_code = detail_scope.building_code
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "客户导入模板"
    headers = [
        "客户名称",
        "简称",
        "统一社会信用代码",
        "客户类型",
        "行业",
        "联系人",
        "联系电话",
        "邮箱",
        "地址",
        "状态",
        "备注",
        "楼宇联系人",
        "楼宇负责人",
        "楼宇备注",
    ]
    ws.append(headers)
    _style_header_row(ws)
    ws.append(
        [
            "示例科技有限公司",
            "示例科技",
            "91310000EXAMPLE001",
            "企业",
            "科技服务",
            "法定资料联系人",
            "13800000000",
            "contact@example.com",
            "上海市",
            "active",
            "共享主档备注",
            "张三 / 13800000000",
            "Alice",
            "仅属于当前导入楼宇的运营备注",
        ]
    )
    note = workbook.create_sheet("导入说明")
    note.append(["规则", "说明"])
    note.append(["导入楼宇", f"本模板仅用于 {building_code}；上传时仍按当前 URL 楼宇重新校验。"])
    note.append(["客户名称", "按客户名称完全一致匹配；名称相同则更新主档，名称不存在则新增。"])
    note.append(["信用代码", "仅作为辅助匹配；如果信用代码已属于不同客户，不会自动合并。"])
    note.append(["手机号/邮箱", "格式错误会返回行号和字段名。"])
    note.append(["共享与本地", "客户名称、信用代码等为共享主档；楼宇联系人、楼宇负责人和楼宇备注只写入当前楼宇。"])
    _style_header_row(note)
    _autosize(ws)
    _autosize(note)
    return _xlsx_response(workbook, "客户导入模板.xlsx")


def follow_up_import_template(
    *,
    scope: SelectedPropertyScope,
    db: Session | None = None,
) -> StreamingResponse:
    detail_scope = _resolve_detail_write_scope(scope, "customers", db=db)
    building_code = detail_scope.building_code
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "跟进记录导入模板"
    headers = ["客户名称", "合同编号", "跟进类型", "发生时间", "跟进内容", "下一步", "下次跟进日期", "负责人", "状态"]
    ws.append(headers)
    _style_header_row(ws)
    ws.append(["示例科技有限公司", _sample_contract_no(detail_scope), "电话", "2026-05-19 10:00:00", "确认续租意向", "下周发送报价", "2026-05-26", "Alice", "open"])
    note = workbook.create_sheet("导入说明")
    note.append(["规则", "说明"])
    note.append(["导入楼宇", f"本模板仅用于 {building_code}；上传时仍按当前 URL 楼宇重新校验。"])
    note.append(["客户名称", "必须完全匹配客户主档；匹配不到会返回行号和客户名称。"])
    note.append(["合同编号", "可留空；填写时必须存在，且应属于对应客户。"])
    note.append(["状态", "可填 open 或 done。"])
    _style_header_row(note)
    _autosize(ws)
    _autosize(note)
    return _xlsx_response(workbook, "客户跟进记录导入模板.xlsx")


def deposit_import_template(
    *,
    scope: SelectedPropertyScope,
    db: Session | None = None,
) -> StreamingResponse:
    detail_scope = _resolve_detail_write_scope(scope, "receivables", db=db)
    building_code = detail_scope.building_code
    workbook = _make_workbook(
        "押金导入模板",
        ["合同编号", "客户名称", "押金类型", "应收金额", "已收金额", "已退金额", "已冲抵金额", "应收日期", "收取日期", "退还日期", "备注"],
        [[_sample_contract_no(detail_scope), "示例科技有限公司", "租赁押金", 30000, 0, 0, 0, "2026-05-19", "", "", "首期押金"]],
    )
    note = workbook.create_sheet("导入说明")
    note.append(["规则", "说明"])
    note.append(["导入楼宇", f"本模板仅用于 {building_code}；上传时仍按当前 URL 楼宇重新校验。"])
    note.append(["原子性", "任一合同不存在、越权或任一行字段错误时，整份文件均不写入。"])
    note.append(["金额", "金额不得为负数；已退金额与已冲抵金额合计不得超过已收金额。"])
    _style_header_row(note)
    _autosize(note)
    return _xlsx_response(workbook, "押金导入模板.xlsx")


def _customer_import_plan(
    content: bytes,
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id, _building_code = _require_customer_detail_write_scope(scope, db=db)
    rows = _read_import_rows(content, CUSTOMER_IMPORT_ALIASES)
    existing_by_code = {
        customer.credit_code: customer
        for customer in db.query(Customer).filter(Customer.credit_code.isnot(None), Customer.credit_code != "").all()
    }
    errors: list[dict] = []
    warnings: list[dict] = []
    prepared: list[tuple[Customer | None, dict, dict]] = []
    preview_rows: list[dict] = []
    seen_names: set[str] = set()

    for row_idx, raw in rows:
        row_errors: list[dict] = []
        row_warnings: list[dict] = []
        data = {field: _cell_text(value) for field, value in raw.items()}
        present_fields = set(data)
        name = data.get("name", "")
        if not name:
            row_errors.append(_issue(row_idx, "name", "客户名称不能为空"))
        elif name in seen_names:
            row_errors.append(_issue(row_idx, "name", "导入文件中客户名称重复", customer_name=name, value=name))
        if name:
            seen_names.add(name)

        status = data.get("status") or "active"
        if status not in {"active", "inactive"}:
            row_errors.append(_issue(row_idx, "status", "状态只能为 active 或 inactive", customer_name=name, value=status))
        data["status"] = status
        _validate_phone_email(row_idx, data, row_errors)

        existing = find_customer_by_exact_name(db, name) if name else None
        if existing:
            row_warnings.append(_issue(row_idx, "name", "客户已存在，正式导入将更新该客户的资料", customer_name=name, value=name, hint="update"))
        credit_code = data.get("credit_code", "")
        if credit_code:
            code_match = existing_by_code.get(credit_code)
            if code_match and (not existing or code_match.id != existing.id):
                row_errors.append(_issue(row_idx, "credit_code", f"信用代码已属于客户 {code_match.name}，未自动合并", customer_name=name, value=credit_code))
            if existing and existing.credit_code and existing.credit_code != credit_code:
                row_errors.append(_issue(row_idx, "credit_code", "同名客户已有不同信用代码", customer_name=name, value=credit_code))

        if row_errors:
            errors.extend(row_errors)
            warnings.extend(row_warnings)
            continue
        warnings.extend(row_warnings)
        clean_master_data = {
            key: data[key]
            for key in (
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
            )
            if key in present_fields
        }
        clean_master_data["name"] = name
        if existing is None:
            clean_master_data.setdefault("status", status)
        # Legacy customer workbooks do not contain building-local columns.  In
        # that case keep the existing profile intact instead of silently
        # replacing it with empty values.
        clean_profile_data: dict[str, str] = {}
        if "local_contact" in data:
            clean_profile_data["local_contact"] = data["local_contact"]
        if "local_notes" in data:
            clean_profile_data["local_notes"] = data["local_notes"]
        if "profile_owner" in data:
            clean_profile_data["owner"] = data["profile_owner"]
        prepared.append((existing, clean_master_data, clean_profile_data))
        preview_rows.append(
            {
                "row_number": row_idx,
                "action": "update" if existing else "create",
                "name": clean_master_data["name"],
                "short_name": clean_master_data.get(
                    "short_name",
                    existing.short_name if existing is not None else "",
                ),
                "credit_code": clean_master_data.get(
                    "credit_code",
                    existing.credit_code if existing is not None else "",
                ),
                "status": clean_master_data.get(
                    "status",
                    existing.status if existing is not None else "active",
                ),
                "local_contact": clean_profile_data.get("local_contact", ""),
                "owner": clean_profile_data.get("owner", ""),
            }
        )

    error_row_numbers = {error["row_number"] for error in errors}
    warning_row_numbers = {warning["row_number"] for warning in warnings}
    return {
        "total_rows": len(rows),
        "valid_rows": len(rows) - len(error_row_numbers),
        "error_rows": len(error_row_numbers),
        "warning_rows": len(warning_row_numbers),
        "errors": errors,
        "warnings": warnings,
        "preview_rows": preview_rows[:20],
        "prepared": prepared,
        "building_id": building_id,
    }


def preview_customers_excel(
    content: bytes,
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    return _import_preview_response(_customer_import_plan(content, db, scope=scope))


def import_customers_excel(
    content: bytes,
    db: Session,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    plan = _customer_import_plan(content, db, scope=scope)
    errors = plan["errors"]
    prepared = plan["prepared"]
    building_id = int(plan["building_id"])

    imported = 0
    master_changed = 0
    profile_changed = 0
    affected_customer_ids: list[int] = []
    try:
        for existing, master_data, profile_data in prepared:
            if existing:
                row_master_changed = any(
                    str(getattr(existing, key) or "") != str(value or "")
                    for key, value in master_data.items()
                )
                for key, value in master_data.items():
                    setattr(existing, key, value)
                if row_master_changed:
                    existing.updated_at = now_in_timezone()
                customer = existing
            else:
                row_master_changed = True
                customer = Customer(**master_data)
                db.add(customer)
                db.flush()
            profile = ensure_customer_building_profile(db, customer.id, building_id)
            row_profile_changed = profile.id is None or any(
                str(getattr(profile, key) or "") != str(value or "")
                for key, value in profile_data.items()
            )
            for key, value in profile_data.items():
                setattr(profile, key, value)
            if row_profile_changed:
                profile.updated_at = now_in_timezone()
                profile_changed += 1
            if row_master_changed:
                master_changed += 1
            affected_customer_ids.append(int(customer.id))
            imported += 1
        _log_import_result(
            db,
            "customer_import",
            "客户主档导入",
            imported,
            len({error["row"] for error in errors}),
            operator,
            "customer",
            scope,
        )
        if profile_changed:
            _log_import_result(
                db,
                "customer_profile_import",
                "楼宇客户资料导入",
                profile_changed,
                len({error["row"] for error in errors}),
                operator,
                "customer",
                scope,
            )
        if master_changed:
            log_action(
                db,
                None,
                "customer_master_import",
                dump_changes(
                    {
                        "import_type": "共享客户主档导入",
                        "succeeded": master_changed,
                        "failed": len({error["row"] for error in errors}),
                    }
                ),
                operator,
                commit=False,
                target_type="customer",
                object_scope=customer_master_log_scope(
                    db,
                    customer_ids=affected_customer_ids,
                    selected_building_ids=(building_id,),
                ),
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"客户导入失败，未写入数据：{exc}") from exc

    return {
        "imported": imported,
        "master_changed": master_changed,
        "profile_changed": profile_changed,
        "failed": len({error["row"] for error in errors}),
        "errors": errors,
    }


def _follow_up_import_plan(
    content: bytes,
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id, _building_code = _require_customer_detail_write_scope(scope, db=db)
    rows = _read_import_rows(content, FOLLOW_UP_IMPORT_ALIASES)
    errors: list[dict] = []
    warnings: list[dict] = []
    prepared: list[CustomerFollowUp] = []
    preview_rows: list[dict] = []
    fatal_scope_errors: list[dict] = []

    for row_idx, raw in rows:
        row_errors: list[dict] = []
        row_warnings: list[dict] = []
        data = {field: _cell_text(value) for field, value in raw.items()}
        customer_name = data.get("customer_name", "")
        customer = (
            find_customer_by_exact_name(db, customer_name, building_ids=(building_id,))
            if customer_name
            else None
        )
        if not customer_name:
            row_errors.append(_issue(row_idx, "customer_name", "客户名称不能为空"))
        elif not customer:
            issue = _issue(
                row_idx,
                "customer_name",
                "客户名称未匹配到当前楼宇客户主档",
                customer_name=customer_name,
                value=customer_name,
            )
            row_errors.append(issue)

        contract_id = None
        contract_no = data.get("contract_no", "")
        if contract_no:
            contract = db.query(Contract).filter(Contract.contract_no == contract_no, Contract.is_deleted.is_(False)).first()
            if not contract or contract.building_id != building_id:
                issue = _issue(
                    row_idx,
                    "contract_no",
                    "合同编号不存在或不属于当前楼宇",
                    customer_name=customer_name,
                    value=contract_no,
                )
                row_errors.append(issue)
                fatal_scope_errors.append(issue)
            elif not customer or contract.customer_id != customer.id:
                issue = _issue(
                    row_idx,
                    "contract_no",
                    "合同与客户不匹配",
                    customer_name=customer_name,
                    value=contract_no,
                )
                row_errors.append(issue)
                fatal_scope_errors.append(issue)
            else:
                contract_id = contract.id

        follow_up_type = data.get("follow_up_type") or "电话"
        if follow_up_type not in FOLLOW_UP_TYPES:
            row_errors.append(_issue(row_idx, "follow_up_type", "跟进类型无效", customer_name=customer_name, value=follow_up_type))
        status = data.get("status") or "open"
        if status not in FOLLOW_UP_STATUSES:
            row_errors.append(_issue(row_idx, "status", "状态只能为 open 或 done", customer_name=customer_name, value=status))
        content_text = data.get("content", "")
        if not content_text:
            row_errors.append(_issue(row_idx, "content", "跟进内容不能为空", customer_name=customer_name))

        raw_occurred_at = raw.get("occurred_at")
        if raw_occurred_at in (None, ""):
            row_warnings.append(_issue(row_idx, "occurred_at", "发生时间为空，正式导入将使用当前时间", customer_name=customer_name, hint="default_now"))
        occurred_at = _parse_datetime_field(raw_occurred_at, row=row_idx, field="occurred_at", errors=row_errors) or now_in_timezone()
        next_follow_up_date = _parse_date_field(raw.get("next_follow_up_date"), row=row_idx, field="next_follow_up_date", errors=row_errors)

        if row_errors:
            errors.extend(row_errors)
            warnings.extend(row_warnings)
            continue
        warnings.extend(row_warnings)
        follow_up_data = {
            "customer_id": customer.id,
            "contract_id": contract_id,
            "building_id": building_id,
            "follow_up_type": follow_up_type,
            "occurred_at": occurred_at,
            "content": content_text,
            "next_action": data.get("next_action", ""),
            "next_follow_up_date": next_follow_up_date,
            "owner": data.get("owner", ""),
            "status": status,
        }
        prepared.append(CustomerFollowUp(**follow_up_data))
        preview_rows.append(
            {
                "row_number": row_idx,
                "customer_name": customer_name,
                "contract_no": contract_no,
                "follow_up_type": follow_up_type,
                "occurred_at": _datetime_text(occurred_at),
                "content": content_text,
                "owner": data.get("owner", ""),
                "status": status,
            }
        )

    error_row_numbers = {error["row_number"] for error in errors}
    warning_row_numbers = {warning["row_number"] for warning in warnings}
    return {
        "total_rows": len(rows),
        "valid_rows": len(rows) - len(error_row_numbers),
        "error_rows": len(error_row_numbers),
        "warning_rows": len(warning_row_numbers),
        "errors": errors,
        "warnings": warnings,
        "preview_rows": preview_rows[:20],
        "prepared": prepared,
        "fatal_scope_errors": fatal_scope_errors,
        "building_id": building_id,
    }


def preview_follow_ups_excel(
    content: bytes,
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    return _import_preview_response(_follow_up_import_plan(content, db, scope=scope))


def import_follow_ups_excel(
    content: bytes,
    db: Session,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    plan = _follow_up_import_plan(content, db, scope=scope)
    errors = plan["errors"]
    prepared = plan["prepared"]
    fatal_scope_errors = plan["fatal_scope_errors"]
    building_id = int(plan["building_id"])

    if fatal_scope_errors:
        raise HTTPException(
            400,
            {
                "message": "跟进导入包含无效或跨楼对象，未写入任何跟进记录",
                "errors": fatal_scope_errors,
            },
        )

    imported = 0
    try:
        for follow_up in prepared:
            db.add(follow_up)
            if follow_up.contract_id is None:
                ensure_customer_building_profile(db, follow_up.customer_id, building_id)
            imported += 1
        _log_import_result(
            db,
            "followup_import",
            "客户跟进记录导入",
            imported,
            len({error["row"] for error in errors}),
            operator,
            "follow_up",
            scope,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"客户跟进导入失败，未写入数据：{exc}") from exc

    return {"imported": imported, "failed": len({error["row"] for error in errors}), "errors": errors}


def _deposit_import_plan(
    content: bytes,
    db: Session,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id, building_code = _require_deposit_detail_write_scope(scope, db=db)
    rows = _read_import_rows(content, DEPOSIT_IMPORT_ALIASES, min_matches=2)
    if not rows:
        raise HTTPException(400, "押金导入文件没有可处理的数据行")

    contract_numbers = {
        _cell_text(raw.get("contract_no"))
        for _row_idx, raw in rows
        if _cell_text(raw.get("contract_no"))
    }
    selected_contracts = (
        db.query(Contract)
        .options(selectinload(Contract.customer))
        .filter(
            Contract.contract_no.in_(contract_numbers or {"__none__"}),
            Contract.building_id == building_id,
            Contract.is_deleted.is_(False),
        )
        .all()
    )
    contracts_by_no = {str(contract.contract_no): contract for contract in selected_contracts}
    errors: list[dict] = []
    prepared: list[ContractDeposit] = []

    for row_idx, raw in rows:
        row_errors: list[dict] = []
        contract_no = _cell_text(raw.get("contract_no"))
        contract = contracts_by_no.get(contract_no)
        if not contract_no:
            row_errors.append(_issue(row_idx, "contract_no", "合同编号不能为空"))
        elif contract is None:
            row_errors.append(
                _issue(
                    row_idx,
                    "contract_no",
                    "合同不存在或无权访问",
                    value=contract_no,
                )
            )

        customer_name = _cell_text(raw.get("customer_name"))
        if contract is not None and customer_name and customer_name != customer_display_name(contract).strip():
            row_errors.append(
                _issue(
                    row_idx,
                    "customer_name",
                    "客户名称与合同不一致",
                    customer_name=customer_name,
                    value=customer_name,
                )
            )

        deposit_type = _cell_text(raw.get("deposit_type")) or "租赁押金"
        if deposit_type not in DEPOSIT_TYPES:
            row_errors.append(
                _issue(
                    row_idx,
                    "deposit_type",
                    "押金类型无效",
                    value=deposit_type,
                )
            )
        expected_amount = _parse_money_field(
            raw.get("expected_amount"),
            row=row_idx,
            field="expected_amount",
            errors=row_errors,
        )
        received_amount = _parse_money_field(
            raw.get("received_amount"),
            row=row_idx,
            field="received_amount",
            errors=row_errors,
        )
        refunded_amount = _parse_money_field(
            raw.get("refunded_amount"),
            row=row_idx,
            field="refunded_amount",
            errors=row_errors,
        )
        offset_amount = _parse_money_field(
            raw.get("offset_amount"),
            row=row_idx,
            field="offset_amount",
            errors=row_errors,
        )
        if round(refunded_amount + offset_amount, 2) > received_amount:
            row_errors.append(
                _issue(
                    row_idx,
                    "refunded_amount",
                    "已退金额与已冲抵金额合计不得超过已收金额",
                )
            )
        due_date = _parse_date_field(
            raw.get("due_date"),
            row=row_idx,
            field="due_date",
            errors=row_errors,
        )
        received_date = _parse_date_field(
            raw.get("received_date"),
            row=row_idx,
            field="received_date",
            errors=row_errors,
        )
        refund_date = _parse_date_field(
            raw.get("refund_date"),
            row=row_idx,
            field="refund_date",
            errors=row_errors,
        )

        if row_errors:
            errors.extend(row_errors)
            continue
        prepared.append(
            ContractDeposit(
                contract_id=contract.id,
                customer_id=contract.customer_id,
                deposit_type=deposit_type,
                expected_amount=expected_amount,
                received_amount=received_amount,
                refunded_amount=refunded_amount,
                offset_amount=offset_amount,
                status=deposit_status(
                    expected_amount,
                    received_amount,
                    refunded_amount,
                    offset_amount,
                ),
                due_date=due_date,
                received_date=received_date,
                refund_date=refund_date,
                notes=_cell_text(raw.get("notes")),
            )
        )

    return {
        "building_id": building_id,
        "building_code": building_code,
        "total_rows": len(rows),
        "errors": errors,
        "prepared": prepared,
    }


def import_deposits_excel(
    content: bytes,
    db: Session,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    plan = _deposit_import_plan(content, db, scope=scope)
    if plan["errors"]:
        raise HTTPException(
            400,
            {
                "message": "押金导入包含无效或跨楼合同，未写入任何押金记录",
                "errors": plan["errors"],
            },
        )

    prepared = plan["prepared"]
    try:
        db.add_all(prepared)
        db.flush()
        _log_import_result(
            db,
            "deposit_import",
            "押金台账导入",
            len(prepared),
            0,
            operator,
            "deposit",
            scope,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"押金导入失败，未写入数据：{exc}") from exc
    return {
        "imported": len(prepared),
        "failed": 0,
        "building": plan["building_code"],
    }


def reject_deposit_import() -> None:
    raise HTTPException(400, "押金台账导入暂未启用。请下载模板核对字段，写入仍通过合同详情押金台账逐笔登记。")
