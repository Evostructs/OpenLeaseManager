from __future__ import annotations

from contextlib import ExitStack

from fastapi.testclient import TestClient

from app.main import app


PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
PASSWORD = "Phase5Password123"


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "系统管理员"},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert response.status_code == 200, response.text


def _create_user(
    admin: TestClient,
    *,
    username: str,
    display_name: str,
    role: str,
) -> int:
    response = admin.post(
        "/api/auth/create-user",
        json={
            "username": username,
            "password": PASSWORD,
            "display_name": display_name,
            "role": role,
            "initial_scope": "none",
        },
    )
    assert response.status_code == 200, response.text
    users = admin.get("/api/auth/users")
    assert users.status_code == 200, users.text
    return next(int(item["id"]) for item in users.json() if item["username"] == username)


def _replace_authorization(
    admin: TestClient,
    user_id: int,
    grants: list[dict],
) -> dict:
    current = admin.get(f"/api/auth/authorizations/users/{user_id}")
    assert current.status_code == 200, current.text
    response = admin.put(
        f"/api/auth/authorizations/users/{user_id}",
        json={
            "expected_authorization_version": current.json()["authorization_version"],
            "grants": grants,
            "capabilities": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _building_grant(code: str, access_level: str) -> dict:
    return {
        "scope_type": "building",
        "scope_code": code,
        "access_level": access_level,
        "include_future_buildings": False,
    }


def _overview_grant() -> dict:
    return {
        "scope_type": "portfolio_overview",
        "scope_code": PORTFOLIO_CODE,
        "access_level": "view",
        "include_future_buildings": False,
    }


def _assert_denied(response) -> None:
    # 422 is the product's explicit "authorized building, unsupported detail capability"
    # denial; 403/404 cover role and object-scope denials.
    assert response.status_code in {403, 404, 422}, response.text


def test_four_product_account_types_keep_workspace_and_data_boundaries() -> None:
    account_specs = {
        "phase5_leader": {
            "display_name": "概况专用账号",
            "role": "viewer",
            "grants": [_overview_grant()],
        },
        "phase5_single_operator": {
            "display_name": "单楼运营",
            "role": "editor",
            "grants": [_building_grant("DEMO_A", "edit")],
        },
        "phase5_multi_operator": {
            "display_name": "多楼运营",
            "role": "editor",
            "grants": [
                _building_grant("DEMO_A", "edit"),
                _building_grant("DEMO_B", "view"),
            ],
        },
        "phase5_hybrid": {
            "display_name": "概况兼运营",
            "role": "editor",
            "grants": [_building_grant("DEMO_A", "edit"), _overview_grant()],
        },
    }

    with ExitStack() as stack:
        admin = stack.enter_context(TestClient(app))
        clients = {
            username: stack.enter_context(TestClient(app))
            for username in account_specs
        }
        _register_admin(admin)

        user_ids: dict[str, int] = {}
        for username, spec in account_specs.items():
            user_id = _create_user(
                admin,
                username=username,
                display_name=spec["display_name"],
                role=spec["role"],
            )
            user_ids[username] = user_id
            _replace_authorization(admin, user_id, spec["grants"])
            login = clients[username].post(
                "/api/auth/login",
                json={"username": username, "password": PASSWORD},
            )
            assert login.status_code == 200, login.text

        leader = clients["phase5_leader"]
        leader_me = leader.get("/api/auth/me")
        assert leader_me.status_code == 200, leader_me.text
        assert leader_me.json()["scope"]["items"] == []
        assert [
            item["code"]
            for item in leader_me.json()["scope"]["portfolio_overview"]["items"]
        ] == [PORTFOLIO_CODE]
        overview = leader.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": PORTFOLIO_CODE},
        )
        assert overview.status_code == 200, overview.text
        _assert_denied(leader.get("/api/contracts", params={"building": "DEMO_A"}))
        _assert_denied(leader.get("/api/rooms", params={"building": "DEMO_A"}))

        single = clients["phase5_single_operator"]
        single_me = single.get("/api/auth/me")
        assert single_me.status_code == 200, single_me.text
        assert {item["code"] for item in single_me.json()["scope"]["items"]} == {"DEMO_A"}
        assert single_me.json()["scope"]["portfolio_overview"]["items"] == []
        assert single.get("/api/contracts", params={"building": "DEMO_A"}).status_code == 200
        _assert_denied(
            single.get(
                "/api/operations/portfolio-overview",
                params={"portfolio": PORTFOLIO_CODE},
            )
        )
        _assert_denied(single.get("/api/contracts", params={"building": "DEMO_B"}))

        multi = clients["phase5_multi_operator"]
        multi_me = multi.get("/api/auth/me")
        assert multi_me.status_code == 200, multi_me.text
        assert {item["code"] for item in multi_me.json()["scope"]["items"]} == {
            "DEMO_A",
            "DEMO_B",
        }
        assert multi_me.json()["scope"]["portfolio_overview"]["items"] == []
        assert multi.get("/api/contracts", params={"building": "DEMO_A"}).status_code == 200
        snapshot = multi.get(
            "/api/operations/snapshot",
            params={"building": "DEMO_B", "include_trend": False, "queue_limit": 0},
        )
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["scope"]["building_codes"] == ["DEMO_B"]
        _assert_denied(multi.get("/api/contracts", params={"building": "DEMO_B"}))
        _assert_denied(multi.get("/api/rooms", params={"building": "DEMO_B"}))
        _assert_denied(multi.get("/api/contracts", params={"building": "DEMO_C"}))
        _assert_denied(
            multi.get(
                "/api/operations/portfolio-overview",
                params={"portfolio": PORTFOLIO_CODE},
            )
        )

        hybrid = clients["phase5_hybrid"]
        hybrid_me = hybrid.get("/api/auth/me")
        assert hybrid_me.status_code == 200, hybrid_me.text
        assert {item["code"] for item in hybrid_me.json()["scope"]["items"]} == {"DEMO_A"}
        assert [
            item["code"]
            for item in hybrid_me.json()["scope"]["portfolio_overview"]["items"]
        ] == [PORTFOLIO_CODE]
        assert hybrid.get("/api/contracts", params={"building": "DEMO_A"}).status_code == 200
        assert hybrid.get(
            "/api/operations/portfolio-overview",
            params={"portfolio": PORTFOLIO_CODE},
        ).status_code == 200
        _assert_denied(hybrid.get("/api/contracts", params={"building": "DEMO_B"}))

        replacement = _replace_authorization(
            admin,
            user_ids["phase5_hybrid"],
            [_building_grant("DEMO_A", "edit")],
        )
        assert replacement["scope"]["portfolio_overview"]["items"] == []
        assert hybrid.get("/api/auth/me").status_code == 401

        relogin = hybrid.post(
            "/api/auth/login",
            json={"username": "phase5_hybrid", "password": PASSWORD},
        )
        assert relogin.status_code == 200, relogin.text
        refreshed_me = hybrid.get("/api/auth/me")
        assert refreshed_me.status_code == 200, refreshed_me.text
        assert refreshed_me.json()["scope"]["portfolio_overview"]["items"] == []
        assert hybrid.get("/api/contracts", params={"building": "DEMO_A"}).status_code == 200
        _assert_denied(
            hybrid.get(
                "/api/operations/portfolio-overview",
                params={"portfolio": PORTFOLIO_CODE},
            )
        )
