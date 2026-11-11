from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import Index, MetaData, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.building_backfill import (
    BuildingBackfillBlockedError,
    BuildingBackfillExpectedTotalError,
    BuildingBackfillSessionError,
    apply_building_room_backfill,
    audit_building_room_links,
    run_building_room_backfill,
)


def _pre_strict_metadata(*, allow_duplicate_room_numbers: bool = False) -> MetaData:
    """Recreate the permissive 0022 shape that the one-time backfill targets."""
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)

    metadata.tables["contracts"].c.building_id.nullable = True
    rooms = metadata.tables["rooms"]
    rooms.c.building_id.nullable = True
    rooms.c.zone_id.nullable = True
    rooms.c.room_no.nullable = True
    contract_rooms = metadata.tables["contract_rooms"]
    contract_rooms.c.building_id.nullable = True
    contract_rooms.c.room_id.nullable = True
    contract_rooms.c.room_no.nullable = True
    metadata.tables["customer_follow_ups"].c.building_id.nullable = True

    strict_constraint_names = {
        "uq_zones_building_id_id",
        "uq_contracts_building_id_id",
        "uq_rooms_building_room_no",
        "uq_rooms_building_id_id_room_no",
        "fk_rooms_building_zone",
        "ck_rooms_room_no_canonical",
        "fk_contract_rooms_building_contract",
        "fk_contract_rooms_building_room_number",
        "ck_contract_rooms_room_no_canonical",
        "fk_customer_follow_ups_building_contract",
    }
    for table in metadata.tables.values():
        for constraint in list(table.constraints):
            if constraint.name in strict_constraint_names:
                table.constraints.remove(constraint)

    for index in list(rooms.indexes):
        if "room_no" in index.columns:
            rooms.indexes.remove(index)
    if not allow_duplicate_room_numbers:
        Index("ix_rooms_room_no", rooms.c.room_no, unique=True)
    return metadata


def _backfill_session(*, allow_duplicate_room_numbers: bool = False) -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _pre_strict_metadata(
        allow_duplicate_room_numbers=allow_duplicate_room_numbers
    ).create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    return session_factory(), engine


@pytest.fixture()
def db() -> Iterator[Session]:
    session, engine = _backfill_session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def duplicate_room_db() -> Iterator[Session]:
    """Test a dirty/future schema where one building has duplicate room numbers."""
    session, engine = _backfill_session(allow_duplicate_room_numbers=True)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_building(db: Session, code: str) -> Building:
    building = Building(code=code, name=f"{code} 测试楼宇")
    db.add(building)
    db.flush()
    return building


def _add_contract(db: Session, building_id: int, contract_no: str) -> Contract:
    contract = Contract(contract_no=contract_no, building_id=building_id)
    db.add(contract)
    db.flush()
    return contract


def _add_room(
    db: Session,
    building_id: int,
    room_no: str,
    *,
    tower: str = "",
    area: float | None = None,
    zone_id: int | None = None,
) -> Room:
    room = Room(
        building_id=building_id,
        room_no=room_no,
        tower=tower,
        area=area,
        zone_id=zone_id,
    )
    db.add(room)
    db.flush()
    return room


def _add_contract_room(
    db: Session,
    contract_id: int,
    room_no: str,
    *,
    building_id: int | None = None,
    room_id: int | None = None,
    tower: str = "",
    area: float | None = None,
) -> ContractRoom:
    item = ContractRoom(
        contract_id=contract_id,
        building_id=building_id,
        room_id=room_id,
        room_no=room_no,
        tower=tower,
        area=area,
    )
    db.add(item)
    db.flush()
    return item


def test_default_audit_is_read_only_scoped_and_count_only(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    other = _add_building(db, "OTHER")
    room_a = _add_room(db, demo_a.id, "A-101")
    room_b = _add_room(db, demo_a.id, "B-201")
    _add_room(db, demo_a.id, "C-301")
    wrong_link = _add_room(db, other.id, "OTHER-01")
    other_room = _add_room(db, other.id, "OTHER-02")

    contract = _add_contract(db, demo_a.id, "DEMO_A-AUDIT")
    needs_backfill = _add_contract_room(db, contract.id, room_a.room_no)
    _add_contract_room(
        db,
        contract.id,
        room_b.room_no,
        building_id=demo_a.id,
        room_id=room_b.id,
    )
    _add_contract_room(db, contract.id, "MISSING")
    _add_contract_room(
        db,
        contract.id,
        "C-301",
        building_id=demo_a.id,
        room_id=wrong_link.id,
    )

    out_of_scope_contract = _add_contract(db, other.id, "OTHER-AUDIT")
    out_of_scope_item = _add_contract_room(
        db,
        out_of_scope_contract.id,
        other_room.room_no,
    )
    db.commit()
    needs_backfill_id = needs_backfill.id
    out_of_scope_item_id = out_of_scope_item.id

    statements: list[str] = []

    @event.listens_for(db.bind, "before_cursor_execute")
    def _capture_sql(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lstrip().lower())

    summary = audit_building_room_links(db, demo_a.id)
    event.remove(db.bind, "before_cursor_execute", _capture_sql)

    assert summary.as_dict() == {
        "total": 4,
        "unique_matches": 3,
        "no_matches": 1,
        "multiple_matches": 0,
        "building_conflicts": 1,
        "tower_conflicts": 0,
        "area_conflicts": 0,
        "zone_conflicts": 0,
        "already_consistent": 1,
        "needs_backfill": 1,
        "applied": 0,
    }
    assert statements and all(statement.startswith("select") for statement in statements)
    assert not db.new and not db.dirty and not db.deleted
    db.expire_all()
    assert db.get(ContractRoom, needs_backfill_id).room_id is None
    assert db.get(ContractRoom, needs_backfill_id).building_id is None
    assert db.get(ContractRoom, out_of_scope_item_id).room_id is None
    assert db.get(ContractRoom, out_of_scope_item_id).building_id is None


def test_apply_backfills_clean_scope_and_is_idempotent(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    other = _add_building(db, "OTHER")
    room_a = _add_room(db, demo_a.id, "A-101")
    room_b = _add_room(db, demo_a.id, "B-201")
    room_c = _add_room(db, demo_a.id, "C-301")
    other_room = _add_room(db, other.id, "OTHER-01")

    contract = _add_contract(db, demo_a.id, "DEMO_A-APPLY")
    missing_both = _add_contract_room(db, contract.id, room_a.room_no)
    missing_building = _add_contract_room(
        db,
        contract.id,
        room_b.room_no,
        room_id=room_b.id,
    )
    _add_contract_room(
        db,
        contract.id,
        room_c.room_no,
        building_id=demo_a.id,
        room_id=room_c.id,
    )
    other_contract = _add_contract(db, other.id, "OTHER-APPLY")
    out_of_scope = _add_contract_room(db, other_contract.id, other_room.room_no)
    db.commit()
    ids = (missing_both.id, missing_building.id, out_of_scope.id)

    result = run_building_room_backfill(
        db,
        demo_a.id,
        apply=True,
        expected_total=3,
    )

    assert result.total == 3
    assert result.unique_matches == 3
    assert result.already_consistent == 1
    assert result.needs_backfill == 2
    assert result.applied == 2
    assert db.get(ContractRoom, ids[0]).building_id == demo_a.id
    assert db.get(ContractRoom, ids[0]).room_id == room_a.id
    assert db.get(ContractRoom, ids[1]).building_id == demo_a.id
    assert db.get(ContractRoom, ids[1]).room_id == room_b.id
    assert db.get(ContractRoom, ids[2]).building_id is None
    assert db.get(ContractRoom, ids[2]).room_id is None
    db.commit()

    repeated = apply_building_room_backfill(db, demo_a.id, expected_total=3)
    assert repeated.total == 3
    assert repeated.already_consistent == 3
    assert repeated.needs_backfill == 0
    assert repeated.applied == 0


def test_apply_refuses_all_writes_when_any_row_has_an_anomaly(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    room = _add_room(db, demo_a.id, "A-101")
    contract = _add_contract(db, demo_a.id, "DEMO_A-BLOCKED")
    valid = _add_contract_room(db, contract.id, room.room_no)
    _add_contract_room(db, contract.id, "MISSING")
    db.commit()
    valid_id = valid.id

    with pytest.raises(BuildingBackfillBlockedError) as raised:
        apply_building_room_backfill(db, demo_a.id, expected_total=2)

    assert raised.value.summary.no_matches == 1
    assert "A-101" not in str(raised.value)
    assert "DEMO_A-BLOCKED" not in str(raised.value)
    assert not db.dirty
    db.expire_all()
    assert db.get(ContractRoom, valid_id).building_id is None
    assert db.get(ContractRoom, valid_id).room_id is None


def test_multiple_matches_are_reported_and_block_apply(
    duplicate_room_db: Session,
) -> None:
    db = duplicate_room_db
    demo_a = _add_building(db, "DEMO_A")
    _add_room(db, demo_a.id, "DUPLICATE")
    _add_room(db, demo_a.id, "DUPLICATE")
    contract = _add_contract(db, demo_a.id, "DEMO_A-DUPLICATE")
    item = _add_contract_room(db, contract.id, "DUPLICATE")
    db.commit()
    item_id = item.id

    summary = audit_building_room_links(db, demo_a.id)

    assert summary.total == 1
    assert summary.unique_matches == 0
    assert summary.no_matches == 0
    assert summary.multiple_matches == 1
    assert summary.needs_backfill == 0
    with pytest.raises(BuildingBackfillBlockedError):
        apply_building_room_backfill(db, demo_a.id, expected_total=1)
    db.expire_all()
    assert db.get(ContractRoom, item_id).building_id is None
    assert db.get(ContractRoom, item_id).room_id is None


def test_apply_uses_savepoint_and_rolls_back_every_update_on_flush_error(
    db: Session,
) -> None:
    demo_a = _add_building(db, "DEMO_A")
    room_a = _add_room(db, demo_a.id, "A-101")
    room_b = _add_room(db, demo_a.id, "B-201")
    contract = _add_contract(db, demo_a.id, "DEMO_A-FLUSH-FAIL")
    item_a = _add_contract_room(db, contract.id, room_a.room_no)
    item_b = _add_contract_room(db, contract.id, room_b.room_no)
    db.commit()
    item_ids = (item_a.id, item_b.id)

    @event.listens_for(db, "before_flush")
    def _fail_changed_contract_rooms(session, _flush_context, _instances) -> None:
        if any(isinstance(item, ContractRoom) for item in session.dirty):
            raise RuntimeError("simulated flush failure")

    with pytest.raises(RuntimeError, match="simulated flush failure"):
        apply_building_room_backfill(db, demo_a.id, expected_total=2)
    event.remove(db, "before_flush", _fail_changed_contract_rooms)

    assert not db.dirty
    db.expire_all()
    for item_id in item_ids:
        item = db.get(ContractRoom, item_id)
        assert item.building_id is None
        assert item.room_id is None


def test_apply_rejects_a_session_with_unrelated_pending_changes(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    room = _add_room(db, demo_a.id, "A-101")
    contract = _add_contract(db, demo_a.id, "DEMO_A-DIRTY-SESSION")
    item = _add_contract_room(db, contract.id, room.room_no)
    db.commit()
    item_id = item.id

    contract.notes = "unrelated pending change"
    with pytest.raises(BuildingBackfillSessionError):
        apply_building_room_backfill(db, demo_a.id, expected_total=1)

    assert db.get(ContractRoom, item_id).building_id is None
    assert db.get(ContractRoom, item_id).room_id is None
    db.rollback()


def test_apply_requires_and_checks_the_expected_scope_count(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    room = _add_room(db, demo_a.id, "A-101")
    contract = _add_contract(db, demo_a.id, "DEMO_A-COUNT")
    _add_contract_room(db, contract.id, room.room_no)
    db.commit()

    with pytest.raises(BuildingBackfillExpectedTotalError):
        run_building_room_backfill(db, demo_a.id, apply=True)
    with pytest.raises(BuildingBackfillExpectedTotalError, match="预期 176，实际 1"):
        apply_building_room_backfill(db, demo_a.id, expected_total=176)


def test_area_tower_and_zone_conflicts_block_the_entire_scope(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    other = _add_building(db, "OTHER")
    wrong_zone = Zone(
        building_id=other.id,
        code="OTHER-ZONE",
        name="其他楼分区",
        zone_type="office",
        valid_from=date(2026, 1, 1),
    )
    db.add(wrong_zone)
    db.flush()
    area_room = _add_room(db, demo_a.id, "AREA", area=100.0)
    tower_room = _add_room(db, demo_a.id, "TOWER", tower="南塔", area=50.0)
    zone_room = _add_room(
        db,
        demo_a.id,
        "ZONE",
        tower="北塔",
        area=60.0,
        zone_id=wrong_zone.id,
    )
    contract = _add_contract(db, demo_a.id, "DEMO_A-DIMENSIONS")
    rows = [
        _add_contract_room(db, contract.id, area_room.room_no, area=99.0),
        _add_contract_room(
            db,
            contract.id,
            tower_room.room_no,
            tower="北塔",
            area=50.0,
        ),
        _add_contract_room(
            db,
            contract.id,
            zone_room.room_no,
            tower="北塔",
            area=60.0,
        ),
    ]
    db.commit()
    row_ids = [row.id for row in rows]

    summary = audit_building_room_links(db, demo_a.id)
    assert summary.area_conflicts == 1
    assert summary.tower_conflicts == 1
    assert summary.zone_conflicts == 1
    with pytest.raises(BuildingBackfillBlockedError):
        apply_building_room_backfill(db, demo_a.id, expected_total=3)

    db.expire_all()
    assert all(db.get(ContractRoom, row_id).room_id is None for row_id in row_ids)


def test_blank_or_whitespace_room_numbers_never_match(db: Session) -> None:
    demo_a = _add_building(db, "DEMO_A")
    _add_room(db, demo_a.id, "   ")
    contract = _add_contract(db, demo_a.id, "DEMO_A-BLANK")
    item = _add_contract_room(db, contract.id, "   ")
    db.commit()
    item_id = item.id

    summary = audit_building_room_links(db, demo_a.id)
    assert summary.total == 1
    assert summary.no_matches == 1
    assert summary.unique_matches == 0
    with pytest.raises(BuildingBackfillBlockedError):
        apply_building_room_backfill(db, demo_a.id, expected_total=1)

    db.expire_all()
    assert db.get(ContractRoom, item_id).room_id is None
