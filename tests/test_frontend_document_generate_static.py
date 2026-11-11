from __future__ import annotations

from pathlib import Path

from frontend_style_util import load_style_source


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_MODAL = ROOT / "frontend" / "src" / "components" / "DocumentGenerateModal.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _generate_source() -> str:
    source = _source(DOCUMENT_MODAL)
    start = source.index("async function generate")
    end = source.index("</script>", start)
    return source[start:end]


def _template_source() -> str:
    source = _source(DOCUMENT_MODAL)
    return source[source.index("<template>") :]


def test_document_generate_modal_has_stable_result_and_error_selectors() -> None:
    template = _template_source()

    for selector in [
        'data-testid="document-generate-modal"',
        'data-testid="document-generate-submit"',
        'data-testid="document-result-panel"',
        'data-testid="document-result-preview"',
        'data-testid="document-result-download"',
        'data-testid="document-generate-error"',
    ]:
        assert selector in template

    assert "文档类型" in template
    assert "文件名" in template
    assert "预览" in template
    assert "下载" in template


def test_document_generate_success_stays_in_modal_and_uses_explicit_actions() -> None:
    source = _source(DOCUMENT_MODAL)
    generate_source = _generate_source()

    assert "if (generating.value) return" in generate_source
    assert "generating.value = true" in generate_source
    assert "generatedDocument.value = result" in generate_source
    assert "emit('close')" not in generate_source
    assert "openHtml(result)" not in generate_source
    assert "downloadHtml(result)" not in generate_source
    assert "generate('open')" not in source
    assert "generate('download')" not in source
    assert '@click="previewGeneratedDocument"' in source
    assert '@click="downloadGeneratedDocument"' in source


def test_document_generate_failure_is_inline_and_close_clears_result_state() -> None:
    source = _source(DOCUMENT_MODAL)
    generate_source = _generate_source()
    template = _template_source()

    assert "generateError.value = '文档未生成，请检查合同、账期或收款信息后重试。'" in generate_source
    assert "emit('notify', { type: 'error'" not in generate_source
    assert "function clearDocumentState()" in source
    assert "generationRequestId += 1" in source
    assert "if (generationRequestId !== requestId || !props.open) return" in generate_source
    assert "generatedDocument.value = null" in source
    assert "generateError.value = ''" in source
    assert "@close=\"closeModal\"" in template
    assert "@click=\"closeModal\"" in template


def test_document_generate_visible_copy_stays_business_facing() -> None:
    template = _template_source().lower()

    for forbidden in ["payload", "debug", "provider", "prompt", "token", "api", "接口", "模型", "调试", "只读辅助"]:
        assert forbidden not in template


def test_document_generate_styles_keep_result_actions_responsive() -> None:
    style = load_style_source()
    start = style.index(".document-action-row")
    document_styles = style[start : style.index(".badge.compact", start)]

    for selector in [
        ".document-generate-error",
        ".document-result-panel",
        ".document-result-meta",
        ".document-result-actions",
        ".document-generate-submit:disabled",
    ]:
        assert selector in document_styles

    assert "@media (max-width: 640px)" in document_styles
    assert ".document-result-actions .btn" in document_styles
    assert "grid-template-columns: 1fr" in document_styles
