# OHORA Trend Catcher — GPT 현재 상황 보고서

## Latest recheck / n8n environment decision — 2026-10-22

**2026-10-22 KST · 최신 재확인:** P50 소유 브랜치 tip `0b330fc0d04317a2949b42bf83e7310b4afe9cef`. P50 history CLI·recover dispatch는 여전히 pilot entrypoint에 연결되지 않았고 n8n pin은 `10ebaf8` 유지. P50 offline validation `39737113080`/`39737108191` and History Tests `39737108250` SUCCESS. Three API `39737113093` and P6 Runtime `39737113072` IN_PROGRESS; C Contract `39737113042` and Frozen Design Guard `39737113228` SUCCESS. 로컬 Windows n8n CLI 2.40.7 설치, n8n 프로세스/5678·5680·5681 listener 없음. 원격 학원 n8n host는 자료가 없어 미확인. P50 첫 연동 목표는 Windows 로컬 n8n + 기존 PowerShell runner(A안)로 정하되 운영 instance/schedule은 비활성 유지; 별도 instance 격리와 가동승인은 미완료. Meta 실호출·운영 DB 변경·P294 활성화·main 병합 없음.

Detailed volume and operating-window analysis: [Simulation v2.0 analysis](docs/P50_N8N_SIMULATION_V2_2026-10-22.md).

### A/B host comparison (planning assessment, not production activation)

- **A — Windows local n8n + local P50 runner:** `[F]` `n8n` CLI 2.40.7 is installed; this check found no local n8n/Node process and no listener on 5678/5680/5681. The checked-in workflow invokes `powershell.exe` and `raw_v2/p50_n8n_tick.ps1`, which uses a local `%LOCALAPPDATA%\OHORA_P50\p50` workspace. This keeps runner/storage and orchestration on one host and avoids a new bridge. Setup effort/cost is the lower option (estimate `[H]`: hours to create and validate a dedicated isolated profile); availability depends on the Windows PC staying awake and connected. Do not reuse a profile with unresolved active duplicate schedules.
- **B — remote n8n + authenticated Windows agent:** could keep orchestration on a continuously available remote host, but the repository has no academy n8n address or access evidence. A secure command/lease bridge, authentication, deduplication, monitoring, and local-agent lifecycle would need design and verification; cost and time are `[U]` until the host and existing capabilities are known. It adds a cross-host security boundary.
- **Working choice:** A is the least-work P50 integration target using existing local components. It is not a production deployment decision. Keep all schedules inactive; before any instance start/import, isolate its user folder and ensure no existing Meta workflow can fire. B remains unverified, not rejected.

The P50 owner tip advanced after the prior `04df381` report. At `88c3611`, `raw_v2/three_api_p50_manual_pilot.py` still exposes only the bounded latest pilot; status is a separate reader, and history/recover are not n8n-callable. The n8n pin remains `10ebaf8`. Current owner-branch checks are mixed/incomplete as stated above; pending/in-progress is not PASS.

**작성 기준:** 2026-10-22 KST
**저장소:** `hibi89/ohora-trend-catcher`
**로컬 작업 루트:** `C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\ohora-trend-catcher`
**작업 브랜치:** `codex/p6-readiness-hardening-20261019`
**P6-103 code fix:** `2a09fb346721893a749df4b74ab7a427551a38d3` (pushed); this report records the current P50 owner-branch recheck

## 1. 사용자 목표와 현재 우선순위

- 최종 전략은 **P50 우선 완성 → 제한된 실수집 및 n8n 정상화 검증 → P294 확장**이다.
- P50은 공통 수집 엔진/n8n 경계를 재사용해야 한다. P294는 현재 운영 활성화 보류다.
- 설계가 크게 바뀌었다는 사용자 알림이 있었고, 이후 사용자는 GPT에 지금까지 한 작업과 최신 상태를 보고하도록 요청했다. 그러므로 이 보고서는 현황 전달 목적이며, 새 설계의 구현 승인을 뜻하지 않는다.
- P6 frozen design과 타 작업자의 수집기/커서 복구 작업은 변경하지 않는다. `main` 병합도 하지 않았다.

## 2. 이번 브랜치에서 실제로 한 작업

### P50 n8n 결과 라우팅 (P2)

- 검토 또는 부분 실패(`manual_review`, `attention`)가 n8n에서 성공처럼 끝날 수 있던 흐름을 수정했다.
- 실패는 민감정보 없는 고정 메시지의 `StopAndError`로 남기고, 정상 진척/대기 상태는 실행 기록에 보존한다.
- `serial_busy`는 Meta 호출·재시도 없이 대기 결과로 처리한다.

### Capture 복구 결과 분류 (P6-103)

- 재현된 결함: 이미 저장된 Capture를 확인·커밋한 `recovered` 결과를 새 Meta 실호출로 표시했고, 회수 페이지만 있는 성공 결과가 `manual_review`로 오분류됐다.
- n8n 어댑터는 `recovered`에서 `live_meta_called=false`로 표시한다.
- 워크플로 분류기는 검증된 `recovered_pages`를 진척으로 계산하고, 누락/음수 등 잘못된 값은 fail-closed로 처리한다.
- P50 수집 엔진, 커서 알고리즘, RAW/운영 DB, workflow 활성 상태는 변경하지 않았다.

## 3. 검증된 결과와 한계

- 로컬 집중 테스트: `python -m pytest -q tests/test_three_api_p50_n8n_tick.py tests/test_n8n_p50_export.py tests/test_p6_troubleshooting_handoff_guard.py` → **20 passed, 6 subtests passed**.
- pinned P50 serial-lock 테스트 → **8/8 통과**. Node classifier 합성 시나리오 → **5/5 통과**. P6-103 변경에는 recovery-only, 잘못된 recovery count, 신규 페이지, serial wait 케이스가 포함된다.
- 격리 n8n CLI 2.40.7의 앞선 mock E2E에서는 정상 오프라인 결과가 `success`, 검토 필요 결과가 sanitised `error`로 저장된 기록이 있다. P6-103 변경 후 운영 n8n 재실행은 하지 않았다.
- 최신 원격 Actions 확인(코드 SHA `2a09fb3`): 6개 Actions가 전부 **success**로 완료됐다. Runtime Integration Offline run `39733073179`도 성공이다.
- **실제 API 성공과 코드 검증은 별개다.** 이 수정 작업에서는 Meta 실호출 0회, 운영 DB 변경 0회, workflow schedule 활성화 0회다. 기존 문서에 기록된 승인된 단일 P50 A-lane 1페이지 실수집 증거는 별도 과거 작업이며 이번 변경의 검증이 아니다.

## 4. P50 엔진·n8n·데이터 상태

- n8n은 검토된 P50 엔진 SHA `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`에 고정되어 있다. P50 담당 브랜치 `feature/p6-50-account-pilot-20261022`의 최신 tip은 `04df3817e3c983f5457d1a8a111e4c1affecd354`다. P50 offline/history workflows는 성공했으며, P6 Runtime `39734635964`과 Three API offline `39734635988`은 취소 상태였고 취소 원인은 확인되지 않았다. 최신 CLI에는 latest 실행만 있고 status는 별도 read-only command이며, history/recover CLI와 n8n receipt contract가 없어 pin은 `10ebaf8`로 유지한다.
- n8n workflow는 비활성이다. 로컬 n8n CLI 2.40.7은 설치되어 있으나 현재 프로세스/listener는 없고, 원격 n8n 운영 주소/host는 확인되지 않았다.
- 알림 채널은 사용자가 “나중에”로 미뤘다. Google Sheets는 이후 단계이며 인증/스프레드시트 ID를 아직 요청하지 않았다.
- P294, unattended scheduling, 추가 live collection은 보류다. P50/P294 간 DB·커서·이력 독립성을 유지한다.
- v3 처리량 식 `H=max(0,Q−L−C−R)`는 조건식이다. 실제 quota capacity `Q`, catchup 부하 `C`, reserve `R`, 계정별 게시물 분포 및 quota-group 공유 여부는 확인되지 않았다.

## 5. Git 및 문서 상태

- P6-103 code fix `2a09fb3` is pushed. HANDOFF/STATUS, the GPT handoff, progress checklist, and ledger reflect the latest P50 branch recheck in a documentation-only commit on `codex/p6-readiness-hardening-20261019`. The final branch SHA is rechecked after push; no main merge or force push.
- P6-103 incident record and the current P50 owner branch recheck are documented in `docs/P6_TROUBLESHOOTING_LEDGER.md`.
- HANDOFF/STATUS, GPT reports, progress checklist, and ledger are synchronized as documentation-only updates on the existing branch; check the final branch HEAD after the documentation commit.
- Project Book `main/BOOK.md` is updated and re-fetched after the branch documentation sync.

## 6. 다음 GPT 담당자의 작업 순서

1. 새 설계 원문과 사용자의 가장 최신 지시를 먼저 확인하고, 변경 범위/승인이 불명확하면 기존 frozen P6 및 운영 제한을 유지한다.
2. 현재 작업 트리의 기존 변경을 보존한다. 먼저 `git status`, `git diff`를 확인하고 문서 변경을 코드 변경과 혼동하지 않는다.
3. 최신 SHA `2a09fb3` Actions 6개가 모두 성공했음을 확인했다. 이후 새 commit이 생기면 해당 SHA의 CI를 다시 확인한다.
4. 요청된 설계가 여전히 n8n 범위라면 P50 엔진 소유자가 제공할 CLI/status/receipt contract를 기다리거나 mock contract로만 작업한다. P50 내부/커서 로직은 새로 구현하지 않는다.
5. 구현 완료 뒤 HANDOFF, STATUS, ledger, Project Book를 동기화할 범위와 사용자가 방금 요청한 새 설계를 대조한다.
6. Meta 실호출, 운영 DB 수정, schedule 활성화, P294 활성화 또는 `main` 병합 전에 별도 승인을 받는다.

## 7. 안전/민감정보

이 보고서에는 토큰, 계정 ID, 계정 로스터, RAW payload를 포함하지 않았다. 사용자가 과거 대화에 인증 문자열을 직접 붙인 이력이 있으므로 그 문자열을 코드·로그·문서·Git으로 옮기지 말 것. 자격증명 재발급/회전은 사용자 계정에서 별도 처리해야 한다.
