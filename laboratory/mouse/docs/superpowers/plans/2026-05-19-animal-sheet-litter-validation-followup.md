# Animal Sheet Litter Validation Follow-Up Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend animal sheet litter validation to catch pup count text mismatches and surface uncertain date/source evidence as review warnings without blocking safe exports.

**Architecture:** Keep the existing pure validator as the integration point. Add three additional validator-specific checks that still map to schema-compatible validation report keys: `pubs_count_conflicts_with_number_born -> count_mismatch`, `ambiguous_litter_date_normalization -> impossible_date`, and `litter_source_record_without_photo_or_note -> missing_source_trace`.

**Tech Stack:** FastAPI, SQLite, pytest, existing `app/main.py` export validation helpers, existing `tests/test_artifact_workflow.py`.

---

## Task 1: Pubs Text Count Mismatch

**Files:**
- Modify: `tests/test_artifact_workflow.py`
- Modify: `app/main.py`

- [ ] Add a failing test where an animal sheet litter row has `pubs = "2026-04-22 12p"` but `number_born = 10`; expect a blocked `count_mismatch` validator check.
- [ ] Run only that test and confirm RED.
- [ ] Add a small parser for `p` counts in export text and a blocked validator check when `pubs` count conflicts with accepted `number_born`.
- [ ] Re-run the test and confirm GREEN.

## Task 2: Review Warnings For Ambiguous Date And Source-Only Litter Rows

**Files:**
- Modify: `tests/test_artifact_workflow.py`
- Modify: `app/main.py`

- [ ] Add a failing test where `litter_birth_date_review_status = "needs_review"` and a normalized `litter_birth_date` is present; expect warning `ambiguous_litter_date_normalization` mapped to `impossible_date`.
- [ ] Add a failing test where a litter row has `source_record_id` but no `source_photo_ids` or `source_note_item_ids`; expect warning `litter_source_record_without_photo_or_note` mapped to `missing_source_trace`.
- [ ] Run both tests and confirm RED.
- [ ] Implement warning checks without changing canonical state or blocking export.
- [ ] Re-run both tests and confirm GREEN.

## Task 3: Report Mapping And Verification

**Files:**
- Modify: `tests/test_artifact_workflow.py`
- Verify: `app/main.py`

- [ ] Add or update a report test showing warnings produce `validation_report.status = "warning"` without Focus Review blocker status.
- [ ] Run `python -m pytest tests/test_artifact_workflow.py -q`.
- [ ] Run `python -m pytest tests -q`, `npm test`, and `npm run test:local`.
- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Commit with message `Extend animal sheet litter validation warnings`.

## Self-Review Notes

- This follow-up deliberately keeps warnings non-blocking.
- It does not add new validation report enum values.
- It does not infer new biological rules or hard-code strain-specific thresholds.
