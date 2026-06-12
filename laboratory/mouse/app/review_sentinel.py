from __future__ import annotations

import json

from .db import connection
from .storage import new_id


def classify_recheck_risk(evidence: dict) -> dict:
    reasons = []
    if evidence.get("requires_external_service"):
        reasons.append("external_service_required")
        return {"risk_status": "blocked", "risk_reasons": reasons, "canonical": False}
    if not evidence.get("source_photo_id"):
        reasons.append("missing_source_photo")
        return {
            "risk_status": "photo_check_required",
            "risk_reasons": reasons,
            "canonical": False,
        }
    ocr_confidence = evidence.get("ocr_confidence")
    if ocr_confidence is not None and ocr_confidence < 0.5:
        reasons.append("low_ocr_confidence")
        return {"risk_status": "quick_review", "risk_reasons": reasons, "canonical": False}
    candidate = evidence.get("candidate_value")
    canonical = evidence.get("current_canonical_value")
    if candidate is not None and canonical is not None and candidate != canonical:
        reasons.append("canonical_conflict")
        return {"risk_status": "conflict_review", "risk_reasons": reasons, "canonical": False}
    return {"risk_status": "auto_passed", "risk_reasons": reasons, "canonical": False}


def create_auto_recheck_run(
    *,
    source_type: str,
    source_id: str,
    input_manifest: dict,
    allow_external_services: bool,
) -> str:
    run_id = new_id("auto_recheck")
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO auto_recheck_runs (
                run_id,
                source_type,
                source_id,
                input_manifest_json,
                allow_external_services,
                canonical
            )
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                run_id,
                source_type,
                source_id,
                json.dumps(input_manifest, ensure_ascii=False),
                1 if allow_external_services else 0,
            ),
        )
    return run_id


def create_data_guardian_review_item(
    *,
    run_id: str,
    target_type: str,
    target_id: str,
    field_key: str,
    candidate_value_raw: str,
    candidate_value_normalized: str,
    current_canonical_value: str,
    previous_confirmed_value: str,
    risk_status: str,
    risk_reasons: list[str],
    recommended_action: str,
    evidence_bundle_id: str,
) -> str:
    review_id = new_id("dg_review")
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO data_guardian_review_items (
                review_id,
                run_id,
                target_type,
                target_id,
                field_key,
                candidate_value_raw,
                candidate_value_normalized,
                current_canonical_value,
                previous_confirmed_value,
                risk_status,
                risk_reasons_json,
                recommended_action,
                evidence_bundle_id,
                canonical
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                review_id,
                run_id,
                target_type,
                target_id,
                field_key,
                candidate_value_raw,
                candidate_value_normalized,
                current_canonical_value,
                previous_confirmed_value,
                risk_status,
                json.dumps(risk_reasons, ensure_ascii=False),
                recommended_action,
                evidence_bundle_id,
            ),
        )
    return review_id


def create_evidence_bundle(
    *,
    evidence_bundle_id: str,
    run_id: str,
    target_type: str,
    target_id: str,
    field_key: str,
    evidence: dict,
    source_layer: str,
) -> str:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO evidence_bundles (
                evidence_bundle_id,
                run_id,
                target_type,
                target_id,
                field_key,
                evidence_json,
                source_layer,
                canonical
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                evidence_bundle_id,
                run_id,
                target_type,
                target_id,
                field_key,
                json.dumps(evidence, ensure_ascii=False),
                source_layer,
            ),
        )
    return evidence_bundle_id


def build_evidence_bundle_from_review_package_item(item: dict, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "target_type": "review_item",
        "target_id": str(item.get("review_id") or ""),
        "field_key": str(item.get("field_key") or ""),
        "source_layer": "parsed or intermediate result",
        "canonical": False,
        "evidence": {
            "candidate_value": item.get("candidate_value"),
            "source_photo_id": item.get("source_photo_id"),
            "note_line_id": item.get("note_line_id"),
            "excel_row_id": item.get("excel_row_id"),
            "ocr_confidence": item.get("ocr_confidence"),
        },
    }


def create_proposed_changeset(
    *,
    run_id: str,
    target_type: str,
    target_id: str,
    field_key: str,
    before_value: str,
    proposed_after_value: str,
    evidence_bundle_id: str,
    confidence: float,
) -> str:
    changeset_id = new_id("dg_changeset")
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO proposed_changesets (
                changeset_id,
                run_id,
                target_type,
                target_id,
                field_key,
                before_value,
                proposed_after_value,
                evidence_bundle_id,
                confidence,
                approval_status,
                canonical
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)
            """,
            (
                changeset_id,
                run_id,
                target_type,
                target_id,
                field_key,
                before_value,
                proposed_after_value,
                evidence_bundle_id,
                confidence,
            ),
        )
    return changeset_id
