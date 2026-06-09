import json

from app import db
from app.db import connection, init_db
from app.main import app
from app.review_sentinel import (
    build_evidence_bundle_from_review_package_item,
    classify_recheck_risk,
    create_auto_recheck_run,
    create_data_guardian_review_item,
    create_proposed_changeset,
)
from fastapi.testclient import TestClient


def test_missing_source_photo_cannot_auto_pass():
    result = classify_recheck_risk(
        {
            "field_key": "mouse_id",
            "candidate_value": "NC 9 R'",
            "source_photo_id": None,
            "note_line_id": "line-1",
            "ocr_confidence": 0.95,
            "current_canonical_value": "NC 9 R'",
            "requires_external_service": False,
        }
    )

    assert result["risk_status"] == "photo_check_required"
    assert "missing_source_photo" in result["risk_reasons"]
    assert result["canonical"] is False


def test_canonical_conflict_routes_to_conflict_review():
    result = classify_recheck_risk(
        {
            "field_key": "mouse_id",
            "candidate_value": "NC 9 R'",
            "source_photo_id": "photo-1",
            "note_line_id": "line-1",
            "ocr_confidence": 0.91,
            "current_canonical_value": "NC 9 R''",
            "requires_external_service": False,
        }
    )

    assert result["risk_status"] == "conflict_review"
    assert "canonical_conflict" in result["risk_reasons"]
    assert result["canonical"] is False


def test_external_service_requirement_blocks_recheck_by_default():
    result = classify_recheck_risk(
        {
            "field_key": "genotype",
            "candidate_value": None,
            "source_photo_id": "photo-1",
            "note_line_id": "line-1",
            "ocr_confidence": 0.4,
            "current_canonical_value": None,
            "requires_external_service": True,
        }
    )

    assert result["risk_status"] == "blocked"
    assert "external_service_required" in result["risk_reasons"]
    assert result["canonical"] is False


def test_low_confidence_routes_to_quick_review():
    result = classify_recheck_risk(
        {
            "field_key": "mouse_id",
            "candidate_value": "NC 9 R'",
            "source_photo_id": "photo-1",
            "note_line_id": "line-1",
            "ocr_confidence": 0.49,
            "current_canonical_value": "NC 9 R'",
            "requires_external_service": False,
        }
    )

    assert result["risk_status"] == "quick_review"
    assert "low_ocr_confidence" in result["risk_reasons"]
    assert result["canonical"] is False


def test_auto_recheck_storage_is_non_canonical(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()

    run_id = create_auto_recheck_run(
        source_type="review_package",
        source_id="pkg-1",
        input_manifest={"items": 1},
        allow_external_services=False,
    )
    review_id = create_data_guardian_review_item(
        run_id=run_id,
        target_type="field",
        target_id="field-1",
        field_key="mouse_id",
        candidate_value_raw="NC 9 R'",
        candidate_value_normalized="NC 9 R'",
        current_canonical_value="NC 9 R''",
        previous_confirmed_value="NC 9 R''",
        risk_status="conflict_review",
        risk_reasons=["canonical_conflict"],
        recommended_action="compare_photo_and_confirmed_value",
        evidence_bundle_id="evidence-1",
    )

    with connection() as conn:
        run = conn.execute(
            "SELECT canonical, allow_external_services FROM auto_recheck_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT canonical, risk_status FROM data_guardian_review_items WHERE review_id = ?",
            (review_id,),
        ).fetchone()

    assert run["canonical"] == 0
    assert run["allow_external_services"] == 0
    assert item["canonical"] == 0
    assert item["risk_status"] == "conflict_review"


def test_review_package_item_builds_non_canonical_evidence_bundle():
    bundle = build_evidence_bundle_from_review_package_item(
        {
            "review_id": "review-1",
            "field_key": "mouse_id",
            "candidate_value": "NC 9 R'",
            "source_photo_id": "photo-1",
            "note_line_id": "line-1",
            "excel_row_id": "row-1",
            "ocr_confidence": 0.72,
        },
        run_id="run-1",
    )

    assert bundle["run_id"] == "run-1"
    assert bundle["target_id"] == "review-1"
    assert bundle["field_key"] == "mouse_id"
    assert bundle["source_layer"] == "parsed or intermediate result"
    assert bundle["canonical"] is False
    assert bundle["evidence"]["source_photo_id"] == "photo-1"
    assert bundle["evidence"]["note_line_id"] == "line-1"
    assert bundle["evidence"]["excel_row_id"] == "row-1"


def test_auto_recheck_api_returns_non_canonical_summary(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()

    response = TestClient(app).post(
        "/api/data-guardian/auto-recheck",
        json={
            "source_type": "review_package",
            "source_id": "pkg-1",
            "approved_local_recheck": True,
            "allow_external_services": False,
            "items": [
                {
                    "review_id": "review-1",
                    "field_key": "mouse_id",
                    "candidate_value": "NC 9 R'",
                    "source_photo_id": None,
                    "note_line_id": "line-1",
                    "ocr_confidence": 0.94,
                    "current_canonical_value": "NC 9 R'",
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["canonical"] is False
    assert payload["source_layer"] == "review item"
    assert payload["summary"]["photo_check_required_count"] == 1
    assert payload["summary"]["auto_passed_count"] == 0
    assert payload["review_item_ids"]


def test_auto_recheck_api_persists_non_canonical_evidence_bundle(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()

    response = TestClient(app).post(
        "/api/data-guardian/auto-recheck",
        json={
            "source_type": "review_package",
            "source_id": "pkg-1",
            "approved_local_recheck": True,
            "allow_external_services": False,
            "items": [
                {
                    "review_id": "review-1",
                    "field_key": "mouse_id",
                    "candidate_value": "NC 9 R'",
                    "source_photo_id": "photo-1",
                    "note_line_id": "line-1",
                    "excel_row_id": "row-1",
                    "ocr_confidence": 0.94,
                    "current_canonical_value": "NC 9 R''",
                }
            ],
        },
    )

    assert response.status_code == 200
    review_id = response.json()["review_item_ids"][0]

    with connection() as conn:
        review_item = conn.execute(
            "SELECT evidence_bundle_id FROM data_guardian_review_items WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        bundle = conn.execute(
            """
            SELECT canonical, source_layer, evidence_json
            FROM evidence_bundles
            WHERE evidence_bundle_id = ?
            """,
            (review_item["evidence_bundle_id"],),
        ).fetchone()

    assert bundle["canonical"] == 0
    assert bundle["source_layer"] == "parsed or intermediate result"
    evidence = json.loads(bundle["evidence_json"])
    assert evidence["source_photo_id"] == "photo-1"
    assert evidence["note_line_id"] == "line-1"
    assert evidence["excel_row_id"] == "row-1"


def test_auto_recheck_api_does_not_update_canonical_mouse_master(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO mouse_master (
                mouse_id,
                display_id,
                raw_strain_text,
                genotype_status,
                status,
                last_verified_at
            )
            VALUES ('mouse-1', 'NC 9 R''', 'Npc flfl', 'unknown', 'active', 'before-mus')
            """
        )

    response = TestClient(app).post(
        "/api/data-guardian/auto-recheck",
        json={
            "source_type": "review_package",
            "source_id": "pkg-1",
            "approved_local_recheck": True,
            "allow_external_services": False,
            "items": [
                {
                    "review_id": "review-1",
                    "field_key": "mouse_id",
                    "candidate_value": "NC 9 R'",
                    "source_photo_id": "photo-1",
                    "note_line_id": "line-1",
                    "ocr_confidence": 0.94,
                    "current_canonical_value": "NC 9 R''",
                }
            ],
        },
    )

    assert response.status_code == 200
    with connection() as conn:
        row = conn.execute(
            "SELECT display_id, last_verified_at FROM mouse_master WHERE mouse_id = 'mouse-1'"
        ).fetchone()
        proposed_count = conn.execute("SELECT COUNT(*) AS count FROM proposed_changesets").fetchone()["count"]

    assert row["display_id"] == "NC 9 R'"
    assert row["last_verified_at"] == "before-mus"
    assert proposed_count == 0


def test_review_queue_hides_auto_passed_by_default(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()
    run_id = create_auto_recheck_run(
        source_type="review_package",
        source_id="pkg-1",
        input_manifest={"items": 2},
        allow_external_services=False,
    )
    create_data_guardian_review_item(
        run_id=run_id,
        target_type="field",
        target_id="field-auto",
        field_key="mouse_id",
        candidate_value_raw="NC 9 R'",
        candidate_value_normalized="NC 9 R'",
        current_canonical_value="NC 9 R'",
        previous_confirmed_value="NC 9 R'",
        risk_status="auto_passed",
        risk_reasons=[],
        recommended_action="none",
        evidence_bundle_id="evidence-auto",
    )
    expected_id = create_data_guardian_review_item(
        run_id=run_id,
        target_type="field",
        target_id="field-photo",
        field_key="mouse_id",
        candidate_value_raw="NC 10 L'",
        candidate_value_normalized="NC 10 L'",
        current_canonical_value="NC 10 L'",
        previous_confirmed_value="NC 10 L'",
        risk_status="photo_check_required",
        risk_reasons=["missing_source_photo"],
        recommended_action="compare_photo",
        evidence_bundle_id="evidence-photo",
    )

    response = TestClient(app).get("/api/data-guardian/review-queue")

    assert response.status_code == 200
    ids = [item["review_id"] for item in response.json()["items"]]
    assert ids == [expected_id]


def test_proposed_changeset_is_not_canonical_until_approved(tmp_path):
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    init_db()
    run_id = create_auto_recheck_run(
        source_type="review_package",
        source_id="pkg-1",
        input_manifest={"items": 1},
        allow_external_services=False,
    )

    changeset_id = create_proposed_changeset(
        run_id=run_id,
        target_type="mouse",
        target_id="mouse-1",
        field_key="mouse_id",
        before_value="NC 9 R''",
        proposed_after_value="NC 9 R'",
        evidence_bundle_id="evidence-1",
        confidence=0.91,
    )

    with connection() as conn:
        row = conn.execute(
            "SELECT canonical, approval_status, before_value, proposed_after_value FROM proposed_changesets WHERE changeset_id = ?",
            (changeset_id,),
        ).fetchone()

    assert row["canonical"] == 0
    assert row["approval_status"] == "pending"
    assert row["before_value"] == "NC 9 R''"
    assert row["proposed_after_value"] == "NC 9 R'"
