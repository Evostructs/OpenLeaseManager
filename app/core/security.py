from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
import jwt

from app.core.config import get_settings


ALGORITHM = "HS256"
PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def _verify_legacy_pbkdf2(password: str, hashed_password: str) -> bool:
    """Verify hashes created by versions before the Argon2id migration."""
    if "$" not in hashed_password:
        return False
    stored_salt, digest = hashed_password.split("$", 1)
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        stored_salt.encode("utf-8"),
        120_000,
    ).hex()
    return hmac.compare_digest(expected, digest)


def verify_password(password: str, hashed_password: str) -> bool:
    if hashed_password.startswith("$argon2"):
        try:
            return PASSWORD_HASHER.verify(hashed_password, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return _verify_legacy_pbkdf2(password, hashed_password)


def password_needs_rehash(hashed_password: str) -> bool:
    if not hashed_password.startswith("$argon2"):
        return True
    try:
        return PASSWORD_HASHER.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True


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
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid token") from exc
