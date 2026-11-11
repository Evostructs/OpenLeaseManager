from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings


DEFAULT_TIMEZONE = "Asia/Shanghai"


def app_timezone() -> ZoneInfo:
    timezone_name = get_settings().timezone or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def now_in_timezone() -> datetime:
    return datetime.now(app_timezone()).replace(tzinfo=None)


def today_in_timezone() -> date:
    return now_in_timezone().date()
