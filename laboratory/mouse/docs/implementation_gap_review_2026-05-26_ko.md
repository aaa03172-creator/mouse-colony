# MouseDB Implementation Gap Review - 2026-05-26

Layer classification: review item / non-canonical implementation review.

Canonical status: non-canonical. This document summarizes related planning, audit, backlog, and implementation documents against the current checked-out code. If this document conflicts with `AGENTS.md`, `final_mouse_colony_prd.md`, committed runtime behavior, or tests, call out the mismatch before implementation.

Branch reviewed: `codex/review-implementation-plan`.

## Purpose

This document consolidates the external Antigravity plan at `C:\Users\User\.gemini\antigravity\brain\951ad1b7-52ae-48c5-bac4-bd49550d44b8\implementation_plan.md` with repository documents and current code evidence. Its goal is to give future implementation sessions a compact map of:

- what the related documents ask for;
- which items are already implemented in this checkout;
- which items remain incomplete or ambiguous;
- which files and tests should be touched first.

It does not define new canonical product behavior. Treat it as a dated review and planning artifact.

## Documents Reviewed

| Document | Role in this review |
| --- | --- |
| `AGENTS.md` | Active project guardrails for data boundaries, review safety, OCR/inference, and Git hygiene. |
| `docs/DOCUMENTATION_MAP.md` | Source of document precedence and navigation. |
| `final_mouse_colony_prd.md` | Product intent anchor; used indirectly through the documentation map and audit documents. |
| `mvp_vertical_slice_plan.md` | First end-to-end photo-to-review-to-export implementation sequence. |
| `mvp_acceptance_matrix_ko.md` | Current MVP acceptance status; most core evidence/review/export contracts are marked Done. |
| `docs/cage_card_photo_pipeline_implementation_baseline_ko.md` | Baseline rules for raw photos, parsed/intermediate data, review gates, canonical apply, and export provenance. |
| `docs/cage_card_photo_pipeline_implementation_audit_2026-05-11.md` | Audit of current evidence/provenance infrastructure and remaining domain-event propagation gaps. |
| `mousedb_open_design_artifact_workflow_review_ko.md` | Artifact lifecycle, validation report, export manifest, and preview-before-commit guidance. |
| `docs/artifact_contracts/*.schema.json` | Current proposed changeset, validation report, and export manifest schema contracts. |
| `docs/code_review_backlog_2026-05-19_ko.md` | Review backlog with resolved export counter issues and remaining P2/P3 findings. |
| `docs/ui_ux_revision_plan_2026-05-13_ko.md` | Seven UI/UX revision slices. |
| `docs/ui_ux_implementation_tracker_2026-05-11.md` | Merged UI/UX slices and current remaining UI recommendations. |
| `docs/superpowers/specs/2026-05-19-animal-sheet-litter-date-count-validation-review_ko.md` | Animal sheet litter/date/count validation design. |
| `docs/superpowers/plans/2026-05-19-animal-sheet-litter-validation.md` | Initial animal sheet validation implementation plan. |
| `docs/superpowers/plans/2026-05-19-animal-sheet-litter-validation-followup.md` | Follow-up validator plan for Pubs count, ambiguous date, and source-only warnings. |
| Antigravity `implementation_plan.md` | External consolidated plan; several items are now stale or partially implemented. |

## Current High-Level Status

The core MVP evidence loop is largely in place:

- raw source photos, parsed evidence, card snapshots, note items, review items, canonical candidates, validation artifacts, and export manifests exist;
- canonical apply is guarded by preview/review checks and evidence references;
- final animal sheet export is blocked by Focus Review blockers and animal-sheet litter/date/count blockers;
- validation report and export manifest artifacts preserve source refs, state watermark, and export provenance for workbook paths;
- animal-sheet-specific litter/date/count validation now exists and is wired into `/api/export-preview`.

The remaining gaps are mostly contract cleanup and workflow completion, not a new data model:

- some review resolution paths can still mark a review resolved without the domain-specific correction payload;
- some read models are actionable only through generic Focus Review navigation;
- some source/evidence refs are not consistently validated across all high-risk flows;
- seed/example configuration is still mixed into active DB initialization;
- UI copy and count contracts are only partly aligned with the low-fatigue review plan.

## Implemented Or Mostly Implemented Since Earlier Plans

### Export Blocker Count Separation

Status: implemented.

Evidence:

- `app/main.py` returns `final_export_blocker_items`, `focus_review_blocker_items`, and `animal_sheet_validation_blocker_items` from `/api/export-preview`.
- `static/index.html` renders Focus blockers separately from final export blockers.
- `tests/test_artifact_workflow.py` asserts animal-sheet-only validation blockers keep `focus_review_blocker_items == 0`.

Implementation note:

- `blocked_review_items` remains a backward-compatible combined final-export blocker count. Future code should prefer the more explicit fields.

### Animal Sheet Litter/Date/Count Validator

Status: implemented beyond the original spec.

Evidence:

- `app/main.py` defines `validate_animal_sheet_litter_date_counts`.
- Current checks include:
  - `litter_date_before_mating`;
  - `weaning_date_before_birth`;
  - `weaned_count_exceeds_born`;
  - `pubs_count_conflicts_with_number_born`;
  - `current_pup_count_delta_without_event`;
  - `current_pup_count_delta_explained`;
  - `ambiguous_litter_date_normalization`;
  - `litter_source_record_without_photo_or_note`.
- `/api/export-preview` sets animal-sheet rows to `blocked_by_litter_conflict` for blocked litter rows.
- `tests/test_artifact_workflow.py` covers the validator, report mapping, blocked export, and separation/CSV behavior when animal sheet alone is blocked.

Implementation note:

- The validator currently returns `review_item_candidates: []`. It blocks export through preview/report state but does not yet persist dedicated review items for every validator finding.

### Validation Report And Export Manifest Provenance

Status: mostly implemented.

Evidence:

- `build_export_validation_report`, `build_export_manifest`, `persist_export_manifest_artifact`, and `create_export_provenance_artifacts` exist in `app/main.py`.
- `source_record_ids` are present in `validation_report.schema.json`, `export_manifest.schema.json`, and artifact tests.
- Export log parsing returns manifest path, validation report id/path, and state watermark.

Important limitation:

- `/api/exports/mice.csv` and `/api/exports/genotyping-worklist.csv` still write export logs directly and do not appear to call `create_export_provenance_artifacts`, even though the manifest schema includes `mouse_csv` and `genotyping_worklist_csv`. Future work must either add CSV manifests or narrow the schema/docs.

### Mouse Timeline Evidence Details

Status: mostly implemented.

Evidence:

- `/api/ui/mouse-timeline` now parses `mouse_event.details` for `source_photo_id`, `source_note_item_id`, and `photo_evidence_id`.
- The read model includes an `event_trace` block with source photo filename, note text, photo evidence text, and trace status.

Remaining concern:

- Domain-specific service flows still need review to ensure they actually pass the most specific photo/note/evidence refs into event details, not only a broad manual `source_record_id`.

## Accuracy Strategy Review

The accuracy strategy should not mean "auto-accept more values." For this project, accuracy improves when the system:

- preserves raw evidence;
- separates raw, normalized candidate, and accepted canonical values;
- makes uncertainty visible at the right review level;
- blocks only high-risk contradictions;
- measures whether each rule helps or creates avoidable review fatigue.

### Rule Severity Ladder

Use three levels for new accuracy rules.

#### Hard Blocker

Use this only when false negatives are more dangerous than false positives.

Good hard blockers:

- birth date earlier than mating start date;
- weaning/separation date earlier than birth date;
- `number_weaned > number_born`;
- one mouse mapped to multiple father/mother pairs without reviewed correction;
- genotype confirmation evidence refs disagree, such as `photo_evidence_id` belonging to a different `source_photo_id`;
- high-risk canonical/event writes without source photo, note item, photo evidence, or source record trace;
- duplicate active mouse identity where source context does not resolve the conflict.

Behavior:

- block canonical apply or final export;
- keep raw values intact;
- create or expose a review item with source refs and before/after context;
- write validation report/export manifest provenance when the blocker affects export.

#### Review Warning

Use this for plausible problems that may be normal under lab-specific timing, delayed entry, or unusual but valid colony operations.

Good warning candidates:

- gestation/weaning windows outside configurable thresholds;
- source-record-only litter rows with no photo/note trace;
- older photo capture time than the latest accepted state for the same mouse/cage;
- ID continuity gaps such as `101, 102, 104` without a struck-through or loss/death explanation;
- cage density / welfare warnings;
- parent replacement and no-birth productivity warnings;
- current pup count lower than born count but explained by source-backed death/loss/separation events.

Behavior:

- show in review/export readiness surfaces without automatically blocking unrelated exports;
- record `validator_check_key`, source refs, and recommended action;
- allow operator dismissal only with reason when the warning affects handoff decisions.

#### Assistive Hint

Use this for predictions or suggestions that can help the operator but should not decide state.

Good hint candidates:

- Mendelian genotype expectation;
- parent candidate suggestions from mating/litter history;
- OCR normalization suggestions for `1/l`, `0/O`, spacing, and punctuation;
- strain or labeling-rule preset pre-selection from OCR text;
- possible source-priority conflict where newer photo evidence may supersede older Excel evidence, before a deterministic conflict exists.

Behavior:

- label as `hint`, `candidate`, or `review suggestion`;
- never overwrite raw evidence;
- never unblock a hard blocker by itself;
- require a review decision or policy-approved path before accepted state changes.

### Measurement Loop For Rule Accuracy

The next accuracy improvement should connect validation/review rules to outcome measurement. The repository already has private accuracy infrastructure (`scripts/report-private-accuracy.py`, review field outcomes, private runbooks), so the missing piece is to make rule outcomes visible enough to measure.

Recommended additions:

- keep stable `validator_check_key` values for every deterministic rule;
- store review outcomes such as `accepted`, `corrected`, `dismissed_with_reason`, `false_positive`, and `policy_exception`;
- aggregate rule-level counts in sanitized reports:
  - fired count;
  - blocker count;
  - warning count;
  - corrected count;
  - false-positive count;
  - median review seconds if available;
  - export/apply blocks prevented;
- include `rule_set_id` or threshold snapshot when a configurable policy triggers a warning;
- add a small audit export for validation/review rules that contains only sanitized IDs/counts and no private raw photo text.

This lets the lab tune thresholds without weakening safety. A rule that catches real mistakes should stay prominent. A rule that mostly creates false positives should be downgraded from hard blocker to warning, or from warning to hint.

### Additional Accuracy Ideas To Consider

These are not immediate implementation requirements, but they are good candidates for future slices:

| Idea | First level | Why |
| --- | --- | --- |
| Source priority conflict check | Review warning | Newer reviewed photo/note evidence should outrank older Excel-derived rows, but delayed entry can be valid. |
| Chronological overwrite warning | Review warning | Prevents old photos from silently reversing accepted newer state. |
| Ambiguous normalized value quarantine | Hard blocker for canonical apply, warning for preview | Normalized dates/ear labels/genotypes should not become accepted if raw evidence is marked ambiguous. |
| Rule-threshold snapshotting | Review warning metadata | Lets future reviewers understand which gestation/weaning/cage-density policy fired. |
| Per-field confidence calibration | Assistive hint / measurement | Track whether confidence bands match actual correction rates. |
| Cross-source agreement score | Assistive hint | OCR, AI draft, manual transcription, and legacy rows agreeing can lower review burden but should not replace source refs. |
| Review fatigue budget | Measurement | Track how many warnings per photo/export are generated so accuracy rules do not overwhelm operators. |
| Policy exception library | Review warning metadata | Repeated valid exceptions can become configurable policy rather than repeated dismissals. |

## Remaining Implementation Gaps

### P1: Ear-Label Review Can Still Resolve Without Updating Note Evidence

Boundary classification: review item and parsed/intermediate result.

Current evidence:

- `resolve_ear_label_correction()` returns `None` when `payload.ear_label_code` is missing.
- `resolve_review_item()` updates `review_queue.status = 'resolved'` before calling `resolve_ear_label_correction()`.
- The review-card list quick button excludes `Unlabeled numeric note needs review` but not `Ear label needs review`.
- The card-list quick button is outside `.review-actions`, so it can submit no `ear_label_code`.

Risk:

- The review queue can say an ear-label issue is resolved while `card_note_item_log.parsed_ear_label_*` remains unresolved. This weakens note-line continuity and mouse identity traceability.

Suggested implementation:

1. Add a backend guard in `resolve_review_item()` before updating `review_queue`:
   - if `existing["issue"] == "Ear label needs review"` and `payload.ear_label_code` is blank, return HTTP 400.
2. Update the card-list quick button condition in `static/index.html` to also exclude `Ear label needs review`.
3. Add tests in `tests/test_review_attention.py`:
   - resolving `review_ear_<note_item_id>` without `ear_label_code` is rejected;
   - the review remains open;
   - the note item remains reviewable.

### P1: Generic Quick Resolve Can Close Must-Review Items

Boundary classification: review item and canonical apply/export safety.

Current evidence:

- `reviewResolutionControls()` labels quick resolution for `attention_level === "must_review"` as `Accept after check`.
- The button is shown for every issue except a short hard-coded exclusion list.
- `/api/ui/focus-review` exposes `action_hint.safe_quick_resolve`, but the classic Review Queue detail controls still compute quick buttons from local issue-name checks rather than using that action contract.
- Backend `resolve_review_item()` does not reject generic quick resolution of `must_review` items by attention level or issue class.

Risk:

- A high-risk item can be removed from the review queue without an issue-specific correction or canonical-state reconciliation. This can unblock export readiness without proving the underlying biological or evidence conflict was resolved.

Suggested implementation:

1. Make backend resolution enforce the review action contract:
   - `must_review` items require an issue-specific payload or explicit supported resolution mode;
   - generic quick confirmation is allowed only for whitelisted `quick_check` issues.
2. Drive all Review Queue quick buttons from a shared `action_hint` contract instead of scattered issue-name exclusions.
3. Add tests that duplicate-active, ear-label, and animal-sheet validation `must_review` items cannot be closed through the generic quick path.

### P2: Ear-Label Review Note Join Relies On A Fragile ID Prefix

Boundary classification: review item and parsed/intermediate result.

Current evidence:

- `review_note_item_id()` recognizes `review_ear_`.
- `/api/review-items` SQL still checks `review.review_id LIKE 'review_ear_note_%'` for the note join.
- Normal generated IDs use `review_ear_<note_item_id>`, and current note item IDs usually start with `note_`, so the common case can still match.

Risk:

- The SQL join embeds a note-id naming assumption instead of matching the actual `review_ear_` prefix helper. If a future note item ID does not start with `note_`, ear-label review detail can lose the note-line anchor, card snapshot, and source evidence summary even when the note item exists.

Suggested implementation:

1. Change the `/api/review-items` join pattern to match normal `review_ear_%` IDs.
2. Add a regression test proving `review_ear_<note_item_id>` returns `note_item_id`, raw line text, card snapshot fields, and evidence preview.

### P1: Photo-Level Review Items Still Need Snapshot Fallback

Boundary classification: review item and parsed/intermediate result.

Current evidence:

- `/api/review-items` joins `card_snapshot` only through `review_note.card_snapshot_id`.
- Review IDs such as `review_<parse_id>` may have `parse_id` but no note anchor.

Risk:

- A photo-level review can show `source pending` or miss card snapshot context even though a card snapshot exists for the parse.

Suggested implementation:

1. Add a fallback join or post-query lookup for latest/current `card_snapshot` by `review.parse_id` when there is no note anchor.
2. Add a test for a `review_<parse_id>` item that returns snapshot fields and note summary without fabricating a note-line anchor.

### P1: Pending Parent Relationship Action Still Opens Generic Focus Review

Boundary classification: export/view read model and review item prompt.

Current evidence:

- `/api/ui/mouse-pedigree` returns an attention link labeled `Open Focus Review` when `pending_relationships > 0`, even if there are no open review items.
- `static/index.html` binds `.open-focus-review` to `setActiveView("review")`.
- Tests currently assert this generic Focus Review link.

Risk:

- The operator can click the only suggested action for missing parent evidence and land on an empty Review Queue. This makes lineage uncertainty visible but not actionable.

Suggested implementation:

1. Return a distinct action contract for pending lineage evidence, for example:
   - `mode: "relationship_evidence_missing"`;
   - `label: "Review parent evidence"`;
   - `target_view: "pedigree_correction"` or a real generated review item id.
2. Either create/open a real lineage review item or add a pedigree-specific correction/evidence panel.
3. Update UI and tests so pending parent action does not route to an empty Focus Review page.

### P1: Genotyping Evidence Refs Do Not Reject Mismatched Photo Evidence

Boundary classification: canonical structured state and source trace.

Current evidence:

- `genotyping_result_evidence_refs()` validates that `photo_evidence_id`, `source_photo_id`, and `source_record_id` exist.
- If both `photo_evidence_id` and `source_photo_id` are supplied, the function does not currently reject a mismatch where the evidence row belongs to a different photo.
- A stricter helper exists elsewhere for optional event evidence refs, but genotyping uses its own function.

Risk:

- A genotype result can be accepted with internally inconsistent traceability: the genotype row points to one source photo while the evidence row points to another.

Suggested implementation:

1. In `genotyping_result_evidence_refs()`, when both values are present, require `photo_evidence_item.source_photo_id == payload.source_photo_id`.
2. Add a negative test in `tests/test_genotyping_evidence_enforcement.py`.

### P2: CSV Export Manifest Contract Is Uneven

Boundary classification: export or view.

Current evidence:

- `docs/artifact_contracts/export_manifest.schema.json` allows `mouse_csv` and `genotyping_worklist_csv`.
- `/api/exports/mice.csv` and `/api/exports/genotyping-worklist.csv` still write `export_log` rows directly and do not create validation report/export manifest artifacts.

Risk:

- The schema implies manifest-backed provenance for CSV exports, but runtime behavior remains log-only. This creates uneven audit behavior between workbook exports and CSV exports.

Suggested implementation options:

1. Preferred: create validation report and export manifest artifacts for CSV endpoints too.
2. Alternative: narrow the schema and documentation to workbook exports only.

Tests:

- Add generated and blocked CSV export tests in `tests/test_artifact_workflow.py`.
- Assert `/api/export-log` exposes CSV manifest provenance if option 1 is chosen.

### P2: Active ApoM Labeling Rule Seeds Remain In `app/db.py`

Boundary classification: review item / workflow policy config.

Current evidence:

- `app/db.py` still defines `LABELING_RULE_SET_SEEDS` and `LABELING_RULE_EAR_SEQUENCE_SEEDS` with `ApoM Tg/Tg 2026-05-06`, `ApoM Tg/Tg`, `ApoM-tg`, and a specific rule date.

Risk:

- This can look like generic product behavior instead of pilot/example configuration. It conflicts with the project instruction to avoid hard-coded strain names, genotype categories, protocols, and date rules.

Suggested implementation:

1. Move example rule sets to `config/seeds/` or `fixtures/`.
2. Load seed config dynamically during DB initialization.
3. Keep tests that use ApoM-specific data explicitly scoped as fixture/pilot tests.
4. Add a test proving rule behavior comes from editable seed config, not a Python constant.

### P2: Review Workload Count Contract Is Still Not Fully Aligned With UI Plan

Boundary classification: export/view read model.

Current evidence:

- `docs/ui_ux_revision_plan_2026-05-13_ko.md` asks for `operator_workload_count`, `must_review_count`, `quick_check_count`, `open_review_total`, `hidden_diagnostic_count`, and `trace_only_count`.
- `static/index.html` topbar still uses broad `openReviewCount` values in several places.
- No current code search evidence found the planned `operator_workload_count` or `hidden_diagnostic_count` fields.

Risk:

- Hidden-default, trace-only, or diagnostic rows can inflate perceived operator workload.

Suggested implementation:

1. Add a dedicated review workload read model.
2. Use `operator_workload_count` for primary nav/topbar.
3. Show hidden/diagnostic/trace-only counts only in secondary diagnostics.
4. Add tests in `tests/test_review_attention.py`, `tests/test_low_fatigue_ui_contracts.py`, and `tests/test_operations_home.py`.

### P2: External AI Readiness Copy Still Understates Approval Requirement

Boundary classification: export/view status copy; external inference remains parsed/intermediate after approval.

Current evidence:

- `/api/health` returns `approval_required: True` for AI draft status.
- `static/index.html` topbar still renders `local + AI draft ready` when AI is available.
- The actual AI draft action does ask for explicit confirmation before external inference.

Risk:

- The topbar copy can imply external AI is ready to use without highlighting approval, even though payload minimization and explicit approval are required.

Suggested implementation:

1. Change the topbar status to `AI draft: approval required` when `approval_required` is true.
2. Keep local-only wording when no provider/key is available.
3. Add or update browser/DOM tests for the health status copy.

### P2: Source Photo Missing-State UI Is Not Clearly Implemented

Boundary classification: raw source and export/view UI state.

Current evidence:

- UI revision docs ask for explicit `Source photo unavailable` rendering.
- Code search did not find a durable `Source photo unavailable` label or obvious image `onerror` handling for source-photo previews.

Risk:

- Missing local source files can appear as broken image UI instead of a named evidence state.

Suggested implementation:

1. Add image load failure handling to source-photo preview components.
2. Render a user-facing state such as `Source photo unavailable in this local run`.
3. Preserve review blocking behavior for source-required reviews.
4. Add a UI contract test.

### P2: Domain-Specific Event Evidence Propagation Remains Incomplete

Boundary classification: canonical structured state and event/action history.

Current evidence:

- Generic high-risk event creation and timeline rendering can carry photo/note/evidence refs.
- The 2026-05-11 audit still identifies movement, mating, litter, offspring generation, and weaning service flows as often preserving only broad manual `source_record_id` refs.

Risk:

- Durable events can be source-backed but not linked to the most specific cage-card photo or note line that justified the event.

Suggested implementation:

1. Add a shared evidence-ref normalization helper for domain service payloads.
2. Start with movement and weaning, then mating/litter/offspring generation.
3. Add tests proving event details include `source_photo_id`, `source_note_item_id`, and/or `photo_evidence_id` when available.

### P2: Validation Rule Outcomes Are Not Yet Measurable Enough

Boundary classification: review item / sanitized accuracy report.

Current evidence:

- `validate_animal_sheet_litter_date_counts()` emits stable `validator_check_key` values.
- Review resolution already stores scoring audit and field-level outcome metadata.
- Private accuracy reporter infrastructure exists for sanitized aggregate metrics.
- There is no clear cross-cutting contract that maps each validation rule firing to later review outcomes such as false positive, corrected, policy exception, or dismissed with reason.

Risk:

- New accuracy rules can increase review burden without a feedback loop. The team may not know whether a rule catches real mistakes or mostly creates avoidable warnings.

Suggested implementation:

1. Add a small validation/review outcome contract:
   - `validator_check_key`;
   - `review_id` or artifact/check id;
   - `outcome`: `accepted`, `corrected`, `dismissed_with_reason`, `false_positive`, `policy_exception`;
   - sanitized rule metadata such as threshold snapshot or rule set id.
2. Aggregate these outcomes in a local/sanitized report.
3. Use the report to decide whether future rules should be hard blockers, warnings, or hints.

### P3: Draft Watermarked Animal Sheet Export Is Still An Open Product Decision

Boundary classification: export or view.

Current evidence:

- The external plan recommends final export blocking with optional `?draft=true` watermarked draft.
- Code search did not find an implemented `draft=true` animal-sheet workbook path or watermark text.

Risk:

- This is not a blocker if the product chooses strict final-export blocking only. It becomes a gap only if review-draft handoff is adopted.

Suggested decision:

- Default to strict HTTP 409 final export blocking.
- Implement watermarked draft only after explicit product approval and clear UI labeling.

### P3: Mobile Ergonomics And Review Queue First Viewport Need Browser Verification

Boundary classification: export/view UI.

Current evidence:

- Several UI tracker slices are merged, but the 2026-05-13 UI revision plan still lists mobile stacking and Review Queue first-viewport reorder as explicit slices.
- This review did not run browser screenshots.

Risk:

- A code search cannot prove the current rendered UI satisfies the low-fatigue/mobile criteria.

Suggested implementation:

1. Run browser checks for Review Queue first viewport and `390 x 844` mobile layout.
2. Add DOM/screenshot checks only after deciding the intended layout.

## Recommended Next Implementation Order

1. Fix ear-label review resolution safety.
   - Files: `app/main.py`, `static/index.html`, `tests/test_review_attention.py`.
   - Reason: prevents review queue from diverging from note-line evidence.

2. Block generic quick resolve for must-review items.
   - Files: `app/main.py`, `static/index.html`, `tests/test_review_attention.py`, `tests/test_low_fatigue_ui_contracts.py`.
   - Reason: prevents high-risk review blockers from being dismissed without issue-specific evidence or correction.

3. Fix `/api/review-items` evidence joins and snapshot fallback.
   - Files: `app/main.py`, `tests/test_review_attention.py`.
   - Reason: restores traceability for fragile ear-label joins and photo-level review items.

4. Fix pending parent action routing.
   - Files: `app/main.py`, `static/index.html`, `tests/test_low_fatigue_ui_contracts.py`.
   - Reason: turns visible lineage uncertainty into an actionable workflow.

5. Fix genotyping evidence mismatch validation.
   - Files: `app/main.py`, `tests/test_genotyping_evidence_enforcement.py`.
   - Reason: closes an audit hole in high-risk genotype evidence.

6. Add validation rule outcome telemetry.
   - Files: `app/main.py`, `tests/test_artifact_workflow.py`, possibly `scripts/report-private-accuracy.py`.
   - Reason: lets the team tune accuracy rules by observed false positives and correction outcomes instead of intuition.

7. Decide and implement CSV manifest contract.
   - Files: `app/main.py`, `docs/artifact_contracts/export_manifest.schema.json`, `tests/test_artifact_workflow.py`.
   - Reason: aligns schema promises with export runtime behavior.

8. Move active ApoM seed config out of `app/db.py`.
   - Files: `app/db.py`, `config/seeds/` or `fixtures/`, `tests/test_labeling_session_rules.py`, possibly `tests/test_hybrid_note_line_evaluator.py`.
   - Reason: removes project-specific seed coupling from active initialization.

9. Continue UI workload and safety copy cleanup.
   - Files: `static/index.html`, `app/main.py`, UI contract tests.
   - Reason: improves operator clarity without changing canonical state.

## Verification Commands For Future Slices

Use the narrowest relevant command first, then broaden:

```powershell
python -m pytest tests/test_review_attention.py -q
python -m pytest tests/test_low_fatigue_ui_contracts.py -q
python -m pytest tests/test_genotyping_evidence_enforcement.py -q
python -m pytest tests/test_artifact_workflow.py -q
npm test
npm run verify
```

For visible UI changes, add a browser check after the focused automated test passes.

## Notes For Future Agents

- Do not re-implement the animal sheet litter/date/count validator from the external plan; it already exists and is tested.
- Do not treat `blocked_review_items` as a Focus Review-only count; it is currently a combined final-export blocker count.
- Do not remove `source_record_id` as valid trace evidence; imported Excel/manual rows are valid source anchors even when photo/note evidence is absent.
- Keep all new validation output as `export or view` unless it creates an explicit `review item`.
- Keep raw photos, raw note lines, raw workbook cells, OCR text, and AI drafts separate from normalized/canonical values.
