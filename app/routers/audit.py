from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.access import require_admin
from app.database import get_db
from app.models import User
from app.services.common import get_operator, log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.audit import export_audit_logs, get_audit_logs
from app.services.property_scope import (
    SelectedPropertyScope,
    get_selected_view_scope,
    require_platform_admin,
)


router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs")
def logs(
    page: int = 1,
    page_size: int = 50,
    operator: str | None = None,
    user: str | None = None,
    action: str | None = None,
    target: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_audit_logs(
        db,
        page,
        page_size,
        operator or user,
        action,
        target_type or target,
        target_id,
        start_date,
        end_date,
        building_ids=scope.building_ids,
        portfolio_id=scope.portfolio_id,
    )


@router.get("/export")
def export_logs(
    request: Request,
    _admin: User = Depends(require_admin),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    response = export_audit_logs(
        db,
        building_ids=scope.building_ids,
        portfolio_id=scope.portfolio_id,
    )
    operator = get_operator(request)
    for building_id in scope.building_ids:
        log_action(
            db,
            None,
            "export",
            "导出操作日志",
            operator,
            commit=False,
            target_type="audit",
            object_scope=ObjectScope("building", building_id=int(building_id)),
        )
    db.commit()
    return response


@router.get("/platform/logs")
def platform_logs(
    page: int = 1,
    page_size: int = 50,
    operator: str | None = None,
    user: str | None = None,
    action: str | None = None,
    target: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    _platform_admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    return get_audit_logs(
        db,
        page,
        page_size,
        operator or user,
        action,
        target_type or target,
        target_id,
        start_date,
        end_date,
        scope_type="global",
    )


@router.get("/platform/export")
def export_platform_logs(
    request: Request,
    _platform_admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    log_action(
        db,
        None,
        "export",
        "导出平台操作日志",
        get_operator(request),
        target_type="audit",
        object_scope=ObjectScope("global"),
    )
    return export_audit_logs(db, scope_type="global")
