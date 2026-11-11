from __future__ import annotations

from datetime import date
import io

import openpyxl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    ContractRoom,
    OperationLog,
    Portfolio,
    PortfolioBuilding,
    Room,
    User,
    UserScopeGrant,
    Zone,
)
from app.routers.rooms import _ensure_room_not_occupied
from app.services.rooms import update_room


def _room_payload(room_no: str, *, area: float = 100) -> dict:
    return {
        "tower": "南塔",
        "floor": "5",
        "room_no": room_no,
        "room_type": "办公",
        "area": area,
        "is_leasable": True,
        "notes": "",
    }


def _register_admin_with_other_building(
    client: TestClient,
    building_id: int,
    *,
    portfolio_id: int | None = None,
) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert registered.status_code == 200, registered.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").one()
        grants = [
            UserScopeGrant(
                user_id=user.id,
                scope_type="building",
                building_id=building_id,
                access_level="edit",
                include_future_buildings=False,
                valid_from=today_in_timezone(),
            )
        ]
        if portfolio_id is not None:
            grants.append(
                UserScopeGrant(
                    user_id=user.id,
                    scope_type="portfolio",
                    portfolio_id=portfolio_id,
                    building_id=None,
                    access_level="edit",
                    include_future_buildings=True,
                    valid_from=today_in_timezone(),
                )
            )
        db.add_all(grants)
        db.commit()
    finally:
        db.close()

    logged_in = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert logged_in.status_code == 200, logged_in.text


@pytest.fixture
def property_buildings(isolated_database) -> dict[str, int | str]:
    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = Building(code="ROOMS_SCOPE_OTHER", name="房源范围测试乙楼", is_active=True)
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        db.add(other)
        db.flush()
        other_zone = Zone(
            building_id=other.id,
            code="SOUTH_OFFICE",
            name="房源范围测试乙楼南塔",
            zone_type="office",
            valid_from=date(2026, 1, 1),
            is_active=True,
        )
        db.add(other_zone)
        db.flush()
        db.add(
            PortfolioBuilding(
                portfolio_id=portfolio.id,
                building_id=other.id,
                valid_from=date(2026, 1, 1),
            )
        )
        db.commit()
        db.refresh(other)
        result = {
            "demo_a_id": int(demo_a.id),
            "other_id": int(other.id),
            "other_code": str(other.code),
            "other_zone_id": int(other_zone.id),
            "portfolio_id": int(portfolio.id),
            "portfolio_code": str(portfolio.code),
        }
    finally:
        db.close()

    yield result

    db = SessionLocal()
    try:
        other_id = int(result["other_id"])
        contract_ids = [
            contract_id
            for (contract_id,) in db.query(Contract.id).filter(Contract.building_id == other_id).all()
        ]
        if contract_ids:
            db.query(ContractRoom).filter(ContractRoom.contract_id.in_(contract_ids)).delete(
                synchronize_session=False
            )
            db.query(Contract).filter(Contract.id.in_(contract_ids)).delete(synchronize_session=False)
        db.query(OperationLog).filter(OperationLog.building_id == other_id).delete(synchronize_session=False)
        db.query(Room).filter(Room.building_id == other_id).delete(synchronize_session=False)
        db.query(UserScopeGrant).filter(UserScopeGrant.building_id == other_id).delete(synchronize_session=False)
        db.query(PortfolioBuilding).filter(
            PortfolioBuilding.portfolio_id == int(result["portfolio_id"]),
            PortfolioBuilding.building_id == other_id,
        ).delete(synchronize_session=False)
        db.query(Zone).filter(Zone.building_id == other_id).delete(synchronize_session=False)
        db.query(Building).filter(Building.id == other_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _insert_room(building_id: int, room_no: str, *, is_deleted: bool = False) -> int:
    db = SessionLocal()
    try:
        zone = db.query(Zone).filter(Zone.building_id == building_id).order_by(Zone.id).first()
        assert zone is not None
        room = Room(
            building_id=building_id,
            zone_id=zone.id,
            tower="南塔",
            floor="5",
            room_no=room_no,
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=is_deleted,
        )
        db.add(room)
        db.commit()
        db.refresh(room)
        return int(room.id)
    finally:
        db.close()


def _insert_contract(
    building_id: int,
    *,
    contract_no: str,
    room_no: str,
    room_id: int | None,
) -> int:
    db = SessionLocal()
    try:
        if room_id is None:
            zone = db.query(Zone).filter(Zone.building_id == building_id).order_by(Zone.id).first()
            assert zone is not None
            room = Room(
                building_id=building_id,
                zone_id=zone.id,
                tower="南塔",
                floor="5",
                room_no=room_no,
                room_type="办公",
                area=100,
                is_leasable=True,
                is_deleted=False,
            )
            db.add(room)
            db.flush()
            room_id = int(room.id)
        contract = Contract(
            building_id=building_id,
            contract_no=contract_no,
            tower="南塔",
            room_no=room_no,
            tenant_name=f"{contract_no}租户",
            area=100,
            unit_price=4,
            monthly_rent=12000,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        db.add(contract)
        db.flush()
        db.add(
            ContractRoom(
                contract_id=contract.id,
                building_id=building_id,
                room_id=room_id,
                tower="南塔",
                room_no=room_no,
                area=100,
                unit_price=4,
                monthly_rent=12000,
            )
        )
        db.commit()
        return int(contract.id)
    finally:
        db.close()


def test_room_list_stats_and_export_are_isolated_by_selected_building(property_buildings) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    other_code = str(property_buildings["other_code"])
    portfolio_id = int(property_buildings["portfolio_id"])
    portfolio_code = str(property_buildings["portfolio_code"])
    _insert_room(demo_a_id, "ROOM-SCOPE-DEMO_A")
    _insert_room(other_id, "ROOM-SCOPE-OTHER")

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id, portfolio_id=portfolio_id)
        default_rows = client.get("/api/rooms")
        other_rows = client.get("/api/rooms", params={"building": other_code})
        portfolio_rows = client.get("/api/rooms", params={"portfolio": portfolio_code})
        default_stats = client.get("/api/rooms/stats")
        other_stats = client.get("/api/rooms/stats", params={"building": other_code})
        exported = client.get("/api/rooms/export/excel")

    assert default_rows.status_code == 200
    assert [row["room_no"] for row in default_rows.json()] == ["ROOM-SCOPE-DEMO_A"]
    assert other_rows.status_code == 200
    assert [row["room_no"] for row in other_rows.json()] == ["ROOM-SCOPE-OTHER"]
    assert portfolio_rows.status_code == 422
    assert default_stats.json()["total_rooms"] == 1
    assert other_stats.json()["total_rooms"] == 1
    assert exported.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), read_only=True)
    exported_text = "\n".join(
        str(cell.value or "")
        for row in workbook.active.iter_rows()
        for cell in row
    )
    assert "ROOM-SCOPE-DEMO_A" in exported_text
    assert "ROOM-SCOPE-OTHER" not in exported_text


def test_authorized_other_building_object_is_still_404_when_not_selected(property_buildings) -> None:
    other_id = int(property_buildings["other_id"])
    other_code = str(property_buildings["other_code"])
    room_id = _insert_room(other_id, "ROOM-SCOPE-OBJECT")

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id)
        default_update = client.put(f"/api/rooms/{room_id}", json=_room_payload("ROOM-SCOPE-OBJECT"))
        default_delete = client.delete(f"/api/rooms/{room_id}")
        selected_update = client.put(
            f"/api/rooms/{room_id}",
            params={"building": other_code},
            json=_room_payload("ROOM-SCOPE-OBJECT"),
        )

    assert default_update.status_code == 404
    assert default_delete.status_code == 404
    assert selected_update.status_code == 200, selected_update.text
    db = SessionLocal()
    try:
        room = db.get(Room, room_id)
        assert room is not None
        assert room.is_deleted is False
        assert room.area == 100
    finally:
        db.close()


@pytest.mark.parametrize(
    ("endpoint", "is_deleted"),
    [
        ("/api/rooms/batch-delete", False),
        ("/api/rooms/recycle/batch-restore", True),
    ],
)
def test_room_batch_scope_and_missing_id_fail_atomically(
    property_buildings,
    endpoint: str,
    is_deleted: bool,
) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    valid_id = _insert_room(demo_a_id, f"ROOM-BATCH-DEMO_A-{int(is_deleted)}", is_deleted=is_deleted)
    other_id_value = _insert_room(other_id, f"ROOM-BATCH-OTHER-{int(is_deleted)}", is_deleted=is_deleted)

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id)
        mixed_scope = client.post(endpoint, json=[valid_id, other_id_value])
        missing_id = client.post(endpoint, json=[valid_id, 99999999])

    assert mixed_scope.status_code == 404
    assert missing_id.status_code == 404
    db = SessionLocal()
    try:
        assert db.get(Room, valid_id).is_deleted is is_deleted
        assert db.get(Room, other_id_value).is_deleted is is_deleted
        assert (
            db.query(OperationLog)
            .filter(OperationLog.target_id.in_([valid_id, other_id_value]))
            .count()
            == 0
        )
    finally:
        db.close()


def test_same_room_number_contract_history_and_occupancy_use_building_and_room_number(
    property_buildings,
) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    other_code = str(property_buildings["other_code"])
    room_id = _insert_room(demo_a_id, "ROOM-SAME-NO")
    _insert_contract(demo_a_id, contract_no="ROOM-HISTORY-DEMO_A", room_no="ROOM-SAME-NO", room_id=room_id)
    _insert_contract(other_id, contract_no="ROOM-HISTORY-OTHER", room_no="ROOM-SAME-NO", room_id=None)

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id)
        default_history = client.get("/api/rooms/ROOM-SAME-NO/contracts")
        other_history = client.get(
            "/api/rooms/ROOM-SAME-NO/contracts",
            params={"building": other_code},
        )
        listed = client.get("/api/rooms")

    assert default_history.status_code == 200
    assert [row["contract_no"] for row in default_history.json()["contracts"]] == ["ROOM-HISTORY-DEMO_A"]
    assert other_history.status_code == 200
    assert [row["contract_no"] for row in other_history.json()["contracts"]] == ["ROOM-HISTORY-OTHER"]
    listed_room = next(row for row in listed.json() if row["room_no"] == "ROOM-SAME-NO")
    assert listed_room["occupied"] is True
    assert listed_room["contract"]["contract_no"] == "ROOM-HISTORY-DEMO_A"


def test_non_demo_a_payload_requires_registered_zone_without_guessing(property_buildings) -> None:
    other_id = int(property_buildings["other_id"])
    other_code = str(property_buildings["other_code"])
    existing_id = _insert_room(other_id, "ROOM-NONDEMO_A-EXISTING")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["楼塔", "室号", "面积"])
    worksheet.append(["南塔", "ROOM-NONDEMO_A-IMPORT", 100])
    buffer = io.BytesIO()
    workbook.save(buffer)

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id)
        created = client.post(
            "/api/rooms",
            params={"building": other_code},
            json=_room_payload("ROOM-NONDEMO_A-CREATE"),
        )
        batched = client.post(
            "/api/rooms/batch",
            params={"building": other_code},
            json=[_room_payload("ROOM-NONDEMO_A-BATCH")],
        )
        imported = client.post(
            "/api/rooms/import",
            params={"building": other_code},
            files={
                "file": (
                    "rooms.xlsx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        updated = client.put(
            f"/api/rooms/{existing_id}",
            params={"building": other_code},
            json=_room_payload("ROOM-NONDEMO_A-EXISTING"),
        )

    assert created.status_code == 400
    assert batched.status_code == 400
    assert imported.status_code == 400
    assert all("分区" in response.json()["detail"] for response in (created, batched, imported))
    assert updated.status_code == 200, updated.text

    db = SessionLocal()
    try:
        rows = db.query(Room).filter(Room.building_id == other_id).all()
        assert [row.room_no for row in rows] == ["ROOM-NONDEMO_A-EXISTING"]
    finally:
        db.close()


def test_room_write_requires_one_selected_building(property_buildings) -> None:
    other_id = int(property_buildings["other_id"])
    portfolio_id = int(property_buildings["portfolio_id"])
    portfolio_code = str(property_buildings["portfolio_code"])

    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id, portfolio_id=portfolio_id)
        response = client.post(
            "/api/rooms",
            params={"portfolio": portfolio_code},
            json=_room_payload("ROOM-PORTFOLIO-WRITE"),
        )

    assert response.status_code == 422
    db = SessionLocal()
    try:
        assert db.query(Room).filter(Room.room_no == "ROOM-PORTFOLIO-WRITE").count() == 0
    finally:
        db.close()


def test_legacy_no_parameter_room_request_still_defaults_to_demo_a(property_buildings) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    with TestClient(app) as client:
        _register_admin_with_other_building(client, other_id)
        created = client.post("/api/rooms", json=_room_payload("ROOM-LEGACY-DEFAULT"))
        listed = client.get("/api/rooms")

    assert created.status_code == 200, created.text
    assert created.json().keys() == {"message", "id"}
    row = next(item for item in listed.json() if item["room_no"] == "ROOM-LEGACY-DEFAULT")
    assert set(row) == {
        "id",
        "tower",
        "floor",
        "room_no",
        "room_type",
        "area",
        "is_leasable",
        "notes",
        "occupied",
        "availability_mode",
        "contract",
        "signals",
        "operating_statuses",
    }
    db = SessionLocal()
    try:
        assert db.get(Room, created.json()["id"]).building_id == demo_a_id
    finally:
        db.close()


def test_same_room_number_is_allowed_in_different_buildings(property_buildings) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])

    demo_a_room_id = _insert_room(demo_a_id, "ROOM-BUILDING-LOCAL-UNIQUE")
    other_room_id = _insert_room(other_id, "ROOM-BUILDING-LOCAL-UNIQUE")

    assert demo_a_room_id != other_room_id
    db = SessionLocal()
    try:
        rows = (
            db.query(Room)
            .filter(Room.room_no == "ROOM-BUILDING-LOCAL-UNIQUE")
            .order_by(Room.building_id)
            .all()
        )
        assert {int(room.building_id) for room in rows} == {demo_a_id, other_id}
    finally:
        db.close()


@pytest.mark.parametrize("mismatch_target", ("relation", "contract"))
def test_update_room_rejects_cross_building_contract_graph_before_mutation(
    property_buildings,
    mismatch_target: str,
) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    room_no = f"ROOM-UPDATE-GRAPH-{mismatch_target.upper()}"
    room_id = _insert_room(demo_a_id, room_no)
    contract_id = _insert_contract(
        demo_a_id,
        contract_no=f"ROOM-UPDATE-GRAPH-{mismatch_target.upper()}",
        room_no=room_no,
        room_id=room_id,
    )

    db = SessionLocal()
    try:
        room = db.get(Room, room_id)
        contract = db.get(Contract, contract_id)
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract_id).one()
        if mismatch_target == "relation":
            relation.building_id = other_id
        else:
            contract.building_id = other_id

        with pytest.raises(HTTPException) as exc_info:
            update_room(
                room,
                _room_payload(f"{room_no}-CHANGED", area=999),
                db,
                building_id=demo_a_id,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "房源关联关系楼宇不一致，当前操作已停止"
        assert (room.room_no, room.area, room.building_id) == (room_no, 100, demo_a_id)
        db.rollback()
        db.expire_all()
        stored_room = db.get(Room, room_id)
        stored_contract = db.get(Contract, contract_id)
        stored_relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract_id).one()
        assert (stored_room.room_no, stored_room.area, stored_room.building_id) == (
            room_no,
            100,
            demo_a_id,
        )
        assert stored_contract.building_id == demo_a_id
        assert stored_relation.building_id == demo_a_id
        assert (
            db.query(OperationLog)
            .filter(OperationLog.target_type == "room", OperationLog.target_id == room_id)
            .count()
            == 0
        )
    finally:
        db.close()


def test_delete_occupancy_guard_returns_generic_conflict_for_mismatched_graph(
    property_buildings,
) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    room_no = "ROOM-DELETE-GRAPH"
    room_id = _insert_room(demo_a_id, room_no)
    contract_no = "ROOM-DELETE-GRAPH-CONTRACT"
    contract_id = _insert_contract(
        demo_a_id,
        contract_no=contract_no,
        room_no=room_no,
        room_id=room_id,
    )

    db = SessionLocal()
    try:
        room = db.get(Room, room_id)
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract_id).one()
        relation.building_id = other_id

        with pytest.raises(HTTPException) as exc_info:
            _ensure_room_not_occupied(db, room)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "房源关联关系楼宇不一致，当前操作已停止"
        assert contract_no not in str(exc_info.value.detail)
        assert room.is_deleted is False
        db.rollback()
    finally:
        db.close()


def test_occupancy_guard_uses_contracts_from_the_rooms_building_only(
    property_buildings,
) -> None:
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    room_no = "ROOM-OCCUPANCY-BUILDING-SCOPE"
    demo_a_room_id = _insert_room(demo_a_id, room_no)
    other_room_id = _insert_room(other_id, room_no)
    _insert_contract(
        other_id,
        contract_no="ROOM-OCCUPANCY-OTHER",
        room_no=room_no,
        room_id=other_room_id,
    )

    db = SessionLocal()
    try:
        _ensure_room_not_occupied(db, db.get(Room, demo_a_room_id))
    finally:
        db.close()

    _insert_contract(
        demo_a_id,
        contract_no="ROOM-OCCUPANCY-DEMO_A",
        room_no=room_no,
        room_id=demo_a_room_id,
    )
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc_info:
            _ensure_room_not_occupied(db, db.get(Room, demo_a_room_id))
        assert exc_info.value.status_code == 400
        assert "ROOM-OCCUPANCY-DEMO_A" in str(exc_info.value.detail)
        assert "ROOM-OCCUPANCY-OTHER" not in str(exc_info.value.detail)
    finally:
        db.close()


def test_room_search_is_bounded_literal_and_scoped(property_buildings):
    client = TestClient(app)
    _register_admin_with_other_building(client, int(property_buildings["other_id"]))
    demo_a_id = int(property_buildings["demo_a_id"])
    other_id = int(property_buildings["other_id"])
    first = _insert_room(demo_a_id, "SEARCH-101")
    _insert_room(demo_a_id, "SEARCH-102")
    _insert_room(demo_a_id, "SEARCH-DELETED", is_deleted=True)
    other = _insert_room(other_id, "SEARCH-101")
    response = client.get("/api/rooms/search", params={"building":"DEMO_A", "keyword":"SEARCH", "page_size":1})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [first]
    assert set(result["items"][0]) == {"id","room_no","tower","building_id"}
    response = client.get("/api/rooms/search", params={"building":property_buildings["other_code"], "keyword":"SEARCH"})
    assert [item["id"] for item in response.json()["items"]] == [other]
    assert client.get("/api/rooms/search", params={"keyword":"%", "building":"DEMO_A"}).json()["items"] == []
    assert client.get("/api/rooms/search", params={"keyword":"SEARCH", "page_size":21}).status_code == 422
    with SessionLocal() as db:
        db.query(UserScopeGrant).filter(UserScopeGrant.building_id == other_id).delete(synchronize_session=False)
        db.commit()
    assert client.get("/api/rooms/search", params={"keyword":"SEARCH", "building":property_buildings["other_code"]}).status_code == 404
