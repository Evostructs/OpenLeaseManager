from __future__ import annotations

import json
import os
from datetime import timedelta

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import get_settings
from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import OperationLog
from app.routers.ai import get_ai_provider
from app.services.ai_assist import (
    AI_TASK_CUSTOMER_SUMMARY,
    AiAssistProviderError,
    CUSTOMER_SUMMARY_CONTRACT_LIMIT,
    CUSTOMER_SUMMARY_FOLLOW_UP_LIMIT,
    CUSTOMER_SUMMARY_RISK_LIMIT,
    FakeAiAssistProvider,
    OpenAICompatibleAiAssistProvider,
    build_customer_ai_context,
    build_customer_summary_ai_context,
    build_risk_ai_context,
)


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert response.status_code in {200, 403}


def _login(client: TestClient, username: str = "admin", password: str = "Admin123") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_customer(client: TestClient, *, expiring: bool = True) -> tuple[dict, dict]:
    today = today_in_timezone()
    customer_response = client.post(
        "/api/customers",
        json={
            "name": "AI Assist customer",
            "short_name": "AI",
            "contact_name": "Alice",
            "contact_phone": "13900000000",
            "contact_email": "secret@example.com",
            "notes": "customer-secret-note",
        },
    )
    assert customer_response.status_code == 200
    customer = customer_response.json()
    end_date = today + timedelta(days=30 if expiring else 180)
    contract_response = client.post(
        "/api/contracts",
        json={
            "contract_no": "DEMO_A-AI-001",
            "customer_id": customer["id"],
            "tower": "南塔办公",
            "room_no": "AI-101",
            "tenant_name": "Legacy AI customer",
            "area": 120,
            "unit_price": 5,
            "monthly_rent": 18250,
            "internal_contact_person": "Owner A",
            "internal_contact_feishu_id": "feishu-secret-id",
            "start_date": today.replace(day=1).isoformat(),
            "end_date": end_date.isoformat(),
            "notes": "contract-secret-note",
        },
    )
    assert contract_response.status_code == 200
    contract = contract_response.json()
    follow_response = client.post(
        f"/api/customers/{customer['id']}/follow-ups",
        json={
            "contract_id": contract["id"],
            "follow_up_type": "电话",
            "content": "Customer asked for latest invoice status.",
            "next_action": "Check received amount and attachment status.",
            "next_follow_up_date": (today + timedelta(days=1)).isoformat(),
            "owner": "Owner A",
        },
    )
    assert follow_response.status_code == 200
    return customer, contract


def _with_fake_provider():
    provider = FakeAiAssistProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    return provider


class EmptyAiAssistProvider:
    label = "empty"
    model = "empty-ai-assist"

    def generate(self, task: str, context: dict) -> dict:
        return {}


class RiskAliasAiAssistProvider:
    label = "alias"
    model = "alias-ai-assist"

    def generate(self, task: str, context: dict) -> dict:
        return {
            "risk_explanation": "该风险解释来自兼容字段。",
            "highlights": ["兼容字段"],
            "suggested_actions": ["人工核对风险项"],
        }


class CapturingAiAssistProvider:
    label = "capture"
    model = "capture-ai-assist"

    def __init__(self):
        self.calls: list[dict] = []

    def generate(self, task: str, context: dict) -> dict:
        self.calls.append({"task": task, "context": context})
        return {
            "summary": "compact summary",
            "highlights": ["compact"],
            "suggested_actions": ["review customer"],
        }


class _ProviderResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://example.test/chat/completions")
        self.response = httpx.Response(status_code, request=self.request)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("provider failed", request=self.request, response=self.response)

    def json(self) -> object:
        return self.payload


class _ProviderClient:
    def __init__(self, responses: list[_ProviderResponse], calls: list[dict], exception: Exception | None = None, timeout: float | None = None):
        self.responses = responses
        self.calls = calls
        self.exception = exception
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def post(self, endpoint: str, *, headers: dict, json: dict):
        self.calls.append(
            {
                "endpoint": endpoint,
                "headers": sorted(headers.keys()),
                "payload": json and __import__("copy").deepcopy(json),
            }
        )
        if self.exception:
            raise self.exception
        return self.responses.pop(0)


def _override_provider(provider):
    app.dependency_overrides[get_ai_provider] = lambda: provider
    return provider


def _clear_provider_override() -> None:
    app.dependency_overrides.pop(get_ai_provider, None)


def test_ai_assist_requires_login() -> None:
    with _client() as client:
        response = client.post("/api/ai/customer-summary", json={"customer_id": 1})
        assert response.status_code == 401


def test_ai_assist_unconfigured_returns_clear_error(monkeypatch) -> None:
    for key in ("AI_ASSIST_ENABLED", "AI_ASSIST_API_KEY", "AI_ASSIST_MODEL", "AI_ASSIST_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    _clear_provider_override()

    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer, _contract = _seed_customer(client)
        response = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
        assert response.status_code == 503
        assert response.json()["detail"] == "AI Assist 未配置"


def test_customer_summary_and_follow_up_suggestion_use_fake_provider_and_audit() -> None:
    provider = _with_fake_provider()
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            customer, _contract = _seed_customer(client)

            summary = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            assert summary.status_code == 200
            assert summary.json()["summary"]
            assert summary.json()["provider_label"] == provider.label

            suggestion = client.post("/api/ai/follow-up-suggestion", json={"customer_id": customer["id"]})
            assert suggestion.status_code == 200
            assert suggestion.json()["suggestion"]
            assert "催收话术" not in suggestion.json()["suggestion"]

            db = SessionLocal()
            try:
                logs = db.query(OperationLog).filter(OperationLog.action.in_(["ai_customer_summary", "ai_follow_suggestion"])).all()
                assert {row.action for row in logs} == {"ai_customer_summary", "ai_follow_suggestion"}
                for row in logs:
                    changes = row.changes or ""
                    assert row.target_type == "customer"
                    assert row.target_id == customer["id"]
                    assert "prompt" not in changes.lower()
                    assert "Customer asked for latest invoice status" not in changes
                    assert "sk-" not in changes
            finally:
                db.close()
    finally:
        _clear_provider_override()


def test_risk_explanation_uses_structured_risk_input() -> None:
    _with_fake_provider()
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            _customer, _contract = _seed_customer(client)
            risks = client.get("/api/risks", params={"limit": 20}).json()["items"]
            assert risks

            response = client.post(
                "/api/ai/risk-explanation",
                json={"id": risks[0]["id"], "risk_type": risks[0]["risk_type"]},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["explanation"]
            assert payload["suggested_actions"]

            db = SessionLocal()
            try:
                context = build_risk_ai_context(db, {"id": risks[0]["id"], "risk_type": risks[0]["risk_type"]})
                text = json.dumps(context, ensure_ascii=False, default=str)
                assert "customer_context" not in context
                assert "recent_follow_ups" not in context
                assert "Customer asked for latest invoice status." not in text
                assert "contract-secret-note" not in text
                assert "feishu-secret-id" not in text
            finally:
                db.close()
    finally:
        _clear_provider_override()


def test_ai_assist_empty_provider_returns_502_without_audit() -> None:
    _override_provider(EmptyAiAssistProvider())
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            customer, _contract = _seed_customer(client)
            response = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            assert response.status_code == 502
            assert response.json()["detail"] == "AI Assist 返回为空，请重试"

            db = SessionLocal()
            try:
                logs = db.query(OperationLog).filter(OperationLog.action == "ai_customer_summary").all()
                assert logs == []
            finally:
                db.close()
    finally:
        _clear_provider_override()


def test_risk_explanation_accepts_alias_text_field() -> None:
    _override_provider(RiskAliasAiAssistProvider())
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            _customer, _contract = _seed_customer(client)
            risks = client.get("/api/risks", params={"limit": 20}).json()["items"]
            assert risks

            response = client.post(
                "/api/ai/risk-explanation",
                json={"id": risks[0]["id"], "risk_type": risks[0]["risk_type"]},
            )
            assert response.status_code == 200
            assert response.json()["explanation"] == "该风险解释来自兼容字段。"
    finally:
        _clear_provider_override()


def test_ai_customer_not_found_returns_404() -> None:
    _with_fake_provider()
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            response = client.post("/api/ai/customer-summary", json={"customer_id": 999999})
            assert response.status_code == 404
    finally:
        _clear_provider_override()


def test_viewer_cannot_use_ai_assist_but_editor_can() -> None:
    # AI Assist 会外发业务数据并产生调用费用，viewer 不可触发，editor 及以上可用
    _with_fake_provider()
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            customer, _contract = _seed_customer(client)
            create_viewer = client.post(
                "/api/auth/create-user",
                json={"username": "viewer", "password": "Viewer123", "display_name": "Viewer", "role": "viewer"},
            )
            assert create_viewer.status_code == 200
            create_editor = client.post(
                "/api/auth/create-user",
                json={"username": "editor", "password": "Editor123", "display_name": "Editor", "role": "editor"},
            )
            assert create_editor.status_code == 200

            _login(client, "viewer", "Viewer123")
            response = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            assert response.status_code == 403

            _login(client, "editor", "Editor123")
            response = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            assert response.status_code == 200
            assert response.json()["summary"]
    finally:
        _clear_provider_override()


def test_ai_context_excludes_sensitive_and_full_row_fields() -> None:
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer, _contract = _seed_customer(client)

    db = SessionLocal()
    try:
        os.environ["AI_ASSIST_API_KEY"] = "sk-test-secret-not-logged"
        context = build_customer_ai_context(db, customer["id"])
        text = json.dumps(context, ensure_ascii=False, default=str)
        assert "13900000000" not in text
        assert "secret@example.com" not in text
        assert "customer-secret-note" not in text
        assert "contract-secret-note" not in text
        assert "feishu-secret-id" not in text
        # 个人姓名不外发：外部联系人、内部对接人/跟进负责人
        assert "Alice" not in text
        assert "Owner A" not in text
        assert "sk-test-secret-not-logged" not in text
        assert "password" not in text.lower()
        assert "api_key" not in text.lower()
        assert context["ai_boundaries"]["read_only"] is True
    finally:
        os.environ.pop("AI_ASSIST_API_KEY", None)
        db.close()


def test_customer_summary_context_is_compact_and_redacted() -> None:
    today = today_in_timezone()
    long_follow_up = "Follow up prefix " + ("x" * 180) + "very-sensitive-followup-tail"
    with _client() as client:
        _ensure_admin(client)
        _login(client)
        customer, contract = _seed_customer(client)
        for index in range(2, 8):
            response = client.post(
                "/api/contracts",
                json={
                    "contract_no": f"DEMO_A-AI-{index:03d}",
                    "customer_id": customer["id"],
                    "tower": "N",
                    "room_no": f"AI-{index:03d}",
                    "tenant_name": "Legacy AI customer",
                    "area": 100 + index,
                    "unit_price": 5,
                    "monthly_rent": 10000 + index,
                    "internal_contact_person": f"Owner {index}",
                    "internal_contact_feishu_id": f"feishu-secret-{index}",
                    "start_date": today.replace(day=1).isoformat(),
                    "end_date": (today + timedelta(days=30 + index)).isoformat(),
                    "notes": f"contract-secret-note-{index}",
                    "room_items": [
                        {
                            "tower": "N",
                            "room_no": f"AI-{index:03d}-A",
                            "area": 50,
                            "unit_price": 5,
                            "monthly_rent": 5000,
                        }
                    ],
                },
            )
            assert response.status_code == 200
        for index in range(6):
            response = client.post(
                f"/api/customers/{customer['id']}/follow-ups",
                json={
                    "contract_id": contract["id"],
                    "follow_up_type": "电话",
                    "content": long_follow_up,
                    "next_action": "Check received amount and attachment status.",
                    "next_follow_up_date": (today + timedelta(days=index + 1)).isoformat(),
                    "owner": "Owner A",
                },
            )
            assert response.status_code == 200

    db = SessionLocal()
    try:
        context = build_customer_summary_ai_context(db, customer["id"])
        text = json.dumps(context, ensure_ascii=False, default=str)
        assert context["context_scope"]["purpose"] == "short_customer_operating_summary"
        assert len(context["contracts"]) <= CUSTOMER_SUMMARY_CONTRACT_LIMIT
        assert len(context["risks"]) <= CUSTOMER_SUMMARY_RISK_LIMIT
        assert len(context["recent_follow_ups"]) <= CUSTOMER_SUMMARY_FOLLOW_UP_LIMIT
        assert "rooms" not in text
        assert "internal_owner" not in text
        assert "action_target" not in text
        assert '"meta"' not in text
        assert "13900000000" not in text
        assert "secret@example.com" not in text
        assert "customer-secret-note" not in text
        assert "contract-secret-note" not in text
        assert "feishu-secret" not in text
        assert "very-sensitive-followup-tail" not in text
        assert context["ai_boundaries"]["read_only"] is True
    finally:
        db.close()


def test_customer_summary_route_uses_compact_context() -> None:
    provider = _override_provider(CapturingAiAssistProvider())
    try:
        with _client() as client:
            _ensure_admin(client)
            _login(client)
            customer, _contract = _seed_customer(client)
            response = client.post("/api/ai/customer-summary", json={"customer_id": customer["id"]})
            assert response.status_code == 200

        assert provider.calls
        call = provider.calls[0]
        assert call["task"] == AI_TASK_CUSTOMER_SUMMARY
        assert call["context"]["context_scope"]["purpose"] == "short_customer_operating_summary"
    finally:
        _clear_provider_override()


def _openai_provider() -> OpenAICompatibleAiAssistProvider:
    return OpenAICompatibleAiAssistProvider(
        api_key="sk-test-secret-not-logged",
        model="deepseek-chat",
        base_url="https://example.test/v1",
        timeout_seconds=1,
    )


def _patch_provider_client(monkeypatch, responses: list[_ProviderResponse], exception: Exception | None = None) -> list[dict]:
    calls: list[dict] = []

    def factory(timeout):
        return _ProviderClient(responses, calls, exception=exception, timeout=timeout)

    monkeypatch.setattr("app.services.ai_assist.httpx.Client", factory)
    return calls


def test_openai_compatible_provider_retries_empty_content_once(monkeypatch) -> None:
    calls = _patch_provider_client(
        monkeypatch,
        [
            _ProviderResponse({"choices": [{"message": {"content": ""}}]}),
            _ProviderResponse({"choices": [{"message": {"content": "{\"suggestion\":\"ok after retry\"}"}}]}),
        ],
    )

    result = _openai_provider().generate("follow_up_suggestion", {"customer": {"name": "A"}})

    assert result["suggestion"] == "ok after retry"
    assert len(calls) == 2
    assert "response_format" in calls[0]["payload"]
    assert "response_format" not in calls[1]["payload"]
    logged_keys = json.dumps(calls[0]["headers"])
    assert "Authorization" in logged_keys
    assert "sk-test-secret-not-logged" not in json.dumps(calls, ensure_ascii=False)


def test_customer_summary_provider_uses_short_output_contract(monkeypatch) -> None:
    calls = _patch_provider_client(
        monkeypatch,
        [
            _ProviderResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "{\"summary\":\"short\",\"highlights\":[\"h\"],\"suggested_actions\":[\"a\"]}"
                            }
                        }
                    ]
                }
            )
        ],
    )

    result = _openai_provider().generate("customer_summary", {"customer": {"name": "A"}})

    assert result["summary"] == "short"
    payload = calls[0]["payload"]
    assert payload["max_tokens"] == 700
    assert "response_format" not in payload
    user_message = json.loads(payload["messages"][1]["content"])
    assert user_message["output_contract"]["text_field"] == "summary"
    assert "<= 160" in user_message["output_contract"]["summary"]
    assert "JSON only" in user_message["output_contract"]["style"]


def test_openai_compatible_provider_accepts_text_block_content(monkeypatch) -> None:
    _patch_provider_client(
        monkeypatch,
        [
            _ProviderResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "{\"summary\":\"text block result\"}"},
                                ]
                            }
                        }
                    ]
                }
            )
        ],
    )

    result = _openai_provider().generate("customer_summary", {"customer": {"name": "A"}})

    assert result["summary"] == "text block result"


def test_openai_compatible_provider_empty_after_retry_is_clear_error(monkeypatch) -> None:
    _patch_provider_client(
        monkeypatch,
        [
            _ProviderResponse({"choices": [{"message": {"content": ""}}]}),
            _ProviderResponse({"choices": []}),
        ],
    )

    with pytest.raises(AiAssistProviderError, match="AI Assist"):
        _openai_provider().generate("customer_summary", {"customer": {"name": "A"}})


def test_openai_compatible_provider_malformed_payload_is_clear_error(monkeypatch) -> None:
    _patch_provider_client(monkeypatch, [_ProviderResponse({"choices": {"bad": True}})])

    with pytest.raises(AiAssistProviderError, match="AI Assist"):
        _openai_provider().generate("customer_summary", {"customer": {"name": "A"}})



def test_openai_compatible_provider_retries_timeout_once(monkeypatch) -> None:
    calls: list[dict] = []
    responses = [_ProviderResponse({"choices": [{"message": {"content": "{\"suggestion\":\"ok after timeout\"}"}}]})]
    attempts = {"count": 0}

    def factory(timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return _ProviderClient([], calls, exception=httpx.TimeoutException("timeout"), timeout=timeout)
        return _ProviderClient(responses, calls, timeout=timeout)

    monkeypatch.setattr("app.services.ai_assist.httpx.Client", factory)

    result = _openai_provider().generate("follow_up_suggestion", {"customer": {"name": "A"}})

    assert result["suggestion"] == "ok after timeout"
    assert len(calls) == 2
    assert "response_format" in calls[0]["payload"]
    assert "response_format" not in calls[1]["payload"]


def test_openai_compatible_provider_timeout_is_clear_error(monkeypatch) -> None:
    _patch_provider_client(monkeypatch, [], exception=httpx.TimeoutException("timeout"))

    with pytest.raises(AiAssistProviderError, match="AI Assist"):
        _openai_provider().generate("customer_summary", {"customer": {"name": "A"}})
