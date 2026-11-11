from __future__ import annotations

from datetime import timedelta
import io

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    Customer,
    CustomerBuildingProfile,
    CustomerFollowUp,
    User,
    UserScopeGrant,
)


OTHER_BUILDING_CODE = "CUSTOMER_LINK_B"


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
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert response.status_code == 200


def _grant_other_building_and_seed_other_only_customer() -> tuple[int, int, int]:
    today = today_in_timezone()
    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = db.query(Building).filter(Building.code == OTHER_BUILDING_CODE).one_or_none()
        if other is None:
            other = Building(
                code=OTHER_BUILDING_CODE,
                name="客户关联安全乙楼",
                ingestion_mode="detail",
                is_active=True,
            )
            db.add(other)
            db.flush()

        admin = db.query(User).filter(User.username == "admin").one()
        db.add(
            UserScopeGrant(
                user_id=admin.id,
                scope_type="building",
                building_id=other.id,
                access_level="edit",
                include_future_buildings=False,
                valid_from=today,
                granted_by=admin.id,
            )
        )

        customer = Customer(name="仅乙楼有关联的全局客户", status="active")
        db.add(customer)
        db.flush()
        db.add(
            CustomerBuildingProfile(
                customer_id=customer.id,
                building_id=other.id,
                local_contact="乙楼联系人",
                local_notes="只允许在乙楼关联",
                owner="乙楼招商主管",
                valid_from=today,
            )
        )
        contract = Contract(
            contract_no="CUSTOMER-LINK-B-ONLY",
            customer_id=customer.id,
            building_id=other.id,
            tower="乙塔",
            room_no="B-ONLY-801",
            tenant_name=customer.name,
            area=120,
            unit_price=5,
            monthly_rent=18000,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=330),
            is_deleted=False,
        )
        db.add(contract)
        db.flush()
        db.add(
            CustomerFollowUp(
                customer_id=customer.id,
                contract_id=contract.id,
                building_id=other.id,
                follow_up_type="拜访",
                occurred_at=now_in_timezone() - timedelta(days=1),
                content="仅乙楼可见的跟进",
                owner="乙楼招商主管",
                status="open",
            )
        )
        db.commit()
        return int(demo_a.id), int(other.id), int(customer.id)
    finally:
        db.close()


def _add_demo_a_profile(customer_id: int, demo_a_building_id: int) -> None:
    db = SessionLocal()
    try:
        db.add(
            CustomerBuildingProfile(
                customer_id=customer_id,
                building_id=demo_a_building_id,
                local_contact="DEMO_A 联系人",
                local_notes="经明确确认允许在 DEMO_A 关联",
                owner="DEMO_A 招商经理",
                valid_from=today_in_timezone(),
            )
        )
        db.commit()
    finally:
        db.close()


def _demo_a_profile_count(customer_id: int, demo_a_building_id: int) -> int:
    db = SessionLocal()
    try:
        return int(
            db.query(CustomerBuildingProfile)
            .filter(
                CustomerBuildingProfile.customer_id == customer_id,
                CustomerBuildingProfile.building_id == demo_a_building_id,
            )
            .count()
        )
    finally:
        db.close()


def _contract_payload(contract_no: str, room_no: str, customer_id: int) -> dict:
    today = today_in_timezone()
    return {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": "请求中的旧客户名",
        "area": 100,
        "unit_price": 4,
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=365)).isoformat(),
    }


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _contract_count(*contract_numbers: str) -> int:
    db = SessionLocal()
    try:
        return int(
            db.query(Contract)
            .filter(Contract.contract_no.in_(contract_numbers))
            .count()
        )
    finally:
        db.close()


def _follow_up_count(*contents: str) -> int:
    db = SessionLocal()
    try:
        return int(
            db.query(CustomerFollowUp)
            .filter(CustomerFollowUp.content.in_(contents))
            .count()
        )
    finally:
        db.close()


def test_contract_create_and_update_cannot_link_other_building_customer_by_id() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, _other_building_id, other_customer_id = (
            _grant_other_building_and_seed_other_only_customer()
        )
        demo_a_customer = client.post(
            "/api/customers",
            json={"name": "DEMO_A 合同原客户"},
        )
        assert demo_a_customer.status_code == 200
        existing = client.post(
            "/api/contracts",
            json=_contract_payload(
                "CUSTOMER-LINK-DEMO_A-EXISTING",
                "LINK-DEMO_A-101",
                demo_a_customer.json()["id"],
            ),
        )
        assert existing.status_code == 200

        rejected_create = client.post(
            "/api/contracts",
            json=_contract_payload(
                "CUSTOMER-LINK-DEMO_A-REJECTED",
                "LINK-DEMO_A-102",
                other_customer_id,
            ),
        )
        rejected_update = client.put(
            f"/api/contracts/{existing.json()['id']}",
            json={"customer_id": other_customer_id},
        )

        assert rejected_create.status_code == 404
        assert rejected_update.status_code == 404
        assert _contract_count("CUSTOMER-LINK-DEMO_A-REJECTED") == 0
        assert _demo_a_profile_count(other_customer_id, demo_a_building_id) == 0
        unchanged = client.get(f"/api/contracts/{existing.json()['id']}")
        assert unchanged.status_code == 200
        assert unchanged.json()["customer_id"] == demo_a_customer.json()["id"]

        _add_demo_a_profile(other_customer_id, demo_a_building_id)
        allowed_create = client.post(
            "/api/contracts",
            json=_contract_payload(
                "CUSTOMER-LINK-DEMO_A-REJECTED",
                "LINK-DEMO_A-102",
                other_customer_id,
            ),
        )
        allowed_update = client.put(
            f"/api/contracts/{existing.json()['id']}",
            json={"customer_id": other_customer_id},
        )
        assert allowed_create.status_code == 200
        assert allowed_create.json()["customer_id"] == other_customer_id
        assert allowed_update.status_code == 200
        assert allowed_update.json()["customer_id"] == other_customer_id


def test_contract_excel_keeps_other_building_customer_name_unlinked_until_profile_exists() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, _other_building_id, other_customer_id = (
            _grant_other_building_and_seed_other_only_customer()
        )
        demo_a_customer = client.post(
            "/api/customers",
            json={"name": "DEMO_A Excel 合法客户"},
        )
        assert demo_a_customer.status_code == 200

        today = today_in_timezone()
        content = _workbook_bytes(
            ["合同编号", "楼塔", "室号", "客户名称", "出租面积", "租金单价", "起租日", "终止日"],
            [
                [
                    "CUSTOMER-LINK-IMPORT-VALID",
                    "南塔",
                    "LINK-IMP-101",
                    "DEMO_A Excel 合法客户",
                    100,
                    4,
                    today.isoformat(),
                    (today + timedelta(days=365)).isoformat(),
                ],
                [
                    "CUSTOMER-LINK-IMPORT-CROSS",
                    "南塔",
                    "LINK-IMP-102",
                    "仅乙楼有关联的全局客户",
                    100,
                    4,
                    today.isoformat(),
                    (today + timedelta(days=365)).isoformat(),
                ],
            ],
        )
        imported = client.post(
            "/api/contracts/import",
            files={
                "file": (
                    "customer-link-contracts.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 2
        assert _contract_count(
            "CUSTOMER-LINK-IMPORT-VALID",
            "CUSTOMER-LINK-IMPORT-CROSS",
        ) == 2
        assert _demo_a_profile_count(other_customer_id, demo_a_building_id) == 0
        db = SessionLocal()
        try:
            linked = (
                db.query(Contract)
                .filter(Contract.contract_no == "CUSTOMER-LINK-IMPORT-CROSS")
                .one()
            )
            assert linked.customer_id is None
            assert linked.building_id == demo_a_building_id
        finally:
            db.close()


def test_unlinked_follow_up_excel_treats_hidden_customer_as_unmatched_until_profile_exists() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, _other_building_id, other_customer_id = (
            _grant_other_building_and_seed_other_only_customer()
        )
        demo_a_customer = client.post(
            "/api/customers",
            json={"name": "DEMO_A 跟进合法客户"},
        )
        assert demo_a_customer.status_code == 200

        content = _workbook_bytes(
            ["客户名称", "合同编号", "跟进类型", "跟进内容"],
            [
                ["DEMO_A 跟进合法客户", "", "电话", "VALID_FOLLOW_UP_MUST_ROLL_BACK"],
                ["仅乙楼有关联的全局客户", "", "电话", "CROSS_SCOPE_FOLLOW_UP_MUST_NOT_WRITE"],
            ],
        )
        partial = client.post(
            "/api/customers/follow-ups/import",
            files={
                "file": (
                    "customer-link-followups.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["imported"] == 1
        assert partial.json()["failed"] == 1
        assert _follow_up_count(
            "VALID_FOLLOW_UP_MUST_ROLL_BACK",
            "CROSS_SCOPE_FOLLOW_UP_MUST_NOT_WRITE",
        ) == 1
        assert _demo_a_profile_count(other_customer_id, demo_a_building_id) == 0

        _add_demo_a_profile(other_customer_id, demo_a_building_id)
        allowed = client.post(
            "/api/customers/follow-ups/import",
            files={
                "file": (
                    "customer-link-followups.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["imported"] == 2
        db = SessionLocal()
        try:
            linked = (
                db.query(CustomerFollowUp)
                .filter(CustomerFollowUp.content == "CROSS_SCOPE_FOLLOW_UP_MUST_NOT_WRITE")
                .one()
            )
            assert linked.customer_id == other_customer_id
            assert linked.building_id == demo_a_building_id
            assert linked.contract_id is None
        finally:
            db.close()
