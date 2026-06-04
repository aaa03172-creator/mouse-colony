from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import db
from app.main import GenotypingUpdate, update_genotyping


def seed_mouse(conn) -> None:
    conn.execute(
        """
        INSERT INTO mouse_master
            (mouse_id, display_id, raw_strain_text, status, last_verified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("mouse_gt_evidence", "GT318", "ApoM Tg/Tg", "active", "2026-05-09T00:00:00Z"),
    )


def test_genotype_result_requires_evidence_before_canonical_update(tmp_path) -> None:
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            seed_mouse(conn)

        with pytest.raises(HTTPException) as exc_info:
            update_genotyping(
                GenotypingUpdate(
                    mouse_id="mouse_gt_evidence",
                    sample_id="GT318",
                    raw_result="Tg/+",
                    normalized_result="Tg/+",
                    result_date="2026-05-09",
                    target_name="ApoM-tg",
                )
            )

        assert exc_info.value.status_code == 409
        assert "evidence" in str(exc_info.value.detail).lower()
        with db.connection() as conn:
            mouse = conn.execute(
                """
                SELECT genotyping_status, genotype_status, genotype_result
                FROM mouse_master
                WHERE mouse_id = ?
                """,
                ("mouse_gt_evidence",),
            ).fetchone()
            records = conn.execute("SELECT COUNT(*) AS count FROM genotyping_record").fetchone()

        assert mouse["genotyping_status"] == "not_sampled"
        assert mouse["genotype_status"] == "unknown"
        assert mouse["genotype_result"] is None
        assert records["count"] == 0
    finally:
        db.DB_PATH = old_db_path


def test_genotype_result_with_source_photo_records_evidence_and_event(tmp_path) -> None:
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            seed_mouse(conn)
            conn.execute(
                """
                INSERT INTO photo_log
                    (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "photo_gt_evidence",
                    "gel-result.jpg",
                    "data/photos/test/gel-result.jpg",
                    "2026-05-09T01:00:00Z",
                    "review_pending",
                    "gel_image",
                ),
            )

        result = update_genotyping(
            GenotypingUpdate(
                mouse_id="mouse_gt_evidence",
                sample_id="GT318",
                raw_result="Tg/+",
                normalized_result="Tg/+",
                result_date="2026-05-09",
                target_name="ApoM-tg",
                source_photo_id="photo_gt_evidence",
                notes="Reviewed against gel image.",
            )
        )

        with db.connection() as conn:
            record = conn.execute(
                """
                SELECT source_photo_id, source_record_id, photo_evidence_id,
                       raw_result, normalized_result, result_status
                FROM genotyping_record
                WHERE genotyping_id = ?
                """,
                (result["genotyping_id"],),
            ).fetchone()
            event = conn.execute(
                """
                SELECT event_type, related_entity_type, related_entity_id, details
                FROM mouse_event
                WHERE related_entity_id = ?
                """,
                (result["genotyping_id"],),
            ).fetchone()

        assert record["source_photo_id"] == "photo_gt_evidence"
        assert record["source_record_id"] is None
        assert record["photo_evidence_id"] is None
        assert record["raw_result"] == "Tg/+"
        assert record["normalized_result"] == "Tg/+"
        assert record["result_status"] == "resulted"
        assert event["event_type"] == "genotyped"
        assert event["related_entity_type"] == "genotyping_record"
        assert "photo_gt_evidence" in event["details"]
    finally:
        db.DB_PATH = old_db_path


def test_genotype_result_rejects_mismatched_photo_evidence_trace(tmp_path) -> None:
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        with db.connection() as conn:
            seed_mouse(conn)
            for photo_id, filename in [
                ("photo_gt_report", "genotype-report.jpg"),
                ("photo_other_report", "other-genotype-report.jpg"),
            ]:
                conn.execute(
                    """
                    INSERT INTO photo_log
                        (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        photo_id,
                        filename,
                        f"data/photos/test/{filename}",
                        "2026-05-09T01:00:00Z",
                        "review_pending",
                        "gel_image",
                    ),
                )
            conn.execute(
                """
                INSERT INTO photo_evidence_item
                    (photo_evidence_id, source_photo_id, evidence_kind, observed_raw_text,
                     parsed_value, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "photo_evidence_gt_report",
                    "photo_gt_report",
                    "genotype_result",
                    "GT318 Tg/+",
                    "Tg/+",
                    0.98,
                    "2026-05-09T01:05:00Z",
                    "2026-05-09T01:05:00Z",
                ),
            )

        with pytest.raises(HTTPException) as exc_info:
            update_genotyping(
                GenotypingUpdate(
                    mouse_id="mouse_gt_evidence",
                    sample_id="GT318",
                    raw_result="Tg/+",
                    normalized_result="Tg/+",
                    result_date="2026-05-09",
                    target_name="ApoM-tg",
                    source_photo_id="photo_other_report",
                    photo_evidence_id="photo_evidence_gt_report",
                )
            )

        assert exc_info.value.status_code == 400
        assert "photo_evidence_id does not match source_photo_id" in str(exc_info.value.detail)
        with db.connection() as conn:
            mouse = conn.execute(
                """
                SELECT genotyping_status, genotype_status, genotype_result
                FROM mouse_master
                WHERE mouse_id = ?
                """,
                ("mouse_gt_evidence",),
            ).fetchone()
            records = conn.execute("SELECT COUNT(*) AS count FROM genotyping_record").fetchone()
            events = conn.execute("SELECT COUNT(*) AS count FROM mouse_event").fetchone()

        assert mouse["genotyping_status"] == "not_sampled"
        assert mouse["genotype_status"] == "unknown"
        assert mouse["genotype_result"] is None
        assert records["count"] == 0
        assert events["count"] == 0
    finally:
        db.DB_PATH = old_db_path
