from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings


SECRET_PREFIX = "LEASE-MANAGER-SECRET-v1:"


def encrypt_secret(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    settings = get_settings()
    if not settings.data_encryption_enabled:
        raise ValueError("保存集成密钥前请先启用 DATA_ENCRYPTION_ENABLED=true")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    nonce = os.urandom(12)
    encrypted = AESGCM(settings.data_key).encrypt(nonce, payload, None)
    return SECRET_PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_secret(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    text = value.strip()
    if not text.startswith(SECRET_PREFIX):
        raise ValueError("集成密钥格式不正确")
    raw = base64.urlsafe_b64decode(text[len(SECRET_PREFIX):].encode("ascii"))
    nonce, encrypted = raw[:12], raw[12:]
    payload = AESGCM(get_settings().data_key).decrypt(nonce, encrypted, None)
    return json.loads(payload.decode("utf-8"))
