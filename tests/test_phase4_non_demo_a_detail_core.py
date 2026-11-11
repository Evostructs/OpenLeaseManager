from __future__ import annotations

import asyncio
from datetime import date
import io

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import openpyxl
import pytest

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import Building, Contract, OperationLog, Room, User, UserScopeGrant, Zone
from app.schemas import ContractCreate
from app.services.building_operations_profile import (
    DataAdapterType,
    resolve_building_operations_profile,
)
from app.services.detail_operations_scope import require_detail_page_scope
from app.services.contracts import create_contract, next_contract_no
from app.services.property_scope import SelectedPropertyScope
from app.services.rooms import create_room, room_template


SIMULATED_BUILDING_CODE = "DEMO_C"
SIMULATED_ZONE_CODE = "WHOLE_BUILDING"
SIMULATED_ZONE_NAME = "整楼"


def _selected_scope(building: Building, *, access_level: str = "edit") -> SelectedPropertyScope:
    return SelectedPropertyScope(
        user_id=1,
        building_ids=(int(building.id),),
        building_codes=(str(building.code),),
        scope_type="building",
        scope_code=str(building.code),
        portfolio_id=None,
        access_level=access_level,
        authorization_version="phase4-test",
    )


def _activate_simulated_detail_pilot(db) -> tuple[Building, Zone]:
    building = db.query(Building).filter(Building.code == SIMULATED_BUILDING_CODE).one()
    building.ingestion_mode = "detail"
    zone = Zone(
        building_id=building.id,
        code=SIMULATED_ZONE_CODE,
        name=SIMULATED_ZONE_NAME,
        zone_type="whole_building",
        valid_from=date(2026, 8, 24),
        is_active=True,
    )
    db.add(zone)
    db.commit()
    db.refresh(building)
    db.refresh(zone)
    return building, zone


def _login_admin_with_building_grants(client: TestClient, *building_codes: str) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert registered.status_code == 200, registered.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").one()
        buildings = db.query(Building).filter(Building.code.in_(building_codes)).all()
        assert {item.code for item in buildings} == set(building_codes)
        db.add_all(
            [
                UserScopeGrant(
                    user_id=user.id,
                    scope_type="building",
                    building_id=building.id,
                    access_level="edit",
                    include_future_buildings=False,
                    valid_from=today_in_timezone(),
                )
                for building in buildings
            ]
        )
        db.commit()
    finally:
        db.close()

    logged_in = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert logged_in.status_code == 200, logged_in.text


def _room_payload(room_no: str, *, area: float = 100) -> dict:
    return {
        "tower": SIMULATED_ZONE_NAME,
        "floor": "",
        "room_no": room_no,
        "room_type": "办公",
        "area": area,
        "is_leasable": True,
        "notes": "Phase 4 明确标注模拟房源",
    }


def _contract_payload(
    contract_no: str,
    room_no: str,
    *,
    start_date: str,
    end_date: str,
    room_id: int | None = None,
    prev_contract_no: str = "",
) -> dict:
    return {
        "contract_no": contract_no,
        "tower": SIMULATED_ZONE_NAME,
        "room_no": room_no,
        "tenant_name": f"模拟客户-{contract_no}",
        "area": 100,
        "unit_price": 4,
        "lease_type": "续租" if prev_contract_no else "新签",
        "start_date": start_date,
        "end_date": end_date,
        "prev_contract_no": prev_contract_no,
        "notes": "Phase 4 模拟合同",
        "room_items": [
            {
                "room_id": room_id,
                "tower": SIMULATED_ZONE_NAME,
                "room_no": room_no,
                "area": 100,
                "unit_price": 4,
                "start_date": start_date,
                "end_date": end_date,
            }
        ],
    }


async def _streaming_response_bytes(response: StreamingResponse) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    return b"".join(chunks)


def test_default_snapshot_catalog_remains_closed_and_detail_scope_fails_closed() -> None:
    db = SessionLocal()
    try:
        modes = {
            item.code: item.ingestion_mode
            for item in db.query(Building)
            .filter(Building.code.in_(("DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E")))
            .all()
        }
        assert modes == {
            "DEMO_B": "snapshot",
            "DEMO_C": "snapshot",
            "DEMO_D": "snapshot",
            "DEMO_E": "snapshot",
        }

        building = db.query(Building).filter(Building.code == SIMULATED_BUILDING_CODE).one()
        profile = resolve_building_operations_profile(building)
        assert profile.data_adapter is DataAdapterType.PUBLISHED_SNAPSHOT
        assert profile.space_structure.zones == ()

        with pytest.raises(HTTPException) as exc_info:
            require_detail_page_scope(db, _selected_scope(building), "rooms", for_write=True)
        assert exc_info.value.status_code == 422

        forged = SelectedPropertyScope(
            user_id=1,
            building_ids=(int(building.id),),
            building_codes=("DEMO_A",),
            scope_type="building",
            scope_code="DEMO_A",
            portfolio_id=None,
            access_level="edit",
            authorization_version="forged",
        )
        with pytest.raises(HTTPException) as forged_exc:
            require_detail_page_scope(db, forged, "rooms", for_write=True)
        assert forged_exc.value.status_code == 403
    finally:
        db.close()


def test_simulated_non_demo_a_rooms_create_update_import_and_template() -> None:
    db = SessionLocal()
    try:
        building, zone = _activate_simulated_detail_pilot(db)
        profile = resolve_building_operations_profile(building)
        assert profile.data_adapter is DataAdapterType.LIVE_DETAIL
        assert [(item.code, item.name) for item in profile.space_structure.zones] == [
            (SIMULATED_ZONE_CODE, SIMULATED_ZONE_NAME)
        ]
        building_id = int(building.id)
        zone_id = int(zone.id)
    finally:
        db.close()

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["楼塔", "楼层", "室号", "类型", "面积", "备注"])
    sheet.append([SIMULATED_ZONE_NAME, "", "SIM-IMPORT-01", "办公", 88, "Phase 4 模拟导入"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    with TestClient(app) as client:
        _login_admin_with_building_grants(client, SIMULATED_BUILDING_CODE)
        created = client.post(
            "/api/rooms",
            params={"building": SIMULATED_BUILDING_CODE},
            json=_room_payload("SIM-ROOM-01"),
        )
        assert created.status_code == 200, created.text
        room_id = int(created.json()["id"])

        updated = client.put(
            f"/api/rooms/{room_id}",
            params={"building": SIMULATED_BUILDING_CODE},
            json=_room_payload("SIM-ROOM-01A", area=120),
        )
        assert updated.status_code == 200, updated.text

        imported = client.post(
            "/api/rooms/import",
            params={"building": SIMULATED_BUILDING_CODE},
            files={
                "file": (
                    "phase4-simulated-rooms.xlsx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["added"] == 1

    db = SessionLocal()
    try:
        rows = (
            db.query(Room)
            .filter(Room.building_id == building_id)
            .order_by(Room.room_no)
            .all()
        )
        assert [(item.room_no, item.zone_id, item.tower) for item in rows] == [
            ("SIM-IMPORT-01", zone_id, SIMULATED_ZONE_NAME),
            ("SIM-ROOM-01A", zone_id, SIMULATED_ZONE_NAME),
        ]

        template = room_template(db=db, building_id=building_id)
        content = asyncio.run(_streaming_response_bytes(template))
        template_book = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        template_sheet = template_book.active
        assert template_sheet["A2"].value == SIMULATED_ZONE_NAME
        assert template_sheet["C2"].value == "示例单元-01"
    finally:
        db.close()


def test_simulated_non_demo_a_contract_lifecycle_numbering_templates_and_strict_units() -> None:
    db = SessionLocal()
    try:
        building, zone = _activate_simulated_detail_pilot(db)
        room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower=SIMULATED_ZONE_NAME,
            floor="",
            room_no="SIM-CONTRACT-01",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=False,
            notes="Phase 4 模拟合同房源",
        )
        import_room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower=SIMULATED_ZONE_NAME,
            floor="",
            room_no="SIM-CONTRACT-IMPORT",
            room_type="办公",
            area=90,
            is_leasable=True,
            is_deleted=False,
            notes="Phase 4 模拟导入合同房源",
        )
        db.add_all([room, import_room])
        db.commit()
        db.refresh(room)
        building_id = int(building.id)
        room_id = int(room.id)
    finally:
        db.close()


    import_book = openpyxl.Workbook()
    import_sheet = import_book.active
    import_sheet.append(["合同编号", "楼塔", "室号", "客户名称", "出租面积", "租金单价", "起租日", "终止日"])
    import_sheet.append(
        [
            "DEMO_C-2026-099",
            SIMULATED_ZONE_NAME,
            "SIM-CONTRACT-IMPORT",
            "Phase 4 模拟导入客户",
            90,
            4,
            "2026-01-01",
            "2026-12-31",
        ]
    )
    import_buffer = io.BytesIO()
    import_book.save(import_buffer)

    with TestClient(app) as client:
        _login_admin_with_building_grants(client, SIMULATED_BUILDING_CODE, "DEMO_D")

        next_no = client.get(
            "/api/contracts/next-no",
            params={"building": SIMULATED_BUILDING_CODE},
        )
        assert next_no.status_code == 200, next_no.text
        assert next_no.json()["next_no"] == f"DEMO_C-{today_in_timezone().year}-001"

        template = client.get(
            "/api/contracts/export/template",
            params={"building": SIMULATED_BUILDING_CODE},
        )
        assert template.status_code == 200, template.text
        template_book = openpyxl.load_workbook(io.BytesIO(template.content), read_only=True)
        assert template_book.active["A2"].value == f"DEMO_C-{today_in_timezone().year}-001"
        assert template_book.active["B2"].value == SIMULATED_ZONE_NAME

        created = client.post(
            "/api/contracts",
            params={"building": SIMULATED_BUILDING_CODE},
            json=_contract_payload(
                "DEMO_C-2026-001",
                "SIM-CONTRACT-01",
                room_id=room_id,
                start_date="2026-01-01",
                end_date="2026-12-31",
            ),
        )
        assert created.status_code == 200, created.text
        first_id = int(created.json()["id"])

        updated = client.put(
            f"/api/contracts/{first_id}",
            params={"building": SIMULATED_BUILDING_CODE},
            json={"notes": "Phase 4 模拟合同已修改"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["notes"] == "Phase 4 模拟合同已修改"

        renewed = client.post(
            "/api/contracts",
            params={"building": SIMULATED_BUILDING_CODE},
            json=_contract_payload(
                "DEMO_C-2027-001",
                "SIM-CONTRACT-01",
                start_date="2027-01-01",
                end_date="2027-12-31",
                prev_contract_no="DEMO_C-2026-001",
            ),
        )
        assert renewed.status_code == 200, renewed.text
        renewed_id = int(renewed.json()["id"])

        terminated = client.post(
            f"/api/contracts/{renewed_id}/terminate",
            params={"building": SIMULATED_BUILDING_CODE},
            json={"end_date": "2027-06-30", "notes": "Phase 4 模拟退租"},
        )
        assert terminated.status_code == 200, terminated.text
        assert terminated.json()["disposal"] == "已退租"
        assert terminated.json()["end_date"] == "2027-06-30"

        imported = client.post(
            "/api/contracts/import",
            params={"building": SIMULATED_BUILDING_CODE},
            files={
                "file": (
                    "phase4-simulated-contracts.xlsx",
                    import_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 1

        missing = client.post(
            "/api/contracts",
            params={"building": SIMULATED_BUILDING_CODE},
            json=_contract_payload(
                "DEMO_C-MISSING-UNIT",
                "N0001",
                start_date="2028-01-01",
                end_date="2028-12-31",
            ),
        )
        assert missing.status_code == 400
        assert "必须先在当前楼宇房源台账中创建" in missing.json()["detail"]

        snapshot_write = client.post(
            "/api/contracts",
            params={"building": "DEMO_D"},
            json=_contract_payload(
                "DEMO_D-CLOSED",
                "S0001",
                start_date="2028-01-01",
                end_date="2028-12-31",
            ),
        )
        assert snapshot_write.status_code == 422

    db = SessionLocal()
    try:
        first = db.get(Contract, first_id)
        successor = db.get(Contract, renewed_id)
        assert first.building_id == building_id
        assert first.disposal == "已续租"
        assert successor.building_id == building_id
        assert successor.prev_contract_no == first.contract_no
        assert db.query(Room).filter(Room.building_id == building_id, Room.room_no == "N0001").count() == 0
        assert db.query(Contract).filter(Contract.contract_no == "DEMO_C-MISSING-UNIT").count() == 0
        assert db.query(Contract).filter(Contract.contract_no == "DEMO_C-2026-099").count() == 1
        logs = db.query(OperationLog).filter(OperationLog.contract_id.in_((first_id, renewed_id))).all()
        assert logs
        assert all(log.scope_type == "building" and log.building_id == building_id for log in logs)
    finally:
        db.close()


def test_registered_generic_detail_building_uses_active_database_zones() -> None:
    db = SessionLocal()
    try:
        building = Building(
            code="GENERIC_DETAIL_P4",
            name="Phase 4 通用明细模拟楼",
            ingestion_mode="detail",
            is_active=True,
        )
        db.add(building)
        db.flush()
        zone = Zone(
            building_id=building.id,
            code="GENERIC_OFFICE",
            name="通用办公区",
            zone_type="office",
            valid_from=date(2026, 8, 24),
            is_active=True,
        )
        db.add(zone)
        db.flush()

        profile = resolve_building_operations_profile(building)
        assert profile.data_adapter is DataAdapterType.LIVE_DETAIL
        assert profile.space_structure.zones == ()
        scope = _selected_scope(building)
        require_detail_page_scope(db, scope, "contracts", for_write=True)

        room = create_room(
            db,
            {
                "tower": zone.name,
                "floor": "3",
                "room_no": "GEN-P4-301",
                "room_type": "办公",
                "area": 80,
                "is_leasable": True,
                "notes": "Phase 4 通用明细模拟房源",
            },
            building_id=int(building.id),
        )
        db.commit()
        assert (room.building_id, room.zone_id, room.tower) == (
            building.id,
            zone.id,
            zone.name,
        )
        assert next_contract_no(db, scope=scope)["next_no"] == (
            f"GENERIC_DETAIL_P4-{today_in_timezone().year}-001"
        )

        created = create_contract(
            db,
            ContractCreate(
                contract_no="GENERIC_DETAIL_P4-2026-001",
                tower=zone.name,
                room_no=room.room_no,
                tenant_name="Phase 4 通用明细模拟客户",
                area=80,
                unit_price=4,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                room_items=[
                    {
                        "room_id": room.id,
                        "tower": zone.name,
                        "room_no": room.room_no,
                        "area": 80,
                        "unit_price": 4,
                        "start_date": date(2026, 1, 1),
                        "end_date": date(2026, 12, 31),
                    }
                ],
            ),
            "phase4-test",
            scope=scope,
        )
        assert db.get(Contract, created["id"]).building_id == building.id
    finally:
        db.close()
