from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError

import app.routers.rooms as rooms_router
from app.database import SessionLocal
from app.main import app
from app.models import Building, Contract, ContractRoom, OperationLog, Room, Zone


def _login_admin(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert registered.status_code == 200, registered.text
    logged_in = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert logged_in.status_code == 200, logged_in.text


def _insert_room(
    room_no: str,
    *,
    is_deleted: bool,
    building_id: int | None = None,
    zone_id: int | None = None,
    unscoped: bool = False,
) -> int:
    db = SessionLocal()
    try:
        if building_id is None and not unscoped:
            building = db.query(Building).filter(Building.code == "DEMO_A").one()
            zone = db.query(Zone).filter(Zone.building_id == building.id, Zone.code == "SOUTH_OFFICE").one()
            building_id = building.id
            zone_id = zone.id
        elif building_id is not None and zone_id is None:
            zone = db.query(Zone).filter(Zone.building_id == building_id).order_by(Zone.id).first()
            assert zone is not None
            zone_id = zone.id
        room = Room(
            building_id=building_id,
            zone_id=zone_id,
            tower="南塔",
            floor="1",
            room_no=room_no,
            room_type="办公",
            area=100,
            is_deleted=is_deleted,
        )
        db.add(room)
        db.commit()
        db.refresh(room)
        return room.id
    finally:
        db.close()


def _insert_contract_reference(room_id: int, contract_no: str) -> int:
    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        room = db.get(Room, room_id)
        contract = Contract(
            contract_no=contract_no,
            building_id=building.id,
            tower=room.tower,
            room_no=room.room_no,
            tenant_name="房源回收站范围测试",
            area=room.area,
            unit_price=4,
            monthly_rent=12000,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        db.add(contract)
        db.flush()
        relation = ContractRoom(
            contract_id=contract.id,
            building_id=building.id,
            room_id=room.id,
            tower=room.tower,
            room_no=room.room_no,
            area=room.area,
            unit_price=4,
            monthly_rent=12000,
        )
        db.add(relation)
        db.commit()
        db.refresh(relation)
        return relation.id
    finally:
        db.close()


@pytest.fixture
def other_building_id(isolated_database) -> int:
    db = SessionLocal()
    try:
        building = Building(code="ROOM_SCOPE_OTHER", name="房源范围反例楼宇")
        db.add(building)
        db.flush()
        db.add(
            Zone(
                building_id=building.id,
                code="SOUTH_OFFICE",
                name="房源范围反例楼宇南塔",
                zone_type="office",
                valid_from=date(2026, 1, 1),
                is_active=True,
            )
        )
        db.commit()
        building_id = building.id
    finally:
        db.close()

    yield building_id

    db = SessionLocal()
    try:
        db.query(Room).filter(Room.building_id == building_id).delete(synchronize_session=False)
        db.query(Zone).filter(Zone.building_id == building_id).delete(synchronize_session=False)
        db.query(Building).filter(Building.id == building_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_single_delete_and_restore_keep_demo_a_scope_with_targeted_logs() -> None:
    delete_room_id = _insert_room("ROOM-SCOPE-DELETE", is_deleted=False)
    restore_room_id = _insert_room("ROOM-SCOPE-RESTORE", is_deleted=True)

    with TestClient(app) as client:
        _login_admin(client)
        deleted = client.delete(f"/api/rooms/{delete_room_id}")
        restored = client.post(f"/api/rooms/recycle/restore/{restore_room_id}")

    assert deleted.status_code == 200, deleted.text
    assert restored.status_code == 200, restored.text

    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        south_zone = db.query(Zone).filter(Zone.building_id == building.id, Zone.code == "SOUTH_OFFICE").one()
        deleted_room = db.get(Room, delete_room_id)
        restored_room = db.get(Room, restore_room_id)
        assert (deleted_room.building_id, deleted_room.zone_id, deleted_room.is_deleted) == (
            building.id,
            south_zone.id,
            True,
        )
        assert (restored_room.building_id, restored_room.zone_id, restored_room.is_deleted) == (
            building.id,
            south_zone.id,
            False,
        )

        logs = (
            db.query(OperationLog)
            .filter(OperationLog.target_type == "room")
            .order_by(OperationLog.id)
            .all()
        )
        assert [(log.action, log.target_id) for log in logs] == [
            ("room_delete", delete_room_id),
            ("room_restore", restore_room_id),
        ]
        assert all(
            (log.scope_type, log.building_id, log.portfolio_id) == ("building", building.id, None)
            for log in logs
        )
    finally:
        db.close()


@pytest.mark.parametrize(
    ("endpoint", "initial_deleted"),
    [
        ("/api/rooms/batch-delete", False),
        ("/api/rooms/recycle/batch-restore", True),
    ],
)
def test_batch_delete_and_restore_reject_other_building_atomically(
    endpoint: str,
    initial_deleted: bool,
    other_building_id: int,
) -> None:
    legacy_room_id = _insert_room(
        f"ROOM-SCOPE-LEGACY-{int(initial_deleted)}",
        is_deleted=initial_deleted,
    )
    other_room_id = _insert_room(
        f"ROOM-SCOPE-OTHER-{int(initial_deleted)}",
        is_deleted=initial_deleted,
        building_id=other_building_id,
    )

    with TestClient(app) as client:
        _login_admin(client)
        response = client.post(endpoint, json=[legacy_room_id, other_room_id])

    assert response.status_code == 404, response.text

    db = SessionLocal()
    try:
        legacy_room = db.get(Room, legacy_room_id)
        other_room = db.get(Room, other_room_id)
        assert legacy_room.is_deleted is initial_deleted
        assert legacy_room.building_id == db.query(Building).filter(Building.code == "DEMO_A").one().id
        assert legacy_room.zone_id is not None
        assert other_room.is_deleted is initial_deleted
        assert other_room.building_id == other_building_id
        assert (
            db.query(OperationLog)
            .filter(OperationLog.target_id.in_([legacy_room_id, other_room_id]))
            .count()
            == 0
        )
    finally:
        db.close()


def test_all_single_and_clear_paths_reject_other_building_without_changes(other_building_id: int) -> None:
    active_room_id = _insert_room(
        "ROOM-SCOPE-OTHER-ACTIVE",
        is_deleted=False,
        building_id=other_building_id,
    )
    restore_room_id = _insert_room(
        "ROOM-SCOPE-OTHER-RESTORE",
        is_deleted=True,
        building_id=other_building_id,
    )
    purge_room_id = _insert_room(
        "ROOM-SCOPE-OTHER-PURGE",
        is_deleted=True,
        building_id=other_building_id,
    )
    legacy_deleted_id = _insert_room("ROOM-SCOPE-CLEAR-ATOMIC", is_deleted=True)

    with TestClient(app) as client:
        _login_admin(client)
        responses = [
            client.delete(f"/api/rooms/{active_room_id}"),
            client.post(f"/api/rooms/recycle/restore/{restore_room_id}"),
            client.delete(f"/api/rooms/recycle/purge/{purge_room_id}"),
            client.post("/api/rooms/recycle/clear"),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404, 200]

    db = SessionLocal()
    try:
        assert db.get(Room, active_room_id).is_deleted is False
        assert db.get(Room, restore_room_id).is_deleted is True
        assert db.get(Room, purge_room_id) is not None
        assert db.get(Room, legacy_deleted_id) is None
        clear_log = db.query(OperationLog).filter(OperationLog.target_id == legacy_deleted_id).one()
        assert clear_log.action == "room_clear"
    finally:
        db.close()


def test_purge_and_clear_reject_contract_references_without_clearing_room_id() -> None:
    referenced_room_id = _insert_room("ROOM-SCOPE-REFERENCED", is_deleted=True)
    untouched_room_id = _insert_room("ROOM-SCOPE-UNREFERENCED", is_deleted=True)
    relation_id = _insert_contract_reference(referenced_room_id, "ROOM-SCOPE-CONTRACT")

    with TestClient(app) as client:
        _login_admin(client)
        purge_response = client.delete(f"/api/rooms/recycle/purge/{referenced_room_id}")
        clear_response = client.post("/api/rooms/recycle/clear")

    assert purge_response.status_code == 400, purge_response.text
    assert clear_response.status_code == 400, clear_response.text
    assert "关联合同房源" in purge_response.json()["detail"]
    assert "关联合同房源" in clear_response.json()["detail"]

    db = SessionLocal()
    try:
        relation = db.get(ContractRoom, relation_id)
        assert relation.room_id == referenced_room_id
        assert db.get(Room, referenced_room_id) is not None
        untouched = db.get(Room, untouched_room_id)
        assert untouched is not None
        assert untouched.building_id == db.query(Building).filter(Building.code == "DEMO_A").one().id
        assert db.query(OperationLog).filter(OperationLog.target_type == "room").count() == 0
    finally:
        db.close()


def test_successful_purge_and_clear_write_one_scoped_log_per_room() -> None:
    purge_room_id = _insert_room("ROOM-SCOPE-PURGE-OK", is_deleted=True)
    clear_room_ids = [
        _insert_room("ROOM-SCOPE-CLEAR-OK-1", is_deleted=True),
        _insert_room("ROOM-SCOPE-CLEAR-OK-2", is_deleted=True),
    ]

    with TestClient(app) as client:
        _login_admin(client)
        purge_response = client.delete(f"/api/rooms/recycle/purge/{purge_room_id}")
        clear_response = client.post("/api/rooms/recycle/clear")

    assert purge_response.status_code == 200, purge_response.text
    assert clear_response.status_code == 200, clear_response.text
    assert clear_response.json()["message"] == "已清空 2 个房间"

    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        assert db.query(Room).filter(Room.id.in_([purge_room_id, *clear_room_ids])).count() == 0
        logs = (
            db.query(OperationLog)
            .filter(OperationLog.target_type == "room")
            .order_by(OperationLog.id)
            .all()
        )
        assert [(log.action, log.target_id) for log in logs] == [
            ("room_purge", purge_room_id),
            ("room_clear", clear_room_ids[0]),
            ("room_clear", clear_room_ids[1]),
        ]
        assert all(log.target_id is not None for log in logs)
        assert all(
            (log.scope_type, log.building_id, log.portfolio_id) == ("building", building.id, None)
            for log in logs
        )
    finally:
        db.close()


def test_batch_scope_backfill_and_logs_roll_back_together_when_logging_fails(monkeypatch) -> None:
    room_ids = [
        _insert_room("ROOM-SCOPE-ROLLBACK-1", is_deleted=False),
        _insert_room("ROOM-SCOPE-ROLLBACK-2", is_deleted=False),
    ]
    real_log_action = rooms_router.log_action
    call_count = 0

    def fail_on_second_log(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        real_log_action(*args, **kwargs)
        if call_count == 2:
            raise RuntimeError("simulated room log failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        _login_admin(client)
        monkeypatch.setattr(rooms_router, "log_action", fail_on_second_log)
        response = client.post("/api/rooms/batch-delete", json=room_ids)

    assert response.status_code == 500

    db = SessionLocal()
    try:
        rooms = db.query(Room).filter(Room.id.in_(room_ids)).order_by(Room.id).all()
        assert len(rooms) == 2
        assert all(room.is_deleted is False for room in rooms)
        assert all(room.building_id is not None and room.zone_id is not None for room in rooms)
        assert db.query(OperationLog).filter(OperationLog.target_id.in_(room_ids)).count() == 0
    finally:
        db.close()


def test_unscoped_room_object_fails_closed() -> None:
    with pytest.raises(IntegrityError):
        _insert_room("ROOM-SCOPE-UNRESOLVED", is_deleted=False, unscoped=True)

    db = SessionLocal()
    try:
        assert db.query(Room).filter(Room.room_no == "ROOM-SCOPE-UNRESOLVED").count() == 0
    finally:
        db.close()
