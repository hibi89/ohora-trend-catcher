# OHORA Trend Catcher — GPT 현재 상황 보고서

**기준 시각:** 2026-10-22 KST · **저장소:** `hibi89/ohora-trend-catcher`

## 한눈에 보는 상태

- **n8n 전담 브랜치:** `codex/p6-readiness-hardening-20261019`, 로컬 작업 트리 clean.
- **n8n 검증 기준:** 직전 코드 SHA `63fbabb0f6e430a9b5b26ecff706ecd9ad460b18` check-runs 16/16 SUCCESS. 그 뒤 docs-only 보고서/인수인계 갱신을 같은 작업 브랜치에 push했으며, 최종 SHA와 해당 CI 상태는 이 보고서 갱신 직후 확인한다.
- **P50 엔진 소유 브랜치:** `feature/p6-50-account-pilot-20261022`, 현재 원격 tip `ff7eeb4974f6d7af46e33922d9c89cad53951c8b`.
- **최신 P50 CI:** 이전 tip `d322ae3`의 P50 offline/history runs `39738595564`/`39738587930`는 fixture mismatch로 실패했다. P50 담당자가 `e67788a`에서 timestamp 단위 테스트와 receipt 검증 통합 테스트를 분리한 뒤, 최신 tip `ff7eeb4`의 P50 offline validation runs `39739596046`와 `39739590503` SUCCESS. Same-tip Three API `39739596033` SUCCESS, P6 Runtime `39739596181` IN_PROGRESS, RAW `39739590508` FAIL because the P50 branch ledger quick index omits detailed P6-081–086 records; C Contract/Frozen Guard SUCCESS. This is a documentation guard failure, not a collector defect. 마지막 P50 History 전용 SUCCESS는 이전 tip `d82a90b` run `39739469191`이며, `ff7eeb4`에서 같은 history job은 아직 확인되지 않았다. 전체 P50 CI 미완료.
- **수집/운영:** 문서에 기록된 과거 승인 A-lane 1페이지 실수집 증거는 존재한다. 이 보고 작업에서는 Meta 호출 0, 운영 DB/RAW 수정 0, n8n 시작·워크플로 활성화 0, P294 활성화 0, main 병합 0.
- **최종 목표 진행:** P50-first 개발 중. 기존 오프라인 n8n latest/mock 경로는 있고, 운영 n8n 자동수집 및 P50 history 실행·재개 연동은 미완료다.

## 지금까지 완료한 주요 작업

1. P6 frozen design와 담당자 분리를 보존했다. 이 브랜치에서는 n8n 실행 경계·결과 분류만 다루며, P50/P6 엔진의 커서·수집 알고리즘을 별도 구현하지 않는다.
2. n8n P50 workflow에서 `manual_review`와 부분 `attention` 결과가 성공처럼 종료될 수 있던 라우팅을 fail-visible `StopAndError`로 바꿨다. 민감정보는 오류 메시지에 싣지 않는다. `serial_busy`는 호출·재시도 없이 대기 상태로 처리한다.
3. P6-103에서 Capture-only recovery를 새 Meta 호출로 오분류하고, recovered page를 미진척으로 판단하던 문제를 수정했다. 복구 완료 페이지는 진척으로 계산하고 실제 신규 Meta 호출 여부는 `false`로 분리한다.
4. P50 n8n runner는 테스트된 엔진 SHA `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`에 고정했다. P50 담당자 브랜치에 새 history 구성요소가 생겼더라도 CLI/영수증 계약과 CI가 검증되기 전에는 pin을 옮기지 않는다.
5. Simulation v2.0을 이용해 처리량을 조건식으로 모델링했다. `H=max(0,Q−L−C−R)`에서 실제 quota `Q`, catchup `C`, reserve `R`, quota group 공유 여부, 처리시간 및 게시량은 미확인이라 365일 완료일을 사실처럼 예측하지 않았다.
6. 실행 환경은 A안(Windows 로컬 n8n + 로컬 PowerShell P50 runner)을 낮은 공수의 개발 기준으로 선택했다. 이는 운영 승인이나 배포 결정이 아니다. n8n CLI 2.40.7은 있으나 마지막 확인 때 프로세스/포트가 없었고, 원격 학원 n8n 주소는 확인되지 않았다. 워크플로와 스케줄은 계속 비활성이다.
7. 기존 기록에 격리 n8n mock E2E 성공/검토 오류 저장이 있으며, 최신 P6-103 후속 집중 검증은 20 passed + 6 guard subtests, serial-lock 8/8, Node 분류 mock 5/5였다. 해당 테스트는 실제 Meta 수집을 증명하지 않는다.

## 최신 P50 병목과 원인

P50 `ff7eeb4`의 source를 GitHub에서 확인했다. `three_api_p50_manual_pilot.py` 진입점에는 `--source`, `--workspace`, `--max-pages`, `--live`, `--confirm-owner-approval`, `--credentials-dir`만 있고 `history/status/recover` dispatcher가 없다. history anchor/fixture 관련 엔진 코드가 일부 추가됐지만, n8n이 호출할 history CLI와 결과/영수증 계약이 아직 준비됐다고 볼 수 없다. 따라서 n8n pin은 `10ebaf8`을 유지한다.

이전 `d322ae3`에서 두 P50 offline job에 다음 fixture 오류가 발생했으며, P50 owner commit `e67788a`가 timestamp unit test를 receipt integration에서 분리했다. 최신 tip `ff7eeb4`의 offline validation은 통과했으며, history integration의 마지막 별도 성공은 이전 tip `d82a90b`다.

- 실패 테스트: `test_invalid_tail_timestamp_rejected_before_store_write`
- 실제 증상: `validate_raw_receipt()`가 synthetic `SimpleNamespace`에서 `capture_id`를 읽다가 `AttributeError`.
- 확인 가능한 즉시 원인: 테스트 double이 현재 validator가 요구하는 receipt 필드를 제공하지 않는다. 이는 해당 테스트 fixture/계약 불일치 증거이며, production history 엔진 결함으로 단정할 수 없다.
- 미확인: 최신 tip에서 History 전용 job과 전체 P50 CI가 같은 SHA로 통과할지. P50 담당자 영역이므로 이 n8n 브랜치에서 엔진 소스를 수정하지 않는다.

fixture 오류는 해결된 [P6-105](P6_TROUBLESHOOTING_LEDGER.md), 현재 RAW CI 문서 guard 실패는 [P6-106](P6_TROUBLESHOOTING_LEDGER.md)에 구분해 기록했다.

## n8n 브랜치 검증 및 Git

- 문서 갱신 전 code HEAD `63fbabb0f6e430a9b5b26ecff706ecd9ad460b18`는 원격과 일치했고, 보고서/docs-only 변경을 기존 브랜치에 push했다. 마지막 SHA와 Actions를 최종 재확인할 것.
- 직전 n8n SHA `63fbabb` GitHub check-runs: **16/16 SUCCESS** (Windows 오프라인 shards, offline runtime/tests, contract, frozen-design SHA guards).
- 마지막 로컬 집중 실행 기록: `python -m pytest -q tests/test_three_api_p50_n8n_tick.py tests/test_n8n_p50_export.py tests/test_p6_troubleshooting_handoff_guard.py` → **20 passed, 6 subtests passed**.
- 이번 turn의 코드 변경은 없고, 문서 검사 `git diff --check` PASS 및 handoff guard **4 passed + 2 subtests passed**를 실행했다. Docs-only push 이후 branch SHA/Actions는 완료 보고 기준으로 최종 확인한다.
- main 병합 및 강제 push 금지.

## 다음 담당 GPT가 이어서 할 일

1. P50 owner에 P6-081–086 빠른 색인 누락을 전달해 상세 이력 보존 상태로 색인을 채우고 RAW/guard CI를 재실행하도록 요청한다. 이후 `ff7eeb4`의 Runtime과 History 동급 SHA 검증을 확인한다.
2. P50 소유자가 실제 CLI, 상태/영수증 JSON 계약, `latest/history/status/recover`와 offline verification SHA를 제공할 때까지 n8n pin `10ebaf8`을 유지한다. 엔진/커서 코드는 이 n8n 작업자가 수정하지 않는다.
3. 새 엔진 계약에 맞춰 n8n mock latest → receipt verify → history → 다음 KST 날짜 재개 경로를 오프라인에서 추가 검증한다. 중복 실행 lock, 상태 전이, RAW/Capture/receipt 교차검증, failure alert redaction을 확인한다.
4. n8n 운영 프로필 격리와 command allowlist를 확인하고, 알림 대상 credential을 정한다. 알림 채널은 사용자가 “나중에”로 보류했다.
5. 운영 workflow/schedule 실행, 추가 Meta 요청, 운영 DB 수정, P294 활성화는 별도 사용자 승인 전까지 금지한다. P50 실증 뒤 P294, 이후 Google Sheets 순으로 진행한다.

## 선행 문서

- [AGENTS.md](../AGENTS.md) → [DESIGN_FREEZE_NOTICE.md](../DESIGN_FREEZE_NOTICE.md) → [동결 P6 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)
- [HANDOFF.md](../HANDOFF.md) · [STATUS.md](../STATUS.md) · [트러블슈팅 장부](P6_TROUBLESHOOTING_LEDGER.md)
- [n8n P50 완료 체크리스트](P50_N8N_COMPLETION_TODO.md) · [P50 Simulation v2.0](P50_N8N_SIMULATION_V2_2026-10-22.md) · [P50 진행 상세](P50_GPT_PROGRESS_REPORT_2026-10-22.md)

**민감정보:** 사용자 메시지에 과거 인증 문자열이 포함됐지만, 이 보고서는 토큰·계정 ID·로스터·RAW payload를 재인용하지 않는다. 해당 문자열을 Git, 로그, 이슈 또는 보고서에 옮기지 말 것.
