from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.database import get_db
from app.services.common import get_operator
from app.services.detail_operations_access import detail_scope_dependency
from app.services.reports import build_operations_daily_report_html
from app.services.property_scope import SelectedPropertyScope

get_selected_view_scope = detail_scope_dependency("contracts", for_write=False)


router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(get_current_user)])


@router.get("/operations-daily", response_class=HTMLResponse)
def operations_daily_report(
    request: Request,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    html = build_operations_daily_report_html(
        db,
        get_operator(request),
        building_ids=scope.building_ids,
    )
    filename = quote("operations-daily-report.html")
    # 日报是全程 escape 的独立 HTML 页，含打印按钮的内联 onclick，
    # 单独放宽 script-src；全局 CSP 仍保持 script-src 'self'。
    report_csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    return HTMLResponse(
        html,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "Content-Security-Policy": report_csp,
        },
    )
