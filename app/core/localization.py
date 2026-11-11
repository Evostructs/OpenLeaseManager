"""Presentation-only localization. Stored values and API field names stay stable."""
from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path
import re

# Unversioned integrations keep their existing Chinese workbook format unless
# they explicitly request English. The browser always sends its chosen locale.
request_locale: ContextVar[str] = ContextVar("request_locale", default="zh-CN")
_catalog_dir = Path(__file__).resolve().parents[1] / "i18n"
MESSAGES = json.loads((_catalog_dir / "en.json").read_text(encoding="utf-8"))
_patterns = []
for _key in json.loads((_catalog_dir / "patterns.json").read_text(encoding="utf-8")):
    _slots = re.findall(r"\{(\d+)\}", _key)
    if _slots and _key in MESSAGES:
        _regex = "^" + "(.*?)".join(re.escape(x) for x in re.split(r"\{\d+\}", _key)) + "$"
        _patterns.append((len(re.sub(r"\{\d+\}", "", _key)), re.compile(_regex, re.S), _slots, MESSAGES[_key]))
_patterns.sort(key=lambda item: item[0], reverse=True)


@lru_cache(maxsize=3000)
def english(text: str) -> str:
    key = re.sub(r"\s+", " ", text).strip()
    if key in MESSAGES:
        return MESSAGES[key]
    for _, regex, slots, translation in _patterns:
        match = regex.fullmatch(key)
        if match:
            args = dict(zip(slots, match.groups()))
            return re.sub(r"\{(\d+)\}", lambda m: args.get(m[1], ""), translation)
    return text


def display(value):
    return english(value) if request_locale.get() == "en" and isinstance(value, str) else value


def header_key(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def bilingual_aliases(aliases: dict) -> dict:
    result = dict(aliases)
    for label, field in aliases.items():
        result[header_key(label)] = field
        result[header_key(english(label))] = field
        if field:
            result[header_key(field)] = field
    return result


# A field-specific allowlist prevents words in tenant names, notes, contacts or
# arbitrary property identifiers from being changed by an import.
ENUM_VALUES = {
    "lease_type": ("新签", "续签", "换签", "变更签", "扩租", "减租"),
    "room_type": ("办公", "商业", "商铺", "餐饮", "健身", "其他"),
    "follow_up_type": ("电话", "微信", "拜访", "邮件", "催收", "续租", "其他"),
    "deposit_type": ("履约保证金", "租赁押金", "其他"),
    "disposal": ("续租", "退租", "待定"),
    "deposit_status": ("未收", "已收", "部分收取", "已退", "已冲抵"),
}


def import_value(field: str, value):
    if not isinstance(value, str):
        return value
    for original in ENUM_VALUES.get(field, ()):
        if value.strip().casefold() == english(original).casefold():
            return original
    # These statuses are already language-neutral in storage.
    if field == "status":
        return {"active": "active", "inactive": "inactive", "enabled": "active", "disabled": "inactive",
                "open": "open", "done": "done", "incomplete": "open", "completed": "done"}.get(value.strip().casefold(), value)
    return value


def import_demo_tower(value, building_code):
    if str(building_code).casefold() != "demo_a" or not isinstance(value, str):
        return value
    aliases = {english(name).casefold(): name for name in ("南塔", "北塔", "商业配套")}
    return aliases.get(value.strip().casefold(), value)


def localize_static_html(template: str) -> str:
    """Only for trusted, static HTML literals, never interpolated user content."""
    from html import escape
    return re.sub(r">([^<>]+)<", lambda m: ">" + escape(display(m[1])) + "<", template)
