# OHORA Trend Catcher — GPT 작업 인계 보고서

**기준일:** 2026-10-22 KST
**저장소:** `C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\ohora-trend-catcher`
**현재 브랜치:** `codex/p6-readiness-hardening-20261019`
**P2 source fix:** `0278cae64da5ec52430eaa034ca32ed0360d69c3`; **P6-103 n8n correction:** `2a09fb346721893a749df4b74ab7a427551a38d3` (pushed; all 6 Actions success, including Runtime Integration `39733073179`)
**P50 엔진 origin tip:** `e0ecd043b70c2f8f49936bc279e40d544232c862`

## 최근 사용자 지시와 현재 우선순위

사용자는 설계 대격변을 알리며 대기를 요청했으나, 이후 제공된 최신 지침 파일은 설계 v3.0에 맞춰 P1/P2 오프라인 개발·테스트·기존 브랜치 커밋/push를 재개하도록 명시한다. 이 인계서는 그 이후 상태를 반영한다. 신규 Meta 실호출, 운영 DB 변경, n8n 일정 활성화, P294 활성화 및 main 병합은 금지 상태다.

이후 사용자가 첨부 지침으로 오프라인 P1/P2 재개를 명시했다. P50 v3/Simulation v2.0은 목표·용량 모델의 최신 기준이지만 P6 동결 설계의 294계정 운영 안전 조건은 유지한다. P50/P294 저장소·커서·이력 분리, P294 실운영 보류도 유지된다.

## 이번에 구현·검증된 n8n P1/P2 수정

- `kq3_294_collection/n8n_p50_daily_collection.json`에서 `manual_review` 및 부분 실패 `attention` 결과가 성공으로 끝나던 제어 흐름을 수정했다. 이제 redacted 분류 이후 IF gate를 거쳐 `StopAndError`로 종료되어 n8n execution이 실패로 기록된다. 정상 진행 및 대기 결과는 기존 결과 저장 경로를 따른다.
- `serial_busy`는 엔진의 기존 serial lock 결과를 기다림 상태로 표현하며 API 호출이나 자동 재시도를 하지 않는다.
- P50 수집 엔진/커서 복구 로직은 수정하지 않았다. 기존 P50 엔진 pin은 검토·테스트된 `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`이다.
- 수정은 P2 소스 커밋 `0278cae64da5ec52430eaa034ca32ed0360d69c3`과 후속 문서 동기화 커밋으로 같은 작업 브랜치에 반영됐다. `main` 병합은 하지 않았다.

## P6-103 n8n recovery result correction

- Offline Node reproduction showed a verified recovery-only result was classified as `manual_review` and reported a new Meta call. Source `LiveSerialStep.step()` confirms `recovered` commits an existing Capture before dispatch; no HTTP is issued on that path.
- Adapter now maps `recovered` to `live_meta_called=false`; workflow requires valid nonnegative recovery count and counts verified recovered pages as progress. Invalid counts remain fail-closed. P50 engine internals untouched.
- `2a09fb346721893a749df4b74ab7a427551a38d3` pushed; local focused run 20 PASS +6 subtests. All 6 Actions succeeded, including Runtime Integration run `39733073179`. No API/DB/schedule side effect.

## 오프라인 검증 증거

- Latest focused command `python -m pytest -q tests/test_three_api_p50_n8n_tick.py tests/test_n8n_p50_export.py tests/test_p6_troubleshooting_handoff_guard.py`: **20 passed, 6 subtests passed** after P6-103.
- pinned engine `tests/test_three_api_serial_lock.py`: **8/8 passed**.
- Node classifier 시나리오: **5/5 passed** (offline wait, clean progress, partial attention, manual review, serial busy).
- 격리 n8n 2.40.7 CLI E2E: offline preflight 성공 결과가 `success`, operator-review 결과가 sanitised `error`로 저장됨. 임시 비활성 workflow/격리 DB만 사용했다.
- JSON parse 및 `git diff --check`: PASS.
- 이 검증은 실제 Meta API 수집, 운영 n8n 예약 실행 또는 운영 DB 결과를 증명하지 않는다.

## 실수집·자동화 운영 현황

- 기존 HANDOFF에는 별도의 사용자 승인으로 수행된 P50 A 레인 1페이지 성공 기록이 있다: HTTP 200, 안전 usage 0%, 게시물 관측 4건, durable receipt/Capture ack/coordinator 완료를 read-only로 확인했다. 이번 P1/P2 수정 중 새 Meta 요청은 없었다.
- 이번 격리 E2E는 코드/워크플로의 오프라인 결과 라우팅만 검증했다. 예약 운영 workflow는 비활성이다. 운영 n8n 인스턴스, 예약 cadence/budget, 경고 채널/자격증명은 미확정 또는 보류 상태다.
- P50 upstream은 `e0ecd04`까지 진행했다. 추가/수정된 history modules 및 2페이지 연속 과거 복구 테스트가 있고 최신 변경은 history recovery 전 latest-complete roster 검증을 추가했다. 최신 `three_api_p50_manual_pilot.py`는 기존 `run_p50`과 latest용 flags만 제공하므로 n8n에서 history를 안전하게 호출할 owner CLI 계약이 아직 없다. Keep pin `10ebaf8`. `e0ecd04` combined-status query returned no contexts; P50 CI is unknown.
- 로컬 n8n 인프라 read-only probe: CLI `2.40.7` 설치, n8n/Node 실행 프로세스 0, 포트 5678/5680/5681 listener 0, Docker service 미설치. 이는 이 Windows PC의 상태만 나타내며 원격 학원 n8n host는 미확인이다.
- Simulation v2/v3 illustrative throughput uses `H=max(0,Q−L−C−R)`, with P50 `L≤150`; `Q`, `C`, `R`, actual posts, quota sharing and latency remain unknown. If `Q≤L+C+R` persists, history cannot progress. Example page/day estimates are hypothetical, not measured Meta capacity.
- P294 활성화와 Google Sheets 연동은 미완료 후속 범위다. Google 계정 인증/스프레드시트 ID는 Sheets 단계가 실제 시작될 때 요청한다.

## Git 및 문서 상태

- P6-103 source commit push 이후 GitHub branch API에서 origin ref가 `2a09fb346721893a749df4b74ab7a427551a38d3`임을 재확인했다. P50 origin feature ref는 `e0ecd043b70c2f8f49936bc279e40d544232c862`다.
- 수정 상세 및 테스트 증거는 `docs/P50_GPT_PROGRESS_REPORT_2026-10-22.md`, `docs/P50_N8N_COMPLETION_TODO.md`, `docs/P6_TROUBLESHOOTING_LEDGER.md`에 있다. HANDOFF/STATUS 상단 요약도 이번 P1/P2 및 v3 자료를 가리킨다.
- GitHub Actions on `2a09fb346721893a749df4b74ab7a427551a38d3`: Frozen Design Guard `39733073246`, C Contract `39733073167`, Three API offline `39733073258`, Frozen Guard `39733068560`, RAW v2 offline `39733068493` **SUCCESS**; P6 Runtime Integration Offline `39733073179` completed **SUCCESS**. Focused local suite is 20 passed +6 subtests and serial-lock is 8/8.
- Project Book 색인 업데이트/재조회는 다음 인계 작업이다.

## 다음 작업자가 먼저 할 일

1. 새 설계 지시를 읽고 기존 전략을 대체하는 범위를 확인한다. 변경 설계나 운영 범위가 불명확하면 추정 구현하지 않는다.
2. 작업 전 `AGENTS.md`, `DESIGN_FREEZE_NOTICE.md`, P6 동결 설계, 최신 `HANDOFF.md`, `STATUS.md`, `docs/P6_TROUBLESHOOTING_LEDGER.md`를 읽는다. P6/RAW-v2 관련 작업이면 AGENTS가 지시한 사고 기록도 확인한다.
3. `2a09fb3` Actions 6개가 모두 성공했음을 확인했다. 문서 변경은 현재 미커밋이다.
4. P6 동결 설계, 운영 DB/RAW/Capture, 비활성 예약 및 P294 상태를 유지한다. 새로운 Meta 호출·운영 DB 변경·예약 활성화·main 병합 전에 별도 사용자 승인을 받는다.
5. 설계가 n8n 연동을 계속 요구할 경우, 알림 credential/channel과 운영 n8n endpoint/cadence/budget이 여전히 결정되지 않았음을 고려한다. 먼저 mock 기반 계약을 갱신하고 운영 워크플로는 비활성으로 둔다.

## 민감정보 취급

이 보고서에는 토큰, 계정 ID 원문, 사용자 제공 인증값, 계정 로스터, RAW payload를 넣지 않았다. 사용자가 대화에 인증 문자열을 붙여넣은 이력이 있으므로 이 파일이나 다른 문서에 그 값을 복사하지 말고, 운영자는 해당 자격증명 재발급/회전 필요성을 별도로 검토해야 한다.

## 완료 상태 한눈에 보기

| 항목 | 상태 |
|---|---|
| P1/P2 n8n 분기 수정 | 원격 반영 (`0278cae`, `2a09fb3`); 최신 CI Runtime Integration 1개 실행 중 |
| 오프라인 단위/격리 E2E | 위 범위 PASS |
| 새 Meta 수집 성공 | 이번 작업에서는 실행하지 않음; 기존 승인 A 1페이지 proof는 HANDOFF에 기록 |
| n8n 예약 운영 | 미완료, 비활성 |
| P50 최신 엔진 tip 통합 | 미완료; upstream CI 실패로 pin 유지 |
| P294 실운영 | 보류 |
| Google Sheets | 명세/후속 단계, 구현 전 |
| 설계 대격변 반영 | 최신 지침 반영, P1/P2 오프라인 단계 재개; 운영 변경은 금지 |
