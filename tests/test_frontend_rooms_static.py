from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
ROOMS_VIEW = ROOT / "frontend" / "src" / "views" / "RoomsView.vue"
ROOMS_DOMAIN = ROOT / "frontend" / "src" / "lib" / "roomsDomain.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _delete_flow_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index("function deleteRoom")
    end = source.index("async function openRoomContracts")
    return source[start:end]


def _heatmap_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index('data-testid="rooms-heatmap"')
    end = source.index('<article class="card table-card contracts-shell">')
    return source[start:end]


def _workspace_summary_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index('data-testid="rooms-workspace-summary"')
    end = source.index('data-testid="rooms-heatmap"', start)
    return source[start:end]


def _toolbar_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index('class="action-cluster room-action-cluster"')
    end = source.index('<div class="table-wrapper">', start)
    return source[start:end]


def _room_table_action_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index('<td class="actions-cell">', source.index('v-for="room in paginatedRooms"'))
    end = source.index("</td>", source.index('data-testid="room-row-more-menu"', start)) + len("</td>")
    return source[start:end]


def _room_row_more_menu_source() -> str:
    action_source = _room_table_action_source()
    start = action_source.index('data-testid="room-row-more-menu"')
    return action_source[start:]


def _room_modal_operation_source() -> str:
    source = _source(ROOMS_VIEW)
    start = source.index('class="room-operation-detail"')
    end = source.index('<div v-if="roomContractDetail.contracts?.length"', start)
    return source[start:end]


def test_room_delete_flow_uses_app_native_modal() -> None:
    source = _source(ROOMS_VIEW)
    delete_source = _delete_flow_source()

    assert "window.confirm" not in source
    assert "window.confirm" not in delete_source
    assert "BaseModal" in source
    assert 'data-testid="room-delete-action"' in source
    assert 'data-testid="room-delete-confirm-modal"' in source
    assert 'data-testid="room-delete-cancel"' in source
    assert 'data-testid="room-delete-confirm"' in source
    assert "function closeRoomDeleteConfirm()" in delete_source
    assert "async function confirmDeleteRoom()" in delete_source


def test_room_delete_call_keeps_endpoint_and_success_behavior() -> None:
    delete_source = _delete_flow_source()

    assert "await api(`/api/rooms/${row.id}`, { method: 'DELETE' })" in delete_source
    assert "{ method: 'DELETE' }" in delete_source
    assert "body:" not in delete_source
    assert "emit('notify', { text: '房间已删除' })" in delete_source
    assert "await fetchRooms()" in delete_source


def test_rooms_operator_map_keeps_stable_selectors_and_modal_drill_in() -> None:
    source = _source(ROOMS_VIEW)
    domain_source = _source(ROOMS_DOMAIN)
    heatmap_source = _heatmap_source()

    for selector in [
        'data-testid="rooms-workspace-summary"',
        'data-testid="room-status-overview"',
        'data-testid="rooms-status-strip"',
        ':data-testid="`room-status-segment-${item.key}`"',
        'data-testid="rooms-segment-summary"',
        'data-testid="rooms-priority-list"',
        'data-testid="room-priority-action-card"',
        ':data-testid="`room-priority-lane-toggle-${lane.key}`"',
        'data-testid="room-vacancy-top3"',
        'data-testid="rooms-vacancy-pressure-row"',
        'data-testid="rooms-segment-pressure-bar"',
        'data-testid="rooms-table"',
        'data-testid="rooms-occupancy-filter"',
        'data-testid="rooms-zone-filter"',
        'data-testid="rooms-operator-map"',
        'data-testid="rooms-heatmap"',
        'data-testid="rooms-heatmap-signal-strip"',
        '`rooms-heatmap-signal-${item.key}`',
        'data-testid="room-floor-map"',
        'data-testid="rooms-heatmap-grid"',
    ]:
        assert selector in source

    assert "export function roomHeatmapTestId(room)" in domain_source
    assert ':data-testid="roomHeatmapTestId(room)"' in heatmap_source
    assert '@click="openRoomContracts(room)"' in heatmap_source
    assert ':aria-pressed="selectedRoomId === room.id"' in heatmap_source
    assert "const expandedPriorityLanes = ref([])" in source
    assert "const heatmapSignalSummary = computed" in source
    assert "function togglePriorityLane(key)" in source
    assert 'role="button"' in source
    assert '@click="openRoomContracts(room)"' in source
    assert '@click.stop="openRoomContract(room)"' in source
    assert '@click.stop="openRoomCustomer(room)"' in source
    assert '@click="setTowerFilter(lane.filterValue)"' in source


def test_rooms_operator_map_consumes_existing_operation_fields_without_new_endpoints() -> None:
    source = _source(ROOMS_VIEW)
    domain_source = _source(ROOMS_DOMAIN)

    assert "params.set('include_operations', 'true')" in source
    assert "contract: room.contract || null" in domain_source
    assert "signals: room.signals || {}" in domain_source
    assert "operating_statuses: room.operating_statuses || []" in domain_source
    assert "room.contract?.tenant_name" in source
    assert "room.signals?.overdue" in source
    assert "roomOperation.receivable?.overdue_balance" in source
    assert "roomOperation.attachment_summary?.missing_required" in source
    assert "roomOperation.follow_up" in source

    for forbidden_endpoint in [
        "/api/rooms/map",
        "/api/rooms/heatmap",
        "/api/room-operations",
        "floor=",
    ]:
        assert forbidden_endpoint not in source


def test_room_row_actions_are_folded_into_more_menu() -> None:
    action_source = _room_table_action_source()
    menu_source = _room_row_more_menu_source()
    before_menu = action_source.split('data-testid="room-row-more-menu"', maxsplit=1)[0]

    assert 'data-testid="room-row-detail-action"' in action_source
    assert 'data-testid="room-row-more-actions"' in action_source
    assert 'data-testid="room-row-more-menu"' in action_source
    assert '>{{ $t("详情") }}</button>' in before_menu
    assert re.search('>\\s*\\{\\{ \\$t\\(" 更多 "\\) \\}\\}\\s*</button>', before_menu)

    assert "查看合同" in menu_source
    assert "查看客户" in menu_source
    assert "编辑房间" in menu_source
    assert "删除房间" in menu_source
    assert "关联对象" in menu_source
    assert "资料维护" in menu_source
    assert "危险操作" in menu_source
    assert 'data-testid="room-delete-action"' in menu_source

    assert "编辑房间" not in before_menu
    assert "删除房间" not in before_menu
    assert ':title="$t(&quot;编辑房间&quot;)"' not in before_menu
    assert ':title="$t(&quot;删除房间&quot;)"' not in before_menu


def test_room_row_more_menu_has_mobile_safe_styles() -> None:
    style = load_style_source()

    for selector in [
        ".room-row-more-actions",
        ".room-row-more-popover",
        ".room-row-more-section",
    ]:
        assert selector in style

    assert re.search(
        r"\.room-row-more-popover\s*\{[^}]*width:\s*min\(320px,\s*calc\(100vw - 48px\)\)",
        style,
        re.DOTALL,
    )
    assert re.search(
        r"\.room-ledger-table \.actions-cell\s*\{[^}]*width:\s*128px",
        style,
        re.DOTALL,
    )


def test_room_detail_modal_uses_action_lane_instead_of_flat_button_row() -> None:
    modal_source = _room_modal_operation_source()

    assert 'class="room-operation-action-lane"' in modal_source
    assert 'data-testid="room-operation-action-lane"' in modal_source
    assert "room-operation-primary-action" in modal_source
    assert "room-operation-secondary-actions" in modal_source
    assert "查看合同" in modal_source
    assert "查看客户" in modal_source
    assert "登记跟进" in modal_source
    assert "查看附件" in modal_source
    assert 'class="room-operation-actions"' not in modal_source


def test_rooms_vacancy_pressure_layer_is_frontend_only_and_click_filters_existing_state() -> None:
    source = _source(ROOMS_VIEW)
    summary_source = _workspace_summary_source()

    assert "const vacancyPressureRows = computed(() => {" in source
    assert "if (room.is_leasable === false) continue" in source
    assert "const vacant = !room.occupied || room.signals?.vacant" in source
    assert "if (roomMatchesPrioritySignal(room)) bucket.priorityCount += 1" in source
    assert "right.vacantArea - left.vacantArea" in source
    assert "right.vacantCount - left.vacantCount" in source
    assert "right.priorityCount - left.priorityCount" in source
    assert ".slice(0, 3)" in source

    assert '@click="applyVacancyPressureRow(row)"' in summary_source
    assert "function applyVacancyPressureRow(row)" in source
    assert "filters.tower = row.towerFilter || ''" in source
    assert "filters.occupancy = '空置'" in source
    assert "if (row.zone) filters.zone = row.zone" in source
    assert "preserveViewportAfterFilterChange()" in source
    assert "params.set('floor'" not in source
    assert "filters.floor" not in source


def test_rooms_first_screen_replaces_metric_wall_with_status_overview_and_floor_map() -> None:
    source = _source(ROOMS_VIEW)
    summary_source = _workspace_summary_source()

    assert "房态总图" in summary_source
    assert 'class="room-status-overview"' in summary_source
    assert 'class="room-status-stack"' in summary_source
    assert 'class="room-status-segment"' in summary_source
    assert '{{ $t(occupancyRate) }}%' in summary_source
    assert "空置集中 Top 3" in summary_source
    assert 'data-testid="room-floor-map"' in source
    assert ".rooms-heatmap-panel" in load_style_source()
    assert "order: -1" in load_style_source()

    assert 'v-for="metric in operatingMetrics"' not in source
    assert "const operatingMetrics" not in source
    assert "月租 {{ formatCurrencyFixed(segment.rent) }}" not in summary_source
    assert "待处理房间关联月租" not in summary_source


def test_rooms_filter_chips_preserve_page_workflow_without_navigation() -> None:
    source = _source(ROOMS_VIEW)

    assert "filters.occupancy = filters.occupancy === value ? '' : value" in source
    assert "filters.tower = filters.tower === value ? '' : value" in source
    assert "@click=\"setOccupancyFilter(item.filter)\"" in source
    assert '@click="setTowerFilter(segment.filterValue)"' in source
    assert "preserveViewportAfterFilterChange()" in source


def test_room_batch_actions_are_folded_out_of_primary_toolbar() -> None:
    source = _source(ROOMS_VIEW)
    toolbar_source = _toolbar_source()

    assert "const roomBatchActionsOpen = ref(false)" in source
    assert "function closeRoomBatchActions()" in source
    assert "function toggleRoomBatchActions()" in source
    assert 'data-testid="room-batch-actions"' in toolbar_source
    assert 'data-testid="room-batch-trigger"' in toolbar_source
    assert 'data-testid="room-batch-panel"' in toolbar_source

    before_panel = toolbar_source.split('data-testid="room-batch-panel"', maxsplit=1)[0]
    panel_block = toolbar_source.split('data-testid="room-batch-panel"', maxsplit=1)[1]

    assert "批量处理" in before_panel
    assert "新增房间" in toolbar_source
    assert toolbar_source.index("新增房间") < toolbar_source.index('data-testid="room-batch-actions"')
    assert "下载模板" not in before_panel
    assert "导入房间" not in before_panel
    assert "导出房间" not in before_panel
    assert "downloadRoomTemplateFromBatch" in panel_block
    assert "triggerRoomImportFromBatch" in panel_block
    assert "exportRoomsFromBatch" in panel_block
    assert 'ref="importInput"' in toolbar_source


def test_room_batch_menu_has_mobile_safe_styles() -> None:
    style = load_style_source()

    for selector in [
        ".room-batch-actions",
        ".room-batch-trigger",
        ".room-batch-popover",
        ".room-batch-grid",
        ".room-batch-buttons",
    ]:
        assert selector in style

    assert re.search(
        r"\.room-batch-popover\s*\{[^}]*width:\s*min\(360px,\s*calc\(100vw - 64px\)\)",
        style,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width:\s*1280px\).*?\.room-batch-popover\s*\{[^}]*left:\s*0",
        style,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width:\s*640px\).*?\.room-batch-trigger\s*\{[^}]*width:\s*100%",
        style,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width:\s*640px\).*?\.room-batch-grid\s*\{[^}]*grid-template-columns:\s*1fr",
        style,
        re.DOTALL,
    )
