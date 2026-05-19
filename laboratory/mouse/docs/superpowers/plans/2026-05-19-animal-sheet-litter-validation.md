# Animal Sheet Litter Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block or warn on animal sheet exports when litter dates, pup counts, or separation/weaning counts conflict with accepted mating/litter state.

**Architecture:** Keep Excel as an `export or view` artifact. Add hidden validation metadata to animal sheet preview rows, run a pure validator that emits schema-compatible checks, surface row-level validation state in preview/report/export 409 payloads, and persist deduplicated review items only when a final animal sheet export is blocked.

**Tech Stack:** FastAPI, SQLite, pytest, existing `app/main.py` export helpers, existing `review_queue`, `export_log`, validation report, and export manifest artifacts.

---

## File Structure

- Modify `app/main.py`
  - Add pure helper functions near existing export validation helpers.
  - Add hidden metadata to `animal_sheet_rows` inside `export_preview`.
  - Add validation checks to `build_export_validation_report`.
  - Add blocked-export review item persistence to `export_animal_sheet_xlsx`.
- Modify `tests/test_artifact_workflow.py`
  - Add compact DB fixture tests for date/count conflicts, schema-compatible check keys, blocked XLSX export, and deduped review items.
- No schema change initially
  - Keep `validation_report.checks[].check_key` limited to existing enum values: `impossible_date`, `count_mismatch`, `missing_source_trace`, `open_focus_review_blocker`.

## Task 1: Failing Tests For Pure Validation Checks

**Files:**
- Modify: `tests/test_artifact_workflow.py`

- [ ] **Step 1: Add tests that describe the validator output**

Add tests near existing export validation report tests:

```python
def test_animal_sheet_litter_validator_blocks_birth_before_mating() -> None:
    rows = [
        {
            "sex": "F1",
            "mouse_id": "10p",
            "litter_id": "litter_bad_date",
            "mating_id": "mating_bad_date",
            "mating_start_date": "2026-05-01",
            "litter_birth_date": "2026-04-13",
            "source_record_id": "source_litter_bad_date",
            "row_state": "ready",
        }
    ]

    result = app_main.validate_animal_sheet_litter_date_counts(rows)

    assert result["status"] == "blocked"
    assert result["blocked_count"] == 1
    check = result["checks"][0]
    assert check["validator_check_key"] == "litter_date_before_mating"
    assert check["validation_report_check_key"] == "impossible_date"
    assert check["status"] == "blocked"
    assert check["severity"] == "high"
    assert check["target_refs"] == ["litter_bad_date"]
```

```python
def test_animal_sheet_litter_validator_blocks_weaned_count_above_born() -> None:
    rows = [
        {
            "sex": "F1",
            "mouse_id": "12p",
            "litter_id": "litter_bad_count",
            "mating_id": "mating_bad_count",
            "mating_start_date": "2026-04-01",
            "litter_birth_date": "2026-04-22",
            "number_born": 10,
            "number_weaned": 12,
            "weaning_date": "2026-05-15",
            "source_record_id": "source_litter_bad_count",
            "row_state": "ready",
        }
    ]

    result = app_main.validate_animal_sheet_litter_date_counts(rows)

    assert result["status"] == "blocked"
    assert result["blocked_count"] == 1
    check = result["checks"][0]
    assert check["validator_check_key"] == "weaned_count_exceeds_born"
    assert check["validation_report_check_key"] == "count_mismatch"
    assert check["status"] == "blocked"
    assert check["target_refs"] == ["litter_bad_count"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_artifact_workflow.py::test_animal_sheet_litter_validator_blocks_birth_before_mating tests/test_artifact_workflow.py::test_animal_sheet_litter_validator_blocks_weaned_count_above_born -q
```

Expected: FAIL because `validate_animal_sheet_litter_date_counts` is not defined.

- [ ] **Step 3: Implement the minimal pure validator**

Add to `app/main.py` near `build_export_validation_report`:

```python
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
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Midpoint check**

Run:

```powershell
git diff --check
git status --short
```

Expected: no diff check errors; only task files changed.

## Task 2: Wire Validation Into Preview And Report

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_artifact_workflow.py`

- [ ] **Step 1: Add failing tests for report integration**

Add a test that passes `animal_sheet_litter_validation` through `build_export_validation_report`:

```python
def test_export_validation_report_maps_litter_checks_to_schema_keys() -> None:
    preview = {
        "blocked_review_items": 1,
        "latest_data_change_at": "2026-05-19T00:00:00Z",
        "review_blockers": [],
        "animal_sheet_rows": [
            {
                "mouse_id": "10p",
                "litter_id": "litter_bad_date",
                "source_record_id": "source_litter_bad_date",
            }
        ],
        "animal_sheet_litter_validation": {
            "source_layer": "export or view",
            "status": "blocked",
            "blocked_count": 1,
            "warning_count": 0,
            "checks": [
                {
                    "validator_check_key": "litter_date_before_mating",
                    "validation_report_check_key": "impossible_date",
                    "status": "blocked",
                    "severity": "high",
                    "message": "Animal sheet litter birth date is earlier than the mating start date.",
                    "target_refs": ["litter_bad_date"],
                    "evidence_refs": ["source_litter_bad_date"],
                    "recommended_action": "Confirm the litter date before export.",
                }
            ],
        },
        "preview_rows": [],
        "separation_rows": [],
    }

    report = app_main.build_export_validation_report(
        preview,
        export_type="animal_sheet_xlsx",
        filename="animal.xlsx",
    )

    check = next(item for item in report["checks"] if item["check_key"] == "impossible_date")
    assert check["status"] == "blocked"
    assert check["target_refs"] == ["litter_bad_date"]
    assert "litter_date_before_mating" in check["message"]
    assert report["status"] == "blocked"
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
python -m pytest tests/test_artifact_workflow.py::test_export_validation_report_maps_litter_checks_to_schema_keys -q
```

Expected: FAIL because the report ignores `animal_sheet_litter_validation`.

- [ ] **Step 3: Implement report mapping and preview metadata**

In `build_export_validation_report`, append mapped validator checks after `missing_source_trace`:

```python
    if export_type == "animal_sheet_xlsx":
        litter_validation = preview.get("animal_sheet_litter_validation")
        if isinstance(litter_validation, dict):
            for check in litter_validation.get("checks", []):
                if not isinstance(check, dict):
                    continue
                report_key = str(check.get("validation_report_check_key") or "")
                if report_key not in {"impossible_date", "count_mismatch", "missing_source_trace", "open_focus_review_blocker"}:
                    report_key = "open_focus_review_blocker"
                checks.append(
                    {
                        "check_key": report_key,
                        "status": str(check.get("status") or "warning"),
                        "severity": str(check.get("severity") or "medium"),
                        "message": f"{check.get('validator_check_key')}: {check.get('message')}",
                        "target_refs": unique_nonempty(check.get("target_refs", [])),
                        "evidence_refs": unique_nonempty(check.get("evidence_refs", [])),
                        "recommended_action": str(check.get("recommended_action") or "Review animal sheet litter/date/count conflict before export."),
                    }
                )
```

In `export_preview`, add hidden validation fields to litter rows:

```python
"mating_id": litter["mating_id"] or "",
"litter_id": litter["litter_id"] or "",
"mating_start_date": mating["start_date"] or "",
"litter_birth_date": litter["birth_date"] or "",
"number_born": litter["number_born"],
"number_alive": litter["number_alive"],
"number_weaned": litter["number_weaned"],
"weaning_date": litter["weaning_date"] or "",
```

After building `animal_rows`, run:

```python
    animal_sheet_litter_validation = validate_animal_sheet_litter_date_counts(animal_rows)
    validation_blockers = int(animal_sheet_litter_validation["blocked_count"])
    if validation_blockers:
        row_state = {
            "row_state": "blocked_by_litter_conflict",
            "row_state_reason": "Animal sheet litter/date/count validation blocked final export.",
        }
```

Then include:

```python
"animal_sheet_litter_validation": animal_sheet_litter_validation,
"animal_sheet_validation_blocker_items": validation_blockers,
"blocked_review_items": blocked_reviews + validation_blockers,
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_artifact_workflow.py::test_export_validation_report_maps_litter_checks_to_schema_keys tests/test_artifact_workflow.py::test_animal_sheet_litter_validator_blocks_birth_before_mating tests/test_artifact_workflow.py::test_animal_sheet_litter_validator_blocks_weaned_count_above_born -q
```

Expected: PASS.

## Task 3: Block Animal Sheet XLSX And Persist Review Items

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_artifact_workflow.py`

- [ ] **Step 1: Add failing blocked export test**

Add a compact DB-backed test:

```python
def test_animal_sheet_export_blocks_litter_date_conflict_and_logs_review(tmp_path: Path, monkeypatch) -> None:
    old_db_path = db.DB_PATH
    db.DB_PATH = tmp_path / "mouse_lims.sqlite"
    monkeypatch.setattr(app_main, "ARTIFACT_ROOT", tmp_path / "mousedb_artifacts")
    try:
        db.init_db()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO mating_registry
                    (mating_id, mating_label, strain_goal, start_date, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("mating_conflict", "Mating Conflict", "ApoM Tg/Tg", "2026-05-01", "active", "2026-05-19T00:00:00Z", "2026-05-19T00:00:00Z"),
            )
            conn.execute(
                """
                INSERT INTO litter_registry
                    (litter_id, litter_label, mating_id, birth_date, number_born,
                     number_alive, number_weaned, status, source_record_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("litter_conflict", "F1", "mating_conflict", "2026-04-13", 10, 10, None, "born", "source_litter_conflict", "2026-05-19T00:00:01Z", "2026-05-19T00:00:01Z"),
            )

        response = TestClient(app_main.app).get("/api/exports/animal-sheet.xlsx")

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["source_layer"] == "export or view"
        assert detail["animal_sheet_litter_validation"]["blocked_count"] == 1
        with db.connection() as conn:
            review_count = conn.execute(
                "SELECT COUNT(*) AS count FROM review_queue WHERE issue = ?",
                ("Animal sheet litter/date conflict",),
            ).fetchone()["count"]
            assert review_count == 1
            export_log = conn.execute(
                "SELECT status, export_type FROM export_log ORDER BY exported_at DESC LIMIT 1"
            ).fetchone()
            assert dict(export_log) == {"status": "blocked", "export_type": "animal_sheet_xlsx"}
    finally:
        db.DB_PATH = old_db_path
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
python -m pytest tests/test_artifact_workflow.py::test_animal_sheet_export_blocks_litter_date_conflict_and_logs_review -q
```

Expected: FAIL because no review item is persisted and the 409 payload lacks the validation block.

- [ ] **Step 3: Implement deduped review item persistence**

Add:

```python
def persist_animal_sheet_litter_review_items(conn: Any, validation: dict[str, Any], parse_id: str = "export_validation") -> list[dict[str, Any]]:
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
            SELECT review_id FROM review_queue
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
```

In `export_animal_sheet_xlsx`, before creating blocked provenance, persist the validation reviews if `animal_sheet_litter_validation.blocked_count > 0`. The current app has no parse row named `export_validation`, so create a parse_result placeholder if needed:

```python
def ensure_export_validation_parse(conn: Any) -> str:
    parse_id = "parse_export_validation"
    existing = conn.execute("SELECT parse_id FROM parse_result WHERE parse_id = ?", (parse_id,)).fetchone()
    if not existing:
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
```

Add `animal_sheet_litter_validation` to the HTTP 409 detail.

- [ ] **Step 4: Run blocked export test**

Run the blocked export test. Expected: PASS.

- [ ] **Step 5: Add and run dedupe test**

Call `/api/exports/animal-sheet.xlsx` twice against the same DB fixture and assert the open review count stays `1`.

Expected: PASS.

## Task 4: Full Focused Verification And Commit

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused pytest**

Run:

```powershell
python -m pytest tests/test_artifact_workflow.py -q
```

Expected: all tests in that file pass.

- [ ] **Step 2: Run export-related JS smoke if feasible**

Run:

```powershell
npm run test:local
```

Expected: pass. If runtime dependencies or time prevent completion, record the exact failure.

- [ ] **Step 3: Inspect diff**

Run:

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: no whitespace errors; only planned files changed.

- [ ] **Step 4: Commit**

Stage only the plan, code, and tests:

```powershell
git add -- docs/superpowers/plans/2026-05-19-animal-sheet-litter-validation.md app/main.py tests/test_artifact_workflow.py
git commit -m "Block animal sheet litter export conflicts"
```

## Self-Review Notes

- Spec coverage: impossible date order, impossible count relationships, schema-compatible check keys, blocked final export, and review routing are covered.
- Deferred intentionally: new validation report enum values, UI polish beyond existing 409 payload, and advanced death/loss count reconciliation.
- Boundary check: validator result remains `export or view`; persisted review rows remain `review item`; no canonical tables are modified by validation.
