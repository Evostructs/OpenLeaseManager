from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.database import get_db
from app.models import Contract, User
from app.schemas import ContractCreate, ContractOut, ContractUpdate, LogOut
from app.services.common import get_operator
from app.services.contracts import (
    batch_delete_contracts,
    batch_restore_contracts,
    batch_upload_pdfs,
    build_rent_plan,
    clear_recycle_bin,
    create_contract,
    download_contract_pdf,
    export_contract_template,
    export_contracts_excel,
    get_contract,
    get_contract_stats,
    get_monthly_receivables,
    import_contracts_excel,
    list_contracts,
    list_missing_contract_pdfs,
    list_unmatched_pdfs,
    manual_match_pdf,
    next_contract_no,
    purge_contract,
    restore_contract,
    save_contract_pdf,
    soft_delete_contract,
    update_contract,
)
from app.services.common import serialize_contract


router = APIRouter(prefix="/api/contracts", tags=["contracts"], dependencies=[Depends(get_current_user)])


@router.get("/next-no")
def get_next_contract_no(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return next_contract_no(db)


@router.get("", response_model=dict)
def get_contract_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    tower: str | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = Query(None, pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    return list_contracts(db, page, page_size, keyword, tower, status, sort_by, sort_dir)


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return get_contract_stats(db)


@router.get("/receivables/monthly")
def receivables(month: str | None = None, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return get_monthly_receivables(db, month)


@router.get("/recycle/list")
def recycle_list(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    items = db.query(Contract).filter(Contract.is_deleted.is_(True)).order_by(Contract.updated_at.desc()).all()
    return [serialize_contract(item) for item in items]


@router.post("/recycle/batch-restore")
def recycle_batch_restore(ids: list[int], request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return batch_restore_contracts(db, ids, get_operator(request))


@router.post("/recycle/restore/{contract_id}")
def recycle_restore(contract_id: int, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id, include_deleted=True)
    return restore_contract(db, contract, get_operator(request))


@router.delete("/recycle/purge/{contract_id}")
def recycle_purge(contract_id: int, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id, include_deleted=True)
    return purge_contract(db, contract)


@router.post("/recycle/clear")
def recycle_clear(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return clear_recycle_bin(db)


@router.get("/export/excel")
def export_excel(
    keyword: str | None = None,
    tower: str | None = None,
    status: str | None = None,
    _editor: User = Depends(require_editor),
    db: Session = Depends(get_db),
):
    query_result = list_contracts(db, 1, 100000, keyword, tower, status)["items"]
    contracts = [get_contract(db, item["id"]) for item in query_result]
    return export_contracts_excel(contracts, keyword, tower, status)


@router.get("/export/template")
def download_template(_editor: User = Depends(require_editor)):
    return export_contract_template()


@router.post("/import")
async def import_excel(file: UploadFile = File(...), request: Request = None, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 Excel 文件")
    content = await file.read()
    return import_contracts_excel(content, db, get_operator(request))


@router.post("/batch-upload-pdf")
async def upload_many_pdfs(files: list[UploadFile] = File(...), request: Request = None, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return batch_upload_pdfs(db, files, get_operator(request))


@router.post("/match-pdf")
def match_pdf(filename: str, contract_id: int, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return manual_match_pdf(db, filename, contract, get_operator(request))


@router.get("/unmatched-pdfs")
def unmatched_pdfs(_editor: User = Depends(require_editor)):
    return list_unmatched_pdfs()


@router.get("/missing-pdfs")
def missing_pdfs(_editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return list_missing_contract_pdfs(db)


@router.post("/batch-delete")
def batch_delete(ids: list[int], request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return batch_delete_contracts(db, ids, get_operator(request))


@router.get("/{contract_id}/rent-plan")
def rent_plan(contract_id: int, db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return build_rent_plan(contract)


@router.get("/{contract_id}/logs", response_model=list[LogOut])
def contract_logs(contract_id: int, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return contract.logs


@router.post("/{contract_id}/upload-pdf")
def upload_pdf(contract_id: int, request: Request, file: UploadFile = File(...), _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return save_contract_pdf(db, contract, file, get_operator(request))


@router.get("/{contract_id}/pdf")
def view_pdf(contract_id: int, db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return download_contract_pdf(contract)


@router.get("/{contract_id}", response_model=ContractOut)
def detail(contract_id: int, db: Session = Depends(get_db)):
    return serialize_contract(get_contract(db, contract_id))


@router.post("", response_model=ContractOut)
def create(payload: ContractCreate, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    return create_contract(db, payload, get_operator(request))


@router.put("/{contract_id}", response_model=ContractOut)
def update(contract_id: int, payload: ContractUpdate, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return update_contract(db, contract, payload, get_operator(request))


@router.delete("/{contract_id}")
def delete(contract_id: int, request: Request, _editor: User = Depends(require_editor), db: Session = Depends(get_db)):
    contract = get_contract(db, contract_id)
    return soft_delete_contract(db, contract, get_operator(request))
