from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Portfolio
from app.ops_v1 import OPS_V1_DEFINITIONS
from app.services.property_scope import SelectedPropertyScope
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


_COVERAGE_KEYS = (
    "authorized_buildings",
    "presentation_enabled_buildings",
    "contribution_enabled_buildings",
    "eligible_buildings",
    "included_buildings",
    "completeness_status",
    "freshness_status",
    "validation_status",
    "publication_status",
)
_METRIC_DISPLAY = {
    str(item["code"]): {"name": str(item["name"]), "unit": str(item["unit"])}
    for item in OPS_V1_DEFINITIONS
}


def _int_value(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metric_projection(raw: dict | None, *, code_hint: str = "") -> dict:
    source = raw if isinstance(raw, dict) else {}
    coverage = source.get("coverage") if isinstance(source.get("coverage"), dict) else {}
    code = str(source.get("code") or code_hint or "")
    display = _METRIC_DISPLAY.get(code, {})
    source_name = str(source.get("name") or "")
    return {
        "code": code,
        "name": str(display.get("name") or source_name or code)
        if not source_name or source_name == code
        else source_name,
        "value": source.get("value"),
        "unit": str(source.get("unit") or display.get("unit") or ""),
        "as_of_date": str(source.get("as_of_date") or ""),
        "unavailable_reason": source.get("unavailable_reason"),
        "coverage": {key: coverage.get(key) for key in _COVERAGE_KEYS},
    }


def _metric_map(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(code): _metric_projection(metric, code_hint=str(code))
        for code, metric in raw.items()
        if isinstance(metric, dict)
    }


def _gate_projection(raw: object, *, contribution: dict) -> dict:
    gate = raw if isinstance(raw, dict) else {}
    return {
        "state": str(gate.get("state") or contribution.get("gate_state") or "unknown"),
        "presentation_enabled": bool(
            gate.get("presentation_enabled", contribution.get("presentation_enabled", False))
        ),
        "contribution_enabled": bool(
            gate.get("contribution_enabled", contribution.get("contribution_enabled", False))
        ),
    }


def _operations_profile_projection(raw: object) -> dict | None:
    source = raw if isinstance(raw, dict) else {}
    if not source:
        return None
    brand = source.get("brand") if isinstance(source.get("brand"), dict) else {}
    structure = (
        source.get("space_structure")
        if isinstance(source.get("space_structure"), dict)
        else {}
    )
    zones = structure.get("zones") if isinstance(structure.get("zones"), list) else []
    groups = (
        source.get("dashboard_groups")
        if isinstance(source.get("dashboard_groups"), list)
        else []
    )
    return {
        "profile_version": str(source.get("profile_version") or ""),
        "building_code": str(source.get("building_code") or ""),
        "building_name": str(source.get("building_name") or ""),
        "brand": {
            "short_name": str(brand.get("short_name") or ""),
            "operations_title": str(brand.get("operations_title") or ""),
            "positioning": str(brand.get("positioning") or ""),
            "operating_focus": [str(item) for item in (brand.get("operating_focus") or [])],
            "logo_url": brand.get("logo_url"),
        },
        "space_structure": {
            "type": str(structure.get("type") or "unknown"),
            "zones": [
                {
                    "code": str(item.get("code") or ""),
                    "name": str(item.get("name") or ""),
                    "kind": str(item.get("kind") or ""),
                }
                for item in zones
                if isinstance(item, dict)
            ],
            "detail_unavailable_reason": structure.get("detail_unavailable_reason"),
        },
        "data_granularity": [str(item) for item in (source.get("data_granularity") or [])],
        "dashboard_groups": [
            {
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "zone_codes": [str(code) for code in (item.get("zone_codes") or [])],
                "purpose": str(item.get("purpose") or ""),
                "available": bool(item.get("available", False)),
                "unavailable_reason": item.get("unavailable_reason"),
            }
            for item in groups
            if isinstance(item, dict)
        ],
    }


def _contribution_projection(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    building = source.get("building") if isinstance(source.get("building"), dict) else {}
    projected_building = {
        "code": str(building.get("code") or ""),
        "name": str(building.get("name") or building.get("code") or ""),
    }
    operations_profile = _operations_profile_projection(building.get("operations_profile"))
    if operations_profile is not None:
        projected_building["operations_profile"] = operations_profile
    return {
        "building": {
            **projected_building,
        },
        "publication_status": str(source.get("publication_status") or "missing"),
        "published_at": source.get("published_at"),
        "reason": source.get("reason"),
        "presentation_enabled": bool(source.get("presentation_enabled", False)),
        "contribution_enabled": bool(source.get("contribution_enabled", False)),
        "gate_state": str(source.get("gate_state") or "unknown"),
        "operating_gate": _gate_projection(source.get("operating_gate"), contribution=source),
        "data_classification": "simulated",
        "metrics": _metric_map(source.get("metrics")),
    }


def _rollout_item_projection(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    return {
        "building_code": str(source.get("building_code") or ""),
        "building_name": str(source.get("building_name") or source.get("building_code") or ""),
        "state": str(source.get("state") or "unknown"),
        "presentation_enabled": bool(source.get("presentation_enabled", False)),
        "contribution_enabled": bool(source.get("contribution_enabled", False)),
    }


def _trend_projection(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        month = str(item.get("month") or item.get("date") or "")[:7]
        if not month:
            continue
        result.append(
            {
                "month": month,
                "receivable": item.get("receivable"),
                "received": item.get("received"),
                "received_rate": item.get("received_rate"),
            }
        )
    return result


def build_portfolio_overview(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    as_of_date: date | None = None,
) -> dict:
    """Return the leadership-safe projection; never return source or business detail objects."""

    raw = build_unified_operating_snapshot(
        db,
        scope=scope,
        as_of_date=as_of_date,
        include_trend=True,
        queue_limit=0,
        risk_limit=0,
    )
    raw_scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    if scope.scope_type != "portfolio" or scope.portfolio_id is None:
        raise ValueError("portfolio overview requires a portfolio scope")
    target = db.get(Portfolio, scope.portfolio_id)
    display_name = str(
        getattr(target, "name", None)
        or raw_scope.get("display_name")
        or scope.scope_code
    )

    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    timestamps = raw.get("timestamps") if isinstance(raw.get("timestamps"), dict) else {}
    period = raw.get("period") if isinstance(raw.get("period"), dict) else {}
    rollout = raw.get("rollout") if isinstance(raw.get("rollout"), dict) else {}
    contributions = [
        _contribution_projection(item)
        for item in (raw.get("contributions") or [])
    ]
    presented_codes = {
        item["building"]["code"]
        for item in contributions
        if item["presentation_enabled"]
    }
    presented_contributions = [
        item for item in contributions if item["building"]["code"] in presented_codes
    ]
    raw_presentation = (
        raw.get("presentation") if isinstance(raw.get("presentation"), dict) else {}
    )
    gate_policy_version = str(
        raw.get("gate_policy_version")
        or rollout.get("policy_version")
        or "building-gates-v1-unknown"
    )
    authorized_building_count = _int_value(coverage.get("authorized_buildings"))
    return {
        "contract_version": "portfolio-overview-v1",
        "definition_version": str(raw.get("definition_version") or "OPS_V1"),
        "definition_status": str(raw.get("definition_status") or "published"),
        "data_classification": "simulated",
        "gate_policy_version": gate_policy_version,
        "as_of_date": str(raw.get("as_of_date") or ""),
        "period": {
            "month": str(period.get("month") or ""),
            "month_start": str(period.get("month_start") or ""),
            "month_end": str(period.get("month_end") or ""),
        },
        "scope": {
            "scope_type": scope.scope_type,
            "scope_code": scope.scope_code,
            "display_name": display_name,
            "building_codes": list(scope.building_codes),
        },
        "coverage": {
            # Never reveal memberships outside this frozen overview grant.
            "portfolio_buildings": authorized_building_count,
            "authorized_buildings": authorized_building_count,
            "presentation_enabled_buildings": _int_value(
                coverage.get("presentation_enabled_buildings")
            ),
            "contribution_enabled_buildings": _int_value(
                coverage.get("contribution_enabled_buildings")
            ),
            "published_same_day_buildings": _int_value(
                coverage.get("published_same_day_buildings")
            ),
            "live_adapter_buildings": _int_value(coverage.get("live_adapter_buildings")),
            "same_day_snapshot_buildings": _int_value(
                coverage.get("same_day_snapshot_buildings")
            ),
            "included_same_day_buildings": _int_value(
                coverage.get("included_same_day_buildings")
            ),
        },
        "timestamps": {
            "source_observed_at": timestamps.get("source_observed_at"),
            "source_updated_at": timestamps.get("source_updated_at"),
            "published_at": timestamps.get("published_at"),
        },
        "metrics": _metric_map(raw.get("metrics")),
        # Hidden/aggregate-only buildings remain visible only as rollout state;
        # their per-building metrics are never part of the leadership payload.
        "contributions": presented_contributions,
        "rollout": {
            "policy_version": gate_policy_version,
            "evaluated_at": str(rollout.get("evaluated_at") or ""),
            "authorized_buildings": authorized_building_count,
            "presentation_enabled_buildings": _int_value(
                rollout.get(
                    "presentation_enabled_buildings",
                    coverage.get("presentation_enabled_buildings"),
                )
            ),
            "contribution_enabled_buildings": _int_value(
                rollout.get(
                    "contribution_enabled_buildings",
                    coverage.get("contribution_enabled_buildings"),
                )
            ),
            "items": [
                _rollout_item_projection(item)
                for item in (rollout.get("items") or [])
            ],
        },
        "presentation": {
            "data_classification": "simulated",
            "gate_policy_version": gate_policy_version,
            "trend": _trend_projection(raw_presentation.get("trend")),
            "contributions": presented_contributions,
        },
    }
