from __future__ import annotations

import io
from datetime import timedelta

from fastapi.testclient import TestClient
import openpyxl

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    CustomerBuildingProfile,
    OperationLog,
    User,
    UserScopeGrant,
)


OTHER_BUILDING_CODE = "CUSTOMER_PROFILE_B"


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(
    client: TestClient,
    username: str = "admin",
    password: str = "Admin123",
) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def _grant_other_building_to_admin() -> tuple[int, int]:
    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        other = (
            db.query(Building)
            .filter(Building.code == OTHER_BUILDING_CODE)
            .one_or_none()
        )
        if other is None:
            other = Building(
                code=OTHER_BUILDING_CODE,
                name="客户资料隔离乙楼",
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


def _workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(content: bytes) -> dict:
    return {
        "file": (
            "customers.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def _response_workbook(response) -> openpyxl.Workbook:
    assert response.status_code == 200
    return openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)


def test_shared_master_and_building_profiles_are_serialized_and_updated_separately() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        demo_a_building_id, other_building_id = _grant_other_building_to_admin()

        created = client.post(
            "/api/customers",
            json={
                "name": "跨楼客户资料隔离",
                "short_name": "共享简称",
                "contact_name": "共享联系人",
                "local_contact": "DEMO_A_LOCAL_CONTACT_UNIQUE",
                "local_notes": "DEMO_A_LOCAL_NOTES_UNIQUE",
                "owner": "DEMO_A_OWNER_UNIQUE",
            },
        )
        assert created.status_code == 200
        customer = created.json()
        customer_id = int(customer["id"])
        assert customer["shared_master"]["short_name"] == "共享简称"
        assert customer["local_contact"] == "DEMO_A_LOCAL_CONTACT_UNIQUE"
        assert customer["building_profile"]["building_code"] == "DEMO_A"
        assert customer["building_profile"]["has_profile"] is True

        db = SessionLocal()
        try:
            db.add(
                CustomerBuildingProfile(
                    customer_id=customer_id,
                    building_id=other_building_id,
                    local_contact="OTHER_LOCAL_CONTACT_UNIQUE",
                    local_notes="OTHER_LOCAL_NOTES_UNIQUE",
                    owner="OTHER_OWNER_UNIQUE",
                    valid_from=today_in_timezone(),
                )
            )
            db.commit()
        finally:
            db.close()

        demo_a_detail = client.get(
            f"/api/customers/{customer_id}",
            params={"building": "DEMO_A"},
        )
        other_detail = client.get(
            f"/api/customers/{customer_id}",
            params={"building": OTHER_BUILDING_CODE},
        )
        assert demo_a_detail.status_code == other_detail.status_code == 200
        assert demo_a_detail.json()["short_name"] == other_detail.json()["short_name"] == "共享简称"
        assert demo_a_detail.json()["local_contact"] == "DEMO_A_LOCAL_CONTACT_UNIQUE"
        assert demo_a_detail.json()["owner"] == "DEMO_A_OWNER_UNIQUE"
        assert other_detail.json()["local_contact"] == "OTHER_LOCAL_CONTACT_UNIQUE"
        assert other_detail.json()["owner"] == "OTHER_OWNER_UNIQUE"

        assert client.get(
            "/api/customers",
            params={"building": "DEMO_A", "keyword": "DEMO_A_LOCAL_NOTES_UNIQUE"},
        ).json()["total"] == 1
        assert client.get(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE, "keyword": "DEMO_A_LOCAL_NOTES_UNIQUE"},
        ).json()["total"] == 0
        assert client.get(
            "/api/customers",
            params={"building": OTHER_BUILDING_CODE, "keyword": "OTHER_OWNER_UNIQUE"},
        ).json()["total"] == 1

        local_update = client.put(
            f"/api/customers/{customer_id}",
            params={"building": "DEMO_A"},
            json={
                "local_contact": "DEMO_A_LOCAL_CONTACT_UPDATED",
                "local_notes": "DEMO_A_LOCAL_NOTES_UPDATED",
                "owner": "DEMO_A_OWNER_UPDATED",
            },
        )
        assert local_update.status_code == 200
        assert local_update.json()["local_contact"] == "DEMO_A_LOCAL_CONTACT_UPDATED"

        master_update = client.put(
            f"/api/customers/{customer_id}",
            params={"building": "DEMO_A"},
            json={"short_name": "共享简称已更新"},
        )
        assert master_update.status_code == 200

        other_after = client.get(
            f"/api/customers/{customer_id}",
            params={"building": OTHER_BUILDING_CODE},
        ).json()
        assert other_after["short_name"] == "共享简称已更新"
        assert other_after["local_contact"] == "OTHER_LOCAL_CONTACT_UNIQUE"
        assert other_after["local_notes"] == "OTHER_LOCAL_NOTES_UNIQUE"
        assert other_after["owner"] == "OTHER_OWNER_UNIQUE"

        db = SessionLocal()
        try:
            local_log = (
                db.query(OperationLog)
                .filter(
                    OperationLog.action == "customer_profile_update",
                    OperationLog.target_id == customer_id,
                )
                .order_by(OperationLog.id.desc())
                .first()
            )
            master_log = (
                db.query(OperationLog)
                .filter(
                    OperationLog.action == "customer_update",
                    OperationLog.target_id == customer_id,
                )
                .order_by(OperationLog.id.desc())
                .first()
            )
            assert local_log is not None
            assert (local_log.scope_type, local_log.building_id, local_log.portfolio_id) == (
                "building",
                demo_a_building_id,
                None,
            )
            assert master_log is not None
            assert master_log.scope_type == "global"
            assert master_log.building_id is None
            assert master_log.portfolio_id is None
            assert "short_name" in master_log.changes
            assert "local_contact" not in master_log.changes
        finally:
            db.close()


def test_building_editor_can_change_only_current_building_profile() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        created = client.post(
            "/api/customers",
            json={
                "name": "楼宇编辑权限客户",
                "short_name": "共享简称不应被改",
                "local_contact": "原楼宇联系人",
                "owner": "原楼宇负责人",
            },
        )
        assert created.status_code == 200
        customer = created.json()
        customer_id = int(customer["id"])
        shared_updated_at = customer["updated_at"]

        created_user = client.post(
            "/api/auth/create-user",
            json={
                "username": "profile_editor",
                "password": "Editor123",
                "display_name": "Profile Editor",
                "role": "editor",
            },
        )
        assert created_user.status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        _login(client, "profile_editor", "Editor123")

        auth_me = client.get("/api/auth/me")
        assert auth_me.status_code == 200
        assert "customer_master_manage" not in auth_me.json()["scope"]["effective_capabilities"]

        local_update = client.put(
            f"/api/customers/{customer_id}",
            json={
                "local_contact": "编辑员更新的楼宇联系人",
                "local_notes": "编辑员更新的楼宇备注",
                "owner": "编辑员更新的楼宇负责人",
            },
        )
        assert local_update.status_code == 200
        assert local_update.json()["local_contact"] == "编辑员更新的楼宇联系人"
        assert local_update.json()["updated_at"] == shared_updated_at

        unchanged_master_with_local_update = client.put(
            f"/api/customers/{customer_id}",
            json={
                "name": "楼宇编辑权限客户",
                "short_name": "共享简称不应被改",
                "local_notes": "只更新本楼，不因携带未变化主档而拒绝",
            },
        )
        assert unchanged_master_with_local_update.status_code == 200
        assert unchanged_master_with_local_update.json()["local_notes"] == "只更新本楼，不因携带未变化主档而拒绝"
        assert unchanged_master_with_local_update.json()["updated_at"] == shared_updated_at

        rejected = client.put(
            f"/api/customers/{customer_id}",
            json={"short_name": "越权修改"},
        )
        assert rejected.status_code == 403
        detail = client.get(f"/api/customers/{customer_id}").json()
        assert detail["short_name"] == "共享简称不应被改"
        assert detail["local_notes"] == "只更新本楼，不因携带未变化主档而拒绝"
        assert client.post("/api/customers", json={"name": "无权新增主档"}).status_code == 403

        db = SessionLocal()
        try:
            editor_master_logs = (
                db.query(OperationLog)
                .filter(
                    OperationLog.operator == "profile_editor",
                    OperationLog.action == "customer_update",
                )
                .count()
            )
            editor_profile_logs = (
                db.query(OperationLog)
                .filter(
                    OperationLog.operator == "profile_editor",
                    OperationLog.action == "customer_profile_update",
                )
                .all()
            )
            assert editor_master_logs == 0
            assert len(editor_profile_logs) == 2
            assert all(
                log.scope_type == "building"
                and log.building_id is not None
                and log.portfolio_id is None
                for log in editor_profile_logs
            )
        finally:
            db.close()


def test_customer_import_export_keeps_shared_and_building_fields_separate() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        created = client.post(
            "/api/customers",
            json={
                "name": "客户资料导入隔离",
                "short_name": "导入前共享简称",
                "contact_name": "共享联系人保留",
                "local_contact": "导入前楼宇联系人",
                "local_notes": "导入前楼宇备注",
                "owner": "导入前楼宇负责人",
            },
        )
        assert created.status_code == 200
        customer_id = int(created.json()["id"])

        preview_content = _workbook_bytes(
            ["客户名称", "楼宇联系人", "楼宇负责人", "楼宇备注"],
            [["客户资料导入隔离", "预览联系人", "预览负责人", "预览备注"]],
        )
        preview = client.post(
            "/api/customers/import/preview",
            files=_upload(preview_content),
        )
        assert preview.status_code == 200
        assert preview.json()["preview_rows"][0]["local_contact"] == "预览联系人"
        assert client.get(f"/api/customers/{customer_id}").json()["local_contact"] == "导入前楼宇联系人"

        import_content = _workbook_bytes(
            ["客户名称", "简称", "楼宇联系人", "楼宇负责人", "楼宇备注"],
            [["客户资料导入隔离", "导入后共享简称", "导入后楼宇联系人", "导入后楼宇负责人", "导入后楼宇备注"]],
        )
        imported = client.post("/api/customers/import", files=_upload(import_content))
        assert imported.status_code == 200
        assert imported.json()["imported"] == 1
        assert imported.json()["master_changed"] == 1
        assert imported.json()["profile_changed"] == 1

        detail = client.get(f"/api/customers/{customer_id}").json()
        assert detail["short_name"] == "导入后共享简称"
        assert detail["contact_name"] == "共享联系人保留"
        assert detail["local_contact"] == "导入后楼宇联系人"
        assert detail["local_notes"] == "导入后楼宇备注"
        assert detail["owner"] == "导入后楼宇负责人"

        legacy_content = _workbook_bytes(
            ["客户名称", "简称"],
            [["客户资料导入隔离", "旧表更新共享简称"]],
        )
        legacy_import = client.post("/api/customers/import", files=_upload(legacy_content))
        assert legacy_import.status_code == 200
        assert legacy_import.json()["master_changed"] == 1
        assert legacy_import.json()["profile_changed"] == 0
        after_legacy = client.get(f"/api/customers/{customer_id}").json()
        assert after_legacy["short_name"] == "旧表更新共享简称"
        assert after_legacy["contact_name"] == "共享联系人保留"
        assert after_legacy["local_contact"] == "导入后楼宇联系人"
        assert after_legacy["local_notes"] == "导入后楼宇备注"
        assert after_legacy["owner"] == "导入后楼宇负责人"

        exported = _response_workbook(
            client.get(
                "/api/customers/export",
                params={"keyword": "客户资料导入隔离"},
            )
        )
        sheet = exported.active
        headers = [cell.value for cell in sheet[2]]
        values = {header: sheet.cell(row=3, column=index + 1).value for index, header in enumerate(headers)}
        assert values["客户名称"] == "客户资料导入隔离"
        assert values["简称"] == "旧表更新共享简称"
        assert values["楼宇范围"] == "DEMO_A"
        assert values["楼宇联系人"] == "导入后楼宇联系人"
        assert values["楼宇负责人"] == "导入后楼宇负责人"
        assert values["楼宇备注"] == "导入后楼宇备注"

        template = _response_workbook(client.get("/api/customers/import/template"))
        template_headers = [cell.value for cell in template.active[1]]
        assert {"楼宇联系人", "楼宇负责人", "楼宇备注"} <= set(template_headers)

        db = SessionLocal()
        try:
            profile_import_logs = (
                db.query(OperationLog)
                .filter(OperationLog.action == "customer_profile_import")
                .all()
            )
            master_import_logs = (
                db.query(OperationLog)
                .filter(OperationLog.action == "customer_master_import")
                .all()
            )
            assert len(profile_import_logs) == 1
            assert profile_import_logs[0].scope_type == "building"
            assert profile_import_logs[0].building_id is not None
            assert len(master_import_logs) == 2
            assert all(log.scope_type in {"portfolio", "global"} for log in master_import_logs)
            assert all(log.building_id is None for log in master_import_logs)
        finally:
            db.close()


def test_overlapping_active_building_profiles_fail_closed() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        created = client.post(
            "/api/customers",
            json={"name": "重叠楼宇资料复核客户", "local_contact": "当前版本"},
        )
        assert created.status_code == 200
        customer_id = int(created.json()["id"])

        db = SessionLocal()
        try:
            demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
            db.add(
                CustomerBuildingProfile(
                    customer_id=customer_id,
                    building_id=demo_a.id,
                    local_contact="错误的重叠版本",
                    local_notes="",
                    owner="",
                    valid_from=today_in_timezone() - timedelta(days=1),
                    valid_to=None,
                )
            )
            db.commit()
        finally:
            db.close()

        detail = client.get(f"/api/customers/{customer_id}")
        assert detail.status_code == 409
        assert "重叠有效版本" in detail.json()["detail"]
