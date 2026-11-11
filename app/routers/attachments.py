from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_admin, require_editor
from app.database import get_db
from app.models import User
from app.schemas import AttachmentCompletenessTasksResponse, AttachmentOut, AttachmentUpdate
from app.services.attachments import (
    create_attachment,
    delete_attachment,
    download_attachment,
    get_attachment,
    list_completeness_tasks,
    list_attachments,
    record_attachment_download,
    update_attachment,
)
from app.services.common import get_operator
from app.services.detail_operations_access import detail_scope_dependency
from app.services.property_scope import (
    SelectedPropertyScope,
)

get_selected_view_scope = detail_scope_dependency("contracts", for_write=False)
get_selected_edit_scope = detail_scope_dependency("contracts", for_write=True)


router = APIRouter(prefix="/api/attachments", tags=["attachments"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[AttachmentOut])
def attachment_list(
    entity_type: str,
    entity_id: int,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_attachments(db, entity_type, entity_id, scope=scope)


@router.get("/completeness-tasks", response_model=AttachmentCompletenessTasksResponse)
def attachment_completeness_tasks(
    target_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 100,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_completeness_tasks(
        db,
        scope=scope,
        target_type=target_type,
        contract_id=contract_id,
        customer_id=customer_id,
        limit=max(1, min(limit, 500)),
    )


@router.post("", response_model=AttachmentOut)
def upload_attachment(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    category: str = Form("其他"),
    notes: str = Form(""),
    file: UploadFile = File(...),
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    return create_attachment(
        db,
        scope=scope,
        entity_type=entity_type,
        entity_id=entity_id,
        upload=file,
        category=category,
        notes=notes,
        operator=get_operator(request),
    )


@router.patch("/{attachment_id}", response_model=AttachmentOut)
def edit_attachment(
    attachment_id: int,
    payload: AttachmentUpdate,
    request: Request,
    _editor: User = Depends(require_editor),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    attachment = get_attachment(db, attachment_id, scope=scope)
    return update_attachment(db, attachment, payload, get_operator(request), scope=scope)


@router.delete("/{attachment_id}")
def remove_attachment(
    attachment_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
):
    attachment = get_attachment(db, attachment_id, scope=scope)
    return delete_attachment(db, attachment, get_operator(request), scope=scope)


@router.get("/{attachment_id}/download")
def download(
    attachment_id: int,
    user: User = Depends(get_current_user),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    attachment = get_attachment(db, attachment_id, scope=scope)
    response = download_attachment(attachment, scope=scope)
    record_attachment_download(db, attachment, user.username, scope=scope)
    return response
