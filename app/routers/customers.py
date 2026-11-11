from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_admin, require_editor
from app.core.uploads import read_import_upload
from app.database import get_db
from app.models import User
from app.schemas import (
    CustomerCreate,
    CustomerDetailOut,
    CustomerFollowUpCreate,
    CustomerFollowUpOut,
    CustomerFollowUpUpdate,
    CustomerOut,
    CustomerUpdate,
)
from app.services.common import get_operator
from app.services.customers import (
    complete_customer_follow_up,
    create_customer,
    create_customer_follow_up,
    delete_customer_follow_up,
    get_customer,
    get_customer_follow_up,
    list_customers,
    list_due_follow_up_customers,
    serialize_customer,
    update_customer,
    update_customer_follow_up,
)
from app.services.detail_operations_access import detail_scope_dependency
from app.services.import_export import (
    customer_import_template,
    export_customers_excel,
    export_follow_ups_excel,
    follow_up_import_template,
    import_customers_excel,
    import_follow_ups_excel,
    preview_customers_excel,
    preview_follow_ups_excel,
)
from app.services.property_scope import (
    SelectedPropertyScope,
    require_customer_master_manager,
    resolve_user_scope,
)

get_selected_view_scope = detail_scope_dependency("customers", for_write=False)
get_selected_edit_scope = detail_scope_dependency("customers", for_write=True)


router = APIRouter(prefix="/api/customers", tags=["customers"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=dict)
def customer_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=0, le=100),
    keyword: str | None = None,
    status: str | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_customers(db, page, page_size, keyword, status, scope=scope)


@router.get("/export")
def export_customers(
    keyword: str | None = None,
    status: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return export_customers_excel(db, keyword, status, scope=scope)


@router.get("/import/template")
def download_customer_import_template(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return customer_import_template(scope=scope, db=db)


@router.post("/import")
async def import_customers(
    file: UploadFile = File(...),
    request: Request = None,
    _admin: User = Depends(require_admin),
    _master_manager: User = Depends(require_customer_master_manager),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return import_customers_excel(
        await read_import_upload(file),
        db,
        get_operator(request),
        scope=scope,
    )


@router.post("/import/preview")
async def preview_customers_import(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
    _master_manager: User = Depends(require_customer_master_manager),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return preview_customers_excel(await read_import_upload(file), db, scope=scope)


@router.get("/follow-ups/due", response_model=dict)
def due_follow_ups(
    limit: int = Query(20, ge=1, le=100),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_due_follow_up_customers(db, limit, scope=scope)


@router.get("/follow-ups/export")
def export_follow_ups(
    customer_id: int | None = None,
    keyword: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return export_follow_ups_excel(
        db,
        customer_id=customer_id,
        keyword=keyword,
        status=status,
        owner=owner,
        scope=scope,
    )


@router.get("/follow-ups/import/template")
def download_follow_up_import_template(
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return follow_up_import_template(scope=scope, db=db)


@router.post("/follow-ups/import")
async def import_follow_ups(
    file: UploadFile = File(...),
    request: Request = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return import_follow_ups_excel(
        await read_import_upload(file),
        db,
        get_operator(request),
        scope=scope,
    )


@router.post("/follow-ups/import/preview")
async def preview_follow_ups_import(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    return preview_follow_ups_excel(await read_import_upload(file), db, scope=scope)


@router.get("/{customer_id}", response_model=CustomerDetailOut)
def customer_detail(
    customer_id: int,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return serialize_customer(
        get_customer(db, customer_id, scope=scope),
        include_contracts=True,
        db=db,
        scope=scope,
    )


@router.post("", response_model=CustomerOut)
def create(
    payload: CustomerCreate,
    request: Request,
    _editor: User = Depends(require_editor),
    _master_manager: User = Depends(require_customer_master_manager),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return create_customer(db, payload, get_operator(request), scope=scope)


@router.put("/{customer_id}", response_model=CustomerOut)
def update(
    customer_id: int,
    payload: CustomerUpdate,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return update_customer(
        db,
        get_customer(db, customer_id, scope=scope),
        payload,
        get_operator(request),
        scope=scope,
        can_manage_master=resolve_user_scope(db, _editor).has_capability(
            "customer_master_manage"
        ),
    )


@router.post("/{customer_id}/follow-ups", response_model=CustomerFollowUpOut)
def create_follow_up(
    customer_id: int,
    payload: CustomerFollowUpCreate,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return create_customer_follow_up(
        db,
        get_customer(db, customer_id, scope=scope),
        payload,
        get_operator(request),
        scope=scope,
    )


@router.put("/{customer_id}/follow-ups/{follow_up_id}", response_model=CustomerFollowUpOut)
def update_follow_up(
    customer_id: int,
    follow_up_id: int,
    payload: CustomerFollowUpUpdate,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    customer = get_customer(db, customer_id, scope=scope)
    follow_up = get_customer_follow_up(db, customer, follow_up_id, scope=scope)
    return update_customer_follow_up(
        db,
        customer,
        follow_up,
        payload,
        get_operator(request),
        scope=scope,
    )


@router.post("/{customer_id}/follow-ups/{follow_up_id}/complete", response_model=CustomerFollowUpOut)
def complete_follow_up(
    customer_id: int,
    follow_up_id: int,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    customer = get_customer(db, customer_id, scope=scope)
    follow_up = get_customer_follow_up(db, customer, follow_up_id, scope=scope)
    return complete_customer_follow_up(db, customer, follow_up, get_operator(request), scope=scope)


@router.delete("/{customer_id}/follow-ups/{follow_up_id}")
def delete_follow_up(
    customer_id: int,
    follow_up_id: int,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    customer = get_customer(db, customer_id, scope=scope)
    follow_up = get_customer_follow_up(db, customer, follow_up_id, scope=scope)
    return delete_customer_follow_up(db, customer, follow_up, get_operator(request), scope=scope)
