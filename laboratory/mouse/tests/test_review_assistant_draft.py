from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_static_ui_exposes_review_assistant_draft_controls() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "/assistant-draft" in html
    assert "assistant-review-draft" in html
    assert "renderAssistantReviewDraft" in html
    assert "function loadAssistantReviewDraft" in html
    assert "Load Assistant Draft" in html
    assert "function fillReviewResolutionFromAssistantDraft" in html
    assert "apply-assistant-review-draft" in html
    assert "Apply Draft To Form" in html
    assert "Assistant draft unavailable" in html
    assert "Review source evidence directly; no canonical state was changed." in html
    assert "Review type:" in html
    assert "Form policy:" in html

    start = html.index("function fillReviewResolutionFromAssistantDraft")
    end = html.index("async function loadAssistantReviewDraft", start)
    fill_function = html[start:end]
    assert '#reviewDetailPanel .review-actions' in fill_function
    assert "draft.review_type" in fill_function
    assert "draft.form_fill_policy" in fill_function
    assert ".review-resolved-value" in fill_function
    assert ".review-resolution-note" in fill_function
    assert ".ear-label-code" in fill_function
    assert ".note-label-decision" in fill_function
    assert ".note-label-mouse-id" in fill_function
    assert ".note-label-count" in fill_function
    assert "assistantCorrectionFieldName" in fill_function
    assert "assistantNoteItemId" in fill_function
    assert "Assistant draft copied into the form" in fill_function
    assert "submitReviewResolution" not in fill_function
    assert "api(" not in fill_function

    start = html.index("function reviewResolutionPayload")
    end = html.index("async function submitReviewResolution", start)
    payload_function = html[start:end]
    assert "assistantCorrectionFieldName" in payload_function
    assert "assistantNoteItemId" in payload_function

    start = html.index("async function loadAssistantReviewDraft")
    end = html.index("function attachReviewAuditHandler", start)
    load_function = html[start:end]
    assert "try {" in load_function
    assert "catch (error)" in load_function
    assert 'panel.dataset.stateKind = "error"' in load_function
    assert "apiErrorMessage(error)" in load_function
    assert "return null;" in load_function


def test_static_ui_exposes_review_scoring_audit_taxonomy_controls() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "review-audit-taxonomy-status" in html
    assert "Scoring audit taxonomy" in html
    assert "partial_match" in html
    assert "near_miss" in html
    assert "unscorable_due_to_occlusion" in html

    start = html.index("function reviewResolutionPayload")
    end = html.index("async function submitReviewResolution", start)
    payload_function = html[start:end]
    assert ".review-audit-taxonomy-status" in payload_function
    assert "audit_taxonomy_status" in payload_function
    assert "audit_taxonomy_note" in payload_function


def test_static_ui_builds_field_level_accuracy_outcome_payload() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "reviewAccuracyOutcomeControls" in html
    assert "review-note-line-scoring-scope" in html
    assert "no_visible_note_line_for_evaluator_scoring" in html
    assert "review-field-outcome-status" in html
    assert "data-field-family" in html
    assert "mouse_ids_or_note_lines" in html
    assert "card_type_review_routing" in html
    assert "sex_count_dob" in html
    assert "mating_litter_context" in html
    assert "export_provenance" in html

    start = html.index("function reviewResolutionPayload")
    end = html.index("async function submitReviewResolution", start)
    payload_function = html[start:end]
    assert ".review-note-line-scoring-scope" in payload_function
    assert ".review-field-outcome-status" in payload_function
    assert "note_line_scoring_scope" in payload_function
    assert "field_review_outcome" in payload_function
    assert "reviewed_before_apply: true" in payload_function
    assert "traceable: true" in payload_function
    assert "no_visible_note_line_for_evaluator_scoring" in payload_function
    assert "Choose note-line scope before resolving field accuracy outcome." in payload_function
    assert "Select at least one field outcome before resolving scoring scope." in payload_function


def test_static_ui_allows_quick_resolve_only_for_safe_review_issues() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "function reviewAllowsQuickResolve(item)" in html
    assert '"Low-confidence strain alias"' in html
    assert '"fixture auto-filled by policy"' in html
    assert 'item?.attention_level !== "quick_check"' in html
    assert "reviewAllowsQuickResolve(item)" in html

    start = html.index("function reviewResolutionControls")
    end = html.index("function reviewCheckTargetsText", start)
    controls = html[start:end]
    assert "reviewAllowsQuickResolve(item)" in controls
    assert "Accept after check" not in controls


def test_static_ui_uses_operator_workload_for_primary_review_counts() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert '<option value="workload">Actionable Workload</option>' in html
    assert "function operatorReviewWorkloadCount(reviews)" in html

    start = html.index("function filteredReviews")
    end = html.index("function reviewAttentionCounts", start)
    filtered = html[start:end]
    assert 'reviewStatusFilter === "workload"' in filtered
    assert 'item.status === "open"' in filtered
    assert '["must_review", "quick_check"].includes(item.attention_level)' in filtered

    start = html.index("function operatorReviewWorkloadCount")
    end = html.index("function renderShellStatus", start)
    helper = html[start:end]
    assert "reviewAttentionCounts(reviews)" in helper
    assert "must_review" in helper
    assert "quick_check" in helper
    assert "trace_only" not in helper
    assert "hidden_default" not in helper

    start = html.index("function renderShellStatus")
    end = html.index("function firstMouse", start)
    render_shell = html[start:end]
    assert "const operatorWorkloadCount = operatorReviewWorkloadCount(reviews);" in render_shell
    assert 'document.getElementById("navReviewCount").textContent = String(operatorWorkloadCount);' in render_shell
    assert 'document.getElementById("heroOpenReviews").textContent = String(operatorWorkloadCount);' in render_shell
    assert 'document.getElementById("topOpenReviews").textContent = `${operatorWorkloadCount} review task' in render_shell
    assert "openReviewCount} open reviews" not in render_shell


def test_static_ui_labels_ai_draft_as_approval_required_when_available() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    start = html.index("function renderShellStatus")
    end = html.index("function firstMouse", start)
    render_shell = html[start:end]

    assert "aiStatus.approval_required" in render_shell
    assert "AI draft: approval required" in render_shell
    assert "local + AI draft ready" not in render_shell
    assert "uploaded photos will be extracted automatically" not in html
    assert "button approval" in html


def test_static_ui_renders_source_photo_missing_state() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    start = html.index("function reviewSourceEvidencePanel")
    end = html.index("function renderReviewDetail", start)
    source_panel = html[start:end]

    assert "source-photo-frame" in source_panel
    assert "source-photo-missing" in source_panel
    assert "Source photo unavailable in this local run" in source_panel
    assert "data-source-photo-state" in source_panel
    assert "onerror=" in source_panel
    assert "this.hidden = true" in source_panel


def test_static_ui_renders_focus_review_output_first_summary() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    start = html.index("function renderFocusReviewReadModel")
    end = html.index("function operationsRiskTone", start)
    render_function = html[start:end]

    assert "model?.output_first" in render_function
    assert "show_result_first" in render_function
    assert "primary_output_label" in render_function
    assert "result_status" in render_function
    assert "exception_count" in render_function
    assert "visible_exception_count" in render_function
    assert "remaining_exception_count" in render_function
    assert "overflow_action" in render_function
    assert "exceptions" in render_function
    assert "small exception list" in render_function
    assert "showing" in render_function
    assert "remaining" in render_function
    assert "review-output-first" in render_function
    assert "open-output-exception-review" in render_function
    assert "open-output-exception-photo" in render_function
    assert "open-output-exception-overflow" in render_function
    assert "data-review-id" in render_function
    assert "data-target-view" in render_function
    assert "data-source-photo-id" in render_function
    assert "openOutputExceptionReview" in render_function
    assert "openOutputExceptionPhoto" in render_function
    assert "openOutputExceptionOverflow" in render_function

    start = html.index("function openOutputExceptionReview")
    end = html.index("function openOutputExceptionPhoto", start)
    handler = html[start:end]
    assert 'reviewStatusFilter = "focus"' in handler
    assert 'reviewSeverityFilter = "all"' in handler
    assert 'reviewEvidenceFilter = "all"' in handler
    assert 'reviewRoleFilter = "all"' in handler
    assert "selectedReviewId = reviewId" in handler
    assert 'setActiveView(targetView || "review")' in handler
    assert "refresh()" in handler

    start = html.index("function openOutputExceptionPhoto")
    end = html.index("function operationsRiskTone", start)
    photo_handler = html[start:end]
    assert "setActiveView(\"photo\")" in photo_handler
    assert "selectedTranscriptionPhotoId = photoId" in photo_handler
    assert "await refresh()" in photo_handler
    assert "setTranscriptionPhoto(photoId)" in photo_handler
    assert "Source photo opened from output-first exception" in photo_handler

    start = html.index("function openOutputExceptionOverflow")
    end = html.index("function operationsRiskTone", start)
    overflow_handler = html[start:end]
    assert 'reviewStatusFilter = "workload"' in overflow_handler
    assert 'reviewStatusFilter = "all"' not in overflow_handler
    assert 'reviewStatusFilter = "focus"' not in overflow_handler
    assert 'reviewSeverityFilter = "all"' in overflow_handler
    assert 'reviewEvidenceFilter = "all"' in overflow_handler
    assert 'reviewRoleFilter = "all"' in overflow_handler
    assert 'setActiveView("review")' in overflow_handler
    assert "refresh()" in overflow_handler


def test_static_ui_routes_pedigree_relationship_action_to_mouse_detail() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    start = html.index("function renderMousePedigreeReadModel")
    end = html.index("function renderEvidenceLedgerReadModel", start)
    render_function = html[start:end]

    assert "open-pedigree-action" in render_function
    assert "link.target_view ||" in render_function
    assert "data-view-target" in render_function
    assert "selectedAuditMouseId = mouse.mouse_id" in render_function
    assert "setActiveView(targetView)" in render_function
    assert 'setActiveView("review")' not in render_function


def test_static_ui_warns_before_mapping_trace_only_candidate_and_refreshes_after_apply() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "function canonicalMappingPreflightWarning" in html
    assert "may not create an apply-ready candidate" in html
    assert "Map a source-backed mouse note review instead" in html

    start = html.index("function reviewResolutionPayload")
    end = html.index("async function submitReviewResolution", start)
    payload_function = html[start:end]
    assert "canonicalMappingPreflightWarning(item)" in payload_function
    assert "canonical-mapping-warning" in payload_function

    start = html.index('document.querySelectorAll(".apply-canonical-candidate")')
    end = html.index('document.querySelectorAll(".audit-canonical-candidate")', start)
    apply_handler = html[start:end]
    assert "markCanonicalCandidateApplied(result)" in apply_handler
    assert "result.status" in apply_handler
    assert "applied" in apply_handler


def test_review_assistant_draft_is_local_read_only_and_traceable(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO photo_log
                    (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "photo_assistant_draft",
                    "assistant-draft-card.jpg",
                    "data/photos/test/assistant-draft-card.jpg",
                    "2026-05-12T10:00:00Z",
                    "review_pending",
                    "cage_card_photo",
                ),
            )
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "parse_assistant_draft",
                    "photo_assistant_draft",
                    "ai_photo_extraction",
                    json.dumps({"confidence": 42, "rawStrain": "ApoM ?", "sexRaw": "M?"}, ensure_ascii=False),
                    "2026-05-12T10:01:00Z",
                    "review",
                    42,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, parse_id, photo_id, raw_line_text, parsed_type,
                     interpreted_status, parsed_mouse_display_id, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "note_assistant_draft",
                    "parse_assistant_draft",
                    "photo_assistant_draft",
                    "MT901 R0 male? verify",
                    "mouse_item",
                    "active",
                    "MT901",
                    0.42,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value,
                     suggested_value, review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review_assistant_draft",
                    "parse_assistant_draft",
                    "High",
                    "AI-extracted photo transcription needs review",
                    "ApoM ? / M?",
                    "Confirm MT901 strain and sex from source photo.",
                    "Low-confidence OCR draft needs focused review before export.",
                    "open",
                    "2026-05-12T10:02:00Z",
                ),
            )
            before_actions = conn.execute("SELECT COUNT(*) FROM action_log").fetchone()[0]
            before_corrections = conn.execute("SELECT COUNT(*) FROM correction_log").fetchone()[0]

        client = TestClient(app)
        response = client.get("/api/review-items/review_assistant_draft/assistant-draft")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "review item"
        assert payload["boundary"] == "review item"
        assert payload["draft_kind"] == "assistant_review_draft"
        assert payload["external_payload_policy"] == "local_only_until_approved"
        assert payload["writes_canonical_state"] is False
        assert payload["requires_operator_approval"] is True
        assert payload["review"]["review_id"] == "review_assistant_draft"
        assert payload["evidence_refs"]["source_photo_id"] == "photo_assistant_draft"
        assert payload["evidence_refs"]["note_item_ids"] == ["note_assistant_draft"]
        assert payload["draft"]["resolution_payload"]["resolved_value"] == "Confirm MT901 strain and sex from source photo."
        assert payload["draft"]["resolution_payload"]["correction_entity_type"] == "review_item"
        assert payload["draft"]["resolution_payload"]["correction_entity_id"] == "review_assistant_draft"
        assert payload["draft"]["resolution_payload"]["correction_before_value"] == "ApoM ? / M?"
        assert payload["draft"]["resolution_payload"]["correction_after_value"] == "Confirm MT901 strain and sex from source photo."
        assert "MT901 R0 male? verify" in payload["draft"]["evidence_summary"]
        assert "operator" in payload["draft"]["operator_note"].lower()

        with db.connection() as conn:
            after_actions = conn.execute("SELECT COUNT(*) FROM action_log").fetchone()[0]
            after_corrections = conn.execute("SELECT COUNT(*) FROM correction_log").fetchone()[0]
        assert after_actions == before_actions
        assert after_corrections == before_corrections
    finally:
        db.DB_PATH = old_db_path


def test_review_assistant_draft_specializes_ear_label_review_payload(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("parse_ear_draft", "manual_photo_transcription", "{}", "2026-05-12T11:00:00Z", "review", 65, 1),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, parse_id, raw_line_text, parsed_type,
                     parsed_ear_label_raw, parsed_ear_label_code, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("note_ear_draft", "parse_ear_draft", "318 R' needs confirmation", "mouse_item", "R'", "R_PRIME", 0.65, 1),
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value,
                     suggested_value, review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review_ear_note_ear_draft",
                    "parse_ear_draft",
                    "Medium",
                    "Ear label needs review",
                    "R'",
                    "R_PRIME",
                    "Ear label normalization requires a bounded review choice.",
                    "open",
                    "2026-05-12T11:01:00Z",
                ),
            )

        payload = TestClient(app).get("/api/review-items/review_ear_note_ear_draft/assistant-draft").json()

        assert payload["draft"]["review_type"] == "ear_label_review"
        assert payload["draft"]["form_fill_policy"] == "bounded_choice_only"
        assert payload["draft"]["resolution_payload"]["resolved_value"] == "R_PRIME"
        assert payload["draft"]["resolution_payload"]["ear_label_code"] == "R_PRIME"
        assert payload["draft"]["resolution_payload"]["correction_field_name"] == "ear_label_code"
        assert "bounded" in payload["draft"]["operator_note"].lower()
    finally:
        db.DB_PATH = old_db_path


def test_review_assistant_draft_specializes_unlabeled_numeric_note_payload(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("parse_numeric_draft", "manual_photo_transcription", "{}", "2026-05-12T11:10:00Z", "review", 50, 1),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, parse_id, raw_line_text, parsed_type,
                     parsed_count, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("note_numeric_draft", "parse_numeric_draft", "3", "unlabeled_numeric_note", 3, 0.5, 1),
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value,
                     suggested_value, review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review_unlabeled_numeric_note_numeric_draft",
                    "parse_numeric_draft",
                    "Medium",
                    "Unlabeled numeric note needs review",
                    "3",
                    "Review whether this is count or mouse ID.",
                    "Numeric note lacks a label and must not be inferred silently.",
                    "open",
                    "2026-05-12T11:11:00Z",
                ),
            )

        payload = TestClient(app).get("/api/review-items/review_unlabeled_numeric_note_numeric_draft/assistant-draft").json()

        assert payload["draft"]["review_type"] == "unlabeled_numeric_note_review"
        assert payload["draft"]["form_fill_policy"] == "operator_choose_note_label"
        assert payload["draft"]["resolution_payload"]["note_item_id"] == "note_numeric_draft"
        assert payload["draft"]["resolution_payload"]["note_label_decision"] == ""
        assert payload["draft"]["resolution_payload"]["correction_field_name"] == "parsed_label"
        assert "must choose" in payload["draft"]["operator_note"].lower()
    finally:
        db.DB_PATH = old_db_path


def test_review_assistant_draft_anchors_type_specific_note_item_when_parse_has_multiple_notes(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("parse_multi_note_draft", "manual_photo_transcription", "{}", "2026-05-12T11:20:00Z", "review", 58, 1),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, parse_id, line_number, raw_line_text, parsed_type,
                     parsed_mouse_display_id, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("note_mouse_first", "parse_multi_note_draft", 1, "MT318 R' clear", "mouse_item", "MT318", 0.91, 0),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, parse_id, line_number, raw_line_text, parsed_type,
                     parsed_count, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("note_numeric_target", "parse_multi_note_draft", 2, "3", "unlabeled_numeric_note", 3, 0.5, 1),
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value,
                     suggested_value, review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review_unlabeled_numeric_parse_multi_note_draft",
                    "parse_multi_note_draft",
                    "Medium",
                    "Unlabeled numeric note needs review",
                    "3",
                    "Confirm as temporary labels, ignore, or map to mouse IDs.",
                    "Grouped numeric note review should anchor the numeric line, not the first note line.",
                    "open",
                    "2026-05-12T11:21:00Z",
                ),
            )

        payload = TestClient(app).get("/api/review-items/review_unlabeled_numeric_parse_multi_note_draft/assistant-draft").json()

        assert payload["draft"]["review_type"] == "unlabeled_numeric_note_review"
        assert payload["draft"]["resolution_payload"]["note_item_id"] == "note_numeric_target"
    finally:
        db.DB_PATH = old_db_path


def test_review_assistant_draft_404_for_missing_review(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/review-items/missing_review/assistant-draft")

        assert response.status_code == 404
    finally:
        db.DB_PATH = old_db_path
