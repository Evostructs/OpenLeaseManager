from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.database import get_db
from app.models import User
from app.services.common import log_action_for_buildings
from app.services.detail_operations_access import detail_scope_dependency
from app.services.rent_analytics import (
    build_rent_analytics,
    export_rent_analytics_excel,
    parse_segments,
)
from app.services.property_scope import SelectedPropertyScope

get_selected_view_scope = detail_scope_dependency("analytics", for_write=False)


router = APIRouter(
    prefix="/api/rent-analytics",
    tags=["rent-analytics"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
def rent_analytics(
    start_date: date | None = None,
    end_date: date | None = None,
    segments: str | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return build_rent_analytics(
        db,
        start_date=start_date,
        end_date=end_date,
        segments=parse_segments(segments),
        building_ids=scope.building_ids,
    )


@router.get("/export")
def export_rent_analytics(
    start_date: date | None = None,
    end_date: date | None = None,
    segments: str | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    editor: User = Depends(require_editor),
    db: Session = Depends(get_db),
):
    selected_segments = parse_segments(segments)
    analytics = build_rent_analytics(
        db,
        start_date=start_date,
        end_date=end_date,
        segments=selected_segments,
        building_ids=scope.building_ids,
    )
    response = export_rent_analytics_excel(analytics, selected_segments)
    bounds = analytics["bounds"]
    log_action_for_buildings(
        db,
        scope.building_ids,
        "rent_analytics_export",
        (
            f"导出租金时间分析；日期 {bounds['requested_start']} 至 {bounds['requested_end']}；"
            f"分区 {','.join(selected_segments)}"
        ),
        editor.username,
        target_type="rent_analytics",
    )
    return response
