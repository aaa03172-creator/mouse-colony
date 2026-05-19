from __future__ import annotations

import json
import re
import hashlib
import csv
import io
import html
import zipfile
import mimetypes
import base64
import os
import math
import sqlite3
import threading
from datetime import date, timedelta
from urllib.parse import quote
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
import httpx

from . import db as app_db
from .db import ROOT, connection, init_db
from .breeding_rules import DEFAULT_BREEDING_RULE_SET
from .labeling_rules import interpret_crossed_out_status, match_samples_to_mice
from .hybrid_note_line_evaluator import build_rule_snapshot, evaluate_note_line_candidate
from .matching import MatchCandidate, match_candidate
from .storage import new_id, save_legacy_workbook, save_upload, utc_now
from scripts.parse_legacy_workbooks import parse_workbook


STATIC_DIR = ROOT / "static"
FIXTURE_PATH = ROOT / "fixtures" / "sample_parse_results.json"
ROI_PRESET_PATH = ROOT / "config" / "roi_presets.json"
ARTIFACT_ROOT = Path(os.environ.get("MOUSEDB_ARTIFACT_ROOT", ROOT / "mousedb_artifacts")).expanduser().resolve()
RUNTIME_OPENAI_API_KEY = ""
ROI_CACHE_LOCK = threading.RLock()
ANIMAL_SHEET_VALIDATION_ISSUES = {
    "animal sheet count conflict",
    "animal sheet litter/date conflict",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Mouse Colony LIMS Local MVP", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AssignedStrainCreate(BaseModel):
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    source_type: str = "manual"
    source_reference: str = ""
    notes: str = ""


class EvidenceComparisonReviewCreate(BaseModel):
    manual_parse_id: str = ""
    photo_id: str = ""
    upload_batch_id: str = ""


class DistributionImportPayload(BaseModel):
    layer: str = ""
    description: str = ""
    source_file_name: str = ""
    source_file_path: str = ""
    received_date: str = ""
    sheet_name: str = ""
    rows: list[dict[str, Any]]


class StrainRegistryCreate(BaseModel):
    strain_name: str = Field(min_length=1)
    common_name: str = ""
    official_name: str = ""
    gene: str = ""
    allele: str = ""
    background: str = ""
    source: str = ""
    status: str = "active"
    breeding_note: str = ""
    genotyping_note: str = ""
    owner: str = ""
    source_record_id: str | None = None


class GeneRegistryUpdate(BaseModel):
    full_name: str = ""
    description: str = ""
    external_reference: str = ""


class AlleleRegistryUpdate(BaseModel):
    description: str = ""
    allele_type: str = ""
    inheritance: str = ""
    zygosity_options: str = ""
    genotyping_protocol: str = ""


class CorrectionCreate(BaseModel):
    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    before_value: str = ""
    after_value: str = ""
    reason: str = ""
    source_record_id: str | None = None
    review_id: str | None = None
    scoring_audit_status: str = ""
    scoring_audit_note: str = ""


class MouseEventCreate(BaseModel):
    mouse_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    event_date: str = ""
    related_entity_type: str = ""
    related_entity_id: str = ""
    source_record_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "local_user"


class ReviewResolutionCreate(BaseModel):
    resolution_note: str = Field(min_length=1)
    resolved_value: str = ""
    legacy_decision: str = "resolve"
    canonical_entity_type: str = ""
    canonical_entity_id: str = ""
    reviewed_strain_name: str = ""
    reviewed_gene_symbol: str = ""
    reviewed_allele_name: str = ""
    correction_entity_type: str = ""
    correction_entity_id: str = ""
    correction_field_name: str = ""
    correction_before_value: str = ""
    correction_after_value: str = ""
    correction_source_record_id: str | None = None
    note_item_id: str = ""
    note_label_decision: str = ""
    note_label_mouse_id: str = ""
    note_label_count: int | None = Field(default=None, ge=0)
    note_label_interpreted_status: str = ""
    ear_label_code: str = ""
    audit_taxonomy_status: str = ""
    audit_taxonomy_note: str = ""
    note_line_scoring_scope: str = ""
    field_review_outcome: dict[str, Any] = Field(default_factory=dict)


class PhotoManualTranscriptionCreate(BaseModel):
    card_type: str = "Separated"
    raw_strain: str = ""
    matched_strain: str = ""
    sex_raw: str = ""
    sex_normalized: str = ""
    id_raw: str = ""
    dob_raw: str = ""
    dob_normalized: str = ""
    mating_date_raw: str = ""
    mating_date_normalized: str = ""
    lmo_raw: str = ""
    mouse_count: str = ""
    confidence: float = Field(default=75, ge=0, le=100)
    notes: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_note: str = ""
    extraction_method: str = "manual_entry"
    raw_visible_text_lines: list[str] = Field(default_factory=list)
    symbol_confusions: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    plausibility_findings: list[dict[str, Any]] = Field(default_factory=list)
    extraction_image_mode: str = ""
    roi_template_type: str = ""
    extraction_regions: list[dict[str, Any]] = Field(default_factory=list)
    external_approval: dict[str, Any] = Field(default_factory=dict)
    payload_minimization: str = ""


class PhotoAiDraftCreate(BaseModel):
    approved_external_inference: bool = False
    detail: str = "high"


class AiDraftSettingsUpdate(BaseModel):
    api_key: str = ""


class UploadBatchCreate(BaseModel):
    batch_label: str = ""
    expected_photo_count: int = Field(default=0, ge=0)
    note: str = ""


class GenotypingUpdate(BaseModel):
    mouse_id: str = Field(min_length=1)
    sample_id: str = ""
    sample_date: str = ""
    raw_result: str = ""
    normalized_result: str = ""
    result_date: str = ""
    target_name: str = ""
    notes: str = ""
    source_record_id: str | None = None
    source_photo_id: str | None = None
    photo_evidence_id: str | None = None


class GenotypingRequestCreate(BaseModel):
    mouse_id: str = Field(min_length=1)
    sample_id: str = ""
    sample_date: str = ""
    target_name: str = ""
    labeling_rule_set_id: str = ""
    note: str = ""


class StrainTargetGenotypeCreate(BaseModel):
    strain_text: str = Field(min_length=1)
    target_genotype: str = Field(min_length=1)
    purpose: str = "strain_maintenance"


class CageCreate(BaseModel):
    cage_label: str = Field(min_length=1)
    location: str = ""
    rack: str = ""
    shelf: str = ""
    cage_type: str = "holding"
    status: str = "active"
    note: str = ""


class MatingCreate(BaseModel):
    mating_label: str = Field(min_length=1)
    male_mouse_id: str = ""
    female_mouse_id: str = ""
    strain_goal: str = ""
    expected_genotype: str = ""
    start_date: str = ""
    status: str = "active"
    purpose: str = ""
    note: str = ""
    source_photo_id: str = ""
    source_note_item_id: str = ""
    photo_evidence_id: str = ""


class LitterCreate(BaseModel):
    litter_label: str = Field(min_length=1)
    mating_id: str = Field(min_length=1)
    birth_date: str = ""
    number_born: int | None = None
    number_alive: int | None = None
    number_weaned: int | None = None
    weaning_date: str = ""
    status: str = "born"
    note: str = ""
    source_photo_id: str = ""
    source_note_item_id: str = ""
    photo_evidence_id: str = ""


class LitterOffspringCreate(BaseModel):
    count: int = Field(gt=0, le=100)
    display_prefix: str = ""
    start_number: int = Field(default=1, ge=1)
    sex: str = "unknown"
    cage_id: str = ""
    status: str = "weaning_pending"
    note: str = ""
    source_photo_id: str = ""
    source_note_item_id: str = ""
    photo_evidence_id: str = ""


class LitterWeanCreate(BaseModel):
    weaning_date: str = ""
    number_weaned: int | None = Field(default=None, ge=0)
    note: str = ""
    source_photo_id: str = ""
    source_note_item_id: str = ""
    photo_evidence_id: str = ""


class MouseCageMove(BaseModel):
    cage_id: str = Field(min_length=1)
    note: str = ""
    moved_at: str = ""
    source_photo_id: str = ""
    source_note_item_id: str = ""
    photo_evidence_id: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def current_openai_api_key() -> str:
    return RUNTIME_OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")


def review_assigned_role(issue: str = "", review_reason: str = "", source_name: str = "") -> str:
    haystack = " ".join([issue, review_reason, source_name]).lower()
    if any(token in haystack for token in ["strain", "allele", "genotype protocol", "assigned strain"]):
        return "Strain Curator"
    if any(token in haystack for token in ["experiment", "sample", "genotyping worklist"]):
        return "Experiment Planner"
    if any(token in haystack for token in ["export", "excel", "workbook", "legacy", "comparison"]):
        return "Data / Export Manager"
    return "Colony Reviewer"


def review_priority(severity: str = "", issue: str = "", review_reason: str = "") -> str:
    severity_key = severity.strip().lower()
    haystack = " ".join([issue, review_reason]).lower()
    if severity_key == "high" or any(
        token in haystack
        for token in [
            "duplicate active",
            "dead",
            "sacrificed",
            "genotype conflict",
            "canonical",
            "no source evidence",
        ]
    ):
        return "high"
    if severity_key == "low":
        return "low"
    return "medium"


def review_uncertain_fields(parse_payload: dict[str, Any]) -> list[str]:
    raw_uncertain_fields: list[Any] = []
    for key in ("uncertainFields", "uncertain_fields"):
        if isinstance(parse_payload.get(key), list):
            raw_uncertain_fields.extend(parse_payload[key])
    uncertain_fields = []
    seen_uncertain_fields: set[str] = set()
    for field in raw_uncertain_fields:
        field_name = normalize_uncertain_field_name(field)
        if not field_name or field_name in seen_uncertain_fields:
            continue
        seen_uncertain_fields.add(field_name)
        uncertain_fields.append(field_name)
    return uncertain_fields


def review_attention_level(item: dict[str, Any], parse_payload: dict[str, Any] | None = None) -> dict[str, str]:
    payload = parse_payload or {}
    status = str(item.get("status") or "").strip().lower()
    issue = str(item.get("issue") or "").strip()
    issue_key = issue.lower()
    source_name = str(item.get("source_name") or "").strip()
    source_key = source_name.lower()
    priority = str(item.get("priority") or "").strip().lower()
    severity = str(item.get("severity") or "").strip().lower()
    uncertain_fields = review_uncertain_fields(payload)
    plausibility_findings = parse_payload_plausibility_findings(payload)
    high_plausibility_findings = [finding for finding in plausibility_findings if finding["severity"] == "high"]
    confidence_source = payload["confidence"] if "confidence" in payload else item.get("confidence", 0)
    confidence = bounded_float(confidence_source)
    raw_strain = str(payload.get("rawStrain") or "").strip()
    sex_raw = str(payload.get("sexRaw") or "").strip()
    photo_id = str(item.get("photo_id") or "")

    if status and status != "open":
        return {
            "attention_level": "trace_only",
            "attention_reason": "Resolved or non-open review retained for traceability.",
        }
    if source_key.startswith("fixtures/"):
        return {
            "attention_level": "hidden_default",
            "attention_reason": "Fixture/sample review is hidden from the default user work queue.",
        }
    if priority == "high" or severity == "high" or "duplicate active" in issue_key:
        return {
            "attention_level": "must_review",
            "attention_reason": "High-priority biological or canonical-state risk.",
        }
    if issue_key == "unlabeled numeric note needs review":
        return {
            "attention_level": "quick_check",
            "attention_reason": "Numeric note labels should be reviewed in a grouped photo-level pass.",
        }
    if issue_key == "ear label needs review":
        return {
            "attention_level": "quick_check",
            "attention_reason": "Ear label normalization needs a quick source-photo check.",
        }
    if issue_key == "ai-extracted photo transcription needs review" and photo_id:
        if confidence <= 55:
            return {
                "attention_level": "must_review",
                "attention_reason": "Low-confidence OCR draft needs focused review.",
            }
        if high_plausibility_findings:
            return {
                "attention_level": "must_review",
                "attention_reason": "Plausibility check found an impossible or cross-field OCR value.",
            }
        if not raw_strain or not sex_raw:
            return {
                "attention_level": "must_review",
                "attention_reason": "Core cage-card field is missing.",
            }
        if "matched_strain" in uncertain_fields and confidence < 60:
            return {
                "attention_level": "must_review",
                "attention_reason": "Assigned strain match is uncertain on a lower-confidence card.",
            }
        if "mouse_count" in uncertain_fields and ("dob_raw" in uncertain_fields or confidence < 60):
            return {
                "attention_level": "must_review",
                "attention_reason": "Count and date evidence need a focused source-photo check.",
            }
        if (
            confidence < 65
            or any(field in uncertain_fields for field in ["raw_strain", "matched_strain", "sex_raw", "mouse_count", "notes"])
        ):
            return {
                "attention_level": "quick_check",
                "attention_reason": "Main evidence is present, but one or more fields need a quick check.",
            }
        return {
            "attention_level": "trace_only",
            "attention_reason": "Low-risk OCR uncertainty retained as trace evidence.",
        }
    return {
        "attention_level": "quick_check",
        "attention_reason": "Open review retained outside the focused default queue.",
    }


def review_check_targets(item: dict[str, Any], parse_payload: dict[str, Any] | None = None) -> list[str]:
    payload = parse_payload or {}
    issue_key = str(item.get("issue") or "").strip().lower()
    confidence_source = payload["confidence"] if "confidence" in payload else item.get("confidence", 0)
    confidence = bounded_float(confidence_source)
    uncertain_fields = set(review_uncertain_fields(payload))
    plausibility_findings = parse_payload_plausibility_findings(payload)
    targets: list[str] = []

    def add(label: str) -> None:
        if label and label not in targets:
            targets.append(label)

    if issue_key == "unlabeled numeric note needs review":
        add("Numeric note label")
        add("Note line anchor")
    elif issue_key == "ear label needs review":
        add("Ear label")
        add("Source photo")
    elif issue_key == "ai-extracted photo transcription needs review":
        if confidence <= 55:
            add("Low OCR confidence")
        if plausibility_findings:
            add("Plausibility warning")
        if not str(payload.get("rawStrain") or "").strip():
            add("Strain field")
        if not str(payload.get("sexRaw") or "").strip():
            add("Sex/count field")
        if "matched_strain" in uncertain_fields:
            add("Assigned strain match")
        if "raw_strain" in uncertain_fields:
            add("Raw strain text")
        if "sex_raw" in uncertain_fields:
            add("Sex/count field")
        if "mouse_count" in uncertain_fields:
            add("Mouse count")
        if "dob_raw" in uncertain_fields or "dob_normalized" in uncertain_fields:
            add("DOB")
        if "notes" in uncertain_fields:
            add("Notes")
        if not targets:
            add("Source photo")
    elif issue_key == "legacy strain registry candidate requires review":
        add("Strain registry")
        add("Raw strain/genotype")
        add("Gene/allele link")
        add("Workbook row evidence")
    elif "duplicate active" in issue_key:
        add("Duplicate mouse ID")
        add("Source evidence")
    else:
        add("Source evidence")
    return targets[:5]


def focus_review_workload_summary(review_items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"must_review": 0, "quick_check": 0}
    for item in review_items:
        if item.get("status") != "open":
            continue
        level = str(item.get("attention_level") or "")
        if level in counts:
            counts[level] += 1
    return counts


def focus_review_issue_label(item: dict[str, Any]) -> str:
    issue = str(item.get("issue") or "").lower()
    if "duplicate active" in issue:
        return "Duplicate active"
    if item.get("attention_level") == "must_review":
        return "Must review"
    if item.get("attention_level") == "quick_check":
        return "Quick check"
    return "Trace only"


def focus_review_action_hint(item: dict[str, Any]) -> dict[str, Any]:
    issue = str(item.get("issue") or "").lower()
    level = str(item.get("attention_level") or "")
    has_photo = bool(item.get("photo_id") or item.get("image_url"))
    safe_quick_resolve_issues = {
        "low-confidence strain alias",
        "fixture auto-filled by policy",
    }
    safe_quick_resolve = level == "quick_check" and issue in safe_quick_resolve_issues
    if safe_quick_resolve:
        mode = "quick_confirmation"
        primary_label = "Confirm from evidence"
    else:
        mode = "manual_review_required"
        primary_label = "Inspect source evidence"
    return {
        "source_layer": "export or view",
        "mode": mode,
        "primary_label": primary_label,
        "requires_note": True,
        "requires_source_photo": has_photo,
        "safe_quick_resolve": safe_quick_resolve,
    }


def focus_review_empty_state() -> dict[str, Any]:
    return {
        "message": "No Focus Review items are currently open.",
        "fabricated_records": False,
    }


def operations_home_empty_state() -> dict[str, Any]:
    return {
        "message": "No operations tasks are currently open.",
        "fabricated_records": False,
    }


def colony_state_empty_state() -> dict[str, Any]:
    return {
        "message": "No accepted active colony records are available yet.",
        "fabricated_records": False,
    }


def colony_schedule_empty_state() -> dict[str, Any]:
    return {
        "message": "No accepted schedule tasks are available yet.",
        "fabricated_records": False,
    }


def mouse_timeline_empty_state() -> dict[str, Any]:
    return {
        "message": "Choose a mouse to view accepted timeline events.",
        "fabricated_records": False,
    }


def mouse_event_evidence_refs(row: Any) -> dict[str, str]:
    details: dict[str, Any] = {}
    try:
        loaded = json.loads(row["details"] or "{}")
        if isinstance(loaded, dict):
            details = loaded
    except (TypeError, json.JSONDecodeError):
        details = {}
    return {
        "source_record_id": str(row["source_record_id"] or ""),
        "source_photo_id": str(details.get("source_photo_id") or ""),
        "source_note_item_id": str(details.get("source_note_item_id") or ""),
        "photo_evidence_id": str(details.get("photo_evidence_id") or ""),
    }


def mouse_pedigree_empty_state() -> dict[str, Any]:
    return {
        "message": "Choose a mouse to view accepted pedigree relationships.",
        "fabricated_records": False,
    }


def evidence_ledger_empty_state() -> dict[str, Any]:
    return {
        "message": "No photo evidence items are available yet.",
        "fabricated_records": False,
    }


def colony_litter_action_hint(
    birth_date: str,
    rule_set: dict[str, Any],
    *,
    observed_date: date | None = None,
) -> dict[str, Any]:
    parsed_birth_date = _safe_iso_date(birth_date)
    thresholds = rule_set.get("thresholds", {})
    due_days = int(thresholds.get("litter_separation_due_after_days", 30))
    overdue_days = int(thresholds.get("litter_separation_overdue_after_days", 45))
    high_overdue_days = int(thresholds.get("litter_separation_high_overdue_after_days", 60))
    if parsed_birth_date is None:
        return {
            "mode": "date_review_needed",
            "label": "Review litter date",
            "priority": "medium",
            "age_days": None,
            "threshold_days": due_days,
            "automation": "manual_review_only",
            "suggested_actions": ["review_source_date", "open_focus_review"],
        }
    age_days = ((observed_date or date.today()) - parsed_birth_date).days
    if age_days >= high_overdue_days:
        return {
            "mode": "urgent_review",
            "label": "Separation review urgently overdue",
            "priority": "high",
            "age_days": age_days,
            "threshold_days": high_overdue_days,
            "automation": "manual_review_only",
            "suggested_actions": ["open_focus_review", "review_litter", "record_weaning_if_done"],
        }
    if age_days >= overdue_days:
        return {
            "mode": "overdue_review",
            "label": "Separation review overdue",
            "priority": "medium",
            "age_days": age_days,
            "threshold_days": overdue_days,
            "automation": "manual_review_only",
            "suggested_actions": ["open_focus_review", "review_litter"],
        }
    if age_days >= due_days:
        return {
            "mode": "review_due",
            "label": "Separation review due",
            "priority": "medium",
            "age_days": age_days,
            "threshold_days": due_days,
            "automation": "manual_review_only",
            "suggested_actions": ["review_litter", "record_weaning_if_done"],
        }
    return {
        "mode": "upcoming",
        "label": "Separation review upcoming",
        "priority": "low",
        "age_days": age_days,
        "threshold_days": due_days,
        "automation": "manual_review_only",
        "suggested_actions": ["watch_litter", "review_at_threshold"],
    }


def _safe_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def ai_draft_status() -> dict[str, Any]:
    key_source = "session" if RUNTIME_OPENAI_API_KEY else ("environment" if os.environ.get("OPENAI_API_KEY") else "missing")
    return {
        "available": bool(current_openai_api_key()),
        "key_source": key_source,
        "model": os.environ.get("OPENAI_PARSE_ASSIST_MODEL", "gpt-5.2"),
        "approval_required": True,
        "payload_minimization": "selected photo is locally reduced to card/field ROI crops plus active assigned strain names only",
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "storage": "local-only",
        "ai_draft": ai_draft_status(),
    }


def create_upload_batch_record(
    conn: Any,
    *,
    batch_label: str = "",
    expected_photo_count: int = 0,
    note: str = "",
) -> dict[str, Any]:
    now = utc_now()
    upload_batch_id = new_id("batch")
    label = batch_label.strip() or f"Cage card upload {now[:10]}"
    conn.execute(
        """
        INSERT INTO upload_batch
            (upload_batch_id, batch_label, expected_photo_count, status,
             source_layer, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            upload_batch_id,
            label,
            max(0, expected_photo_count),
            "open",
            "raw source",
            note.strip(),
            now,
            now,
        ),
    )
    return {
        "upload_batch_id": upload_batch_id,
        "batch_label": label,
        "expected_photo_count": max(0, expected_photo_count),
        "status": "open",
        "source_layer": "raw source",
        "note": note.strip(),
        "created_at": now,
        "updated_at": now,
    }


def operator_upload_batch_status(
    *,
    status: str,
    photo_count: int,
    pending: int,
    open_reviews: int,
    open_comparisons: int,
    comparison_needed: int,
    candidate_count: int,
    applied_candidate_count: int,
) -> dict[str, str]:
    if status == "closed":
        return {
            "label": "Closed",
            "detail": "This upload batch is closed for operator handoff.",
            "next_action": "No release action is needed.",
        }
    if photo_count == 0:
        return {
            "label": "Waiting for photos",
            "detail": "Upload copied cage-card photos before review can start.",
            "next_action": "Upload source photos.",
        }
    if pending:
        return {
            "label": "Needs transcription",
            "detail": f"{pending} photo(s) still need manual or AI transcription evidence.",
            "next_action": "Open each pending photo and save transcription evidence.",
        }
    if open_reviews or open_comparisons:
        return {
            "label": "Needs review resolution",
            "detail": f"{open_reviews} open review item(s) remain; {open_comparisons} are comparison review(s).",
            "next_action": "Resolve open review items before export or batch close.",
        }
    if comparison_needed:
        return {
            "label": "Needs comparison review setup",
            "detail": f"{comparison_needed} transcribed photo(s) still need comparison review creation.",
            "next_action": "Create Batch Comparison Reviews.",
        }
    if candidate_count == 0:
        return {
            "label": "Ready to map candidate",
            "detail": "All transcriptions and comparison reviews are resolved; map source-backed evidence into a canonical candidate.",
            "next_action": "Open evidence mapping and map source-backed evidence.",
        }
    if applied_candidate_count < candidate_count:
        return {
            "label": "Needs candidate apply",
            "detail": f"{applied_candidate_count} of {candidate_count} canonical candidate(s) have been applied.",
            "next_action": "Preview and apply remaining canonical candidates.",
        }
    return {
        "label": "Ready to close",
        "detail": "All photos are transcribed, reviewed, mapped, and applied.",
        "next_action": "Close Batch.",
    }


def upload_batch_payload(row: Any) -> dict[str, Any]:
    photo_count = int(row["photo_count"] or 0)
    pending = int(row["pending_transcription_count"] or 0)
    open_reviews = int(row["open_review_count"] or 0)
    open_comparisons = int(row["open_comparison_review_count"] or 0)
    comparison_needed = int(row["comparison_needed_count"] or 0)
    candidate_count = int(row["canonical_candidate_count"] or 0)
    applied_candidate_count = int(row["applied_candidate_count"] or 0)
    if row["status"] == "closed":
        derived_status = "mapped_or_closed"
    elif photo_count == 0 or pending:
        derived_status = "upload_or_transcription_pending"
    elif open_reviews or open_comparisons or comparison_needed:
        derived_status = "review_pending"
    elif candidate_count == 0 or applied_candidate_count < candidate_count:
        derived_status = "ready_for_mapping"
    else:
        derived_status = "mapped_or_closed"
    payload = dict(row)
    payload["photo_count"] = photo_count
    payload["transcribed_photo_count"] = int(payload["transcribed_photo_count"] or 0)
    payload["pending_transcription_count"] = pending
    payload["total_review_count"] = int(payload["total_review_count"] or 0)
    payload["open_review_count"] = open_reviews
    payload["resolved_review_count"] = max(0, payload["total_review_count"] - open_reviews)
    payload["comparison_review_count"] = int(payload["comparison_review_count"] or 0)
    payload["open_comparison_review_count"] = open_comparisons
    payload["comparison_needed_count"] = comparison_needed
    payload["canonical_candidate_count"] = candidate_count
    payload["applied_candidate_count"] = applied_candidate_count
    payload["derived_status"] = derived_status
    operator_status = operator_upload_batch_status(
        status=str(row["status"] or ""),
        photo_count=photo_count,
        pending=pending,
        open_reviews=open_reviews,
        open_comparisons=open_comparisons,
        comparison_needed=comparison_needed,
        candidate_count=candidate_count,
        applied_candidate_count=applied_candidate_count,
    )
    payload["operator_status_label"] = operator_status["label"]
    payload["operator_status_detail"] = operator_status["detail"]
    payload["operator_next_action"] = operator_status["next_action"]
    payload["boundary"] = "raw source"
    return payload


def upload_batch_summary_row(conn: Any, upload_batch_id: str) -> Any:
    row = conn.execute(
        """
        SELECT batch.upload_batch_id, batch.batch_label,
               batch.expected_photo_count, batch.status,
               batch.source_layer, batch.note, batch.created_at, batch.updated_at,
               COUNT(photo.photo_id) AS photo_count,
               SUM(CASE WHEN transcribed.photo_id IS NOT NULL THEN 1 ELSE 0 END) AS transcribed_photo_count,
               SUM(CASE WHEN photo.photo_id IS NOT NULL AND transcribed.photo_id IS NULL THEN 1 ELSE 0 END) AS pending_transcription_count,
               COALESCE(SUM(review_counts.total_reviews), 0) AS total_review_count,
               COALESCE(SUM(review_counts.open_reviews), 0) AS open_review_count,
               COALESCE(SUM(comparison_counts.comparison_reviews), 0) AS comparison_review_count,
               COALESCE(SUM(comparison_counts.open_comparison_reviews), 0) AS open_comparison_review_count,
               SUM(CASE WHEN transcribed.photo_id IS NOT NULL AND COALESCE(comparison_counts.comparison_reviews, 0) = 0 THEN 1 ELSE 0 END) AS comparison_needed_count,
               COALESCE(candidate_counts.canonical_candidate_count, 0) AS canonical_candidate_count,
               COALESCE(candidate_counts.applied_candidate_count, 0) AS applied_candidate_count,
               MIN(photo.uploaded_at) AS first_photo_uploaded_at,
               MAX(photo.uploaded_at) AS latest_photo_uploaded_at
        FROM upload_batch batch
        LEFT JOIN photo_log photo
            ON photo.upload_batch_id = batch.upload_batch_id
        LEFT JOIN (
            SELECT DISTINCT photo_id
            FROM parse_result
            WHERE source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
        ) transcribed ON transcribed.photo_id = photo.photo_id
        LEFT JOIN (
            SELECT parse.photo_id,
                   COUNT(review.review_id) AS total_reviews,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_reviews
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            GROUP BY parse.photo_id
        ) review_counts ON review_counts.photo_id = photo.photo_id
        LEFT JOIN (
            SELECT parse.photo_id,
                   COUNT(review.review_id) AS comparison_reviews,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_comparison_reviews
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            WHERE review.issue LIKE 'Photo transcription%'
            GROUP BY parse.photo_id
        ) comparison_counts ON comparison_counts.photo_id = photo.photo_id
        LEFT JOIN (
            SELECT photo.upload_batch_id,
                   COUNT(candidate.candidate_id) AS canonical_candidate_count,
                   SUM(CASE WHEN candidate.status = 'applied' THEN 1 ELSE 0 END) AS applied_candidate_count
            FROM photo_log photo
            JOIN parse_result parse ON parse.photo_id = photo.photo_id
            JOIN review_queue review ON review.parse_id = parse.parse_id
            JOIN canonical_candidate candidate ON candidate.review_id = review.review_id
            GROUP BY photo.upload_batch_id
        ) candidate_counts ON candidate_counts.upload_batch_id = batch.upload_batch_id
        WHERE batch.upload_batch_id = ?
        GROUP BY batch.upload_batch_id
        """,
        (upload_batch_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Upload batch not found.")
    return row


def upload_batch_release_preview_payload(conn: Any, upload_batch_id: str) -> dict[str, Any]:
    batch = upload_batch_payload(upload_batch_summary_row(conn, upload_batch_id))
    worklist_rows = upload_batch_release_worklist(conn, upload_batch_id)
    blockers = []
    warnings = []

    def add_check(key: str, label: str, passed: bool, detail: str, severity: str = "blocker") -> dict[str, Any]:
        item = {"key": key, "label": label, "passed": passed, "detail": detail, "severity": severity}
        if not passed:
            if severity == "warning":
                warnings.append(item)
            else:
                blockers.append(item)
        return item

    expected = int(batch["expected_photo_count"] or 0)
    photo_count = int(batch["photo_count"] or 0)
    worklist_blocker_count = sum(1 for item in worklist_rows if item["next_action"] != "ready_to_close")
    checklist = [
        add_check(
            "has_photos",
            "Batch has uploaded photos",
            photo_count > 0,
            f"{photo_count} uploaded photo(s).",
        ),
        add_check(
            "expected_photo_count",
            "Uploaded photo count matches expected count",
            expected == 0 or photo_count == expected,
            f"Expected {expected or 'unspecified'}, uploaded {photo_count}.",
        ),
        add_check(
            "transcription_complete",
            "Every photo has manual or AI transcription evidence",
            int(batch["pending_transcription_count"]) == 0,
            f"{batch['pending_transcription_count']} photo(s) still need transcription.",
        ),
        add_check(
            "open_reviews_resolved",
            "Open review blockers are resolved",
            int(batch["open_review_count"]) == 0,
            f"{batch['open_review_count']} open review item(s) remain.",
        ),
        add_check(
            "comparison_reviews_created",
            "Transcribed photos have comparison review coverage",
            int(batch["comparison_needed_count"]) == 0,
            f"{batch['comparison_needed_count']} transcribed photo(s) still need comparison review creation.",
        ),
        add_check(
            "comparison_reviews_resolved",
            "Comparison reviews are resolved",
            int(batch["open_comparison_review_count"]) == 0,
            f"{batch['open_comparison_review_count']} open comparison review item(s) remain.",
        ),
        add_check(
            "canonical_mapping_applied",
            "Canonical candidate mapping has been applied",
            int(batch["canonical_candidate_count"]) > 0 and int(batch["applied_candidate_count"]) >= int(batch["canonical_candidate_count"]),
            f"{batch['applied_candidate_count']} applied candidate(s) of {batch['canonical_candidate_count']} candidate(s).",
        ),
        add_check(
            "photo_worklist_clear",
            "Every photo-level release action is complete",
            worklist_blocker_count == 0,
            f"{worklist_blocker_count} photo-level action(s) remain in the release worklist.",
        ),
    ]
    ready = not blockers
    return {
        "boundary": "export or view",
        "source_layer": "raw source release check",
        "ready": ready,
        "release_status": "ready_to_close" if ready else "blocked",
        "batch": batch,
        "checklist": checklist,
        "worklist": worklist_rows,
        "blockers": blockers,
        "warnings": warnings,
        "note": "Release changes only upload_batch status; it does not write mouse, event, genotype, or other canonical colony state.",
    }


def upload_batch_release_worklist(conn: Any, upload_batch_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT photo.photo_id, photo.original_filename, photo.uploaded_at, photo.status,
               COUNT(DISTINCT transcribed.parse_id) AS transcription_count,
               COALESCE(review_counts.total_reviews, 0) AS total_review_count,
               COALESCE(review_counts.open_reviews, 0) AS open_review_count,
               COALESCE(comparison_counts.comparison_reviews, 0) AS comparison_review_count,
               COALESCE(comparison_counts.open_comparison_reviews, 0) AS open_comparison_review_count,
               COALESCE(candidate_counts.canonical_candidate_count, 0) AS canonical_candidate_count,
               COALESCE(candidate_counts.applied_candidate_count, 0) AS applied_candidate_count
        FROM photo_log photo
        LEFT JOIN parse_result transcribed
            ON transcribed.photo_id = photo.photo_id
           AND transcribed.source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
        LEFT JOIN (
            SELECT parse.photo_id,
                   COUNT(review.review_id) AS total_reviews,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_reviews
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            GROUP BY parse.photo_id
        ) review_counts ON review_counts.photo_id = photo.photo_id
        LEFT JOIN (
            SELECT parse.photo_id,
                   COUNT(review.review_id) AS comparison_reviews,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_comparison_reviews
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            WHERE review.issue LIKE 'Photo transcription%'
            GROUP BY parse.photo_id
        ) comparison_counts ON comparison_counts.photo_id = photo.photo_id
        LEFT JOIN (
            SELECT parse.photo_id,
                   COUNT(candidate.candidate_id) AS canonical_candidate_count,
                   SUM(CASE WHEN candidate.status = 'applied' THEN 1 ELSE 0 END) AS applied_candidate_count
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            JOIN canonical_candidate candidate ON candidate.review_id = review.review_id
            GROUP BY parse.photo_id
        ) candidate_counts ON candidate_counts.photo_id = photo.photo_id
        WHERE photo.upload_batch_id = ?
        GROUP BY photo.photo_id
        ORDER BY photo.uploaded_at, photo.original_filename COLLATE NOCASE
        """,
        (upload_batch_id,),
    ).fetchall()
    worklist = []
    for row in rows:
        payload = dict(row)
        photo_id = payload["photo_id"]
        latest_transcription = conn.execute(
            """
            SELECT parse_id, source_name, parsed_at
            FROM parse_result
            WHERE photo_id = ?
              AND source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
            ORDER BY parsed_at DESC
            LIMIT 1
            """,
            (photo_id,),
        ).fetchone()
        review_rows = conn.execute(
            """
            SELECT review.review_id, review.status, review.issue
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            WHERE parse.photo_id = ?
            ORDER BY CASE WHEN review.status = 'open' THEN 0 ELSE 1 END,
                     review.created_at,
                     review.review_id
            """,
            (photo_id,),
        ).fetchall()
        comparison_review_rows = [
            review for review in review_rows if str(review["issue"] or "").startswith("Photo transcription")
        ]
        candidate_rows = conn.execute(
            """
            SELECT candidate.candidate_id, candidate.status,
                   candidate.proposed_mouse_display_id
            FROM parse_result parse
            JOIN review_queue review ON review.parse_id = parse.parse_id
            JOIN canonical_candidate candidate ON candidate.review_id = review.review_id
            WHERE parse.photo_id = ?
            ORDER BY CASE WHEN candidate.status = 'applied' THEN 1 ELSE 0 END,
                     candidate.created_at,
                     candidate.candidate_id
            """,
            (photo_id,),
        ).fetchall()
        transcription_count = int(payload["transcription_count"] or 0)
        open_review_count = int(payload["open_review_count"] or 0)
        comparison_review_count = int(payload["comparison_review_count"] or 0)
        open_comparison_review_count = int(payload["open_comparison_review_count"] or 0)
        canonical_candidate_count = int(payload["canonical_candidate_count"] or 0)
        applied_candidate_count = int(payload["applied_candidate_count"] or 0)
        open_review = next((review for review in review_rows if review["status"] == "open"), None)
        unapplied_candidate = next((candidate for candidate in candidate_rows if candidate["status"] != "applied"), None)
        if transcription_count == 0:
            next_action = "transcribe_photo"
            blocker = "No manual or AI transcription evidence is attached."
            action_target_type = "photo"
            action_target_id = photo_id
            action_target_label = "Open photo transcription"
        elif comparison_review_count == 0:
            next_action = "create_comparison_review"
            blocker = "Create the photo transcription comparison review."
            action_target_type = "parse_result" if latest_transcription is not None else "photo"
            action_target_id = latest_transcription["parse_id"] if latest_transcription is not None else photo_id
            action_target_label = "Create comparison review"
        elif open_review_count or open_comparison_review_count:
            next_action = "resolve_reviews"
            blocker = f"{open_review_count} open review item(s) remain."
            action_target_type = "review"
            action_target_id = open_review["review_id"] if open_review is not None else ""
            action_target_label = open_review["issue"] if open_review is not None else "Resolve reviews"
        elif canonical_candidate_count == 0:
            next_action = "map_canonical_candidate"
            blocker = "No canonical candidate draft is linked to this photo."
            action_target_type = "photo"
            action_target_id = photo_id
            action_target_label = "Open evidence mapping"
        elif applied_candidate_count < canonical_candidate_count:
            next_action = "apply_canonical_candidate"
            blocker = f"{applied_candidate_count} of {canonical_candidate_count} canonical candidate(s) applied."
            action_target_type = "canonical_candidate"
            action_target_id = unapplied_candidate["candidate_id"] if unapplied_candidate is not None else ""
            action_target_label = "Apply canonical candidate"
        else:
            next_action = "ready_to_close"
            blocker = ""
            action_target_type = "upload_batch"
            action_target_id = upload_batch_id
            action_target_label = "Ready"
        worklist.append(
            {
                **payload,
                "transcription_count": transcription_count,
                "open_review_count": open_review_count,
                "comparison_review_count": comparison_review_count,
                "open_comparison_review_count": open_comparison_review_count,
                "canonical_candidate_count": canonical_candidate_count,
                "applied_candidate_count": applied_candidate_count,
                "next_action": next_action,
                "blocker": blocker,
                "action_target_type": action_target_type,
                "action_target_id": action_target_id,
                "action_target_label": action_target_label,
                "latest_transcription_parse_id": latest_transcription["parse_id"] if latest_transcription is not None else "",
                "review_ids": [review["review_id"] for review in review_rows],
                "open_review_ids": [review["review_id"] for review in review_rows if review["status"] == "open"],
                "comparison_review_ids": [review["review_id"] for review in comparison_review_rows],
                "candidate_ids": [candidate["candidate_id"] for candidate in candidate_rows],
                "image_url": f"/api/photos/{quote(photo_id)}/image",
                "boundary": "export or view",
            }
        )
    return worklist


def operations_task(
    *,
    task_id: str,
    family: str,
    label: str,
    status: str,
    risk_class: str,
    target_type: str,
    target_id: str,
    target_label: str,
    blocker_reason: str = "",
    evidence_refs: dict[str, Any] | None = None,
    action_channel: str = "human",
    action_channel_label: str = "Human review",
    action_channel_reason: str = "This task changes or confirms colony records and should stay operator-led.",
    external_payload_policy: str = "local_only_until_approved",
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "family": family,
        "label": label,
        "status": status,
        "risk_class": risk_class,
        "target_type": target_type,
        "target_id": target_id,
        "target_label": target_label,
        "blocker_reason": blocker_reason,
        "evidence_refs": evidence_refs or {},
        "action_channel": action_channel,
        "action_channel_label": action_channel_label,
        "action_channel_reason": action_channel_reason,
        "external_payload_policy": external_payload_policy,
        "source_layer": "export or view",
    }


def operations_home_grouped_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    family_labels = {
        "focus_review": "Focus Review",
        "photo_worklist": "Photo Worklist",
        "canonical_apply": "Canonical Apply",
        "genotyping": "Genotyping",
        "breeding_weaning": "Breeding / Weaning",
        "export_readiness": "Export Readiness",
    }
    family_order = list(family_labels)
    grouped: list[dict[str, Any]] = []
    for family in family_order:
        family_tasks = [task for task in tasks if task["family"] == family]
        if not family_tasks:
            continue
        grouped.append(
            {
                "family": family,
                "label": family_labels[family],
                "count": len(family_tasks),
                "tasks": family_tasks,
            }
        )
    return grouped


def operations_home_tasks(conn: Any) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    batch_rows = conn.execute(
        """
        SELECT upload_batch_id
        FROM upload_batch
        WHERE status NOT IN ('closed', 'archived')
        ORDER BY created_at DESC, upload_batch_id
        LIMIT 25
        """
    ).fetchall()
    for batch in batch_rows:
        upload_batch_id = batch["upload_batch_id"]
        for item in upload_batch_release_worklist(conn, upload_batch_id):
            if item["next_action"] == "ready_to_close":
                continue
            risk_class = "blocker" if item["next_action"] == "resolve_reviews" else "action"
            tasks.append(
                operations_task(
                    task_id=f"photo_worklist:{item['photo_id']}:{item['next_action']}",
                    family="photo_worklist",
                    label=item["action_target_label"] or item["next_action"].replace("_", " "),
                    status=item["next_action"],
                    risk_class=risk_class,
                    target_type=item["action_target_type"],
                    target_id=item["action_target_id"],
                    target_label=item["action_target_label"],
                    blocker_reason=item["blocker"],
                    evidence_refs={
                        "upload_batch_id": upload_batch_id,
                        "source_photo_id": item["photo_id"],
                        "review_ids": item.get("review_ids", []),
                        "candidate_ids": item.get("candidate_ids", []),
                    },
                )
            )

    review_rows = conn.execute(
        """
        SELECT review.review_id, review.parse_id, review.severity, review.issue,
               review.current_value, review.suggested_value, review.review_reason,
               review.assigned_role, review.assigned_to, review.priority,
               review.status, review.created_at,
               parse.source_name, parse.photo_id, parse.raw_payload AS parse_raw_payload,
               parse.confidence AS parse_confidence, photo.original_filename
        FROM review_queue review
        LEFT JOIN parse_result parse ON parse.parse_id = review.parse_id
        LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
        WHERE review.status = 'open'
        ORDER BY review.created_at DESC, review.review_id
        """
    ).fetchall()
    for row in review_rows:
        payload = dict(row)
        payload["confidence"] = payload.get("parse_confidence")
        parse_payload = json_object(payload.pop("parse_raw_payload", "{}"))
        attention = review_attention_level(payload, parse_payload)
        if attention["attention_level"] not in {"must_review", "quick_check"}:
            continue
        risk_class = "blocker" if attention["attention_level"] == "must_review" else "review"
        tasks.append(
            operations_task(
                task_id=f"focus_review:{payload['review_id']}",
                family="focus_review",
                label=payload["issue"] or "Review source evidence",
                status=attention["attention_level"],
                risk_class=risk_class,
                target_type="review",
                target_id=payload["review_id"],
                target_label=payload["issue"] or payload["review_id"],
                blocker_reason=attention["attention_reason"],
                evidence_refs={
                    "review_id": payload["review_id"],
                    "parse_id": payload.get("parse_id") or "",
                    "source_photo_id": payload.get("photo_id") or "",
                    "source_photo_filename": payload.get("original_filename") or "",
                },
                action_channel="assistant",
                action_channel_label="Assistant-supported review",
                action_channel_reason="Assistant can summarize evidence and draft a correction, but the review remains operator-approved.",
                external_payload_policy="local_only_until_approved",
            )
        )

    candidate_rows = conn.execute(
        """
        SELECT candidate.candidate_id, candidate.review_id, candidate.parse_id,
               candidate.proposed_mouse_display_id, candidate.proposed_strain,
               candidate.status, candidate.created_at, parse.photo_id, photo.original_filename
        FROM canonical_candidate candidate
        JOIN review_queue review ON review.review_id = candidate.review_id
        LEFT JOIN parse_result parse ON parse.parse_id = candidate.parse_id
        LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
        WHERE candidate.status = 'draft'
          AND review.status = 'resolved'
        ORDER BY candidate.created_at DESC, candidate.candidate_id
        """
    ).fetchall()
    for row in candidate_rows:
        payload = dict(row)
        display = payload["proposed_mouse_display_id"] or payload["candidate_id"]
        tasks.append(
            operations_task(
                task_id=f"canonical_apply:{payload['candidate_id']}",
                family="canonical_apply",
                label=f"Apply reviewed candidate {display}",
                status="ready_to_apply",
                risk_class="operator_confirmed",
                target_type="canonical_candidate",
                target_id=payload["candidate_id"],
                target_label=display,
                blocker_reason="Review is resolved; preview before applying canonical state.",
                evidence_refs={
                    "candidate_id": payload["candidate_id"],
                    "review_id": payload["review_id"],
                    "parse_id": payload["parse_id"],
                    "source_photo_id": payload.get("photo_id") or "",
                    "source_photo_filename": payload.get("original_filename") or "",
                },
                action_channel="human",
                action_channel_label="Human canonical apply",
                action_channel_reason="Applying this task writes canonical mouse state and events.",
                external_payload_policy="no_external_write",
            )
        )

    mouse_rows = conn.execute(
        """
        SELECT mouse_id, display_id, raw_strain_text, genotype_status,
               genotyping_status, next_action, use_category, target_match_status,
               source_photo_id, source_note_item_id, source_record_id, updated_at
        FROM mouse_master
        WHERE status = 'active'
          AND next_action NOT IN ('', 'keep_for_maintenance')
        ORDER BY updated_at DESC, display_id COLLATE NOCASE
        LIMIT 50
        """
    ).fetchall()
    for row in mouse_rows:
        payload = dict(row)
        next_action = payload["next_action"] or "review_needed"
        family = "breeding_weaning" if next_action == "weaning_due" else "genotyping"
        risk_class = "blocker" if next_action in {"review_result", "review_needed", "weaning_due"} else "action"
        action_channel = "api_mcp" if family == "genotyping" else "human"
        action_channel_label = "API/MCP candidate" if action_channel == "api_mcp" else "Human colony action"
        action_channel_reason = (
            "This genotyping next action can be handed to a lab/API/MCP connector after traceable review."
            if action_channel == "api_mcp"
            else "This breeding or weaning action is physical colony work and should stay operator-led."
        )
        tasks.append(
            operations_task(
                task_id=f"{family}:{payload['mouse_id']}:{next_action}",
                family=family,
                label=f"{payload['display_id']} {next_action.replace('_', ' ')}",
                status=next_action,
                risk_class=risk_class,
                target_type="mouse",
                target_id=payload["mouse_id"],
                target_label=payload["display_id"],
                blocker_reason=f"Mouse next action is {next_action}.",
                evidence_refs={
                    "mouse_id": payload["mouse_id"],
                    "source_photo_id": payload.get("source_photo_id") or "",
                    "source_note_item_id": payload.get("source_note_item_id") or "",
                    "source_record_id": payload.get("source_record_id") or "",
                },
                action_channel=action_channel,
                action_channel_label=action_channel_label,
                action_channel_reason=action_channel_reason,
                external_payload_policy="minimal_payload_after_approval",
            )
        )

    blocked_reviews = export_review_blocker_count(conn)
    if blocked_reviews:
        blockers = open_review_blockers(conn, limit=5)
        tasks.append(
            operations_task(
                task_id="export_readiness:blocked",
                family="export_readiness",
                label="Resolve export blockers",
                status="export_blocked",
                risk_class="blocker",
                target_type="export",
                target_id="current_export_preview",
                target_label="Export readiness",
                blocker_reason=f"{blocked_reviews} focus review blocker(s) prevent final export.",
                evidence_refs={
                    "review_ids": [item["review_id"] for item in blockers],
                    "blocked_review_count": blocked_reviews,
                },
            )
        )
    return tasks


@app.get("/api/ui/operations-home")
def ui_operations_home() -> dict[str, Any]:
    with connection() as conn:
        attention_counts = open_review_attention_counts(conn)
        tasks = operations_home_tasks(conn)
    grouped = operations_home_grouped_tasks(tasks)
    action_channels = {"human": 0, "assistant": 0, "api_mcp": 0}
    for task in tasks:
        channel = task.get("action_channel", "human")
        if channel in action_channels:
            action_channels[channel] += 1
    return {
        "source_layer": "export or view",
        "page_question": "What needs doing next?",
        "summary": {
            "total_tasks": len(tasks),
            "must_review": int(attention_counts.get("must_review", 0)),
            "quick_check": int(attention_counts.get("quick_check", 0)),
            "export_blockers": int(attention_counts.get("must_review", 0)),
            "families": {group["family"]: group["count"] for group in grouped},
            "action_channels": action_channels,
        },
        "task_groups": grouped,
        "empty_state": operations_home_empty_state(),
    }


@app.post("/api/upload-batches")
def create_upload_batch(payload: UploadBatchCreate) -> dict[str, Any]:
    with connection() as conn:
        batch = create_upload_batch_record(
            conn,
            batch_label=payload.batch_label,
            expected_photo_count=payload.expected_photo_count,
            note=payload.note,
        )
    return batch


@app.get("/api/upload-batches")
def list_upload_batches() -> dict[str, Any]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT batch.upload_batch_id, batch.batch_label,
                   batch.expected_photo_count, batch.status,
                   batch.source_layer, batch.note, batch.created_at, batch.updated_at,
                   COUNT(photo.photo_id) AS photo_count,
                   SUM(CASE WHEN transcribed.photo_id IS NOT NULL THEN 1 ELSE 0 END) AS transcribed_photo_count,
                   SUM(CASE WHEN photo.photo_id IS NOT NULL AND transcribed.photo_id IS NULL THEN 1 ELSE 0 END) AS pending_transcription_count,
                   COALESCE(SUM(review_counts.total_reviews), 0) AS total_review_count,
                   COALESCE(SUM(review_counts.open_reviews), 0) AS open_review_count,
                   COALESCE(SUM(comparison_counts.comparison_reviews), 0) AS comparison_review_count,
                   COALESCE(SUM(comparison_counts.open_comparison_reviews), 0) AS open_comparison_review_count,
                   SUM(CASE WHEN transcribed.photo_id IS NOT NULL AND COALESCE(comparison_counts.comparison_reviews, 0) = 0 THEN 1 ELSE 0 END) AS comparison_needed_count,
                   COALESCE(candidate_counts.canonical_candidate_count, 0) AS canonical_candidate_count,
                   COALESCE(candidate_counts.applied_candidate_count, 0) AS applied_candidate_count,
                   MIN(photo.uploaded_at) AS first_photo_uploaded_at,
                   MAX(photo.uploaded_at) AS latest_photo_uploaded_at
            FROM upload_batch batch
            LEFT JOIN photo_log photo
                ON photo.upload_batch_id = batch.upload_batch_id
            LEFT JOIN (
                SELECT DISTINCT photo_id
                FROM parse_result
                WHERE source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
            ) transcribed ON transcribed.photo_id = photo.photo_id
            LEFT JOIN (
                SELECT parse.photo_id,
                       COUNT(review.review_id) AS total_reviews,
                       SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_reviews
                FROM parse_result parse
                JOIN review_queue review ON review.parse_id = parse.parse_id
                GROUP BY parse.photo_id
            ) review_counts ON review_counts.photo_id = photo.photo_id
            LEFT JOIN (
                SELECT parse.photo_id,
                       COUNT(review.review_id) AS comparison_reviews,
                       SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_comparison_reviews
                FROM parse_result parse
                JOIN review_queue review ON review.parse_id = parse.parse_id
                WHERE review.issue LIKE 'Photo transcription%'
                GROUP BY parse.photo_id
            ) comparison_counts ON comparison_counts.photo_id = photo.photo_id
            LEFT JOIN (
                SELECT photo.upload_batch_id,
                       COUNT(candidate.candidate_id) AS canonical_candidate_count,
                       SUM(CASE WHEN candidate.status = 'applied' THEN 1 ELSE 0 END) AS applied_candidate_count
                FROM photo_log photo
                JOIN parse_result parse ON parse.photo_id = photo.photo_id
                JOIN review_queue review ON review.parse_id = parse.parse_id
                JOIN canonical_candidate candidate ON candidate.review_id = review.review_id
                GROUP BY photo.upload_batch_id
            ) candidate_counts ON candidate_counts.upload_batch_id = batch.upload_batch_id
            GROUP BY batch.upload_batch_id
            ORDER BY batch.created_at DESC
            """
        ).fetchall()
        unbatched = conn.execute(
            """
            SELECT COUNT(*) AS photo_count
            FROM photo_log
            WHERE upload_batch_id IS NULL OR upload_batch_id = ''
            """
        ).fetchone()
    batches = [upload_batch_payload(row) for row in rows]
    return {
        "boundary": "export or view",
        "source_layer": "raw source summary",
        "batch_count": len(batches),
        "photo_count": sum(batch["photo_count"] for batch in batches) + int(unbatched["photo_count"] or 0),
        "pending_transcription_count": sum(batch["pending_transcription_count"] for batch in batches),
        "open_review_count": sum(batch["open_review_count"] for batch in batches),
        "unbatched_photo_count": int(unbatched["photo_count"] or 0),
        "rows": batches,
    }


@app.get("/api/upload-batches/{upload_batch_id}/release-preview")
def upload_batch_release_preview(upload_batch_id: str) -> dict[str, Any]:
    with connection() as conn:
        return upload_batch_release_preview_payload(conn, upload_batch_id)


@app.post("/api/upload-batches/{upload_batch_id}/release")
def release_upload_batch(upload_batch_id: str) -> dict[str, Any]:
    with connection() as conn:
        preview = upload_batch_release_preview_payload(conn, upload_batch_id)
        if not preview["ready"]:
            raise HTTPException(status_code=409, detail=preview)
        now = utc_now()
        conn.execute(
            """
            UPDATE upload_batch
            SET status = 'closed',
                updated_at = ?
            WHERE upload_batch_id = ?
            """,
            (now, upload_batch_id),
        )
        closed_preview = upload_batch_release_preview_payload(conn, upload_batch_id)
    return {
        **closed_preview,
        "released": True,
        "released_at": now,
    }


@app.post("/api/ai-draft-settings")
def update_ai_draft_settings(payload: AiDraftSettingsUpdate) -> dict[str, Any]:
    global RUNTIME_OPENAI_API_KEY
    RUNTIME_OPENAI_API_KEY = payload.api_key.strip()
    return {
        "status": "updated",
        "stored": "server_session_only" if RUNTIME_OPENAI_API_KEY else "cleared",
        "ai_draft": ai_draft_status(),
    }


@app.get("/api/photos")
def list_photos() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT photo.photo_id, photo.original_filename, photo.stored_path,
                   photo.upload_batch_id, batch.batch_label,
                   photo.uploaded_at, photo.status, photo.raw_source_kind,
                   COALESCE(review_counts.open_reviews, 0) AS open_review_count,
                   review_counts.latest_parse_id
            FROM photo_log photo
            LEFT JOIN upload_batch batch
                ON batch.upload_batch_id = photo.upload_batch_id
            LEFT JOIN (
                SELECT parse.photo_id, COUNT(review.review_id) AS open_reviews,
                       MAX(parse.parse_id) AS latest_parse_id
                FROM parse_result parse
                LEFT JOIN review_queue review
                    ON review.parse_id = parse.parse_id AND review.status = 'open'
                GROUP BY parse.photo_id
            ) review_counts ON review_counts.photo_id = photo.photo_id
            ORDER BY uploaded_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/photos/{photo_id}/image")
def get_photo_image(photo_id: str) -> FileResponse:
    photo, image_path, media_type = photo_image_path(photo_id)
    filename = photo["original_filename"] or image_path.name
    return FileResponse(image_path, media_type=media_type, filename=filename)


def photo_image_path(photo_id: str) -> tuple[Any, Path, str]:
    with connection() as conn:
        photo = conn.execute(
            """
            SELECT photo_id, original_filename, stored_path, uploaded_at, status, upload_batch_id
            FROM photo_log
            WHERE photo_id = ?
            """,
            (photo_id,),
        ).fetchone()
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")

    stored_path = Path(str(photo["stored_path"] or ""))
    image_path = (stored_path if stored_path.is_absolute() else ROOT / stored_path).resolve()
    photo_root = (app_db.DATA_DIR / "photos").resolve()
    if photo_root != image_path and photo_root not in image_path.parents:
        raise HTTPException(status_code=400, detail="Stored photo path is outside the photo evidence directory.")
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Stored photo file is missing.")

    filename = photo["original_filename"] or image_path.name
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return photo, image_path, media_type


def storage_trace_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def load_roi_presets() -> dict[str, Any]:
    if not ROI_PRESET_PATH.exists():
        raise HTTPException(status_code=500, detail="ROI preset configuration is missing.")
    with ROI_PRESET_PATH.open("r", encoding="utf-8") as file:
        presets = json.load(file)
    if not isinstance(presets, dict) or not presets:
        raise HTTPException(status_code=500, detail="ROI preset configuration is invalid.")
    if not validate_roi_presets(presets):
        raise HTTPException(status_code=500, detail="ROI preset configuration is invalid.")
    return presets


def validate_roi_presets(presets: dict[str, Any]) -> bool:
    for template_key, preset in presets.items():
        if not isinstance(template_key, str) or not template_key.strip() or not isinstance(preset, dict):
            return False
        if preset.get("boundary") != "parsed or intermediate result":
            return False
        rois = preset.get("rois")
        if not isinstance(rois, list) or not rois:
            return False
        seen_labels: set[str] = set()
        for roi in rois:
            if not isinstance(roi, dict):
                return False
            label = str(roi.get("label") or "").strip()
            if not label or label in seen_labels:
                return False
            seen_labels.add(label)
            target_fields = roi.get("target_fields")
            if not isinstance(target_fields, list) or not all(isinstance(field, str) and field.strip() for field in target_fields):
                return False
            try:
                x = float(roi.get("x"))
                y = float(roi.get("y"))
                width = float(roi.get("w"))
                height = float(roi.get("h"))
            except (TypeError, ValueError):
                return False
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                return False
            if x + width > 1 or y + height > 1:
                return False
    return True


def normalized_roi_rect(roi: dict[str, Any], card_width: int, card_height: int) -> tuple[int, int, int, int]:
    left = int(round(float(roi.get("x", 0)) * card_width))
    top = int(round(float(roi.get("y", 0)) * card_height))
    width = max(1, int(round(float(roi.get("w", 0)) * card_width)))
    height = max(1, int(round(float(roi.get("h", 0)) * card_height)))
    right = min(card_width, max(left + 1, left + width))
    bottom = min(card_height, max(top + 1, top + height))
    left = max(0, min(left, right - 1))
    top = max(0, min(top, bottom - 1))
    return left, top, right, bottom


def detect_card_bbox(image: Image.Image) -> dict[str, Any]:
    width, height = image.size
    if width <= 1 or height <= 1:
        return {"x": 0, "y": 0, "w": width, "h": height, "source": "tiny_image_full_frame", "template_hint": "blue_structured_card"}

    sample = image.convert("RGB")
    sample.thumbnail((900, 900))
    sample_width, sample_height = sample.size
    hsv = sample.convert("HSV")
    pixels = hsv.load()
    masks = {
        "blue_structured_card": [[False for _ in range(sample_width)] for _ in range(sample_height)],
        "yellow_note_dense_card": [[False for _ in range(sample_width)] for _ in range(sample_height)],
    }
    mask_counts = {"blue_structured_card": 0, "yellow_note_dense_card": 0}
    for y in range(sample_height):
        for x in range(sample_width):
            hue, saturation, value = pixels[x, y]
            if saturation < 35 or value < 45:
                continue
            if 105 <= hue <= 175:
                masks["blue_structured_card"][y][x] = True
                mask_counts["blue_structured_card"] += 1
            elif 22 <= hue <= 62:
                masks["yellow_note_dense_card"][y][x] = True
                mask_counts["yellow_note_dense_card"] += 1

    template_hint = "blue_structured_card"
    if mask_counts["yellow_note_dense_card"] > mask_counts[template_hint]:
        template_hint = "yellow_note_dense_card"

    min_required = max(80, int(sample_width * sample_height * 0.015))
    if mask_counts[template_hint] < min_required:
        fallback_y = int(height * 0.18)
        return {
            "x": 0,
            "y": fallback_y,
            "w": width,
            "h": max(1, height - fallback_y),
            "source": "heuristic_lower_frame",
            "template_hint": template_hint,
        }

    mask = masks[template_hint]
    visited = [[False for _ in range(sample_width)] for _ in range(sample_height)]
    components: list[dict[str, Any]] = []
    for start_y in range(sample_height):
        for start_x in range(sample_width):
            if visited[start_y][start_x] or not mask[start_y][start_x]:
                continue
            stack = [(start_x, start_y)]
            visited[start_y][start_x] = True
            min_x = max_x = start_x
            min_y = max_y = start_y
            area = 0
            while stack:
                x, y = stack.pop()
                area += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for next_x, next_y in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                    (x - 1, y - 1),
                    (x - 1, y + 1),
                    (x + 1, y - 1),
                    (x + 1, y + 1),
                ):
                    if 0 <= next_x < sample_width and 0 <= next_y < sample_height and not visited[next_y][next_x] and mask[next_y][next_x]:
                        visited[next_y][next_x] = True
                        stack.append((next_x, next_y))
            if area >= min_required:
                bbox_area = max(1, (max_x - min_x + 1) * (max_y - min_y + 1))
                fill_ratio = area / bbox_area
                components.append(
                    {
                        "area": area,
                        "bbox_area": bbox_area,
                        "fill_ratio": fill_ratio,
                        "min_x": min_x,
                        "max_x": max_x,
                        "min_y": min_y,
                        "max_y": max_y,
                    }
                )

    if not components:
        fallback_y = int(height * 0.18)
        return {
            "x": 0,
            "y": fallback_y,
            "w": width,
            "h": max(1, height - fallback_y),
            "source": "heuristic_lower_frame",
            "template_hint": template_hint,
        }

    component = max(components, key=lambda item: (item["area"], item["bbox_area"] * item["fill_ratio"]))
    scale_x = width / sample_width
    scale_y = height / sample_height
    pad_x = max(8, int((component["max_x"] - component["min_x"] + 1) * scale_x * 0.04))
    pad_y = max(8, int((component["max_y"] - component["min_y"] + 1) * scale_y * 0.04))
    left = max(0, int(component["min_x"] * scale_x) - pad_x)
    top = max(0, int(component["min_y"] * scale_y) - pad_y)
    right = min(width, int((component["max_x"] + 1) * scale_x) + pad_x)
    bottom = min(height, int((component["max_y"] + 1) * scale_y) + pad_y)
    if template_hint == "blue_structured_card":
        component_width = max(1, right - left)
        component_height = max(1, bottom - top)
        if component_width < width * 0.55 or component_height < height * 0.35:
            left = max(0, left - int(component_width * 0.75))
            top = max(0, top - int(component_height * 0.4))
            right = min(width, right + int(component_width * 1.25))
            bottom = min(height, bottom + int(component_height * 0.15))
    return {
        "x": left,
        "y": top,
        "w": max(1, right - left),
        "h": max(1, bottom - top),
        "source": "color_card_connected_component_expanded" if template_hint == "blue_structured_card" else "color_card_connected_component",
        "template_hint": template_hint,
    }


def card_color_mask_match(hue: int, saturation: int, value: int, template_type: str) -> bool:
    if saturation < 35 or value < 45:
        return False
    if template_type == "blue_structured_card":
        return 105 <= hue <= 175
    if template_type == "yellow_note_dense_card":
        return 22 <= hue <= 62
    return False


def estimate_card_color_axis_angle(card_image: Image.Image, template_type: str) -> float | None:
    sample = card_image.convert("RGB")
    sample.thumbnail((700, 700))
    hsv = sample.convert("HSV")
    pixels = hsv.load()
    points: list[tuple[float, float]] = []
    for y in range(sample.height):
        for x in range(sample.width):
            hue, saturation, value = pixels[x, y]
            if card_color_mask_match(hue, saturation, value, template_type):
                points.append((float(x), float(y)))
    if len(points) < max(80, int(sample.width * sample.height * 0.015)):
        return None
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    cov_xx = sum((point[0] - mean_x) ** 2 for point in points) / len(points)
    cov_yy = sum((point[1] - mean_y) ** 2 for point in points) / len(points)
    cov_xy = sum((point[0] - mean_x) * (point[1] - mean_y) for point in points) / len(points)
    if cov_xx == 0 and cov_yy == 0:
        return None
    angle = math.degrees(0.5 * math.atan2(2 * cov_xy, cov_xx - cov_yy))
    if angle <= -90:
        angle += 180
    if angle > 90:
        angle -= 180
    return angle


def normalize_card_orientation(card_image: Image.Image, template_type: str) -> tuple[Image.Image, str]:
    angle = estimate_card_color_axis_angle(card_image, template_type)
    if angle is not None and 6 < abs(angle) <= 15:
        corrected = card_image.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
        return corrected, f"deskewed_{round(angle, 1)}_degrees"
    if angle is not None and abs(angle) > 15:
        return card_image, f"as_detected_ignored_axis_{round(angle, 1)}"
    return card_image, "as_detected"


def longest_true_run(values: list[bool]) -> tuple[int, int] | None:
    best_start = -1
    best_end = -1
    start = -1
    for index, value in enumerate(values + [False]):
        if value and start < 0:
            start = index
        elif not value and start >= 0:
            if index - start > best_end - best_start:
                best_start = start
                best_end = index
            start = -1
    if best_start < 0:
        return None
    return best_start, best_end


def trim_card_color_body(card_image: Image.Image, template_type: str) -> tuple[Image.Image, str]:
    if template_type not in {"blue_structured_card", "yellow_note_dense_card"}:
        return card_image, "not_applied"
    sample = card_image.convert("RGB")
    hsv = sample.convert("HSV")
    pixels = hsv.load()
    width, height = sample.size
    blue_template = template_type == "blue_structured_card"
    row_hits: list[int] = []
    col_hits = [0 for _ in range(width)]
    for y in range(height):
        row_count = 0
        for x in range(width):
            hue, saturation, value = pixels[x, y]
            if saturation < 35 or value < 45:
                continue
            is_card_color = 105 <= hue <= 175 if blue_template else 22 <= hue <= 62
            if is_card_color:
                row_count += 1
                col_hits[x] += 1
        row_hits.append(row_count)

    min_row_hits = int(width * 0.55)
    row_run = longest_true_run([count >= min_row_hits for count in row_hits])
    if row_run is None or row_run[1] - row_run[0] < height * 0.25:
        fallback_image, fallback_source = trim_card_paper_body(card_image, template_type)
        if fallback_source != "paper_body_trim_not_confident":
            return fallback_image, fallback_source
        return card_image, "color_body_trim_not_confident"

    row_top, row_bottom = row_run
    min_col_hits = int((row_bottom - row_top) * 0.45)
    col_run = longest_true_run([count >= min_col_hits for count in col_hits])
    if col_run is None or col_run[1] - col_run[0] < width * 0.35:
        left, right = 0, width
    else:
        left, right = col_run

    pad_x = max(8, int((right - left) * 0.025))
    pad_y = max(8, int((row_bottom - row_top) * 0.025))
    crop_box = (
        max(0, left - pad_x),
        max(0, row_top - pad_y),
        min(width, right + pad_x),
        min(height, row_bottom + pad_y),
    )
    return card_image.crop(crop_box), "color_body_trimmed"


def trim_card_paper_body(card_image: Image.Image, template_type: str) -> tuple[Image.Image, str]:
    sample = card_image.convert("RGB")
    hsv = sample.convert("HSV")
    pixels = hsv.load()
    width, height = sample.size
    blue_template = template_type == "blue_structured_card"
    row_hits: list[int] = []
    col_hits = [0 for _ in range(width)]

    for y in range(height):
        row_count = 0
        for x in range(width):
            hue, saturation, value = pixels[x, y]
            is_light_paper = value >= 130 and saturation <= 70
            is_tinted_card = (
                value >= 85
                and saturation >= 25
                and (95 <= hue <= 180 if blue_template else 18 <= hue <= 70)
            )
            if is_light_paper or is_tinted_card:
                row_count += 1
                col_hits[x] += 1
        row_hits.append(row_count)

    min_row_hits = int(width * 0.42)
    row_run = longest_true_run([count >= min_row_hits for count in row_hits])
    if row_run is None or row_run[1] - row_run[0] < height * 0.22:
        return card_image, "paper_body_trim_not_confident"

    row_top, row_bottom = row_run
    min_col_hits = int((row_bottom - row_top) * 0.38)
    col_run = longest_true_run([count >= min_col_hits for count in col_hits])
    if col_run is None or col_run[1] - col_run[0] < width * 0.35:
        left, right = 0, width
    else:
        left, right = col_run

    pad_x = max(8, int((right - left) * 0.02))
    pad_y = max(8, int((row_bottom - row_top) * 0.02))
    crop_box = (
        max(0, left - pad_x),
        max(0, row_top - pad_y),
        min(width, right + pad_x),
        min(height, row_bottom + pad_y),
    )
    return card_image.crop(crop_box), "paper_body_trimmed"


def safe_roi_root(photo_id: str, template_type: str) -> Path:
    safe_photo_id = re.sub(r"[^A-Za-z0-9_.-]", "_", photo_id)
    safe_template = re.sub(r"[^A-Za-z0-9_.-]", "_", template_type)
    roi_root = (ROOT / "data" / "roi" / safe_photo_id / safe_template).resolve()
    allowed_root = (ROOT / "data" / "roi").resolve()
    if allowed_root != roi_root and allowed_root not in roi_root.parents:
        raise HTTPException(status_code=400, detail="ROI crop path is outside the derived ROI directory.")
    roi_root.mkdir(parents=True, exist_ok=True)
    return roi_root


def save_jpeg_atomic(image: Image.Image, path: Path, *, quality: int = 92) -> None:
    temp_path = path.with_name(f".{path.stem}-{new_id('roi_tmp')}{path.suffix}")
    try:
        image.save(temp_path, "JPEG", quality=quality)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _generate_roi_preview(photo_id: str, template_type: str | None = None) -> dict[str, Any]:
    presets = load_roi_presets()
    photo, image_path, media_type = photo_image_path(photo_id)
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="ROI preview requires an image source photo.")
    try:
        with Image.open(image_path) as raw_image:
            image = ImageOps.exif_transpose(raw_image).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=415, detail="ROI preview requires a readable image source photo.") from error

    bbox = detect_card_bbox(image)
    selected_template = template_type or bbox["template_hint"]
    if selected_template not in presets:
        raise HTTPException(status_code=400, detail=f"Unknown ROI template: {selected_template}")
    preset = presets[selected_template]
    left = int(bbox["x"])
    top = int(bbox["y"])
    right = min(image.width, left + int(bbox["w"]))
    bottom = min(image.height, top + int(bbox["h"]))
    card_image = image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
    card_image, orientation_source = normalize_card_orientation(card_image, selected_template)
    card_image, trim_source = trim_card_color_body(card_image, selected_template)
    roi_root = safe_roi_root(photo_id, selected_template)
    card_path = roi_root / "card.jpg"
    save_jpeg_atomic(card_image, card_path, quality=92)

    crops: list[dict[str, Any]] = []
    for roi in preset.get("rois", []):
        label = str(roi.get("label") or "").strip()
        if not label:
            continue
        rect = normalized_roi_rect(roi, card_image.width, card_image.height)
        crop_path = roi_root / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}.jpg"
        save_jpeg_atomic(card_image.crop(rect), crop_path, quality=92)
        crops.append(
            {
                "label": label,
                "display_name": roi.get("display_name") or label,
                "target_fields": roi.get("target_fields") or [],
                "mode": roi.get("mode") or "field_crop",
                "artifact_layer": "cache",
                "bbox": {"x": rect[0], "y": rect[1], "w": rect[2] - rect[0], "h": rect[3] - rect[1]},
                "image_url": f"/api/photos/{quote(photo_id)}/roi/{quote(label)}/image?template_type={quote(selected_template)}",
            }
        )

    return {
        "boundary": "parsed or intermediate result",
        "source_layer": "raw source photo",
        "derived_layer": "parsed or intermediate result",
        "artifact_layer": "cache",
        "photo_id": photo_id,
        "photo_filename": photo["original_filename"],
        "template_type": selected_template,
        "template_label": preset.get("label") or selected_template,
        "template_source": "config/roi_presets.json",
        "card_bbox": bbox,
        "card_orientation": orientation_source,
        "card_trim": trim_source,
        "card_image_url": f"/api/photos/{quote(photo_id)}/roi-card/image?template_type={quote(selected_template)}",
        "crops": crops,
        "review_note": "ROI crops are derived review aids. The original uploaded photo remains the raw evidence.",
    }


def generate_roi_preview(photo_id: str, template_type: str | None = None) -> dict[str, Any]:
    with ROI_CACHE_LOCK:
        return _generate_roi_preview(photo_id, template_type)


@app.get("/api/photos/{photo_id}/roi-preview")
def get_photo_roi_preview(photo_id: str, template_type: str | None = None) -> dict[str, Any]:
    return generate_roi_preview(photo_id, template_type)


@app.get("/api/photos/{photo_id}/roi-card/image")
def get_photo_roi_card_image(photo_id: str, template_type: str | None = None) -> Response:
    with ROI_CACHE_LOCK:
        preview = generate_roi_preview(photo_id, template_type)
        roi_root = safe_roi_root(photo_id, preview["template_type"])
        return Response(
            (roi_root / "card.jpg").read_bytes(),
            media_type="image/jpeg",
            headers={"Content-Disposition": f'inline; filename="{photo_id}-card.jpg"'},
        )


@app.get("/api/photos/{photo_id}/roi/{roi_label}/image")
def get_photo_roi_image(photo_id: str, roi_label: str, template_type: str | None = None) -> Response:
    with ROI_CACHE_LOCK:
        preview = generate_roi_preview(photo_id, template_type)
        labels = {crop["label"] for crop in preview["crops"]}
        if roi_label not in labels:
            raise HTTPException(status_code=404, detail="ROI label not found for the selected template.")
        roi_root = safe_roi_root(photo_id, preview["template_type"])
        crop_name = re.sub(r"[^A-Za-z0-9_.-]", "_", roi_label)
        return Response(
            (roi_root / f"{crop_name}.jpg").read_bytes(),
            media_type="image/jpeg",
            headers={"Content-Disposition": f'inline; filename="{photo_id}-{crop_name}.jpg"'},
        )


def image_input_from_path(image_path: Path, *, detail: str) -> dict[str, Any]:
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    image_bytes = image_path.read_bytes()
    if len(image_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="AI draft image payload is too large.")
    data_url = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    return {"type": "input_image", "image_url": data_url, "detail": detail}


def ai_transcription_image_content(photo_id: str, image_path: Path, media_type: str, detail: str) -> dict[str, Any]:
    try:
        preview = generate_roi_preview(photo_id)
    except HTTPException as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=f"ROI crop generation is required before external AI transcription: {error.detail}",
        ) from error

    roi_root = safe_roi_root(photo_id, preview["template_type"])
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
                "text": (
                    "Images are minimized ROI evidence derived from the selected source photo. "
                    "Image 1 is the normalized card crop. Later images are fixed field crops. "
                    "Use each field crop as a reading aid for its target fields, but cross-check against the full card crop. "
                    "If a field crop appears shifted, blank, or inconsistent with the same region on the card crop, prefer the visible full card crop and mark the field uncertain."
                ),
            },
        image_input_from_path(roi_root / "card.jpg", detail=detail),
    ]
    extraction_regions = [
        {
            "label": "card",
            "display_name": "Normalized card",
            "target_fields": ["raw_visible_text_lines", "card_type"],
            "mode": "context_card_crop",
        }
    ]
    for index, crop in enumerate(preview["crops"], start=2):
        label = str(crop["label"])
        crop_name = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
        target_fields = crop.get("target_fields") or []
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"Image {index}: ROI label={label}; display={crop.get('display_name') or label}; "
                    f"target_fields={', '.join(target_fields)}; mode={crop.get('mode') or 'field_crop'}."
                ),
            }
        )
        content.append(image_input_from_path(roi_root / f"{crop_name}.jpg", detail=detail))
        extraction_regions.append(
            {
                "label": label,
                "display_name": crop.get("display_name") or label,
                "target_fields": target_fields,
                "mode": crop.get("mode") or "field_crop",
            }
        )

    return {
        "mode": "roi_field_crops",
        "payload_minimization": (
            "Selected source photo was processed locally into a normalized card crop plus fixed field ROI crops; "
            "only those derived crops and active assigned strain names/aliases were sent; no colony records or Excel rows sent."
        ),
        "extraction_regions": extraction_regions,
        "roi_template_type": preview["template_type"],
        "roi_card_bbox": preview["card_bbox"],
        "roi_card_orientation": preview["card_orientation"],
        "roi_card_trim": preview["card_trim"],
        "content": content,
    }


def assigned_strain_scope_for_prompt() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT display_name, aliases_json
            FROM my_assigned_strain
            WHERE active = 1
            ORDER BY display_name COLLATE NOCASE
            LIMIT 25
            """
        ).fetchall()
    return [
        {
            "display_name": row["display_name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
        }
        for row in rows
    ]


def bounded_float(value: Any, *, minimum: float = 0, maximum: float = 100) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    if not math.isfinite(number):
        number = minimum
    if 0 < number <= 1 and maximum >= 100:
        number *= 100
    return max(minimum, min(maximum, number))


def repair_known_ocr_symbol_mojibake(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    replacements = {
        "\u00a1\u00ce": "\u2642",
        "\u00a1\u00cf": "\u2640",
        "\u00e2\u2122\u201a": "\u2642",
        "\u00e2\u2122\u20ac": "\u2640",
        "\u0432\u2122\u201a": "\u2642",
        "\u0432\u2122\u0402": "\u2640",
    }
    for broken, symbol in replacements.items():
        text = text.replace(broken, symbol)
    return text


def conservative_normalized_date(raw_value: Any, normalized_value: Any, uncertain_fields: list[str], field_name: str) -> str:
    normalized = str(normalized_value or "").strip()
    raw = str(raw_value or "").strip()
    if not normalized:
        return ""
    if field_name in uncertain_fields:
        return ""
    if "?" in raw or "?" in normalized:
        return ""
    # Preserve normalized values only when the raw text visibly contains a full date-like sequence.
    visible_full_dates = re.findall(r"\d{2,4}\D+\d{1,2}\D+\d{1,2}", raw)
    if not visible_full_dates:
        return ""
    partial_range = bool(
        "/" in normalized
        or any(separator in raw for separator in ("~", "\u2013", "\u2014"))
        or re.search(r"\d{2,4}\D+\d{1,2}\D+\d{1,2}\s*-\s*\d{1,2}\b", raw)
    )
    if partial_range and len(visible_full_dates) < 2:
        return ""
    if not valid_iso_date_or_range(normalized):
        return ""
    if not normalized_dates_match_visible_raw_dates(raw, normalized):
        return ""
    return normalized


def normalized_dates_match_visible_raw_dates(raw: str, normalized: str) -> bool:
    visible_dates = {
        parsed.isoformat()
        for parsed in visible_raw_dates(raw)
    }
    if not visible_dates:
        return False
    return all(part in visible_dates for part in normalized.split("/"))


def visible_raw_dates(raw: str) -> list[date]:
    parsed_dates: list[date] = []
    for match in re.finditer(r"\d{2,4}\D+\d{1,2}\D+\d{1,2}", raw):
        numbers = re.findall(r"\d+", match.group(0))
        if len(numbers) != 3:
            continue
        year_text, month_text, day_text = numbers
        if len(year_text) != 4:
            continue
        try:
            parsed_dates.append(date(int(year_text), int(month_text), int(day_text)))
        except ValueError:
            continue
    return parsed_dates


def valid_iso_date_or_range(value: str) -> bool:
    parts = value.split("/")
    if len(parts) not in {1, 2}:
        return False
    parsed_dates: list[date] = []
    for part in parts:
        try:
            parsed_dates.append(date.fromisoformat(part))
        except ValueError:
            return False
    if len(parsed_dates) == 2 and parsed_dates[0] > parsed_dates[1]:
        return False
    return True


def add_uncertain_field(uncertain_fields: list[str], field_name: str) -> None:
    normalized = normalize_uncertain_field_name(field_name)
    if normalized and normalized not in uncertain_fields:
        uncertain_fields.append(normalized)


def field_contains_neighbor_label(value: Any) -> bool:
    text = str(value or "").lower()
    return bool(
        re.search(
            r"\b(strain|d\.?\s*o\.?\s*b|dob|i\.?\s*d|lmo|mating|cage|card|note|no\.?)\b",
            text,
        )
    )


def has_sex_hint(value: Any) -> bool:
    text = repair_known_ocr_symbol_mojibake(value)
    lowered = text.lower()
    return bool(
        "\u2642" in text
        or "\u2640" in text
        or re.search(r"(^|[^a-z])(m|f)([^a-z]|$)", lowered)
        or any(token in lowered for token in ["male", "female", "mixed", "both", "m/f", "f/m"])
        or any(token in text for token in ["수", "암"])
    )


def mouse_count_has_unexpected_text(value: Any) -> bool:
    text = repair_known_ocr_symbol_mojibake(value).strip()
    if not text:
        return False
    scrubbed = text.lower()
    scrubbed = scrubbed.replace("\u2642", " ").replace("\u2640", " ")
    scrubbed = re.sub(r"\b(total|mixed|both|male|female|m|f|p|ea|pcs|count)\b", " ", scrubbed)
    scrubbed = re.sub(r"[\d\s,./+\-()]+", " ", scrubbed)
    return bool(re.search(r"[a-z]{2,}", scrubbed))


def first_visible_invalid_date_token(value: Any) -> str:
    text = str(value or "")
    for match in re.finditer(r"\d{4}\D+\d{1,2}\D+\d{1,2}", text):
        numbers = re.findall(r"\d+", match.group(0))
        if len(numbers) != 3:
            continue
        try:
            date(int(numbers[0]), int(numbers[1]), int(numbers[2]))
        except ValueError:
            return match.group(0).strip()
    return ""


def extract_declared_mouse_count(value: Any) -> int | None:
    text = repair_known_ocr_symbol_mojibake(value).strip()
    if not text:
        return None
    if "\u2642" not in text and "\u2640" not in text and not re.search(r"\b(total|mixed|both|male|female|m|f|p)\b", text, re.I):
        return None
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    if not numbers:
        return None
    return max(numbers)


def strain_tokens(value: Any) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", str(value or ""))
        if len(token) >= 2
    }


def ai_draft_plausibility_findings(draft: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(field: str, message: str, severity: str = "medium") -> None:
        if not any(item["field"] == field and item["message"] == message for item in findings):
            findings.append({"field": field, "severity": severity, "message": message})

    raw_strain = str(draft.get("raw_strain") or "").strip()
    matched_strain = str(draft.get("matched_strain") or "").strip()
    sex_raw = repair_known_ocr_symbol_mojibake(draft.get("sex_raw")).strip()
    mouse_count = repair_known_ocr_symbol_mojibake(draft.get("mouse_count")).strip()
    notes = draft.get("notes") if isinstance(draft.get("notes"), list) else []

    if field_contains_neighbor_label(raw_strain):
        add("raw_strain", "Raw strain text appears to include a neighboring card label.", "high")
    if field_contains_neighbor_label(matched_strain):
        add("matched_strain", "Assigned strain match appears to include a neighboring card label.", "high")
    if raw_strain and matched_strain and raw_strain.lower() != matched_strain.lower():
        raw_tokens = strain_tokens(raw_strain)
        matched_tokens = strain_tokens(matched_strain)
        if raw_tokens and matched_tokens and raw_tokens.isdisjoint(matched_tokens):
            add("matched_strain", "Assigned strain has no visible token overlap with raw strain text.", "medium")
    if matched_strain and not raw_strain:
        add("raw_strain", "Assigned strain exists but raw visible strain text is blank.", "medium")

    if sex_raw:
        sex_normalized = normalize_sex_raw(sex_raw)
        if field_contains_neighbor_label(sex_raw):
            add("sex_raw", "Sex field appears to include neighboring card-label text.", "high")
        if sex_normalized == "unknown":
            add("sex_raw", "Sex field is not one of the expected sex symbols or words.", "high")
        if re.search(r"\d", sex_raw) and not has_sex_hint(sex_raw):
            add("sex_raw", "Sex field contains digits without a sex symbol or sex word.", "high")
            add("mouse_count", "Digits in the sex field may belong to mouse count instead.", "medium")

    if mouse_count:
        if field_contains_neighbor_label(mouse_count):
            add("mouse_count", "Mouse count appears to include neighboring card-label text.", "high")
        if not re.search(r"\d", mouse_count):
            add("mouse_count", "Mouse count has no visible number.", "medium")
        if mouse_count_has_unexpected_text(mouse_count):
            add("mouse_count", "Mouse count contains unexpected long text; it may be strain or note text.", "high")

    for raw_field, normalized_field in [("dob_raw", "dob_normalized"), ("mating_date_raw", "mating_date_normalized")]:
        invalid_token = first_visible_invalid_date_token(draft.get(raw_field))
        if invalid_token:
            add(raw_field, f"Visible date-like text is not a valid calendar date: {invalid_token}.", "high")
        if str(draft.get(raw_field) or "").strip() and not str(draft.get(normalized_field) or "").strip():
            add(normalized_field, "Visible date text was preserved raw but not normalized.", "low")

    declared_count = extract_declared_mouse_count(mouse_count)
    if declared_count is not None and notes:
        active_note_count = 0
        for note in notes:
            raw_line = str(note.get("raw") if isinstance(note, dict) else note)
            strike = str(note.get("strike") if isinstance(note, dict) else "none") or "none"
            if parse_note_line(raw_line, str(draft.get("card_type") or "unknown"))["parsed_type"] == "mouse_item" and strike == "none":
                active_note_count += 1
        if active_note_count and declared_count != active_note_count:
            add(
                "mouse_count",
                f"Declared count {declared_count} does not match {active_note_count} active mouse note line(s).",
                "medium",
            )

    return findings[:10]


def append_plausibility_note(reviewer_note: str, findings: list[dict[str, str]]) -> str:
    note = str(reviewer_note or "").strip()
    if not findings:
        return note
    summary = "; ".join(
        f"{finding['field']}: {finding['message'].rstrip('.')}"
        for finding in findings[:5]
    )
    suffix = f"Plausibility checks: {summary}."
    return f"{note} {suffix}".strip() if note else suffix


def parse_payload_plausibility_findings(parse_payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_findings = []
    for key in ("plausibilityFindings", "plausibility_findings"):
        if isinstance(parse_payload.get(key), list):
            raw_findings.extend(parse_payload[key])
    findings: list[dict[str, str]] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        field = normalize_uncertain_field_name(item.get("field"))
        message = str(item.get("message") or "").strip()
        severity = str(item.get("severity") or "medium").strip().lower()
        if not field or not message:
            continue
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        findings.append({"field": field, "severity": severity, "message": message})
    return findings


def ai_draft_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "card_type": {"type": "string", "enum": ["Separated", "Mating", "unknown"]},
            "raw_strain": {"type": "string"},
            "matched_strain": {"type": "string"},
            "sex_raw": {"type": "string"},
            "id_raw": {"type": "string"},
            "dob_raw": {"type": "string"},
            "dob_normalized": {"type": "string"},
            "mating_date_raw": {"type": "string"},
            "mating_date_normalized": {"type": "string"},
            "lmo_raw": {"type": "string"},
            "mouse_count": {"type": "string"},
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw": {"type": "string"},
                        "meaning": {"type": "string"},
                        "strike": {"type": "string", "enum": ["none", "single", "double", "unclear"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["raw", "meaning", "strike", "confidence"],
                },
            },
            "raw_visible_text_lines": {"type": "array", "items": {"type": "string"}},
            "symbol_confusions": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "uncertain_fields": {"type": "array", "items": {"type": "string"}},
            "reviewer_note": {"type": "string"},
        },
        "required": [
            "card_type",
            "raw_strain",
            "matched_strain",
            "sex_raw",
            "id_raw",
            "dob_raw",
            "dob_normalized",
            "mating_date_raw",
            "mating_date_normalized",
            "lmo_raw",
            "mouse_count",
            "notes",
            "raw_visible_text_lines",
            "symbol_confusions",
            "confidence",
            "uncertain_fields",
            "reviewer_note",
        ],
    }


def is_unlabeled_numeric_note_line(raw_line: Any) -> bool:
    line = str(raw_line or "").strip()
    if not re.search(r"\d", line):
        return False
    if not re.fullmatch(r"[\d\s,./\\-]+", line):
        return False
    return not bool(re.search(r"\d{2,4}[./-]\d{1,2}[./-]\d{1,2}", line))


def normalize_ai_draft_payload(value: Any) -> dict[str, Any]:
    draft = value if isinstance(value, dict) else {}
    notes = draft.get("notes") if isinstance(draft.get("notes"), list) else []
    normalized_notes = []
    for note in notes[:25]:
        if not isinstance(note, dict):
            continue
        raw = repair_known_ocr_symbol_mojibake(note.get("raw")).strip()
        if not raw:
            continue
        strike = str(note.get("strike") or "unclear")
        if strike not in {"none", "single", "double", "unclear"}:
            strike = "unclear"
        meaning = str(note.get("meaning") or "")
        if is_unlabeled_numeric_note_line(raw):
            meaning = "unlabeled_numeric_note"
        normalized_notes.append(
            {
                "raw": raw,
                "meaning": meaning,
                "strike": strike,
                "confidence": bounded_float(note.get("confidence")),
            }
        )
    card_type = str(draft.get("card_type") or "unknown")
    if card_type not in {"Separated", "Mating", "unknown"}:
        card_type = "unknown"
    card_type = infer_card_type_from_sex(card_type, draft.get("sex_raw"), "")
    uncertain_fields: list[str] = []
    seen_uncertain_fields: set[str] = set()
    for item in draft.get("uncertain_fields") if isinstance(draft.get("uncertain_fields"), list) else []:
        field_name = normalize_uncertain_field_name(item)
        if not field_name or field_name in seen_uncertain_fields:
            continue
        seen_uncertain_fields.add(field_name)
        uncertain_fields.append(field_name)
    normalized_draft = {
        "card_type": card_type,
        "raw_strain": str(draft.get("raw_strain") or ""),
        "matched_strain": str(draft.get("matched_strain") or ""),
        "sex_raw": repair_known_ocr_symbol_mojibake(draft.get("sex_raw")),
        "id_raw": str(draft.get("id_raw") or ""),
        "dob_raw": str(draft.get("dob_raw") or ""),
        "dob_normalized": conservative_normalized_date(draft.get("dob_raw"), draft.get("dob_normalized"), uncertain_fields, "dob_normalized"),
        "mating_date_raw": str(draft.get("mating_date_raw") or ""),
        "mating_date_normalized": conservative_normalized_date(
            draft.get("mating_date_raw"),
            draft.get("mating_date_normalized"),
            uncertain_fields,
            "mating_date_normalized",
        ),
        "lmo_raw": str(draft.get("lmo_raw") or ""),
        "mouse_count": repair_known_ocr_symbol_mojibake(draft.get("mouse_count")),
        "notes": normalized_notes,
        "raw_visible_text_lines": [
            repair_known_ocr_symbol_mojibake(item).strip()
            for item in (draft.get("raw_visible_text_lines") if isinstance(draft.get("raw_visible_text_lines"), list) else [])
            if repair_known_ocr_symbol_mojibake(item).strip()
        ][:60],
        "symbol_confusions": [
            str(item).strip()
            for item in (draft.get("symbol_confusions") if isinstance(draft.get("symbol_confusions"), list) else [])
            if str(item).strip()
        ][:30],
        "confidence": bounded_float(draft.get("confidence")),
        "uncertain_fields": uncertain_fields,
        "reviewer_note": str(draft.get("reviewer_note") or ""),
    }
    findings = ai_draft_plausibility_findings(normalized_draft)
    for finding in findings:
        if finding["severity"] in {"medium", "high"}:
            add_uncertain_field(uncertain_fields, finding["field"])
    normalized_draft["plausibility_findings"] = findings
    normalized_draft["reviewer_note"] = append_plausibility_note(normalized_draft["reviewer_note"], findings)
    return normalized_draft


def normalize_uncertain_field_name(value: Any) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    aliases = {
        "dobnormalized": "dob_normalized",
        "dateofbirthnormalized": "dob_normalized",
        "matingdatenormalized": "mating_date_normalized",
        "rawstrain": "raw_strain",
        "matchedstrain": "matched_strain",
        "sexraw": "sex_raw",
        "mousecount": "mouse_count",
    }
    if compact in aliases:
        return aliases[compact]
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def request_ai_transcription_draft(photo_id: str, payload: PhotoAiDraftCreate) -> dict[str, Any]:
    if not payload.approved_external_inference:
        raise HTTPException(
            status_code=403,
            detail="External AI draft transcription requires explicit per-request approval.",
        )
    api_key = current_openai_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured; AI draft transcription is unavailable.",
        )
    if payload.detail not in {"low", "high", "auto"}:
        raise HTTPException(status_code=400, detail="Image detail must be low, high, or auto.")

    photo, image_path, media_type = photo_image_path(photo_id)
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="AI draft transcription requires an image source photo.")

    image_content = ai_transcription_image_content(photo_id, image_path, media_type, payload.detail)
    assigned_scope = assigned_strain_scope_for_prompt()
    request_payload = {
        "model": os.environ.get("OPENAI_PARSE_ASSIST_MODEL", "gpt-5.2"),
        "store": False,
        "instructions": (
            "You are a careful OCR transcription assistant for handwritten mouse cage cards. "
            "Work in two steps internally: first transcribe visible text exactly from the supplied card/ROI images, then extract fields from that transcription. "
            "Never invent hidden text. Preserve visible raw text, including punctuation, primes, circles, sex symbols, and line breaks. "
            "Use unknown/empty values when uncertain, and list uncertainty explicitly. "
            "Prioritize exact transcription of symbols, letters, and digits over normalization. "
            "When ROI field crops are supplied, use them as target-field reading aids and cross-check them against the normalized card crop. "
            "Do not let an obviously shifted ROI crop override clearer text visible on the card crop. "
            "Classify all output as draft parsed evidence, not canonical state."
        ),
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Read this mouse cage card photo. Return only JSON matching the schema. "
                            f"Extraction image mode: {image_content['mode']}. "
                            f"Extraction regions: {json.dumps(image_content.get('extraction_regions', []), ensure_ascii=False)}. "
                            "Fields: card_type, raw_strain, matched_strain, sex_raw, id_raw, dob_raw, "
                            "dob_normalized, mating_date_raw, mating_date_normalized, lmo_raw, mouse_count, "
                            "notes, raw_visible_text_lines, symbol_confusions. "
                            "First fill raw_visible_text_lines with the visible card text line-by-line before field extraction. "
                            "Then extract fields only from those visible lines. "
                            "sex_raw is the visible Sex field: preserve the exact symbol/text. "
                            "Use the sex_raw ROI as a reading aid for sex_raw and mouse_count, but compare it with the Sex row on the card crop. "
                            "Do not copy Strain, D.O.B, I.D, LMO/Y/N checkbox text, or other neighboring-row text into sex_raw; if the sex ROI is shifted, blank, obstructed, or contains neighboring-row text, read from the card crop if visible and list sex_raw in uncertain_fields. "
                            "The symbol \u2642 means male and \u2640 means female; do not leave these blank when visible. "
                            "If the card shows the Korean sex labels \uc218/\uc218\ucef7 or \uc554/\uc554\ucef7, preserve that raw text and map only in reviewer_note. "
                            "mouse_count should preserve count text such as \u2642 2p, \u2640 6p, 2 total, or mixed. "
                            "id_raw is the visible I.D field, not the internal database id. lmo_raw preserves visible "
                            "LMO/O/N or similar checkbox marks without interpretation. Notes should preserve each "
                            "visible mouse ID, date/event line, or numeric-only temporary label line. "
                            "Mouse IDs commonly combine letters and digits, such as MT318 or Atg021. "
                            "Be careful with ambiguous characters: O vs 0, I/l vs 1, S vs 5, Z vs 2, B vs 8, G vs 6. "
                            "If a character is uncertain, keep the best raw visible guess, add the ambiguity to symbol_confusions, "
                            "and list the field in uncertain_fields. "
                            "For dob_normalized and mating_date_normalized, only emit ISO dates or ISO ranges when the full date order and year are unambiguous from the visible crop. "
                            "Do not guess the century or reorder ambiguous handwritten two-digit dates. Leave normalized date fields empty when uncertain. "
                            "For numeric-only post-separation labels, keep the raw numbers as notes and mark "
                            "meaning as unlabeled_numeric_note rather than inventing mouse IDs. "
                            "Ear label suffixes are constrained lab marks: R', L', R\u00b0/L\u00b0 or R0/L0 when circle-vs-zero is ambiguous, plus N for none. "
                            "If OCR sees impossible suffixes such as RWM, RL1M, stray letters, or a line crossing the text, preserve the raw note but mark the ear label uncertain instead of normalizing it. "
                            "Strike marks: none, single, double, unclear. "
                            f"Assigned strain scope for matching only: {json.dumps(assigned_scope, ensure_ascii=False)}"
                        ),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "cage_card_transcription_draft",
                "strict": True,
                "schema": ai_draft_schema(),
            }
        },
    }
    request_payload["input"][0]["content"].extend(image_content["content"])
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
        response.raise_for_status()
        response_payload = response.json()
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"AI draft transcription request failed: {error.response.text[:500]}",
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"AI draft transcription request failed: {error}") from error

    output_text = str(response_payload.get("output_text") or "")
    if not output_text:
        for item in response_payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    output_text += str(content.get("text") or "")
    try:
        draft = normalize_ai_draft_payload(json.loads(output_text))
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail="AI draft transcription returned invalid JSON.") from error

    return {
        "boundary": "parsed or intermediate result",
        "source_layer": "parsed or intermediate result",
        "photo_id": photo_id,
        "photo_filename": photo["original_filename"],
        "external_inference_used": True,
        "external_approval": {
            "approved_external_inference": True,
            "approval_scope": "single_photo_ai_transcription_draft",
            "payload_review": {
                "full_colony_records_sent": False,
                "excel_rows_sent": False,
                "raw_source_photo_sent": False,
                "derived_roi_crops_sent": image_content["mode"] == "roi_field_crops",
                "assigned_strain_scope_sent": bool(assigned_scope),
            },
        },
        "payload_minimization": image_content["payload_minimization"],
        "extraction_image_mode": image_content["mode"],
        "extraction_regions": image_content.get("extraction_regions", []),
        "roi_template_type": image_content.get("roi_template_type", ""),
        "stored": False,
        "draft": draft,
    }


@app.post("/api/photos/{photo_id}/ai-transcription-draft")
def create_photo_ai_transcription_draft(photo_id: str, payload: PhotoAiDraftCreate) -> dict[str, Any]:
    return request_ai_transcription_draft(photo_id, payload)


@app.post("/api/photos/{photo_id}/ai-extract-transcription")
def create_photo_ai_extracted_transcription(photo_id: str, payload: PhotoAiDraftCreate) -> dict[str, Any]:
    extraction = request_ai_transcription_draft(photo_id, payload)
    draft = extraction["draft"]
    transcription = create_photo_manual_transcription(
        photo_id,
        PhotoManualTranscriptionCreate(
            card_type=draft["card_type"],
            raw_strain=draft["raw_strain"],
            matched_strain=draft["matched_strain"],
            sex_raw=draft["sex_raw"],
            sex_normalized=normalize_sex_raw(draft["sex_raw"]),
            id_raw=draft["id_raw"],
            dob_raw=draft["dob_raw"],
            dob_normalized=draft["dob_normalized"],
            mating_date_raw=draft["mating_date_raw"],
            mating_date_normalized=draft["mating_date_normalized"],
            lmo_raw=draft["lmo_raw"],
            mouse_count=draft["mouse_count"],
            confidence=draft["confidence"],
            notes=draft["notes"],
            reviewer_note=(
                f"AI-extracted cage-card draft using {extraction['extraction_image_mode']}. "
                "Reviewer must compare against the raw photo before canonical writes. "
                f"Uncertain fields: {', '.join(draft['uncertain_fields']) or 'none listed'}. "
                f"Symbol confusions: {', '.join(draft['symbol_confusions']) or 'none listed'}. "
                f"{draft['reviewer_note']}"
            ),
            extraction_method="ai_photo_extraction",
            raw_visible_text_lines=draft["raw_visible_text_lines"],
            symbol_confusions=draft["symbol_confusions"],
            uncertain_fields=draft["uncertain_fields"],
            plausibility_findings=draft["plausibility_findings"],
            extraction_image_mode=extraction["extraction_image_mode"],
            roi_template_type=extraction.get("roi_template_type", ""),
            extraction_regions=extraction.get("extraction_regions", []),
            external_approval=extraction["external_approval"],
            payload_minimization=extraction["payload_minimization"],
        ),
    )
    return {
        **transcription,
        "extraction_method": "ai_photo_extraction",
        "external_inference_used": True,
        "payload_minimization": extraction["payload_minimization"],
        "external_approval": extraction["external_approval"],
        "extraction_image_mode": extraction["extraction_image_mode"],
        "extraction_regions": extraction["extraction_regions"],
        "roi_template_type": extraction.get("roi_template_type", ""),
        "draft_confidence": draft["confidence"],
        "uncertain_fields": draft["uncertain_fields"],
        "plausibility_findings": draft["plausibility_findings"],
    }


@app.get("/api/photo-review-workbench")
def photo_review_workbench() -> dict[str, Any]:
    with connection() as conn:
        photos = conn.execute(
            """
            SELECT photo.photo_id, photo.original_filename, photo.upload_batch_id,
                   batch.batch_label, photo.uploaded_at, photo.status, photo.raw_source_kind
            FROM photo_log photo
            LEFT JOIN upload_batch batch
                ON batch.upload_batch_id = photo.upload_batch_id
            ORDER BY photo.uploaded_at DESC
            """
        ).fetchall()
        rows = []
        for photo in photos:
            manual = conn.execute(
                """
                SELECT parse_id, parsed_at, status, raw_payload, source_name
                FROM parse_result
                WHERE photo_id = ?
                  AND source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
                ORDER BY parsed_at DESC
                LIMIT 1
                """,
                (photo["photo_id"],),
            ).fetchone()
            manual_parse_id = manual["parse_id"] if manual is not None else ""
            manual_payload = json_object(manual["raw_payload"]) if manual is not None else {}
            note_counts = {"note_lines": 0, "mouse_note_lines": 0}
            if manual_parse_id:
                note_row = conn.execute(
                    """
                    SELECT COUNT(*) AS note_lines,
                           SUM(CASE WHEN parsed_type = 'mouse_item' THEN 1 ELSE 0 END) AS mouse_note_lines
                    FROM card_note_item_log
                    WHERE parse_id = ?
                    """,
                    (manual_parse_id,),
                ).fetchone()
                note_counts = {
                    "note_lines": int(note_row["note_lines"] or 0),
                    "mouse_note_lines": int(note_row["mouse_note_lines"] or 0),
                }
            review_counts = conn.execute(
                """
                SELECT COUNT(*) AS total_reviews,
                       SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_reviews
                FROM parse_result parse
                JOIN review_queue review ON review.parse_id = parse.parse_id
                WHERE parse.photo_id = ?
                """,
                (photo["photo_id"],),
            ).fetchone()
            comparison_counts = {"comparison_reviews": 0, "open_comparison_reviews": 0, "resolved_comparison_reviews": 0}
            if manual_parse_id:
                comparison_row = conn.execute(
                    """
                    SELECT COUNT(*) AS comparison_reviews,
                           SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_comparison_reviews,
                           SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_comparison_reviews
                    FROM review_queue
                    WHERE parse_id = ?
                      AND issue LIKE 'Photo transcription%'
                    """,
                    (manual_parse_id,),
                ).fetchone()
                comparison_counts = {
                    "comparison_reviews": int(comparison_row["comparison_reviews"] or 0),
                    "open_comparison_reviews": int(comparison_row["open_comparison_reviews"] or 0),
                    "resolved_comparison_reviews": int(comparison_row["resolved_comparison_reviews"] or 0),
                }
            open_reviews = int(review_counts["open_reviews"] or 0)
            if not manual_parse_id:
                next_action = "transcribe_photo"
            elif open_reviews:
                next_action = "resolve_photo_reviews"
            elif comparison_counts["comparison_reviews"] == 0:
                next_action = "create_comparison_review"
            elif comparison_counts["open_comparison_reviews"]:
                next_action = "resolve_evidence_comparison"
            else:
                next_action = "ready_for_candidate_mapping"
            rows.append(
                {
                    "source_layer": "export or view",
                    "photo_id": photo["photo_id"],
                    "upload_batch_id": photo["upload_batch_id"] or "",
                    "batch_label": photo["batch_label"] or "",
                    "original_filename": photo["original_filename"],
                    "uploaded_at": photo["uploaded_at"],
                    "status": photo["status"],
                    "raw_source_kind": photo["raw_source_kind"],
                    "image_url": f"/api/photos/{quote(photo['photo_id'])}/image",
                    "manual_parse_id": manual_parse_id,
                    "manual_transcribed_at": manual["parsed_at"] if manual is not None else "",
                    "manual_source_name": manual["source_name"] if manual is not None else "",
                    "manual_payload": manual_payload,
                    "note_line_count": note_counts["note_lines"],
                    "mouse_note_line_count": note_counts["mouse_note_lines"],
                    "total_review_count": int(review_counts["total_reviews"] or 0),
                    "open_review_count": open_reviews,
                    **comparison_counts,
                    "next_action": next_action,
                }
            )
    batch_summary = list_upload_batches()
    return {
        "boundary": "export or view",
        "source_priority": ["raw source photo", "manual transcription", "review item", "canonical candidate"],
        "photo_count": len(rows),
        "pending_transcription_count": sum(1 for row in rows if row["next_action"] == "transcribe_photo"),
        "open_review_count": sum(row["open_review_count"] for row in rows),
        "batch_count": batch_summary["batch_count"],
        "unbatched_photo_count": batch_summary["unbatched_photo_count"],
        "batches": batch_summary["rows"],
        "rows": rows,
    }


def stable_checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tagged_parse_payload(
    payload: dict[str, Any],
    *,
    payload_kind: str,
    source_layer: str,
    schema_version: str = "parse_payload_v1",
) -> dict[str, Any]:
    return {
        **payload,
        "payload_kind": payload_kind,
        "source_layer": source_layer,
        "schema_version": schema_version,
    }


def create_source_record(
    conn: Any,
    *,
    source_type: str,
    source_uri: str = "",
    source_label: str = "",
    raw_payload: str = "",
    note: str = "",
) -> str:
    source_record_id = new_id("source")
    imported_at = utc_now()
    conn.execute(
        """
        INSERT INTO source_record
            (source_record_id, source_type, source_uri, source_label,
             raw_payload, imported_at, checksum, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_record_id,
            source_type,
            source_uri,
            source_label,
            raw_payload,
            imported_at,
            stable_checksum(f"{source_type}|{source_uri}|{source_label}|{raw_payload}"),
            note,
        ),
    )
    return source_record_id


def ensure_photo_review_candidate(
    conn: Any,
    *,
    photo_id: str,
    original_filename: str,
    stored_path: str,
    uploaded_at: str,
    source_record_id: str = "",
) -> dict[str, Any]:
    existing = conn.execute(
        """
        SELECT parse.parse_id, review.review_id
        FROM parse_result parse
        LEFT JOIN review_queue review ON review.parse_id = parse.parse_id
        WHERE parse.photo_id = ? AND parse.source_name = ?
        ORDER BY parse.parsed_at DESC
        LIMIT 1
        """,
        (photo_id, "photo_manual_review"),
    ).fetchone()
    if existing is not None:
        return {
            "parse_id": existing["parse_id"],
            "review_id": existing["review_id"],
            "created": False,
        }

    parse_id = new_id("parse")
    review_id = new_id("review")
    now = utc_now()
    raw_payload = tagged_parse_payload(
        {
        "layer": "review item",
        "raw_source_layer": "raw source",
        "source_type": "cage_card_photo",
        "photo_id": photo_id,
        "source_record_id": source_record_id,
        "original_filename": original_filename,
        "stored_path": stored_path,
        "uploaded_at": uploaded_at,
        "external_processing": "none",
        "extraction_status": "not_attempted",
        "note": "Create a manual card transcription before accepting this latest photo into canonical state.",
        },
        payload_kind="photo_manual_review_placeholder",
        source_layer="review item",
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
            "photo_manual_review",
            json.dumps(raw_payload, ensure_ascii=False),
            now,
            "review",
            0,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO review_queue
            (review_id, parse_id, severity, issue, current_value, suggested_value,
             review_reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            parse_id,
            "Medium",
            "Photo requires manual cage-card transcription",
            original_filename,
            "Review visible strain, sex, IDs, DOB, mating date, notes, and strike marks before accepting.",
            "Latest cage-card photo is raw evidence and may supersede predecessor Excel views. No OCR or inference has been accepted.",
            "open",
            now,
        ),
    )
    conn.execute("UPDATE photo_log SET status = ? WHERE photo_id = ?", ("review_pending", photo_id))
    conn.execute(
        """
        INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("action"),
            "photo_review_candidate_created",
            photo_id,
            None,
            json.dumps({"parse_id": parse_id, "review_id": review_id}, ensure_ascii=False),
            now,
        ),
    )
    return {"parse_id": parse_id, "review_id": review_id, "created": True}


@app.get("/api/source-records")
def list_source_records() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT source_record_id, source_type, source_uri, source_label,
                   raw_payload, imported_at, checksum, note
            FROM source_record
            ORDER BY imported_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def normalized_registry_text(value: str) -> str:
    return " ".join(str(value or "").split())


def get_or_create_gene_master(conn: Any, gene_symbol: str, source_record_id: str | None, now: str) -> str | None:
    clean_symbol = normalized_registry_text(gene_symbol)
    if not clean_symbol:
        return None
    existing = conn.execute(
        """
        SELECT gene_id, gene_symbol
        FROM gene_master
        WHERE LOWER(gene_symbol) = LOWER(?)
        ORDER BY active DESC, created_at
        LIMIT 1
        """,
        (clean_symbol,),
    ).fetchone()
    if existing is not None:
        return existing["gene_id"]
    gene_id = new_id("gene")
    conn.execute(
        """
        INSERT INTO gene_master
            (gene_id, gene_symbol, display_name, source_record_id, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (gene_id, clean_symbol, "", source_record_id, 1, now, now),
    )
    return gene_id


def gene_payload(row: Any) -> dict[str, Any]:
    return {
        "gene_id": row["gene_id"],
        "gene_symbol": row["gene_symbol"],
        "full_name": row["display_name"],
        "organism": "mouse",
        "description": row["description"],
        "external_reference": row["external_reference"],
        "source_record_id": row["source_record_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_or_create_allele_master(
    conn: Any,
    *,
    allele_symbol: str,
    gene_id: str | None,
    source_record_id: str | None,
    now: str,
) -> str | None:
    clean_symbol = normalized_registry_text(allele_symbol)
    if not clean_symbol:
        return None
    existing = conn.execute(
        """
        SELECT allele_id, gene_id
        FROM allele_master
        WHERE LOWER(allele_symbol) = LOWER(?)
          AND (gene_id = ? OR gene_id IS NULL OR ? IS NULL)
        ORDER BY active DESC, CASE WHEN gene_id = ? THEN 0 ELSE 1 END, created_at
        LIMIT 1
        """,
        (clean_symbol, gene_id, gene_id, gene_id),
    ).fetchone()
    if existing is not None:
        if gene_id and not existing["gene_id"]:
            before = {
                "allele_id": existing["allele_id"],
                "allele_symbol": clean_symbol,
                "gene_id": existing["gene_id"] or "",
            }
            conn.execute(
                """
                UPDATE allele_master
                SET gene_id = ?,
                    updated_at = ?
                WHERE allele_id = ?
                """,
                (gene_id, now, existing["allele_id"]),
            )
            conn.execute(
                """
                INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("action"),
                    "allele_master_gene_linked",
                    existing["allele_id"],
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(
                        {
                            "allele_id": existing["allele_id"],
                            "allele_symbol": clean_symbol,
                            "gene_id": gene_id,
                            "source_record_id": source_record_id or "",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        return existing["allele_id"]
    allele_id = new_id("allele")
    conn.execute(
        """
        INSERT INTO allele_master
            (allele_id, allele_symbol, display_name, gene_id, source_record_id, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (allele_id, clean_symbol, "", gene_id, source_record_id, 1, now, now),
    )
    return allele_id


def allele_payload(row: Any) -> dict[str, Any]:
    return {
        "allele_id": row["allele_id"],
        "gene_id": row["gene_id"] or "",
        "gene_symbol": row["gene_symbol"] or "",
        "allele_name": row["allele_symbol"],
        "allele_type": row["allele_type"],
        "description": row["display_name"],
        "inheritance": row["inheritance"],
        "zygosity_options": row["zygosity_options"],
        "genotyping_protocol": row["genotyping_protocol"],
        "source_record_id": row["source_record_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def link_strain_allele_master(
    conn: Any,
    *,
    strain_id: str,
    gene_id: str | None,
    allele_id: str | None,
    source_record_id: str | None,
    now: str,
) -> None:
    if not allele_id:
        return
    existing = conn.execute(
        """
        SELECT relationship_id
        FROM strain_allele_relationship
        WHERE strain_id = ?
          AND allele_id = ?
          AND status = 'active'
        LIMIT 1
        """,
        (strain_id, allele_id),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO strain_allele_relationship
            (relationship_id, strain_id, gene_id, allele_id, relationship_type,
             source_record_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("strain_allele"),
            strain_id,
            gene_id,
            allele_id,
            "configured_from_strain_registry",
            source_record_id,
            "active",
            now,
            now,
        ),
    )


def get_or_create_strain_registry_from_review(
    conn: Any,
    *,
    strain_name: str,
    gene_symbol: str,
    allele_name: str,
    source_record_id: str | None,
    canonical_entity_type: str,
    canonical_entity_id: str,
    now: str,
) -> tuple[str, bool]:
    clean_name = normalized_registry_text(strain_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="Reviewed strain name is required.")
    if canonical_entity_id or canonical_entity_type:
        if canonical_entity_type != "strain" or not canonical_entity_id:
            raise HTTPException(
                status_code=400,
                detail="canonical_entity_id must explicitly map the existing strain before applying a registry candidate.",
            )
        mapped = conn.execute(
            """
            SELECT strain_id, strain_name
            FROM strain_registry
            WHERE strain_id = ?
            """,
            (canonical_entity_id,),
        ).fetchone()
        if mapped is None:
            raise HTTPException(status_code=400, detail="Mapped canonical strain was not found.")
        if normalized_registry_text(mapped["strain_name"]).lower() != clean_name.lower():
            raise HTTPException(status_code=400, detail="Reviewed strain name must match the mapped canonical strain.")
        return mapped["strain_id"], False
    existing = conn.execute(
        """
        SELECT strain_id
        FROM strain_registry
        WHERE LOWER(strain_name) = LOWER(?)
        ORDER BY status = 'active' DESC, created_at
        LIMIT 1
        """,
        (clean_name,),
    ).fetchone()
    if existing is not None:
        if canonical_entity_type != "strain" or canonical_entity_id != existing["strain_id"]:
            raise HTTPException(
                status_code=400,
                detail="canonical_entity_id must explicitly map the existing strain before applying a registry candidate.",
            )
        return existing["strain_id"], False
    strain_id = new_id("strain")
    conn.execute(
        """
        INSERT INTO strain_registry
            (strain_id, strain_name, common_name, official_name, gene,
             allele, background, source, status, breeding_note,
             genotyping_note, owner, source_record_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strain_id,
            clean_name,
            "",
            "",
            normalized_registry_text(gene_symbol),
            normalized_registry_text(allele_name),
            "",
            "legacy_workbook_review",
            "active",
            "",
            "",
            "",
            source_record_id,
            now,
            now,
        ),
    )
    return strain_id, True


def legacy_source_record_id_for_parse(conn: Any, parse_id: str) -> str | None:
    prefix = "legacy_parse_"
    if not parse_id.startswith(prefix):
        return None
    legacy_import_id = parse_id[len(prefix) :]
    row = conn.execute(
        """
        SELECT source_record_id
        FROM legacy_workbook_import
        WHERE legacy_import_id = ?
        """,
        (legacy_import_id,),
    ).fetchone()
    return row["source_record_id"] if row is not None else None


def apply_legacy_strain_registry_candidate(
    conn: Any,
    *,
    review: Any,
    payload: ReviewResolutionCreate,
    resolved_at: str,
) -> dict[str, Any]:
    if str(review["issue"] or "") != "Legacy strain registry candidate requires review":
        raise HTTPException(status_code=400, detail="This review item is not a legacy strain registry candidate.")
    strain_name = normalized_registry_text(payload.reviewed_strain_name)
    gene_symbol = normalized_registry_text(payload.reviewed_gene_symbol)
    allele_name = normalized_registry_text(payload.reviewed_allele_name)
    if not all([strain_name, gene_symbol, allele_name]):
        raise HTTPException(
            status_code=400,
            detail="Reviewed strain name, gene symbol, and allele name are required before applying a registry candidate.",
        )
    candidate = json_object(str(review["current_value"] or "{}"))
    source_record_id = legacy_source_record_id_for_parse(conn, str(review["parse_id"] or ""))
    if not source_record_id:
        raise HTTPException(status_code=400, detail="A source record is required before applying a registry candidate.")
    before = {
        "review_id": review["review_id"],
        "parse_id": review["parse_id"],
        "candidate": candidate,
        "review_status": review["status"],
    }
    strain_id, created_strain = get_or_create_strain_registry_from_review(
        conn,
        strain_name=strain_name,
        gene_symbol=gene_symbol,
        allele_name=allele_name,
        source_record_id=source_record_id,
        canonical_entity_type=payload.canonical_entity_type.strip(),
        canonical_entity_id=payload.canonical_entity_id.strip(),
        now=resolved_at,
    )
    gene_id = get_or_create_gene_master(conn, gene_symbol, source_record_id, resolved_at)
    allele_id = get_or_create_allele_master(
        conn,
        allele_symbol=allele_name,
        gene_id=gene_id,
        source_record_id=source_record_id,
        now=resolved_at,
    )
    link_strain_allele_master(
        conn,
        strain_id=strain_id,
        gene_id=gene_id,
        allele_id=allele_id,
        source_record_id=source_record_id,
        now=resolved_at,
    )
    after = {
        "strain_id": strain_id,
        "created_strain": created_strain,
        "strain_name": strain_name,
        "gene_id": gene_id,
        "gene_symbol": gene_symbol,
        "allele_id": allele_id,
        "allele_name": allele_name,
        "source_record_id": source_record_id,
        "candidate": candidate,
        "boundary": "canonical structured state",
    }
    conn.execute(
        """
        INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("action"),
            "legacy_strain_registry_candidate_applied",
            review["review_id"],
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            resolved_at,
        ),
    )
    return after


def strain_allele_rows(conn: Any, strain_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT allele.allele_id, gene.gene_id, gene.gene_symbol,
               allele.allele_symbol AS allele_name,
               relationship.relationship_type
        FROM strain_allele_relationship relationship
        JOIN allele_master allele ON allele.allele_id = relationship.allele_id
        LEFT JOIN gene_master gene ON gene.gene_id = COALESCE(relationship.gene_id, allele.gene_id)
        WHERE relationship.strain_id = ?
          AND relationship.status = 'active'
          AND allele.active = 1
        ORDER BY gene.gene_symbol COLLATE NOCASE, allele.allele_symbol COLLATE NOCASE
        """,
        (strain_id,),
    ).fetchall()
    return [
        {
            "allele_id": row["allele_id"],
            "gene_id": row["gene_id"] or "",
            "gene_symbol": row["gene_symbol"] or "",
            "allele_name": row["allele_name"],
            "default_zygosity": "",
            "note": "",
        }
        for row in rows
    ]


@app.get("/api/genes")
def list_genes() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT gene_id, gene_symbol, display_name, description, external_reference,
                   source_record_id, created_at, updated_at
            FROM gene_master
            WHERE active = 1
            ORDER BY gene_symbol COLLATE NOCASE
            """
        ).fetchall()
    return [gene_payload(row) for row in rows]


@app.patch("/api/genes/{gene_id}")
def update_gene(gene_id: str, payload: GeneRegistryUpdate) -> dict[str, Any]:
    updated_at = utc_now()
    with connection() as conn:
        existing = conn.execute(
            """
            SELECT gene_id
            FROM gene_master
            WHERE gene_id = ? AND active = 1
            """,
            (gene_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Gene not found.")
        conn.execute(
            """
            UPDATE gene_master
            SET display_name = ?,
                description = ?,
                external_reference = ?,
                updated_at = ?
            WHERE gene_id = ?
            """,
            (
                normalized_registry_text(payload.full_name),
                normalized_registry_text(payload.description),
                normalized_registry_text(payload.external_reference),
                updated_at,
                gene_id,
            ),
        )
        row = conn.execute(
            """
            SELECT gene_id, gene_symbol, display_name, description, external_reference,
                   source_record_id, created_at, updated_at
            FROM gene_master
            WHERE gene_id = ?
            """,
            (gene_id,),
        ).fetchone()
    return gene_payload(row)


@app.get("/api/alleles")
def list_alleles() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT allele.allele_id, allele.gene_id, gene.gene_symbol,
                   allele.allele_symbol, allele.display_name, allele.allele_type,
                   allele.inheritance, allele.zygosity_options, allele.genotyping_protocol,
                   allele.source_record_id,
                   allele.created_at, allele.updated_at
            FROM allele_master allele
            LEFT JOIN gene_master gene ON gene.gene_id = allele.gene_id
            WHERE allele.active = 1
            ORDER BY gene.gene_symbol COLLATE NOCASE, allele.allele_symbol COLLATE NOCASE
            """
        ).fetchall()
    return [allele_payload(row) for row in rows]


@app.patch("/api/alleles/{allele_id}")
def update_allele(allele_id: str, payload: AlleleRegistryUpdate) -> dict[str, Any]:
    updated_at = utc_now()
    with connection() as conn:
        existing = conn.execute(
            """
            SELECT allele_id
            FROM allele_master
            WHERE allele_id = ? AND active = 1
            """,
            (allele_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Allele not found.")
        conn.execute(
            """
            UPDATE allele_master
            SET display_name = ?,
                allele_type = ?,
                inheritance = ?,
                zygosity_options = ?,
                genotyping_protocol = ?,
                updated_at = ?
            WHERE allele_id = ?
            """,
            (
                normalized_registry_text(payload.description),
                normalized_registry_text(payload.allele_type),
                normalized_registry_text(payload.inheritance),
                normalized_registry_text(payload.zygosity_options),
                normalized_registry_text(payload.genotyping_protocol),
                updated_at,
                allele_id,
            ),
        )
        row = conn.execute(
            """
            SELECT allele.allele_id, allele.gene_id, gene.gene_symbol,
                   allele.allele_symbol, allele.display_name, allele.allele_type,
                   allele.inheritance, allele.zygosity_options, allele.genotyping_protocol,
                   allele.source_record_id,
                   allele.created_at, allele.updated_at
            FROM allele_master allele
            LEFT JOIN gene_master gene ON gene.gene_id = allele.gene_id
            WHERE allele.allele_id = ?
            """,
            (allele_id,),
        ).fetchone()
    return allele_payload(row)


@app.get("/api/strains")
def list_strains() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT strain_id, strain_name, common_name, official_name, gene,
                   allele, background, source, status, breeding_note,
                   genotyping_note, owner, source_record_id, created_at, updated_at
            FROM strain_registry
            ORDER BY status = 'active' DESC, strain_name COLLATE NOCASE
            """
        ).fetchall()
        result = []
        for row in rows:
            payload = dict(row)
            payload["alleles"] = strain_allele_rows(conn, payload["strain_id"])
            result.append(payload)
    return result


@app.post("/api/strains")
def create_strain(payload: StrainRegistryCreate) -> dict[str, Any]:
    strain_name = " ".join(payload.strain_name.split())
    if not strain_name:
        raise HTTPException(status_code=400, detail="Strain name is required.")

    now = utc_now()
    strain_id = new_id("strain")
    raw_payload = payload.model_dump_json()
    with connection() as conn:
        source_record_id = payload.source_record_id
        if source_record_id is None:
            source_record_id = create_source_record(
                conn,
                source_type="manual_entry",
                source_label=f"Manual strain registry entry: {strain_name}",
                raw_payload=raw_payload,
                note="Created from local Strain Registry form.",
            )
        else:
            exists = conn.execute(
                "SELECT 1 FROM source_record WHERE source_record_id = ?",
                (source_record_id,),
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=400, detail="source_record_id does not exist.")

        conn.execute(
            """
            INSERT INTO strain_registry
                (strain_id, strain_name, common_name, official_name, gene,
                 allele, background, source, status, breeding_note,
                 genotyping_note, owner, source_record_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strain_id,
                strain_name,
                payload.common_name,
                payload.official_name,
                payload.gene,
                payload.allele,
                payload.background,
                payload.source,
                payload.status or "active",
                payload.breeding_note,
                payload.genotyping_note,
                payload.owner,
                source_record_id,
                now,
                now,
            ),
        )
        gene_id = get_or_create_gene_master(conn, payload.gene, source_record_id, now)
        allele_id = get_or_create_allele_master(
            conn,
            allele_symbol=payload.allele,
            gene_id=gene_id,
            source_record_id=source_record_id,
            now=now,
        )
        link_strain_allele_master(
            conn,
            strain_id=strain_id,
            gene_id=gene_id,
            allele_id=allele_id,
            source_record_id=source_record_id,
            now=now,
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "strain_created",
                strain_id,
                None,
                raw_payload,
                now,
            ),
        )

    return {
        "strain_id": strain_id,
        "strain_name": strain_name,
        "status": payload.status or "active",
        "source_record_id": source_record_id,
        "created_at": now,
    }


@app.get("/api/corrections")
def list_corrections() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT correction_id, entity_type, entity_id, field_name,
                   before_value, after_value, reason, source_record_id,
                   review_id, source_layer, evidence_reference_json,
                   correction_context_json, corrected_at
            FROM correction_log
            ORDER BY corrected_at DESC
            """
        ).fetchall()
    corrections = []
    for row in rows:
        payload = dict(row)
        payload["source_layer"] = payload.get("source_layer") or "review item"
        payload["evidence_reference"] = json_object(payload.pop("evidence_reference_json", "{}"))
        payload["correction_context"] = json_object(payload.pop("correction_context_json", "{}"))
        corrections.append(payload)
    return corrections


@app.get("/api/ui/action-log")
def ui_action_log(target_id: str = "", action_type: str = "", limit: int = 50) -> dict[str, Any]:
    clean_target_id = target_id.strip()
    clean_action_type = action_type.strip()
    bounded_limit = min(max(limit, 1), 200)
    clauses: list[str] = []
    params: list[Any] = []
    if clean_target_id:
        clauses.append("target_id = ?")
        params.append(clean_target_id)
    if clean_action_type:
        clauses.append("action_type = ?")
        params.append(clean_action_type)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT action_id, action_type, target_id, before_value, after_value,
                   performed_by, performed_role, created_at
            FROM action_log
            {where_clause}
            ORDER BY created_at DESC, action_id DESC
            LIMIT ?
            """,
            [*params, bounded_limit],
        ).fetchall()
        action_type_rows = conn.execute(
            """
            SELECT action_type, COUNT(*) AS count
            FROM action_log
            GROUP BY action_type
            ORDER BY count DESC, action_type
            """
        ).fetchall()
    actions = []
    for row in rows:
        before_value = row["before_value"] or ""
        after_value = row["after_value"] or ""
        actions.append(
            {
                "action_id": row["action_id"],
                "action_type": row["action_type"],
                "target_id": row["target_id"],
                "before_value": before_value,
                "after_value": after_value,
                "before": json_object(before_value),
                "after": json_object(after_value),
                "performed_by": row["performed_by"],
                "performed_role": row["performed_role"],
                "created_at": row["created_at"],
            }
        )
    return {
        "source_layer": "export or view",
        "filters": {
            "target_id": clean_target_id,
            "action_type": clean_action_type,
            "limit": bounded_limit,
        },
        "summary": {
            "returned_actions": len(actions),
            "available_action_types": len(action_type_rows),
        },
        "action_types": [dict(row) for row in action_type_rows],
        "actions": actions,
    }


def optional_existing_source_record_id(conn: Any, source_record_id: str | None) -> str | None:
    clean_id = str(source_record_id or "").strip()
    if not clean_id:
        return None
    exists = conn.execute(
        "SELECT 1 FROM source_record WHERE source_record_id = ?",
        (clean_id,),
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=400, detail="source_record_id does not exist.")
    return clean_id


def optional_existing_review_id(conn: Any, review_id: str | None) -> str | None:
    clean_id = str(review_id or "").strip()
    if not clean_id:
        return None
    exists = conn.execute(
        "SELECT 1 FROM review_queue WHERE review_id = ?",
        (clean_id,),
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=400, detail="review_id does not exist.")
    return clean_id


def correction_evidence_reference_json(source_record_id: str | None, review_id: str | None) -> str:
    return json.dumps(
        {
            "source_layer": "review item",
            "source_record_id": source_record_id or "",
            "review_id": review_id or "",
        },
        ensure_ascii=False,
    )


def correction_context_json(payload: CorrectionCreate) -> str:
    context = {
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "field_name": payload.field_name,
        "before_value": payload.before_value,
        "after_value": payload.after_value,
        "reason": payload.reason,
    }
    if payload.scoring_audit_status:
        context["scoring_audit_status"] = payload.scoring_audit_status
        context["scoring_audit_note"] = payload.scoring_audit_note
    return json.dumps(context, ensure_ascii=False)


def record_correction(conn: Any, payload: CorrectionCreate, corrected_at: str) -> str:
    correction_id = new_id("correction")
    source_record_id = optional_existing_source_record_id(conn, payload.source_record_id)
    review_id = optional_existing_review_id(conn, payload.review_id)
    conn.execute(
        """
        INSERT INTO correction_log
            (correction_id, entity_type, entity_id, field_name,
             before_value, after_value, reason, source_record_id,
             review_id, source_layer, evidence_reference_json,
             correction_context_json, corrected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            correction_id,
            payload.entity_type,
            payload.entity_id,
            payload.field_name,
            payload.before_value,
            payload.after_value,
            payload.reason,
            source_record_id,
            review_id,
            "review item",
            correction_evidence_reference_json(source_record_id, review_id),
            correction_context_json(payload),
            corrected_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("action"),
            "correction_applied",
            payload.entity_id,
            payload.before_value,
            payload.after_value,
            corrected_at,
        ),
    )
    return correction_id


def review_source_context(conn: Any, review_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT review.review_id, review.parse_id, review.severity, review.issue,
               review.current_value, review.suggested_value, review.review_reason,
               review.assigned_role, review.assigned_to, review.priority,
               review.status, review.created_at, review.resolved_at, review.resolution_note,
               parse.source_name, parse.photo_id, photo.original_filename,
               snapshot.card_snapshot_id, snapshot.card_type, snapshot.card_id_raw,
               snapshot.note_summary_json, snapshot.status AS snapshot_status
        FROM review_queue review
        LEFT JOIN parse_result parse ON parse.parse_id = review.parse_id
        LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
        LEFT JOIN card_snapshot snapshot ON snapshot.parse_id = review.parse_id
        WHERE review.review_id = ?
        """,
        (review_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    payload = dict(row)
    payload["note_summary"] = json_object(payload.pop("note_summary_json", "{}"))
    payload["image_url"] = f"/api/photos/{quote(payload['photo_id'])}/image" if payload.get("photo_id") else ""
    return payload


def review_item_audit_view(conn: Any, review_id: str) -> dict[str, Any]:
    review = review_source_context(conn, review_id)
    note_rows = conn.execute(
        """
        SELECT note_item_id, photo_id, parse_id, card_snapshot_id, card_type,
               line_number, raw_line_text, strike_status, parsed_type,
               interpreted_status, parsed_mouse_display_id, parsed_ear_label_raw,
               parsed_ear_label_code, parsed_ear_label_review_status,
               parsed_event_date, parsed_count, confidence, needs_review, created_at
        FROM card_note_item_log
        WHERE parse_id = ?
        ORDER BY line_number, created_at
        """,
        (review["parse_id"],),
    ).fetchall()
    correction_rows = conn.execute(
        """
        SELECT correction_id, entity_type, entity_id, field_name,
               before_value, after_value, reason, source_record_id,
               review_id, source_layer, evidence_reference_json,
               correction_context_json, corrected_at
        FROM correction_log
        WHERE review_id = ?
        ORDER BY corrected_at, correction_id
        """,
        (review_id,),
    ).fetchall()
    photo_evidence_rows = conn.execute(
        """
        SELECT evidence.source_photo_id, evidence.parse_id, evidence.note_item_id,
               evidence.card_type, evidence.evidence_kind, evidence.roi_label,
               evidence.observed_raw_text, evidence.ocr_text, evidence.parsed_value,
               evidence.confidence, evidence.needs_review, evidence.review_reason,
               evidence.status
        FROM review_evidence_link link
        JOIN photo_evidence_item evidence
          ON evidence.photo_evidence_id = link.photo_evidence_id
        WHERE link.review_id = ?
        ORDER BY evidence.evidence_kind, evidence.roi_label,
                 evidence.observed_raw_text, evidence.photo_evidence_id
        """,
        (review_id,),
    ).fetchall()
    action_rows = conn.execute(
        """
        SELECT action_id, action_type, target_id, before_value, after_value,
               performed_by, performed_role, created_at
        FROM action_log
        WHERE target_id = ?
           OR after_value LIKE ?
           OR before_value LIKE ?
        ORDER BY created_at, action_id
        """,
        (review_id, f"%{review_id}%", f"%{review_id}%"),
    ).fetchall()
    actions = []
    for action in action_rows:
        payload = dict(action)
        payload["before_value"] = json_object(payload.get("before_value"))
        payload["after_value"] = json_object(payload.get("after_value"))
        actions.append(payload)
    return {
        "source_layer": "export or view",
        "boundary": "export or view",
        "review": review,
        "note_items": [dict(row) for row in note_rows],
        "photo_evidence_items": [dict(row) for row in photo_evidence_rows],
        "corrections": [dict(row) for row in correction_rows],
        "actions": actions,
        "summary": {
            "note_item_count": len(note_rows),
            "photo_evidence_count": len(photo_evidence_rows),
            "correction_count": len(correction_rows),
            "action_count": len(actions),
            "has_photo": bool(review.get("photo_id")),
            "has_card_snapshot": bool(review.get("card_snapshot_id")),
        },
    }


def assistant_review_target_note_item_id(review: dict[str, Any], note_items: list[dict[str, Any]]) -> str:
    issue = str(review.get("issue") or "").strip()
    review_id = str(review.get("review_id") or "").strip()
    inferred_note_item_id = review_note_item_id(review_id)
    note_by_id = {str(item.get("note_item_id") or ""): item for item in note_items}
    if issue == "Ear label needs review":
        inferred_note = note_by_id.get(inferred_note_item_id)
        if inferred_note is not None and str(inferred_note.get("parsed_type") or "") == "mouse_item":
            return inferred_note_item_id
        for item in note_items:
            if (
                str(item.get("parsed_type") or "") == "mouse_item"
                and str(item.get("parsed_ear_label_review_status") or "") in {"check", "needs_review"}
            ):
                return str(item.get("note_item_id") or "")
    if issue == "Unlabeled numeric note needs review":
        inferred_note = note_by_id.get(inferred_note_item_id)
        if inferred_note is not None and str(inferred_note.get("parsed_type") or "") == "unlabeled_numeric_note":
            return inferred_note_item_id
        for item in note_items:
            if str(item.get("parsed_type") or "") == "unlabeled_numeric_note":
                return str(item.get("note_item_id") or "")
    return str(note_items[0].get("note_item_id") or "") if note_items else ""


def assistant_review_draft_from_audit(audit: dict[str, Any]) -> dict[str, Any]:
    review = audit["review"]
    note_items = audit.get("note_items", [])
    photo_evidence_items = audit.get("photo_evidence_items", [])
    target_note_item_id = assistant_review_target_note_item_id(review, note_items)
    evidence_lines = [
        str(item.get("raw_line_text") or "").strip()
        for item in sorted(note_items, key=lambda item: str(item.get("note_item_id") or "") != target_note_item_id)
        if str(item.get("raw_line_text") or "").strip()
    ]
    evidence_summary = " | ".join(evidence_lines[:5])
    if not evidence_summary:
        evidence_summary = review.get("suggested_value") or review.get("current_value") or review.get("review_reason") or ""
    suggested_value = str(review.get("suggested_value") or "").strip()
    current_value = str(review.get("current_value") or "").strip()
    issue = str(review.get("issue") or "").strip()
    review_type = "general_review"
    form_fill_policy = "draft_value_and_note"
    operator_note = "Use this as a local assistant draft. Do not resolve or write canonical state without operator approval."
    correction_field_name = "reviewed_value"
    extra_resolution_fields: dict[str, Any] = {}
    if issue == "Ear label needs review":
        review_type = "ear_label_review"
        form_fill_policy = "bounded_choice_only"
        operator_note = "Use this bounded ear-label draft only after comparing the source note and photo evidence."
        correction_field_name = "ear_label_code"
        extra_resolution_fields["ear_label_code"] = suggested_value
        extra_resolution_fields["note_item_id"] = target_note_item_id
    elif issue == "Unlabeled numeric note needs review":
        review_type = "unlabeled_numeric_note_review"
        form_fill_policy = "operator_choose_note_label"
        operator_note = "Assistant can summarize the numeric note, but the operator must choose whether it is a count, mouse ID, reviewed note, or ignored line."
        correction_field_name = "parsed_label"
        extra_resolution_fields["note_item_id"] = target_note_item_id
        extra_resolution_fields["note_label_decision"] = ""
        extra_resolution_fields["note_label_mouse_id"] = ""
        extra_resolution_fields["note_label_count"] = None
    resolution_note_parts = [
        "Assistant draft only; operator must compare against source evidence before resolving.",
        str(review.get("review_reason") or "").strip(),
    ]
    if evidence_summary:
        resolution_note_parts.append(f"Evidence summary: {evidence_summary}")
    resolution_note = " ".join(part for part in resolution_note_parts if part)
    return {
        "source_layer": "review item",
        "boundary": "review item",
        "draft_kind": "assistant_review_draft",
        "external_payload_policy": "local_only_until_approved",
        "writes_canonical_state": False,
        "requires_operator_approval": True,
        "review": {
            "review_id": review.get("review_id") or "",
            "parse_id": review.get("parse_id") or "",
            "status": review.get("status") or "",
            "issue": review.get("issue") or "",
            "severity": review.get("severity") or "",
        },
        "evidence_refs": {
            "source_photo_id": review.get("photo_id") or "",
            "source_photo_filename": review.get("original_filename") or "",
            "parse_id": review.get("parse_id") or "",
            "card_snapshot_id": review.get("card_snapshot_id") or "",
            "note_item_ids": [item.get("note_item_id") for item in note_items if item.get("note_item_id")],
            "photo_evidence_count": len(photo_evidence_items),
        },
        "draft": {
            "review_type": review_type,
            "form_fill_policy": form_fill_policy,
            "evidence_summary": evidence_summary,
            "operator_note": operator_note,
            "resolution_payload": {
                "resolution_note": resolution_note,
                "resolved_value": suggested_value or current_value,
                "legacy_decision": "resolve",
                "correction_entity_type": "review_item",
                "correction_entity_id": review.get("review_id") or "",
                "correction_field_name": correction_field_name,
                "correction_before_value": current_value,
                "correction_after_value": suggested_value or current_value,
                "correction_source_record_id": None,
                **extra_resolution_fields,
            },
        },
    }


def create_canonical_candidate_draft(
    conn: Any,
    *,
    review_id: str,
    parse_id: str,
    created_at: str,
) -> str | None:
    existing = conn.execute(
        """
        SELECT candidate_id
        FROM canonical_candidate
        WHERE review_id = ?
        """,
        (review_id,),
    ).fetchone()
    if existing is not None:
        return existing["candidate_id"]

    review = conn.execute(
        """
        SELECT review_id, current_value, suggested_value, review_reason
        FROM review_queue
        WHERE review_id = ?
        """,
        (review_id,),
    ).fetchone()
    if review is None:
        return None

    current_value = json_object(review["current_value"])
    suggested_value = json_object(review["suggested_value"])
    manual = current_value.get("manual") if isinstance(current_value.get("manual"), dict) else current_value
    legacy = current_value.get("legacy") if isinstance(current_value.get("legacy"), dict) else suggested_value
    legacy_summary = legacy.get("summary") if isinstance(legacy.get("summary"), dict) else {}
    proposed = {
        "display_id": manual.get("display_id") or legacy_summary.get("display_id") or "",
        "strain": manual.get("strain") or legacy_summary.get("strain") or "",
        "sex": manual.get("sex") or legacy_summary.get("sex") or "",
        "card_id_raw": manual.get("id") or legacy_summary.get("display_id") or "",
        "dob": manual.get("dob") or legacy_summary.get("dob") or "",
        "count": manual.get("count_raw") or manual.get("count") or legacy_summary.get("count_raw") or legacy_summary.get("count") or "",
        "manual": manual,
        "legacy": legacy,
        "review_reason": review["review_reason"],
        "boundary": "review item",
        "note": "Draft only. Does not write mouse_master or other canonical colony tables.",
    }
    candidate_id = new_id("candidate")
    conn.execute(
        """
        INSERT INTO canonical_candidate
            (candidate_id, review_id, parse_id, legacy_row_id, proposed_mouse_display_id,
             proposed_strain, proposed_dob, proposed_count, candidate_payload, status,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            review_id,
            parse_id,
            str(legacy.get("legacy_row_id") or ""),
            str(proposed["display_id"] or ""),
            str(proposed["strain"] or ""),
            str(proposed["dob"] or ""),
            str(proposed["count"] or ""),
            json.dumps(proposed, ensure_ascii=False),
            "draft",
            created_at,
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("action"),
            "canonical_candidate_draft_created",
            candidate_id,
            None,
            json.dumps({"review_id": review_id, "parse_id": parse_id, "boundary": "review item"}, ensure_ascii=False),
            created_at,
        ),
    )
    return candidate_id


def review_note_item_id(review_id: str, payload_note_item_id: str = "") -> str:
    if payload_note_item_id.strip():
        return payload_note_item_id.strip()
    for prefix in ("review_unlabeled_numeric_", "review_ear_"):
        if review_id.startswith(prefix):
            return review_id[len(prefix) :]
    return ""


def update_note_item_label(conn: Any, note_item_id: str, after: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE card_note_item_log
        SET parsed_type = ?,
            interpreted_status = ?,
            parsed_mouse_display_id = ?,
            parsed_ear_label_raw = ?,
            parsed_ear_label_code = ?,
            parsed_ear_label_confidence = ?,
            parsed_ear_label_review_status = ?,
            parsed_event_date = ?,
            parsed_count = ?,
            confidence = ?,
            needs_review = ?
        WHERE note_item_id = ?
        """,
        (
            after["parsed_type"],
            after["interpreted_status"],
            after["parsed_mouse_display_id"],
            after["parsed_ear_label_raw"],
            after["parsed_ear_label_code"],
            after["parsed_ear_label_confidence"],
            after["parsed_ear_label_review_status"],
            after["parsed_event_date"],
            after["parsed_count"],
            after["confidence"],
            after["needs_review"],
            note_item_id,
        ),
    )


def log_note_label_review_action(
    conn: Any,
    *,
    action_type: str,
    note_item_id: str,
    before: dict[str, Any],
    review_id: str,
    decision: str,
    after: dict[str, Any],
    resolved_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("action"),
            action_type,
            note_item_id,
            json.dumps(before, ensure_ascii=False),
            json.dumps(
                {
                    "review_id": review_id,
                    "decision": decision,
                    "after": after,
                    "boundary": "parsed or intermediate result",
                },
                ensure_ascii=False,
            ),
            resolved_at,
        ),
    )


def resolve_note_label_correction(
    conn: Any,
    *,
    review_id: str,
    parse_id: str,
    payload: ReviewResolutionCreate,
    resolved_at: str,
) -> dict[str, Any] | None:
    decision = payload.note_label_decision.strip()
    is_grouped_numeric_review_id = review_id == f"review_unlabeled_numeric_{parse_id}"
    if is_grouped_numeric_review_id and not decision:
        raise HTTPException(
            status_code=400,
            detail="note_label_decision is required for grouped numeric note reviews.",
        )
    if not decision:
        return None
    allowed_decisions = {"mouse_item", "count_note", "reviewed_note", "ignored_note"}
    if decision not in allowed_decisions:
        raise HTTPException(
            status_code=400,
            detail="note_label_decision must be mouse_item, count_note, reviewed_note, or ignored_note.",
        )

    note_item_id = review_note_item_id(review_id, payload.note_item_id)
    if is_grouped_numeric_review_id and not payload.note_item_id.strip():
        first_numeric_note = conn.execute(
            """
            SELECT note_item_id
            FROM card_note_item_log
            WHERE parse_id = ?
              AND parsed_type = 'unlabeled_numeric_note'
            ORDER BY line_number
            LIMIT 1
            """,
            (parse_id,),
        ).fetchone()
        note_item_id = first_numeric_note["note_item_id"] if first_numeric_note is not None else ""
    if not note_item_id:
        raise HTTPException(status_code=400, detail="note_item_id is required for note label review corrections.")
    note = conn.execute(
        """
        SELECT note_item_id, photo_id, parse_id, card_snapshot_id, card_type, line_number, raw_line_text, strike_status,
               parsed_type, interpreted_status, parsed_mouse_display_id,
               parsed_ear_label_raw, parsed_ear_label_code, parsed_ear_label_confidence,
               parsed_ear_label_review_status, parsed_event_date, parsed_count,
               confidence, needs_review
        FROM card_note_item_log
        WHERE note_item_id = ?
        """,
        (note_item_id,),
    ).fetchone()
    if note is None:
        raise HTTPException(status_code=404, detail="Note item not found for label review correction.")
    if note["parse_id"] != parse_id:
        raise HTTPException(status_code=409, detail="Review item and note item parse IDs do not match.")
    is_grouped_numeric_review = (
        is_grouped_numeric_review_id
        and note["parsed_type"] == "unlabeled_numeric_note"
        and decision in {"count_note", "reviewed_note", "ignored_note"}
    )

    before = dict(note)
    after = dict(before)
    after.update(
        {
            "parsed_type": decision,
            "interpreted_status": payload.note_label_interpreted_status.strip() or decision,
            "parsed_mouse_display_id": None,
            "parsed_ear_label_raw": None,
            "parsed_ear_label_code": None,
            "parsed_ear_label_confidence": None,
            "parsed_ear_label_review_status": "user_corrected",
            "parsed_event_date": None,
            "parsed_count": None,
            "confidence": 1.0,
            "needs_review": 0,
        }
    )
    if decision == "mouse_item":
        display_id = (payload.note_label_mouse_id or payload.resolved_value).strip()
        if not display_id:
            raise HTTPException(status_code=400, detail="note_label_mouse_id is required when decision is mouse_item.")
        after["parsed_mouse_display_id"] = display_id
        after["interpreted_status"] = payload.note_label_interpreted_status.strip() or interpreted_status(
            note["card_type"],
            note["strike_status"],
        )
    elif decision == "count_note":
        count_value = payload.note_label_count
        if is_grouped_numeric_review:
            count_value = note["parsed_count"]
        if count_value is None:
            match = re.search(r"\d+", payload.resolved_value or note["raw_line_text"] or "")
            if match:
                count_value = int(match.group(0))
        if count_value is None:
            raise HTTPException(status_code=400, detail="note_label_count is required when decision is count_note.")
        after["parsed_count"] = count_value
        after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "reviewed_count"
    elif decision == "reviewed_note":
        after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "reviewed_note"
    elif decision == "ignored_note":
        after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "ignored"

    conn.execute(
        """
        UPDATE card_note_item_log
        SET parsed_type = ?,
            interpreted_status = ?,
            parsed_mouse_display_id = ?,
            parsed_ear_label_raw = ?,
            parsed_ear_label_code = ?,
            parsed_ear_label_confidence = ?,
            parsed_ear_label_review_status = ?,
            parsed_event_date = ?,
            parsed_count = ?,
            confidence = ?,
            needs_review = ?
        WHERE note_item_id = ?
        """,
        (
            after["parsed_type"],
            after["interpreted_status"],
            after["parsed_mouse_display_id"],
            after["parsed_ear_label_raw"],
            after["parsed_ear_label_code"],
            after["parsed_ear_label_confidence"],
            after["parsed_ear_label_review_status"],
            after["parsed_event_date"],
            after["parsed_count"],
            after["confidence"],
            after["needs_review"],
            note_item_id,
        ),
    )
    correction_id = record_correction(
        conn,
        CorrectionCreate(
            entity_type="note_item",
            entity_id=note_item_id,
            field_name="parsed_label",
            before_value=json.dumps(before, ensure_ascii=False),
            after_value=json.dumps(after, ensure_ascii=False),
            reason=payload.resolution_note,
            review_id=review_id,
        ),
        resolved_at,
    )
    log_note_label_review_action(
        conn,
        action_type="note_label_reviewed",
        note_item_id=note_item_id,
        before=before,
        review_id=review_id,
        decision=decision,
        after=after,
        resolved_at=resolved_at,
    )
    grouped_note_item_ids = [note_item_id]
    if is_grouped_numeric_review:
        sibling_rows = conn.execute(
            """
            SELECT note_item_id, photo_id, parse_id, card_snapshot_id, card_type, line_number, raw_line_text, strike_status,
                   parsed_type, interpreted_status, parsed_mouse_display_id,
                   parsed_ear_label_raw, parsed_ear_label_code, parsed_ear_label_confidence,
                   parsed_ear_label_review_status, parsed_event_date, parsed_count,
                   confidence, needs_review
            FROM card_note_item_log
            WHERE parse_id = ?
              AND parsed_type = 'unlabeled_numeric_note'
              AND note_item_id <> ?
            ORDER BY line_number
            """,
            (parse_id, note_item_id),
        ).fetchall()
        for sibling in sibling_rows:
            sibling_before = dict(sibling)
            sibling_after = dict(sibling_before)
            sibling_after.update(
                {
                    "parsed_type": decision,
                    "interpreted_status": payload.note_label_interpreted_status.strip() or (
                        "reviewed_count" if decision == "count_note" else decision.replace("_note", "")
                    ),
                    "parsed_mouse_display_id": None,
                    "parsed_ear_label_raw": None,
                    "parsed_ear_label_code": None,
                    "parsed_ear_label_confidence": None,
                    "parsed_ear_label_review_status": "user_corrected",
                    "parsed_event_date": None,
                    "parsed_count": None,
                    "confidence": 1.0,
                    "needs_review": 0,
                }
            )
            if decision == "count_note":
                count_value = sibling["parsed_count"]
                if count_value is None:
                    match = re.search(r"\d+", sibling["raw_line_text"] or "")
                    count_value = int(match.group(0)) if match else 0
                sibling_after["parsed_count"] = count_value
                sibling_after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "reviewed_count"
            elif decision == "reviewed_note":
                sibling_after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "reviewed_note"
            elif decision == "ignored_note":
                sibling_after["interpreted_status"] = payload.note_label_interpreted_status.strip() or "ignored"
            update_note_item_label(conn, sibling["note_item_id"], sibling_after)
            grouped_note_item_ids.append(str(sibling["note_item_id"]))
            log_note_label_review_action(
                conn,
                action_type="grouped_note_label_reviewed",
                note_item_id=sibling["note_item_id"],
                before=sibling_before,
                review_id=review_id,
                decision=decision,
                after=sibling_after,
                resolved_at=resolved_at,
            )
    snapshot_update = refresh_card_snapshot_summary(conn, str(note["card_snapshot_id"] or ""), resolved_at)
    return {
        "note_item_id": note_item_id,
        "grouped_note_item_ids": grouped_note_item_ids,
        "decision": decision,
        "correction_id": correction_id,
        "card_snapshot_update": snapshot_update,
        "boundary": "parsed or intermediate result",
    }


def resolve_ear_label_correction(
    conn: Any,
    *,
    review_id: str,
    parse_id: str,
    payload: ReviewResolutionCreate,
    resolved_at: str,
) -> dict[str, Any] | None:
    selected_code = payload.ear_label_code.strip().upper()
    if not selected_code:
        return None
    allowed_codes = {
        "R_PRIME",
        "L_PRIME",
        "R_CIRCLE",
        "L_CIRCLE",
        "R_PRIME_L_PRIME",
        "R_PRIME_L_CIRCLE",
        "R_CIRCLE_L_PRIME",
        "R_CIRCLE_L_CIRCLE",
        "NONE",
        "UNREADABLE",
    }
    if selected_code not in allowed_codes:
        raise HTTPException(status_code=400, detail="ear_label_code is not an allowed review choice.")

    note_item_id = review_note_item_id(review_id, payload.note_item_id)
    if not note_item_id:
        raise HTTPException(status_code=400, detail="note_item_id is required for ear label review corrections.")
    note = conn.execute(
        """
        SELECT note_item_id, photo_id, parse_id, card_snapshot_id, card_type, line_number, raw_line_text, strike_status,
               parsed_type, interpreted_status, parsed_mouse_display_id,
               parsed_ear_label_raw, parsed_ear_label_code, parsed_ear_label_confidence,
               parsed_ear_label_review_status, parsed_event_date, parsed_count,
               confidence, needs_review
        FROM card_note_item_log
        WHERE note_item_id = ?
        """,
        (note_item_id,),
    ).fetchone()
    if note is None:
        raise HTTPException(status_code=404, detail="Note item not found for ear label review correction.")
    if note["parse_id"] != parse_id:
        raise HTTPException(status_code=409, detail="Review item and note item parse IDs do not match.")
    if note["parsed_type"] != "mouse_item":
        raise HTTPException(status_code=400, detail="Ear label review can only update mouse note items.")

    before = dict(note)
    after = dict(before)
    after.update(
        {
            "parsed_ear_label_code": None if selected_code == "UNREADABLE" else selected_code,
            "parsed_ear_label_confidence": 1.0,
            "parsed_ear_label_review_status": "reviewed_unreadable" if selected_code == "UNREADABLE" else "user_corrected",
            "confidence": 1.0,
            "needs_review": 0,
        }
    )
    update_note_item_label(conn, note_item_id, after)
    log_note_label_review_action(
        conn,
        action_type="ear_label_reviewed",
        note_item_id=note_item_id,
        before=before,
        review_id=review_id,
        decision=selected_code,
        after=after,
        resolved_at=resolved_at,
    )
    snapshot_update = refresh_card_snapshot_summary(conn, str(note["card_snapshot_id"] or ""), resolved_at)
    return {
        "note_item_id": note_item_id,
        "ear_label_code": after["parsed_ear_label_code"],
        "ear_label_review_status": after["parsed_ear_label_review_status"],
        "card_snapshot_update": snapshot_update,
        "boundary": "parsed or intermediate result",
    }


@app.post("/api/corrections")
def create_correction(payload: CorrectionCreate) -> dict[str, Any]:
    corrected_at = utc_now()
    with connection() as conn:
        correction_id = record_correction(conn, payload, corrected_at)

    return {"correction_id": correction_id, "corrected_at": corrected_at}


@app.get("/api/canonical-candidates")
def list_canonical_candidates() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT candidate_id, review_id, parse_id, legacy_row_id,
                   proposed_mouse_display_id, proposed_strain, proposed_dob,
                   proposed_count, candidate_payload, status, created_at, updated_at
            FROM canonical_candidate
            ORDER BY created_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["candidate_payload"] = json_object(payload.get("candidate_payload"))
        payload["boundary"] = "review item"
        payload["source_layer"] = "review item"
        result.append(payload)
    return result


def safe_artifact_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return slug.strip("._") or "artifact"


def unique_nonempty(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def persist_proposed_changeset_artifact(
    preview: dict[str, Any],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    created_at = created_at or utc_now()
    candidate_id = str(preview.get("candidate_id") or "")
    artifact_id = f"proposed_changeset_{safe_artifact_slug(candidate_id)}"
    proposed_mice = preview.get("proposed_mice") if isinstance(preview.get("proposed_mice"), list) else []
    source_photo_ids = unique_nonempty([item.get("source_photo_id") for item in proposed_mice if isinstance(item, dict)])
    note_item_ids = unique_nonempty([item.get("source_note_item_id") for item in proposed_mice if isinstance(item, dict)])
    card_snapshot_ids = unique_nonempty([item.get("card_snapshot_id") for item in proposed_mice if isinstance(item, dict)])
    legacy_row_ids = unique_nonempty([preview.get("legacy_row_id")])

    proposed_writes = []
    for item in proposed_mice:
        if not isinstance(item, dict):
            continue
        evidence_refs = unique_nonempty(
            [item.get("source_note_item_id"), item.get("source_photo_id"), item.get("card_snapshot_id")]
        )
        operation = "insert" if item.get("will_create_mouse") else "update"
        proposed_writes.append(
            {
                "target_layer": "canonical structured state",
                "target_table": "mouse_master",
                "target_id": item.get("mouse_id") or "",
                "operation": operation,
                "field": "",
                "before_value": None,
                "after_value": {
                    "display_id": item.get("display_id") or "",
                    "status": item.get("status") or "",
                    "source_note_item_id": item.get("source_note_item_id") or "",
                    "source_photo_id": item.get("source_photo_id") or "",
                },
                "risk": "medium",
                "evidence_refs": evidence_refs,
                "review_required": False,
            }
        )
        if item.get("will_create_event"):
            proposed_writes.append(
                {
                    "target_layer": "canonical structured state",
                    "target_table": "mouse_event",
                    "target_id": "",
                    "operation": "event_insert",
                    "field": "",
                    "before_value": None,
                    "after_value": {
                        "event_type": "canonical_candidate_applied",
                        "mouse_id": item.get("mouse_id") or "",
                        "related_entity_type": "canonical_candidate",
                        "related_entity_id": candidate_id,
                    },
                    "risk": "medium",
                    "evidence_refs": evidence_refs,
                    "review_required": False,
                }
            )

    blockers = [
        {
            "check_key": "canonical_apply_preview_blocker",
            "severity": "high",
            "message": str(blocker),
            "evidence_refs": [],
        }
        for blocker in preview.get("blockers", [])
    ]
    artifact = {
        "artifact_id": artifact_id,
        "artifact_type": "proposed_changeset",
        "source_layer": "export or view",
        "created_at": created_at,
        "created_by": "local_user",
        "status": "blocked" if blockers else "draft",
        "canonical_candidate_id": candidate_id,
        "review_id": str(preview.get("review_id") or ""),
        "parse_id": str(preview.get("parse_id") or ""),
        "source_refs": {
            "photo_ids": source_photo_ids,
            "note_item_ids": note_item_ids,
            "card_snapshot_ids": card_snapshot_ids,
            "legacy_row_ids": legacy_row_ids,
            "source_record_ids": [],
        },
        "proposed_writes": proposed_writes,
        "blockers": blockers,
        "validation_report_id": "",
        "notes": "Generated from canonical candidate apply preview. This artifact is not canonical state.",
    }
    target_dir = ARTIFACT_ROOT / "proposed_changesets"
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / f"{safe_artifact_slug(candidate_id)}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "artifact_id": artifact_id,
        "artifact_path": str(artifact_path),
        "artifact": artifact,
        "boundary": "export or view",
    }


def validation_report_status(checks: list[dict[str, Any]]) -> str:
    if any(check.get("status") == "blocked" for check in checks):
        return "blocked"
    if any(check.get("status") == "warning" for check in checks):
        return "warning"
    return "pass"


def build_canonical_apply_validation_report(
    preview: dict[str, Any],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    created_at = created_at or utc_now()
    candidate_id = str(preview.get("candidate_id") or "")
    proposed_mice = preview.get("proposed_mice") if isinstance(preview.get("proposed_mice"), list) else []
    duplicate_risks = preview.get("duplicate_risks") if isinstance(preview.get("duplicate_risks"), list) else []
    source_photo_ids = unique_nonempty([item.get("source_photo_id") for item in proposed_mice if isinstance(item, dict)])
    note_item_ids = unique_nonempty([item.get("source_note_item_id") for item in proposed_mice if isinstance(item, dict)])
    mouse_ids = unique_nonempty([item.get("mouse_id") for item in proposed_mice if isinstance(item, dict)])
    missing_trace = [
        item
        for item in proposed_mice
        if isinstance(item, dict) and (not item.get("source_note_item_id") or not item.get("source_photo_id"))
    ]
    checks = [
        {
            "check_key": "duplicate_active_mouse_id",
            "status": "blocked" if duplicate_risks else "pass",
            "severity": "high" if duplicate_risks else "low",
            "message": (
                "Active duplicate display IDs must be resolved before canonical apply."
                if duplicate_risks
                else "No active duplicate mouse IDs found for proposed mice."
            ),
            "target_refs": unique_nonempty([risk.get("candidate_mouse_id") for risk in duplicate_risks if isinstance(risk, dict)]),
            "evidence_refs": unique_nonempty([risk.get("existing_source_note_item_id") for risk in duplicate_risks if isinstance(risk, dict)]),
            "recommended_action": "Resolve duplicate active mouse IDs in Review Queue before applying.",
        },
        {
            "check_key": "missing_source_trace",
            "status": "blocked" if missing_trace or not proposed_mice else "pass",
            "severity": "high" if missing_trace or not proposed_mice else "low",
            "message": (
                "One or more proposed mouse writes are missing source photo or note-line trace."
                if missing_trace
                else (
                    "Candidate has no proposed mouse writes with source traces."
                    if not proposed_mice
                    else "All proposed mouse writes keep source photo and note-line trace."
                )
            ),
            "target_refs": unique_nonempty([item.get("mouse_id") for item in missing_trace if isinstance(item, dict)]),
            "evidence_refs": unique_nonempty(source_photo_ids + note_item_ids),
            "recommended_action": "Return to photo review or candidate mapping before applying.",
        },
    ]
    report = {
        "report_id": f"validation_report_{safe_artifact_slug(candidate_id)}",
        "artifact_type": "validation_report",
        "source_layer": "export or view",
        "created_at": created_at,
        "created_by": "local_user",
        "scope": "canonical_apply",
        "status": validation_report_status(checks),
        "related_artifact_id": f"proposed_changeset_{safe_artifact_slug(candidate_id)}",
        "canonical_candidate_id": candidate_id,
        "export_id": "",
        "state_watermark": str(preview.get("candidate_status") or ""),
        "source_refs": {
            "photo_ids": source_photo_ids,
            "parse_ids": unique_nonempty([preview.get("parse_id")]),
            "review_ids": unique_nonempty([preview.get("review_id")]),
            "note_item_ids": note_item_ids,
            "mouse_ids": mouse_ids,
        },
        "checks": checks,
        "summary": "Canonical apply validation report generated from apply preview; no canonical state was written.",
    }
    return report


def persist_validation_report_artifact(report: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(report.get("canonical_candidate_id") or report.get("report_id") or "candidate")
    target_dir = ARTIFACT_ROOT / "validation_reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / f"{safe_artifact_slug(candidate_id)}.json"
    artifact_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "report_id": report["report_id"],
        "artifact_path": str(artifact_path),
        "artifact": report,
        "boundary": "export or view",
    }


def split_export_ref_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return unique_nonempty(value)
    return unique_nonempty(str(value or "").replace(";", ",").split(","))


def export_row_trace_refs(row: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "photo_ids": split_export_ref_values(row.get("source_photo_ids") or row.get("source_photo_id")),
        "note_item_ids": split_export_ref_values(row.get("source_note_item_ids") or row.get("source_note_item_id")),
        "source_record_ids": split_export_ref_values(row.get("source_record_ids") or row.get("source_record_id")),
    }


def _export_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _export_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _export_p_count(value: Any) -> int | None:
    match = re.search(r"(?<!\d)(\d+)\s*p\b", str(value or ""), flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _animal_sheet_check_refs(row: dict[str, Any]) -> dict[str, list[str]]:
    refs = export_row_trace_refs(row)
    return {
        "photo_ids": refs["photo_ids"],
        "note_item_ids": refs["note_item_ids"],
        "source_record_ids": refs["source_record_ids"],
        "mating_ids": split_export_ref_values(row.get("mating_id")),
        "litter_ids": split_export_ref_values(row.get("litter_id")),
    }


def validate_animal_sheet_litter_date_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("litter_id") or "").strip():
            continue
        refs = _animal_sheet_check_refs(row)
        target_refs = unique_nonempty(refs["litter_ids"] + refs["mating_ids"])
        evidence_refs = unique_nonempty(
            refs["photo_ids"] + refs["note_item_ids"] + refs["source_record_ids"]
        )
        mating_date = _export_iso_date(row.get("mating_start_date"))
        birth_date = _export_iso_date(row.get("litter_birth_date") or row.get("dob"))
        if mating_date and birth_date and birth_date < mating_date:
            checks.append(
                {
                    "validator_check_key": "litter_date_before_mating",
                    "validation_report_check_key": "impossible_date",
                    "status": "blocked",
                    "severity": "high",
                    "message": "Animal sheet litter birth date is earlier than the mating start date.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Confirm the litter date from source photo, note line, or imported row before final animal sheet export.",
                    "source_refs": refs,
                }
            )
        weaning_date = _export_iso_date(row.get("weaning_date"))
        if birth_date and weaning_date and weaning_date < birth_date:
            checks.append(
                {
                    "validator_check_key": "weaning_date_before_birth",
                    "validation_report_check_key": "impossible_date",
                    "status": "blocked",
                    "severity": "high",
                    "message": "Animal sheet weaning/separation date is earlier than the litter birth date.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Confirm separation or weaning date before final animal sheet export.",
                    "source_refs": refs,
                }
            )
        born = _export_int(row.get("number_born"))
        weaned = _export_int(row.get("number_weaned"))
        if born is not None and weaned is not None and weaned > born:
            checks.append(
                {
                    "validator_check_key": "weaned_count_exceeds_born",
                    "validation_report_check_key": "count_mismatch",
                    "status": "blocked",
                    "severity": "high",
                    "message": "Animal sheet weaned/separated count is greater than the recorded pup count.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Review pup count and separation count before final animal sheet export.",
                    "source_refs": refs,
                }
            )
        pubs_count = _export_p_count(row.get("pubs"))
        if born is not None and pubs_count is not None and pubs_count != born:
            checks.append(
                {
                    "validator_check_key": "pubs_count_conflicts_with_number_born",
                    "validation_report_check_key": "count_mismatch",
                    "status": "blocked",
                    "severity": "high",
                    "message": "Animal sheet Pubs text count conflicts with the accepted litter pup count.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Review the Pubs text and accepted litter count before final animal sheet export.",
                    "source_refs": refs,
                }
            )
        date_review_status = str(row.get("litter_birth_date_review_status") or "").strip().lower()
        if birth_date and date_review_status in {"check", "needs_review"}:
            checks.append(
                {
                    "validator_check_key": "ambiguous_litter_date_normalization",
                    "validation_report_check_key": "impossible_date",
                    "status": "warning",
                    "severity": "medium",
                    "message": "Animal sheet litter date has a normalized value but remains marked for review.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Review ambiguous litter date evidence before relying on the animal sheet row.",
                    "source_refs": refs,
                }
            )
        if refs["source_record_ids"] and not unique_nonempty(refs["photo_ids"] + refs["note_item_ids"]):
            checks.append(
                {
                    "validator_check_key": "litter_source_record_without_photo_or_note",
                    "validation_report_check_key": "missing_source_trace",
                    "status": "warning",
                    "severity": "medium",
                    "message": "Animal sheet litter row has source-record trace but no source photo or note-line trace.",
                    "target_refs": target_refs,
                    "evidence_refs": evidence_refs,
                    "recommended_action": "Review source workbook/manual row and attach photo or note evidence when available.",
                    "source_refs": refs,
                }
            )
    blocked_count = sum(1 for check in checks if check["status"] == "blocked")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    return {
        "source_layer": "export or view",
        "status": "blocked" if blocked_count else ("warning" if warning_count else "pass"),
        "blocked_count": blocked_count,
        "warning_count": warning_count,
        "checks": checks,
        "review_item_candidates": [],
    }


def build_export_validation_report(
    preview: dict[str, Any],
    *,
    export_type: str,
    query: str = "",
    filename: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    created_at = created_at or utc_now()
    review_blockers = preview.get("review_blockers") if isinstance(preview.get("review_blockers"), list) else []
    focus_review_count = int(
        preview.get("focus_review_blocker_items", preview.get("blocked_review_items") or len(review_blockers) or 0)
        or 0
    )
    export_rows = []
    trace_rows = []
    if export_type == "animal_sheet_xlsx":
        export_rows = preview.get("animal_sheet_rows") if isinstance(preview.get("animal_sheet_rows"), list) else []
        trace_rows = export_rows
    elif export_type == "separation_xlsx":
        export_rows = preview.get("separation_rows") if isinstance(preview.get("separation_rows"), list) else []
        trace_rows = preview.get("preview_rows") if isinstance(preview.get("preview_rows"), list) else export_rows
    else:
        export_rows = preview.get("preview_rows") if isinstance(preview.get("preview_rows"), list) else []
        trace_rows = export_rows

    photo_ids: list[str] = []
    note_item_ids: list[str] = []
    source_record_ids: list[str] = []
    mouse_ids: list[str] = []
    rows_missing_trace: list[str] = []
    for row in trace_rows:
        if not isinstance(row, dict):
            continue
        refs = export_row_trace_refs(row)
        photo_ids.extend(refs["photo_ids"])
        note_item_ids.extend(refs["note_item_ids"])
        source_record_ids.extend(refs["source_record_ids"])
        row_mouse_ids = split_export_ref_values(row.get("mouse_id"))
        mouse_ids.extend(row_mouse_ids)
        if not unique_nonempty(refs["photo_ids"] + refs["note_item_ids"] + refs["source_record_ids"]):
            rows_missing_trace.extend(row_mouse_ids or split_export_ref_values(row.get("display_id")) or ["unidentified_export_row"])
    review_ids = unique_nonempty(
        [item.get("review_id") for item in review_blockers if isinstance(item, dict)]
    )
    trace_evidence_refs = unique_nonempty(photo_ids + note_item_ids + source_record_ids)
    checks = [
        {
            "check_key": "open_focus_review_blocker",
            "status": "blocked" if focus_review_count else "pass",
            "severity": "high" if focus_review_count else "low",
            "message": (
                f"{focus_review_count} Focus Review blocker(s) remain open before final export."
                if focus_review_count
                else "No Focus Review blockers remain for final export."
            ),
            "target_refs": review_ids,
            "evidence_refs": review_ids,
            "recommended_action": "Resolve Focus Review blockers before generating final export.",
        },
        {
            "check_key": "missing_source_trace",
            "status": "warning" if rows_missing_trace else "pass",
            "severity": "medium" if rows_missing_trace else "low",
            "message": (
                f"{len(rows_missing_trace)} export row(s) are missing row-level source trace."
                if rows_missing_trace
                else "Export preview rows include source trace or no rows are present."
            ),
            "target_refs": unique_nonempty(rows_missing_trace),
            "evidence_refs": trace_evidence_refs,
            "recommended_action": "Review export trace sheet before handoff.",
        },
    ]
    if export_type == "animal_sheet_xlsx":
        litter_validation = preview.get("animal_sheet_litter_validation")
        if isinstance(litter_validation, dict):
            for check in litter_validation.get("checks", []):
                if not isinstance(check, dict):
                    continue
                report_key = str(check.get("validation_report_check_key") or "")
                if report_key not in {
                    "impossible_date",
                    "count_mismatch",
                    "missing_source_trace",
                    "open_focus_review_blocker",
                }:
                    report_key = "open_focus_review_blocker"
                checks.append(
                    {
                        "check_key": report_key,
                        "status": str(check.get("status") or "warning"),
                        "severity": str(check.get("severity") or "medium"),
                        "message": f"{check.get('validator_check_key')}: {check.get('message')}",
                        "target_refs": unique_nonempty(check.get("target_refs", [])),
                        "evidence_refs": unique_nonempty(check.get("evidence_refs", [])),
                        "recommended_action": str(
                            check.get("recommended_action")
                            or "Review animal sheet litter/date/count conflict before export."
                        ),
                    }
                )
    report_id = f"validation_report_export_{safe_artifact_slug(export_type)}_{safe_artifact_slug(query or filename or 'all')}"
    return {
        "report_id": report_id,
        "artifact_type": "validation_report",
        "source_layer": "export or view",
        "created_at": created_at,
        "created_by": "local_user",
        "scope": "export",
        "status": validation_report_status(checks),
        "related_artifact_id": "",
        "canonical_candidate_id": "",
        "export_id": "",
        "state_watermark": str(preview.get("latest_data_change_at") or ""),
        "source_refs": {
            "photo_ids": unique_nonempty(photo_ids),
            "parse_ids": [],
            "review_ids": review_ids,
            "note_item_ids": unique_nonempty(note_item_ids),
            "source_record_ids": unique_nonempty(source_record_ids),
            "mouse_ids": unique_nonempty(mouse_ids),
        },
        "checks": checks,
        "summary": (
            f"Export validation report for {export_type}; "
            f"query={query.strip() or 'all'}, filename={filename or 'not selected'}."
        ),
    }


def build_export_manifest(
    *,
    export_type: str,
    filename: str,
    query: str,
    status: str,
    row_count: int,
    blocked_review_count: int,
    validation_report: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    created_at = created_at or utc_now()
    report_artifact = validation_report.get("artifact") if isinstance(validation_report.get("artifact"), dict) else {}
    source_refs = report_artifact.get("source_refs") if isinstance(report_artifact.get("source_refs"), dict) else {}
    manifest_id = (
        f"export_manifest_{safe_artifact_slug(export_type)}_"
        f"{safe_artifact_slug(query or filename or created_at)}"
    )
    return {
        "manifest_id": manifest_id,
        "artifact_type": "export_manifest",
        "source_layer": "export or view",
        "created_at": created_at,
        "created_by": "local_user",
        "export_type": export_type,
        "filename": filename,
        "query": query.strip(),
        "status": status,
        "row_count": int(row_count),
        "blocked_review_count": int(blocked_review_count),
        "validation_report_id": str(validation_report.get("report_id") or report_artifact.get("report_id") or ""),
        "validation_report_path": str(validation_report.get("artifact_path") or ""),
        "state_watermark": str(report_artifact.get("state_watermark") or ""),
        "source_refs": {
            "photo_ids": unique_nonempty(source_refs.get("photo_ids", [])),
            "note_item_ids": unique_nonempty(source_refs.get("note_item_ids", [])),
            "source_record_ids": unique_nonempty(source_refs.get("source_record_ids", [])),
            "review_ids": unique_nonempty(source_refs.get("review_ids", [])),
            "mouse_ids": unique_nonempty(source_refs.get("mouse_ids", [])),
        },
        "visual_qa": {
            "status": "manual_review_required",
            "automated_checks": [
                "workbook_structure",
                "trace_sheet_present",
                "source_refs_present",
            ],
            "manual_checks": [
                "lab_format_spacing",
                "printed_readability",
                "recipient_template_compatibility",
            ],
            "note": "Automated export checks do not replace manual lab-format workbook QA.",
        },
        "notes": "Generated from export preview and validation report. This manifest is not canonical state.",
    }


def persist_export_manifest_artifact(manifest: dict[str, Any]) -> dict[str, Any]:
    export_type = str(manifest.get("export_type") or "export")
    filename = str(manifest.get("filename") or manifest.get("manifest_id") or "manifest")
    target_dir = ARTIFACT_ROOT / "export_manifests"
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / f"{safe_artifact_slug(export_type)}_{safe_artifact_slug(filename)}.json"
    artifact_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "manifest_id": manifest["manifest_id"],
        "artifact_path": str(artifact_path),
        "artifact": manifest,
        "boundary": "export or view",
    }


def create_export_provenance_artifacts(
    preview: dict[str, Any],
    *,
    export_type: str,
    filename: str,
    query: str,
    status: str,
    row_count: int,
    blocked_review_count: int,
) -> dict[str, Any]:
    validation_report = persist_validation_report_artifact(
        build_export_validation_report(
            preview,
            export_type=export_type,
            query=query,
            filename=filename,
        )
    )
    manifest = persist_export_manifest_artifact(
        build_export_manifest(
            export_type=export_type,
            filename=filename,
            query=query,
            status=status,
            row_count=row_count,
            blocked_review_count=blocked_review_count,
            validation_report=validation_report,
        )
    )
    return {
        "validation_report": validation_report,
        "manifest": manifest,
        "manifest_artifact_path": manifest["artifact_path"],
        "validation_report_id": validation_report["report_id"],
        "state_watermark": manifest["artifact"].get("state_watermark", ""),
    }


def ensure_export_validation_parse(conn: Any) -> str:
    parse_id = "parse_export_validation"
    existing = conn.execute("SELECT parse_id FROM parse_result WHERE parse_id = ?", (parse_id,)).fetchone()
    if existing:
        return parse_id
    now = utc_now()
    conn.execute(
        """
        INSERT INTO parse_result
            (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (parse_id, "animal_sheet_export_validation", "{}", now, "review", 0, 1),
    )
    return parse_id


def persist_animal_sheet_litter_review_items(
    conn: Any,
    validation: dict[str, Any],
    parse_id: str = "parse_export_validation",
) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for check in validation.get("checks", []):
        if not isinstance(check, dict) or check.get("status") != "blocked":
            continue
        issue = (
            "Animal sheet count conflict"
            if check.get("validation_report_check_key") == "count_mismatch"
            else "Animal sheet litter/date conflict"
        )
        trigger = {
            "validator_check_key": check.get("validator_check_key", ""),
            "validation_report_check_key": check.get("validation_report_check_key", ""),
            "target_refs": check.get("target_refs", []),
        }
        trigger_json = json.dumps(trigger, sort_keys=True, ensure_ascii=False)
        existing = conn.execute(
            """
            SELECT review_id
            FROM review_queue
            WHERE status = 'open'
              AND issue = ?
              AND review_trigger_json = ?
            """,
            (issue, trigger_json),
        ).fetchone()
        if existing:
            created.append({"review_id": existing["review_id"], "created": False})
            continue
        review_id = new_id("review")
        now = utc_now()
        conn.execute(
            """
            INSERT INTO review_queue
                (review_id, parse_id, severity, issue, current_value, suggested_value,
                 review_reason, priority, evidence_reference_json, review_trigger_json,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                parse_id,
                str(check.get("severity") or "high"),
                issue,
                json.dumps(check.get("target_refs", []), ensure_ascii=False),
                json.dumps(check.get("evidence_refs", []), ensure_ascii=False),
                str(check.get("message") or "Review animal sheet litter/date/count conflict before export."),
                "high",
                json.dumps(check.get("source_refs", {}), ensure_ascii=False),
                trigger_json,
                "open",
                now,
            ),
        )
        created.append({"review_id": review_id, "created": True})
    return created


def canonical_candidate_apply_preview(conn: Any, candidate_id: str) -> dict[str, Any]:
    candidate = conn.execute(
        """
        SELECT candidate.candidate_id, candidate.review_id, candidate.parse_id,
               candidate.legacy_row_id, candidate.proposed_strain,
               candidate.proposed_dob, candidate.candidate_payload,
               candidate.status, review.status AS review_status
        FROM canonical_candidate candidate
        JOIN review_queue review ON review.review_id = candidate.review_id
        WHERE candidate.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Canonical candidate draft not found.")

    payload = json_object(candidate["candidate_payload"])
    raw_strain_text = str(candidate["proposed_strain"] or payload.get("strain") or "")
    dob_raw = str(candidate["proposed_dob"] or payload.get("dob") or "")
    dob_raw, dob_start, dob_end = split_dob_range(dob_raw, dob_raw)
    note_rows = conn.execute(
        """
        SELECT note_item_id, photo_id, card_snapshot_id, line_number, raw_line_text, interpreted_status,
               parsed_mouse_display_id, parsed_ear_label_raw, parsed_ear_label_code,
               parsed_ear_label_confidence, parsed_ear_label_review_status, needs_review
        FROM card_note_item_log
        WHERE parse_id = ?
          AND parsed_type = 'mouse_item'
          AND parsed_mouse_display_id IS NOT NULL
          AND parsed_mouse_display_id != ''
        ORDER BY line_number
        """,
        (candidate["parse_id"],),
    ).fetchall()
    proposed_mice = []
    duplicate_risks = []
    unresolved_note_reviews = []
    missing_note_evidence = []
    for note in note_rows:
        display_id = str(note["parsed_mouse_display_id"])
        mouse_id = f"mouse_{display_id}_{candidate['parse_id']}".replace(" ", "_")
        evidence = conn.execute(
            """
            SELECT photo_evidence_id
            FROM photo_evidence_item
            WHERE note_item_id = ?
              AND evidence_kind = 'note_line'
              AND status NOT IN ('rejected', 'superseded')
            LIMIT 1
            """,
            (note["note_item_id"],),
        ).fetchone()
        if int(note["needs_review"] or 0) or note["parsed_ear_label_review_status"] not in {"auto_filled", "verified", "user_corrected"}:
            unresolved_note_reviews.append(
                {
                    "note_item_id": note["note_item_id"],
                    "raw_line_text": note["raw_line_text"],
                    "review_status": note["parsed_ear_label_review_status"],
                }
            )
        if evidence is None:
            missing_note_evidence.append(
                {
                    "note_item_id": note["note_item_id"],
                    "raw_line_text": note["raw_line_text"],
                    "source_photo_id": note["photo_id"],
                }
            )
        existing = conn.execute(
            "SELECT mouse_id FROM mouse_master WHERE mouse_id = ?",
            (mouse_id,),
        ).fetchone()
        duplicate = conn.execute(
            """
            SELECT mouse_id, source_note_item_id
            FROM mouse_master
            WHERE display_id = ?
              AND mouse_id != ?
              AND status IN ('active', 'mating', 'pre_weaning', 'weaning_pending')
            LIMIT 1
            """,
            (display_id, mouse_id),
        ).fetchone()
        if duplicate is not None:
            duplicate_risks.append(
                {
                    "display_id": display_id,
                    "candidate_mouse_id": mouse_id,
                    "existing_mouse_id": duplicate["mouse_id"],
                    "existing_source_note_item_id": duplicate["source_note_item_id"],
                    "source_note_item_id": note["note_item_id"],
                }
            )
        proposed_mice.append(
            {
                "mouse_id": mouse_id,
                "display_id": display_id,
                "raw_strain_text": raw_strain_text,
                "dob_raw": dob_raw,
                "dob_start": dob_start,
                "dob_end": dob_end,
                "status": note["interpreted_status"] or "active",
                "source_note_item_id": note["note_item_id"],
                "source_photo_id": note["photo_id"],
                "card_snapshot_id": note["card_snapshot_id"] or "",
                "photo_evidence_id": evidence["photo_evidence_id"] if evidence is not None else "",
                "raw_line_text": note["raw_line_text"],
                "will_create_mouse": existing is None,
                "existing_mouse_id": existing["mouse_id"] if existing is not None else "",
                "will_create_event": True,
            }
        )
    blockers = []
    if candidate["status"] != "draft":
        blockers.append(f"Candidate status is {candidate['status']}, not draft.")
    if candidate["review_status"] != "resolved":
        blockers.append("Candidate review is not resolved.")
    if not note_rows:
        blockers.append("Candidate has no parsed mouse note lines to apply.")
    if duplicate_risks:
        blockers.append("Active duplicate display IDs must be resolved before applying.")
    if unresolved_note_reviews:
        blockers.append("All note-line review items must be resolved before applying.")
    if missing_note_evidence:
        blockers.append("Each canonical apply row must have note-line photo evidence.")
    return {
        "boundary": "export or view",
        "candidate_id": candidate_id,
        "canonical_apply_rule": canonical_apply_rule(),
        "candidate_status": candidate["status"],
        "review_id": candidate["review_id"],
        "review_status": candidate["review_status"],
        "parse_id": candidate["parse_id"],
        "legacy_row_id": candidate["legacy_row_id"],
        "proposed_mice": proposed_mice,
        "duplicate_risks": duplicate_risks,
        "unresolved_note_reviews": unresolved_note_reviews,
        "missing_note_evidence": missing_note_evidence,
        "blocked": bool(blockers),
        "blockers": blockers,
        "summary": {
            "mouse_rows": len(proposed_mice),
            "new_mouse_rows": sum(1 for item in proposed_mice if item["will_create_mouse"]),
            "existing_mouse_rows": sum(1 for item in proposed_mice if not item["will_create_mouse"]),
            "events": len(proposed_mice),
            "duplicate_risks": len(duplicate_risks),
        },
    }


def canonical_apply_rule() -> dict[str, Any]:
    return {
        "source_layer": "canonical structured state",
        "requires_resolved_review": True,
        "requires_note_line_evidence": True,
        "requires_duplicate_check": True,
        "writes_only_after_preview_clear": True,
    }


def canonical_candidate_audit_view(conn: Any, candidate_id: str) -> dict[str, Any]:
    candidate = conn.execute(
        """
        SELECT candidate.candidate_id, candidate.review_id, candidate.parse_id,
               candidate.legacy_row_id, candidate.proposed_mouse_display_id,
               candidate.proposed_strain, candidate.proposed_dob,
               candidate.proposed_count, candidate.candidate_payload,
               candidate.status, candidate.created_at, candidate.updated_at,
               review.status AS review_status
        FROM canonical_candidate candidate
        JOIN review_queue review ON review.review_id = candidate.review_id
        WHERE candidate.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Canonical candidate not found.")

    event_rows = conn.execute(
        """
        SELECT event_id, mouse_id, event_type, event_date,
               related_entity_type, related_entity_id, source_record_id,
               details, created_by, created_at
        FROM mouse_event
        WHERE related_entity_type = 'canonical_candidate'
          AND related_entity_id = ?
        ORDER BY created_at, event_id
        """,
        (candidate_id,),
    ).fetchall()
    events = []
    applied_mouse_ids: list[str] = []
    for event in event_rows:
        payload = dict(event)
        payload["details"] = json_object(payload.get("details"))
        if payload["event_type"] == "canonical_candidate_applied":
            applied_mouse_ids.append(payload["mouse_id"])
        events.append(payload)

    unique_mouse_ids = list(dict.fromkeys(applied_mouse_ids))
    mice = []
    if unique_mouse_ids:
        placeholders = ",".join("?" for _ in unique_mouse_ids)
        mouse_rows = conn.execute(
            f"""
            SELECT mouse_id, display_id, raw_strain_text, dob_raw, dob_start, dob_end,
                   ear_label_raw, ear_label_code, source_note_item_id, source_photo_id,
                   status, updated_at
            FROM mouse_master
            WHERE mouse_id IN ({placeholders})
            ORDER BY display_id COLLATE NOCASE
            """,
            unique_mouse_ids,
        ).fetchall()
        mice = [dict(row) for row in mouse_rows]
    found_mouse_ids = {mouse["mouse_id"] for mouse in mice}
    missing_mouse_ids = [mouse_id for mouse_id in unique_mouse_ids if mouse_id not in found_mouse_ids]

    action_rows = conn.execute(
        """
        SELECT action_id, action_type, target_id, before_value, after_value, created_at
        FROM action_log
        WHERE target_id = ?
          AND action_type LIKE 'canonical_candidate_%'
        ORDER BY created_at, action_id
        """,
        (candidate_id,),
    ).fetchall()
    actions = []
    for action in action_rows:
        payload = dict(action)
        payload["before_value"] = json_object(payload.get("before_value"))
        payload["after_value"] = json_object(payload.get("after_value"))
        actions.append(payload)

    can_void = candidate["status"] == "applied" and bool(unique_mouse_ids) and not missing_mouse_ids
    blockers = []
    if candidate["status"] != "applied":
        blockers.append(f"Candidate status is {candidate['status']}, not applied.")
    if not unique_mouse_ids:
        blockers.append("No applied mouse records are linked to this candidate.")
    if missing_mouse_ids:
        blockers.append(f"Applied mouse records are missing: {', '.join(missing_mouse_ids)}.")

    return {
        "boundary": "export or view",
        "candidate": {
            **dict(candidate),
            "candidate_payload": json_object(candidate["candidate_payload"]),
        },
        "applied_mouse_ids": unique_mouse_ids,
        "missing_mouse_ids": missing_mouse_ids,
        "mice": mice,
        "events": events,
        "actions": actions,
        "can_void": can_void,
        "blockers": blockers,
        "summary": {
            "applied_mouse_count": len(unique_mouse_ids),
            "current_mouse_count": len(mice),
            "event_count": len(events),
            "action_count": len(actions),
            "voided_event_count": sum(1 for item in events if item["event_type"] == "canonical_candidate_voided"),
        },
    }


@app.get("/api/canonical-candidates/{candidate_id}/apply-preview")
def preview_canonical_candidate_apply(candidate_id: str) -> dict[str, Any]:
    with connection() as conn:
        return canonical_candidate_apply_preview(conn, candidate_id)


@app.post("/api/canonical-candidates/{candidate_id}/proposed-changeset-artifact")
def create_canonical_candidate_changeset_artifact(candidate_id: str) -> dict[str, Any]:
    with connection() as conn:
        preview = canonical_candidate_apply_preview(conn, candidate_id)
    return persist_proposed_changeset_artifact(preview)


@app.post("/api/canonical-candidates/{candidate_id}/validation-report-artifact")
def create_canonical_candidate_validation_report_artifact(candidate_id: str) -> dict[str, Any]:
    with connection() as conn:
        preview = canonical_candidate_apply_preview(conn, candidate_id)
    report = build_canonical_apply_validation_report(preview)
    return persist_validation_report_artifact(report)


@app.get("/api/canonical-candidates/{candidate_id}/audit")
def audit_canonical_candidate(candidate_id: str) -> dict[str, Any]:
    with connection() as conn:
        return canonical_candidate_audit_view(conn, candidate_id)


@app.post("/api/canonical-candidates/{candidate_id}/apply")
def apply_canonical_candidate(candidate_id: str) -> dict[str, Any]:
    applied_at = utc_now()
    created_mice = 0
    existing_mice = 0
    created_events = 0
    mouse_ids: list[str] = []
    with connection() as conn:
        preview = canonical_candidate_apply_preview(conn, candidate_id)
        if preview["blockers"]:
            raise HTTPException(status_code=409, detail=preview)
        candidate = conn.execute(
            """
            SELECT candidate.candidate_id, candidate.review_id, candidate.parse_id,
                   candidate.legacy_row_id, candidate.proposed_strain,
                   candidate.proposed_dob, candidate.candidate_payload,
                   candidate.status, review.status AS review_status
            FROM canonical_candidate candidate
            JOIN review_queue review ON review.review_id = candidate.review_id
            WHERE candidate.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise HTTPException(status_code=404, detail="Canonical candidate draft not found.")
        if candidate["status"] != "draft":
            raise HTTPException(status_code=409, detail="Only draft canonical candidates can be applied.")
        if candidate["review_status"] != "resolved":
            raise HTTPException(status_code=409, detail="Candidate review must be resolved before canonical apply.")

        payload = json_object(candidate["candidate_payload"])
        raw_strain_text = str(candidate["proposed_strain"] or payload.get("strain") or "")
        dob_raw = str(candidate["proposed_dob"] or payload.get("dob") or "")
        dob_raw, dob_start, dob_end = split_dob_range(dob_raw, dob_raw)
        note_rows = conn.execute(
            """
            SELECT note_item_id, photo_id, card_snapshot_id, line_number, raw_line_text, interpreted_status,
                   parsed_mouse_display_id, parsed_ear_label_raw, parsed_ear_label_code,
                   parsed_ear_label_confidence, parsed_ear_label_review_status
            FROM card_note_item_log
            WHERE parse_id = ?
              AND parsed_type = 'mouse_item'
              AND parsed_mouse_display_id IS NOT NULL
              AND parsed_mouse_display_id != ''
            ORDER BY line_number
            """,
            (candidate["parse_id"],),
        ).fetchall()
        if not note_rows:
            raise HTTPException(status_code=400, detail="Candidate has no parsed mouse note lines to apply.")

        for note in note_rows:
            display_id = str(note["parsed_mouse_display_id"])
            mouse_id = f"mouse_{display_id}_{candidate['parse_id']}".replace(" ", "_")
            existing = conn.execute(
                "SELECT mouse_id FROM mouse_master WHERE mouse_id = ?",
                (mouse_id,),
            ).fetchone()
            duplicate = conn.execute(
                """
                SELECT mouse_id, source_note_item_id
                FROM mouse_master
                WHERE display_id = ?
                  AND mouse_id != ?
                  AND status IN ('active', 'mating', 'pre_weaning', 'weaning_pending')
                LIMIT 1
                """,
                (display_id, mouse_id),
            ).fetchone()
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Active mouse with this display ID already exists. Resolve duplicate evidence before applying.",
                        "display_id": display_id,
                        "existing_mouse_id": duplicate["mouse_id"],
                        "existing_source_note_item_id": duplicate["source_note_item_id"],
                        "candidate_id": candidate_id,
                        "source_note_item_id": note["note_item_id"],
                    },
                )
            reviewed_ear_code = (
                note["parsed_ear_label_code"]
                if note["parsed_ear_label_review_status"] in {"auto_filled", "verified", "user_corrected"}
                else None
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO mouse_master
                        (mouse_id, display_id, id_prefix, raw_strain_text, dob_raw, dob_start, dob_end,
                         ear_label_raw, ear_label_code, ear_label_confidence, ear_label_review_status,
                         source_note_item_id, status, source_photo_id, current_card_snapshot_id,
                         last_verified_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mouse_id,
                        display_id,
                        mouse_id_prefix(display_id),
                        raw_strain_text,
                        dob_raw,
                        dob_start,
                        dob_end,
                        note["parsed_ear_label_raw"],
                        reviewed_ear_code,
                        note["parsed_ear_label_confidence"],
                        note["parsed_ear_label_review_status"],
                        note["note_item_id"],
                        note["interpreted_status"] or "active",
                        note["photo_id"],
                        note["card_snapshot_id"],
                        applied_at,
                        applied_at,
                    ),
                )
                created_mice += 1
            else:
                existing_mice += 1
            mouse_ids.append(mouse_id)

            event_id = f"event_apply_{candidate_id}_{note['note_item_id']}".replace(" ", "_")
            photo_evidence = conn.execute(
                """
                SELECT photo_evidence_id, source_photo_id
                FROM photo_evidence_item
                WHERE note_item_id = ?
                  AND status NOT IN ('rejected', 'superseded')
                ORDER BY
                  CASE evidence_kind WHEN 'note_line' THEN 0 ELSE 1 END,
                  CASE status WHEN 'accepted' THEN 0 WHEN 'review_open' THEN 1 WHEN 'draft' THEN 2 WHEN 'linked' THEN 3 ELSE 4 END,
                  confidence DESC,
                  created_at DESC,
                  photo_evidence_id
                LIMIT 1
                """,
                (note["note_item_id"],),
            ).fetchone()
            existing_event = conn.execute(
                "SELECT event_id FROM mouse_event WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is None:
                event_details = {
                    "review_id": candidate["review_id"],
                    "parse_id": candidate["parse_id"],
                    "legacy_row_id": candidate["legacy_row_id"],
                    "note_item_id": note["note_item_id"],
                    "raw_line_text": note["raw_line_text"],
                    "boundary": "canonical structured state",
                    "canonical_apply_rule": canonical_apply_rule(),
                    "source_trace": {
                        "source_photo_id": note["photo_id"] or "",
                        "parse_id": candidate["parse_id"],
                        "note_item_id": note["note_item_id"],
                        "photo_evidence_id": "",
                    },
                }
                if photo_evidence is not None:
                    event_details["photo_evidence_id"] = photo_evidence["photo_evidence_id"]
                    event_details["source_photo_id"] = photo_evidence["source_photo_id"]
                    event_details["source_trace"]["source_photo_id"] = photo_evidence["source_photo_id"]
                    event_details["source_trace"]["photo_evidence_id"] = photo_evidence["photo_evidence_id"]
                conn.execute(
                    """
                    INSERT INTO mouse_event
                        (event_id, mouse_id, event_type, event_date, related_entity_type,
                         related_entity_id, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        mouse_id,
                        "canonical_candidate_applied",
                        applied_at[:10],
                        "canonical_candidate",
                        candidate_id,
                        json.dumps(event_details, ensure_ascii=False),
                        applied_at,
                    ),
                )
                created_events += 1
            if photo_evidence is not None:
                conn.execute(
                    """
                    UPDATE photo_evidence_item
                    SET linked_mouse_id = ?,
                        linked_event_id = ?,
                        status = 'linked',
                        updated_at = ?
                    WHERE photo_evidence_id = ?
                    """,
                    (
                        mouse_id,
                        event_id,
                        applied_at,
                        photo_evidence["photo_evidence_id"],
                    ),
                )

        before_status = candidate["status"]
        conn.execute(
            """
            UPDATE canonical_candidate
            SET status = 'applied',
                updated_at = ?
            WHERE candidate_id = ?
            """,
            (applied_at, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "canonical_candidate_applied",
                candidate_id,
                json.dumps({"status": before_status}, ensure_ascii=False),
                json.dumps(
                    {
                        "status": "applied",
                        "created_mice": created_mice,
                        "existing_mice": existing_mice,
                        "created_events": created_events,
                        "mouse_ids": mouse_ids,
                        "review_id": candidate["review_id"],
                        "parse_id": candidate["parse_id"],
                        "legacy_row_id": candidate["legacy_row_id"],
                        "source_note_item_ids": [note["note_item_id"] for note in note_rows],
                        "source_photo_ids": list(dict.fromkeys(note["photo_id"] for note in note_rows if note["photo_id"])),
                        "card_snapshot_ids": list(dict.fromkeys(note["card_snapshot_id"] for note in note_rows if note["card_snapshot_id"])),
                        "raw_note_lines": [note["raw_line_text"] for note in note_rows],
                        "boundary": "canonical structured state",
                    },
                    ensure_ascii=False,
                ),
                applied_at,
            ),
        )

    return {
        "candidate_id": candidate_id,
        "status": "applied",
        "applied_at": applied_at,
        "created_mice": created_mice,
        "existing_mice": existing_mice,
        "created_events": created_events,
        "mouse_ids": mouse_ids,
        "boundary": "canonical structured state",
    }


@app.post("/api/canonical-candidates/{candidate_id}/void")
def void_canonical_candidate(candidate_id: str) -> dict[str, Any]:
    voided_at = utc_now()
    created_events = 0
    updated_mice = 0
    affected_mouse_ids: list[str] = []
    with connection() as conn:
        audit = canonical_candidate_audit_view(conn, candidate_id)
        if not audit["can_void"]:
            raise HTTPException(status_code=409, detail=audit)

        before_candidate = audit["candidate"]
        affected_mouse_ids = audit["applied_mouse_ids"]
        before_mouse_statuses = {item["mouse_id"]: item["status"] for item in audit["mice"]}
        for mouse_id in affected_mouse_ids:
            current_mouse = conn.execute(
                """
                SELECT mouse_id, status
                FROM mouse_master
                WHERE mouse_id = ?
                """,
                (mouse_id,),
            ).fetchone()
            if current_mouse is None:
                continue
            before_status = current_mouse["status"]
            if before_status != "voided":
                conn.execute(
                    """
                    UPDATE mouse_master
                    SET status = 'voided',
                        last_verified_at = ?,
                        updated_at = ?
                    WHERE mouse_id = ?
                    """,
                    (voided_at, voided_at, mouse_id),
                )
                updated_mice += 1

            event_id = f"event_void_{candidate_id}_{mouse_id}".replace(" ", "_")
            existing_event = conn.execute(
                "SELECT event_id FROM mouse_event WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is None:
                conn.execute(
                    """
                    INSERT INTO mouse_event
                        (event_id, mouse_id, event_type, event_date, related_entity_type,
                         related_entity_id, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        mouse_id,
                        "canonical_candidate_voided",
                        voided_at[:10],
                        "canonical_candidate",
                        candidate_id,
                        json.dumps(
                            {
                                "candidate_id": candidate_id,
                                "before_status": before_status,
                                "after_status": "voided",
                                "reason": "Applied canonical candidate was voided without deleting source-backed mouse records.",
                                "boundary": "canonical structured state",
                            },
                            ensure_ascii=False,
                        ),
                        voided_at,
                    ),
                )
                created_events += 1

        conn.execute(
            """
            UPDATE canonical_candidate
            SET status = 'voided',
                updated_at = ?
            WHERE candidate_id = ?
            """,
            (voided_at, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "canonical_candidate_voided",
                candidate_id,
                json.dumps(
                    {
                        "status": before_candidate["status"],
                        "mouse_statuses": before_mouse_statuses,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "status": "voided",
                        "updated_mice": updated_mice,
                        "created_events": created_events,
                        "mouse_ids": affected_mouse_ids,
                    },
                    ensure_ascii=False,
                ),
                voided_at,
            ),
        )

    return {
        "candidate_id": candidate_id,
        "status": "voided",
        "voided_at": voided_at,
        "updated_mice": updated_mice,
        "created_events": created_events,
        "mouse_ids": affected_mouse_ids,
        "boundary": "canonical structured state",
    }


@app.get("/api/mouse-events")
def list_mouse_events() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT event_id, mouse_id, event_type, event_date,
                   related_entity_type, related_entity_id, source_record_id,
                   details, created_by, created_at
            FROM mouse_event
            ORDER BY event_date DESC, created_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["details"] = json.loads(payload["details"] or "{}")
        result.append(payload)
    return result


HIGH_RISK_MOUSE_EVENT_KEYWORDS = {
    "death",
    "dead",
    "sacrifice",
    "sacrificed",
    "euthanasia",
    "euthanized",
    "separation",
    "separated",
    "wean",
    "weaned",
    "move",
    "moved",
    "transfer",
    "transferred",
    "mating",
    "mate",
    "paired",
    "pairing",
    "litter",
    "genotype",
    "genotyped",
    "genotyping",
}


def is_high_risk_mouse_event(event_type: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(event_type or "").lower()).strip("_")
    tokens = {token for token in normalized.split("_") if token}
    return normalized in HIGH_RISK_MOUSE_EVENT_KEYWORDS or bool(tokens & HIGH_RISK_MOUSE_EVENT_KEYWORDS)


def validate_mouse_event_evidence(conn: Any, payload: MouseEventCreate) -> None:
    details = payload.details or {}
    source_record_id = payload.source_record_id
    source_photo_id = str(details.get("source_photo_id") or "").strip()
    photo_evidence_id = str(details.get("photo_evidence_id") or "").strip()
    source_note_item_id = str(details.get("source_note_item_id") or "").strip()

    if source_record_id:
        exists = conn.execute(
            "SELECT 1 FROM source_record WHERE source_record_id = ?",
            (source_record_id,),
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=400, detail="source_record_id does not exist.")
    if source_photo_id:
        exists = conn.execute("SELECT 1 FROM photo_log WHERE photo_id = ?", (source_photo_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=400, detail="source_photo_id does not exist.")
    if photo_evidence_id:
        exists = conn.execute(
            "SELECT 1 FROM photo_evidence_item WHERE photo_evidence_id = ?",
            (photo_evidence_id,),
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=400, detail="photo_evidence_id does not exist.")
    if source_note_item_id:
        exists = conn.execute(
            "SELECT 1 FROM card_note_item_log WHERE note_item_id = ?",
            (source_note_item_id,),
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=400, detail="source_note_item_id does not exist.")

    if is_high_risk_mouse_event(payload.event_type) and not any(
        [source_record_id, source_photo_id, photo_evidence_id, source_note_item_id]
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "High-risk mouse events require evidence before canonical commit: "
                "source_record_id, details.source_photo_id, details.photo_evidence_id, "
                "or details.source_note_item_id."
            ),
        )


def validated_optional_event_evidence_refs(conn: Any, payload: Any) -> dict[str, str]:
    refs = {
        "source_photo_id": str(getattr(payload, "source_photo_id", "") or "").strip(),
        "source_note_item_id": str(getattr(payload, "source_note_item_id", "") or "").strip(),
        "photo_evidence_id": str(getattr(payload, "photo_evidence_id", "") or "").strip(),
    }
    if refs["source_photo_id"]:
        exists = conn.execute("SELECT 1 FROM photo_log WHERE photo_id = ?", (refs["source_photo_id"],)).fetchone()
        if exists is None:
            raise HTTPException(status_code=400, detail="source_photo_id does not exist.")
    if refs["source_note_item_id"]:
        note_row = conn.execute(
            "SELECT photo_id FROM card_note_item_log WHERE note_item_id = ?",
            (refs["source_note_item_id"],),
        ).fetchone()
        if note_row is None:
            raise HTTPException(status_code=400, detail="source_note_item_id does not exist.")
        note_photo_id = str(note_row["photo_id"] or "")
        if refs["source_photo_id"] and note_photo_id and refs["source_photo_id"] != note_photo_id:
            raise HTTPException(status_code=400, detail="source_note_item_id does not match source_photo_id.")
        refs["source_photo_id"] = refs["source_photo_id"] or note_photo_id
    if refs["photo_evidence_id"]:
        evidence = conn.execute(
            """
            SELECT source_photo_id, note_item_id
            FROM photo_evidence_item
            WHERE photo_evidence_id = ?
            """,
            (refs["photo_evidence_id"],),
        ).fetchone()
        if evidence is None:
            raise HTTPException(status_code=400, detail="photo_evidence_id does not exist.")
        evidence_photo_id = str(evidence["source_photo_id"] or "")
        evidence_note_item_id = str(evidence["note_item_id"] or "")
        if refs["source_photo_id"] and evidence_photo_id and refs["source_photo_id"] != evidence_photo_id:
            raise HTTPException(status_code=400, detail="photo_evidence_id does not match source_photo_id.")
        if refs["source_note_item_id"] and evidence_note_item_id and refs["source_note_item_id"] != evidence_note_item_id:
            raise HTTPException(status_code=400, detail="photo_evidence_id does not match source_note_item_id.")
        refs["source_photo_id"] = refs["source_photo_id"] or evidence_photo_id
        refs["source_note_item_id"] = refs["source_note_item_id"] or evidence_note_item_id
    return {key: value for key, value in refs.items() if value}


def _biological_review_blocker(issue: str, reason: str, current_value: dict[str, Any], suggested_value: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "issue": issue,
        "reason": reason,
        "current_value": current_value,
        "suggested_value": suggested_value or {"next_step": "manual_review_required"},
    }


def _canonical_review_reason(blocker: dict[str, Any]) -> str:
    return (
        f"{blocker['reason']} This biologically unlikely value was routed to review before canonical state, "
        "source record, or event writes. Confirm the source photo, note line, or lab context before applying."
    )


def create_biological_transition_review_item(
    conn: Any,
    *,
    action_type: str,
    target_id: str,
    raw_payload: str,
    blocker: dict[str, Any],
) -> str:
    now = utc_now()
    parse_id = new_id("parse")
    review_id = new_id("review")
    conn.execute(
        """
        INSERT INTO parse_result
            (parse_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parse_id,
            "biological_transition_review",
            json.dumps(
                {
                    "action_type": action_type,
                    "target_id": target_id,
                    "raw_payload": json_object(raw_payload),
                    "blocker": blocker,
                    "source_layer": "parsed or intermediate result",
                },
                ensure_ascii=False,
            ),
            now,
            "review",
            0,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO review_queue
            (review_id, parse_id, severity, issue, current_value, suggested_value,
             review_reason, priority, evidence_reference_json, review_trigger_json,
             status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            parse_id,
            "High",
            blocker["issue"],
            json.dumps(blocker["current_value"], ensure_ascii=False),
            json.dumps(blocker["suggested_value"], ensure_ascii=False),
            _canonical_review_reason(blocker),
            "high",
            json.dumps({"target_id": target_id, "action_type": action_type}, ensure_ascii=False),
            json.dumps(
                {
                    "condition": "biologically_unlikely",
                    "action_type": action_type,
                    "canonical_write_blocked": True,
                },
                ensure_ascii=False,
            ),
            "open",
            now,
        ),
    )
    return review_id


def block_biological_transition_review(
    conn: Any,
    *,
    action_type: str,
    target_id: str,
    raw_payload: str,
    blocker: dict[str, Any] | None,
) -> None:
    if blocker is None:
        return
    create_biological_transition_review_item(
        conn,
        action_type=action_type,
        target_id=target_id,
        raw_payload=raw_payload,
        blocker=blocker,
    )
    conn.commit()
    raise HTTPException(
        status_code=409,
        detail={
            "review_required": True,
            "condition": "biologically_unlikely",
            "issue": blocker["issue"],
            "reason": blocker["reason"],
        },
    )


def litter_creation_biological_blocker(mating: Any, payload: LitterCreate, birth_date: str) -> dict[str, Any] | None:
    mating_start = str(mating["start_date"] or "") if "start_date" in mating.keys() else ""
    mating_start_date = _safe_iso_date(mating_start)
    birth = _safe_iso_date(birth_date)
    if mating_start_date and birth and birth < mating_start_date:
        return _biological_review_blocker(
            "Litter birth date before mating start",
            "Litter birth date is earlier than the linked mating start date.",
            {
                "mating_id": mating["mating_id"],
                "mating_start_date": mating_start,
                "birth_date": birth_date,
            },
        )
    if payload.number_born is not None and payload.number_alive is not None and payload.number_alive > payload.number_born:
        return _biological_review_blocker(
            "Litter alive count exceeds born count",
            "Number alive is greater than number born.",
            {
                "mating_id": payload.mating_id,
                "number_born": payload.number_born,
                "number_alive": payload.number_alive,
            },
        )
    if payload.number_weaned is not None and payload.number_alive is not None and payload.number_weaned > payload.number_alive:
        return _biological_review_blocker(
            "Litter weaned count exceeds alive count",
            "Number weaned is greater than number alive.",
            {
                "mating_id": payload.mating_id,
                "number_alive": payload.number_alive,
                "number_weaned": payload.number_weaned,
            },
        )
    return None


def offspring_creation_biological_blocker(litter: Any, payload: LitterOffspringCreate) -> dict[str, Any] | None:
    number_born = litter["number_born"]
    if number_born is not None and payload.count > int(number_born):
        return _biological_review_blocker(
            "Offspring count exceeds born count",
            "Generated offspring count is greater than the litter's recorded number born.",
            {
                "litter_id": litter["litter_id"],
                "number_born": number_born,
                "offspring_count": payload.count,
            },
        )
    return None


def weaning_biological_blocker(litter: Any, payload: LitterWeanCreate, weaning_date: str, requested_count: int) -> dict[str, Any] | None:
    birth_date = str(litter["birth_date"] or "")
    birth = _safe_iso_date(birth_date)
    weaning = _safe_iso_date(weaning_date)
    if birth and weaning and weaning < birth:
        return _biological_review_blocker(
            "Litter weaning date before birth date",
            "Weaning date is earlier than the litter birth date.",
            {
                "litter_id": litter["litter_id"],
                "birth_date": birth_date,
                "weaning_date": weaning_date,
            },
        )
    number_alive = litter["number_alive"] if litter["number_alive"] is not None else litter["number_born"]
    if number_alive is not None and requested_count > int(number_alive):
        return _biological_review_blocker(
            "Litter weaned count exceeds alive count",
            "Requested weaned count is greater than the litter's current alive or born count.",
            {
                "litter_id": litter["litter_id"],
                "number_alive": number_alive,
                "number_weaned": requested_count,
            },
        )
    return None


@app.post("/api/mouse-events")
def create_mouse_event(payload: MouseEventCreate) -> dict[str, Any]:
    event_id = new_id("event")
    created_at = utc_now()
    event_date = payload.event_date or created_at[:10]
    with connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM mouse_master WHERE mouse_id = ?",
            (payload.mouse_id,),
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")
        validate_mouse_event_evidence(conn, payload)
        conn.execute(
            """
            INSERT INTO mouse_event
                (event_id, mouse_id, event_type, event_date, related_entity_type,
                 related_entity_id, source_record_id, details, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                payload.mouse_id,
                payload.event_type,
                event_date,
                payload.related_entity_type,
                payload.related_entity_id,
                payload.source_record_id,
                json.dumps(payload.details, ensure_ascii=False),
                payload.created_by,
                created_at,
            ),
        )
    return {"event_id": event_id, "event_date": event_date, "created_at": created_at}


def assigned_strain_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["aliases"] = json.loads(result.pop("aliases_json") or "[]")
    result["active"] = bool(result["active"])
    return result


def compact_strain_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def assigned_scope_map(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT display_name, aliases_json
        FROM my_assigned_strain
        WHERE active = 1
        """
    ).fetchall()
    scope: dict[str, str] = {}
    for row in rows:
        names = [row["display_name"], *json.loads(row["aliases_json"] or "[]")]
        for name in names:
            key = compact_strain_key(str(name))
            if key:
                scope[key] = row["display_name"]
    return scope


def assigned_scope_candidates(conn: Any) -> list[MatchCandidate]:
    rows = conn.execute(
        """
        SELECT display_name, aliases_json
        FROM my_assigned_strain
        WHERE active = 1
        """
    ).fetchall()
    candidates: list[MatchCandidate] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        canonical = str(row["display_name"] or "").strip()
        names = [canonical, *json.loads(row["aliases_json"] or "[]")]
        for name in names:
            alias = str(name or "").strip()
            key = (canonical, alias.lower())
            if canonical and alias and key not in seen:
                candidates.append(MatchCandidate(canonical=canonical, alias=alias))
                seen.add(key)
    return candidates


def assigned_scope_match(scope: dict[str, str], record: dict[str, Any]) -> str:
    for value in [record.get("matchedStrain"), record.get("rawStrain")]:
        key = compact_strain_key(str(value or ""))
        if key in scope:
            return scope[key]
    return ""


def assigned_scope_suggestion(conn: Any, record: dict[str, Any]) -> dict[str, Any]:
    suggestion = match_candidate(
        [str(record.get("matchedStrain") or ""), str(record.get("rawStrain") or "")],
        assigned_scope_candidates(conn),
    )
    return suggestion.as_dict()


def strain_match_review_reason(match_info: dict[str, Any], base_reason: str) -> str:
    canonical = str(match_info.get("canonical") or "")
    alias = str(match_info.get("matched_alias") or "")
    score = float(match_info.get("score") or 0)
    decision = str(match_info.get("decision") or "needs_review")
    alternatives = match_info.get("alternatives") if isinstance(match_info.get("alternatives"), list) else []
    alt_text = ", ".join(
        f"{item.get('canonical')} via {item.get('alias')} ({item.get('score')})"
        for item in alternatives
        if isinstance(item, dict) and item.get("canonical")
    )
    suggestion_text = (
        f" RapidFuzz suggestion: {canonical or '--'} via {alias or '--'} "
        f"score {score:.2f}, decision {decision}."
    )
    if alt_text:
        suggestion_text += f" Alternatives: {alt_text}."
    return f"{base_reason}{suggestion_text}"


def compact_genotype(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def parse_optional_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def distribution_row_payload(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["traceability"] = json.loads(result.get("traceability") or "{}")
    return result


def legacy_row_payload(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["raw_row"] = json_object(result.pop("raw_row_json", "{}"))
    return result


def legacy_import_payload(row: Any) -> dict[str, Any]:
    result = dict(row)
    raw_payload = json_object(result.pop("raw_payload", "{}"))
    candidates = raw_payload.get("strain_registry_candidates")
    result["strain_registry_candidates"] = candidates if isinstance(candidates, list) else []
    return result


def legacy_review_current_value(row: dict[str, Any], fallback_row_number: int) -> str:
    source_cells = row.get("source_cells") if isinstance(row.get("source_cells"), dict) else {}
    anchors = {
        "row_type": row.get("row_type") or "",
        "source_row_number": row.get("source_row_number") or fallback_row_number,
        "cage_no_raw": row.get("cage_no_raw") or row.get("group_name_raw") or "",
        "strain_raw": row.get("strain_raw") or row.get("male_raw") or "",
        "display_id_raw": row.get("display_id_raw") or row.get("female_raw") or "",
        "genotype_raw": row.get("genotype_raw") or row.get("total_raw") or "",
        "source_cells": source_cells,
    }
    return json.dumps({key: value for key, value in anchors.items() if value not in ("", None, {})}, ensure_ascii=False)


def legacy_review_reason(row: dict[str, Any], source_file_name: str, sheet_name: str, fallback_row_number: int) -> str:
    row_number = parse_optional_int(row.get("source_row_number")) or fallback_row_number
    source_sheet = str(row.get("source_sheet") or sheet_name or "")
    cells = row.get("source_cells") if isinstance(row.get("source_cells"), dict) else {}
    cell_refs = ", ".join(f"{key}:{value}" for key, value in cells.items() if value) or "no cell refs"
    return (
        f"Legacy workbook row from {source_file_name}, sheet {source_sheet or '--'}, row {row_number}; "
        f"source cells {cell_refs}. Imported as a review candidate only; do not write canonical mouse, cage, "
        "litter, or genotype state until a human resolves the row."
    )


def strain_registry_candidate_review_reason(
    candidate: dict[str, Any],
    source_file_name: str,
    sheet_name: str,
) -> str:
    evidence_ids = candidate.get("source_evidence_ids") if isinstance(candidate.get("source_evidence_ids"), list) else []
    evidence_text = ", ".join(str(item) for item in evidence_ids if item) or "no row evidence"
    return (
        f"Legacy workbook strain registry candidate from {source_file_name}, sheet {sheet_name or '--'}; "
        f"row evidence {evidence_text}. Preserve raw strain/genotype text and review before creating or linking "
        "gene/allele records; the parser intentionally does not infer gene or allele from genotype text."
    )


def json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def compact_compare_value(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def date_tokens(value: Any) -> set[str]:
    text = str(value or "")
    tokens = set(re.findall(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{2}[-./]\d{1,2}[-./]\d{1,2}", text))
    return {token.replace("/", ".").replace("-", ".") for token in tokens}


def first_count(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def manual_transcription_summary(record: dict[str, Any]) -> dict[str, Any]:
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []
    return {
        "strain": record.get("matchedStrain") or record.get("rawStrain") or "",
        "sex": record.get("sexRaw") or "",
        "id": record.get("idRaw") or "",
        "dob": record.get("dobRaw") or record.get("dobNormalized") or "",
        "count": first_count(record.get("mouseCount")) or len(notes) or None,
        "count_raw": record.get("mouseCount") or "",
        "card_type": record.get("type") or "",
        "note_count": len(notes),
    }


def legacy_candidate_summary(row: dict[str, Any]) -> dict[str, Any]:
    row_type = str(row.get("row_type") or "")
    return {
        "strain": row.get("strain_raw") or row.get("group_name_raw") or row.get("male_raw") or "",
        "dob": row.get("dob_raw") or "",
        "count": row.get("count_candidate") or first_count(row.get("total_raw") or row.get("display_id_raw")),
        "count_raw": row.get("total_raw") or row.get("display_id_raw") or row.get("pubs_raw") or "",
        "row_type": row_type,
    }


def comparison_score(manual: dict[str, Any], legacy: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    matched: list[str] = []
    mismatched: list[str] = []
    score = 0

    manual_strain = compact_compare_value(manual.get("strain"))
    legacy_strain = compact_compare_value(legacy.get("strain"))
    if manual_strain and legacy_strain:
        if manual_strain == legacy_strain or manual_strain in legacy_strain or legacy_strain in manual_strain:
            score += 3
            matched.append("strain")
        else:
            mismatched.append("strain")

    manual_dates = date_tokens(manual.get("dob"))
    legacy_dates = date_tokens(legacy.get("dob"))
    if manual_dates and legacy_dates:
        if manual_dates & legacy_dates:
            score += 2
            matched.append("dob")
        else:
            mismatched.append("dob")

    manual_count = manual.get("count")
    legacy_count = legacy.get("count")
    if manual_count is not None and legacy_count is not None:
        if manual_count == legacy_count:
            score += 1
            matched.append("count")
        else:
            mismatched.append("count")

    return score, matched, mismatched


def comparison_review_id(manual_parse_id: str, legacy_row_id: str) -> str:
    raw_id = f"review_comparison_{manual_parse_id}_{legacy_row_id}"
    return re.sub(r"[^A-Za-z0-9_-]+", "_", raw_id)


def build_evidence_comparison_payload(conn: Any) -> dict[str, Any]:
    manual_rows = conn.execute(
        """
        SELECT parse.parse_id, parse.photo_id, parse.raw_payload, parse.parsed_at,
               photo.original_filename, photo.upload_batch_id
        FROM parse_result parse
        LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
        WHERE parse.source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
        ORDER BY parse.parsed_at DESC
        LIMIT 50
        """
    ).fetchall()
    legacy_rows = conn.execute(
        """
        SELECT row.legacy_row_id, row.review_id, row.row_type, row.source_sheet,
               row.source_row_number, row.raw_row_json,
               legacy.source_file_name
        FROM legacy_workbook_row row
        LEFT JOIN legacy_workbook_import legacy ON legacy.legacy_import_id = row.legacy_import_id
        ORDER BY legacy.imported_at DESC, row.source_row_number
        """
    ).fetchall()

    legacy_candidates = []
    for row in legacy_rows:
        raw_row = json_object(row["raw_row_json"])
        summary = legacy_candidate_summary(raw_row)
        legacy_candidates.append(
            {
                "legacy_row_id": row["legacy_row_id"],
                "review_id": row["review_id"],
                "source_file_name": row["source_file_name"] or "",
                "source_sheet": row["source_sheet"] or "",
                "source_row_number": row["source_row_number"],
                "raw_row": raw_row,
                "summary": summary,
            }
        )

    comparisons = []
    for manual in manual_rows:
        record = json_object(manual["raw_payload"])
        manual_summary = manual_transcription_summary(record)
        ranked = []
        for legacy in legacy_candidates:
            score, matched, mismatched = comparison_score(manual_summary, legacy["summary"])
            if score >= 2:
                ranked.append((score, matched, mismatched, legacy))
        ranked.sort(key=lambda item: item[0], reverse=True)
        best = ranked[0] if ranked else None
        if best is None:
            status = "no_legacy_candidate"
            matched_fields: list[str] = []
            mismatched_fields: list[str] = []
            legacy_payload: dict[str, Any] | None = None
            detail = "No predecessor Excel candidate matched strain, DOB, or count."
        else:
            score, matched_fields, mismatched_fields, legacy_payload = best
            status = "exact_match" if score >= 6 and not mismatched_fields else "value_mismatch"
            detail = (
                f"Matched {', '.join(matched_fields) or 'none'}"
                + (f"; differs on {', '.join(mismatched_fields)}" if mismatched_fields else "")
            )
        legacy_row_id = (
            str(legacy_payload.get("legacy_row_id"))
            if isinstance(legacy_payload, dict) and legacy_payload.get("legacy_row_id")
            else "no_legacy_candidate"
        )
        review_id = comparison_review_id(manual["parse_id"], legacy_row_id)
        comparisons.append(
            {
                "source_layer": "export or view",
                "manual_parse_id": manual["parse_id"],
                "photo_id": manual["photo_id"],
                "upload_batch_id": manual["upload_batch_id"] or "",
                "photo_filename": manual["original_filename"] or "",
                "manual_summary": manual_summary,
                "legacy_candidate": legacy_payload,
                "status": status,
                "matched_fields": matched_fields,
                "mismatched_fields": mismatched_fields,
                "detail": detail,
                "review_required": status != "exact_match",
                "review_id": review_id,
                "review_status": "not_created" if status != "exact_match" else "not_required",
            }
        )

    review_ids = [comparison["review_id"] for comparison in comparisons if comparison.get("review_required")]
    if review_ids:
        placeholders = ",".join("?" for _ in review_ids)
        review_rows = conn.execute(
            f"""
            SELECT review_id, status, severity, issue
            FROM review_queue
            WHERE review_id IN ({placeholders})
            """,
            review_ids,
        ).fetchall()
        reviews_by_id = {row["review_id"]: dict(row) for row in review_rows}
        for comparison in comparisons:
            review = reviews_by_id.get(comparison["review_id"])
            if review:
                comparison["review_status"] = review["status"]
                comparison["review_severity"] = review["severity"]
                comparison["review_issue"] = review["issue"]

    return {
        "boundary": "export or view",
        "source_priority": ["raw source photo", "manual transcription", "predecessor Excel view"],
        "manual_transcription_count": len(manual_rows),
        "legacy_candidate_count": len(legacy_candidates),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
    }


def split_dob_range(raw: Any, normalized: Any) -> tuple[str, str | None, str | None]:
    dob_raw = str(raw or "")
    normalized_text = str(normalized or "")
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", normalized_text)
    if len(dates) >= 2:
        return dob_raw, dates[0], dates[1]
    if len(dates) == 1:
        return dob_raw, dates[0], dates[0]
    return dob_raw, None, None


def normalize_sex_raw(raw: Any) -> str:
    text = repair_known_ocr_symbol_mojibake(raw).strip()
    lowered = text.lower()
    has_male = "\u2642" in text or bool(re.search(r"\b(m|male|man)\b", lowered))
    has_female = "\u2640" in text or bool(re.search(r"\b(f|female|woman)\b", lowered))
    if has_male and has_female:
        return "mixed"
    if any(token in lowered for token in ["mixed", "both", "m/f", "f/m", "mf"]):
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown" if text else ""


def infer_card_type_from_sex(card_type: str, sex_raw: Any = "", sex_normalized: str = "") -> str:
    normalized_card_type = str(card_type or "unknown")
    sex_value = str(sex_normalized or "").strip().lower() or normalize_sex_raw(sex_raw)
    if sex_value == "mixed" and normalized_card_type in {"", "unknown", "Separated"}:
        return "Mating"
    return normalized_card_type or "unknown"


def first_int(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def unlabeled_numeric_metadata(raw_line: str) -> dict[str, Any]:
    labels = re.findall(r"\d+", raw_line)
    return {
        "labels": labels,
        "count": len(labels),
        "display": f"Label pending {', '.join(labels)} ({len(labels)}p)" if labels else "Label pending",
        "display_ko": f"라벨 미정 {', '.join(labels)} ({len(labels)}p)" if labels else "라벨 미정",
        "export_policy": "block_final_export_until_labeled_or_confirmed",
    }


def mouse_id_prefix(display_id: str) -> str:
    match = re.match(r"^([A-Za-z]+)", display_id)
    return match.group(1) if match else ""


def normalize_ear_label(raw: str) -> dict[str, Any]:
    text = "".join(str(raw or "").split())
    if not text:
        return {"code": None, "confidence": 0.0, "status": "needs_review", "candidates": [], "reason": "Ear label is blank."}
    if text.upper() == "N":
        return {"code": "NONE", "confidence": 1.0, "status": "auto_filled", "candidates": [], "reason": ""}

    components: list[tuple[str, str, float, str]] = []
    index = 0
    while index < len(text):
        side = text[index].upper()
        if side not in {"R", "L"}:
            return {
                "code": None,
                "confidence": 0.0,
                "status": "needs_review",
                "candidates": [],
                "reason": f"Unexpected ear-label side '{text[index]}' in '{text}'. Expected R or L.",
            }
        index += 1
        if index >= len(text):
            return {
                "code": None,
                "confidence": 0.2,
                "status": "needs_review",
                "candidates": [{"code": f"{side}_PRIME", "confidence": 0.35}, {"code": f"{side}_CIRCLE", "confidence": 0.35}],
                "reason": f"Ear-label side '{side}' is missing a mark. Expected prime or circle.",
            }
        mark = text[index]
        index += 1
        if mark in {"'", "\u2032", "\u2019", "`", "."}:
            components.append((side, "PRIME", 0.98 if mark != "." else 0.75, "auto_filled" if mark != "." else "check"))
        elif mark in {"\u00b0", "\u00ba", "\u02da"}:
            components.append((side, "CIRCLE", 1.0 if mark == "\u00b0" else 0.92, "auto_filled"))
        elif mark in {"0", "o", "O"}:
            components.append((side, "CIRCLE", 0.65, "check"))
        else:
            return {
                "code": None,
                "confidence": 0.0,
                "status": "needs_review",
                "candidates": [{"code": f"{side}_PRIME", "confidence": 0.25}, {"code": f"{side}_CIRCLE", "confidence": 0.25}],
                "reason": f"Unexpected ear-label mark '{mark}' in '{text}'. Expected prime or circle.",
            }

    if not components:
        return {"code": None, "confidence": 0.0, "status": "needs_review", "candidates": [], "reason": "Ear label could not be parsed."}

    code = "_".join(f"{side}_{mark}" for side, mark, _, _ in components)
    confidence = min(confidence for _, _, confidence, _ in components)
    status = "check" if any(status == "check" for _, _, _, status in components) else "auto_filled"
    reason = "Ambiguous circle/zero mark needs review." if status == "check" else ""
    return {"code": code, "confidence": confidence, "status": status, "candidates": [], "reason": reason}


def interpreted_status(card_type: str, strike_status: str) -> str:
    normalized_card_type = card_type.lower()
    if strike_status == "single":
        return "separated" if normalized_card_type == "mating" else "moved"
    if strike_status == "double":
        return "dead"
    if strike_status == "unclear":
        return "needs_review"
    return "open" if normalized_card_type == "mating" else "active"


def parse_note_line(raw_line: str, card_type: str) -> dict[str, Any]:
    line = str(raw_line or "").strip()
    numeric_tokens = re.findall(r"\d+", line)
    numeric_only = bool(numeric_tokens) and re.fullmatch(r"[\d\s,./\\-]+", line)
    if numeric_only and not re.search(r"\d{2,4}[./-]\d{1,2}[./-]\d{1,2}", line):
        metadata = unlabeled_numeric_metadata(line)
        return {
            "parsed_type": "unlabeled_numeric_note",
            "parsed_mouse_display_id": None,
            "parsed_ear_label_raw": None,
            "parsed_ear_label_code": None,
            "parsed_ear_label_confidence": None,
            "parsed_ear_label_review_status": "needs_review",
            "parsed_event_date": None,
            "parsed_count": metadata["count"],
            "parsed_metadata": metadata,
            "confidence": 0.5,
            "needs_review": 1,
        }

    litter_match = re.search(r"(\d{2,4}[./-]\d{1,2}[./-]\d{1,2})\s*[-\u2013]\s*(\d+)\s*p?", line, re.IGNORECASE)
    if litter_match:
        return {
            "parsed_type": "litter_event",
            "parsed_mouse_display_id": None,
            "parsed_ear_label_raw": None,
            "parsed_ear_label_code": None,
            "parsed_ear_label_confidence": None,
            "parsed_ear_label_review_status": "needs_review",
            "parsed_event_date": litter_match.group(1),
            "parsed_count": int(litter_match.group(2)),
            "parsed_metadata": {},
            "confidence": 0.9,
            "needs_review": 0,
        }

    mouse_match = re.match(r"^\s*([A-Za-z]{0,4}\d+[A-Za-z0-9-]*)\s+(.+?)\s*$", line)
    if mouse_match and "x" not in line.lower():
        ear_raw = mouse_match.group(2).strip()
        ear = normalize_ear_label(ear_raw)
        parsed_metadata = {}
        if ear["status"] in {"check", "needs_review"}:
            parsed_metadata = {
                "ear_label_issue": ear.get("reason") or "Ear label token needs source-photo review.",
                "ear_label_raw": ear_raw,
                "allowed_ear_label_examples": ["R'", "L'", "R°", "L°", "R0", "L0", "N"],
                "display": f"{mouse_match.group(1)} [ear label review: {ear_raw}]",
            }
        return {
            "parsed_type": "mouse_item",
            "parsed_mouse_display_id": mouse_match.group(1),
            "parsed_ear_label_raw": ear_raw,
            "parsed_ear_label_code": ear["code"],
            "parsed_ear_label_confidence": ear["confidence"],
            "parsed_ear_label_review_status": ear["status"],
            "parsed_event_date": None,
            "parsed_count": None,
            "parsed_metadata": parsed_metadata,
            "confidence": ear["confidence"],
            "needs_review": 1 if ear["status"] in {"check", "needs_review"} else 0,
        }

    return {
        "parsed_type": "unknown",
        "parsed_mouse_display_id": None,
        "parsed_ear_label_raw": None,
        "parsed_ear_label_code": None,
        "parsed_ear_label_confidence": None,
        "parsed_ear_label_review_status": "needs_review",
        "parsed_event_date": None,
        "parsed_count": None,
        "parsed_metadata": {},
        "confidence": 0.0,
        "needs_review": 1,
    }


def should_write_mouse_candidate(record: dict[str, Any], status: str) -> bool:
    if status != "auto":
        return False
    if str(record.get("type") or "").lower() != "separated":
        return False
    review_field = str(record.get("reviewField") or "").lower()
    issue = str(record.get("issue") or "").lower()
    return review_field not in {"mouseid", "mousecount"} and "count mismatch" not in issue


def load_labeling_rule_ear_sequence(conn: Any, rule_set_id: str) -> list[str]:
    rule_id = str(rule_set_id or "").strip()
    if not rule_id:
        return []
    rows = conn.execute(
        """
        SELECT ear_label_code
        FROM labeling_rule_ear_sequence
        WHERE rule_set_id = ?
        ORDER BY sequence_index
        """,
        (rule_id,),
    ).fetchall()
    return [str(row["ear_label_code"]) for row in rows]


def load_labeling_rule_crossed_out_handling(conn: Any, rule_set_id: str) -> str:
    rule_id = str(rule_set_id or "").strip()
    if not rule_id:
        return ""
    row = conn.execute(
        """
        SELECT crossed_out_handling
        FROM labeling_rule_set
        WHERE rule_set_id = ?
        """,
        (rule_id,),
    ).fetchone()
    return str(row["crossed_out_handling"] or "").strip() if row else ""


def load_labeling_rule_genotyping_target(conn: Any, rule_set_id: str) -> str:
    rule_id = str(rule_set_id or "").strip()
    if not rule_id:
        return ""
    row = conn.execute(
        """
        SELECT genotyping_target
        FROM labeling_rule_set
        WHERE rule_set_id = ?
        """,
        (rule_id,),
    ).fetchone()
    return str(row["genotyping_target"] or "").strip() if row else ""


def load_labeling_rule_sample_mapping(conn: Any, rule_set_id: str) -> str:
    rule_id = str(rule_set_id or "").strip()
    if not rule_id:
        return ""
    row = conn.execute(
        """
        SELECT sample_mapping
        FROM labeling_rule_set
        WHERE rule_set_id = ?
        """,
        (rule_id,),
    ).fetchone()
    return str(row["sample_mapping"] or "").strip() if row else ""


def load_labeling_rule_context(conn: Any, rule_set_id: str) -> dict[str, Any]:
    rule_id = str(rule_set_id or "").strip()
    if not rule_id:
        return {}
    row = conn.execute(
        """
        SELECT rule_set_id, display_name, session_date, crossed_out_handling,
               sample_mapping, genotyping_target
        FROM labeling_rule_set
        WHERE rule_set_id = ?
        """,
        (rule_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "rule_set_id": row["rule_set_id"],
        "display_name": row["display_name"],
        "session_date": row["session_date"],
        "crossed_out_handling": row["crossed_out_handling"],
        "ear_label_sequence": load_labeling_rule_ear_sequence(conn, rule_id),
        "sample_mapping": row["sample_mapping"],
        "genotyping_target": row["genotyping_target"],
    }


@app.get("/api/labeling-rule-sets")
def list_labeling_rule_sets() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT rule_set_id, display_name, applies_to_strain_text, session_date,
                   numbering_order, mouse_number_scope, ear_sequence_scope,
                   crossed_out_handling, sample_mapping, genotyping_target, active
            FROM labeling_rule_set
            ORDER BY active DESC, session_date DESC, display_name
            """
        ).fetchall()
        sequence_rows = conn.execute(
            """
            SELECT rule_set_id, ear_label_code
            FROM labeling_rule_ear_sequence
            ORDER BY rule_set_id, sequence_index
            """
        ).fetchall()

    sequences: dict[str, list[str]] = {}
    for row in sequence_rows:
        sequences.setdefault(str(row["rule_set_id"]), []).append(str(row["ear_label_code"]))

    return [
        {
            "rule_set_id": row["rule_set_id"],
            "display_name": row["display_name"],
            "applies_to_strain_text": row["applies_to_strain_text"],
            "session_date": row["session_date"],
            "numbering_order": row["numbering_order"],
            "mouse_number_scope": row["mouse_number_scope"],
            "ear_sequence_scope": row["ear_sequence_scope"],
            "crossed_out_handling": row["crossed_out_handling"],
            "sample_mapping": row["sample_mapping"],
            "genotyping_target": row["genotyping_target"],
            "active": bool(row["active"]),
            "ear_label_sequence": sequences.get(str(row["rule_set_id"]), []),
        }
        for row in rows
    ]


def active_mouse_note_count(record: dict[str, Any]) -> int:
    card_type = str(record.get("type") or "unknown").lower()
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []
    active_count = 0
    for note in notes:
        raw_line = str(note.get("raw") if isinstance(note, dict) else note)
        strike_status = str(note.get("strike") or "none") if isinstance(note, dict) else "none"
        parsed = parse_note_line(raw_line, card_type)
        if parsed["parsed_type"] == "mouse_item" and interpreted_status(card_type, strike_status) == "active":
            active_count += 1
    return active_count


def declared_total_count(record: dict[str, Any]) -> int | None:
    mouse_count = str(record.get("mouseCount") or "")
    if "total" not in mouse_count.lower():
        return None
    match = re.search(r"\d+", mouse_count)
    return int(match.group(0)) if match else None


def card_note_summary(record: dict[str, Any]) -> dict[str, Any]:
    card_type = str(record.get("type") or "unknown")
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []
    summary = {
        "note_count": 0,
        "mouse_item_count": 0,
        "litter_event_count": 0,
        "unlabeled_numeric_count": 0,
        "unlabeled_numeric_labels": [],
        "unlabeled_numeric_display": [],
        "unknown_count": 0,
        "needs_review_count": 0,
    }
    for note in notes:
        raw_line = str(note.get("raw") if isinstance(note, dict) else note)
        if not raw_line.strip():
            continue
        parsed = parse_note_line(raw_line, card_type)
        summary["note_count"] += 1
        parsed_type = parsed["parsed_type"]
        if parsed_type == "mouse_item":
            summary["mouse_item_count"] += 1
        elif parsed_type == "litter_event":
            summary["litter_event_count"] += 1
        elif parsed_type == "unlabeled_numeric_note":
            metadata = parsed.get("parsed_metadata") or {}
            summary["unlabeled_numeric_count"] += int(metadata.get("count") or 0)
            summary["unlabeled_numeric_labels"].extend(metadata.get("labels") or [])
            summary["unlabeled_numeric_display"].append(metadata.get("display_ko") or raw_line)
        else:
            summary["unknown_count"] += 1
        if parsed["needs_review"]:
            summary["needs_review_count"] += 1
    return summary


def create_card_snapshot(conn: Any, parse_id: str, photo_id: str | None, record: dict[str, Any], created_at: str) -> str:
    card_snapshot_id = new_id("card_snapshot")
    dob_raw, dob_start, dob_end = split_dob_range(record.get("dobRaw"), record.get("dobNormalized"))
    note_summary = card_note_summary(record)
    conn.execute(
        """
        INSERT INTO card_snapshot
            (card_snapshot_id, photo_id, parse_id, card_type, card_id_raw,
             raw_strain_text, matched_strain_text, sex_raw, sex_normalized,
             sex_count_raw, count_value, dob_raw, dob_start, dob_end,
             mating_date_raw, mating_date_normalized, lmo_raw, note_summary_json,
             status, source_layer, confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_snapshot_id,
            photo_id,
            parse_id,
            str(record.get("type") or "unknown"),
            str(record.get("idRaw") or ""),
            str(record.get("rawStrain") or ""),
            str(record.get("matchedStrain") or ""),
            str(record.get("sexRaw") or ""),
            str(record.get("sexNormalized") or "") or normalize_sex_raw(record.get("sexRaw")),
            str(record.get("mouseCount") or ""),
            first_int(record.get("mouseCount")),
            dob_raw,
            dob_start,
            dob_end,
            str(record.get("matingDateRaw") or ""),
            str(record.get("matingDateNormalized") or ""),
            str(record.get("lmoRaw") or ""),
            json.dumps(note_summary, ensure_ascii=False),
            "review",
            "parsed or intermediate result",
            bounded_float(record.get("confidence")),
            created_at,
            created_at,
        ),
    )
    return card_snapshot_id


def refresh_card_snapshot_summary(conn: Any, card_snapshot_id: str, updated_at: str) -> dict[str, Any] | None:
    if not card_snapshot_id:
        return None
    rows = conn.execute(
        """
        SELECT parsed_type, raw_line_text, parsed_count, parsed_metadata_json, needs_review
        FROM card_note_item_log
        WHERE card_snapshot_id = ?
        ORDER BY line_number
        """,
        (card_snapshot_id,),
    ).fetchall()
    if not rows:
        return None

    summary: dict[str, Any] = {
        "note_count": len(rows),
        "mouse_item_count": 0,
        "litter_event_count": 0,
        "unlabeled_numeric_count": 0,
        "unlabeled_numeric_labels": [],
        "unlabeled_numeric_display": [],
        "count_note_total": 0,
        "count_note_lines": 0,
        "reviewed_note_count": 0,
        "ignored_note_count": 0,
        "unknown_count": 0,
        "needs_review_count": 0,
    }
    for row in rows:
        parsed_type = row["parsed_type"]
        if parsed_type == "mouse_item":
            summary["mouse_item_count"] += 1
        elif parsed_type == "litter_event":
            summary["litter_event_count"] += 1
        elif parsed_type == "unlabeled_numeric_note":
            metadata = json_object(row["parsed_metadata_json"])
            summary["unlabeled_numeric_count"] += int(metadata.get("count") or 0)
            summary["unlabeled_numeric_labels"].extend(metadata.get("labels") or [])
            summary["unlabeled_numeric_display"].append(metadata.get("display_ko") or row["raw_line_text"])
        elif parsed_type == "count_note":
            summary["count_note_lines"] += 1
            summary["count_note_total"] += int(row["parsed_count"] or 0)
        elif parsed_type == "reviewed_note":
            summary["reviewed_note_count"] += 1
        elif parsed_type == "ignored_note":
            summary["ignored_note_count"] += 1
        else:
            summary["unknown_count"] += 1
        if row["needs_review"]:
            summary["needs_review_count"] += 1

    status = "review" if summary["needs_review_count"] else "reviewed"
    conn.execute(
        """
        UPDATE card_snapshot
        SET note_summary_json = ?,
            status = ?,
            updated_at = ?
        WHERE card_snapshot_id = ?
        """,
        (json.dumps(summary, ensure_ascii=False), status, updated_at, card_snapshot_id),
    )
    return {"card_snapshot_id": card_snapshot_id, "status": status, "note_summary": summary}


PHOTO_EVIDENCE_FIELD_MAP = {
    "raw_strain": ("rawStrain", "raw_strain"),
    "matched_strain": ("matchedStrain", "raw_strain"),
    "sex_raw": ("sexRaw", "sex_raw"),
    "sex_normalized": ("sexNormalized", "sex_raw"),
    "id_raw": ("idRaw", "id_raw"),
    "dob_raw": ("dobRaw", "dob_raw"),
    "dob_normalized": ("dobNormalized", "dob_raw"),
    "mating_date_raw": ("matingDateRaw", "mating_date_raw"),
    "mating_date_normalized": ("matingDateNormalized", "mating_date_raw"),
    "lmo_raw": ("lmoRaw", "lmo_raw"),
    "mouse_count": ("mouseCount", "sex_raw"),
}


PHOTO_EVIDENCE_NORMALIZED_FIELD_PAIRS = {
    "raw_strain": "matched_strain",
    "sex_raw": "sex_normalized",
    "dob_raw": "dob_normalized",
    "mating_date_raw": "mating_date_normalized",
}


def roi_label_for_field(record: dict[str, Any], field_name: str) -> str:
    for region in record.get("extractionRegions") or []:
        if not isinstance(region, dict):
            continue
        target_fields = region.get("targetFields") if isinstance(region.get("targetFields"), list) else []
        if field_name in {str(field).strip() for field in target_fields}:
            return str(region.get("label") or field_name)
    return PHOTO_EVIDENCE_FIELD_MAP.get(field_name, ("", field_name))[1]


def photo_evidence_raw_normalized_values(record: dict[str, Any], field_name: str) -> tuple[str, str]:
    record_key = PHOTO_EVIDENCE_FIELD_MAP[field_name][0]
    observed_value = str(record.get(record_key) or "").strip()
    if field_name in PHOTO_EVIDENCE_NORMALIZED_FIELD_PAIRS:
        normalized_field = PHOTO_EVIDENCE_NORMALIZED_FIELD_PAIRS[field_name]
        normalized_key = PHOTO_EVIDENCE_FIELD_MAP[normalized_field][0]
        return observed_value, str(record.get(normalized_key) or "").strip()
    for raw_field, normalized_field in PHOTO_EVIDENCE_NORMALIZED_FIELD_PAIRS.items():
        if field_name == normalized_field:
            raw_key = PHOTO_EVIDENCE_FIELD_MAP[raw_field][0]
            return str(record.get(raw_key) or "").strip(), observed_value
    return observed_value, ""


def photo_evidence_reference_json(
    *,
    source_photo_id: str,
    parse_id: str,
    card_snapshot_id: str,
    evidence_kind: str,
    roi_label: str,
    note_item_id: str = "",
) -> str:
    reference = {
        "source_layer": "parsed or intermediate result",
        "source_photo_id": source_photo_id,
        "parse_id": parse_id,
        "card_snapshot_id": card_snapshot_id,
        "evidence_kind": evidence_kind,
        "roi_label": roi_label,
    }
    if note_item_id:
        reference["note_item_id"] = note_item_id
    return json.dumps(reference, ensure_ascii=False)


def review_item_evidence_reference_json(
    *,
    source_photo_id: str,
    parse_id: str,
    card_snapshot_id: str,
    photo_evidence_items: int,
    linked_photo_evidence_items: int = 0,
) -> str:
    return json.dumps(
        {
            "source_layer": "review item",
            "source_photo_id": source_photo_id,
            "parse_id": parse_id,
            "card_snapshot_id": card_snapshot_id,
            "photo_evidence_items": photo_evidence_items,
            "linked_photo_evidence_items": linked_photo_evidence_items,
        },
        ensure_ascii=False,
    )


def review_item_suggested_value(record: dict[str, Any]) -> str:
    proposed = {
        "proposed_normalized_values": {
            "matched_strain": str(record.get("matchedStrain") or "").strip(),
            "sex_normalized": str(record.get("sexNormalized") or "").strip(),
            "dob_normalized": str(record.get("dobNormalized") or "").strip(),
            "mating_date_normalized": str(record.get("matingDateNormalized") or "").strip(),
        },
        "instruction": "Compare with raw photo and predecessor Excel before accepting.",
    }
    return json.dumps(proposed, ensure_ascii=False)


def invalid_iso_date(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return True
    return False


def review_required_conditions(conn: Any, record: dict[str, Any], confidence: float) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    uncertain_fields = set(review_uncertain_fields(record))
    plausibility_findings = parse_payload_plausibility_findings(record)
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []

    def add(condition: str, message: str, **extra: Any) -> None:
        if any(item["condition"] == condition and item.get("new_value") == extra.get("new_value") for item in conditions):
            return
        payload = {"condition": condition, "message": message}
        payload.update({key: value for key, value in extra.items() if value not in (None, "", [])})
        conditions.append(payload)

    if bounded_float(confidence) < 60:
        add("low_ocr_confidence", "OCR confidence is below the canonical apply threshold.", confidence=bounded_float(confidence))

    parsed_note_rows = []
    for note in notes:
        raw_line = str(note.get("raw") if isinstance(note, dict) else note).strip()
        if not raw_line:
            continue
        parsed = parse_note_line(raw_line, str(record.get("type") or "unknown").lower())
        parsed_note_rows.append((raw_line, parsed))
        if parsed["parsed_type"] in {"unknown", "unlabeled_numeric_note"}:
            add(
                "uncertain_mouse_id_format",
                "Mouse ID or note-line format is uncertain and must remain reviewable.",
                raw_extracted_value=raw_line,
            )

    by_display_id: dict[str, set[str]] = {}
    for raw_line, parsed in parsed_note_rows:
        display_id = str(parsed.get("parsed_mouse_display_id") or "")
        if display_id:
            by_display_id.setdefault(display_id, set()).add(raw_line)
    for display_id, raw_lines in by_display_id.items():
        if len(raw_lines) > 1:
            add(
                "snapshot_value_conflict",
                "Same cage/card snapshot contains conflicting note-line values for one mouse ID.",
                new_value=display_id,
                raw_extracted_value=", ".join(sorted(raw_lines)),
            )
        existing = conn.execute(
            """
            SELECT mouse_id, source_note_item_id
            FROM mouse_master
            WHERE display_id = ?
              AND status IN ('active', 'mating', 'pre_weaning', 'weaning_pending')
            LIMIT 1
            """,
            (display_id,),
        ).fetchone()
        if existing is not None:
            add(
                "canonical_state_conflict",
                "Parsed value conflicts with existing canonical structured state.",
                existing_value=existing["mouse_id"],
                new_value=display_id,
                evidence_reference=existing["source_note_item_id"],
            )

    if any(finding.get("severity") == "high" for finding in plausibility_findings) or invalid_iso_date(record.get("dobNormalized")):
        add(
            "biologically_unlikely",
            "Date, mating, litter, genotype, or biological plausibility check requires review.",
            proposed_normalized_value=str(record.get("dobNormalized") or ""),
        )

    raw_visible_lines = record.get("rawVisibleTextLines") if isinstance(record.get("rawVisibleTextLines"), list) else []
    if not raw_visible_lines or any(raw_line in {"?", "-", "unknown"} for raw_line, _parsed in parsed_note_rows):
        add("insufficient_source_evidence", "Source evidence is missing or too weak for automatic canonical use.")

    normalized_pairs = [
        ("rawStrain", "matchedStrain", "matched_strain"),
        ("sexRaw", "sexNormalized", "sex_raw"),
        ("dobRaw", "dobNormalized", "dob_normalized"),
        ("matingDateRaw", "matingDateNormalized", "mating_date_normalized"),
    ]
    for raw_key, normalized_key, field_name in normalized_pairs:
        raw_value = str(record.get(raw_key) or "").strip()
        normalized_value = str(record.get(normalized_key) or "").strip()
        if normalized_value and raw_value and raw_value != normalized_value and field_name in uncertain_fields:
            add(
                "unconfirmed_normalization_rule",
                "Proposed normalized value depends on an uncertain or unconfirmed normalization rule.",
                raw_extracted_value=raw_value,
                proposed_normalized_value=normalized_value,
            )

    return conditions


def review_item_trigger_json(
    record: dict[str, Any],
    confidence: float,
    reason: str,
    required_conditions: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {
            "reason": reason,
            "confidence": bounded_float(confidence),
            "uncertain_fields": review_uncertain_fields(record),
            "plausibility_findings": parse_payload_plausibility_findings(record),
            "review_required_conditions": required_conditions or [],
        },
        ensure_ascii=False,
    )


def write_photo_evidence_items(
    conn: Any,
    *,
    parse_id: str,
    photo_id: str,
    card_snapshot_id: str,
    record: dict[str, Any],
    created_at: str,
) -> int:
    uncertain_fields = {
        normalize_uncertain_field_name(field)
        for field in record.get("uncertainFields", [])
        if normalize_uncertain_field_name(field)
    }
    card_type = str(record.get("type") or "")
    written = 0

    for field_name, (record_key, _default_roi) in PHOTO_EVIDENCE_FIELD_MAP.items():
        observed_value = str(record.get(record_key) or "").strip()
        if not observed_value:
            continue
        raw_extracted_value, normalized_value = photo_evidence_raw_normalized_values(record, field_name)
        roi_label = roi_label_for_field(record, field_name)
        needs_review = 1 if field_name in uncertain_fields else 0
        review_reason = f"Field {field_name} was marked uncertain by the transcription draft." if needs_review else ""
        conn.execute(
            """
            INSERT OR REPLACE INTO photo_evidence_item
                (photo_evidence_id, source_photo_id, parse_id, card_snapshot_id,
                 card_type, evidence_kind, roi_label, observed_raw_text,
                 ocr_text, parsed_value, raw_extracted_value, normalized_value,
                 confidence, confidence_source, evidence_reference_json, interpretation,
                 needs_review, review_reason, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pe_{parse_id}_{field_name}",
                photo_id,
                parse_id,
                card_snapshot_id or None,
                card_type,
                "card_field",
                roi_label,
                observed_value,
                observed_value,
                observed_value,
                raw_extracted_value,
                normalized_value,
                bounded_float(record.get("confidence")),
                f"{str(record.get('extractionMethod') or 'manual_photo_transcription')}:card_field",
                photo_evidence_reference_json(
                    source_photo_id=photo_id,
                    parse_id=parse_id,
                    card_snapshot_id=card_snapshot_id,
                    evidence_kind="card_field",
                    roi_label=roi_label,
                ),
                "",
                needs_review,
                review_reason,
                "review_open" if needs_review else "draft",
                created_at,
                created_at,
            ),
        )
        written += 1

    note_rows = conn.execute(
        """
        SELECT note_item_id, card_type, raw_line_text, parsed_type,
               parsed_mouse_display_id, parsed_ear_label_raw,
               parsed_ear_label_code, confidence, needs_review
        FROM card_note_item_log
        WHERE parse_id = ?
        ORDER BY line_number
        """,
        (parse_id,),
    ).fetchall()
    for row in note_rows:
        parsed_bits = [
            str(row["parsed_type"] or ""),
            str(row["parsed_mouse_display_id"] or ""),
            str(row["parsed_ear_label_code"] or row["parsed_ear_label_raw"] or ""),
        ]
        parsed_value = " ".join(bit for bit in parsed_bits if bit).strip()
        needs_review = int(row["needs_review"] or 0)
        roi_label = roi_label_for_field(record, "notes")
        conn.execute(
            """
            INSERT OR REPLACE INTO photo_evidence_item
                (photo_evidence_id, source_photo_id, parse_id, card_snapshot_id,
                 note_item_id, card_type, evidence_kind, roi_label,
                 observed_raw_text, ocr_text, parsed_value,
                 raw_extracted_value, normalized_value, confidence,
                 confidence_source, evidence_reference_json,
                 interpretation, needs_review, review_reason, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pe_{row['note_item_id']}",
                photo_id,
                parse_id,
                card_snapshot_id or None,
                row["note_item_id"],
                row["card_type"] or card_type,
                "note_line",
                roi_label,
                row["raw_line_text"] or "",
                row["raw_line_text"] or "",
                parsed_value,
                row["raw_line_text"] or "",
                parsed_value,
                bounded_float(row["confidence"]),
                f"{str(record.get('extractionMethod') or 'manual_photo_transcription')}:note_line",
                photo_evidence_reference_json(
                    source_photo_id=photo_id,
                    parse_id=parse_id,
                    card_snapshot_id=card_snapshot_id,
                    evidence_kind="note_line",
                    roi_label=roi_label,
                    note_item_id=str(row["note_item_id"] or ""),
                ),
                "",
                needs_review,
                "Note line requires review before canonical use." if needs_review else "",
                "review_open" if needs_review else "draft",
                created_at,
                created_at,
            ),
        )
        written += 1

    return written


def link_review_to_photo_evidence_items(
    conn: Any,
    *,
    review_id: str,
    parse_id: str,
    created_at: str,
) -> int:
    evidence_rows = conn.execute(
        """
        SELECT photo_evidence_id
        FROM photo_evidence_item
        WHERE parse_id = ?
        ORDER BY evidence_kind, roi_label, observed_raw_text, photo_evidence_id
        """,
        (parse_id,),
    ).fetchall()
    linked = 0
    for row in evidence_rows:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO review_evidence_link
                (link_id, review_id, photo_evidence_id, link_reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"rel_{review_id}_{row['photo_evidence_id']}",
                review_id,
                row["photo_evidence_id"],
                "Review the parsed transcription against linked photo evidence before canonical writes.",
                created_at,
            ),
        )
        linked += max(cursor.rowcount, 0)
    return linked


def validation_review_for_record(conn: Any, record: dict[str, Any], status: str) -> dict[str, str] | None:
    if status in {"review", "conflict"}:
        return None

    review_field = str(record.get("reviewField") or "").lower()
    issue = str(record.get("issue") or "").lower()
    card_type = str(record.get("type") or "").lower()

    if card_type == "separated" and (review_field == "mousecount" or "count mismatch" in issue):
        expected = declared_total_count(record)
        active_count = active_mouse_note_count(record)
        if expected is None or expected != active_count:
            return {
                "severity": "Medium",
                "issue": "Count mismatch",
                "currentValue": str(record.get("mouseCount") or ""),
                "suggestedValue": f"{active_count} active parsed note line{'s' if active_count != 1 else ''}",
                "reviewReason": "Parsed sex/count does not match the active unstruck mouse note lines. Review before creating canonical mouse candidates.",
            }

    if review_field == "mouseid" or "duplicate active" in issue:
        mouse_id = str(record.get("currentValue") or "").strip()
        if mouse_id:
            existing = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM mouse_master
                WHERE display_id = ? AND status IN ('active', 'mating')
                """,
                (mouse_id,),
            ).fetchone()["count"]
        else:
            existing = 0
        return {
            "severity": "High" if existing else str(record.get("severity") or "High"),
            "issue": "Duplicate active mouse",
            "currentValue": mouse_id or str(record.get("currentValue") or ""),
            "suggestedValue": "Resolve movement or confirm this is a different mouse before accepting",
            "reviewReason": "Mouse ID appears as a duplicate-active risk. Review movement/source evidence before writing mouse state.",
        }

    return None


def write_note_items_and_mouse_candidates(
    conn: Any,
    parse_id: str,
    record: dict[str, Any],
    status: str,
    card_snapshot_id: str = "",
) -> tuple[int, int, int]:
    card_type = str(record.get("type") or "unknown").lower()
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []
    note_count = 0
    mouse_count = 0
    ear_review_count = 0
    write_mouse = should_write_mouse_candidate(record, status)
    dob_raw, dob_start, dob_end = split_dob_range(record.get("dobRaw"), record.get("dobNormalized"))
    raw_strain_text = str(record.get("matchedStrain") or record.get("rawStrain") or "")
    photo_id = str(record.get("sourcePhotoId") or "")
    snapshot_id = card_snapshot_id or str(record.get("cardSnapshotId") or "")
    numeric_note_review_items: list[dict[str, Any]] = []
    labeling_rule_set_id = str(record.get("labelingRuleSetId") or record.get("labeling_rule_set_id") or "").strip()
    ear_sequence = load_labeling_rule_ear_sequence(conn, labeling_rule_set_id) if labeling_rule_set_id else []
    crossed_out_handling = (
        load_labeling_rule_crossed_out_handling(conn, labeling_rule_set_id) if labeling_rule_set_id else ""
    )
    labeling_rule_context = load_labeling_rule_context(conn, labeling_rule_set_id) if labeling_rule_set_id else {}
    ear_sequence_index = 0

    for index, note in enumerate(notes, start=1):
        raw_line = str(note.get("raw") if isinstance(note, dict) else note)
        strike_status = str(note.get("strike") or "none") if isinstance(note, dict) else "none"
        note_item_id = f"note_{parse_id}_{index}"
        parsed = parse_note_line(raw_line, card_type)
        status_from_strike = interpreted_status(card_type, strike_status)
        interpreted = (
            "needs_label_review"
            if parsed["parsed_type"] == "unlabeled_numeric_note"
            else status_from_strike if parsed["parsed_type"] != "unknown" else "unknown"
        )
        parsed_metadata = dict(parsed.get("parsed_metadata") or {})
        note_needs_review = int(parsed["needs_review"] or 0)
        if parsed["parsed_type"] == "mouse_item" and labeling_rule_set_id:
            expected_ear_label_code = None
            label_status = (
                interpret_crossed_out_status(strike_status, crossed_out_handling)
                if crossed_out_handling
                else interpreted
            )
            if label_status != "dead":
                expected_ear_label_code = ear_sequence[ear_sequence_index] if ear_sequence_index < len(ear_sequence) else None
                ear_sequence_index += 1
            parsed_metadata["expected_ear_label_code"] = expected_ear_label_code
            parsed_metadata["labeling_rule_set_id"] = labeling_rule_set_id
            if labeling_rule_context:
                parsed_metadata["labeling_rule_snapshot_hash"] = build_rule_snapshot(
                    {**labeling_rule_context, "expected_ear_label_code": expected_ear_label_code}
                )["rule_hash"]
        has_hybrid_candidate_input = isinstance(note, dict) and (
            note.get("ocrCandidate")
            or note.get("ocr_candidate")
            or note.get("aiCandidate")
            or note.get("ai_candidate")
        )
        if has_hybrid_candidate_input:
            rule_context = dict(labeling_rule_context)
            if "expected_ear_label_code" in parsed_metadata:
                rule_context["expected_ear_label_code"] = parsed_metadata["expected_ear_label_code"]
            evaluator_result = evaluate_note_line_candidate(
                ocr_candidate=note.get("ocrCandidate") or note.get("ocr_candidate"),
                ai_candidate=note.get("aiCandidate") or note.get("ai_candidate"),
                parsed_note_row={
                    **parsed,
                    "raw_line_text": raw_line,
                    "photo_id": photo_id or None,
                    "parse_id": parse_id,
                    "note_item_id": note_item_id,
                    "line_number": index,
                    "card_snapshot_id": snapshot_id or None,
                    "roi_ref": note.get("roiRef") or note.get("roi_ref"),
                    "strike_status": strike_status,
                    "interpreted_status": interpreted,
                },
                source_quality=note.get("sourceQuality") or note.get("source_quality"),
                rule_context=rule_context or None,
            )
            parsed_metadata["hybrid_note_line_evaluator"] = evaluator_result
            if evaluator_result["review_routing"]["must_review"]:
                note_needs_review = 1
        conn.execute(
            """
            INSERT OR REPLACE INTO card_note_item_log
                (note_item_id, photo_id, parse_id, card_snapshot_id, card_type, line_number, raw_line_text, strike_status,
                 parsed_type, interpreted_status, parsed_mouse_display_id, parsed_ear_label_raw,
                 parsed_ear_label_code, parsed_ear_label_confidence, parsed_ear_label_review_status,
                 parsed_event_date, parsed_count, parsed_metadata_json, confidence, needs_review)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_item_id,
                photo_id or None,
                parse_id,
                snapshot_id or None,
                card_type,
                index,
                raw_line,
                strike_status,
                parsed["parsed_type"],
                interpreted,
                parsed["parsed_mouse_display_id"],
                parsed["parsed_ear_label_raw"],
                parsed["parsed_ear_label_code"],
                parsed["parsed_ear_label_confidence"],
                parsed["parsed_ear_label_review_status"],
                parsed["parsed_event_date"],
                parsed["parsed_count"],
                json.dumps(parsed_metadata, ensure_ascii=False),
                parsed["confidence"],
                note_needs_review,
            ),
        )
        note_count += 1
        if parsed["parsed_type"] == "mouse_item" and parsed["parsed_ear_label_review_status"] in {"check", "needs_review"}:
            review_id = f"review_ear_{note_item_id}"
            suggested_value = parsed["parsed_ear_label_code"] or "Confirm raw note line before normalization"
            conn.execute(
                """
                INSERT OR REPLACE INTO review_queue
                    (review_id, parse_id, severity, issue, current_value, suggested_value,
                     review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    parse_id,
                    "Medium",
                    "Ear label needs review",
                    str(parsed["parsed_ear_label_raw"] or raw_line),
                    str(suggested_value),
                    f"Note item {note_item_id} has uncertain ear label evidence. Confirm against the source cage card before using a normalized ear label.",
                    "open",
                    utc_now(),
                ),
            )
            ear_review_count += 1

        if parsed["parsed_type"] == "unlabeled_numeric_note":
            metadata = parsed.get("parsed_metadata", {}) if isinstance(parsed.get("parsed_metadata"), dict) else {}
            numeric_note_review_items.append(
                {
                    "note_item_id": note_item_id,
                    "raw_line": raw_line,
                    "display": metadata.get("display_ko") or raw_line,
                    "labels": metadata.get("labels") if isinstance(metadata.get("labels"), list) else [],
                }
            )

        if write_mouse and parsed["parsed_type"] == "mouse_item" and parsed["parsed_mouse_display_id"]:
            display_id = str(parsed["parsed_mouse_display_id"])
            mouse_id = f"mouse_{display_id}_{parse_id}".replace(" ", "_")
            reviewed_ear_code = (
                parsed["parsed_ear_label_code"]
                if parsed["parsed_ear_label_review_status"] == "auto_filled"
                else None
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO mouse_master
                    (mouse_id, display_id, id_prefix, raw_strain_text, dob_raw, dob_start, dob_end,
                     ear_label_raw, ear_label_code, ear_label_confidence, ear_label_review_status,
                     source_note_item_id, status, last_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mouse_id,
                    display_id,
                    mouse_id_prefix(display_id),
                    raw_strain_text,
                    dob_raw,
                    dob_start,
                    dob_end,
                    parsed["parsed_ear_label_raw"],
                    reviewed_ear_code,
                    parsed["parsed_ear_label_confidence"],
                    parsed["parsed_ear_label_review_status"],
                    note_item_id,
                    status_from_strike,
                    utc_now(),
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"event_{mouse_id}_{index}",
                    mouse_id,
                    "note_added",
                    dob_start or utc_now()[:10],
                    "note_item",
                    note_item_id,
                    json.dumps(
                        {
                            "display_id": display_id,
                            "interpreted_status": status_from_strike,
                            "raw_line_text": raw_line,
                            "raw_strain_text": raw_strain_text,
                        },
                        ensure_ascii=False,
                    ),
                    utc_now(),
                ),
            )
            mouse_count += 1
    if numeric_note_review_items:
        labels: list[str] = []
        display_values: list[str] = []
        note_item_ids: list[str] = []
        for item in numeric_note_review_items:
            note_item_ids.append(str(item["note_item_id"]))
            item_labels = [str(label).strip() for label in item["labels"] if str(label).strip()]
            labels.extend(item_labels or [str(item["raw_line"]).strip()])
            display_values.append(str(item["display"]).strip() or str(item["raw_line"]).strip())
        line_count = len(numeric_note_review_items)
        review_id = f"review_unlabeled_numeric_{parse_id}"
        current_value = ", ".join(label for label in labels if label)
        suggested_value = "Confirm as temporary labels, ignore, or map to mouse IDs."
        review_reason = (
            f"Parse {parse_id} has {line_count} numeric-only note lines "
            f"({'; '.join(display_values)}). Treat them as grouped temporary cage evidence "
            f"until labels are assigned. Source note items: {', '.join(note_item_ids)}."
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO review_queue
                (review_id, parse_id, severity, issue, current_value, suggested_value,
                 review_reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                parse_id,
                "Medium",
                "Unlabeled numeric note needs review",
                current_value,
                suggested_value,
                review_reason,
                "open",
                utc_now(),
            ),
        )
    return note_count, mouse_count, ear_review_count


@app.get("/api/assigned-strains")
def list_assigned_strains() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT assigned_strain_id, display_name, aliases_json, source_type,
                   source_reference, active, assigned_at, removed_at, notes
            FROM my_assigned_strain
            ORDER BY active DESC, display_name COLLATE NOCASE
            """
        ).fetchall()
    return [assigned_strain_row(row) for row in rows]


@app.post("/api/assigned-strains")
def create_assigned_strain(payload: AssignedStrainCreate) -> dict[str, Any]:
    display_name = " ".join(payload.display_name.split())
    aliases = sorted({" ".join(alias.split()) for alias in payload.aliases if alias.strip()})
    if not display_name:
        raise HTTPException(status_code=400, detail="Assigned strain display name is required.")

    assigned_strain_id = new_id("assigned_strain")
    assigned_at = utc_now()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO my_assigned_strain
                (assigned_strain_id, display_name, aliases_json, source_type,
                 source_reference, active, assigned_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assigned_strain_id,
                display_name,
                json.dumps(aliases, ensure_ascii=False),
                payload.source_type,
                payload.source_reference,
                1,
                assigned_at,
                payload.notes,
            ),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "assigned_strain_created",
                assigned_strain_id,
                None,
                json.dumps({"display_name": display_name, "aliases": aliases}, ensure_ascii=False),
                assigned_at,
            ),
        )

    return {
        "assigned_strain_id": assigned_strain_id,
        "display_name": display_name,
        "aliases": aliases,
        "source_type": payload.source_type,
        "source_reference": payload.source_reference,
        "active": True,
        "assigned_at": assigned_at,
        "removed_at": None,
        "notes": payload.notes,
    }


@app.post("/api/assigned-strains/{assigned_strain_id}/deactivate")
def deactivate_assigned_strain(assigned_strain_id: str) -> dict[str, Any]:
    removed_at = utc_now()
    with connection() as conn:
        existing = conn.execute(
            "SELECT display_name, active FROM my_assigned_strain WHERE assigned_strain_id = ?",
            (assigned_strain_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Assigned strain not found.")
        conn.execute(
            """
            UPDATE my_assigned_strain
            SET active = 0, removed_at = ?
            WHERE assigned_strain_id = ?
            """,
            (removed_at, assigned_strain_id),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "assigned_strain_deactivated",
                assigned_strain_id,
                json.dumps({"active": bool(existing["active"])}, ensure_ascii=False),
                json.dumps({"active": False}, ensure_ascii=False),
                removed_at,
            ),
        )
    return {"assigned_strain_id": assigned_strain_id, "active": False, "removed_at": removed_at}


@app.get("/api/distribution-imports")
def list_distribution_imports() -> list[dict[str, Any]]:
    with connection() as conn:
        imports = conn.execute(
            """
            SELECT distribution_import_id, source_file_name, source_file_path,
                   received_date, sheet_name, imported_at, status, notes
            FROM distribution_import
            ORDER BY imported_at DESC
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT assignment_row_id, distribution_import_id, source_sheet,
                   source_row_number, institution_or_group, responsible_person_raw,
                   mating_type_raw, matched_strain_id, cage_count_raw,
                   mating_cage_count_raw, confidence, review_status, traceability
            FROM distribution_assignment_row
            ORDER BY distribution_import_id, source_row_number
            """
        ).fetchall()

    rows_by_import: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        payload = distribution_row_payload(row)
        rows_by_import.setdefault(payload["distribution_import_id"], []).append(payload)

    return [
        {**dict(import_row), "rows": rows_by_import.get(import_row["distribution_import_id"], [])}
        for import_row in imports
    ]


@app.post("/api/distribution-imports")
def create_distribution_import(payload: DistributionImportPayload) -> dict[str, Any]:
    if payload.layer and payload.layer != "parsed or intermediate result":
        raise HTTPException(status_code=400, detail="Distribution import must be parsed/intermediate JSON.")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="Distribution import requires parsed rows.")

    import_id = new_id("distribution_import")
    imported_at = utc_now()
    source_file_name = payload.source_file_name or "distribution_import.json"
    with connection() as conn:
        source_record_id = create_source_record(
            conn,
            source_type="excel_row_import",
            source_uri=payload.source_file_path,
            source_label=source_file_name,
            raw_payload=payload.model_dump_json(),
            note="Distribution assignment import remains parsed/intermediate until reviewed.",
        )
        conn.execute(
            """
            INSERT INTO distribution_import
                (distribution_import_id, source_file_name, source_file_path,
                 received_date, sheet_name, imported_at, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                source_file_name,
                payload.source_file_path,
                payload.received_date,
                payload.sheet_name,
                imported_at,
                "parsed",
                payload.description,
            ),
        )
        inserted_rows = 0
        for index, row in enumerate(payload.rows, start=1):
            mating_type = str(row.get("mating_type_raw") or row.get("matingTypeRaw") or "").strip()
            if not mating_type:
                continue
            source_row = parse_optional_int(row.get("source_row_number") or row.get("sourceRowNumber"))
            conn.execute(
                """
                INSERT INTO distribution_assignment_row
                    (assignment_row_id, distribution_import_id, source_sheet,
                     source_row_number, institution_or_group, responsible_person_raw,
                     mating_type_raw, matched_strain_id, cage_count_raw,
                     mating_cage_count_raw, confidence, review_status, traceability)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("assignment_row"),
                    import_id,
                    payload.sheet_name,
                    source_row,
                    str(row.get("institution_or_group") or row.get("institutionOrGroup") or ""),
                    str(row.get("responsible_person_raw") or row.get("responsiblePersonRaw") or ""),
                    mating_type,
                    str(row.get("matched_strain_id") or row.get("matchedStrainId") or "") or None,
                    str(row.get("cage_count_raw") or row.get("cageCountRaw") or ""),
                    str(row.get("mating_cage_count_raw") or row.get("matingCageCountRaw") or ""),
                    float(row.get("confidence") or 0),
                    str(row.get("review_status") or row.get("reviewStatus") or "candidate"),
                    json.dumps(
                        {
                            "source_file_name": source_file_name,
                            "sheet_name": payload.sheet_name,
                            "source_row_number": source_row or index,
                            "source_cells": row.get("source_cells") or row.get("sourceCells") or {},
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            inserted_rows += 1
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "distribution_import_created",
                import_id,
                None,
                json.dumps(
                    {
                        "source_file_name": source_file_name,
                        "rows": inserted_rows,
                        "source_record_id": source_record_id,
                    },
                    ensure_ascii=False,
                ),
                imported_at,
            ),
        )

    return {
        "distribution_import_id": import_id,
        "source_record_id": source_record_id,
        "source_file_name": source_file_name,
        "sheet_name": payload.sheet_name,
        "imported_at": imported_at,
        "status": "parsed",
        "stored_rows": inserted_rows,
        "boundary": "parsed or intermediate result",
    }


@app.get("/api/legacy-workbook-imports")
def list_legacy_workbook_imports() -> list[dict[str, Any]]:
    with connection() as conn:
        imports = conn.execute(
            """
            SELECT legacy_workbook_import.legacy_import_id,
                   legacy_workbook_import.source_record_id,
                   legacy_workbook_import.source_file_name,
                   legacy_workbook_import.source_file_path,
                   legacy_workbook_import.workbook_kind,
                   legacy_workbook_import.sheet_name,
                   legacy_workbook_import.imported_at,
                   legacy_workbook_import.status,
                   legacy_workbook_import.notes,
                   parse.raw_payload AS raw_payload,
                   (
                       SELECT COUNT(*)
                       FROM review_queue review
                       WHERE review.parse_id = 'legacy_parse_' || legacy_workbook_import.legacy_import_id
                   ) AS review_count,
                   (
                       SELECT COUNT(*)
                       FROM review_queue review
                       WHERE review.parse_id = 'legacy_parse_' || legacy_workbook_import.legacy_import_id
                         AND review.status = 'open'
                   ) AS open_review_count
            FROM legacy_workbook_import
            LEFT JOIN parse_result parse
              ON parse.parse_id = 'legacy_parse_' || legacy_workbook_import.legacy_import_id
            ORDER BY imported_at DESC
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT legacy_row_id, legacy_import_id, review_id, row_type, source_sheet,
                   source_row_number, raw_row_json, review_status
            FROM legacy_workbook_row
            ORDER BY legacy_import_id, source_row_number
            """
        ).fetchall()

    rows_by_import: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        payload = legacy_row_payload(row)
        rows_by_import.setdefault(payload["legacy_import_id"], []).append(payload)

    return [
        {**legacy_import_payload(import_row), "rows": rows_by_import.get(import_row["legacy_import_id"], [])}
        for import_row in imports
    ]


@app.post("/api/legacy-workbook-imports")
def create_legacy_workbook_import(
    file: UploadFile = File(...),
    kind: str = Form("auto"),
    sheet_name: str = Form(""),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")
    if kind not in {"auto", "animal", "separation"}:
        raise HTTPException(status_code=400, detail="Legacy workbook kind must be auto, animal, or separation.")

    import_id = new_id("legacy_import")
    parse_id = f"legacy_parse_{import_id}"
    stored_path = save_legacy_workbook(file, import_id)
    source_uri = storage_trace_path(stored_path)
    try:
        parsed = parse_workbook(stored_path, kind=kind, sheet_name=sheet_name.strip() or None)
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not parse legacy workbook: {error}") from error

    rows = parsed.get("rows") or []
    if not isinstance(rows, list):
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Legacy workbook parser returned an invalid row payload.")

    imported_at = utc_now()
    raw_payload = json.dumps(parsed, ensure_ascii=False)
    parse_raw_payload = json.dumps(
        tagged_parse_payload(
            parsed,
            payload_kind="legacy_workbook_parse",
            source_layer="parsed or intermediate result",
        ),
        ensure_ascii=False,
    )
    strain_registry_candidates = parsed.get("strain_registry_candidates")
    if not isinstance(strain_registry_candidates, list):
        strain_registry_candidates = []
    inserted_rows = 0
    created_review_items = 0
    try:
        with connection() as conn:
            source_record_id = create_source_record(
                conn,
                source_type="legacy_workbook",
                source_uri=source_uri,
                source_label=file.filename,
                raw_payload=raw_payload,
                note="Predecessor Excel workbook preserved as an export/view source; parsed rows stay review candidates.",
            )
            conn.execute(
                """
                INSERT INTO parse_result
                    (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parse_id,
                    None,
                    file.filename,
                    parse_raw_payload,
                    imported_at,
                    "review",
                    1,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO legacy_workbook_import
                    (legacy_import_id, source_record_id, source_file_name, source_file_path,
                     workbook_kind, sheet_name, imported_at, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    source_record_id,
                    file.filename,
                    source_uri,
                    str(parsed.get("workbook_kind") or ""),
                    str(parsed.get("sheet_name") or ""),
                    imported_at,
                    "parsed",
                    "Legacy Excel rows are parsed/intermediate review candidates, not canonical colony state.",
                ),
            )
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                row_number = parse_optional_int(row.get("source_row_number")) or index
                legacy_row_id = new_id("legacy_row")
                review_id = new_id("review")
                conn.execute(
                    """
                    INSERT INTO review_queue
                        (review_id, parse_id, severity, issue, current_value, suggested_value,
                         review_reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        parse_id,
                        "Medium",
                        "Legacy workbook row requires review",
                        legacy_review_current_value(row, row_number),
                        str(row.get("row_type") or "candidate"),
                        legacy_review_reason(
                            row,
                            file.filename,
                            str(parsed.get("sheet_name") or ""),
                            row_number,
                        ),
                        "open",
                        imported_at,
                    ),
                )
                created_review_items += 1
                conn.execute(
                    """
                    INSERT INTO legacy_workbook_row
                        (legacy_row_id, legacy_import_id, review_id, row_type, source_sheet,
                         source_row_number, raw_row_json, review_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        legacy_row_id,
                        import_id,
                        review_id,
                        str(row.get("row_type") or ""),
                        str(row.get("source_sheet") or parsed.get("sheet_name") or ""),
                        row_number,
                        json.dumps(row, ensure_ascii=False),
                        str(row.get("review_status") or "candidate"),
                    ),
                )
                inserted_rows += 1
            for candidate in strain_registry_candidates:
                if not isinstance(candidate, dict):
                    continue
                review_id = new_id("review")
                issue = "Legacy strain registry candidate requires review"
                review_reason = strain_registry_candidate_review_reason(
                    candidate,
                    file.filename,
                    str(parsed.get("sheet_name") or ""),
                )
                conn.execute(
                    """
                    INSERT INTO review_queue
                        (review_id, parse_id, severity, issue, current_value, suggested_value,
                         review_reason, assigned_role, priority, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        parse_id,
                        "Medium",
                        issue,
                        json.dumps(candidate, ensure_ascii=False),
                        "Review or create a strain registry link without overwriting raw workbook evidence.",
                        review_reason,
                        review_assigned_role(issue, review_reason, file.filename),
                        review_priority("Medium", issue, review_reason),
                        "open",
                        imported_at,
                    ),
                )
                created_review_items += 1
            conn.execute(
                """
                INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("action"),
                    "legacy_workbook_import_created",
                    import_id,
                    None,
                    json.dumps(
                        {
                            "source_file_name": file.filename,
                            "source_record_id": source_record_id,
                            "parse_id": parse_id,
                            "workbook_kind": parsed.get("workbook_kind"),
                            "rows": inserted_rows,
                            "created_review_items": created_review_items,
                            "strain_registry_candidates": len(strain_registry_candidates),
                            "boundary": "parsed or intermediate result",
                        },
                        ensure_ascii=False,
                    ),
                    imported_at,
                ),
            )
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    return {
        "legacy_import_id": import_id,
        "parse_id": parse_id,
        "source_record_id": source_record_id,
        "source_file_name": file.filename,
        "source_file_path": source_uri,
        "workbook_kind": parsed.get("workbook_kind"),
        "sheet_name": parsed.get("sheet_name"),
        "imported_at": imported_at,
        "status": "parsed",
        "stored_rows": inserted_rows,
        "created_review_items": created_review_items,
        "strain_registry_candidate_count": len(strain_registry_candidates),
        "boundary": "parsed or intermediate result",
    }


@app.post("/api/photos")
def upload_photo(file: UploadFile = File(...), upload_batch_id: str = Form("")) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")
    photo_id = new_id("photo")
    stored_path = save_upload(file, photo_id)
    uploaded_at = utc_now()
    try:
        with connection() as conn:
            batch_id = upload_batch_id.strip()
            if batch_id:
                batch = conn.execute(
                    "SELECT upload_batch_id FROM upload_batch WHERE upload_batch_id = ?",
                    (batch_id,),
                ).fetchone()
                if batch is None:
                    raise HTTPException(status_code=404, detail="Upload batch not found.")
                conn.execute(
                    "UPDATE upload_batch SET updated_at = ? WHERE upload_batch_id = ?",
                    (uploaded_at, batch_id),
                )
            else:
                batch = create_upload_batch_record(
                    conn,
                    batch_label=f"Single photo upload - {file.filename}",
                    expected_photo_count=1,
                    note="Automatically created for a direct photo upload.",
                )
                batch_id = batch["upload_batch_id"]
            source_record_id = create_source_record(
                conn,
                source_type="photo",
                source_uri=storage_trace_path(stored_path),
                source_label=file.filename,
                raw_payload=json.dumps(
                    {"original_filename": file.filename, "upload_batch_id": batch_id},
                    ensure_ascii=False,
                ),
                note="Uploaded cage card photo retained as raw source evidence.",
            )
            conn.execute(
                """
                INSERT INTO photo_log
                    (photo_id, upload_batch_id, original_filename, stored_path, uploaded_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (photo_id, batch_id, file.filename, storage_trace_path(stored_path), uploaded_at, "uploaded"),
            )
            review_candidate = ensure_photo_review_candidate(
                conn,
                photo_id=photo_id,
                original_filename=file.filename,
                stored_path=storage_trace_path(stored_path),
                uploaded_at=uploaded_at,
                source_record_id=source_record_id,
            )
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    return {
        "photo_id": photo_id,
        "original_filename": file.filename,
        "stored_path": storage_trace_path(stored_path),
        "uploaded_at": uploaded_at,
        "status": "review_pending",
        "upload_batch_id": batch_id,
        "source_record_id": source_record_id,
        "review_candidate": review_candidate,
    }


@app.post("/api/photos/review-candidates")
def create_missing_photo_review_candidates() -> dict[str, Any]:
    created = 0
    existing = 0
    with connection() as conn:
        photos = conn.execute(
            """
            SELECT photo.photo_id, photo.original_filename, photo.stored_path,
                   photo.uploaded_at,
                   source.source_record_id
            FROM photo_log photo
            LEFT JOIN source_record source
                ON source.source_type = 'photo'
               AND source.source_uri = photo.stored_path
               AND source.source_label = photo.original_filename
            ORDER BY photo.uploaded_at
            """
        ).fetchall()
        for photo in photos:
            result = ensure_photo_review_candidate(
                conn,
                photo_id=photo["photo_id"],
                original_filename=photo["original_filename"],
                stored_path=photo["stored_path"],
                uploaded_at=photo["uploaded_at"],
                source_record_id=photo["source_record_id"] or "",
            )
            if result["created"]:
                created += 1
            else:
                existing += 1
    return {
        "created_review_candidates": created,
        "existing_review_candidates": existing,
        "boundary": "review item",
        "source_layer": "raw source",
    }


def supersede_open_ai_photo_reviews(conn: Any, photo_id: str, now: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT parse.parse_id, review.review_id, parse.raw_payload
        FROM parse_result parse
        LEFT JOIN review_queue review
            ON review.parse_id = parse.parse_id
           AND review.status = 'open'
        WHERE parse.photo_id = ?
          AND parse.source_name = 'ai_photo_extraction'
          AND parse.status = 'review'
        """,
        (photo_id,),
    ).fetchall()
    parse_ids = sorted({row["parse_id"] for row in rows})
    review_ids = sorted({row["review_id"] for row in rows if row["review_id"]})
    if not parse_ids:
        return {"superseded_parse_ids": [], "superseded_review_ids": []}

    for row in rows:
        raw_payload = json_object(row["raw_payload"])
        if not raw_payload:
            continue
        raw_payload["status"] = "superseded"
        raw_payload["supersededAt"] = now
        raw_payload["supersededReason"] = "A newer AI ROI extraction was created for the same source photo."
        conn.execute(
            "UPDATE parse_result SET raw_payload = ? WHERE parse_id = ?",
            (json.dumps(raw_payload, ensure_ascii=False), row["parse_id"]),
        )

    parse_placeholders = ",".join("?" for _ in parse_ids)
    conn.execute(
        f"""
        UPDATE parse_result
        SET status = 'superseded',
            needs_review = 0
        WHERE parse_id IN ({parse_placeholders})
        """,
        parse_ids,
    )
    if review_ids:
        review_placeholders = ",".join("?" for _ in review_ids)
        conn.execute(
            f"""
            UPDATE review_queue
            SET status = 'superseded',
                resolved_at = ?,
                resolution_note = ?
            WHERE review_id IN ({review_placeholders})
            """,
            [
                now,
                "Superseded by a newer AI ROI extraction for the same source photo.",
                *review_ids,
            ],
        )
    return {"superseded_parse_ids": parse_ids, "superseded_review_ids": review_ids}


@app.post("/api/photos/{photo_id}/manual-transcription")
def create_photo_manual_transcription(photo_id: str, payload: PhotoManualTranscriptionCreate) -> dict[str, Any]:
    now = utc_now()
    source_name = "ai_photo_extraction" if payload.extraction_method == "ai_photo_extraction" else "manual_photo_transcription"
    issue = "AI-extracted photo transcription needs review" if source_name == "ai_photo_extraction" else "Manual photo transcription needs review"
    review_reason = "Photo transcription is parsed/intermediate evidence. Review it before writing canonical mouse or cage state."
    action_note = (
        "Review AI-extracted fields against the raw cage-card photo before canonical writes."
        if source_name == "ai_photo_extraction"
        else "Keep manual transcription reviewable."
    )
    with connection() as conn:
        photo = conn.execute(
            """
            SELECT photo_id, original_filename, stored_path, uploaded_at, status
            FROM photo_log
            WHERE photo_id = ?
            """,
            (photo_id,),
        ).fetchone()
        if photo is None:
            raise HTTPException(status_code=404, detail="Photo not found.")

        superseded_ai = (
            supersede_open_ai_photo_reviews(conn, photo_id, now)
            if source_name == "ai_photo_extraction"
            else {"superseded_parse_ids": [], "superseded_review_ids": []}
        )
        parse_id = new_id("parse")
        notes = [
            {
                "raw": repair_known_ocr_symbol_mojibake(note.get("raw")).strip(),
                "meaning": str(note.get("meaning") or ""),
                "strike": str(note.get("strike") or "none"),
            }
            for note in payload.notes
            if isinstance(note, dict) and str(note.get("raw") or "").strip()
        ]
        record = tagged_parse_payload(
            {
            "id": parse_id,
            "uploaded": photo["original_filename"],
            "type": infer_card_type_from_sex(payload.card_type or "Separated", payload.sex_raw, payload.sex_normalized),
            "rawStrain": payload.raw_strain,
            "matchedStrain": payload.matched_strain or payload.raw_strain,
            "sexRaw": repair_known_ocr_symbol_mojibake(payload.sex_raw),
            "sexNormalized": payload.sex_normalized,
            "idRaw": payload.id_raw,
            "dobRaw": payload.dob_raw,
            "dobNormalized": payload.dob_normalized,
            "matingDateRaw": payload.mating_date_raw,
            "matingDateNormalized": payload.mating_date_normalized,
            "lmoRaw": payload.lmo_raw,
            "mouseCount": repair_known_ocr_symbol_mojibake(payload.mouse_count),
            "confidence": payload.confidence,
            "status": "review",
            "issue": issue,
            "severity": "Medium",
            "reviewField": "manualTranscription",
            "currentValue": repair_known_ocr_symbol_mojibake(payload.mouse_count) or payload.raw_strain or photo["original_filename"],
            "suggestedValue": "Compare against latest cage-card photo and predecessor Excel candidate rows.",
            "reviewReason": review_reason,
            "notes": notes,
            "actions": [
                "Preserve latest photo as raw source evidence.",
                action_note,
                "Compare against predecessor Excel rows before accepting changes.",
            ],
            "sourcePhotoId": photo_id,
            "sourceLayer": "parsed or intermediate result",
            "reviewerNote": payload.reviewer_note,
            "extractionMethod": payload.extraction_method,
            "rawVisibleTextLines": [
                repair_known_ocr_symbol_mojibake(item).strip()
                for item in payload.raw_visible_text_lines
                if repair_known_ocr_symbol_mojibake(item).strip()
            ][:60],
            "symbolConfusions": [
                str(item).strip()
                for item in payload.symbol_confusions
                if str(item).strip()
            ][:30],
            "uncertainFields": [
                str(item).strip()
                for item in payload.uncertain_fields
                if str(item).strip()
            ][:30],
            "plausibilityFindings": [
                {
                    "field": normalize_uncertain_field_name(item.get("field")),
                    "severity": str(item.get("severity") or "medium").strip().lower(),
                    "message": str(item.get("message") or "").strip(),
                }
                for item in payload.plausibility_findings
                if isinstance(item, dict)
                and normalize_uncertain_field_name(item.get("field"))
                and str(item.get("message") or "").strip()
            ][:10],
            "extractionImageMode": payload.extraction_image_mode,
            "roiTemplateType": payload.roi_template_type,
            "externalApproval": payload.external_approval,
            "payloadMinimization": payload.payload_minimization,
            "extractionRegions": [
                {
                    "label": str(region.get("label") or ""),
                    "displayName": str(region.get("display_name") or region.get("label") or ""),
                    "targetFields": [
                        str(field)
                        for field in (region.get("target_fields") if isinstance(region.get("target_fields"), list) else [])
                        if str(field).strip()
                    ],
                    "mode": str(region.get("mode") or ""),
                }
                for region in payload.extraction_regions
                if isinstance(region, dict)
            ][:20],
            },
            payload_kind=source_name,
            source_layer="parsed or intermediate result",
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
                source_name,
                json.dumps(record, ensure_ascii=False),
                now,
                "review",
                payload.confidence,
                1,
            ),
        )
        card_snapshot_id = create_card_snapshot(conn, parse_id, photo_id, record, now)
        record = {**record, "cardSnapshotId": card_snapshot_id}
        conn.execute(
            "UPDATE parse_result SET raw_payload = ? WHERE parse_id = ?",
            (json.dumps(record, ensure_ascii=False), parse_id),
        )
        note_count, mouse_count, ear_review_count = write_note_items_and_mouse_candidates(
            conn,
            parse_id,
            record,
            "review",
            card_snapshot_id,
        )
        evidence_count = write_photo_evidence_items(
            conn,
            parse_id=parse_id,
            photo_id=photo_id,
            card_snapshot_id=card_snapshot_id,
            record=record,
            created_at=now,
        )
        review_conditions = review_required_conditions(conn, record, payload.confidence)
        review_id = f"review_{parse_id}"
        conn.execute(
            """
            INSERT INTO review_queue
                (review_id, parse_id, severity, issue, current_value, suggested_value,
                 review_reason, source_layer, evidence_reference_json,
                 review_trigger_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                parse_id,
                "Medium",
                issue,
                payload.mouse_count or payload.raw_strain or photo["original_filename"],
                review_item_suggested_value(record),
                "Latest cage-card photo should drive updates, but the parsed transcription itself must be reviewed before canonical writes.",
                "review item",
                review_item_evidence_reference_json(
                    source_photo_id=photo_id,
                    parse_id=parse_id,
                    card_snapshot_id=card_snapshot_id,
                    photo_evidence_items=evidence_count,
                ),
                review_item_trigger_json(
                    record,
                    payload.confidence,
                    "manual_transcription_review_required",
                    required_conditions=review_conditions,
                ),
                "open",
                now,
            ),
        )
        linked_evidence_count = link_review_to_photo_evidence_items(
            conn,
            review_id=review_id,
            parse_id=parse_id,
            created_at=now,
        )
        conn.execute(
            """
            UPDATE review_queue
            SET evidence_reference_json = ?
            WHERE review_id = ?
            """,
            (
                review_item_evidence_reference_json(
                    source_photo_id=photo_id,
                    parse_id=parse_id,
                    card_snapshot_id=card_snapshot_id,
                    photo_evidence_items=evidence_count,
                    linked_photo_evidence_items=linked_evidence_count,
                ),
                review_id,
            ),
        )
        conn.execute("UPDATE photo_log SET status = ? WHERE photo_id = ?", ("transcribed_review_pending", photo_id))
        photo_review_rows = conn.execute(
            """
            SELECT review.review_id
            FROM review_queue review
            JOIN parse_result parse ON parse.parse_id = review.parse_id
            WHERE parse.photo_id = ?
              AND parse.source_name = 'photo_manual_review'
              AND review.status = 'open'
            """,
            (photo_id,),
        ).fetchall()
        resolved_photo_review_ids = [row["review_id"] for row in photo_review_rows]
        if resolved_photo_review_ids:
            placeholders = ",".join("?" for _ in resolved_photo_review_ids)
            conn.execute(
                f"""
                UPDATE review_queue
                SET status = 'resolved',
                    resolved_at = ?,
                    resolution_note = ?
                WHERE review_id IN ({placeholders})
                """,
                [
                    now,
                    "Manual cage-card transcription was entered; review the parsed transcription before canonical writes.",
                    *resolved_photo_review_ids,
                ],
            )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "manual_photo_transcription_created",
                photo_id,
                json.dumps({"resolved_photo_review_ids": resolved_photo_review_ids}, ensure_ascii=False),
                json.dumps(
                    {
                    "parse_id": parse_id,
                    "card_snapshot_id": card_snapshot_id,
                    "review_id": review_id,
                    "note_count": note_count,
                    "evidence_count": evidence_count,
                    "linked_evidence_count": linked_evidence_count,
                    "source_name": source_name,
                    "resolved_photo_review_items": len(resolved_photo_review_ids),
                    "superseded_ai_parse_ids": superseded_ai["superseded_parse_ids"],
                    "superseded_ai_review_ids": superseded_ai["superseded_review_ids"],
                },
                ensure_ascii=False,
                ),
                now,
            ),
        )

    return {
        "parse_id": parse_id,
        "review_id": review_id,
        "card_snapshot_id": card_snapshot_id,
        "photo_id": photo_id,
        "created_note_items": note_count,
        "created_photo_evidence_items": evidence_count,
        "linked_photo_evidence_items": linked_evidence_count,
        "created_mouse_candidates": mouse_count,
        "created_ear_review_items": ear_review_count,
        "resolved_photo_review_items": len(resolved_photo_review_ids),
        "superseded_ai_parse_ids": superseded_ai["superseded_parse_ids"],
        "superseded_ai_review_ids": superseded_ai["superseded_review_ids"],
        "boundary": "parsed or intermediate result",
        "source_name": source_name,
    }


@app.get("/api/review-items")
def list_review_items() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT review.review_id, review.parse_id, review.severity, review.issue,
                   review.current_value, review.suggested_value, review.review_reason,
                   review.assigned_role, review.assigned_to, review.priority,
                   review.source_layer, review.evidence_reference_json,
                   review.review_trigger_json,
                   review.status, review.created_at, review.resolved_at, review.resolution_note,
                   parse.source_name, parse.photo_id, parse.raw_payload AS parse_raw_payload,
                   parse.confidence AS parse_confidence, photo.original_filename,
                   review_note.note_item_id, review_note.raw_line_text AS review_note_raw_line,
                   review_note.parsed_type AS review_note_parsed_type,
                   review_note.interpreted_status AS review_note_interpreted_status,
                   review_note.parsed_mouse_display_id AS review_note_mouse_display_id,
                   review_note.parsed_count AS review_note_count,
                   review_note.parsed_metadata_json AS review_note_parsed_metadata_json,
                   review_snapshot.card_snapshot_id,
                   review_snapshot.card_type AS review_card_type,
                   review_snapshot.card_id_raw AS review_card_id_raw,
                   review_snapshot.raw_strain_text AS review_raw_strain_text,
                   review_snapshot.matched_strain_text AS review_matched_strain_text,
                   review_snapshot.sex_raw AS review_sex_raw,
                   review_snapshot.sex_normalized AS review_sex_normalized,
                   review_snapshot.count_value AS review_count_value,
                   review_snapshot.dob_raw AS review_dob_raw,
                   review_snapshot.note_summary_json AS review_note_summary_json,
                   (
                       SELECT COUNT(*)
                       FROM card_note_item_log note
                       WHERE note.parse_id = review.parse_id
                   ) AS note_line_count,
                   COALESCE((
                       SELECT GROUP_CONCAT(note.raw_line_text, ' | ')
                       FROM card_note_item_log note
                       WHERE note.parse_id = review.parse_id
                   ), '') AS evidence_preview
            FROM review_queue review
            LEFT JOIN parse_result parse ON parse.parse_id = review.parse_id
            LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
            LEFT JOIN card_note_item_log review_note
                ON (
                    review_note.note_item_id = CASE
                        WHEN review.review_id LIKE 'review_unlabeled_numeric_note_%'
                            THEN SUBSTR(review.review_id, LENGTH('review_unlabeled_numeric_') + 1)
                        WHEN review.review_id LIKE 'review_ear_note_%'
                            THEN SUBSTR(review.review_id, LENGTH('review_ear_') + 1)
                        ELSE ''
                    END
                    OR review_note.note_item_id = CASE
                        WHEN review.review_id = 'review_unlabeled_numeric_' || review.parse_id
                            THEN (
                                SELECT note.note_item_id
                                FROM card_note_item_log note
                                WHERE note.parse_id = review.parse_id
                                  AND (
                                      note.parsed_type = 'unlabeled_numeric_note'
                                      OR note.note_item_id IN (
                                          SELECT correction.entity_id
                                          FROM correction_log correction
                                          WHERE correction.review_id = review.review_id
                                            AND correction.entity_type = 'note_item'
                                            AND correction.field_name = 'parsed_label'
                                      )
                                  )
                                ORDER BY note.line_number
                                LIMIT 1
                            )
                        ELSE ''
                    END
                )
            LEFT JOIN card_snapshot review_snapshot
                ON review_snapshot.card_snapshot_id = review_note.card_snapshot_id
            ORDER BY review.created_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["assigned_role"] = payload.get("assigned_role") or review_assigned_role(
            payload.get("issue", ""),
            payload.get("review_reason", ""),
            payload.get("source_name", ""),
        )
        payload["source_layer"] = payload.get("source_layer") or "review item"
        payload["evidence_reference"] = json_object(payload.pop("evidence_reference_json", "{}"))
        payload["review_trigger"] = json_object(payload.pop("review_trigger_json", "{}"))
        payload["priority"] = payload.get("priority") or review_priority(
            payload.get("severity", ""),
            payload.get("issue", ""),
            payload.get("review_reason", ""),
        )
        payload["confidence"] = payload.get("parse_confidence")
        payload["image_url"] = f"/api/photos/{quote(payload['photo_id'])}/image" if payload.get("photo_id") else ""
        payload["review_note_summary"] = json_object(payload.pop("review_note_summary_json", "{}"))
        review_note_metadata = json_object(payload.pop("review_note_parsed_metadata_json", "{}"))
        payload["hybrid_note_line_evaluator"] = review_note_metadata.get("hybrid_note_line_evaluator", {})
        parse_payload = json_object(payload.pop("parse_raw_payload", "{}"))
        payload["review_plausibility_findings"] = parse_payload_plausibility_findings(parse_payload)
        payload.update(review_attention_level(payload, parse_payload))
        payload["review_check_targets"] = review_check_targets(payload, parse_payload)
        payload.pop("parse_confidence", None)
        result.append(payload)
    return result


@app.get("/api/ui/focus-review")
def ui_focus_review() -> dict[str, Any]:
    reviews = [
        item for item in list_review_items()
        if item.get("status") == "open" and item.get("attention_level") in {"must_review", "quick_check"}
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in reviews:
        group_key = str(item.get("parse_id") or item.get("photo_id") or item.get("review_id") or "")
        grouped.setdefault(group_key, []).append(item)

    with connection() as conn:
        parse_ids = [key for key in grouped if key]
        note_rows_by_parse: dict[str, list[dict[str, Any]]] = {}
        snapshot_by_parse: dict[str, dict[str, Any]] = {}
        if parse_ids:
            placeholders = ",".join("?" for _ in parse_ids)
            note_rows = conn.execute(
                f"""
                SELECT note_item_id, parse_id, card_snapshot_id, line_number, raw_line_text,
                       parsed_mouse_display_id, interpreted_status, needs_review
                FROM card_note_item_log
                WHERE parse_id IN ({placeholders})
                ORDER BY parse_id, line_number, note_item_id
                """,
                parse_ids,
            ).fetchall()
            for row in note_rows:
                note_rows_by_parse.setdefault(str(row["parse_id"]), []).append(dict(row))

            snapshot_rows = conn.execute(
                f"""
                SELECT card_snapshot_id, parse_id, photo_id, card_type, card_id_raw,
                       raw_strain_text, matched_strain_text, sex_raw, sex_normalized,
                       count_value, dob_raw, status
                FROM card_snapshot
                WHERE parse_id IN ({placeholders})
                ORDER BY updated_at DESC, card_snapshot_id
                """,
                parse_ids,
            ).fetchall()
            for row in snapshot_rows:
                snapshot_by_parse.setdefault(str(row["parse_id"]), dict(row))

    cards: list[dict[str, Any]] = []
    for parse_id, items in grouped.items():
        first = items[0]
        note_rows = note_rows_by_parse.get(parse_id, [])
        snapshot = snapshot_by_parse.get(parse_id, {})
        mouse_rows = [
            {
                "mouse_id": row["parsed_mouse_display_id"],
                "raw_line": row["raw_line_text"],
                "interpreted_status": row["interpreted_status"],
                "needs_review": bool(row["needs_review"]),
                "note_item_id": row["note_item_id"],
            }
            for row in note_rows
            if row.get("parsed_mouse_display_id")
        ]
        cards.append(
            {
                "parse_id": parse_id,
                "source_layer": "export or view",
                "source_photo": {
                    "photo_id": first.get("photo_id") or "",
                    "filename": first.get("original_filename") or "",
                    "image_url": first.get("image_url") or "",
                    "source_photo_role": "primary_evidence" if first.get("photo_id") else "not_available",
                    "open_source_photo_label": "Open source photo",
                },
                "card_snapshot": {
                    "card_snapshot_id": snapshot.get("card_snapshot_id", ""),
                    "card_type": snapshot.get("card_type", ""),
                    "card_id_raw": snapshot.get("card_id_raw", ""),
                    "raw_strain_text": snapshot.get("raw_strain_text", ""),
                    "matched_strain_text": snapshot.get("matched_strain_text", ""),
                    "status": snapshot.get("status", ""),
                },
                "review_count": len(items),
                "review_items": [
                    {
                        "review_id": item.get("review_id"),
                        "issue": item.get("issue"),
                        "issue_label": focus_review_issue_label(item),
                        "attention_level": item.get("attention_level"),
                        "attention_reason": item.get("attention_reason"),
                        "review_check_targets": item.get("review_check_targets", []),
                        "evidence_preview": item.get("evidence_preview") or item.get("suggested_value") or "",
                        "action_hint": focus_review_action_hint(item),
                    }
                    for item in items
                ],
                "mouse_rows": mouse_rows,
                "collapsed_sections": {
                    "evidence": int(bool(first.get("photo_id"))) + int(bool(snapshot)) + len(note_rows),
                    "raw_ocr": 1 if first.get("source_name") else 0,
                    "note_lines": len(mouse_rows),
                    "proposed_events": 0,
                    "review_history": len(items),
                },
                "actions": ["Apply confirmed rows only", "Hold card", "Open source photo"],
            }
        )

    return {
        "source_layer": "export or view",
        "page_question": "What needs my decision today?",
        "workload_summary": focus_review_workload_summary(reviews),
        "cards": cards,
        "empty_state": focus_review_empty_state(),
    }


@app.get("/api/ui/colony-state")
def ui_colony_state(as_of: str = "") -> dict[str, Any]:
    try:
        as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be an ISO date.")

    actionable_reviews = [
        item for item in list_review_items()
        if item.get("status") == "open" and item.get("attention_level") in {"must_review", "quick_check"}
    ]
    review_counts_by_parse: dict[str, dict[str, int]] = {}
    for item in actionable_reviews:
        parse_id = str(item.get("parse_id") or "")
        level = str(item.get("attention_level") or "")
        if not parse_id or level not in {"must_review", "quick_check"}:
            continue
        review_counts_by_parse.setdefault(parse_id, {"must_review": 0, "quick_check": 0})
        review_counts_by_parse[parse_id][level] += 1

    with connection() as conn:
        attention_counts = open_review_attention_counts(conn)
        active_mice = conn.execute(
            """
            SELECT COUNT(*)
            FROM mouse_master
            WHERE status = 'active'
            """
        ).fetchone()[0]
        active_matings = conn.execute(
            """
            SELECT COUNT(*)
            FROM mating_registry
            WHERE status = 'active'
            """
        ).fetchone()[0]
        active_litters = conn.execute(
            """
            SELECT COUNT(*)
            FROM litter_registry
            WHERE status IN ('active', 'born')
            """
        ).fetchone()[0]
        strain_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(raw_strain_text, ''), 'Unknown') AS strain,
                   COUNT(*) AS active_mice
            FROM mouse_master
            WHERE status = 'active'
            GROUP BY COALESCE(NULLIF(raw_strain_text, ''), 'Unknown')
            ORDER BY active_mice DESC, strain COLLATE NOCASE
            """
        ).fetchall()
        status_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS mouse_count
            FROM mouse_master
            WHERE status = 'active'
            GROUP BY status
            ORDER BY mouse_count DESC, status COLLATE NOCASE
            """
        ).fetchall()
        card_rows = conn.execute(
            """
            SELECT card.card_snapshot_id, card.parse_id, card.photo_id, card.card_type,
                   card.card_id_raw, card.raw_strain_text, card.matched_strain_text,
                   card.sex_raw, card.sex_normalized, card.count_value, card.dob_raw,
                   card.status, card.source_layer, photo.original_filename,
                   COUNT(mouse.mouse_id) AS mouse_count,
                   COALESCE((
                       SELECT COUNT(*)
                       FROM card_note_item_log note
                       WHERE note.card_snapshot_id = card.card_snapshot_id
                   ), 0) AS note_line_count
            FROM card_snapshot card
            JOIN mouse_master mouse
              ON mouse.current_card_snapshot_id = card.card_snapshot_id
             AND mouse.status = 'active'
            LEFT JOIN photo_log photo ON photo.photo_id = card.photo_id
            WHERE card.status IN ('accepted', 'active', 'current')
              AND card.source_layer = 'canonical structured state'
            GROUP BY card.card_snapshot_id, card.parse_id, card.photo_id, card.card_type,
                     card.card_id_raw, card.raw_strain_text, card.matched_strain_text,
                     card.sex_raw, card.sex_normalized, card.count_value, card.dob_raw,
                     card.status, card.source_layer, photo.original_filename
            ORDER BY card.updated_at DESC, card.card_snapshot_id
            """
        ).fetchall()
        mating_rows = conn.execute(
            """
            SELECT m.mating_id, m.mating_label, m.strain_goal, m.expected_genotype,
                   m.start_date, m.status, m.purpose, m.source_record_id,
                   COUNT(DISTINCT CASE WHEN mm.removed_date IS NULL THEN mm.mouse_id END) AS parent_count,
                   COUNT(DISTINCT CASE WHEN l.status IN ('active', 'born', 'pre_weaning', 'weaning_pending') THEN l.litter_id END) AS active_litter_count
            FROM mating_registry m
            LEFT JOIN mating_mouse mm ON mm.mating_id = m.mating_id
            LEFT JOIN litter_registry l ON l.mating_id = m.mating_id
            WHERE m.status = 'active'
            GROUP BY m.mating_id, m.mating_label, m.strain_goal, m.expected_genotype,
                     m.start_date, m.status, m.purpose, m.source_record_id
            ORDER BY m.start_date DESC, m.mating_label COLLATE NOCASE
            """
        ).fetchall()
        litter_rows = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, m.mating_label,
                   l.birth_date, l.number_born, l.number_alive, l.number_weaned,
                   l.weaning_date, l.status, l.source_record_id
            FROM litter_registry l
            JOIN mating_registry m ON m.mating_id = l.mating_id
            WHERE l.status IN ('active', 'born', 'pre_weaning', 'weaning_pending')
            ORDER BY l.birth_date DESC, l.litter_label COLLATE NOCASE
            """
        ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in card_rows:
        parse_id = str(row["parse_id"] or "")
        review_counts = review_counts_by_parse.get(parse_id, {"must_review": 0, "quick_check": 0})
        cards.append(
            {
                "card_snapshot_id": row["card_snapshot_id"],
                "parse_id": parse_id,
                "card_type": row["card_type"],
                "card_id_raw": row["card_id_raw"],
                "raw_strain_text": row["raw_strain_text"],
                "matched_strain_text": row["matched_strain_text"],
                "sex_raw": row["sex_raw"],
                "sex_normalized": row["sex_normalized"],
                "count_value": row["count_value"],
                "dob_raw": row["dob_raw"],
                "status": row["status"],
                "source_layer": row["source_layer"],
                "mouse_count": row["mouse_count"],
                "source_photo": {
                    "photo_id": row["photo_id"] or "",
                    "filename": row["original_filename"] or "",
                    "source_photo_role": "primary_evidence" if row["photo_id"] else "not_available",
                    "open_source_photo_label": "Open source photo",
                },
                "collapsed_sections": {
                    "mice": row["mouse_count"],
                    "note_lines": row["note_line_count"],
                    "review_blockers": review_counts["must_review"],
                    "source_evidence": 1 if row["photo_id"] else 0,
                },
            }
        )

    matings = [
        {
            "mating_id": row["mating_id"],
            "mating_label": row["mating_label"],
            "strain_goal": row["strain_goal"],
            "expected_genotype": row["expected_genotype"],
            "start_date": row["start_date"],
            "status": row["status"],
            "purpose": row["purpose"],
            "source_record_id": row["source_record_id"] or "",
            "parent_count": row["parent_count"],
            "active_litter_count": row["active_litter_count"],
            "source_layer": "canonical structured state",
            "collapsed_sections": {
                "parents": row["parent_count"],
                "active_litters": row["active_litter_count"],
                "source_evidence": 1 if row["source_record_id"] else 0,
            },
        }
        for row in mating_rows
    ]
    litters = [
        {
            "litter_id": row["litter_id"],
            "litter_label": row["litter_label"],
            "mating_id": row["mating_id"],
            "mating_label": row["mating_label"],
            "birth_date": row["birth_date"],
            "number_born": row["number_born"],
            "number_alive": row["number_alive"],
            "number_weaned": row["number_weaned"],
            "weaning_date": row["weaning_date"],
            "status": row["status"],
            "source_record_id": row["source_record_id"] or "",
            "source_layer": "canonical structured state",
            "collapsed_sections": {
                "pups_alive": row["number_alive"] if row["number_alive"] is not None else row["number_born"] or 0,
                "source_evidence": 1 if row["source_record_id"] else 0,
            },
            "action_hint": colony_litter_action_hint(
                row["birth_date"],
                DEFAULT_BREEDING_RULE_SET,
                observed_date=as_of_date,
            ),
        }
        for row in litter_rows
    ]

    attention_links = []
    must_review = int(attention_counts.get("must_review", 0))
    quick_check = int(attention_counts.get("quick_check", 0))
    if must_review or quick_check:
        attention_links.append(
            {
                "label": "Focus Review",
                "target_path": "/api/ui/focus-review",
                "must_review": must_review,
                "quick_check": quick_check,
            }
        )

    return {
        "source_layer": "export or view",
        "page_question": "What is active now?",
        "as_of": as_of_date.isoformat(),
        "summary": {
            "active_mice": active_mice,
            "active_card_snapshots": len(cards),
            "active_matings": active_matings,
            "active_litters": active_litters,
            "must_review": must_review,
            "quick_check": quick_check,
        },
        "active_card_snapshots": cards,
        "active_matings": matings,
        "active_litters": litters,
        "strain_summary": [dict(row) for row in strain_rows],
        "status_summary": [dict(row) for row in status_rows],
        "attention_links": attention_links,
        "empty_state": colony_state_empty_state(),
    }


def schedule_group_for_due_date(due_date: date, as_of_date: date, due_soon_days: int) -> str:
    days_until_due = (due_date - as_of_date).days
    if days_until_due <= 0:
        return "due_now"
    if days_until_due <= due_soon_days:
        return "due_soon"
    return "later"


@app.get("/api/ui/colony-schedule")
def ui_colony_schedule(as_of: str = "") -> dict[str, Any]:
    try:
        as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be an ISO date.")

    rule_set = DEFAULT_BREEDING_RULE_SET
    threshold_days = int(rule_set.get("thresholds", {}).get("litter_separation_due_after_days", 30))
    due_soon_days = int(rule_set.get("thresholds", {}).get("schedule_due_soon_window_days", 30))
    with connection() as conn:
        attention_counts = open_review_attention_counts(conn)
        litter_rows = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, m.mating_label,
                   l.birth_date, l.number_born, l.number_alive, l.number_weaned,
                   l.weaning_date, l.status, l.source_record_id
            FROM litter_registry l
            JOIN mating_registry m ON m.mating_id = l.mating_id
            WHERE l.status IN ('active', 'born', 'pre_weaning', 'weaning_pending')
              AND COALESCE(l.birth_date, '') <> ''
              AND COALESCE(l.weaning_date, '') = ''
            ORDER BY l.birth_date ASC, l.litter_label COLLATE NOCASE
            """
        ).fetchall()
        completed = conn.execute(
            """
            SELECT COUNT(*)
            FROM litter_registry
            WHERE status = 'weaned'
               OR COALESCE(weaning_date, '') <> ''
            """
        ).fetchone()[0]

    must_review = int(attention_counts.get("must_review", 0))
    quick_check = int(attention_counts.get("quick_check", 0))
    tasks: list[dict[str, Any]] = []
    for row in litter_rows:
        try:
            birth_date = date.fromisoformat(str(row["birth_date"]))
        except (TypeError, ValueError):
            continue
        due_date = birth_date + timedelta(days=threshold_days)
        group = schedule_group_for_due_date(due_date, as_of_date, due_soon_days)
        days_until_due = (due_date - as_of_date).days
        status = "blocked_by_review" if must_review else group
        tasks.append(
            {
                "task_id": f"schedule_litter_separation_{row['litter_id']}",
                "task_type": "litter_separation",
                "label": f"Separate/wean litter {row['litter_label'] or row['litter_id']}",
                "status": status,
                "recorded_date": row["birth_date"],
                "due_date": due_date.isoformat(),
                "days_until_due": days_until_due,
                "source_layer": "export or view",
                "source_entity": {
                    "entity_type": "litter",
                    "entity_id": row["litter_id"],
                    "label": row["litter_label"],
                },
                "source_evidence": {
                    "source_record_id": row["source_record_id"] or "",
                    "mating_id": row["mating_id"],
                    "mating_label": row["mating_label"],
                },
                "due_date_rule": {
                    "rule_set_id": rule_set["rule_set_id"],
                    "rule_key": "litter_separation_due_after_days",
                    "value_days": threshold_days,
                },
                "attention_link": {
                    "label": "Open Focus Review",
                    "target_path": "/api/ui/focus-review",
                    "must_review": must_review,
                    "quick_check": quick_check,
                } if must_review or quick_check else {},
            }
        )

    grouped_tasks: dict[str, list[dict[str, Any]]] = {"due_now": [], "due_soon": [], "later": []}
    for task in tasks:
        due_date_value = date.fromisoformat(str(task["due_date"]))
        grouped_tasks[schedule_group_for_due_date(due_date_value, as_of_date, due_soon_days)].append(task)
    task_groups = [
        {"group": group, "tasks": grouped_tasks[group]}
        for group in ["due_now", "due_soon", "later"]
        if grouped_tasks[group]
    ]

    return {
        "source_layer": "export or view",
        "page_question": "What needs doing next?",
        "as_of": as_of_date.isoformat(),
        "rule_set": {
            "rule_set_id": rule_set["rule_set_id"],
            "display_name": rule_set["display_name"],
            "source_layer": "parsed or intermediate result",
        },
        "summary": {
            "due_now": len(grouped_tasks["due_now"]),
            "due_soon": len(grouped_tasks["due_soon"]),
            "later": len(grouped_tasks["later"]),
            "blocked_by_review": sum(1 for task in tasks if task["status"] == "blocked_by_review"),
            "completed": completed,
        },
        "task_groups": task_groups,
        "calendar_mirror": {
            "status": "not_configured",
            "canonical_source": "MouseDB internal schedule",
            "note": "External calendar sync can mirror accepted schedule tasks later; it is not canonical.",
        },
        "empty_state": colony_schedule_empty_state(),
    }


@app.get("/api/ui/mouse-timeline")
def ui_mouse_timeline(mouse_id: str = "") -> dict[str, Any]:
    selected_mouse_id = mouse_id.strip()
    with connection() as conn:
        attention_counts = open_review_attention_counts(conn)
        if not selected_mouse_id:
            must_review = int(attention_counts.get("must_review", 0))
            quick_check = int(attention_counts.get("quick_check", 0))
            return {
                "source_layer": "export or view",
                "page_question": "How did this mouse get here?",
                "mouse": None,
                "summary": {
                    "accepted_events": 0,
                    "source_records": 0,
                    "must_review": must_review,
                    "quick_check": quick_check,
                },
                "lineage": {"father": None, "mother": None, "litter": None},
                "events": [],
                "attention_links": [],
                "empty_state": mouse_timeline_empty_state(),
            }

        mouse = conn.execute(
            """
            SELECT mouse_id, display_id, status, raw_strain_text, father_id, mother_id, litter_id
            FROM mouse_master
            WHERE mouse_id = ? OR display_id = ?
            """,
            (selected_mouse_id, selected_mouse_id),
        ).fetchone()
        if mouse is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")

        event_rows = conn.execute(
            """
            SELECT event_id, mouse_id, event_type, event_date, related_entity_type,
                   related_entity_id, source_record_id, details, created_by, created_at
            FROM mouse_event
            WHERE mouse_id = ?
            ORDER BY event_date, created_at, event_id
            """,
            (mouse["mouse_id"],),
        ).fetchall()
        litter = (
            conn.execute(
                """
                SELECT l.litter_id, l.litter_label, l.mating_id, m.mating_label,
                       l.birth_date
                FROM litter_registry l
                LEFT JOIN mating_registry m ON m.mating_id = l.mating_id
                WHERE l.litter_id = ?
                """,
                (mouse["litter_id"],),
            ).fetchone()
            if mouse["litter_id"]
            else None
        )
        father = (
            conn.execute(
                "SELECT mouse_id, display_id, status, raw_strain_text FROM mouse_master WHERE mouse_id = ?",
                (mouse["father_id"],),
            ).fetchone()
            if mouse["father_id"]
            else None
        )
        mother = (
            conn.execute(
                "SELECT mouse_id, display_id, status, raw_strain_text FROM mouse_master WHERE mouse_id = ?",
                (mouse["mother_id"],),
            ).fetchone()
            if mouse["mother_id"]
            else None
        )
        source_ids = sorted({row["source_record_id"] for row in event_rows if row["source_record_id"]})
        source_rows: dict[str, dict[str, Any]] = {}
        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""
                SELECT source_record_id, source_type, source_label
                FROM source_record
                WHERE source_record_id IN ({placeholders})
                """,
                source_ids,
            ).fetchall()
            source_rows = {row["source_record_id"]: dict(row) for row in rows}

        event_detail_rows = [json_object(row["details"]) for row in event_rows]
        source_photo_ids = unique_nonempty(
            [details.get("source_photo_id") for details in event_detail_rows if isinstance(details, dict)]
        )
        source_note_item_ids = unique_nonempty(
            [details.get("source_note_item_id") for details in event_detail_rows if isinstance(details, dict)]
        )
        photo_evidence_ids = unique_nonempty(
            [details.get("photo_evidence_id") for details in event_detail_rows if isinstance(details, dict)]
        )
        source_photo_rows: dict[str, dict[str, Any]] = {}
        if source_photo_ids:
            placeholders = ", ".join("?" for _ in source_photo_ids)
            rows = conn.execute(
                f"""
                SELECT photo_id, original_filename, stored_path, raw_source_kind
                FROM photo_log
                WHERE photo_id IN ({placeholders})
                """,
                source_photo_ids,
            ).fetchall()
            source_photo_rows = {row["photo_id"]: dict(row) for row in rows}
        source_note_rows: dict[str, dict[str, Any]] = {}
        if source_note_item_ids:
            placeholders = ", ".join("?" for _ in source_note_item_ids)
            rows = conn.execute(
                f"""
                SELECT note_item_id, photo_id, line_number, raw_line_text, parsed_type,
                       interpreted_status
                FROM card_note_item_log
                WHERE note_item_id IN ({placeholders})
                """,
                source_note_item_ids,
            ).fetchall()
            source_note_rows = {row["note_item_id"]: dict(row) for row in rows}
        photo_evidence_rows: dict[str, dict[str, Any]] = {}
        if photo_evidence_ids:
            placeholders = ", ".join("?" for _ in photo_evidence_ids)
            rows = conn.execute(
                f"""
                SELECT photo_evidence_id, source_photo_id, note_item_id, evidence_kind,
                       roi_label, observed_raw_text, normalized_value, confidence,
                       needs_review, status
                FROM photo_evidence_item
                WHERE photo_evidence_id IN ({placeholders})
                """,
                photo_evidence_ids,
            ).fetchall()
            photo_evidence_rows = {row["photo_evidence_id"]: dict(row) for row in rows}

    must_review = int(attention_counts.get("must_review", 0))
    quick_check = int(attention_counts.get("quick_check", 0))
    events = []
    for row, details in zip(event_rows, event_detail_rows):
        source = source_rows.get(row["source_record_id"] or "", {})
        evidence_refs = mouse_event_evidence_refs(row)
        source_evidence = {
            "source_record_id": row["source_record_id"] or "",
            "source_label": source.get("source_label", ""),
            "source_type": source.get("source_type", ""),
        }
        source_photo_id = str(details.get("source_photo_id") or "") if isinstance(details, dict) else ""
        source_note_item_id = str(details.get("source_note_item_id") or "") if isinstance(details, dict) else ""
        photo_evidence_id = str(details.get("photo_evidence_id") or "") if isinstance(details, dict) else ""
        source_photo = source_photo_rows.get(source_photo_id, {})
        source_note = source_note_rows.get(source_note_item_id, {})
        photo_evidence = photo_evidence_rows.get(photo_evidence_id, {})
        if source_photo_id or source_note_item_id or photo_evidence_id:
            source_evidence["event_trace"] = {
                "source_layer": "export or view",
                "source_photo_id": source_photo_id,
                "source_photo_filename": source_photo.get("original_filename", ""),
                "source_note_item_id": source_note_item_id,
                "source_note_text": source_note.get("raw_line_text", ""),
                "source_note_line_number": source_note.get("line_number"),
                "photo_evidence_id": photo_evidence_id,
                "evidence_kind": photo_evidence.get("evidence_kind", ""),
                "evidence_raw_text": photo_evidence.get("observed_raw_text", ""),
                "evidence_normalized_value": photo_evidence.get("normalized_value", ""),
                "evidence_status": photo_evidence.get("status", ""),
                "needs_review": bool(photo_evidence.get("needs_review")) if photo_evidence else False,
                "trace_status": (
                    "resolved"
                    if (
                        (not source_photo_id or bool(source_photo))
                        and (not source_note_item_id or bool(source_note))
                        and (not photo_evidence_id or bool(photo_evidence))
                    )
                    else "source_detail_missing"
                ),
            }
        events.append(
            {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "event_date": row["event_date"],
                "label": row["event_type"],
                "source_layer": "canonical structured state",
                "related_entity": {
                    "entity_type": row["related_entity_type"] or "",
                    "entity_id": row["related_entity_id"] or "",
                },
                "source_evidence": source_evidence,
                "evidence_refs": evidence_refs,
            }
        )

    attention_links = []
    if must_review or quick_check:
        attention_links.append(
            {
                "label": "Open Focus Review",
                "target_path": "/api/ui/focus-review",
                "must_review": must_review,
                "quick_check": quick_check,
            }
        )

    return {
        "source_layer": "export or view",
        "page_question": "How did this mouse get here?",
        "mouse": {
            "mouse_id": mouse["mouse_id"],
            "display_id": mouse["display_id"],
            "status": mouse["status"],
            "strain": mouse["raw_strain_text"],
            "litter_id": mouse["litter_id"] or "",
        },
        "summary": {
            "accepted_events": len(events),
            "source_records": len(source_rows),
            "must_review": must_review,
            "quick_check": quick_check,
        },
        "lineage": {
            "father": dict(father) if father else None,
            "mother": dict(mother) if mother else None,
            "litter": {
                "litter_id": litter["litter_id"],
                "litter_label": litter["litter_label"],
                "mating_id": litter["mating_id"],
                "mating_label": litter["mating_label"],
                "birth_date": litter["birth_date"],
            } if litter else None,
        },
        "events": events,
        "attention_links": attention_links,
        "empty_state": mouse_timeline_empty_state(),
    }


@app.get("/api/ui/mouse-pedigree")
def ui_mouse_pedigree(mouse_id: str = "") -> dict[str, Any]:
    selected_mouse_id = mouse_id.strip()
    with connection() as conn:
        attention_counts = open_review_attention_counts(conn)
        must_review = int(attention_counts.get("must_review", 0))
        quick_check = int(attention_counts.get("quick_check", 0))
        if not selected_mouse_id:
            return {
                "source_layer": "export or view",
                "page_question": "Where did this mouse come from?",
                "mode": "selected_path",
                "mouse": None,
                "relationship_summary": {
                    "confirmed_relationships": 0,
                    "pending_relationships": 0,
                    "same_litter_siblings": 0,
                    "offspring_events": 0,
                    "must_review": must_review,
                    "quick_check": quick_check,
                },
                "nodes": {},
                "evidence_rows": [],
                "attention_links": [],
                "empty_state": mouse_pedigree_empty_state(),
            }

        mouse = conn.execute(
            """
            SELECT mouse_id, display_id, status, raw_strain_text, father_id, mother_id, litter_id
            FROM mouse_master
            WHERE mouse_id = ? OR display_id = ?
            """,
            (selected_mouse_id, selected_mouse_id),
        ).fetchone()
        if mouse is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")
        father = conn.execute(
            "SELECT mouse_id, display_id, status, raw_strain_text FROM mouse_master WHERE mouse_id = ?",
            (mouse["father_id"],),
        ).fetchone() if mouse["father_id"] else None
        mother = conn.execute(
            "SELECT mouse_id, display_id, status, raw_strain_text FROM mouse_master WHERE mouse_id = ?",
            (mouse["mother_id"],),
        ).fetchone() if mouse["mother_id"] else None
        litter = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, l.birth_date,
                   l.source_record_id, m.mating_label, m.status AS mating_status,
                   m.source_record_id AS mating_source_record_id
            FROM litter_registry l
            LEFT JOIN mating_registry m ON m.mating_id = l.mating_id
            WHERE l.litter_id = ?
            """,
            (mouse["litter_id"],),
        ).fetchone() if mouse["litter_id"] else None
        sibling_rows = conn.execute(
            """
            SELECT mouse_id, display_id, status, raw_strain_text, sex
            FROM mouse_master
            WHERE litter_id = ? AND mouse_id <> ? AND status = 'active'
            ORDER BY display_id, mouse_id
            LIMIT 12
            """,
            (mouse["litter_id"], mouse["mouse_id"]),
        ).fetchall() if mouse["litter_id"] else []
        offspring_events = conn.execute(
            "SELECT COUNT(*) AS child_count FROM mouse_master WHERE father_id = ? OR mother_id = ?",
            (mouse["mouse_id"], mouse["mouse_id"]),
        ).fetchone()["child_count"]
        source_ids = set()
        if litter is not None:
            source_ids.update(
                source_id
                for source_id in [litter["source_record_id"], litter["mating_source_record_id"]]
                if source_id
            )
        source_rows: dict[str, dict[str, Any]] = {}
        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""
                SELECT source_record_id, source_type, source_label
                FROM source_record
                WHERE source_record_id IN ({placeholders})
                """,
                sorted(source_ids),
            ).fetchall()
            source_rows = {row["source_record_id"]: dict(row) for row in rows}

    def mouse_node(row: Any, relationship: str) -> dict[str, Any]:
        return {
            "node_type": "mouse",
            "relationship": relationship,
            "mouse_id": row["mouse_id"],
            "display_id": row["display_id"],
            "status": row["status"],
            "strain": row["raw_strain_text"],
            "relationship_status": "confirmed",
            "source_layer": "canonical structured state",
        }

    def pending_parent_node(relationship: str) -> dict[str, Any]:
        return {
            "node_type": "pending_relationship",
            "relationship": relationship,
            "label": "Parent pending",
            "relationship_status": "pending_review",
            "not_inferred": True,
        }

    def evidence_source(source_record_id: str) -> dict[str, str]:
        source = source_rows.get(source_record_id, {})
        return {
            "source_record_id": source_record_id,
            "label": source.get("source_label", ""),
            "source_type": source.get("source_type", ""),
        }

    def missing_parent_source() -> dict[str, str]:
        return {
            "source_record_id": "",
            "label": "No accepted parent evidence",
            "source_type": "pending_relationship",
        }

    lineage_source_record_id = ""
    if litter is not None:
        lineage_source_record_id = litter["mating_source_record_id"] or litter["source_record_id"] or ""

    nodes: dict[str, Any] = {
        "selected_mouse": {
            "node_type": "mouse",
            "relationship": "selected",
            "mouse_id": mouse["mouse_id"],
            "display_id": mouse["display_id"],
            "status": mouse["status"],
            "strain": mouse["raw_strain_text"],
            "relationship_status": "selected",
            "source_layer": "canonical structured state",
        },
        "father": mouse_node(father, "father") if father else pending_parent_node("father"),
        "mother": mouse_node(mother, "mother") if mother else pending_parent_node("mother"),
        "same_litter_siblings": [
            {
                "node_type": "mouse",
                "relationship": "same_litter_sibling",
                "mouse_id": row["mouse_id"],
                "display_id": row["display_id"],
                "status": row["status"],
                "strain": row["raw_strain_text"],
                "sex": row["sex"] or "",
                "relationship_status": "confirmed",
                "source_layer": "canonical structured state",
            }
            for row in sibling_rows
        ],
    }
    if litter is not None:
        nodes["mating"] = {
            "node_type": "mating",
            "relationship": "source_mating",
            "mating_id": litter["mating_id"] or "",
            "mating_label": litter["mating_label"] or "",
            "status": litter["mating_status"] or "",
            "relationship_status": "confirmed",
            "source_layer": "canonical structured state",
        }
        nodes["litter"] = {
            "node_type": "litter",
            "relationship": "source_litter",
            "litter_id": litter["litter_id"],
            "litter_label": litter["litter_label"],
            "mating_id": litter["mating_id"] or "",
            "birth_date": litter["birth_date"],
            "relationship_status": "confirmed",
            "source_layer": "canonical structured state",
        }

    evidence_rows = []
    for field_name, row in [("mother_id", mother), ("father_id", father)]:
        if row:
            evidence_rows.append(
                {
                    "field": field_name,
                    "value": row["mouse_id"],
                    "status": "confirmed",
                    "source_layer": "canonical structured state",
                    "source": evidence_source(lineage_source_record_id),
                }
            )
        else:
            evidence_rows.append(
                {
                    "field": field_name,
                    "value": "Parent pending",
                    "status": "pending_review",
                    "source_layer": "canonical structured state",
                    "source": missing_parent_source(),
                    "not_inferred": True,
                }
            )
    if litter is not None:
        evidence_rows.append(
            {
                "field": "litter_id",
                "value": litter["litter_id"],
                "status": "confirmed",
                "source_layer": "canonical structured state",
                "source": evidence_source(lineage_source_record_id),
            }
        )
        if litter["mating_id"]:
            evidence_rows.append(
                {
                    "field": "mating_id",
                    "value": litter["mating_id"],
                    "status": "confirmed",
                    "source_layer": "canonical structured state",
                    "source": evidence_source(lineage_source_record_id),
                }
            )

    pending_relationships = sum(1 for row in evidence_rows if row["status"] == "pending_review")
    attention_links = []
    if pending_relationships or must_review or quick_check:
        attention_links.append(
            {
                "label": "Open Focus Review",
                "target_path": "/api/ui/focus-review",
                "reason": "pending_relationship" if pending_relationships else "open_review_workload",
                "must_review": must_review,
                "quick_check": quick_check,
            }
        )
    return {
        "source_layer": "export or view",
        "page_question": "Where did this mouse come from?",
        "mode": "selected_path",
        "mouse": {
            "mouse_id": mouse["mouse_id"],
            "display_id": mouse["display_id"],
            "status": mouse["status"],
            "strain": mouse["raw_strain_text"],
            "litter_id": mouse["litter_id"] or "",
        },
        "relationship_summary": {
            "confirmed_relationships": sum(1 for row in evidence_rows if row["status"] == "confirmed"),
            "pending_relationships": pending_relationships,
            "same_litter_siblings": len(sibling_rows),
            "offspring_events": int(offspring_events),
            "must_review": must_review,
            "quick_check": quick_check,
        },
        "nodes": nodes,
        "evidence_rows": evidence_rows,
        "attention_links": attention_links,
        "empty_state": mouse_pedigree_empty_state(),
    }

@app.get("/api/ui/evidence-ledger")
def ui_evidence_ledger(source_photo_id: str = "", linked_mouse_id: str = "") -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if source_photo_id.strip():
        clauses.append("evidence.source_photo_id = ?")
        params.append(source_photo_id.strip())
    if linked_mouse_id.strip():
        clauses.append("evidence.linked_mouse_id = ?")
        params.append(linked_mouse_id.strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT evidence.photo_evidence_id, evidence.source_photo_id, evidence.parse_id,
                   evidence.card_snapshot_id, evidence.note_item_id, evidence.card_type,
                   evidence.evidence_kind, evidence.roi_label, evidence.bbox_json,
                   evidence.observed_raw_text, evidence.ocr_text, evidence.parsed_value,
                   evidence.raw_extracted_value, evidence.normalized_value,
                   evidence.confidence, evidence.confidence_source,
                   evidence.evidence_reference_json, evidence.interpretation, evidence.needs_review,
                   evidence.review_reason, evidence.linked_mouse_id, evidence.linked_cage_id,
                   evidence.linked_event_id, evidence.status, evidence.created_at,
                   evidence.updated_at, photo.original_filename, photo.raw_source_kind,
                   photo.uploaded_at, parse.source_name, parse.status AS parse_status,
                   parse.confidence AS parse_confidence, parse.needs_review AS parse_needs_review
            FROM photo_evidence_item evidence
            LEFT JOIN photo_log photo ON photo.photo_id = evidence.source_photo_id
            LEFT JOIN parse_result parse ON parse.parse_id = evidence.parse_id
            {where_sql}
            ORDER BY evidence.created_at DESC, evidence.photo_evidence_id
            LIMIT 50
            """,
            params,
        ).fetchall()
        evidence_ids = [row["photo_evidence_id"] for row in rows]
        review_ids_by_evidence: dict[str, list[str]] = {evidence_id: [] for evidence_id in evidence_ids}
        if evidence_ids:
            placeholders = ", ".join("?" for _ in evidence_ids)
            for row in conn.execute(
                f"""
                SELECT photo_evidence_id, review_id
                FROM review_evidence_link
                WHERE photo_evidence_id IN ({placeholders})
                ORDER BY created_at, review_id
                """,
                evidence_ids,
            ).fetchall():
                review_ids_by_evidence.setdefault(row["photo_evidence_id"], []).append(row["review_id"])

    evidence_items = []
    for row in rows:
        bbox = {}
        try:
            bbox = json.loads(row["bbox_json"] or "{}")
        except json.JSONDecodeError:
            bbox = {}
        evidence_reference = json_object(row["evidence_reference_json"])
        evidence_items.append(
            {
                "photo_evidence_id": row["photo_evidence_id"],
                "evidence_kind": row["evidence_kind"],
                "card_type": row["card_type"],
                "status": row["status"],
                "source_photo": {
                    "photo_id": row["source_photo_id"],
                    "original_filename": row["original_filename"] or "",
                    "raw_source_kind": row["raw_source_kind"] or "",
                    "uploaded_at": row["uploaded_at"] or "",
                    "open_source_photo_label": "Open source photo",
                },
                "parsed_trace": {
                    "parse_id": row["parse_id"] or "",
                    "source_name": row["source_name"] or "",
                    "status": row["parse_status"] or "",
                    "confidence": row["parse_confidence"] or 0,
                    "needs_review": bool(row["parse_needs_review"]),
                },
                "direct_observation": {
                    "roi_label": row["roi_label"] or "",
                    "bbox": bbox,
                    "observed_raw_text": row["observed_raw_text"] or "",
                    "raw_extracted_value": row["raw_extracted_value"] or "",
                },
                "ocr": {"text": row["ocr_text"] or ""},
                "ai_interpretation": {
                    "parsed_value": row["parsed_value"] or "",
                    "normalized_value": row["normalized_value"] or "",
                    "confidence": row["confidence"] or 0,
                    "confidence_source": row["confidence_source"] or "",
                    "interpretation": row["interpretation"] or "",
                    "needs_review": bool(row["needs_review"]),
                    "review_reason": row["review_reason"] or "",
                },
                "evidence_reference": evidence_reference,
                "links": {
                    "note_item_id": row["note_item_id"] or "",
                    "linked_mouse_id": row["linked_mouse_id"] or "",
                    "linked_cage_id": row["linked_cage_id"] or "",
                    "linked_event_id": row["linked_event_id"] or "",
                    "review_ids": review_ids_by_evidence.get(row["photo_evidence_id"], []),
                },
                "correction_history": [],
            }
        )

    return {
        "source_layer": "export or view",
        "page_question": "What evidence supports this record?",
        "summary": {
            "total_evidence": len(evidence_items),
            "needs_review": sum(1 for item in evidence_items if item["ai_interpretation"]["needs_review"]),
            "linked_events": sum(1 for item in evidence_items if item["links"]["linked_event_id"]),
            "source_photos": len({item["source_photo"]["photo_id"] for item in evidence_items if item["source_photo"]["photo_id"]}),
        },
        "filters": {
            "source_photo_id": source_photo_id.strip(),
            "linked_mouse_id": linked_mouse_id.strip(),
        },
        "evidence_items": evidence_items,
        "empty_state": evidence_ledger_empty_state(),
    }


@app.get("/api/review-items/{review_id}/audit")
def audit_review_item(review_id: str) -> dict[str, Any]:
    with connection() as conn:
        return review_item_audit_view(conn, review_id)


@app.get("/api/review-items/{review_id}/assistant-draft")
def assistant_draft_review_item(review_id: str) -> dict[str, Any]:
    with connection() as conn:
        audit = review_item_audit_view(conn, review_id)
    return assistant_review_draft_from_audit(audit)


SCORING_AUDIT_TAXONOMY_STATUSES = {
    "exact",
    "partial_match",
    "near_miss",
    "unscorable_due_to_occlusion",
}
PRIVATE_ACCURACY_FIELD_FAMILIES = {
    "mouse_ids_or_note_lines",
    "card_type_review_routing",
    "sex_count_dob",
    "mating_litter_context",
    "export_provenance",
}
PRIVATE_ACCURACY_FIELD_STATUSES = {"exact", "corrected", "missed", "not_applicable"}
NOTE_LINE_SCORING_SCOPES = {
    "scored_note_line",
    "no_visible_note_line_for_evaluator_scoring",
}
PRIVATE_ACCURACY_REVIEW_LEVELS = {"must_review", "quick_check", "trace_only", "hidden_default"}
PRIVATE_ACCURACY_FAILURE_LABELS = {
    "no_visible_note_line_for_evaluator_scoring",
    "partial_match",
    "near_miss",
    "unscorable_due_to_occlusion",
}


def review_scoring_audit_metadata(payload: ReviewResolutionCreate) -> dict[str, str]:
    status = payload.audit_taxonomy_status.strip()
    if not status:
        return {}
    if status not in SCORING_AUDIT_TAXONOMY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Audit taxonomy status must be exact, partial_match, near_miss, or unscorable_due_to_occlusion.",
        )
    note = payload.audit_taxonomy_note.strip()
    if len(note) > 200 or re.search(r"([A-Za-z]:\\|\\\\|/Users/|/home/|SECRET_)", note):
        note = "sanitized_review_note"
    return {
        "status": status,
        "note": note,
        "boundary": "review item / scoring audit metadata",
        "provenance": "operator_selected_review_resolution",
    }


def review_private_accuracy_field_outcome(payload: ReviewResolutionCreate) -> dict[str, Any]:
    outcome = payload.field_review_outcome if isinstance(payload.field_review_outcome, dict) else {}
    scope = str(payload.note_line_scoring_scope or outcome.get("note_line_scoring_scope") or "").strip()
    if scope and scope not in NOTE_LINE_SCORING_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="Note-line scoring scope must be scored_note_line or no_visible_note_line_for_evaluator_scoring.",
        )
    field_scores_input = outcome.get("field_scores")
    field_scores = {}
    if isinstance(field_scores_input, dict):
        for family, score in field_scores_input.items():
            family_key = str(family or "").strip()
            if family_key not in PRIVATE_ACCURACY_FIELD_FAMILIES or not isinstance(score, dict):
                continue
            status = str(score.get("status") or "").strip()
            if status not in PRIVATE_ACCURACY_FIELD_STATUSES:
                continue
            field_scores[family_key] = {
                "status": status,
                "reviewed_before_apply": score.get("reviewed_before_apply") is True,
                "traceable": score.get("traceable") is not False,
            }
    labels = []
    raw_labels = outcome.get("failure_labels")
    if isinstance(raw_labels, list):
        for label in raw_labels:
            label_key = str(label or "").strip()
            if label_key in PRIVATE_ACCURACY_FAILURE_LABELS and label_key not in labels:
                labels.append(label_key)
    if not scope and (field_scores or labels):
        raise HTTPException(
            status_code=400,
            detail="Note-line scoring scope is required when recording private accuracy field outcomes.",
        )
    if scope and not field_scores and not labels:
        raise HTTPException(
            status_code=400,
            detail="At least one field score or failure label is required when recording private accuracy field outcomes.",
        )
    if not scope and not field_scores and not labels:
        return {}
    actual_review_level = str(outcome.get("actual_review_level") or "").strip()
    if actual_review_level and actual_review_level not in PRIVATE_ACCURACY_REVIEW_LEVELS:
        raise HTTPException(
            status_code=400,
            detail="Actual review level must be must_review, quick_check, trace_only, or hidden_default.",
        )
    return {
        "boundary": "review item / private accuracy field outcome",
        "provenance": "operator_selected_review_resolution",
        "note_line_scoring_scope": scope,
        "actual_review_level": actual_review_level,
        "export_blocked_until_resolved": outcome.get("export_blocked_until_resolved") is True,
        "unresolved_must_review_at_export": outcome.get("unresolved_must_review_at_export") is True,
        "manual_transcription_required": outcome.get("manual_transcription_required") is True,
        "failure_labels": labels,
        "field_scores": field_scores,
    }


@app.post("/api/review-items/{review_id}/resolve")
def resolve_review_item(review_id: str, payload: ReviewResolutionCreate) -> dict[str, Any]:
    resolved_at = utc_now()
    scoring_audit = review_scoring_audit_metadata(payload)
    field_review_outcome = review_private_accuracy_field_outcome(payload)
    if (
        field_review_outcome.get("note_line_scoring_scope") == "scored_note_line"
        and not scoring_audit.get("status")
    ):
        raise HTTPException(
            status_code=400,
            detail="Scored note-line field outcomes require scoring audit taxonomy status.",
        )
    allowed_legacy_decisions = {
        "resolve",
        "accept_legacy_candidate",
        "reject_legacy_candidate",
        "map_to_canonical_candidate",
        "apply_strain_registry_candidate",
    }
    legacy_decision = payload.legacy_decision.strip() or "resolve"
    if legacy_decision not in allowed_legacy_decisions:
        raise HTTPException(status_code=400, detail="Legacy decision must be resolve, accept, reject, map, or apply strain registry.")
    legacy_status_by_decision = {
        "accept_legacy_candidate": "accepted",
        "reject_legacy_candidate": "rejected",
        "map_to_canonical_candidate": "mapped_candidate",
    }
    with connection() as conn:
        existing = conn.execute(
            """
            SELECT review_id, parse_id, severity, issue, current_value, suggested_value,
                   review_reason, assigned_role, assigned_to, priority, status, resolution_note
            FROM review_queue
            WHERE review_id = ?
            """,
            (review_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Review item not found.")
        if existing["status"] == "resolved":
            raise HTTPException(status_code=409, detail="Review item is already resolved.")
        correction_id = None
        correction_identity_fields = [
            payload.correction_entity_type.strip(),
            payload.correction_entity_id.strip(),
            payload.correction_field_name.strip(),
        ]
        correction_fields = [
            *correction_identity_fields,
            payload.correction_before_value,
            payload.correction_after_value,
            payload.correction_source_record_id or "",
        ]
        if any(correction_fields) and not all(correction_identity_fields):
            raise HTTPException(
                status_code=400,
                detail="Correction entity type, entity id, and field name are required when recording a review correction.",
            )
        before = dict(existing)
        canonical_candidate_id = None
        note_label_update = None
        ear_label_update = None
        strain_registry_apply = None
        if legacy_decision == "apply_strain_registry_candidate":
            strain_registry_apply = apply_legacy_strain_registry_candidate(
                conn,
                review=existing,
                payload=payload,
                resolved_at=resolved_at,
            )
        after = {
            "status": "resolved",
            "resolved_value": payload.resolved_value,
            "resolution_note": payload.resolution_note,
            "legacy_decision": legacy_decision,
            "canonical_entity_type": payload.canonical_entity_type,
            "canonical_entity_id": payload.canonical_entity_id,
            "reviewed_strain_name": payload.reviewed_strain_name,
            "reviewed_gene_symbol": payload.reviewed_gene_symbol,
            "reviewed_allele_name": payload.reviewed_allele_name,
            "scoring_audit": scoring_audit,
            "field_review_outcome": field_review_outcome,
        }
        conn.execute(
            """
            UPDATE review_queue
            SET status = 'resolved',
                resolved_at = ?,
                resolution_note = ?
            WHERE review_id = ?
            """,
            (resolved_at, payload.resolution_note, review_id),
        )
        note_label_update = resolve_note_label_correction(
            conn,
            review_id=review_id,
            parse_id=existing["parse_id"],
            payload=payload,
            resolved_at=resolved_at,
        )
        ear_label_update = resolve_ear_label_correction(
            conn,
            review_id=review_id,
            parse_id=existing["parse_id"],
            payload=payload,
            resolved_at=resolved_at,
        )
        legacy_row_review_status = None
        if legacy_decision in legacy_status_by_decision:
            legacy_row_review_status = legacy_status_by_decision[legacy_decision]
            legacy_row = conn.execute(
                """
                SELECT legacy_row_id, review_status
                FROM legacy_workbook_row
                WHERE review_id = ?
                """,
                (review_id,),
            ).fetchone()
            if legacy_row is not None:
                conn.execute(
                    """
                    UPDATE legacy_workbook_row
                    SET review_status = ?
                    WHERE legacy_row_id = ?
                    """,
                    (legacy_row_review_status, legacy_row["legacy_row_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("action"),
                        "legacy_workbook_row_reviewed",
                        legacy_row["legacy_row_id"],
                        json.dumps({"review_status": legacy_row["review_status"]}, ensure_ascii=False),
                        json.dumps(
                            {
                                "review_status": legacy_row_review_status,
                                "legacy_decision": legacy_decision,
                                "canonical_entity_type": payload.canonical_entity_type,
                                "canonical_entity_id": payload.canonical_entity_id,
                                "review_id": review_id,
                            },
                            ensure_ascii=False,
                        ),
                        resolved_at,
                    ),
                )
        if legacy_decision == "map_to_canonical_candidate":
            canonical_candidate_id = create_canonical_candidate_draft(
                conn,
                review_id=review_id,
                parse_id=existing["parse_id"],
                created_at=resolved_at,
            )
        if all(correction_identity_fields):
            correction_id = record_correction(
                conn,
                CorrectionCreate(
                    entity_type=payload.correction_entity_type,
                    entity_id=payload.correction_entity_id,
                    field_name=payload.correction_field_name,
                    before_value=payload.correction_before_value,
                    after_value=payload.correction_after_value or payload.resolved_value,
                    reason=payload.resolution_note,
                    source_record_id=payload.correction_source_record_id,
                    review_id=review_id,
                    scoring_audit_status=scoring_audit.get("status", ""),
                    scoring_audit_note=scoring_audit.get("note", ""),
                ),
                resolved_at,
            )
        source_context = review_source_context(conn, review_id)
        after.update(
            {
                "correction_id": correction_id,
                "canonical_candidate_id": canonical_candidate_id,
                "strain_registry_apply": strain_registry_apply,
                "legacy_row_review_status": legacy_row_review_status,
                "note_label_update": note_label_update,
                "ear_label_update": ear_label_update,
                "source_parse_id": source_context.get("parse_id") or "",
                "source_photo_id": source_context.get("photo_id") or "",
                "source_photo_filename": source_context.get("original_filename") or "",
                "card_snapshot_id": source_context.get("card_snapshot_id") or "",
                "note_summary": source_context.get("note_summary") or {},
                "boundary": "review item",
            }
        )
        review_action_id = new_id("action")
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                review_action_id,
                "review_resolved",
                review_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                resolved_at,
            ),
        )
    return {
        "review_id": review_id,
        "status": "resolved",
        "resolved_at": resolved_at,
        "resolution_note": payload.resolution_note,
        "resolved_value": payload.resolved_value,
        "legacy_decision": legacy_decision,
        "legacy_row_review_status": legacy_row_review_status,
        "canonical_candidate_id": canonical_candidate_id,
        "strain_registry_apply": strain_registry_apply,
        "correction_id": correction_id,
        "note_label_update": note_label_update,
        "ear_label_update": ear_label_update,
        "scoring_audit": scoring_audit,
        "field_review_outcome": field_review_outcome,
        "review_action_id": review_action_id,
        "audit_url": f"/api/review-items/{quote(review_id)}/audit",
    }


def target_suggestion(conn: Any, mouse_row: Any, normalized_result: str) -> dict[str, str]:
    result_key = compact_genotype(normalized_result)
    if not result_key:
        return {"target_match_status": "unknown", "use_category": "unknown", "next_action": "awaiting_result"}
    rows = conn.execute(
        """
        SELECT target_genotype, purpose
        FROM strain_target_genotype
        WHERE active = 1 AND strain_text = ?
        ORDER BY created_at DESC
        """,
        (mouse_row["raw_strain_text"],),
    ).fetchall()
    if not rows:
        return {"target_match_status": "unknown", "use_category": "unknown", "next_action": "review_result"}
    for row in rows:
        if compact_genotype(row["target_genotype"]) == result_key:
            purpose = row["purpose"] or "unknown"
            if purpose == "mating_candidate":
                return {"target_match_status": "matches_target", "use_category": "mating_candidate", "next_action": "consider_for_mating"}
            if purpose == "experimental_cross":
                return {"target_match_status": "matches_target", "use_category": "experimental_candidate", "next_action": "available_for_experiment"}
            if purpose == "backup":
                return {"target_match_status": "matches_target", "use_category": "backup", "next_action": "review_needed"}
            return {"target_match_status": "matches_target", "use_category": "maintenance_candidate", "next_action": "keep_for_maintenance"}
    return {"target_match_status": "does_not_match_target", "use_category": "cleanup_candidate", "next_action": "cleanup_or_confirm"}


def suggested_genotyping_fields(normalized_result: str, target: dict[str, str] | None = None) -> dict[str, str]:
    result = normalized_result.strip()
    if not result:
        return {
            "genotyping_status": "sampled",
            "genotype_result": "",
            "genotype_result_date": "",
            "target_match_status": "unknown",
            "use_category": "unknown",
            "next_action": "awaiting_result",
        }
    if result.lower() in {"failed", "fail", "no result"}:
        return {
            "genotyping_status": "failed",
            "genotype_result": result,
            "target_match_status": "unknown",
            "use_category": "unknown",
            "next_action": "review_result",
        }
    if target:
        return {
            "genotyping_status": "resulted",
            "genotype_result": result,
            **target,
        }
    return {
        "genotyping_status": "resulted",
        "genotype_result": result,
        "target_match_status": "unknown",
        "use_category": "unknown",
        "next_action": "review_result",
    }


def genotyping_result_evidence_refs(conn: Any, payload: GenotypingUpdate, normalized_result: str) -> dict[str, str | None]:
    if not normalized_result:
        return {
            "source_record_id": payload.source_record_id,
            "source_photo_id": payload.source_photo_id,
            "photo_evidence_id": payload.photo_evidence_id,
        }

    source_record_id = payload.source_record_id
    source_photo_id = payload.source_photo_id
    photo_evidence_id = payload.photo_evidence_id
    if photo_evidence_id:
        evidence = conn.execute(
            """
            SELECT photo_evidence_id, source_photo_id
            FROM photo_evidence_item
            WHERE photo_evidence_id = ?
            """,
            (photo_evidence_id,),
        ).fetchone()
        if evidence is None:
            raise HTTPException(status_code=400, detail="photo_evidence_id does not exist.")
        source_photo_id = source_photo_id or evidence["source_photo_id"]
    if source_photo_id:
        photo = conn.execute("SELECT 1 FROM photo_log WHERE photo_id = ?", (source_photo_id,)).fetchone()
        if photo is None:
            raise HTTPException(status_code=400, detail="source_photo_id does not exist.")
    if source_record_id:
        source = conn.execute(
            "SELECT 1 FROM source_record WHERE source_record_id = ?",
            (source_record_id,),
        ).fetchone()
        if source is None:
            raise HTTPException(status_code=400, detail="source_record_id does not exist.")
    if not any([source_record_id, source_photo_id, photo_evidence_id]):
        raise HTTPException(
            status_code=409,
            detail=(
                "Genotype result confirmation requires evidence: provide source_photo_id, "
                "photo_evidence_id, or source_record_id."
            ),
        )
    return {
        "source_record_id": source_record_id,
        "source_photo_id": source_photo_id,
        "photo_evidence_id": photo_evidence_id,
    }


@app.post("/api/genotyping/update")
def update_genotyping(payload: GenotypingUpdate) -> dict[str, Any]:
    updated_at = utc_now()
    sample_date = payload.sample_date or updated_at[:10]
    normalized_result = payload.normalized_result.strip() or payload.raw_result.strip()
    result_date = payload.result_date or (updated_at[:10] if normalized_result else "")
    with connection() as conn:
        existing = conn.execute(
            """
            SELECT mouse_id, display_id, sample_id, sample_date, genotyping_status,
                   raw_strain_text, genotype_result, genotype_result_date, next_action
            FROM mouse_master
            WHERE mouse_id = ?
            """,
            (payload.mouse_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")
        before = dict(existing)
        evidence_refs = genotyping_result_evidence_refs(conn, payload, normalized_result)
        target = target_suggestion(conn, existing, normalized_result) if normalized_result else None
        suggestions = suggested_genotyping_fields(normalized_result, target)
        conn.execute(
            """
            UPDATE mouse_master
            SET sample_id = ?,
                sample_date = ?,
                genotyping_status = ?,
                genotype = COALESCE(NULLIF(?, ''), genotype),
                genotype_status = ?,
                genotype_result = ?,
                genotype_result_date = ?,
                target_match_status = ?,
                use_category = ?,
                next_action = ?,
                last_verified_at = ?,
                updated_at = ?
            WHERE mouse_id = ?
            """,
            (
                payload.sample_id or before["display_id"],
                sample_date,
                suggestions["genotyping_status"],
                suggestions["genotype_result"],
                "confirmed" if suggestions["genotyping_status"] == "resulted" else "pending",
                suggestions["genotype_result"],
                result_date,
                suggestions["target_match_status"],
                suggestions["use_category"],
                suggestions["next_action"],
                updated_at,
                updated_at,
                payload.mouse_id,
            ),
        )
        record_id = new_id("genotyping")
        conn.execute(
            """
            INSERT INTO genotyping_record
                (genotyping_id, mouse_id, sample_id, sample_date, result_date,
                 target_name, raw_result, normalized_result, result_status,
                 source_photo_id, source_record_id, photo_evidence_id,
                 confidence, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                payload.mouse_id,
                payload.sample_id or before["display_id"],
                sample_date,
                result_date,
                payload.target_name,
                payload.raw_result,
                normalized_result,
                suggestions["genotyping_status"] if suggestions["genotyping_status"] != "sampled" else "pending",
                evidence_refs["source_photo_id"],
                evidence_refs["source_record_id"],
                evidence_refs["photo_evidence_id"],
                1.0 if normalized_result else 0.8,
                payload.notes,
                updated_at,
                updated_at,
            ),
        )
        after = conn.execute(
            """
            SELECT mouse_id, sample_id, sample_date, genotyping_status,
                   genotype_result, genotype_result_date, target_match_status,
                   use_category, next_action
            FROM mouse_master
            WHERE mouse_id = ?
            """,
            (payload.mouse_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "genotyping_resulted" if normalized_result else "sample_collected",
                payload.mouse_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(dict(after) | {"evidence_refs": evidence_refs}, ensure_ascii=False),
                updated_at,
            ),
        )
        if normalized_result:
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    payload.mouse_id,
                    "genotyped",
                    result_date,
                    "genotyping_record",
                    record_id,
                    evidence_refs["source_record_id"],
                    json.dumps(
                        {
                            "sample_id": payload.sample_id or before["display_id"],
                            "target_name": payload.target_name,
                            "raw_result": payload.raw_result,
                            "normalized_result": normalized_result,
                            "source_photo_id": evidence_refs["source_photo_id"],
                            "photo_evidence_id": evidence_refs["photo_evidence_id"],
                        },
                        ensure_ascii=False,
                    ),
                    updated_at,
                ),
            )
    return {"genotyping_id": record_id, **dict(after)}


@app.post("/api/genotyping/request")
def request_genotyping(payload: GenotypingRequestCreate) -> dict[str, Any]:
    requested_at = utc_now()
    sample_date = payload.sample_date or requested_at[:10]
    with connection() as conn:
        existing = conn.execute(
            """
            SELECT mouse_id, display_id, sample_id, sample_date, genotyping_status,
                   genotype_result, next_action, raw_strain_text
            FROM mouse_master
            WHERE mouse_id = ?
            """,
            (payload.mouse_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")
        if existing["genotyping_status"] == "resulted":
            raise HTTPException(status_code=409, detail="Mouse already has a genotyping result.")

        before = dict(existing)
        sample_id = payload.sample_id.strip() or existing["display_id"]
        sample_mapping = load_labeling_rule_sample_mapping(conn, payload.labeling_rule_set_id)
        if sample_mapping == "sample_id_equals_mouse_display_id":
            candidate_rows = conn.execute(
                """
                SELECT mouse_id, display_id
                FROM mouse_master
                WHERE display_id = ?
                  AND status NOT IN ('dead', 'sacrificed', 'archived', 'transferred')
                """,
                (sample_id,),
            ).fetchall()
            [sample_match] = match_samples_to_mice(
                [{"sample_id": sample_id}],
                [dict(row) for row in candidate_rows],
            )
            if sample_match["match_status"] != "matched":
                raise HTTPException(
                    status_code=409,
                    detail="Sample ID could not be uniquely matched to a mouse display ID under the selected labeling rule.",
                )
            if sample_match["mouse_id"] != payload.mouse_id:
                raise HTTPException(
                    status_code=409,
                    detail="Sample ID matches a different mouse display ID under the selected labeling rule.",
                )
        target_name = payload.target_name.strip() or load_labeling_rule_genotyping_target(
            conn, payload.labeling_rule_set_id
        )
        raw_payload = payload.model_dump_json()
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual genotyping request: {existing['display_id']}",
            raw_payload=raw_payload,
            note="Tail biopsy / genotyping request entered from local Genotyping Worklist.",
        )
        conn.execute(
            """
            UPDATE mouse_master
            SET sample_id = ?,
                sample_date = ?,
                genotyping_status = 'submitted',
                genotype_status = 'pending',
                next_action = 'awaiting_result',
                last_verified_at = ?,
                updated_at = ?
            WHERE mouse_id = ?
            """,
            (sample_id, sample_date, requested_at, requested_at, payload.mouse_id),
        )
        record_id = new_id("genotyping")
        conn.execute(
            """
            INSERT INTO genotyping_record
                (genotyping_id, mouse_id, sample_id, sample_date, submitted_date,
                 target_name, raw_result, normalized_result, result_status,
                 confidence, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                payload.mouse_id,
                sample_id,
                sample_date,
                requested_at[:10],
                target_name,
                "",
                "",
                "pending",
                1.0,
                payload.note,
                requested_at,
                requested_at,
            ),
        )
        for event_type in ["tail_biopsy", "genotyping_requested"]:
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    payload.mouse_id,
                    event_type,
                    sample_date,
                    "genotyping_record",
                    record_id,
                    source_record_id,
                    json.dumps(
                        {
                            "display_id": existing["display_id"],
                            "sample_id": sample_id,
                            "target_name": target_name,
                            "labeling_rule_set_id": payload.labeling_rule_set_id,
                            "note": payload.note,
                        },
                        ensure_ascii=False,
                    ),
                    requested_at,
                ),
            )
        after = conn.execute(
            """
            SELECT mouse_id, display_id, sample_id, sample_date,
                   genotyping_status, genotype_status, next_action
            FROM mouse_master
            WHERE mouse_id = ?
            """,
            (payload.mouse_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "genotyping_requested",
                payload.mouse_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(dict(after), ensure_ascii=False),
                requested_at,
            ),
        )

    return {
        "genotyping_id": record_id,
        "source_record_id": source_record_id,
        **dict(after),
    }


@app.get("/api/strain-target-genotypes")
def list_strain_target_genotypes() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT target_id, strain_text, target_genotype, purpose, active, created_at
            FROM strain_target_genotype
            ORDER BY active DESC, strain_text COLLATE NOCASE, created_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["active"] = bool(payload["active"])
        result.append(payload)
    return result


@app.post("/api/strain-target-genotypes")
def create_strain_target_genotype(payload: StrainTargetGenotypeCreate) -> dict[str, Any]:
    target_id = new_id("target_genotype")
    created_at = utc_now()
    strain_text = " ".join(payload.strain_text.split())
    target_genotype = " ".join(payload.target_genotype.split())
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO strain_target_genotype
                (target_id, strain_text, target_genotype, purpose, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (target_id, strain_text, target_genotype, payload.purpose, 1, created_at),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "strain_target_genotype_created",
                target_id,
                None,
                json.dumps({"strain_text": strain_text, "target_genotype": target_genotype, "purpose": payload.purpose}, ensure_ascii=False),
                created_at,
            ),
        )
    return {
        "target_id": target_id,
        "strain_text": strain_text,
        "target_genotype": target_genotype,
        "purpose": payload.purpose,
        "active": True,
        "created_at": created_at,
    }


@app.get("/api/genotype-status-vocabulary")
def list_genotype_status_vocabulary() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT status_key, display_label, meaning, workflow_stage,
                   blocks_experiment, export_warning, legacy_genotyping_status,
                   active, sort_order
            FROM genotype_status_master
            ORDER BY active DESC, sort_order, status_key
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["blocks_experiment"] = bool(payload["blocks_experiment"])
        payload["export_warning"] = bool(payload["export_warning"])
        payload["active"] = bool(payload["active"])
        payload["boundary"] = "canonical structured state"
        result.append(payload)
    return result


@app.get("/api/review-vocabulary")
def review_vocabulary() -> dict[str, Any]:
    with connection() as conn:
        role_rows = conn.execute(
            """
            SELECT role_key, display_label, responsibility, default_priority,
                   active, sort_order
            FROM review_role_master
            ORDER BY active DESC, sort_order, role_key
            """
        ).fetchall()
        priority_rows = conn.execute(
            """
            SELECT priority_key, display_label, severity_rank,
                   export_blocking_hint, response_expectation,
                   active, sort_order
            FROM review_priority_master
            ORDER BY active DESC, sort_order, priority_key
            """
        ).fetchall()
    roles = []
    for row in role_rows:
        payload = dict(row)
        payload["active"] = bool(payload["active"])
        roles.append(payload)
    priorities = []
    for row in priority_rows:
        payload = dict(row)
        payload["export_blocking_hint"] = bool(payload["export_blocking_hint"])
        payload["active"] = bool(payload["active"])
        priorities.append(payload)
    return {
        "boundary": "canonical structured state",
        "source_layer": "configured master",
        "roles": roles,
        "priorities": priorities,
    }


def genotype_status_context(mouse: Any, vocabulary: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {item["status_key"]: item for item in vocabulary}
    by_legacy = {
        item["legacy_genotyping_status"]: item
        for item in vocabulary
        if item["legacy_genotyping_status"]
    }
    key = str(mouse["genotype_status"] or "").strip()
    legacy = str(mouse["genotyping_status"] or "").strip()
    if key in {"", "unknown", "pending"} and legacy in by_legacy:
        return by_legacy[legacy]
    return by_key.get(key) or by_legacy.get(legacy) or by_key["not_requested"]


@app.get("/api/experiment-readiness")
def experiment_readiness(query: str = "") -> dict[str, Any]:
    with connection() as conn:
        vocabulary = list_genotype_status_vocabulary()
        rows = []
        for mouse in mouse_rows(conn, query):
            if mouse["status"] != "active":
                continue
            status = genotype_status_context(mouse, vocabulary)
            reasons = []
            if status["blocks_experiment"]:
                reasons.append(f"Genotype status: {status['display_label']}")
            if mouse["use_category"] != "experimental_candidate":
                reasons.append(f"Use category: {mouse['use_category'] or 'unknown'}")
            if mouse["next_action"] not in {"available_for_experiment", "consider_for_mating"} and mouse["use_category"] == "experimental_candidate":
                reasons.append(f"Next action: {mouse['next_action'] or 'unknown'}")
            ready = not status["blocks_experiment"] and mouse["use_category"] == "experimental_candidate"
            readiness_status = "ready" if ready else ("blocked" if status["blocks_experiment"] else "warning")
            rows.append(
                {
                    "mouse_id": mouse["mouse_id"],
                    "display_id": mouse["display_id"],
                    "strain": mouse["raw_strain_text"] or "",
                    "sex": mouse["sex"] or "",
                    "dob": mouse["dob_raw"] or mouse["dob_start"] or "",
                    "cage": mouse["current_cage_label"] or "",
                    "genotype_result": mouse["genotype_result"] or mouse["genotype"] or "",
                    "genotype_status": status["status_key"],
                    "genotype_status_label": status["display_label"],
                    "blocks_experiment": status["blocks_experiment"],
                    "target_match_status": mouse["target_match_status"] or "",
                    "use_category": mouse["use_category"] or "",
                    "next_action": mouse["next_action"] or "",
                    "last_verified_at": mouse["last_verified_at"] or "",
                    "readiness_status": readiness_status,
                    "readiness_reason": "; ".join(reasons) if reasons else "Ready for experiment planning.",
                    "source_note_item_id": mouse["source_note_item_id"] or "",
                    "source_record_id": mouse["source_record_id"] or "",
                }
            )
    ready_count = sum(1 for row in rows if row["readiness_status"] == "ready")
    blocked_count = sum(1 for row in rows if row["readiness_status"] == "blocked")
    warning_count = sum(1 for row in rows if row["readiness_status"] == "warning")
    return {
        "source_layer": "export or view",
        "boundary": "export or view",
        "query": query,
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "warning_count": warning_count,
        "total_count": len(rows),
        "rows": rows,
    }


@app.get("/api/cages")
def list_cages() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT c.cage_id, c.cage_label, c.location, c.rack, c.shelf,
                   c.cage_type, c.status, c.note, c.source_record_id,
                   c.created_at, c.updated_at,
                   COUNT(a.assignment_id) AS active_mouse_count
            FROM cage_registry c
            LEFT JOIN mouse_cage_assignment a
                ON a.cage_id = c.cage_id AND a.status = 'active'
            GROUP BY c.cage_id
            ORDER BY c.status = 'active' DESC, c.cage_label COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/cages")
def create_cage(payload: CageCreate) -> dict[str, Any]:
    cage_label = " ".join(payload.cage_label.split())
    if not cage_label:
        raise HTTPException(status_code=400, detail="Cage label is required.")
    now = utc_now()
    cage_id = new_id("cage")
    raw_payload = payload.model_dump_json()
    with connection() as conn:
        duplicate = conn.execute(
            "SELECT cage_id FROM cage_registry WHERE LOWER(cage_label) = LOWER(?)",
            (cage_label,),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Cage label already exists.")
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual cage registry entry: {cage_label}",
            raw_payload=raw_payload,
            note="Created from local Cage View form.",
        )
        conn.execute(
            """
            INSERT INTO cage_registry
                (cage_id, cage_label, location, rack, shelf, cage_type, status,
                 note, source_record_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cage_id,
                cage_label,
                payload.location,
                payload.rack,
                payload.shelf,
                payload.cage_type or "holding",
                payload.status or "active",
                payload.note,
                source_record_id,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("action"), "cage_created", cage_id, None, raw_payload, now),
        )
    return {
        "cage_id": cage_id,
        "cage_label": cage_label,
        "status": payload.status or "active",
        "source_record_id": source_record_id,
        "created_at": now,
    }


@app.post("/api/mice/{mouse_id}/move-cage")
def move_mouse_to_cage(mouse_id: str, payload: MouseCageMove) -> dict[str, Any]:
    moved_at = payload.moved_at or utc_now()
    event_date = moved_at[:10]
    with connection() as conn:
        mouse = conn.execute(
            "SELECT mouse_id, display_id FROM mouse_master WHERE mouse_id = ?",
            (mouse_id,),
        ).fetchone()
        if mouse is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")
        cage = conn.execute(
            "SELECT cage_id, cage_label FROM cage_registry WHERE cage_id = ?",
            (payload.cage_id,),
        ).fetchone()
        if cage is None:
            raise HTTPException(status_code=404, detail="Cage not found.")
        evidence_refs = validated_optional_event_evidence_refs(conn, payload)
        previous = conn.execute(
            """
            SELECT a.assignment_id, c.cage_id, c.cage_label
            FROM mouse_cage_assignment a
            JOIN cage_registry c ON c.cage_id = a.cage_id
            WHERE a.mouse_id = ? AND a.status = 'active'
            ORDER BY a.assigned_at DESC
            LIMIT 1
            """,
            (mouse_id,),
        ).fetchone()
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual cage move: {mouse['display_id']} -> {cage['cage_label']}",
            raw_payload=json.dumps(
                {
                    "mouse_id": mouse_id,
                    "display_id": mouse["display_id"],
                    "cage_id": payload.cage_id,
                    "cage_label": cage["cage_label"],
                    "note": payload.note,
                    "source_photo_id": evidence_refs.get("source_photo_id", ""),
                    "source_note_item_id": evidence_refs.get("source_note_item_id", ""),
                    "photo_evidence_id": evidence_refs.get("photo_evidence_id", ""),
                },
                ensure_ascii=False,
            ),
            note="Mouse cage assignment created from local Cage View form.",
        )
        conn.execute(
            """
            UPDATE mouse_cage_assignment
            SET status = 'ended', ended_at = ?
            WHERE mouse_id = ? AND status = 'active'
            """,
            (moved_at, mouse_id),
        )
        assignment_id = new_id("cage_assignment")
        conn.execute(
            """
            INSERT INTO mouse_cage_assignment
                (assignment_id, mouse_id, cage_id, status, assigned_at,
                 source_record_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (assignment_id, mouse_id, payload.cage_id, "active", moved_at, source_record_id, payload.note),
        )
        event_id = new_id("event")
        details = {
            "display_id": mouse["display_id"],
            "from_cage_label": previous["cage_label"] if previous else "",
            "to_cage_label": cage["cage_label"],
            "note": payload.note,
        }
        details.update(evidence_refs)
        conn.execute(
            """
            INSERT INTO mouse_event
                (event_id, mouse_id, event_type, event_date, related_entity_type,
                 related_entity_id, source_record_id, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                mouse_id,
                "moved",
                event_date,
                "cage",
                payload.cage_id,
                source_record_id,
                json.dumps(details, ensure_ascii=False),
                moved_at,
            ),
        )
        conn.execute(
            """
            UPDATE mouse_master
            SET last_verified_at = ?,
                updated_at = ?
            WHERE mouse_id = ?
            """,
            (moved_at, moved_at, mouse_id),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "mouse_cage_moved",
                mouse_id,
                json.dumps(dict(previous) if previous else {}, ensure_ascii=False),
                json.dumps({"cage_id": payload.cage_id, "cage_label": cage["cage_label"]}, ensure_ascii=False),
                moved_at,
            ),
        )
    return {
        "assignment_id": assignment_id,
        "mouse_id": mouse_id,
        "cage_id": payload.cage_id,
        "cage_label": cage["cage_label"],
        "source_record_id": source_record_id,
        "event_id": event_id,
        "assigned_at": moved_at,
    }


@app.get("/api/genotyping-records")
def list_genotyping_records() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT genotyping_id, mouse_id, sample_id, sample_date, submitted_date,
                   result_date, target_name, raw_result, normalized_result,
                   result_status, source_photo_id, confidence, notes, created_at, updated_at
            FROM genotyping_record
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/genotyping-dashboard")
def genotyping_dashboard() -> list[dict[str, Any]]:
    cards = [
        (
            "not_sampled",
            "Not sampled",
            "Separated active mice that still need tail sampling.",
            "status = 'active' AND genotyping_status = 'not_sampled'",
        ),
        (
            "awaiting_result",
            "Awaiting result",
            "Sampled or submitted mice without an accepted genotype result.",
            "status = 'active' AND (genotyping_status IN ('sampled', 'submitted', 'pending') OR next_action = 'awaiting_result')",
        ),
        (
            "failed_ambiguous",
            "Failed / ambiguous",
            "Genotyping records that need retry, interpretation, or review.",
            "status = 'active' AND (genotyping_status = 'failed' OR next_action = 'review_result')",
        ),
        (
            "target_confirmed",
            "Target genotype confirmed",
            "Mice whose result matches a configured strain-level target genotype.",
            "status = 'active' AND genotyping_status = 'resulted' AND target_match_status = 'matches_target'",
        ),
        (
            "non_target",
            "Non-target genotype",
            "Resulted mice that do not match configured target genotype.",
            "status = 'active' AND genotyping_status = 'resulted' AND target_match_status = 'does_not_match_target'",
        ),
        (
            "review_needed",
            "Review needed",
            "Mice with genotype workflow state that should not be acted on silently.",
            "status = 'active' AND (next_action = 'review_needed' OR use_category = 'unknown' OR target_match_status = 'unknown')",
        ),
    ]
    with connection() as conn:
        result = []
        for key, label, meaning, where in cards:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM mouse_master WHERE {where}"
            ).fetchone()
            result.append(
                {
                    "key": key,
                    "label": label,
                    "count": row["count"],
                    "meaning": meaning,
                }
            )
    return result


@app.get("/api/matings")
def list_matings() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT m.mating_id, m.mating_label, m.strain_goal, m.expected_genotype,
                   m.start_date, m.end_date, m.status, m.purpose, m.note,
                   m.source_record_id, m.created_at, m.updated_at,
                   COALESCE((
                       SELECT GROUP_CONCAT(mm.mouse_id || ':' || mouse.display_id, ', ')
                       FROM mating_mouse mm
                       JOIN mouse_master mouse ON mouse.mouse_id = mm.mouse_id
                       WHERE mm.mating_id = m.mating_id AND mm.role = 'male'
                   ), '') AS male_mice,
                   COALESCE((
                       SELECT GROUP_CONCAT(mm.mouse_id || ':' || mouse.display_id, ', ')
                       FROM mating_mouse mm
                       JOIN mouse_master mouse ON mouse.mouse_id = mm.mouse_id
                       WHERE mm.mating_id = m.mating_id AND mm.role = 'female'
                   ), '') AS female_mice,
                   (
                       SELECT COUNT(*)
                       FROM litter_registry l
                       WHERE l.mating_id = m.mating_id
                   ) AS litter_count
            FROM mating_registry m
            ORDER BY m.status = 'active' DESC, m.start_date DESC, m.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/matings")
def create_mating(payload: MatingCreate) -> dict[str, Any]:
    mating_label = " ".join(payload.mating_label.split())
    if not mating_label:
        raise HTTPException(status_code=400, detail="Mating label is required.")

    parent_ids = [
        ("male", payload.male_mouse_id.strip()),
        ("female", payload.female_mouse_id.strip()),
    ]
    parent_ids = [(role, mouse_id) for role, mouse_id in parent_ids if mouse_id]
    if not parent_ids:
        raise HTTPException(status_code=400, detail="At least one mouse is required for a mating.")

    now = utc_now()
    start_date = payload.start_date or now[:10]
    mating_id = new_id("mating")
    raw_payload = payload.model_dump_json()

    with connection() as conn:
        duplicate = conn.execute(
            "SELECT mating_id FROM mating_registry WHERE LOWER(mating_label) = LOWER(?) AND status = 'active'",
            (mating_label,),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Active mating label already exists.")
        parent_rows: dict[str, Any] = {}
        for _, mouse_id in parent_ids:
            mouse = conn.execute(
                "SELECT mouse_id, display_id FROM mouse_master WHERE mouse_id = ?",
                (mouse_id,),
            ).fetchone()
            if mouse is None:
                raise HTTPException(status_code=404, detail=f"Mouse not found: {mouse_id}")
            parent_rows[mouse_id] = mouse

        evidence_refs = validated_optional_event_evidence_refs(conn, payload)
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual mating entry: {mating_label}",
            raw_payload=raw_payload,
            note="Created from local Breeding / Litter View form.",
        )
        conn.execute(
            """
            INSERT INTO mating_registry
                (mating_id, mating_label, strain_goal, expected_genotype, start_date,
                 status, purpose, note, source_record_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mating_id,
                mating_label,
                payload.strain_goal,
                payload.expected_genotype,
                start_date,
                payload.status or "active",
                payload.purpose,
                payload.note,
                source_record_id,
                now,
                now,
            ),
        )
        for role, mouse_id in parent_ids:
            conn.execute(
                """
                INSERT INTO mating_mouse
                    (mating_mouse_id, mating_id, mouse_id, role, joined_date,
                     note, source_record_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id("mating_mouse"), mating_id, mouse_id, role, start_date, payload.note, source_record_id, now),
            )
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    mouse_id,
                    "paired",
                    start_date,
                    "mating",
                    mating_id,
                    source_record_id,
                    json.dumps(
                        {
                            "mating_label": mating_label,
                            "role": role,
                            "display_id": parent_rows[mouse_id]["display_id"],
                            "strain_goal": payload.strain_goal,
                            "expected_genotype": payload.expected_genotype,
                            **evidence_refs,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("action"), "mating_created", mating_id, None, raw_payload, now),
        )

    return {
        "mating_id": mating_id,
        "mating_label": mating_label,
        "status": payload.status or "active",
        "source_record_id": source_record_id,
        "created_at": now,
    }


@app.get("/api/litters")
def list_litters() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, m.mating_label,
                   l.birth_date, l.number_born, l.number_alive, l.number_weaned,
                   l.weaning_date, l.status, l.note, l.source_record_id,
                   l.created_at, l.updated_at,
                   (
                       SELECT COUNT(*)
                       FROM mouse_master mouse
                       WHERE mouse.litter_id = l.litter_id
                   ) AS offspring_count
            FROM litter_registry l
            JOIN mating_registry m ON m.mating_id = l.mating_id
            ORDER BY l.birth_date DESC, l.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/litters")
def create_litter(payload: LitterCreate) -> dict[str, Any]:
    litter_label = " ".join(payload.litter_label.split())
    if not litter_label:
        raise HTTPException(status_code=400, detail="Litter label is required.")

    now = utc_now()
    birth_date = payload.birth_date or now[:10]
    litter_id = new_id("litter")
    raw_payload = payload.model_dump_json()
    with connection() as conn:
        mating = conn.execute(
            "SELECT mating_id, mating_label, start_date FROM mating_registry WHERE mating_id = ?",
            (payload.mating_id,),
        ).fetchone()
        if mating is None:
            raise HTTPException(status_code=404, detail="Mating not found.")
        duplicate = conn.execute(
            "SELECT litter_id FROM litter_registry WHERE LOWER(litter_label) = LOWER(?) AND mating_id = ?",
            (litter_label, payload.mating_id),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Litter label already exists for this mating.")

        block_biological_transition_review(
            conn,
            action_type="litter_created",
            target_id=payload.mating_id,
            raw_payload=raw_payload,
            blocker=litter_creation_biological_blocker(mating, payload, birth_date),
        )
        evidence_refs = validated_optional_event_evidence_refs(conn, payload)
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual litter entry: {litter_label}",
            raw_payload=raw_payload,
            note="Created from local Breeding / Litter View form.",
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
                litter_id,
                litter_label,
                payload.mating_id,
                birth_date,
                payload.number_born,
                payload.number_alive,
                payload.number_weaned,
                payload.weaning_date,
                payload.status or "born",
                payload.note,
                source_record_id,
                now,
                now,
            ),
        )
        parents = conn.execute(
            """
            SELECT mm.mouse_id, mm.role, mouse.display_id
            FROM mating_mouse mm
            JOIN mouse_master mouse ON mouse.mouse_id = mm.mouse_id
            WHERE mm.mating_id = ?
            """,
            (payload.mating_id,),
        ).fetchall()
        for parent in parents:
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    parent["mouse_id"],
                    "litter_produced",
                    birth_date,
                    "litter",
                    litter_id,
                    source_record_id,
                    json.dumps(
                        {
                            "litter_label": litter_label,
                            "mating_label": mating["mating_label"],
                            "role": parent["role"],
                            "display_id": parent["display_id"],
                            "number_born": payload.number_born,
                            "number_alive": payload.number_alive,
                            **evidence_refs,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("action"), "litter_created", litter_id, None, raw_payload, now),
        )

    return {
        "litter_id": litter_id,
        "litter_label": litter_label,
        "mating_id": payload.mating_id,
        "status": payload.status or "born",
        "source_record_id": source_record_id,
        "created_at": now,
    }


@app.post("/api/litters/{litter_id}/offspring")
def create_litter_offspring(litter_id: str, payload: LitterOffspringCreate) -> dict[str, Any]:
    now = utc_now()
    raw_payload = payload.model_dump_json()
    with connection() as conn:
        litter = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, l.birth_date,
                   l.number_born, l.number_alive, m.mating_label, m.strain_goal
            FROM litter_registry l
            JOIN mating_registry m ON m.mating_id = l.mating_id
            WHERE l.litter_id = ?
            """,
            (litter_id,),
        ).fetchone()
        if litter is None:
            raise HTTPException(status_code=404, detail="Litter not found.")
        cage = None
        cage_id = payload.cage_id.strip()
        if cage_id:
            cage = conn.execute(
                "SELECT cage_id, cage_label FROM cage_registry WHERE cage_id = ?",
                (cage_id,),
            ).fetchone()
            if cage is None:
                raise HTTPException(status_code=404, detail="Cage not found.")

        parent_rows = conn.execute(
            """
            SELECT mouse_id, role
            FROM mating_mouse
            WHERE mating_id = ? AND removed_date IS NULL
            """,
            (litter["mating_id"],),
        ).fetchall()
        father_id = next((row["mouse_id"] for row in parent_rows if row["role"] == "male"), None)
        mother_id = next((row["mouse_id"] for row in parent_rows if row["role"] == "female"), None)
        display_prefix = " ".join(payload.display_prefix.split()) or litter["litter_label"]
        planned = []
        for offset in range(payload.count):
            number = payload.start_number + offset
            display_id = f"{display_prefix}-{number:02d}"
            mouse_id = f"mouse_{litter_id}_{number:02d}".replace(" ", "_")
            planned.append((mouse_id, display_id))
        placeholders = ",".join(["?"] * len(planned))
        duplicate = conn.execute(
            f"SELECT mouse_id FROM mouse_master WHERE mouse_id IN ({placeholders}) LIMIT 1",
            [mouse_id for mouse_id, _display_id in planned],
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="One or more offspring IDs already exist for this litter.")

        block_biological_transition_review(
            conn,
            action_type="offspring_created",
            target_id=litter_id,
            raw_payload=raw_payload,
            blocker=offspring_creation_biological_blocker(litter, payload),
        )
        evidence_refs = validated_optional_event_evidence_refs(conn, payload)
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual offspring generation: {litter['litter_label']}",
            raw_payload=raw_payload,
            note="Generated offspring mouse cards from a reviewed litter record.",
        )
        created = []
        for mouse_id, display_id in planned:
            conn.execute(
                """
                INSERT INTO mouse_master
                    (mouse_id, display_id, id_prefix, father_id, mother_id, litter_id,
                     raw_strain_text, sex, dob_raw, dob_start, status, use_category,
                     next_action, source_record_id, last_verified_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mouse_id,
                    display_id,
                    mouse_id_prefix(display_id),
                    father_id,
                    mother_id,
                    litter_id,
                    litter["strain_goal"] or "",
                    payload.sex or "unknown",
                    litter["birth_date"] or "",
                    litter["birth_date"] or "",
                    payload.status or "weaning_pending",
                    "stock",
                    "weaning_due" if payload.status == "weaning_pending" else "sample_needed",
                    source_record_id,
                    now,
                    now,
                    now,
                ),
            )
            if cage is not None:
                conn.execute(
                    """
                    INSERT INTO mouse_cage_assignment
                        (assignment_id, mouse_id, cage_id, status, assigned_at,
                         source_record_id, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("cage_assignment"),
                        mouse_id,
                        cage["cage_id"],
                        "active",
                        now,
                        source_record_id,
                        payload.note,
                    ),
                )
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    mouse_id,
                    "born",
                    litter["birth_date"] or now[:10],
                    "litter",
                    litter_id,
                    source_record_id,
                    json.dumps(
                        {
                            "display_id": display_id,
                            "litter_label": litter["litter_label"],
                            "mating_label": litter["mating_label"],
                            "father_id": father_id,
                            "mother_id": mother_id,
                            "cage_label": cage["cage_label"] if cage else "",
                            **evidence_refs,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            created.append({"mouse_id": mouse_id, "display_id": display_id})

        conn.execute(
            """
            UPDATE litter_registry
            SET number_alive = COALESCE(number_alive, ?),
                status = CASE WHEN status = 'born' THEN 'pre_weaning' ELSE status END,
                updated_at = ?
            WHERE litter_id = ?
            """,
            (payload.count, now, litter_id),
        )
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "offspring_created",
                litter_id,
                None,
                json.dumps(
                    {
                        "litter_id": litter_id,
                        "created_count": len(created),
                        "source_record_id": source_record_id,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    return {
        "litter_id": litter_id,
        "created_count": len(created),
        "created_mice": created,
        "source_record_id": source_record_id,
        "created_at": now,
    }


@app.post("/api/litters/{litter_id}/wean")
def wean_litter(litter_id: str, payload: LitterWeanCreate) -> dict[str, Any]:
    now = utc_now()
    weaning_date = payload.weaning_date or now[:10]
    raw_payload = payload.model_dump_json()
    with connection() as conn:
        litter = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, l.birth_date,
                   l.number_born, l.number_alive, l.number_weaned, l.weaning_date,
                   l.status, m.mating_label
            FROM litter_registry l
            JOIN mating_registry m ON m.mating_id = l.mating_id
            WHERE l.litter_id = ?
            """,
            (litter_id,),
        ).fetchone()
        if litter is None:
            raise HTTPException(status_code=404, detail="Litter not found.")
        if litter["status"] == "weaned":
            raise HTTPException(status_code=409, detail="Litter is already weaned.")

        offspring_rows = conn.execute(
            """
            SELECT mouse_id, display_id, status
            FROM mouse_master
            WHERE litter_id = ?
            ORDER BY display_id COLLATE NOCASE
            """,
            (litter_id,),
        ).fetchall()
        requested_count = payload.number_weaned
        if requested_count is None:
            requested_count = len(offspring_rows) if offspring_rows else (litter["number_alive"] or litter["number_born"] or 0)
        if offspring_rows and requested_count > len(offspring_rows):
            raise HTTPException(status_code=409, detail="Weaned count exceeds generated offspring records.")

        block_biological_transition_review(
            conn,
            action_type="litter_weaned",
            target_id=litter_id,
            raw_payload=raw_payload,
            blocker=weaning_biological_blocker(litter, payload, weaning_date, requested_count),
        )
        evidence_refs = validated_optional_event_evidence_refs(conn, payload)
        source_record_id = create_source_record(
            conn,
            source_type="manual_entry",
            source_label=f"Manual litter weaning: {litter['litter_label']}",
            raw_payload=raw_payload,
            note="Weaning completion entered from local Breeding / Litter View.",
        )
        before = dict(litter)
        selected_offspring = offspring_rows[:requested_count]
        conn.execute(
            """
            UPDATE litter_registry
            SET number_weaned = ?,
                weaning_date = ?,
                status = 'weaned',
                updated_at = ?
            WHERE litter_id = ?
            """,
            (requested_count, weaning_date, now, litter_id),
        )
        for offspring in selected_offspring:
            conn.execute(
                """
                UPDATE mouse_master
                SET status = CASE WHEN status IN ('weaning_pending', 'pre_weaning') THEN 'active' ELSE status END,
                    next_action = CASE WHEN next_action = 'weaning_due' THEN 'sample_needed' ELSE next_action END,
                    last_verified_at = ?,
                    updated_at = ?
                WHERE mouse_id = ?
                """,
                (now, now, offspring["mouse_id"]),
            )
            conn.execute(
                """
                INSERT INTO mouse_event
                    (event_id, mouse_id, event_type, event_date, related_entity_type,
                     related_entity_id, source_record_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("event"),
                    offspring["mouse_id"],
                    "weaned",
                    weaning_date,
                    "litter",
                    litter_id,
                    source_record_id,
                    json.dumps(
                        {
                            "display_id": offspring["display_id"],
                            "litter_label": litter["litter_label"],
                            "mating_label": litter["mating_label"],
                            "previous_status": offspring["status"],
                            "note": payload.note,
                            **evidence_refs,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        after = {
            "litter_id": litter_id,
            "status": "weaned",
            "number_weaned": requested_count,
            "weaning_date": weaning_date,
            "source_record_id": source_record_id,
            "weaned_mouse_ids": [row["mouse_id"] for row in selected_offspring],
        }
        conn.execute(
            """
            INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("action"),
                "litter_weaned",
                litter_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                now,
            ),
        )

    return {
        "litter_id": litter_id,
        "status": "weaned",
        "number_weaned": requested_count,
        "weaning_date": weaning_date,
        "weaned_mouse_count": len(selected_offspring),
        "source_record_id": source_record_id,
        "updated_at": now,
    }


def contains_filter(columns: list[str], query: str) -> tuple[str, list[str]]:
    normalized = f"%{query.lower()}%"
    clause = " OR ".join([f"LOWER(COALESCE({column}, '')) LIKE ?" for column in columns])
    return f"({clause})", [normalized for _ in columns]


@app.get("/api/note-items")
def list_note_items() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT note_item_id, photo_id, parse_id, card_type, line_number, raw_line_text,
                   strike_status, parsed_type, interpreted_status, parsed_mouse_display_id,
                   parsed_ear_label_raw, parsed_ear_label_code, parsed_ear_label_confidence,
                   parsed_ear_label_review_status, parsed_event_date, parsed_count,
                   parsed_metadata_json, confidence, needs_review, created_at
            FROM card_note_item_log
            ORDER BY parse_id, line_number
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["parsed_metadata"] = json_object(payload.pop("parsed_metadata_json", "{}"))
        if payload["parsed_type"] == "unlabeled_numeric_note":
            payload["display_value"] = payload["parsed_metadata"].get("display_ko") or payload["raw_line_text"]
        elif (
            payload["parsed_type"] == "mouse_item"
            and payload["parsed_ear_label_review_status"] in {"check", "needs_review"}
        ):
            payload["display_value"] = (
                payload["parsed_metadata"].get("display")
                or f"{payload['parsed_mouse_display_id'] or '--'} [ear label review: {payload['parsed_ear_label_raw'] or payload['raw_line_text']}]"
            )
        else:
            payload["display_value"] = payload["raw_line_text"]
        result.append(payload)
    return result


@app.get("/api/card-snapshots")
def list_card_snapshots() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT snapshot.card_snapshot_id, snapshot.photo_id, snapshot.parse_id,
                   snapshot.card_type, snapshot.card_id_raw, snapshot.raw_strain_text,
                   snapshot.matched_strain_text, snapshot.sex_raw, snapshot.sex_normalized,
                   snapshot.sex_count_raw, snapshot.count_value, snapshot.dob_raw,
                   snapshot.dob_start, snapshot.dob_end, snapshot.mating_date_raw,
                   snapshot.mating_date_normalized, snapshot.lmo_raw, snapshot.note_summary_json,
                   snapshot.status, snapshot.source_layer, snapshot.confidence,
                   snapshot.created_at, snapshot.updated_at, photo.original_filename
            FROM card_snapshot snapshot
            LEFT JOIN photo_log photo ON photo.photo_id = snapshot.photo_id
            ORDER BY snapshot.updated_at DESC, snapshot.card_snapshot_id
            """
        ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["note_summary"] = json_object(payload.pop("note_summary_json", "{}"))
        payload["boundary"] = payload["source_layer"]
        result.append(payload)
    return result


MOUSE_SELECT = """
    SELECT mouse_id, display_id, id_prefix, strain_id, father_id, mother_id,
           litter_id, raw_strain_text, sex,
           genotype, genotype_status, dob_raw, dob_start, dob_end,
           ear_label_raw, ear_label_code, ear_label_confidence,
           ear_label_review_status, sample_id, sample_date, genotyping_status,
           genotype_result, genotype_result_date, target_match_status,
           use_category, next_action, source_note_item_id,
           current_card_snapshot_id, status, source_photo_id, source_record_id,
           last_verified_at,
           created_at, updated_at,
           (
               SELECT a.cage_id
               FROM mouse_cage_assignment a
               WHERE a.mouse_id = mouse_master.mouse_id AND a.status = 'active'
               ORDER BY a.assigned_at DESC
               LIMIT 1
           ) AS current_cage_id,
           (
               SELECT c.cage_label
               FROM mouse_cage_assignment a
               JOIN cage_registry c ON c.cage_id = a.cage_id
               WHERE a.mouse_id = mouse_master.mouse_id AND a.status = 'active'
               ORDER BY a.assigned_at DESC
               LIMIT 1
           ) AS current_cage_label
    FROM mouse_master
"""


def mouse_rows(conn: Any, query: str = "") -> list[Any]:
    params: list[str] = []
    where = ""
    if query.strip():
        normalized = f"%{query.strip().lower()}%"
        clause, params = contains_filter(
            [
                "display_id",
                "raw_strain_text",
                "sex",
                "genotype",
                "genotype_status",
                "dob_raw",
                "ear_label_raw",
                "ear_label_code",
                "sample_id",
                "genotyping_status",
                "genotype_result",
                "use_category",
                "next_action",
                "status",
                "source_note_item_id",
            ],
            query.strip(),
        )
        where = f"""
        WHERE {clause}
           OR EXISTS (
               SELECT 1
               FROM mouse_cage_assignment a
               JOIN cage_registry c ON c.cage_id = a.cage_id
               WHERE a.mouse_id = mouse_master.mouse_id
                 AND a.status = 'active'
                 AND LOWER(COALESCE(c.cage_label, '') || ' ' || COALESCE(c.location, '')) LIKE ?
           )
        """
        params.append(normalized)
    return conn.execute(
        f"""
        {MOUSE_SELECT}
        {where}
        ORDER BY display_id COLLATE NOCASE, created_at
        """,
        params,
    ).fetchall()


def xlsx_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_cell_xml(row_number: int, column_number: int, value: Any, style_id: int = 0) -> str:
    ref = f"{xlsx_column_name(column_number)}{row_number}"
    text = html.escape("" if value is None else str(value), quote=False)
    style_attr = f' s="{style_id}"' if style_id else ""
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{text}</t></is></c>'


def xlsx_sheet_xml(headers: list[str], rows: list[list[Any]], column_widths: list[int] | None = None) -> str:
    matrix = [headers, *rows]
    row_xml = []
    for row_index, values in enumerate(matrix, start=1):
        style_id = 1 if row_index == 1 else 0
        cells = "".join(
            xlsx_cell_xml(row_index, column_index, value, style_id)
            for column_index, value in enumerate(values, start=1)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    last_col = xlsx_column_name(max(len(headers), 1))
    last_row = max(len(matrix), 1)
    widths = column_widths or [max(12, min(35, len(str(header)) + 4)) for header in headers]
    cols_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:{last_col}{last_row}"/>
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{cols_xml}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
</worksheet>"""


def build_xlsx(
    sheet_name: str,
    headers: list[str],
    rows: list[list[Any]],
    trace_rows: list[list[Any]] | None = None,
    column_widths: list[int] | None = None,
) -> bytes:
    sheets = [
        {
            "name": sheet_name[:31] or "Sheet1",
            "xml": xlsx_sheet_xml(headers, rows, column_widths),
            "target": "worksheets/sheet1.xml",
        }
    ]
    if trace_rows:
        trace_headers = [
            "Row",
            "Source note",
            "Source record",
            "Boundary",
            "Export note",
            "Source photo",
            "Card snapshot",
            "Raw note line",
            "Uncertainty",
            "Row state",
            "Row state reason",
        ]
        sheets.append(
            {
                "name": "Export_Trace",
                "xml": xlsx_sheet_xml(
                    trace_headers,
                    trace_rows,
                    [10, 28, 28, 24, 40, 26, 28, 42, 36, 18, 42],
                ),
                "target": "worksheets/sheet2.xml",
            }
        )
    sheet_entries = "".join(
        f'<sheet name="{html.escape(sheet["name"], quote=True)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(sheets, start=1)
    )
    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheet_entries}</sheets>
</workbook>"""
    rel_entries = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="{sheet["target"]}"/>'
        for index, sheet in enumerate(sheets, start=1)
    )
    rel_entries += f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    workbook_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {rel_entries}
</Relationships>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/>
  <tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    sheet_overrides = "".join(
        f'<Override PartName="/xl/{sheet["target"]}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for sheet in sheets
    )
    content_types = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {sheet_overrides}
</Types>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types)
        workbook.writestr("_rels/.rels", root_rels)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        workbook.writestr("xl/styles.xml", styles_xml)
        for sheet in sheets:
            workbook.writestr(f"xl/{sheet['target']}", sheet["xml"])
    return output.getvalue()


def export_filename(export_kind: str, preview: dict[str, Any], query: str = "") -> str:
    if query.strip():
        strain = query.strip()
    elif export_kind == "animal":
        strain = next((row.get("strain") for row in preview["animal_sheet_rows"] if row.get("strain")), "selected strain")
    else:
        strain = next((row.get("strain") for row in preview["separation_rows"] if row.get("strain")), "selected strain")
    safe_strain = re.sub(r'[<>:"/\\|?*]+', " ", strain).strip() or "selected strain"
    safe_strain = re.sub(r"\s+", " ", safe_strain)
    date_label = utc_now()[:10].replace("-", "")
    suffix = "animal sheet" if export_kind == "animal" else "분리 현황표"
    return f"{date_label} {safe_strain} {suffix}.xlsx"


def trace_rows_from_export_rows(rows: list[dict[str, Any]], source_key: str) -> list[list[Any]]:
    trace_rows = []
    for index, row in enumerate(rows, start=2):
        source_value = row.get(source_key) or row.get("source") or ""
        trace_rows.append(
            [
                index,
                source_value,
                row.get("source_record_id", ""),
                "export or view",
                row.get("export_note", "Generated from accepted structured state."),
                row.get("source_photo_ids", "") or row.get("source_photo_id", ""),
                row.get("card_snapshot_ids", "") or row.get("card_snapshot_id", ""),
                row.get("raw_note_lines", "") or row.get("raw_note_line", ""),
                row.get("uncertainty", ""),
                row.get("row_state", ""),
                row.get("row_state_reason", ""),
            ]
        )
    return trace_rows


def workbook_content_disposition(filename: str, fallback: str) -> str:
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def log_workbook_export(
    export_type: str,
    filename: str,
    query: str,
    row_count: int,
    blocked_review_count: int,
    status: str,
    manifest_artifact_path: str = "",
    validation_report_id: str = "",
    state_watermark: str = "",
) -> None:
    note = (
        "Blocked final XLSX export because Focus Review blockers remain."
        if status == "blocked"
        else "XLSX generated from workbook preview."
    )
    provenance_bits = [
        f"manifest={manifest_artifact_path}" if manifest_artifact_path else "",
        f"validation_report={validation_report_id}" if validation_report_id else "",
        f"state_watermark={state_watermark}" if state_watermark else "",
    ]
    provenance_note = "; ".join(bit for bit in provenance_bits if bit)
    if provenance_note:
        note = f"{note} {provenance_note}"
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO export_log
                (export_id, export_type, filename, query, row_count,
                 blocked_review_count, status, exported_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("export"),
                export_type,
                filename,
                query.strip(),
                row_count,
                blocked_review_count,
                status,
                utc_now(),
                note,
            ),
        )


def export_staleness(conn: Any) -> dict[str, Any]:
    latest_data_change = conn.execute(
        """
        SELECT MAX(changed_at) AS changed_at
        FROM (
            SELECT uploaded_at AS changed_at FROM photo_log
            UNION ALL SELECT parsed_at FROM parse_result
            UNION ALL SELECT created_at FROM review_queue
            UNION ALL SELECT resolved_at FROM review_queue WHERE resolved_at IS NOT NULL
            UNION ALL SELECT imported_at FROM source_record
            UNION ALL SELECT updated_at FROM strain_registry
            UNION ALL SELECT corrected_at FROM correction_log
            UNION ALL SELECT updated_at FROM card_snapshot
            UNION ALL SELECT created_at FROM mouse_event
            UNION ALL SELECT updated_at FROM genotyping_record
            UNION ALL SELECT updated_at FROM cage_registry
            UNION ALL SELECT assigned_at FROM mouse_cage_assignment
            UNION ALL SELECT ended_at FROM mouse_cage_assignment WHERE ended_at IS NOT NULL
            UNION ALL SELECT updated_at FROM mating_registry
            UNION ALL SELECT updated_at FROM litter_registry
            UNION ALL SELECT updated_at FROM mouse_master
            UNION ALL SELECT created_at FROM action_log
        )
        """
    ).fetchone()["changed_at"]
    latest_generated_export = conn.execute(
        """
        SELECT MAX(exported_at) AS exported_at
        FROM export_log
        WHERE status = 'generated'
        """
    ).fetchone()["exported_at"]
    return {
        "latest_data_change_at": latest_data_change or "",
        "latest_generated_export_at": latest_generated_export or "",
        "export_stale": bool(latest_data_change and (not latest_generated_export or latest_data_change > latest_generated_export)),
    }


def search_index_available(conn: Any) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'search_index'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def latest_search_data_change(conn: Any) -> str:
    return export_staleness(conn)["latest_data_change_at"]


def search_text(*values: Any) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip())


def insert_search_document(
    conn: Any,
    entity_type: str,
    entity_id: str,
    title: str,
    body: str,
    source_layer: str,
    updated_at: str,
) -> None:
    if not entity_id:
        return
    conn.execute(
        """
        INSERT INTO search_index
            (entity_type, entity_id, title, body, source_layer, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, title, body, source_layer, updated_at),
    )


def rebuild_search_index(conn: Any) -> bool:
    if not search_index_available(conn):
        return False
    conn.execute("DELETE FROM search_index")

    for row in conn.execute(
        """
        SELECT mouse_id, display_id, raw_strain_text, sex, genotype,
               genotype_status, genotype_result, sample_id, next_action,
               status, source_note_item_id, source_record_id, updated_at
        FROM mouse_master
        """
    ).fetchall():
        insert_search_document(
            conn,
            "mouse",
            row["mouse_id"],
            row["display_id"],
            search_text(
                row["display_id"],
                row["raw_strain_text"],
                row["sex"],
                row["genotype"],
                row["genotype_status"],
                row["genotype_result"],
                row["sample_id"],
                row["next_action"],
                row["status"],
                row["source_note_item_id"],
                row["source_record_id"],
            ),
            "canonical structured state",
            row["updated_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT strain_id, strain_name, common_name, official_name, gene,
               allele, background, source, status, owner, updated_at
        FROM strain_registry
        """
    ).fetchall():
        insert_search_document(
            conn,
            "strain",
            row["strain_id"],
            row["strain_name"],
            search_text(*dict(row).values()),
            "canonical structured state",
            row["updated_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT review_id, parse_id, severity, issue, current_value,
               suggested_value, review_reason, status, assigned_role,
               priority, created_at
        FROM review_queue
        """
    ).fetchall():
        insert_search_document(
            conn,
            "review",
            row["review_id"],
            row["issue"],
            search_text(*dict(row).values()),
            "review item",
            row["created_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT source_record_id, source_type, source_uri, source_label,
               raw_payload, checksum, note, imported_at
        FROM source_record
        """
    ).fetchall():
        insert_search_document(
            conn,
            "source",
            row["source_record_id"],
            row["source_label"] or row["source_type"],
            search_text(*dict(row).values()),
            "raw source",
            row["imported_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT photo_id, original_filename, stored_path, status,
               raw_source_kind, uploaded_at
        FROM photo_log
        """
    ).fetchall():
        insert_search_document(
            conn,
            "photo",
            row["photo_id"],
            row["original_filename"],
            search_text(*dict(row).values()),
            "raw source",
            row["uploaded_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT note_item_id, photo_id, parse_id, card_type, line_number,
               raw_line_text, strike_status, parsed_type, interpreted_status,
               parsed_mouse_display_id, parsed_ear_label_raw,
               parsed_ear_label_code, parsed_ear_label_review_status,
               parsed_metadata_json, created_at
        FROM card_note_item_log
        """
    ).fetchall():
        insert_search_document(
            conn,
            "note_line",
            row["note_item_id"],
            row["raw_line_text"],
            search_text(*dict(row).values()),
            "parsed or intermediate result",
            row["created_at"] or "",
        )

    for row in conn.execute(
        """
        SELECT genotyping_id, mouse_id, sample_id, sample_date,
               submitted_date, result_date, target_name, raw_result,
               normalized_result, result_status, notes, updated_at
        FROM genotyping_record
        """
    ).fetchall():
        insert_search_document(
            conn,
            "genotyping",
            row["genotyping_id"],
            row["sample_id"] or row["target_name"] or row["genotyping_id"],
            search_text(*dict(row).values()),
            "event/history record",
            row["updated_at"] or "",
        )

    latest = latest_search_data_change(conn)
    conn.execute(
        """
        INSERT INTO search_index_meta (key, value)
        VALUES ('latest_data_change_at', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (latest,),
    )
    return True


def ensure_search_index_current(conn: Any) -> bool:
    if not search_index_available(conn):
        return False
    latest = latest_search_data_change(conn)
    indexed = conn.execute(
        "SELECT value FROM search_index_meta WHERE key = 'latest_data_change_at'"
    ).fetchone()
    if indexed is None or indexed["value"] != latest:
        return rebuild_search_index(conn)
    return True


def fts_query(term: str) -> str:
    tokens = re.findall(r"\w+", term, flags=re.UNICODE)
    return " ".join(token.replace('"', '""') for token in tokens[:8])


def search_index_hits(conn: Any, term: str, limit: int = 50) -> list[dict[str, Any]]:
    if not ensure_search_index_current(conn):
        return []
    query = fts_query(term)
    if not query:
        return []
    try:
        rows = conn.execute(
            """
            SELECT entity_type, entity_id, title,
                   snippet(search_index, 3, '[', ']', '...', 12) AS snippet,
                   source_layer, updated_at, rank
            FROM search_index
            WHERE search_index MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def ids_from_hits(hits: list[dict[str, Any]], entity_type: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for hit in hits:
        if hit["entity_type"] == entity_type and hit["entity_id"] not in seen:
            result.append(str(hit["entity_id"]))
            seen.add(str(hit["entity_id"]))
    return result


def order_by_ids(rows: list[Any], id_column: str, ordered_ids: list[str]) -> list[dict[str, Any]]:
    order = {value: index for index, value in enumerate(ordered_ids)}
    return sorted([dict(row) for row in rows], key=lambda row: order.get(str(row[id_column]), len(order)))


def animal_sheet_validation_issue(issue: Any) -> bool:
    return str(issue or "").strip().lower() in ANIMAL_SHEET_VALIDATION_ISSUES


def open_review_attention_counts(conn: Any, *, exclude_animal_sheet_validation: bool = False) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT review.review_id, review.parse_id, review.severity, review.issue,
               review.current_value, review.suggested_value, review.review_reason,
               review.assigned_role, review.assigned_to, review.priority,
               review.status, review.created_at,
               parse.source_name, parse.photo_id, parse.raw_payload AS parse_raw_payload,
               parse.confidence AS parse_confidence
        FROM review_queue review
        LEFT JOIN parse_result parse ON parse.parse_id = review.parse_id
        WHERE review.status = 'open'
        """
    ).fetchall()
    counts = {"must_review": 0, "quick_check": 0, "trace_only": 0, "hidden_default": 0}
    for row in rows:
        payload = dict(row)
        if exclude_animal_sheet_validation and animal_sheet_validation_issue(payload.get("issue")):
            continue
        payload["confidence"] = payload.get("parse_confidence")
        parse_payload = json_object(payload.pop("parse_raw_payload", "{}"))
        payload.pop("parse_confidence", None)
        attention = review_attention_level(payload, parse_payload)["attention_level"]
        counts[attention] = counts.get(attention, 0) + 1
    return counts


def open_review_blockers(
    conn: Any,
    limit: int = 10,
    *,
    exclude_animal_sheet_validation: bool = False,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT review.review_id, review.parse_id, review.severity, review.issue,
               review.current_value, review.suggested_value, review.review_reason,
               review.assigned_role, review.assigned_to, review.priority,
               review.status, review.created_at,
               parse.source_name, parse.photo_id, parse.raw_payload AS parse_raw_payload,
               parse.confidence AS parse_confidence, photo.original_filename,
               (
                   SELECT COUNT(*)
                   FROM card_note_item_log note
                   WHERE note.parse_id = review.parse_id
               ) AS note_line_count,
               COALESCE((
                   SELECT GROUP_CONCAT(note.raw_line_text, ' | ')
                   FROM card_note_item_log note
                   WHERE note.parse_id = review.parse_id
               ), '') AS evidence_preview
        FROM review_queue review
        LEFT JOIN parse_result parse ON parse.parse_id = review.parse_id
        LEFT JOIN photo_log photo ON photo.photo_id = parse.photo_id
        WHERE review.status = 'open'
        ORDER BY review.severity DESC, review.created_at DESC
        """
    ).fetchall()
    blockers: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        if exclude_animal_sheet_validation and animal_sheet_validation_issue(payload.get("issue")):
            continue
        payload["confidence"] = payload.get("parse_confidence")
        parse_payload = json_object(payload.pop("parse_raw_payload", "{}"))
        payload.pop("parse_confidence", None)
        attention = review_attention_level(payload, parse_payload)
        payload.update(attention)
        payload["review_plausibility_findings"] = parse_payload_plausibility_findings(parse_payload)
        payload["review_check_targets"] = review_check_targets(payload, parse_payload)
        if payload["attention_level"] == "must_review":
            blockers.append(payload)
        if len(blockers) >= limit:
            break
    return blockers


def export_review_blocker_count(conn: Any) -> int:
    return open_review_attention_counts(conn).get("must_review", 0)


def genotype_export_blockers(conn: Any, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT mouse.mouse_id, mouse.display_id, mouse.raw_strain_text,
               mouse.genotype_status, mouse.genotyping_status,
               mouse.genotype_result, mouse.next_action,
               status.status_key, status.display_label, status.meaning,
               status.workflow_stage, status.blocks_experiment,
               status.export_warning, status.legacy_genotyping_status
        FROM mouse_master mouse
        JOIN genotype_status_master status
          ON (
              status.status_key = mouse.genotype_status
              OR (
                  status.legacy_genotyping_status = mouse.genotyping_status
                  AND mouse.genotype_status IN ('', 'unknown', 'pending', 'confirmed')
              )
          )
        WHERE mouse.status = 'active'
          AND status.active = 1
          AND (status.blocks_experiment = 1 OR status.export_warning = 1)
        ORDER BY status.blocks_experiment DESC,
                 status.export_warning DESC,
                 status.sort_order,
                 mouse.display_id COLLATE NOCASE
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    blockers = []
    for row in rows:
        payload = dict(row)
        issue = (
            f"Genotype status blocks experiment: {payload['display_label']}"
            if payload["blocks_experiment"]
            else f"Genotype status export warning: {payload['display_label']}"
        )
        blockers.append(
            {
                "review_id": "",
                "parse_id": "",
                "severity": "High" if payload["blocks_experiment"] else "Medium",
                "issue": issue,
                "suggested_value": payload["genotype_result"] or payload["genotyping_status"] or payload["genotype_status"],
                "review_reason": (
                    f"Mouse {payload['display_id']} is {payload['display_label']} "
                    f"({payload['status_key']}). {payload['meaning']}"
                ),
                "created_at": "",
                "source_name": "genotype_status_master",
                "photo_id": "",
                "original_filename": "",
                "note_line_count": 0,
                "evidence_preview": f"{payload['display_id']} / {payload['raw_strain_text'] or '--'} / {payload['next_action'] or '--'}",
                "assigned_role": "Experiment Planner",
                "assigned_to": "",
                "priority": "high" if payload["blocks_experiment"] else "medium",
                "mouse_id": payload["mouse_id"],
                "display_id": payload["display_id"],
                "blocker_type": "genotype_status",
                "status_key": payload["status_key"],
                "workflow_stage": payload["workflow_stage"],
                "blocks_experiment": bool(payload["blocks_experiment"]),
                "export_warning": bool(payload["export_warning"]),
            }
        )
    return blockers


@app.get("/api/mice")
def list_mice(query: str = "") -> list[dict[str, Any]]:
    with connection() as conn:
        rows = mouse_rows(conn, query)
    return [dict(row) for row in rows]


@app.get("/api/mice/{mouse_id}/audit-trace")
@app.get("/api/mice/{mouse_id}/audit-trail")
def mouse_audit_trace(mouse_id: str) -> dict[str, Any]:
    with connection() as conn:
        mouse = conn.execute(
            f"""
            {MOUSE_SELECT}
            WHERE mouse_id = ?
            """,
            (mouse_id,),
        ).fetchone()
        if mouse is None:
            raise HTTPException(status_code=404, detail="Mouse not found.")

        note_items = conn.execute(
            """
            SELECT note_item_id, photo_id, parse_id, line_number, raw_line_text,
                   strike_status, parsed_type, interpreted_status,
                   parsed_ear_label_raw, parsed_ear_label_code,
                   parsed_ear_label_review_status, confidence, needs_review, created_at
            FROM card_note_item_log
            WHERE note_item_id = ?
               OR parsed_mouse_display_id = ?
            ORDER BY created_at, line_number
            """,
            (mouse["source_note_item_id"], mouse["display_id"]),
        ).fetchall()
        parse_ids = sorted({row["parse_id"] for row in note_items if row["parse_id"]})

        event_rows = conn.execute(
            """
            SELECT event_id, event_type, event_date, related_entity_type,
                   related_entity_id, source_record_id, details, created_by, created_at
            FROM mouse_event
            WHERE mouse_id = ?
            ORDER BY event_date, created_at, event_id
            """,
            (mouse_id,),
        ).fetchall()
        correction_rows = conn.execute(
            """
            SELECT correction_id, entity_type, entity_id, field_name,
                   before_value, after_value, reason, source_record_id,
                   review_id, corrected_at
            FROM correction_log
            WHERE entity_type = 'mouse' AND entity_id IN (?, ?)
            ORDER BY corrected_at, correction_id
            """,
            (mouse_id, mouse["display_id"]),
        ).fetchall()
        genotype_rows = conn.execute(
            """
            SELECT genotyping_id, sample_id, sample_date, submitted_date, result_date,
                   target_name, raw_result, normalized_result, result_status, source_photo_id,
                   confidence, notes, created_at
            FROM genotyping_record
            WHERE mouse_id = ? OR sample_id IN (?, ?)
            ORDER BY created_at, genotyping_id
            """,
            (mouse_id, mouse["sample_id"] or "", mouse["display_id"]),
        ).fetchall()
        cage_assignment_rows = conn.execute(
            """
            SELECT a.assignment_id, a.cage_id, c.cage_label, c.location,
                   a.status, a.assigned_at, a.ended_at, a.source_record_id, a.note
            FROM mouse_cage_assignment a
            JOIN cage_registry c ON c.cage_id = a.cage_id
            WHERE a.mouse_id = ?
            ORDER BY a.assigned_at, a.assignment_id
            """,
            (mouse_id,),
        ).fetchall()

        action_target_ids = {mouse_id, mouse["display_id"]}
        if mouse["litter_id"]:
            action_target_ids.add(mouse["litter_id"])
        action_target_ids = {target_id for target_id in action_target_ids if target_id}
        action_rows = []
        if action_target_ids:
            placeholders = ", ".join("?" for _ in action_target_ids)
            action_rows = conn.execute(
                f"""
                SELECT action_id, action_type, target_id, before_value, after_value, created_at
                FROM action_log
                WHERE target_id IN ({placeholders})
                ORDER BY created_at, action_id
                """,
                sorted(action_target_ids),
            ).fetchall()

        father = (
            conn.execute(f"{MOUSE_SELECT} WHERE mouse_id = ?", (mouse["father_id"],)).fetchone()
            if mouse["father_id"]
            else None
        )
        mother = (
            conn.execute(f"{MOUSE_SELECT} WHERE mouse_id = ?", (mouse["mother_id"],)).fetchone()
            if mouse["mother_id"]
            else None
        )
        litter = (
            conn.execute(
                """
                SELECT l.litter_id, l.litter_label, l.mating_id, m.mating_label,
                       l.birth_date, l.number_born, l.number_alive, l.number_weaned,
                       l.weaning_date, l.status, l.source_record_id
                FROM litter_registry l
                LEFT JOIN mating_registry m ON m.mating_id = l.mating_id
                WHERE l.litter_id = ?
                """,
                (mouse["litter_id"],),
            ).fetchone()
            if mouse["litter_id"]
            else None
        )

        display_like = f"%{mouse['display_id']}%"
        mouse_id_like = f"%{mouse_id}%"
        review_clause = """
            (current_value LIKE ? OR suggested_value LIKE ? OR review_reason LIKE ?
             OR issue LIKE ? OR parse_id LIKE ?
             OR current_value LIKE ? OR suggested_value LIKE ? OR review_reason LIKE ?
             OR issue LIKE ? OR parse_id LIKE ?)
        """
        review_clauses = [review_clause]
        review_params: list[Any] = [display_like] * 5 + [mouse_id_like] * 5
        if parse_ids:
            placeholders = ", ".join("?" for _ in parse_ids)
            review_clauses.append(f"parse_id IN ({placeholders})")
            review_params.extend(parse_ids)
        review_rows = conn.execute(
            f"""
            SELECT review_id, parse_id, severity, issue, current_value,
                   suggested_value, review_reason, status, created_at,
                   resolved_at, resolution_note
            FROM review_queue
            WHERE {' OR '.join(review_clauses)}
            ORDER BY created_at, review_id
            """,
            review_params,
        ).fetchall()

        source_ids = {
            mouse["source_record_id"],
            *[row["source_record_id"] for row in event_rows],
            *[row["source_record_id"] for row in correction_rows],
            *[row["source_record_id"] for row in cage_assignment_rows],
            litter["source_record_id"] if litter else None,
        }
        source_ids = {source_id for source_id in source_ids if source_id}
        source_rows = []
        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            source_rows = conn.execute(
                f"""
                SELECT source_record_id, source_type, source_uri, source_label,
                       checksum, note, imported_at
                FROM source_record
                WHERE source_record_id IN ({placeholders})
                ORDER BY imported_at, source_record_id
                """,
                sorted(source_ids),
            ).fetchall()

    timeline = []
    for row in note_items:
        timeline.append(
            {
                "category": "note_line",
                "at": row["created_at"],
                "title": f"Note line {row['line_number'] or ''}".strip(),
                "evidence_id": row["note_item_id"],
                "source_record_id": None,
                "details": {
                    "raw_line_text": row["raw_line_text"],
                    "strike_status": row["strike_status"],
                    "interpreted_status": row["interpreted_status"],
                    "ear_label_raw": row["parsed_ear_label_raw"],
                    "ear_label_code": row["parsed_ear_label_code"],
                    "review_status": row["parsed_ear_label_review_status"],
                },
            }
        )
    for row in event_rows:
        timeline.append(
            {
                "category": "mouse_event",
                "at": row["event_date"],
                "title": row["event_type"],
                "evidence_id": row["event_id"],
                "source_record_id": row["source_record_id"],
                "details": {
                    "related": ":".join([row["related_entity_type"] or "", row["related_entity_id"] or ""]).strip(":"),
                    "details": json_object(row["details"]),
                    "created_by": row["created_by"],
                },
            }
        )
    for row in cage_assignment_rows:
        timeline.append(
            {
                "category": "cage_assignment",
                "at": row["assigned_at"],
                "title": f"{row['status']} cage {row['cage_label']}",
                "evidence_id": row["assignment_id"],
                "source_record_id": row["source_record_id"],
                "details": {
                    "cage_id": row["cage_id"],
                    "location": row["location"],
                    "ended_at": row["ended_at"],
                    "note": row["note"],
                },
            }
        )
    for row in review_rows:
        timeline.append(
            {
                "category": "review",
                "at": row["created_at"],
                "title": row["issue"],
                "evidence_id": row["review_id"],
                "source_record_id": None,
                "details": {
                    "status": row["status"],
                    "severity": row["severity"],
                    "current_value": row["current_value"],
                    "suggested_value": row["suggested_value"],
                    "resolution_note": row["resolution_note"],
                },
            }
        )
    for row in correction_rows:
        timeline.append(
            {
                "category": "correction",
                "at": row["corrected_at"],
                "title": row["field_name"],
                "evidence_id": row["correction_id"],
                "source_record_id": row["source_record_id"],
                "details": {
                    "before": row["before_value"],
                    "after": row["after_value"],
                    "reason": row["reason"],
                    "review_id": row["review_id"],
                },
            }
        )
    for row in genotype_rows:
        timeline.append(
            {
                "category": "genotyping",
                "at": row["result_date"] or row["submitted_date"] or row["sample_date"] or row["created_at"],
                "title": row["result_status"],
                "evidence_id": row["genotyping_id"],
                "source_record_id": None,
                "details": {
                    "sample_id": row["sample_id"],
                    "target_name": row["target_name"],
                    "raw_result": row["raw_result"],
                    "normalized_result": row["normalized_result"],
                    "confidence": row["confidence"],
                    "notes": row["notes"],
                },
            }
        )
    for row in action_rows:
        timeline.append(
            {
                "category": "action_log",
                "at": row["created_at"],
                "title": row["action_type"],
                "evidence_id": row["action_id"],
                "source_record_id": None,
                "details": {
                    "target_id": row["target_id"],
                    "before": json_object(row["before_value"]),
                    "after": json_object(row["after_value"]),
                },
            }
        )
    timeline.sort(key=lambda item: (item["at"] or "", item["category"], item["evidence_id"] or ""))

    return {
        "source_layer": "export or view",
        "mouse": dict(mouse),
        "lineage": {
            "father": dict(father) if father else None,
            "mother": dict(mother) if mother else None,
            "litter": dict(litter) if litter else None,
        },
        "source_records": [dict(row) for row in source_rows],
        "note_items": [dict(row) for row in note_items],
        "review_items": [dict(row) for row in review_rows],
        "corrections": [dict(row) for row in correction_rows],
        "events": [dict(row) | {"details": json_object(row["details"])} for row in event_rows],
        "cage_assignments": [dict(row) for row in cage_assignment_rows],
        "actions": [dict(row) for row in action_rows],
        "genotyping_records": [dict(row) for row in genotype_rows],
        "timeline": timeline,
    }


@app.get("/api/search")
def search_records(query: str = "") -> dict[str, Any]:
    term = query.strip()
    if not term:
        return {"query": "", "mice": [], "strains": [], "reviews": [], "sources": [], "hits": []}

    with connection() as conn:
        hits = search_index_hits(conn, term)
        mouse_ids = ids_from_hits(hits, "mouse")
        strain_ids = ids_from_hits(hits, "strain")
        review_ids = ids_from_hits(hits, "review")
        source_ids = ids_from_hits(hits, "source")

        if mouse_ids:
            placeholders = ", ".join("?" for _ in mouse_ids)
            mouse_rows_by_hit = conn.execute(
                f"""
                {MOUSE_SELECT}
                WHERE mouse_id IN ({placeholders})
                """,
                mouse_ids,
            ).fetchall()
            mouse_matches = order_by_ids(mouse_rows_by_hit, "mouse_id", mouse_ids)[:25]
        else:
            mouse_matches = [dict(row) for row in mouse_rows(conn, term)[:25]]

        if strain_ids:
            placeholders = ", ".join("?" for _ in strain_ids)
            strain_rows = conn.execute(
                f"""
                SELECT strain_id, strain_name, gene, allele, background, source, status, owner
                FROM strain_registry
                WHERE strain_id IN ({placeholders})
                """,
                strain_ids,
            ).fetchall()
            strains = order_by_ids(strain_rows, "strain_id", strain_ids)[:25]
        else:
            strain_clause, strain_params = contains_filter(
                ["strain_name", "common_name", "official_name", "gene", "allele", "background", "source", "status", "owner"],
                term,
            )
            strains = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT strain_id, strain_name, gene, allele, background, source, status, owner
                    FROM strain_registry
                    WHERE {strain_clause}
                    ORDER BY strain_name COLLATE NOCASE
                    LIMIT 25
                    """,
                    strain_params,
                ).fetchall()
            ]

        if review_ids:
            placeholders = ", ".join("?" for _ in review_ids)
            review_rows = conn.execute(
                f"""
                SELECT review_id, parse_id, severity, issue, suggested_value, status
                FROM review_queue
                WHERE review_id IN ({placeholders})
                """,
                review_ids,
            ).fetchall()
            reviews = order_by_ids(review_rows, "review_id", review_ids)[:25]
        else:
            review_clause, review_params = contains_filter(
                ["parse_id", "severity", "issue", "current_value", "suggested_value", "review_reason", "status"],
                term,
            )
            reviews = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT review_id, parse_id, severity, issue, suggested_value, status
                    FROM review_queue
                    WHERE {review_clause}
                    ORDER BY created_at DESC
                    LIMIT 25
                    """,
                    review_params,
                ).fetchall()
            ]

        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            source_rows = conn.execute(
                f"""
                SELECT source_record_id, source_type, source_label, source_uri, note, imported_at
                FROM source_record
                WHERE source_record_id IN ({placeholders})
                """,
                source_ids,
            ).fetchall()
            sources = order_by_ids(source_rows, "source_record_id", source_ids)[:25]
        else:
            source_clause, source_params = contains_filter(
                ["source_type", "source_uri", "source_label", "raw_payload", "note"],
                term,
            )
            sources = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT source_record_id, source_type, source_label, source_uri, note, imported_at
                    FROM source_record
                    WHERE {source_clause}
                    ORDER BY imported_at DESC
                    LIMIT 25
                    """,
                    source_params,
                ).fetchall()
            ]

    return {
        "query": term,
        "mice": mouse_matches,
        "strains": strains,
        "reviews": reviews,
        "sources": sources,
        "hits": hits,
    }


@app.get("/api/evidence-reconciliation")
def evidence_reconciliation() -> dict[str, Any]:
    with connection() as conn:
        photo_summary = conn.execute(
            """
            SELECT COUNT(*) AS total_photos,
                   SUM(CASE WHEN status = 'review_pending' THEN 1 ELSE 0 END) AS review_pending_photos,
                   MAX(uploaded_at) AS latest_photo_uploaded_at
            FROM photo_log
            """
        ).fetchone()
        photo_review_summary = conn.execute(
            """
            SELECT COUNT(*) AS photo_review_candidates,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_photo_reviews
            FROM parse_result parse
            LEFT JOIN review_queue review ON review.parse_id = parse.parse_id
            WHERE parse.source_name = 'photo_manual_review'
            """
        ).fetchone()
        manual_transcription_summary = conn.execute(
            """
            SELECT COUNT(*) AS manual_transcriptions,
                   SUM(CASE WHEN review.status = 'open' THEN 1 ELSE 0 END) AS open_manual_transcription_reviews
            FROM parse_result parse
            LEFT JOIN review_queue review ON review.parse_id = parse.parse_id
            WHERE parse.source_name IN ('manual_photo_transcription', 'ai_photo_extraction')
            """
        ).fetchone()
        legacy_summary = conn.execute(
            """
            SELECT COUNT(DISTINCT legacy.legacy_import_id) AS legacy_imports,
                   COUNT(row.legacy_row_id) AS legacy_rows,
                   MAX(legacy.imported_at) AS latest_legacy_imported_at
            FROM legacy_workbook_import legacy
            LEFT JOIN legacy_workbook_row row ON row.legacy_import_id = legacy.legacy_import_id
            """
        ).fetchone()
        canonical_summary = conn.execute(
            """
            SELECT COUNT(*) AS accepted_mice,
                   SUM(CASE WHEN source_record_id IS NOT NULL OR source_note_item_id IS NOT NULL OR source_photo_id IS NOT NULL THEN 1 ELSE 0 END)
                       AS mice_with_evidence
            FROM mouse_master
            """
        ).fetchone()
        open_reviews = conn.execute(
            "SELECT COUNT(*) AS count FROM review_queue WHERE status = 'open'"
        ).fetchone()["count"]
        review_attention_counts = open_review_attention_counts(conn)
        export_blockers = review_attention_counts.get("must_review", 0)

    total_photos = int(photo_summary["total_photos"] or 0)
    photo_review_candidates = int(photo_review_summary["photo_review_candidates"] or 0)
    open_photo_reviews = int(photo_review_summary["open_photo_reviews"] or 0)
    manual_transcriptions = int(manual_transcription_summary["manual_transcriptions"] or 0)
    open_manual_transcription_reviews = int(manual_transcription_summary["open_manual_transcription_reviews"] or 0)
    legacy_rows = int(legacy_summary["legacy_rows"] or 0)
    accepted_mice = int(canonical_summary["accepted_mice"] or 0)
    mice_with_evidence = int(canonical_summary["mice_with_evidence"] or 0)
    return {
        "boundary": "export or view",
        "source_priority": ["raw source photo", "reviewed note line", "predecessor Excel view"],
        "total_photos": total_photos,
        "photo_review_candidates": photo_review_candidates,
        "photos_missing_review_candidates": max(total_photos - photo_review_candidates, 0),
        "open_photo_reviews": open_photo_reviews,
        "manual_transcriptions": manual_transcriptions,
        "open_manual_transcription_reviews": open_manual_transcription_reviews,
        "legacy_imports": int(legacy_summary["legacy_imports"] or 0),
        "legacy_rows": legacy_rows,
        "accepted_mice": accepted_mice,
        "mice_with_evidence": mice_with_evidence,
        "open_reviews": int(open_reviews or 0),
        "export_review_blockers": export_blockers,
        "open_review_attention_counts": review_attention_counts,
        "latest_photo_uploaded_at": photo_summary["latest_photo_uploaded_at"] or "",
        "latest_legacy_imported_at": legacy_summary["latest_legacy_imported_at"] or "",
        "ready_for_comparison": total_photos > 0 and legacy_rows > 0,
        "comparison_rows": [
            {
                "check": "Latest photo evidence",
                "status": "ready" if total_photos else "missing",
                "detail": f"{total_photos} photo(s), {open_photo_reviews} open manual review(s)",
            },
            {
                "check": "Predecessor Excel view",
                "status": "ready" if legacy_rows else "missing",
                "detail": f"{legacy_rows} candidate row(s) across {int(legacy_summary['legacy_imports'] or 0)} import(s)",
            },
            {
                "check": "Manual photo transcription",
                "status": "ready" if manual_transcriptions else "pending",
                "detail": f"{manual_transcriptions} transcription(s), {open_manual_transcription_reviews} open review(s)",
            },
            {
                "check": "Canonical acceptance",
                "status": "blocked" if export_blockers else "ready",
                "detail": f"{accepted_mice} accepted mouse row(s), {mice_with_evidence} with source evidence, {export_blockers} focus blocker(s)",
            },
        ],
        "next_action": (
            "Review latest photo cards and resolve Focus Review blockers before final acceptance/export."
            if export_blockers
            else "Comparison view is clear of Focus Review export blockers."
        ),
    }


@app.get("/api/evidence-comparison")
def evidence_comparison() -> dict[str, Any]:
    with connection() as conn:
        return build_evidence_comparison_payload(conn)


def comparison_matches_review_scope(comparison: dict[str, Any], scope: EvidenceComparisonReviewCreate | None) -> bool:
    if scope is None:
        return True
    manual_parse_id = scope.manual_parse_id.strip()
    photo_id = scope.photo_id.strip()
    upload_batch_id = scope.upload_batch_id.strip()
    if manual_parse_id and comparison.get("manual_parse_id") != manual_parse_id:
        return False
    if photo_id and comparison.get("photo_id") != photo_id:
        return False
    if upload_batch_id and comparison.get("upload_batch_id") != upload_batch_id:
        return False
    return True


@app.post("/api/evidence-comparison/review-candidates")
def create_evidence_comparison_reviews(payload: EvidenceComparisonReviewCreate | None = None) -> dict[str, Any]:
    created = 0
    existing = 0
    skipped = 0
    review_ids: list[str] = []
    now = utc_now()
    with connection() as conn:
        comparison_payload = build_evidence_comparison_payload(conn)
        scoped_comparisons = [
            comparison
            for comparison in comparison_payload["comparisons"]
            if comparison_matches_review_scope(comparison, payload)
        ]
        for comparison in scoped_comparisons:
            if not comparison.get("review_required"):
                skipped += 1
                continue
            review_id = str(comparison.get("review_id") or "")
            current = conn.execute(
                "SELECT review_id FROM review_queue WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if current is not None:
                existing += 1
                review_ids.append(review_id)
                continue
            manual = comparison.get("manual_summary") or {}
            legacy = comparison.get("legacy_candidate") or {}
            legacy_summary = legacy.get("summary") or {}
            current_value = json.dumps(
                {
                    "manual": manual,
                    "legacy": {
                        "legacy_row_id": legacy.get("legacy_row_id"),
                        "source_file_name": legacy.get("source_file_name"),
                        "source_row_number": legacy.get("source_row_number"),
                        "summary": legacy_summary,
                    },
                    "status": comparison.get("status"),
                },
                ensure_ascii=False,
            )
            conn.execute(
                """
                INSERT INTO review_queue
                    (review_id, parse_id, severity, issue, current_value, suggested_value,
                     review_reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    comparison["manual_parse_id"],
                    "High" if comparison["status"] == "value_mismatch" else "Medium",
                    (
                        "Photo transcription differs from predecessor Excel"
                        if comparison["status"] == "value_mismatch"
                        else "Photo transcription missing predecessor Excel match"
                    ),
                    current_value,
                    "Resolve the latest photo transcription against the predecessor Excel candidate before export.",
                    (
                        f"{comparison.get('detail') or 'Manual photo transcription requires predecessor Excel comparison review.'} "
                        "This is a review item from an export/view comparison; do not write canonical mouse state "
                        "until a reviewer resolves the source photo and predecessor Excel evidence."
                    ),
                    "open",
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO action_log (action_id, action_type, target_id, before_value, after_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("action"),
                    "evidence_comparison_review_created",
                    review_id,
                    None,
                    json.dumps(
                        {
                            "manual_parse_id": comparison["manual_parse_id"],
                            "legacy_row_id": legacy.get("legacy_row_id") or "no_legacy_candidate",
                            "comparison_status": comparison["status"],
                            "boundary": "review item",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            created += 1
            review_ids.append(review_id)
    return {
        "boundary": "review item",
        "scope": {
            "manual_parse_id": payload.manual_parse_id.strip() if payload else "",
            "photo_id": payload.photo_id.strip() if payload else "",
            "upload_batch_id": payload.upload_batch_id.strip() if payload else "",
            "matched_comparisons": len(scoped_comparisons),
        },
        "created_review_candidates": created,
        "existing_review_candidates": existing,
        "skipped_exact_matches": skipped,
        "created_review_items": created,
        "existing_review_items": existing,
        "skipped_comparisons": skipped,
        "review_ids": review_ids,
    }


@app.get("/api/exports/mice.csv")
def export_mice_csv(query: str = "", require_ready: bool = False) -> Response:
    output = io.StringIO()
    fieldnames = [
        "mouse_id",
        "display_id",
        "raw_strain_text",
        "father_id",
        "mother_id",
        "litter_id",
        "sex",
        "dob_raw",
        "dob_start",
        "dob_end",
        "ear_label_raw",
        "ear_label_code",
        "status",
        "current_cage_label",
        "genotyping_status",
        "sample_id",
        "sample_date",
        "genotype_result",
        "genotype_result_date",
        "target_match_status",
        "use_category",
        "next_action",
        "last_verified_at",
        "source_note_item_id",
        "source_record_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    row_count = 0
    blocked_error: dict[str, Any] | None = None
    with connection() as conn:
        for row in mouse_rows(conn, query):
            payload = dict(row)
            writer.writerow({field: payload.get(field, "") for field in fieldnames})
            row_count += 1
        blocked_review_count = export_review_blocker_count(conn)
        suffix = "_filtered" if query.strip() else ""
        filename = f"mouse_records{suffix}.csv"
        export_status = "blocked" if require_ready and blocked_review_count else "generated"
        note = (
            "Blocked final CSV export because Focus Review blockers remain."
            if export_status == "blocked"
            else "Generated from local mouse records CSV endpoint."
        )
        conn.execute(
            """
            INSERT INTO export_log
                (export_id, export_type, filename, query, row_count,
                 blocked_review_count, status, exported_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("export"),
                "mouse_csv",
                filename,
                query.strip(),
                row_count,
                blocked_review_count,
                export_status,
                utc_now(),
                note,
            ),
        )
        if export_status == "blocked":
            blocked_error = {
                "blocked_review_count": blocked_review_count,
                "review_blockers": open_review_blockers(conn),
                "readiness_warnings": genotype_export_blockers(conn),
                "filename": filename,
                "source_layer": "export or view",
            }

    if blocked_error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Resolve Focus Review blockers before final export.",
                **blocked_error,
            },
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/exports/genotyping-worklist.csv")
def export_genotyping_worklist_csv(query: str = "") -> Response:
    output = io.StringIO()
    fieldnames = [
        "display_id",
        "ear_label",
        "strain",
        "dob",
        "current_cage",
        "sample_id",
        "sample_date",
        "genotyping_status",
        "genotype_result",
        "genotype_result_date",
        "target_match_status",
        "use_category",
        "next_action",
        "last_verified_at",
        "source_note_item_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    row_count = 0
    with connection() as conn:
        for row in mouse_rows(conn, query):
            payload = dict(row)
            writer.writerow(
                {
                    "display_id": payload.get("display_id", ""),
                    "ear_label": payload.get("ear_label_raw") or payload.get("ear_label_code") or "",
                    "strain": payload.get("raw_strain_text", ""),
                    "dob": payload.get("dob_raw") or payload.get("dob_start") or "",
                    "current_cage": payload.get("current_cage_label") or "",
                    "sample_id": payload.get("sample_id") or "",
                    "sample_date": payload.get("sample_date") or "",
                    "genotyping_status": payload.get("genotyping_status") or "",
                    "genotype_result": payload.get("genotype_result") or "",
                    "genotype_result_date": payload.get("genotype_result_date") or "",
                    "target_match_status": payload.get("target_match_status") or "",
                    "use_category": payload.get("use_category") or "",
                    "next_action": payload.get("next_action") or "",
                    "last_verified_at": payload.get("last_verified_at") or "",
                    "source_note_item_id": payload.get("source_note_item_id") or "",
                }
            )
            row_count += 1
        blocked_review_count = export_review_blocker_count(conn)
        suffix = "_filtered" if query.strip() else ""
        filename = f"genotyping_worklist{suffix}.csv"
        conn.execute(
            """
            INSERT INTO export_log
                (export_id, export_type, filename, query, row_count,
                 blocked_review_count, status, exported_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("export"),
                "genotyping_worklist_csv",
                filename,
                query.strip(),
                row_count,
                blocked_review_count,
                "generated",
                utc_now(),
                "Generated as a companion genotyping worklist export without changing lab workbook shape.",
            ),
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/exports/{export_type}/validation-report-artifact")
def create_export_validation_report_artifact(export_type: str, query: str = "") -> dict[str, Any]:
    if export_type not in {"separation_xlsx", "animal_sheet_xlsx", "mouse_csv", "genotyping_worklist_csv"}:
        raise HTTPException(status_code=400, detail="Unsupported export type.")
    preview = export_preview()
    if export_type == "animal_sheet_xlsx":
        filename = export_filename("animal", preview, query)
    elif export_type == "separation_xlsx":
        filename = export_filename("separation", preview, query)
    elif export_type == "genotyping_worklist_csv":
        filename = "genotyping_worklist_filtered.csv" if query.strip() else "genotyping_worklist.csv"
    else:
        filename = "mouse_records_filtered.csv" if query.strip() else "mouse_records.csv"
    report = build_export_validation_report(
        preview,
        export_type=export_type,
        query=query,
        filename=filename,
    )
    return persist_validation_report_artifact(report)


def parse_export_log_provenance(note: str) -> dict[str, str]:
    provenance = {
        "export_manifest_path": "",
        "validation_report_id": "",
        "validation_report_path": "",
        "state_watermark": "",
    }
    note_text = str(note or "")
    patterns = {
        "export_manifest_path": r"(?:^|\s|;)manifest=([^;]+)",
        "validation_report_id": r"(?:^|\s|;)validation_report=([^;]+)",
        "state_watermark": r"(?:^|\s|;)state_watermark=([^;]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, note_text)
        if match:
            provenance[key] = match.group(1).strip()
    return provenance


def validation_report_path_from_manifest(manifest_path: str) -> str:
    if not manifest_path:
        return ""
    try:
        artifact_path = resolve_artifact_preview_path(manifest_path)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (HTTPException, OSError, json.JSONDecodeError):
        return ""
    if artifact.get("artifact_type") != "export_manifest":
        return ""
    validation_report_path = str(artifact.get("validation_report_path") or "")
    if not validation_report_path:
        return ""
    try:
        resolve_artifact_preview_path(validation_report_path)
    except HTTPException:
        return ""
    return validation_report_path


def resolve_artifact_preview_path(path: str) -> Path:
    requested = Path(str(path or ""))
    if not str(requested).strip():
        raise HTTPException(status_code=400, detail="Artifact path is required.")
    if requested.is_absolute():
        artifact_path = requested
    else:
        path_text = requested.as_posix()
        if path_text == ARTIFACT_ROOT.name or path_text.startswith(f"{ARTIFACT_ROOT.name}/"):
            artifact_path = ROOT / requested
        else:
            artifact_path = ARTIFACT_ROOT / requested
    artifact_root = ARTIFACT_ROOT.resolve()
    resolved_path = artifact_path.resolve()
    try:
        resolved_path.relative_to(artifact_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Artifact path must stay under mousedb_artifacts.") from exc
    if resolved_path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only JSON artifacts can be previewed.")
    if not resolved_path.exists() or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return resolved_path


@app.get("/api/artifacts/preview")
def get_artifact_preview(path: str) -> dict[str, Any]:
    artifact_path = resolve_artifact_preview_path(path)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    relative_path = artifact_path.relative_to(ARTIFACT_ROOT.resolve()).as_posix()
    return {
        "artifact_path": str(artifact_path),
        "relative_path": relative_path,
        "artifact_type": str(artifact.get("artifact_type") or ""),
        "source_layer": str(artifact.get("source_layer") or ""),
        "artifact": artifact,
        "boundary": "export or view",
    }


@app.get("/api/export-log")
def list_export_log() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT export_id, export_type, filename, query, row_count,
                   blocked_review_count, status, exported_at, source_layer,
                   generated_by, generated_role, note
            FROM export_log
            ORDER BY exported_at DESC, rowid DESC
            LIMIT 25
            """
        ).fetchall()
    export_rows = []
    for row in rows:
        item = dict(row)
        provenance = parse_export_log_provenance(item.get("note", ""))
        provenance["validation_report_path"] = validation_report_path_from_manifest(
            provenance.get("export_manifest_path", "")
        )
        item.update(provenance)
        item["provenance"] = provenance
        export_rows.append(item)
    return export_rows


@app.get("/api/exports/separation.xlsx")
def export_separation_xlsx(query: str = "", require_ready: bool = True) -> Response:
    preview = export_preview()
    filtered_rows = [
        row
        for row in preview["separation_rows"]
        if not query.strip() or query.strip().lower() in row["strain"].lower()
    ]
    rows = [
        [
            row["cage_number"],
            row["strain"],
            row["genotype"],
            row["total"],
            row["dob"],
            row["wt"],
            row["tg"],
            row["sampling_point"],
            row["source_note_item_ids"],
        ]
        for row in filtered_rows
    ]
    filename = export_filename("separation", preview, query)
    blocked_count = preview.get("focus_review_blocker_items", preview["blocked_review_items"])
    if require_ready and blocked_count:
        provenance = create_export_provenance_artifacts(
            preview,
            export_type="separation_xlsx",
            filename=filename,
            query=query,
            status="blocked",
            row_count=len(rows),
            blocked_review_count=blocked_count,
        )
        log_workbook_export(
            "separation_xlsx",
            filename,
            query,
            len(rows),
            blocked_count,
            "blocked",
            manifest_artifact_path=provenance["manifest_artifact_path"],
            validation_report_id=provenance["validation_report_id"],
            state_watermark=provenance["state_watermark"],
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Resolve Focus Review blockers before final separation workbook export.",
                "blocked_review_count": blocked_count,
                "review_blockers": preview["review_blockers"],
                "filename": filename,
                "source_layer": "export or view",
                "export_manifest_path": provenance["manifest_artifact_path"],
                "validation_report_id": provenance["validation_report_id"],
            },
        )
    payload = build_xlsx(
        "분리 현황표",
        preview["separation_columns"],
        rows,
        trace_rows_from_export_rows(filtered_rows, "source_note_item_ids"),
        [14, 22, 22, 12, 18, 10, 10, 22, 32],
    )
    provenance = create_export_provenance_artifacts(
        preview,
        export_type="separation_xlsx",
        filename=filename,
        query=query,
        status="generated",
        row_count=len(rows),
        blocked_review_count=blocked_count,
    )
    log_workbook_export(
        "separation_xlsx",
        filename,
        query,
        len(rows),
        blocked_count,
        "generated",
        manifest_artifact_path=provenance["manifest_artifact_path"],
        validation_report_id=provenance["validation_report_id"],
        state_watermark=provenance["state_watermark"],
    )
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": workbook_content_disposition(filename, "separation.xlsx")},
    )


@app.get("/api/exports/animal-sheet.xlsx")
def export_animal_sheet_xlsx(query: str = "", require_ready: bool = True) -> Response:
    preview = export_preview()
    filtered_rows = [
        row
        for row in preview["animal_sheet_rows"]
        if not query.strip() or not row["strain"] or query.strip().lower() in row["strain"].lower()
    ]
    rows = [
        [
            row["cage_no"],
            row["strain"],
            row["sex"],
            row["mouse_id"],
            row["genotype"],
            row["dob"],
            row["mating_date"],
            row["pubs"],
            row["status"],
            row["source"],
        ]
        for row in filtered_rows
    ]
    filename = export_filename("animal", preview, query)
    blocked_count = preview["blocked_review_items"]
    if require_ready and blocked_count:
        validation_reviews: list[dict[str, Any]] = []
        animal_sheet_validation = preview.get("animal_sheet_litter_validation", {})
        if isinstance(animal_sheet_validation, dict) and int(animal_sheet_validation.get("blocked_count") or 0):
            with connection() as conn:
                parse_id = ensure_export_validation_parse(conn)
                validation_reviews = persist_animal_sheet_litter_review_items(
                    conn,
                    animal_sheet_validation,
                    parse_id=parse_id,
                )
        provenance = create_export_provenance_artifacts(
            preview,
            export_type="animal_sheet_xlsx",
            filename=filename,
            query=query,
            status="blocked",
            row_count=len(rows),
            blocked_review_count=blocked_count,
        )
        log_workbook_export(
            "animal_sheet_xlsx",
            filename,
            query,
            len(rows),
            blocked_count,
            "blocked",
            manifest_artifact_path=provenance["manifest_artifact_path"],
            validation_report_id=provenance["validation_report_id"],
            state_watermark=provenance["state_watermark"],
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Resolve Focus Review blockers before final animal sheet workbook export.",
                "blocked_review_count": blocked_count,
                "review_blockers": preview["review_blockers"],
                "filename": filename,
                "source_layer": "export or view",
                "animal_sheet_litter_validation": preview.get("animal_sheet_litter_validation", {}),
                "animal_sheet_validation_review_items": validation_reviews,
                "export_manifest_path": provenance["manifest_artifact_path"],
                "validation_report_id": provenance["validation_report_id"],
            },
        )
    payload = build_xlsx(
        "animal sheet",
        preview["animal_sheet_columns"],
        rows,
        trace_rows_from_export_rows(filtered_rows, "source"),
        [10, 22, 10, 18, 16, 16, 16, 18, 16, 32],
    )
    provenance = create_export_provenance_artifacts(
        preview,
        export_type="animal_sheet_xlsx",
        filename=filename,
        query=query,
        status="generated",
        row_count=len(rows),
        blocked_review_count=blocked_count,
    )
    log_workbook_export(
        "animal_sheet_xlsx",
        filename,
        query,
        len(rows),
        blocked_count,
        "generated",
        manifest_artifact_path=provenance["manifest_artifact_path"],
        validation_report_id=provenance["validation_report_id"],
        state_watermark=provenance["state_watermark"],
    )
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": workbook_content_disposition(filename, "animal sheet.xlsx")},
    )


def compact_export_values(values: list[Any], limit: int = 3) -> str:
    seen: list[str] = []
    for value in values:
        text_value = str(value or "").strip()
        if text_value and text_value not in seen:
            seen.append(text_value)
    if len(seen) > limit:
        return f"{', '.join(seen[:limit])}, +{len(seen) - limit}"
    return ", ".join(seen)


def export_uncertainty_label(row: Any) -> str:
    status = str(row["ear_label_review_status"] or "").strip()
    if not status or status in {"auto_filled", "verified", "user_corrected"}:
        return ""
    raw_label = row["ear_label_raw"] or row["ear_label_code"] or "ear label"
    confidence = row["ear_label_confidence"]
    confidence_text = f"; confidence {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
    return f"Ear label {raw_label}: {status}{confidence_text}"


def export_rows_have_trace(rows: list[dict[str, Any]], trace_fields: list[str]) -> bool | str:
    if not rows:
        return "not_applicable"
    return all(any(str(row.get(field) or "").strip() for field in trace_fields) for row in rows)


def export_rows_have_row_state(rows: list[dict[str, Any]]) -> bool | str:
    if not rows:
        return "not_applicable"
    return all(str(row.get("row_state") or "").strip() for row in rows)


def export_row_state_policy() -> dict[str, Any]:
    return {
        "source_layer": "export or view",
        "source_state_layer": "canonical structured state",
        "states": ["ready", "blocked_by_review", "blocked_by_litter_conflict", "stale_after_correction"],
        "editable": False,
    }


def export_row_state(blocked_review_items: int, stale_state: dict[str, Any]) -> dict[str, str]:
    if blocked_review_items:
        return {
            "row_state": "blocked_by_review",
            "row_state_reason": "Focus Review blockers remain before Excel export.",
        }
    if stale_state.get("export_stale") and stale_state.get("latest_generated_export_at"):
        return {
            "row_state": "stale_after_correction",
            "row_state_reason": "Accepted state changed after the latest export.",
        }
    return {
        "row_state": "ready",
        "row_state_reason": "Canonical row is ready for Excel export.",
    }


def export_consistency_checks(
    preview_rows: list[dict[str, Any]],
    separation_rows: list[dict[str, Any]],
    animal_sheet_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source_layer": "export or view",
        "source_state_layer": "canonical structured state",
        "preview_row_count": len(preview_rows),
        "separation_row_count": len(separation_rows),
        "animal_sheet_row_count": len(animal_sheet_rows),
        "preview_rows_have_trace": export_rows_have_trace(
            preview_rows,
            ["source_note_item_id", "source_photo_id", "card_snapshot_id", "source_record_id"],
        ),
        "separation_rows_have_trace": export_rows_have_trace(
            separation_rows,
            ["source_note_item_ids", "source_photo_ids", "card_snapshot_ids", "source_record_id"],
        ),
        "animal_sheet_rows_have_trace": export_rows_have_trace(
            animal_sheet_rows,
            ["source_note_item_ids", "source_photo_ids", "card_snapshot_ids", "source_record_id", "source"],
        ),
        "excel_export_is_view": True,
        "preview_rows_have_row_state": export_rows_have_row_state(preview_rows),
        "separation_rows_have_row_state": export_rows_have_row_state(separation_rows),
        "animal_sheet_rows_have_row_state": export_rows_have_row_state(animal_sheet_rows),
    }


def load_export_note_evidence(conn: Any, note_item_ids: list[str]) -> dict[str, dict[str, Any]]:
    note_item_ids = [note_id for note_id in dict.fromkeys(note_item_ids) if note_id]
    if not note_item_ids:
        return {}
    placeholders = ", ".join("?" for _ in note_item_ids)
    rows = conn.execute(
        f"""
        SELECT note.note_item_id, note.photo_id, photo.original_filename,
               note.card_snapshot_id, note.raw_line_text,
               note.parsed_ear_label_review_status, note.confidence
        FROM card_note_item_log note
        LEFT JOIN photo_log photo ON photo.photo_id = note.photo_id
        WHERE note.note_item_id IN ({placeholders})
        """,
        note_item_ids,
    ).fetchall()
    return {row["note_item_id"]: dict(row) for row in rows}


@app.get("/api/export-preview")
def export_preview() -> dict[str, Any]:
    with connection() as conn:
        photos = conn.execute("SELECT COUNT(*) AS count FROM photo_log").fetchone()["count"]
        review_rows = open_review_blockers(conn, exclude_animal_sheet_validation=True)
        open_reviews = conn.execute(
            "SELECT COUNT(*) AS count FROM review_queue WHERE status = 'open'"
        ).fetchone()["count"]
        review_attention_counts = open_review_attention_counts(
            conn,
            exclude_animal_sheet_validation=True,
        )
        blocked_reviews = review_attention_counts.get("must_review", 0)
        genotype_blocker_rows = genotype_export_blockers(conn)
        genotype_blocker_count = len(genotype_blocker_rows)
        parsed = conn.execute("SELECT COUNT(*) AS count FROM parse_result").fetchone()["count"]
        mice = conn.execute(
            f"""
            {MOUSE_SELECT}
            WHERE status IN ('active', 'moved')
            ORDER BY raw_strain_text COLLATE NOCASE, dob_start, display_id COLLATE NOCASE
            LIMIT 50
            """
        ).fetchall()
        mating_rows = conn.execute(
            """
            SELECT m.mating_id, m.mating_label, m.strain_goal, m.expected_genotype,
                   m.start_date, m.status AS mating_status,
                   mm.role, mouse.display_id, mouse.sex, mouse.ear_label_raw,
                   mouse.ear_label_code, mouse.ear_label_confidence,
                   mouse.ear_label_review_status, mouse.genotype, mouse.genotype_result,
                   mouse.dob_raw, mouse.dob_start, mouse.source_note_item_id,
                   mouse.current_card_snapshot_id, mouse.source_photo_id, mouse.source_record_id
            FROM mating_registry m
            LEFT JOIN mating_mouse mm ON mm.mating_id = m.mating_id AND mm.removed_date IS NULL
            LEFT JOIN mouse_master mouse ON mouse.mouse_id = mm.mouse_id
            ORDER BY m.created_at, m.mating_label COLLATE NOCASE,
                     CASE mm.role WHEN 'male' THEN 1 WHEN 'female' THEN 2 ELSE 3 END,
                     mouse.display_id COLLATE NOCASE
            LIMIT 120
            """
        ).fetchall()
        note_item_ids = [
            str(row["source_note_item_id"])
            for row in list(mice) + list(mating_rows)
            if row["source_note_item_id"]
        ]
        note_evidence = load_export_note_evidence(conn, note_item_ids)
        litter_rows = conn.execute(
            """
            SELECT l.litter_id, l.litter_label, l.mating_id, l.birth_date,
                   l.number_born, l.number_alive, l.number_weaned, l.weaning_date,
                   l.status, l.source_record_id
            FROM litter_registry l
            ORDER BY l.birth_date, l.created_at
            LIMIT 120
            """
        ).fetchall()
        stale_state = export_staleness(conn)
        row_state = export_row_state(blocked_reviews, stale_state)
    rows = []
    separation_groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for mouse in mice:
        sex = mouse["sex"] or ""
        sex_symbol = {"male": "\u2642", "female": "\u2640"}.get(sex.lower(), sex)
        genotype = mouse["genotype_result"] or mouse["genotype"] or ""
        dob = mouse["dob_raw"] or mouse["dob_start"] or ""
        cage = mouse["current_cage_label"] or ""
        group_key = (cage, mouse["raw_strain_text"] or "", genotype, sex_symbol, dob)
        if group_key not in separation_groups:
            separation_groups[group_key] = {
                "cage_number": cage,
                "strain": mouse["raw_strain_text"] or "",
                "genotype": genotype,
                "sex": sex_symbol,
                "count": 0,
                "dob": dob,
                "wt": "",
                "tg": "",
                "sampling_point": "",
                "source_note_item_ids": [],
                "source_record_ids": [],
                "source_photo_ids": [],
                "card_snapshot_ids": [],
                "raw_note_lines": [],
                "uncertainties": [],
            }
        group = separation_groups[group_key]
        note_source = note_evidence.get(mouse["source_note_item_id"] or "", {})
        source_photo_id = mouse["source_photo_id"] or note_source.get("photo_id") or ""
        card_snapshot_id = mouse["current_card_snapshot_id"] or note_source.get("card_snapshot_id") or ""
        raw_note_line = note_source.get("raw_line_text") or ""
        uncertainty = export_uncertainty_label(mouse)
        group["count"] += 1
        if "wt" in genotype.lower():
            group["wt"] = str(int(group["wt"] or "0") + 1)
        elif "tg" in genotype.lower():
            group["tg"] = str(int(group["tg"] or "0") + 1)
        elif mouse["genotyping_status"] and mouse["genotyping_status"] != "resulted":
            group["wt"] = mouse["genotyping_status"]
        if mouse["next_action"]:
            group["sampling_point"] = mouse["next_action"]
        if mouse["source_note_item_id"]:
            group["source_note_item_ids"].append(mouse["source_note_item_id"])
        if mouse["source_record_id"]:
            group["source_record_ids"].append(mouse["source_record_id"])
        if source_photo_id:
            group["source_photo_ids"].append(source_photo_id)
        if card_snapshot_id:
            group["card_snapshot_ids"].append(card_snapshot_id)
        if raw_note_line:
            group["raw_note_lines"].append(raw_note_line)
        if uncertainty:
            group["uncertainties"].append(uncertainty)
        rows.append(
            {
                "mouse_id": mouse["mouse_id"],
                "display_id": mouse["display_id"],
                "strain": mouse["raw_strain_text"] or "",
                "genotype": mouse["genotype_result"] or mouse["genotype"] or "",
                "dob": mouse["dob_raw"] or mouse["dob_start"] or "",
                "ear_label": mouse["ear_label_raw"] or mouse["ear_label_code"] or "",
                "ear_label_code": mouse["ear_label_code"] or "",
                "ear_label_confidence": mouse["ear_label_confidence"],
                "ear_label_review_status": mouse["ear_label_review_status"] or "",
                "status": mouse["status"],
                "current_cage": mouse["current_cage_label"] or "",
                "next_action": mouse["next_action"],
                "source_note_item_id": mouse["source_note_item_id"] or "",
                "source_photo_id": source_photo_id,
                "source_photo_filename": note_source.get("original_filename") or "",
                "card_snapshot_id": card_snapshot_id,
                "raw_note_line": raw_note_line,
                "uncertainty": uncertainty,
                **row_state,
                "source_evidence": compact_export_values(
                    [
                        mouse["source_note_item_id"] and f"note {mouse['source_note_item_id']}",
                        source_photo_id and f"photo {source_photo_id}",
                        card_snapshot_id and f"snapshot {card_snapshot_id}",
                    ],
                    limit=4,
                ),
            }
        )
    separation_rows = []
    for group in separation_groups.values():
        source_notes = ", ".join(group["source_note_item_ids"][:3])
        if len(group["source_note_item_ids"]) > 3:
            source_notes = f"{source_notes}, +{len(group['source_note_item_ids']) - 3}"
        separation_rows.append(
            {
                "cage_number": group["cage_number"],
                "strain": group["strain"],
                "genotype": group["genotype"],
                "total": f"{group['sex']} {group['count']}p".strip(),
                "dob": group["dob"],
                "wt": group["wt"],
                "tg": group["tg"],
                "sampling_point": group["sampling_point"],
                "source_note_item_ids": source_notes,
                "source_record_id": compact_export_values(group["source_record_ids"]),
                "source_photo_ids": compact_export_values(group["source_photo_ids"]),
                "card_snapshot_ids": compact_export_values(group["card_snapshot_ids"]),
                "raw_note_lines": compact_export_values(group["raw_note_lines"], limit=2),
                "uncertainty": compact_export_values(group["uncertainties"], limit=2),
                **row_state,
                "export_note": "Grouped from source-backed mouse records; raw note/photo evidence is on this trace sheet.",
            }
        )
    litter_by_mating: dict[str, list[Any]] = {}
    for litter in litter_rows:
        litter_by_mating.setdefault(litter["mating_id"], []).append(litter)
    mating_groups: dict[str, dict[str, Any]] = {}
    for mating in mating_rows:
        mating_id = mating["mating_id"]
        if mating_id not in mating_groups:
            mating_groups[mating_id] = {"mating": mating, "parents": []}
        if mating["display_id"]:
            mating_groups[mating_id]["parents"].append(mating)
    animal_rows = []
    for cage_no, group in enumerate(mating_groups.values(), start=1):
        mating = group["mating"]
        for index, parent in enumerate(group["parents"]):
            sex_value = {"male": "\u2642", "female": "\u2640"}.get(
                (parent["sex"] or "").lower(), parent["sex"] or parent["role"] or ""
            )
            mouse_label = " ".join([parent["display_id"] or "", parent["ear_label_raw"] or ""]).strip()
            note_source = note_evidence.get(parent["source_note_item_id"] or "", {})
            parent_photo_id = parent["source_photo_id"] or note_source.get("photo_id") or ""
            parent_snapshot_id = parent["current_card_snapshot_id"] or note_source.get("card_snapshot_id") or ""
            animal_rows.append(
                {
                    "cage_no": str(cage_no) if index == 0 else "",
                    "strain": mating["strain_goal"] or "",
                    "sex": sex_value,
                    "mouse_id": mouse_label,
                    "genotype": parent["genotype_result"] or parent["genotype"] or mating["expected_genotype"] or "",
                    "dob": parent["dob_raw"] or parent["dob_start"] or "",
                    "mating_date": mating["start_date"] if index == 0 else "",
                    "pubs": "",
                    "status": mating["mating_status"] or "",
                    "source": parent["source_note_item_id"] or parent["source_record_id"] or "",
                    "source_note_item_ids": parent["source_note_item_id"] or "",
                    "source_record_id": parent["source_record_id"] or "",
                    "source_photo_ids": parent_photo_id,
                    "card_snapshot_ids": parent_snapshot_id,
                    "raw_note_lines": note_source.get("raw_line_text") or "",
                    "uncertainty": export_uncertainty_label(parent),
                    **row_state,
                    "export_note": "Parent row generated from accepted mating state; source note/photo evidence is on this trace sheet.",
                }
            )
        for litter_index, litter in enumerate(litter_by_mating.get(mating["mating_id"], []), start=1):
            born_count = litter["number_alive"] if litter["number_alive"] is not None else litter["number_born"]
            animal_rows.append(
                {
                    "cage_no": "",
                    "strain": "",
                    "sex": f"F{litter_index}",
                    "mouse_id": f"{born_count or ''}p".strip(),
                    "genotype": litter["status"] or "",
                    "dob": litter["birth_date"] or "",
                    "mating_date": "",
                    "pubs": f"{litter['birth_date']} {litter['number_born']}p".strip() if litter["number_born"] else "",
                    "status": litter["status"] or "",
                    "source": litter["source_record_id"] or "",
                    "source_record_id": litter["source_record_id"] or "",
                    "source_note_item_ids": "",
                    "source_photo_ids": "",
                    "card_snapshot_ids": "",
                    "raw_note_lines": "",
                    "uncertainty": "",
                    "mating_id": litter["mating_id"] or "",
                    "litter_id": litter["litter_id"] or "",
                    "mating_start_date": mating["start_date"] or "",
                    "litter_birth_date": litter["birth_date"] or "",
                    "number_born": litter["number_born"],
                    "number_alive": litter["number_alive"],
                    "number_weaned": litter["number_weaned"],
                    "weaning_date": litter["weaning_date"] or "",
                    **row_state,
                    "export_note": "Litter row generated from accepted litter state.",
                }
            )
    animal_sheet_litter_validation = validate_animal_sheet_litter_date_counts(animal_rows)
    validation_blockers = int(animal_sheet_litter_validation["blocked_count"])
    if validation_blockers:
        blocked_litter_ids = {
            target_ref
            for check in animal_sheet_litter_validation["checks"]
            if check.get("status") == "blocked"
            for target_ref in check.get("target_refs", [])
        }
        for row in animal_rows:
            if row.get("litter_id") in blocked_litter_ids:
                row["row_state"] = "blocked_by_litter_conflict"
                row["row_state_reason"] = "Animal sheet litter/date/count validation blocked final export."
    total_blocked_reviews = blocked_reviews + validation_blockers
    return {
        "source_layer": "export or view",
        "export_type": "separation_preview",
        "expected_filename": "mouse_records_preview.csv",
        "row_state_policy": export_row_state_policy(),
        **stale_state,
        "expected_separation_filename": export_filename(
            "separation",
            {"separation_rows": separation_rows, "animal_sheet_rows": animal_rows},
        ),
        "expected_animal_sheet_filename": export_filename(
            "animal",
            {"separation_rows": separation_rows, "animal_sheet_rows": animal_rows},
        ),
        "separation_columns": ["Cage number", "Strain", "Genotype", "total", "DOB", "WT", "Tg", "Sampling point", "Source note"],
        "animal_sheet_columns": ["Cage No.", "Strain", "Sex", "I.D", "genotype", "DOB", "Mating date", "Pubs", "Status", "Source"],
        "photos": photos,
        "parsed_results": parsed,
        "blocked_review_items": total_blocked_reviews,
        "focus_review_blocker_items": blocked_reviews,
        "animal_sheet_validation_blocker_items": validation_blockers,
        "open_review_items": open_reviews,
        "open_review_attention_counts": review_attention_counts,
        "genotype_blocker_items": genotype_blocker_count,
        "experiment_ready": genotype_blocker_count == 0 and bool(rows),
        "ready": total_blocked_reviews == 0 and bool(rows),
        "preview_rows": rows,
        "separation_rows": separation_rows,
        "animal_sheet_rows": animal_rows,
        "animal_sheet_litter_validation": animal_sheet_litter_validation,
        "preview_row_count": len(rows),
        "separation_row_count": len(separation_rows),
        "animal_sheet_row_count": len(animal_rows),
        "review_blockers": [dict(row) for row in review_rows],
        "readiness_warnings": genotype_blocker_rows,
        "export_consistency": export_consistency_checks(rows, separation_rows, animal_rows),
        "generated_at": utc_now(),
    }


@app.post("/api/fixtures/import-sample")
def import_sample_fixture() -> dict[str, Any]:
    if not FIXTURE_PATH.exists():
        raise HTTPException(status_code=404, detail="Sample fixture not found.")
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Fixture records must be a list.")

    imported = 0
    reviews = 0
    note_items = 0
    mouse_candidates = 0
    with connection() as conn:
        scope = assigned_scope_map(conn)
        for record in records:
            parse_id = record.get("id") or new_id("parse")
            status = str(record.get("status") or "review")
            matched_scope = assigned_scope_match(scope, record)
            match_info = assigned_scope_suggestion(conn, record)
            if status == "auto" and not matched_scope and match_info.get("decision") == "auto_filled":
                matched_scope = str(match_info.get("canonical") or "")
                record = {**record, "matchedStrain": matched_scope, "strainMatch": match_info}
            elif status == "auto" and not matched_scope:
                status = "review"
                suggestion = str(match_info.get("canonical") or "")
                record = {
                    **record,
                    "status": status,
                    "issue": (
                        "Assigned strain fuzzy match needs review"
                        if suggestion
                        else "Outside assigned strain scope"
                    ),
                    "severity": "Medium",
                    "currentValue": record.get("matchedStrain") or record.get("rawStrain") or "",
                    "suggestedValue": (
                        f"Review suggested assigned strain: {suggestion}"
                        if suggestion
                        else "Confirm assigned strain or add to My Assigned Strains"
                    ),
                    "reviewReason": strain_match_review_reason(
                        match_info,
                        "Parsed strain is not an exact match in My Assigned Strains. Confirm scope before accepting this cage card.",
                    ),
                    "strainMatch": match_info,
                }
            elif matched_scope:
                record = {**record, "matchedStrain": matched_scope, "strainMatch": match_info}
            validation_review = validation_review_for_record(conn, record, status)
            if validation_review:
                status = "review"
                record = {**record, "status": status, **validation_review}
            record = tagged_parse_payload(
                record,
                payload_kind="fixture_parse_import",
                source_layer="parsed or intermediate result",
            )
            confidence = float(record.get("confidence") or 0)
            needs_review = 1 if status in {"review", "conflict"} else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO parse_result
                    (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parse_id,
                    None,
                    "fixtures/sample_parse_results.json",
                    json.dumps(record, ensure_ascii=False),
                    utc_now(),
                    status,
                    confidence,
                    needs_review,
                ),
            )
            imported += 1
            written_notes, written_mice, written_ear_reviews = write_note_items_and_mouse_candidates(conn, parse_id, record, status)
            note_items += written_notes
            mouse_candidates += written_mice
            reviews += written_ear_reviews
            if needs_review:
                review_id = f"review_{parse_id}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO review_queue
                        (review_id, parse_id, severity, issue, current_value, suggested_value,
                         review_reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        parse_id,
                        str(record.get("severity") or "Medium"),
                        str(record.get("issue") or "Needs review"),
                        str(record.get("currentValue") or ""),
                        str(record.get("suggestedValue") or ""),
                        str(record.get("reviewReason") or ""),
                        "open",
                        utc_now(),
                    ),
                )
                reviews += 1
            else:
                conn.execute(
                    "DELETE FROM review_queue WHERE review_id = ?",
                    (f"review_{parse_id}",),
                )

    return {
        "imported_parse_results": imported,
        "created_or_updated_review_items": reviews,
        "created_or_updated_note_items": note_items,
        "created_or_updated_mouse_candidates": mouse_candidates,
    }
