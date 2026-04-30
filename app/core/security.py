from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from jose import JWTError, jwt

from app.core.config import get_settings


ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, hashed_password: str) -> bool:
    if "$" not in hashed_password:
        return False
    salt, digest = hashed_password.split("$", 1)
    expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return hmac.compare_digest(expected, digest)


def create_access_token(*, user_id: int, username: str, role: str, session_version: int) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.token_expire_hours)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "session_version": session_version,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
