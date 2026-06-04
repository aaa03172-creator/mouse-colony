from __future__ import annotations

import json
import sqlite3
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def configured_path(env_name: str, fallback: Path) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured).expanduser().resolve()
    return fallback


DATA_DIR = configured_path("MOUSEDB_DATA_DIR", ROOT / "data")
DB_PATH = configured_path("MOUSEDB_DB_PATH", DATA_DIR / "mouse_lims.sqlite")


EAR_LABEL_MASTER_SEEDS = [
    ("R_PRIME", "R'", "right ear prime mark"),
    ("L_PRIME", "L'", "left ear prime mark"),
    ("R_CIRCLE", "R\u00b0", "right ear circle mark"),
    ("L_CIRCLE", "L\u00b0", "left ear circle mark"),
    ("R_PRIME_L_PRIME", "R'L'", "right prime + left prime"),
    ("R_CIRCLE_L_CIRCLE", "R\u00b0L\u00b0", "right circle + left circle"),
    ("R_PRIME_L_CIRCLE", "R'L\u00b0", "right prime + left circle"),
    ("R_CIRCLE_L_PRIME", "R\u00b0L'", "right circle + left prime"),
    ("R_DOUBLE_CIRCLE", "R\u00b0\u00b0", "right ear double circle mark"),
    ("L_DOUBLE_CIRCLE", "L\u00b0\u00b0", "left ear double circle mark"),
    ("R_DOUBLE_CIRCLE_L_DOUBLE_CIRCLE", "R\u00b0\u00b0L\u00b0\u00b0", "right double circle + left double circle"),
    ("R_PRIME_L_DOUBLE_CIRCLE", "R'L\u00b0\u00b0", "right prime + left double circle"),
    ("R_DOUBLE_CIRCLE_L_PRIME", "R\u00b0\u00b0L'", "right double circle + left prime"),
    ("NONE", "N", "no ear label / no mark"),
]


LABELING_RULE_SEED_CONFIG_PATH = ROOT / "config" / "seeds" / "labeling_rule_sets.json"


def load_labeling_rule_seed_config(path: Path = LABELING_RULE_SEED_CONFIG_PATH) -> tuple[list[tuple], list[tuple]]:
    if not path.exists():
        return [], []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rule_set_rows = []
    ear_sequence_rows = []
    for rule in payload.get("rule_sets", []):
        if not isinstance(rule, dict):
            continue
        rule_set_id = str(rule.get("rule_set_id") or "").strip()
        if not rule_set_id:
            continue
        rule_set_rows.append(
            (
                rule_set_id,
                str(rule.get("display_name") or ""),
                str(rule.get("applies_to_strain_text") or ""),
                str(rule.get("session_date") or ""),
                str(rule.get("numbering_order") or ""),
                str(rule.get("mouse_number_scope") or ""),
                str(rule.get("ear_sequence_scope") or ""),
                str(rule.get("crossed_out_handling") or ""),
                str(rule.get("sample_mapping") or ""),
                str(rule.get("genotyping_target") or ""),
                int(rule.get("active") or 0),
            )
        )
        for index, ear_label_code in enumerate(rule.get("ear_sequence", []), start=1):
            ear_sequence_rows.append((rule_set_id, index, str(ear_label_code or "")))
    return rule_set_rows, ear_sequence_rows


EAR_LABEL_ALIAS_SEEDS = [
    ("ear_alias_r_prime_ascii", "R'", "R_PRIME", 1.0, 1),
    ("ear_alias_r_prime_unicode", "R\u2032", "R_PRIME", 0.98, 1),
    ("ear_alias_r_prime_curly", "R\u2019", "R_PRIME", 0.98, 1),
    ("ear_alias_l_prime_ascii", "L'", "L_PRIME", 1.0, 1),
    ("ear_alias_l_prime_unicode", "L\u2032", "L_PRIME", 0.98, 1),
    ("ear_alias_l_prime_curly", "L\u2019", "L_PRIME", 0.98, 1),
    ("ear_alias_r_circle_degree", "R\u00b0", "R_CIRCLE", 1.0, 1),
    ("ear_alias_r_circle_ordinal", "R\u00ba", "R_CIRCLE", 0.92, 1),
    ("ear_alias_r_circle_ring", "R\u02da", "R_CIRCLE", 0.92, 1),
    ("ear_alias_l_circle_degree", "L\u00b0", "L_CIRCLE", 1.0, 1),
    ("ear_alias_l_circle_ordinal", "L\u00ba", "L_CIRCLE", 0.92, 1),
    ("ear_alias_l_circle_ring", "L\u02da", "L_CIRCLE", 0.92, 1),
    ("ear_alias_none_n", "N", "NONE", 1.0, 1),
    ("ear_alias_r_circle_zero", "R0", "R_CIRCLE", 0.65, 0),
    ("ear_alias_r_circle_o", "Ro", "R_CIRCLE", 0.65, 0),
    ("ear_alias_l_circle_zero", "L0", "L_CIRCLE", 0.65, 0),
    ("ear_alias_l_circle_o", "Lo", "L_CIRCLE", 0.65, 0),
]


GENOTYPE_STATUS_MASTER_SEEDS = [
    ("not_requested", "Not requested", "No genotyping request or sample has been recorded.", "pre_result", 1, 1, "not_sampled", 10),
    ("requested", "Requested", "Genotyping has been requested but no result is available.", "in_progress", 1, 1, "submitted", 20),
    ("sample_collected", "Sample collected", "A tail/sample was collected and is waiting for submission or result.", "in_progress", 1, 1, "sampled", 30),
    ("pending", "Pending", "A submitted sample is waiting for a genotype result.", "in_progress", 1, 1, "pending", 40),
    ("failed", "Failed", "The assay failed and should be reviewed or repeated.", "review", 1, 1, "failed", 50),
    ("inconclusive", "Inconclusive", "The result is ambiguous and must remain reviewable.", "review", 1, 1, "inconclusive", 60),
    ("repeat_requested", "Repeat requested", "A repeat genotyping run has been requested.", "review", 1, 1, "repeat_requested", 70),
    ("confirmed", "Confirmed", "A genotype result has been accepted for operational use.", "final", 0, 0, "resulted", 80),
    ("superseded", "Superseded", "A previous genotype result was replaced by newer evidence.", "final", 1, 1, "superseded", 90),
]


REVIEW_ROLE_MASTER_SEEDS = [
    (
        "colony_reviewer",
        "Colony Reviewer",
        "Reviews cage-card photos, note-line evidence, mouse identity continuity, cage movement, and day-to-day colony state.",
        "medium",
        10,
    ),
    (
        "strain_curator",
        "Strain Curator",
        "Reviews strain aliases, assigned strain scope, allele/genotype wording, and strain registry corrections.",
        "medium",
        20,
    ),
    (
        "experiment_planner",
        "Experiment Planner",
        "Reviews experiment readiness, sample/genotyping worklists, and mice that should or should not be released for use.",
        "high",
        30,
    ),
    (
        "data_export_manager",
        "Data / Export Manager",
        "Reviews predecessor Excel rows, workbook reconciliation, export blockers, and final Excel handoff readiness.",
        "medium",
        40,
    ),
]


REVIEW_PRIORITY_MASTER_SEEDS = [
    (
        "high",
        "High",
        1,
        1,
        "Blocks export, experiment release, or risky canonical state changes until reviewed.",
        10,
    ),
    (
        "medium",
        "Medium",
        2,
        1,
        "Should be reviewed before routine export, but can usually wait behind high-risk identity or genotype conflicts.",
        20,
    ),
    (
        "low",
        "Low",
        3,
        0,
        "Useful cleanup or documentation review that should stay visible without stopping urgent work.",
        30,
    ),
]


def ensure_data_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "photos").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "legacy_workbooks").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "exports").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "roi").mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sqlite_add_column_definition(definition: str) -> str:
    normalized = " ".join(definition.upper().split())
    if "DEFAULT CURRENT_TIMESTAMP" in normalized:
        return definition.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT ''")
    return definition


def ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, definition in columns.items():
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sqlite_add_column_definition(definition)}"
            )


def ensure_schema_compatibility(conn: sqlite3.Connection) -> None:
    ensure_columns(
        conn,
        "photo_log",
        {
            "upload_batch_id": "TEXT",
            "raw_source_kind": "TEXT NOT NULL DEFAULT 'cage_card_photo'",
            "source_layer": "TEXT NOT NULL DEFAULT 'raw source'",
        },
    )
    ensure_columns(
        conn,
        "parse_result",
        {
            "source_layer": "TEXT NOT NULL DEFAULT 'parsed or intermediate result'",
        },
    )
    ensure_columns(
        conn,
        "photo_evidence_item",
        {
            "raw_extracted_value": "TEXT NOT NULL DEFAULT ''",
            "normalized_value": "TEXT NOT NULL DEFAULT ''",
            "confidence_source": "TEXT NOT NULL DEFAULT ''",
            "evidence_reference_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    ensure_columns(
        conn,
        "mouse_master",
        {
            "id_prefix": "TEXT NOT NULL DEFAULT ''",
            "strain_id": "TEXT",
            "father_id": "TEXT",
            "mother_id": "TEXT",
            "litter_id": "TEXT",
            "raw_strain_text": "TEXT NOT NULL DEFAULT ''",
            "sex": "TEXT",
            "genotype": "TEXT",
            "genotype_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "dob_raw": "TEXT",
            "dob_start": "TEXT",
            "dob_end": "TEXT",
            "ear_label_raw": "TEXT",
            "ear_label_code": "TEXT",
            "ear_label_confidence": "REAL",
            "ear_label_review_status": "TEXT NOT NULL DEFAULT 'auto_filled'",
            "sample_id": "TEXT",
            "sample_date": "TEXT",
            "genotyping_status": "TEXT NOT NULL DEFAULT 'not_sampled'",
            "genotype_result": "TEXT",
            "genotype_result_date": "TEXT",
            "target_match_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "use_category": "TEXT NOT NULL DEFAULT 'unknown'",
            "next_action": "TEXT NOT NULL DEFAULT 'sample_needed'",
            "source_note_item_id": "TEXT",
            "current_card_snapshot_id": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "source_photo_id": "TEXT",
            "source_record_id": "TEXT",
            "last_verified_at": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
    )
    ensure_columns(
        conn,
        "card_note_item_log",
        {
            "photo_id": "TEXT",
            "parse_id": "TEXT",
            "card_snapshot_id": "TEXT",
            "card_type": "TEXT NOT NULL DEFAULT 'unknown'",
            "line_number": "INTEGER",
            "raw_line_text": "TEXT NOT NULL DEFAULT ''",
            "strike_status": "TEXT NOT NULL DEFAULT 'none'",
            "parsed_type": "TEXT NOT NULL DEFAULT 'unknown'",
            "interpreted_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "parsed_mouse_display_id": "TEXT",
            "parsed_ear_label_raw": "TEXT",
            "parsed_ear_label_code": "TEXT",
            "parsed_ear_label_confidence": "REAL",
            "parsed_ear_label_review_status": "TEXT NOT NULL DEFAULT 'auto_filled'",
            "parsed_event_date": "TEXT",
            "parsed_count": "INTEGER",
            "parsed_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "confidence": "REAL NOT NULL DEFAULT 0",
            "needs_review": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
    )
    ensure_columns(
        conn,
        "genotyping_record",
        {
            "submitted_date": "TEXT",
            "target_name": "TEXT",
            "raw_result": "TEXT",
            "normalized_result": "TEXT",
            "result_status": "TEXT NOT NULL DEFAULT 'pending'",
            "source_photo_id": "TEXT",
            "source_record_id": "TEXT",
            "photo_evidence_id": "TEXT",
            "confidence": "REAL NOT NULL DEFAULT 0",
            "notes": "TEXT",
            "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
    )
    ensure_columns(
        conn,
        "legacy_workbook_row",
        {
            "review_id": "TEXT",
        },
    )
    ensure_columns(
        conn,
        "review_queue",
        {
            "assigned_role": "TEXT NOT NULL DEFAULT 'Colony Reviewer'",
            "assigned_to": "TEXT NOT NULL DEFAULT ''",
            "priority": "TEXT NOT NULL DEFAULT 'medium'",
            "source_layer": "TEXT NOT NULL DEFAULT 'review item'",
            "evidence_reference_json": "TEXT NOT NULL DEFAULT '{}'",
            "review_trigger_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    ensure_columns(
        conn,
        "correction_log",
        {
            "source_layer": "TEXT NOT NULL DEFAULT 'review item'",
            "evidence_reference_json": "TEXT NOT NULL DEFAULT '{}'",
            "correction_context_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    ensure_columns(
        conn,
        "action_log",
        {
            "performed_by": "TEXT NOT NULL DEFAULT 'local_user'",
            "performed_role": "TEXT NOT NULL DEFAULT 'Colony Reviewer'",
        },
    )
    ensure_columns(
        conn,
        "export_log",
        {
            "generated_by": "TEXT NOT NULL DEFAULT 'local_user'",
            "generated_role": "TEXT NOT NULL DEFAULT 'Data / Export Manager'",
        },
    )
    ensure_columns(
        conn,
        "gene_master",
        {
            "description": "TEXT NOT NULL DEFAULT ''",
            "external_reference": "TEXT NOT NULL DEFAULT ''",
        },
    )
    ensure_columns(
        conn,
        "allele_master",
        {
            "allele_type": "TEXT NOT NULL DEFAULT ''",
            "inheritance": "TEXT NOT NULL DEFAULT ''",
            "zygosity_options": "TEXT NOT NULL DEFAULT ''",
            "genotyping_protocol": "TEXT NOT NULL DEFAULT ''",
        },
    )
    for table_name, column_name in [
        ("mouse_master", "created_at"),
        ("mouse_master", "updated_at"),
        ("card_note_item_log", "created_at"),
        ("genotyping_record", "updated_at"),
    ]:
        existing = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing:
            conn.execute(
                f"UPDATE {table_name} SET {column_name} = CURRENT_TIMESTAMP WHERE {column_name} = '' OR {column_name} IS NULL"
            )
    conn.execute(
        """
        UPDATE review_queue
        SET priority = CASE
                WHEN LOWER(severity) = 'high' THEN 'high'
                WHEN LOWER(severity) = 'low' THEN 'low'
                ELSE priority
            END
        WHERE priority = 'medium'
        """
    )
    conn.execute(
        """
        UPDATE review_queue
        SET assigned_role = 'Data / Export Manager'
        WHERE assigned_role = 'Colony Reviewer'
          AND (
              LOWER(issue) LIKE '%excel%'
              OR LOWER(issue) LIKE '%export%'
              OR LOWER(issue) LIKE '%workbook%'
              OR LOWER(issue) LIKE '%legacy%'
              OR LOWER(issue) LIKE '%comparison%'
          )
        """
    )
    conn.execute(
        """
        UPDATE review_queue
        SET assigned_role = 'Strain Curator'
        WHERE assigned_role = 'Colony Reviewer'
          AND (
              LOWER(issue) LIKE '%strain%'
              OR LOWER(issue) LIKE '%allele%'
              OR LOWER(review_reason) LIKE '%strain%'
          )
        """
    )


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_batch (
                upload_batch_id TEXT PRIMARY KEY,
                batch_label TEXT NOT NULL,
                expected_photo_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                source_layer TEXT NOT NULL DEFAULT 'raw source',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS photo_log (
                photo_id TEXT PRIMARY KEY,
                upload_batch_id TEXT,
                original_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_source_kind TEXT NOT NULL DEFAULT 'cage_card_photo',
                source_layer TEXT NOT NULL DEFAULT 'raw source',
                FOREIGN KEY (upload_batch_id) REFERENCES upload_batch(upload_batch_id)
            );

            CREATE TABLE IF NOT EXISTS parse_result (
                parse_id TEXT PRIMARY KEY,
                photo_id TEXT,
                source_name TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                parsed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                needs_review INTEGER NOT NULL DEFAULT 1,
                source_layer TEXT NOT NULL DEFAULT 'parsed or intermediate result',
                FOREIGN KEY (photo_id) REFERENCES photo_log(photo_id)
            );

            CREATE TABLE IF NOT EXISTS review_queue (
                review_id TEXT PRIMARY KEY,
                parse_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                issue TEXT NOT NULL,
                current_value TEXT NOT NULL DEFAULT '',
                suggested_value TEXT NOT NULL DEFAULT '',
                review_reason TEXT NOT NULL DEFAULT '',
                assigned_role TEXT NOT NULL DEFAULT 'Colony Reviewer',
                assigned_to TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'medium',
                source_layer TEXT NOT NULL DEFAULT 'review item',
                evidence_reference_json TEXT NOT NULL DEFAULT '{}',
                review_trigger_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution_note TEXT,
                FOREIGN KEY (parse_id) REFERENCES parse_result(parse_id)
            );

            CREATE TABLE IF NOT EXISTS action_log (
                action_id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                performed_by TEXT NOT NULL DEFAULT 'local_user',
                performed_role TEXT NOT NULL DEFAULT 'Colony Reviewer',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_record (
                source_record_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_uri TEXT NOT NULL DEFAULT '',
                source_label TEXT NOT NULL DEFAULT '',
                raw_payload TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL,
                checksum TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS strain_registry (
                strain_id TEXT PRIMARY KEY,
                strain_name TEXT NOT NULL,
                common_name TEXT NOT NULL DEFAULT '',
                official_name TEXT NOT NULL DEFAULT '',
                gene TEXT NOT NULL DEFAULT '',
                allele TEXT NOT NULL DEFAULT '',
                background TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                breeding_note TEXT NOT NULL DEFAULT '',
                genotyping_note TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS gene_master (
                gene_id TEXT PRIMARY KEY,
                gene_symbol TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                external_reference TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS allele_master (
                allele_id TEXT PRIMARY KEY,
                allele_symbol TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                allele_type TEXT NOT NULL DEFAULT '',
                inheritance TEXT NOT NULL DEFAULT '',
                zygosity_options TEXT NOT NULL DEFAULT '',
                genotyping_protocol TEXT NOT NULL DEFAULT '',
                gene_id TEXT,
                source_record_id TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (gene_id) REFERENCES gene_master(gene_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS strain_allele_relationship (
                relationship_id TEXT PRIMARY KEY,
                strain_id TEXT NOT NULL,
                gene_id TEXT,
                allele_id TEXT,
                relationship_type TEXT NOT NULL DEFAULT 'configured_from_strain_registry',
                source_record_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (strain_id) REFERENCES strain_registry(strain_id),
                FOREIGN KEY (gene_id) REFERENCES gene_master(gene_id),
                FOREIGN KEY (allele_id) REFERENCES allele_master(allele_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS correction_log (
                correction_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                before_value TEXT NOT NULL DEFAULT '',
                after_value TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                review_id TEXT,
                source_layer TEXT NOT NULL DEFAULT 'review item',
                evidence_reference_json TEXT NOT NULL DEFAULT '{}',
                correction_context_json TEXT NOT NULL DEFAULT '{}',
                corrected_at TEXT NOT NULL,
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id),
                FOREIGN KEY (review_id) REFERENCES review_queue(review_id)
            );

            CREATE TABLE IF NOT EXISTS canonical_candidate (
                candidate_id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL,
                parse_id TEXT NOT NULL,
                legacy_row_id TEXT NOT NULL DEFAULT '',
                proposed_mouse_display_id TEXT NOT NULL DEFAULT '',
                proposed_strain TEXT NOT NULL DEFAULT '',
                proposed_dob TEXT NOT NULL DEFAULT '',
                proposed_count TEXT NOT NULL DEFAULT '',
                candidate_payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (review_id) REFERENCES review_queue(review_id),
                FOREIGN KEY (parse_id) REFERENCES parse_result(parse_id)
            );

            CREATE TABLE IF NOT EXISTS card_snapshot (
                card_snapshot_id TEXT PRIMARY KEY,
                photo_id TEXT,
                parse_id TEXT,
                card_type TEXT NOT NULL DEFAULT 'unknown',
                card_id_raw TEXT NOT NULL DEFAULT '',
                raw_strain_text TEXT NOT NULL DEFAULT '',
                matched_strain_text TEXT NOT NULL DEFAULT '',
                sex_raw TEXT NOT NULL DEFAULT '',
                sex_normalized TEXT NOT NULL DEFAULT '',
                sex_count_raw TEXT NOT NULL DEFAULT '',
                count_value INTEGER,
                dob_raw TEXT NOT NULL DEFAULT '',
                dob_start TEXT,
                dob_end TEXT,
                mating_date_raw TEXT NOT NULL DEFAULT '',
                mating_date_normalized TEXT NOT NULL DEFAULT '',
                lmo_raw TEXT NOT NULL DEFAULT '',
                note_summary_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'review',
                source_layer TEXT NOT NULL DEFAULT 'parsed or intermediate result',
                confidence REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (photo_id) REFERENCES photo_log(photo_id),
                FOREIGN KEY (parse_id) REFERENCES parse_result(parse_id)
            );

            CREATE TABLE IF NOT EXISTS export_log (
                export_id TEXT PRIMARY KEY,
                export_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                row_count INTEGER NOT NULL DEFAULT 0,
                blocked_review_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'generated',
                exported_at TEXT NOT NULL,
                source_layer TEXT NOT NULL DEFAULT 'export or view',
                generated_by TEXT NOT NULL DEFAULT 'local_user',
                generated_role TEXT NOT NULL DEFAULT 'Data / Export Manager',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS mouse_event (
                event_id TEXT PRIMARY KEY,
                mouse_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                related_entity_type TEXT NOT NULL DEFAULT '',
                related_entity_id TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                details TEXT NOT NULL DEFAULT '{}',
                created_by TEXT NOT NULL DEFAULT 'local_user',
                created_at TEXT NOT NULL,
                FOREIGN KEY (mouse_id) REFERENCES mouse_master(mouse_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS genotyping_record (
                genotyping_id TEXT PRIMARY KEY,
                mouse_id TEXT,
                sample_id TEXT NOT NULL DEFAULT '',
                sample_date TEXT,
                submitted_date TEXT,
                result_date TEXT,
                target_name TEXT NOT NULL DEFAULT '',
                raw_result TEXT NOT NULL DEFAULT '',
                normalized_result TEXT NOT NULL DEFAULT '',
                result_status TEXT NOT NULL DEFAULT 'pending',
                source_photo_id TEXT,
                source_record_id TEXT,
                photo_evidence_id TEXT,
                confidence REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (mouse_id) REFERENCES mouse_master(mouse_id),
                FOREIGN KEY (source_photo_id) REFERENCES photo_log(photo_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id),
                FOREIGN KEY (photo_evidence_id) REFERENCES photo_evidence_item(photo_evidence_id)
            );

            CREATE TABLE IF NOT EXISTS photo_evidence_item (
                photo_evidence_id TEXT PRIMARY KEY,
                source_photo_id TEXT NOT NULL,
                parse_id TEXT,
                card_snapshot_id TEXT,
                note_item_id TEXT,
                card_type TEXT NOT NULL DEFAULT '',
                evidence_kind TEXT NOT NULL,
                roi_label TEXT NOT NULL DEFAULT '',
                bbox_json TEXT NOT NULL DEFAULT '{}',
                observed_raw_text TEXT NOT NULL DEFAULT '',
                ocr_text TEXT NOT NULL DEFAULT '',
                parsed_value TEXT NOT NULL DEFAULT '',
                raw_extracted_value TEXT NOT NULL DEFAULT '',
                normalized_value TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                confidence_source TEXT NOT NULL DEFAULT '',
                evidence_reference_json TEXT NOT NULL DEFAULT '{}',
                interpretation TEXT NOT NULL DEFAULT '',
                needs_review INTEGER NOT NULL DEFAULT 1,
                review_reason TEXT NOT NULL DEFAULT '',
                linked_mouse_id TEXT,
                linked_cage_id TEXT,
                linked_event_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_photo_id) REFERENCES photo_log(photo_id),
                FOREIGN KEY (parse_id) REFERENCES parse_result(parse_id),
                FOREIGN KEY (card_snapshot_id) REFERENCES card_snapshot(card_snapshot_id),
                FOREIGN KEY (note_item_id) REFERENCES card_note_item_log(note_item_id),
                FOREIGN KEY (linked_mouse_id) REFERENCES mouse_master(mouse_id),
                FOREIGN KEY (linked_cage_id) REFERENCES cage_registry(cage_id),
                FOREIGN KEY (linked_event_id) REFERENCES mouse_event(event_id)
            );

            CREATE TABLE IF NOT EXISTS review_evidence_link (
                link_id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL,
                photo_evidence_id TEXT NOT NULL,
                link_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (review_id) REFERENCES review_queue(review_id),
                FOREIGN KEY (photo_evidence_id) REFERENCES photo_evidence_item(photo_evidence_id),
                UNIQUE (review_id, photo_evidence_id)
            );

            CREATE TABLE IF NOT EXISTS strain_target_genotype (
                target_id TEXT PRIMARY KEY,
                strain_text TEXT NOT NULL,
                target_genotype TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'unknown',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE (strain_text, target_genotype, purpose)
            );

            CREATE TABLE IF NOT EXISTS genotype_status_master (
                status_key TEXT PRIMARY KEY,
                display_label TEXT NOT NULL,
                meaning TEXT NOT NULL DEFAULT '',
                workflow_stage TEXT NOT NULL DEFAULT 'review',
                blocks_experiment INTEGER NOT NULL DEFAULT 1,
                export_warning INTEGER NOT NULL DEFAULT 1,
                legacy_genotyping_status TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS review_role_master (
                role_key TEXT PRIMARY KEY,
                display_label TEXT NOT NULL UNIQUE,
                responsibility TEXT NOT NULL DEFAULT '',
                default_priority TEXT NOT NULL DEFAULT 'medium',
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS review_priority_master (
                priority_key TEXT PRIMARY KEY,
                display_label TEXT NOT NULL,
                severity_rank INTEGER NOT NULL DEFAULT 2,
                export_blocking_hint INTEGER NOT NULL DEFAULT 1,
                response_expectation TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cage_registry (
                cage_id TEXT PRIMARY KEY,
                cage_label TEXT NOT NULL UNIQUE,
                location TEXT NOT NULL DEFAULT '',
                rack TEXT NOT NULL DEFAULT '',
                shelf TEXT NOT NULL DEFAULT '',
                cage_type TEXT NOT NULL DEFAULT 'holding',
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS mouse_cage_assignment (
                assignment_id TEXT PRIMARY KEY,
                mouse_id TEXT NOT NULL,
                cage_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                assigned_at TEXT NOT NULL,
                ended_at TEXT,
                source_record_id TEXT,
                note TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (mouse_id) REFERENCES mouse_master(mouse_id),
                FOREIGN KEY (cage_id) REFERENCES cage_registry(cage_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS mating_registry (
                mating_id TEXT PRIMARY KEY,
                mating_label TEXT NOT NULL,
                strain_goal TEXT NOT NULL DEFAULT '',
                expected_genotype TEXT NOT NULL DEFAULT '',
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                purpose TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS mating_mouse (
                mating_mouse_id TEXT PRIMARY KEY,
                mating_id TEXT NOT NULL,
                mouse_id TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_date TEXT NOT NULL DEFAULT '',
                removed_date TEXT,
                note TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (mating_id) REFERENCES mating_registry(mating_id),
                FOREIGN KEY (mouse_id) REFERENCES mouse_master(mouse_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS litter_registry (
                litter_id TEXT PRIMARY KEY,
                litter_label TEXT NOT NULL,
                mating_id TEXT NOT NULL,
                birth_date TEXT NOT NULL DEFAULT '',
                number_born INTEGER,
                number_alive INTEGER,
                number_weaned INTEGER,
                weaning_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'born',
                note TEXT NOT NULL DEFAULT '',
                source_record_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (mating_id) REFERENCES mating_registry(mating_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS my_assigned_strain (
                assigned_strain_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_reference TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                assigned_at TEXT NOT NULL,
                removed_at TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS distribution_import (
                distribution_import_id TEXT PRIMARY KEY,
                source_file_name TEXT NOT NULL,
                source_file_path TEXT NOT NULL DEFAULT '',
                received_date TEXT NOT NULL DEFAULT '',
                sheet_name TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'parsed',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS distribution_assignment_row (
                assignment_row_id TEXT PRIMARY KEY,
                distribution_import_id TEXT NOT NULL,
                source_sheet TEXT NOT NULL DEFAULT '',
                source_row_number INTEGER,
                institution_or_group TEXT NOT NULL DEFAULT '',
                responsible_person_raw TEXT NOT NULL DEFAULT '',
                mating_type_raw TEXT NOT NULL DEFAULT '',
                matched_strain_id TEXT,
                cage_count_raw TEXT NOT NULL DEFAULT '',
                mating_cage_count_raw TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'candidate',
                traceability TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (distribution_import_id) REFERENCES distribution_import(distribution_import_id)
            );

            CREATE TABLE IF NOT EXISTS legacy_workbook_import (
                legacy_import_id TEXT PRIMARY KEY,
                source_record_id TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                source_file_path TEXT NOT NULL DEFAULT '',
                workbook_kind TEXT NOT NULL DEFAULT '',
                sheet_name TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'parsed',
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );

            CREATE TABLE IF NOT EXISTS legacy_workbook_row (
                legacy_row_id TEXT PRIMARY KEY,
                legacy_import_id TEXT NOT NULL,
                review_id TEXT,
                row_type TEXT NOT NULL DEFAULT '',
                source_sheet TEXT NOT NULL DEFAULT '',
                source_row_number INTEGER,
                raw_row_json TEXT NOT NULL DEFAULT '{}',
                review_status TEXT NOT NULL DEFAULT 'candidate',
                FOREIGN KEY (review_id) REFERENCES review_queue(review_id),
                FOREIGN KEY (legacy_import_id) REFERENCES legacy_workbook_import(legacy_import_id)
            );

            CREATE TABLE IF NOT EXISTS ear_label_master (
                ear_label_code TEXT PRIMARY KEY,
                display_text TEXT NOT NULL,
                meaning TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ear_label_alias (
                alias_id TEXT PRIMARY KEY,
                raw_text TEXT NOT NULL,
                ear_label_code TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                hit_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ear_label_code) REFERENCES ear_label_master(ear_label_code),
                UNIQUE (raw_text, ear_label_code)
            );

            CREATE TABLE IF NOT EXISTS labeling_rule_set (
                rule_set_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL UNIQUE,
                applies_to_strain_text TEXT NOT NULL DEFAULT '',
                session_date TEXT NOT NULL DEFAULT '',
                numbering_order TEXT NOT NULL DEFAULT 'unknown',
                mouse_number_scope TEXT NOT NULL DEFAULT 'unknown',
                ear_sequence_scope TEXT NOT NULL DEFAULT 'unknown',
                crossed_out_handling TEXT NOT NULL DEFAULT 'review',
                sample_mapping TEXT NOT NULL DEFAULT 'review',
                genotyping_target TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS labeling_rule_ear_sequence (
                rule_set_id TEXT NOT NULL,
                sequence_index INTEGER NOT NULL,
                ear_label_code TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (rule_set_id, sequence_index),
                FOREIGN KEY (rule_set_id) REFERENCES labeling_rule_set(rule_set_id),
                FOREIGN KEY (ear_label_code) REFERENCES ear_label_master(ear_label_code)
            );

            CREATE TABLE IF NOT EXISTS card_note_item_log (
                note_item_id TEXT PRIMARY KEY,
                photo_id TEXT,
                parse_id TEXT,
                card_snapshot_id TEXT,
                card_type TEXT NOT NULL DEFAULT 'unknown',
                line_number INTEGER,
                raw_line_text TEXT NOT NULL,
                strike_status TEXT NOT NULL DEFAULT 'none',
                parsed_type TEXT NOT NULL DEFAULT 'unknown',
                interpreted_status TEXT NOT NULL DEFAULT 'unknown',
                parsed_mouse_display_id TEXT,
                parsed_ear_label_raw TEXT,
                parsed_ear_label_code TEXT,
                parsed_ear_label_confidence REAL,
                parsed_ear_label_review_status TEXT NOT NULL DEFAULT 'auto_filled',
                parsed_event_date TEXT,
                parsed_count INTEGER,
                parsed_metadata_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0,
                needs_review INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (photo_id) REFERENCES photo_log(photo_id),
                FOREIGN KEY (parse_id) REFERENCES parse_result(parse_id),
                FOREIGN KEY (parsed_ear_label_code) REFERENCES ear_label_master(ear_label_code)
            );

            CREATE TABLE IF NOT EXISTS mouse_master (
                mouse_id TEXT PRIMARY KEY,
                display_id TEXT NOT NULL,
                id_prefix TEXT NOT NULL DEFAULT '',
                strain_id TEXT,
                father_id TEXT,
                mother_id TEXT,
                litter_id TEXT,
                raw_strain_text TEXT NOT NULL DEFAULT '',
                sex TEXT,
                genotype TEXT,
                genotype_status TEXT NOT NULL DEFAULT 'unknown',
                dob_raw TEXT,
                dob_start TEXT,
                dob_end TEXT,
                ear_label_raw TEXT,
                ear_label_code TEXT,
                ear_label_confidence REAL,
                ear_label_review_status TEXT NOT NULL DEFAULT 'auto_filled',
                sample_id TEXT,
                sample_date TEXT,
                genotyping_status TEXT NOT NULL DEFAULT 'not_sampled',
                genotype_result TEXT,
                genotype_result_date TEXT,
                target_match_status TEXT NOT NULL DEFAULT 'unknown',
                use_category TEXT NOT NULL DEFAULT 'unknown',
                next_action TEXT NOT NULL DEFAULT 'sample_needed',
                source_note_item_id TEXT,
                current_card_snapshot_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                source_photo_id TEXT,
                source_record_id TEXT,
                last_verified_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ear_label_code) REFERENCES ear_label_master(ear_label_code),
                FOREIGN KEY (source_photo_id) REFERENCES photo_log(photo_id),
                FOREIGN KEY (source_record_id) REFERENCES source_record(source_record_id)
            );
            """
        )
        ensure_schema_compatibility(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_card_note_item_log_photo_line
                ON card_note_item_log(photo_id, line_number);
            CREATE INDEX IF NOT EXISTS idx_photo_log_batch
                ON photo_log(upload_batch_id, uploaded_at);
            CREATE INDEX IF NOT EXISTS idx_upload_batch_time
                ON upload_batch(created_at);
            CREATE INDEX IF NOT EXISTS idx_card_note_item_log_mouse
                ON card_note_item_log(parsed_mouse_display_id);
            CREATE INDEX IF NOT EXISTS idx_mouse_master_display
                ON mouse_master(display_id);
            CREATE INDEX IF NOT EXISTS idx_mouse_master_identity_candidate
                ON mouse_master(display_id, raw_strain_text, dob_start, dob_end, ear_label_code);
            CREATE INDEX IF NOT EXISTS idx_mouse_master_sample
                ON mouse_master(sample_id);
            CREATE INDEX IF NOT EXISTS idx_distribution_assignment_import
                ON distribution_assignment_row(distribution_import_id, source_row_number);
            CREATE INDEX IF NOT EXISTS idx_legacy_workbook_import_time
                ON legacy_workbook_import(imported_at);
            CREATE INDEX IF NOT EXISTS idx_legacy_workbook_row_import
                ON legacy_workbook_row(legacy_import_id, source_row_number);
            CREATE INDEX IF NOT EXISTS idx_legacy_workbook_row_review
                ON legacy_workbook_row(review_id);
            CREATE INDEX IF NOT EXISTS idx_strain_registry_name
                ON strain_registry(strain_name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_gene_master_symbol
                ON gene_master(gene_symbol COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_allele_master_symbol
                ON allele_master(allele_symbol COLLATE NOCASE);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gene_master_symbol_unique
                ON gene_master(gene_symbol COLLATE NOCASE);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_allele_master_gene_symbol_unique
                ON allele_master(COALESCE(gene_id, ''), allele_symbol COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_strain_allele_relationship_strain
                ON strain_allele_relationship(strain_id, status);
            CREATE INDEX IF NOT EXISTS idx_source_record_type
                ON source_record(source_type, imported_at);
            CREATE INDEX IF NOT EXISTS idx_correction_log_entity
                ON correction_log(entity_type, entity_id, corrected_at);
            CREATE INDEX IF NOT EXISTS idx_canonical_candidate_review
                ON canonical_candidate(review_id, status);
            CREATE INDEX IF NOT EXISTS idx_card_snapshot_photo
                ON card_snapshot(photo_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_export_log_type_time
                ON export_log(export_type, exported_at);
            CREATE INDEX IF NOT EXISTS idx_mouse_event_mouse
                ON mouse_event(mouse_id, event_date);
            CREATE INDEX IF NOT EXISTS idx_genotyping_record_mouse
                ON genotyping_record(mouse_id, sample_id);
            CREATE INDEX IF NOT EXISTS idx_genotyping_record_source_photo
                ON genotyping_record(source_photo_id, result_date);
            CREATE INDEX IF NOT EXISTS idx_genotyping_record_photo_evidence
                ON genotyping_record(photo_evidence_id);
            CREATE INDEX IF NOT EXISTS idx_photo_evidence_source_photo
                ON photo_evidence_item(source_photo_id, evidence_kind);
            CREATE INDEX IF NOT EXISTS idx_photo_evidence_parse
                ON photo_evidence_item(parse_id, status);
            CREATE INDEX IF NOT EXISTS idx_photo_evidence_note
                ON photo_evidence_item(note_item_id);
            CREATE INDEX IF NOT EXISTS idx_photo_evidence_mouse
                ON photo_evidence_item(linked_mouse_id, status);
            CREATE INDEX IF NOT EXISTS idx_photo_evidence_event
                ON photo_evidence_item(linked_event_id);
            CREATE INDEX IF NOT EXISTS idx_review_evidence_review
                ON review_evidence_link(review_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_review_evidence_item
                ON review_evidence_link(photo_evidence_id);
            CREATE INDEX IF NOT EXISTS idx_strain_target_genotype_strain
                ON strain_target_genotype(strain_text, active);
            CREATE INDEX IF NOT EXISTS idx_genotype_status_master_active
                ON genotype_status_master(active, sort_order);
            CREATE INDEX IF NOT EXISTS idx_review_role_master_active
                ON review_role_master(active, sort_order);
            CREATE INDEX IF NOT EXISTS idx_review_priority_master_active
                ON review_priority_master(active, sort_order);
            CREATE INDEX IF NOT EXISTS idx_labeling_rule_set_active
                ON labeling_rule_set(active, session_date);
            CREATE INDEX IF NOT EXISTS idx_cage_registry_label
                ON cage_registry(cage_label COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_mouse_cage_assignment_active
                ON mouse_cage_assignment(mouse_id, status, assigned_at);
            CREATE INDEX IF NOT EXISTS idx_mating_registry_status
                ON mating_registry(status, start_date);
            CREATE INDEX IF NOT EXISTS idx_mating_mouse_mating
                ON mating_mouse(mating_id, role);
            CREATE INDEX IF NOT EXISTS idx_mating_mouse_mouse
                ON mating_mouse(mouse_id, joined_date);
            CREATE INDEX IF NOT EXISTS idx_litter_registry_mating
                ON litter_registry(mating_id, birth_date);
            """
        )
        try:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    entity_type UNINDEXED,
                    entity_id UNINDEXED,
                    title,
                    body,
                    source_layer UNINDEXED,
                    updated_at UNINDEXED
                );

                CREATE TABLE IF NOT EXISTS search_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )
        except sqlite3.OperationalError:
            pass
        conn.executemany(
            """
            INSERT OR IGNORE INTO ear_label_master
                (ear_label_code, display_text, meaning)
            VALUES (?, ?, ?)
            """,
            EAR_LABEL_MASTER_SEEDS,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO ear_label_alias
                (alias_id, raw_text, ear_label_code, confidence, confirmed)
            VALUES (?, ?, ?, ?, ?)
            """,
            EAR_LABEL_ALIAS_SEEDS,
        )
        labeling_rule_set_seeds, labeling_rule_ear_sequence_seeds = load_labeling_rule_seed_config()
        conn.executemany(
            """
            INSERT OR IGNORE INTO labeling_rule_set
                (rule_set_id, display_name, applies_to_strain_text, session_date,
                 numbering_order, mouse_number_scope, ear_sequence_scope,
                 crossed_out_handling, sample_mapping, genotyping_target, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            labeling_rule_set_seeds,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO labeling_rule_ear_sequence
                (rule_set_id, sequence_index, ear_label_code)
            VALUES (?, ?, ?)
            """,
            labeling_rule_ear_sequence_seeds,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO genotype_status_master
                (status_key, display_label, meaning, workflow_stage,
                 blocks_experiment, export_warning, legacy_genotyping_status, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            GENOTYPE_STATUS_MASTER_SEEDS,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO review_role_master
                (role_key, display_label, responsibility, default_priority, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            REVIEW_ROLE_MASTER_SEEDS,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO review_priority_master
                (priority_key, display_label, severity_rank, export_blocking_hint,
                 response_expectation, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            REVIEW_PRIORITY_MASTER_SEEDS,
        )
