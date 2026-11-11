from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.core.time import today_in_timezone
from app.database import get_db
from app.models import (
    Building,
    Portfolio,
    PortfolioBuilding,
    User,
    UserCapability,
    UserPortfolioOverviewGrant,
    UserScopeGrant,
    Zone,
)
from app.services.building_operations_profile import resolve_building_operations_profile


LEGACY_DEFAULT_BUILDING_CODE = "DEMO_A"
EDIT_ROLES = frozenset({"admin", "editor"})


@dataclass(frozen=True)
class BuildingAccess:
    id: int
    code: str
    name: str
    timezone: str
    currency: str
    ingestion_mode: str
    is_provisional: bool
    can_edit: bool
    operations_zones: tuple[tuple[str, str, str], ...] = ()

    @property
    def access_level(self) -> str:
        return "edit" if self.can_edit else "view"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "timezone": self.timezone,
            "currency": self.currency,
            "ingestion_mode": self.ingestion_mode,
            "is_provisional": self.is_provisional,
            "can_view": True,
            "can_edit": self.can_edit,
            "access_level": self.access_level,
            "operations_profile": resolve_building_operations_profile(self).as_dict(),
        }


@dataclass(frozen=True)
class PortfolioAccess:
    id: int
    code: str
    name: str
    building_codes: tuple[str, ...]
    editable_building_codes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "building_codes": list(self.building_codes),
            "editable_building_codes": list(self.editable_building_codes),
        }


@dataclass(frozen=True)
class PortfolioOverviewAccess:
    """Portfolio summary access without any implied building business access."""

    id: int
    code: str
    name: str
    building_ids: tuple[int, ...]
    building_codes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "building_codes": list(self.building_codes),
        }


@dataclass(frozen=True)
class ResolvedScope:
    user_id: int
    role: str
    buildings: tuple[BuildingAccess, ...]
    portfolios: tuple[PortfolioAccess, ...]
    portfolio_overviews: tuple[PortfolioOverviewAccess, ...]
    default_building_code: str | None
    effective_capabilities: tuple[str, ...]
    authorization_revision: int
    authorization_version: str

    @property
    def view_building_ids(self) -> frozenset[int]:
        return frozenset(item.id for item in self.buildings)

    @property
    def edit_building_ids(self) -> frozenset[int]:
        return frozenset(item.id for item in self.buildings if item.can_edit)

    @property
    def view_building_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.buildings)

    @property
    def edit_building_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.buildings if item.can_edit)

    @property
    def default_building(self) -> BuildingAccess | None:
        if self.default_building_code is None:
            return None
        return next((item for item in self.buildings if item.code == self.default_building_code), None)

    def has_capability(self, capability: str) -> bool:
        return capability in self.effective_capabilities

    def require_capability(self, capability: str) -> None:
        if not self.has_capability(capability):
            raise HTTPException(403, "缺少所需平台权限")

    def require_building_code(self, code: str, *, access_level: str = "view") -> BuildingAccess:
        normalized = str(code or "").strip().casefold()
        matches = [item for item in self.buildings if item.code.casefold() == normalized]
        if len(matches) != 1:
            raise HTTPException(404, "楼宇不存在或无权访问")
        item = matches[0]
        if access_level == "edit" and not item.can_edit:
            raise HTTPException(403, "当前楼宇仅有查看权限")
        if access_level != "view" and access_level != "edit":
            raise ValueError("access_level must be 'view' or 'edit'")
        return item


@dataclass(frozen=True)
class SelectedPropertyScope:
    """One explicit, request-stable property selection using only public codes."""

    user_id: int
    building_ids: tuple[int, ...]
    building_codes: tuple[str, ...]
    scope_type: str
    scope_code: str
    portfolio_id: int | None
    access_level: str
    authorization_version: str


def assert_selected_object_building(
    scope: SelectedPropertyScope,
    building_id: int | None,
) -> int:
    """Validate against the request selection, not merely the user's full catalog."""
    if building_id is None:
        raise HTTPException(409, "对象尚未明确楼宇归属")
    normalized = int(building_id)
    if normalized not in scope.building_ids:
        raise HTTPException(404, "对象不存在或无权访问")
    return normalized


def require_single_selected_building(scope: SelectedPropertyScope) -> int:
    if scope.scope_type != "building" or len(scope.building_ids) != 1:
        raise HTTPException(422, "该操作必须明确选择一栋楼宇")
    return int(scope.building_ids[0])


def _normalize_public_scope_code(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(422, f"{label}不能为空")
    if re.fullmatch(r"[+-]?\d+", normalized):
        raise HTTPException(422, f"{label}必须使用稳定代码，不能使用数字 ID")
    return normalized.casefold()


def select_property_scope(
    resolved: ResolvedScope,
    *,
    building: str | None = None,
    portfolio: str | None = None,
    access_level: str = "view",
) -> SelectedPropertyScope:
    """Select an authorized building or portfolio without exposing numeric IDs."""
    if access_level not in {"view", "edit"}:
        raise ValueError("access_level must be 'view' or 'edit'")
    if building is not None and portfolio is not None:
        raise HTTPException(422, "building 与 portfolio 只能选择一个")

    if building is None and portfolio is None:
        selected_building = resolved.default_building
        if selected_building is None:
            raise HTTPException(404, "楼宇不存在或无权访问")
        if access_level == "edit" and not selected_building.can_edit:
            raise HTTPException(404, "楼宇不存在或无权访问")
        return SelectedPropertyScope(
            user_id=resolved.user_id,
            building_ids=(selected_building.id,),
            building_codes=(selected_building.code,),
            scope_type="building",
            scope_code=selected_building.code,
            portfolio_id=None,
            access_level=access_level,
            authorization_version=resolved.authorization_version,
        )

    if building is not None:
        normalized = _normalize_public_scope_code(building, label="building")
        matches = [item for item in resolved.buildings if item.code.casefold() == normalized]
        if len(matches) != 1 or (access_level == "edit" and not matches[0].can_edit):
            raise HTTPException(404, "楼宇不存在或无权访问")
        selected_building = matches[0]
        return SelectedPropertyScope(
            user_id=resolved.user_id,
            building_ids=(selected_building.id,),
            building_codes=(selected_building.code,),
            scope_type="building",
            scope_code=selected_building.code,
            portfolio_id=None,
            access_level=access_level,
            authorization_version=resolved.authorization_version,
        )

    normalized = _normalize_public_scope_code(portfolio or "", label="portfolio")
    portfolio_matches = [item for item in resolved.portfolios if item.code.casefold() == normalized]
    if len(portfolio_matches) != 1:
        raise HTTPException(404, "板块不存在或无权访问")
    selected_portfolio = portfolio_matches[0]
    building_by_code = {item.code.casefold(): item for item in resolved.buildings}
    selected_buildings = tuple(
        building_by_code[code.casefold()]
        for code in selected_portfolio.building_codes
        if code.casefold() in building_by_code
    )
    if len(selected_buildings) != len(selected_portfolio.building_codes) or not selected_buildings:
        raise HTTPException(404, "板块不存在或无权访问")
    if access_level == "edit" and any(not item.can_edit for item in selected_buildings):
        raise HTTPException(404, "板块不存在或无权访问")
    return SelectedPropertyScope(
        user_id=resolved.user_id,
        building_ids=tuple(item.id for item in selected_buildings),
        building_codes=tuple(item.code for item in selected_buildings),
        scope_type="portfolio",
        scope_code=selected_portfolio.code,
        portfolio_id=selected_portfolio.id,
        access_level=access_level,
        authorization_version=resolved.authorization_version,
    )


def _validate_public_scope_query(request: Request) -> None:
    pairs = list(request.query_params.multi_items())
    keys = [key for key, _value in pairs]
    if "building_id" in keys or "portfolio_id" in keys:
        raise HTTPException(422, "楼宇和板块必须使用稳定代码，不能使用数字 ID")
    if keys.count("building") > 1 or keys.count("portfolio") > 1:
        raise HTTPException(422, "building 与 portfolio 均只能提供一次")


def get_selected_view_scope(
    request: Request,
    building: str | None = Query(default=None),
    portfolio: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SelectedPropertyScope:
    _validate_public_scope_query(request)
    return select_property_scope(
        resolve_user_scope(db, user),
        building=building,
        portfolio=portfolio,
        access_level="view",
    )


def get_selected_edit_scope(
    request: Request,
    building: str | None = Query(default=None),
    portfolio: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SelectedPropertyScope:
    _validate_public_scope_query(request)
    return select_property_scope(
        resolve_user_scope(db, user),
        building=building,
        portfolio=portfolio,
        access_level="edit",
    )


def get_selected_portfolio_overview_scope(
    request: Request,
    portfolio: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SelectedPropertyScope:
    """Select only an explicitly granted overview; never reuse building scope."""

    pairs = list(request.query_params.multi_items())
    keys = [key for key, _value in pairs]
    unsupported = sorted(set(keys) - {"portfolio", "as_of_date"})
    if unsupported:
        raise HTTPException(422, f"板块概况不接受参数：{unsupported[0]}")
    if keys.count("portfolio") != 1 or keys.count("as_of_date") > 1:
        raise HTTPException(422, "板块概况参数不能重复")
    resolved = resolve_user_scope(db, user)
    normalized_portfolio = _normalize_public_scope_code(portfolio, label="portfolio")
    matches = [
        item
        for item in resolved.portfolio_overviews
        if item.code.casefold() == normalized_portfolio
    ]
    if len(matches) != 1:
        raise HTTPException(404, "板块概况不存在或无权访问")
    selected = matches[0]

    if not selected.building_ids:
        raise HTTPException(404, "板块概况暂无可展示楼宇")
    return SelectedPropertyScope(
        user_id=resolved.user_id,
        building_ids=selected.building_ids,
        building_codes=selected.building_codes,
        scope_type="portfolio",
        scope_code=selected.code,
        portfolio_id=selected.id,
        access_level="overview",
        authorization_version=resolved.authorization_version,
    )


def require_capability(capability: str):
    """Build a FastAPI dependency for platform-wide actions outside property scope."""
    required = str(capability).strip()
    if not required:
        raise ValueError("capability must not be empty")

    def dependency(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        resolve_user_scope(db, user).require_capability(required)
        return user

    stable_name = f"require_{re.sub(r'[^a-zA-Z0-9_]+', '_', required).strip('_')}"
    dependency.__name__ = stable_name
    dependency.__qualname__ = stable_name
    return dependency


require_platform_admin = require_capability("platform_admin")
require_customer_master_manager = require_capability("customer_master_manage")


def _active_on(model, as_of_date: date):
    return (
        model.valid_from <= as_of_date,
        or_(model.valid_to.is_(None), model.valid_to >= as_of_date),
    )


def _merge_access(current: str | None, incoming: str) -> str:
    if current == "edit" or incoming == "edit":
        return "edit"
    return "view"


def _membership_existed_when_granted(
    membership: PortfolioBuilding,
    grant: UserScopeGrant | UserPortfolioOverviewGrant,
) -> bool:
    if not (
        membership.valid_from <= grant.valid_from
        and (membership.valid_to is None or membership.valid_to >= grant.valid_from)
    ):
        return False
    # Date-only validity cannot distinguish "grant first, add building later" on
    # the same day.  The immutable creation/grant timestamps close that boundary.
    if membership.created_at is None or grant.granted_at is None:
        return membership.valid_from < grant.valid_from
    return membership.created_at <= grant.granted_at


def _effective_capabilities(db: Session, user_id: int, as_of_date: date) -> tuple[str, ...]:
    rows = (
        db.query(UserCapability.capability)
        .filter(UserCapability.user_id == user_id, *_active_on(UserCapability, as_of_date))
        .distinct()
        .all()
    )
    return tuple(sorted({str(value) for (value,) in rows if value}))


def _authorization_version(
    *,
    user: User,
    grants: tuple[UserScopeGrant, ...],
    overview_grants: tuple[UserPortfolioOverviewGrant, ...],
    buildings: tuple[BuildingAccess, ...],
    portfolios: tuple[PortfolioAccess, ...],
    portfolio_overviews: tuple[PortfolioOverviewAccess, ...],
    capabilities: tuple[str, ...],
) -> str:
    canonical = {
        "user_id": int(user.id),
        "role": str(user.role or ""),
        "authorization_revision": int(user.authorization_revision or 0),
        "raw_grants": [
            [
                str(item.scope_type),
                int(item.portfolio_id) if item.portfolio_id is not None else None,
                int(item.building_id) if item.building_id is not None else None,
                str(item.access_level),
                bool(item.include_future_buildings),
                item.valid_from.isoformat() if item.valid_from else None,
                item.valid_to.isoformat() if item.valid_to else None,
            ]
            for item in grants
        ],
        "raw_overview_grants": [
            [
                int(item.portfolio_id),
                bool(item.include_future_buildings),
                item.valid_from.isoformat() if item.valid_from else None,
                item.valid_to.isoformat() if item.valid_to else None,
            ]
            for item in overview_grants
        ],
        "buildings": [[item.id, item.code, item.access_level] for item in buildings],
        "portfolios": [
            [item.id, item.code, list(item.building_codes), list(item.editable_building_codes)]
            for item in portfolios
        ],
        "portfolio_overviews": [
            [item.id, item.code, list(item.building_codes)]
            for item in portfolio_overviews
        ],
        "capabilities": list(capabilities),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"scope-v1-r{int(user.authorization_revision or 0)}-{digest}"


def resolve_user_scope(
    db: Session,
    user: User,
    *,
    as_of_date: date | None = None,
) -> ResolvedScope:
    """Resolve only explicitly granted, active buildings; roles never imply global access."""
    if user.id is None:
        raise ValueError("user must be persisted before resolving scope")
    effective_date = as_of_date or today_in_timezone()
    grants = tuple(
        db.query(UserScopeGrant)
        .filter(UserScopeGrant.user_id == user.id, *_active_on(UserScopeGrant, effective_date))
        .order_by(UserScopeGrant.id.asc())
        .all()
    )
    overview_grants = tuple(
        db.query(UserPortfolioOverviewGrant)
        .filter(
            UserPortfolioOverviewGrant.user_id == user.id,
            *_active_on(UserPortfolioOverviewGrant, effective_date),
        )
        .order_by(UserPortfolioOverviewGrant.id.asc())
        .all()
    )

    access_by_building_id: dict[int, str] = {}
    direct_ids = {int(grant.building_id) for grant in grants if grant.scope_type == "building" and grant.building_id}
    if direct_ids:
        active_direct_ids = {
            building_id
            for (building_id,) in db.query(Building.id)
            .filter(Building.id.in_(direct_ids), Building.is_active.is_(True))
            .all()
        }
        for grant in grants:
            if grant.scope_type != "building" or grant.building_id not in active_direct_ids:
                continue
            access_by_building_id[int(grant.building_id)] = _merge_access(
                access_by_building_id.get(int(grant.building_id)),
                grant.access_level,
            )

    portfolio_grants: dict[int, list[UserScopeGrant]] = {}
    for grant in grants:
        if grant.scope_type == "portfolio" and grant.portfolio_id:
            portfolio_grants.setdefault(int(grant.portfolio_id), []).append(grant)
    if portfolio_grants:
        rows = (
            db.query(PortfolioBuilding, Building, Portfolio)
            .join(Building, Building.id == PortfolioBuilding.building_id)
            .join(Portfolio, Portfolio.id == PortfolioBuilding.portfolio_id)
            .filter(
                PortfolioBuilding.portfolio_id.in_(portfolio_grants),
                *_active_on(PortfolioBuilding, effective_date),
                Building.is_active.is_(True),
                Portfolio.is_active.is_(True),
            )
            .all()
        )
        for membership, building, _portfolio in rows:
            for grant in portfolio_grants.get(int(membership.portfolio_id), []):
                if not grant.include_future_buildings and not _membership_existed_when_granted(membership, grant):
                    continue
                access_by_building_id[int(building.id)] = _merge_access(
                    access_by_building_id.get(int(building.id)),
                    grant.access_level,
                )

    allowed_ids = tuple(sorted(access_by_building_id))
    active_buildings = (
        db.query(Building)
        .filter(Building.id.in_(allowed_ids), Building.is_active.is_(True))
        .all()
        if allowed_ids
        else []
    )
    active_zones = (
        db.query(Zone)
        .filter(
            Zone.building_id.in_(allowed_ids),
            Zone.is_active.is_(True),
            *_active_on(Zone, effective_date),
        )
        .order_by(Zone.building_id.asc(), Zone.sort_order.asc(), Zone.id.asc())
        .all()
        if allowed_ids
        else []
    )
    zones_by_building_id: dict[int, list[tuple[str, str, str]]] = {}
    for zone in active_zones:
        zones_by_building_id.setdefault(int(zone.building_id), []).append(
            (str(zone.code), str(zone.name), str(zone.zone_type))
        )
    role_allows_edit = str(user.role or "") in EDIT_ROLES
    buildings = tuple(
        sorted(
            (
                BuildingAccess(
                    id=int(item.id),
                    code=str(item.code).strip(),
                    name=str(item.name),
                    timezone=str(item.timezone),
                    currency=str(item.currency),
                    ingestion_mode=str(item.ingestion_mode),
                    is_provisional=bool(item.is_provisional),
                    can_edit=role_allows_edit and access_by_building_id[int(item.id)] == "edit",
                    operations_zones=tuple(zones_by_building_id.get(int(item.id), ())),
                )
                for item in active_buildings
            ),
            key=lambda item: (item.code.casefold(), item.id),
        )
    )
    building_by_id = {item.id: item for item in buildings}

    # A collection of directly granted buildings is not a portfolio grant.
    # Exposing its parent portfolio would let a single-building user select a
    # portfolio-scoped endpoint (notably portfolio audit records).  Portfolio
    # selectors therefore exist only when an effective portfolio grant exists;
    # direct grants may still add authorized member buildings to that selector.
    portfolio_groups: dict[int, dict] = {}
    if building_by_id and portfolio_grants:
        rows = (
            db.query(PortfolioBuilding, Portfolio)
            .join(Portfolio, Portfolio.id == PortfolioBuilding.portfolio_id)
            .filter(
                PortfolioBuilding.portfolio_id.in_(tuple(portfolio_grants)),
                PortfolioBuilding.building_id.in_(building_by_id),
                *_active_on(PortfolioBuilding, effective_date),
                Portfolio.is_active.is_(True),
            )
            .all()
        )
        for membership, portfolio in rows:
            group = portfolio_groups.setdefault(
                int(portfolio.id),
                {"portfolio": portfolio, "building_codes": set(), "editable_building_codes": set()},
            )
            building = building_by_id[int(membership.building_id)]
            group["building_codes"].add(building.code)
            if building.can_edit:
                group["editable_building_codes"].add(building.code)

    portfolios = tuple(
        sorted(
            (
                PortfolioAccess(
                    id=portfolio_id,
                    code=str(group["portfolio"].code).strip(),
                    name=str(group["portfolio"].name),
                    building_codes=tuple(sorted(group["building_codes"], key=str.casefold)),
                    editable_building_codes=tuple(sorted(group["editable_building_codes"], key=str.casefold)),
                )
                for portfolio_id, group in portfolio_groups.items()
            ),
            key=lambda item: (item.code.casefold(), item.id),
        )
    )
    overview_portfolio_ids = tuple(
        sorted({int(item.portfolio_id) for item in overview_grants})
    )
    overview_grants_by_portfolio: dict[int, list[UserPortfolioOverviewGrant]] = {}
    for grant in overview_grants:
        overview_grants_by_portfolio.setdefault(int(grant.portfolio_id), []).append(grant)
    overview_groups: dict[int, dict] = {}
    if overview_portfolio_ids:
        active_portfolios = {
            int(item.id): item
            for item in db.query(Portfolio)
            .filter(
                Portfolio.id.in_(overview_portfolio_ids),
                Portfolio.is_active.is_(True),
            )
            .all()
        }
        for portfolio_id, portfolio in active_portfolios.items():
            overview_groups[portfolio_id] = {
                "portfolio": portfolio,
                "building_ids": set(),
                "building_codes": set(),
            }
        membership_rows = (
            db.query(PortfolioBuilding, Building)
            .join(Building, Building.id == PortfolioBuilding.building_id)
            .filter(
                PortfolioBuilding.portfolio_id.in_(tuple(active_portfolios) or (-1,)),
                *_active_on(PortfolioBuilding, effective_date),
                Building.is_active.is_(True),
            )
            .all()
        )
        for membership, building in membership_rows:
            group = overview_groups.get(int(membership.portfolio_id))
            if group is None:
                continue
            grants_for_portfolio = overview_grants_by_portfolio.get(
                int(membership.portfolio_id),
                [],
            )
            if not any(
                bool(grant.include_future_buildings)
                or _membership_existed_when_granted(membership, grant)
                for grant in grants_for_portfolio
            ):
                continue
            group["building_ids"].add(int(building.id))
            group["building_codes"].add(str(building.code).strip())
    portfolio_overviews = tuple(
        sorted(
            (
                PortfolioOverviewAccess(
                    id=portfolio_id,
                    code=str(group["portfolio"].code).strip(),
                    name=str(group["portfolio"].name),
                    building_ids=tuple(sorted(group["building_ids"])),
                    building_codes=tuple(
                        sorted(group["building_codes"], key=str.casefold)
                    ),
                )
                for portfolio_id, group in overview_groups.items()
            ),
            key=lambda item: (item.code.casefold(), item.id),
        )
    )
    default = next((item for item in buildings if item.code.casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold()), None)
    if default is None and buildings:
        default = buildings[0]
    capabilities = _effective_capabilities(db, int(user.id), effective_date)
    version = _authorization_version(
        user=user,
        grants=grants,
        overview_grants=overview_grants,
        buildings=buildings,
        portfolios=portfolios,
        portfolio_overviews=portfolio_overviews,
        capabilities=capabilities,
    )
    return ResolvedScope(
        user_id=int(user.id),
        role=str(user.role or ""),
        buildings=buildings,
        portfolios=portfolios,
        portfolio_overviews=portfolio_overviews,
        default_building_code=default.code if default else None,
        effective_capabilities=capabilities,
        authorization_revision=int(user.authorization_revision or 0),
        authorization_version=version,
    )


def assert_object_building(
    scope: ResolvedScope,
    building_id: int | None,
    *,
    access_level: str = "view",
) -> BuildingAccess:
    """Fail closed for unassigned or unauthorized objects; never infer DEMO_A from NULL."""
    if building_id is None:
        raise HTTPException(409, "对象尚未明确楼宇归属")
    item = next((building for building in scope.buildings if building.id == int(building_id)), None)
    if item is None:
        raise HTTPException(404, "对象不存在或无权访问")
    if access_level == "edit" and not item.can_edit:
        raise HTTPException(403, "当前楼宇仅有查看权限")
    if access_level != "view" and access_level != "edit":
        raise ValueError("access_level must be 'view' or 'edit'")
    return item


def scope_summary(scope: ResolvedScope) -> dict:
    default = scope.default_building
    default_portfolio = next(
        (
            item
            for item in scope.portfolios
            if default is not None and default.code in item.building_codes
        ),
        scope.portfolios[0] if scope.portfolios else None,
    )
    default_overview = scope.portfolio_overviews[0] if scope.portfolio_overviews else None
    return {
        "items": [item.as_dict() for item in scope.buildings],
        "default": default.as_dict() if default else None,
        "portfolio": {
            "items": [item.as_dict() for item in scope.portfolios],
            "default": default_portfolio.as_dict() if default_portfolio else None,
        },
        "portfolio_overview": {
            "items": [item.as_dict() for item in scope.portfolio_overviews],
            "default": default_overview.as_dict() if default_overview else None,
        },
        "effective_capabilities": list(scope.effective_capabilities),
        "authorization_revision": scope.authorization_revision,
        "authorization_version": scope.authorization_version,
    }
