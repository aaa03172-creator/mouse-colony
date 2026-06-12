# Data Guardian Auto-Recheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a local-first Data Guardian auto-recheck workflow that turns generated MouseDB review/export artifacts into evidence-backed review items without automatically changing canonical colony state.

**Architecture:** Add a bounded `review_sentinel` lane that creates non-canonical run records, evidence bundles, risk classifications, review items, and proposed changesets. The lane reads review packages, OCR/parser outputs, confirmed values, and export workbooks, then writes only review/report artifacts until a human resolves an item through the existing review flow.

**Tech Stack:** FastAPI, SQLite, pytest, static HTML/CSS/JavaScript, existing browser verification scripts, optional local-only OCR/parser adapters.

---

## Active Goal

Create this goal before implementation:

```text
Implement a local-first Data Guardian auto-recheck workflow that watches generated mouse colony review/export artifacts, runs safe local rechecks automatically, writes evidence-backed review items, and never changes canonical colony state without explicit human resolution.
```

Completion requires:

- `auto_recheck_runs`, evidence bundles, review items, and proposed changesets remain non-canonical.
- Local rechecks can run automatically.
- External OCR/LLM calls are blocked unless explicitly approved.
- User-confirmed values are never overwritten by auto-recheck.
- Task-level TDD, doublecheck, and Codex read-only review gates are satisfied.

## Files

- Create: `app/review_sentinel.py`
- Create: `tests/test_review_sentinel.py`
- Modify: `app/db.py`
- Modify: `app/main.py`
- Modify: `static/index.html`
- Test: `tests/test_review_sentinel.py`
- Test: `tests/test_review_package_import.py`
- Test: `tests/test_focus_review_workbench_ui.py`
- Optional browser test: `scripts/verify-review-field-outcome-e2e.js`

## Global Rules

- Use TDD for every production change.
- Do not write production code before watching the targeted test fail.
- After each task, run the task-specific pytest command, inspect `git diff`, and run `git status --short --untracked-files=all`.
- Codex review is read-only. Treat its output as findings, not automatic edits.
- Do not use `git add .` while unrelated dirty files exist.

## Codex Review Checkpoints

Use the app API when available:

```http
POST /api/codex-cli/read-only-audit
{
  "task": "data_guardian_auto_recheck_audit",
  "context": "Review the Data Guardian auto-recheck implementation. Check TDD coverage, canonical write risks, review item boundaries, external-service payload safety, partial write risks, and generated artifact cleanup.",
  "approved_codex_cli_run": true
}
```

Run Codex review after Task 2, Task 4, Task 6, Task 8, and before completion.

## Task 1: Risk Classifier Contract

**Files:**
- Create: `app/review_sentinel.py`
- Create: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write the failing test**

Add this test:

```python
from app.review_sentinel import classify_recheck_risk


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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py::test_missing_source_photo_cannot_auto_pass -q
```

Expected: FAIL because `app.review_sentinel` or `classify_recheck_risk` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `app/review_sentinel.py` with:

```python
def classify_recheck_risk(evidence):
    reasons = []
    if not evidence.get("source_photo_id"):
        reasons.append("missing_source_photo")
        return {
            "risk_status": "photo_check_required",
            "risk_reasons": reasons,
            "canonical": False,
        }
    return {"risk_status": "auto_passed", "risk_reasons": reasons, "canonical": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py::test_missing_source_photo_cannot_auto_pass -q
```

Expected: PASS.

- [ ] **Step 5: Doublecheck**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
git diff -- app/review_sentinel.py tests/test_review_sentinel.py
git status --short --untracked-files=all
```

Confirm no canonical writer was added.

## Task 2: Conflict And External-Service Risk

**Files:**
- Modify: `app/review_sentinel.py`
- Modify: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing tests**

Add:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py::test_canonical_conflict_routes_to_conflict_review tests/test_review_sentinel.py::test_external_service_requirement_blocks_recheck_by_default -q
```

Expected: FAIL because classifier only handles missing source photo.

- [ ] **Step 3: Implement minimal classification**

Update `classify_recheck_risk`:

```python
def classify_recheck_risk(evidence):
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
    candidate = evidence.get("candidate_value")
    canonical = evidence.get("current_canonical_value")
    if candidate is not None and canonical is not None and candidate != canonical:
        reasons.append("canonical_conflict")
        return {"risk_status": "conflict_review", "risk_reasons": reasons, "canonical": False}
    return {"risk_status": "auto_passed", "risk_reasons": reasons, "canonical": False}
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

Expected: PASS.

- [ ] **Step 5: Codex read-only review**

Ask Codex to review the classifier for canonical write risks and hard-coded domain assumptions. Triage findings before Task 3.

## Task 3: Auto-Recheck Run And Review Item Models

**Files:**
- Modify: `app/db.py`
- Modify: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing DB test**

Add:

```python
from app import db
from app.db import connection, init_db
from app.review_sentinel import create_auto_recheck_run, create_data_guardian_review_item


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
```

- [ ] **Step 2: Run test**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

Expected: FAIL because tables or helper functions do not exist.

- [ ] **Step 3: Implement schema and helper functions**

Add these tables inside `init_db()`:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS auto_recheck_runs (
        run_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        input_manifest_json TEXT NOT NULL DEFAULT '{}',
        allow_external_services INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'created',
        summary_json TEXT NOT NULL DEFAULT '{}',
        canonical INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS evidence_bundles (
        evidence_bundle_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        field_key TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        source_layer TEXT NOT NULL,
        canonical INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS data_guardian_review_items (
        review_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        field_key TEXT NOT NULL,
        candidate_value_raw TEXT NOT NULL DEFAULT '',
        candidate_value_normalized TEXT NOT NULL DEFAULT '',
        current_canonical_value TEXT NOT NULL DEFAULT '',
        previous_confirmed_value TEXT NOT NULL DEFAULT '',
        risk_status TEXT NOT NULL,
        risk_reasons_json TEXT NOT NULL DEFAULT '[]',
        recommended_action TEXT NOT NULL DEFAULT '',
        evidence_bundle_id TEXT NOT NULL DEFAULT '',
        resolution_status TEXT NOT NULL DEFAULT 'open',
        canonical INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
)
```

Add helper functions in `app/review_sentinel.py` using `new_id("auto_recheck")` and `new_id("dg_review")`. The helpers must insert `canonical = 0` explicitly.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

Expected: PASS.

- [ ] **Step 5: Doublecheck**

Run:

```powershell
git diff -- app/db.py tests/test_review_sentinel.py
git status --short --untracked-files=all
```

Confirm canonical colony tables are not updated by the new helpers.

## Task 4: Review Package Adapter

**Files:**
- Modify: `app/review_sentinel.py`
- Test: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing adapter test**

Add:

```python
from app.review_sentinel import build_evidence_bundle_from_review_package_item


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
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 3: Implement adapter**

Add:

```python
def build_evidence_bundle_from_review_package_item(item, run_id):
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
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 5: Codex read-only review**

Review source traceability and payload minimization before adding API endpoints.

## Task 5: Auto-Recheck API

**Files:**
- Modify: `app/main.py`
- Modify: `app/review_sentinel.py`
- Test: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing API test**

Add:

```python
from fastapi.testclient import TestClient
from app.main import app


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
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 3: Add the minimal local-only endpoint**

Add a Pydantic request model in `app/main.py` with `source_type`, `source_id`, `approved_local_recheck`, `allow_external_services`, and `items`. The endpoint must return HTTP 400 when `allow_external_services` is true and no separate external-service approval flow exists.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py tests/test_review_package_import.py -q
```

- [ ] **Step 5: Codex read-only review**

Review route behavior, partial write handling, and no-canonical-write guarantees.

## Task 6: Review Queue API

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing queue test**

Add:

```python
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
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 3: Implement queue endpoint**

Query `data_guardian_review_items` where `risk_status != 'auto_passed'` unless `include_auto_passed=true` is passed. Return `source_layer: "review item"` and `canonical: false`.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 5: Doublecheck**

Confirm review queue payload is a review item layer, not canonical state.

## Task 7: UI Summary

**Files:**
- Modify: `static/index.html`
- Test: `tests/test_focus_review_workbench_ui.py`
- Optional: `scripts/verify-review-field-outcome-e2e.js`

- [ ] **Step 1: Write failing UI contract test**

Add:

```python
from pathlib import Path


def test_data_guardian_ui_warns_canonical_is_unchanged():
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert "데이터 지킴이" in html
    assert "canonical 값은 아직 변경되지 않았습니다" in html
    assert "/api/data-guardian/review-queue" in html
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
python -m pytest tests/test_focus_review_workbench_ui.py -q
```

- [ ] **Step 3: Implement UI**

Add a compact Data Guardian section to `static/index.html`. It may fetch `/api/data-guardian/review-queue`, display counts and risk reasons, and fill existing review resolution form values. It must not call canonical writer endpoints directly.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_focus_review_workbench_ui.py -q
```

- [ ] **Step 5: Browser doublecheck**

If this changes browser behavior, run the focused Node/browser verification script.

## Task 8: Proposed Changeset

**Files:**
- Modify: `app/db.py`
- Modify: `app/review_sentinel.py`
- Test: `tests/test_review_sentinel.py`

- [ ] **Step 1: Write failing test**

Add:

```python
from app.review_sentinel import create_proposed_changeset


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
```

- [ ] **Step 2: Run test**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 3: Implement proposed changeset storage**

Add `proposed_changesets` to `init_db()` with `canonical INTEGER NOT NULL DEFAULT 0` and `approval_status TEXT NOT NULL DEFAULT 'pending'`. Add `create_proposed_changeset()` in `app/review_sentinel.py`; it must not update `mouse_master`, `review_queue`, or any canonical table.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_review_sentinel.py -q
```

- [ ] **Step 5: Codex read-only review**

Review no-auto-write and approval boundary.

## Task 9: Final Verification

**Files:**
- All task files

- [ ] **Step 1: Run focused backend tests**

```powershell
python -m pytest tests/test_review_sentinel.py tests/test_review_package_import.py -q
```

- [ ] **Step 2: Run focused UI tests**

```powershell
python -m pytest tests/test_focus_review_workbench_ui.py -q
```

- [ ] **Step 3: Inspect diff**

```powershell
git diff -- app/review_sentinel.py app/db.py app/main.py static/index.html tests/test_review_sentinel.py tests/test_review_package_import.py tests/test_focus_review_workbench_ui.py
```

- [ ] **Step 4: Inspect worktree**

```powershell
git status --short --untracked-files=all
```

- [ ] **Step 5: Final Codex read-only review**

Run the final review and resolve all `fix_now` findings.

- [ ] **Step 6: Commit only task files**

```powershell
git add app/review_sentinel.py app/db.py app/main.py static/index.html tests/test_review_sentinel.py tests/test_review_package_import.py tests/test_focus_review_workbench_ui.py docs/data_guardian_auto_recheck_design_ko.md docs/superpowers/plans/2026-06-09-data-guardian-auto-recheck.md
git commit -m "feat: add data guardian auto recheck workflow"
```
