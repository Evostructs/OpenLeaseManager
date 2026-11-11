from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace

from sqlalchemy.orm import Session

from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.towers import normalize_tower_for_building


@dataclass(frozen=True, slots=True)
class BuildingBackfillSummary:
    """Count-only result for a building-scoped contract-room audit."""

    total: int = 0
    unique_matches: int = 0
    no_matches: int = 0
    multiple_matches: int = 0
    building_conflicts: int = 0
    tower_conflicts: int = 0
    area_conflicts: int = 0
    zone_conflicts: int = 0
    already_consistent: int = 0
    needs_backfill: int = 0
    applied: int = 0

    @property
    def has_blocking_anomalies(self) -> bool:
        return bool(
            self.no_matches
            or self.multiple_matches
            or self.building_conflicts
            or self.tower_conflicts
            or self.area_conflicts
            or self.zone_conflicts
        )

    def as_dict(self) -> dict[str, int]:
        """Return only aggregate counts; no contract, room, or tenant details."""

        return asdict(self)


class BuildingBackfillError(RuntimeError):
    """Base error for a refused or failed building relationship backfill."""


class BuildingNotFoundError(BuildingBackfillError):
    pass


class BuildingBackfillBlockedError(BuildingBackfillError):
    def __init__(self, summary: BuildingBackfillSummary) -> None:
        self.summary = summary
        super().__init__(
            "关系回填被拒绝："
            f"无命中 {summary.no_matches}，"
            f"多命中 {summary.multiple_matches}，"
            f"楼宇冲突 {summary.building_conflicts}，"
            f"旧分区冲突 {summary.tower_conflicts}，"
            f"面积冲突 {summary.area_conflicts}，"
            f"正式分区冲突 {summary.zone_conflicts}"
        )


class BuildingBackfillSessionError(BuildingBackfillError):
    pass


class BuildingBackfillExpectedTotalError(BuildingBackfillError):
    pass


@dataclass(frozen=True, slots=True)
class _PlannedUpdate:
    contract_room: ContractRoom
    room_id: int


def _validate_arguments(
    building_id: int,
    apply: bool,
    expected_total: int | None,
) -> None:
    if isinstance(building_id, bool) or not isinstance(building_id, int) or building_id <= 0:
        raise ValueError("building_id 必须是正整数")
    if not isinstance(apply, bool):
        raise TypeError("apply 必须是布尔值；仅 apply=True 会执行回填")
    if expected_total is not None and (
        isinstance(expected_total, bool)
        or not isinstance(expected_total, int)
        or expected_total < 0
    ):
        raise ValueError("expected_total 必须是非负整数")
    if apply and expected_total is None:
        raise BuildingBackfillExpectedTotalError(
            "apply 必须显式提供 expected_total，防止选错楼宇或处理不完整范围"
        )


def _session_has_pending_changes(db: Session) -> bool:
    if db.new or db.deleted:
        return True
    return any(db.is_modified(item, include_collections=True) for item in db.dirty)


def _areas_conflict(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is not right
    return abs(float(left) - float(right)) > 0.01


def _has_room_number(value: object) -> bool:
    return bool(str(value).strip()) if value is not None else False


def _build_plan(
    db: Session,
    building_id: int,
) -> tuple[BuildingBackfillSummary, list[_PlannedUpdate]]:
    # Audits must not accidentally flush unrelated caller state. Apply mode rejects
    # such state separately before this function is called.
    with db.no_autoflush:
        building = db.query(Building).filter(Building.id == building_id).first()
        if building is None:
            raise BuildingNotFoundError("指定楼宇不存在")

        scoped_rows = (
            db.query(ContractRoom, Contract)
            .join(Contract, Contract.id == ContractRoom.contract_id)
            .filter(Contract.building_id == building_id)
            .order_by(ContractRoom.id.asc())
            .all()
        )

        candidate_rooms = (
            db.query(Room)
            .filter(Room.building_id == building_id)
            .order_by(Room.id.asc())
            .all()
        )
        candidates_by_room_no: dict[str, list[Room]] = defaultdict(list)
        for room in candidate_rooms:
            if _has_room_number(room.room_no):
                candidates_by_room_no[room.room_no].append(room)

        linked_room_ids = {
            contract_room.room_id
            for contract_room, _contract in scoped_rows
            if contract_room.room_id is not None
        }
        linked_rooms = (
            db.query(Room).filter(Room.id.in_(linked_room_ids)).all()
            if linked_room_ids
            else []
        )
        linked_rooms_by_id = {room.id: room for room in linked_rooms}
        zone_ids = {
            room.zone_id
            for room in [*candidate_rooms, *linked_rooms]
            if room.zone_id is not None
        }
        zones = db.query(Zone).filter(Zone.id.in_(zone_ids)).all() if zone_ids else []
        zones_by_id = {zone.id: zone for zone in zones}

    unique_matches = 0
    no_matches = 0
    multiple_matches = 0
    building_conflicts = 0
    tower_conflicts = 0
    area_conflicts = 0
    zone_conflicts = 0
    already_consistent = 0
    updates: list[_PlannedUpdate] = []

    for contract_room, contract in scoped_rows:
        candidates = (
            candidates_by_room_no.get(contract_room.room_no, [])
            if _has_room_number(contract_room.room_no)
            else []
        )
        if not candidates:
            no_matches += 1
        elif len(candidates) > 1:
            multiple_matches += 1
        else:
            unique_matches += 1

        has_building_conflict = contract.building_id != building_id
        if contract_room.building_id not in (None, building_id):
            has_building_conflict = True

        linked_room = None
        if contract_room.room_id is not None:
            linked_room = linked_rooms_by_id.get(contract_room.room_id)
            if (
                linked_room is None
                or linked_room.building_id != building_id
                or linked_room.room_no != contract_room.room_no
            ):
                has_building_conflict = True

        candidate = candidates[0] if len(candidates) == 1 else None
        if candidate is not None and contract_room.room_id not in (None, candidate.id):
            has_building_conflict = True

        has_tower_conflict = False
        has_area_conflict = False
        has_zone_conflict = False
        if candidate is not None:
            relation_tower = normalize_tower_for_building(contract_room.tower, building.code)
            room_tower = normalize_tower_for_building(candidate.tower, building.code)
            has_tower_conflict = bool(
                relation_tower and room_tower and relation_tower != room_tower
            )
            has_area_conflict = _areas_conflict(contract_room.area, candidate.area)
            if candidate.zone_id is not None:
                zone = zones_by_id.get(candidate.zone_id)
                has_zone_conflict = zone is None or zone.building_id != building_id

        if has_building_conflict:
            building_conflicts += 1
        if has_tower_conflict:
            tower_conflicts += 1
        if has_area_conflict:
            area_conflicts += 1
        if has_zone_conflict:
            zone_conflicts += 1
        if (
            has_building_conflict
            or has_tower_conflict
            or has_area_conflict
            or has_zone_conflict
        ):
            continue
        if candidate is None:
            continue

        if (
            contract_room.building_id == building_id
            and contract_room.room_id == candidate.id
        ):
            already_consistent += 1
        else:
            updates.append(_PlannedUpdate(contract_room=contract_room, room_id=candidate.id))

    summary = BuildingBackfillSummary(
        total=len(scoped_rows),
        unique_matches=unique_matches,
        no_matches=no_matches,
        multiple_matches=multiple_matches,
        building_conflicts=building_conflicts,
        tower_conflicts=tower_conflicts,
        area_conflicts=area_conflicts,
        zone_conflicts=zone_conflicts,
        already_consistent=already_consistent,
        needs_backfill=len(updates),
    )
    return summary, updates


def run_building_room_backfill(
    db: Session,
    building_id: int,
    *,
    apply: bool = False,
    expected_total: int | None = None,
) -> BuildingBackfillSummary:
    """Audit or stage an atomic building-scoped relationship backfill.

    The default is read-only. ``apply=True`` is accepted only on a clean caller-
    supplied session, preflights every scoped row before mutation, and flushes all
    updates inside one savepoint. Transaction ownership remains with the caller:
    this function never opens a database connection and never commits.
    """

    _validate_arguments(building_id, apply, expected_total)
    if apply and _session_has_pending_changes(db):
        raise BuildingBackfillSessionError("apply 需要没有待提交改动的独立数据库会话")

    summary, updates = _build_plan(db, building_id)
    if expected_total is not None and summary.total != expected_total:
        raise BuildingBackfillExpectedTotalError(
            f"范围总数不符：预期 {expected_total}，实际 {summary.total}"
        )
    if not apply:
        return summary
    if summary.has_blocking_anomalies:
        raise BuildingBackfillBlockedError(summary)
    if not updates:
        return summary

    with db.begin_nested():
        for update in updates:
            update.contract_room.building_id = building_id
            update.contract_room.room_id = update.room_id
        db.flush()

    return replace(summary, applied=len(updates))


def audit_building_room_links(db: Session, building_id: int) -> BuildingBackfillSummary:
    return run_building_room_backfill(db, building_id)


def apply_building_room_backfill(
    db: Session,
    building_id: int,
    *,
    expected_total: int,
) -> BuildingBackfillSummary:
    return run_building_room_backfill(
        db,
        building_id,
        apply=True,
        expected_total=expected_total,
    )
