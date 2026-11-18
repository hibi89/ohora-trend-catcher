# OHORA Trend Catcher — GPT 인수인계 보고서 (최신 확인)

**기준:** 2026-10-22 KST
**대상:** `hibi89/ohora-trend-catcher` · n8n 담당 브랜치 `codex/p6-readiness-hardening-20261019`

## 현재 판정

P50 전용 최신/history/status 수동 CLI와 history runner가 다른 작업자의 원격 브랜치에 추가됐다. P50 branch `feature/p6-50-account-pilot-20261022` tip은 `112bb2aefcbf006eedebcd3837afdf98534ca85d`이며 해당 SHA check-runs 6/6 SUCCESS (P50 offline 2, history offline 1, contract 1, frozen-design SHA guard 2)다. n8n 브랜치는 아직 검증된 구형 latest-only pin `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`을 사용하므로 신규 history cycle과 연결되지는 않았다.

이 보고서는 코드 변경 없이 현황만 갱신한다. n8n schedule/workflow는 inactive다. 새 Meta 요청, 운영 DB/RAW/Capture 수정, P294 활성화, main 병합은 없다.

## 완료한 n8n 작업

- P50 n8n 워크플로가 `manual_review`와 부분 `attention`을 성공으로 삼키지 않고 민감정보가 제거된 `StopAndError`로 끝내도록 고쳤다. `serial_busy`는 호출 없는 대기로 분류한다.
- Capture-only recovery를 신규 Meta 요청으로 잘못 집계하던 P6-103 결함을 고쳤다. 복구된 페이지는 진척으로 세고 `live_meta_called=false`로 표현한다.
- adapter는 Capture → RAW/contract receipt → coordinator 완료 결과를 read-only SQLite snapshot으로 교차검증하고, 오류 출력에는 raw stdout/credential을 노출하지 않는다.
- 기존 isolated n8n 2.40.7 mock E2E가 성공·review-error 실행을 저장했다는 기록이 있다. 이 turn에서는 새 n8n instance를 시작하거나 workflow를 가져오지 않았다.
- 작업 범위에서 벗어나지 않도록 P50 engine/history runner를 별도 개발하지 않았다. Google Sheets 사양은 파생 출력, receipt 확인 후 증분·idempotent sync를 요구하며 인증·대상 Sheet가 필요한 단계는 아직 아니다.

## 이번 현황 재검증

- GitHub `origin/codex/p6-readiness-hardening-20261019`와 로컬 HEAD가 `1c4a1f5c87c067a7d6254db7fb3353c8179aa843`로 일치한다. PR #6은 Draft/open이며 main merge는 하지 않았다.
- 해당 n8n branch SHA의 GitHub Actions check-runs는 16/16 SUCCESS.
- 집중 오프라인 명령 `python -m pytest -q tests/test_three_api_p50_n8n_tick.py tests/test_n8n_p50_export.py tests/test_p6_troubleshooting_handoff_guard.py`: 20 passed, 6 subtests passed.
- 전체 `python tests/run_offline.py`는 1,018개 테스트 발견 후 사용자가 중단해 CANCELLED다. 중간 출력에 RAW-v2/batch-recovery의 ERROR와 FAIL이 보였으나 최종 요약을 받지 못해 원인·총 실패 수는 미확인. 이는 수집기 결함으로 단정할 수 없다. P6-107에 미해결 진단 기록을 남겼다. 이 기록의 pipe-table 색인 행 누락은 P6-108로 기록·수정했고, 같은 n8n 집중 명령을 다시 실행해 20 PASS + 6 subtests PASS를 확인했다.
- 로컬 Windows n8n CLI 2.40.7 존재, n8n/Node 실행 프로세스와 5678/5680/5681 listener는 확인 시 없었다. 운영 n8n host/프로필과 Execute Command 운영 허용 여부는 미확인.

## 현재 P50 engine 계약 및 n8n blocker

P50 최신 tip `112bb2a`에 `raw_v2/three_api_p50_cycle.py`가 있고 mode는 `history`, `latest`, `status`다. `status`는 read-only 옵션만 허용한다. latest/history는 둘 다 `--live`와 `--confirm-owner-approval`을 요구하며, `history`는 먼저 미완료 최신 job을 점검/완료하고 history 실행을 호출한다. 별도 `three_api_p50_history_live_runner.py`는 Capture/RAW 복구 및 결과불명 재전송 금지 등을 구현한다. 신규 runner는 n8n 담당 브랜치에서 아직 통합·검증되지 않았다. `recover`는 독립 CLI mode로 보이지 않고 history 경로의 내부 reconciliation 여부를 계약 테스트로 추가 확인해야 한다.

다음 개발은 이 실제 cycle API를 대상으로 한 offline-only adapter/mock contract 검증이다. n8n에서 Meta-capable mode를 자동으로 실행시키려면 승인/예산 전달 정책이 필요하므로, 기존 one-page approval을 상시 스케줄 승인으로 넓히면 안 된다. workflow는 inactive로 유지하고 P50 담당자 코드 파일을 수정하지 않는다. CLI가 제공한 상태 (`resume_required`, `coverage_gap`, `resume_ready` 등)가 있으면 raw engine state를 보존해 분류하되, 이름·전이 semantics를 소스/fixture에서 확인하기 전 임의 전이 로직을 만들지 않는다.

## 전체 최종 목표와 남은 단계

목표는 P50 50계정 실증 → 수동 latest/history cycle → n8n 자동 운영 → P294 확장 → Google Sheets 출력 순서다. SQLite/RAW/Capture/커서는 기준 원본이고 P50/P294 저장소·이력은 독립 유지한다.

1. **P1:** P50 112bb2a CLI/receipt contract와 n8n 경계 검토, Windows 및 운영-host 경로 확인. Owner branch 6 checks PASS를 n8n acceptance로 오인하지 않는다.
2. **P2:** synthetic `status/latest/history`, 직렬 lock, one-run bounded dispatch, receipt/readback, fail-visible errors를 mocked CLI로 E2E 검증. status-only mock은 Meta 호출 0이어야 한다. History operation mock은 network disabled이며 engine의 실제 history 코드 소유권은 P50 담당자에게 둔다.
3. **P3:** operator의 승인된 manual latest/history 실제 cycle과 다음 KST 날짜 재개 증거를 기다려 n8n contract를 확정한다. 코드 구현/오프라인 PASS/실 Meta success는 별개다.
4. **P4:** 별도 isolated local n8n profile, Execute Command allowlist, credential-backed 알림 채널/실패 전달을 시험한다. 사용자는 알림 채널을 “나중에”로 보류했다. cadence와 per-run budget이 승인될 때까지 비활성.
5. **P5:** P50 실운영·history 연속성 검증 후 P294 확대. 지금은 비활성.
6. **P6:** 핵심 n8n 완성 뒤 Google 계정·대상 스프레드시트 확인, receipt-based idempotent sync와 partial-write resume를 검증한다.
7. 실제 quota group `Q`, 일일 latest `L`, catchup `C`, reserve `R` 및 `D_365`, uptime/latency는 미측정. `H=max(0,Q-L-C-R)`에서 장기 평균 `H`가 history 유입보다 크지 않으면 완료기한은 보장할 수 없다. 성공확률/실제 처리량을 임의로 제시하지 않는다.

## 다음 작업과 승인 경계

다음 작업은 n8n adapter/워크플로를 최신 P50 CLI 계약에 맞추는 offline mock integration이다. 먼저 current engine source/contract tests에서 CLI JSON shape, exit code, `resume_*`/coverage states, shared lock behavior를 확인하고, one-call-per-tick와 no-retry semantics를 fixture로 고정한다. 이후 HANDOFF/STATUS/ledger/Project Book을 함께 갱신하고 이 branch에 commit/push한다.

실 Meta 요청, 운영 DB/RAW/Capture 수정, workflow import/activation, schedule enable, P294 run, main merge, Google account/Sheet mutation은 별도 사용자 승인 없이 진행하지 않는다.

## 기준 자료

- [P50 v3 design](https://github.com/hibi89/ohora-trend-catcher/blob/design/p50-manual-cycle-simulation-v2-20261022/docs/P50_GOAL_DRIVEN_SIMULATION_ARCHITECTURE_V3_2026-10-22.md)
- [Official design issue #15](https://github.com/hibi89/ohora-trend-catcher/issues/15) · [Draft design PR #16](https://github.com/hibi89/ohora-trend-catcher/pull/16)
- [n8n P50 workflow](../kq3_294_collection/n8n_p50_daily_collection.json) · [completion checklist](P50_N8N_COMPLETION_TODO.md) · [troubleshooting ledger](P6_TROUBLESHOOTING_LEDGER.md)
