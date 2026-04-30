from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _create_contract(client: TestClient, contract_no: str = "LEASE-INT-001") -> int:
    response = client.post(
        "/api/contracts",
        json={
            "contract_no": contract_no,
            "tower": "Tower A",
            "room_no": contract_no.replace("-", "")[-12:],
            "tenant_name": "Integration Tenant",
            "area": 100,
            "unit_price": 4,
            "monthly_rent": 12000,
            "property_fee_rate": 28,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _create_connection(client: TestClient, *, enabled: bool = True, secret: dict | None = None) -> int:
    response = client.post(
        "/api/integrations",
        json={
            "name": "Webhook",
            "provider": "rest_webhook",
            "direction": "push",
            "enabled": enabled,
            "config": {"endpoint_url": "https://example.invalid/webhook"},
            **({"secret": secret} if secret is not None else {}),
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_integrations_are_admin_only() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")
        response = client.post(
            "/api/auth/create-user",
            json={"username": "editor_int", "password": "Editor123", "display_name": "Editor", "role": "editor"},
        )
        assert response.status_code == 200

    with _client() as editor:
        _login(editor, "editor_int", "Editor123")
        assert editor.get("/api/integrations/providers").status_code == 403
        assert editor.get("/api/integrations").status_code == 403


def test_admin_can_create_reserved_and_webhook_connections_without_secret_leakage() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        providers = client.get("/api/integrations/providers")
        assert providers.status_code == 200
        values = {item["value"] for item in providers.json()["providers"]}
        assert {"jde", "oa", "finance", "saas", "rest_webhook", "mcp_server", "mcp_client", "custom"} <= values

        reserved = client.post(
            "/api/integrations",
            json={"name": "JDE Reserved", "provider": "jde", "direction": "bidirectional", "enabled": True, "config": {}},
        )
        assert reserved.status_code == 200

        webhook_id = _create_connection(client, secret={"bearer_token": "secret-token"})
        listing = client.get("/api/integrations")
        assert listing.status_code == 200
        webhook = next(item for item in listing.json() if item["id"] == webhook_id)
        assert webhook["secret_configured"] is True
        assert "secret-token" not in str(webhook)


def test_secret_save_requires_data_encryption() -> None:
    old_enabled = os.environ.get("DATA_ENCRYPTION_ENABLED")
    os.environ["DATA_ENCRYPTION_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client, "admin", "Admin123")
            response = client.post(
                "/api/integrations",
                json={
                    "name": "Secret Blocked",
                    "provider": "rest_webhook",
                    "direction": "push",
                    "enabled": True,
                    "config": {"endpoint_url": "https://example.invalid/webhook"},
                    "secret": {"bearer_token": "secret-token"},
                },
            )
            assert response.status_code == 400
    finally:
        if old_enabled is None:
            os.environ.pop("DATA_ENCRYPTION_ENABLED", None)
        else:
            os.environ["DATA_ENCRYPTION_ENABLED"] = old_enabled
        get_settings.cache_clear()


def test_outbox_queues_events_for_enabled_push_connections_only() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        _create_connection(client, enabled=False)
        _create_contract(client, "LEASE-INT-DISABLED")
        assert client.get("/api/integrations/events").json()["total"] == 0

        _create_connection(client, enabled=True)
        contract_id = _create_contract(client, "LEASE-INT-ENABLED")
        payload = client.get("/api/integrations/events").json()
        assert payload["total"] == 1
        assert payload["items"][0]["event_type"] == "contract.created"
        assert payload["items"][0]["resource_id"] == contract_id


def test_webhook_event_retry_marks_event_sent(monkeypatch) -> None:
    class Response:
        status_code = 204

    monkeypatch.setattr("app.integrations.adapters.httpx.post", lambda *args, **kwargs: Response())

    with _client() as client:
        _ensure_admin(client)
        _login(client, "admin", "Admin123")

        _create_connection(client, enabled=True)
        _create_contract(client, "LEASE-INT-RETRY")
        event_id = client.get("/api/integrations/events").json()["items"][0]["id"]

        response = client.post(f"/api/integrations/events/{event_id}/retry")
        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        assert response.json()["attempts"] == 1
