from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.labeling_rules import (
    apply_ear_sequence,
    interpret_crossed_out_status,
    match_samples_to_mice,
)
from app.main import (
    GenotypingRequestCreate,
    create_card_snapshot,
    list_labeling_rule_sets,
    request_genotyping,
    write_note_items_and_mouse_candidates,
)


ROOT = Path(__file__).resolve().parents[1]


def test_active_labeling_rule_seed_config_lives_outside_db_module() -> None:
    db_source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    seed_config = ROOT / "config" / "seeds" / "labeling_rule_sets.json"

    assert seed_config.exists()
    assert "ApoM Tg/Tg 2026-05-06" not in db_source
    assert "label_rule_apom_tgtg_20260506" not in db_source
    payload = json.loads(seed_config.read_text(encoding="utf-8"))
    assert payload["source_layer"] == "cache"
    assert payload["seed_scope"] == "pilot/example labeling rule configuration"
    assert payload["rule_sets"][0]["rule_set_id"] == "label_rule_apom_tgtg_20260506"


def test_labeling_rule_schema_seeds_default_apom_rule(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            rule = conn.execute(
                """
                SELECT display_name, numbering_order, mouse_number_scope,
                       ear_sequence_scope, crossed_out_handling, sample_mapping,
                       genotyping_target
                FROM labeling_rule_set
                WHERE rule_set_id = ?
                """,
                ("label_rule_apom_tgtg_20260506",),
            ).fetchone()
            sequence = conn.execute(
                """
                SELECT ear_label_code
                FROM labeling_rule_ear_sequence
                WHERE rule_set_id = ?
                ORDER BY sequence_index
                """,
                ("label_rule_apom_tgtg_20260506",),
            ).fetchall()

        assert dict(rule) == {
            "display_name": "ApoM Tg/Tg 2026-05-06",
            "numbering_order": "male_first",
            "mouse_number_scope": "continues_across_cages_within_same_id",
            "ear_sequence_scope": "resets_per_cage",
            "crossed_out_handling": "dead",
            "sample_mapping": "sample_id_equals_mouse_display_id",
            "genotyping_target": "ApoM-tg",
        }
        assert [row["ear_label_code"] for row in sequence[:6]] == [
            "R_PRIME",
            "L_PRIME",
            "R_PRIME_L_PRIME",
            "R_CIRCLE",
            "L_CIRCLE",
            "R_CIRCLE_L_CIRCLE",
        ]
    finally:
        db.DB_PATH = old_db_path


def test_ear_sequence_resets_for_each_cage_group():
    sequence = ["R_PRIME", "L_PRIME", "R_PRIME_L_PRIME"]
    note_groups = [
        [{"mouse": "1"}, {"mouse": "2"}],
        [{"mouse": "3"}, {"mouse": "4"}],
    ]

    result = apply_ear_sequence(note_groups, sequence)

    assert result[0][0]["expected_ear_label_code"] == "R_PRIME"
    assert result[0][1]["expected_ear_label_code"] == "L_PRIME"
    assert result[1][0]["expected_ear_label_code"] == "R_PRIME"
    assert result[1][1]["expected_ear_label_code"] == "L_PRIME"


def test_ear_sequence_deep_copies_groups_and_skips_dead_notes():
    sequence = ["R_PRIME", "L_PRIME", "R_PRIME_L_PRIME"]
    note_groups = [
        [
            {"mouse": "1", "metadata": {"raw": "1 R'"}},
            {"mouse": "2", "interpreted_status": "dead", "metadata": {"raw": "2 crossed"}},
            {"mouse": "3", "metadata": {"raw": "3 L'"}},
        ]
    ]

    result = apply_ear_sequence(note_groups, sequence)

    assert result is not note_groups
    assert result[0] is not note_groups[0]
    assert result[0][0] is not note_groups[0][0]
    assert result[0][0]["metadata"] is not note_groups[0][0]["metadata"]
    assert result[0][0]["expected_ear_label_code"] == "R_PRIME"
    assert result[0][1]["expected_ear_label_code"] is None
    assert result[0][2]["expected_ear_label_code"] == "L_PRIME"
    assert "expected_ear_label_code" not in note_groups[0][0]


def test_crossed_out_mouse_line_interprets_as_dead_under_rule():
    assert interpret_crossed_out_status("double", "dead") == "dead"
    assert interpret_crossed_out_status("single", "dead") == "dead"
    assert interpret_crossed_out_status("none", "dead") == "active"


def test_crossed_out_status_requires_review_when_not_dead_or_none():
    assert interpret_crossed_out_status("single", "review") == "review"
    assert interpret_crossed_out_status("double", "ignore") == "review"
    assert interpret_crossed_out_status("smudged", "dead") == "review"


def test_sample_id_matches_mouse_display_id():
    mice = [{"mouse_id": "mouse_24", "display_id": "24"}]
    samples = [{"sample_id": "24", "target_name": "ApoM-tg"}]

    [match] = match_samples_to_mice(samples, mice)

    assert match["match_status"] == "matched"
    assert match["mouse_id"] == "mouse_24"


def test_duplicate_sample_match_requires_review():
    mice = [
        {"mouse_id": "mouse_a", "display_id": "24"},
        {"mouse_id": "mouse_b", "display_id": "24"},
    ]
    samples = [{"sample_id": "24", "target_name": "ApoM-tg"}]

    [match] = match_samples_to_mice(samples, mice)

    assert match["match_status"] == "duplicate_mouse_match"
    assert match["mouse_id"] is None


def test_blank_sample_id_stays_unmatched():
    mice = [
        {"mouse_id": "mouse_blank", "display_id": ""},
        {"mouse_id": "mouse_missing"},
    ]
    samples = [{"sample_id": ""}, {}]

    matches = match_samples_to_mice(samples, mice)

    assert [match["match_status"] for match in matches] == ["unmatched", "unmatched"]
    assert [match["mouse_id"] for match in matches] == [None, None]


def test_writer_stores_expected_ear_label_metadata_without_overwriting_raw_note(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        rule_set_id = "label_rule_apom_tgtg_20260506"
        records = [
            (
                "photo_label_rule_a",
                "parse_label_rule_a",
                {
                    "type": "Separated",
                    "sourcePhotoId": "photo_label_rule_a",
                    "labelingRuleSetId": rule_set_id,
                    "notes": [{"raw": "101 L'"}, {"raw": "102 R'"}],
                },
            ),
            (
                "photo_label_rule_b",
                "parse_label_rule_b",
                {
                    "type": "Separated",
                    "sourcePhotoId": "photo_label_rule_b",
                    "labelingRuleSetId": rule_set_id,
                    "notes": [{"raw": "201 L'"}, {"raw": "202 R'"}],
                },
            ),
        ]

        with db.connection() as conn:
            for photo_id, parse_id, record in records:
                conn.execute(
                    """
                    INSERT INTO photo_log
                        (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        photo_id,
                        f"{photo_id}.jpg",
                        f"data/photos/test/{photo_id}.jpg",
                        "2026-05-06T00:00:00Z",
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
                        "2026-05-06T00:00:00Z",
                        "review",
                        90,
                        1,
                    ),
                )
                snapshot_id = create_card_snapshot(conn, parse_id, photo_id, record, "2026-05-06T00:00:00Z")
                write_note_items_and_mouse_candidates(
                    conn,
                    parse_id,
                    {**record, "cardSnapshotId": snapshot_id},
                    "review",
                )

            rows = conn.execute(
                """
                SELECT parse_id, raw_line_text, parsed_ear_label_raw, parsed_ear_label_code,
                       parsed_metadata_json
                FROM card_note_item_log
                WHERE line_number = 1
                ORDER BY parse_id
                """
            ).fetchall()

        assert len(rows) == 2
        assert [row["raw_line_text"] for row in rows] == ["101 L'", "201 L'"]
        assert [row["parsed_ear_label_raw"] for row in rows] == ["L'", "L'"]
        assert [row["parsed_ear_label_code"] for row in rows] == ["L_PRIME", "L_PRIME"]
        for row in rows:
            metadata = json.loads(row["parsed_metadata_json"])
            assert metadata["expected_ear_label_code"] == "R_PRIME"
            assert metadata["labeling_rule_set_id"] == rule_set_id
    finally:
        db.DB_PATH = old_db_path


def test_writer_skips_crossed_out_mouse_for_labeling_rule_sequence(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        rule_set_id = "label_rule_apom_tgtg_20260506"
        photo_id = "photo_label_rule_crossed"
        parse_id = "parse_label_rule_crossed"
        record = {
            "type": "Separated",
            "sourcePhotoId": photo_id,
            "labelingRuleSetId": rule_set_id,
            "notes": [
                {"raw": "301 R'", "strike": "single"},
                {"raw": "302 R'"},
                {"raw": "303 L'"},
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
                    "crossed-card.jpg",
                    "data/photos/test/crossed-card.jpg",
                    "2026-05-06T00:00:00Z",
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
                    "2026-05-06T00:00:00Z",
                    "review",
                    90,
                    1,
                ),
            )
            snapshot_id = create_card_snapshot(conn, parse_id, photo_id, record, "2026-05-06T00:00:00Z")
            write_note_items_and_mouse_candidates(
                conn,
                parse_id,
                {**record, "cardSnapshotId": snapshot_id},
                "review",
            )
            rows = conn.execute(
                """
                SELECT raw_line_text, strike_status, interpreted_status, parsed_metadata_json
                FROM card_note_item_log
                WHERE parse_id = ?
                ORDER BY line_number
                """,
                (parse_id,),
            ).fetchall()

        metadata = [json.loads(row["parsed_metadata_json"]) for row in rows]
        assert rows[0]["strike_status"] == "single"
        assert rows[0]["interpreted_status"] == "moved"
        assert metadata[0]["expected_ear_label_code"] is None
        assert metadata[1]["expected_ear_label_code"] == "R_PRIME"
        assert metadata[2]["expected_ear_label_code"] == "L_PRIME"
    finally:
        db.DB_PATH = old_db_path


def test_genotyping_request_uses_rule_set_default_target_without_overwriting_sample(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, raw_strain_text, status, last_verified_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("mouse_24", "24", "ApoM Tg/Tg", "active", "2026-05-06T00:00:00Z"),
            )

        result = request_genotyping(
            GenotypingRequestCreate(
                mouse_id="mouse_24",
                sample_id="24",
                target_name="",
                labeling_rule_set_id="label_rule_apom_tgtg_20260506",
            )
        )

        with db.connection() as conn:
            record = conn.execute(
                """
                SELECT mouse_id, sample_id, target_name
                FROM genotyping_record
                WHERE genotyping_id = ?
                """,
                (result["genotyping_id"],),
            ).fetchone()

        assert record["mouse_id"] == "mouse_24"
        assert record["sample_id"] == "24"
        assert record["target_name"] == "ApoM-tg"
    finally:
        db.DB_PATH = old_db_path


def test_labeling_rule_api_lists_active_rule_with_sequence(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()

        rules = list_labeling_rule_sets()

        [rule] = [item for item in rules if item["rule_set_id"] == "label_rule_apom_tgtg_20260506"]
        assert rule["display_name"] == "ApoM Tg/Tg 2026-05-06"
        assert rule["active"] is True
        assert rule["sample_mapping"] == "sample_id_equals_mouse_display_id"
        assert rule["genotyping_target"] == "ApoM-tg"
        assert rule["ear_label_sequence"][:2] == ["R_PRIME", "L_PRIME"]
    finally:
        db.DB_PATH = old_db_path


def test_genotyping_request_rejects_rule_set_sample_id_mismatch(tmp_path):
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            conn.executemany(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, raw_strain_text, status, last_verified_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("mouse_24", "24", "ApoM Tg/Tg", "active", "2026-05-06T00:00:00Z"),
                    ("mouse_25", "25", "ApoM Tg/Tg", "active", "2026-05-06T00:00:00Z"),
                ],
            )

        with pytest.raises(HTTPException) as exc_info:
            request_genotyping(
                GenotypingRequestCreate(
                    mouse_id="mouse_24",
                    sample_id="25",
                    target_name="",
                    labeling_rule_set_id="label_rule_apom_tgtg_20260506",
                )
            )

        assert exc_info.value.status_code == 409
        with db.connection() as conn:
            records = conn.execute("SELECT COUNT(*) AS count FROM genotyping_record").fetchone()
            mouse = conn.execute(
                "SELECT sample_id, genotyping_status FROM mouse_master WHERE mouse_id = ?",
                ("mouse_24",),
            ).fetchone()

        assert records["count"] == 0
        assert mouse["sample_id"] is None
        assert mouse["genotyping_status"] == "not_sampled"
    finally:
        db.DB_PATH = old_db_path
