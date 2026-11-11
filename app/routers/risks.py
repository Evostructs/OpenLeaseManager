from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.database import get_db
from app.models import User
from app.services.detail_operations_access import detail_scope_dependency
from app.services.import_export import export_risks_excel
from app.services.risks import list_risks
from app.services.property_scope import SelectedPropertyScope

get_selected_view_scope = detail_scope_dependency("risks", for_write=False)


router = APIRouter(prefix="/api/risks", tags=["risks"], dependencies=[Depends(get_current_user)])


@router.get("/export")
def export_risks(
    risk_type: str | None = None,
    severity: str | None = None,
    entity_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    _editor: User = Depends(require_editor),
    db: Session = Depends(get_db),
):
    return export_risks_excel(
        db,
        risk_type=risk_type,
        severity=severity,
        entity_type=entity_type,
        contract_id=contract_id,
        customer_id=customer_id,
        building_ids=scope.building_ids,
    )


@router.get("", response_model=dict)
def risk_list(
    risk_type: str | None = None,
    severity: str | None = None,
    entity_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    return list_risks(
        db,
        risk_type=risk_type,
        severity=severity,
        entity_type=entity_type,
        contract_id=contract_id,
        customer_id=customer_id,
        limit=limit,
        building_ids=scope.building_ids,
    )
