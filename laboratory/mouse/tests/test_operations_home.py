from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_static_ui_exposes_operations_home_surface() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'data-view-target="operations"' in html
    assert 'data-view="operations"' in html
    assert 'id="operationsHomeReadModel"' in html
    assert 'api("/api/ui/operations-home")' in html
    assert "function renderOperationsHomeReadModel" in html
    assert "renderOperationsHomeReadModel(operationsHome)" in html
    assert "function operationsActionChannelClass" in html
    assert "action_channel_label" in html
    assert "external_payload_policy" in html
    assert "data-action-channel" in html


def test_static_operations_home_uses_operator_evidence_summary() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("function renderOperationsHomeReadModel")
    end = html.index('document.querySelectorAll(".operations-target-action")', start)
    render_block = html[start:end]

    assert "function operationsEvidenceSummary" in html
    assert "operationsEvidenceSummary(task)" in render_block
    assert "compactDetails(task.evidence_refs || {})" not in render_block
    assert "operationsDebugDetails(task)" in render_block
    assert "Debug details" in html


def test_static_operations_home_target_actions_refresh_selected_views() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index('document.querySelectorAll(".operations-target-action")')
    end = html.index("function renderColonyStateReadModel", start)
    handler = html[start:end]

    assert 'targetType === "review"' in handler
    assert 'selectedReviewId = targetId;\n            setActiveView("review");\n            await refresh();' in handler
    assert 'if (button.dataset.actionChannel === "assistant") await loadAssistantReviewDraft(targetId);' in handler
    assert 'setTranscriptionPhoto(targetId);\n            setActiveView("photo");\n            await refresh();' in handler
    assert 'selectedAuditMouseId = targetId;\n            setActiveView("mouse-detail");\n            await refresh();' in handler


def test_static_mouse_detail_uses_selected_operations_target() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "function selectedMouse(mice)" in html
    assert "const mouse = selectedMouse(mice);" in html
    assert "const selectedTimelineMouse = selectedMouse(mice);" in html
    assert "const mouse = firstMouse(mice);" not in html
    assert "const selectedTimelineMouse = firstMouse(mice);" not in html


def test_operations_home_empty_state_is_read_only_view(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/operations-home")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "What needs doing next?"
        assert payload["summary"]["total_tasks"] == 0
        assert payload["task_groups"] == []
        assert payload["empty_state"] == {
            "message": "No operations tasks are currently open.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_operations_home_groups_review_candidate_genotyping_and_export_tasks(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO upload_batch
                    (upload_batch_id, batch_label, expected_photo_count, status, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "batch_operations",
                    "Operations batch",
                    1,
                    "open",
                    "Needs transcription.",
                    "2026-05-12T08:59:00Z",
                    "2026-05-12T08:59:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO photo_log
                    (photo_id, upload_batch_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "photo_operations_worklist",
                    "batch_operations",
                    "operations-worklist-card.jpg",
                    "data/photos/test/operations-worklist-card.jpg",
                    "2026-05-12T08:59:30Z",
                    "review_pending",
                    "cage_card_photo",
                ),
            )
            conn.execute(
                """
                INSERT INTO photo_log
                    (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "photo_operations",
                    "operations-card.jpg",
                    "data/photos/test/operations-card.jpg",
                    "2026-05-12T09:00:00Z",
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
                    "parse_operations",
                    "photo_operations",
                    "ai_photo_extraction",
                    json.dumps({"confidence": 40, "rawStrain": "", "sexRaw": ""}, ensure_ascii=False),
                    "2026-05-12T09:01:00Z",
                    "review",
                    40,
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
                    "review_operations_focus",
                    "parse_operations",
                    "High",
                    "AI-extracted photo transcription needs review",
                    "photo_operations",
                    "Review low-confidence OCR draft.",
                    "Low-confidence OCR draft needs focused review before export.",
                    "open",
                    "2026-05-12T09:02:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value,
                     suggested_value, review_reason, status, created_at, resolved_at, resolution_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review_operations_resolved",
                    "parse_operations",
                    "Medium",
                    "Manual photo transcription needs review",
                    "MT501",
                    "Accept MT501 candidate.",
                    "Reviewed source photo.",
                    "resolved",
                    "2026-05-12T09:03:00Z",
                    "2026-05-12T09:04:00Z",
                    "Accepted from photo evidence.",
                ),
            )
            conn.execute(
                """
                INSERT INTO canonical_candidate
                    (candidate_id, review_id, parse_id, proposed_mouse_display_id,
                     proposed_strain, proposed_dob, proposed_count, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "candidate_operations",
                    "review_operations_resolved",
                    "parse_operations",
                    "MT501",
                    "ApoM Tg/Tg",
                    "2026-04-01",
                    "1",
                    "draft",
                    "2026-05-12T09:05:00Z",
                    "2026-05-12T09:05:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, raw_strain_text, sex, genotype_status,
                     genotyping_status, next_action, status, source_photo_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "mouse_operations",
                    "MT777",
                    "ApoM Tg/Tg",
                    "male",
                    "pending",
                    "sampled",
                    "awaiting_result",
                    "active",
                    "photo_operations",
                    "2026-05-12T09:06:00Z",
                ),
            )

        client = TestClient(app)
        response = client.get("/api/ui/operations-home")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["summary"]["total_tasks"] >= 4
        assert payload["summary"]["must_review"] == 1
        assert payload["summary"]["export_blockers"] == 1
        assert payload["empty_state"]["fabricated_records"] is False

        tasks = [task for group in payload["task_groups"] for task in group["tasks"]]
        families = {task["family"] for task in tasks}
        assert {"focus_review", "photo_worklist", "canonical_apply", "genotyping", "export_readiness"}.issubset(families)
        assert all(task["source_layer"] == "export or view" for task in tasks)
        assert all(task["task_id"] for task in tasks)
        assert all(task["target_type"] for task in tasks)
        assert all("evidence_refs" in task for task in tasks)
        assert all(task["action_channel"] in {"human", "assistant", "api_mcp"} for task in tasks)
        assert all(task["action_channel_label"] for task in tasks)
        assert all(task["external_payload_policy"] for task in tasks)
        assert payload["summary"]["action_channels"]["human"] >= 1
        assert payload["summary"]["action_channels"]["assistant"] >= 1
        assert payload["summary"]["action_channels"]["api_mcp"] >= 1

        focus_task = next(task for task in tasks if task["family"] == "focus_review")
        assert focus_task["risk_class"] == "blocker"
        assert focus_task["target_type"] == "review"
        assert focus_task["target_id"] == "review_operations_focus"
        assert focus_task["evidence_refs"]["review_id"] == "review_operations_focus"
        assert focus_task["evidence_refs"]["source_photo_id"] == "photo_operations"
        assert focus_task["action_channel"] == "assistant"
        assert focus_task["external_payload_policy"] == "local_only_until_approved"

        photo_task = next(task for task in tasks if task["family"] == "photo_worklist")
        assert photo_task["status"] == "transcribe_photo"
        assert photo_task["target_type"] == "photo"
        assert photo_task["target_id"] == "photo_operations_worklist"
        assert photo_task["evidence_refs"]["upload_batch_id"] == "batch_operations"
        assert photo_task["evidence_refs"]["source_photo_id"] == "photo_operations_worklist"
        assert photo_task["action_channel"] == "human"

        candidate_task = next(task for task in tasks if task["family"] == "canonical_apply")
        assert candidate_task["target_type"] == "canonical_candidate"
        assert candidate_task["target_id"] == "candidate_operations"
        assert candidate_task["evidence_refs"]["review_id"] == "review_operations_resolved"
        assert candidate_task["action_channel"] == "human"
        assert candidate_task["external_payload_policy"] == "no_external_write"

        genotype_task = next(task for task in tasks if task["family"] == "genotyping")
        assert genotype_task["status"] == "awaiting_result"
        assert genotype_task["target_type"] == "mouse"
        assert genotype_task["target_id"] == "mouse_operations"
        assert genotype_task["action_channel"] == "api_mcp"

        export_task = next(task for task in tasks if task["family"] == "export_readiness")
        assert export_task["status"] == "export_blocked"
        assert export_task["risk_class"] == "blocker"
        assert export_task["target_type"] == "export"
    finally:
        db.DB_PATH = old_db_path
