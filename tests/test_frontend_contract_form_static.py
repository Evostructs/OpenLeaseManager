from __future__ import annotations

import re
from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FORM = ROOT / "frontend" / "src" / "components" / "ContractFormModal.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _template(source: str) -> str:
    match = re.search(r"<template>(.*)</template>", source, re.S)
    assert match is not None
    return match.group(1)


def _submit_source(source: str) -> str:
    start = source.index("function submit()")
    end = source.index("</script>", start)
    return source[start:end]


def test_contract_form_uses_business_sections_and_summary() -> None:
    source = _source(CONTRACT_FORM)
    template = _template(source)

    assert 'data-testid="contract-form-summary"' in template
    assert 'data-testid="contract-form-conflict-status"' in template
    for section in [
        "合同对象",
        "房源明细",
        "租期租金",
        "押金物业",
        "联系人",
        "备注",
    ]:
        assert section in template

    for selector in [
        'data-testid="contract-form-section-object"',
        'data-testid="contract-form-section-rooms"',
        'data-testid="contract-form-section-rent"',
        'data-testid="contract-form-section-deposit"',
        'data-testid="contract-form-section-contact"',
        'data-testid="contract-form-section-notes"',
    ]:
        assert selector in template

    for computed_name in [
        "summaryCustomerName",
        "summaryRoomCount",
        "summaryAreaText",
        "summaryRentText",
        "summaryLeaseWindowText",
        "summaryConflictText",
    ]:
        assert computed_name in source


def test_contract_form_keeps_room_checks_and_save_payload_shape() -> None:
    source = _source(CONTRACT_FORM)
    template = _template(source)
    submit_source = _submit_source(source)

    assert "roomConflictItems" in source
    assert "roomDateErrorItems" in source
    assert "fetchAvailableRooms" in source
    assert "roomContractOverlapsItem" in source
    assert "applyRoomSelection(index)" in template
    assert "addRoomItem" in template
    assert "removeRoomItem(index)" in template

    assert "emit('save', {" in submit_source
    assert "...form" in submit_source
    assert "customer_id: form.customer_id || null" in submit_source
    assert "room_items: form.room_items.map" in submit_source
    assert "start_date: item.start_date || null" in submit_source
    assert "end_date: item.end_date || null" in submit_source


def test_contract_form_save_stays_in_modal_footer() -> None:
    template = _template(_source(CONTRACT_FORM))
    footer_start = template.index("<template #footer>")
    footer_source = template[footer_start:]
    body_source = template[:footer_start]

    assert 'data-testid="contract-form-save"' in footer_source
    assert '@click="submit"' in footer_source
    assert ':disabled="!canSubmit || saving"' in footer_source
    assert 'data-testid="contract-form-save"' not in body_source


def test_contract_form_uses_info_hint_instead_of_long_title_copy() -> None:
    template = _template(_source(CONTRACT_FORM))
    visible_text = re.sub(r"<[^>]+>", " ", template)

    assert "InfoHint" in _source(CONTRACT_FORM)
    assert "月租由房源面积和日租单价汇总" in template
    assert 'title="按租金单价' not in template
    assert 'title="默认按月租金' not in template

    for forbidden in [
        "prompt",
        "token",
        "provider",
        "model",
        "接口",
        "调试",
        "JSON",
    ]:
        assert forbidden not in visible_text


def test_contract_form_css_prevents_narrow_layout_overflow() -> None:
    style = load_style_source()

    assert ".contract-form-summary" in style
    assert "position: sticky" in style
    assert ".contract-form-grid" in style
    assert ".contract-room-items" in style
    assert "overflow-x: auto" in style
    assert ".contract-room-row" in style
    assert "min-width: 900px" in style
    assert "text-overflow: ellipsis" in style
    assert "@media (max-width: 1024px)" in style
    assert "@media (max-width: 640px)" in style
