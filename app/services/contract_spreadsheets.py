from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Any, Callable, Iterable
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
import openpyxl

from app.core.excel_safety import sanitize_workbook
from app.core.workbook_units import normalize_import_workbook
from app.core.workbook_localization import localize_workbook
from app.core.time import today_in_timezone
from app.core.localization import bilingual_aliases, import_value, import_demo_tower
from app.models import Building, Contract, ContractRoom
from app.services.contract_rent import calculate_broker_fee, calculate_monthly_rent
from app.services.common import (
    contract_effective_net_rent,
    contract_monthly_property_fee,
    contract_room_items,
    contract_room_summary,
    customer_display_name,
    log_action,
)
from app.services.legacy_demo_a_scope import ObjectScope


@dataclass(frozen=True)
class ContractSpreadsheetDependencies:
    calculate_broker_fee: Callable[..., float]
    find_overlapping_contract: Callable[..., Any]
    find_overlapping_room_contract: Callable[..., Any]
    import_room_items_from_row: Callable[..., list[dict]]
    legacy_deposit_from_contract_data: Callable[..., Any]
    normalize_signing_type: Callable[..., str]
    normalize_tower: Callable[..., str]
    parse_date: Callable[..., Any]
    periods_overlap: Callable[..., bool]
    prepare_contract_write: Callable[..., Any]
    resolve_contract_customer: Callable[..., Any]
    room_item_effective_period_from_data: Callable[..., Any]
    sync_contract_summary_from_rooms: Callable[..., Any]
    validate_contract_values: Callable[..., Any]
    validate_room_item_periods: Callable[..., Any]


EXPORT_HEADERS = [
    "序号",
    "合同编号",
    "楼塔",
    "室号",
    "客户名称",
    "出租面积",
    "租金单价",
    "签约类型",
    "免租期",
    "月租金",
    "中介佣金",
    "净租金",
    "起租日",
    "终止日",
    "合同状态",
    "到期处置",
    "客户方对接人",
    "联系电话",
    "我方对接人",
    "我方飞书ID",
    "押金",
    "押金状态",
    "物业费单价",
    "月物业费",
    "关联上份合同",
    "备注",
]


ROOM_DETAIL_EXPORT_HEADERS = [
    "合同编号",
    "客户名称",
    "楼塔",
    "室号",
    "出租面积",
    "租金单价",
    "月租金",
    "房源起租日",
    "房源终止日",
    "备注",
]


FIELD_MAP: dict[str, str | None] = {}


_ALIAS_LIST = [
    (None, ["序号", "编号", "No", "NO", "no"]),
    ("contract_no", ["合同编号", "合同号", "合同编码", "合同No", "contract"]),
    ("tower", ["南/北塔", "南北塔", "楼塔", "塔楼", "南塔/北塔", "塔", "楼栋", "栋"]),
    ("room_no", ["室号", "房号", "房间号", "房间", "室", "单元号", "单元"]),
    ("tenant_name", ["客户名称", "客户", "租户", "租户名称", "承租方", "承租人", "公司名称", "租赁方"]),
    ("area", ["出租面积", "面积", "租赁面积", "建筑面积", "使用面积", "面积(㎡)", "面积（㎡）", "面积㎡"]),
    ("unit_price", ["租金单价", "单价", "租金/单价", "单价(元)", "单价（元）"]),
    ("lease_type", ["签约类型", "合同类型", "业务类型", "新租/续租", "新租续租", "租赁类型", "类型", "新/续租", "新续租", "租赁方式"]),
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
    ("contact_person", ["客户方对接人", "客户联系人", "联系人", "联系人姓名", "对接人"]),
    ("contact_phone", ["联系电话", "电话", "手机", "手机号"]),
    ("contact_email", ["邮箱", "电子邮箱", "email", "Email"]),
    ("internal_contact_person", ["我方对接人", "我方联系人", "内部对接人", "合同责任人", "责任人", "维护人"]),
    ("internal_contact_feishu_id", ["我方飞书ID", "飞书ID", "飞书用户ID", "责任人飞书ID", "维护人飞书ID"]),
    ("deposit", ["押金", "保证金", "押金金额"]),
    ("deposit_status", ["押金状态", "保证金状态"]),
    ("property_fee_rate", ["物业费单价", "物业单价", "物业费", "管理费单价", "管理费"]),
    ("property_fee_free", ["物业费免费期", "物业免费期", "物业费减免期", "管理费免费期"]),
    ("prev_contract_no", ["关联合同", "上份合同", "关联上份合同", "原合同编号"]),
]


_ALIAS_LIST.extend(
    [
        ("room_start_date", ["房源起租日", "房源开始日", "明细起租日", "房间起租日"]),
        ("room_end_date", ["房源终止日", "房源结束日", "明细终止日", "房间终止日"]),
    ]
)


for field, aliases in _ALIAS_LIST:
    for alias in aliases:
        FIELD_MAP[alias] = field


FIELD_MAP = bilingual_aliases(FIELD_MAP)


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


def _import_issue(row: int, contract_no: str, reason: str) -> dict:
    return {"row": row, "contract_no": contract_no, "reason": reason}


def _format_import_issue(issue: dict) -> str:
    prefix = f"第{issue.get('row')}行" if issue.get("row") else ""
    contract_no = issue.get("contract_no") or ""
    if contract_no:
        prefix = f"{prefix} 合同 {contract_no}".strip()
    reason = issue.get("reason") or ""
    return f"{prefix}: {reason}" if prefix else reason


def import_contracts_excel(
    content: bytes,
    db: Session,
    operator: str,
    dependencies: ContractSpreadsheetDependencies,
    *,
    building_id: int,
) -> dict:
    selected_building = db.get(Building, building_id)
    wb = normalize_import_workbook(openpyxl.load_workbook(io.BytesIO(content)))
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
    skipped_rows = []
    fatal_error_rows = []
    pending_contracts: list[Contract] = []
    seen_contract_nos: set[str] = set()
    for row_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=False), start=header_row + 1):
        row_data = {}
        for cell in row:
            if cell.column in headers:
                row_data[headers[cell.column]] = import_value(headers[cell.column], cell.value)

        contract_no = str(row_data.get("contract_no") or "").strip()
        if not contract_no:
            continue
        if contract_no in seen_contract_nos:
            skipped += 1
            issue = _import_issue(row_idx, contract_no, "在导入文件中重复，跳过")
            skipped_rows.append(issue)
            errors.append(_format_import_issue(issue))
            continue
        seen_contract_nos.add(contract_no)
        existing_contract = db.query(Contract).filter(Contract.contract_no == contract_no).first()
        if existing_contract:
            if existing_contract.building_id != building_id:
                issue = _import_issue(row_idx, contract_no, "合同编号不可用或不属于当前楼宇")
                fatal_error_rows.append(issue)
                fatal_errors.append(_format_import_issue(issue))
            else:
                skipped += 1
                issue = _import_issue(row_idx, contract_no, "合同编号已存在，跳过")
                skipped_rows.append(issue)
                errors.append(_format_import_issue(issue))
            continue

        try:
            data = {
                "contract_no": contract_no,
                "building_id": building_id,
                "tower": dependencies.normalize_tower(import_demo_tower(row_data.get("tower"), selected_building.code)),
                "room_no": str(row_data.get("room_no") or ""),
                "tenant_name": str(row_data.get("tenant_name") or ""),
                "area": float(row_data.get("area") or 0),
                "unit_price": float(row_data.get("unit_price") or 0),
                "lease_type": dependencies.normalize_signing_type(row_data.get("lease_type")),
                "free_period": int(float(row_data.get("free_period") or 0)),
                "monthly_rent": 0,
                "broker_fee": float(row_data.get("broker_fee") or 0),
                "start_date": dependencies.parse_date(row_data.get("start_date")),
                "end_date": dependencies.parse_date(row_data.get("end_date")),
                "disposal": str(row_data.get("disposal") or ""),
                "contact_person": str(row_data.get("contact_person") or ""),
                "contact_phone": str(row_data.get("contact_phone") or ""),
                "contact_email": str(row_data.get("contact_email") or ""),
                "internal_contact_person": str(row_data.get("internal_contact_person") or ""),
                "internal_contact_feishu_id": str(row_data.get("internal_contact_feishu_id") or ""),
                "deposit": float(row_data.get("deposit") or 0),
                "deposit_status": str(row_data.get("deposit_status") or ""),
                "property_fee_rate": float(row_data.get("property_fee_rate") or 0),
                "property_fee_free": 0,
                "prev_contract_no": str(row_data.get("prev_contract_no") or ""),
                "notes": str(row_data.get("notes") or ""),
            }
            room_start_date = dependencies.parse_date(row_data.get("room_start_date"))
            room_end_date = dependencies.parse_date(row_data.get("room_end_date"))
            room_items = dependencies.import_room_items_from_row(db, data, room_start_date, room_end_date)
            data, room_items = dependencies.prepare_contract_write(db, data, room_items)
            dependencies.sync_contract_summary_from_rooms(data, room_items)
            if row_data.get("broker_fee") in (None, ""):
                data["broker_fee"] = dependencies.calculate_broker_fee(data.get("monthly_rent"))
            dependencies.resolve_contract_customer(db, data, match_name=True)
            dependencies.validate_contract_values(data)
            dependencies.validate_room_item_periods(data, room_items)
            for item in room_items:
                item_start, item_end = dependencies.room_item_effective_period_from_data(data, item)
                overlapping = dependencies.find_overlapping_room_contract(
                    db,
                    building_id=building_id,
                    room_no=item["room_no"],
                    start_date=item_start,
                    end_date=item_end,
                )
                if overlapping:
                    raise HTTPException(400, f"室号 {item['room_no']} 在该租期内已关联合同 {overlapping.contract_no}")
            for pending in pending_contracts:
                pending_items = contract_room_items(pending)
                for item in room_items:
                    item_start, item_end = dependencies.room_item_effective_period_from_data(data, item)
                    item_building_id = int(item.get("building_id") or building_id)
                    for pending_item in pending_items:
                        if (
                            int(pending.building_id or 0) == item_building_id
                            and item["room_no"] == pending_item.get("room_no")
                            and dependencies.periods_overlap(
                                pending_item.get("start_date"),
                                pending_item.get("end_date"),
                                item_start,
                                item_end,
                            )
                        ):
                            raise HTTPException(400, f"室号 {item['room_no']} 在导入文件中存在租期重叠")
            contract = Contract(**data)
            legacy_deposit = dependencies.legacy_deposit_from_contract_data(data)
            if legacy_deposit:
                contract.deposits.append(legacy_deposit)
            for item in room_items:
                contract.room_items.append(ContractRoom(**item))
            pending_contracts.append(contract)
        except HTTPException as exc:
            issue = _import_issue(row_idx, contract_no, str(exc.detail))
            fatal_error_rows.append(issue)
            fatal_errors.append(_format_import_issue(issue))
        except Exception as exc:
            issue = _import_issue(row_idx, contract_no, str(exc))
            fatal_error_rows.append(issue)
            fatal_errors.append(_format_import_issue(issue))

    if fatal_errors:
        raise HTTPException(
            400,
            {
                "message": "Excel 存在无法导入的数据，未写入任何新合同",
                "fatal_errors": fatal_error_rows,
                "errors": fatal_errors[:10],
                "skipped": skipped,
                "skipped_rows": skipped_rows,
            },
        )

    try:
        for contract in pending_contracts:
            db.add(contract)
        db.flush()
        for contract in pending_contracts:
            log_action(
                db,
                contract.id,
                "import",
                f"Excel导入: {contract.contract_no}",
                operator,
                commit=False,
                object_scope=ObjectScope(
                    scope_type="building",
                    building_id=int(contract.building_id),
                ),
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Excel 导入失败，未写入任何新合同：{exc}") from exc

    imported = len(pending_contracts)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "skipped_rows": skipped_rows,
        "fatal_errors": [],
        "matched_fields": matched_fields,
        "unmatched_headers": unmatched_headers,
        "header_row": header_row,
    }


def export_contracts_excel(
    contracts: list[Contract],
    keyword: str | None,
    tower: str | None,
    status: str | None,
    *,
    property_label: str = "示例双塔中心",
    normalize_signing_type: Callable[..., str],
    normalize_tower: Callable[..., str],
) -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "租赁合同"

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(EXPORT_HEADERS))
    title = ws.cell(
        row=1,
        column=1,
        value=f"{property_label or '所选楼宇'}租赁情况明细表（{today_in_timezone().strftime('%Y年%m月%d日')}）",
    )
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
        summary = contract_room_summary(contract)
        values = [
            idx,
            contract.contract_no,
            summary["tower"],
            summary["room_no"],
            customer_display_name(contract),
            summary["area"],
            summary["unit_price"],
            normalize_signing_type(contract.lease_type),
            contract.free_period,
            summary["monthly_rent"],
            contract.broker_fee,
            contract_effective_net_rent(contract),
            contract.start_date.strftime("%Y-%m-%d") if contract.start_date else "",
            contract.end_date.strftime("%Y-%m-%d") if contract.end_date else "",
            contract.status,
            contract.disposal or "",
            contract.contact_person or "",
            contract.contact_phone or "",
            contract.internal_contact_person or "",
            contract.internal_contact_feishu_id or "",
            contract.deposit or 0,
            contract.deposit_status or "",
            contract.property_fee_rate or 0,
            contract_monthly_property_fee(contract, summary),
            contract.prev_contract_no or "",
            contract.notes or "",
        ]
        for col_idx, value in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = [6, 15, 8, 10, 15, 10, 10, 10, 8, 12, 12, 12, 12, 12, 10, 10, 12, 14, 12, 18, 12, 10, 10, 10, 15, 20]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width

    detail_ws = wb.create_sheet("房源明细")
    for col_idx, header in enumerate(ROOM_DETAIL_EXPORT_HEADERS, 1):
        cell = detail_ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    detail_row = 2
    for contract in contracts:
        for item in contract_room_items(contract):
            values = [
                contract.contract_no,
                customer_display_name(contract),
                str(item.get("tower") or "").strip(),
                item.get("room_no"),
                item.get("area"),
                item.get("unit_price"),
                item.get("monthly_rent"),
                item.get("start_date").strftime("%Y-%m-%d") if item.get("start_date") else "",
                item.get("end_date").strftime("%Y-%m-%d") if item.get("end_date") else "",
                item.get("notes"),
            ]
            for col_idx, value in enumerate(values, 1):
                cell = detail_ws.cell(row=detail_row, column=col_idx, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center", vertical="center")
            detail_row += 1

    for idx, width in enumerate([15, 16, 10, 12, 12, 12, 14, 13, 13, 24], 1):
        detail_ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width

    localize_workbook(wb)

    sanitize_workbook(wb)
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
    parts.append(today_in_timezone().strftime("%Y%m%d"))
    filename = "_".join(parts) + ".xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _export_ledger_excel(title: str, sheet_name: str, headers: list[tuple[str, str]], rows: list[dict], filename: str) -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")

    header_fill = PatternFill("solid", fgColor="3B82F6")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    for col_idx, (_field, label) in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    for row_idx, row in enumerate(rows, 3):
        for col_idx, (field, _label) in enumerate(headers, 1):
            value = row.get(field, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    for col_idx, (_field, label) in enumerate(headers, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = max(10, min(24, len(label) + 8))

    localize_workbook(wb)

    sanitize_workbook(wb)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def export_invoice_ledger_excel(
    db: Session,
    month: str | None,
    invoice_status: str | None = None,
    keyword: str | None = None,
    *,
    building_ids: Iterable[int] | None = None,
    get_invoice_ledger: Callable[..., dict],
) -> StreamingResponse:
    ledger = get_invoice_ledger(
        db,
        month,
        invoice_status,
        keyword,
        building_ids=building_ids,
    )
    headers = [
        ("contract_no", "合同号"),
        ("tenant_name", "客户"),
        ("room_no", "室号"),
        ("tower", "塔楼"),
        ("period_month", "期间"),
        ("rent", "应收租金"),
        ("received_amount", "已收金额"),
        ("balance", "本月未收"),
        ("invoice_status", "发票状态"),
        ("invoice_no", "发票号"),
        ("invoice_date", "开票日期"),
        ("invoice_amount", "开票金额"),
        ("invoice_tax_rate", "税率"),
        ("invoice_notes", "备注"),
    ]
    parts = ["发票台账", ledger["month"]]
    if invoice_status:
        parts.append(invoice_status)
    if keyword:
        parts.append(keyword)
    return _export_ledger_excel(
        f"{ledger['month']} 发票台账",
        "发票台账",
        headers,
        ledger["rows"],
        "_".join(parts) + ".xlsx",
    )


def export_dunning_ledger_excel(
    db: Session,
    month: str | None,
    dunning_status: str | None = None,
    dunning_owner: str | None = None,
    overdue_only: bool | None = None,
    focus: bool = True,
    *,
    building_ids: Iterable[int] | None = None,
    get_dunning_ledger: Callable[..., dict],
) -> StreamingResponse:
    ledger = get_dunning_ledger(
        db,
        month,
        dunning_status,
        dunning_owner,
        overdue_only,
        focus,
        building_ids=building_ids,
    )
    headers = [
        ("contract_no", "合同号"),
        ("tenant_name", "客户"),
        ("room_no", "室号"),
        ("tower", "塔楼"),
        ("period_month", "期间"),
        ("rent", "应收租金"),
        ("received_amount", "已收金额"),
        ("balance", "本月未收"),
        ("overdue_balance", "本月逾期"),
        ("aging_days", "逾期天数"),
        ("dunning_status", "催收状态"),
        ("dunning_owner", "负责人"),
        ("last_dunning_date", "最近催收日"),
        ("next_follow_up_date", "下次跟进日"),
        ("dunning_notes", "备注"),
    ]
    parts = ["催收台账", ledger["month"]]
    if dunning_status:
        parts.append(dunning_status)
    if dunning_owner:
        parts.append(dunning_owner)
    return _export_ledger_excel(
        f"{ledger['month']} 催收台账",
        "催收台账",
        headers,
        ledger["rows"],
        "_".join(parts) + ".xlsx",
    )


def export_contract_template(*, building_code: str = "DEMO_A") -> StreamingResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "导入模板"
    wb._demo_tower_values = building_code.casefold() == "demo_a"
    headers = ["合同编号", "楼塔", "室号", "客户名称", "出租面积", "租金单价", "签约类型", "免租期", "月租金", "中介佣金", "起租日", "终止日", "房源起租日", "房源终止日", "客户方对接人", "联系电话", "我方对接人", "我方飞书ID", "备注"]
    header_comments = {
        "楼塔": "室号匹配房源清单时，导入以房源清单塔楼为准；未匹配的单房源才使用本列。",
        "室号": "可填单个室号；多房源可用顿号、逗号、分号或斜杠分隔，例如 IMP1801、IMPC101。",
        "出租面积": "室号匹配房源清单时，面积以房源清单为准；未匹配的单房源才使用本列。",
        "月租金": "导入时按租金单价 × 面积 × 365 / 12 自动计算，本列仅作参考。",
        "房源起租日": "可留空；留空时该房源继承合同起租日。",
        "房源终止日": "可留空；留空时该房源继承合同终止日。",
    }
    for idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=idx, value=header)
        cell.font = Font(bold=True)
        if header in header_comments:
            cell.comment = Comment(header_comments[header], "lease-manager")
    samples = [
        [
            "WC-2024-001",
            "南塔",
            "1201",
            "示例公司",
            120.5,
            4.5,
            "新签",
            2,
            calculate_monthly_rent(120.5, 4.5),
            calculate_broker_fee(calculate_monthly_rent(120.5, 4.5)),
            "2024-01-15",
            "2026-12-20",
            "",
            "",
            "张三",
            "13800000000",
            "李四",
            "ou_xxxxxxxxxxxxxxxxxxxx",
            "单房源示例",
        ],
        [
            "WC-2024-002",
            "",
            "IMP1801、IMPC101",
            "多房源示例公司",
            "",
            5,
            "新签",
            0,
            "",
            "",
            "2024-03-01",
            "2026-02-28",
            "",
            "",
            "张三",
            "13800000000",
            "李四",
            "ou_xxxxxxxxxxxxxxxxxxxx",
            "多房源写在同一格；塔楼和面积以房源清单为准",
        ],
    ]
    for row_idx, sample in enumerate(samples, 2):
        for col_idx, value in enumerate(sample, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)
    for idx, width in enumerate([16, 10, 22, 18, 12, 12, 10, 10, 14, 14, 13, 13, 13, 13, 14, 15, 14, 22, 34], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width

    note_ws = wb.create_sheet("导入说明")
    notes = [
        ("导入楼宇", f"本模板仅用于 {building_code}；上传时仍按当前 URL 楼宇重新校验。"),
        ("室号", "多个室号可写在同一个单元格，用顿号、逗号、分号或斜杠分隔，如 IMP1801、IMPC101。"),
        ("面积/塔楼", "室号已存在于房源清单时，导入以房源清单的面积和塔楼为准。"),
        ("多房源", "多房源导入要求所有室号都能在房源清单中匹配，否则整份文件不写入。"),
        ("租金", "月租金按租金单价 × 面积 × 365 / 12 重新计算。"),
        ("房源期间", "房源起止日可留空继承合同期间；填写后用于房态、历史和占用冲突判断。"),
        ("冲突", "导入会按房源明细有效期检查房源占用，错误会返回 Excel 行号、合同编号和冲突室号。"),
    ]
    note_ws.append(["字段", "说明"])
    note_ws["A1"].font = Font(bold=True)
    note_ws["B1"].font = Font(bold=True)
    for row in notes:
        note_ws.append(row)
    note_ws.column_dimensions["A"].width = 14
    note_ws.column_dimensions["B"].width = 86
    localize_workbook(wb)
    sanitize_workbook(wb)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"},
    )
