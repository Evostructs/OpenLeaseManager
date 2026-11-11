from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Attachment, Building, Contract, OperationLog, User, UserScopeGrant


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
    assert response.status_code == 200


def _login_as(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _contract_payload(contract_no: str, room_no: str, customer_id: int | None = None) -> dict:
    return {
        "contract_no": contract_no,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": "附件测试客户",
        "customer_id": customer_id,
        "area": 100,
        "unit_price": 4,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }


def _upload_attachment(
    client: TestClient,
    *,
    entity_type: str,
    entity_id: int,
    filename: str,
    content: bytes,
    category: str,
    notes: str = "",
    building: str | None = None,
):
    return client.post(
        "/api/attachments",
        params={"building": building} if building else None,
        data={"entity_type": entity_type, "entity_id": str(entity_id), "category": category, "notes": notes},
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_attachment_crud_contract_customer_details_download_and_logs() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "附件主档客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-ATTACH-001", "ATT-101", customer["id"]),
        ).json()

        contract_detail = client.get(f"/api/contracts/{contract['id']}").json()
        assert contract_detail["attachment_summary"]["missing_required"] == ["合同正文"]
        customer_detail = client.get(f"/api/customers/{customer['id']}").json()
        assert customer_detail["attachment_summary"]["missing_required"] == ["证照"]

        uploaded_contract = _upload_attachment(
            client,
            entity_type="contract",
            entity_id=contract["id"],
            filename="../contract-main.pdf",
            content=b"contract attachment bytes",
            category="合同正文",
            notes="signed copy",
        )
        assert uploaded_contract.status_code == 200
        contract_attachment = uploaded_contract.json()
        assert contract_attachment["file_name"] == "contract-main.pdf"
        assert contract_attachment["category"] == "合同正文"
        assert contract_attachment["notes"] == "signed copy"
        assert contract_attachment["file_size"] == len(b"contract attachment bytes")

        contract_list = client.get(
            "/api/attachments",
            params={"entity_type": "contract", "entity_id": contract["id"]},
        )
        assert contract_list.status_code == 200
        assert [item["id"] for item in contract_list.json()] == [contract_attachment["id"]]

        contract_detail = client.get(f"/api/contracts/{contract['id']}").json()
        assert contract_detail["attachments"][0]["id"] == contract_attachment["id"]
        assert contract_detail["attachment_summary"]["missing_required"] == []
        assert contract_detail["attachment_summary"]["categories"]["合同正文"] == 1

        downloaded = client.get(f"/api/attachments/{contract_attachment['id']}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == b"contract attachment bytes"

        edited = client.patch(
            f"/api/attachments/{contract_attachment['id']}",
            json={"category": "补充协议", "notes": "updated note"},
        )
        assert edited.status_code == 200
        assert edited.json()["category"] == "补充协议"
        assert edited.json()["notes"] == "updated note"

        uploaded_customer = _upload_attachment(
            client,
            entity_type="customer",
            entity_id=customer["id"],
            filename="business-license.png",
            content=b"license bytes",
            category="证照",
        )
        assert uploaded_customer.status_code == 200
        customer_detail = client.get(f"/api/customers/{customer['id']}").json()
        assert customer_detail["attachments"][0]["id"] == uploaded_customer.json()["id"]
        assert customer_detail["attachment_summary"]["missing_required"] == []
        assert customer_detail["attachment_summary"]["categories"]["证照"] == 1

        db = SessionLocal()
        try:
            stored = db.query(Attachment).filter(Attachment.id == contract_attachment["id"]).one()
            assert stored.storage_path.startswith(f"contract/{contract['id']}/")
            assert ".." not in stored.storage_path
            stored_path = get_settings().attachment_dir / stored.storage_path
            assert stored_path.exists()
            log_actions = {row.action for row in db.query(OperationLog).all()}
        finally:
            db.close()
        assert {"attachment_upload", "attachment_update"} <= log_actions

        deleted = client.delete(f"/api/attachments/{contract_attachment['id']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/attachments/{contract_attachment['id']}/download").status_code == 404
        assert client.get(
            "/api/attachments",
            params={"entity_type": "contract", "entity_id": contract["id"]},
        ).json() == []

        db = SessionLocal()
        try:
            stored = db.query(Attachment).filter(Attachment.id == contract_attachment["id"]).one()
            assert stored.deleted_at is not None
            assert (get_settings().attachment_dir / stored.storage_path).exists()
            log_actions = {row.action for row in db.query(OperationLog).all()}
        finally:
            db.close()
        assert "attachment_delete" in log_actions


def test_attachment_download_rejects_registered_unsafe_storage_path() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "附件路径客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-ATTACH-PATH", "ATT-201", customer["id"]),
        ).json()

        db = SessionLocal()
        try:
            building = db.query(Building).filter(Building.code == "DEMO_A").one()
            attachment = Attachment(
                scope_type="building",
                building_id=building.id,
                portfolio_id=None,
                entity_type="contract",
                entity_id=contract["id"],
                file_name="escape.txt",
                storage_path="../escape.txt",
                content_type="text/plain",
                file_size=4,
                category="其他",
            )
            db.add(attachment)
            db.commit()
            attachment_id = attachment.id
        finally:
            db.close()

        response = client.get(f"/api/attachments/{attachment_id}/download")
        assert response.status_code == 400


def test_attachment_completeness_tasks_follow_upload_and_soft_delete() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "附件任务客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-ATTACH-TASK", "ATT-301", customer["id"]),
        ).json()

        before = client.get("/api/attachments/completeness-tasks")
        assert before.status_code == 200
        data = before.json()
        task_ids = {item["id"] for item in data["items"]}
        assert f"attachment:contract:{contract['id']}:body" in task_ids
        assert f"attachment:customer:{customer['id']}:DEMO_A:license" in task_ids
        contract_task = next(item for item in data["items"] if item["target_type"] == "contract")
        assert contract_task["contract_id"] == contract["id"]
        assert contract_task["customer_id"] == customer["id"]
        assert contract_task["missing_category"] == "合同正文"
        assert contract_task["action_target"] == {"type": "contract", "contract_id": contract["id"], "section": "attachments"}
        assert data["summary"]["by_target_type"]["contract"] >= 1

        db = SessionLocal()
        try:
            log_count = db.query(OperationLog).count()
        finally:
            db.close()

        viewer = client.post(
            "/api/auth/create-user",
            json={"username": "viewer_attach", "password": "Viewer123", "display_name": "Viewer", "role": "viewer"},
        )
        assert viewer.status_code == 200
        with _client() as viewer_client:
            _login_as(viewer_client, "viewer_attach", "Viewer123")
            viewer_response = viewer_client.get("/api/attachments/completeness-tasks")
            assert viewer_response.status_code == 200
            assert f"attachment:contract:{contract['id']}:body" in {item["id"] for item in viewer_response.json()["items"]}

        db = SessionLocal()
        try:
            assert db.query(OperationLog).count() == log_count
        finally:
            db.close()

        uploaded = _upload_attachment(
            client,
            entity_type="contract",
            entity_id=contract["id"],
            filename="signed-contract.pdf",
            content=b"signed body",
            category="合同正文",
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["id"]
        after_upload = client.get("/api/attachments/completeness-tasks", params={"target_type": "contract"}).json()
        assert f"attachment:contract:{contract['id']}:body" not in {item["id"] for item in after_upload["items"]}

        deleted = client.delete(f"/api/attachments/{attachment_id}")
        assert deleted.status_code == 200
        after_delete = client.get("/api/attachments/completeness-tasks", params={"target_type": "contract"}).json()
        assert f"attachment:contract:{contract['id']}:body" in {item["id"] for item in after_delete["items"]}


def test_attachment_scope_is_explicit_and_direct_ids_cannot_cross_buildings() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)

        customer = client.post("/api/customers", json={"name": "附件跨楼客户"}).json()
        demo_a_contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-ATTACH-SHARED", "ATT-DEMO_A-501", customer["id"]),
        ).json()

        db = SessionLocal()
        try:
            other = db.query(Building).filter(Building.code == "ATTACH_B").first()
            if other is None:
                other = Building(code="ATTACH_B", name="附件范围乙楼", is_active=True)
                db.add(other)
                db.flush()
            admin = db.query(User).filter(User.username == "admin").one()
            db.add(
                UserScopeGrant(
                    user_id=admin.id,
                    scope_type="building",
                    building_id=other.id,
                    portfolio_id=None,
                    access_level="edit",
                    include_future_buildings=False,
                    valid_from=date(2026, 1, 1),
                )
            )
            other_contract = Contract(
                contract_no="ATTACH-B-001",
                customer_id=customer["id"],
                building_id=other.id,
                tower="乙塔",
                room_no="B-501",
                tenant_name="附件跨楼客户",
                area=100,
                unit_price=4,
                start_date=date(2026, 1, 1),
                end_date=date(2027, 12, 31),
                is_deleted=False,
            )
            db.add(other_contract)
            db.commit()
            other_building_id = other.id
            other_contract_id = other_contract.id
        finally:
            db.close()

        uploaded = _upload_attachment(
            client,
            entity_type="contract",
            entity_id=other_contract_id,
            filename="other-building.txt",
            content=b"other building attachment",
            category="其他",
            building="ATTACH_B",
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["id"]

        default_list = client.get(
            "/api/attachments",
            params={"entity_type": "contract", "entity_id": other_contract_id},
        )
        assert default_list.status_code == 404
        assert client.get(f"/api/attachments/{attachment_id}/download").status_code == 404
        assert client.patch(f"/api/attachments/{attachment_id}", json={"notes": "越权修改"}).status_code == 404
        assert client.delete(f"/api/attachments/{attachment_id}").status_code == 404

        selected_list = client.get(
            "/api/attachments",
            params={"entity_type": "contract", "entity_id": other_contract_id, "building": "ATTACH_B"},
        )
        assert selected_list.status_code == 200
        assert [item["id"] for item in selected_list.json()] == [attachment_id]
        selected_download = client.get(
            f"/api/attachments/{attachment_id}/download",
            params={"building": "ATTACH_B"},
        )
        assert selected_download.status_code == 200
        assert selected_download.content == b"other building attachment"
        selected_edit = client.patch(
            f"/api/attachments/{attachment_id}",
            params={"building": "ATTACH_B"},
            json={"notes": "乙楼已核验"},
        )
        assert selected_edit.status_code == 200
        assert selected_edit.json()["notes"] == "乙楼已核验"

        customer_upload = _upload_attachment(
            client,
            entity_type="customer",
            entity_id=customer["id"],
            filename="other-customer.txt",
            content=b"other customer attachment",
            category="其他",
            building="ATTACH_B",
        )
        assert customer_upload.status_code == 200
        customer_attachment_id = customer_upload.json()["id"]
        default_customer_list = client.get(
            "/api/attachments",
            params={"entity_type": "customer", "entity_id": customer["id"]},
        )
        assert default_customer_list.status_code == 200
        assert customer_attachment_id not in {item["id"] for item in default_customer_list.json()}
        other_customer_list = client.get(
            "/api/attachments",
            params={"entity_type": "customer", "entity_id": customer["id"], "building": "ATTACH_B"},
        )
        assert [item["id"] for item in other_customer_list.json()] == [customer_attachment_id]

        default_tasks = client.get("/api/attachments/completeness-tasks").json()
        default_task_ids = {item["id"] for item in default_tasks["items"]}
        assert f"attachment:contract:{other_contract_id}:body" not in default_task_ids
        assert f"attachment:customer:{customer['id']}:ATTACH_B:license" not in default_task_ids
        assert f"attachment:contract:{demo_a_contract['id']}:body" in default_task_ids

        other_tasks = client.get(
            "/api/attachments/completeness-tasks",
            params={"building": "ATTACH_B"},
        ).json()
        other_task_ids = {item["id"] for item in other_tasks["items"]}
        assert f"attachment:contract:{other_contract_id}:body" in other_task_ids
        assert f"attachment:customer:{customer['id']}:ATTACH_B:license" in other_task_ids
        hidden_contract = client.get(
            "/api/attachments/completeness-tasks",
            params={"contract_id": other_contract_id},
        ).json()
        assert hidden_contract["total"] == 0
        assert hidden_contract["items"] == []

        db = SessionLocal()
        try:
            stored = db.get(Attachment, attachment_id)
            assert (stored.scope_type, stored.building_id, stored.portfolio_id) == (
                "building",
                other_building_id,
                None,
            )
            logs = (
                db.query(OperationLog)
                .filter(OperationLog.target_type == "attachment", OperationLog.target_id == attachment_id)
                .all()
            )
            assert {row.action for row in logs} >= {"attachment_upload", "download", "attachment_update"}
            assert all((row.scope_type, row.building_id, row.portfolio_id) == ("building", other_building_id, None) for row in logs)
        finally:
            db.close()

        deleted = client.delete(
            f"/api/attachments/{attachment_id}",
            params={"building": "ATTACH_B"},
        )
        assert deleted.status_code == 200


def test_unscoped_attachment_fails_closed_without_touching_file_path() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer = client.post("/api/customers", json={"name": "附件待归属客户"}).json()
        contract = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-ATTACH-UNSCOPED", "ATT-601", customer["id"]),
        ).json()

        db = SessionLocal()
        try:
            attachment = Attachment(
                scope_type=None,
                building_id=None,
                portfolio_id=None,
                entity_type="contract",
                entity_id=contract["id"],
                file_name="legacy.txt",
                storage_path="contract/legacy-unscoped.txt",
                content_type="text/plain",
                file_size=1,
                category="其他",
            )
            db.add(attachment)
            db.commit()
            attachment_id = attachment.id
        finally:
            db.close()

        listed = client.get(
            "/api/attachments",
            params={"entity_type": "contract", "entity_id": contract["id"]},
        )
        assert listed.status_code == 200
        assert attachment_id not in {item["id"] for item in listed.json()}
        assert client.get(f"/api/attachments/{attachment_id}/download").status_code == 409
        assert client.patch(f"/api/attachments/{attachment_id}", json={"notes": "不可补写"}).status_code == 409
        assert client.delete(f"/api/attachments/{attachment_id}").status_code == 409
