from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session, selectinload

from app.core.access import get_current_user, require_admin, require_editor
from app.core.uploads import read_import_upload
from app.database import get_db
from app.models import Building, Contract, User
from app.schemas import (
    ContractChangeConfirmPayload,
    ContractCreate,
    ContractOut,
    ContractRoomPayload,
    ContractRoomReducePayload,
    ContractRoomSwapPayload,
    ContractTerminatePayload,
    ContractUpdate,
    DepositActionPayload,
    DepositCreate,
    DepositOut,
    DepositUpdate,
    LeaseDocumentPayload,
    LogOut,
    ReceivableMetadataPayload,
    ReceivablePaymentPayload,
    ReceivablePaymentReceipt,
    ReceivableRecordPayload,
    RentTermOut,
    RentTermPayload,
)
from app.services.common import get_operator, log_action, log_action_for_buildings
from app.services.contracts import (
    batch_delete_contracts,
    batch_restore_contracts,
    batch_upload_pdfs,
    build_rent_plan,
    build_successor_contract_draft,
    build_scoped_contract_decision_card,
    build_contract_change_preview,
    clear_recycle_bin,
    confirm_contract_change,
    create_contract,
    create_rent_term,
    delete_rent_term,
    expand_contract_room,
    add_receivable_payment,
    delete_receivable_payment,
    download_contract_pdf,
    export_contract_template,
    export_contracts_excel,
    export_dunning_ledger_excel,
    export_invoice_ledger_excel,
    get_dunning_ledger,
    get_invoice_ledger,
    get_contract,
    get_contract_stats,
    generate_receivable_statements,
    get_monthly_receivables,
    list_rent_terms,
    import_contracts_excel,
    list_contracts,
    list_recycled_contracts,
    list_missing_contract_pdfs,
    list_unmatched_pdfs,
    manual_match_pdf,
    next_contract_no,
    purge_contract,
    restore_contract,
    save_contract_pdf,
    serialize_contract_with_scope,
    soft_delete_contract,
    reduce_contract_room,
    require_contract_detail_write_scope,
    terminate_contract,
    swap_contract_room,
    update_rent_term,
    update_receivable_metadata,
    upsert_receivable_record,
    update_contract,
)
from app.services.contract_receivables import get_receivable_payment_receipt
from app.services.deposits import (
    create_deposit,
    get_deposit,
    offset_deposit,
    receive_deposit,
    refund_deposit,
    update_deposit,
)
from app.services.detail_operations_access import detail_scope_dependency
from app.services.documents import generate_lease_document, resolve_lease_document_contract
from app.services.import_export import deposit_import_template, export_deposits_excel, import_deposits_excel
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import (
    SelectedPropertyScope,
)

get_selected_view_scope = detail_scope_dependency("contracts", for_write=False)
get_selected_edit_scope = detail_scope_dependency("contracts", for_write=True)

router = APIRouter(prefix="/api/contracts", tags=["contracts"], dependencies=[Depends(get_current_user)])


@router.get("/next-no")
def get_next_contract_no(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return next_contract_no(db, scope=scope)


@router.get("", response_model=dict)
def get_contract_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=0, le=100),
    keyword: str | None = None,
    tower: str | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = Query(None, pattern="^(asc|desc)$"),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_contracts(db, page, page_size, keyword, tower, status, sort_by, sort_dir, scope=scope)


@router.get("/stats")
def stats(
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_contract_stats(db, building_ids=scope.building_ids)


@router.get("/receivables/monthly")
def receivables(
    month: str | None = None,
    invoice_status: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_monthly_receivables(db, month, invoice_status, building_ids=scope.building_ids)


@router.get("/receivables/invoices")
def invoice_ledger(
    month: str | None = None,
    invoice_status: str | None = None,
    keyword: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_invoice_ledger(
        db,
        month,
        invoice_status,
        keyword,
        building_ids=scope.building_ids,
    )


@router.get("/receivables/invoices/export")
def export_invoice_ledger(
    month: str | None = None,
    invoice_status: str | None = None,
    keyword: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    response = export_invoice_ledger_excel(
        db,
        month,
        invoice_status,
        keyword,
        building_ids=scope.building_ids,
    )
    log_action_for_buildings(
        db,
        scope.building_ids,
        "export",
        f"导出发票台账（{month or '当月'}）",
        _editor.username,
        target_type="receivable",
    )
    return response


@router.get("/receivables/dunning")
def dunning_ledger(
    month: str | None = None,
    dunning_status: str | None = None,
    dunning_owner: str | None = None,
    overdue_only: bool | None = None,
    focus: bool = True,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_dunning_ledger(
        db,
        month,
        dunning_status,
        dunning_owner,
        overdue_only,
        focus,
        building_ids=scope.building_ids,
    )


@router.get("/receivables/dunning/export")
def export_dunning_ledger(
    month: str | None = None,
    dunning_status: str | None = None,
    dunning_owner: str | None = None,
    overdue_only: bool | None = None,
    focus: bool = True,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    response = export_dunning_ledger_excel(
        db,
        month,
        dunning_status,
        dunning_owner,
        overdue_only,
        focus,
        building_ids=scope.building_ids,
    )
    log_action_for_buildings(
        db,
        scope.building_ids,
        "export",
        f"导出催收台账（{month or '当月'}）",
        _editor.username,
        target_type="receivable",
    )
    return response


@router.post("/receivables/statements/{month}/generate")
def generate_statements(
    month: str,
    regenerate: bool = False,
    request: Request = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_contract_detail_write_scope(scope, db=db)
    return generate_receivable_statements(
        db,
        month,
        get_operator(request),
        regenerate=regenerate,
        building_ids=(building_id,),
        object_scope=ObjectScope(scope_type="building", building_id=building_id),
    )


@router.post("/receivables/monthly/{contract_id}")
def update_receivable(
    contract_id: int,
    payload: ReceivableRecordPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return upsert_receivable_record(db, contract, payload, get_operator(request))


@router.patch("/receivables/monthly/{contract_id}")
def patch_receivable_metadata(
    contract_id: int,
    payload: ReceivableMetadataPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return update_receivable_metadata(db, contract, payload, get_operator(request))


@router.post("/receivables/monthly/{contract_id}/payments")
def create_receivable_payment(
    contract_id: int,
    payload: ReceivablePaymentPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return add_receivable_payment(db, contract, payload, get_operator(request))


@router.post("/receivables/monthly/{contract_id}/payment-operations", response_model=ReceivablePaymentReceipt)
def record_receivable_payment_operation(
    contract_id: int,
    payload: ReceivablePaymentPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not payload.operation_id:
        raise HTTPException(422, "本次收款必须提供操作标识")
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return add_receivable_payment(db, contract, payload, get_operator(request))


@router.get("/receivables/monthly/{contract_id}/payment-operations/{operation_id}", response_model=ReceivablePaymentReceipt)
def read_receivable_payment_receipt(
    contract_id: int,
    operation_id: str,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return get_receivable_payment_receipt(db, contract, operation_id)


@router.delete("/receivables/payments/{payment_id}")
def remove_receivable_payment(
    payment_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    building_id = require_contract_detail_write_scope(scope, db=db)
    return delete_receivable_payment(
        db,
        payment_id,
        get_operator(request),
        building_ids=(building_id,),
    )


@router.post("/documents/generate")
def generate_document(
    payload: LeaseDocumentPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = resolve_lease_document_contract(db, payload, scope=scope)
    return generate_lease_document(
        db,
        payload,
        get_operator(request),
        scope=scope,
        parent_contract=contract,
    )


@router.get("/recycle/list")
def recycle_list(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return list_recycled_contracts(db, scope=scope)


@router.post("/recycle/batch-restore")
def recycle_batch_restore(
    ids: list[int],
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return batch_restore_contracts(db, ids, get_operator(request), scope=scope)


@router.post("/recycle/restore/{contract_id}")
def recycle_restore(
    contract_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, include_deleted=True, scope=scope, for_write=True)
    return restore_contract(db, contract, get_operator(request), scope=scope)


@router.delete("/recycle/purge/{contract_id}")
def recycle_purge(
    contract_id: int,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, include_deleted=True, scope=scope, for_write=True)
    return purge_contract(db, contract, scope=scope)


@router.post("/recycle/clear")
def recycle_clear(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return clear_recycle_bin(db, scope=scope)


@router.get("/export/excel")
def export_excel(
    keyword: str | None = None,
    tower: str | None = None,
    status: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    query_result = list_contracts(db, 1, 100000, keyword, tower, status, scope=scope)["items"]
    ids = [item["id"] for item in query_result]
    # 一次性预加载导出所需关联，避免逐合同重查的 N+1
    loaded = (
        db.query(Contract)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.room_items),
            selectinload(Contract.rent_terms),
            selectinload(Contract.deposits),
        )
        .filter(Contract.id.in_(ids), Contract.building_id.in_(scope.building_ids))
        .all()
        if ids
        else []
    )
    by_id = {contract.id: contract for contract in loaded}
    contracts = [by_id[item_id] for item_id in ids if item_id in by_id]
    selected_buildings = (
        db.query(Building)
        .filter(Building.id.in_(scope.building_ids))
        .order_by(Building.id)
        .all()
    )
    if len(selected_buildings) == 1:
        selected_building = selected_buildings[0]
        property_label = (
            "示例双塔中心"
            if str(selected_building.code).casefold() == "demo_a"
            else (str(selected_building.name or "").strip() or str(selected_building.code))
        )
    else:
        property_label = "所选楼宇"
    response = export_contracts_excel(
        contracts,
        keyword,
        tower,
        status,
        property_label=property_label,
    )
    contract_counts = {int(building_id): 0 for building_id in scope.building_ids}
    for contract in contracts:
        contract_counts[int(contract.building_id)] += 1
    log_action_for_buildings(
        db,
        scope.building_ids,
        "export",
        f"导出合同 Excel（{len(contracts)} 条）",
        _editor.username,
        target_type="contract",
        changes_by_building={
            building_id: f"导出合同 Excel（{count} 条）"
            for building_id, count in contract_counts.items()
        },
    )
    return response


@router.get("/export/template")
def download_template(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return export_contract_template(scope=scope, db=db)


@router.get("/deposits/export")
def export_deposits(
    keyword: str | None = None,
    status: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    response = export_deposits_excel(
        db,
        keyword=keyword,
        status=status,
        contract_id=contract_id,
        customer_id=customer_id,
        building_ids=scope.building_ids,
    )
    log_action_for_buildings(
        db,
        scope.building_ids,
        "export",
        "导出押金台账",
        _editor.username,
        target_type="deposit",
    )
    return response


@router.get("/deposits/import/template")
def download_deposit_import_template(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return deposit_import_template(scope=scope, db=db)


@router.post("/deposits/import")
async def import_deposits(
    file: UploadFile = File(...),
    request: Request = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    content = await read_import_upload(file)
    return import_deposits_excel(
        content,
        db,
        get_operator(request),
        scope=scope,
    )


@router.post("/import")
async def import_excel(
    file: UploadFile = File(...),
    request: Request = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    content = await read_import_upload(file)
    return import_contracts_excel(content, db, get_operator(request), scope=scope)


@router.post("/batch-upload-pdf")
async def upload_many_pdfs(
    files: list[UploadFile] = File(...),
    request: Request = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return batch_upload_pdfs(db, files, get_operator(request), scope=scope)


@router.post("/match-pdf")
def match_pdf(
    filename: str,
    contract_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return manual_match_pdf(db, filename, contract, get_operator(request), scope=scope)


@router.get("/unmatched-pdfs")
def unmatched_pdfs(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
):
    return list_unmatched_pdfs(scope=scope)


@router.get("/missing-pdfs")
def missing_pdfs(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_missing_contract_pdfs(db, scope=scope)


@router.post("/batch-delete")
def batch_delete(
    ids: list[int],
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return batch_delete_contracts(db, ids, get_operator(request), scope=scope)


@router.post("/change-preview")
def change_preview(
    payload: ContractCreate,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return build_contract_change_preview(db, payload, scope=scope)


@router.post("/change-confirm", response_model=ContractOut)
def change_confirm(
    payload: ContractChangeConfirmPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return confirm_contract_change(db, payload.contract, get_operator(request), scope=scope)


@router.get("/{contract_id}/rent-plan")
def rent_plan(
    contract_id: int,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope)
    return build_rent_plan(contract)


@router.get("/{contract_id}/rent-terms", response_model=list[RentTermOut])
def rent_terms(
    contract_id: int,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope)
    return list_rent_terms(contract)


@router.post("/{contract_id}/rent-terms", response_model=RentTermOut)
def add_rent_term(
    contract_id: int,
    payload: RentTermPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return create_rent_term(db, contract, payload, get_operator(request))


@router.put("/{contract_id}/rent-terms/{term_id}", response_model=RentTermOut)
def edit_rent_term(
    contract_id: int,
    term_id: int,
    payload: RentTermPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return update_rent_term(db, contract, term_id, payload, get_operator(request))


@router.delete("/{contract_id}/rent-terms/{term_id}")
def remove_rent_term(
    contract_id: int,
    term_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return delete_rent_term(db, contract, term_id, get_operator(request))


@router.post("/{contract_id}/deposits", response_model=DepositOut)
def add_deposit(
    contract_id: int,
    payload: DepositCreate,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return create_deposit(db, contract, payload, get_operator(request))


@router.put("/{contract_id}/deposits/{deposit_id}", response_model=DepositOut)
def edit_deposit(
    contract_id: int,
    deposit_id: int,
    payload: DepositUpdate,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    deposit = get_deposit(contract, deposit_id)
    return update_deposit(db, contract, deposit, payload, get_operator(request))


@router.post("/{contract_id}/deposits/{deposit_id}/receive", response_model=DepositOut)
def mark_deposit_received(
    contract_id: int,
    deposit_id: int,
    payload: DepositActionPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    deposit = get_deposit(contract, deposit_id)
    return receive_deposit(db, contract, deposit, payload, get_operator(request))


@router.post("/{contract_id}/deposits/{deposit_id}/refund", response_model=DepositOut)
def mark_deposit_refunded(
    contract_id: int,
    deposit_id: int,
    payload: DepositActionPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    deposit = get_deposit(contract, deposit_id)
    return refund_deposit(db, contract, deposit, payload, get_operator(request))


@router.post("/{contract_id}/deposits/{deposit_id}/offset", response_model=DepositOut)
def mark_deposit_offset(
    contract_id: int,
    deposit_id: int,
    payload: DepositActionPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    deposit = get_deposit(contract, deposit_id)
    return offset_deposit(db, contract, deposit, payload, get_operator(request))


@router.get("/{contract_id}/logs", response_model=list[LogOut])
def contract_logs(
    contract_id: int,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope)
    return contract.logs


@router.post("/{contract_id}/upload-pdf")
def upload_pdf(
    contract_id: int,
    request: Request,
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return save_contract_pdf(db, contract, file, get_operator(request), scope=scope)


@router.get("/{contract_id}/pdf")
def view_pdf(
    contract_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope)
    log_action(
        db,
        contract.id,
        "download",
        f"下载合同 PDF {contract.contract_no}",
        user.username,
        target_type="contract",
        target_id=contract.id,
        object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
    )
    return download_contract_pdf(contract, scope=scope)


@router.get("/{contract_id}/successor-draft")
def successor_draft(
    contract_id: int,
    lease_type: str = Query("续签"),
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope)
    return build_successor_contract_draft(db, contract, lease_type, scope=scope)


@router.get("/{contract_id}/decision-card")
def decision_card(
    contract_id: int,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return build_scoped_contract_decision_card(db, contract_id, scope)


@router.post("/{contract_id}/terminate", response_model=ContractOut)
def terminate(
    contract_id: int,
    payload: ContractTerminatePayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return terminate_contract(db, contract, payload, get_operator(request), scope=scope)


@router.post("/{contract_id}/rooms/expand", response_model=ContractOut)
def expand_room(
    contract_id: int,
    payload: ContractRoomPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return expand_contract_room(db, contract, payload, get_operator(request), scope=scope)


@router.post("/{contract_id}/rooms/{room_item_id}/reduce", response_model=ContractOut)
def reduce_room(
    contract_id: int,
    room_item_id: int,
    payload: ContractRoomReducePayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return reduce_contract_room(db, contract, room_item_id, payload, get_operator(request), scope=scope)


@router.post("/{contract_id}/rooms/{room_item_id}/swap", response_model=ContractOut)
def swap_room(
    contract_id: int,
    room_item_id: int,
    payload: ContractRoomSwapPayload,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return swap_contract_room(db, contract, room_item_id, payload, get_operator(request), scope=scope)


@router.get("/{contract_id}", response_model=ContractOut)
def detail(
    contract_id: int,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return serialize_contract_with_scope(db, get_contract(db, contract_id, scope=scope), scope)


@router.post("", response_model=ContractOut)
def create(
    payload: ContractCreate,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return create_contract(db, payload, get_operator(request), scope=scope)


@router.put("/{contract_id}", response_model=ContractOut)
def update(
    contract_id: int,
    payload: ContractUpdate,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return update_contract(db, contract, payload, get_operator(request), scope=scope)


@router.delete("/{contract_id}")
def delete(
    contract_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    contract = get_contract(db, contract_id, scope=scope, for_write=True)
    return soft_delete_contract(db, contract, get_operator(request), scope=scope)
