from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.vue"
PRESENTATION = ROOT / "frontend" / "src" / "components" / "PresentationView.vue"
PROFILE_DOMAIN = ROOT / "frontend" / "src" / "lib" / "buildingOperationsProfile.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_app_passes_selected_central_profile_to_homepage_and_presentation() -> None:
    source = _source(APP)

    assert "const selectedBuildingOperationsProfile = computed" in source
    assert "building?.operations_profile || {}" in source
    assert "operationsProfile: selectedBuildingOperationsProfile.value" in source
    assert ':operations-profile="activePresentationOperationsProfile"' in source


def test_snapshot_presentation_uses_profile_groups_without_building_name_special_cases() -> None:
    source = _source(PRESENTATION)

    assert "buildBuildingOperationsProfileView(presentationOperationsProfile.value)" in source
    assert "props.snapshot?.scope?.operations_profile || {}" in source
    assert 'data-testid="presentation-building-profile"' in source
    assert '`presentation-profile-group-${group.code}`' in source
    assert 'data-testid="presentation-profile-safety"' in source
    assert "DEMO_B" not in source
    assert "DEMO_C" not in source


def test_profile_view_model_maps_shape_types_and_never_invents_floor_rows() -> None:
    source = _source(PROFILE_DOMAIN)

    for shape in (
        "multi_tower_with_commercial",
        "single_building",
        "main_building_with_podium",
        "whole_building",
    ):
        assert shape in source
    assert "structure.zones" in source
    assert "detail_unavailable_reason" in source
    assert "暂无分区数据" in source
    assert "DEMO_B" not in source
    assert "DEMO_C" not in source
