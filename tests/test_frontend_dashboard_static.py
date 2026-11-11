from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_VIEW = ROOT / "frontend" / "src" / "views" / "DashboardView.vue"


def _source() -> str:
    return DASHBOARD_VIEW.read_text(encoding="utf-8")


def test_dashboard_today_board_uses_existing_operations_payload() -> None:
    source = _source()

    assert "const priorityLanes = computed" in source
    assert "const businessSignalCounts = computed" in source
    assert "operations.value.queues || {}" in source
    assert "operations.value.queue_counts || {}" in source
    assert "operations.value.current_month || {}" in source
    assert "props.stats.receivables || {}" in source
    assert "props.stats.active_count" in source
    assert "operations.value.todos || []" in source


def test_dashboard_today_board_exposes_lane_selectors() -> None:
    source = _source()

    for test_id in [
        'data-testid="today-health-overview"',
        'data-testid="today-signal-lanes"',
        'data-testid="today-top-actions"',
        "today-signal-lane-payment",
        "today-signal-lane-followup",
        "today-signal-lane-materials",
        "today-signal-lane-expiry",
        "today-signal-lane-normal",
    ]:
        assert test_id in source

    assert "const selectedTrafficTone = ref('')" in source
    assert "const normalProgressLane = computed" in source
    assert "const filteredPriorityLanes = computed" in source
    assert "tone === 'green'" in source
    assert '@click="selectTrafficTone(item.key)"' in source
    assert ':aria-pressed="selectedTrafficTone === item.key"' in source
    assert "v-for=\"lane in filteredPriorityLanes\"" in source


def test_dashboard_today_board_limits_homepage_queue_detail() -> None:
    source = _source()
    template = source[source.index("<template>") :]

    assert "slice(0, 3)" in source
    assert 'data-testid="today-open-operations-queue"' in source
    assert "dashboard-lane-row" not in template


def test_dashboard_keeps_page_load_endpoint_boundary() -> None:
    source = _source()
    template = source[source.index("<template>") :]

    assert "api(" not in source
    assert source.count("/api/") == 1
    assert "openDownload('/api/reports/operations-daily')" in source
    assert "/api/dashboard/operations" not in source
    assert "/api/dashboard/operations/trend" not in source
    assert "/api/reports/operations-daily" not in template


def test_dashboard_requires_explicit_live_detail_projection_and_otherwise_fails_closed() -> None:
    source = _source()

    assert "operations.value?.projection?.detail_lists_available === true" in source
    assert 'v-if="detailListsAvailable"' in source
    assert 'data-testid="snapshot-dashboard-summary"' in source
    assert "不把未知数据当作 0" in source


def test_snapshot_dashboard_renders_profile_driven_building_shape_and_safe_groups() -> None:
    source = _source()

    assert "buildBuildingOperationsProfileView(props.operationsProfile)" in source
    assert 'data-testid="building-operations-profile"' in source
    assert ':data-structure="operationsProfileView.structureType"' in source
    assert '`building-profile-group-${group.code}`' in source
    assert 'data-testid="building-profile-safety"' in source
    assert "不会用整栋数字反推或代替" not in source
    assert "DEMO_B" not in source
    assert "DEMO_C" not in source
