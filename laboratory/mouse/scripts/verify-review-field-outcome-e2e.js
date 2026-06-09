const fs = require("fs");
const http = require("http");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const PYTHON = fs.existsSync(path.join(ROOT, ".venv", "Scripts", "python.exe"))
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : "python";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runPython(args, options = {}) {
  const result = spawnSync(PYTHON, args, {
    cwd: ROOT,
    encoding: "utf-8",
    ...options,
  });
  assert(
    result.status === 0,
    `Python command failed: stdout=${result.stdout} stderr=${result.stderr}`
  );
  return result.stdout;
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, (response) => {
      response.resume();
      response.on("end", () => resolve(response.statusCode || 0));
    });
    request.on("error", reject);
    request.setTimeout(1000, () => request.destroy(new Error("request timed out")));
  });
}

async function waitForServer(baseUrl) {
  const deadline = Date.now() + 30000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      if ((await httpGet(`${baseUrl}/api/health`)) === 200) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Local app did not become healthy: ${lastError ? lastError.message : "no response"}`);
}

function writeManifest(tempRoot, manifestPath) {
  const privateSource = path.join(tempRoot, "private-source");
  fs.mkdirSync(privateSource, { recursive: true });
  const sourcePhoto = path.join(privateSource, "apom-ui-field-outcome-card.jpg");
  fs.copyFileSync(path.join(ROOT, "static", "assets", "cage-card-evidence-art.png"), sourcePhoto);
  const manifest = {
    layer: "review item / test fixture",
    canonical: false,
    source_policy: "Local-only browser E2E fixture. Raw photos and private paths are never reported.",
    cases: [
      {
        case_id: "apom_ui_field_outcome_001",
        source_photo_path: sourcePhoto,
        source_photo_filename: "apom-ui-field-outcome-card.jpg",
        card_type: "separated",
        traceability_label: "Browser E2E review field outcome fixture",
        expected_review_level: "must_review",
        expected_export_blocking: true,
        expected_fields: {
          raw_strain_text: "operator reviewed",
          mouse_ids_or_note_lines: ["MT777 R_PRIME"],
          sex_count: "operator reviewed",
          dob: "operator reviewed",
          mating_or_litter_note: "operator reviewed",
          expected_review_blockers: ["partial_match"],
        },
      },
    ],
  };
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");
}

function startServer(dataDir, port) {
  const code = `
import json
import os
from pathlib import Path
from app import db

data_dir = Path(os.environ["MOUSE_E2E_DATA_DIR"])
db.DATA_DIR = data_dir
db.DB_PATH = data_dir / "mouse_lims.sqlite"
db.init_db()

asset_path = Path(os.environ["MOUSE_E2E_SOURCE_ASSET"])
now = "2026-05-16T00:00:00Z"
hybrid_evaluator = {
    "review_routing": {"attention_level": "must_review", "must_review": True},
    "source_quality": {
        "source_image_quality": "usable",
        "roi_alignment_confidence": 0.82,
        "line_segmentation_confidence": 0.78,
    },
    "ocr_candidate": {"raw_line_text": "MT777 R", "parsed_mouse_display_id": "MT777"},
    "hybrid_candidate": {"raw_line_text": "MT777 R'", "parsed_mouse_display_id": "MT777"},
    "conflicts": ["partial_match"],
}

with db.connection() as conn:
    conn.execute(
        """
        INSERT INTO photo_log
            (photo_id, original_filename, stored_path, uploaded_at, status, raw_source_kind)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "photo_ui_field_outcome",
            "apom-ui-field-outcome-card.jpg",
            str(asset_path),
            now,
            "review_pending",
            "cage_card_photo",
        ),
    )
    conn.execute(
        """
        INSERT INTO parse_result
            (parse_id, photo_id, source_name, raw_payload, parsed_at, status, confidence, needs_review)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "parse_ui_field_outcome",
            "photo_ui_field_outcome",
            "ai_photo_extraction",
            json.dumps(
                {
                    "confidence": 58,
                    "rawStrain": "ApoM Tg/Tg",
                    "sexRaw": "M",
                    "notes": [{"raw": "MT777 R'", "meaning": "mouse_item", "confidence": 74}],
                },
                ensure_ascii=False,
            ),
            now,
            "review",
            58,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO card_snapshot
            (card_snapshot_id, photo_id, parse_id, card_type, card_id_raw,
             raw_strain_text, matched_strain_text, sex_raw, sex_normalized,
             count_value, dob_raw, note_summary_json, status, confidence,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snapshot_ui_field_outcome",
            "photo_ui_field_outcome",
            "parse_ui_field_outcome",
            "separated",
            "C-UI-001",
            "ApoM Tg/Tg",
            "ApoM Tg/Tg",
            "M",
            "male",
            1,
            "2026-01-01",
            json.dumps({"note_count": 1, "mouse_item_count": 1}, ensure_ascii=False),
            "review",
            58,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO card_note_item_log
            (note_item_id, photo_id, parse_id, card_snapshot_id, card_type,
             line_number, raw_line_text, parsed_type, interpreted_status,
             parsed_mouse_display_id, parsed_ear_label_raw, parsed_metadata_json,
             confidence, needs_review, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "note_ui_field_outcome",
            "photo_ui_field_outcome",
            "parse_ui_field_outcome",
            "snapshot_ui_field_outcome",
            "separated",
            1,
            "MT777 R'",
            "mouse_item",
            "active",
            "MT777",
            "R'",
            json.dumps({"hybrid_note_line_evaluator": hybrid_evaluator}, ensure_ascii=False),
            74,
            1,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO review_queue
            (review_id, parse_id, severity, issue, current_value, suggested_value,
             review_reason, assigned_role, priority, evidence_reference_json,
             review_trigger_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "review_ear_note_ui_field_outcome",
            "parse_ui_field_outcome",
            "high",
            "AI-extracted photo transcription needs review",
            "MT777 R",
            "MT777 R'",
            "Hybrid evaluator found a partial note-line match that requires operator scoring.",
            "Colony Reviewer",
            "high",
            json.dumps({"photo_id": "photo_ui_field_outcome", "note_item_id": "note_ui_field_outcome"}),
            json.dumps({"trigger": "hybrid_note_line_evaluator"}),
            "open",
            now,
        ),
    )

from app import main as app_main
import uvicorn
uvicorn.run(app_main.app, host="127.0.0.1", port=int(os.environ["MOUSE_E2E_PORT"]), log_level="warning")
`;
  return spawn(PYTHON, ["-c", code], {
    cwd: ROOT,
    env: {
      ...process.env,
      MOUSE_E2E_DATA_DIR: dataDir,
      MOUSE_E2E_PORT: String(port),
      MOUSE_E2E_SOURCE_ASSET: path.join(ROOT, "static", "assets", "cage-card-evidence-art.png"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
}

async function setView(page, view) {
  await page.locator(`button[data-view-target="${view}"]`).click();
  await page.waitForFunction(
    (expected) => document.querySelector("#appContent")?.dataset.activeView === expected,
    view
  );
}

async function waitForText(page, selector, expected, timeout = 15000) {
  await page.waitForFunction(
    ({ selector, expected }) => document.querySelector(selector)?.textContent?.includes(expected),
    { selector, expected },
    { timeout }
  );
}

async function selectFieldOutcome(panel, family, status) {
  const selector = `.review-field-outcome-status[data-field-family="${family}"]`;
  let actual = "";
  let options = [];
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const field = panel.locator(selector);
    assert((await field.count()) === 1, `Expected one field outcome select for ${family}.`);
    options = await field.locator("option").evaluateAll((nodes) => nodes.map((node) => node.value));
    assert(options.includes(status), `Field outcome ${family} is missing option ${status}; options=${JSON.stringify(options)}`);
    await field.selectOption({ value: status });
    actual = await field.evaluate((node) => node.value);
    if (actual === status) return;
    await delay(100);
  }
  assert(actual === status, `Field outcome ${family} should be ${status}; got ${actual}`);
}

async function selectNoteLineScope(panel, scope) {
  let actual = "";
  let options = [];
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const field = panel.locator(".review-note-line-scoring-scope");
    assert((await field.count()) === 1, "Expected one note-line scoring scope select.");
    options = await field.locator("option").evaluateAll((nodes) => nodes.map((node) => node.value));
    assert(options.includes(scope), `Note-line scope is missing option ${scope}; options=${JSON.stringify(options)}`);
    await field.selectOption({ value: scope });
    actual = await field.evaluate((node) => node.value);
    if (actual === scope) return;
    await delay(100);
  }
  assert(actual === scope, `Note-line scope should be ${scope}; got ${actual}`);
}

async function fillReviewFieldOutcomeControls(panel) {
  await selectNoteLineScope(panel, "scored_note_line");
  await panel.locator(".review-export-blocked-until-resolved").check();
  await selectFieldOutcome(panel, "mouse_ids_or_note_lines", "corrected");
  await selectFieldOutcome(panel, "card_type_review_routing", "exact");
  await selectFieldOutcome(panel, "sex_count_dob", "exact");
  await selectFieldOutcome(panel, "mating_litter_context", "exact");
  await selectFieldOutcome(panel, "export_provenance", "exact");
}

async function verifySideBySideWorkbenchControls(panel) {
  await panel.locator(".field-review-workbench").waitFor();
  await panel.locator(".field-review-input").first().fill("MT777 R'");
  await panel.locator(".use-field-review-value").first().click();

  const resolvedValue = await panel.locator(".review-resolved-value").inputValue();
  assert(
    resolvedValue === "MT777 R'",
    `Side-by-side workbench should copy checked value into resolved value; got ${resolvedValue}`
  );

  const resolutionNote = await panel.locator(".review-resolution-note").inputValue();
  assert(
    resolutionNote.includes("Checked from source photo"),
    `Side-by-side workbench should add a source-photo resolution note; got ${resolutionNote}`
  );
}

async function stopServer(server) {
  if (server.exitCode !== null || server.signalCode !== null) return;
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, 5000);
    server.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    server.kill();
  });
}

function runReporter(manifestPath, outputPath) {
  const code = `
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("report_private_accuracy", Path("scripts") / "report-private-accuracy.py")
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
report = module.build_report(manifest_path=Path(sys.argv[1]), results_path=Path(sys.argv[2]))
print(json.dumps({
    "decision": report["decision"],
    "matched_case_count": report["matched_case_count"],
    "failure_taxonomy_counts": report["failure_taxonomy_counts"],
}, ensure_ascii=False))
`;
  return JSON.parse(runPython(["-c", code, manifestPath, outputPath]));
}

async function run() {
  fs.mkdirSync(path.join(ROOT, "data"), { recursive: true });
  const tempRoot = fs.mkdtempSync(path.join(ROOT, "data", "review-field-outcome-e2e-"));
  const dataDir = path.join(tempRoot, "data");
  const manifestPath = path.join(tempRoot, "private-manifest.json");
  const outputPath = path.join(tempRoot, "sanitized-private-input.json");
  fs.mkdirSync(dataDir, { recursive: true });
  writeManifest(tempRoot, manifestPath);

  const port = 20000 + Math.floor(Math.random() * 1000);
  const baseUrl = `http://127.0.0.1:${port}`;
  const server = startServer(dataDir, port);
  const serverOutput = [];
  server.stdout.on("data", (chunk) => serverOutput.push(chunk.toString()));
  server.stderr.on("data", (chunk) => serverOutput.push(chunk.toString()));

  let browser = null;
  try {
    await waitForServer(baseUrl);
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1366, height: 920 } });
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });

    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await setView(page, "review");
    await page.selectOption("#reviewStatusFilter", "open");
    await page.waitForSelector('#reviewRows .review-card:has-text("AI-extracted photo transcription needs review")');
    await page.locator("#reviewRows .review-card")
      .filter({ hasText: "AI-extracted photo transcription needs review" })
      .locator(".inspect-review")
      .click();
    await page.waitForSelector("#reviewDetailPanel .review-accuracy-outcome");

    const panel = page.locator("#reviewDetailPanel");
    await verifySideBySideWorkbenchControls(panel);
    await fillReviewFieldOutcomeControls(panel);
    await panel.locator(".review-audit-taxonomy-status").selectOption("partial_match");
    await panel.locator(".review-audit-taxonomy-note").fill("Operator scored field outcome from source note-line evidence.");
    await panel.locator(".review-resolved-value").fill("MT777 R'");
    await panel.locator(".review-resolution-note").fill("Operator reviewed source note-line evidence before applying the resolution.");
    await fillReviewFieldOutcomeControls(panel);

    let resolveRequestPayload = null;
    page.on("request", (request) => {
      if (
        request.url().includes("/api/review-items/review_ear_note_ui_field_outcome/resolve") &&
        request.method() === "POST"
      ) {
        resolveRequestPayload = JSON.parse(request.postData() || "{}");
      }
    });
    const responsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/review-items/review_ear_note_ui_field_outcome/resolve") &&
      response.request().method() === "POST"
    );
    await panel.locator(".resolve-review").click();
    const response = await responsePromise;
    const resolution = await response.json();
    assert(response.ok(), `Review resolution failed: ${JSON.stringify(resolution)}`);
    assert(
      resolveRequestPayload?.note_line_scoring_scope === "scored_note_line",
      `Browser payload should include selected note-line scoring scope: ${JSON.stringify(resolveRequestPayload)}`
    );
    assert(
      resolveRequestPayload?.field_review_outcome?.field_scores?.mouse_ids_or_note_lines?.status === "corrected",
      `Browser payload should include corrected mouse-id outcome: ${JSON.stringify(resolveRequestPayload)}`
    );
    assert(
      Boolean(resolveRequestPayload?.field_review_outcome?.checked_workbench_field),
      `Browser payload should include checked workbench field: ${JSON.stringify(resolveRequestPayload)}`
    );
    assert(
      resolveRequestPayload?.field_review_outcome?.field_review_workbench?.canonical === false,
      `Browser payload should include non-canonical side-by-side workbench context: ${JSON.stringify(resolveRequestPayload)}`
    );
    assert(
      resolution.field_review_outcome.note_line_scoring_scope === "scored_note_line",
      `Resolution response should include note-line scoring scope: ${JSON.stringify(resolution.field_review_outcome)}`
    );
    assert(
      resolution.field_review_outcome.field_scores.mouse_ids_or_note_lines.status === "corrected",
      `Resolution response should include corrected mouse-id outcome: ${JSON.stringify(resolution.field_review_outcome)}`
    );

    const audit = await page.evaluate(() =>
      fetch("/api/review-items/review_ear_note_ui_field_outcome/audit").then((res) => res.json())
    );
    const reviewAction = audit.actions.find((action) => action.action_type === "review_resolved");
    assert(reviewAction, `review_resolved action should be present: ${JSON.stringify(audit.actions)}`);
    assert(
      reviewAction.after_value.field_review_outcome.boundary === "review item / private accuracy field outcome",
      `Action log should preserve sanitized field outcome boundary: ${JSON.stringify(reviewAction.after_value)}`
    );
    assert(
      reviewAction.after_value.scoring_audit.status === "partial_match",
      `Action log should preserve scoring taxonomy: ${JSON.stringify(reviewAction.after_value.scoring_audit)}`
    );

    const exportSummary = JSON.parse(runPython([
      "scripts/export-review-scoring-audit-input.py",
      "--db-path",
      path.join(dataDir, "mouse_lims.sqlite"),
      "--manifest",
      manifestPath,
      "--output",
      outputPath,
      "--run-label",
      "ui field outcome e2e",
      "--json",
    ]));
    const outputJson = fs.readFileSync(outputPath, "utf-8");
    assert(!outputJson.includes(tempRoot), "Sanitized reporter input must not include temp/private paths.");
    assert(!outputJson.includes('"raw_payload"'), "Sanitized reporter input must not include raw payload keys.");
    const reporter = runReporter(manifestPath, outputPath);

    assert(pageErrors.length === 0, `Browser page errors occurred: ${pageErrors.join("\\n")}`);
    const relevantConsoleErrors = consoleErrors.filter((entry) =>
      !entry.includes("Failed to load resource: the server responded with a status of 400")
    );
    assert(relevantConsoleErrors.length === 0, `Browser console errors occurred: ${relevantConsoleErrors.join("\\n")}`);

    console.log(JSON.stringify({
      status: "passed",
      matched_case_count: exportSummary.matched_case_count,
      report_decision: reporter.decision,
      failure_taxonomy_counts: reporter.failure_taxonomy_counts,
      data_boundary: {
        source_photo: "raw source",
        review_resolution: "review item",
        reporter_input: "parsed or intermediate result",
        private_accuracy_report: "export or view",
      },
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer(server);
    fs.rmSync(tempRoot, { recursive: true, force: true });
    if (server.exitCode && server.exitCode !== 0) {
      process.stderr.write(serverOutput.join(""));
    }
  }
}

run().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
