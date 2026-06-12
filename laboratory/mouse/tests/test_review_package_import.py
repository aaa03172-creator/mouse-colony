from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.review_package_import import ReviewPackageError, parse_review_package


def review_package_payload() -> dict:
    return {
        "schema_version": "mouse-colony-review-package.v1",
        "canonical": False,
        "owner": "Jang Sungjin",
        "import_rules": {
            "excluded_terms": ["sgpl1"],
            "forbidden_text": ["26.09.58"],
        },
        "cards": [
            {
                "card_id": "Npc_flfl_21_23",
                "sheet_name": "Npc flfl",
                "strain": "Npc flfl",
                "sex_total_raw": "male 1 / female 1",
                "id_raw": "male NP10 Rdeg; female NP13 Rprime Lprime",
                "genotype_raw": "fl/fl",
                "dob_raw": "24.09.06-20",
                "mating_date_raw": "",
                "pubs_or_note_raw": "25.09.08 - 5P",
                "source_photo": "KakaoTalk_20260604_171243095_23.jpg",
                "review_status": "user_confirmed",
            }
        ],
        "events": [
            {
                "card_id": "Npc_flfl_21_23",
                "event_type": "litter",
                "event_date_raw": "25.09.08",
                "event_value_raw": "25.09.08 - 5P",
                "source_photo": "KakaoTalk_20260604_171243095_23.jpg",
            }
        ],
    }


def test_parse_review_package_keeps_raw_and_normalized_separate() -> None:
    parsed = parse_review_package(review_package_payload())

    assert parsed["canonical"] is False
    assert parsed["cards"][0]["raw"]["id"] == "male NP10 Rdeg; female NP13 Rprime Lprime"
    assert parsed["cards"][0]["raw"]["pubs_or_note"] == "25.09.08 - 5P"
    assert parsed["cards"][0]["normalized"]["strain_key"] == "Npc flfl"
    assert parsed["events"][0]["raw"]["event_value"] == "25.09.08 - 5P"
    assert parsed["cards"][0]["source"]["photo"] == "KakaoTalk_20260604_171243095_23.jpg"


def test_parse_review_package_rejects_canonical_true() -> None:
    payload = review_package_payload()
    payload["canonical"] = True

    try:
        parse_review_package(payload)
    except ReviewPackageError as exc:
        assert "canonical must be false" in str(exc)
    else:
        raise AssertionError("expected ReviewPackageError")


def test_parse_review_package_rejects_excluded_sgpl1_and_impossible_date() -> None:
    sgpl1_payload = review_package_payload()
    sgpl1_payload["cards"][0]["strain"] = "Sgpl1"
    try:
        parse_review_package(sgpl1_payload)
    except ReviewPackageError as exc:
        assert "excluded strain present" in str(exc)
    else:
        raise AssertionError("expected ReviewPackageError")

    bad_date_payload = review_package_payload()
    bad_date_payload["cards"][0]["pubs_or_note_raw"] = "26.09.58 - 5P"
    try:
        parse_review_package(bad_date_payload)
    except ReviewPackageError as exc:
        assert "forbidden text present" in str(exc)
    else:
        raise AssertionError("expected ReviewPackageError")


def test_review_package_import_creates_source_parse_review_and_candidates(tmp_path) -> None:
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    try:
        db.init_db()
        client = TestClient(app)
        response = client.post("/api/review-packages/import", json=review_package_payload())

        assert response.status_code == 200
        body = response.json()
        assert body["source_layer"] == "parsed or intermediate result"
        assert body["created_review_items"] == 1
        assert body["created_candidates"] == 1
        assert body["export_ready"] is False

        with db.connection() as conn:
            source = conn.execute(
                "SELECT source_type, raw_payload FROM source_record WHERE source_record_id = ?",
                (body["source_record_id"],),
            ).fetchone()
            parse = conn.execute(
                "SELECT raw_payload FROM parse_result WHERE parse_id = ?",
                (body["parse_id"],),
            ).fetchone()
            review_count = conn.execute(
                "SELECT COUNT(*) AS count FROM review_queue WHERE parse_id = ?",
                (body["parse_id"],),
            ).fetchone()["count"]
            candidate = conn.execute(
                "SELECT proposed_strain, candidate_payload FROM canonical_candidate WHERE parse_id = ?",
                (body["parse_id"],),
            ).fetchone()

        assert source["source_type"] == "mouse_card_review_package"
        assert json.loads(source["raw_payload"])["canonical"] is False
        parse_payload = json.loads(parse["raw_payload"])
        assert parse_payload["payload_kind"] == "review_package_import"
        assert parse_payload["source_layer"] == "parsed or intermediate result"
        assert review_count == 1
        assert candidate["proposed_strain"] == "Npc flfl"
        assert json.loads(candidate["candidate_payload"])["raw"]["id"] == "male NP10 Rdeg; female NP13 Rprime Lprime"
    finally:
        db.DB_PATH = old_db_path
