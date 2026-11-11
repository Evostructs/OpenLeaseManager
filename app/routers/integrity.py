from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.property_scope import require_platform_admin
from app.core.config import get_settings
from app.database import get_db
from app.services.integrity import export_integrity_report, run_integrity_check


router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_platform_admin)])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "db": "unavailable"})


@router.get("/integrity-check")
def integrity_check(db: Session = Depends(get_db)):
    return run_integrity_check(db)


@router.get("/integrity-check/export")
def integrity_export(db: Session = Depends(get_db)):
    return export_integrity_report(db)


@router.get("/version")
def version():
    settings = get_settings()
    return {"version": settings.app_version}
