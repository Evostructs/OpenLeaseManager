from __future__ import annotations

from typing import Iterable, Protocol


TOWER_RENAMES = {
    "南塔裙楼": "商业配套",
}

LEGACY_TOWER_RULE_BUILDING_CODE = "DEMO_A"

OFFICE_TOWER_GROUPS = {
    "南塔": "南塔办公",
    "北塔": "北塔办公",
}


class OperationsGroupDescriptor(Protocol):
    code: str
    name: str
    zone_codes: tuple[str, ...]
    available: bool


def normalize_tower(value: object) -> str:
    text = str(value or "").strip()
    return TOWER_RENAMES.get(text, text)


def normalize_tower_for_building(value: object, building_code: object = None) -> str:
    """Apply the historical DEMO_A rename only inside DEMO_A.

    Other buildings own their registered zone names.  A missing building code
    therefore fails closed to the stored text instead of borrowing DEMO_A rules.
    """
    text = str(value or "").strip()
    if str(building_code or "").strip().casefold() == LEGACY_TOWER_RULE_BUILDING_CODE.casefold():
        return normalize_tower(text)
    return text


def _parse_floor(value: object) -> int | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text.startswith("B"):
        try:
            return -int(text[1:] or "0")
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


def leasing_group(tower: object, *, floor: object = None, room_type: object = None, room_no: object = None) -> str:
    normalized_tower = normalize_tower(tower)
    type_text = str(room_type or "").strip()
    floor_number = _parse_floor(floor)

    if normalized_tower == "商业配套" or (type_text and type_text != "办公"):
        return "商业配套"
    if normalized_tower in {"南塔", "北塔"} and floor_number in {1, 2}:
        return "商业配套"
    if normalized_tower in OFFICE_TOWER_GROUPS:
        return OFFICE_TOWER_GROUPS[normalized_tower]

    room_no_text = str(room_no or "").strip().upper()
    if room_no_text.startswith("P"):
        return "商业配套"
    if room_no_text.startswith("S"):
        return OFFICE_TOWER_GROUPS["南塔"]
    if room_no_text.startswith("N"):
        return OFFICE_TOWER_GROUPS["北塔"]
    return normalized_tower or "未标注"


def operations_group_codes(
    groups: Iterable[OperationsGroupDescriptor],
    *,
    zone_code: object = None,
    tower: object = None,
    floor: object = None,
    room_type: object = None,
    room_no: object = None,
) -> tuple[str, ...]:
    """Resolve profile groups from a room's registered zone, then its legacy tower facts."""
    available_groups = tuple(item for item in groups if item.available)
    normalized_zone = str(zone_code or "").strip().casefold()
    resolved: list[str] = [
        item.code
        for item in available_groups
        if str(item.code).strip().upper() == "WHOLE_BUILDING"
    ]

    if normalized_zone:
        resolved.extend(
            item.code
            for item in available_groups
            if str(item.code).strip().upper() != "WHOLE_BUILDING"
            and normalized_zone in {str(code).strip().casefold() for code in item.zone_codes}
        )

    if len(resolved) == 1 or not resolved:
        legacy_name = leasing_group(
            tower,
            floor=floor,
            room_type=room_type,
            room_no=room_no,
        )
        resolved.extend(
            item.code
            for item in available_groups
            if str(item.code).strip().upper() != "WHOLE_BUILDING"
            and item.name == legacy_name
        )

    return tuple(dict.fromkeys(resolved))
