from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.building_operations_profile import (
    BUILDING_OPERATIONS_PROFILES,
    PROFILE_VERSION,
    BuildingOperationsProfile,
    DataAdapterType,
    resolve_building_operations_profile,
)


FORMAL_BUILDINGS = (
    ("DEMO_A", "示例双塔中心", "detail"),
    ("DEMO_B", "示例主裙楼中心", "snapshot"),
    ("DEMO_C", "示例单栋中心", "snapshot"),
    ("DEMO_D", "示例办公中心", "snapshot"),
    ("DEMO_E", "示例商务中心", "snapshot"),
)


def _building(code: str, name: str, ingestion_mode: str):
    return SimpleNamespace(code=code, name=name, ingestion_mode=ingestion_mode)


def test_five_formal_profiles_are_complete_typed_frozen_and_json_safe() -> None:
    assert set(BUILDING_OPERATIONS_PROFILES) == {
        "demo_a",
        "demo_b",
        "demo_c",
        "demo_d",
        "demo_e",
    }

    for code, name, ingestion_mode in FORMAL_BUILDINGS:
        profile = resolve_building_operations_profile(_building(code, name, ingestion_mode))
        assert isinstance(profile, BuildingOperationsProfile)
        assert profile.building_code == code
        assert profile.building_name == name
        assert profile.default_page in profile.available_tabs
        assert profile.brand.operations_title
        assert profile.brand.positioning
        assert profile.brand.operating_focus
        assert profile.space_structure.structure_type.value
        assert profile.data_granularity
        assert profile.analysis_groups
        assert profile.dashboard_groups

        public = profile.as_dict()
        assert public["profile_version"] == PROFILE_VERSION
        assert public["data_adapter"] in {
            "live_detail",
            "published_snapshot",
            "unavailable",
        }
        assert json.loads(json.dumps(public, ensure_ascii=False)) == public
        presentation = profile.presentation_dict()
        assert set(presentation) == {
            "profile_version",
            "building_code",
            "building_name",
            "brand",
            "space_structure",
            "data_granularity",
            "dashboard_groups",
        }
        assert "available_tabs" not in presentation
        assert "contract_number_rule" not in presentation
        assert "import_rule" not in presentation
        assert json.loads(json.dumps(presentation, ensure_ascii=False)) == presentation

    with pytest.raises(TypeError):
        BUILDING_OPERATIONS_PROFILES["new"] = BUILDING_OPERATIONS_PROFILES["demo_a"]
    with pytest.raises(FrozenInstanceError):
        BUILDING_OPERATIONS_PROFILES["demo_a"].building_name = "被修改"


def test_demo_a_profile_preserves_all_existing_phase1_pages() -> None:
    profile = resolve_building_operations_profile(
        _building(" demo_a ", "数据库展示名", " detail ")
    )

    assert profile.data_adapter is DataAdapterType.LIVE_DETAIL
    assert profile.adapter_unavailable_reason is None
    assert profile.brand.logo_url == "/lease-manager-mark.svg"
    assert profile.available_tabs == (
        "dashboard",
        "operations",
        "analytics",
        "evidence",
        "ai-assist",
        "risks",
        "contracts",
        "customers",
        "rooms",
        "receivables",
        "exchange",
    )
    assert [item.code for item in profile.space_structure.zones] == [
        "SOUTH_OFFICE",
        "NORTH_OFFICE",
        "COMMERCIAL_SUPPORT",
    ]
    assert [item.value for item in profile.data_granularity] == [
        "building",
        "zone",
        "floor",
        "leasable_unit",
    ]
    assert [item.code for item in profile.analysis_groups] == [
        "WHOLE_BUILDING",
        "SOUTH_OFFICE",
        "NORTH_OFFICE",
        "COMMERCIAL_SUPPORT",
    ]
    assert profile.contract_number_rule.as_dict() == {
        "supported": True,
        "prefix": "DEMO_A",
        "pattern": "DEMO_A-{year}-{sequence:03d}",
        "sequence_scope": "building_year",
        "unavailable_reason": None,
    }
    assert profile.import_rule.mode == "detail"
    assert profile.import_rule.requires_existing_units is True
    assert "exchange" not in profile.as_dict()["capability_unavailable_reasons"]


@pytest.mark.parametrize("code,name,_ingestion_mode", FORMAL_BUILDINGS[1:])
def test_formal_snapshot_profiles_expose_only_confirmed_snapshot_pages(
    code: str,
    name: str,
    _ingestion_mode: str,
) -> None:
    profile = resolve_building_operations_profile(_building(code, name, "snapshot"))

    assert profile.data_adapter is DataAdapterType.PUBLISHED_SNAPSHOT
    assert profile.brand.logo_url is None
    assert profile.available_tabs == ("dashboard", "evidence", "exchange")
    assert profile.default_page == "dashboard"
    assert profile.import_rule.as_dict() == {
        "supported": True,
        "mode": "published_snapshot",
        "template_key": "ops_v1_snapshot_exchange",
        "requires_existing_units": False,
        "unavailable_reason": None,
    }
    assert profile.contract_number_rule.supported is False
    public = profile.as_dict()
    for tab in (
        "operations",
        "analytics",
        "ai-assist",
        "risks",
        "contracts",
        "customers",
        "rooms",
        "receivables",
    ):
        assert tab not in public["available_tabs"]
        assert public["capability_unavailable_reasons"][tab]


def test_gjjt_uses_main_building_and_podium_without_claiming_segment_snapshot_data() -> None:
    profile = resolve_building_operations_profile(
        _building("DEMO_B", "示例主裙楼中心", "snapshot")
    )

    assert profile.space_structure.structure_type.value == "main_building_with_podium"
    assert [(item.code, item.name) for item in profile.space_structure.zones] == [
        ("MAIN_BUILDING", "主楼"),
        ("PODIUM", "裙楼"),
    ]
    whole, market = profile.analysis_groups
    assert whole.available is True
    assert whole.zone_codes == ("MAIN_BUILDING", "PODIUM")
    assert market.code == "MARKET_LEASING"
    assert market.zone_codes == ("MAIN_BUILDING",)
    assert market.available is False
    assert market.unavailable_reason


def test_unknown_snapshot_building_gets_a_safe_generic_whole_building_profile() -> None:
    profile = resolve_building_operations_profile(
        _building(" FUTURE_SNAPSHOT ", "未来快照楼", " SNAPSHOT ")
    )

    assert profile.building_code == "FUTURE_SNAPSHOT"
    assert profile.building_name == "未来快照楼"
    assert profile.data_adapter is DataAdapterType.PUBLISHED_SNAPSHOT
    assert profile.available_tabs == ("dashboard", "evidence", "exchange")
    assert [item.value for item in profile.data_granularity] == ["building"]
    assert profile.space_structure.zones == ()
    assert profile.space_structure.detail_unavailable_reason
    assert profile.contract_number_rule.supported is False


@pytest.mark.parametrize(
    ("ingestion_mode", "expected_reason"),
    (
        ("detail", "尚未注册明细 Adapter"),
        ("external", "外部数据来源尚未配置"),
        ("unknown", "尚未配置受支持的数据 Adapter"),
    ),
)
def test_unknown_non_snapshot_modes_fail_closed_without_fake_adapter(
    ingestion_mode: str,
    expected_reason: str,
) -> None:
    profile = resolve_building_operations_profile(
        _building("FUTURE_BUILDING", "未来楼宇", ingestion_mode)
    )

    assert profile.data_adapter is DataAdapterType.UNAVAILABLE
    assert profile.data_adapter.value != "external"
    assert profile.available_tabs == ("dashboard",)
    assert profile.default_page == "dashboard"
    assert expected_reason in (profile.adapter_unavailable_reason or "")
    assert profile.import_rule.supported is False
    assert profile.contract_number_rule.supported is False
    assert set(profile.as_dict()["capability_unavailable_reasons"]) == {
        "operations",
        "analytics",
        "evidence",
        "ai-assist",
        "risks",
        "contracts",
        "customers",
        "rooms",
        "receivables",
        "exchange",
    }


def test_known_profile_with_mismatched_ingestion_mode_fails_closed() -> None:
    profile = resolve_building_operations_profile(
        _building("DEMO_A", "示例双塔中心", "external")
    )

    assert profile.data_adapter is DataAdapterType.UNAVAILABLE
    assert profile.available_tabs == ("dashboard",)
    assert profile.default_page == "dashboard"
    assert "与运行档案要求的 detail 不一致" in (profile.adapter_unavailable_reason or "")
    assert all(not item.available for item in profile.analysis_groups)


def test_profile_can_only_remove_upstream_tabs_and_never_add_permission() -> None:
    detail = resolve_building_operations_profile(
        _building("DEMO_A", "示例双塔中心", "detail")
    )
    snapshot = resolve_building_operations_profile(
        _building("DEMO_E", "示例商务中心", "snapshot")
    )
    upstream = ["contracts", "dashboard", "exchange", "tools"]

    assert detail.restrict_tabs(upstream) == ("contracts", "dashboard", "exchange")
    assert snapshot.restrict_tabs(upstream) == ("dashboard", "exchange")
    assert set(detail.restrict_tabs(upstream)).issubset(upstream)
    assert set(snapshot.restrict_tabs(upstream)).issubset(upstream)


def test_unified_snapshot_orchestrator_has_no_building_code_special_case() -> None:
    source = (
        Path(__file__).parents[1] / "app" / "services" / "unified_operating_snapshot.py"
    ).read_text(encoding="utf-8")

    assert "LEGACY_BUILDING_CODE" not in source
    assert "DEMO_A" not in source
