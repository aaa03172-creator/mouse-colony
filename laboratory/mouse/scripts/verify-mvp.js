const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const pagePath = path.join(root, "index.html");
const staticPagePath = path.join(root, "static", "index.html");
const designPath = path.join(root, "design.md");
const fixturePath = path.join(root, "fixtures", "sample_parse_results.json");
const distributionFixturePath = path.join(root, "fixtures", "sample_distribution_import.json");
const distributionParserPath = path.join(root, "scripts", "parse_distribution_workbook.py");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function fileUrl(filePath) {
  return `file:///${filePath.replace(/\\/g, "/")}`;
}

function makeTinyPng() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "mouse-lims-"));
  const pngPath = path.join(tempDir, "card-photo.png");
  const pngBase64 =
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAGklEQVR4nGNk+M+ABzAOMoCJYQ3DhhQAAO8SARKa6CdmAAAAAElFTkSuQmCC";
  fs.writeFileSync(pngPath, Buffer.from(pngBase64, "base64"));
  return pngPath;
}

function pythonExecutable() {
  const bundled = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "python",
    process.platform === "win32" ? "python.exe" : "bin/python"
  );
  return fs.existsSync(bundled) ? bundled : "python";
}

function runPython(args, options = {}) {
  const result = spawnSync(pythonExecutable(), args, {
    cwd: root,
    encoding: "utf8",
    ...options
  });
  if (result.status !== 0) {
    throw new Error(`Python command failed: ${args.join(" ")}\n${result.stdout}\n${result.stderr}`);
  }
  return result;
}

function verifyDistributionParser() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "mouse-distribution-"));
  const workbookPath = path.join(tempDir, "distribution.xlsx");
  const parsedPath = path.join(tempDir, "distribution-parsed.json");
  const workbookScript = `
from pathlib import Path
from openpyxl import Workbook

workbook_path = Path(${JSON.stringify(workbookPath)})

wb = Workbook()
ws = wb.active
ws.title = "Mating"
ws.append(["Vet Med", "mating type", "Cage count", "mating cage count", "", "Medicine", "mating type", "Cage count", "mating cage count"])
ws.append(["Jang S.", "ApoMtg/tg", "6", "", "", "Kim S.", "GFAP Cre; S1PR1 fl/fl", "8", ""])
ws.append([None, "TAStg/+; TPMtg/+; ApoMtg/+", "16", "16", "", None, "Lyz2-cre+/-; S565Afl/fl", "8", ""])
wb.save(workbook_path)
`;
  runPython(["-c", workbookScript]);
  runPython([distributionParserPath, workbookPath, "--sheet", "Mating", "--out", parsedPath]);

  const parsed = JSON.parse(fs.readFileSync(parsedPath, "utf8"));
  assert(parsed.layer === "parsed or intermediate result", "Parsed distribution output must stay non-canonical.");
  assert(parsed.sheet_name === "Mating", "Distribution parser used the wrong sheet.");
  assert(parsed.detected_blocks.length === 2, "Distribution parser did not detect both column blocks.");
  assert(parsed.rows.some((row) => row.mating_type_raw === "ApoMtg/tg"), "Distribution parser missed the ApoMtg/tg row.");
  assert(
    parsed.rows.some((row) => row.responsible_person_raw === "Jang S." && row.mating_type_raw.startsWith("TAStg")),
    "Distribution parser did not carry down merged/person context."
  );
  assert(
    parsed.rows.every((row) => row.source_row_number && row.source_cells),
    "Distribution parser rows must preserve source row and cell traceability."
  );
  return parsedPath;
}

async function main() {
  assert(fs.existsSync(pagePath), "index.html is missing.");
  assert(fs.existsSync(staticPagePath), "static/index.html is missing.");
  assert(fs.existsSync(designPath), "design.md is missing.");
  assert(fs.existsSync(fixturePath), "fixtures/sample_parse_results.json is missing.");
  assert(fs.existsSync(distributionFixturePath), "fixtures/sample_distribution_import.json is missing.");
  assert(fs.existsSync(distributionParserPath), "Distribution workbook parser is missing.");
  const generatedDistributionPath = verifyDistributionParser();

  const html = fs.readFileSync(pagePath, "utf8");
  const staticHtml = fs.readFileSync(staticPagePath, "utf8");
  const design = fs.readFileSync(designPath, "utf8");
  assert(
    design.includes("awesome-design-md") &&
      design.includes("Airtable") &&
      design.includes("Linear") &&
      design.includes("Layer classification: design guidance / non-canonical product documentation"),
    "design.md should document the non-canonical awesome-design-md adaptation sources."
  );
  assert(
    html.includes("--accent-linear: #5e6ad2") &&
      staticHtml.includes("--accent-linear: #5e6ad2") &&
      html.includes("--focus-ring: rgba(94, 106, 210, 0.28)") &&
      staticHtml.includes("--focus-ring: rgba(94, 106, 210, 0.28)") &&
      staticHtml.includes("--selected-bg: var(--accent-soft)") &&
      staticHtml.includes("--disabled-bg: #f3f4f6") &&
      staticHtml.includes("--ready-bg: #f0faf6") &&
      staticHtml.includes("--blocked-bg: #fff8f7") &&
      staticHtml.includes("--processing-bg: #e8eefc") &&
      staticHtml.includes("border-color: var(--selected-line)") &&
      staticHtml.includes("background: var(--ready-bg)") &&
      staticHtml.includes("background: var(--blocked-bg)") &&
      html.includes("DESIGN.md-inspired") &&
      staticHtml.includes("DESIGN.md-inspired"),
    "Local UI should expose Airtable/Linear-inspired semantic design tokens without copying brand surfaces."
  );
  assert(
    staticHtml.includes("attention-must-review") &&
      staticHtml.includes("attention-quick-check") &&
      staticHtml.includes("attention-trace-only") &&
      staticHtml.includes("Needs quick confirmation") &&
      staticHtml.includes("Trace only") &&
      staticHtml.includes("data-attention-level") &&
      staticHtml.includes("review-card-symbol") &&
      staticHtml.includes("review-card-consequence") &&
      staticHtml.includes("Blocks export until reviewed."),
    "Focus Review should expose low-fatigue attention cues with text, symbols, consequence copy, structure, and stable classes."
  );
  assert(
    staticHtml.includes("/api/ui/focus-review") &&
      staticHtml.includes("renderFocusReviewReadModel") &&
      staticHtml.includes("Focus Review unavailable") &&
      staticHtml.includes("fabricated_records") &&
      staticHtml.includes("function stateMessageHtml(kind, title, detail = \"\")") &&
      staticHtml.includes("function emptyTableRow(title, detail = \"\", colspan = 1)") &&
      staticHtml.includes('class="state-message"') &&
      staticHtml.includes('data-state-kind="loading"'),
    "Focus Review UI should consume the read-only read model and render honest loading, empty, or error states without fabricated records."
  );
  assert(
    staticHtml.includes("No upload batches yet") &&
      staticHtml.includes("No photos yet") &&
      staticHtml.includes("No canonical candidate drafts yet") &&
      staticHtml.includes("No separation preview rows yet") &&
      staticHtml.includes("No exports generated yet") &&
      staticHtml.includes('class="table-empty-row"'),
    "Table-only empty states should use consistent non-fabricated state-message rows for photo, review, candidate, and export surfaces."
  );
  assert(
    staticHtml.includes("No review roles configured") &&
      staticHtml.includes("No assigned strains yet") &&
      staticHtml.includes("No corrections yet") &&
      staticHtml.includes("No mouse selected") &&
      staticHtml.includes("No genotype statuses configured") &&
      staticHtml.includes("function auditTraceRow(item)") &&
      staticHtml.includes("Source record") &&
      staticHtml.includes("Evidence pending"),
    "Lower-priority settings, reference, and audit tables should reuse state-message empty rows and badge trace evidence."
  );
  assert(
    staticHtml.includes("/api/ui/colony-state") &&
      staticHtml.includes("renderColonyStateReadModel") &&
      staticHtml.includes("Colony State unavailable") &&
      staticHtml.includes("active_card_snapshots"),
    "Colony State UI should consume the read-only read model and render honest unavailable or empty states without fabricated records."
  );
  assert(
    staticHtml.includes("/api/ui/colony-schedule") &&
      staticHtml.includes("renderColonyScheduleReadModel") &&
      staticHtml.includes("Colony Schedule unavailable") &&
      staticHtml.includes("calendar_mirror"),
    "Colony Schedule UI should consume the read-only read model and keep calendar sync as a non-canonical mirror."
  );
  assert(
    staticHtml.includes("/api/ui/mouse-timeline") &&
      staticHtml.includes("renderMouseTimelineReadModel") &&
      staticHtml.includes("Mouse Timeline unavailable") &&
      staticHtml.includes("accepted_events"),
    "Mouse Timeline UI should consume the read-only read model and render only accepted events by default."
  );
  assert(
    staticHtml.includes("/api/ui/mouse-pedigree") &&
      staticHtml.includes("renderMousePedigreeReadModel") &&
      staticHtml.includes("Pedigree / Lineage unavailable") &&
      staticHtml.includes("pending_relationships"),
    "Mouse Pedigree UI should consume the read-only read model without inferring uncertain relationships."
  );
  assert(
    staticHtml.includes("/api/ui/evidence-ledger") &&
      staticHtml.includes("renderEvidenceLedgerReadModel") &&
      staticHtml.includes("Evidence Ledger unavailable") &&
      staticHtml.includes("observed_raw_text"),
    "Evidence Ledger UI should consume the read-only read model and separate observed, OCR, and interpreted evidence."
  );
  assert(
    staticHtml.includes("function evidenceBadge(kind, label = \"\")") &&
      staticHtml.includes(".evidence-badge.source-photo") &&
      staticHtml.includes("Source photo") &&
      staticHtml.includes("OCR text") &&
      staticHtml.includes("Note line") &&
      staticHtml.includes("Review item") &&
      staticHtml.includes("Export manifest") &&
      staticHtml.includes("Validation report"),
    "Evidence type badges should be available for source photo, OCR, note line, review, and export artifact evidence."
  );
  assert(
    staticHtml.includes("const cropImageUrl = escapeHtml(crop.image_url || \"\")") &&
      staticHtml.includes('src="${cropImageUrl}&t=${Date.now()}"'),
    "ROI crop image URLs should be escaped before insertion into innerHTML attributes."
  );
  assert(
    staticHtml.includes("function setFinalExportActionState(preview)") &&
      staticHtml.includes("exportDisabledReason") &&
      staticHtml.includes("button.disabled = !buttonReady") &&
      staticHtml.includes("preview.mouse_csv_ready") &&
      staticHtml.includes("preview.separation_ready") &&
      staticHtml.includes("preview.animal_sheet_ready") &&
      staticHtml.includes('blocked_by_litter_conflict: "Litter conflict"') &&
      staticHtml.includes("Animal sheet review required") &&
      staticHtml.includes('button.setAttribute("aria-describedby", "exportDisabledReason")') &&
      staticHtml.includes("accepted source-backed export row(s) are ready"),
    "Export Center final actions should expose disabled reasons, accessibility links, and empty accepted-row guidance."
  );
  assert(
    html.includes("function workbookPreviewRowState(rowIndex, headerCount, row, model)") &&
      html.includes("Preview only") &&
      html.includes("Source evidence") &&
      staticHtml.includes("function exportPreviewRowStateChips(item, previewReady)") &&
      staticHtml.includes("Preview state") &&
      staticHtml.includes("row-state-chip") &&
      staticHtml.includes("Trace linked"),
    "Workbook preview rows should expose text-backed row-state chips without changing export schemas."
  );
  assert(
    staticHtml.includes("function anchorId(prefix, value)") &&
      staticHtml.includes("data-evidence-anchor-link") &&
      staticHtml.includes("function exportEvidenceAnchorLinks(item)") &&
      staticHtml.includes("function openEvidenceAnchor(targetId, view)") &&
      staticHtml.includes("anchor-highlight") &&
      staticHtml.includes('id="${anchorId("note"') &&
      staticHtml.includes('id="${anchorId("evidence"'),
    "Evidence rows should expose stable row anchors and non-mutating deep-link helpers for note, ledger, and export preview surfaces."
  );
  assert(
    staticHtml.includes("function exportBlockerReviewButton(item)") &&
      staticHtml.includes("open-export-blocker-review") &&
      staticHtml.includes("function exportBlockerEvidenceActions(item)") &&
      staticHtml.includes("open-export-blocker-photo") &&
      staticHtml.includes("open-export-blocker-note") &&
      staticHtml.includes("open-export-blocker-artifact") &&
      staticHtml.includes("Opened export blocker review") &&
      staticHtml.includes("selectedReviewId = button.dataset.reviewId"),
    "Export Center blockers should link directly to responsible Focus Review items or source evidence without editing export preview data."
  );
  assert(
    staticHtml.includes('id="extractionProgressBar" role="progressbar"') &&
      staticHtml.includes("extractionProgressPercent") &&
      staticHtml.includes('bar.setAttribute("aria-valuenow", String(percent))') &&
      staticHtml.includes("function progressStatusClass(status = \"\")") &&
      staticHtml.includes("progress-extracting") &&
      staticHtml.includes("progress-uploading"),
    "Upload and extraction progress should expose an accessible progressbar, visible percentage, and state-specific visual cues."
  );
  assert(
    staticHtml.includes("button:focus-visible") &&
      staticHtml.includes(".review-card:focus-within") &&
      staticHtml.includes('.photo-worklist-card[aria-current="true"]') &&
      staticHtml.includes('card.setAttribute("aria-current"') &&
      staticHtml.includes("Open Focus Review item") &&
      staticHtml.includes("Preview ${escapeHtml(label)} artifact"),
    "High-traffic review, photo, blocker, and artifact controls should expose focus-visible styles, current-state attributes, and specific accessible names."
  );
  assert(
    staticHtml.includes('id="reviewFollowThrough"') &&
      staticHtml.includes("function setReviewFollowThroughMessage(resolvedItem, nextItem)") &&
      staticHtml.includes("function renderReviewFollowThroughStatus(visibleReviews)") &&
      staticHtml.includes("reviewSourceContextText") &&
      staticHtml.includes('stateMessageHtml("success"'),
    "Review resolution should expose follow-through status with next-item and source-evidence context."
  );
  assert(
    staticHtml.includes('id="reviewCompletionSummary"') &&
      staticHtml.includes("function renderReviewCompletionSummary(actionLog, reviews)") &&
      staticHtml.includes("function reviewCompletionRows(actionLog, reviews)") &&
      staticHtml.includes("Review action summary") &&
      staticHtml.includes("By source evidence") &&
      staticHtml.includes("Action log only; canonical writes still require an explicit apply step."),
    "Review queue should summarize recent completed review actions by evidence, role, and blocker type without implying canonical writes."
  );
  assert(
    staticHtml.includes("function photoStageProgress(photo, compact = false)") &&
      staticHtml.includes("Photo stage progress") &&
      staticHtml.includes("Parse/OCR") &&
      staticHtml.includes("Accepted/Held") &&
      staticHtml.includes(".photo-stage-step.blocked") &&
      staticHtml.includes("photoStageProgress(photo, true)"),
    "Photo Review should expose per-photo stage progress without implying early canonical acceptance."
  );
  const scriptMatch = html.match(/<script>([\s\S]*)<\/script>/);
  assert(scriptMatch, "index.html must contain an inline script.");
  new Function(scriptMatch[1]);

  const referencedIds = [...scriptMatch[1].matchAll(/getElementById\("([^"]+)"\)/g)].map((match) => match[1]);
  const missingIds = [...new Set(referencedIds)].filter((id) => !html.includes(`id="${id}"`));
  assert(!missingIds.length, `Missing DOM ids: ${missingIds.join(", ")}`);

  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  const distributionFixture = JSON.parse(fs.readFileSync(distributionFixturePath, "utf8"));
  assert(fixture.layer === "parsed or intermediate result", "Fixture layer must stay non-canonical.");
  assert(Array.isArray(fixture.records) && fixture.records.length >= 3, "Fixture must contain at least three parse records.");
  assert(
    fixture.records.some((record) => record.status === "review") &&
      fixture.records.some((record) => record.status === "conflict") &&
      fixture.records.some((record) => record.status === "auto"),
    "Fixture should cover review, conflict, and auto-filled states."
  );
  assert(distributionFixture.layer === "parsed or intermediate result", "Distribution fixture must stay non-canonical.");
  assert(Array.isArray(distributionFixture.rows) && distributionFixture.rows.length >= 3, "Distribution fixture should contain parsed assignment rows.");

  const uploadPath = makeTinyPng();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  const browserErrors = [];

  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });

  await page.goto(fileUrl(pagePath));
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector("#inboxRows tr");

  const staticPage = await context.newPage();
  const staticPageErrors = [];
  staticPage.on("pageerror", (error) => staticPageErrors.push(error.message));
  staticPage.on("console", (message) => {
    if (message.type() === "error") {
      const text = message.text();
      if (!text.includes("net::ERR_FILE_NOT_FOUND")) {
        staticPageErrors.push(text);
      }
    }
  });
  await staticPage.addInitScript(() => {
    const reviewItem = {
      review_id: "review_focus_static_contract",
      status: "open",
      issue: "Count mismatch",
      severity: "High",
      attention_level: "must_review",
      priority: "high",
      assigned_role: "Colony Reviewer",
      review_reason: "Parsed count conflicts with note-line evidence.",
      evidence_preview: "MT401 R' / MT402 L'",
      photo_id: "photo_focus_static_contract",
      original_filename: "focus-card.png",
      note_line_count: 2
    };
    const evidenceWarning = {
      issue: "Genotype readiness warning",
      severity: "Medium",
      priority: "medium",
      review_reason: "Genotype readiness needs source evidence before export.",
      evidence_preview: "Check source photo and note evidence before final workbook export.",
      photo_id: "photo_warning_static",
      original_filename: "warning-card.png",
      note_item_id: "note_warning_static",
      artifact_path: "mousedb_artifacts/validation/static-warning.json"
    };
    const warningPhoto = {
      photo_id: "photo_warning_static",
      original_filename: "warning-card.png",
      batch_label: "Static warning batch",
      status: "stored",
      next_action: "resolve_reviews",
      uploaded_at: "2026-05-09T12:00:00Z",
      transcription_count: 1,
      note_line_count: 1,
      total_review_count: 0,
      open_review_count: 0,
      canonical_candidate_count: 0,
      applied_candidate_count: 0
    };
    const responses = {
      "/api/health": { ai_draft: { available: false } },
      "/api/assigned-strains": [],
      "/api/strains": [],
      "/api/source-records": [],
      "/api/corrections": [],
      "/api/canonical-candidates": [],
      "/api/distribution-imports": [],
      "/api/legacy-workbook-imports": [],
      "/api/evidence-reconciliation": {},
      "/api/evidence-comparison": { comparisons: [] },
      "/api/photo-review-workbench": { pending_transcription_count: 0, rows: [warningPhoto], batches: [] },
      "/api/upload-batches": [],
      "/api/photos": [warningPhoto],
      "/api/review-items": [reviewItem],
      "/api/ui/focus-review": {
        source_layer: "export or view",
        page_question: "What needs my decision today?",
        workload_summary: { must_review: 1, quick_check: 0 },
        cards: [
          {
            parse_id: "parse_focus_static_contract",
            source_photo: { photo_id: "photo_focus_static_contract", filename: "focus-card.png" },
            review_count: 1,
            review_items: [
              {
                review_id: reviewItem.review_id,
                issue_label: "Count mismatch",
                attention_level: "must_review",
                action_hint: {
                  source_layer: "export or view",
                  mode: "manual_review_required",
                  primary_label: "Inspect source evidence",
                  requires_note: true,
                  requires_source_photo: true,
                  safe_quick_resolve: false
                }
              }
            ],
            mouse_rows: [{ mouse_id: "MT401", raw_line: "MT401 R'" }]
          }
        ],
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/ui/colony-state": {
        source_layer: "export or view",
        page_question: "What is active now?",
        summary: {
          active_mice: 2,
          active_card_snapshots: 1,
          active_matings: 0,
          active_litters: 0,
          must_review: 1,
          quick_check: 0
        },
        active_card_snapshots: [
          {
            card_snapshot_id: "card_colony_static",
            parse_id: "parse_colony_static",
            card_type: "Separated",
            card_id_raw: "C-12",
            matched_strain_text: "C57BL/6J",
            mouse_count: 2,
            source_photo: { photo_id: "photo_colony_static", filename: "colony-card.png", source_photo_role: "primary_evidence" },
            collapsed_sections: { mice: 2, note_lines: 2, review_blockers: 1, source_evidence: 1 }
          }
        ],
        strain_summary: [{ strain: "C57BL/6J", active_mice: 2 }],
        status_summary: [{ status: "active", mouse_count: 2 }],
        attention_links: [{ label: "Focus Review", target_path: "/api/ui/focus-review", must_review: 1, quick_check: 0 }],
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/ui/colony-schedule": {
        source_layer: "export or view",
        page_question: "What needs doing next?",
        as_of: "2026-05-09",
        rule_set: {
          rule_set_id: "breeding_rule_default_20260509",
          display_name: "Default breeding operation review rules",
          source_layer: "parsed or intermediate result"
        },
        summary: { due_now: 0, due_soon: 1, later: 0, blocked_by_review: 1, completed: 0 },
        task_groups: [
          {
            group: "due_soon",
            tasks: [
              {
                task_id: "schedule_litter_separation_static",
                task_type: "litter_separation",
                label: "Separate/wean litter F1",
                status: "blocked_by_review",
                recorded_date: "2026-05-01",
                due_date: "2026-05-31",
                days_until_due: 22,
                source_entity: { entity_type: "litter", entity_id: "litter_static", label: "F1" },
                source_evidence: { source_record_id: "source_static", mating_id: "mating_static", mating_label: "C-12 breeding pair" },
                due_date_rule: { rule_set_id: "breeding_rule_default_20260509", rule_key: "litter_separation_due_after_days", value_days: 30 },
                attention_link: { label: "Open Focus Review", target_path: "/api/ui/focus-review", must_review: 1, quick_check: 0 }
              }
            ]
          }
        ],
        calendar_mirror: {
          status: "not_configured",
          canonical_source: "MouseDB internal schedule",
          note: "External calendar sync can mirror accepted schedule tasks later; it is not canonical."
        },
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/ui/mouse-timeline": {
        source_layer: "export or view",
        page_question: "How did this mouse get here?",
        mouse: { mouse_id: "MT401", display_id: "MT401", status: "active", strain: "C57BL/6J", litter_id: "litter_static" },
        summary: { accepted_events: 2, source_records: 1, must_review: 1, quick_check: 0 },
        lineage: {
          father: null,
          mother: null,
          litter: { litter_id: "litter_static", litter_label: "F1", mating_id: "mating_static", mating_label: "C-12 breeding pair", birth_date: "2026-05-01" }
        },
        events: [
          {
            event_id: "event_birth_static",
            event_type: "born",
            event_date: "2026-05-01",
            label: "born",
            source_layer: "canonical structured state",
            related_entity: { entity_type: "litter", entity_id: "litter_static" },
            source_evidence: { source_record_id: "source_static", source_label: "Reviewed mating cage C-12", source_type: "manual_review" }
          },
          {
            event_id: "event_weaned_static",
            event_type: "weaned",
            event_date: "2026-05-31",
            label: "weaned",
            source_layer: "canonical structured state",
            related_entity: { entity_type: "litter", entity_id: "litter_static" },
            source_evidence: { source_record_id: "source_static", source_label: "Reviewed mating cage C-12", source_type: "manual_review" }
          }
        ],
        attention_links: [{ label: "Open Focus Review", target_path: "/api/ui/focus-review", must_review: 1, quick_check: 0 }],
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/ui/mouse-pedigree": {
        source_layer: "export or view",
        page_question: "Where did this mouse come from?",
        mode: "selected_path",
        mouse: { mouse_id: "MT401", display_id: "MT401", status: "active", strain: "C57BL/6J", litter_id: "litter_static" },
        relationship_summary: {
          confirmed_relationships: 3,
          pending_relationships: 1,
          same_litter_siblings: 3,
          offspring_events: 0,
          must_review: 1,
          quick_check: 0
        },
        nodes: {
          father: {
            node_type: "mouse",
            relationship: "father",
            mouse_id: "MT402",
            display_id: "MT402",
            status: "active",
            strain: "C57BL/6J",
            relationship_status: "confirmed",
            source_layer: "canonical structured state"
          },
          mother: {
            node_type: "pending_relationship",
            relationship: "mother",
            label: "Parent pending",
            relationship_status: "pending_review",
            not_inferred: true
          },
          mating: { node_type: "mating", mating_id: "mating_static", mating_label: "C-12 breeding pair", start_date: "2026-04-15", relationship_status: "confirmed", source_layer: "canonical structured state" },
          litter: { node_type: "litter", litter_id: "litter_static", litter_label: "F1", birth_date: "2026-05-01", relationship_status: "confirmed", source_layer: "canonical structured state" },
          selected_mouse: { node_type: "mouse", relationship: "selected_mouse", mouse_id: "MT401", display_id: "MT401", status: "active", strain: "C57BL/6J", relationship_status: "selected", source_layer: "canonical structured state" },
          same_litter_siblings: [
            { node_type: "mouse", relationship: "same_litter_sibling", mouse_id: "MT403", display_id: "MT403", status: "active", strain: "C57BL/6J", relationship_status: "confirmed", source_layer: "canonical structured state" },
            { node_type: "mouse", relationship: "same_litter_sibling", mouse_id: "MT404", display_id: "MT404", status: "active", strain: "C57BL/6J", relationship_status: "confirmed", source_layer: "canonical structured state" },
            { node_type: "mouse", relationship: "same_litter_sibling", mouse_id: "MT405", display_id: "MT405", status: "active", strain: "C57BL/6J", relationship_status: "confirmed", source_layer: "canonical structured state" }
          ]
        },
        evidence_rows: [
          { field: "mother_id", value: "Parent pending", status: "pending_review", source_layer: "canonical structured state", source: { source_record_id: "", label: "No accepted parent evidence", source_type: "pending_relationship" }, not_inferred: true },
          { field: "father_id", value: "MT402", status: "confirmed", source_layer: "canonical structured state", source: { source_record_id: "source_static", label: "Reviewed mating cage C-12", source_type: "manual_review" } },
          { field: "litter_id", value: "litter_static", status: "confirmed", source_layer: "canonical structured state", source: { source_record_id: "source_static", label: "Reviewed mating cage C-12", source_type: "manual_review" } },
          { field: "mating_id", value: "mating_static", status: "confirmed", source_layer: "canonical structured state", source: { source_record_id: "source_static", label: "Reviewed mating cage C-12", source_type: "manual_review" } }
        ],
        attention_links: [{ label: "Open Focus Review", target_path: "/api/ui/focus-review", reason: "pending_relationship", must_review: 1, quick_check: 0 }],
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/ui/evidence-ledger": {
        source_layer: "export or view",
        page_question: "What evidence supports this record?",
        summary: { total_evidence: 1, needs_review: 1, linked_events: 0, source_photos: 1 },
        evidence_items: [
          {
            photo_evidence_id: "pe_static_ear",
            evidence_kind: "ear_label",
            card_type: "Separated",
            status: "review_open",
            source_photo: {
              photo_id: "photo_static_evidence",
              original_filename: "evidence-card.png",
              raw_source_kind: "cage_card_photo",
              uploaded_at: "2026-05-09T12:00:00Z",
              open_source_photo_label: "Open source photo"
            },
            parsed_trace: {
              parse_id: "parse_static_evidence",
              source_name: "manual_photo_transcription",
              status: "review",
              confidence: 0.62,
              needs_review: true
            },
            direct_observation: {
              roi_label: "note_line_1",
              bbox: { x: 10, y: 20, w: 80, h: 24 },
              observed_raw_text: "MT401 R0"
            },
            ocr: { text: "MT401 R0" },
            ai_interpretation: {
              parsed_value: "right_circle",
              confidence: 0.62,
              interpretation: "R0 may indicate a right ear circle; keep reviewable.",
              needs_review: true,
              review_reason: "Ambiguous ear mark."
            },
            links: {
              note_item_id: "note_static_evidence",
              linked_mouse_id: "",
              linked_cage_id: "",
              linked_event_id: "",
              review_ids: ["review_static_evidence"]
            },
            correction_history: []
          }
        ],
        empty_state: { message: "", fabricated_records: false }
      },
      "/api/mice": [],
      "/api/note-items": [
        {
          note_item_id: "note_warning_static",
          photo_id: "photo_warning_static",
          parse_id: "parse_warning_static",
          line_number: 1,
          raw_line_text: "MT401 genotype pending",
          parsed_type: "genotype_note",
          interpreted_status: "needs_review",
          mouse_display_id: "MT401",
          ear_code: "",
          needs_review: true
        }
      ],
      "/api/card-snapshots": [],
      "/api/mouse-events": [],
      "/api/cages": [],
      "/api/matings": [],
      "/api/litters": [],
      "/api/strain-target-genotypes": [],
      "/api/genotype-status-vocabulary": [],
      "/api/review-vocabulary": { roles: [], priorities: [] },
      "/api/experiment-readiness": {},
      "/api/genotyping-dashboard": [],
      "/api/labeling-rule-sets": [],
      "/api/export-preview": {
        ready: false,
        blocked_review_items: 1,
        open_review_items: 1,
        review_blockers: [reviewItem],
        readiness_warnings: [evidenceWarning],
        preview_row_count: 0,
        photos: 0,
        parsed_results: 0,
        separation_row_count: 0,
        animal_sheet_row_count: 0,
        genotype_blocker_items: 0
      },
      "/api/export-log": [],
      "/api/artifacts/preview": {
        artifact: {
          artifact_type: "validation_report",
          source_layer: "export or view",
          note_item_id: "note_warning_static"
        }
      },
      "/api/search": { query: "", mice: [], strains: [], reviews: [], sources: [] }
    };
    window.fetch = async (input) => {
      const url = String(input);
      const path = url.startsWith("file:///api/") ? url.replace("file://", "") : url;
      const key = path.split("?")[0];
      if (key === "/api/review-items/review_legacy_static/resolve") {
        return new Response(JSON.stringify({ detail: "Reviewed strain name must match the mapped canonical strain." }), {
          status: 400,
          headers: { "Content-Type": "application/json" }
        });
      }
      const body = Object.prototype.hasOwnProperty.call(responses, key) ? responses[key] : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    };
  });
  await staticPage.goto(fileUrl(staticPagePath));
  await staticPage.waitForFunction(() => typeof legacyWorkbookRow === "function");
  try {
    await staticPage.waitForFunction(() => document.querySelectorAll("#reviewRows .review-card").length > 0, { timeout: 5000 });
  } catch (error) {
    const bodyText = await staticPage.locator("body").innerText().catch(() => "");
    throw new Error(`Static review cards did not render. Errors: ${staticPageErrors.join(" | ") || "none"}. Body: ${bodyText.slice(0, 500)}`);
  }
  assert(staticPageErrors.length === 0, `Static app startup errors: ${staticPageErrors.join(" | ")}`);
  assert(
    (await staticPage.locator("#reviewRows .review-card").filter({ hasText: "Count mismatch" }).count()) === 1 &&
      (await staticPage.locator("#reviewRows .review-card").filter({ hasText: "MT401 R'" }).count()) === 1,
    "Static app-served review queue should render evidence-backed review cards."
  );
  assert(
    (await staticPage.locator("#focusReviewReadModel").filter({ hasText: "Must review 1" }).filter({ hasText: "focus-card.png" }).count()) === 1,
    "Static app startup should render the Focus Review read model from /api/ui/focus-review."
  );
  assert(
    (await staticPage.locator("#focusReviewReadModel").filter({ hasText: "Inspect source evidence" }).filter({ hasText: "manual_review_required" }).count()) === 1,
    "Static Focus Review cards should render read-model action hints without inventing actions."
  );
  const reviewCompletionSummaryText = await staticPage.evaluate(() => {
    renderReviewCompletionSummary({
      actions: [
        {
          action_type: "review_resolved",
          target_id: "review_focus_static_contract",
          performed_role: "Colony Reviewer",
          after: { status: "resolved" }
        },
        {
          action_type: "quick_review_resolved",
          target_id: "review_focus_static_contract",
          performed_role: "Colony Reviewer",
          after: { status: "resolved" }
        }
      ]
    }, currentVisibleReviews);
    return document.getElementById("reviewCompletionSummary")?.textContent || "";
  });
  assert(
    reviewCompletionSummaryText.includes("Review action summary: 2 recent completion(s)") &&
      reviewCompletionSummaryText.includes("focus-card.png") &&
      reviewCompletionSummaryText.includes("Colony Reviewer") &&
      reviewCompletionSummaryText.includes("Count mismatch") &&
      reviewCompletionSummaryText.includes("Action log only; canonical writes still require an explicit apply step."),
    "Static Review Queue should group completed review actions by source evidence, role, and blocker type without mutating review rows."
  );
  assert(
    (await staticPage.locator("#colonyStateReadModel").filter({ hasText: "Active mice 2" }).filter({ hasText: "colony-card.png" }).count()) === 1,
    "Static app startup should render the Colony State read model from /api/ui/colony-state."
  );
  assert(
    (await staticPage.locator("#colonyStateReadModel").filter({ hasText: "Open Focus Review" }).filter({ hasText: "Must review 1" }).count()) === 1,
    "Static Colony State should summarize unresolved blockers with a Focus Review link instead of duplicating review details."
  );
  assert(
    (await staticPage.locator("#colonyScheduleReadModel").filter({ hasText: "Due soon 1" }).filter({ hasText: "Separate/wean litter F1" }).count()) === 1,
    "Static app startup should render the Colony Schedule read model from /api/ui/colony-schedule."
  );
  assert(
    (await staticPage.locator("#colonyScheduleReadModel").filter({ hasText: "Open Focus Review" }).filter({ hasText: "Google Calendar mirror: not_configured" }).count()) === 1,
    "Static Colony Schedule should show blocked review links and non-canonical calendar mirror status."
  );
  assert(
    (await staticPage.locator("#mouseTimelineReadModel").filter({ hasText: "Accepted events 2" }).filter({ hasText: "MT401" }).count()) === 1,
    "Static app startup should render the Mouse Timeline read model from /api/ui/mouse-timeline."
  );
  assert(
    (await staticPage.locator("#mouseTimelineReadModel").filter({ hasText: "born" }).filter({ hasText: "canonical structured state" }).count()) === 1,
    "Static Mouse Timeline should show accepted canonical events without review-item detail."
  );
  assert(
    (await staticPage.locator("#mousePedigreeReadModel").filter({ hasText: "Pedigree / Lineage" }).filter({ hasText: "MT401" }).count()) === 1,
    "Static app startup should render the Mouse Pedigree read model from /api/ui/mouse-pedigree."
  );
  assert(
    (await staticPage.locator("#mousePedigreeReadModel").filter({ hasText: "Parent pending" }).filter({ hasText: "Same litter siblings" }).count()) === 1,
    "Static Mouse Pedigree should show pending relationships and same-litter siblings without inference."
  );
  assert(
    (await staticPage.locator("#mousePedigreeReadModel").filter({ hasText: "Open Focus Review" }).filter({ hasText: "Unconfirmed relationships are not inferred" }).count()) === 1,
    "Static Mouse Pedigree should link pending relationships to Focus Review and state the non-inference rule."
  );
  assert(
    (await staticPage.locator("#evidenceLedgerReadModel").filter({ hasText: "Evidence items 1" }).filter({ hasText: "evidence-card.png" }).count()) === 1,
    "Static app startup should render the Evidence Ledger read model from /api/ui/evidence-ledger."
  );
  assert(
    (await staticPage.locator("#evidenceLedgerReadModel").filter({ hasText: "Observed MT401 R0" }).filter({ hasText: "Parsed right_circle" }).count()) === 1,
    "Static Evidence Ledger should visibly separate direct observation, OCR, and interpreted parsed value."
  );
  assert(
    (await staticPage.locator("#evidenceLedgerReadModel .evidence-badge", { hasText: "Source photo" }).count()) === 1 &&
      (await staticPage.locator("#evidenceLedgerReadModel .evidence-badge", { hasText: "OCR text" }).count()) === 1 &&
      (await staticPage.locator("#evidenceLedgerReadModel .evidence-badge", { hasText: "Note line" }).count()) === 1 &&
      (await staticPage.locator("#evidenceLedgerReadModel .evidence-badge", { hasText: "Review item" }).count()) === 1,
    "Static Evidence Ledger should show text-backed evidence type badges."
  );
  assert(
    (await staticPage.locator("#reviewRoleRows .table-empty-row").filter({ hasText: "No review roles configured" }).count()) === 1 &&
      (await staticPage.locator("#reviewPriorityRows .table-empty-row").filter({ hasText: "No review priorities configured" }).count()) === 1 &&
      (await staticPage.locator("#assignedStrainRows .table-empty-row").filter({ hasText: "No assigned strains yet" }).count()) === 1 &&
      (await staticPage.locator("#auditTraceRows .table-empty-row").filter({ hasText: "No mouse selected" }).count()) === 1 &&
      (await staticPage.locator("#actionLogRows .table-empty-row").filter({ hasText: "No actions yet" }).count()) === 1,
    "Static lower-priority settings and audit tables should render structured empty states instead of bare placeholder cells."
  );
  const evidenceAnchorContract = await staticPage.evaluate(() => {
    document.getElementById("noteRows").innerHTML = noteEvidenceRow({
      note_item_id: "note_anchor_static",
      photo_id: "photo_anchor_static",
      parse_id: "parse_anchor_static",
      line_number: 1,
      raw_line_text: "MT401 R'",
      parsed_type: "mouse_note",
      strike_status: "not_struck",
      interpreted_status: "needs_review",
      parsed_mouse_display_id: "MT401",
      parsed_ear_label_code: "R_PRIME",
      needs_review: true,
      confidence: 0.72
    });
    document.getElementById("exportRows").innerHTML = exportPreviewRow([
      "C-12", "C57BL/6J", "--", "1", "2026-05-01", "--", "--", "--", "note_anchor_static", "photo photo_static_evidence", "needs review"
    ], {
      source_note_item_ids: "note_anchor_static",
      photo_evidence_id: "pe_static_ear",
      source_photo_ids: "photo_static_evidence",
      uncertainty: "needs review"
    }, false, "separation");
    const noteLink = document.querySelector("#exportRows [href='#note-note_anchor_static']");
    const ledgerLink = document.querySelector("#exportRows [href='#evidence-pe_static_ear']");
    noteLink?.click();
    const noteRow = document.getElementById("note-note_anchor_static");
    ledgerLink?.click();
    const ledgerCard = document.getElementById("evidence-pe_static_ear");
    return {
      noteRowExists: Boolean(noteRow),
      ledgerCardExists: Boolean(ledgerCard),
      noteLinkText: noteLink?.textContent || "",
      ledgerLinkText: ledgerLink?.textContent || "",
      activeView: document.querySelector("#appContent")?.dataset.activeView || "",
      hash: window.location.hash,
      noteHighlighted: noteRow?.classList.contains("anchor-highlight") || false,
      ledgerHighlighted: ledgerCard?.classList.contains("anchor-highlight") || false
    };
  });
  assert(
    evidenceAnchorContract.noteRowExists &&
      evidenceAnchorContract.ledgerCardExists &&
      evidenceAnchorContract.noteLinkText.includes("Note evidence") &&
      evidenceAnchorContract.ledgerLinkText.includes("Ledger evidence") &&
      evidenceAnchorContract.activeView === "photo" &&
      evidenceAnchorContract.hash === "#evidence-pe_static_ear",
    "Evidence anchor links should target parsed note and ledger rows without changing canonical data."
  );
  await staticPage.evaluate(() => {
    history.replaceState(null, "", window.location.pathname);
    document.querySelectorAll(".anchor-highlight").forEach((element) => element.classList.remove("anchor-highlight"));
  });
  assert(
    (await staticPage.locator("#exportBlockerList .open-export-blocker-review").filter({ hasText: "Open review" }).count()) === 1 &&
      (await staticPage.locator("#exportBlockerRows .open-export-blocker-review").filter({ hasText: "Open review" }).count()) === 1,
    "Static Export Center should expose direct Open review actions for source-backed blockers."
  );
  assert(
    (await staticPage.locator(".open-export-blocker-photo").filter({ hasText: "Open photo" }).count()) === 2 &&
      (await staticPage.locator(".open-export-blocker-note").filter({ hasText: "Open note" }).count()) === 2 &&
      (await staticPage.locator(".open-export-blocker-artifact").filter({ hasText: "Preview artifact" }).count()) === 2,
    "Static Export Center should expose direct photo, note, and artifact evidence links when a blocker has no review_id."
  );
  await staticPage.locator('button[data-view-target="exports"]').click();
  await staticPage.locator("#exportBlockerList .open-export-blocker-photo").click();
  await staticPage.waitForFunction(() => document.querySelector("#appContent")?.dataset.activeView === "photo");
  assert(
    (await staticPage.locator("#viewSubtitle").innerText()).includes("photo_warning_static"),
    "Export blocker photo evidence should open Photo Review without requiring a review item."
  );
  await staticPage.locator('button[data-view-target="exports"]').click();
  await staticPage.locator("#exportBlockerList .open-export-blocker-note").click();
  await staticPage.waitForFunction(() => document.querySelector("#appContent")?.dataset.activeView === "records");
  assert(
    (await staticPage.locator("#viewSubtitle").innerText()).includes("note_warning_static") &&
      (await staticPage.locator("#records-evidence details").evaluate((details) => details.hasAttribute("open"))),
    "Export blocker note evidence should open the parsed note evidence section without requiring a review item."
  );
  await staticPage.locator('button[data-view-target="exports"]').click();
  await staticPage.locator("#exportBlockerList .open-export-blocker-artifact").click();
  await staticPage.waitForFunction(() => document.querySelector("#artifactPreviewJson")?.textContent.includes("validation_report"));
  assert(
    (await staticPage.locator("#artifactPreviewJson").innerText()).includes("export or view"),
    "Export blocker artifact evidence should load artifact preview as an export or view surface."
  );
  const photoStageRender = await staticPage.evaluate(() => {
    const blockedPhoto = {
      photo_id: "photo_stage_static",
      original_filename: "stage-card.png",
      status: "stored",
      next_action: "resolve_reviews",
      transcription_count: 1,
      note_line_count: 2,
      total_review_count: 2,
      open_review_count: 1,
      canonical_candidate_count: 0,
      applied_candidate_count: 0
    };
    const readyPhoto = {
      photo_id: "photo_ready_static",
      original_filename: "ready-card.png",
      status: "stored",
      next_action: "ready_to_close",
      transcription_count: 1,
      note_line_count: 2,
      total_review_count: 2,
      open_review_count: 0,
      canonical_candidate_count: 1,
      applied_candidate_count: 1
    };
    const host = document.createElement("div");
    host.innerHTML = photoStageProgress(blockedPhoto, true) + photoStageProgress(readyPhoto, true);
    document.body.append(host);
    return {
      text: host.textContent,
      blocked: host.querySelectorAll(".photo-stage-step.blocked").length,
      held: host.querySelectorAll(".photo-stage-step.held").length,
      done: host.querySelectorAll(".photo-stage-step.done").length
    };
  });
  assert(
    photoStageRender.text.includes("Uploaded") &&
      photoStageRender.text.includes("Parse/OCR") &&
      photoStageRender.text.includes("Review") &&
      photoStageRender.text.includes("Candidate") &&
      photoStageRender.text.includes("Accepted/Held") &&
      photoStageRender.text.includes("Export") &&
      photoStageRender.blocked >= 2 &&
      photoStageRender.done >= 6,
    "Photo-stage progress should render all stages with blocked and done states."
  );
  await staticPage.locator('button[data-view-target="exports"]').click();
  await staticPage.locator("#exportBlockerList .open-export-blocker-review").click();
  await staticPage.waitForFunction(() => document.querySelector("#appContent")?.dataset.activeView === "review");
  assert(
    (await staticPage.locator('#reviewRows .review-card.selected-card[data-review-id="review_focus_static_contract"]').count()) === 1,
    "Export blocker Open review should navigate to Review Queue and select the responsible review item."
  );
  const keyboardFocusCue = await staticPage.evaluate(() => {
    const selectedCard = document.querySelector('#reviewRows .review-card.selected-card[data-review-id="review_focus_static_contract"]');
    const inspectButton = selectedCard?.querySelector(".inspect-review");
    inspectButton?.focus();
    const host = document.createElement("div");
    host.innerHTML = `
      <button class="photo-worklist-card" aria-current="true">Keyboard photo</button>
      <button class="export-blocker-action open-export-blocker-review" aria-label="Open Focus Review item review_keyboard_static from export blocker">Open review</button>
      <button class="artifact-preview-button" aria-label="Preview Manifest artifact">Manifest</button>
    `;
    document.body.appendChild(host);
    const photoButton = host.querySelector(".photo-worklist-card");
    const blockerButton = host.querySelector(".open-export-blocker-review");
    const artifactButton = host.querySelector(".artifact-preview-button");
    return {
      selectedCurrent: selectedCard?.getAttribute("aria-current") === "true",
      focusWithin: Boolean(selectedCard?.matches(":focus-within")),
      photoCurrent: photoButton?.getAttribute("aria-current") === "true",
      blockerName: blockerButton?.getAttribute("aria-label") || "",
      artifactName: artifactButton?.getAttribute("aria-label") || ""
    };
  });
  assert(
    keyboardFocusCue.selectedCurrent &&
      keyboardFocusCue.focusWithin &&
      keyboardFocusCue.photoCurrent &&
      keyboardFocusCue.blockerName.includes("Open Focus Review item") &&
      keyboardFocusCue.artifactName.includes("Preview Manifest artifact"),
    "Keyboard focus checks should preserve selected review state, focus-within card feedback, current photo state, and specific action names."
  );
  const attentionCue = await staticPage.evaluate(() => {
    const item = { review_id: "review_dom_contract", attention_level: "quick_check", status: "open" };
    const visual = reviewVisual(item);
    const attentionClass = `attention-${text(item.attention_level || "quick_check").replaceAll("_", "-")}`;
    const host = document.createElement("div");
    host.innerHTML = `
      <article class="review-card ${attentionClass}" data-attention-level="${item.attention_level}">
        <span class="review-card-kind ${visual.tone}"><span class="review-card-symbol" aria-hidden="true">${visual.symbol}</span>${visual.label}</span>
        <div class="review-card-consequence">${visual.consequence}</div>
        <button class="inspect-review" type="button">Inspect</button>
      </article>
    `;
    document.body.appendChild(host);
    const card = host.querySelector(".review-card");
    return {
      label: host.querySelector(".review-card-kind")?.textContent || "",
      consequence: host.querySelector(".review-card-consequence")?.textContent || "",
      hasDataLevel: card?.getAttribute("data-attention-level") === "quick_check",
      hasStableClass: card?.classList.contains("attention-quick-check"),
      symbolHidden: host.querySelector(".review-card-symbol")?.getAttribute("aria-hidden") === "true",
      hasAction: Boolean(host.querySelector(".inspect-review")),
    };
  });
  assert(
    attentionCue.label.includes("Needs quick confirmation") &&
      attentionCue.consequence === "Check source evidence before accepting." &&
      attentionCue.hasDataLevel &&
      attentionCue.hasStableClass &&
      attentionCue.symbolHidden &&
      attentionCue.hasAction,
    "Rendered Focus Review cards should expose quick-check text, symbol, consequence copy, stable cue class, data level, and actions."
  );
  const focusReviewRender = await staticPage.evaluate(() => {
    const panel = document.getElementById("focusReviewReadModel");
    renderFocusReviewReadModel({
      source_layer: "export or view",
      page_question: "What needs my decision today?",
      workload_summary: { must_review: 2, quick_check: 1 },
      cards: [
        {
          parse_id: "parse_focus_contract",
          source_photo: { filename: "source-card.png" },
          review_count: 2,
          review_items: [
            {
              issue_label: "Count mismatch",
              action_hint: {
                source_layer: "export or view",
                mode: "manual_review_required",
                primary_label: "Inspect source evidence",
                requires_note: true,
                requires_source_photo: true,
                safe_quick_resolve: false
              }
            }
          ],
          mouse_rows: [{ mouse_id: "M-001" }]
        }
      ],
      empty_state: { message: "", fabricated_records: false }
    });
    const loaded = {
      state: panel.dataset.state,
      fabricated: panel.dataset.fabricatedRecords,
      text: panel.textContent
    };
    renderFocusReviewReadModel({
      source_layer: "export or view",
      page_question: "What needs my decision today?",
      cards: [],
      empty_state: { message: "No Focus Review items are currently open.", fabricated_records: false }
    });
    const missingWorkload = {
      state: panel.dataset.state,
      fabricated: panel.dataset.fabricatedRecords,
      stateKind: panel.querySelector(".state-message")?.dataset.stateKind || "",
      text: panel.textContent
    };
    renderFocusReviewReadModel({
      source_layer: "export or view",
      page_question: "What needs my decision today?",
      workload_summary: { must_review: null, quick_check: "" },
      cards: [],
      empty_state: { message: "No Focus Review items are currently open.", fabricated_records: false }
    });
    const malformedWorkload = {
      state: panel.dataset.state,
      fabricated: panel.dataset.fabricatedRecords,
      stateKind: panel.querySelector(".state-message")?.dataset.stateKind || "",
      text: panel.textContent
    };
    renderFocusReviewReadModel({
      source_layer: "export or view",
      load_error: true,
      error_message: "backend down",
      page_question: "What needs my decision today?",
      cards: [],
      empty_state: { message: "Focus Review unavailable.", fabricated_records: false }
    });
    return {
      loaded,
      missingWorkload,
      malformedWorkload,
      error: {
        state: panel.dataset.state,
        fabricated: panel.dataset.fabricatedRecords,
        stateKind: panel.querySelector(".state-message")?.dataset.stateKind || "",
        text: panel.textContent
      }
    };
  });
  assert(
    focusReviewRender.loaded.state === "loaded" &&
      focusReviewRender.loaded.fabricated === "false" &&
      focusReviewRender.loaded.text.includes("Must review 2") &&
      focusReviewRender.loaded.text.includes("Quick check 1") &&
      focusReviewRender.loaded.text.includes("source-card.png") &&
      focusReviewRender.loaded.text.includes("M-001") &&
      focusReviewRender.loaded.text.includes("Inspect source evidence"),
    "Focus Review read-model cards should render source-backed counts and evidence previews."
  );
  assert(
    focusReviewRender.missingWorkload.state === "empty" &&
      focusReviewRender.missingWorkload.fabricated === "false" &&
      focusReviewRender.missingWorkload.stateKind === "empty" &&
      focusReviewRender.missingWorkload.text.includes("Counts unavailable") &&
      focusReviewRender.missingWorkload.text.includes("No mouse IDs, strains, dates, or review counts are invented") &&
      !focusReviewRender.missingWorkload.text.includes("Must review 0"),
    "Focus Review should not invent zero workload when workload_summary is missing."
  );
  assert(
    focusReviewRender.malformedWorkload.state === "empty" &&
      focusReviewRender.malformedWorkload.fabricated === "false" &&
      focusReviewRender.malformedWorkload.stateKind === "empty" &&
      focusReviewRender.malformedWorkload.text.includes("Counts unavailable") &&
      !focusReviewRender.malformedWorkload.text.includes("Must review 0"),
    "Focus Review should not invent zero workload when workload_summary values are malformed."
  );
  assert(
    focusReviewRender.error.state === "error" &&
      focusReviewRender.error.fabricated === "false" &&
      focusReviewRender.error.stateKind === "error" &&
      focusReviewRender.error.text.includes("Focus Review unavailable") &&
      focusReviewRender.error.text.includes("backend down"),
    "Focus Review unavailable state should be explicit and non-fabricated."
  );
  const colonyStateRender = await staticPage.evaluate(() => {
    const panel = document.getElementById("colonyStateReadModel");
    renderColonyStateReadModel({
      source_layer: "export or view",
      page_question: "What is active now?",
      summary: {
        active_mice: 3,
        active_card_snapshots: 1,
        active_matings: 1,
        active_litters: 0,
        must_review: 1,
        quick_check: 1
      },
      active_card_snapshots: [
        {
          card_snapshot_id: "card_contract",
          card_type: "Separated",
          card_id_raw: "C-21",
          matched_strain_text: "C57BL/6J",
          mouse_count: 3,
          source_photo: { filename: "accepted-card.png", source_photo_role: "primary_evidence" },
          collapsed_sections: { mice: 3, note_lines: 3, review_blockers: 1, source_evidence: 1 }
        }
      ],
      active_matings: [
        {
          mating_id: "mating_contract",
          mating_label: "C-21 breeding pair",
          strain_goal: "C57BL/6J",
          start_date: "2026-04-15",
          parent_count: 2,
          active_litter_count: 1,
          source_record_id: "source_mating_contract",
          collapsed_sections: { parents: 2, active_litters: 1, source_evidence: 1 }
        }
      ],
      active_litters: [
        {
          litter_id: "litter_contract",
          litter_label: "F1",
          mating_label: "C-21 breeding pair",
          birth_date: "2026-05-01",
          number_alive: 5,
          status: "born",
          source_record_id: "source_mating_contract",
          collapsed_sections: { pups_alive: 5, source_evidence: 1 }
        }
      ],
      strain_summary: [{ strain: "C57BL/6J", active_mice: 3 }],
      status_summary: [{ status: "active", mouse_count: 3 }],
      attention_links: [{ label: "Focus Review", target_path: "/api/ui/focus-review", must_review: 1, quick_check: 1 }],
      empty_state: { message: "", fabricated_records: false }
    });
    const loaded = {
      state: panel.dataset.state,
      fabricated: panel.dataset.fabricatedRecords,
      text: panel.textContent
    };
    renderColonyStateReadModel({
      source_layer: "export or view",
      page_question: "What is active now?",
      active_card_snapshots: [],
      empty_state: { message: "No accepted active colony records are available yet.", fabricated_records: false }
    });
    const missingSummary = {
      state: panel.dataset.state,
      fabricated: panel.dataset.fabricatedRecords,
      text: panel.textContent
    };
    renderColonyStateReadModel({
      source_layer: "export or view",
      load_error: true,
      error_message: "backend down",
      page_question: "What is active now?",
      active_card_snapshots: [],
      empty_state: { message: "Colony State unavailable.", fabricated_records: false }
    });
    return {
      loaded,
      missingSummary,
      error: {
        state: panel.dataset.state,
        fabricated: panel.dataset.fabricatedRecords,
        text: panel.textContent
      }
    };
  });
  assert(
    colonyStateRender.loaded.state === "loaded" &&
      colonyStateRender.loaded.fabricated === "false" &&
      colonyStateRender.loaded.text.includes("Active mice 3") &&
      colonyStateRender.loaded.text.includes("Active matings 1") &&
      colonyStateRender.loaded.text.includes("accepted-card.png") &&
      colonyStateRender.loaded.text.includes("C-21 breeding pair") &&
      colonyStateRender.loaded.text.includes("Parents 2") &&
      colonyStateRender.loaded.text.includes("F1") &&
      colonyStateRender.loaded.text.includes("Alive pups 5") &&
      colonyStateRender.loaded.text.includes("Open Focus Review") &&
      !colonyStateRender.loaded.text.includes("review_colony"),
    "Colony State read-model cards should render accepted-state cards, matings, litters, and review links without detailed review items."
  );
  assert(
    colonyStateRender.missingSummary.state === "empty" &&
      colonyStateRender.missingSummary.fabricated === "false" &&
      colonyStateRender.missingSummary.text.includes("Counts unavailable") &&
      !colonyStateRender.missingSummary.text.includes("Active mice 0"),
    "Colony State should not invent zero counts when summary is missing."
  );
  assert(
    colonyStateRender.error.state === "error" &&
      colonyStateRender.error.fabricated === "false" &&
      colonyStateRender.error.text.includes("Colony State unavailable") &&
      colonyStateRender.error.text.includes("backend down"),
    "Colony State unavailable state should be explicit and non-fabricated."
  );
  const legacyApplyFailure = await staticPage.evaluate(async () => {
    const item = {
      review_id: "review_legacy_static",
      parse_id: "legacy_parse_static",
      status: "open",
      issue: "Legacy strain registry candidate requires review",
      severity: "High",
      attention_level: "must_review",
      priority: "high",
      assigned_role: "Strain Curator",
      current_value: JSON.stringify({
        strain_raw: "ApoM Tg/Tg",
        normalized_candidate: { strain_name: "ApoM Tg/Tg", gene_symbol: "", allele_name: "" }
      }),
      suggested_value: "",
      review_reason: "Legacy workbook strain registry candidate requires reviewed values.",
      evidence_preview: "animal-sheet.xlsx row 2"
    };
    currentVisibleReviews = [item];
    selectedReviewId = item.review_id;
    renderReviewDetail(item);
    const panel = document.getElementById("reviewDetailPanel");
    panel.querySelector(".review-legacy-decision").value = "apply_strain_registry_candidate";
    panel.querySelector(".review-resolution-note").value = "Attempt mismatched canonical strain link.";
    panel.querySelector(".reviewed-strain-name").value = "ApoM Tg/Tg";
    panel.querySelector(".reviewed-gene-symbol").value = "ApoM";
    panel.querySelector(".reviewed-allele-name").value = "Tg transgene";
    panel.querySelector(".reviewed-existing-strain-id").value = "strain_different";
    await submitReviewResolution(panel.querySelector(".resolve-review"), item, false);
    return {
      detailText: panel.textContent,
      selectedReviewId,
      status: item.status
    };
  });
  assert(
    legacyApplyFailure.selectedReviewId === "review_legacy_static" &&
      legacyApplyFailure.status === "open" &&
      legacyApplyFailure.detailText.includes("Reviewed strain name must match the mapped canonical strain."),
    "Legacy strain apply failures should stay on the open review and show the backend guard message."
  );
  const hybridEvaluatorDetail = await staticPage.evaluate(() => {
    const item = {
      review_id: "review_hybrid_static",
      parse_id: "parse_hybrid_static",
      status: "open",
      issue: "Hybrid note-line evaluator conflict",
      severity: "High",
      attention_level: "must_review",
      attention_reason: "hybrid evaluator flagged note-line candidate conflict",
      priority: "high",
      assigned_role: "Colony Reviewer",
      current_value: "101 L'",
      suggested_value: "Review OCR, AI, and rule candidates",
      review_reason: "Hybrid evaluator detected note-line candidate conflicts.",
      evidence_preview: "101 L'",
      photo_id: "photo_hybrid_static",
      original_filename: "hybrid-card.png",
      note_item_id: "note_hybrid_static",
      hybrid_note_line_evaluator: {
        candidate_kind: "hybrid_note_line",
        review_routing: { attention_level: "must_review", must_review: true },
        ocr_candidate: { raw_line_text: "101 L'", parsed_ear_label_code: "L_PRIME", confidence: 0.91 },
        ai_candidate: { raw_line_text: "101 R'", parsed_ear_label_code: "R_PRIME", confidence: 0.93 },
        hybrid_candidate: { raw_line_text: "101 L'", parsed_mouse_display_id: "101", parsed_ear_label_code: "L_PRIME", confidence: 0.9 },
        conflicts: ["ocr_ai_note_line_disagreement", "rule_expected_ear_label_mismatch"],
        source_quality: {
          source_image_quality: "acceptable",
          roi_alignment_confidence: 0.9,
          line_segmentation_confidence: 0.88
        },
        rule_candidate: {
          expected_ear_label_code: "R_PRIME",
          rule_interpretation_candidate: "active",
          rule_interpretation_boundary: "review hint only",
          rule_snapshot: {
            rule_set_id: "label_rule_apom_tgtg_20260506",
            display_name: "ApoM Tg/Tg 2026-05-06",
            rule_hash: "abc123hash"
          }
        }
      }
    };
    currentVisibleReviews = [item];
    selectedReviewId = item.review_id;
    renderReviewDetail(item);
    const panel = document.getElementById("reviewDetailPanel");
    return {
      text: panel.textContent,
      hasPanel: Boolean(panel.querySelector(".hybrid-note-line-evaluator-panel")),
      hasConflict: Boolean(panel.querySelector("[data-hybrid-conflict='ocr_ai_note_line_disagreement']")),
      boundary: panel.querySelector("[data-rule-boundary]")?.textContent || ""
    };
  });
  assert(
    hybridEvaluatorDetail.hasPanel &&
      hybridEvaluatorDetail.text.includes("Hybrid Note-Line Evaluator") &&
      hybridEvaluatorDetail.text.includes("OCR candidate") &&
      hybridEvaluatorDetail.text.includes("AI candidate") &&
      hybridEvaluatorDetail.text.includes("Hybrid candidate") &&
      hybridEvaluatorDetail.text.includes("ocr_ai_note_line_disagreement") &&
      hybridEvaluatorDetail.text.includes("ROI alignment") &&
      hybridEvaluatorDetail.text.includes("Line segmentation") &&
      hybridEvaluatorDetail.text.includes("abc123hash") &&
      hybridEvaluatorDetail.boundary.includes("review hint only") &&
      hybridEvaluatorDetail.hasConflict,
    "Review detail should render hybrid evaluator candidates, conflicts, quality signals, rule snapshot, and review-hint boundary."
  );
  const reviewFollowThrough = await staticPage.evaluate(async () => {
    const originalFetch = window.fetch;
    window.fetch = async (input, options) => {
      const key = String(input).replace("file://", "").split("?")[0];
      if (key === "/api/review-items/review_follow_done/resolve") {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }
      return originalFetch(input, options);
    };
    try {
      const doneItem = {
        review_id: "review_follow_done",
        parse_id: "parse_follow_done",
        status: "open",
        issue: "Resolved count check",
        severity: "Medium",
        attention_level: "quick_check",
        priority: "medium",
        assigned_role: "Colony Reviewer",
        suggested_value: "ok",
        evidence_preview: "done raw evidence",
        photo_id: "photo_done",
        original_filename: "done-card.png",
        note_item_id: "note_done"
      };
      const nextItem = {
        review_id: "review_follow_next",
        parse_id: "parse_follow_next",
        status: "open",
        issue: "Next count check",
        severity: "High",
        attention_level: "must_review",
        priority: "high",
        assigned_role: "Colony Reviewer",
        suggested_value: "review next",
        evidence_preview: "next raw evidence",
        photo_id: "photo_next",
        original_filename: "next-card.png",
        note_item_id: "note_next"
      };
      currentVisibleReviews = [doneItem, nextItem];
      selectedReviewId = doneItem.review_id;
      renderReviewDetail(doneItem);
      await submitReviewResolution(document.querySelector("#reviewDetailPanel .quick-resolve-review"), doneItem, true);
      return {
        statusText: document.getElementById("reviewFollowThrough")?.textContent || "",
        selectedReviewId,
        stateKind: document.querySelector("#reviewFollowThrough .state-message")?.dataset.stateKind || ""
      };
    } finally {
      window.fetch = originalFetch;
    }
  });
  assert(
    reviewFollowThrough.stateKind === "success" &&
      reviewFollowThrough.statusText.includes("Review resolved: Resolved count check") &&
      reviewFollowThrough.statusText.includes("Next: Next count check") &&
      reviewFollowThrough.statusText.includes("next-card.png") &&
      reviewFollowThrough.statusText.includes("Current detail remains anchored"),
    "Successful review resolution should leave a follow-through message with next-item and source evidence context."
  );
  const legacyWorkbookHtml = await staticPage.evaluate(() => legacyWorkbookRow({
    source_file_name: "legacy <source>.xlsx",
    workbook_kind: "animal",
    sheet_name: "Sheet <1>",
    source_record_id: "source <img src=x onerror=alert(1)>",
    open_review_count: 1,
    review_count: 2,
    rows: [
      {
        raw_row: {
          cage_no_raw: "Cage <A>",
          strain_raw: "ApoM <bad>",
          display_id_raw: "M\"1",
          total_raw: "2",
          dob_raw: "2026-01-01"
        }
      }
    ],
    strain_registry_candidates: [
      {
        strain_raw: "ApoM <script>",
        genotype_raw: "tg/+ \"quoted\"",
        normalized_candidate: {
          gene_symbol: "ApoM",
          allele_name: "<allele>"
        }
      }
    ]
  }));
  await staticPage.setContent(`<table><tbody>${legacyWorkbookHtml}</tbody></table>`);
  const legacyCells = await staticPage.locator("tbody tr td").allTextContents();
  assert(legacyCells.length === 7, `Legacy workbook rows should render 7 cells, saw ${legacyCells.length}.`);
  assert(
    legacyCells[4].includes("ApoM <script> / tg/+ \"quoted\" -> ApoM / <allele>"),
    "Legacy strain registry candidate text should remain visible after escaping."
  );
  assert(
    (await staticPage.locator("tbody script, tbody img").count()) === 0,
    "Legacy strain registry candidate text must not be injected as executable markup."
  );
  await staticPage.close();

  assert((await page.locator("title").textContent()) === "Mouse Colony LIMS - Photo Review Workbench", "Browser title should make Photo Review Workbench the first workflow.");
  assert((await page.locator("#inboxView.active h1").textContent()) === "Photo Review Workbench", "Initial active view should be the Photo Review Workbench.");
  assert((await page.locator(".sidebar .nav-button.active").first().textContent()).includes("Photo Review"), "Sidebar should open on Photo Review rather than a broad dashboard.");
  assert((await page.locator(".topbar-title").filter({ hasText: "Photo Review Workbench" }).count()) === 1, "Top utility bar should name the active Photo Review workflow.");
  assert((await page.locator("#recordsView h1").textContent()) === "Candidate Records", "Records view should be framed as candidate records.");
  assert((await page.locator("#exportsView h1").textContent()) === "Export Center", "Export view should be framed as the Export Center.");

  const mobilePage = await context.newPage();
  await mobilePage.setViewportSize({ width: 390, height: 844 });
  await mobilePage.goto(fileUrl(pagePath));
  await mobilePage.waitForSelector("#inboxView.active");
  const mobileOverflow = await mobilePage.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert(mobileOverflow <= 1, `Photo Review Workbench should not create mobile horizontal overflow; overflow=${mobileOverflow}.`);
  await mobilePage.close();

  assert((await page.locator("#inboxRows tr").count()) >= 5, "Seed inbox rows did not render.");
  assert((await page.locator("#reviewSummary").textContent()).includes("3 pending"), "Initial review count is wrong.");
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "ApoM Tg/Tg" }).filter({ hasText: "Canonical scope" }).count()) === 1,
    "My Assigned Strains seed scope missing."
  );
  await page.locator('.sidebar [data-view="dashboard"]').click();
  assert((await page.locator("#dashboardView.active").count()) === 1, "Dashboard visual view did not open.");
  assert((await page.locator("body.visual-mode").count()) === 1, "Visual dashboard mode was not enabled.");
  await page.locator('.sidebar [data-view="mouseDetail"]').click();
  assert((await page.locator("#mouseDetailView.active").count()) === 1, "Mouse detail visual view did not open.");
  await page.locator('.sidebar [data-view="strainDetail"]').click();
  assert((await page.locator("#strainDetailView.active").count()) === 1, "Strain detail visual view did not open.");
  await page.locator('.sidebar [data-view="inbox"]').click();
  assert((await page.locator("#inboxView.active").count()) === 1, "Inbox view did not reopen after visual views.");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.locator("#assignedStrainName").fill("GFAP Cre; S1PR1 fl/fl");
  await page.locator("#assignedStrainAliases").fill("GFAP S1PR1");
  await page.getByRole("button", { name: "Add / Update Scope" }).click();
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "Active" }).count()) === 1,
    "Manual assigned strain was not added to active scope."
  );
  await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).getByRole("button", { name: "Deactivate" }).click();
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "Inactive" }).count()) === 1,
    "Assigned strain deactivate action failed."
  );
  await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).getByRole("button", { name: "Reactivate" }).click();
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "Active" }).count()) === 1,
    "Assigned strain reactivate action failed."
  );

  await page.getByRole("button", { name: "Load Distribution Fixture" }).click();
  assert(
    (await page.locator("#distributionSummary").textContent()).includes("20260407"),
    "Distribution fixture did not update settings summary."
  );
  assert(
    (await page.locator("#distributionRows tr").filter({ hasText: "ApoMtg/tg" }).count()) === 1,
    "Distribution fixture assignment row missing."
  );
  assert(
    (await page.locator("#distributionSuggestionRows tr").filter({ hasText: "ApoMtg/tg" }).filter({ hasText: "Possible alias for ApoM Tg/Tg" }).count()) === 1,
    "Distribution fixture did not create a reviewable strain candidate suggestion."
  );
  await page
    .locator("#distributionSuggestionRows tr")
    .filter({ hasText: "GFAP Cre; S1PR1 fl/fl" })
    .getByRole("button", { name: "Add Scope" })
    .click();
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "Distribution import" }).count()) === 1,
    "Distribution candidate was not added to My Assigned Strains."
  );
  assert(
    (await page.locator("#recordRows tr").filter({ hasText: "ApoMtg/tg" }).count()) === 0,
    "Distribution assignment leaked into canonical mouse records."
  );
  await page.reload();
  await page.waitForFunction(() => document.querySelector("#distributionRows")?.textContent.includes("TAStg/+; TPMtg/+; ApoMtg/+"));
  assert(
    (await page.locator("#distributionRows tr").filter({ hasText: "TAStg/+; TPMtg/+; ApoMtg/+" }).count()) === 1,
    "Distribution import did not persist after reload."
  );

  await page.getByRole("button", { name: "Load Sample Fixture" }).click();
  await page.waitForSelector("#reviewRows tr");
  assert((await page.locator("#inboxRows tr").filter({ hasText: "FIXTURE-AUTO-MATING" }).count()) === 1, "Embedded fixture mating row missing.");
  assert((await page.locator("#reviewRows tr").filter({ hasText: "Count mismatch" }).count()) >= 1, "Embedded fixture did not route count mismatch to review.");
  assert((await page.locator("#reviewRows tr").filter({ hasText: "FIXTURE-DUPLICATE-ACTIVE" }).count()) === 1, "Embedded fixture did not route duplicate active mouse to review.");
  await page.getByRole("button", { name: "Reset Local" }).click();
  await page.waitForSelector("#inboxRows tr");
  assert((await page.locator("#inboxRows tr").filter({ hasText: "FIXTURE-AUTO-MATING" }).count()) === 0, "Reset did not remove embedded fixture rows.");
  assert((await page.locator("#distributionRows tr").filter({ hasText: "ApoMtg/tg" }).count()) === 0, "Reset did not remove distribution assignment rows.");

  const outsideScopeFixturePath = path.join(os.tmpdir(), `outside-scope-${Date.now()}.json`);
  fs.writeFileSync(outsideScopeFixturePath, JSON.stringify({
    layer: "parsed or intermediate result",
    records: [
      {
        id: "FIXTURE-OUTSIDE-SCOPE",
        uploaded: "Fixture import",
        type: "Separated",
        rawStrain: "GFAP Cre; S1PR1 fl/fl",
        matchedStrain: "GFAP Cre; S1PR1 fl/fl",
        dobRaw: "26.01.01",
        dobNormalized: "2026-01-01",
        mouseCount: "2 total",
        confidence: 96,
        status: "auto",
        issue: "Fixture auto-filled by policy",
        severity: "Low",
        reviewField: "matchedStrain",
        currentValue: "GFAP Cre; S1PR1 fl/fl",
        suggestedValue: "GFAP Cre; S1PR1 fl/fl",
        reviewReason: "Tests assigned scope validation.",
        notes: [
          { raw: "GF101 R'", meaning: "Mouse GF101, right prime ear label (R_PRIME)", strike: "none" },
          { raw: "GF102 L'", meaning: "Mouse GF102, left prime ear label (L_PRIME)", strike: "none" }
        ],
        actions: ["Import as parsed result."]
      }
    ]
  }), "utf8");
  await page.getByRole("button", { name: "Import Parse JSON" }).click();
  await page.setInputFiles("#parseInput", outsideScopeFixturePath);
  await page.waitForSelector("#reviewRows tr");
  assert(
    (await page.locator("#reviewRows tr").filter({ hasText: "FIXTURE-OUTSIDE-SCOPE" }).filter({ hasText: "Outside assigned strain scope" }).count()) === 1,
    "Outside assigned strain scope fixture was not routed to review."
  );
  assert(
    (await page.locator("#recordRows tr").filter({ hasText: "FIXTURE-OUTSIDE-SCOPE" }).count()) === 0,
    "Outside assigned strain scope fixture leaked into canonical candidates."
  );
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector("#inboxRows tr");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.locator("#assignedStrainName").fill("GFAP Cre; S1PR1 fl/fl");
  await page.locator("#assignedStrainAliases").fill("GFAP S1PR1");
  await page.getByRole("button", { name: "Add / Update Scope" }).click();
  await page.getByRole("button", { name: "Import Parse JSON" }).click();
  await page.setInputFiles("#parseInput", outsideScopeFixturePath);
  await page.waitForFunction(() =>
    [...document.querySelectorAll("#recordRows tr")].some((row) => row.textContent.includes("FIXTURE-OUTSIDE-SCOPE"))
  );
  assert(
    (await page.locator("#recordRows tr").filter({ hasText: "FIXTURE-OUTSIDE-SCOPE" }).filter({ hasText: "GF101" }).count()) >= 1,
    "Newly assigned strain did not allow canonical candidates for matching parsed records."
  );
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.waitForSelector("#inboxRows tr");

  await page.getByRole("button", { name: "Import Distribution JSON" }).click();
  await page.setInputFiles("#distributionInput", generatedDistributionPath);
  await page.waitForFunction(() => document.querySelector("#distributionRows")?.textContent.includes("TAStg/+; TPMtg/+; ApoMtg/+"));
  assert(
    (await page.locator("#distributionRows tr").filter({ hasText: "ApoMtg/tg" }).count()) === 1,
    "Distribution parser JSON import row missing."
  );
  assert(
    (await page.locator("#distributionSuggestionRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "New candidate" }).count()) === 1,
    "Distribution parser JSON import did not create a new candidate review signal."
  );
  await page.locator("#distributionSuggestionRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).getByRole("button", { name: "Add Scope" }).click();
  assert(
    (await page.locator("#assignedStrainRows tr").filter({ hasText: "GFAP Cre; S1PR1 fl/fl" }).filter({ hasText: "Distribution import" }).count()) === 1,
    "Distribution candidate was not added to My Assigned Strains."
  );
  assert(
    (await page.locator("#recordRows tr").filter({ hasText: "ApoMtg/tg" }).count()) === 0,
    "Distribution parser JSON import leaked into canonical mouse records."
  );
  await page.getByRole("button", { name: "Reset Local" }).click();
  await page.waitForSelector("#inboxRows tr");

  await page.getByRole("button", { name: "Import Parse JSON" }).click();
  await page.setInputFiles("#parseInput", fixturePath);
  await page.waitForSelector("#reviewRows tr");
  assert((await page.locator("#inboxRows tr").filter({ hasText: "FIXTURE-LOW-STRAIN" }).count()) === 1, "Fixture import row missing.");
  assert((await page.locator("#reviewRows tr").filter({ hasText: "FIXTURE-MATING-CONFLICT" }).count()) === 1, "Fixture conflict row missing.");
  assert((await page.locator("#reviewRows tr").filter({ hasText: "Count mismatch" }).count()) >= 1, "Count mismatch validation did not create a review item.");
  assert((await page.locator("#reviewRows tr").filter({ hasText: "FIXTURE-DUPLICATE-ACTIVE" }).count()) === 1, "Duplicate active mouse validation did not create a review item.");

  await page.getByRole("button", { name: "Candidate Records" }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll("#recordRows tr")].some((row) => row.textContent.includes("FIXTURE-AUTO-SEPARATED"))
  );
  assert((await page.locator("#recordRows tr").filter({ hasText: "MT321" }).count()) >= 1, "Auto fixture mouse candidate missing.");
  assert((await page.locator("#recordRows tr").filter({ hasText: "Moved candidate" }).count()) >= 1, "Strike-through candidate status missing.");
  assert((await page.locator("#recordRows tr").filter({ hasText: "MT401" }).count()) === 0, "Count mismatch fixture leaked into canonical candidates.");
  assert((await page.locator("#recordRows tr").filter({ hasText: "FIXTURE-DUPLICATE-ACTIVE" }).count()) === 0, "Duplicate active fixture leaked into canonical candidates.");

  await page.getByRole("button", { name: "Review Queue" }).click();
  await page.locator("#reviewRows tr").filter({ hasText: "FIXTURE-DUPLICATE-ACTIVE" }).click();
  const duplicateReviewCount = await page.locator("#reviewRows tr").count();
  const focusChecklist = page.locator("#focusReviewChecklist");
  assert((await focusChecklist.filter({ hasText: "Export blocker" }).count()) === 1, "Focus Review should show export blocker status in the review drawer.");
  assert((await focusChecklist.filter({ hasText: "Source photo evidence" }).count()) === 1, "Focus Review should keep source evidence visible in the review drawer.");
  assert((await focusChecklist.filter({ hasText: "Parsed field" }).count()) === 1, "Focus Review should show the parsed field being corrected.");
  assert((await focusChecklist.filter({ hasText: "Before / after" }).count()) === 1, "Focus Review should show before/after correction context.");
  assert((await focusChecklist.filter({ hasText: "Movement decision required" }).count()) === 1, "Duplicate active mouse reviews should show the movement decision requirement.");
  await page.locator("#afterValue").fill("Reviewed duplicate without movement");
  await page.getByRole("button", { name: "Apply Reviewed Changes" }).click();
  await page.waitForTimeout(50);
  assert((await page.locator("#reviewRows tr").count()) === duplicateReviewCount, "Duplicate active mouse was accepted without movement review.");
  await page.locator("#movementReason").fill("Reviewed card evidence closes the previous active snapshot before accepting this source.");
  await page.getByRole("button", { name: "Resolve Mouse Movement" }).click();
  await page.waitForTimeout(50);
  assert((await page.locator("#reviewRows tr").count()) === duplicateReviewCount - 1, "Duplicate active mouse resolution did not leave the review queue.");
  await page.getByRole("button", { name: "Candidate Records" }).click();
  assert((await page.locator("#recordRows tr").filter({ hasText: "FIXTURE-DUPLICATE-ACTIVE" }).count()) >= 1, "Resolved duplicate source did not create reviewed canonical candidates.");
  assert((await page.locator("#recordRows tr").filter({ hasText: "Closed by movement review" }).count()) >= 1, "Previous active source was not marked closed by movement review.");

  await page.getByRole("button", { name: "Review Queue" }).click();
  await page.locator("#reviewRows tr").first().click();
  const beforeReviewCount = await page.locator("#reviewRows tr").count();
  await page.locator("#afterValue").fill("Reviewed configured value");
  await page.getByRole("button", { name: "Apply Reviewed Changes" }).click();
  await page.waitForTimeout(50);
  assert((await page.locator("#reviewRows tr").count()) === beforeReviewCount - 1, "Reviewed correction did not leave the queue.");

  await page.getByRole("button", { name: "Candidate Records" }).click();
  assert((await page.locator("#recordRows tr").filter({ hasText: "Reviewed configured value" }).count()) >= 1, "Reviewed record candidate missing.");

  await page.getByRole("button", { name: "Review Queue" }).click();
  await page.locator("#reviewRows tr").first().click();
  const dismissedSource = await page.locator("#drawerTitle").textContent();
  const dismissCountBeforeReason = await page.locator("#reviewRows tr").count();
  await page.getByRole("button", { name: "Dismiss With Reason" }).click();
  assert(
    (await page.locator("#reviewRows tr").count()) === dismissCountBeforeReason,
    "Dismissed review item without a required reason."
  );
  await page.locator("#dismissReason").fill("Not actionable for this MVP slice");
  await page.getByRole("button", { name: "Dismiss With Reason" }).click();
  await page.getByRole("button", { name: "Candidate Records" }).click();
  assert(
    (await page.locator("#recordRows tr").filter({ hasText: dismissedSource }).count()) === 0,
    "Dismissed review item leaked into canonical candidates."
  );

  await page.getByRole("button", { name: "Photo Review" }).click();
  await page.setInputFiles("#photoInput", uploadPath);
  await page.waitForFunction(() =>
    [...document.querySelectorAll("#inboxRows tr")].some((row) => row.textContent.includes("UPLOAD-"))
  );
  assert((await page.locator("#inboxRows tr").filter({ hasText: "UPLOAD-" }).count()) === 1, "Uploaded source photo row missing.");

  await page.reload();
  await page.waitForSelector("#inboxRows tr");
  assert((await page.locator("#inboxRows tr").filter({ hasText: "UPLOAD-" }).count()) === 1, "Local source photo session did not persist after reload.");
  assert((await page.locator("#inboxRows tr").filter({ hasText: "FIXTURE-AUTO-SEPARATED" }).count()) === 1, "Imported parse fixture did not persist after reload.");

  await page.getByRole("button", { name: "Exports" }).click();
  const blockedDetailRows = page.locator("#blockedExportRows tr[data-id]");
  assert(
    (await blockedDetailRows.count()) >= 1,
    "Blocked export detail rows missing."
  );
  assert(await page.locator("#exportStrainSelect").isEnabled(), "Export strain selector should be enabled when accepted strains exist.");
  assert(
    (await page.locator("#exportStrainSelect option", { hasText: "ApoM Tg/Tg" }).count()) === 1,
    "Export strain selector did not list the accepted separation strain."
  );
  assert(
    (await page.locator("#separationFilenamePreview").textContent()).endsWith("분리 현황표.xlsx"),
    "Separation filename preview is missing the lab filename pattern."
  );
  assert((await page.locator("#separationExportStatus").textContent()).includes("row"), "Separation export status did not show ready rows.");
  assert(await page.locator("#downloadSeparationXlsx").isEnabled(), "Separation XLSX button should be enabled when accepted rows exist.");
  assert(
    (await page.locator("#animalsheetFilenamePreview").textContent()).endsWith("animal sheet.xlsx"),
    "Animal sheet filename preview is missing the lab filename pattern."
  );
  assert((await page.locator("#animalsheetExportStatus").textContent()).includes("row"), "Animal sheet export status did not show ready rows.");
  assert(await page.locator("#downloadAnimalsheetXlsx").isEnabled(), "Animal sheet XLSX button should be enabled when accepted rows exist.");
  assert((await page.locator("#workbookPreviewTitle").textContent()) === "Separation workbook preview", "Separation workbook preview title missing.");
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "Sampling point" }).count()) === 1,
    "Separation preview is missing the Sampling point template column."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "FIXTURE-AUTO-SEPARATED" }).count()) === 1,
    "Accepted separated fixture missing from separation workbook preview."
  );
  assert(
    (await page.locator("#workbookPreviewTable .row-state-chip", { hasText: "Preview only" }).count()) >= 1 &&
      (
        (await page.locator("#workbookPreviewTable .row-state-chip", { hasText: "Ready" }).count()) >= 1 ||
        (await page.locator("#workbookPreviewTable .row-state-chip", { hasText: "Blocked" }).count()) >= 1
      ) &&
      (await page.locator("#workbookPreviewTable .row-state-chip", { hasText: "Source evidence" }).count()) >= 1,
    "Separation workbook preview rows should show preview-only, explicit readiness/blocker, and source evidence chips."
  );
  await page.getByRole("button", { name: "Preview Animalsheet" }).click();
  assert(
    (await page.locator("#exportStrainSelect option", { hasText: "ApoM Tg/Tg" }).count()) === 1,
    "Export strain selector did not list the accepted mating strain."
  );
  assert((await page.locator("#workbookPreviewTitle").textContent()) === "Animal sheet preview", "Animalsheet preview title missing.");
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "Cage No." }).count()) === 1,
    "Animalsheet preview is missing the Cage No. template column."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "FIXTURE-AUTO-MATING" }).count()) === 1,
    "Accepted mating fixture missing from animalsheet preview."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "F1" }).count()) === 1,
    "Accepted mating fixture did not render a litter row."
  );
  assert(
    (await page.locator("#workbookPreviewTable .row-state-chip", { hasText: "Preview only" }).count()) >= 1,
    "Animalsheet preview rows should keep preview-only row-state chips visible."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "Source evidence" }).count()) === 1,
    "Animalsheet preview is missing source evidence column."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "MT321" }).count()) >= 1 &&
      (await page.locator("#workbookPreviewTable").filter({ hasText: "MT322" }).count()) >= 1,
    "Animalsheet preview did not split parent IDs into rows."
  );
  assert(
    (await page.locator("#workbookPreviewTable tr").filter({ hasText: "Litter" }).filter({ hasText: "10" }).count()) >= 1,
    "Animalsheet preview did not map pup count into the Pubs column."
  );
  assert(
    (await page.locator("#workbookPreviewTable").filter({ hasText: "26.04.13 - 10p" }).count()) === 1,
    "Litter note evidence missing from animalsheet preview."
  );
  const downloadDir = fs.mkdtempSync(path.join(os.tmpdir(), "mouse-xlsx-"));
  const [separationXlsx] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Download Separation XLSX" }).click()
  ]);
  const separationXlsxPath = path.join(downloadDir, separationXlsx.suggestedFilename());
  await separationXlsx.saveAs(separationXlsxPath);
  assert(separationXlsx.suggestedFilename().endsWith(".xlsx"), "Unexpected separation XLSX filename.");
  const separationWorkbook = fs.readFileSync(separationXlsxPath);
  const separationWorkbookText = separationWorkbook.toString("utf8");
  assert(separationWorkbook.subarray(0, 4).toString("hex") === "504b0304", "Separation XLSX is not a ZIP-based workbook.");
  assert(separationWorkbookText.includes("Sampling point"), "Separation XLSX is missing the template header.");
  assert(separationWorkbookText.includes("FIXTURE-AUTO-SEPARATED"), "Separation XLSX is missing accepted source traceability.");
  assert(!separationWorkbookText.includes("FIXTURE-COUNT-MISMATCH"), "Separation XLSX included a count mismatch review item.");
  assert(!separationWorkbookText.includes("FIXTURE-MATING-CONFLICT"), "Separation XLSX included a blocked mating conflict.");
  const [animalsheetXlsx] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Download Animal Sheet XLSX" }).click()
  ]);
  const animalsheetXlsxPath = path.join(downloadDir, animalsheetXlsx.suggestedFilename());
  await animalsheetXlsx.saveAs(animalsheetXlsxPath);
  assert(animalsheetXlsx.suggestedFilename().endsWith("animal sheet.xlsx"), "Unexpected animal sheet XLSX filename.");
  const animalsheetWorkbook = fs.readFileSync(animalsheetXlsxPath);
  const animalsheetWorkbookText = animalsheetWorkbook.toString("utf8");
  assert(animalsheetWorkbook.subarray(0, 4).toString("hex") === "504b0304", "Animal sheet XLSX is not a ZIP-based workbook.");
  assert(animalsheetWorkbookText.includes("Source evidence"), "Animal sheet XLSX is missing the evidence header.");
  assert(animalsheetWorkbookText.includes("MT321") && animalsheetWorkbookText.includes("MT322"), "Animal sheet XLSX is missing parsed parent IDs.");
  assert(animalsheetWorkbookText.includes("26.04.13 - 10p"), "Animal sheet XLSX is missing litter note evidence.");
  assert(!animalsheetWorkbookText.includes("FIXTURE-MATING-CONFLICT"), "Animal sheet XLSX included a blocked mating conflict.");
  assert(
    (await page.locator("#exportLogRows tr").filter({ hasText: "XLSX generated from workbook preview" }).count()) >= 1,
    "XLSX exports were not recorded in the export log."
  );
  assert(
    (await page.locator("#exportLogRows .evidence-badge", { hasText: "Export manifest" }).count()) >= 1 &&
      (await page.locator("#exportLogRows .evidence-badge", { hasText: "Validation report" }).count()) >= 1,
    "Export log should label manifest and validation report artifacts with evidence badges."
  );
  const blockedSource = await blockedDetailRows.first().locator("td").first().textContent();
  await blockedDetailRows.first().evaluate((row) => row.click());
  assert((await page.locator("#drawerTitle").textContent()) === blockedSource, "Blocked export row did not open review evidence.");
  await page.getByRole("button", { name: "Exports" }).click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Generate Separation CSV" }).click()
  ]);
  assert(download.suggestedFilename() === "separation_export_preview.csv", "Unexpected export filename.");
  assert(
    (await page.locator("#exportLogRows tr").filter({ hasText: "Preview generated" }).count()) >= 1,
    "Export attempt log did not record the generated preview."
  );
  assert(
    (await page.locator("#exportLogRows tr").filter({ hasText: "blocked rows excluded" }).count()) >= 1,
    "Export log did not preserve blocked-row context."
  );
  assert(!browserErrors.length, `Browser errors: ${browserErrors.join(" | ")}`);
  await browser.close();
  console.log("MVP verification passed.");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
