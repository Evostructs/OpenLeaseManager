from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import io
import json

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    Building,
    Contract,
    CustomerBuildingProfile,
    CustomerFollowUp,
    OperationLog,
    ReceivableStatement,
    User,
    UserScopeGrant,
)
from app.routers.ai import get_ai_provider
from app.services.ai_assist import (
    AI_TASK_CUSTOMER_SUMMARY,
    AI_TASK_FOLLOW_UP_SUGGESTION,
    AI_TASK_RISK_EXPLANATION,
)


OTHER_BUILDING_CODE = "CUSTOMER_EXTERNAL_B"


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


def _grant_other_building() -> tuple[int, int]:
    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = db.query(Building).filter(Building.code == OTHER_BUILDING_CODE).one_or_none()
        if other is None:
            other = Building(
                code=OTHER_BUILDING_CODE,
                name="客户外发范围乙楼",
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


def _seed_two_building_customer(
    customer_id: int,
    demo_a_building_id: int,
    other_building_id: int,
) -> dict[str, int]:
    today = today_in_timezone()
    month = today.strftime("%Y-%m")
    db = SessionLocal()
    try:
        demo_a_contract = Contract(
            contract_no="AI-SCOPE-A-CONTRACT",
            customer_id=customer_id,
            building_id=demo_a_building_id,
            tower="南塔",
            room_no="AI-A-101",
            tenant_name="AI 双楼客户",
            area=100,
            unit_price=4,
            monthly_rent=12000,
            start_date=today.replace(day=1),
            end_date=today + timedelta(days=40),
            is_deleted=False,
        )
        other_contract = Contract(
            contract_no="AI-SCOPE-B-SECRET-CONTRACT",
            customer_id=customer_id,
            building_id=other_building_id,
            tower="乙塔",
            room_no="AI-B-SECRET-ROOM",
            tenant_name="AI 双楼客户",
            area=900,
            unit_price=9,
            monthly_rent=99000,
            start_date=today.replace(day=1),
            end_date=today + timedelta(days=35),
            is_deleted=False,
        )
        db.add_all([demo_a_contract, other_contract])
        db.flush()

        demo_a_follow_up = CustomerFollowUp(
            customer_id=customer_id,
            contract_id=demo_a_contract.id,
            building_id=demo_a_building_id,
            follow_up_type="电话",
            occurred_at=now_in_timezone() - timedelta(days=2),
            content="A楼允许外发的跟进",
            next_action="A楼下一步",
            next_follow_up_date=today - timedelta(days=2),
            owner="A Owner",
            status="open",
        )
        other_follow_up = CustomerFollowUp(
            customer_id=customer_id,
            contract_id=other_contract.id,
            building_id=other_building_id,
            follow_up_type="拜访",
            occurred_at=now_in_timezone() - timedelta(days=1),
            content="B_SCOPE_SECRET_FOLLOW_UP",
            next_action="B_SCOPE_SECRET_NEXT_ACTION",
            next_follow_up_date=today - timedelta(days=1),
            owner="B Owner",
            status="open",
        )
        db.add_all([demo_a_follow_up, other_follow_up])
        db.flush()

        db.add_all(
            [
                ReceivableStatement(
                    contract_id=demo_a_contract.id,
                    period_month=month,
                    rent=111.11,
                    property_fee=0,
                    total_amount=111.11,
                    source="test",
                    operator="test",
                ),
                ReceivableStatement(
                    contract_id=other_contract.id,
                    period_month=month,
                    rent=999999.99,
                    property_fee=0,
                    total_amount=999999.99,
                    source="test",
                    operator="test",
                ),
                Attachment(
                    scope_type="building",
                    portfolio_id=None,
                    building_id=other_building_id,
                    entity_type="customer",
                    entity_id=customer_id,
                    file_name="B_SCOPE_SECRET_LICENSE.txt",
                    storage_path=f"customer-external/{customer_id}/b-license.txt",
                    content_type="text/plain",
                    file_size=12,
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


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _workbook_text(response) -> str:
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)
    return "\n".join(
        str(value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if value is not None
    )


class CapturingProvider:
    label = "scope-capture"
    model = "scope-capture-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, task: str, context: dict) -> dict:
        self.calls.append({"task": task, "context": deepcopy(context)})
        if task == AI_TASK_CUSTOMER_SUMMARY:
            return {"summary": "范围内摘要"}
        if task == AI_TASK_FOLLOW_UP_SUGGESTION:
            return {"suggestion": "范围内建议"}
        if task == AI_TASK_RISK_EXPLANATION:
            return {"explanation": "范围内风险解释"}
        raise AssertionError(task)


def test_customer_and_follow_up_exports_only_contain_selected_building() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, other_building_id = _grant_other_building()
        shared = client.post("/api/customers", json={"name": "导出双楼共享客户"}).json()
        ids = _seed_two_building_customer(shared["id"], demo_a_building_id, other_building_id)
        other_only = client.post(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE},
            json={"name": "B_SCOPE_SECRET_CUSTOMER"},
        )
        assert other_only.status_code == 200

        demo_a_customer_text = _workbook_text(client.get("/api/customers/export"))
        other_customer_text = _workbook_text(
            client.get("/api/customers/export", params={"building": OTHER_BUILDING_CODE})
        )
        assert "导出双楼共享客户" in demo_a_customer_text
        assert "B_SCOPE_SECRET_CUSTOMER" not in demo_a_customer_text
        assert "B_SCOPE_SECRET_CUSTOMER" in other_customer_text

        demo_a_follow_text = _workbook_text(client.get("/api/customers/follow-ups/export"))
        other_follow_text = _workbook_text(
            client.get(
                "/api/customers/follow-ups/export",
                params={"building": OTHER_BUILDING_CODE},
            )
        )
        assert "A楼允许外发的跟进" in demo_a_follow_text
        assert "B_SCOPE_SECRET_FOLLOW_UP" not in demo_a_follow_text
        assert "B_SCOPE_SECRET_FOLLOW_UP" in other_follow_text
        assert client.get(
            "/api/customers/follow-ups/export",
            params={"customer_id": other_only.json()["id"]},
        ).status_code == 404
        assert ids["other_follow_up_id"] != ids["demo_a_follow_up_id"]


def test_customer_follow_up_imports_follow_selected_detail_building_and_cross_contract_batch_is_atomic() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, other_building_id = _grant_other_building()
        shared = client.post("/api/customers", json={"name": "导入双楼共享客户"}).json()
        ids = _seed_two_building_customer(shared["id"], demo_a_building_id, other_building_id)
        profile_only = client.post(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE},
            json={"name": "导入乙楼主档客户", "short_name": "导入前"},
        )
        assert profile_only.status_code == 200
        profile_only_id = profile_only.json()["id"]

        customer_content = _workbook_bytes(
            ["客户名称", "简称", "统一社会信用代码"],
            [["导入乙楼主档客户", "导入后", "91310000SCOPEIMPORT"]],
        )
        customer_import = client.post(
            "/api/customers/import",
            files={"file": ("customers.xlsx", customer_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert customer_import.status_code == 200
        assert customer_import.json()["imported"] == 1
        assert client.get(f"/api/customers/{profile_only_id}").status_code == 200

        no_contract_customer = client.post(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE},
            json={"name": "无合同导入共享主档"},
        )
        assert no_contract_customer.status_code == 200
        follow_content = _workbook_bytes(
            ["客户名称", "合同编号", "跟进类型", "跟进内容"],
            [["无合同导入共享主档", "", "电话", "DEMO_A无合同导入跟进"]],
        )
        follow_import = client.post(
            "/api/customers/follow-ups/import",
            files={"file": ("followups.xlsx", follow_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert follow_import.status_code in {200, 400}
        if follow_import.status_code == 200:
            assert follow_import.json()["imported"] == 0
            assert follow_import.json()["failed"] == 1

        db = SessionLocal()
        try:
            assert db.query(CustomerBuildingProfile).filter(
                CustomerBuildingProfile.customer_id == no_contract_customer.json()["id"],
                CustomerBuildingProfile.building_id == demo_a_building_id,
            ).count() == 0
            db.add(
                CustomerBuildingProfile(
                    customer_id=no_contract_customer.json()["id"],
                    building_id=demo_a_building_id,
                    local_contact="",
                    local_notes="明确允许在 DEMO_A 建立无合同跟进",
                    owner="",
                    valid_from=today_in_timezone(),
                )
            )
            db.commit()
        finally:
            db.close()

        follow_import = client.post(
            "/api/customers/follow-ups/import",
            files={"file": ("followups.xlsx", follow_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert follow_import.status_code == 200
        assert follow_import.json()["imported"] == 1

        atomic_content = _workbook_bytes(
            ["客户名称", "合同编号", "跟进类型", "跟进内容"],
            [
                ["导入双楼共享客户", "", "电话", "ATOMIC_VALID_SHOULD_NOT_WRITE"],
                ["导入双楼共享客户", "AI-SCOPE-B-SECRET-CONTRACT", "电话", "跨楼合同跟进"],
            ],
        )
        atomic_import = client.post(
            "/api/customers/follow-ups/import",
            files={"file": ("atomic.xlsx", atomic_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert atomic_import.status_code == 400

        for path in (
            "/api/customers/import/template",
            "/api/customers/follow-ups/import/template",
        ):
            response = client.get(path, params={"building": OTHER_BUILDING_CODE})
            assert response.status_code == 200, response.text
        for path in (
            "/api/customers/import/preview",
            "/api/customers/import",
        ):
            response = client.post(
                path,
                params={"building": OTHER_BUILDING_CODE},
                files={"file": ("customers.xlsx", customer_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert response.status_code == 200, response.text
        for path in (
            "/api/customers/follow-ups/import/preview",
            "/api/customers/follow-ups/import",
        ):
            response = client.post(
                path,
                params={"building": OTHER_BUILDING_CODE},
                files={"file": ("followups.xlsx", follow_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert response.status_code == 200, response.text

        db = SessionLocal()
        try:
            profiles = db.query(CustomerBuildingProfile).filter(
                CustomerBuildingProfile.customer_id.in_([profile_only_id, no_contract_customer.json()["id"]]),
                CustomerBuildingProfile.building_id == demo_a_building_id,
            ).all()
            imported_follow_ups = db.query(CustomerFollowUp).filter(
                CustomerFollowUp.content == "DEMO_A无合同导入跟进"
            ).all()
            assert len(profiles) == 2
            assert {row.building_id for row in imported_follow_ups} == {demo_a_building_id, other_building_id}
            assert all(row.contract_id is None for row in imported_follow_ups)
            assert db.query(CustomerFollowUp).filter(
                CustomerFollowUp.content == "ATOMIC_VALID_SHOULD_NOT_WRITE"
            ).count() == 0
            import_logs = db.query(OperationLog).filter(
                OperationLog.action.in_(["customer_import", "followup_import"])
            ).all()
            assert import_logs
            assert all(row.scope_type == "building" for row in import_logs)
            assert {row.building_id for row in import_logs} == {demo_a_building_id, other_building_id}
        finally:
            db.close()
        assert ids["other_contract_id"] > 0


def test_ai_provider_receives_only_selected_building_context_and_cross_scope_risks_are_404() -> None:
    provider = CapturingProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            demo_a_building_id, other_building_id = _grant_other_building()
            customer = client.post("/api/customers", json={"name": "AI 双楼客户"}).json()
            ids = _seed_two_building_customer(customer["id"], demo_a_building_id, other_building_id)

            summary = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            suggestion = client.post("/api/ai/follow-up-suggestion", json={"customer_id": customer["id"]})
            assert summary.status_code == suggestion.status_code == 200
            assert len(provider.calls) == 2

            for call in provider.calls:
                context = call["context"]
                text = json.dumps(context, ensure_ascii=False, default=str)
                assert "AI-SCOPE-A-CONTRACT" in text
                assert "A楼允许外发的跟进" in text
                assert "AI-SCOPE-B-SECRET-CONTRACT" not in text
                assert "AI-B-SECRET-ROOM" not in text
                assert "B_SCOPE_SECRET_FOLLOW_UP" not in text
                assert "B_SCOPE_SECRET_NEXT_ACTION" not in text
                assert "999999.99" not in text
                assert context["current_month_receivable"]["total"] == 111.11
                assert context["attachments"]["customer_missing_required"] == ["证照"]

            demo_a_risks = client.get("/api/risks").json()["items"]
            other_risks = client.get(
                "/api/risks",
                params={"building": OTHER_BUILDING_CODE},
            ).json()["items"]
            demo_a_risk = next(
                row for row in demo_a_risks
                if row["stable_key"] == f"follow_up_overdue:{ids['demo_a_follow_up_id']}"
            )
            other_risk = next(
                row for row in other_risks
                if row["stable_key"] == f"follow_up_overdue:{ids['other_follow_up_id']}"
            )

            calls_before_rejection = len(provider.calls)
            assert client.post(
                "/api/ai/risk-explanation",
                json={"id": other_risk["id"], "risk_type": other_risk["risk_type"]},
            ).status_code == 404
            assert client.post(
                "/api/ai/risk-explanation",
                json={
                    "id": demo_a_risk["id"],
                    "risk_type": demo_a_risk["risk_type"],
                    "contract_id": ids["other_contract_id"],
                },
            ).status_code == 404
            assert len(provider.calls) == calls_before_rejection

            explained = client.post(
                "/api/ai/risk-explanation",
                json={"id": demo_a_risk["id"], "risk_type": demo_a_risk["risk_type"]},
            )
            assert explained.status_code == 200
            risk_context_text = json.dumps(provider.calls[-1]["context"], ensure_ascii=False, default=str)
            assert "B_SCOPE_SECRET_FOLLOW_UP" not in risk_context_text

            db = SessionLocal()
            try:
                logs = db.query(OperationLog).filter(
                    OperationLog.action.in_([
                        "ai_customer_summary",
                        "ai_follow_suggestion",
                        "ai_risk_explain",
                    ])
                ).all()
                assert len(logs) == 3
                assert all(
                    row.scope_type == "building" and row.building_id == demo_a_building_id
                    for row in logs
                )
            finally:
                db.close()
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)
