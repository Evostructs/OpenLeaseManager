from __future__ import annotations

from datetime import date
import io

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.crypto import encrypted_name, encrypt_stream_to_file
from app.database import SessionLocal
from app.main import app
from app.models import Building, Contract, ContractRoom, Room, User, UserScopeGrant, Zone
from app.services.contract_files import _scoped_unmatched_name


def _contract_payload(contract_no: str, room_no: str) -> dict:
    return {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"客户-{contract_no}",
        "area": 100,
        "unit_price": 4,
        "start_date": "2026-01-01",
        "end_date": "2027-12-31",
    }


def _login_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200


def _seed_other_building_contract() -> tuple[int, int]:
    settings = get_settings()
    public_pdf_name = "CONTRACT-B-001.pdf"
    stored_pdf_name = encrypted_name(public_pdf_name) if settings.data_encryption_enabled else public_pdf_name
    db = SessionLocal()
    try:
        building = db.query(Building).filter(Building.code == "CONTRACT_B").first()
        if building is None:
            building = Building(code="CONTRACT_B", name="合同范围乙楼", is_active=True, ingestion_mode="detail")
            db.add(building)
            db.flush()
        admin = db.query(User).filter(User.username == "admin").one()
        db.add(
            UserScopeGrant(
                user_id=admin.id,
                scope_type="building",
                building_id=building.id,
                portfolio_id=None,
                access_level="edit",
                include_future_buildings=False,
                valid_from=date(2026, 1, 1),
            )
        )
        zone = Zone(
            building_id=building.id,
            code="CONTRACT_B_OFFICE",
            name="乙楼办公",
            zone_type="office",
            valid_from=date(2026, 1, 1),
            is_active=True,
        )
        db.add(zone)
        db.flush()
        room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="乙塔",
            floor="12",
            room_no="B-1201",
            room_type="办公",
            area=100,
            is_leasable=True,
        )
        db.add(room)
        db.flush()
        contract = Contract(
            contract_no="CONTRACT-B-001",
            building_id=building.id,
            tower="乙塔",
            room_no="B-1201",
            tenant_name="乙楼合同客户",
            area=100,
            unit_price=4,
            monthly_rent=12166.67,
            start_date=date(2026, 1, 1),
            end_date=date(2027, 12, 31),
            pdf_path=stored_pdf_name,
            is_deleted=False,
        )
        contract.room_items.append(
            ContractRoom(
                building_id=building.id,
                room_id=room.id,
                tower="乙塔",
                room_no="B-1201",
                area=100,
                unit_price=4,
                monthly_rent=12166.67,
                start_date=date(2026, 1, 1),
                end_date=date(2027, 12, 31),
            )
        )
        db.add(contract)
        db.commit()
        contract_id = contract.id
        building_id = building.id
    finally:
        db.close()
    pdf_bytes = b"%PDF-1.4\nB building\n%%EOF"
    pdf_path = settings.upload_dir / stored_pdf_name
    if settings.data_encryption_enabled:
        encrypt_stream_to_file(io.BytesIO(pdf_bytes), pdf_path, settings.data_key)
    else:
        pdf_path.write_bytes(pdf_bytes)
    return contract_id, building_id


def test_contract_reads_files_and_batches_use_only_selected_building() -> None:
    with TestClient(app) as client:
        _login_admin(client)
        demo_a = client.post("/api/contracts", json=_contract_payload("DEMO_A-SCOPE-001", "SCOPE-1201"))
        assert demo_a.status_code == 200, demo_a.text
        demo_a_id = demo_a.json()["id"]
        other_id, other_building_id = _seed_other_building_contract()

        default_list = client.get("/api/contracts", params={"page_size": 100}).json()
        assert demo_a_id in {item["id"] for item in default_list["items"]}
        assert other_id not in {item["id"] for item in default_list["items"]}
        other_list = client.get(
            "/api/contracts",
            params={"page_size": 100, "building": "CONTRACT_B"},
        ).json()
        assert {item["id"] for item in other_list["items"]} == {other_id}

        assert client.get(f"/api/contracts/{other_id}").status_code == 404
        assert client.get(f"/api/contracts/{other_id}/decision-card").status_code == 404
        assert client.get(f"/api/contracts/{other_id}/pdf").status_code == 404
        assert client.get(f"/api/contracts/{other_id}", params={"building": "CONTRACT_B"}).status_code == 200
        other_decision = client.get(
            f"/api/contracts/{other_id}/decision-card",
            params={"building": "CONTRACT_B"},
        )
        assert other_decision.status_code == 200
        assert other_decision.json()["rooms"][0]["tower"] == "乙塔"
        assert other_decision.json()["rooms"][0]["tower"] != "商业配套"
        other_pdf = client.get(f"/api/contracts/{other_id}/pdf", params={"building": "CONTRACT_B"})
        assert other_pdf.status_code == 200, other_pdf.text
        assert other_pdf.content.startswith(b"%PDF-1.4")

        other_create = client.post(
            "/api/contracts",
            params={"building": "CONTRACT_B"},
            json=_contract_payload("CONTRACT-B-WRITE", "B-1301"),
        )
        assert other_create.status_code == 400
        assert "房源" in other_create.json()["detail"]
        other_update = client.put(
            f"/api/contracts/{other_id}",
            params={"building": "CONTRACT_B"},
            json={"notes": "通用明细楼宇可按本楼范围更新"},
        )
        assert other_update.status_code == 200, other_update.text
        assert other_update.json()["notes"] == "通用明细楼宇可按本楼范围更新"

        db = SessionLocal()
        try:
            assert db.query(Room).filter(
                Room.building_id == other_building_id,
                Room.room_no == "B-1301",
            ).count() == 0
        finally:
            db.close()

        mixed_delete = client.post("/api/contracts/batch-delete", json=[demo_a_id, other_id])
        assert mixed_delete.status_code == 404
        assert client.get(f"/api/contracts/{demo_a_id}").status_code == 200
        missing_delete = client.post("/api/contracts/batch-delete", json=[demo_a_id, 999999])
        assert missing_delete.status_code == 404
        assert client.get(f"/api/contracts/{demo_a_id}").status_code == 200

        default_missing = client.get("/api/contracts/missing-pdfs").json()
        assert other_id not in {item["id"] for item in default_missing}
        other_missing = client.get(
            "/api/contracts/missing-pdfs",
            params={"building": "CONTRACT_B"},
        ).json()
        assert other_id not in {item["id"] for item in other_missing}

        upload_dir = get_settings().upload_dir
        (upload_dir / _scoped_unmatched_name("DEMO_A", "demo_a-only.pdf")).write_bytes(b"demo_a")
        (upload_dir / _scoped_unmatched_name("CONTRACT_B", "other-secret.pdf")).write_bytes(b"other")
        default_names = {item["filename"] for item in client.get("/api/contracts/unmatched-pdfs").json()}
        other_names = {
            item["filename"]
            for item in client.get(
                "/api/contracts/unmatched-pdfs",
                params={"building": "CONTRACT_B"},
            ).json()
        }
        assert default_names == {"demo_a-only.pdf"}
        assert other_names == {"other-secret.pdf"}

        cross_file = client.post(
            "/api/contracts/batch-upload-pdf",
            files=[("files", ("CONTRACT-B-001.pdf", b"%PDF-1.4\nnew\n%%EOF", "application/pdf"))],
        )
        assert cross_file.status_code == 200
        assert cross_file.json()["matched_count"] == 0
        assert cross_file.json()["unmatched"] == [{"filename": "CONTRACT-B-001.pdf"}]
        assert client.get(
            f"/api/contracts/{other_id}/pdf",
            params={"building": "CONTRACT_B"},
        ).content == b"%PDF-1.4\nB building\n%%EOF"
        assert "CONTRACT-B-001.pdf" in {
            item["filename"] for item in client.get("/api/contracts/unmatched-pdfs").json()
        }

        db = SessionLocal()
        try:
            demo_a_building = db.query(Building).filter(Building.code == "DEMO_A").one()
            room_item = db.query(ContractRoom).filter(ContractRoom.contract_id == other_id).one()
            room_item.building_id = demo_a_building.id
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        finally:
            db.close()
        consistent_item = client.get(
            f"/api/contracts/{other_id}",
            params={"building": "CONTRACT_B"},
        )
        assert consistent_item.status_code == 200
        assert client.get(
            "/api/contracts",
            params={"building": "CONTRACT_B", "page_size": 100},
        ).status_code == 200
        assert client.get("/api/contracts/stats", params={"building": "CONTRACT_B"}).status_code == 200

        db = SessionLocal()
        try:
            demo_a_building = db.query(Building).filter(Building.code == "DEMO_A").one()
            demo_a_zone = db.query(Zone).filter(Zone.building_id == demo_a_building.id).first()
            assert demo_a_zone is not None
            linked_room = Room(
                building_id=demo_a_building.id,
                zone_id=demo_a_zone.id,
                tower="南塔",
                floor="12",
                room_no="GRAPH-CROSS-ROOM",
                area=100,
                is_leasable=True,
                is_deleted=False,
            )
            db.add(linked_room)
            db.flush()
            room_item = db.query(ContractRoom).filter(ContractRoom.contract_id == other_id).one()
            room_item.room_id = linked_room.id
            room_item.room_no = linked_room.room_no
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        finally:
            db.close()
        still_consistent = client.get(
            f"/api/contracts/{other_id}",
            params={"building": "CONTRACT_B"},
        )
        assert still_consistent.status_code == 200
