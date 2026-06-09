from pathlib import Path


HTML = Path("static/index.html").read_text(encoding="utf-8")


def test_focus_review_workbench_ui_helpers_exist() -> None:
    assert "function reviewFieldWorkbenchPanel" in HTML
    assert "field-review-workbench" in HTML
    assert "data-field-key" in HTML
    assert "Use checked value" in HTML
    assert "previous_confirmed_value" in HTML
    assert "rule_candidate" in HTML


def test_review_detail_renders_workbench_before_resolution_controls() -> None:
    detail_start = HTML.index("function renderReviewDetail")
    detail_end = HTML.index("function renderAssistantReviewDraft", detail_start)
    detail_block = HTML[detail_start:detail_end]
    assert "${reviewFieldWorkbenchPanel(item)}" in detail_block
    assert detail_block.index("${reviewFieldWorkbenchPanel(item)}") < detail_block.index(
        "${reviewResolutionControls(item)}"
    )


def test_field_workbench_handler_fills_resolution_form() -> None:
    assert "attachFieldReviewWorkbenchHandlers" in HTML
    assert ".use-field-review-value" in HTML
    assert "workbench?.nextElementSibling?.querySelector(\".review-actions\")" in HTML
    assert ".review-resolved-value" in HTML
    assert ".review-resolution-note" in HTML
    assert "Checked from source photo" in HTML


def test_field_workbench_exposes_photo_and_roi_controls() -> None:
    assert "field-review-photo-frame" in HTML
    assert "roi_preview_url" in HTML
    assert "Open source photo" in HTML
    assert "Source cage-card photo for field review" in HTML


def test_field_workbench_photo_has_missing_image_fallback() -> None:
    start = HTML.index("function reviewFieldWorkbenchPanel")
    end = HTML.index("function renderCanonicalApplyPreview", start)
    workbench_block = HTML[start:end]
    assert 'const photoState = photo.image_url ? "ready" : "missing"' in workbench_block
    assert 'data-source-photo-state="${escapeHtml(photoState)}"' in workbench_block
    assert "onerror=" in workbench_block
    assert "Source photo unavailable in this local run" in workbench_block


def test_previous_confirmed_value_is_hint_not_default_input() -> None:
    start = HTML.index("function reviewFieldInputHtml")
    end = HTML.index("function reviewFieldWorkbenchPanel", start)
    input_block = HTML[start:end]
    assert "previous_confirmed_value || field.suggested_value" not in input_block
    assert "previous_confirmed_value || \"--\"" in HTML


def test_resolution_payload_includes_checked_workbench_field() -> None:
    start = HTML.index("function reviewResolutionPayload")
    end = HTML.index("async function submitReviewResolution", start)
    payload_block = HTML[start:end]
    assert "const checkedWorkbenchField = container.dataset.checkedWorkbenchField" in payload_block
    assert "item?.field_review_workbench?.fields?.[0]?.field_key" in payload_block
    assert "const hasScoringOutcome" in payload_block
    assert "hasScoringOutcome || checkedWorkbenchField" in payload_block
    assert "checked_workbench_field" in payload_block
    assert "field_review_workbench" in payload_block
