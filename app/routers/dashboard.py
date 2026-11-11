from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.database import get_db
from app.schemas import OperationsDashboardV2Response
from app.services.dashboard import get_operations_dashboard, get_operations_dashboard_v2, get_operations_trend
from app.services.detail_operations_access import detail_scope_dependency
from app.services.property_scope import SelectedPropertyScope

get_selected_view_scope = detail_scope_dependency("dashboard", for_write=False)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


@router.get("/operations")
def operations(
    include_trend: bool = True,
    queue_limit: int = Query(0, ge=0, le=50),
    risk_limit: int = Query(8, ge=0, le=50),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_operations_dashboard(
        db,
        include_trend=include_trend,
        queue_limit=queue_limit,
        risk_limit=risk_limit,
        building_ids=scope.building_ids,
    )


@router.get("/operations/v2", response_model=OperationsDashboardV2Response)
def operations_v2(
    include_trend: bool = True,
    queue_limit: int = Query(0, ge=0, le=50),
    risk_limit: int = Query(8, ge=0, le=50),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_operations_dashboard_v2(
        db,
        include_trend=include_trend,
        queue_limit=queue_limit,
        risk_limit=risk_limit,
        building_ids=scope.building_ids,
    )


@router.get("/operations/trend")
def operations_trend(
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return get_operations_trend(db, building_ids=scope.building_ids)
