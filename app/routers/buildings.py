from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.database import get_db
from app.models import Building, User
from app.services.building_operating_gate import (
    decorate_scope_summary_with_operating_gates,
    record_building_operating_gate,
    resolve_building_operating_gates,
)
from app.services.property_scope import require_platform_admin, resolve_user_scope, scope_summary


router = APIRouter(prefix="/api/buildings", tags=["buildings"])


class BuildingOperatingGatePayload(BaseModel):
    presentation_enabled: bool
    contribution_enabled: bool
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0)


def _active_building_ids(db: Session) -> tuple[int, ...]:
    return tuple(
        int(building_id)
        for (building_id,) in db.query(Building.id)
        .filter(Building.is_active.is_(True))
        .order_by(Building.code.asc(), Building.id.asc())
        .all()
    )


@router.get("")
def buildings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    resolved = resolve_user_scope(db, user)
    gates = resolve_building_operating_gates(db, resolved.view_building_ids)
    return decorate_scope_summary_with_operating_gates(scope_summary(resolved), gates)


@router.get("/operating-gates")
def operating_gates(
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    buildings = (
        db.query(Building)
        .filter(Building.is_active.is_(True))
        .order_by(Building.code.asc(), Building.id.asc())
        .all()
    )
    resolution = resolve_building_operating_gates(db, tuple(int(item.id) for item in buildings))
    name_by_id = {int(item.id): str(item.name) for item in buildings}
    return {
        **resolution.as_dict(),
        "items": [
            {**item.as_dict(), "building_name": name_by_id[item.building_id]}
            for item in resolution.decisions
        ],
        "gate_policy_version": resolution.policy_version,
        "data_classification": "simulated",
    }


@router.put("/{building_code}/operating-gate")
def put_operating_gate(
    building_code: str,
    payload: BuildingOperatingGatePayload,
    actor: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    decision = record_building_operating_gate(
        db,
        building_code,
        actor,
        presentation_enabled=payload.presentation_enabled,
        contribution_enabled=payload.contribution_enabled,
        reason=payload.reason,
        expected_revision=payload.expected_revision,
    )
    resolution = resolve_building_operating_gates(db, _active_building_ids(db))
    return {
        "decision": decision.as_dict(),
        "gate_policy_version": resolution.policy_version,
        "data_classification": "simulated",
    }
