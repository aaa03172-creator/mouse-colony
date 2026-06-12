# MouseDB Documentation Map

Layer classification: review item / export or view. Canonical: false.

This map organizes the repository's project documents. It does not define product behavior, database schema, API contracts, or lab workflow policy. When documents disagree, use this precedence:

1. `AGENTS.md` for agent safety, data-boundary, and worktree rules.
2. `final_mouse_colony_prd.md` for adopted product behavior and product principles.
3. Committed implementation and tests for the current executable contract.
4. Supporting design, review, planning, and evaluation documents listed below.

Default rule: if a document or artifact is ambiguous, treat it as non-canonical until `final_mouse_colony_prd.md`, `AGENTS.md`, or another adopted project document explicitly says otherwise.

## Start Here

| Need | Read |
| --- | --- |
| Product intent and adopted requirements | `final_mouse_colony_prd.md` |
| Agent and implementation guardrails | `AGENTS.md` |
| CLI usage, setup, tests, and runtime summary | `README.md` |
| Local pilot baseline and copied-data rule | `docs/pilot_readiness_baseline_2026-05-13.md` |
| UI/workflow design direction | `design.md` |
| Developer-only UI reference safety | `docs/ui_references.md` |
| UI/UX implementation tracking | `docs/ui_ux_implementation_tracker_2026-05-11.md` |
| Current state and mismatch summary | `docs/superpowers/specs/2026-05-09-current-state-design-implementation-review.md` |
| MVP acceptance status | `mvp_acceptance_matrix_ko.md` |
| First end-to-end implementation sequence | `mvp_vertical_slice_plan.md` |

## Canonical And Supporting Boundaries

| Document or area | Current role | Boundary |
| --- | --- | --- |
| `AGENTS.md` | Active project instructions | Defines safety and workflow rules for agents, including data-layer classification and Git hygiene. |
| `final_mouse_colony_prd.md` | Adopted product PRD | Primary product reference. It lists adopted supporting documents but remains the behavior anchor. |
| `README.md` | Operator/developer entry point | Runtime usage, commands, tests, and compact architecture summary. |
| `tests/` and verification scripts | Executable contract | Current behavior evidence. Tests should be checked when docs and implementation differ. |
| `config/breeding_rules.json` | Review item / workflow policy config | Non-canonical breeding thresholds and strain-scoped assumptions loaded by runtime rule helpers. |
| `docs/artifact_contracts/*.schema.json` | Export/view artifact contracts | Schema references for proposed changesets, validation reports, and export manifests. |
| `evals/cage_card_skill_gym/` | Non-canonical safety eval layer | Review/test probes only; does not define runtime behavior or schema. |

## Design And Product Notes

| File | Layer | Use |
| --- | --- | --- |
| `design.md` | design guidance / non-canonical product documentation | UI and workflow direction for evidence-first review, correction, and exports. |
| `docs/ui_references.md` | design guidance / non-canonical reference policy | Developer-only UI reference research rules, including Lazyweb fake-data-only usage and local MCP/token safety. |
| `docs/ui_interaction_visual_feedback_direction_2026-05-11_ko.md` | design guidance / non-canonical product documentation | Interaction, visual feedback, progress, icon/chip, and state-cue guidance for reducing text-heavy UI without weakening evidence-first boundaries. |
| `docs/ui_ux_implementation_tracker_2026-05-11.md` | design guidance / non-canonical project tracking documentation | Tracks merged UI/UX implementation slices, verification evidence, remaining gaps, and recommended next slices. |
| `docs/ui_ux_review_2026-05-13_ko.md` | review item / non-canonical UI review documentation | Current rendered UI/UX review across workflow, evidence, review fatigue, export safety, accessibility, privacy, and documentation-alignment perspectives. |
| `docs/ui_ux_revision_plan_2026-05-13_ko.md` | implementation planning / non-canonical UI plan | Scoped follow-up plan for fixing review counters, Review Queue hierarchy, internal ID exposure, export download hierarchy, source-photo missing states, AI copy, and mobile ergonomics. |
| `mouse_strain_colony_system_design_ko.md` | design guidance / non-canonical project document | Broader strain registry and colony tracking design. |
| `reference_adoption_notes.md` | design guidance / non-canonical reference adoption note | External references filtered for this project. |
| `mouse_open_source_research_adoption_ko.md` | design guidance / non-canonical reference adoption note | Open-source mouse colony research adoption notes. |
| `wet_lab_operational_review_ko.md` | operational review / non-canonical project note | Wet-lab workflow fit and operational risk review. |
| `ui_image_usage_improvement_plan_ko.md` | design guidance / non-canonical UI note | UI image usage cleanup guidance. |
| `ui_reference_comparison_plan_ko.md` | export or view / non-canonical planning document | Visual reference comparison and redesign notes. |

## Evidence, Review, And Workflow Planning

| File | Layer | Use |
| --- | --- | --- |
| `mvp_vertical_slice_plan.md` | implementation planning / non-canonical project note | First source photo to review to canonical candidate to export slice. |
| `docs/pilot_readiness_baseline_2026-05-13.md` | export or view / non-canonical baseline record | Captures the local pilot baseline, verification command set, runtime artifact classification, and copied/synthetic-data rule. |
| `docs/real_photo_pilot_protocol_2026-05-13.md` | review item / non-canonical pilot protocol | Defines the copied-photo pilot dataset size, per-photo labels, expected values, and local-only safety rules for real-photo evaluation. |
| `docs/private_real_photo_pilot_accuracy_pack_2026-05-14.md` | review item / non-canonical pilot and evaluation runbook | Defines the 20-30 copied-photo private manifest, accuracy-evaluation reuse, sanitized public metrics, failure taxonomy, reviewer workload, and go/no-go gates. |
| `docs/apom_tgtg_private_accuracy_regression_runbook_2026-05-17.md` | review item / local operations runbook | Describes rerunning the ApoM tgtg 17-photo private field-level accuracy regression with the gated runner, including artifact boundaries, pass criteria, failure handling, and leak checks. |
| `docs/copied_photo_pilot_go_no_go_2026-05-14.md` | review item / pilot readiness guide | Defines hard and soft go/no-go gates for the 20-30 copied-photo pilot, including manifest coverage, private data containment, traceability, and backup/restore evidence. |
| `docs/local_backup_restore_2026-05-13.md` | export or view / local operational procedure | Documents local pilot backup and restore scripts for SQLite data, uploaded photos, exports, and generated artifacts. |
| `docs/pilot_operator_checklist_2026-05-13.md` | review item / local pilot checklist | One-page operator checklist for copied-photo setup, review, canonical apply, export readiness, stop conditions, and backup. |
| `docs/manual_pilot_walkthrough_2026-05-13.md` | review item / local pilot procedure | Step-by-step pilot run from `start.bat` through upload, extraction decisions, review, canonical apply, XLSX export, and post-session inspection. |
| `docs/pilot_run_log_template_2026-05-13.md` | review item / pilot run log template | Sanitized dry-run log template for photo counts, reviews, corrections, candidates, exports, timing, friction points, and verification. |
| `docs/five_photo_dry_run_manifest_guide_2026-05-13.md` | review item / local pilot guide | Private-manifest guide and 5-photo JSON template for the first copied-photo dry run; private paths and photos remain outside Git. |
| `docs/pilot_runs/README.md` | review item / pilot run log index | Explains where sanitized pilot run logs go and what private artifacts must not be committed. |
| `docs/pilot_runs/2026-05-14-copied-photo-pilot-readiness-example.md` | review item / sanitized example pilot run log | Public-safe generated example from `config/copied_photo_pilot_readiness_manifest.example.json`; contains coverage counts, go/no-go evidence labels, and no private source paths. |
| `docs/cage_card_photo_pipeline_implementation_baseline_ko.md` | implementation baseline / non-canonical project documentation | Recommended implementation baseline for cage-card photo processing boundaries, review gates, canonical apply, and export provenance. |
| `docs/cage_card_photo_pipeline_implementation_audit_2026-05-11.md` | implementation audit / review item | Current implementation audit against the cage-card photo pipeline baseline, with evidence-linking gaps and next slice recommendation. |
| `docs/cage_card_photo_upload_flow_doublecheck_audit_2026-05-11_ko.md` | implementation audit / review item | Code-level doublecheck of the current photo upload, extraction, review, correction, canonical apply, and export flow boundaries. |
| `docs/cage_card_photo_accuracy_evaluation_test_plan_2026-05-11_ko.md` | implementation verification plan / review item | Evaluation criteria and task-slice test plan for cage-card photo parsing, correction, canonical apply safety, and Excel export provenance. |
| `docs/cage_card_photo_accuracy_task_slices_2026-05-11_ko.md` | implementation planning / review item | Task-slice plan for closing photo accuracy and evidence-propagation gaps identified by the upload-flow audit and evaluation plan. |
| `docs/cage_card_photo_accuracy_doublecheck_summary_2026-05-11_ko.md` | implementation verification summary / review item | Concise doublecheck summary of coverage, test priority, manual checks, residual risks, and verification results for the cage-card photo accuracy test plan. |
| `mvp_acceptance_matrix_ko.md` | implementation verification / non-canonical project note | MVP acceptance status matrix. |
| `roi_card_extraction_plan_ko.md` | implementation planning / non-canonical project note | ROI-based cage-card extraction planning. |
| `pvm_photo_evidence_ledger_adoption_ko.md` | implementation planning / non-canonical project note | Photo Evidence Ledger adoption review. |
| `review_burden_reduction_plan_ko.md` | workflow planning / non-canonical project note | Focus Review, quick check, and trace-only uncertainty planning. |
| `review_burden_reduction_progress_ko.md` | progress review / non-canonical project note | Progress notes for review burden reduction. |
| `selective_normalization_controls_plan_ko.md` | workflow planning / non-canonical project note | Raw/normalized field controls and bounded selection planning. |
| `photo_e2e_validation_plan_ko.md` | verification planning / non-canonical project note | Photo end-to-end validation plan. |

## Architecture And Acceleration Reviews

| File | Layer | Use |
| --- | --- | --- |
| `mousedb_cli_first_review_ko.md` | design review / non-canonical project note | CLI-first MouseDB boundary and integration review. |
| `docs/mouse_db_assistant_integration_review_2026-05-11.md` | review item / non-canonical project documentation | MouseDB-specific adaptation of external assistant/API/MCP integration proposals, preserving MouseDB as the colony truth owner. |
| `docs/mouse_data_utilization_implementation_review_2026-05-12_ko.md` | implementation/product review / review item | Reviews how extracted cage-card data can support mouse timelines, operational next actions, and future assistant/API/MCP wrappers against current implementation. |
| `docs/code_review_backlog_2026-05-19_ko.md` | review item / non-canonical implementation review | Records current branch code-review findings and a batching map for later cross-branch review. |
| `docs/implementation_gap_review_2026-05-26_ko.md` | review item / non-canonical implementation review | Consolidates current implementation gaps and recommended next slices after animal-sheet validation work. |
| `mousedb_open_design_artifact_workflow_review_ko.md` | implementation planning / non-canonical project note | Artifact lifecycle, preview-before-commit, validation report, and export provenance review. |
| `open_source_acceleration_candidates_ko.md` | design guidance / non-canonical technical reference note | Possible implementation accelerators. |
| `open_source_acceleration_doublecheck_ko.md` | design guidance / non-canonical technical reference note | License, fit, and MVP-risk double-check for accelerators. |
| `docs/ctx2skill_lite_adoption_review.md` | implementation planning / non-canonical project note | Small evaluation-loop subset adoption review. |

## Superpowers Specs

These are non-canonical specs or reviews unless explicitly adopted elsewhere.

| File | Layer | Use |
| --- | --- | --- |
| `docs/superpowers/specs/2026-05-06-labeling-session-rules-design.md` | parsed/intermediate workflow policy design | Day-specific labeling rule parsing design. |
| `docs/superpowers/specs/2026-05-09-breeding-operations-rules-review.md` | non-canonical implementation planning note | Breeding operations rule review. |
| `docs/superpowers/specs/2026-05-09-breeding-rule-implementation-contract.md` | parsed/intermediate result and review item implementation contract | Breeding rule parser/review contract. |
| `docs/superpowers/specs/2026-05-09-current-state-design-implementation-review.md` | review item / non-canonical project documentation | Current state, design alignment, and mismatch review. |
| `docs/superpowers/specs/2026-05-09-labeling-rule-ui-design.md` | implementation planning / non-canonical project note | Labeling rule UI design. |
| `docs/superpowers/specs/2026-05-09-low-fatigue-colony-ui-design.md` | design guidance / non-canonical product documentation | Low-fatigue UI design direction. |
| `docs/superpowers/specs/2026-05-15-hybrid-note-line-extraction-evaluator-design.md` | parsed/intermediate workflow policy design | Hybrid local OCR, AI draft, ROI, and rule-context evaluator design for mouse ID and note-line extraction accuracy. |
| `docs/superpowers/specs/2026-05-19-animal-sheet-litter-date-count-validation-review_ko.md` | review item / non-canonical implementation design | Animal sheet pup count, separation count, and litter/date validation review; recommends blocked export/review routing instead of silent overwrite. |

## Superpowers Plans

Plans are execution guides. They are not canonical product truth after implementation moves on.

| File | Status signal | Use |
| --- | --- | --- |
| `docs/superpowers/plans/2026-05-06-labeling-session-rules.md` | implementation plan | Labeling session rules task plan. |
| `docs/superpowers/plans/2026-05-09-cage-card-skill-gym.md` | implementation plan | Cage Card Skill Gym task plan. |
| `docs/superpowers/plans/2026-05-09-evidence-first-alignment-stabilization.md` | implementation plan | Evidence-first alignment stabilization plan. |
| `docs/superpowers/plans/2026-05-09-labeling-rule-ui.md` | implementation plan | Labeling rule UI plan. |
| `docs/superpowers/plans/2026-05-09-low-fatigue-colony-ui.md` | implementation plan | Low-fatigue colony UI plan. |
| `docs/superpowers/plans/2026-05-09-mouse-pedigree-lineage-mvp.md` | implementation plan | Mouse pedigree / lineage read model MVP plan. |
| `docs/superpowers/plans/2026-05-09-strain-gene-allele-registry.md` | implementation plan | Strain/gene/allele registry plan. |

## Known Documentation Mismatches

| Issue | Impact | Suggested next action |
| --- | --- | --- |
| Several Korean documents display mojibake in headings or body text in this checkout. | Humans cannot reliably review those sections even if structural verifiers pass. | Repair or rewrite the affected files from a known-good UTF-8 source. Start with `mvp_acceptance_matrix_ko.md`, `photo_e2e_validation_plan_ko.md`, `review_burden_reduction_plan_ko.md`, `review_burden_reduction_progress_ko.md`, `selective_normalization_controls_plan_ko.md`, `ui_reference_comparison_plan_ko.md`, `mouse_strain_colony_system_design_ko.md`, and `mousedb_cli_first_review_ko.md`. |
| Branch and commit snapshot in `docs/superpowers/specs/2026-05-09-current-state-design-implementation-review.md` is historical. | It may look like the current branch/status, but it is a dated review note. | Keep it as a snapshot; update only when intentionally writing a new dated state review. |
| Root-level planning notes are mixed with entry-point docs. | New contributors may confuse old plans with current behavior. | Keep root files stable for now, but use this map as the navigation entry point before moving or archiving files. |
| Implementation plans can remain checked even after code has changed. | A checked task list can be mistaken for current behavior. | Treat plans as historical execution records and verify against tests/source before relying on them. |

## Cleanup Policy

Before moving, deleting, or rewriting documentation:

1. Classify the document's layer.
2. Check whether `final_mouse_colony_prd.md` references it as adopted support.
3. Preserve traceability if the document records a design decision, review finding, or verification result.
4. Prefer adding a status note over deleting historical context.
5. If a document is mojibake-corrupted, repair from a known-good source or rewrite explicitly; do not silently "summarize away" information that may have been lost.
