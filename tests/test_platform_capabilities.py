from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _register_and_login(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "root_admin", "password": "Admin123", "display_name": "Root Admin"},
        )
        assert response.status_code == 200
    response = client.post(
        "/api/auth/login",
        json={"username": "root_admin", "password": "Admin123"},
    )
    assert response.status_code == 200


def test_initial_admin_gets_explicit_platform_capabilities() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        capabilities = set(me.json()["scope"]["effective_capabilities"])
        assert {"platform_admin", "customer_master_manage"} <= capabilities
        assert client.get("/api/auth/users").status_code == 200
        assert client.get("/api/system/version").status_code == 200


def test_admin_role_alone_cannot_use_platform_wide_endpoints() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        created = client.post(
            "/api/auth/create-user",
            json={
                "username": "building_admin",
                "password": "Admin123",
                "display_name": "Building Admin",
                "role": "admin",
            },
        )
        assert created.status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"username": "building_admin", "password": "Admin123"},
        ).status_code == 200

        for path in (
            "/api/auth/users",
            "/api/system/version",
            "/api/settings/feishu",
            "/api/backups",
        ):
            assert client.get(path).status_code == 403
        assert client.post(
            "/api/customers",
            json={"name": "不应由楼宇管理员新建的板块客户"},
        ).status_code == 403
