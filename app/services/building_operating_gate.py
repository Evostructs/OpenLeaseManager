from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import app_timezone, now_in_timezone
from app.models import Building, BuildingOperatingGateEvent, User
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope


LEGACY_DEFAULT_BUILDING_CODE = "DEMO_A"
POLICY_VERSION_PREFIX = "building-gates-v1-"


def _state(*, presentation_enabled: bool, contribution_enabled: bool) -> str:
    return {
        (False, False): "closed",
        (True, False): "observe",
        (True, True): "open",
        (False, True): "aggregate_only",
    }[(bool(presentation_enabled), bool(contribution_enabled))]


@dataclass(frozen=True)
class GateDecision:
    building_id: int
    building_code: str
    event_id: int | None
    revision: int
    presentation_enabled: bool
    contribution_enabled: bool
    state: str
    reason: str
    actor_id: int | None
    origin: str
    previous_event_id: int | None
    created_at: datetime | None
    decision_source: str

    def as_dict(self) -> dict:
        return {
            "building_id": self.building_id,
            "building_code": self.building_code,
            "event_id": self.event_id,
            "revision": self.revision,
            "presentation_enabled": self.presentation_enabled,
            "contribution_enabled": self.contribution_enabled,
            "state": self.state,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "origin": self.origin,
            "previous_event_id": self.previous_event_id,
            "created_at": self.created_at.isoformat() if self.created_at is not None else None,
            "decision_source": self.decision_source,
        }


@dataclass(frozen=True)
class GateResolution:
    decisions: tuple[GateDecision, ...]
    policy_version: str
    evaluated_at: datetime

    @property
    def by_building_id(self) -> dict[int, GateDecision]:
        return {item.building_id: item for item in self.decisions}

    def as_dict(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "items": [item.as_dict() for item in self.decisions],
        }


def decorate_scope_summary_with_operating_gates(
    summary: dict,
    gate_resolution: GateResolution,
) -> dict:
    """Add rollout metadata without changing the authorization catalog itself."""

    decision_by_id = gate_resolution.by_building_id
    decision_by_code = {
        item.building_code.casefold(): item for item in gate_resolution.decisions
    }
    for item in summary.get("items") or []:
        decision = decision_by_id.get(int(item.get("id") or 0))
        if decision is not None:
            item["operating_gate"] = decision.as_dict()
    default = summary.get("default")
    if isinstance(default, dict):
        decision = decision_by_id.get(int(default.get("id") or 0))
        if decision is not None:
            default["operating_gate"] = decision.as_dict()

    portfolio = summary.get("portfolio") or {}
    portfolio_items = list(portfolio.get("items") or [])
    if isinstance(portfolio.get("default"), dict):
        portfolio_items.append(portfolio["default"])
    for item in portfolio_items:
        codes = tuple(str(code) for code in item.get("building_codes") or [])
        decisions = [
            decision_by_code[code.casefold()]
            for code in codes
            if code.casefold() in decision_by_code
        ]
        item["presentation_enabled_building_codes"] = [
            decision.building_code
            for decision in decisions
            if decision.presentation_enabled
        ]
        item["contribution_enabled_building_codes"] = [
            decision.building_code
            for decision in decisions
            if decision.contribution_enabled
        ]

    rollout = gate_resolution.as_dict()
    rollout.pop("evaluated_at", None)
    summary["data_classification"] = "simulated"
    summary["gate_policy_version"] = gate_resolution.policy_version
    summary["operating_rollout"] = {
        **rollout,
        "data_classification": "simulated",
    }
    return summary


def _normalized_building_ids(building_ids: Iterable[int]) -> tuple[int, ...]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in building_ids:
        building_id = int(raw)
        if building_id <= 0:
            raise HTTPException(404, "楼宇不存在或无权访问")
        if building_id not in seen:
            normalized.append(building_id)
            seen.add(building_id)
    return tuple(normalized)


def _default_decision(building: Building) -> GateDecision:
    is_demo_a = str(building.code or "").strip().casefold() == LEGACY_DEFAULT_BUILDING_CODE.casefold()
    return GateDecision(
        building_id=int(building.id),
        building_code=str(building.code),
        event_id=None,
        revision=0,
        presentation_enabled=is_demo_a,
        contribution_enabled=is_demo_a,
        state="open" if is_demo_a else "closed",
        reason="DEMO_A 兼容默认保持展示并参与合计" if is_demo_a else "楼宇尚无开闸决定，按安全默认关闭",
        actor_id=None,
        origin="compatibility_default" if is_demo_a else "safe_default",
        previous_event_id=None,
        created_at=None,
        decision_source="default",
    )


def _event_decision(building: Building, event: BuildingOperatingGateEvent) -> GateDecision:
    presentation_enabled = bool(event.presentation_enabled)
    contribution_enabled = bool(event.contribution_enabled)
    return GateDecision(
        building_id=int(building.id),
        building_code=str(building.code),
        event_id=int(event.id),
        revision=int(event.revision),
        presentation_enabled=presentation_enabled,
        contribution_enabled=contribution_enabled,
        state=_state(
            presentation_enabled=presentation_enabled,
            contribution_enabled=contribution_enabled,
        ),
        reason=str(event.reason),
        actor_id=int(event.actor_id) if event.actor_id is not None else None,
        origin=str(event.origin),
        previous_event_id=(
            int(event.previous_event_id) if event.previous_event_id is not None else None
        ),
        created_at=event.created_at,
        decision_source="event",
    )


def _policy_version(decisions: tuple[GateDecision, ...]) -> str:
    canonical = [
        {
            "building_id": item.building_id,
            "building_code": item.building_code,
            "event_id": item.event_id,
            "revision": item.revision,
            "presentation_enabled": item.presentation_enabled,
            "contribution_enabled": item.contribution_enabled,
            "decision_source": item.decision_source,
        }
        for item in sorted(decisions, key=lambda candidate: candidate.building_id)
    ]
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{POLICY_VERSION_PREFIX}{digest}"


def _latest_valid_chain_events(
    events: Iterable[BuildingOperatingGateEvent],
) -> dict[int, BuildingOperatingGateEvent]:
    grouped: dict[int, list[BuildingOperatingGateEvent]] = {}
    for event in events:
        grouped.setdefault(int(event.building_id), []).append(event)

    latest: dict[int, BuildingOperatingGateEvent] = {}
    for building_id, chain in grouped.items():
        chain.sort(key=lambda item: (int(item.revision), int(item.id)))
        previous: BuildingOperatingGateEvent | None = None
        for expected_revision, event in enumerate(chain, start=1):
            expected_previous_id = int(previous.id) if previous is not None else None
            actual_previous_id = (
                int(event.previous_event_id) if event.previous_event_id is not None else None
            )
            if (
                int(event.revision) != expected_revision
                or actual_previous_id != expected_previous_id
            ):
                raise HTTPException(409, "楼宇展示与合计状态历史不完整，请联系管理员")
            previous = event
        if previous is not None:
            latest[building_id] = previous
    return latest


def resolve_building_operating_gates(
    db: Session,
    building_ids: Iterable[int],
    evaluated_at: datetime | None = None,
) -> GateResolution:
    normalized_ids = _normalized_building_ids(building_ids)
    effective_time = evaluated_at or now_in_timezone()
    if effective_time.tzinfo is not None:
        effective_time = effective_time.astimezone(app_timezone()).replace(tzinfo=None)

    buildings = (
        db.query(Building)
        .filter(Building.id.in_(normalized_ids), Building.is_active.is_(True))
        .all()
        if normalized_ids
        else []
    )
    building_by_id = {int(item.id): item for item in buildings}
    if set(building_by_id) != set(normalized_ids):
        raise HTTPException(404, "楼宇不存在或无权访问")

    events = (
        db.query(BuildingOperatingGateEvent)
        .filter(
            BuildingOperatingGateEvent.building_id.in_(normalized_ids),
            BuildingOperatingGateEvent.created_at <= effective_time,
        )
        .order_by(
            BuildingOperatingGateEvent.building_id.asc(),
            BuildingOperatingGateEvent.revision.asc(),
            BuildingOperatingGateEvent.id.asc(),
        )
        .all()
        if normalized_ids
        else []
    )
    latest_by_building = _latest_valid_chain_events(events)

    decisions = tuple(
        _event_decision(building_by_id[building_id], latest_by_building[building_id])
        if building_id in latest_by_building
        else _default_decision(building_by_id[building_id])
        for building_id in normalized_ids
    )
    return GateResolution(
        decisions=decisions,
        policy_version=_policy_version(decisions),
        evaluated_at=effective_time,
    )


def record_building_operating_gate(
    db: Session,
    building_code: str,
    actor: User,
    presentation_enabled: bool,
    contribution_enabled: bool,
    reason: str,
    expected_revision: int,
    *,
    commit: bool = True,
) -> GateDecision:
    normalized_code = str(building_code or "").strip()
    if not normalized_code:
        raise HTTPException(422, "楼宇代码不能为空")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise HTTPException(422, "开闸原因不能为空")
    if not isinstance(presentation_enabled, bool) or not isinstance(contribution_enabled, bool):
        raise HTTPException(422, "展示与合计开关必须为布尔值")
    if actor.id is None:
        raise HTTPException(409, "开闸操作人尚未持久化")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
        raise HTTPException(422, "开闸修订号无效")

    buildings = (
        db.query(Building)
        .filter(
            func.lower(func.trim(Building.code)) == normalized_code.casefold(),
            Building.is_active.is_(True),
        )
        .all()
    )
    if len(buildings) != 1:
        raise HTTPException(404, "楼宇不存在或无权访问")
    building = buildings[0]
    current_decision = resolve_building_operating_gates(db, (int(building.id),)).decisions[0]
    if expected_revision != current_decision.revision:
        raise HTTPException(409, "楼宇开闸状态已变化，请刷新后重试")
    normalized_presentation = presentation_enabled
    normalized_contribution = contribution_enabled
    if (
        normalized_presentation == current_decision.presentation_enabled
        and normalized_contribution == current_decision.contribution_enabled
    ):
        raise HTTPException(409, "楼宇开闸状态没有变化")

    recorded_at = now_in_timezone()
    event = BuildingOperatingGateEvent(
        building_id=int(building.id),
        revision=current_decision.revision + 1,
        presentation_enabled=normalized_presentation,
        contribution_enabled=normalized_contribution,
        reason=normalized_reason,
        actor_id=int(actor.id),
        origin="user",
        previous_event_id=current_decision.event_id,
        created_at=recorded_at,
    )
    try:
        db.add(event)
        db.flush()
        decision = _event_decision(building, event)
        changes = json.dumps(
            {
                "building_code": decision.building_code,
                "previous_revision": current_decision.revision,
                "revision": decision.revision,
                "presentation_enabled": decision.presentation_enabled,
                "contribution_enabled": decision.contribution_enabled,
                "state": decision.state,
                "reason": decision.reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        log_action(
            db,
            None,
            "gate_change",
            changes,
            str(actor.username or "system"),
            commit=False,
            target_type="building_operating_gate",
            target_id=int(event.id),
            object_scope=ObjectScope(scope_type="building", building_id=int(building.id)),
        )
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "楼宇开闸状态正在被其他用户修改，请刷新后重试") from exc
    except Exception:
        db.rollback()
        raise
    return decision
