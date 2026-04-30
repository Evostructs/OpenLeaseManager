from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

from app.core.crypto import CryptoError, decode_key


BASE_DIR = Path(__file__).resolve().parents[2]
DOTENV_PATH = BASE_DIR / ".env"


def _load_dotenv() -> None:
    if not DOTENV_PATH.exists():
        return
    for raw_line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    upload_dir: Path
    backup_dir: Path
    frontend_dir: Path
    jwt_secret: str
    token_expire_hours: int
    cookie_secure: bool
    cookie_samesite: str
    timezone: str
    app_version: str
    contract_no_prefix: str
    data_encryption_enabled: bool
    data_encryption_key: str

    def validate(self) -> None:
        if not self.jwt_secret:
            raise RuntimeError("JWT_SECRET must be set before the application can start.")
        if self.data_encryption_enabled:
            try:
                decode_key(self.data_encryption_key)
            except CryptoError as exc:
                raise RuntimeError(str(exc)) from exc

    @property
    def data_key(self) -> bytes:
        return decode_key(self.data_encryption_key)


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data"))).resolve()
    upload_dir = data_dir / "pdfs"
    backup_dir = data_dir / "backups"
    frontend_dir = BASE_DIR / "app" / "frontend_dist"

    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    cookie_samesite = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
    if cookie_samesite not in {"lax", "strict", "none"}:
        cookie_samesite = "lax"

    return Settings(
        data_dir=data_dir,
        upload_dir=upload_dir,
        backup_dir=backup_dir,
        frontend_dir=frontend_dir,
        jwt_secret=os.environ.get("JWT_SECRET", "").strip(),
        token_expire_hours=max(1, int(os.environ.get("TOKEN_EXPIRE_HOURS", "24"))),
        cookie_secure=_to_bool(os.environ.get("COOKIE_SECURE"), default=False),
        cookie_samesite=cookie_samesite,
        timezone=os.environ.get("APP_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
        app_version=os.environ.get("APP_VERSION", "dev").strip() or "dev",
        contract_no_prefix=os.environ.get("CONTRACT_NO_PREFIX", "LEASE").strip() or "LEASE",
        data_encryption_enabled=_to_bool(os.environ.get("DATA_ENCRYPTION_ENABLED"), default=False),
        data_encryption_key=os.environ.get("DATA_ENCRYPTION_KEY", "").strip(),
    )
