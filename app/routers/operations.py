from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.database import get_db
from app.models import User
from app.portfolio_overview_schemas import PortfolioOverviewOut
from app.services.portfolio_overview import build_portfolio_overview
from app.services.property_scope import (
    SelectedPropertyScope,
    get_selected_edit_scope,
    get_selected_portfolio_overview_scope,
    get_selected_view_scope,
    require_capability,
    resolve_user_scope,
)
from app.services.published_operating_snapshot import build_published_operating_snapshot
from app.services.snapshot_workflow import (
    create_snapshot_source,
    get_snapshot_submission,
    list_snapshot_sources,
    publish_operating_snapshot,
    review_operating_snapshot,
    submit_operating_snapshot,
)
from app.services.snapshot_evidence import build_snapshot_evidence
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


router = APIRouter(prefix="/api/operations", tags=["operations"])
require_data_import_submit = require_capability("data_import_submit")
require_snapshot_review = require_capability("snapshot_review")
require_snapshot_publish = require_capability("snapshot_publish")


class SnapshotSourcePayload(BaseModel):
    source_type: Literal["manual_snapshot", "excel"]
    name: str = Field(min_length=1, max_length=150)
    owner: str = Field(default="", max_length=100)
    cadence: str = Field(min_length=1, max_length=50)
    freshness_rule: str = Field(min_length=1, max_length=2000)


class SnapshotMetricFactPayload(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    value: float | None = None
    numerator: float
    denominator: float | None = None
    covered_area_sqm: float | None = Field(default=None, ge=0)
    eligible_area_sqm: float | None = Field(default=None, ge=0)
    covered_days: int = Field(default=0, ge=0)
    eligible_days: int = Field(default=0, ge=0)
    estimate_share: float | None = Field(default=None, ge=0, le=1)
    estimate_method: str = Field(default="", max_length=2000)
    completeness_status: Literal["unknown", "partial", "complete"] = "unknown"
    freshness_status: Literal["unknown", "current", "stale"] = "unknown"
    calculation_version: str = Field(min_length=1, max_length=100)


class SnapshotSubmissionPayload(BaseModel):
    source_id: int = Field(gt=0)
    source_file_name: str | None = Field(default=None, max_length=255)
    external_batch_no: str | None = Field(default=None, max_length=100)
    file_checksum: str | None = Field(default=None, min_length=16, max_length=128)
    parser_version: str = Field(default="ops-v1-normalized-v1", min_length=1, max_length=100)
    period_start: date
    period_end: date
    as_of_date: date
    source_observed_at: datetime
    source_updated_at: datetime | None = None
    correction_reason: str | None = Field(default=None, max_length=2000)
    facts: list[SnapshotMetricFactPayload] = Field(min_length=1, max_length=100)


class SnapshotReviewPayload(BaseModel):
    approved: bool
    notes: str = Field(default="", max_length=2000)


class SnapshotPublishPayload(BaseModel):
    notes: str = Field(default="", max_length=2000)


@router.get("/portfolio-overview", response_model=PortfolioOverviewOut)
def portfolio_overview(
    response: Response,
    as_of_date: date | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_portfolio_overview_scope),
    db: Session = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    return build_portfolio_overview(db, scope=scope, as_of_date=as_of_date)


def require_snapshot_workflow_reader(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    capabilities = set(resolve_user_scope(db, user).effective_capabilities)
    if capabilities.isdisjoint({"data_import_submit", "snapshot_review", "snapshot_publish"}):
        from fastapi import HTTPException

        raise HTTPException(403, "缺少经营快照流程权限")
    return user


@router.get("/snapshot")
def operating_snapshot(
    as_of_date: date | None = None,
    include_trend: bool = True,
    queue_limit: int = Query(0, ge=0, le=50),
    risk_limit: int = Query(8, ge=0, le=50),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
) -> dict:
    return build_unified_operating_snapshot(
        db,
        scope=scope,
        as_of_date=as_of_date,
        include_trend=include_trend,
        queue_limit=queue_limit,
        risk_limit=risk_limit,
    )


@router.get("/published-snapshot")
def published_snapshot(
    as_of_date: date,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
) -> dict:
    return build_published_operating_snapshot(db, scope=scope, as_of_date=as_of_date)


@router.get("/snapshot-evidence")
def snapshot_evidence(
    metric_code: str = Query(..., min_length=1, max_length=100),
    as_of_date: date | None = None,
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
) -> dict:
    return build_snapshot_evidence(
        db,
        scope=scope,
        metric_code=metric_code,
        as_of_date=as_of_date,
    )


@router.get("/snapshot-sources")
def snapshot_sources(
    _workflow_user: User = Depends(require_snapshot_workflow_reader),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_snapshot_sources(db, scope=scope)


@router.post("/snapshot-sources")
def post_snapshot_source(
    payload: SnapshotSourcePayload,
    _editor: User = Depends(require_editor),
    actor: User = Depends(require_data_import_submit),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
) -> dict:
    return create_snapshot_source(
        db,
        scope=scope,
        actor=actor,
        payload=payload.model_dump(),
    )


@router.post("/snapshot-submissions")
def post_snapshot_submission(
    payload: SnapshotSubmissionPayload,
    _editor: User = Depends(require_editor),
    actor: User = Depends(require_data_import_submit),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
) -> dict:
    return submit_operating_snapshot(
        db,
        scope=scope,
        actor=actor,
        payload=payload.model_dump(),
    )


@router.get("/snapshot-submissions/{snapshot_id}")
def snapshot_submission(
    snapshot_id: int,
    _workflow_user: User = Depends(require_snapshot_workflow_reader),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
) -> dict:
    capabilities = set(resolve_user_scope(db, _workflow_user).effective_capabilities)
    return get_snapshot_submission(
        db,
        snapshot_id,
        scope=scope,
        include_exchange_evidence="snapshot_review" in capabilities,
    )


@router.post("/snapshot-submissions/{snapshot_id}/review")
def post_snapshot_review(
    snapshot_id: int,
    payload: SnapshotReviewPayload,
    _editor: User = Depends(require_editor),
    actor: User = Depends(require_snapshot_review),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
) -> dict:
    return review_operating_snapshot(
        db,
        snapshot_id,
        scope=scope,
        actor=actor,
        approved=payload.approved,
        notes=payload.notes,
        include_exchange_evidence=True,
    )


@router.post("/snapshot-submissions/{snapshot_id}/publish")
def post_snapshot_publish(
    snapshot_id: int,
    payload: SnapshotPublishPayload,
    _editor: User = Depends(require_editor),
    actor: User = Depends(require_snapshot_publish),
    scope: SelectedPropertyScope = Depends(get_selected_edit_scope),
    db: Session = Depends(get_db),
) -> dict:
    return publish_operating_snapshot(
        db,
        snapshot_id,
        scope=scope,
        actor=actor,
        notes=payload.notes,
    )
