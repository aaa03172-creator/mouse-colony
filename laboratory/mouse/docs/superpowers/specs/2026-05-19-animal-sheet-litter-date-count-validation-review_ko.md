# Animal Sheet Litter Date And Count Validation Review

Layer classification: review item / non-canonical implementation design.

Canonical: false.

작성일: 2026-05-19.

## 목적

직접 확인한 animal sheet Excel export에서 pup 수, 분리 수, 날짜 오류가 발견되었다. 사진 자체가 헷갈릴 수 있는 상태라면 시스템은 값을 더 자신 있게 덮어쓰는 방향이 아니라, 헷갈리는 값을 더 명확히 review로 보내고 final Excel export 또는 canonical apply를 막아야 한다.

이 문서는 현재 구현이 이미 갖춘 안전장치와 부족한 부분을 검토하고, pup 수 / 분리 수 / 날짜 오류를 줄이기 위한 다음 설계 단위를 정의한다.

## 핵심 결론

현재 구조는 export provenance와 review blocker gate는 갖추고 있다. 그러나 animal sheet row의 litter 날짜, pup count, separated/weaned count를 기존 canonical state 및 source evidence와 다시 대조하는 전용 검증기는 아직 부족하다.

따라서 다음 구현 단위는 전체 colony reasoning engine이 아니라 `animal_sheet_litter_date_count_validator`가 적절하다. 이 검증기는 자동 수정기가 아니며, 다음 역할만 해야 한다.

1. animal sheet export row와 accepted mating/litter/mouse state를 비교한다.
2. source photo, note item, legacy workbook row trace가 없는 high-risk row를 표시한다.
3. 날짜 순서와 count reconciliation이 생물학적으로 또는 기록상 모순될 때 review item을 만든다.
4. high severity conflict가 있으면 final animal sheet XLSX export를 blocked 상태로 남기고 파일 생성을 막는다.
5. 사용자가 리뷰하기 전에는 canonical state를 절대 덮어쓰지 않는다.

## 현재 구현 검토

### 이미 있는 안전장치

`review_queue` 테이블은 uncertain, conflicting, high-risk 항목을 review item으로 보관한다. `export_log`는 Excel export history를 기록하며 `source_layer = export or view`를 유지한다.

Animal sheet export path는 다음 안전장치를 이미 갖고 있다.

- `/api/export-preview`가 `animal_sheet_rows`를 만든다.
- `/api/exports/animal-sheet.xlsx`는 Focus Review blocker가 있으면 HTTP 409로 final export를 막는다.
- export 시 `validation_report`와 `export_manifest` artifact를 생성한다.
- generated/blocked export 모두 `export_log`에 남긴다.
- XLSX에는 `Export_Trace` sheet가 있고 source note, source record, source photo, card snapshot, raw note line, row state를 노출한다.

Legacy animal sheet import path도 canonical write가 아니라 `parsed or intermediate result`와 reviewable candidate로 남기는 방향이 이미 잡혀 있다.

### 부족한 부분

현재 export validation은 주로 다음 두 가지를 본다.

- 열린 Focus Review blocker가 있는가.
- export row에 source trace가 있는가.

하지만 pup/date/count 오류에 필요한 다음 검증은 아직 충분히 전용 규칙으로 연결되어 있지 않다.

- litter birth date가 mating start date보다 빠른지.
- separation/weaning date가 litter birth date보다 빠른지.
- `Pubs` count와 later separated/weaned count가 설명 가능한지.
- animal sheet의 `F1`, `F2`, `10p`, `separated` row가 accepted `litter_registry` state와 일치하는지.
- newer photo-backed evidence가 older Excel-derived source보다 우선되는지.
- ambiguous date normalization이 final export row에 확정값처럼 들어갔는지.
- litter row가 source photo/note item 없이 `source_record_id`만 갖는 경우 어떤 review level이어야 하는지.

## Data Boundary Classification

| Artifact or response shape | Boundary | Rule |
| --- | --- | --- |
| Cage card photo | raw source | 원본 이미지는 품질이 낮아도 보존한다. |
| OCR/AI/manual draft field | parsed or intermediate result | raw value와 normalized candidate를 분리하고 confidence를 유지한다. |
| Card note item | parsed or intermediate result | mouse ID, litter note, strike status의 source evidence이다. |
| `mouse_master`, `mating_registry`, `mating_mouse`, `litter_registry` | canonical structured state | reviewed/apply된 state만 포함한다. |
| Animal sheet row in export preview | export or view | canonical truth가 아니라 현재 state의 view이다. |
| Animal sheet XLSX | export or view | lab sharing format이며 source of truth가 아니다. |
| Validation report | export or view | deterministic check result; canonical write를 하지 않는다. |
| Export manifest | export or view | generated/blocked export provenance이다. |
| Pup/date/count conflict | review item | low confidence, conflict, biological unlikely case는 review로 보낸다. |

## Target Validator

새 검증기는 animal sheet export preview 생성 이후, XLSX build 이전에 실행한다.

Proposed function boundary:

```python
def validate_animal_sheet_litter_date_counts(
    *,
    animal_sheet_rows: list[dict[str, Any]],
    mating_rows: list[Any],
    litter_rows: list[Any],
    note_evidence: dict[str, dict[str, Any]],
    source_policy: dict[str, Any],
) -> dict[str, Any]:
    ...
```

Return shape:

```json
{
  "source_layer": "review item / export or view",
  "status": "pass | warning | blocked",
  "checks": [
    {
      "check_key": "litter_date_before_mating",
      "status": "blocked",
      "severity": "high",
      "animal_sheet_row_key": "mating_animal_render:F1",
      "message": "Litter birth date is earlier than mating start date.",
      "current_export_value": "2026-04-13",
      "canonical_value": "2026-05-01",
      "source_refs": {
        "photo_ids": [],
        "note_item_ids": [],
        "source_record_ids": ["source_litter_manual"],
        "mating_ids": ["mating_animal_render"],
        "litter_ids": ["litter_animal_render"]
      },
      "recommended_action": "Open source photo or source row and confirm litter date before export."
    }
  ],
  "review_items": []
}
```

The validator should not write canonical state. It may either return proposed review items to the caller or insert review items in the same transaction that logs a blocked export. The safer first implementation is to return proposed review items and let the export/apply path own persistence.

## Validation Rules

### Date Order Rules

High severity / blocked:

- `litter.birth_date < mating.start_date`, unless an explicit reviewed exception says the mating start date was inherited or late-entered.
- `litter.weaning_date < litter.birth_date`.
- separated/dead/moved event date for pups is earlier than litter birth date.
- normalized date appears in export but raw source only contains an ambiguous or partial date.

Medium severity / warning or review:

- litter birth date is very close to mating date and below configurable threshold.
- source date fields disagree across photo note, manual correction, and legacy workbook row.
- row has a date but no source photo, note item, or source record trace.

No rule should hard-code strain-specific gestation or separation timing. Thresholds should come from `config/breeding_rules.json` or a future rule master.

### Pup Count And Separation Count Rules

High severity / blocked:

- `number_weaned > number_born` when both are known.
- animal sheet `Pubs` count conflicts with accepted `litter_registry.number_born` and there is no correction/review record explaining the change.
- export row says separated/weaned but accepted litter status is still active/pre-weaning and no reviewed event exists.
- accepted separated/weaned count cannot be reconciled with active pup rows, dead rows, or explicitly reviewed loss/death events.

Medium severity / review:

- `Pubs` text exists but parsed count is missing or low confidence.
- count is present only in legacy Excel source and not backed by photo/note/manual review.
- multiple rows for `F1`, `F2`, etc. point to the same litter candidate with different counts.

Pass:

- count difference is explained by reviewed death/loss/separation events with source refs.
- export row is trace-only and clearly marked as needing review, not final accepted state.

### Source Priority Rules

When evidence conflicts, source priority should be:

1. reviewed source photo / reviewed note item,
2. manual correction with before/after log and source reference,
3. accepted canonical state derived from reviewed evidence,
4. legacy workbook row as parsed/intermediate evidence,
5. generated export row.

Legacy workbook rows and generated animal sheet rows must not outrank newer photo-backed evidence.

## Review Item Contract

Review items created by this validator should use the existing `review_queue` pattern. Suggested fields:

| Field | Value |
| --- | --- |
| `issue` | `Animal sheet litter/date conflict` or `Animal sheet count conflict` |
| `severity` | `high` for blocked export, `medium` for review before next handoff |
| `current_value` | compact JSON containing animal sheet row values |
| `suggested_value` | compact JSON containing canonical/source-backed candidate values |
| `review_reason` | user-facing reason in lab language |
| `evidence_reference_json` | photo IDs, note item IDs, source record IDs, mating IDs, litter IDs |
| `review_trigger_json` | check key, rule set ID, threshold snapshot, export filename/query |

Example user-facing reason:

```text
Animal sheet shows F1 as 10p on 2026-04-13, but accepted litter state for this mating has birth date 2026-05-01. Check the source photo or imported Excel row before export.
```

## Export Behavior

The export preview should expose row-level validation:

- `row_validation_state`: `ready | warning | blocked_by_litter_conflict`
- `row_validation_reason`: short human-readable reason
- `row_validation_refs`: photo/note/source/mating/litter IDs

The validation report should add check keys beyond the current `open_focus_review_blocker` and `missing_source_trace`:

- `impossible_date`
- `count_mismatch`
- `litter_state_conflict`
- `ambiguous_date_normalization`
- `stale_source_priority_conflict`

If any high severity check is blocked, `/api/exports/animal-sheet.xlsx?require_ready=true` should:

1. create validation report,
2. create export manifest with `status = blocked`,
3. log blocked export,
4. return HTTP 409 with review item details,
5. not write a final XLSX payload.

## Failure Paths To Check

Implementation must explicitly test these cases:

- validation report is created even when export is blocked;
- blocked export log links the validation report and manifest;
- review item insert failure does not partially write canonical state;
- repeated export attempts do not create duplicate unresolved review items for the same row/check/source set;
- stale validation reports are not reused after a review correction;
- source trace remains visible even when the export row is blocked;
- ambiguous OCR or AI date candidates are reviewable and do not become normalized export values without review.

## Testing Plan

Add focused tests before implementation:

1. animal sheet export blocks when litter birth date is earlier than mating start date.
2. export warns when litter row has source record trace but no photo/note trace.
3. export blocks when `number_weaned > number_born`.
4. export blocks when `Pubs` count conflicts with accepted litter count without reviewed explanation.
5. export passes when count difference is explained by reviewed death/loss/separation events.
6. ambiguous raw date remains raw/reviewable and does not appear as a confident normalized export date.
7. blocked export creates validation report, export manifest, export log, and review item without XLSX response.
8. repeated blocked export does not duplicate open review items.

Recommended starting files:

- `tests/test_artifact_workflow.py` for validation report and export manifest behavior.
- `tests/test_mouse_event_evidence_enforcement.py` for litter/mouse event evidence rules.
- `app/main.py` for export preview, validation report generation, and animal sheet XLSX endpoint.
- `app/db.py` only if review item deduplication requires an index or new metadata field.

## Rollout Sequence

1. Add failing tests for date order and count mismatch using compact DB fixtures.
2. Add pure validator helper that takes prepared rows/state and returns checks.
3. Wire validator into `build_export_validation_report` and `/api/export-preview` row metadata.
4. Add review item persistence for high severity checks, with deduplication.
5. Update animal sheet export 409 payload to include litter/date/count conflicts.
6. Update UI copy only enough to show blocked row reasons and source refs.
7. Run focused pytest, local export tests, and `git status --short`.

## Open Decisions

1. Should high severity conflicts always block animal sheet export, or should the app allow a clearly watermarked "review draft" XLSX?
2. Which source counts as enough trace for litter rows: source record only, or must there be photo/note evidence for final handoff?
3. Should count reconciliation include explicit death/loss event categories now, or initially only compare `number_born`, `number_alive`, and `number_weaned`?
4. Should reviewed exceptions for unusual dates live in `review_queue` resolution metadata, `mouse_event`, or a future rule/exception table?

## Recommendation

Start with blocking validation for impossible date order and impossible count relationships. Treat ambiguous OCR/date normalization and source-priority conflicts as review warnings first. This gives immediate protection against destructive animal sheet mistakes without forcing the app to solve every breeding-history inference problem at once.

The guiding rule should be: when pup/date/count evidence is unclear, preserve the source and block silent overwrite; never promote a confusing export row into canonical state without review.
