const fs = require("fs");
const http = require("http");
const os = require("os");
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

async function removeTempRoot(tempRoot) {
  let lastError = null;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      fs.rmSync(tempRoot, { recursive: true, force: true });
      return;
    } catch (error) {
      lastError = error;
      await delay(250);
    }
  }
  throw lastError;
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, (response) => {
      response.resume();
      response.on("end", () => resolve(response.statusCode || 0));
    });
    request.on("error", reject);
    request.setTimeout(1000, () => {
      request.destroy(new Error("request timed out"));
    });
  });
}

async function waitForServer(baseUrl) {
  const deadline = Date.now() + 30000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const status = await httpGet(`${baseUrl}/api/health`);
      if (status === 200) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Local app did not become healthy: ${lastError ? lastError.message : "no response"}`);
}

function createLegacyWorkbook(targetPath) {
  const code = `
from openpyxl import Workbook
import sys
wb = Workbook()
ws = wb.active
ws.title = "animal sheet"
ws.append(["Cage No.", "Strain", "Sex", "I.D", "genotype", "DOB", "Mating date", "Pubs"])
ws.append(["C-014", "ApoM Tg/Tg", "M", "MT321", "Tg/Tg", "2026-01-01", "2026-05-01", ""])
ws.append(["", "", "F1", "9p", "pre_weaning", "2026-05-02", "", "2026-05-02 9p"])
wb.save(sys.argv[1])
`;
  const result = spawnSync(PYTHON, ["-c", code, targetPath], {
    cwd: ROOT,
    encoding: "utf-8",
  });
  assert(
    result.status === 0,
    `Could not create legacy workbook: stdout=${result.stdout} stderr=${result.stderr}`
  );
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

from app import main as app_main

class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "output_text": json.dumps({
                "card_type": "Separated",
                "raw_strain": "ApoM Tg/Tg",
                "matched_strain": "ApoM Tg/Tg",
                "sex_raw": "female",
                "id_raw": "MT",
                "dob_raw": "25.10.20-28",
                "dob_normalized": "",
                "mating_date_raw": "",
                "mating_date_normalized": "",
                "lmo_raw": "",
                "mouse_count": "2 total",
                "notes": [
                    {"raw": "MT321 R'", "meaning": "mouse_item", "strike": "none", "confidence": 92},
                    {"raw": "MT322 L'", "meaning": "mouse_item", "strike": "none", "confidence": 91},
                    {"raw": "1 2 3", "meaning": "unlabeled_numeric_note", "strike": "none", "confidence": 72}
                ],
                "raw_visible_text_lines": [
                    "ApoM Tg/Tg",
                    "Sex female / 2 total",
                    "I.D MT",
                    "D.O.B 25.10.20-28",
                    "MT321 R'",
                    "MT322 L'",
                    "1 2 3"
                ],
                "symbol_confusions": [],
                "confidence": 88,
                "uncertain_fields": ["dob_normalized"],
                "reviewer_note": "Synthetic browser E2E fake AI response; operator must review the raw photo."
            }, ensure_ascii=False)
        }

class FakeClient:
    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def post(self, url, *, headers, json):
        return FakeResponse()

app_main.current_openai_api_key = lambda: "browser-e2e-test-key"
app_main.httpx.Client = FakeClient

import uvicorn
uvicorn.run(app_main.app, host="127.0.0.1", port=int(os.environ["MOUSE_E2E_PORT"]), log_level="warning")
`;
  return spawn(PYTHON, ["-c", code], {
    cwd: ROOT,
    env: {
      ...process.env,
      MOUSE_E2E_DATA_DIR: dataDir,
      MOUSE_E2E_PORT: String(port),
      OPENAI_API_KEY: "browser-e2e-test-key",
      OPENAI_PARSE_ASSIST_MODEL: "",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
}

async function waitForText(page, selector, expected, timeout = 15000) {
  try {
    await page.waitForFunction(
      ({ selector, expected }) => {
        const node = document.querySelector(selector);
        return Boolean(node && node.textContent && node.textContent.includes(expected));
      },
      { selector, expected },
      { timeout }
    );
  } catch (error) {
    const actual = await page.locator(selector).textContent().catch(() => "<missing>");
    throw new Error(`Timed out waiting for ${selector} to contain ${JSON.stringify(expected)}. Actual text: ${JSON.stringify(actual)}`);
  }
}

async function waitForValue(page, callback, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let value = null;
  while (Date.now() < deadline) {
    value = await page.evaluate(callback);
    if (value) return value;
    await page.waitForTimeout(200);
  }
  throw new Error(`Timed out waiting for browser state. Last value: ${JSON.stringify(value)}`);
}

async function fillAndVerify(locator, value, description) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await locator.fill(value);
    if ((await locator.inputValue()) === value) {
      return;
    }
  }
  throw new Error(`${description} did not retain the expected value.`);
}

async function setView(page, view) {
  await page.locator(`button[data-view-target="${view}"]`).click();
  await page.waitForFunction(
    (expected) => document.querySelector("#appContent")?.dataset.activeView === expected,
    view
  );
}

async function inspectReviewByIssue(page, issueText) {
  await setView(page, "review");
  await page.selectOption("#reviewStatusFilter", "open");
  try {
    await page.waitForFunction(
      (issue) => Array.from(document.querySelectorAll("#reviewRows .review-card h3"))
        .some((node) => node.textContent.includes(issue)),
      issueText
    );
  } catch (error) {
    const issues = await page.evaluate(() => Array.from(
      document.querySelectorAll("#reviewRows .review-card h3"),
      (node) => node.textContent
    ));
    throw new Error(`Could not find review issue ${JSON.stringify(issueText)}. Visible issues: ${JSON.stringify(issues)}`);
  }
  const card = page.locator("#reviewRows .review-card").filter({ hasText: issueText });
  assert(await card.count() === 1, `Expected one review card for ${issueText}`);
  await card.locator(".inspect-review").click();
  await waitForText(page, "#reviewDetailPanel", issueText);
}

async function visibleReviewIssues(page) {
  await setView(page, "review");
  await page.selectOption("#reviewStatusFilter", "open");
  await page.waitForFunction(
    () => document.querySelectorAll("#reviewRows .review-card h3").length > 0,
    null,
    { timeout: 30000 }
  );
  return page.locator("#reviewRows .review-card h3").allTextContents();
}

async function resolveNumericNoteReview(page) {
  await inspectReviewByIssue(page, "Unlabeled numeric note needs review");
  const panel = page.locator("#reviewDetailPanel");
  await panel.locator(".review-resolved-value").fill("3 temporary count labels");
  await panel.locator(".review-resolution-note").fill(
    "Browser E2E checked the source note line; numeric-only values are count labels, not mouse IDs."
  );
  await page.evaluate(() => {
    const panel = document.querySelector("#reviewDetailPanel");
    panel.querySelector(".note-label-decision").value = "count_note";
    panel.querySelector(".note-label-count").value = "3";
  });
  assert(
    (await panel.locator(".note-label-decision").inputValue()) === "count_note",
    "Numeric note decision should be selected in the visible review form."
  );
  assert(
    (await panel.locator(".review-resolution-note").inputValue()).trim().length > 0,
    "Numeric note review note should be filled before resolving."
  );
  const numericResolution = await page.evaluate(async () => {
    const panel = document.querySelector("#reviewDetailPanel");
    const button = panel.querySelector(".resolve-review");
    const response = await fetch(`/api/review-items/${encodeURIComponent(button.dataset.reviewId)}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resolution_note: panel.querySelector(".review-resolution-note").value,
        resolved_value: panel.querySelector(".review-resolved-value").value,
        legacy_decision: "resolve",
        correction_entity_type: "review_item",
        correction_entity_id: button.dataset.reviewId,
        correction_field_name: "reviewed_value",
        correction_before_value: button.dataset.currentValue || "",
        correction_after_value: panel.querySelector(".review-resolved-value").value,
        note_item_id: button.dataset.noteItemId || "",
        note_label_decision: panel.querySelector(".note-label-decision").value,
        note_label_count: Number(panel.querySelector(".note-label-count").value || 0),
      }),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }
    return response.json();
  });
  assert(
    numericResolution.status === "resolved",
    `Numeric note review API should resolve the item: ${JSON.stringify(numericResolution)}`
  );
  await page.reload();
  await page.waitForLoadState("networkidle");
  const numericReviewAfterReload = await page.evaluate(async (reviewId) => {
    const response = await fetch("/api/review-items");
    const reviews = await response.json();
    return reviews.find((item) => item.review_id === reviewId) || null;
  }, numericResolution.review_id);
  assert(
    numericReviewAfterReload?.status === "resolved",
    `Numeric note review should be resolved after reload: ${JSON.stringify(numericReviewAfterReload)}`
  );
  try {
    await page.waitForFunction(
      () => !Array.from(document.querySelectorAll("#reviewRows .review-card h3"))
        .some((node) => node.textContent.includes("Unlabeled numeric note needs review"))
    );
  } catch (error) {
    const message = await page.locator("#reviewDetailPanel .review-resolution-message").textContent().catch(() => "");
    const detail = await page.locator("#reviewDetailPanel").innerText().catch(() => "");
    throw new Error(`Numeric note review did not resolve. Message: ${JSON.stringify(message || "")}. Detail panel: ${JSON.stringify(detail)}`);
  }
}

async function resolveEarLabelReview(page) {
  await inspectReviewByIssue(page, "Ear label needs review");
  const panel = page.locator("#reviewDetailPanel");
  await panel.locator(".ear-label-code").selectOption("R_PRIME");
  assert(
    (await panel.locator(".ear-label-code").inputValue()) === "R_PRIME",
    "Ear-label bounded select should retain the reviewed value."
  );
  await panel.locator(".review-resolution-note").fill(
    "Browser E2E checked the source note line and selected the bounded ear-label value from the review form."
  );
  assert(
    (await panel.locator(".review-resolution-note").inputValue()).trim().length > 0,
    "Ear-label review note should be filled before resolving."
  );
  await panel.locator(".resolve-review").click();
  await waitForText(page, "#reviewDetailPanel .review-resolution-message", "Submitting reviewed decision", 5000)
    .catch(() => {});
  try {
    await page.waitForFunction(
      () => !Array.from(document.querySelectorAll("#reviewRows .review-card h3"))
        .some((node) => node.textContent.includes("Ear label needs review"))
    );
  } catch (error) {
    const message = await page.locator("#reviewDetailPanel .review-resolution-message").textContent().catch(() => "");
    const detail = await page.locator("#reviewDetailPanel").innerText().catch(() => "");
    throw new Error(`Ear-label review did not resolve. Message: ${JSON.stringify(message || "")}. Detail panel: ${JSON.stringify(detail)}`);
  }
}

async function resolveSourceNoteReview(page) {
  const issues = await visibleReviewIssues(page);
  if (issues.some((issue) => issue.includes("Ear label needs review"))) {
    await resolveEarLabelReview(page);
    return "ear_label";
  }
  if (issues.some((issue) => issue.includes("Unlabeled numeric note needs review"))) {
    await resolveNumericNoteReview(page);
    return "numeric_note";
  }
  throw new Error(`Expected an ear-label or numeric note review. Visible issues: ${JSON.stringify(issues)}`);
}

async function mapComparisonReviewToCandidate(page) {
  await setView(page, "comparison");
  await page.locator("#comparisonReviewButton").click();
  await waitForText(page, "#comparisonReviewMessage", "comparison review item");

  await inspectReviewByIssue(page, "Photo transcription differs from predecessor Excel");
  const panel = page.locator("#reviewDetailPanel");
  await fillAndVerify(panel.locator(".review-resolved-value"), "draft candidate", "Comparison review resolved value");
  await fillAndVerify(
    panel.locator(".review-resolution-note"),
    "Browser E2E mapped reviewed photo-vs-Excel evidence into a canonical candidate draft.",
    "Comparison review note"
  );
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await panel.locator(".review-legacy-decision").selectOption("map_to_canonical_candidate");
    if ((await panel.locator(".review-legacy-decision").inputValue()) === "map_to_canonical_candidate") {
      break;
    }
    await page.waitForTimeout(100);
  }
  await fillAndVerify(panel.locator(".review-resolved-value"), "draft candidate", "Comparison review resolved value");
  await fillAndVerify(
    panel.locator(".review-resolution-note"),
    "Browser E2E mapped reviewed photo-vs-Excel evidence into a canonical candidate draft.",
    "Comparison review note"
  );
  assert(
    (await panel.locator(".review-resolution-note").inputValue()).trim().length > 0,
    "Comparison review note should be filled before mapping to a canonical candidate."
  );
  assert(
    (await panel.locator(".review-legacy-decision").inputValue()) === "map_to_canonical_candidate",
    "Comparison review decision should be set to map_to_canonical_candidate."
  );
  await panel.locator(".resolve-review").click();
  await waitForText(
    page,
    "#reviewDetailPanel .review-resolution-message",
    "may not create an apply-ready candidate"
  );
  assert(
    (await panel.locator(".review-resolution-message").getAttribute("data-warning-kind")) === "canonical-mapping-warning",
    "Mapping a candidate from weak note-line evidence should warn before creating a draft."
  );
  await panel.locator(".resolve-review").click();
  const mapped = await waitForValue(page, () => fetch("/api/canonical-candidates")
    .then((response) => response.json())
    .then((candidates) => candidates.find((candidate) => candidate.status === "draft") || null));
  assert(
    mapped.candidate_id,
    `Mapping comparison review should create a canonical candidate draft: ${JSON.stringify(mapped)}`
  );
  await page.reload();
  await page.waitForLoadState("networkidle");
  await waitForValue(page, () => fetch("/api/canonical-candidates")
    .then((response) => response.json())
    .then((candidates) => candidates.some((candidate) => candidate.status === "draft")));
}

async function resolveAuxiliaryReviewsThroughBrowser(page) {
  const resolved = await page.evaluate(async () => {
    async function api(url, options) {
      const response = await fetch(url, {
        headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${await response.text()}`);
      }
      return response.json();
    }

    const reviews = await api("/api/review-items");
    let count = 0;
    for (const item of reviews.filter((review) => review.status === "open")) {
      const payload = {
        resolution_note: "Browser E2E resolved auxiliary review after the target review path was exercised.",
        resolved_value: item.suggested_value || item.current_value || "reviewed",
        legacy_decision: "resolve",
        correction_entity_type: "review_item",
        correction_entity_id: item.review_id,
        correction_field_name: "reviewed_value",
        correction_before_value: item.current_value || "",
        correction_after_value: item.suggested_value || item.current_value || "reviewed",
      };
      if (item.issue === "Unlabeled numeric note needs review") {
        payload.note_item_id = item.note_item_id || "";
        payload.note_label_decision = "count_note";
        payload.note_label_count = Number(String(item.current_value || "").split(",").length || 1);
      }
      await api(`/api/review-items/${encodeURIComponent(item.review_id)}/resolve`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      count += 1;
    }
    return count;
  });
  await page.reload();
  await page.waitForLoadState("networkidle");
  return resolved;
}

async function applyCanonicalCandidate(page) {
  await setView(page, "review");
  await page.evaluate(async () => {
    if (typeof refresh === "function") await refresh();
  });
  try {
    await page.waitForSelector("#canonicalCandidateRows .preview-canonical-candidate");
  } catch (error) {
    const candidates = await page.evaluate(() => fetch("/api/canonical-candidates").then((response) => response.json()));
    throw new Error(`No draft canonical candidate preview button found. Candidates: ${JSON.stringify(candidates)}`);
  }
  await page.locator("#canonicalCandidateRows .preview-canonical-candidate").click();
  await waitForText(page, "#canonicalApplyPreviewPanel", "New mice");
  await page.locator("#canonicalCandidateRows .apply-canonical-candidate").click();
  await waitForValue(page, () => {
    const rows = Array.from(document.querySelectorAll("#canonicalCandidateRows tr"));
    return rows.some((row) => row.textContent.includes("applied"));
  });
}

async function downloadFinalExports(page) {
  await setView(page, "exports");
  await waitForText(page, "#exportReadinessState", "Ready for final export");
  const disabled = await page.locator("#exportReadyMouseCsvButton").isDisabled();
  assert(!disabled, "Ready CSV button should be enabled after review resolution and candidate apply.");

  const csvDownload = page.waitForEvent("download");
  await page.locator("#exportReadyMouseCsvButton").click();
  const csv = await csvDownload;
  assert(csv.suggestedFilename().endsWith(".csv"), "Ready CSV download should produce a CSV file.");

  const workbookDownload = page.waitForEvent("download");
  await page.locator("#exportSeparationXlsxButton").click();
  const workbook = await workbookDownload;
  assert(workbook.suggestedFilename().endsWith(".xlsx"), "Separation export should produce an XLSX file.");

  const animalWorkbookDownload = page.waitForEvent("download");
  await page.locator("#exportAnimalSheetXlsxButton").click();
  const animalWorkbook = await animalWorkbookDownload;
  assert(animalWorkbook.suggestedFilename().endsWith(".xlsx"), "Animal sheet export should produce an XLSX file.");
  await waitForText(page, "#exportDownloadMessage", "Downloaded");
}

async function closeReadyUploadBatch(page) {
  await setView(page, "photo");
  await page.locator(".preview-upload-batch-release").click();
  await waitForText(page, "#uploadBatchReleasePanel", "ready_to_close");
  const closeButton = page.locator("#uploadBatchReleasePanel .release-upload-batch");
  assert(!(await closeButton.isDisabled()), "Upload batch close button should be enabled after the full flow.");
  await closeButton.click();
  await waitForText(page, "#uploadMessage", "Closed");
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

async function run() {
  const tempRoot = fs.mkdtempSync(path.join(ROOT, "data", "browser-photo-export-e2e-"));
  const dataDir = path.join(tempRoot, "data");
  const legacyPath = path.join(tempRoot, "legacy_animal_upload.xlsx");
  fs.mkdirSync(dataDir, { recursive: true });
  createLegacyWorkbook(legacyPath);

  const port = 19000 + Math.floor(Math.random() * 1000);
  const baseUrl = `http://127.0.0.1:${port}`;
  const server = startServer(dataDir, port);
  const serverOutput = [];
  server.stdout.on("data", (chunk) => serverOutput.push(chunk.toString()));
  server.stderr.on("data", (chunk) => serverOutput.push(chunk.toString()));

  let browser = null;
  try {
    await waitForServer(baseUrl);
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const consoleErrors = [];
    const pageErrors = [];
    const failedResponses = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 400) {
        failedResponses.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("dialog", (dialog) => dialog.accept());

    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await waitForText(page, "#viewTitle", "Photo Review Workbench");
    assert(
      (await page.locator("body").innerText()).includes("Upload Source Photo"),
      "The browser should render the local app shell, not an empty page."
    );

    await setView(page, "settings");
    await page.selectOption("#legacyWorkbookKind", "animal");
    await page.setInputFiles("#legacyWorkbookFile", legacyPath);
    const legacyImportResponsePromise = page.waitForResponse((response) =>
      response.url().endsWith("/api/legacy-workbook-imports") &&
      response.request().method() === "POST"
    );
    await page.locator("#legacyWorkbookButton").click();
    const legacyImportResponse = await legacyImportResponsePromise;
    const legacyImportBody = await legacyImportResponse.text();
    assert(
      legacyImportResponse.ok(),
      `Legacy workbook import failed: ${legacyImportResponse.status()} ${legacyImportBody}`
    );
    await waitForText(page, "#legacyWorkbookMessage", "Imported");
    await page.fill("#assignedStrainName", "ApoM Tg/Tg");
    await page.fill("#assignedStrainAliases", "ApoM");
    const assignedStrainResponsePromise = page.waitForResponse((response) =>
      response.url().endsWith("/api/assigned-strains") &&
      response.request().method() === "POST"
    );
    await page.locator("#assignedStrainButton").click();
    const assignedStrainResponse = await assignedStrainResponsePromise;
    const assignedStrainBody = await assignedStrainResponse.text();
    assert(
      assignedStrainResponse.ok(),
      `Assigned strain scope creation failed: ${assignedStrainResponse.status()} ${assignedStrainBody}`
    );
    await waitForText(page, "#assignedStrainMessage", "Added ApoM Tg/Tg");

    await setView(page, "photo");
    await waitForText(page, "#aiDraftMessage", "AI extraction ready");
    const photoPath = path.join(ROOT, "static", "assets", "cage-card-evidence-art.png");
    await page.setInputFiles("#photoFile", photoPath);
    await page.locator("#uploadButton").click();
    await waitForText(page, "#uploadMessage", "saved 1 extracted review transcription");
    await waitForValue(page, () => {
      const select = document.querySelector("#transcriptionPhotoId");
      return select && select.value && !select.textContent.includes("No photos uploaded");
    });
    const aiExtractionEvidence = await waitForValue(page, () =>
      fetch("/api/photo-review-workbench")
        .then((response) => response.json())
        .then((workbench) => {
          const row = (workbench.rows || []).find((item) => item.manual_source_name === "ai_photo_extraction");
          if (!row) return null;
          const payload = row.manual_payload || {};
          if (!payload.externalApproval?.approved_external_inference) return null;
          return {
            parse_id: row.manual_parse_id,
            extraction_method: payload.extractionMethod,
            external_approval: payload.externalApproval,
            payload_minimization: payload.payloadMinimization,
            manual_source_name: row.manual_source_name,
          };
        })
    );

    const sourceNoteReview = await resolveSourceNoteReview(page);
    await mapComparisonReviewToCandidate(page);
    const auxiliaryResolved = await resolveAuxiliaryReviewsThroughBrowser(page);
    const candidatesAfterAuxiliaryResolution = await page.evaluate(() =>
      fetch("/api/canonical-candidates").then((response) => response.json())
    );
    assert(
      candidatesAfterAuxiliaryResolution.some((candidate) => candidate.status === "draft"),
      `Draft candidate should remain after auxiliary review resolution: ${JSON.stringify(candidatesAfterAuxiliaryResolution)}`
    );
    await applyCanonicalCandidate(page);
    await downloadFinalExports(page);
    await closeReadyUploadBatch(page);

    assert(pageErrors.length === 0, `Browser page errors occurred: ${pageErrors.join("\\n")}`);
    const relevantFailedResponses = failedResponses.filter((entry) =>
      !entry.includes("/api/photos/") || !entry.includes("/roi/")
    );
    const relevantConsoleErrors = consoleErrors.filter((entry) =>
      !entry.includes("Failed to load resource: the server responded with a status of 400")
    );
    assert(relevantFailedResponses.length === 0, `Unexpected failed browser responses: ${relevantFailedResponses.join("\\n")}`);
    assert(relevantConsoleErrors.length === 0, `Browser console errors occurred: ${relevantConsoleErrors.join("\\n")}`);

    console.log(JSON.stringify({
      status: "passed",
      url: baseUrl,
      extraction_method: aiExtractionEvidence.extraction_method,
      external_approval: aiExtractionEvidence.external_approval,
      payload_minimization: aiExtractionEvidence.payload_minimization,
      source_note_review: sourceNoteReview,
      auxiliary_reviews_resolved: auxiliaryResolved,
      ignored_roi_response_count: failedResponses.length - relevantFailedResponses.length,
      data_boundary: {
        photo_upload: "raw source",
        ai_extraction: "parsed or intermediate result",
        review_resolution: "review item",
        candidate_apply: "canonical structured state",
        export_download: "export or view",
        upload_batch_release: "raw source batch workflow",
      },
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer(server);
    await removeTempRoot(tempRoot);
    if (server.exitCode && server.exitCode !== 0) {
      process.stderr.write(serverOutput.join(""));
    }
  }
}

run().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
