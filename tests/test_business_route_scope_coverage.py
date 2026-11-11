from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app


BUSINESS_PREFIXES = (
    "/api/ai",
    "/api/attachments",
    "/api/contracts",
    "/api/customers",
    "/api/dashboard",
    "/api/financial-planning",
    "/api/finance-operations",
    "/api/operations",
    "/api/rent-analytics",
    "/api/reports",
    "/api/risks",
    "/api/rooms",
)
VIEW_SCOPE_DEPENDENCY = "get_selected_view_scope"
EDIT_SCOPE_DEPENDENCY = "get_selected_edit_scope"
OVERVIEW_SCOPE_DEPENDENCY = "get_selected_portfolio_overview_scope"
SCOPE_DEPENDENCIES = {
    VIEW_SCOPE_DEPENDENCY,
    EDIT_SCOPE_DEPENDENCY,
    OVERVIEW_SCOPE_DEPENDENCY,
}
PORTFOLIO_OVERVIEW_ROUTE = "/api/operations/portfolio-overview"


def _api_routes(routes):
    """Support both ordinary FastAPI routes and its lazy included-router wrapper."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router.routes)


def _dependency_names(dependant) -> set[str]:
    names: set[str] = set()
    if dependant.call is not None:
        names.add(getattr(dependant.call, "__name__", repr(dependant.call)))
    for child in dependant.dependencies:
        names.update(_dependency_names(child))
    return names


def test_every_business_route_uses_exactly_one_mutually_exclusive_scope_dependency() -> None:
    failures = []
    covered = 0
    for route in _api_routes(app.routes):
        if not route.path.startswith(BUSINESS_PREFIXES):
            continue
        covered += 1
        selected = _dependency_names(route.dependant) & SCOPE_DEPENDENCIES
        if len(selected) != 1:
            failures.append(
                {
                    "methods": sorted(route.methods),
                    "path": route.path,
                    "selected_scope_dependencies": sorted(selected),
                }
            )
    assert covered >= 100, "业务路由数量异常，范围覆盖检查可能没有扫描到主路由"
    assert failures == []


def test_portfolio_overview_scope_is_exclusive_to_the_overview_route() -> None:
    failures = []
    overview_routes = []
    for route in _api_routes(app.routes):
        selected = _dependency_names(route.dependant) & SCOPE_DEPENDENCIES
        if route.path == PORTFOLIO_OVERVIEW_ROUTE:
            overview_routes.append(route)
            if selected != {OVERVIEW_SCOPE_DEPENDENCY}:
                failures.append(
                    {
                        "methods": sorted(route.methods),
                        "path": route.path,
                        "selected_scope_dependencies": sorted(selected),
                    }
                )
        elif OVERVIEW_SCOPE_DEPENDENCY in selected:
            failures.append(
                {
                    "methods": sorted(route.methods),
                    "path": route.path,
                    "selected_scope_dependencies": sorted(selected),
                }
            )

    assert len(overview_routes) == 1, "板块概况路由应且只能注册一次"
    assert overview_routes[0].methods == {"GET"}
    assert failures == []


def test_audit_and_authorization_control_planes_have_explicit_non_overlapping_guards() -> None:
    failures = []
    for route in _api_routes(app.routes):
        names = _dependency_names(route.dependant)
        selected = names & SCOPE_DEPENDENCIES
        if route.path in {"/api/audit/logs", "/api/audit/export"}:
            if selected != {"get_selected_view_scope"} or "require_admin" not in names:
                failures.append({"path": route.path, "dependencies": sorted(names)})
        elif route.path.startswith("/api/audit/platform/"):
            if selected or "require_platform_admin" not in names:
                failures.append({"path": route.path, "dependencies": sorted(names)})
        elif route.path.startswith("/api/auth/authorizations/"):
            if selected or "require_platform_admin" not in names:
                failures.append({"path": route.path, "dependencies": sorted(names)})
    assert failures == []
