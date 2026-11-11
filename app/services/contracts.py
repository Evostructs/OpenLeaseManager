from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
import io
from pathlib import Path
import re
import tempfile
import threading
from typing import Iterable
from urllib.parse import quote

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
import openpyxl
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload, with_loader_criteria
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
from app.core.time import now_in_timezone, today_in_timezone
from app.core.excel_safety import sanitize_workbook
from app.core.workbook_localization import localize_workbook
from app.core.money import round_money
from app.services.calculation_context import reuse_read_calculations
from app.database import SessionLocal
from app.models import Building, Contract, ContractDeposit, ContractRoom, Customer, CustomerFollowUp, ReceivablePayment, ReceivableRecord, ReceivableStatement, RentTerm, Room
from app.schemas import ContractCreate, ContractRoomPayload, ContractRoomReducePayload, ContractRoomSwapPayload, ContractTerminatePayload, ContractUpdate, ReceivableMetadataPayload, ReceivablePaymentPayload, ReceivableRecordPayload, RentTermPayload
from app.services.common import (
    contract_effective_net_rent,
    contract_room_effective_period,
    contract_monthly_property_fee,
    contract_room_items,
    contract_room_summary,
    contract_tower_value,
    customer_display_name,
    deposit_status,
    deposit_summary,
    dump_changes,
    log_action,
    parse_date,
    serialize_contract,
    serialize_contract_lifecycle_item,
    serialize_rent_term,
)
from app.services.contract_rent import (
    DEFAULT_BROKER_FEE_MONTHS,
    ESCALATION_ADJUSTMENT_TYPES,
    ESCALATION_FREQUENCIES,
    RENT_ADJUSTMENT_TYPES,
    RENT_BASE_TERM_TYPES,
    RENT_TERM_LABELS,
    RENT_TERM_TYPES,
    TEMPORARY_ADJUSTMENT_TERM_TYPE,
    _apply_percentage_escalations,
    _date_overlap,
    _fixed_escalation_components_for_day,
    _free_rent_end,
    _legacy_room_components_for_day,
    _legacy_room_rent_for_span,
    _month_bounds,
    _month_rent_detail,
    _rent_escalation_level,
    _rent_free_matches_component,
    _rent_term_active_in_period,
    _rent_term_active_on_day,
    _rent_term_effects_for_month,
    _rent_term_escalations_for_day,
    _rent_term_fallback_area,
    _rent_term_free_components_for_day,
    _rent_term_monthly_base,
    _rent_term_positive_components_for_day,
    _rent_term_room_key,
    _rent_term_scope_component,
    _rent_term_scope_periods,
    _rent_term_scope_text,
    _rent_term_temporary_adjustments_for_day,
    _rent_terms_month_detail,
    _sync_monthly_rent,
    _temporary_adjustment_amount_for_day,
    calculate_broker_fee,
    calculate_monthly_rent,
    contract_net_rent,
)
from app.services.contract_spreadsheets import (
    ContractSpreadsheetDependencies,
    _format_import_issue,
    _import_issue,
    _match_header,
    _normalize_header,
    export_contract_template as _export_contract_template_impl,
    export_contracts_excel as _export_contracts_excel_impl,
    export_dunning_ledger_excel as _export_dunning_ledger_excel_impl,
    export_invoice_ledger_excel as _export_invoice_ledger_excel_impl,
    import_contracts_excel as _import_contracts_excel_impl,
)
from app.services.contract_files import (
    _contract_room_numbers,
    _ensure_pdf_upload_name,
    _safe_upload_path,
    _stored_pdf_name,
    _write_limited_upload,
    batch_upload_pdfs as _batch_upload_pdfs_impl,
    download_contract_pdf,
    list_missing_contract_pdfs,
    list_unmatched_pdfs,
    manual_match_pdf as _manual_match_pdf_impl,
    save_contract_pdf as _save_contract_pdf_impl,
)
from app.services.contract_receivables import (
    AGING_BUCKETS,
    CASH_ALLOCATION_TYPES,
    DUNNING_AGING_THRESHOLD_DAYS,
    DUNNING_BALANCE_THRESHOLD,
    DUNNING_STATUSES,
    INVOICE_STATUSES,
    LEGACY_PAYMENT_SOURCE,
    PAYMENT_ALLOCATION_TYPES,
    _aging_bucket_key,
    _build_receivable_batch_context,
    _dunning_risk,
    _empty_aging_buckets,
    _ensure_month_within_contract,
    _ensure_no_base_term_overlap,
    _ensure_receivable_record,
    _ensure_settled_months_unchanged,
    _follow_up_soon,
    _legacy_receivable_payment_out,
    _matches_receivable_keyword,
    _parse_receivable_month,
    _payment_summary,
    _rank_debtors,
    _rank_dunning_alerts,
    _receivable_due_date,
    _receivable_metadata_changes,
    _receivable_payment_out,
    _settled_month_receivables,
    _validate_receivable_metadata,
    add_receivable_payment,
    build_rent_plan,
    delete_receivable_payment,
    generate_receivable_statements,
    get_dunning_ledger,
    get_invoice_ledger,
    get_monthly_receivables,
    get_monthly_receivables_batch,
    get_receivable_trend,
    update_receivable_metadata,
    upsert_receivable_record,
)
from app.services.customers import resolve_contract_customer
from app.services.detail_operations_scope import (
    prepare_detail_contract_record,
    prepare_detail_contract_write,
    require_detail_building,
    require_detail_write_scope,
)
from app.services.legacy_demo_a_scope import (
    ObjectScope,
    legacy_attachment_scope,
)
from app.services.lease_types import normalize_signing_type
from app.services.property_scope import LEGACY_DEFAULT_BUILDING_CODE, SelectedPropertyScope
from app.services.towers import leasing_group, normalize_tower


SETTINGS = get_settings()
PDF_CHUNK_SIZE = 1024 * 1024
MAX_PDF_FILE_SIZE = 50 * 1024 * 1024
MAX_BATCH_PDF_COUNT = 20
MAX_BATCH_PDF_TOTAL_SIZE = 200 * 1024 * 1024
ACTIVE_CONTRACT_STATUSES = {"在租", "即将到期", "逾期占用"}
FINAL_DISPOSAL_STATUSES = {"已续租", "已退租", "逾期占用", "已变更"}

# 合同写互斥锁：房源重叠/合同号唯一等校验是"先查后写"，
# 单进程部署下用进程内互斥串行化合同写入兜底并发竞态
# （多 worker 部署时需改用数据库级锁，当前 Dockerfile 为单 worker）
_CONTRACT_WRITE_LOCK = threading.Lock()
IMPORT_ROOM_SPLIT_RE = re.compile(r"[、,，;；/\n\r]+")


def _tower_text(value: object) -> str:
    return str(value or "").strip()


def _selected_contract_buildings(scope: SelectedPropertyScope) -> tuple[tuple[int, str], ...]:
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
            (int(building_id), str(code).strip())
            for building_id, code in zip(scope.building_ids, scope.building_codes)
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
    return pairs


def _require_contract_detail_write_scope(db: Session, scope: SelectedPropertyScope) -> int:
    try:
        return require_detail_write_scope(db, scope, "contracts").building_id
    except HTTPException as exc:
        if exc.status_code == 422:
            raise HTTPException(422, "该楼未启用明细写入") from exc
        raise


def _assert_contract_building(contract: Contract, scope: SelectedPropertyScope) -> int:
    selected_ids = {building_id for building_id, _code in _selected_contract_buildings(scope)}
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    building_id = int(contract.building_id)
    if building_id not in selected_ids:
        raise HTTPException(404, "合同不存在或无权访问")
    return building_id


def _contract_object_scope(contract: Contract) -> ObjectScope:
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    return ObjectScope(scope_type="building", building_id=int(contract.building_id))


def _assert_contract_room_graphs(db: Session, contracts: Iterable[Contract]) -> None:
    contract_rows = list(contracts)
    expected_buildings = {
        int(contract.id): int(contract.building_id)
        for contract in contract_rows
        if contract.id is not None and contract.building_id is not None
    }
    if not expected_buildings:
        return

    room_items = (
        db.query(ContractRoom)
        .filter(ContractRoom.contract_id.in_(tuple(expected_buildings)))
        .all()
    )
    for item in room_items:
        expected_building_id = expected_buildings[int(item.contract_id)]
        if item.building_id is None or int(item.building_id) != expected_building_id:
            raise HTTPException(409, "合同与房源明细楼宇不一致")

    linked_room_ids = {int(item.room_id) for item in room_items if item.room_id is not None}
    linked_rooms = (
        {
            int(room.id): room
            for room in db.query(Room).filter(Room.id.in_(tuple(linked_room_ids))).all()
        }
        if linked_room_ids
        else {}
    )
    for item in room_items:
        if item.room_id is None:
            continue
        expected_building_id = expected_buildings[int(item.contract_id)]
        room = linked_rooms.get(int(item.room_id))
        if not room or room.is_deleted or room.building_id is None or int(room.building_id) != expected_building_id:
            raise HTTPException(409, "合同房源明细与房源台账楼宇不一致")


def _assert_contracts_in_scope(
    db: Session,
    contracts: Iterable[Contract],
    scope: SelectedPropertyScope,
    *,
    for_write: bool = False,
) -> list[int]:
    contract_rows = list(contracts)
    selected_building_id = _require_contract_detail_write_scope(db, scope) if for_write else None
    building_ids = []
    for contract in contract_rows:
        building_id = _assert_contract_building(contract, scope)
        if selected_building_id is not None and building_id != selected_building_id:
            raise HTTPException(404, "合同不存在或无权访问")
        building_ids.append(building_id)
    _assert_contract_room_graphs(db, contract_rows)
    return building_ids


def assert_contract_in_scope(
    db: Session,
    contract: Contract,
    scope: SelectedPropertyScope,
    *,
    for_write: bool = False,
) -> int:
    return _assert_contracts_in_scope(db, [contract], scope, for_write=for_write)[0]






def _spreadsheet_dependencies(
    *,
    building_ids: Iterable[int] | None = None,
) -> ContractSpreadsheetDependencies:
    selected_building_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) if building_ids is not None else None

    def resolve_scoped_contract_customer(db: Session, data: dict, *, match_name: bool = False):
        return resolve_contract_customer(
            db,
            data,
            match_name=match_name,
            building_ids=selected_building_ids,
        )

    return ContractSpreadsheetDependencies(
        calculate_broker_fee=calculate_broker_fee,
        find_overlapping_contract=_find_overlapping_contract,
        find_overlapping_room_contract=_find_overlapping_room_contract,
        import_room_items_from_row=_import_room_items_from_row,
        legacy_deposit_from_contract_data=_legacy_deposit_from_contract_data,
        normalize_signing_type=normalize_signing_type,
        normalize_tower=_tower_text,
        parse_date=parse_date,
        periods_overlap=_periods_overlap,
        prepare_contract_write=prepare_detail_contract_write,
        resolve_contract_customer=resolve_scoped_contract_customer,
        room_item_effective_period_from_data=_room_item_effective_period_from_data,
        sync_contract_summary_from_rooms=_sync_contract_summary_from_rooms,
        validate_contract_values=_validate_contract_values,
        validate_room_item_periods=_validate_room_item_periods,
    )


def import_contracts_excel(
    content: bytes,
    db: Session,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id = _require_contract_detail_write_scope(db, scope)
    return _import_contracts_excel_impl(
        content,
        db,
        operator,
        _spreadsheet_dependencies(building_ids=scope.building_ids),
        building_id=building_id,
    )


def require_contract_detail_write_scope(
    scope: SelectedPropertyScope,
    db: Session | None = None,
) -> int:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        return _require_contract_detail_write_scope(session, scope)
    finally:
        if owns_session:
            session.close()


def _detail_contract_template(profile) -> StreamingResponse:
    rule = profile.contract_number_rule
    if not rule.supported or not rule.pattern:
        raise HTTPException(422, "当前楼宇未启用合同编号规则")
    year = today_in_timezone().year
    sample_no = rule.pattern.format(year=year, sequence=1)
    zones = profile.space_structure.zones
    sample_zone = zones[0].name if zones else ""

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "导入模板"
    headers = [
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
        "起租日",
        "终止日",
        "房源起租日",
        "房源终止日",
        "客户方对接人",
        "联系电话",
        "我方对接人",
        "我方飞书ID",
        "备注",
    ]
    for index, header in enumerate(headers, 1):
        cell = worksheet.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True)
    worksheet["B1"].comment = Comment("必须填写当前楼宇已登记的分区；不会根据室号猜分区。", "lease-manager")
    worksheet["C1"].comment = Comment("必须填写当前楼宇房源台账中已存在的室号；合同导入不会自动创建房源。", "lease-manager")
    worksheet.append(
        [
            sample_no,
            sample_zone,
            "示例单元-01",
            "示例公司",
            100,
            4,
            "新签",
            0,
            calculate_monthly_rent(100, 4),
            calculate_broker_fee(calculate_monthly_rent(100, 4)),
            f"{year}-01-01",
            f"{year}-12-31",
            "",
            "",
            "张三",
            "13800000000",
            "李四",
            "ou_xxxxxxxxxxxxxxxxxxxx",
            "示例行仅说明格式；导入前须先建立真实房源台账",
        ]
    )
    zones_worksheet = workbook.create_sheet("可用分区")
    zones_worksheet.append(["分区代码", "分区名称", "分区类型"])
    for cell in zones_worksheet[1]:
        cell.font = Font(bold=True)
    for zone in zones:
        zones_worksheet.append([zone.code, zone.name, zone.kind])
    localize_workbook(workbook)
    sanitize_workbook(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = quote(f"{profile.building_code}-合同导入模板.xlsx")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def export_contract_template(
    *,
    scope: SelectedPropertyScope,
    db: Session | None = None,
) -> StreamingResponse:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        detail_scope = require_detail_write_scope(session, scope, "contracts")
        if detail_scope.building_code.casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold():
            return _export_contract_template_impl(building_code=detail_scope.building_code)
        return _detail_contract_template(detail_scope.profile)
    finally:
        if owns_session:
            session.close()


def export_contracts_excel(
    contracts: list[Contract],
    keyword: str | None,
    tower: str | None,
    status: str | None,
    *,
    property_label: str = "示例双塔中心",
) -> StreamingResponse:
    return _export_contracts_excel_impl(
        contracts,
        keyword,
        tower,
        status,
        property_label=property_label,
        normalize_signing_type=normalize_signing_type,
        normalize_tower=normalize_tower,
    )


def export_invoice_ledger_excel(
    db: Session,
    month: str | None,
    invoice_status: str | None = None,
    keyword: str | None = None,
    *,
    building_ids: Iterable[int] | None = None,
) -> StreamingResponse:
    return _export_invoice_ledger_excel_impl(
        db,
        month,
        invoice_status,
        keyword,
        building_ids=building_ids,
        get_invoice_ledger=get_invoice_ledger,
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
) -> StreamingResponse:
    return _export_dunning_ledger_excel_impl(
        db,
        month,
        dunning_status,
        dunning_owner,
        overdue_only,
        focus,
        building_ids=building_ids,
        get_dunning_ledger=get_dunning_ledger,
    )


def _apply_status_filter(query, status: str | None):
    if not status:
        return query

    today = today_in_timezone()
    soon = today + relativedelta(days=90)
    if status == "信息不全":
        return query.filter(or_(Contract.start_date.is_(None), Contract.end_date.is_(None)))
    if status == "待生效":
        return query.filter(Contract.start_date > today)
    if status in FINAL_DISPOSAL_STATUSES:
        return query.filter(Contract.end_date < today, Contract.disposal == status)
    if status == "已到期":
        return query.filter(
            Contract.end_date < today,
            or_(Contract.disposal.is_(None), ~Contract.disposal.in_(FINAL_DISPOSAL_STATUSES)),
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


def next_contract_no(db: Session, *, scope: SelectedPropertyScope | None = None) -> dict:
    if scope is None:
        building = (
            db.query(Building)
            .filter(Building.code == LEGACY_DEFAULT_BUILDING_CODE, Building.is_active.is_(True))
            .one_or_none()
        )
        if building is None:
            raise HTTPException(503, "DEMO_A 楼宇范围尚未初始化，当前写入已停止")
        detail_scope = require_detail_building(db, int(building.id), "contracts")
    else:
        detail_scope = require_detail_write_scope(db, scope, "contracts")
    building_id = detail_scope.building_id
    rule = detail_scope.profile.contract_number_rule
    if not rule.supported or not rule.pattern or rule.sequence_scope != "building_year":
        raise HTTPException(422, "当前楼宇未启用合同编号规则")
    year = today_in_timezone().year
    try:
        prefix_template = rule.pattern.split("{sequence", 1)[0]
        prefix = prefix_template.format(year=year)
        next_number = lambda sequence: rule.pattern.format(year=year, sequence=sequence)
    except (KeyError, ValueError) as exc:
        raise HTTPException(503, "合同编号规则无效，当前写入已停止") from exc
    query = db.query(Contract).filter(
        Contract.building_id == building_id,
        Contract.contract_no.like(f"{prefix}%"),
    )
    existing = query.all()
    max_seq = 0
    for contract in existing:
        suffix = str(contract.contract_no or "").removeprefix(prefix)
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return {"next_no": next_number(max_seq + 1)}


def get_contract(
    db: Session,
    contract_id: int,
    include_deleted: bool = False,
    *,
    scope: SelectedPropertyScope | None = None,
    for_write: bool = False,
) -> Contract:
    query = db.query(Contract).filter(Contract.id == contract_id)
    if not include_deleted:
        query = query.filter(Contract.is_deleted.is_(False))
    if scope is not None:
        selected_ids = tuple(building_id for building_id, _code in _selected_contract_buildings(scope))
        query = query.filter(Contract.building_id.in_(selected_ids))
    contract = query.first()
    if not contract:
        raise HTTPException(404, "合同不存在或无权访问")
    if scope is not None:
        assert_contract_in_scope(db, contract, scope, for_write=for_write)
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
    *,
    scope: SelectedPropertyScope,
) -> dict:
    selected_ids = tuple(building_id for building_id, _code in _selected_contract_buildings(scope))
    # 列表序列化会触达 customer/room_items/rent_terms/deposits，
    # 统一预加载避免每行 4 次懒加载（page_size=100 时一页约 400 条额外 SQL）
    query = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items).selectinload(ContractRoom.room),
            selectinload(Contract.rent_terms),
            selectinload(Contract.deposits),
        )
        .filter(Contract.is_deleted.is_(False), Contract.building_id.in_(selected_ids))
    )
    selected_codes = {code.casefold() for _building_id, code in _selected_contract_buildings(scope)}
    tower = (
        normalize_tower(tower)
        if tower and selected_codes == {LEGACY_DEFAULT_BUILDING_CODE.casefold()}
        else _tower_text(tower)
    )
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                Contract.contract_no.like(like),
                Contract.room_no.like(like),
                Contract.room_items.any(ContractRoom.room_no.like(like)),
                Contract.tenant_name.like(like),
                Contract.customer.has(or_(Customer.name.like(like), Customer.short_name.like(like), Customer.credit_code.like(like))),
                Contract.internal_contact_person.like(like),
            )
        )
    if tower:
        query = query.filter(or_(Contract.tower == tower, Contract.room_items.any(ContractRoom.tower == tower)))
    query = _apply_status_filter(query, status)

    sort_key = sort_by or "end_date"
    reverse = sort_dir == "desc"
    sortable_columns = {
        "contract_no": Contract.contract_no,
        "internal_contact_person": Contract.internal_contact_person,
        "end_date": Contract.end_date,
    }
    if sort_key == "tenant_name":
        # Match customer_display_name, including legacy contracts without a
        # linked customer. Sort before LIMIT so one page does not hydrate the
        # entire building and all of its related rent terms and deposits.
        query = query.outerjoin(Customer, Contract.customer_id == Customer.id)
        sortable_columns["tenant_name"] = func.coalesce(
            func.nullif(Customer.name, ""), Contract.tenant_name, ""
        )

    if sort_key in sortable_columns:
        column = sortable_columns[sort_key]
        query = query.order_by(column.desc() if reverse else column.asc(), Contract.id.asc())
        total = query.count()
        items = query.all() if page_size == 0 else query.offset((page - 1) * page_size).limit(page_size).all()
        _assert_contracts_in_scope(db, items, scope)
        return {"total": total, "page": page, "page_size": page_size, "items": [serialize_contract(item) for item in items]}

    items = query.all()

    if sort_key == "contract_no":
        items.sort(key=lambda item: item.contract_no or "", reverse=reverse)
    elif sort_key == "tower":
        items.sort(key=lambda item: contract_room_summary(item)["tower"], reverse=reverse)
    elif sort_key == "room_no":
        items.sort(key=lambda item: contract_room_summary(item)["room_no"], reverse=reverse)
    elif sort_key == "tenant_name":
        items.sort(key=lambda item: customer_display_name(item), reverse=reverse)
    elif sort_key == "internal_contact_person":
        items.sort(key=lambda item: item.internal_contact_person or "", reverse=reverse)
    elif sort_key == "area":
        items.sort(key=lambda item: contract_room_summary(item)["area"], reverse=reverse)
    elif sort_key == "monthly_rent":
        items.sort(key=lambda item: contract_room_summary(item)["monthly_rent"], reverse=reverse)
    elif sort_key == "status":
        items.sort(key=lambda item: item.status or "", reverse=reverse)
    else:
        items.sort(key=lambda item: item.end_date or date.max, reverse=reverse)

    total = len(items)
    sliced = items if page_size == 0 else items[(page - 1) * page_size : page * page_size]
    _assert_contracts_in_scope(db, sliced, scope)
    return {"total": total, "page": page, "page_size": page_size, "items": [serialize_contract(item) for item in sliced]}


def serialize_contract_with_scope(
    db: Session,
    contract: Contract,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope)
    data = serialize_contract(contract)
    from app.services.attachments import attachment_summary, list_attachments
    from app.services.risks import list_risks

    attachments = list_attachments(db, "contract", contract.id, scope=scope)
    data["attachments"] = attachments
    data["attachment_summary"] = attachment_summary(attachments, entity_type="contract")
    data["risks"] = list_risks(
        db,
        contract_id=contract.id,
        limit=20,
        building_ids=scope.building_ids,
    )["items"]

    previous = None
    prev_contract_no = (contract.prev_contract_no or "").strip()
    if prev_contract_no:
        previous = (
            db.query(Contract)
            .filter(
                Contract.contract_no == prev_contract_no,
                Contract.building_id == contract.building_id,
                Contract.is_deleted.is_(False),
            )
            .first()
        )
    next_contracts = []
    contract_no = (contract.contract_no or "").strip()
    if contract_no:
        next_contracts = (
            db.query(Contract)
            .filter(
                Contract.prev_contract_no == contract_no,
                Contract.building_id == contract.building_id,
                Contract.is_deleted.is_(False),
            )
            .order_by(Contract.start_date.asc(), Contract.id.asc())
            .all()
        )
    data["previous_contract"] = serialize_contract_lifecycle_item(previous) if previous else None
    data["next_contracts"] = [serialize_contract_lifecycle_item(item) for item in next_contracts]
    return data


def _decision_follow_up_out(item: CustomerFollowUp | None) -> dict | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "customer_id": item.customer_id,
        "contract_id": item.contract_id,
        "follow_up_type": item.follow_up_type or "",
        "occurred_at": item.occurred_at.isoformat() if item.occurred_at else "",
        "next_action": item.next_action or "",
        "next_follow_up_date": item.next_follow_up_date.isoformat() if item.next_follow_up_date else "",
        "owner": item.owner or "",
        "status": item.status or "open",
    }


def build_scoped_contract_decision_card(
    db: Session,
    contract_id: int,
    scope: SelectedPropertyScope,
) -> dict:
    contract = get_contract(db, contract_id, scope=scope)
    from app.services.contract_decision_card import build_contract_decision_card

    return build_contract_decision_card(
        db,
        contract.id,
        building_ids=scope.building_ids,
    )










def _room_payload_to_dict(item) -> dict:
    data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
    room_no = str(data.get("room_no") or "").strip()
    if not room_no:
        raise HTTPException(400, "房源明细室号不能为空")
    area = float(data.get("area") or 0)
    unit_price = float(data.get("unit_price") or 0)
    monthly_rent = calculate_monthly_rent(area, unit_price)
    start_date = parse_date(data.get("start_date"))
    end_date = parse_date(data.get("end_date"))
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400, f"房源 {room_no} 终止日不能早于起租日")
    if area < 0:
        raise HTTPException(400, f"房源 {room_no} 面积不能为负数")
    if unit_price < 0:
        raise HTTPException(400, f"房源 {room_no} 单价不能为负数")
    return {
        "room_id": data.get("room_id"),
        "tower": _tower_text(data.get("tower")),
        "room_no": room_no,
        "area": area,
        "unit_price": unit_price,
        "monthly_rent": monthly_rent,
        "start_date": start_date,
        "end_date": end_date,
        "notes": str(data.get("notes") or ""),
    }


def _fallback_room_item(data: dict) -> list[dict]:
    room_no = str(data.get("room_no") or "").strip()
    if not room_no:
        return []
    area = float(data.get("area") or 0)
    unit_price = float(data.get("unit_price") or 0)
    return [
        {
            "room_id": None,
            "tower": _tower_text(data.get("tower")),
            "room_no": room_no,
            "area": area,
            "unit_price": unit_price,
            "monthly_rent": calculate_monthly_rent(area, unit_price),
            "start_date": None,
            "end_date": None,
            "notes": "",
        }
    ]


def _split_import_room_numbers(value) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [part.strip() for part in IMPORT_ROOM_SPLIT_RE.split(raw) if part.strip()]


def _import_room_items_from_row(
    db: Session,
    data: dict,
    room_start_date: date | None = None,
    room_end_date: date | None = None,
) -> list[dict]:
    room_numbers = _split_import_room_numbers(data.get("room_no"))
    if not room_numbers:
        return []
    if len(set(room_numbers)) != len(room_numbers):
        raise HTTPException(400, "导入房源明细室号重复")

    rooms = (
        db.query(Room)
        .filter(
            Room.building_id == data.get("building_id"),
            Room.is_deleted.is_(False),
            Room.room_no.in_(room_numbers),
        )
        .all()
    )
    rooms_by_no = {room.room_no: room for room in rooms}
    missing = [room_no for room_no in room_numbers if room_no not in rooms_by_no]
    if len(room_numbers) > 1 and missing:
        raise HTTPException(400, f"多房源导入时室号 {', '.join(missing)} 未在房源清单中，无法自动拆分")

    row_area = float(data.get("area") or 0)
    unit_price = float(data.get("unit_price") or 0)
    row_tower = _tower_text(data.get("tower"))
    items = []
    for room_no in room_numbers:
        room = rooms_by_no.get(room_no)
        area = float(room.area or 0) if room else row_area
        tower = _tower_text(room.tower) if room else row_tower
        items.append(
            {
                "room_id": room.id if room else None,
                "tower": tower,
                "room_no": room_no,
                "area": area,
                "unit_price": unit_price,
                "monthly_rent": calculate_monthly_rent(area, unit_price),
                "start_date": room_start_date,
                "end_date": room_end_date,
                "notes": "",
            }
        )
    return items


def _normalize_room_items_from_data(data: dict, fallback_data: dict | None = None) -> list[dict]:
    raw_items = data.pop("room_items", None)
    if raw_items is not None:
        if not raw_items:
            raise HTTPException(400, "合同至少需要一条房源明细")
        items = [_room_payload_to_dict(item) for item in raw_items]
    else:
        items = _fallback_room_item(fallback_data or data)

    seen = set()
    for item in items:
        if item["room_no"] in seen:
            raise HTTPException(400, f"房源明细室号 {item['room_no']} 重复")
        seen.add(item["room_no"])
    return items


def _sync_contract_summary_from_rooms(data: dict, room_items: list[dict]) -> None:
    if not room_items:
        _sync_monthly_rent(data)
        return

    total_area = round_money(sum(item["area"] for item in room_items))
    total_monthly_rent = round_money(sum(item["monthly_rent"] for item in room_items))
    towers = {_tower_text(item.get("tower")) for item in room_items if item.get("tower")}
    data["room_no"] = "、".join(item["room_no"] for item in room_items)
    data["tower"] = next(iter(towers)) if len(towers) == 1 else "多房源"
    data["area"] = total_area
    data["monthly_rent"] = total_monthly_rent
    data["unit_price"] = round_money(total_monthly_rent * 12 / 365 / total_area) if total_area > 0 else 0


def _room_item_effective_period_from_data(contract_data: dict, item: dict) -> tuple[date | None, date | None]:
    return item.get("start_date") or contract_data.get("start_date"), item.get("end_date") or contract_data.get("end_date")


def _validate_room_item_periods(contract_data: dict, room_items: list[dict]) -> None:
    contract_start = contract_data.get("start_date")
    contract_end = contract_data.get("end_date")
    for item in room_items:
        room_no = item.get("room_no") or ""
        item_start = item.get("start_date")
        item_end = item.get("end_date")
        effective_start, effective_end = _room_item_effective_period_from_data(contract_data, item)
        if effective_start and effective_end and effective_end < effective_start:
            raise HTTPException(400, f"房源 {room_no} 终止日不能早于起租日")
        if item_start and contract_start and item_start < contract_start:
            raise HTTPException(400, f"房源 {room_no} 起租日不能早于合同起租日")
        if item_end and contract_end and item_end > contract_end:
            raise HTTPException(400, f"房源 {room_no} 终止日不能晚于合同终止日")


def _stored_room_items_to_dicts(contract: Contract) -> list[dict]:
    return [
        {
            "id": item.id,
            "building_id": item.building_id,
            "room_id": item.room_id,
            "tower": contract_tower_value(contract, item.tower),
            "room_no": item.room_no or "",
            "area": item.area or 0,
            "unit_price": item.unit_price or 0,
            "monthly_rent": item.monthly_rent or calculate_monthly_rent(item.area, item.unit_price),
            "start_date": item.start_date,
            "end_date": item.end_date,
            "notes": item.notes or "",
        }
        for item in getattr(contract, "room_items", []) or []
        if item.room_no
    ]


def _replace_contract_rooms(db: Session, contract: Contract, room_items: list[dict]) -> None:
    scoped_contract, room_items = prepare_detail_contract_write(
        db,
        {"building_id": contract.building_id},
        room_items,
        existing=contract,
    )
    contract.building_id = scoped_contract["building_id"]
    contract.room_items.clear()
    db.flush()
    for item in room_items:
        contract.room_items.append(
            ContractRoom(
                building_id=item["building_id"],
                room_id=item["room_id"],
                tower=item["tower"],
                room_no=item["room_no"],
                area=item["area"],
                unit_price=item["unit_price"],
                monthly_rent=item["monthly_rent"],
                start_date=item.get("start_date"),
                end_date=item.get("end_date"),
                notes=item.get("notes") or "",
            )
        )




def _iter_contract_room_context(
    contract: Contract,
    rooms_by_no: dict[tuple[int, str], Room],
    window_start: date | None = None,
    window_end: date | None = None,
):
    items = contract_room_items(contract, window_start, window_end)
    if not items:
        return
    for item in items:
        room = rooms_by_no.get(
            (int(contract.building_id) if contract.building_id is not None else -1, item["room_no"] or "")
        )
        building_code = str(getattr(getattr(contract, "building", None), "code", "") or "")
        if building_code.casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold():
            group = leasing_group(
                item.get("tower") or contract.tower,
                floor=room.floor if room else None,
                room_type=room.room_type if room else None,
                room_no=item.get("room_no"),
            )
        else:
            group = _tower_text(item.get("tower") or contract.tower) or "未标注"
        yield item, room, group


def _find_overlapping_room_contract(
    db: Session,
    *,
    building_id: int,
    room_no: str,
    start_date: date | None,
    end_date: date | None,
    exclude_id: int | None = None,
) -> Contract | None:
    if not room_no or not start_date or not end_date:
        return None

    room_start = func.coalesce(ContractRoom.start_date, Contract.start_date)
    room_end = func.coalesce(ContractRoom.end_date, Contract.end_date)
    query = (
        db.query(Contract)
        .join(ContractRoom, ContractRoom.contract_id == Contract.id)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.building_id == building_id,
            ContractRoom.building_id == building_id,
            ContractRoom.room_no == room_no,
            room_start.isnot(None),
            room_end.isnot(None),
            room_start <= end_date,
            room_end >= start_date,
        )
    )
    if exclude_id is not None:
        query = query.filter(Contract.id != exclude_id)
    overlap = query.first()
    if overlap:
        return overlap
    return _find_overlapping_contract(
        db,
        building_id=building_id,
        room_no=room_no,
        start_date=start_date,
        end_date=end_date,
        exclude_id=exclude_id,
    )


def _require_contract_room_period(contract: Contract) -> None:
    if not contract.start_date or not contract.end_date:
        raise HTTPException(400, "合同起止日期不完整，不能执行房源变更")


def _apply_contract_summary_from_room_items(contract: Contract) -> None:
    summary = contract_room_summary(contract)
    contract.tower = summary["tower"]
    contract.room_no = summary["room_no"]
    contract.area = summary["area"]
    contract.unit_price = summary["unit_price"]
    contract.monthly_rent = summary["monthly_rent"]


def _get_contract_room_item(contract: Contract, room_item_id: int) -> ContractRoom:
    for item in contract.room_items or []:
        if item.id == room_item_id:
            return item
    raise HTTPException(404, "合同房源明细不存在")


def _prepare_lifecycle_room_item(db: Session, contract: Contract, payload: ContractRoomPayload) -> dict:
    _require_contract_room_period(contract)
    item = _room_payload_to_dict(payload)
    if not item.get("start_date") or not item.get("end_date"):
        raise HTTPException(400, f"房源 {item['room_no']} 必须填写起止日期")
    if (item.get("unit_price") or 0) <= 0:
        raise HTTPException(400, f"房源 {item['room_no']} 必须填写有效单价")

    room = db.query(Room).filter(
        Room.building_id == contract.building_id,
        Room.room_no == item["room_no"],
        Room.is_deleted.is_(False),
    ).first()
    if room:
        item["room_id"] = room.id
        item["tower"] = item["tower"] or _tower_text(room.tower)
        if not item.get("area"):
            item["area"] = float(room.area or 0)
        item["monthly_rent"] = calculate_monthly_rent(item["area"], item["unit_price"])

    scoped_contract, scoped_items = prepare_detail_contract_write(
        db,
        {"building_id": contract.building_id},
        [item],
        existing=contract,
    )
    contract.building_id = scoped_contract["building_id"]
    item = scoped_items[0]

    _validate_room_item_periods({"start_date": contract.start_date, "end_date": contract.end_date}, [item])
    return item


def _ensure_lifecycle_room_item_available(
    db: Session,
    contract: Contract,
    item: dict,
    *,
    exclude_room_item_id: int | None = None,
) -> None:
    item_start, item_end = _room_item_effective_period_from_data(
        {"start_date": contract.start_date, "end_date": contract.end_date},
        item,
    )
    overlapping_contract = _find_overlapping_room_contract(
        db,
        building_id=int(contract.building_id),
        room_no=item["room_no"],
        start_date=item_start,
        end_date=item_end,
        exclude_id=contract.id,
    )
    if overlapping_contract:
        raise HTTPException(400, f"房源 {item['room_no']} 在该期间已关联合同 {overlapping_contract.contract_no}")

    for existing in contract.room_items or []:
        if existing.id == exclude_room_item_id or existing.room_no != item["room_no"]:
            continue
        existing_start, existing_end = contract_room_effective_period(contract, existing)
        if _periods_overlap(existing_start, existing_end, item_start, item_end):
            raise HTTPException(400, f"房源 {item['room_no']} 在本合同内已有重叠明细")


def _validate_room_item_cutoff(contract: Contract, room_item: ContractRoom, end_date: date | None) -> date:
    if not end_date:
        raise HTTPException(400, "房源截止日期不能为空")
    effective_start, effective_end = contract_room_effective_period(contract, room_item)
    if not effective_start or not effective_end:
        raise HTTPException(400, "房源明细有效期不完整")
    if end_date < effective_start:
        raise HTTPException(400, "房源截止日期不能早于房源起租日")
    if end_date > effective_end:
        raise HTTPException(400, "房源截止日期不能晚于当前房源终止日")
    return end_date


def _append_contract_room(contract: Contract, item: dict) -> ContractRoom:
    room_item = ContractRoom(
        building_id=item.get("building_id") or contract.building_id,
        room_id=item.get("room_id"),
        tower=item.get("tower") or "",
        room_no=item["room_no"],
        area=item["area"],
        unit_price=item["unit_price"],
        monthly_rent=item["monthly_rent"],
        start_date=item.get("start_date"),
        end_date=item.get("end_date"),
        notes=item.get("notes") or "",
    )
    contract.room_items.append(room_item)
    return room_item


def _materialize_legacy_contract_room(db: Session, contract: Contract) -> None:
    if contract.room_items or not contract.room_no:
        return
    scoped_contract, room_items = prepare_detail_contract_write(
        db,
        {"building_id": contract.building_id},
        _fallback_room_item(
            {
                "tower": contract.tower,
                "room_no": contract.room_no,
                "area": contract.area,
                "unit_price": contract.unit_price,
            }
        ),
        existing=contract,
    )
    contract.building_id = scoped_contract["building_id"]
    for item in room_items:
        _append_contract_room(contract, item)


def _sync_signing_type(data: dict) -> None:
    data["lease_type"] = normalize_signing_type(data.get("lease_type"))


def _payload_fields(payload) -> set[str]:
    fields = getattr(payload, "model_fields_set", None)
    if fields is None:
        fields = getattr(payload, "__fields_set__", set())
    return set(fields or set())






















































































def _validate_contract_values(data: dict) -> None:
    if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
        raise HTTPException(400, "合同结束日期不能早于开始日期")
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


def _legacy_deposit_from_contract_data(data: dict) -> ContractDeposit | None:
    expected = round(float(data.get("deposit") or 0), 2)
    if expected <= 0:
        return None
    received = expected if (data.get("deposit_status") or "") in {"", "已收"} else 0
    return ContractDeposit(
        customer_id=data.get("customer_id"),
        deposit_type="租赁押金",
        expected_amount=expected,
        received_amount=received,
        refunded_amount=0,
        offset_amount=0,
        status=deposit_status(expected, received, 0, 0),
        notes="从合同押金字段初始化",
    )


def _find_overlapping_contract(
    db: Session,
    *,
    building_id: int,
    room_no: str,
    start_date: date | None,
    end_date: date | None,
    exclude_id: int | None = None,
) -> Contract | None:
    if not room_no or not start_date or not end_date:
        return None
    query = db.query(Contract).filter(
        Contract.is_deleted.is_(False),
        Contract.building_id == building_id,
        Contract.room_no == room_no,
        ~Contract.room_items.any(),
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






































def _normalize_stats_building_ids(building_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if building_ids is None:
        return None
    return tuple(dict.fromkeys(int(item) for item in building_ids))


def get_contract_summary(
    db: Session,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    """Read contract presence for internal portfolio facts, preserving graph guards."""
    scoped_building_ids = _normalize_stats_building_ids(building_ids)
    contract_query = db.query(Contract).filter(Contract.is_deleted.is_(False))
    if scoped_building_ids is not None:
        contract_query = contract_query.filter(
            Contract.building_id.in_(scoped_building_ids or (-1,))
        ).populate_existing()
    contracts = contract_query.all()
    # Validate every in-scope contract, including ones excluded from rent
    # analytics because dates, area or rent terms are unavailable.
    _assert_contract_room_graphs(db, contracts)
    return {"total": len(contracts)}


@reuse_read_calculations
def get_contract_stats(
    db: Session,
    *,
    building_ids: Iterable[int] | None = None,
) -> dict:
    scoped_building_ids = _normalize_stats_building_ids(building_ids)
    contract_query = db.query(Contract).options(selectinload(Contract.deposits)).filter(Contract.is_deleted.is_(False))
    if scoped_building_ids is not None:
        contract_query = contract_query.options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items).selectinload(ContractRoom.room),
            selectinload(Contract.rent_terms),
            with_loader_criteria(
                ContractRoom,
                ContractRoom.building_id.in_(scoped_building_ids or (-1,)),
                include_aliases=True,
            ),
        ).filter(Contract.building_id.in_(scoped_building_ids or (-1,)))
        contract_query = contract_query.populate_existing()
    contracts = contract_query.all()
    _assert_contract_room_graphs(db, contracts)
    today = today_in_timezone()
    active = [item for item in contracts if _is_currently_active(item)]
    active_summaries = {item.id: contract_room_summary(item, today, today) for item in active}

    statuses = {}
    expiring_soon = []
    for contract in contracts:
        statuses[contract.status] = statuses.get(contract.status, 0) + 1
        if contract.days_to_expiry is not None and 0 < contract.days_to_expiry <= 180:
            summary = contract_room_summary(contract)
            expiring_soon.append(
                {
                    "id": contract.id,
                    "contract_no": contract.contract_no,
                    "room_no": summary["room_no"],
                    "tenant_name": customer_display_name(contract),
                    "internal_contact_person": contract.internal_contact_person or "",
                    "internal_contact_feishu_id": contract.internal_contact_feishu_id or "",
                    "end_date": contract.end_date.isoformat() if contract.end_date else None,
                    "days_left": contract.days_to_expiry,
                }
            )
    expiring_soon.sort(key=lambda item: item["days_left"])

    total_area = sum(active_summaries[item.id]["area"] for item in active)
    total_monthly_rent = sum(active_summaries[item.id]["monthly_rent"] for item in active)
    total_net_rent = sum(contract_effective_net_rent(item) for item in active)
    avg_price = round_money(total_monthly_rent / total_area) if total_area > 0 else 0

    try:
        room_query = db.query(Room).filter(Room.is_leasable.is_(True), Room.is_deleted.is_(False))
        if scoped_building_ids is not None:
            room_query = room_query.filter(Room.building_id.in_(scoped_building_ids or (-1,)))
        rooms = room_query.all()
    except Exception:
        rooms = []
    rooms_by_no = {
        (int(room.building_id), room.room_no): room
        for room in rooms
        if room.building_id is not None and room.room_no
    }
    building_code_by_id = {
        int(building_id): str(code)
        for building_id, code in db.query(Building.id, Building.code)
        .filter(Building.id.in_({int(room.building_id) for room in rooms} or {-1}))
        .all()
    }
    room_total_area = sum(room.area or 0 for room in rooms)
    tower_room_area = {}
    for room in rooms:
        if building_code_by_id.get(int(room.building_id), "").casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold():
            key = leasing_group(room.tower, floor=room.floor, room_type=room.room_type, room_no=room.room_no)
        else:
            key = _tower_text(room.tower) or "未标注"
        tower_room_area[key] = tower_room_area.get(key, 0) + (room.area or 0)

    tower_data = {}
    for contract in active:
        counted_groups = set()
        for item, _room, key in _iter_contract_room_context(contract, rooms_by_no, today, today):
            entry = tower_data.setdefault(key, {"count": 0, "area": 0, "rent": 0, "room_area": 0})
            if key not in counted_groups:
                entry["count"] += 1
                counted_groups.add(key)
            entry["area"] += item.get("area") or 0
            entry["rent"] += item.get("monthly_rent") or 0
    for key, area in tower_room_area.items():
        entry = tower_data.setdefault(key, {"count": 0, "area": 0, "rent": 0, "room_area": 0})
        entry["room_area"] = round_money(area)

    customer_data = {}
    for contract in active:
        tenant = (customer_display_name(contract) or "").strip() or "未标注客户"
        customer_key = f"customer:{contract.customer_id}" if contract.customer_id else f"legacy:{tenant}"
        entry = customer_data.setdefault(
            customer_key,
            {
                "customer_id": contract.customer_id,
                "tenant_name": tenant,
                "count": 0,
                "area": 0,
                "rent": 0,
                "rooms": set(),
                "groups": set(),
                "nearest_end_date": None,
            },
        )
        entry["count"] += 1
        for item, _room, group in _iter_contract_room_context(contract, rooms_by_no, today, today):
            entry["area"] += item.get("area") or 0
            entry["rent"] += item.get("monthly_rent") or 0
            if item.get("room_no"):
                entry["rooms"].add(item["room_no"])
            if group:
                entry["groups"].add(group)
        if contract.end_date and contract.end_date >= today and (
            entry["nearest_end_date"] is None or contract.end_date < entry["nearest_end_date"]
        ):
            entry["nearest_end_date"] = contract.end_date
    customer_rows = []
    for entry in customer_data.values():
        customer_rows.append(
            {
                "tenant_name": entry["tenant_name"],
                "customer_id": entry["customer_id"],
                "count": entry["count"],
                "area": round_money(entry["area"]),
                "rent": round_money(entry["rent"]),
                "room_count": len(entry["rooms"]),
                "rooms": sorted(entry["rooms"]),
                "groups": sorted(entry["groups"]),
                "nearest_end_date": entry["nearest_end_date"].isoformat() if entry["nearest_end_date"] else None,
            }
        )
    customer_rows.sort(key=lambda item: (-item["area"], -item["rent"], item["tenant_name"]))

    lease_types = {}
    for contract in active:
        key = normalize_signing_type(contract.lease_type)
        lease_types[key] = lease_types.get(key, 0) + 1

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

    total_deposit = 0.0
    for item in contracts:
        deposits = list(getattr(item, "deposits", []) or [])
        if deposits:
            total_deposit += deposit_summary(deposits)["available_amount"]
        elif item.deposit_status in {"已收", ""}:
            total_deposit += item.deposit or 0
    disposals = {}
    for contract in contracts:
        if contract.status in (FINAL_DISPOSAL_STATUSES | {"已到期"}):
            key = contract.disposal or "未确定"
            disposals[key] = disposals.get(key, 0) + 1

    occupancy_rate = round(total_area / room_total_area * 100, 1) if room_total_area > 0 else 0

    total_property_fee = sum(contract_monthly_property_fee(item, active_summaries[item.id]) for item in active)
    total_net_property_fee = sum(contract_monthly_property_fee(item, active_summaries[item.id]) for item in active)

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
        snapshot_summaries = {item.id: contract_room_summary(item, month_start, month_end) for item in snapshot_contracts}
        snapshot_area = sum(snapshot_summaries[item.id]["area"] for item in snapshot_contracts)
        snapshot_rent = sum(_month_rent_detail(item, month_start)["rent"] for item in snapshot_contracts)
        snapshot_rate = round(snapshot_area / room_total_area * 100, 1) if room_total_area > 0 else 0
        return {
            "area": round_money(snapshot_area),
            "rent": round_money(snapshot_rent),
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
        summary = contract_room_summary(contract)
        if contract.start_date and summary["unit_price"] and summary["unit_price"] > 0:
            price_by_month[contract.start_date.strftime("%Y-%m")].append(summary["unit_price"])
    price_trend = [
        {"month": month_key, "avg_price": round_money(sum(values) / len(values)), "count": len(values)}
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
                    monthly_income += _month_rent_detail(contract, current)["rent"]
        income_forecast.append({"month": current.strftime("%Y-%m"), "income": round_money(monthly_income)})

    if scoped_building_ids is None:
        current_receivables = get_monthly_receivables(db, today.strftime("%Y-%m"))
        receivable_trend = get_receivable_trend(db, today, months=6)
    else:
        trend_months = [
            (today.replace(day=1) + relativedelta(months=offset)).strftime("%Y-%m")
            for offset in range(-5, 1)
        ]
        scoped_reports = get_monthly_receivables_batch(db, trend_months, contracts=contracts)
        current_receivables = scoped_reports[today.strftime("%Y-%m")]
        receivable_trend = [
            {
                "month": report["month"],
                "total_rent": report["total_rent"],
                "total_received": report["total_received"],
                "total_adjustment": report["total_adjustment"],
                "total_balance": report["total_balance"],
                "total_unpaid_balance": report["total_unpaid_balance"],
                "total_overdue_balance": report["total_overdue_balance"],
                "total_not_due_balance": report["total_not_due_balance"],
                "received_rate": report["received_rate"],
                "unpaid_count": report["unpaid_count"],
                "overdue_count": report["overdue_count"],
            }
            for report in (scoped_reports[month] for month in trend_months)
        ]

    return {
        "total": len(contracts),
        "active_count": len(active),
        "statuses": statuses,
        "expiring_soon": expiring_soon,
        "metrics": {
            "total_area": round_money(total_area),
            "total_monthly_rent": round_money(total_monthly_rent),
            "total_net_rent": round_money(total_net_rent),
            "avg_price": avg_price,
            "total_deposit": round_money(total_deposit),
            "room_total_area": round_money(room_total_area),
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
        "customer_data": customer_rows,
        "lease_types": lease_types,
        "expiry_months": expiry_months,
        "metrics_extra": {
            "total_property_fee": round_money(total_property_fee),
            "total_net_property_fee": round_money(total_net_property_fee),
        },
        "term_dist": term_dist,
        "price_trend": price_trend,
        "income_forecast": income_forecast,
        "receivables": {
            "month": current_receivables["month"],
            "total_rent": current_receivables["total_rent"],
            "total_received": current_receivables["total_received"],
            "total_balance": current_receivables["total_balance"],
            "total_unpaid_balance": current_receivables["total_unpaid_balance"],
            "total_overdue_balance": current_receivables["total_overdue_balance"],
            "total_not_due_balance": current_receivables["total_not_due_balance"],
            "received_rate": current_receivables["received_rate"],
            "unpaid_count": current_receivables["unpaid_count"],
            "overdue_count": current_receivables["overdue_count"],
            "aging_buckets": current_receivables["aging_buckets"],
            "key_debtors": current_receivables["key_debtors"],
            "dunning_alerts": current_receivables["dunning_alerts"],
            "trend": receivable_trend,
        },
    }


def _prepare_contract_create(
    db: Session,
    payload: ContractCreate,
    *,
    scope: SelectedPropertyScope,
    exclude_overlap_contract_id: int | None = None,
) -> tuple[dict, list[dict]]:
    building_id = _require_contract_detail_write_scope(db, scope)
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    provided_fields = _payload_fields(payload)
    contract_no = (data.get("contract_no") or "").strip()
    data["tower"] = _tower_text(data.get("tower"))
    data["building_id"] = building_id
    _sync_signing_type(data)
    room_items = _normalize_room_items_from_data(data)
    data, room_items = prepare_detail_contract_write(db, data, room_items)
    _sync_contract_summary_from_rooms(data, room_items)
    resolve_contract_customer(db, data, building_ids=scope.building_ids)
    if "broker_fee" not in provided_fields or data.get("broker_fee") is None:
        data["broker_fee"] = calculate_broker_fee(data.get("monthly_rent"))
    if not contract_no:
        raise HTTPException(400, "合同编号不能为空")
    _validate_contract_values(data)
    _validate_room_item_periods(data, room_items)
    if db.query(Contract).filter(Contract.contract_no == contract_no).first():
        raise HTTPException(400, f"合同编号 {contract_no} 已存在")

    data["contract_no"] = contract_no
    data["property_fee_free"] = 0
    for item in room_items:
        item_start, item_end = _room_item_effective_period_from_data(data, item)
        overlapping = _find_overlapping_room_contract(
            db,
            building_id=building_id,
            room_no=item["room_no"],
            start_date=item_start,
            end_date=item_end,
            exclude_id=exclude_overlap_contract_id,
        )
        if overlapping:
            raise HTTPException(400, f"室号 {item['room_no']} 在该租期内已关联合同 {overlapping.contract_no}")
    return data, room_items


def _receivable_month_impacts(db: Session, contract: Contract, start: date, end: date) -> list[dict]:
    impacts = []
    current = start.replace(day=1)
    last = end.replace(day=1)
    while current <= last:
        month = current.strftime("%Y-%m")
        record = (
            db.query(ReceivableRecord)
            .filter(ReceivableRecord.contract_id == contract.id, ReceivableRecord.period_month == month)
            .first()
        )
        payments = (
            db.query(ReceivablePayment)
            .filter(ReceivablePayment.contract_id == contract.id, ReceivablePayment.period_month == month)
            .all()
        )
        payment_summary = _payment_summary(payments, record)
        if record or payments:
            impacts.append(
                {
                    "month": month,
                    "has_record": bool(record),
                    "payment_count": payment_summary["payment_count"],
                    "received_amount": payment_summary["received_amount"],
                    "adjustment_amount": round_money(payment_summary["adjustment_amount"]),
                    "settlement_amount": round_money(payment_summary["settlement_amount"]),
                }
            )
        current += relativedelta(months=1)
    return impacts


def build_contract_change_preview(
    db: Session,
    payload: ContractCreate,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id = _require_contract_detail_write_scope(db, scope)
    lease_type = normalize_signing_type(payload.lease_type)
    if lease_type != "变更签":
        raise HTTPException(400, "只有变更签需要生效确认")
    prev_contract_no = (payload.prev_contract_no or "").strip()
    if not prev_contract_no:
        raise HTTPException(400, "变更签必须填写关联上份合同编号")
    if not payload.start_date:
        raise HTTPException(400, "变更签必须填写生效日期")

    previous = db.query(Contract).filter(
        Contract.contract_no == prev_contract_no,
        Contract.building_id == building_id,
        Contract.is_deleted.is_(False),
    ).first()
    if not previous:
        raise HTTPException(404, "上份合同不存在或无权访问")
    assert_contract_in_scope(db, previous, scope, for_write=True)
    if not previous.start_date or not previous.end_date:
        raise HTTPException(400, f"上份合同 {prev_contract_no} 租期不完整")
    if payload.start_date <= previous.start_date:
        raise HTTPException(400, "变更生效日必须晚于上份合同起租日")
    if payload.start_date > previous.end_date:
        raise HTTPException(400, "变更生效日晚于上份合同终止日，不需要截断旧合同")

    data, room_items = _prepare_contract_create(
        db,
        payload,
        scope=scope,
        exclude_overlap_contract_id=previous.id,
    )
    cutoff_date = payload.start_date - timedelta(days=1)
    room_no = "、".join(item["room_no"] for item in room_items if item.get("room_no"))
    return {
        "requires_confirmation": True,
        "previous_contract": serialize_contract_lifecycle_item(previous),
        "new_contract": {
            "contract_no": data["contract_no"],
            "tenant_name": data.get("tenant_name") or "",
            "lease_type": data.get("lease_type") or "",
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "room_no": room_no,
        },
        "change_effective_date": payload.start_date,
        "previous_end_date_before": previous.end_date,
        "previous_end_date_after": cutoff_date,
        "receivable_impacts": _receivable_month_impacts(db, previous, payload.start_date, previous.end_date),
        "receivable_policy": "不会自动生成冲抵、减免或坏账记录；已有收款/核销仍保留在旧合同对应月份，需在月度应收中人工调整。",
    }


CONTRACT_AUDIT_FIELDS = {
    "contract_no",
    "tenant_name",
    "tower",
    "room_no",
    "lease_type",
    "start_date",
    "end_date",
    "monthly_rent",
    "property_fee_rate",
    "deposit",
    "prev_contract_no",
}


def _contract_create_audit_summary(data: dict, room_items: list[dict] | None = None) -> dict:
    summary = {key: data.get(key) for key in CONTRACT_AUDIT_FIELDS if key in data}
    rooms = [item.get("room_no") for item in (room_items or []) if item.get("room_no")]
    if rooms:
        summary["rooms"] = "、".join(rooms)
    return summary


def _contract_update_audit_summary(contract: Contract, changes: dict) -> dict:
    visible_changes = {key: value for key, value in changes.items() if key in CONTRACT_AUDIT_FIELDS}
    return {
        "contract_no": contract.contract_no or "",
        "tenant_name": customer_display_name(contract),
        "changed_fields": sorted(changes),
        "changes": visible_changes,
    }


def confirm_contract_change(
    db: Session,
    payload: ContractCreate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id = _require_contract_detail_write_scope(db, scope)
    preview = build_contract_change_preview(db, payload, scope=scope)
    previous = db.query(Contract).filter(
        Contract.contract_no == payload.prev_contract_no,
        Contract.building_id == building_id,
        Contract.is_deleted.is_(False),
    ).first()
    if not previous:
        raise HTTPException(404, "上份合同不存在或无权访问")
    assert_contract_in_scope(db, previous, scope, for_write=True)
    data, room_items = _prepare_contract_create(
        db,
        payload,
        scope=scope,
        exclude_overlap_contract_id=previous.id,
    )
    cutoff_date = data["start_date"] - timedelta(days=1)

    try:
        previous_changes = {
            "end_date": {"old": str(previous.end_date), "new": str(cutoff_date)},
            "disposal": {"old": previous.disposal or "", "new": "已变更"},
            "successor_contract_no": data["contract_no"],
        }
        previous.end_date = cutoff_date
        previous.disposal = "已变更"
        previous.updated_at = now_in_timezone()
        for room_item in previous.room_items:
            if room_item.end_date is None or room_item.end_date > cutoff_date:
                room_item.end_date = cutoff_date

        contract = Contract(**data)
        legacy_deposit = _legacy_deposit_from_contract_data(data)
        if legacy_deposit:
            contract.deposits.append(legacy_deposit)
        db.add(contract)
        db.flush()
        _replace_contract_rooms(db, contract, room_items)
        log_action(
            db,
            previous.id,
            "change_confirm",
            dump_changes(previous_changes),
            operator,
            commit=False,
            object_scope=_contract_object_scope(previous),
        )
        log_action(
            db,
            contract.id,
            "create",
            dump_changes(_contract_create_audit_summary(data, room_items)),
            operator,
            commit=False,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(contract)
    except Exception:
        db.rollback()
        raise

    result = serialize_contract(contract)
    result["change_preview"] = preview
    return result


def create_contract(
    db: Session,
    payload: ContractCreate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    with _CONTRACT_WRITE_LOCK:
        return _create_contract_locked(db, payload, operator, scope=scope)


def _create_contract_locked(
    db: Session,
    payload: ContractCreate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id = _require_contract_detail_write_scope(db, scope)
    data, room_items = _prepare_contract_create(db, payload, scope=scope)
    contract = Contract(**data)
    legacy_deposit = _legacy_deposit_from_contract_data(data)
    if legacy_deposit:
        contract.deposits.append(legacy_deposit)
    # 合同主体、房源明细、前合同联动与审计日志同一事务提交，
    # 避免中途失败留下"合同已建但无审计/前合同未联动"的中间态
    try:
        db.add(contract)
        db.flush()
        _replace_contract_rooms(db, contract, room_items)

        if payload.prev_contract_no:
            previous = (
                db.query(Contract)
                .filter(
                    Contract.contract_no == payload.prev_contract_no,
                    Contract.building_id == building_id,
                    Contract.is_deleted.is_(False),
                )
                .first()
            )
            if previous and previous.disposal not in {"已续租", "已退租", "已变更"}:
                assert_contract_in_scope(db, previous, scope, for_write=True)
                previous.disposal = "已续租"
                previous.updated_at = now_in_timezone()
                log_action(
                    db,
                    previous.id,
                    "update",
                    f"因新合同 {contract.contract_no} 生效，自动标记为已续租",
                    operator,
                    commit=False,
                    object_scope=_contract_object_scope(previous),
                )

        log_action(
            db,
            contract.id,
            "create",
            dump_changes(_contract_create_audit_summary(data, room_items)),
            operator,
            commit=False,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(contract)
    return serialize_contract(contract)


def build_successor_contract_draft(
    db: Session,
    contract: Contract,
    lease_type: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope)
    draft = serialize_contract(contract)
    start_date = None
    end_date = None
    if contract.end_date:
        start_date = contract.end_date + relativedelta(days=1)
        end_date = start_date + relativedelta(years=1, days=-1)

    draft.update(
        {
            "id": None,
            "contract_no": next_contract_no(db, scope=scope)["next_no"],
            "lease_type": normalize_signing_type(lease_type),
            "broker_fee": 0,
            "start_date": start_date,
            "end_date": end_date,
            "status": "待生效" if start_date else "信息不全",
            "days_to_expiry": None,
            "disposal": "",
            "prev_contract_no": contract.contract_no or "",
            "pdf_path": "",
            "created_at": None,
            "updated_at": None,
            "last_updated": "",
        }
    )
    for item in draft.get("room_items", []):
        item["start_date"] = start_date
        item["end_date"] = end_date
    return draft


def update_contract(
    db: Session,
    contract: Contract,
    payload: ContractUpdate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    with _CONTRACT_WRITE_LOCK:
        return _update_contract_locked(db, contract, payload, operator, scope=scope)


def _update_contract_locked(
    db: Session,
    contract: Contract,
    payload: ContractUpdate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    room_items_requested = "room_items" in data
    if "tower" in data:
        data["tower"] = _tower_text(data.get("tower"))
    if "lease_type" in data:
        _sync_signing_type(data)
    if payload.last_updated:
        last_updated = contract.updated_at.isoformat() if contract.updated_at else ""
        if payload.last_updated != last_updated:
            raise HTTPException(409, "该合同已被其他用户修改，请刷新后重试")

    if contract.status in {"在租", "即将到期"}:
        locked_fields = {"start_date": "起租日", "end_date": "终止日", "monthly_rent": "月租金", "area": "面积", "unit_price": "单价", "room_items": "房源明细"}
        for field, label in locked_fields.items():
            if field in data and getattr(contract, field) is not None and str(data[field]) != str(getattr(contract, field)):
                raise HTTPException(400, f"合同已生效，{label}不可修改。如需变更请通过续租/变更流程新建合同。")

    room_items = None
    existing_room_items = _stored_room_items_to_dicts(contract)
    scoped_existing_items: list[dict] = []
    if room_items_requested:
        room_items = _normalize_room_items_from_data(data)
        data, room_items = prepare_detail_contract_write(
            db,
            data,
            room_items,
            existing=contract,
        )
        _sync_contract_summary_from_rooms(data, room_items)
    else:
        data, scoped_existing_items = prepare_detail_contract_write(
            db,
            data,
            existing_room_items,
            existing=contract,
        )

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
    if "customer_id" in data:
        resolve_contract_customer(db, data, building_ids=scope.building_ids)

    candidate = {
        "start_date": data.get("start_date", contract.start_date),
        "end_date": data.get("end_date", contract.end_date),
        "tower": data.get("tower", contract.tower),
        "room_no": data.get("room_no", contract.room_no),
        "monthly_rent": data.get("monthly_rent", contract.monthly_rent),
        "area": data.get("area", contract.area),
        "unit_price": data.get("unit_price", contract.unit_price),
        "broker_fee": data.get("broker_fee", contract.broker_fee),
        "deposit": data.get("deposit", contract.deposit),
        "property_fee_rate": data.get("property_fee_rate", contract.property_fee_rate),
        "free_period": data.get("free_period", contract.free_period),
    }
    if room_items:
        _sync_contract_summary_from_rooms(candidate, room_items)
    else:
        _sync_monthly_rent(candidate)
    if {"area", "unit_price", "monthly_rent"} & set(data) and not room_items_requested:
        data["monthly_rent"] = candidate["monthly_rent"]
    _validate_contract_values(candidate)
    overlap_items = room_items if room_items is not None else (existing_room_items or _fallback_room_item(candidate))
    _validate_room_item_periods(candidate, overlap_items)
    for item in overlap_items:
        item_start, item_end = _room_item_effective_period_from_data(candidate, item)
        overlapping = _find_overlapping_room_contract(
            db,
            building_id=int(contract.building_id),
            room_no=item["room_no"],
            start_date=item_start,
            end_date=item_end,
            exclude_id=contract.id,
        )
        if overlapping:
            raise HTTPException(400, f"室号 {item['room_no']} 在该租期内已关联合同 {overlapping.contract_no}")

    settled_before = _settled_month_receivables(db, contract)

    changes = {}
    for key, value in data.items():
        if key in {"last_updated", "status"}:
            continue
        old_value = getattr(contract, key)
        if str(old_value) != str(value):
            changes[key] = {"old": str(old_value), "new": str(value)}
            setattr(contract, key, value)

    contract.updated_at = now_in_timezone()
    if "customer_id" in data:
        for deposit in getattr(contract, "deposits", []) or []:
            deposit.customer_id = contract.customer_id
    if not (getattr(contract, "deposits", []) or []) and {"deposit", "deposit_status"} & set(data):
        legacy_deposit = _legacy_deposit_from_contract_data(
            {
                "customer_id": contract.customer_id,
                "deposit": contract.deposit,
                "deposit_status": contract.deposit_status,
            }
        )
        if legacy_deposit:
            contract.deposits.append(legacy_deposit)
    try:
        if room_items is not None:
            _replace_contract_rooms(db, contract, room_items)
        elif {"room_no", "tower", "area", "unit_price", "monthly_rent"} & set(data):
            _replace_contract_rooms(db, contract, _fallback_room_item(candidate))
        else:
            stored_by_id = {
                item.id: item
                for item in contract.room_items or []
                if item.id is not None
            }
            for scoped_item in scoped_existing_items:
                stored = stored_by_id.get(scoped_item.get("id"))
                if stored is None:
                    continue
                stored.building_id = scoped_item["building_id"]
                stored.room_id = scoped_item["room_id"]
                stored.tower = scoped_item["tower"]
                stored.room_no = scoped_item["room_no"]
        db.flush()
        _ensure_settled_months_unchanged(db, contract, settled_before)
        if changes:
            log_action(
                db,
                contract.id,
                "update",
                dump_changes(_contract_update_audit_summary(contract, changes)),
                operator,
                commit=False,
                object_scope=_contract_object_scope(contract),
            )
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(contract)
    return serialize_contract(contract)


def terminate_contract(
    db: Session,
    contract: Contract,
    payload: ContractTerminatePayload,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    if contract.disposal == "已退租":
        raise HTTPException(400, f"合同 {contract.contract_no} 已标记为已退租")

    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    requested_end_date = data.get("end_date")
    if requested_end_date and contract.start_date and requested_end_date < contract.start_date:
        raise HTTPException(400, "退租日期不能早于起租日")

    changes = {}
    if requested_end_date and str(requested_end_date) != str(contract.end_date):
        changes["end_date"] = {"old": str(contract.end_date), "new": str(requested_end_date)}
        contract.end_date = requested_end_date
        for room_item in contract.room_items:
            if room_item.end_date is None or room_item.end_date > requested_end_date:
                room_item.end_date = requested_end_date
    if contract.disposal != "已退租":
        changes["disposal"] = {"old": contract.disposal or "", "new": "已退租"}
        contract.disposal = "已退租"

    contract.updated_at = now_in_timezone()
    notes = str(data.get("notes") or "").strip()
    change_text = dump_changes(changes) if changes else f"标记合同 {contract.contract_no} 已退租"
    if notes:
        change_text = f"{change_text}；备注：{notes}"
    try:
        log_action(
            db,
            contract.id,
            "terminate",
            change_text,
            operator,
            commit=False,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(contract)
    return serialize_contract(contract)


def _get_rent_term(contract: Contract, term_id: int) -> RentTerm:
    for term in contract.rent_terms or []:
        if term.id == term_id:
            return term
    raise HTTPException(404, "租金条款不存在")


def _rent_term_payload_to_data(contract: Contract, payload: RentTermPayload) -> dict:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    rule_type = str(data.get("rule_type") or "").strip()
    if rule_type not in RENT_TERM_TYPES:
        raise HTTPException(400, "租金条款类型无效")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    if not start_date or not end_date:
        raise HTTPException(400, "租金条款起止日期不能为空")
    if end_date < start_date:
        raise HTTPException(400, "租金条款终止日不能早于起始日")
    if contract.start_date and start_date < contract.start_date:
        raise HTTPException(400, "租金条款起始日不能早于合同起租日")
    if contract.end_date and end_date > contract.end_date:
        raise HTTPException(400, "租金条款终止日不能晚于合同终止日")

    contract_room_id = data.get("contract_room_id")
    room_no = str(data.get("room_no") or "").strip()
    room_item = None
    if contract_room_id:
        room_item = _get_contract_room_item(contract, int(contract_room_id))
        if room_no and room_no != room_item.room_no:
            raise HTTPException(400, "租金条款房源与房源明细不一致")
        room_no = room_item.room_no or ""
    elif room_no:
        matched_items = [item for item in contract.room_items or [] if item.room_no == room_no]
        if not matched_items and contract.room_no != room_no:
            raise HTTPException(400, f"房源 {room_no} 不属于该合同")

    area = data.get("area")
    unit_price = data.get("unit_price")
    monthly_amount = data.get("monthly_amount")
    adjustment_type = str(data.get("adjustment_type") or "").strip() or None
    frequency = str(data.get("frequency") or "").strip() or None
    adjustment_value = data.get("adjustment_value")
    area = float(area) if area is not None else None
    unit_price = float(unit_price) if unit_price is not None else None
    monthly_amount = float(monthly_amount) if monthly_amount is not None else None
    adjustment_value = float(adjustment_value) if adjustment_value is not None else None
    for value, label in ((area, "面积"), (unit_price, "日租单价"), (monthly_amount, "固定月租金"), (adjustment_value, "递增值")):
        if value is not None and value < 0:
            raise HTTPException(400, f"{label}不能为负数")

    fallback_area = area
    if fallback_area is None and room_item:
        fallback_area = float(room_item.area or 0)
    elif fallback_area is None and room_no:
        fallback_area = round(sum(float(item.area or 0) for item in contract.room_items or [] if item.room_no == room_no), 2)
    elif fallback_area is None:
        fallback_area = float(contract_room_summary(contract)["area"] or 0)

    if rule_type == "area_unit_price":
        if not fallback_area or unit_price is None:
            raise HTTPException(400, "按面积单价条款需要有效面积和日租单价")
    if rule_type == "fixed_monthly" and monthly_amount is None:
        raise HTTPException(400, "固定月租条款需要有效月租金")
    if rule_type == "stepped_rent" and not (
        monthly_amount is not None
        or (fallback_area and unit_price is not None)
    ):
        raise HTTPException(400, "阶梯租金需要有效月租金或面积单价")
    if rule_type == "escalation":
        adjustment_type = adjustment_type or "percent"
        frequency = frequency or "once"
        if adjustment_type not in ESCALATION_ADJUSTMENT_TYPES:
            raise HTTPException(400, "递增类型无效")
        if frequency not in ESCALATION_FREQUENCIES:
            raise HTTPException(400, "递增频率无效")
        if not adjustment_value:
            raise HTTPException(400, "递增条款需要有效递增值")
        area = None
        unit_price = None
        monthly_amount = None
    elif rule_type == TEMPORARY_ADJUSTMENT_TERM_TYPE:
        adjustment_type = adjustment_type or "fixed_amount"
        if adjustment_type not in RENT_ADJUSTMENT_TYPES:
            raise HTTPException(400, "临时调整类型无效")
        if not adjustment_value:
            raise HTTPException(400, "临时调整条款需要有效抵减值")
        area = None
        unit_price = None
        monthly_amount = None
        frequency = None
    else:
        adjustment_type = None
        adjustment_value = None
        frequency = None

    return {
        "contract_room_id": int(contract_room_id) if contract_room_id else None,
        "room_no": room_no or None,
        "rule_type": rule_type,
        "start_date": start_date,
        "end_date": end_date,
        "area": area,
        "unit_price": unit_price,
        "monthly_amount": monthly_amount,
        "adjustment_type": adjustment_type,
        "adjustment_value": adjustment_value,
        "frequency": frequency,
        "notes": str(data.get("notes") or ""),
    }


def list_rent_terms(contract: Contract) -> list[dict]:
    return [serialize_rent_term(term) for term in contract.rent_terms or []]


def create_rent_term(db: Session, contract: Contract, payload: RentTermPayload, operator: str) -> dict:
    prepare_detail_contract_record(db, contract)
    data = _rent_term_payload_to_data(contract, payload)
    _ensure_no_base_term_overlap(contract, data)
    settled_before = _settled_month_receivables(db, contract)
    term = RentTerm(contract_id=contract.id, **data)
    try:
        db.add(term)
        contract.updated_at = now_in_timezone()
        db.flush()
        _ensure_settled_months_unchanged(db, contract, settled_before)
        log_action(
            db,
            contract.id,
            "rent_term_create",
            f"新增租金条款 {term.rule_type}: {term.start_date} 至 {term.end_date}",
            operator,
            commit=False,
            target_type="rent_term",
            target_id=term.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(term)
    except Exception:
        db.rollback()
        raise
    return serialize_rent_term(term)


def update_rent_term(db: Session, contract: Contract, term_id: int, payload: RentTermPayload, operator: str) -> dict:
    prepare_detail_contract_record(db, contract)
    term = _get_rent_term(contract, term_id)
    data = _rent_term_payload_to_data(contract, payload)
    _ensure_no_base_term_overlap(contract, data, exclude_term_id=term_id)
    settled_before = _settled_month_receivables(db, contract)
    try:
        for key, value in data.items():
            setattr(term, key, value)
        term.updated_at = now_in_timezone()
        contract.updated_at = now_in_timezone()
        db.flush()
        _ensure_settled_months_unchanged(db, contract, settled_before)
        log_action(
            db,
            contract.id,
            "rent_term_update",
            f"更新租金条款 {term.id}",
            operator,
            commit=False,
            target_type="rent_term",
            target_id=term.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(term)
    except Exception:
        db.rollback()
        raise
    return serialize_rent_term(term)


def delete_rent_term(db: Session, contract: Contract, term_id: int, operator: str) -> dict:
    prepare_detail_contract_record(db, contract)
    term = _get_rent_term(contract, term_id)
    settled_before = _settled_month_receivables(db, contract)
    try:
        db.delete(term)
        contract.updated_at = now_in_timezone()
        db.flush()
        _ensure_settled_months_unchanged(db, contract, settled_before)
        log_action(
            db,
            contract.id,
            "rent_term_delete",
            f"删除租金条款 {term.id}",
            operator,
            commit=False,
            target_type="rent_term",
            target_id=term.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "已删除租金条款"}


def expand_contract_room(
    db: Session,
    contract: Contract,
    payload: ContractRoomPayload,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    item = _prepare_lifecycle_room_item(db, contract, payload)
    _materialize_legacy_contract_room(db, contract)
    _ensure_lifecycle_room_item_available(db, contract, item)

    try:
        room_item = _append_contract_room(contract, item)
        _apply_contract_summary_from_room_items(contract)
        contract.updated_at = now_in_timezone()
        db.flush()
        log_action(
            db,
            contract.id,
            "room_expand",
            f"扩租新增房源 {room_item.room_no}，期间 {room_item.start_date} 至 {room_item.end_date}，单价 {room_item.unit_price}",
            operator,
            commit=False,
            target_type="contract_room",
            target_id=room_item.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(contract)
    except Exception:
        db.rollback()
        raise
    return serialize_contract(contract)


def reduce_contract_room(
    db: Session,
    contract: Contract,
    room_item_id: int,
    payload: ContractRoomReducePayload,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    room_item = _get_contract_room_item(contract, room_item_id)
    cutoff = _validate_room_item_cutoff(contract, room_item, payload.end_date)
    old_end = contract_room_effective_period(contract, room_item)[1]

    try:
        room_item.end_date = cutoff
        _apply_contract_summary_from_room_items(contract)
        contract.updated_at = now_in_timezone()
        notes = str(payload.notes or "").strip()
        change_text = f"减租截断房源 {room_item.room_no}，终止日 {old_end} -> {cutoff}"
        if notes:
            change_text = f"{change_text}；备注：{notes}"
        log_action(
            db,
            contract.id,
            "room_reduce",
            change_text,
            operator,
            commit=False,
            target_type="contract_room",
            target_id=room_item.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(contract)
    except Exception:
        db.rollback()
        raise
    return serialize_contract(contract)


def swap_contract_room(
    db: Session,
    contract: Contract,
    room_item_id: int,
    payload: ContractRoomSwapPayload,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    old_room_item = _get_contract_room_item(contract, room_item_id)
    cutoff = _validate_room_item_cutoff(contract, old_room_item, payload.end_date)
    old_end = contract_room_effective_period(contract, old_room_item)[1]
    new_item = _prepare_lifecycle_room_item(db, contract, payload.new_room)
    if new_item["room_no"] == old_room_item.room_no:
        raise HTTPException(400, "换房的新房源不能与旧房源相同")
    if new_item["start_date"] <= cutoff:
        raise HTTPException(400, "换房新房源起租日必须晚于旧房源截止日")
    _ensure_lifecycle_room_item_available(db, contract, new_item, exclude_room_item_id=room_item_id)

    try:
        old_room_item.end_date = cutoff
        new_room_item = _append_contract_room(contract, new_item)
        _apply_contract_summary_from_room_items(contract)
        contract.updated_at = now_in_timezone()
        notes = str(payload.notes or "").strip()
        change_text = (
            f"换房 {old_room_item.room_no} 截止 {old_end} -> {cutoff}；"
            f"新增 {new_room_item.room_no}，期间 {new_room_item.start_date} 至 {new_room_item.end_date}"
        )
        if notes:
            change_text = f"{change_text}；备注：{notes}"
        log_action(
            db,
            contract.id,
            "room_swap",
            change_text,
            operator,
            commit=False,
            target_type="contract_room",
            target_id=old_room_item.id,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
        db.refresh(contract)
    except Exception:
        db.rollback()
        raise
    return serialize_contract(contract)


def soft_delete_contract(
    db: Session,
    contract: Contract,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    try:
        contract.is_deleted = True
        contract.updated_at = now_in_timezone()
        log_action(
            db,
            contract.id,
            "delete",
            f"软删除合同 {contract.contract_no}",
            operator,
            commit=False,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "已删除"}


def list_recycled_contracts(db: Session, *, scope: SelectedPropertyScope) -> list[dict]:
    selected_ids = tuple(building_id for building_id, _code in _selected_contract_buildings(scope))
    contracts = (
        db.query(Contract)
        .options(selectinload(Contract.room_items).selectinload(ContractRoom.room))
        .filter(Contract.building_id.in_(selected_ids), Contract.is_deleted.is_(True))
        .order_by(Contract.updated_at.desc())
        .all()
    )
    _assert_contracts_in_scope(db, contracts, scope)
    return [serialize_contract(contract) for contract in contracts]


def _scoped_contract_batch(
    db: Session,
    ids: list[int],
    scope: SelectedPropertyScope,
    *,
    deleted: bool,
) -> list[Contract]:
    building_id = _require_contract_detail_write_scope(db, scope)
    normalized_ids = list(dict.fromkeys(int(item) for item in ids))
    if not normalized_ids:
        return []
    contracts = (
        db.query(Contract)
        .filter(
            Contract.id.in_(normalized_ids),
            Contract.building_id == building_id,
            Contract.is_deleted.is_(deleted),
        )
        .all()
    )
    if len(contracts) != len(normalized_ids):
        raise HTTPException(404, "合同不存在或无权访问")
    by_id = {int(contract.id): contract for contract in contracts}
    ordered = [by_id[item_id] for item_id in normalized_ids]
    _assert_contracts_in_scope(db, ordered, scope, for_write=True)
    return ordered


def batch_delete_contracts(
    db: Session,
    ids: list[int],
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    contracts = _scoped_contract_batch(db, ids, scope, deleted=False)
    try:
        for contract in contracts:
            contract.is_deleted = True
            contract.updated_at = now_in_timezone()
            log_action(
                db,
                contract.id,
                "delete",
                f"批量删除合同 {contract.contract_no}",
                operator,
                commit=False,
                object_scope=_contract_object_scope(contract),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    deleted = len(contracts)
    return {"message": f"已删除 {deleted} 条", "deleted": deleted}


def _ensure_restorable_rooms(db: Session, contract: Contract) -> None:
    """恢复软删合同前校验房源占用：删除期间同房源可能已签新合同。"""
    for item in contract_room_items(contract):
        overlapping = _find_overlapping_room_contract(
            db,
            building_id=int(contract.building_id),
            room_no=item["room_no"],
            start_date=item["start_date"],
            end_date=item["end_date"],
            exclude_id=contract.id,
        )
        if overlapping:
            raise HTTPException(
                400,
                f"室号 {item['room_no']} 在该租期内已关联合同 {overlapping.contract_no}，无法恢复；"
                "请先处理占用冲突",
            )


def restore_contract(
    db: Session,
    contract: Contract,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    building_id = assert_contract_in_scope(db, contract, scope, for_write=True)
    conflict = db.query(Contract).filter(
        Contract.contract_no == contract.contract_no,
        Contract.building_id == building_id,
        Contract.is_deleted.is_(False),
    ).first()
    if conflict:
        raise HTTPException(400, f"合同编号 {contract.contract_no} 已被使用，无法恢复")
    try:
        _ensure_restorable_rooms(db, contract)
        contract.is_deleted = False
        contract.updated_at = now_in_timezone()
        log_action(
            db,
            contract.id,
            "restore",
            f"从回收站恢复合同 {contract.contract_no}",
            operator,
            commit=False,
            object_scope=_contract_object_scope(contract),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": f"已恢复合同 {contract.contract_no}"}


def batch_restore_contracts(
    db: Session,
    ids: list[int],
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    candidates = _scoped_contract_batch(db, ids, scope, deleted=True)
    try:
        for contract in candidates:
            conflict = db.query(Contract).filter(
                Contract.contract_no == contract.contract_no,
                Contract.building_id == contract.building_id,
                Contract.is_deleted.is_(False),
            ).first()
            if conflict:
                raise HTTPException(409, f"合同编号 {contract.contract_no} 已被使用，无法恢复")
            _ensure_restorable_rooms(db, contract)

        for contract in candidates:
            contract.is_deleted = False
            contract.updated_at = now_in_timezone()
            log_action(
                db,
                contract.id,
                "restore",
                f"批量恢复合同 {contract.contract_no}",
                operator,
                commit=False,
                object_scope=_contract_object_scope(contract),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    restored = len(candidates)
    return {"message": f"已恢复 {restored} 条合同", "restored": restored}


def _contract_purge_attachments(db: Session, contract: Contract) -> tuple[list[object], list[Path]]:
    """只读预检合同及其押金附件，并返回待删记录和安全磁盘路径。"""
    from app.models import Attachment
    from app.services.attachments import _attachment_path

    deposit_ids = [row[0] for row in db.query(ContractDeposit.id).filter(ContractDeposit.contract_id == contract.id).all()]
    filters = [and_(Attachment.entity_type == "contract", Attachment.entity_id == contract.id)]
    if deposit_ids:
        filters.append(and_(Attachment.entity_type == "deposit", Attachment.entity_id.in_(deposit_ids)))
    rows = db.query(Attachment).filter(or_(*filters)).all()
    file_paths: list[Path] = []
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    building = db.get(Building, int(contract.building_id))
    if building is None:
        raise HTTPException(409, "合同楼宇不存在，当前清理已停止")
    for row in rows:
        if str(building.code).casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold():
            legacy_attachment_scope(
                db,
                row.entity_type,
                row.entity_id,
                existing=row,
            )
        elif (
            row.scope_type != "building"
            or row.portfolio_id is not None
            or row.building_id != contract.building_id
        ):
            raise HTTPException(409, "合同附件与当前楼宇范围不一致，当前清理已停止")
        file_paths.append(_attachment_path(row.storage_path))
    return rows, file_paths


def _ensure_contracts_can_be_purged(db: Session, contracts: list[Contract]) -> None:
    active = [contract.contract_no for contract in contracts if not contract.is_deleted]
    if active:
        raise HTTPException(409, f"合同 {active[0]} 尚未进入回收站，不能永久删除")
    contract_ids = [contract.id for contract in contracts if contract.id is not None]
    if not contract_ids:
        return
    statement = (
        db.query(ReceivableStatement)
        .filter(ReceivableStatement.contract_id.in_(contract_ids))
        .order_by(ReceivableStatement.id.asc())
        .first()
    )
    if statement is not None:
        blocked = next((item for item in contracts if item.id == statement.contract_id), None)
        label = blocked.contract_no if blocked is not None else str(statement.contract_id)
        raise HTTPException(409, f"合同 {label} 已有固化出账记录，不能永久删除")


def _unlink_committed_contract_files(file_paths: list[Path]) -> int:
    """数据库已经提交后再清理文件；失败只留下孤立文件，不反向破坏已提交数据。"""
    failed = 0
    seen: set[Path] = set()
    for file_path in file_paths:
        if file_path in seen:
            continue
        seen.add(file_path)
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            failed += 1
    return failed


def purge_contract(
    db: Session,
    contract: Contract,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    assert_contract_in_scope(db, contract, scope, for_write=True)
    file_paths: list[Path] = []
    try:
        _ensure_contracts_can_be_purged(db, [contract])
        if contract.pdf_path:
            file_paths.append(_safe_upload_path(contract.pdf_path))
        attachments, attachment_paths = _contract_purge_attachments(db, contract)
        file_paths.extend(attachment_paths)
        for attachment in attachments:
            db.delete(attachment)
        db.delete(contract)
        db.commit()
    except Exception:
        db.rollback()
        raise
    failed = _unlink_committed_contract_files(file_paths)
    result = {"message": f"已永久删除合同 {contract.contract_no}"}
    if failed:
        result["cleanup_warning"] = f"有 {failed} 个磁盘文件未能清理，请检查存储目录"
    return result


def clear_recycle_bin(db: Session, *, scope: SelectedPropertyScope) -> dict:
    building_id = _require_contract_detail_write_scope(db, scope)
    deleted = db.query(Contract).filter(
        Contract.building_id == building_id,
        Contract.is_deleted.is_(True),
    ).all()
    _assert_contracts_in_scope(db, deleted, scope, for_write=True)
    count = len(deleted)
    file_paths: list[Path] = []
    attachments: list[object] = []
    try:
        # 整批预检完成前不删除任何数据库记录，也不触碰磁盘文件。
        _ensure_contracts_can_be_purged(db, deleted)
        for contract in deleted:
            if contract.pdf_path:
                file_paths.append(_safe_upload_path(contract.pdf_path))
            contract_attachments, attachment_paths = _contract_purge_attachments(db, contract)
            attachments.extend(contract_attachments)
            file_paths.extend(attachment_paths)
        for attachment in attachments:
            db.delete(attachment)
        for contract in deleted:
            db.delete(contract)
        db.commit()
    except Exception:
        db.rollback()
        raise
    failed = _unlink_committed_contract_files(file_paths)
    result = {"message": f"已清空回收站（{count} 条）"}
    if failed:
        result["cleanup_warning"] = f"有 {failed} 个磁盘文件未能清理，请检查存储目录"
    return result














def save_contract_pdf(
    db: Session,
    contract: Contract,
    upload: UploadFile,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    return _save_contract_pdf_impl(
        db,
        contract,
        upload,
        operator,
        scope=scope,
        max_file_size=MAX_PDF_FILE_SIZE,
    )


def batch_upload_pdfs(
    db: Session,
    uploads: list[UploadFile],
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    return _batch_upload_pdfs_impl(
        db,
        uploads,
        operator,
        scope=scope,
        max_file_size=MAX_PDF_FILE_SIZE,
        max_batch_count=MAX_BATCH_PDF_COUNT,
        max_total_size=MAX_BATCH_PDF_TOTAL_SIZE,
    )


def manual_match_pdf(
    db: Session,
    filename: str,
    contract: Contract,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    return _manual_match_pdf_impl(
        db,
        filename,
        contract,
        operator,
        scope=scope,
        max_file_size=MAX_PDF_FILE_SIZE,
    )
