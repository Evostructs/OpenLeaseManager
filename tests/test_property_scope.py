from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import or_

from app.core.access import get_current_user
from app.database import get_db
from app.models import Building, Portfolio, PortfolioBuilding, User, UserCapability, UserScopeGrant
from app.routers.auth import me
from app.routers.buildings import buildings as buildings_endpoint
from app.services.property_scope import (
    SelectedPropertyScope,
    assert_object_building,
    assert_selected_object_building,
    get_selected_edit_scope,
    get_selected_view_scope,
    resolve_user_scope,
    require_customer_master_manager,
    require_platform_admin,
    require_single_selected_building,
    scope_summary,
    select_property_scope,
)


AS_OF = date(2026, 8, 4)


def test_capability_dependencies_have_stable_auditable_names() -> None:
    assert require_platform_admin.__name__ == "require_platform_admin"
    assert require_platform_admin.__qualname__ == "require_platform_admin"
    assert require_customer_master_manager.__name__ == "require_customer_master_manage"
    assert require_customer_master_manager.__qualname__ == "require_customer_master_manage"


@pytest.fixture
def db():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def scope_catalog(db):
    portfolio = Portfolio(code="SCOPE_TEST_PORTFOLIO", name="范围测试板块", is_active=True)
    alpha = Building(code="SCOPE_ALPHA", name="范围测试甲楼", is_active=True)
    future = Building(code="SCOPE_FUTURE", name="范围测试未来楼", is_active=True)
    disabled = Building(code="SCOPE_DISABLED", name="范围测试停用楼", is_active=False)
    padded = Building(code=" SCOPE_PADDED ", name="范围测试空格代码楼", is_active=True)
    db.add_all([portfolio, alpha, future, disabled, padded])
    db.flush()
    db.add_all(
        [
            PortfolioBuilding(
                portfolio_id=portfolio.id,
                building_id=alpha.id,
                valid_from=date(2025, 1, 1),
            ),
            PortfolioBuilding(
                portfolio_id=portfolio.id,
                building_id=future.id,
                valid_from=date(2026, 6, 1),
            ),
            PortfolioBuilding(
                portfolio_id=portfolio.id,
                building_id=disabled.id,
                valid_from=date(2025, 1, 1),
            ),
        ]
    )
    db.commit()
    try:
        yield {
            "portfolio": portfolio,
            "alpha": alpha,
            "future": future,
            "disabled": disabled,
            "padded": padded,
        }
    finally:
        db.rollback()
        building_ids = [alpha.id, future.id, disabled.id, padded.id]
        db.query(UserScopeGrant).filter(
            or_(
                UserScopeGrant.portfolio_id == portfolio.id,
                UserScopeGrant.building_id.in_(building_ids),
            )
        ).delete(synchronize_session=False)
        db.query(PortfolioBuilding).filter(PortfolioBuilding.portfolio_id == portfolio.id).delete(
            synchronize_session=False
        )
        db.query(Building).filter(Building.id.in_(building_ids)).delete(synchronize_session=False)
        db.query(Portfolio).filter(Portfolio.id == portfolio.id).delete(synchronize_session=False)
        db.commit()


def _user(db, username: str, role: str = "editor") -> User:
    user = User(username=username, display_name=username, password_hash="unused", role=role, is_active=True)
    db.add(user)
    db.flush()
    return user


def _building(db, code: str) -> Building:
    return db.query(Building).filter(Building.code == code).one()


def _building_grant(
    db,
    user: User,
    building: Building,
    *,
    access_level: str,
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = None,
) -> UserScopeGrant:
    grant = UserScopeGrant(
        user_id=user.id,
        scope_type="building",
        building_id=building.id,
        access_level=access_level,
        include_future_buildings=False,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(grant)
    db.flush()
    return grant


def _portfolio_grant(
    db,
    user: User,
    portfolio: Portfolio,
    *,
    access_level: str,
    include_future: bool,
) -> UserScopeGrant:
    grant = UserScopeGrant(
        user_id=user.id,
        scope_type="portfolio",
        portfolio_id=portfolio.id,
        access_level=access_level,
        include_future_buildings=include_future,
        valid_from=date(2026, 1, 1),
    )
    db.add(grant)
    db.flush()
    return grant


def test_direct_grant_uses_demo_a_default_and_role_is_edit_ceiling(db, scope_catalog) -> None:
    demo_a = _building(db, "DEMO_A")
    viewer = _user(db, "scope_viewer", role="viewer")
    _building_grant(db, viewer, demo_a, access_level="edit")
    db.commit()

    scope = resolve_user_scope(db, viewer, as_of_date=AS_OF)
    assert scope.view_building_codes == ("DEMO_A",)
    assert scope.edit_building_codes == ()
    assert scope.portfolios == ()
    assert scope.default_building_code == "DEMO_A"
    assert scope.require_building_code("demo_a").id == demo_a.id
    public_building = scope.buildings[0].as_dict()
    assert public_building["can_edit"] is False
    assert public_building["access_level"] == "view"
    assert "contracts" in public_building["operations_profile"]["available_tabs"]
    with pytest.raises(HTTPException) as exc_info:
        scope.require_building_code("DEMO_A", access_level="edit")
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        assert_object_building(scope, None)
    assert exc_info.value.status_code == 409
    with pytest.raises(HTTPException) as exc_info:
        assert_object_building(scope, scope_catalog["alpha"].id)
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(scope, portfolio="DEMO_OFFICE_PORTFOLIO")
    assert exc_info.value.status_code == 404


def test_runtime_normalizes_legacy_codes_with_outer_whitespace(db, scope_catalog) -> None:
    user = _user(db, "scope_padded_code", role="viewer")
    _building_grant(db, user, scope_catalog["padded"], access_level="view")
    db.commit()

    resolved = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert resolved.view_building_codes == ("SCOPE_PADDED",)
    selected = select_property_scope(resolved, building="SCOPE_PADDED")
    assert selected.scope_code == "SCOPE_PADDED"


def test_scope_honors_grant_dates_and_inactive_buildings(db, scope_catalog) -> None:
    user = _user(db, "scope_dates")
    demo_a = _building(db, "DEMO_A")
    _building_grant(db, user, demo_a, access_level="view")
    _building_grant(
        db,
        user,
        scope_catalog["alpha"],
        access_level="view",
        valid_from=date(2025, 1, 1),
        valid_to=date(2026, 1, 1),
    )
    _building_grant(
        db,
        user,
        scope_catalog["alpha"],
        access_level="edit",
        valid_from=date(2027, 1, 1),
    )
    _building_grant(db, user, scope_catalog["disabled"], access_level="edit")
    db.commit()

    scope = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert scope.view_building_codes == ("DEMO_A",)
    assert scope.edit_building_codes == ()


def test_portfolio_scope_excludes_future_members_unless_enabled_and_merges_direct_grants(
    db,
    scope_catalog,
) -> None:
    limited = _user(db, "scope_portfolio_limited")
    _portfolio_grant(
        db,
        limited,
        scope_catalog["portfolio"],
        access_level="view",
        include_future=False,
    )
    _building_grant(db, limited, scope_catalog["future"], access_level="edit")

    expanding = _user(db, "scope_portfolio_expanding")
    _portfolio_grant(
        db,
        expanding,
        scope_catalog["portfolio"],
        access_level="edit",
        include_future=True,
    )
    db.commit()

    limited_scope = resolve_user_scope(db, limited, as_of_date=AS_OF)
    assert limited_scope.view_building_codes == ("SCOPE_ALPHA", "SCOPE_FUTURE")
    assert limited_scope.edit_building_codes == ("SCOPE_FUTURE",)
    assert "SCOPE_DISABLED" not in limited_scope.view_building_codes
    assert limited_scope.portfolios[0].building_codes == ("SCOPE_ALPHA", "SCOPE_FUTURE")

    expanding_scope = resolve_user_scope(db, expanding, as_of_date=AS_OF)
    assert expanding_scope.view_building_codes == ("SCOPE_ALPHA", "SCOPE_FUTURE")
    assert expanding_scope.edit_building_codes == ("SCOPE_ALPHA", "SCOPE_FUTURE")


def test_portfolio_grant_without_future_members_uses_same_day_creation_order(db, scope_catalog) -> None:
    granted_at = datetime(2026, 8, 4, 12, 0, 0)
    memberships = {
        int(item.building_id): item
        for item in db.query(PortfolioBuilding)
        .filter(PortfolioBuilding.portfolio_id == scope_catalog["portfolio"].id)
        .all()
    }
    existing = memberships[int(scope_catalog["alpha"].id)]
    added_later = memberships[int(scope_catalog["future"].id)]
    existing.valid_from = AS_OF
    existing.created_at = granted_at - timedelta(minutes=1)
    added_later.valid_from = AS_OF
    added_later.created_at = granted_at + timedelta(minutes=1)

    user = _user(db, "scope_same_day_boundary", role="viewer")
    db.add(
        UserScopeGrant(
            user_id=user.id,
            scope_type="portfolio",
            portfolio_id=scope_catalog["portfolio"].id,
            access_level="view",
            include_future_buildings=False,
            valid_from=AS_OF,
            granted_at=granted_at,
        )
    )
    db.commit()

    resolved = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert resolved.view_building_codes == ("SCOPE_ALPHA",)


def test_formal_catalog_preserves_existing_grant_boundaries_and_endpoint_summary(db) -> None:
    catalog_date = date(2026, 8, 5)
    portfolio = db.query(Portfolio).filter(
        Portfolio.code == "DEMO_OFFICE_PORTFOLIO"
    ).one()
    memberships = (
        db.query(PortfolioBuilding)
        .filter(PortfolioBuilding.portfolio_id == portfolio.id)
        .all()
    )
    assert len(memberships) == 5
    seeded_after = max(item.created_at for item in memberships) + timedelta(minutes=1)

    direct = _user(db, "formal_catalog_direct", role="viewer")
    _building_grant(
        db,
        direct,
        _building(db, "DEMO_A"),
        access_level="view",
        valid_from=date(2026, 8, 4),
    )

    existing_fixed = _user(db, "formal_catalog_existing_fixed", role="viewer")
    db.add(
        UserScopeGrant(
            user_id=existing_fixed.id,
            scope_type="portfolio",
            portfolio_id=portfolio.id,
            access_level="view",
            include_future_buildings=False,
            valid_from=date(2026, 8, 4),
            granted_at=datetime(2026, 8, 4, 22, 0, 0),
        )
    )

    existing_expanding = _user(db, "formal_catalog_existing_expanding", role="viewer")
    db.add(
        UserScopeGrant(
            user_id=existing_expanding.id,
            scope_type="portfolio",
            portfolio_id=portfolio.id,
            access_level="view",
            include_future_buildings=True,
            valid_from=date(2026, 8, 4),
            granted_at=datetime(2026, 8, 4, 22, 0, 0),
        )
    )

    granted_after_seed = _user(db, "formal_catalog_new_fixed", role="viewer")
    db.add(
        UserScopeGrant(
            user_id=granted_after_seed.id,
            scope_type="portfolio",
            portfolio_id=portfolio.id,
            access_level="view",
            include_future_buildings=False,
            valid_from=catalog_date,
            granted_at=seeded_after,
        )
    )
    db.commit()

    full_catalog = ("DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E")
    direct_scope = resolve_user_scope(db, direct, as_of_date=catalog_date)
    assert direct_scope.view_building_codes == ("DEMO_A",)
    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(direct_scope, building="DEMO_B")
    assert exc_info.value.status_code == 404

    fixed_scope = resolve_user_scope(db, existing_fixed, as_of_date=catalog_date)
    assert fixed_scope.view_building_codes == ("DEMO_A",)
    assert fixed_scope.portfolios[0].building_codes == ("DEMO_A",)

    expanding_scope = resolve_user_scope(db, existing_expanding, as_of_date=catalog_date)
    assert expanding_scope.view_building_codes == full_catalog
    assert expanding_scope.portfolios[0].building_codes == full_catalog

    new_fixed_scope = resolve_user_scope(db, granted_after_seed, as_of_date=catalog_date)
    assert new_fixed_scope.view_building_codes == full_catalog
    assert new_fixed_scope.portfolios[0].building_codes == full_catalog

    expected_authorization = scope_summary(expanding_scope)
    endpoint_scope = buildings_endpoint(user=existing_expanding, db=db)
    assert me(user=existing_expanding, db=db)["scope"] == endpoint_scope
    for key in ("effective_capabilities", "authorization_revision", "authorization_version"):
        assert endpoint_scope[key] == expected_authorization[key]
    assert [item["code"] for item in endpoint_scope["items"]] == [
        item["code"] for item in expected_authorization["items"]
    ]
    assert endpoint_scope["default"]["code"] == expected_authorization["default"]["code"]
    assert endpoint_scope["gate_policy_version"].startswith("building-gates-v1-")
    assert endpoint_scope["data_classification"] == "simulated"


def test_capabilities_are_effective_and_authorization_version_is_stable_but_changes(db) -> None:
    user = _user(db, "scope_version", role="admin")
    demo_a = _building(db, "DEMO_A")
    grant = _building_grant(db, user, demo_a, access_level="view")
    db.commit()

    first = resolve_user_scope(db, user, as_of_date=AS_OF)
    second = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert first.authorization_version == second.authorization_version
    assert first.effective_capabilities == ()

    db.add(
        UserCapability(
            user_id=user.id,
            capability="platform_admin",
            valid_from=date(2025, 1, 1),
            valid_to=date(2025, 12, 31),
        )
    )
    db.commit()
    expired_only = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert expired_only.authorization_version == first.authorization_version

    db.add(
        UserCapability(
            user_id=user.id,
            capability="customer_master_manage",
            valid_from=date(2026, 1, 1),
        )
    )
    db.commit()
    with_capability = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert with_capability.effective_capabilities == ("customer_master_manage",)
    assert with_capability.has_capability("customer_master_manage")
    assert with_capability.authorization_version != first.authorization_version

    grant.access_level = "edit"
    db.commit()
    with_edit = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert with_edit.edit_building_codes == ("DEMO_A",)
    assert with_edit.authorization_version != with_capability.authorization_version


def test_buildings_payload_and_auth_me_share_the_same_additive_scope_summary(db) -> None:
    user = _user(db, "scope_endpoint")
    demo_a = _building(db, "DEMO_A")
    _building_grant(db, user, demo_a, access_level="edit")
    db.add(
        UserCapability(
            user_id=user.id,
            capability="snapshot_review",
            valid_from=date(2026, 1, 1),
        )
    )
    db.commit()

    expected_authorization = scope_summary(resolve_user_scope(db, user, as_of_date=AS_OF))
    endpoint_payload = buildings_endpoint(user=user, db=db)
    me_payload = me(user=user, db=db)

    for key in ("effective_capabilities", "authorization_revision", "authorization_version"):
        assert endpoint_payload[key] == expected_authorization[key]
    assert [item["code"] for item in endpoint_payload["items"]] == [
        item["code"] for item in expected_authorization["items"]
    ]
    assert me_payload == {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "scope": endpoint_payload,
    }
    assert endpoint_payload["default"]["code"] == "DEMO_A"
    assert endpoint_payload["effective_capabilities"] == ["snapshot_review"]
    assert endpoint_payload["authorization_version"].startswith("scope-v1-")
    assert endpoint_payload["gate_policy_version"].startswith("building-gates-v1-")
    assert endpoint_payload["data_classification"] == "simulated"
    assert endpoint_payload["items"][0]["operating_gate"]["state"] == "open"
    assert endpoint_payload["items"][0]["operations_profile"] == (
        expected_authorization["items"][0]["operations_profile"]
    )
    assert endpoint_payload["default"]["operations_profile"] == (
        expected_authorization["default"]["operations_profile"]
    )


def test_user_without_grants_gets_no_implicit_building(db) -> None:
    user = _user(db, "scope_empty")
    db.commit()

    scope = resolve_user_scope(db, user, as_of_date=AS_OF)
    assert scope.view_building_ids == frozenset()
    assert scope.edit_building_ids == frozenset()
    assert scope.default_building is None
    assert scope_summary(scope)["default"] is None


def test_selected_scope_defaults_to_authorized_demo_a_and_uses_canonical_code(db) -> None:
    user = _user(db, "scope_select_default")
    demo_a = _building(db, "DEMO_A")
    _building_grant(db, user, demo_a, access_level="edit")
    db.commit()

    resolved = resolve_user_scope(db, user, as_of_date=AS_OF)
    default_scope = select_property_scope(resolved)
    explicit_scope = select_property_scope(resolved, building="  demo_a  ", access_level="edit")

    assert default_scope.building_ids == (demo_a.id,)
    assert default_scope.scope_type == "building"
    assert default_scope.scope_code == "DEMO_A"
    assert explicit_scope.building_codes == ("DEMO_A",)
    assert explicit_scope.scope_code == "DEMO_A"


def test_selected_object_guard_rejects_another_authorized_but_unselected_building(
    db,
) -> None:
    user = _user(db, "scope_selected_object")
    demo_a = _building(db, "DEMO_A")
    other = db.query(Building).filter(Building.code == "SCOPE_SELECTED_OTHER").one_or_none()
    if other is None:
        other = Building(code="SCOPE_SELECTED_OTHER", name="已授权但未选择的楼", is_active=True)
        db.add(other)
        db.flush()
    _building_grant(db, user, demo_a, access_level="edit")
    _building_grant(db, user, other, access_level="edit")
    db.commit()

    selected = select_property_scope(
        resolve_user_scope(db, user, as_of_date=AS_OF),
        building="DEMO_A",
        access_level="edit",
    )
    assert require_single_selected_building(selected) == demo_a.id
    assert assert_selected_object_building(selected, demo_a.id) == demo_a.id
    with pytest.raises(HTTPException) as exc_info:
        assert_selected_object_building(selected, other.id)
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        assert_selected_object_building(selected, None)
    assert exc_info.value.status_code == 409


def test_selected_scope_rejects_ambiguous_numeric_and_unauthorized_selectors(db) -> None:
    user = _user(db, "scope_select_rejections")
    demo_a = _building(db, "DEMO_A")
    _building_grant(db, user, demo_a, access_level="view")
    db.commit()
    resolved = resolve_user_scope(db, user, as_of_date=AS_OF)

    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(resolved, building="DEMO_A", portfolio="DEMO_OFFICE_PORTFOLIO")
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(resolved, building=str(demo_a.id))
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(resolved, building="SCOPE_ALPHA")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(resolved, building="DEMO_A", access_level="edit")
    assert exc_info.value.status_code == 404


def test_portfolio_selection_contains_only_current_authorized_buildings_and_edit_is_all_or_none(
    db,
    scope_catalog,
) -> None:
    limited = _user(db, "scope_select_portfolio_limited")
    _portfolio_grant(
        db,
        limited,
        scope_catalog["portfolio"],
        access_level="view",
        include_future=False,
    )
    _building_grant(db, limited, scope_catalog["future"], access_level="edit")

    full_editor = _user(db, "scope_select_portfolio_editor")
    _portfolio_grant(
        db,
        full_editor,
        scope_catalog["portfolio"],
        access_level="edit",
        include_future=True,
    )
    db.commit()

    limited_resolved = resolve_user_scope(db, limited, as_of_date=AS_OF)
    view_scope = select_property_scope(
        limited_resolved,
        portfolio="scope_test_portfolio",
    )
    assert view_scope.scope_type == "portfolio"
    assert view_scope.scope_code == "SCOPE_TEST_PORTFOLIO"
    assert view_scope.building_ids == (scope_catalog["alpha"].id, scope_catalog["future"].id)
    assert view_scope.building_codes == ("SCOPE_ALPHA", "SCOPE_FUTURE")
    assert scope_catalog["disabled"].id not in view_scope.building_ids
    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(
            limited_resolved,
            portfolio="SCOPE_TEST_PORTFOLIO",
            access_level="edit",
        )
    assert exc_info.value.status_code == 404

    editor_resolved = resolve_user_scope(db, full_editor, as_of_date=AS_OF)
    edit_scope = select_property_scope(
        editor_resolved,
        portfolio="SCOPE_TEST_PORTFOLIO",
        access_level="edit",
    )
    assert edit_scope.building_ids == (scope_catalog["alpha"].id, scope_catalog["future"].id)


def test_selected_scope_without_any_authorized_building_is_404(db) -> None:
    user = _user(db, "scope_select_empty")
    db.commit()

    resolved = resolve_user_scope(db, user, as_of_date=AS_OF)
    with pytest.raises(HTTPException) as exc_info:
        select_property_scope(resolved)
    assert exc_info.value.status_code == 404


def test_fastapi_scope_dependencies_reject_numeric_ids_and_keep_view_edit_separate(db) -> None:
    user = _user(db, "scope_select_dependency")
    demo_a = _building(db, "DEMO_A")
    _building_grant(db, user, demo_a, access_level="view")
    db.commit()

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db

    @app.get("/view")
    def view_endpoint(scope: SelectedPropertyScope = Depends(get_selected_view_scope)) -> dict:
        return {
            "building_ids": list(scope.building_ids),
            "scope_type": scope.scope_type,
            "scope_code": scope.scope_code,
        }

    @app.get("/edit")
    def edit_endpoint(scope: SelectedPropertyScope = Depends(get_selected_edit_scope)) -> dict:
        return {"scope_code": scope.scope_code}

    with TestClient(app) as client:
        response = client.get("/view", params={"building": "demo_a"})
        assert response.status_code == 200
        assert response.json() == {
            "building_ids": [demo_a.id],
            "scope_type": "building",
            "scope_code": "DEMO_A",
        }
        assert client.get("/view", params={"building_id": demo_a.id}).status_code == 422
        assert client.get("/view?building=DEMO_A&building=DEMO_A").status_code == 422
        assert client.get("/edit", params={"building": "DEMO_A"}).status_code == 404
