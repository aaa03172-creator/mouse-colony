from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app, create_card_snapshot, focus_review_output_first_summary, write_note_items_and_mouse_candidates


def seed_focus_review_card(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    db.init_db()
    photo_id = "photo_focus_review"
    parse_id = "parse_focus_review"
    created_at = "2026-05-09T10:17:00Z"
    record = {
        "type": "Separated",
        "rawStrain": "C57BL/6J",
        "matchedStrain": "C57BL/6J",
        "sexRaw": "mixed",
        "dobRaw": "2025-02-10",
        "mouseCount": "3 total",
        "confidence": 82,
        "notes": [
            {"raw": "MT318 R'", "strike": "none"},
            {"raw": "MT319 L'", "strike": "none"},
            {"raw": "MT320", "strike": "none"},
        ],
    }
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO photo_log
                (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                photo_id,
                "focus-review-card.jpg",
                "data/photos/test/focus-review-card.jpg",
                "2026-05-09T10:15:30Z",
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
                parse_id,
                photo_id,
                "manual_photo_transcription",
                json.dumps(record, ensure_ascii=False),
                created_at,
                "review",
                82,
                1,
            ),
        )
        snapshot_id = create_card_snapshot(conn, parse_id, photo_id, record, created_at)
        write_note_items_and_mouse_candidates(conn, parse_id, {**record, "cardSnapshotId": snapshot_id}, "review")
        conn.execute(
            """
            INSERT INTO review_queue
                (review_id, parse_id, severity, issue, current_value,
                 suggested_value, review_reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "review_duplicate_mt319",
                parse_id,
                "High",
                "Duplicate active mouse",
                "MT319 active in two card snapshots",
                "Resolve duplicate active mouse before export.",
                "MT319 appears to duplicate an existing active mouse. Review before canonical apply.",
                "open",
                "2026-05-09T10:18:00Z",
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
                "review_unlabeled_numeric_parse_focus_review",
                parse_id,
                "Medium",
                "Unlabeled numeric note needs review",
                "MT320",
                "Confirm note-line interpretation.",
                "Needs quick confirmation from source photo.",
                "open",
                "2026-05-09T10:19:00Z",
            ),
        )


def test_focus_review_groups_db_backed_review_items_by_photo_card(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_focus_review_card(tmp_path)
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["workload_summary"]["must_review"] == 1
        assert payload["workload_summary"]["quick_check"] >= 1
        assert payload["workload_summary"]["operator_workload_count"] == 2
        assert payload["output_first"] == {
            "source_layer": "export or view",
            "story": "input -> useful draft/output -> small exception list -> final when safe",
            "primary_output_label": "Animal sheet draft",
            "result_status": "blocked",
            "headline": "Animal sheet draft generated; 2 checks before final export",
            "show_result_first": True,
            "exception_count": 2,
            "visible_exception_count": 2,
            "remaining_exception_count": 0,
            "must_review_count": 1,
            "quick_check_count": 1,
            "final_export_blocked": True,
            "exceptions": [
                {
                    "review_id": "review_duplicate_mt319",
                    "issue": "Duplicate active mouse",
                    "attention_level": "must_review",
                    "action_label": "Inspect source evidence",
                    "target_path": "/api/ui/focus-review",
                    "target_view": "review",
                    "target_review_id": "review_duplicate_mt319",
                    "source_photo_id": "photo_focus_review",
                    "evidence_preview": "Resolve duplicate active mouse before export.",
                },
                {
                    "review_id": "review_unlabeled_numeric_parse_focus_review",
                    "issue": "Unlabeled numeric note needs review",
                    "attention_level": "quick_check",
                    "action_label": "Inspect source evidence",
                    "target_path": "/api/ui/focus-review",
                    "target_view": "review",
                    "target_review_id": "review_unlabeled_numeric_parse_focus_review",
                    "source_photo_id": "photo_focus_review",
                    "evidence_preview": "Confirm note-line interpretation.",
                },
            ],
        }
        assert payload["empty_state"]["fabricated_records"] is False
        [card] = payload["cards"]
        assert card["source_photo"]["photo_id"] == "photo_focus_review"
        assert card["source_photo"]["source_photo_role"] == "primary_evidence"
        assert card["source_photo"]["open_source_photo_label"] == "Open source photo"
        assert card["card_snapshot"]["card_type"] == "Separated"
        assert card["review_count"] >= 2
        review_ids = {item["review_id"] for item in card["review_items"]}
        assert {"review_duplicate_mt319", "review_unlabeled_numeric_parse_focus_review"}.issubset(review_ids)
        assert any(item["attention_level"] == "must_review" for item in card["review_items"])
        assert any(item["attention_level"] == "quick_check" for item in card["review_items"])
        duplicate_item = next(item for item in card["review_items"] if item["review_id"] == "review_duplicate_mt319")
        quick_item = next(item for item in card["review_items"] if item["review_id"] == "review_unlabeled_numeric_parse_focus_review")
        assert duplicate_item["action_hint"] == {
            "source_layer": "export or view",
            "mode": "manual_review_required",
            "primary_label": "Inspect source evidence",
            "requires_note": True,
            "requires_source_photo": True,
            "safe_quick_resolve": False,
        }
        assert quick_item["action_hint"]["mode"] == "manual_review_required"
        assert quick_item["action_hint"]["safe_quick_resolve"] is False
        assert quick_item["action_hint"]["requires_note"] is True
        assert [row["mouse_id"] for row in card["mouse_rows"]] == ["MT318", "MT319"]
        assert card["collapsed_sections"]["note_lines"] == 2
        assert card["collapsed_sections"]["evidence"] >= 1
        assert card["actions"] == ["Apply confirmed rows only", "Hold card", "Open source photo"]
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_review_item_includes_side_by_side_workbench(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_focus_review_card(tmp_path)
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        [card] = response.json()["cards"]
        item = next(
            review
            for review in card["review_items"]
            if review["review_id"] == "review_unlabeled_numeric_parse_focus_review"
        )
        workbench = item["field_review_workbench"]
        assert workbench["source_layer"] == "export or view"
        assert workbench["canonical"] is False
        assert workbench["layout"] == "photo_left_fields_right"
        assert workbench["photo"] == {
            "photo_id": "photo_focus_review",
            "image_url": "/api/photos/photo_focus_review/image",
            "roi_preview_url": "/api/photos/photo_focus_review/roi-preview",
            "primary_roi_image_url": "",
            "open_source_photo_label": "Open source photo",
        }
        assert workbench["policy"].startswith("Reviewed field values fill the review resolution form only")
        assert workbench["fields"][0]["field_key"] == "note_line"
        assert workbench["fields"][0]["label"] == "Note line"
        assert workbench["fields"][0]["current_value"] == "MT320"
        assert workbench["fields"][0]["suggested_value"] == "Confirm note-line interpretation."
        assert workbench["fields"][0]["requires_user_value"] is True
        assert workbench["fields"][0]["input_type"] == "text"
        assert workbench["fields"][0]["trace"]["parse_id"] == "parse_focus_review"
    finally:
        db.DB_PATH = old_db_path


def test_review_items_include_side_by_side_workbench(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_focus_review_card(tmp_path)
        client = TestClient(app)

        response = client.get("/api/review-items")

        assert response.status_code == 200
        item = next(
            review
            for review in response.json()
            if review["review_id"] == "review_unlabeled_numeric_parse_focus_review"
        )
        workbench = item["field_review_workbench"]
        assert workbench["canonical"] is False
        assert workbench["layout"] == "photo_left_fields_right"
        assert workbench["photo"]["photo_id"] == "photo_focus_review"
        assert workbench["fields"][0]["current_value"] == "MT320"
        assert workbench["fields"][0]["trace"]["parse_id"] == "parse_focus_review"
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_workbench_surfaces_previous_position_hint(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_focus_review_card(tmp_path)
        with db.connection() as conn:
            metadata = {
                "hybrid_note_line_evaluator": {
                    "previous_position_candidate": {
                        "suggested_preserved_text": "F2 6p",
                        "previous_raw_line_text": "F2 6p",
                        "suggested_status_from_current_strike": "separated",
                        "previous_trace": {
                            "photo_id": "photo_previous",
                            "parse_id": "parse_previous",
                            "note_item_id": "note_previous_3",
                            "line_number": 3,
                            "roi_ref": "note_block:3",
                        },
                    }
                }
            }
            conn.execute(
                """
                UPDATE card_note_item_log
                SET raw_line_text = ?, parsed_metadata_json = ?
                WHERE parse_id = ? AND line_number = ?
                """,
                ("F2 6", json.dumps(metadata, ensure_ascii=False), "parse_focus_review", 3),
            )
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        [card] = response.json()["cards"]
        item = next(
            review
            for review in card["review_items"]
            if review["review_id"] == "review_unlabeled_numeric_parse_focus_review"
        )
        field = item["field_review_workbench"]["fields"][0]
        assert field["current_value"] == "F2 6"
        assert field["previous_confirmed_value"] == "F2 6p"
        assert field["rule_candidate"] == "separated"
        assert field["trace"]["roi_ref"] == "note_block:3"
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_empty_state_does_not_fabricate_colony_data(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["workload_summary"] == {
            "must_review": 0,
            "quick_check": 0,
            "operator_workload_count": 0,
        }
        assert payload["output_first"] == {
            "source_layer": "export or view",
            "story": "input -> useful draft/output -> small exception list -> final when safe",
            "primary_output_label": "Animal sheet draft",
            "result_status": "empty",
            "headline": "No draft output yet; upload photos or source records to generate one",
            "show_result_first": False,
            "exception_count": 0,
            "visible_exception_count": 0,
            "remaining_exception_count": 0,
            "must_review_count": 0,
            "quick_check_count": 0,
            "final_export_blocked": False,
            "exceptions": [],
        }
        assert payload["cards"] == []
        assert payload["empty_state"] == {
            "message": "No Focus Review items are currently open.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_output_first_reports_exception_overflow(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_focus_review_card(tmp_path)
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        with db.connection() as conn:
            for index in range(3):
                conn.execute(
                    """
                    INSERT INTO review_queue
                        (review_id, parse_id, severity, issue, current_value,
                         suggested_value, review_reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"review_extra_quick_{index}",
                        "parse_focus_review",
                        "Medium",
                        "New workflow review type",
                        f"extra {index}",
                        f"Review extra exception {index}.",
                        "Additional exception should be counted without crowding the compact list.",
                        "open",
                        f"2026-05-09T10:2{index}:00Z",
                    ),
                )
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        output = response.json()["output_first"]
        assert output["exception_count"] == 5
        assert output["visible_exception_count"] == 3
        assert output["remaining_exception_count"] == 2
        assert output["overflow_action"] == {
            "label": "Open full Focus Review",
            "target_view": "review",
            "target_path": "/api/ui/focus-review",
            "remaining_exception_count": 2,
        }
        assert len(output["exceptions"]) == 3
        assert [item["review_id"] for item in output["exceptions"]][0] == "review_duplicate_mt319"
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_output_first_excludes_trace_only_from_compact_exceptions() -> None:
    output = focus_review_output_first_summary(
        [
            {
                "review_id": "review_trace_only_noise",
                "status": "open",
                "attention_level": "trace_only",
                "issue": "Low OCR confidence",
                "created_at": "2026-05-09T10:00:00Z",
                "suggested_value": "Trace-only context.",
            },
            {
                "review_id": "review_must_review_action",
                "status": "open",
                "attention_level": "must_review",
                "issue": "Duplicate active mouse",
                "created_at": "2026-05-09T10:01:00Z",
                "suggested_value": "Resolve duplicate active mouse.",
            },
            {
                "review_id": "review_quick_check_action",
                "status": "open",
                "attention_level": "quick_check",
                "issue": "New workflow review type",
                "created_at": "2026-05-09T10:02:00Z",
                "suggested_value": "Confirm the parsed note.",
            },
        ]
    )

    assert output["exception_count"] == 2
    assert output["visible_exception_count"] == 2
    assert [item["review_id"] for item in output["exceptions"]] == [
        "review_must_review_action",
        "review_quick_check_action",
    ]


def test_focus_review_excludes_hidden_default_fixture_reviews(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "parse_fixture_hidden",
                    None,
                    "fixtures/sample_parse_results.json",
                    json.dumps({"confidence": 95, "rawStrain": "Fixture", "sexRaw": "female"}, ensure_ascii=False),
                    "2026-05-09T10:20:00Z",
                    "review",
                    95,
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
                    "review_fixture_hidden",
                    "parse_fixture_hidden",
                    "Low",
                    "Fixture review",
                    "fixture",
                    "fixture",
                    "Fixture/sample records should stay out of default Focus Review workload.",
                    "open",
                    "2026-05-09T10:21:00Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workload_summary"] == {
            "must_review": 0,
            "quick_check": 0,
            "operator_workload_count": 0,
        }
        assert payload["cards"] == []
    finally:
        db.DB_PATH = old_db_path


def test_focus_review_action_hint_does_not_mark_unknown_quick_check_safe(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "parse_unknown_quick",
                    None,
                    "manual_fixture",
                    json.dumps({"confidence": 90, "rawStrain": "B6J"}, ensure_ascii=False),
                    "2026-05-09T10:30:00Z",
                    "review",
                    90,
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
                    "review_unknown_quick",
                    "parse_unknown_quick",
                    "Medium",
                    "New workflow review type",
                    "unknown",
                    "unknown",
                    "Unknown issue types should stay manual until explicitly whitelisted.",
                    "open",
                    "2026-05-09T10:31:00Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/focus-review")

        assert response.status_code == 200
        [card] = response.json()["cards"]
        [item] = card["review_items"]
        assert item["attention_level"] == "quick_check"
        assert item["action_hint"] == {
            "source_layer": "export or view",
            "mode": "manual_review_required",
            "primary_label": "Inspect source evidence",
            "requires_note": True,
            "requires_source_photo": False,
            "safe_quick_resolve": False,
        }
    finally:
        db.DB_PATH = old_db_path


def seed_colony_state_records(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    db.init_db()
    photo_id = "photo_colony_state"
    parse_id = "parse_colony_state"
    created_at = "2026-05-09T11:07:00Z"
    record = {
        "type": "Separated",
        "cardIdRaw": "C-12",
        "rawStrain": "B6J",
        "matchedStrain": "C57BL/6J",
        "sexRaw": "female",
        "dobRaw": "2026-04-01",
        "mouseCount": "2 total",
        "confidence": 91,
        "notes": [
            {"raw": "MT401 R'", "strike": "none"},
            {"raw": "MT402 L'", "strike": "none"},
        ],
    }
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO photo_log
                (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                photo_id,
                "colony-state-card.jpg",
                "data/photos/test/colony-state-card.jpg",
                "2026-05-09T11:05:00Z",
                "accepted",
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
                parse_id,
                photo_id,
                "manual_photo_transcription",
                json.dumps(record, ensure_ascii=False),
                created_at,
                "accepted",
                91,
                0,
            ),
        )
        snapshot_id = create_card_snapshot(conn, parse_id, photo_id, record, created_at)
        conn.execute(
            "UPDATE card_snapshot SET status = ?, source_layer = ? WHERE card_snapshot_id = ?",
            ("accepted", "canonical structured state", snapshot_id),
        )
        write_note_items_and_mouse_candidates(conn, parse_id, {**record, "cardSnapshotId": snapshot_id}, "accepted")
        conn.executemany(
            """
            INSERT INTO mouse_master
                (mouse_id, display_id, raw_strain_text, sex, dob_raw, dob_start,
                 current_card_snapshot_id, status, source_photo_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "MT401",
                    "MT401",
                    "C57BL/6J",
                    "female",
                    "2026-04-01",
                    "2026-04-01",
                    snapshot_id,
                    "active",
                    photo_id,
                    "2026-05-09T11:08:00Z",
                    "2026-05-09T11:08:00Z",
                ),
                (
                    "MT402",
                    "MT402",
                    "C57BL/6J",
                    "female",
                    "2026-04-01",
                    "2026-04-01",
                    snapshot_id,
                    "active",
                    photo_id,
                    "2026-05-09T11:08:00Z",
                    "2026-05-09T11:08:00Z",
                ),
                (
                    "MT999",
                    "MT999",
                    "C57BL/6J",
                    "female",
                    "2026-03-01",
                    "2026-03-01",
                    snapshot_id,
                    "dead",
                    photo_id,
                    "2026-05-09T11:08:00Z",
                    "2026-05-09T11:08:00Z",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO review_queue
                (review_id, parse_id, severity, issue, current_value,
                 suggested_value, review_reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "review_colony_state_blocker",
                parse_id,
                "High",
                "Duplicate active mouse",
                "MT402 active in two card snapshots",
                "Resolve in Focus Review.",
                "Open blocker should be summarized, not duplicated, on Colony State.",
                "open",
                "2026-05-09T11:09:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO source_record
                (source_record_id, source_type, source_uri, source_label,
                 raw_payload, checksum, imported_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source_mating_colony_state",
                "manual_review",
                "review://mating/cage-12",
                "Reviewed mating cage C-12",
                json.dumps({"mating_label": "C-12 breeding pair"}, ensure_ascii=False),
                "checksum_mating_colony_state",
                "2026-05-09T11:10:00Z",
                "Source-backed mating state fixture.",
            ),
        )
        conn.execute(
            """
            INSERT INTO mating_registry
                (mating_id, mating_label, strain_goal, expected_genotype,
                 start_date, status, purpose, note, source_record_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mating_colony_state",
                "C-12 breeding pair",
                "C57BL/6J",
                "",
                "2026-04-15",
                "active",
                "breeding",
                "Accepted reviewed mating.",
                "source_mating_colony_state",
                "2026-05-09T11:11:00Z",
                "2026-05-09T11:11:00Z",
            ),
        )
        conn.executemany(
            """
            INSERT INTO mating_mouse
                (mating_mouse_id, mating_id, mouse_id, role, joined_date,
                 removed_date, note, source_record_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "mating_mouse_colony_state_female",
                    "mating_colony_state",
                    "MT401",
                    "female",
                    "2026-04-15",
                    None,
                    "Active dam.",
                    "source_mating_colony_state",
                    "2026-05-09T11:11:00Z",
                ),
                (
                    "mating_mouse_colony_state_male",
                    "mating_colony_state",
                    "MT402",
                    "male",
                    "2026-04-15",
                    None,
                    "Active sire.",
                    "source_mating_colony_state",
                    "2026-05-09T11:11:00Z",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO litter_registry
                (litter_id, litter_label, mating_id, birth_date, number_born,
                 number_alive, number_weaned, weaning_date, status, note,
                 source_record_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "litter_colony_state",
                "F1",
                "mating_colony_state",
                "2026-05-01",
                6,
                5,
                None,
                "",
                "born",
                "Accepted active litter.",
                "source_mating_colony_state",
                "2026-05-09T11:12:00Z",
                "2026-05-09T11:12:00Z",
            ),
        )


def test_colony_state_uses_only_db_backed_current_records(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        client = TestClient(app)

        response = client.get("/api/ui/colony-state?as_of=2026-05-09")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "What is active now?"
        assert payload["summary"] == {
            "active_mice": 2,
            "active_card_snapshots": 1,
            "active_matings": 1,
            "active_litters": 1,
            "must_review": 1,
            "quick_check": 0,
        }
        [card] = payload["active_card_snapshots"]
        assert card["card_snapshot_id"].startswith("card_")
        assert card["source_layer"] == "canonical structured state"
        assert card["source_photo"]["photo_id"] == "photo_colony_state"
        assert card["source_photo"]["source_photo_role"] == "primary_evidence"
        assert card["mouse_count"] == 2
        assert card["collapsed_sections"] == {
            "mice": 2,
            "note_lines": 2,
            "review_blockers": 1,
            "source_evidence": 1,
        }
        assert payload["strain_summary"] == [{"strain": "C57BL/6J", "active_mice": 2}]
        assert payload["status_summary"] == [{"status": "active", "mouse_count": 2}]
        assert payload["active_matings"] == [
            {
                "mating_id": "mating_colony_state",
                "mating_label": "C-12 breeding pair",
                "strain_goal": "C57BL/6J",
                "expected_genotype": "",
                "start_date": "2026-04-15",
                "status": "active",
                "purpose": "breeding",
                "source_record_id": "source_mating_colony_state",
                "parent_count": 2,
                "active_litter_count": 1,
                "source_layer": "canonical structured state",
                "collapsed_sections": {
                    "parents": 2,
                    "active_litters": 1,
                    "source_evidence": 1,
                },
            }
        ]
        assert payload["active_litters"] == [
            {
                "litter_id": "litter_colony_state",
                "litter_label": "F1",
                "mating_id": "mating_colony_state",
                "mating_label": "C-12 breeding pair",
                "birth_date": "2026-05-01",
                "number_born": 6,
                "number_alive": 5,
                "number_weaned": None,
                "weaning_date": "",
                "status": "born",
                "source_record_id": "source_mating_colony_state",
                "source_layer": "canonical structured state",
                "collapsed_sections": {
                    "pups_alive": 5,
                    "source_evidence": 1,
                },
                "action_hint": {
                    "mode": "upcoming",
                    "label": "Separation review upcoming",
                    "priority": "low",
                    "age_days": 8,
                    "threshold_days": 30,
                    "automation": "manual_review_only",
                    "suggested_actions": ["watch_litter", "review_at_threshold"],
                },
            }
        ]
        assert payload["attention_links"] == [
            {
                "label": "Focus Review",
                "target_path": "/api/ui/focus-review",
                "must_review": 1,
                "quick_check": 0,
            }
        ]
        assert "review_items" not in card
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path


def test_colony_state_empty_state_does_not_fabricate_records(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get(f"/api/ui/colony-state?as_of={date.today().isoformat()}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["summary"] == {
            "active_mice": 0,
            "active_card_snapshots": 0,
            "active_matings": 0,
            "active_litters": 0,
            "must_review": 0,
            "quick_check": 0,
        }
        assert payload["active_card_snapshots"] == []
        assert payload["active_matings"] == []
        assert payload["active_litters"] == []
        assert payload["strain_summary"] == []
        assert payload["status_summary"] == []
        assert payload["attention_links"] == []
        assert payload["empty_state"] == {
            "message": "No accepted active colony records are available yet.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_colony_state_litter_action_hints_are_read_only_and_threshold_backed(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        observed_date = date(2026, 5, 9)
        due_birth_date = (observed_date - timedelta(days=35)).isoformat()
        overdue_birth_date = (observed_date - timedelta(days=50)).isoformat()
        high_overdue_birth_date = (observed_date - timedelta(days=65)).isoformat()
        with db.connection() as conn:
            conn.executemany(
                """
                INSERT INTO litter_registry
                    (litter_id, litter_label, mating_id, birth_date, number_born,
                     number_alive, number_weaned, weaning_date, status, note,
                     source_record_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "litter_due_review",
                        "F2",
                        "mating_colony_state",
                        due_birth_date,
                        7,
                        7,
                        None,
                        "",
                        "born",
                        "Due for separation review.",
                        "source_mating_colony_state",
                        "2026-05-09T11:13:00Z",
                        "2026-05-09T11:13:00Z",
                    ),
                    (
                        "litter_overdue_review",
                        "F3",
                        "mating_colony_state",
                        overdue_birth_date,
                        4,
                        4,
                        None,
                        "",
                        "born",
                        "Overdue for separation review.",
                        "source_mating_colony_state",
                        "2026-05-09T11:14:00Z",
                        "2026-05-09T11:14:00Z",
                    ),
                    (
                        "litter_high_overdue_review",
                        "F4",
                        "mating_colony_state",
                        high_overdue_birth_date,
                        3,
                        3,
                        None,
                        "",
                        "born",
                        "High-overdue separation review.",
                        "source_mating_colony_state",
                        "2026-05-09T11:15:00Z",
                        "2026-05-09T11:15:00Z",
                    ),
                ],
            )
        client = TestClient(app)

        response = client.get("/api/ui/colony-state?as_of=2026-05-09")

        assert response.status_code == 200
        payload = response.json()
        hints = {item["litter_id"]: item["action_hint"] for item in payload["active_litters"]}
        assert hints["litter_colony_state"] == {
            "mode": "upcoming",
            "label": "Separation review upcoming",
            "priority": "low",
            "age_days": (observed_date - date.fromisoformat("2026-05-01")).days,
            "threshold_days": 30,
            "automation": "manual_review_only",
            "suggested_actions": ["watch_litter", "review_at_threshold"],
        }
        assert hints["litter_due_review"]["mode"] == "review_due"
        assert hints["litter_due_review"]["priority"] == "medium"
        assert hints["litter_due_review"]["threshold_days"] == 30
        assert hints["litter_overdue_review"]["mode"] == "overdue_review"
        assert hints["litter_overdue_review"]["priority"] == "medium"
        assert hints["litter_high_overdue_review"]["mode"] == "urgent_review"
        assert hints["litter_high_overdue_review"]["priority"] == "high"
        assert all(hint["automation"] == "manual_review_only" for hint in hints.values())
        with db.connection() as conn:
            statuses = {
                row["litter_id"]: row["status"]
                for row in conn.execute(
                    "SELECT litter_id, status FROM litter_registry WHERE litter_id LIKE 'litter_%review' OR litter_id = 'litter_colony_state'"
                ).fetchall()
            }
            event_count = conn.execute("SELECT COUNT(*) FROM mouse_event").fetchone()[0]
        assert statuses["litter_due_review"] == "born"
        assert statuses["litter_overdue_review"] == "born"
        assert statuses["litter_high_overdue_review"] == "born"
        assert event_count == 0
    finally:
        db.DB_PATH = old_db_path


def test_colony_schedule_derives_tasks_from_accepted_litter_and_rules(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        with db.connection() as conn:
            conn.execute(
                "UPDATE litter_registry SET status = ? WHERE litter_id = ?",
                ("pre_weaning", "litter_colony_state"),
            )
        client = TestClient(app)

        response = client.get("/api/ui/colony-schedule?as_of=2026-05-09")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "What needs doing next?"
        assert payload["rule_set"] == {
            "rule_set_id": "breeding_rule_default_20260509",
            "display_name": "Default breeding operation review rules",
            "source_layer": "parsed or intermediate result",
        }
        assert payload["summary"] == {
            "due_now": 0,
            "due_soon": 1,
            "later": 0,
            "blocked_by_review": 1,
            "completed": 0,
        }
        [group] = payload["task_groups"]
        assert group["group"] == "due_soon"
        [task] = group["tasks"]
        assert task == {
            "task_id": "schedule_litter_separation_litter_colony_state",
            "task_type": "litter_separation",
            "label": "Separate/wean litter F1",
            "status": "blocked_by_review",
            "recorded_date": "2026-05-01",
            "due_date": "2026-05-31",
            "days_until_due": 22,
            "source_layer": "export or view",
            "source_entity": {
                "entity_type": "litter",
                "entity_id": "litter_colony_state",
                "label": "F1",
            },
            "source_evidence": {
                "source_record_id": "source_mating_colony_state",
                "mating_id": "mating_colony_state",
                "mating_label": "C-12 breeding pair",
            },
            "due_date_rule": {
                "rule_set_id": "breeding_rule_default_20260509",
                "rule_key": "litter_separation_due_after_days",
                "value_days": 30,
            },
            "attention_link": {
                "label": "Open Focus Review",
                "target_path": "/api/ui/focus-review",
                "must_review": 1,
                "quick_check": 0,
            },
        }
        assert payload["calendar_mirror"] == {
            "status": "not_configured",
            "canonical_source": "MouseDB internal schedule",
            "note": "External calendar sync can mirror accepted schedule tasks later; it is not canonical.",
        }
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path


def test_colony_schedule_empty_state_does_not_fabricate_tasks(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/colony-schedule?as_of=2026-05-09")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["summary"] == {
            "due_now": 0,
            "due_soon": 0,
            "later": 0,
            "blocked_by_review": 0,
            "completed": 0,
        }
        assert payload["task_groups"] == []
        assert payload["empty_state"] == {
            "message": "No accepted schedule tasks are available yet.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_mouse_timeline_shows_accepted_events_without_review_details(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        with db.connection() as conn:
            conn.execute(
                "UPDATE mouse_master SET litter_id = ? WHERE mouse_id = ?",
                ("litter_colony_state", "MT401"),
            )
            conn.executemany(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "event_mt401_birth",
                        "MT401",
                        "born",
                        "2026-05-01",
                        "litter",
                        "litter_colony_state",
                        "source_mating_colony_state",
                        json.dumps(
                            {
                                "source_photo_id": "photo_birth",
                                "source_note_item_id": "note_birth",
                                "photo_evidence_id": "pe_birth",
                            },
                            ensure_ascii=False,
                        ),
                        "local_user",
                        "2026-05-09T11:16:00Z",
                    ),
                    (
                        "event_mt401_weaned",
                        "MT401",
                        "weaned",
                        "2026-05-31",
                        "litter",
                        "litter_colony_state",
                        "source_mating_colony_state",
                        json.dumps({"weaning_count": 5}, ensure_ascii=False),
                        "local_user",
                        "2026-05-31T09:00:00Z",
                    ),
                ],
            )
        client = TestClient(app)

        response = client.get("/api/ui/mouse-timeline?mouse_id=MT401")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "How did this mouse get here?"
        assert payload["mouse"] == {
            "mouse_id": "MT401",
            "display_id": "MT401",
            "status": "active",
            "strain": "C57BL/6J",
            "litter_id": "litter_colony_state",
        }
        assert payload["summary"] == {
            "accepted_events": 2,
            "source_records": 1,
            "must_review": 1,
            "quick_check": 0,
        }
        assert payload["lineage"] == {
            "father": None,
            "mother": None,
            "litter": {
                "litter_id": "litter_colony_state",
                "litter_label": "F1",
                "mating_id": "mating_colony_state",
                "mating_label": "C-12 breeding pair",
                "birth_date": "2026-05-01",
            },
        }
        assert payload["events"] == [
            {
                "event_id": "event_mt401_birth",
                "event_type": "born",
                "event_date": "2026-05-01",
                "label": "born",
                "source_layer": "canonical structured state",
                "related_entity": {
                    "entity_type": "litter",
                    "entity_id": "litter_colony_state",
                },
                "source_evidence": {
                    "source_record_id": "source_mating_colony_state",
                    "source_label": "Reviewed mating cage C-12",
                    "source_type": "manual_review",
                    "event_trace": {
                        "source_layer": "export or view",
                        "source_photo_id": "photo_birth",
                        "source_photo_filename": "",
                        "source_note_item_id": "note_birth",
                        "source_note_text": "",
                        "source_note_line_number": None,
                        "photo_evidence_id": "pe_birth",
                        "evidence_kind": "",
                        "evidence_raw_text": "",
                        "evidence_normalized_value": "",
                        "evidence_status": "",
                        "needs_review": False,
                        "trace_status": "source_detail_missing",
                    },
                },
                "evidence_refs": {
                    "source_record_id": "source_mating_colony_state",
                    "source_photo_id": "photo_birth",
                    "source_note_item_id": "note_birth",
                    "photo_evidence_id": "pe_birth",
                },
            },
            {
                "event_id": "event_mt401_weaned",
                "event_type": "weaned",
                "event_date": "2026-05-31",
                "label": "weaned",
                "source_layer": "canonical structured state",
                "related_entity": {
                    "entity_type": "litter",
                    "entity_id": "litter_colony_state",
                },
                "source_evidence": {
                    "source_record_id": "source_mating_colony_state",
                    "source_label": "Reviewed mating cage C-12",
                    "source_type": "manual_review",
                },
                "evidence_refs": {
                    "source_record_id": "source_mating_colony_state",
                    "source_photo_id": "",
                    "source_note_item_id": "",
                    "photo_evidence_id": "",
                },
            },
        ]
        assert payload["attention_links"] == [
            {
                "label": "Open Focus Review",
                "target_path": "/api/ui/focus-review",
                "must_review": 1,
                "quick_check": 0,
            }
        ]
        assert "review_items" not in payload
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path


def test_mouse_timeline_resolves_event_photo_note_and_evidence_trace(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, photo_id, parse_id, card_type, line_number,
                     raw_line_text, parsed_type, interpreted_status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "note_timeline_mt401",
                    "photo_colony_state",
                    "parse_colony_state",
                    "Separated",
                    1,
                    "MT401 R'",
                    "mouse_item",
                    "active",
                    0.96,
                    0,
                ),
            )
            note_id = "note_timeline_mt401"
            note_text = "MT401 R'"
            conn.execute(
                """
                INSERT INTO photo_evidence_item
                    (photo_evidence_id, source_photo_id, parse_id, note_item_id,
                     evidence_kind, observed_raw_text, normalized_value, confidence,
                     needs_review, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pe_timeline_note",
                    "photo_colony_state",
                    "parse_colony_state",
                    note_id,
                    "note_line",
                    note_text,
                    "MT401 R'",
                    96,
                    0,
                    "linked",
                    "2026-05-09T11:16:00Z",
                    "2026-05-09T11:16:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_mt401_card_confirmed",
                    "MT401",
                    "card_confirmed",
                    "2026-05-09",
                    "card_snapshot",
                    "card_timeline",
                    "source_mating_colony_state",
                    json.dumps(
                        {
                            "source_photo_id": "photo_colony_state",
                            "source_note_item_id": note_id,
                            "photo_evidence_id": "pe_timeline_note",
                        },
                        ensure_ascii=False,
                    ),
                    "local_user",
                    "2026-05-09T11:16:00Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/mouse-timeline?mouse_id=MT401")

        assert response.status_code == 200
        payload = response.json()
        [event] = payload["events"]
        assert event["event_id"] == "event_mt401_card_confirmed"
        assert event["source_evidence"]["event_trace"] == {
            "source_layer": "export or view",
            "source_photo_id": "photo_colony_state",
            "source_photo_filename": "colony-state-card.jpg",
            "source_note_item_id": note_id,
            "source_note_text": "MT401 R'",
            "source_note_line_number": 1,
            "photo_evidence_id": "pe_timeline_note",
            "evidence_kind": "note_line",
            "evidence_raw_text": "MT401 R'",
            "evidence_normalized_value": "MT401 R'",
            "evidence_status": "linked",
            "needs_review": False,
            "trace_status": "resolved",
        }
    finally:
        db.DB_PATH = old_db_path


def test_mouse_timeline_empty_state_does_not_fabricate_events(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/mouse-timeline")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["mouse"] is None
        assert payload["events"] == []
        assert payload["summary"] == {
            "accepted_events": 0,
            "source_records": 0,
            "must_review": 0,
            "quick_check": 0,
        }
        assert payload["empty_state"] == {
            "message": "Choose a mouse to view accepted timeline events.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_action_log_viewer_exposes_recent_actions_without_mutation(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.executemany(
                """
                INSERT INTO action_log
                    (action_id, action_type, target_id, before_value, after_value,
                     performed_by, performed_role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "action_review_resolved",
                        "review_resolved",
                        "review_mt401",
                        json.dumps({"status": "open"}, ensure_ascii=False),
                        json.dumps({"status": "resolved"}, ensure_ascii=False),
                        "local_user",
                        "Colony Reviewer",
                        "2026-05-09T11:20:00Z",
                    ),
                    (
                        "action_mouse_update",
                        "mouse_updated",
                        "MT401",
                        "unknown",
                        "female",
                        "local_user",
                        "Colony Reviewer",
                        "2026-05-09T11:18:00Z",
                    ),
                ],
            )
        client = TestClient(app)

        response = client.get("/api/ui/action-log?target_id=review_mt401")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["filters"] == {"target_id": "review_mt401", "action_type": "", "limit": 50}
        assert payload["summary"] == {"returned_actions": 1, "available_action_types": 2}
        assert payload["action_types"] == [
            {"action_type": "mouse_updated", "count": 1},
            {"action_type": "review_resolved", "count": 1},
        ]
        assert payload["actions"] == [
            {
                "action_id": "action_review_resolved",
                "action_type": "review_resolved",
                "target_id": "review_mt401",
                "before_value": '{"status": "open"}',
                "after_value": '{"status": "resolved"}',
                "before": {"status": "open"},
                "after": {"status": "resolved"},
                "evidence_refs": {
                    "source_record_id": "",
                    "source_photo_id": "",
                    "source_note_item_id": "",
                    "photo_evidence_id": "",
                },
                "evidence_summary": {
                    "has_supporting_evidence": False,
                    "supporting_evidence_count": 0,
                    "label": "No supporting evidence linked",
                },
                "performed_by": "local_user",
                "performed_role": "Colony Reviewer",
                "created_at": "2026-05-09T11:20:00Z",
            }
        ]
        with db.connection() as conn:
            action_count = conn.execute("SELECT COUNT(*) AS count FROM action_log").fetchone()["count"]
        assert action_count == 2
    finally:
        db.DB_PATH = old_db_path


def test_action_log_viewer_summarizes_supporting_evidence_without_forcing_json_inspection(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO action_log
                    (action_id, action_type, target_id, before_value, after_value,
                     performed_by, performed_role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "action_mouse_moved",
                    "mouse_cage_moved",
                    "MT401",
                    json.dumps({"cage_id": "cage_old"}, ensure_ascii=False),
                    json.dumps(
                        {
                            "cage_id": "cage_new",
                            "source_record_id": "source_move_401",
                            "evidence_refs": {
                                "source_photo_id": "photo_move_401",
                                "source_note_item_id": "note_move_401",
                                "photo_evidence_id": "pe_move_401",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "local_user",
                    "Colony Reviewer",
                    "2026-05-09T11:20:00Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/action-log?target_id=MT401")

        assert response.status_code == 200
        action = response.json()["actions"][0]
        assert action["evidence_refs"] == {
            "source_record_id": "source_move_401",
            "source_photo_id": "photo_move_401",
            "source_note_item_id": "note_move_401",
            "photo_evidence_id": "pe_move_401",
        }
        assert action["evidence_summary"] == {
            "has_supporting_evidence": True,
            "supporting_evidence_count": 4,
            "label": "Open supporting evidence",
        }
    finally:
        db.DB_PATH = old_db_path


def test_mouse_pedigree_shows_selected_path_and_field_evidence(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE mouse_master
                SET father_id = ?, mother_id = ?, litter_id = ?
                WHERE mouse_id = ?
                """,
                ("MT402", "", "litter_colony_state", "MT401"),
            )
            conn.executemany(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, raw_strain_text, sex, dob_raw, dob_start,
                     litter_id, status, source_photo_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "MT403",
                        "MT403",
                        "C57BL/6J",
                        "male",
                        "2026-05-01",
                        "2026-05-01",
                        "litter_colony_state",
                        "active",
                        "photo_colony_state",
                        "2026-05-09T11:20:00Z",
                        "2026-05-09T11:20:00Z",
                    ),
                    (
                        "MT404",
                        "MT404",
                        "C57BL/6J",
                        "female",
                        "2026-05-01",
                        "2026-05-01",
                        "litter_colony_state",
                        "active",
                        "photo_colony_state",
                        "2026-05-09T11:20:00Z",
                        "2026-05-09T11:20:00Z",
                    ),
                    (
                        "MT405",
                        "MT405",
                        "C57BL/6J",
                        "male",
                        "2026-05-01",
                        "2026-05-01",
                        "litter_colony_state",
                        "active",
                        "photo_colony_state",
                        "2026-05-09T11:20:00Z",
                        "2026-05-09T11:20:00Z",
                    ),
                ],
            )
        client = TestClient(app)

        response = client.get("/api/ui/mouse-pedigree?mouse_id=MT401")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "Where did this mouse come from?"
        assert payload["mode"] == "selected_path"
        assert payload["mouse"] == {
            "mouse_id": "MT401",
            "display_id": "MT401",
            "status": "active",
            "strain": "C57BL/6J",
            "litter_id": "litter_colony_state",
        }
        assert payload["relationship_summary"] == {
            "confirmed_relationships": 3,
            "pending_relationships": 1,
            "same_litter_siblings": 3,
            "offspring_events": 0,
            "must_review": 1,
            "quick_check": 0,
        }
        assert payload["nodes"]["father"] == {
            "node_type": "mouse",
            "relationship": "father",
            "mouse_id": "MT402",
            "display_id": "MT402",
            "status": "active",
            "strain": "C57BL/6J",
            "relationship_status": "confirmed",
            "source_layer": "canonical structured state",
        }
        assert payload["nodes"]["mother"] == {
            "node_type": "pending_relationship",
            "relationship": "mother",
            "label": "Parent pending",
            "relationship_status": "pending_review",
            "not_inferred": True,
        }
        assert payload["nodes"]["mating"]["mating_id"] == "mating_colony_state"
        assert payload["nodes"]["litter"]["litter_id"] == "litter_colony_state"
        assert [node["mouse_id"] for node in payload["nodes"]["same_litter_siblings"]] == [
            "MT403",
            "MT404",
            "MT405",
        ]
        assert payload["evidence_rows"] == [
            {
                "field": "mother_id",
                "value": "Parent pending",
                "status": "pending_review",
                "source_layer": "canonical structured state",
                "source": {
                    "source_record_id": "",
                    "label": "No accepted parent evidence",
                    "source_type": "pending_relationship",
                },
                "not_inferred": True,
            },
            {
                "field": "father_id",
                "value": "MT402",
                "status": "confirmed",
                "source_layer": "canonical structured state",
                "source": {
                    "source_record_id": "source_mating_colony_state",
                    "label": "Reviewed mating cage C-12",
                    "source_type": "manual_review",
                },
            },
            {
                "field": "litter_id",
                "value": "litter_colony_state",
                "status": "confirmed",
                "source_layer": "canonical structured state",
                "source": {
                    "source_record_id": "source_mating_colony_state",
                    "label": "Reviewed mating cage C-12",
                    "source_type": "manual_review",
                },
            },
            {
                "field": "mating_id",
                "value": "mating_colony_state",
                "status": "confirmed",
                "source_layer": "canonical structured state",
                "source": {
                    "source_record_id": "source_mating_colony_state",
                    "label": "Reviewed mating cage C-12",
                    "source_type": "manual_review",
                },
            },
        ]
        assert payload["attention_links"] == [
            {
                "label": "Review parent evidence",
                "target_path": "/api/ui/mouse-pedigree",
                "target_view": "mouse-detail",
                "reason": "relationship_evidence_missing",
                "mode": "relationship_evidence_missing",
                "pending_relationships": 1,
                "must_review": 1,
                "quick_check": 0,
            },
            {
                "label": "Open Focus Review",
                "target_path": "/api/ui/focus-review",
                "target_view": "review",
                "reason": "open_review_workload",
                "must_review": 1,
                "quick_check": 0,
            }
        ]
        assert "review_items" not in payload
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path


def test_mouse_pedigree_pending_parent_link_without_open_review_workload(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, raw_strain_text, sex, dob_raw, dob_start,
                     current_card_snapshot_id, status, source_photo_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "MT777",
                    "MT777",
                    "B6J",
                    "female",
                    "2026-05-01",
                    "2026-05-01",
                    None,
                    "active",
                    None,
                    "2026-05-09T13:00:00Z",
                    "2026-05-09T13:00:00Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/mouse-pedigree", params={"mouse_id": "MT777"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["relationship_summary"] == {
            "confirmed_relationships": 0,
            "pending_relationships": 2,
            "same_litter_siblings": 0,
            "offspring_events": 0,
            "must_review": 0,
            "quick_check": 0,
        }
        assert [row["source"] for row in payload["evidence_rows"]] == [
            {
                "source_record_id": "",
                "label": "No accepted parent evidence",
                "source_type": "pending_relationship",
            },
            {
                "source_record_id": "",
                "label": "No accepted parent evidence",
                "source_type": "pending_relationship",
            },
        ]
        assert all(row["source_layer"] == "canonical structured state" for row in payload["evidence_rows"])
        assert all(row["not_inferred"] is True for row in payload["evidence_rows"])
        assert payload["attention_links"] == [
            {
                "label": "Review parent evidence",
                "target_path": "/api/ui/mouse-pedigree",
                "target_view": "mouse-detail",
                "reason": "relationship_evidence_missing",
                "mode": "relationship_evidence_missing",
                "pending_relationships": 2,
                "must_review": 0,
                "quick_check": 0,
            }
        ]
        assert "review_items" not in payload
    finally:
        db.DB_PATH = old_db_path


def test_mouse_pedigree_empty_state_does_not_fabricate_relationships(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/mouse-pedigree")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["mouse"] is None
        assert payload["nodes"] == {}
        assert payload["evidence_rows"] == []
        assert payload["relationship_summary"] == {
            "confirmed_relationships": 0,
            "pending_relationships": 0,
            "same_litter_siblings": 0,
            "offspring_events": 0,
            "must_review": 0,
            "quick_check": 0,
        }
        assert payload["empty_state"] == {
            "message": "Choose a mouse to view accepted pedigree relationships.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_evidence_ledger_separates_raw_ocr_interpretation_and_links_review(tmp_path: Path) -> None:
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
                    "photo_ledger_card",
                    "ledger-card.jpg",
                    "data/photos/test/ledger-card.jpg",
                    "2026-05-09T12:00:00Z",
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
                    "parse_ledger_card",
                    "photo_ledger_card",
                    "manual_photo_transcription",
                    json.dumps({"raw": "MT401 R0"}, ensure_ascii=False),
                    "2026-05-09T12:01:00Z",
                    "review",
                    0.62,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO card_note_item_log
                    (note_item_id, photo_id, parse_id, card_type, line_number,
                     raw_line_text, parsed_type, interpreted_status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "note_ledger_mt401",
                    "photo_ledger_card",
                    "parse_ledger_card",
                    "Separated",
                    1,
                    "MT401 R0",
                    "mouse_item",
                    "active",
                    0.62,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO photo_evidence_item
                    (photo_evidence_id, source_photo_id, parse_id, note_item_id,
                     card_type, evidence_kind, roi_label, bbox_json,
                     observed_raw_text, ocr_text, parsed_value,
                     raw_extracted_value, normalized_value, confidence,
                     confidence_source, evidence_reference_json,
                     interpretation, needs_review, review_reason, linked_mouse_id,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pe_ledger_mt401_ear",
                    "photo_ledger_card",
                    "parse_ledger_card",
                    "note_ledger_mt401",
                    "Separated",
                    "ear_label",
                    "note_line_1",
                    json.dumps({"x": 10, "y": 20, "w": 80, "h": 24}),
                    "MT401 R0",
                    "MT401 R0",
                    "right_circle",
                    "MT401 R0",
                    "right_circle",
                    0.62,
                    "manual_photo_transcription:note_line",
                    json.dumps(
                        {
                            "source_layer": "parsed or intermediate result",
                            "source_photo_id": "photo_ledger_card",
                            "parse_id": "parse_ledger_card",
                            "note_item_id": "note_ledger_mt401",
                            "evidence_kind": "ear_label",
                            "roi_label": "note_line_1",
                        }
                    ),
                    "R0 may indicate a right ear circle; keep reviewable.",
                    1,
                    "Ambiguous ear mark.",
                    None,
                    "review_open",
                    "2026-05-09T12:02:00Z",
                    "2026-05-09T12:02:00Z",
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
                    "review_ledger_ear_mark",
                    "parse_ledger_card",
                    "Medium",
                    "Ambiguous ear mark",
                    "MT401 R0",
                    "Confirm ear mark from source photo.",
                    "Ambiguous ear mark.",
                    "open",
                    "2026-05-09T12:03:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO review_evidence_link
                    (link_id, review_id, photo_evidence_id, link_reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "link_ledger_ear_mark",
                    "review_ledger_ear_mark",
                    "pe_ledger_mt401_ear",
                    "Review is grounded in the original note line.",
                    "2026-05-09T12:03:10Z",
                ),
            )
        client = TestClient(app)

        response = client.get("/api/ui/evidence-ledger?source_photo_id=photo_ledger_card")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["page_question"] == "What evidence supports this record?"
        assert payload["summary"] == {
            "total_evidence": 1,
            "needs_review": 1,
            "linked_events": 0,
            "source_photos": 1,
        }
        assert payload["evidence_items"] == [
            {
                "photo_evidence_id": "pe_ledger_mt401_ear",
                "evidence_kind": "ear_label",
                "card_type": "Separated",
                "status": "review_open",
                "source_photo": {
                    "photo_id": "photo_ledger_card",
                    "original_filename": "ledger-card.jpg",
                    "raw_source_kind": "cage_card_photo",
                    "uploaded_at": "2026-05-09T12:00:00Z",
                    "open_source_photo_label": "Open source photo",
                },
                "parsed_trace": {
                    "parse_id": "parse_ledger_card",
                    "source_name": "manual_photo_transcription",
                    "status": "review",
                    "confidence": 0.62,
                    "needs_review": True,
                },
                "direct_observation": {
                    "roi_label": "note_line_1",
                    "bbox": {"x": 10, "y": 20, "w": 80, "h": 24},
                    "observed_raw_text": "MT401 R0",
                    "raw_extracted_value": "MT401 R0",
                },
                "ocr": {"text": "MT401 R0"},
                "ai_interpretation": {
                    "parsed_value": "right_circle",
                    "normalized_value": "right_circle",
                    "confidence": 0.62,
                    "confidence_source": "manual_photo_transcription:note_line",
                    "interpretation": "R0 may indicate a right ear circle; keep reviewable.",
                    "needs_review": True,
                    "review_reason": "Ambiguous ear mark.",
                },
                "evidence_reference": {
                    "source_layer": "parsed or intermediate result",
                    "source_photo_id": "photo_ledger_card",
                    "parse_id": "parse_ledger_card",
                    "note_item_id": "note_ledger_mt401",
                    "evidence_kind": "ear_label",
                    "roi_label": "note_line_1",
                },
                "links": {
                    "note_item_id": "note_ledger_mt401",
                    "linked_mouse_id": "",
                    "linked_cage_id": "",
                    "linked_event_id": "",
                    "review_ids": ["review_ledger_ear_mark"],
                },
                "correction_history": [],
            }
        ]
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path


def test_evidence_ledger_empty_state_does_not_fabricate_evidence(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        db.DB_PATH = tmp_path / "mouse_lims.sqlite"
        db.init_db()
        client = TestClient(app)

        response = client.get("/api/ui/evidence-ledger")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_layer"] == "export or view"
        assert payload["evidence_items"] == []
        assert payload["summary"] == {
            "total_evidence": 0,
            "needs_review": 0,
            "linked_events": 0,
            "source_photos": 0,
        }
        assert payload["empty_state"] == {
            "message": "No photo evidence items are available yet.",
            "fabricated_records": False,
        }
    finally:
        db.DB_PATH = old_db_path


def test_colony_state_excludes_noncanonical_card_snapshots(tmp_path: Path) -> None:
    old_db_path = db.DB_PATH
    try:
        seed_colony_state_records(tmp_path)
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE card_snapshot
                SET source_layer = ?
                WHERE parse_id = ?
                """,
                ("parsed or intermediate result", "parse_colony_state"),
            )
        client = TestClient(app)

        response = client.get("/api/ui/colony-state")

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["active_mice"] == 2
        assert payload["summary"]["active_card_snapshots"] == 0
        assert payload["active_card_snapshots"] == []
        assert payload["empty_state"]["fabricated_records"] is False
    finally:
        db.DB_PATH = old_db_path
