from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.access import require_admin
from app.database import get_db
from app.services.audit import export_audit_logs, get_audit_logs


router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_admin)])


@router.get("/logs")
def logs(page: int = 1, page_size: int = 50, operator: str | None = None, action: str | None = None, db: Session = Depends(get_db)):
    return get_audit_logs(db, page, page_size, operator, action)


@router.get("/export")
def export_logs(db: Session = Depends(get_db)):
    return export_audit_logs(db)
