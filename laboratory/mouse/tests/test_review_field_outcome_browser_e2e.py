from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_exposes_review_field_outcome_browser_e2e_script() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["test:review-field-outcome-e2e"] == (
        "node scripts/verify-review-field-outcome-e2e.js"
    )
    assert "npm run test:review-field-outcome-e2e" in package["scripts"]["verify"]
    assert (ROOT / "scripts" / "verify-review-field-outcome-e2e.js").exists()


def test_review_field_outcome_browser_e2e_asserts_submit_payload_field_scores() -> None:
    script = (ROOT / "scripts" / "verify-review-field-outcome-e2e.js").read_text(encoding="utf-8")

    assert "fillReviewFieldOutcomeControls" in script
    assert "resolveRequestPayload?.field_review_outcome?.field_scores?.mouse_ids_or_note_lines?.status" in script
    assert "Browser payload should include corrected mouse-id outcome" in script


def test_review_field_outcome_browser_e2e_asserts_side_by_side_workbench() -> None:
    script = (ROOT / "scripts" / "verify-review-field-outcome-e2e.js").read_text(encoding="utf-8")

    assert ".field-review-workbench" in script
    assert ".use-field-review-value" in script
    assert "checked_workbench_field" in script
    assert "Browser payload should include checked workbench field" in script


def test_review_field_outcome_browser_e2e_reaches_sanitized_reporter_input() -> None:
    result = subprocess.run(
        ["node", "scripts/verify-review-field-outcome-e2e.js"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["data_boundary"]["review_resolution"] == "review item"
    assert payload["data_boundary"]["reporter_input"] == "parsed or intermediate result"
    assert payload["matched_case_count"] == 1
    assert payload["report_decision"] == "go"
