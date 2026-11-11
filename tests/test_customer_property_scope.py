from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    Building,
    Contract,
    CustomerBuildingProfile,
    CustomerFollowUp,
    User,
    UserScopeGrant,
)


OTHER_BUILDING_CODE = "CUSTOMER_SCOPE_B"


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient, username: str = "admin", password: str = "Admin123") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _grant_other_building_to_admin() -> tuple[int, int]:
    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = db.query(Building).filter(Building.code == OTHER_BUILDING_CODE).one_or_none()
        if other is None:
            other = Building(code=OTHER_BUILDING_CODE, name="客户范围乙楼", is_active=True)
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
                valid_from=today_in_timezone(),
                granted_by=admin.id,
            )
        )
        db.commit()
        return int(demo_a.id), int(other.id)
    finally:
        db.close()


def _seed_shared_customer_scope_rows(
    customer_id: int,
    demo_a_building_id: int,
    other_building_id: int,
) -> dict[str, int]:
    today = today_in_timezone()
    now = now_in_timezone()
    db = SessionLocal()
    try:
        demo_a_contract = Contract(
            contract_no="CUST-SCOPE-A-001",
            customer_id=customer_id,
            building_id=demo_a_building_id,
            tower="南塔",
            room_no="A-501",
            tenant_name="跨楼共享客户",
            area=100,
            unit_price=4,
            monthly_rent=12000,
            start_date=today - timedelta(days=60),
            end_date=today + timedelta(days=45),
            is_deleted=False,
        )
        other_contract = Contract(
            contract_no="CUST-SCOPE-B-001",
            customer_id=customer_id,
            building_id=other_building_id,
            tower="乙塔",
            room_no="B-801",
            tenant_name="跨楼共享客户",
            area=250,
            unit_price=6,
            monthly_rent=30000,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=75),
            is_deleted=False,
        )
        db.add_all([demo_a_contract, other_contract])
        db.flush()

        demo_a_follow_up = CustomerFollowUp(
            customer_id=customer_id,
            contract_id=demo_a_contract.id,
            building_id=demo_a_building_id,
            follow_up_type="电话",
            occurred_at=now - timedelta(days=2),
            content="DEMO_A 跟进内容",
            next_action="DEMO_A 下一步",
            next_follow_up_date=today - timedelta(days=2),
            owner="Alice",
            status="open",
        )
        other_follow_up = CustomerFollowUp(
            customer_id=customer_id,
            contract_id=other_contract.id,
            building_id=other_building_id,
            follow_up_type="拜访",
            occurred_at=now - timedelta(days=1),
            content="乙楼跟进内容",
            next_action="乙楼下一步",
            next_follow_up_date=today - timedelta(days=1),
            owner="Bob",
            status="open",
        )
        db.add_all([demo_a_follow_up, other_follow_up])
        db.flush()

        db.add_all(
            [
                Attachment(
                    scope_type="building",
                    portfolio_id=None,
                    building_id=demo_a_building_id,
                    entity_type="customer",
                    entity_id=customer_id,
                    file_name="demo_a-license.txt",
                    storage_path=f"customer-scope/{customer_id}/demo_a-license.txt",
                    content_type="text/plain",
                    file_size=10,
                    category="证照",
                ),
                Attachment(
                    scope_type="building",
                    portfolio_id=None,
                    building_id=other_building_id,
                    entity_type="customer",
                    entity_id=customer_id,
                    file_name="other-license.txt",
                    storage_path=f"customer-scope/{customer_id}/other-license.txt",
                    content_type="text/plain",
                    file_size=20,
                    category="证照",
                ),
            ]
        )
        db.commit()
        return {
            "demo_a_contract_id": int(demo_a_contract.id),
            "other_contract_id": int(other_contract.id),
            "demo_a_follow_up_id": int(demo_a_follow_up.id),
            "other_follow_up_id": int(other_follow_up.id),
        }
    finally:
        db.close()


def test_customer_list_detail_summary_attachments_risks_and_due_are_selected_scope_only() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, other_building_id = _grant_other_building_to_admin()

        created = client.post("/api/customers", json={"name": "跨楼共享客户"})
        assert created.status_code == 200
        customer_id = created.json()["id"]
        ids = _seed_shared_customer_scope_rows(customer_id, demo_a_building_id, other_building_id)

        default_list = client.get("/api/customers", params={"keyword": "跨楼共享客户"})
        explicit_demo_a_list = client.get(
            "/api/customers",
            params={"keyword": "跨楼共享客户", "building": "DEMO_A"},
        )
        other_list = client.get(
            "/api/customers",
            params={"keyword": "跨楼共享客户", "building": OTHER_BUILDING_CODE},
        )
        assert default_list.status_code == explicit_demo_a_list.status_code == other_list.status_code == 200
        assert default_list.json() == explicit_demo_a_list.json()
        assert default_list.json()["items"][0]["contract_count"] == 1
        assert other_list.json()["items"][0]["contract_count"] == 1

        default_detail = client.get(f"/api/customers/{customer_id}")
        demo_a_detail = client.get(f"/api/customers/{customer_id}", params={"building": "DEMO_A"})
        other_detail = client.get(
            f"/api/customers/{customer_id}",
            params={"building": OTHER_BUILDING_CODE},
        )
        assert default_detail.status_code == demo_a_detail.status_code == other_detail.status_code == 200
        assert default_detail.json() == demo_a_detail.json()

        demo_a_payload = demo_a_detail.json()
        other_payload = other_detail.json()
        assert [row["id"] for row in demo_a_payload["contracts"]] == [ids["demo_a_contract_id"]]
        assert [row["id"] for row in other_payload["contracts"]] == [ids["other_contract_id"]]
        assert [row["id"] for row in demo_a_payload["follow_ups"]] == [ids["demo_a_follow_up_id"]]
        assert [row["id"] for row in other_payload["follow_ups"]] == [ids["other_follow_up_id"]]
        assert demo_a_payload["operating_summary"]["contract_count"] == 1
        assert other_payload["operating_summary"]["contract_count"] == 1
        assert demo_a_payload["operating_summary"]["current_leased_area"] == 100
        assert other_payload["operating_summary"]["current_leased_area"] == 250
        assert demo_a_payload["operating_summary"]["next_follow_up_date"] == (
            today_in_timezone() - timedelta(days=2)
        ).isoformat()
        assert other_payload["operating_summary"]["next_follow_up_date"] == (
            today_in_timezone() - timedelta(days=1)
        ).isoformat()
        assert [row["file_name"] for row in demo_a_payload["attachments"]] == ["demo_a-license.txt"]
        assert [row["file_name"] for row in other_payload["attachments"]] == ["other-license.txt"]

        demo_a_risk_keys = {row["stable_key"] for row in demo_a_payload["risks"]}
        other_risk_keys = {row["stable_key"] for row in other_payload["risks"]}
        assert f"follow_up_overdue:{ids['demo_a_follow_up_id']}" in demo_a_risk_keys
        assert f"follow_up_overdue:{ids['other_follow_up_id']}" not in demo_a_risk_keys
        assert f"follow_up_overdue:{ids['other_follow_up_id']}" in other_risk_keys
        assert f"follow_up_overdue:{ids['demo_a_follow_up_id']}" not in other_risk_keys

        default_due = client.get("/api/customers/follow-ups/due")
        demo_a_due = client.get("/api/customers/follow-ups/due", params={"building": "DEMO_A"})
        other_due = client.get(
            "/api/customers/follow-ups/due",
            params={"building": OTHER_BUILDING_CODE},
        )
        assert default_due.status_code == demo_a_due.status_code == other_due.status_code == 200
        assert default_due.json() == demo_a_due.json()
        assert default_due.json()["items"][0]["follow_up_id"] == ids["demo_a_follow_up_id"]
        assert other_due.json()["items"][0]["follow_up_id"] == ids["other_follow_up_id"]


def test_customer_create_and_unlinked_follow_up_create_building_profile() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        _demo_a_building_id, other_building_id = _grant_other_building_to_admin()

        created = client.post(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE},
            json={"name": "乙楼独有客户"},
        )
        assert created.status_code == 200
        customer_id = created.json()["id"]

        assert client.get(f"/api/customers/{customer_id}").status_code == 404
        selected_detail = client.get(
            f"/api/customers/{customer_id}",
            params={"building": OTHER_BUILDING_CODE},
        )
        assert selected_detail.status_code == 200
        assert selected_detail.json()["contracts"] == []

        follow_up = client.post(
            f"/api/customers/{customer_id}/follow-ups",
            params={"building": OTHER_BUILDING_CODE},
            json={
                "content": "无合同客户首次拜访",
                "follow_up_type": "拜访",
                "next_follow_up_date": today_in_timezone().isoformat(),
                "owner": "Carol",
            },
        )
        assert follow_up.status_code == 200
        follow_up_id = follow_up.json()["id"]
        assert follow_up.json()["contract_id"] is None

        db = SessionLocal()
        try:
            profiles = (
                db.query(CustomerBuildingProfile)
                .filter(
                    CustomerBuildingProfile.customer_id == customer_id,
                    CustomerBuildingProfile.building_id == other_building_id,
                )
                .all()
            )
            stored_follow_up = db.get(CustomerFollowUp, follow_up_id)
            assert len(profiles) == 1
            assert stored_follow_up is not None
            assert stored_follow_up.building_id == other_building_id
            assert stored_follow_up.contract_id is None
        finally:
            db.close()

        listed_demo_a = client.get("/api/customers", params={"keyword": "乙楼独有客户"})
        listed_other = client.get(
            "/api/customers",
            params={"keyword": "乙楼独有客户", "building": OTHER_BUILDING_CODE},
        )
        assert listed_demo_a.status_code == listed_other.status_code == 200
        assert listed_demo_a.json()["total"] == 0
        assert listed_other.json()["total"] == 1


def test_authorized_other_building_follow_up_ids_remain_hidden_when_demo_a_is_selected() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, other_building_id = _grant_other_building_to_admin()
        customer = client.post("/api/customers", json={"name": "跟进对象隔离客户"}).json()
        ids = _seed_shared_customer_scope_rows(customer["id"], demo_a_building_id, other_building_id)

        update_response = client.put(
            f"/api/customers/{customer['id']}/follow-ups/{ids['other_follow_up_id']}",
            params={"building": "DEMO_A"},
            json={"content": "不应允许的跨楼修改"},
        )
        complete_response = client.post(
            f"/api/customers/{customer['id']}/follow-ups/{ids['other_follow_up_id']}/complete",
            params={"building": "DEMO_A"},
        )
        delete_response = client.delete(
            f"/api/customers/{customer['id']}/follow-ups/{ids['other_follow_up_id']}",
            params={"building": "DEMO_A"},
        )
        linked_create_response = client.post(
            f"/api/customers/{customer['id']}/follow-ups",
            params={"building": "DEMO_A"},
            json={"contract_id": ids["other_contract_id"], "content": "不应允许关联乙楼合同"},
        )
        assert update_response.status_code == 404
        assert complete_response.status_code == 404
        assert delete_response.status_code == 404
        assert linked_create_response.status_code == 404

        other_detail = client.get(
            f"/api/customers/{customer['id']}",
            params={"building": OTHER_BUILDING_CODE},
        )
        assert other_detail.status_code == 200
        stored = other_detail.json()["follow_ups"][0]
        assert stored["id"] == ids["other_follow_up_id"]
        assert stored["content"] == "乙楼跟进内容"
        assert stored["status"] == "open"

        legitimate_update = client.put(
            f"/api/customers/{customer['id']}/follow-ups/{ids['other_follow_up_id']}",
            params={"building": OTHER_BUILDING_CODE},
            json={"content": "乙楼范围内正常修改"},
        )
        assert legitimate_update.status_code == 200
        assert legitimate_update.json()["content"] == "乙楼范围内正常修改"


def test_customer_master_capability_is_required_only_for_shared_master_writes() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer = client.post("/api/customers", json={"name": "主档权限边界客户"})
        assert customer.status_code == 200
        customer_id = customer.json()["id"]

        created_user = client.post(
            "/api/auth/create-user",
            json={
                "username": "scope_editor",
                "password": "Editor123",
                "display_name": "Scope Editor",
                "role": "editor",
            },
        )
        assert created_user.status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        _login(client, "scope_editor", "Editor123")

        assert client.get(f"/api/customers/{customer_id}").status_code == 200
        assert client.post("/api/customers", json={"name": "无主档权限新客户"}).status_code == 403
        assert client.put(
            f"/api/customers/{customer_id}",
            json={"short_name": "不应修改"},
        ).status_code == 403

        follow_up = client.post(
            f"/api/customers/{customer_id}/follow-ups",
            json={"content": "楼宇内日常跟进仍可编辑"},
        )
        assert follow_up.status_code == 200
        assert follow_up.json()["contract_id"] is None
