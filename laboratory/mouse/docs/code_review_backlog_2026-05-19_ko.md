# Code Review Backlog

Layer classification: review item / non-canonical implementation review.
Canonical status: non-canonical. This document records review findings and branch-review candidates for later batching. If it conflicts with `AGENTS.md`, `final_mouse_colony_prd.md`, committed tests, or implementation, call out the mismatch before changing behavior.

Date: 2026-05-19
Reviewed branch: `codex/animal-sheet-litter-validation`
Review scope: source-level review of the current branch, focused on export readiness, review routing, traceability, and project guardrails.

## Current Review Outcome

The animal-sheet litter validation direction is aligned with the project principles: litter/date/count conflicts are routed to review instead of silently producing a risky final workbook, and raw/source references remain visible through validation artifacts.

The main follow-up is not a data-model rewrite. It is a wording and count-contract cleanup so UI labels, preview fields, 409 payloads, and review lists all describe the same blocker category.

## Findings To Batch

### P2: Export blocker counters mix different concepts

Files:

- `app/main.py`
- `static/index.html`

Observed issue:

`/api/export-preview` sets `blocked_review_items` to `focus_review_blocker_items + animal_sheet_validation_blocker_items`, while `review_blockers` and `open_review_attention_counts` intentionally exclude animal-sheet validation reviews. The UI then labels `blocked_review_items` as "Focus blockers" in at least one metric.

Risk:

The operator can see a blocker count without a matching Focus Review blocker row. This weakens the review workflow because the UI appears internally inconsistent even though the underlying validation is doing useful work.

Suggested resolution:

- Keep separate fields for:
  - Focus Review blockers;
  - animal sheet validation blockers;
  - all final-export blockers.
- Rename or add a response field such as `final_export_blocker_items`.
- Update UI labels so "Focus blockers" uses `focus_review_blocker_items`, not the combined count.
- Keep `blocked_review_items` only if backward compatibility needs it, but document that it is a combined final-export count.

Verification target:

- Focused export-preview tests that assert a litter-only conflict returns:
  - `focus_review_blocker_items == 0`;
  - `animal_sheet_validation_blocker_items == 1`;
  - a clearly named combined final-export count;
  - UI text that does not call the combined count "Focus blockers".

### P2: Animal-sheet 409 response message points to the wrong review surface

Files:

- `app/main.py`

Observed issue:

When animal-sheet validation blocks export, `/api/exports/animal-sheet.xlsx` returns a 409 message telling the user to resolve Focus Review blockers. In the animal-sheet-only case, `review_blockers` can be empty because those validation blockers are represented separately under `animal_sheet_litter_validation` and `animal_sheet_validation_review_items`.

Risk:

The response technically includes the validation details, but the headline message sends the operator to the wrong mental model. This matters for lab workflow because blockers should be visible and actionable without digging through a secondary payload.

Suggested resolution:

- Change the 409 message based on blocker type:
  - Focus Review blockers present: resolve Focus Review blockers.
  - Animal-sheet validation blockers present: review animal-sheet litter/date/count validation items.
  - Both present: mention both categories.
- Include a compact `blocker_summary` object in the 409 payload with separate counts.
- Ensure the UI download error copy uses the same language.

Verification target:

- A focused test for animal-sheet-only validation conflict should assert the 409 message mentions animal-sheet litter/date/count review, not only Focus Review.

### P3: Active seed data still contains project-specific labeling rules

Files:

- `app/db.py`

Observed issue:

`LABELING_RULE_SET_SEEDS` and related seed rows include an active `ApoM Tg/Tg` rule, a specific date, and labeling policies. The project instruction says not to hard-code strain names, genotype categories, protocols, or date rules.

Risk:

This may be acceptable as a pilot fixture, but as active seed data it can look like product behavior. Future branches may accidentally build logic around the seed instead of treating it as configurable lab policy.

Suggested resolution:

- Classify these seed rows explicitly as example/pilot configuration, or move them to a local/imported config path.
- Keep rule masters configurable and editable.
- Avoid using the seeded strain/rule names in generic tests unless the test is explicitly about imported pilot config.

Verification target:

- Config/rule tests should prove behavior comes from editable rule masters, not hard-coded strain strings.

## Verification Evidence From This Review

Commands run on `codex/animal-sheet-litter-validation`:

```powershell
python -m pytest tests/test_artifact_workflow.py -q
node scripts/verify-mvp.js
```

Observed result:

- `tests/test_artifact_workflow.py`: 33 passed, 92 FastAPI deprecation warnings.
- `scripts/verify-mvp.js`: MVP verification passed.

These commands prove the focused artifact/export workflow checks pass in the reviewed branch. They do not prove that all branches or the full repository verification suite is clean.

## Other Branches To Review Later

Branch inventory was taken from `git branch --list` on 2026-05-19. The branches below were not reviewed in this pass. Treat this as a batching map, not as findings against those branches.

Recommended grouping:

| Batch | Branch themes | Why group them |
| --- | --- | --- |
| Export and artifact contracts | `codex/artifact-workflow-contracts`, `codex/export-readiness-gate`, `codex/export-row-state-policy`, `codex/export-staleness-check`, `codex/export-filename-preview`, `codex/export-preview-filenames-only`, `codex/export-preview-parent-grouping`, `codex/animal-preview-grouping` | These branches likely touch the same preview/export contract surface as the findings above. Review together to avoid renaming fields repeatedly. |
| Review workflow and evidence UI | `codex/focus-review-low-fatigue`, `codex/focus-review-low-fatigue-v2`, `codex/focus-review-action-hints`, `codex/review-queue-design-polish`, `codex/review-evidence-panel`, `codex/review-rendering-safety`, `codex/review-assistant-draft-quality` | These branches likely share blocker language, review priority, and source-evidence display concerns. |
| Biological and colony-state guards | `codex/biological-review-guards`, `codex/litter-weaning-flow`, `codex/colony-schedule-read-model`, `codex/colony-state-read-model`, `codex/colony-state-ui`, `codex/mouse-timeline-read-model`, `codex/timeline-event-evidence-refs` | These should be checked against the same event-history and review-before-risky-change principles. |
| Photo/OCR/evidence extraction | `codex/hybrid-note-line-extraction-evaluator`, `codex/photo-e2e-*`, `codex/synthetic-*`, `codex/roi-preview-url-escape`, `codex/visual-photo-anchors`, `codex/evidence-ledger-*` | These should be reviewed for payload minimization, raw-vs-normalized separation, traceability, and local-only safety. |
| Strain/genotype/rule configuration | `codex/configurable-breeding-rule-config`, `codex/breeding-*`, `codex/strain-*`, `codex/legacy-strain-*`, `codex/labeling-session-rule-sample-guard` | These are the best place to handle the seed/config hard-coding concern without scattering fixes. |
| Desktop, dependency, and infrastructure | `codex/tauri-desktop-sidecar*`, `codex/dependency-sweep`, `codex/fix-tauri-dev-port-conflict`, `codex/sqlite-migration-stash-review` | These should be reviewed separately from domain behavior because verification and risk profile differ. |

## Suggested Later Workflow

1. Start with the export/artifact batch because it overlaps directly with the current animal-sheet blocker naming issue.
2. For each branch, record:
   - branch name;
   - base branch or merge target;
   - files changed;
   - whether it changes raw source, parsed/intermediate, canonical state, review item, export/view, or cache layers;
   - tests already present;
   - new findings or conflicts with this backlog.
3. Resolve naming and payload contracts once in the export/artifact batch before touching UI polish branches.
4. Only then review UI/review workflow branches so they can consume the cleaned contract.
5. Review strain/rule branches before fixing the `ApoM Tg/Tg` seed concern, because one of those branches may already contain the better configuration direction.

## Open Questions

- Should `blocked_review_items` remain backward-compatible as a combined final-export count, or should it be narrowed back to Focus Review only with a new combined field?
- Should animal-sheet validation review items appear in the main Focus Review UI, or in a dedicated Export Center review section?
- Should the pilot `ApoM Tg/Tg` labeling rule remain seeded for local demos, or move to an example import/config artifact?

---

## Branch Review: `codex/artifact-workflow-contracts`

Review date: 2026-05-19
Comparison base: `main...codex/artifact-workflow-contracts`
Scope: diff review only. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch introduces useful artifact concepts: proposed changesets, validation reports, export manifests, export-log provenance parsing, photo evidence linkage, and high-risk evidence enforcement. The direction matches the evidence-first product principles.

The main issues are contract holes: source-record-only evidence is not carried through export validation/manifest artifacts, CSV exports are listed in the manifest schema but do not actually create manifests, and genotyping evidence references can become internally contradictory.

### P2: Export validation drops source-record trace

Files:

- `app/main.py`
- `docs/artifact_contracts/export_manifest.schema.json`

Branch references:

- `codex/artifact-workflow-contracts:app/main.py:3501`
- `codex/artifact-workflow-contracts:app/main.py:3548`
- `codex/artifact-workflow-contracts:app/main.py:3596`
- `codex/artifact-workflow-contracts:docs/artifact_contracts/export_manifest.schema.json:72`

Observed issue:

`build_export_validation_report` collects photo IDs, note item IDs, and mouse IDs from export preview rows, but it does not collect `source_record_id` / `source_record_ids`. The missing-source-trace check only considers photo/note evidence. The export manifest also serializes only photo, note, review, and mouse IDs.

Risk:

Rows grounded only in a preserved source record, such as imported Excel rows or manual source records, can be reported as missing trace or can lose their trace in export artifacts. That conflicts with the project rule that traceability can point back to a source photo, note item, or imported Excel/source row.

Suggested resolution:

- Add `source_record_ids` to validation report source refs.
- Add `source_record_ids` to export manifest source refs and schema.
- Include source records in the missing-trace evidence check.
- Add a focused test where an export row has `source_record_id` but no photo/note and should still preserve source-record trace.

### P2: CSV export types are in the manifest schema but CSV endpoints do not create manifests

Files:

- `app/main.py`
- `docs/artifact_contracts/export_manifest.schema.json`

Branch references:

- `codex/artifact-workflow-contracts:app/main.py:9787`
- `codex/artifact-workflow-contracts:app/main.py:9831`
- `codex/artifact-workflow-contracts:app/main.py:9907`
- `codex/artifact-workflow-contracts:docs/artifact_contracts/export_manifest.schema.json:40`

Observed issue:

`export_manifest.schema.json` includes `mouse_csv` and `genotyping_worklist_csv` in the `export_type` enum, and there is an endpoint to create validation report artifacts for those export types. But `/api/exports/mice.csv` and `/api/exports/genotyping-worklist.csv` write only `export_log` rows and do not call `create_export_provenance_artifacts`.

Risk:

The artifact contract implies a manifest-backed provenance story for all export types, but CSV exports remain log-only. That creates uneven audit behavior: XLSX exports get validation report plus manifest, while CSV exports do not.

Suggested resolution:

- Either create validation report and export manifest artifacts for CSV endpoints too, or narrow the manifest schema and docs to workbook exports only.
- Add tests for generated and blocked `mouse_csv` exports that assert the chosen contract.
- If CSV manifests are added, include them in `/api/export-log` provenance the same way workbook exports are handled.

### P2: Genotyping evidence references can point to different photos

Files:

- `app/main.py`

Branch references:

- `codex/artifact-workflow-contracts:app/main.py:6885`
- `codex/artifact-workflow-contracts:app/main.py:6896`
- `codex/artifact-workflow-contracts:app/main.py:6916`

Observed issue:

`genotyping_result_evidence_refs` loads the photo for `photo_evidence_id` and uses it only when `source_photo_id` is absent. If the caller supplies both `photo_evidence_id` and a different `source_photo_id`, the function validates that both exist but does not verify that the evidence item belongs to the supplied photo.

Risk:

A canonical genotype result can be recorded with internally inconsistent traceability: `genotyping_record.source_photo_id` can point to one image while `photo_evidence_id` points to an evidence row from another image. This weakens auditability for a high-risk biological field.

Suggested resolution:

- If both `photo_evidence_id` and `source_photo_id` are provided, require `photo_evidence_item.source_photo_id == source_photo_id`.
- Consider also checking that linked evidence is appropriate for the target mouse or sample when that metadata is available.
- Add a negative test with mismatched source photo and photo evidence IDs.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/artifact-workflow-contracts` and `git show codex/artifact-workflow-contracts:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation changes.

---

## Branch Review: `codex/export-readiness-gate`

Review date: 2026-05-19
Comparison base: `main...codex/export-readiness-gate`
Scope: branch inventory check only.

### Summary

`git diff main...codex/export-readiness-gate` returned no changed files in this pass, so there was no implementation surface to review. Keep this branch in the export/artifact batch only if it later receives changes or if it is a placeholder for another local worktree.

---

## Branch Review: `codex/export-row-state-policy`

Review date: 2026-05-19
Comparison base: `main...codex/export-row-state-policy`
Scope: diff/source review only, focused on export row state, staleness, final-export readiness, preview rendering, and workbook trace output. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch adds a useful explicit row-state contract for export views and pushes row state into preview rows, separation rows, animal-sheet rows, and workbook trace sheets. That direction fits the project rule that Excel is an export/view, not the canonical source of truth.

The main issues are contract mismatches: the stale row state is unreachable because the key names do not match, genotype readiness warnings are surfaced as blockers but do not participate in final readiness, and the web preview chips recompute their own row state instead of rendering the backend row-state contract.

### P2: `stale_after_correction` row state is unreachable

Files:

- `app/main.py`
- `tests/test_artifact_workflow.py`

Branch references:

- `codex/export-row-state-policy:app/main.py:11457`
- `codex/export-row-state-policy:app/main.py:13055`
- `codex/export-row-state-policy:app/main.py:13170`
- `codex/export-row-state-policy:tests/test_artifact_workflow.py:603`

Observed issue:

`export_staleness()` returns the boolean as `export_stale`, but `export_row_state()` checks `stale_state.get("stale")`. As a result, rows can only become `blocked_by_review` or `ready`; `stale_after_correction` is listed in the policy but is not produced by the implementation. The focused artifact workflow tests assert only the ready case and do not cover stale state.

Risk:

After accepted data changes following a generated export, the API can expose `export_stale: true` while every preview/workbook trace row still says `ready`. That makes the trace sheet less trustworthy exactly when the operator needs to know that a prior export is stale.

Suggested resolution:

- Change the row-state check to use `stale_state.get("export_stale")`.
- Add a focused test where `latest_data_change_at > latest_generated_export_at` and rows are marked `stale_after_correction`.
- Assert both API preview rows and XLSX `Export_Trace` rows carry the stale state and reason.

### P2: Genotype readiness blockers are displayed but do not gate final export readiness

Files:

- `app/main.py`
- `static/index.html`

Branch references:

- `codex/export-row-state-policy:app/main.py:11782`
- `codex/export-row-state-policy:app/main.py:13370`
- `codex/export-row-state-policy:app/main.py:13371`
- `codex/export-row-state-policy:app/main.py:13372`
- `codex/export-row-state-policy:static/index.html:6552`
- `codex/export-row-state-policy:static/index.html:6559`
- `codex/export-row-state-policy:static/index.html:6567`

Observed issue:

`genotype_export_blockers()` returns rows for statuses where `blocks_experiment = 1` or `export_warning = 1`. The preview exposes these as `genotype_blocker_items` and `readiness_warnings`, and the UI copy can say genotype blockers/readiness warnings "must be reviewed first." But `preview.ready` is still computed only as `blocked_reviews == 0 and bool(rows)`, and the final download buttons are enabled solely from `preview.ready`.

Risk:

If genotype blockers exist but no Focus Review blockers exist, the UI can show final exports as ready and enable CSV/XLSX downloads while also listing genotype readiness concerns. That is especially risky for lab workflow because genotype state can affect experiment and export interpretation.

Suggested resolution:

- Decide whether `blocks_experiment` and/or `export_warning` should block final export or only block experiment planning.
- If they should block export, include them in `preview.ready`, endpoint `require_ready` checks, row-state reasons, and tests.
- If they should not block export, rename the fields/copy away from "blocker" and remove "must be reviewed first" language for those rows.
- Add a focused test for a genotype-only readiness concern with no Focus Review blockers.

### P3: Web preview row-state chips ignore backend `row_state`

Files:

- `static/index.html`
- `app/main.py`

Branch references:

- `codex/export-row-state-policy:static/index.html:3861`
- `codex/export-row-state-policy:static/index.html:3874`
- `codex/export-row-state-policy:app/main.py:13049`
- `codex/export-row-state-policy:app/main.py:13246`
- `codex/export-row-state-policy:app/main.py:13278`
- `codex/export-row-state-policy:app/main.py:13321`
- `codex/export-row-state-policy:app/main.py:13345`

Observed issue:

The backend attaches `row_state` and `row_state_reason` to preview, separation, and animal-sheet rows, and the XLSX trace sheet renders those fields. The web preview chip renderer does not read them; it derives "Ready" or "Blocked" from `preview.ready` and row uncertainty.

Risk:

After the stale-key bug is fixed, or when the backend emits a specific row-state reason, the web preview can disagree with the trace sheet. Operators may see a row as ready in the browser while the exported trace marks it stale or blocked.

Suggested resolution:

- Render `item.row_state` and `item.row_state_reason` directly in the row-state chip.
- Keep uncertainty and trace-link chips as separate indicators rather than folding them into the backend row-state label.
- Add a lightweight UI/unit test or fixture assertion that a `stale_after_correction` row renders as stale in the preview table.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/export-row-state-policy` and `git show codex/export-row-state-policy:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation changes.

---

## Branch Review: Export/Artifact Branches With No Current Diff

Review date: 2026-05-19
Comparison base: `main...<branch>`
Scope: branch inventory check only.

### Summary

The following export/artifact branches returned no changed files against `main` in this pass:

- `codex/export-staleness-check`
- `codex/export-filename-preview`
- `codex/export-preview-filenames-only`
- `codex/export-preview-parent-grouping`
- `codex/animal-preview-grouping`

No findings were recorded for these branches because there was no implementation surface to review. Keep them in the batching map only as historical or placeholder branch names unless new commits appear later.

---

## Branch Review: `codex/focus-review-low-fatigue-v2`

Review date: 2026-05-19
Comparison base: `main...codex/focus-review-low-fatigue-v2`
Scope: diff/source review only, focused on Focus Review read models, low-fatigue review routing, note-line evidence anchors, and review resolution UI. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch adds a helpful Focus Review read model: it classifies review items into `must_review`, `quick_check`, `trace_only`, and `hidden_default`, groups actionable reviews by source card, and keeps the UI centered on source evidence rather than broad queue scanning.

The main issues are in the resolution edge cases. The card-list quick action can resolve ear-label reviews without applying the ear-label correction, and the API's note-line join does not match generated ear-label review IDs. Both weaken the source-photo/note-line continuity that this branch is otherwise trying to improve.

### P2: Card-list quick resolve can close ear-label reviews without updating the note evidence

Files:

- `static/index.html`
- `app/main.py`
- `tests/test_review_attention.py`

Branch references:

- `codex/focus-review-low-fatigue-v2:static/index.html:4267`
- `codex/focus-review-low-fatigue-v2:static/index.html:4269`
- `codex/focus-review-low-fatigue-v2:static/index.html:4301`
- `codex/focus-review-low-fatigue-v2:static/index.html:4319`
- `codex/focus-review-low-fatigue-v2:static/index.html:6078`
- `codex/focus-review-low-fatigue-v2:static/index.html:6114`
- `codex/focus-review-low-fatigue-v2:app/main.py:3836`
- `codex/focus-review-low-fatigue-v2:app/main.py:3844`
- `codex/focus-review-low-fatigue-v2:app/main.py:3845`
- `codex/focus-review-low-fatigue-v2:tests/test_review_attention.py:173`

Observed issue:

The detail-panel resolution controls explicitly exclude `Ear label needs review` from quick resolve and require an `ear_label_code` select value. But the review-card list button only excludes `Unlabeled numeric note needs review`, so an ear-label quick-check card can show a `Looks OK` button. That button is outside `.review-actions`, so `reviewResolutionPayload()` takes the fallback path and sends no `ear_label_code`. On the backend, `resolve_ear_label_correction()` returns `None` when `ear_label_code` is missing, while `resolve_review_item()` can still mark the review as resolved.

Risk:

The operator can close an ear-label review from the list without updating `card_note_item_log.parsed_ear_label_code`, `parsed_ear_label_review_status`, or `needs_review`. That silently separates the review queue status from the note-line evidence state.

Suggested resolution:

- Reuse the same `allowQuickResolve` issue exclusion for card-list buttons and detail-panel buttons.
- For ear-label reviews, require the detail-panel selector path or add a card-list action that includes an explicit reviewed ear-label value.
- Add a UI/API regression test that attempts quick resolve on `review_ear_<note_item_id>` without `ear_label_code` and asserts the review remains open or the request is rejected.
- Keep the existing positive test that verifies explicit `ear_label_code` updates the note without overwriting raw text.

### P2: Ear-label review items lose their note-line anchor in `list_review_items()`

Files:

- `app/main.py`
- `tests/test_review_attention.py`

Branch references:

- `codex/focus-review-low-fatigue-v2:app/main.py:3528`
- `codex/focus-review-low-fatigue-v2:app/main.py:3531`
- `codex/focus-review-low-fatigue-v2:app/main.py:6243`
- `codex/focus-review-low-fatigue-v2:app/main.py:7351`
- `codex/focus-review-low-fatigue-v2:app/main.py:7354`
- `codex/focus-review-low-fatigue-v2:tests/test_review_attention.py:154`
- `codex/focus-review-low-fatigue-v2:tests/test_review_attention.py:173`

Observed issue:

Ear-label review IDs are generated as `review_ear_<note_item_id>`, and helper `review_note_item_id()` knows that prefix. But the `list_review_items()` SQL join looks for `review_ear_note_%`, so it does not join the corresponding `card_note_item_log` row for normal ear-label review IDs.

Risk:

The Review Queue detail view can lose the specific raw note-line anchor for exactly the review type where the operator needs to compare handwritten ear marks against normalized codes. This also makes card-list quick actions more dangerous because `data-note-item-id` can be empty.

Suggested resolution:

- Change the SQL join pattern to match `review_ear_%`, or compute note anchors through the existing `review_note_item_id()` helper outside SQL.
- Add a test that `list_review_items()` for `review_ear_<note_item_id>` returns:
  - `note_item_id`;
  - `review_note_raw_line`;
  - `review_note_parsed_type`;
  - the relevant `card_snapshot_id`.
- Confirm the UI detail panel shows the note-line anchor before allowing resolution.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/focus-review-low-fatigue-v2` and `git show codex/focus-review-low-fatigue-v2:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation and local task changes.

---

## Branch Review: `codex/focus-review-action-hints`

Review date: 2026-05-19
Comparison base: `main...codex/focus-review-action-hints`
Scope: diff/source review only, focused on Focus Review action hints, quick resolution behavior, resolution failure handling, and review evidence anchors. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch is largely an earlier ancestor of the later `codex/focus-review-low-fatigue-v2` branch. It adds the low-fatigue Focus Review surface and keeps selection on the failed review when resolution submission fails.

The main risk is that the UI action shortcuts are broader than the backend/domain contract. High-risk `must_review` items can be auto-resolved from the detail panel, and ear-label quick actions can close the review without applying the note-line correction. Both cases can reduce export blockers without proving the underlying lab evidence was actually reconciled.

### P2: `must_review` items can be quick-resolved as "Accept after check"

Files:

- `static/index.html`
- `app/main.py`
- `tests/test_low_fatigue_ui_contracts.py`
- `tests/test_review_attention.py`

Branch references:

- `codex/focus-review-action-hints:static/index.html:4016`
- `codex/focus-review-action-hints:static/index.html:4017`
- `codex/focus-review-action-hints:static/index.html:4069`
- `codex/focus-review-action-hints:static/index.html:4118`
- `codex/focus-review-action-hints:static/index.html:4147`
- `codex/focus-review-action-hints:app/main.py:7419`
- `codex/focus-review-action-hints:app/main.py:7493`
- `codex/focus-review-action-hints:app/main.py:7566`
- `codex/focus-review-action-hints:tests/test_low_fatigue_ui_contracts.py:75`
- `codex/focus-review-action-hints:tests/test_review_attention.py:683`

Observed issue:

`reviewResolutionControls()` labels quick resolution for `attention_level === "must_review"` as "Accept after check" and allows it for every issue except three hard-coded exclusions. That means a high-risk item such as `Duplicate active mouse` can be resolved with an auto-generated note. The backend then marks `review_queue.status = 'resolved'` and may only record a generic `review_item` correction, without requiring an issue-specific canonical correction, duplicate resolution, or note-line change.

Risk:

Focus Review blockers are used by export readiness. A user can remove a high-risk blocker from the queue without actually resolving the biological or canonical-state risk that made it `must_review`. This conflicts with the rule that risky inferred state changes and conflicts should stay reviewable and traceable until the underlying issue is handled.

Suggested resolution:

- Restrict auto/quick resolve to explicitly whitelisted `quick_check` issues only.
- Hide the quick button for `must_review` items, or require an issue-specific resolution payload for each `must_review` class.
- On the backend, reject auto-resolution of `must_review` items unless the expected correction/application fields are present.
- Add a regression test for a duplicate-active `must_review` item proving it cannot be closed through the quick path alone.

### P2: Card-list quick resolve can close ear-label reviews without updating note evidence

Files:

- `static/index.html`
- `app/main.py`
- `tests/test_review_attention.py`

Branch references:

- `codex/focus-review-action-hints:static/index.html:4017`
- `codex/focus-review-action-hints:static/index.html:4019`
- `codex/focus-review-action-hints:static/index.html:4301`
- `codex/focus-review-action-hints:static/index.html:5824`
- `codex/focus-review-action-hints:static/index.html:5860`
- `codex/focus-review-action-hints:app/main.py:3726`
- `codex/focus-review-action-hints:app/main.py:3744`
- `codex/focus-review-action-hints:app/main.py:3745`
- `codex/focus-review-action-hints:tests/test_review_attention.py:173`

Observed issue:

The detail-panel controls exclude `Ear label needs review` from quick resolve and require an `ear_label_code` selector. The review-card list quick button only excludes `Unlabeled numeric note needs review`, so an ear-label quick-check card can still show `Looks OK`. That card button is outside `.review-actions`, so the fallback payload sends no `ear_label_code`; `resolve_ear_label_correction()` returns `None`, while the review can still be marked resolved.

Risk:

The review queue can say the ear-label issue is resolved while the source note item still has unresolved ear-label evidence. This weakens the mouse-ID/note-line continuity anchor.

Suggested resolution:

- Use one shared allowlist/exclusion helper for all quick-resolve button locations.
- Require the detail-panel ear-label selector, or include an explicit reviewed ear-label value in any card-level action.
- Add a negative test where `review_ear_<note_item_id>` cannot be resolved without `ear_label_code`.

### P3: Focus Review cards expose static action labels instead of item-level action contracts

Files:

- `app/main.py`
- `tests/test_low_fatigue_ui_contracts.py`

Branch references:

- `codex/focus-review-action-hints:app/main.py:7300`
- `codex/focus-review-action-hints:app/main.py:7385`
- `codex/focus-review-action-hints:tests/test_low_fatigue_ui_contracts.py:134`

Observed issue:

`/api/ui/focus-review` returns the same card-level actions for every card: `Apply confirmed rows only`, `Hold card`, and `Open source photo`. It does not expose per-review action contracts such as whether a review is safe to quick-confirm, requires a note decision, requires source photo inspection, or must use a specialized correction path.

Risk:

The read model gives the operator a low-fatigue card, but the actions do not distinguish a duplicate-active blocker from a grouped numeric note or an ear-label correction. That makes the UI rely on scattered button logic instead of a single review contract, which is how the quick-resolve mismatches above appear.

Suggested resolution:

- Add per-review `action_hint` objects to `review_items`, with `mode`, required fields, and whether quick confirmation is allowed.
- Drive the UI buttons from those action hints rather than duplicating issue checks in multiple render paths.
- Add tests for duplicate-active, grouped numeric note, ear-label, and unknown quick-check review types.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/focus-review-action-hints` and `git show codex/focus-review-action-hints:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation and local task changes.

---

## Branch Review: `codex/review-evidence-panel`

Review date: 2026-05-19
Comparison base: `main...codex/review-evidence-panel`
Scope: diff/source review only, focused on Review Queue evidence panels, source-photo/note/card-snapshot trace, and predecessor Excel/source-record trace. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch improves the Review Queue detail panel by showing source photo preview URLs, note-line anchors, card snapshot fields, and parsed snapshot summaries for note-anchored review items. The direction is good and directly supports the lab workflow.

The gaps are in review items that are not anchored to a note item. Photo/parse-level reviews can have a card snapshot but still render as `source pending`, and legacy workbook row reviews can have an imported Excel/source row but the panel does not surface that source-record evidence.

### P2: Photo-level reviews lose card snapshot evidence in the new panel

Files:

- `app/main.py`
- `static/index.html`
- `scripts/verify-local-app.py`

Branch references:

- `codex/review-evidence-panel:app/main.py:3318`
- `codex/review-evidence-panel:app/main.py:3331`
- `codex/review-evidence-panel:app/main.py:3431`
- `codex/review-evidence-panel:app/main.py:3462`
- `codex/review-evidence-panel:app/main.py:3463`
- `codex/review-evidence-panel:static/index.html:2353`
- `codex/review-evidence-panel:static/index.html:2364`
- `codex/review-evidence-panel:static/index.html:2369`
- `codex/review-evidence-panel:scripts/verify-local-app.py:890`

Observed issue:

Manual/photo transcription creates a card snapshot for the parse and also creates a photo-level review item such as `review_<parse_id>`. But `/api/review-items` joins `card_snapshot` only through `review_note.card_snapshot_id`. If a review item has a `parse_id` but no note anchor, its card snapshot fields are blank even though the snapshot exists. The UI then shows `Snapshot --` and `Boundary source pending`.

Risk:

The detail panel can understate available parsed evidence for photo-level reviews. Operators may see a source photo and parse ID but not the card snapshot summary they need for reviewing raw/normalized card fields.

Suggested resolution:

- Join `card_snapshot` by note anchor when present, otherwise fall back to latest/current snapshot for `review.parse_id`.
- Keep note-line anchor distinct from card-level snapshot evidence.
- Add a focused test for `review_<parse_id>` asserting `card_snapshot_id`, card type, raw/matched strain, sex/count, DOB, and `review_note_summary` are present.
- Extend `verify-local-app.py` beyond numeric note reviews so photo-level reviews are covered too.

### P2: Legacy workbook row review evidence is not surfaced in Review Queue detail

Files:

- `app/main.py`
- `static/index.html`

Branch references:

- `codex/review-evidence-panel:app/main.py:3020`
- `codex/review-evidence-panel:app/main.py:3047`
- `codex/review-evidence-panel:app/main.py:3069`
- `codex/review-evidence-panel:app/main.py:3072`
- `codex/review-evidence-panel:app/main.py:3097`
- `codex/review-evidence-panel:static/index.html:2353`
- `codex/review-evidence-panel:static/index.html:2397`
- `codex/review-evidence-panel:static/index.html:2406`

Observed issue:

Legacy workbook imports preserve a `source_record_id`, create `legacy_workbook_row` records with `review_id`, and open review items. The new review evidence panel only displays photo/note/card-snapshot evidence. `/api/review-items` does not join `legacy_workbook_row` or `legacy_workbook_import`, so a legacy row review does not show source workbook, sheet, row number, source record, or raw row JSON in the Review Queue detail.

Risk:

This conflicts with the project rule that traceability can point back to a source photo, note item, or imported Excel row. A reviewer handling predecessor workbook evidence has to leave the Review Queue detail panel to find the actual source row.

Suggested resolution:

- Add legacy row/source-record fields to `/api/review-items` for review IDs linked through `legacy_workbook_row.review_id`.
- Render an "Imported Excel row" evidence block alongside photo/note/card evidence.
- Add tests for a legacy workbook review item showing `source_record_id`, source file, sheet, row number, and raw row payload.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/review-evidence-panel` and `git show codex/review-evidence-panel:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation and local task changes.

---

## Branch Review: `codex/review-rendering-safety`

Review date: 2026-05-19
Comparison base: `main...codex/review-rendering-safety`
Scope: diff/source review only, focused on Review Queue rendering safety, read-model traceability, source evidence display, and whether UI read models preserve photo/note/source-record anchors. This pass did not check out the branch or run that branch's test suite.

### Summary

This branch is larger than the name suggests. It includes Review Queue rendering changes plus Focus Review, Colony State/Schedule, Mouse Timeline, Mouse Pedigree, and Evidence Ledger read models. Most new HTML interpolation paths use `escapeHtml()`, so the immediate rendering-safety direction is reasonable.

The remaining risks are evidence-contract risks: several read models render useful summaries while dropping or hiding the exact source anchors that the lab workflow needs to review later.

### P2: Mouse Timeline drops event-level evidence that is not a `source_record_id`

Files:

- `app/main.py`
- `static/index.html`

Branch references:

- `codex/review-rendering-safety:app/main.py:7939`
- `codex/review-rendering-safety:app/main.py:7977`
- `codex/review-rendering-safety:app/main.py:8007`
- `codex/review-rendering-safety:static/index.html:4298`

Observed issue:

`/api/ui/mouse-timeline` selects and displays only `mouse_event.source_record_id` as source evidence. Other event evidence anchors that the API already supports, such as `details.source_photo_id`, `details.photo_evidence_id`, and `details.source_note_item_id`, are not parsed or returned. The UI then renders `Evidence --` for events that may still be source-backed through photo, note-line, or photo-evidence references.

Risk:

This can make accepted timeline events look untraceable even when they were created with valid photo/note evidence. That conflicts with the project rule that every durable event should remain traceable back to a source photo, note item, imported row, or source record.

Suggested resolution:

- Parse `mouse_event.details` in the timeline read model and expose `source_photo_id`, `photo_evidence_id`, and `source_note_item_id` alongside `source_record_id`.
- Add the referenced photo/note/evidence labels when available.
- Add a regression test with an event that has `details.source_note_item_id` or `details.photo_evidence_id` but no `source_record_id`.

### P2: Pedigree marks parent relationships confirmed without relationship-specific evidence

Files:

- `app/main.py`
- `tests/test_low_fatigue_ui_contracts.py`

Branch references:

- `codex/review-rendering-safety:app/main.py:8088`
- `codex/review-rendering-safety:app/main.py:8178`
- `codex/review-rendering-safety:app/main.py:8231`
- `codex/review-rendering-safety:tests/test_low_fatigue_ui_contracts.py:1114`

Observed issue:

`/api/ui/mouse-pedigree` selects only identity/relationship fields from `mouse_master`, then labels father and mother rows as `confirmed` whenever the IDs exist. The evidence rows attach both parent fields to a single `lineage_source_record_id` derived from the litter or mating source record. If the parent IDs came from a different mouse source, note-line, correction, or event, that relationship-specific evidence is not surfaced. If the mouse has parent IDs but no litter/mating source, the relationship can still be shown as confirmed with an empty source.

Risk:

The pedigree panel can overstate confidence in parentage and hide the exact evidence that established each parent relationship. For colony data, that is a high-friction failure mode: parentage is biologically meaningful and should not be displayed as confirmed unless the source anchor for that specific relationship is visible.

Suggested resolution:

- Include relationship-specific evidence fields in the pedigree read model: mouse source refs, litter/mating source refs, correction refs, and relevant mouse-event refs.
- Distinguish `confirmed_with_source`, `confirmed_source_pending`, and `pending_review` instead of a single confirmed/pending split.
- Add tests where father/mother IDs exist but source evidence is absent or comes from a note/event rather than the litter source record.

### P2: Evidence Ledger cards hide the concrete anchors needed to re-open evidence

Files:

- `app/main.py`
- `static/index.html`

Branch references:

- `codex/review-rendering-safety:app/main.py:8370`
- `codex/review-rendering-safety:app/main.py:8402`
- `codex/review-rendering-safety:static/index.html:4459`
- `codex/review-rendering-safety:static/index.html:4466`
- `codex/review-rendering-safety:static/index.html:4474`

Observed issue:

The Evidence Ledger endpoint returns useful anchors, including `photo_evidence_id`, `source_photo.photo_id`, `links.note_item_id`, and `links.review_ids`. The renderer only shows filename/status plus observed/OCR/parsed text and a review count. It does not display or link the actual photo evidence ID, note item ID, source photo ID, linked mouse/cage/event IDs, or review IDs.

Risk:

The panel answers "what evidence supports this record?" but leaves the operator without the precise anchor needed to reopen the source photo, note line, or review item. That weakens the audit workflow and makes the ledger behave like a summary card rather than a traceable evidence ledger.

Suggested resolution:

- Render the primary anchors explicitly: photo evidence ID, source photo ID/open-photo action, note item ID, linked mouse/cage/event IDs, and review IDs.
- Keep raw observed text, OCR text, and parsed interpretation visually separate.
- Add UI contract coverage asserting that anchor IDs returned by `/api/ui/evidence-ledger` are visible or actionable in the rendered panel.

### Verification Notes

No branch tests were run in this pass. The review used `git diff main...codex/review-rendering-safety` and `git show codex/review-rendering-safety:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation and local task changes.

---

## Branch Review: `codex/review-round-mainline`

Review date: 2026-05-19
Comparison base: `codex/review-rendering-safety...codex/review-round-mainline`
Scope: incremental diff/source review only, focused on the three new commits after `codex/review-rendering-safety`: correction provenance validation, ROI crop URL escaping, and mouse-pedigree pending-parent review routing. This pass did not check out the branch or run that branch's test suite.

### Summary

The correction provenance change is a useful guard: missing `source_record_id` values are normalized to `NULL`, invalid source-record references are rejected before writing, and the new test checks that no partial correction/action rows remain on rejection. The ROI crop URL change also moves the dynamic URL through `escapeHtml()` before inserting it into `innerHTML`.

The remaining issue is in the pending-parent pedigree route. The API now creates an attention link even when there is no open review workload, but the UI still labels that action as `Open Focus Review` and only opens the review queue.

### P2: Pending-parent pedigree link opens an empty Focus Review instead of a relationship review target

Files:

- `app/main.py`
- `static/index.html`
- `tests/test_low_fatigue_ui_contracts.py`

Branch references:

- `codex/review-round-mainline:app/main.py:8283`
- `codex/review-round-mainline:app/main.py:8312`
- `codex/review-round-mainline:app/main.py:8315`
- `codex/review-round-mainline:static/index.html:4377`
- `codex/review-round-mainline:tests/test_low_fatigue_ui_contracts.py:1231`

Observed issue:

`/api/ui/mouse-pedigree` adds an `attention_links` entry whenever parent relationships are pending, even if `must_review` and `quick_check` are both zero. The link is still labeled `Open Focus Review`, points to `/api/ui/focus-review`, and the static renderer always binds it to `setActiveView("review")`. The new test explicitly asserts this state: two pending relationships, no open review items, and a Focus Review link.

Risk:

An operator can click the only suggested action for a pending parent relationship and land on an empty Focus Review queue. That makes the pending parent state visible but not actionable, and it also blurs the boundary between "there is an open review item" and "the canonical pedigree read model has missing relationship evidence." It may look like the system lost a review item.

Suggested resolution:

- For pending parent relationships without open review items, return a distinct action contract such as `mode: "relationship_evidence_missing"` and a label like `Review parent evidence`.
- Route the UI to a pedigree-specific correction/evidence workflow, or create/open a real review item before linking to Focus Review.
- Keep `source_layer` for the pending evidence row explicit as a derived read-model/review prompt rather than implying the missing relationship itself is canonical source evidence.
- Add a UI contract test that clicking the pending-parent action does not lead to an empty review queue.

### Verification Notes

No branch tests were run in this pass. The review used `git diff codex/review-rendering-safety...codex/review-round-mainline` and `git show codex/review-round-mainline:<path>` while staying on the current worktree to avoid disturbing uncommitted documentation and local task changes.
