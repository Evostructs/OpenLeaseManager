from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.authorization_admin import (
    describe_user_authorization,
    replace_user_authorization,
)
from app.services.property_scope import require_platform_admin


router = APIRouter(
    prefix="/api/auth/authorizations",
    tags=["authorizations"],
    dependencies=[Depends(require_platform_admin)],
)


class AuthorizationGrantPayload(BaseModel):
    scope_type: Literal["building", "portfolio", "portfolio_overview"]
    scope_code: str = Field(min_length=1, max_length=50)
    access_level: Literal["view", "edit"]
    include_future_buildings: bool = False


class UserAuthorizationPayload(BaseModel):
    expected_authorization_version: str = Field(min_length=1, max_length=100)
    grants: list[AuthorizationGrantPayload] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list)


@router.get("/users/{user_id}")
def get_user_authorization(user_id: int, db: Session = Depends(get_db)) -> dict:
    return describe_user_authorization(db, user_id)


@router.put("/users/{user_id}")
def put_user_authorization(
    user_id: int,
    payload: UserAuthorizationPayload,
    operator: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    grants = [item.model_dump() for item in payload.grants]
    return replace_user_authorization(
        db,
        user_id,
        grants=grants,
        capabilities=payload.capabilities,
        expected_authorization_version=payload.expected_authorization_version,
        operator=operator,
    )
