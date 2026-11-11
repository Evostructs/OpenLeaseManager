from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.time import today_in_timezone
from app.models import Building
from app.services.building_operations_profile import (
    BuildingOperationsProfile,
    DataAdapterType,
    resolve_building_operations_profile,
)
from app.services.building_operating_gate import resolve_building_operating_gates
from app.services.operating_snapshot import build_operating_snapshot
from app.services.property_scope import SelectedPropertyScope
from app.services.published_operating_snapshot import build_published_operating_snapshot


@dataclass(frozen=True)
class _SingleBuildingScope:
    building_ids: tuple[int, ...]


def _state_value(decision) -> str:
    state = getattr(decision, "state", "unknown")
    return str(getattr(state, "value", state))


def _decision_payload(decision, *, building: Building) -> dict:
    payload = dict(decision.as_dict())
    payload.setdefault("building_id", int(building.id))
    payload.setdefault("building_code", str(building.code))
    payload.setdefault("building_name", str(building.name))
    return payload


def _decorate_live_snapshot(*, snapshot: dict, gate_resolution, building: Building) -> dict:
    decision = gate_resolution.by_building_id.get(int(building.id))
    if decision is None:
        raise RuntimeError("楼宇明细适配器开闸决策缺失")
    presentation_enabled = bool(decision.presentation_enabled)
    contribution_enabled = bool(decision.contribution_enabled)
    rollout_decision = _decision_payload(decision, building=building)
    operating_gate = rollout_decision
    adapter_reason = f"当天{building.name}明细按 OPS_V1 实时适配"
    contribution = {
        "building": {"id": building.id, "code": building.code, "name": building.name},
        "snapshot_id": None,
        "revision": None,
        "publication_status": "live_adapter",
        "published_at": None,
        "reason": adapter_reason,
        "presentation_enabled": presentation_enabled,
        "contribution_enabled": contribution_enabled,
        "gate_state": _state_value(decision),
        "operating_gate": operating_gate,
        "gate": operating_gate,
        "data_classification": "simulated",
        "metrics": snapshot.get("metrics") or {},
    }
    gate_coverage = {
        "authorized_buildings": 1,
        "presentation_enabled_buildings": int(presentation_enabled),
        "contribution_enabled_buildings": int(contribution_enabled),
    }
    snapshot.setdefault("coverage", {}).update(gate_coverage)
    for metric in (snapshot.get("metrics") or {}).values():
        if isinstance(metric, dict):
            metric.setdefault("coverage", {}).update(gate_coverage)
    snapshot["data_classification"] = "simulated"
    snapshot["gate_policy_version"] = gate_resolution.policy_version
    snapshot["contributions"] = [contribution]
    snapshot["rollout"] = {
        "policy_version": gate_resolution.policy_version,
        "evaluated_at": gate_resolution.evaluated_at.isoformat(),
        "data_classification": "simulated",
        "authorized_buildings": 1,
        "presentation_enabled_buildings": int(presentation_enabled),
        "contribution_enabled_buildings": int(contribution_enabled),
        "items": [rollout_decision],
        "decisions": [rollout_decision],
    }
    for source in snapshot.get("sources") or []:
        source["data_classification"] = "simulated"
    presentation = snapshot.setdefault("presentation", {})
    presentation["contributions"] = [contribution] if presentation_enabled else []
    presentation["data_classification"] = "simulated"
    presentation["gate_policy_version"] = gate_resolution.policy_version
    detail_source_state = snapshot.get("detail_source_state") or {}
    detail_facts_available = bool(
        detail_source_state.get("dashboard_facts_available")
    )
    detail_projection = {
        "source": "live_detail",
        "detail_lists_available": True,
        "detail_lists_unavailable_reason": None,
        "detail_facts_available": detail_facts_available,
        "detail_facts_unavailable_reason": (
            None
            if detail_facts_available
            else detail_source_state.get("dashboard_facts_unavailable_reason")
            or "当前楼宇明细源尚无可核验的经营事实"
        ),
    }
    snapshot.setdefault("legacy_contract_stats", {})["projection"] = detail_projection
    snapshot.setdefault("legacy_operations", {})["projection"] = detail_projection
    snapshot["adapters"] = [
        {
            "building": {"id": building.id, "code": building.code, "name": building.name},
            "adapter_type": DataAdapterType.LIVE_DETAIL.value,
            "included": True,
            "reason": adapter_reason,
            "operating_gate": operating_gate,
            "data_classification": "simulated",
        }
    ]
    return snapshot


def _selected_buildings(db: Session, scope: SelectedPropertyScope) -> tuple[Building, ...]:
    building_ids = tuple(dict.fromkeys(int(item) for item in scope.building_ids))
    if not building_ids:
        raise HTTPException(404, "当前范围没有可访问楼宇")
    buildings = (
        db.query(Building)
        .filter(
            Building.id.in_(building_ids),
            Building.is_active.is_(True),
        )
        .all()
    )
    building_by_id = {int(item.id): item for item in buildings}
    if set(building_by_id) != set(building_ids):
        raise HTTPException(404, "楼宇不存在或无权访问")
    return tuple(building_by_id[item] for item in building_ids)


def _add_adapter_metadata(
    result: dict,
    *,
    buildings: tuple[Building, ...],
    profiles: dict[int, BuildingOperationsProfile],
    effective_date: date,
    request_today: date,
) -> None:
    adapters = result.setdefault("adapters", [])
    existing = {
        (
            int((item.get("building") or {}).get("id") or 0),
            str(item.get("adapter_type") or ""),
        )
        for item in adapters
    }
    contribution_by_id = {
        int((item.get("building") or {}).get("id") or 0): item
        for item in result.get("contributions") or []
    }
    scope_type = str((result.get("scope") or {}).get("scope_type") or "")
    for building in buildings:
        profile = profiles[int(building.id)]
        contribution = contribution_by_id.get(int(building.id), {})
        publication_status = str(contribution.get("publication_status") or "missing")
        published_available = publication_status == "published"

        if profile.data_adapter is DataAdapterType.PUBLISHED_SNAPSHOT:
            key = (int(building.id), DataAdapterType.PUBLISHED_SNAPSHOT.value)
            if key not in existing:
                aggregation_eligible = (
                    scope_type != "portfolio"
                    or bool(contribution.get("contribution_enabled"))
                )
                included = published_available and aggregation_eligible
                adapters.append(
                    {
                        "building": {
                            "id": building.id,
                            "code": building.code,
                            "name": building.name,
                        },
                        "adapter_type": DataAdapterType.PUBLISHED_SNAPSHOT.value,
                        "included": included,
                        "reason": (
                            f"读取 {effective_date.isoformat()} 已发布经营快照"
                            if included
                            else contribution.get("reason")
                            if published_available
                            else contribution.get("reason") or "该统计日没有当前已发布快照"
                        ),
                        "data_classification": "simulated",
                    }
                )
            continue

        if profile.data_adapter is DataAdapterType.LIVE_DETAIL and effective_date != request_today:
            live_key = (int(building.id), DataAdapterType.LIVE_DETAIL.value)
            live_reason = (
                f"{profile.brand.short_name} 明细适配器仅支持当天；本次按指定统计日只读取已发布快照"
            )
            if live_key not in existing:
                adapters.append(
                    {
                        "building": {
                            "id": building.id,
                            "code": building.code,
                            "name": building.name,
                        },
                        "adapter_type": DataAdapterType.LIVE_DETAIL.value,
                        "included": False,
                        "reason": live_reason,
                    }
                )
            published_key = (int(building.id), DataAdapterType.PUBLISHED_SNAPSHOT.value)
            if published_available and published_key not in existing:
                aggregation_eligible = (
                    scope_type != "portfolio"
                    or bool(contribution.get("contribution_enabled"))
                )
                adapters.append(
                    {
                        "building": {
                            "id": building.id,
                            "code": building.code,
                            "name": building.name,
                        },
                        "adapter_type": DataAdapterType.PUBLISHED_SNAPSHOT.value,
                        "included": aggregation_eligible,
                        "reason": (
                            f"历史统计日读取 {effective_date.isoformat()} 已发布经营快照"
                            if aggregation_eligible
                            else contribution.get("reason")
                            or "楼宇开闸策略未允许参与板块合计"
                        ),
                        "data_classification": "simulated",
                    }
                )
            if not published_available and contribution:
                contribution["reason"] = live_reason
            continue

        if profile.data_adapter is DataAdapterType.UNAVAILABLE:
            key = (int(building.id), DataAdapterType.UNAVAILABLE.value)
            if key not in existing:
                adapters.append(
                    {
                        "building": {
                            "id": building.id,
                            "code": building.code,
                            "name": building.name,
                        },
                        "adapter_type": DataAdapterType.UNAVAILABLE.value,
                        "included": False,
                        "reason": profile.adapter_unavailable_reason,
                        "data_classification": "simulated",
                    }
                )


def _add_presentation_profile_metadata(
    result: dict,
    *,
    buildings: tuple[Building, ...],
    profiles: dict[int, BuildingOperationsProfile],
) -> None:
    profile_by_id = {
        int(building.id): profiles[int(building.id)].presentation_dict()
        for building in buildings
    }
    profile_by_code = {
        str(building.code).strip().casefold(): profile_by_id[int(building.id)]
        for building in buildings
    }

    def decorate_contributions(rows: object) -> None:
        if not isinstance(rows, list):
            return
        for item in rows:
            if not isinstance(item, dict):
                continue
            building = item.get("building")
            if not isinstance(building, dict):
                continue
            profile = None
            building_id = building.get("id")
            if building_id is not None:
                try:
                    profile = profile_by_id.get(int(building_id))
                except (TypeError, ValueError):
                    profile = None
            if profile is None:
                profile = profile_by_code.get(str(building.get("code") or "").strip().casefold())
            if profile is not None:
                building["operations_profile"] = profile

    decorate_contributions(result.get("contributions"))
    presentation = result.get("presentation")
    if isinstance(presentation, dict):
        decorate_contributions(presentation.get("contributions"))

    scope = result.get("scope")
    if not isinstance(scope, dict) or str(scope.get("scope_type") or "") != "building":
        return
    building = scope.get("building") if isinstance(scope.get("building"), dict) else {}
    scope_code = str(
        scope.get("scope_code")
        or building.get("code")
        or (scope.get("building_codes") or [""])[0]
    ).strip().casefold()
    profile = profile_by_code.get(scope_code)
    if profile is None:
        return
    scope["operations_profile"] = profile
    if building:
        building["operations_profile"] = profile


def build_unified_operating_snapshot(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    as_of_date: date | None = None,
    include_trend: bool = True,
    queue_limit: int = 0,
    risk_limit: int = 8,
) -> dict:
    """Serve one contract by selecting a registered data adapter per building."""

    effective_date = as_of_date or today_in_timezone()
    request_today = today_in_timezone()
    buildings = _selected_buildings(db, scope)
    profiles = {
        int(building.id): resolve_building_operations_profile(building)
        for building in buildings
    }
    live_buildings = tuple(
        building
        for building in buildings
        if profiles[int(building.id)].data_adapter is DataAdapterType.LIVE_DETAIL
    )

    if (
        len(live_buildings) == 1
        and effective_date == request_today
        and scope.scope_type == "building"
        and tuple(scope.building_ids) == (live_buildings[0].id,)
    ):
        building = live_buildings[0]
        gate_resolution = resolve_building_operating_gates(
            db,
            (building.id,),
        )
        snapshot = build_operating_snapshot(
            db,
            scope=_SingleBuildingScope(building_ids=(building.id,)),
            as_of_date=effective_date,
            include_trend=include_trend,
            queue_limit=queue_limit,
            risk_limit=risk_limit,
        )
        snapshot = _decorate_live_snapshot(
            snapshot=snapshot,
            gate_resolution=gate_resolution,
            building=building,
        )
        _add_presentation_profile_metadata(
            snapshot,
            buildings=buildings,
            profiles=profiles,
        )
        return snapshot

    detail_snapshots: dict[int, dict] = {}
    if effective_date == request_today:
        for building in live_buildings:
            detail_snapshots[int(building.id)] = build_operating_snapshot(
                db,
                scope=_SingleBuildingScope(building_ids=(building.id,)),
                as_of_date=effective_date,
                include_trend=include_trend,
                queue_limit=queue_limit,
                risk_limit=risk_limit,
                _contract_summary_only=scope.scope_type == "portfolio",
            )

    adapter_unavailable_reasons = {
        building_id: profile.adapter_unavailable_reason or "楼宇数据 Adapter 不可用"
        for building_id, profile in profiles.items()
        if profile.data_adapter is DataAdapterType.UNAVAILABLE
    }

    result = build_published_operating_snapshot(
        db,
        scope=scope,
        as_of_date=effective_date,
        detail_snapshots=detail_snapshots,
        adapter_unavailable_reasons=adapter_unavailable_reasons,
    )
    effective_ids = {
        int(item) for item in (result.get("scope") or {}).get("building_ids") or []
    }
    effective_buildings = tuple(
        building for building in buildings if int(building.id) in effective_ids
    )
    _add_adapter_metadata(
        result,
        buildings=effective_buildings,
        profiles=profiles,
        effective_date=effective_date,
        request_today=request_today,
    )
    _add_presentation_profile_metadata(
        result,
        buildings=effective_buildings,
        profiles=profiles,
    )
    return result
