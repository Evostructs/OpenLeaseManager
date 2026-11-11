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


def _to_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _to_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    upload_dir: Path
    attachment_dir: Path
    backup_dir: Path
    frontend_dir: Path
    jwt_secret: str
    token_expire_hours: int
    cookie_secure: bool
    cookie_samesite: str
    timezone: str
    app_version: str
    data_encryption_enabled: bool
    data_encryption_key: str
    backup_retention_days: int
    backup_min_keep: int
    backup_mirror_dir: Path | None
    ai_assist_enabled: bool
    ai_assist_api_key: str
    ai_assist_model: str
    ai_assist_base_url: str
    ai_assist_timeout_seconds: float
    max_request_size_bytes: int
    max_import_file_size_bytes: int
    min_password_length: int

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
    attachment_dir = data_dir / "attachments"
    backup_dir = data_dir / "backups"
    frontend_dir = BASE_DIR / "app" / "frontend_dist"

    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    cookie_samesite = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
    if cookie_samesite not in {"lax", "strict", "none"}:
        cookie_samesite = "lax"

    return Settings(
        data_dir=data_dir,
        upload_dir=upload_dir,
        attachment_dir=attachment_dir,
        backup_dir=backup_dir,
        frontend_dir=frontend_dir,
        jwt_secret=os.environ.get("JWT_SECRET", "").strip(),
        token_expire_hours=max(1, _to_int(os.environ.get("TOKEN_EXPIRE_HOURS"), 24)),
        cookie_secure=_to_bool(os.environ.get("COOKIE_SECURE"), default=False),
        cookie_samesite=cookie_samesite,
        timezone=os.environ.get("APP_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
        app_version=os.environ.get("APP_VERSION", "dev").strip() or "dev",
        data_encryption_enabled=_to_bool(os.environ.get("DATA_ENCRYPTION_ENABLED"), default=False),
        data_encryption_key=os.environ.get("DATA_ENCRYPTION_KEY", "").strip(),
        backup_retention_days=max(7, _to_int(os.environ.get("BACKUP_RETENTION_DAYS"), 30)),
        backup_min_keep=max(3, _to_int(os.environ.get("BACKUP_MIN_KEEP"), 10)),
        backup_mirror_dir=(
            Path(os.environ["BACKUP_MIRROR_DIR"]).resolve()
            if os.environ.get("BACKUP_MIRROR_DIR", "").strip()
            else None
        ),
        ai_assist_enabled=_to_bool(os.environ.get("AI_ASSIST_ENABLED"), default=False),
        ai_assist_api_key=os.environ.get("AI_ASSIST_API_KEY", "").strip(),
        ai_assist_model=os.environ.get("AI_ASSIST_MODEL", "").strip(),
        ai_assist_base_url=os.environ.get("AI_ASSIST_BASE_URL", "").strip(),
        ai_assist_timeout_seconds=max(1.0, _to_float(os.environ.get("AI_ASSIST_TIMEOUT_SECONDS"), 12.0)),
        max_request_size_bytes=max(1, _to_int(os.environ.get("MAX_REQUEST_SIZE_MB"), 220)) * 1024 * 1024,
        max_import_file_size_bytes=max(1, _to_int(os.environ.get("MAX_IMPORT_FILE_SIZE_MB"), 20)) * 1024 * 1024,
        min_password_length=min(64, max(8, _to_int(os.environ.get("MIN_PASSWORD_LENGTH"), 12))),
    )
