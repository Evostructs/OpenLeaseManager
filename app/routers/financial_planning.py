from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.database import get_db
from app.financial_planning_schemas import (
    AdoptVersionRequest, CompareRequest, ExpensePayload, ForecastRequest, SaveVersionRequest,
)
from app.models import Building, User
from app.services.detail_operations_access import detail_scope_dependency
from app.services import financial_planning_store as store
from app.services.property_scope import SelectedPropertyScope, require_single_selected_building, resolve_user_scope

get_view_scope = detail_scope_dependency("analytics", for_write=False)
get_edit_scope = detail_scope_dependency("analytics", for_write=True)
router = APIRouter(prefix="/api/financial-planning", tags=["financial-planning"],
                   dependencies=[Depends(get_current_user)])


@router.get("/source")
def source(year: int = Query(ge=2000, le=2100), closed_through_month: int = Query(default=0, ge=0, le=12),
           forecast_mode: Literal["live", "workspace", "workspace_deferred", "none"] = Query(default="live"),
           include_latest_snapshot: bool = Query(default=True),
           scope: SelectedPropertyScope = Depends(get_view_scope),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    building_id = require_single_selected_building(scope)
    building = db.get(Building, building_id)
    state = store.planning_state(db, building_id, year)
    budget = store.get_version(db, state.active_budget_id, building_id, year) if state and state.active_budget_id else None
    latest = store.get_version(db, state.active_forecast_id, building_id, year) if state and state.active_forecast_id else None
    forecast = None
    needs_workspace_forecast = latest is None and (
        forecast_mode == "workspace" or (forecast_mode == "workspace_deferred" and budget is None)
    )
    if forecast_mode == "live" or needs_workspace_forecast:
        forecast = store.build_forecast(db, building_id, ForecastRequest(
            year=year, closed_through_month=closed_through_month, base_version_id=latest.id if latest else None,
        ))
    return {
        "building": {"id": building.id, "code": building.code, "name": building.name},
        "year": year, "revision": state.revision if state else 0,
        "can_write": building_id in resolve_user_scope(db, user).edit_building_ids,
        "forecast": forecast, "expenses": store.expenses_for_year(db, building_id, year),
        "versions": store.list_versions(db, building_id, year),
        "approved_budget": store.version_dict(budget) if budget else None,
        "latest_forecast": store.version_dict(latest, summary=not include_latest_snapshot) if latest else None,
    }


@router.post("/preview")
def preview(payload: ForecastRequest, scope: SelectedPropertyScope = Depends(get_view_scope),
            db: Session = Depends(get_db)):
    return store.build_forecast(db, require_single_selected_building(scope), payload)


@router.get("/expenses")
def expenses(year: int = Query(ge=2000, le=2100), scope: SelectedPropertyScope = Depends(get_view_scope),
             db: Session = Depends(get_db)):
    return {"items": store.expenses_for_year(db, require_single_selected_building(scope), year)}


@router.post("/expenses")
def create_expense(payload: ExpensePayload, scope: SelectedPropertyScope = Depends(get_edit_scope),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.save_expense(db, require_single_selected_building(scope), user, payload)


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: int, payload: ExpensePayload, scope: SelectedPropertyScope = Depends(get_edit_scope),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.save_expense(db, require_single_selected_building(scope), user, payload, expense_id)


@router.get("/versions")
def versions(year: int = Query(ge=2000, le=2100), scope: SelectedPropertyScope = Depends(get_view_scope),
             db: Session = Depends(get_db)):
    building_id = require_single_selected_building(scope)
    state = store.planning_state(db, building_id, year)
    return {"revision": state.revision if state else 0, "items": store.list_versions(db, building_id, year)}


@router.get("/versions/{version_id}")
def version(version_id: int, scope: SelectedPropertyScope = Depends(get_view_scope), db: Session = Depends(get_db)):
    return store.version_dict(store.get_version(db, version_id, require_single_selected_building(scope)))


@router.post("/versions")
def save_version(payload: SaveVersionRequest, scope: SelectedPropertyScope = Depends(get_edit_scope),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.save_version(db, require_single_selected_building(scope), user, payload)


@router.post("/versions/{version_id}/adopt")
def adopt(version_id: int, payload: AdoptVersionRequest, scope: SelectedPropertyScope = Depends(get_edit_scope),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.adopt_version(db, require_single_selected_building(scope), user, version_id, payload.expected_revision)


@router.post("/compare")
def compare(payload: CompareRequest, include_baseline: bool = Query(default=True),
            scope: SelectedPropertyScope = Depends(get_view_scope),
            db: Session = Depends(get_db)):
    from app.services.financial_planning import compare_financial_snapshots

    building_id = require_single_selected_building(scope)
    baseline = store.version_dict(store.get_version(db, payload.baseline_version_id, building_id, payload.year))
    if payload.current_version_id is not None:
        saved_current = store.get_version(db, payload.current_version_id, building_id, payload.year)
        if saved_current.kind not in {"forecast", "scenario"}:
            raise HTTPException(status_code=422, detail="当前比较版本必须为预测或方案")
        current = store.version_dict(saved_current)["snapshot"]
    else:
        current = store.build_forecast(db, building_id, payload)
    result = {"current": current,
              "comparison": compare_financial_snapshots(baseline["snapshot"], current, metric=payload.metric)}
    if include_baseline:
        result["baseline"] = baseline
    return result
