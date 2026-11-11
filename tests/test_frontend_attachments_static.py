from __future__ import annotations

from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
ATTACHMENT_PANEL = ROOT / "frontend" / "src" / "components" / "AttachmentPanel.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _remove_attachment_source() -> str:
    source = _source(ATTACHMENT_PANEL)
    start = source.index("function removeAttachment")
    end = source.index("function closeRemoveAttachmentConfirm")
    return source[start:end]


def _attachment_delete_flow_source() -> str:
    source = _source(ATTACHMENT_PANEL)
    start = source.index("function removeAttachment")
    end = source.index("watch(")
    return source[start:end]


def _template_source() -> str:
    source = _source(ATTACHMENT_PANEL)
    return source[source.index("<template>") :]


def test_attachment_panel_has_evidence_chain_landmarks() -> None:
    source = _source(ATTACHMENT_PANEL)
    template = _template_source()

    assert 'data-testid="attachment-panel"' in template
    assert 'data-testid="attachment-status"' in template
    assert 'data-testid="attachment-summary"' in template
    assert 'data-testid="attachment-upload-row"' in template
    assert 'data-testid="attachment-list"' in template
    assert ':data-testid="`attachment-item-${item.id}`"' in template
    assert "附件证据链" in template
    assert "缺资料" in source
    assert "资料已登记" in source
    assert "totalAttachments" in source
    assert "coveredCategoryCount" in source
    assert "categoryEntries" in source
    assert '{{ $t("上传人 ") }}{{ item.uploaded_by || \'--\' }}' in template
    assert "当前缺少" in template
    assert "下一步建议上传" in template


def test_attachment_panel_copy_avoids_engineering_terms() -> None:
    template = _template_source()

    for term in ["payload", "debug", "provider", "prompt", "token", "API", "接口", "模型", "调试", "只读辅助"]:
        assert term not in template


def test_attachment_panel_mobile_layout_prevents_overflow() -> None:
    style = load_style_source()

    assert ".attachment-panel" in style
    assert ".attachment-file-name" in style
    assert "word-break: break-word;" in style
    assert ".attachment-notes" in style
    assert "overflow-wrap: anywhere;" in style
    assert "@media (max-width: 640px)" in style
    assert ".attachment-item," in style
    assert ".attachment-evidence" in style
    assert "grid-template-columns: minmax(0, 1fr);" in style
    assert ".attachment-actions" in style


def test_attachment_delete_uses_app_native_modal() -> None:
    source = _source(ATTACHMENT_PANEL)
    delete_source = _attachment_delete_flow_source()
    remove_source = _remove_attachment_source()

    assert "window.confirm" not in delete_source
    assert "import BaseModal from './BaseModal.vue'" in source
    assert "<BaseModal" in source
    assert ':title="$t(&quot;删除附件&quot;)"' in source
    assert "const attachmentDeleteConfirm = reactive({ item: null, deleting: false })" in source
    assert "function closeRemoveAttachmentConfirm()" in source
    assert "async function confirmRemoveAttachment()" in source
    assert "if (!props.canDelete) return" in remove_source
    assert "if (!item?.id) return" in remove_source
    assert "attachmentDeleteConfirm.item = item" in remove_source
    assert ':data-testid="`attachment-delete-action-${item.id}`"' in source
    assert 'data-testid="attachment-delete-confirm-modal"' in source
    assert 'data-testid="attachment-delete-cancel"' in source
    assert 'data-testid="attachment-delete-confirm"' in source
    assert "attachmentDeleteConfirm.item?.file_name || '--'" in source
    assert "删除后该附件将从当前业务记录中移除" in source


def test_attachment_delete_keeps_endpoint_method_and_events() -> None:
    delete_source = _attachment_delete_flow_source()

    assert "`/api/attachments/${item.id}`" in delete_source
    assert "{ method: 'DELETE' }" in delete_source
    assert "body:" not in delete_source
    assert "emit('notify', { text: '附件已删除' })" in delete_source
    assert "emit('refresh')" in delete_source
    assert "emit('notify', { type: 'error', text: error.message })" in delete_source
