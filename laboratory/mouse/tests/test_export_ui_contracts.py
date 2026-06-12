from pathlib import Path


HTML = Path("static/index.html").read_text(encoding="utf-8")


def test_export_preview_worklist_actions_are_named_separately_from_final_files() -> None:
    assert "Preview Search & Worklists" in HTML
    assert "Search & CSV Export" not in HTML
    assert "Download Preview Mouse CSV" in HTML
    assert "Download Genotyping Worklist" in HTML
    assert "Final export actions" in HTML
