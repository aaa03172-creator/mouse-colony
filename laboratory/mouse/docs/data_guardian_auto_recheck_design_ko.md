# 데이터 지킴이 자동 재확인 에이전트 설계

Status: proposal / implementation design
Layer: adopted documentation
Canonical status: non-canonical until implemented and tested
Date: 2026-06-09

## 목적

데이터 지킴이는 cage card 사진, OCR/LLM draft, note-line evidence, Excel export, 사람이 확정한 값을 감시하고 자동 재확인을 실행하는 검토 보조 에이전트다.

목표는 사람이 매번 "다시 확인해", "원본 사진이랑 대조해", "Excel export가 맞는지 봐"라고 명령해야 하는 피로를 줄이는 것이다. 단, 자동화 결과가 연구데이터의 canonical structured state를 직접 바꾸면 안 된다. 데이터 지킴이는 근거를 모으고, 위험을 분류하고, 사람이 봐야 할 항목만 review queue로 올린다.

이 문서는 구현 전 설계 문서이며, runtime contract는 별도 테스트와 스키마가 추가되기 전까지 canonical truth가 아니다.

## 구현 진행 골

이 설계를 실제 구현으로 넘길 때는 아래 골을 생성하고 진행한다.

```text
Implement a local-first Data Guardian auto-recheck workflow that watches generated mouse colony review/export artifacts, runs safe local rechecks automatically, writes evidence-backed review items, and never changes canonical colony state without explicit human resolution.
```

골의 한국어 설명:

```text
생성된 MouseDB review/export 산출물을 데이터 지킴이가 자동 감시하고 로컬 재확인을 실행하도록 만든다. 재확인 결과는 근거가 붙은 review item과 validation report로 남기며, 사람의 명시적 resolve 없이는 canonical colony state를 변경하지 않는다.
```

골이 active인 동안의 원칙:

- 한 번에 한 태스크만 진행한다.
- 각 태스크는 TDD red-green-refactor 순서를 따른다.
- 각 태스크가 끝나면 더블체크 게이트를 통과한다.
- 주요 체크포인트마다 Codex를 read-only reviewer로 붙인다.
- Codex 리뷰 결과는 자동 수정 명령이 아니라 review finding으로 취급한다.
- canonical state를 쓰는 변경은 별도 approval boundary를 통과하기 전까지 구현하지 않는다.

골 완료 조건:

- MVP 수용 기준을 만족하는 테스트가 있다.
- auto-recheck run, evidence bundle, review item, proposed changeset이 모두 `canonical: false` 경계를 가진다.
- local-only 재확인은 자동 실행 가능하다.
- 외부 OCR/LLM은 기본 차단되고 승인 없이는 호출되지 않는다.
- user-confirmed/canonical 값을 자동 덮어쓰는 경로가 없다.
- 태스크별 더블체크 로그에서 의도하지 않은 변경 파일이 없다.
- 최종 Codex read-only 리뷰에서 blocking finding이 없다.

## 참고한 레퍼런스

Notion에서 확인한 내부 레퍼런스 중 아래 패턴을 MouseDB에 맞게 적용한다.

- Research Tools & Systems: MouseDB 흐름은 cage card 사진 업로드, 정보 파싱, 사람 검수, 기록 반영이다.
- Research Tagging Agent: 자동화 결과를 바로 확정하지 않고 `confidence`, `evidence`, `status`를 남기는 구조를 사용한다.
- Research Tagging Agent 아카이빙 문서: `Auto-Approved`, `Pending Review`, `Quarantined`, `Escalation Gate / Judge`, `fail-open + status 기록` 패턴을 가져온다.
- PaperPipe Project Knowhow: source/evidence 추적, layer 분류, generated output은 draft-like로 시작, persisted shape 변경 시 contract test 필요 원칙을 가져온다.

## 데이터 계층 분류

데이터 지킴이가 읽거나 쓰는 모든 산출물은 먼저 계층을 분류한다.

| 항목 | 계층 | canonical 여부 | 설명 |
| --- | --- | --- | --- |
| 원본 cage card 사진 | raw source | 아니오 | 보존해야 하는 원본 증거 |
| 원본 Excel 파일 | raw source 또는 export/view | 아니오 | import/export view이며 유일한 진실이 아님 |
| OCR 원문 결과 | parsed or intermediate result | 아니오 | confidence와 source 위치를 포함 |
| LLM/assistant draft | parsed or intermediate result | 아니오 | 추론 결과이며 바로 확정 금지 |
| note-line evidence | raw source 또는 parsed result | 아니오 | line 위치, ROI, 원문 문자열을 보존 |
| 사람이 확정한 값 | canonical structured state | 예 | 다만 변경 시 before/after/evidence 필요 |
| 데이터 지킴이 재확인 결과 | review item 또는 validation report | 아니오 | 사람이 볼 검토 결과 |
| proposed changeset | review item | 아니오 | canonical 변경 제안일 뿐 직접 반영 금지 |
| Excel 제출본 | export or view | 아니오 | canonical state에서 생성된 view |
| ROI crop, 임시 OCR 비교 결과 | cache | 아니오 | 재생성 가능하며 source가 아님 |

기본 원칙은 "애매하면 non-canonical"이다.

## 제품 원칙

1. 자동화는 검토 피로를 줄이지만 최종 책임을 대신하지 않는다.
2. 원본 사진, note-line, imported Excel row로 되돌아갈 수 없는 값은 안전한 값으로 취급하지 않는다.
3. OCR/LLM/재비교 결과는 모두 draft-like로 시작한다.
4. 사람이 확정한 값을 덮어쓰려면 반드시 proposed changeset과 승인 경계를 통과한다.
5. 외부 OCR/LLM 호출은 기본 금지이며, payload 최소화와 사용자 승인이 필요하다.
6. 자동 재확인은 실패해도 전체 batch를 멈추지 않는다. 실패는 상태값과 로그로 남긴다.
7. UI는 "자동 통과"보다 "왜 이 항목을 다시 봐야 하는지"를 먼저 보여준다.

## 페르소나

UI 이름은 "데이터 지킴이"를 권장한다.

내부 코드명은 `review_sentinel`을 권장한다.

데이터 지킴이의 말투는 단정적 판정이 아니라 근거 기반 안내여야 한다.

좋은 문구:

- "18개 항목을 자동 재확인했습니다. 14개는 근거 일치로 통과했고, 3개는 빠른 확인, 1개는 원본 사진 대조가 필요합니다."
- "이 mouse ID는 이전 확정값과 다릅니다. 원본 note-line과 Excel row를 함께 확인해 주세요."
- "재OCR 결과가 기존 draft와 일치하지 않습니다. canonical 값은 변경하지 않았습니다."

피해야 할 문구:

- "이 값이 맞습니다."
- "자동으로 수정했습니다."
- "충돌을 해결했습니다."

## 자동화 권한 경계

| 작업 | 자동 실행 | canonical 변경 | 사용자 승인 |
| --- | --- | --- | --- |
| 새 draft/export/review package 감지 | 가능 | 없음 | 불필요 |
| 낮은 confidence 탐지 | 가능 | 없음 | 불필요 |
| mouse ID 중복/불연속 탐지 | 가능 | 없음 | 불필요 |
| note-line evidence 연결 확인 | 가능 | 없음 | 불필요 |
| 로컬 재OCR | 가능 | 없음 | 불필요 |
| 로컬 parser/LLM 재비교 | 가능 | 없음 | 불필요 |
| 기존 확정값과 diff 생성 | 가능 | 없음 | 불필요 |
| Excel export 형식 검증 | 가능 | 없음 | 불필요 |
| review queue 생성 | 가능 | 없음 | 불필요 |
| proposed changeset 생성 | 가능 | 없음 | 불필요 |
| 외부 OCR/LLM 호출 | 기본 금지 | 없음 | 필요 |
| canonical structured state 변경 | 금지 | 가능성 있음 | 필요 |
| user-confirmed 값 덮어쓰기 | 금지 | 가능성 있음 | 필요 |
| 최종 제출 Excel 교체 | 금지 | 가능성 있음 | 필요 |

## 시스템 구성

### 1. Watcher

Watcher는 새 산출물이 생겼는지 감지한다.

첫 MVP에서는 실시간 파일 시스템 감시보다 명시적 API 또는 CLI 실행을 우선한다. 자동 실행이 필요하지만, 연구 데이터에서는 언제 어떤 batch가 처리됐는지 재현 가능해야 하므로 run 단위를 남기는 방식이 안전하다.

감지 대상:

- 새 review package import
- 새 OCR/LLM draft
- 새 Excel export workbook
- 새 user-confirmed submission
- 기존 batch 재검증 요청

출력:

- `auto_recheck_run`
- 입력 manifest
- payload classification
- 시작/종료 시각
- 처리 상태

### 2. Evidence Collector

Evidence Collector는 한 항목을 판단할 때 필요한 근거를 모은다.

근거 후보:

- source photo id 또는 file path
- ROI 좌표 또는 crop id
- note-line 원문
- OCR raw text
- OCR confidence
- LLM/assistant draft value
- imported Excel row id
- current canonical value
- previous user-confirmed value
- export workbook row/column
- rule hit 또는 validator finding

출력은 canonical 값이 아니라 evidence bundle이다.

예시:

```json
{
  "field_key": "mouse_id",
  "candidate_value": "NC 9 R'",
  "source_photo_id": "photo_20260604_001",
  "note_line_id": "line_12",
  "ocr_confidence": 0.72,
  "current_canonical_value": "NC 9 R''",
  "evidence_layer": "parsed or intermediate result",
  "canonical": false
}
```

### 3. Auto Recheck Runner

Auto Recheck Runner는 위험 신호가 있는 항목에 대해 자동 재확인을 실행한다.

재확인 작업:

- local OCR rerun
- local parser rerun
- note-line 비교
- 이전 confirmed value 비교
- Excel export row 비교
- mouse ID continuity check
- litter/date sequence check
- genotype/background ambiguity check
- separated/dead mark consistency check
- parent/pub row export format check

원칙:

- 한 항목 실패가 batch 전체를 중단하지 않는다.
- 실패도 `recheck_attempt`로 기록한다.
- 같은 항목에 대한 반복 재확인은 run id와 attempt id로 구분한다.
- 자동 재확인은 canonical state를 쓰지 않는다.

### 4. Risk Classifier

Risk Classifier는 항목을 사람이 봐야 하는 정도에 따라 분류한다.

권장 상태:

| 상태 | 의미 | UI 처리 |
| --- | --- | --- |
| `auto_passed` | 근거가 일치하고 위험 신호 없음 | 기본적으로 숨김, summary에만 포함 |
| `quick_review` | 낮은 위험이지만 빠른 확인 필요 | review queue에 표시 |
| `photo_check_required` | 원본 사진 대조 필요 | source photo와 함께 강조 |
| `conflict_review` | 기존 확정값/Excel/OCR 사이 충돌 | before/after/evidence 표시 |
| `blocked` | 근거 부족, 중대한 충돌, 외부 승인 필요 | canonical 반영 차단 |
| `recheck_failed` | 자동 재확인 자체 실패 | 실패 이유와 재시도 액션 표시 |

분류 기준 예시:

- OCR confidence가 낮으면 `quick_review` 이상
- OCR과 기존 confirmed value가 다르면 `conflict_review`
- mouse ID가 note-line evidence 없이 추정되면 `photo_check_required`
- genotype/background가 hard-coded rule에 의존하면 `quick_review`
- 외부 OCR/LLM이 필요하면 `blocked`
- Excel export가 canonical에서 재생성된 값과 다르면 `conflict_review`

### 5. Review Queue Writer

Review Queue Writer는 사람이 볼 항목만 review item으로 저장한다.

review item은 다음을 포함해야 한다.

- review id
- run id
- source layer
- field key
- candidate value
- current canonical value
- previous confirmed value
- risk status
- risk reasons
- evidence bundle references
- recommended action
- created_at
- resolved_at
- resolver
- resolution value
- resolution note

review item은 canonical structured state가 아니다. 사람이 resolve해야만 별도 canonical writer가 변경을 수행할 수 있다.

### 6. Proposed Changeset Writer

자동 재확인 결과가 "이 값으로 바꾸는 것이 좋아 보임"까지 도달할 수는 있다. 하지만 이 결과는 proposed changeset으로만 저장한다.

proposed changeset 필드:

- target entity
- target field
- before value
- proposed after value
- evidence references
- confidence
- risk reasons
- generated_by
- generated_at
- approval status

승인 전에는 어떤 export에도 확정값처럼 섞이면 안 된다.

## 데이터 흐름

```text
raw photo / imported Excel / OCR draft / review package
  -> watcher
  -> evidence collector
  -> auto recheck runner
  -> risk classifier
  -> auto recheck report
  -> review queue
  -> human resolution
  -> canonical writer
  -> Excel export
```

중요한 점은 `auto recheck report`와 `review queue`가 canonical writer 앞에 있어야 한다는 것이다. 데이터 지킴이는 writer가 아니라 gatekeeper다.

## MVP 범위

첫 구현은 "작지만 피로 감소 효과가 바로 보이는" 범위로 제한한다.

### 포함

- review package 또는 Excel export를 입력으로 받는 명시적 auto-recheck API/CLI
- local-only 재검증
- evidence bundle 생성
- 위험 항목 분류
- review queue 생성
- summary report 생성
- UI summary 문구
- focused tests

### 제외

- 외부 OCR/LLM 자동 호출
- 실시간 background daemon
- canonical state 자동 수정
- 최종 제출 Excel 자동 교체
- 새 strain/genotype ontology 자동 생성
- 그래프/대시보드 중심 UI

## 권장 API 초안

### POST `/api/data-guardian/auto-recheck`

입력:

```json
{
  "source_type": "review_package",
  "source_id": "run_20260609_001",
  "approved_local_recheck": true,
  "allow_external_services": false
}
```

출력:

```json
{
  "run_id": "auto_recheck_20260609_001",
  "canonical": false,
  "source_layer": "review item",
  "summary": {
    "checked_count": 143,
    "auto_passed_count": 124,
    "quick_review_count": 12,
    "photo_check_required_count": 5,
    "conflict_review_count": 2,
    "blocked_count": 0
  },
  "review_item_ids": ["review_001", "review_002"],
  "report_url": "/api/data-guardian/reports/auto_recheck_20260609_001"
}
```

### GET `/api/data-guardian/runs/{run_id}`

run summary, input manifest, status, warning counts를 반환한다.

### GET `/api/data-guardian/review-queue`

사람이 봐야 하는 항목만 반환한다. 기본 정렬은 위험도가 높은 순서다.

### POST `/api/data-guardian/runs/{run_id}/retry`

실패한 local-only 재확인만 재시도한다. canonical writer를 호출하지 않는다.

## 저장 모델 초안

SQLite 기준으로 다음 테이블을 고려한다.

### `auto_recheck_runs`

- `run_id`
- `source_type`
- `source_id`
- `input_manifest_json`
- `payload_class`
- `allow_external_services`
- `status`
- `started_at`
- `completed_at`
- `summary_json`
- `canonical` default false

### `auto_recheck_attempts`

- `attempt_id`
- `run_id`
- `target_type`
- `target_id`
- `field_key`
- `action`
- `status`
- `error_code`
- `error_message`
- `started_at`
- `completed_at`
- `result_json`

### `evidence_bundles`

- `evidence_bundle_id`
- `run_id`
- `target_type`
- `target_id`
- `field_key`
- `source_photo_id`
- `note_line_id`
- `excel_row_id`
- `ocr_result_id`
- `canonical_ref_id`
- `evidence_json`
- `canonical` default false

### `data_guardian_review_items`

- `review_id`
- `run_id`
- `target_type`
- `target_id`
- `field_key`
- `candidate_value_raw`
- `candidate_value_normalized`
- `current_canonical_value`
- `previous_confirmed_value`
- `risk_status`
- `risk_reasons_json`
- `recommended_action`
- `evidence_bundle_id`
- `resolution_status`
- `resolution_value`
- `resolution_note`
- `created_at`
- `resolved_at`
- `canonical` default false

### `proposed_changesets`

- `changeset_id`
- `run_id`
- `target_type`
- `target_id`
- `field_key`
- `before_value`
- `proposed_after_value`
- `confidence`
- `evidence_bundle_id`
- `approval_status`
- `created_at`
- `approved_at`
- `canonical` default false

## 위험 신호 규칙 예시

규칙은 코드에 hard-code하지 않고 config로 분리하는 것이 좋다. 다만 첫 구현에서는 validator 함수와 config seed를 함께 두되, strain/genotype/protocol/date rule을 고정 지식으로 박아 넣지 않는다.

초기 위험 신호:

- OCR confidence below threshold
- candidate value missing source photo
- candidate value missing note-line evidence
- normalized value differs from raw value without reason
- candidate mouse ID conflicts with current canonical value
- duplicate active mouse ID in same scope
- litter sequence appears out of order
- separated/dead inference lacks visible strike or note evidence
- genotype/background field uses broad phrase with no mouse-level evidence
- Excel export row cannot trace back to canonical entity
- export workbook differs from regenerated export
- external service would be required to continue

## UI 설계

데이터 지킴이 UI는 dashboard 장식보다 review 피로 감소에 집중한다.

첫 화면 요소:

- 이번 run summary
- 자동 통과 수
- 빠른 확인 수
- 원본 사진 대조 필요 수
- 충돌 수
- 실패 수
- "사람이 봐야 할 항목만 보기" 기본 필터

review item 상세:

- 왼쪽: source photo 또는 ROI
- 오른쪽: candidate value, current canonical value, previous confirmed value
- 아래: risk reasons
- 아래: evidence trace
- 액션: accept candidate, keep canonical, edit value, mark unresolved, request rerun

중요한 UX 문구:

- "canonical 값은 아직 변경되지 않았습니다."
- "이 항목은 원본 사진 대조가 필요합니다."
- "자동 재확인은 로컬에서만 실행되었습니다."
- "외부 서비스 호출이 필요해 중단되었습니다."

## 실패 처리

자동 재확인은 fail-open이어야 한다.

실패 유형:

- OCR provider unavailable
- malformed review package
- missing source photo
- missing Excel row
- validator exception
- export workbook unreadable
- permission error

처리 방식:

- run 전체는 `completed_with_warnings`로 종료 가능
- 실패한 item은 `recheck_failed` review item으로 남김
- 실패 원인과 재시도 가능 여부를 기록
- partial write 방지를 위해 run, attempts, review items는 transaction boundary를 분리하거나 idempotent upsert를 사용

금지:

- 일부 실패 후 성공한 값만 canonical에 조용히 반영
- 실패한 항목을 summary에서 숨김
- source photo 없는 항목을 auto_passed 처리

## 보안 및 외부 서비스 경계

기본값은 local-only다.

외부 OCR/LLM 호출이 필요한 경우:

- payload class를 명시한다.
- 원본 전체 record를 보내지 않는다.
- 필요한 crop/text만 최소화한다.
- mouse ID, lab-specific note, local path 등 불필요한 context를 제거한다.
- 사용자 승인 없이는 호출하지 않는다.
- 호출 결과는 parsed/intermediate 또는 review item으로만 저장한다.

## 테스트 전략

구현 시 아래 테스트를 먼저 둔다.

### Unit tests

- risk classifier가 low confidence를 `quick_review`로 분류
- canonical conflict를 `conflict_review`로 분류
- missing source photo를 auto_passed로 분류하지 않음
- external service required 시 `blocked`
- proposed changeset이 canonical false로 생성

### API tests

- auto-recheck API가 run summary를 반환
- allow_external_services false에서 외부 provider 호출 차단
- review queue가 auto_passed 항목을 기본 숨김
- failed attempt가 run 전체를 완전히 실패시키지 않음

### DB tests

- run 생성과 review item 생성이 idempotent
- partial failure 후 orphan review item 없음
- canonical table이 auto-recheck 중 변경되지 않음
- before/after/evidence references가 보존됨

### Export tests

- regenerated export와 기존 workbook diff 생성
- export mismatch가 review item으로 이동
- export file을 canonical source처럼 취급하지 않음

### UI tests

- summary count 표시
- "canonical 값은 아직 변경되지 않았습니다" 문구 표시
- photo_check_required 항목에서 source photo hint 표시
- accept/edit 액션은 resolution form만 채우고 canonical writer를 직접 호출하지 않음

## 구현 운영 프로토콜

이 기능은 연구데이터의 신뢰 경계를 다루므로 일반 기능보다 엄격하게 진행한다.

### 브랜치 원칙

구현은 별도 브랜치에서 진행한다.

권장 브랜치:

```powershell
git switch -c codex/data-guardian-auto-recheck
```

이미 관련 `codex/data-guardian-*` 브랜치에 있다면 새 브랜치를 더 만들지 않고 이어서 진행한다. 공유 기본 브랜치나 무관한 작업 브랜치에서는 구현하지 않는다.

브랜치 시작 시 확인:

```powershell
git branch --show-current
git status --short --untracked-files=all
```

dirty worktree가 있으면 모든 변경을 아래 계층 중 하나로 분류한다.

- current task source
- adopted documentation
- generated artifact
- cache
- unrelated user work

### TDD 규칙

새 production code는 실패하는 테스트를 먼저 본 뒤 작성한다.

각 구현 태스크는 아래 순서를 반드시 따른다.

1. 실패해야 하는 테스트를 작성한다.
2. 해당 테스트만 실행해 의도한 이유로 실패하는지 확인한다.
3. 테스트를 통과시키는 최소 구현을 작성한다.
4. 같은 테스트를 다시 실행해 통과를 확인한다.
5. 관련 회귀 테스트를 실행한다.
6. 더블체크 게이트를 실행한다.
7. 변경 파일을 분류하고 필요한 파일만 stage한다.

TDD 예외:

- 설계 문서 작성
- 순수 fixture 정리
- throwaway spike

예외를 쓰려면 해당 태스크 로그에 이유를 남기고, 구현 코드로 남기는 순간 테스트를 추가한다.

### 태스크별 더블체크 게이트

각 태스크가 끝나면 아래 명령을 실행하고 결과를 확인한다.

```powershell
python -m pytest <task-specific-tests> -q
git diff -- <task-files>
git status --short --untracked-files=all
```

프론트엔드 또는 browser contract가 바뀐 태스크에서는 추가로 관련 Node/browser 검증을 실행한다.

```powershell
node --test <task-specific-browser-test>
```

더블체크에서 확인할 항목:

- auto-recheck 결과가 canonical table을 쓰지 않는다.
- `source_layer`가 `review item`, `validation report`, `parsed or intermediate result`, `export or view`, `cache` 중 올바른 값이다.
- `canonical` 플래그가 review/report/proposed changeset에서 false다.
- user-confirmed 값과 충돌할 때 before/after/evidence가 보존된다.
- 외부 OCR/LLM 호출은 `allow_external_services: false`에서 차단된다.
- 실패한 item이 숨겨지지 않고 `recheck_failed` 또는 `blocked`로 남는다.
- 새 파일이 source인지 generated artifact인지 분류됐다.
- disposable verification artifact가 생겼다면 삭제하거나 좁은 `.gitignore` 규칙으로 처리했다.

### Codex Read-Only 리뷰 게이트

Codex는 구현 중 read-only reviewer로 붙인다. Codex는 직접 수정자가 아니라 위험을 찾아주는 검토관이다.

권장 체크포인트:

1. Task 2 이후: 저장 모델과 canonical write boundary 리뷰
2. Task 4 이후: evidence collector와 adapter의 source traceability 리뷰
3. Task 6 이후: API와 review queue의 권한 경계 리뷰
4. Task 8 이후: proposed changeset과 no-auto-write 보장 리뷰
5. 최종 완료 전: 전체 diff, 테스트, residual risk 리뷰

앱 API를 사용할 수 있을 때의 권장 요청:

```http
POST /api/codex-cli/read-only-audit
{
  "task": "data_guardian_auto_recheck_audit",
  "context": "Review the Data Guardian auto-recheck implementation. Check TDD coverage, canonical write risks, review item boundaries, external-service payload safety, partial write risks, and generated artifact cleanup.",
  "approved_codex_cli_run": true
}
```

API가 해당 task name을 지원하지 않으면 기존 지원 task를 사용하고 `context`에 데이터 지킴이 범위를 명시한다.

Codex finding 분류:

- `fix_now`: canonical write risk, data loss, partial write, privacy leak, broken test, missing evidence trace
- `document`: domain nuance, known limitation, deferred design decision
- `defer`: MVP 밖의 개선점

`fix_now` finding이 있으면 다음 태스크로 넘어가지 않는다.

### 태스크 완료 보고 형식

각 태스크 완료 시 아래 내용을 남긴다.

```text
Task N complete.
RED: <failing test command and expected failure>
GREEN: <passing test command>
Regression: <focused regression command>
Doublecheck: <git diff/status summary>
Codex review: <not run / no blocking findings / findings triaged>
Changed files:
- <path>: <layer classification>
Residual risk:
- <remaining risk or none>
```

### 커밋 원칙

커밋은 작고 coherent해야 한다.

권장 단위:

- storage schema + tests
- classifier + tests
- adapter + tests
- API endpoint + tests
- UI contract + tests
- Codex review finding fix

stage는 명시 파일만 사용한다.

```powershell
git add docs/data_guardian_auto_recheck_design_ko.md
git add app/<task-file>.py tests/<task-test>.py
git commit -m "feat: add data guardian risk classifier"
```

`git add .`는 unrelated dirty files가 있는 동안 사용하지 않는다.

## 구현 순서

### Task 1: 데이터 계약과 fixture

- `docs/data_guardian_auto_recheck_design_ko.md`의 layer contract를 구현 계획의 기준으로 삼는다.
- `tests/fixtures/` 또는 기존 fixture 위치에 최소 review package/export sample을 만든다.
- 첫 테스트는 "auto-recheck 입력 manifest가 source layer와 canonical false를 가진다"를 검증한다.
- 더블체크: fixture가 source인지 generated artifact인지 분류한다.

### Task 2: 저장 모델

- `auto_recheck_runs`, `auto_recheck_attempts`, `evidence_bundles`, `data_guardian_review_items`, `proposed_changesets` 저장 경계를 추가한다.
- 첫 테스트는 "run과 review item을 생성해도 canonical colony table이 변경되지 않는다"를 검증한다.
- Codex 리뷰 체크포인트 1을 실행한다.

### Task 3: Risk Classifier

- 순수 함수로 `auto_passed`, `quick_review`, `photo_check_required`, `conflict_review`, `blocked`, `recheck_failed`를 분류한다.
- 첫 테스트는 "missing source photo cannot be auto_passed"를 검증한다.
- 더블체크에서 hard-coded strain/genotype/date rule이 들어가지 않았는지 확인한다.

### Task 4: Evidence Collector와 입력 adapter

- review package와 export workbook에서 evidence bundle을 만든다.
- 첫 테스트는 "candidate value가 source photo/note-line/imported row 중 가능한 근거를 참조한다"를 검증한다.
- Codex 리뷰 체크포인트 2를 실행한다.

### Task 5: Local Auto Recheck Runner

- local-only OCR/parser/export verification 재확인을 실행한다.
- 첫 테스트는 "한 항목의 재확인 실패가 run 전체를 중단하지 않고 `completed_with_warnings`를 만든다"를 검증한다.
- 더블체크에서 failed item이 숨겨지지 않는지 확인한다.

### Task 6: Review Queue API

- 사람이 봐야 할 항목만 반환하는 API를 추가한다.
- 첫 테스트는 "`auto_passed` 항목은 기본 review queue에서 숨겨지고 conflict/photo_check 항목은 표시된다"를 검증한다.
- Codex 리뷰 체크포인트 3을 실행한다.

### Task 7: UI Summary와 Review Detail

- 데이터 지킴이 summary와 review item detail을 UI에 붙인다.
- 첫 테스트는 "canonical 값은 아직 변경되지 않았습니다" 문구가 표시되는지 검증한다.
- 더블체크에서 UI action이 canonical writer를 직접 호출하지 않는지 확인한다.

### Task 8: Proposed Changeset

- 자동 재확인 결과를 canonical 변경 제안으로만 저장한다.
- 첫 테스트는 "proposed changeset 생성 후 approval 전 canonical value가 유지된다"를 검증한다.
- Codex 리뷰 체크포인트 4를 실행한다.

### Task 9: Export Verification

- 기존 workbook과 재생성 export를 비교해 mismatch review item을 만든다.
- 첫 테스트는 "export mismatch가 canonical update가 아니라 review item으로 남는다"를 검증한다.
- 더블체크에서 Excel export가 source of truth로 취급되지 않는지 확인한다.

### Task 10: Final No-Canonical-Write Gate

- local-only, no-canonical-write, external-service-blocking 통합 테스트를 추가한다.
- 최종 Codex read-only 리뷰를 실행한다.
- 모든 task source와 generated artifact를 분류한다.
- disposable cache/log/screenshot은 정리한다.

## 수용 기준

첫 릴리스는 다음을 만족해야 한다.

- 새 review package 또는 export workbook에 대해 자동 재확인을 실행할 수 있다.
- 자동 재확인 결과는 canonical state를 직접 변경하지 않는다.
- 사람이 봐야 할 항목만 review queue에 남긴다.
- 각 review item은 source photo, note-line, Excel row, OCR result, current canonical value 중 가능한 근거를 연결한다.
- user-confirmed 값과 충돌하는 경우 before/after/evidence를 보존한다.
- 외부 OCR/LLM은 기본 차단된다.
- 실패한 항목은 숨겨지지 않고 `recheck_failed` 또는 `blocked`로 남는다.
- Excel export는 source of truth가 아니라 export/view로 취급된다.

## 향후 확장

MVP 이후에 고려할 수 있는 확장:

- background watcher
- scheduled nightly recheck
- confidence threshold tuning UI
- reviewer workload metrics
- repeated error pattern learning
- operator-specific review preferences
- batch-level go/no-go report
- external OCR opt-in workflow
- side-by-side photo workbench와 통합

확장 시에도 canonical state 자동 변경은 별도 승인 경계를 통과해야 한다.

## 한 줄 결론

데이터 지킴이는 MouseDB의 자동 판정자가 아니라 자동 재확인 실행자이자 review queue curator다. 반복 확인 피로를 줄이되, 원본 사진과 note-line evidence로 되돌아갈 수 있는 연구데이터의 신뢰 경계를 지키는 것이 핵심이다.
