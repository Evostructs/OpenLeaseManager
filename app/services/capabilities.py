from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.time import now_in_timezone, today_in_timezone
from app.models import User, UserCapability


INITIAL_PLATFORM_CAPABILITIES = frozenset({"platform_admin", "customer_master_manage"})


def grant_capabilities(
    db: Session,
    user: User,
    capabilities: Iterable[str],
    *,
    granted_by: int | None = None,
) -> None:
    """Add explicit, current grants without committing the caller's transaction."""
    if user.id is None:
        raise ValueError("user must be persisted before granting capabilities")
    effective_date = today_in_timezone()
    requested = {str(item).strip() for item in capabilities if str(item).strip()}
    if not requested:
        return
    existing = {
        capability
        for (capability,) in (
            db.query(UserCapability.capability)
            .filter(
                UserCapability.user_id == user.id,
                UserCapability.capability.in_(requested),
                UserCapability.valid_from <= effective_date,
                (UserCapability.valid_to.is_(None) | (UserCapability.valid_to >= effective_date)),
            )
            .all()
        )
    }
    granted_at = now_in_timezone()
    for capability in sorted(requested - existing):
        db.add(
            UserCapability(
                user_id=int(user.id),
                capability=capability,
                valid_from=effective_date,
                valid_to=None,
                granted_by=granted_by,
                granted_at=granted_at,
            )
        )


def grant_initial_platform_capabilities(db: Session, user: User) -> None:
    grant_capabilities(db, user, INITIAL_PLATFORM_CAPABILITIES)
