from __future__ import annotations

from datetime import date
import io

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
import openpyxl
import pytest
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers

from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    Building,
    Contract,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    OperationLog,
    Portfolio,
    Room,
    User,
    UserScopeGrant,
    Zone,
)
from app.schemas import AttachmentUpdate, ContractCreate, CustomerFollowUpCreate
from app.services.attachments import create_attachment, delete_attachment, update_attachment
from app.services.common import log_action
from app.services.contracts import create_contract
from app.services.customers import create_customer_follow_up
from app.services.property_scope import SelectedPropertyScope
from app.services.rooms import create_room, import_rooms_excel, update_room


def _building(db) -> Building:
    return db.query(Building).filter(Building.code == "DEMO_A").one()


def _edit_scope(db) -> SelectedPropertyScope:
    building = _building(db)
    return SelectedPropertyScope(
        user_id=0,
        building_ids=(building.id,),
        building_codes=(building.code,),
        scope_type="building",
        scope_code=building.code,
        portfolio_id=None,
        access_level="edit",
        authorization_version="test",
    )


def _zone(db, code: str) -> Zone:
    return (
        db.query(Zone)
        .filter(Zone.building_id == _building(db).id, Zone.code == code)
        .one()
    )


def _contract_payload(contract_no: str, room_no: str, *, room_id: int | None = None) -> ContractCreate:
    return ContractCreate(
        contract_no=contract_no,
        tower="南塔",
        room_no=room_no,
        tenant_name=f"客户-{contract_no}",
        area=120,
        unit_price=4,
        start_date=date(2027, 1, 1),
        end_date=date(2027, 12, 31),
        room_items=[
            {
                "room_id": room_id,
                "tower": "南塔",
                "room_no": room_no,
                "area": 120,
                "unit_price": 4,
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
            }
        ],
    )


def _seed_other_building_room(db, *, code: str, room_no: str) -> tuple[Building, Room]:
    building = Building(
        code=code,
        name=f"跨楼宇防串测试-{code}",
        timezone="Asia/Shanghai",
        currency="CNY",
        ingestion_mode="detail",
        is_provisional=False,
        is_active=True,
    )
    db.add(building)
    db.flush()
    zone = Zone(
        building_id=building.id,
        code="SOUTH_OFFICE",
        name="测试南塔办公区",
        zone_type="office",
        valid_from=date(2026, 1, 1),
        is_active=True,
    )
    db.add(zone)
    db.flush()
    room = Room(
        building_id=building.id,
        zone_id=zone.id,
        tower="南塔",
        floor="9",
        room_no=room_no,
        room_type="办公",
        area=120,
        is_leasable=True,
        is_deleted=False,
    )
    db.add(room)
    db.commit()
    return building, room


def _remove_test_building(db, building_id: int) -> None:
    db.rollback()
    db.query(Room).filter(Room.building_id == building_id).delete(synchronize_session=False)
    db.query(Zone).filter(Zone.building_id == building_id).delete(synchronize_session=False)
    db.query(Building).filter(Building.id == building_id).delete(synchronize_session=False)
    db.commit()


def test_room_create_import_and_update_keep_demo_a_zone_and_linked_contract_text() -> None:
    db = SessionLocal()
    try:
        building = _building(db)
        south_zone = _zone(db, "SOUTH_OFFICE")
        north_zone = _zone(db, "NORTH_OFFICE")

        room = create_room(
            db,
            {
                "tower": "南塔",
                "floor": "8",
                "room_no": "DW-R-001",
                "room_type": "办公",
                "area": 120,
            },
        )
        db.commit()
        assert (room.building_id, room.zone_id) == (building.id, south_zone.id)

        created = create_contract(db, _contract_payload("DW-C-001", room.room_no), "tester", scope=_edit_scope(db))
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == created["id"]).one()
        assert (relation.building_id, relation.room_id) == (building.id, room.id)

        update_room(
            room,
            {
                "tower": "北塔",
                "floor": "8",
                "room_no": "DW-R-001-N",
                "room_type": "办公",
                "area": 120,
                "is_leasable": True,
                "notes": "",
            },
            db,
        )
        db.commit()
        db.refresh(relation)
        contract = db.get(Contract, created["id"])
        assert (room.building_id, room.zone_id) == (building.id, north_zone.id)
        assert (relation.building_id, relation.room_id, relation.tower, relation.room_no) == (
            building.id,
            room.id,
            "北塔",
            "DW-R-001-N",
        )
        assert (contract.building_id, contract.tower, contract.room_no) == (
            building.id,
            "北塔",
            "DW-R-001-N",
        )

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["塔楼", "楼层", "室号", "类型", "面积"])
        sheet.append(["商业配套", "2", "DW-R-IMPORT", "商业", 88])
        content = io.BytesIO()
        workbook.save(content)
        result = import_rooms_excel(content.getvalue(), db)
        imported = db.query(Room).filter(Room.room_no == "DW-R-IMPORT").one()
        assert result["added"] == 1
        assert imported.building_id == building.id
        assert imported.zone_id == _zone(db, "COMMERCIAL_SUPPORT").id
    finally:
        db.close()


def test_contract_dual_write_links_exact_room_and_rejects_mismatched_room_id() -> None:
    db = SessionLocal()
    try:
        building = _building(db)
        first = create_room(
            db,
            {"tower": "南塔", "floor": "9", "room_no": "DW-R-101", "area": 120},
        )
        second = create_room(
            db,
            {"tower": "南塔", "floor": "9", "room_no": "DW-R-102", "area": 120},
        )
        db.commit()

        result = create_contract(db, _contract_payload("DW-C-101", first.room_no), "tester", scope=_edit_scope(db))
        contract = db.get(Contract, result["id"])
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract.id).one()
        log = (
            db.query(OperationLog)
            .filter(OperationLog.contract_id == contract.id, OperationLog.action == "create")
            .one()
        )
        assert contract.building_id == building.id
        assert (relation.building_id, relation.room_id) == (building.id, first.id)
        assert (log.scope_type, log.building_id, log.portfolio_id) == (
            "building",
            building.id,
            None,
        )

        with pytest.raises(HTTPException) as exc_info:
            create_contract(
                db,
                _contract_payload("DW-C-BAD-LINK", first.room_no, room_id=second.id),
                "tester",
                scope=_edit_scope(db),
            )
        assert exc_info.value.status_code == 400
        db.rollback()
        assert db.query(Contract).filter(Contract.contract_no == "DW-C-BAD-LINK").count() == 0
    finally:
        db.close()


def test_legacy_contract_rejects_an_explicit_room_from_another_building() -> None:
    db = SessionLocal()
    other_building_id = None
    try:
        other, other_room = _seed_other_building_room(
            db,
            code="LEGACY_ROOM_ID_OTHER",
            room_no="DW-R-OTHER-ID",
        )
        other_building_id = int(other.id)

        with pytest.raises(HTTPException) as exc_info:
            create_contract(
                db,
                _contract_payload(
                    "DW-C-OTHER-ROOM-ID",
                    other_room.room_no,
                    room_id=other_room.id,
                ),
                "tester",
                scope=_edit_scope(db),
            )

        assert exc_info.value.status_code == 400
        db.rollback()
        assert (
            db.query(Contract)
            .filter(Contract.contract_no == "DW-C-OTHER-ROOM-ID")
            .count()
            == 0
        )
        stored_other_room = db.get(Room, other_room.id)
        assert stored_other_room is not None
        assert stored_other_room.building_id == other_building_id
    finally:
        if other_building_id is not None:
            _remove_test_building(db, other_building_id)
        db.close()


def test_legacy_text_room_number_never_adopts_another_buildings_room() -> None:
    db = SessionLocal()
    other_building_id = None
    try:
        other, other_room = _seed_other_building_room(
            db,
            code="LEGACY_ROOM_NO_OTHER",
            room_no="DW-R-SAME-NUMBER",
        )
        other_building_id = int(other.id)

        result = create_contract(
            db,
            _contract_payload("DW-C-SAME-NUMBER", other_room.room_no),
            "tester",
            scope=_edit_scope(db),
        )

        contract = db.get(Contract, result["id"])
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract.id).one()
        demo_a_room = db.get(Room, relation.room_id)
        assert contract.building_id == _building(db).id
        assert relation.building_id == _building(db).id
        assert relation.room_id != other_room.id
        assert demo_a_room is not None
        assert (demo_a_room.building_id, demo_a_room.room_no) == (
            _building(db).id,
            other_room.room_no,
        )
        assert db.get(Room, other_room.id).building_id == other_building_id
    finally:
        if other_building_id is not None:
            _remove_test_building(db, other_building_id)
        db.close()


def test_legacy_text_only_contract_keeps_old_behavior_and_creates_exact_room_identity() -> None:
    db = SessionLocal()
    try:
        building = _building(db)
        result = create_contract(
            db,
            _contract_payload("DW-C-TEXT", "DW-NOT-IN-LEDGER"),
            "tester",
            scope=_edit_scope(db),
        )
        contract = db.get(Contract, result["id"])
        relation = db.query(ContractRoom).filter(ContractRoom.contract_id == contract.id).one()
        room = db.get(Room, relation.room_id)
        assert contract.building_id == building.id
        assert relation.building_id == building.id
        assert relation.room_id is not None
        assert room is not None
        assert (room.building_id, room.room_no, room.tower) == (
            building.id,
            "DW-NOT-IN-LEDGER",
            "南塔",
        )
        assert relation.room_no == "DW-NOT-IN-LEDGER"
    finally:
        db.close()


def test_follow_up_attachment_and_logs_freeze_their_scope() -> None:
    db = SessionLocal()
    try:
        building = _building(db)
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        customer = Customer(name="双写客户", status="active")
        db.add(customer)
        db.commit()

        follow_up_result = create_customer_follow_up(
            db,
            customer,
            CustomerFollowUpCreate(content="确认新楼宇范围"),
            "tester",
        )
        follow_up = db.get(CustomerFollowUp, follow_up_result["id"])
        assert follow_up.building_id == building.id
        follow_log = (
            db.query(OperationLog)
            .filter(OperationLog.action == "customer_followup_create")
            .one()
        )
        assert (follow_log.scope_type, follow_log.building_id) == (
            "building",
            building.id,
        )

        upload = UploadFile(
            file=io.BytesIO(b"scope-test"),
            filename="scope.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        attachment_result = create_attachment(
            db,
            scope=_edit_scope(db),
            entity_type="customer",
            entity_id=customer.id,
            upload=upload,
            category="证照",
            notes="",
            operator="tester",
        )
        attachment = db.get(Attachment, attachment_result["id"])
        assert (attachment.scope_type, attachment.building_id, attachment.portfolio_id) == (
            "building",
            building.id,
            None,
        )

        attachment.scope_type = None
        attachment.building_id = None
        db.commit()
        with pytest.raises(HTTPException) as unresolved_attachment:
            update_attachment(
                db,
                attachment,
                AttachmentUpdate(notes="不可修改待分类附件"),
                "tester",
                scope=_edit_scope(db),
            )
        assert unresolved_attachment.value.status_code == 409
        db.rollback()
        assert (attachment.scope_type, attachment.portfolio_id, attachment.building_id) == (
            None,
            None,
            None,
        )
        assert (
            db.query(OperationLog)
            .filter(OperationLog.action == "attachment_update", OperationLog.target_id == attachment.id)
            .count()
            == 0
        )

        # 旧入口不再接受板块级历史附件，避免附件在不同楼宇间共享。
        attachment.scope_type = "portfolio"
        attachment.portfolio_id = portfolio.id
        attachment.building_id = None
        db.commit()
        with pytest.raises(HTTPException) as portfolio_attachment:
            update_attachment(
                db,
                attachment,
                AttachmentUpdate(notes="不可修改板块附件"),
                "tester",
                scope=_edit_scope(db),
            )
        assert portfolio_attachment.value.status_code == 409
        db.rollback()
        assert (attachment.scope_type, attachment.portfolio_id, attachment.building_id) == (
            "portfolio",
            portfolio.id,
            None,
        )
        with pytest.raises(HTTPException) as portfolio_delete:
            delete_attachment(db, attachment, "tester", scope=_edit_scope(db))
        assert portfolio_delete.value.status_code == 409
        db.rollback()
        assert (
            db.query(OperationLog)
            .filter(OperationLog.action == "attachment_delete", OperationLog.target_id == attachment.id)
            .count()
            == 0
        )

        log_action(db, None, "export", "", "tester", target_type="customer")
        customer_log = (
            db.query(OperationLog)
            .filter(OperationLog.action == "export", OperationLog.target_type == "customer")
            .one()
        )
        assert (customer_log.scope_type, customer_log.portfolio_id, customer_log.building_id) == (
            "portfolio",
            portfolio.id,
            None,
        )

        log_action(db, None, "download", "", "tester", target_type="backup")
        global_log = (
            db.query(OperationLog)
            .filter(OperationLog.action == "download", OperationLog.target_type == "backup")
            .one()
        )
        assert (global_log.scope_type, global_log.portfolio_id, global_log.building_id) == (
            "global",
            None,
            None,
        )
    finally:
        db.close()


def test_operation_log_uses_exact_object_scope_and_database_rejects_unresolved_contract() -> None:
    db = SessionLocal()
    try:
        building = _building(db)
        result = create_contract(
            db,
            _contract_payload("DW-C-LOG", "DW-LOG-ROOM"),
            "tester",
            scope=_edit_scope(db),
        )
        contract = db.get(Contract, result["id"])

        log_action(
            db,
            None,
            "contract_export",
            "",
            "tester",
            target_type="contract",
            target_id=contract.id,
        )
        exact_log = (
            db.query(OperationLog)
            .filter(OperationLog.action == "contract_export")
            .one()
        )
        assert (exact_log.scope_type, exact_log.building_id, exact_log.portfolio_id) == (
            "building",
            building.id,
            None,
        )

        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        shared_attachment = Attachment(
            scope_type="portfolio",
            portfolio_id=portfolio.id,
            building_id=None,
            entity_type="contract",
            entity_id=contract.id,
            file_name="shared.txt",
            storage_path="contract/shared-log-scope.txt",
        )
        db.add(shared_attachment)
        db.commit()
        log_action(
            db,
            contract.id,
            "attachment_download",
            "",
            "tester",
            target_type="attachment",
            target_id=shared_attachment.id,
        )
        attachment_log = (
            db.query(OperationLog)
            .filter(OperationLog.action == "attachment_download")
            .one()
        )
        assert (attachment_log.scope_type, attachment_log.portfolio_id, attachment_log.building_id) == (
            "portfolio",
            portfolio.id,
            None,
        )

        with pytest.raises(HTTPException) as unknown_target:
            log_action(db, None, "bad", "", "tester", target_type="unknown_collection")
        assert unknown_target.value.status_code == 400
        db.rollback()

        contract.building_id = None
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.refresh(contract)
        assert contract.building_id == building.id
        assert db.query(OperationLog).filter(OperationLog.action == "bad").count() == 0
    finally:
        db.close()


def test_invalid_room_tower_stops_before_room_insert() -> None:
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc_info:
            create_room(
                db,
                {"tower": "未知楼塔", "floor": "1", "room_no": "DW-R-BAD", "area": 10},
            )
        assert exc_info.value.status_code == 400
        db.rollback()
        assert db.query(Room).filter(Room.room_no == "DW-R-BAD").count() == 0
    finally:
        db.close()


def test_new_legacy_users_receive_equivalent_demo_a_access_in_same_request() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "scope_admin", "password": "Admin123", "display_name": "范围管理员"},
        )
        assert response.status_code == 200
        login = client.post(
            "/api/auth/login",
            json={"username": "scope_admin", "password": "Admin123"},
        )
        assert login.status_code == 200
        response = client.post(
            "/api/auth/create-user",
            json={
                "username": "scope_viewer",
                "password": "Viewer123",
                "display_name": "范围查看人",
                "role": "viewer",
            },
        )
        assert response.status_code == 200

    db = SessionLocal()
    try:
        building = _building(db)
        admin = db.query(User).filter(User.username == "scope_admin").one()
        viewer = db.query(User).filter(User.username == "scope_viewer").one()
        admin_grant = db.query(UserScopeGrant).filter(UserScopeGrant.user_id == admin.id).one()
        viewer_grant = db.query(UserScopeGrant).filter(UserScopeGrant.user_id == viewer.id).one()
        assert (
            admin_grant.scope_type,
            admin_grant.building_id,
            admin_grant.access_level,
            admin_grant.granted_by,
        ) == ("building", building.id, "edit", None)
        assert (
            viewer_grant.scope_type,
            viewer_grant.building_id,
            viewer_grant.access_level,
            viewer_grant.granted_by,
        ) == ("building", building.id, "view", admin.id)
    finally:
        db.close()
