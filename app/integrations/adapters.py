from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AdapterResult:
    success: bool
    message: str


class IntegrationAdapter:
    def test_connection(self, config: dict[str, Any], secret: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "该连接器类型已预留，待实现具体适配器")

    def push_event(self, config: dict[str, Any], secret: dict[str, Any], event: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "该连接器类型已预留，待实现具体适配器")

    def sync(self, config: dict[str, Any], secret: dict[str, Any], direction: str) -> AdapterResult:
        return AdapterResult(False, "该连接器类型已预留，待实现具体适配器")


class RestWebhookAdapter(IntegrationAdapter):
    def _endpoint(self, config: dict[str, Any], secret: dict[str, Any]) -> str:
        return str(config.get("endpoint_url") or secret.get("endpoint_url") or "").strip()

    def _headers(self, secret: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = str(secret.get("bearer_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def test_connection(self, config: dict[str, Any], secret: dict[str, Any]) -> AdapterResult:
        endpoint = self._endpoint(config, secret)
        if not endpoint:
            return AdapterResult(False, "请配置 endpoint_url")
        try:
            response = httpx.post(
                endpoint,
                json={"event_type": "connection.test", "source": "lease-manager"},
                headers=self._headers(secret),
                timeout=10,
            )
        except Exception as exc:  # pragma: no cover - external IO
            return AdapterResult(False, f"连接失败：{exc}")
        if 200 <= response.status_code < 300:
            return AdapterResult(True, "连接成功")
        return AdapterResult(False, f"连接失败：HTTP {response.status_code}")

    def push_event(self, config: dict[str, Any], secret: dict[str, Any], event: dict[str, Any]) -> AdapterResult:
        endpoint = self._endpoint(config, secret)
        if not endpoint:
            return AdapterResult(False, "请配置 endpoint_url")
        try:
            response = httpx.post(endpoint, json=event, headers=self._headers(secret), timeout=10)
        except Exception as exc:  # pragma: no cover - external IO
            return AdapterResult(False, f"推送失败：{exc}")
        if 200 <= response.status_code < 300:
            return AdapterResult(True, "推送成功")
        return AdapterResult(False, f"推送失败：HTTP {response.status_code}")


def adapter_for(provider: str) -> IntegrationAdapter:
    if provider == "rest_webhook":
        return RestWebhookAdapter()
    return IntegrationAdapter()
