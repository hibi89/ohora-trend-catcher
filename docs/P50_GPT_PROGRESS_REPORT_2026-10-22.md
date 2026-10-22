## 최신 추가 보고 — P1/P2 재개 (2026-10-22)

### v3 design and current remote recheck

- Read the v2.0 Simulation skill and P50 architecture v3.0, plus official Issue #15 and documentation PR #16. Issue #15 describes the design as published/design-only; PR #16 is documentation, not runtime implementation or live approval.
- Successfully fetched origin. The n8n branch base at fetch time was `4e1469225a17d28285b09132fdb7175fbee49e1e`; P2 source commit `0278cae64da5ec52430eaa034ca32ed0360d69c3` and the follow-up documentation sync are on the local work branch. Confirm the current GitHub branch ref and its checks before claiming publication. P50 engine branch is now `690383f2e3e02f4e6c7614f201731e3d1e7773e2` (supersedes prior `c4944f1` snapshot).
- P50 changes since the reviewed engine pin add/extend history anchor, backlog, commit, offline ingress, recovery, request, session, status and store modules. The manual pilot CLI still exposes only the existing `run_p50` entry point and latest-oriented flags; n8n history integration is therefore pending an engine-owned CLI/status/receipt boundary. Keep pin `10ebaf8`.
- The v3 throughput model is conditional: `H=max(0,Q−L−C−R)`, with latest page ceiling `L≤150` under the P50 three pages × 50 accounts rule. `Q` (safe API capacity), `C` (required catchup), `R` (reserve), quota-group sharing, posting distribution, and latency are **unknown**. Example 1,950-page / 3-posts-per-account-per-week assumptions and 7/14/30-day thresholds in v3 are illustrative only. Persistent `Q≤L+C+R` means zero history progress.
- Re-run of focused local tests after fetch: **19 passed, 2 handoff guard subtests passed**. P50 owner CI at `690383f` was not independently confirmed; combined-status query returned no status contexts.

- n8n P50 결과 분기 결함을 수정했다. 이전 export는 `MANUAL_REVIEW` 또는 계정 실패(`attention`)를 classifier에서 표시하고도 NoOp으로 끝내 n8n 실행이 성공 상태가 될 수 있었다. 이제 해당 상태를 `StopAndError`로 보내 n8n 실행 기록에 실패로 남긴다. 실패 메시지는 고정 문자열이며 runner stdout/토큰을 포함하지 않는다. 정상 진척과 대기 상태는 결과로 저장한다. Workflow는 계속 비활성이다.
- 검증: `python -m pytest -q tests/test_three_api_p50_n8n_tick.py tests/test_n8n_p50_export.py tests/test_p6_troubleshooting_handoff_guard.py` → **19 PASS, 2 guard subtests PASS**; pinned engine `test_three_api_serial_lock.py` **8/8 PASS**; Node classifier 5/5 PASS (offline wait, clean progress, partial attention, manual review, serial busy). JSON parse 및 `git diff --check` PASS. 격리 n8n CLI E2E도 실제로 재실행했다.
- 비교: 최신 P50 feature branch `c4944f1565ade857754a4a71b6c94af2e61903ec`; pinned `10ebaf8`부터 최신 tip까지 n8n이 호출하는 P50 entrypoint, Capture/RAW/coordinator, roster/status 경계 파일은 변경되지 않았다. 새 history 모듈은 P50 코드에는 추가됐으나 n8n tick에서 dispatch하지 않는다. 따라서 pin은 `10ebaf8` 유지; history 연동을 구현했다고 주장하지 않는다.
- 새 upstream CI run `39727639343`은 green이 아니다. Ubuntu `offline-runtime` 1,087 tests 중 3개 handoff guard 실패: HANDOFF/STATUS 상단에서 ledger 링크 누락 및 incident index/detail mismatch. Windows `offline-windows (remaining)` 927 tests 중 같은 3개 guard 실패와 synthetic `raw.sqlite` cleanup 시 `WinError 32` 17건. Windows 일부 shard는 PASS, 일부 CANCELLED. 17건의 핸들 소유자는 미확인이다. 이는 P50 owner branch 문제이며 이 작업에서 수정하지 않았다.
- 이번 편집 범위: `kq3_294_collection/n8n_p50_daily_collection.json`, `tests/test_n8n_p50_export.py`, `tests/test_three_api_p50_n8n_tick.py`; handoff/status/ledger/checklist/report 문서도 동기화했다. Meta 호출·운영 DB 변경·n8n 활성화·P294 활성화·main 병합은 없음. 로컬 브랜치 `4e1469225a17d28285b09132fdb7175fbee49e1e`와 알려진 `origin/codex/...` SHA는 변경 전 일치했으며, 새 변경은 아직 커밋/push되지 않았다. `.git/FETCH_HEAD` ACL 때문에 일반 `git fetch`는 실패했고 GitHub connector/local remote refs로 latest upstream 확인했다.
- **다음:** P50 owner가 `c4944f1` guard/Windows cleanup CI를 해결한 뒤 candidate SHA를 격리 오프라인 계약 테스트한다. 이 n8n branch의 `serial_tick_lock` is owned by the pinned engine: the tick reports `serial_busy` as a no-call wait; the pinned lock suite is 8/8 PASS. Actual n8n production configuration, alert channel and any live schedule/API call still require follow-up/approval.

> 아래 본문은 이전 인계 시점의 상세 기록이며, P50 remote SHA와 CI 상태는 이 최신 추가 보고가 대체한다.


### Isolated n8n E2E revalidation

- The first temporary roster fixture was invalid (one-line placeholder rather than required 50 rows and country counts), producing the expected fail-closed `MANUAL_REVIEW`. This was a test-fixture mistake, not an engine defect; P6-100 records it.
- Replaced only the ignored temporary input with 50 synthetic accounts (US 18/KR 16/JP 16). The existing launcher returned `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, 0 attempts/pages and `live_meta_called=false`. Isolated inactive workflow execution then completed through classifier, IF false branch and result NoOp. n8n CLI reported `status=success`, `storedAt=db`; no P50 workspace was written.
- The malformed-input run demonstrated the review branch ending in `StopAndError`; n8n reported and persisted `status=error` with sanitized classifier fields. A normal sandbox attempt to reach n8n's loopback task broker failed `EACCES` and left one `running` record in the disposable test DB; an elevated isolated rerun completed. That temporary DB now has one `success`, one `error`, and one orphan `running` record from the sandbox-denied attempt; no records were deleted.
- Import emitted a telemetry DNS `ENOTFOUND`; no connection succeeded. Diagnostics/version notifications were disabled for subsequent CLI runs. Meta API calls and production database writes were zero.
# OHORA P50/n8n 진행 보고서 — 2026-10-22

## 현재 인계 요약

- 작업 범위: P50 기존 수집 엔진을 재사용하는 n8n 연동과 오프라인 검증. P6 수집기 내부 및 커서 복구 기능은 다른 작업자의 소유 영역으로 둔다.
- 저장소: `hibi89/ohora-trend-catcher`
- 브랜치: `codex/p6-readiness-hardening-20261019`
- 보고서 기준 HEAD: `39e1e3a1aa222a25678d538bfec4ee388bb1b2d7`; local HEAD와 origin branch SHA 일치를 push/fetch로 확인.
- 이번 커밋: `6d096d8` Windows fixture correction, `dea9ecc` reviewed P50 pin, `39e1e3a` ledger + HANDOFF/STATUS sync. `main` merge 없음.
- `main` 병합은 하지 않았다.

## 지금까지 확인·완료된 작업

1. n8n이 사용할 정확한 엔진 SHA를 고정했다: 마지막으로 검토·테스트한 P50 SHA `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`. 해당 엔진은 Capture/RAW 증거 기반 latest-tail history anchor와 receipt ID 차단을 포함하고 focused suite 48개 중 47 PASS, 1 SKIP. P50 upstream은 fetch 시점 `52ed20a187dfc25abe725cfacee1a74efe99147c`까지 history request/commit, backlog, recovery 코드를 추가했으나 n8n 계약 검토/통합 테스트가 안 되어 pin에 반영하지 않았다. n8n에서 history를 실행하는 기능은 미구현이다.
2. P50 n8n launcher가 구 엔진 SHA를 고정하던 문제를 찾아 현재 후보 SHA로 로컬 변경했다. 고정 SHA가 테스트에서도 검증되도록 export 테스트 기대값도 갱신했다.
3. 오프라인 테스트 실행기에서 저장소 루트를 `sys.path`에 넣지 않아 `src.collection_schedule` 검색이 실패하는 문제를 수정했다. 이 수정은 `4088bad`에 커밋·push되어 로컬/원격 HEAD 일치를 확인했다.
4. Windows 테스트에서 짧은 경로 별칭과 정규화 경로 문자열을 직접 비교하는 픽스처 문제를 확인했다. 테스트가 `_load_pilot()`의 실제 정규화 동작과 같은 `Path.resolve()` 기준을 사용하도록 로컬 수정했다.
5. n8n 2.40.7의 격리 임시 프로필에서 이전 P50 mock workflow 실행 레코드 ID 3이 `success`로 저장된 것을 read-only 조회로 확인했다. 이후 현재 pinned engine `10ebaf8`으로 launcher를 직접 오프라인 실행해 `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, 시도 0, 페이지 0, `live_meta_called=false`를 재확인했다. n8n workflow 자체를 현재 pin으로 다시 end-to-end 실행한 것은 아니며, 이 결과는 예약 운영이나 Meta API 성공을 뜻하지 않는다.
6. 사용자 제공 50행 로스터의 오프라인 정규화 및 핀된 P50 validator 통과, 기존 한 페이지 P50 live proof는 기존 HANDOFF에 기록돼 있다. 그 proof는 한 번의 승인된 A lane 요청에 한정되며, 예약 실행 검증이 아니다.

## 검증 결과와 현재 CI

- `python -m unittest discover -s tests -p test_collection_schedule.py -v`: 15/15 PASS.
- P50 엔진 `10ebaf8` focused suite: 48개 실행, 47 PASS, 1 SKIP.
- P50 n8n adapter/export focused suite: 13/13 PASS. 최신 launcher pin 반영 후 재실행했다.
- Handoff guard + n8n focused tests: 17 PASS and 2 guard subtests PASS.
- `python tests/run_offline.py --list-groups`: 1,015 distinct tests 발견.
- `git diff --check`: PASS (줄바꿈 변환 경고만 있음).
- 전체 오프라인 테스트의 로컬 실행은 10분 넘게 완료되지 않아 중단했다. **CANCELLED이며 전체 로컬 suite PASS로 집계하지 않는다.**
- GitHub run `39725883303` (`4088bad`): Linux runtime PASS; Windows `remaining` failed one fixture path-alias assertion. Fixture corrected in `6d096d8`. `6d096d8` CI was superseded and Windows shards were cancelled, so it is not a full PASS.
- n8n `dea9ecc`: runtime, `tests`, `offline-tests`, contract and frozen-design checks PASS; several Windows shards were cancelled or superseded. Latest n8n `39e1e3a`: all reported GitHub check-runs completed SUCCESS, including offline-tests, Linux runtime, every Windows shard, contract and frozen-design.
- P50 latest remote SHA `52ed20a`: P50 offline, contract and frozen-design checks PASS; commits after pinned `10ebaf8` add history request/commit/backlog/recovery modules and have not been integrated into n8n. The n8n pin stays at the focused-tested `10ebaf8` pending contract review.
- 과거 CI `39723488710`에서 확인한 import root 문제는 `4088bad`로 수정했다.

## n8n 실행 환경 및 선택지

- 현재 Windows에서 `n8n` CLI는 설치되어 있고 버전은 2.40.7이다. n8n/Node/Docker process 없음, 포트 5678/5680/5681 listening 없음, Docker service 미설치. 저장소 검색에서도 학원 n8n URL/host/endpoint를 찾지 못했다. 이는 로컬 n8n 상태만 증명하며 외부 학원 n8n 상태는 **미확인**이다.
- **A안: 같은 Windows PC에서 n8n + P50 엔진.** 인증 파일·엔진·P50 저장소가 로컬에 있으므로 브리지/원격 인증 작업 큐가 필요 없고 운영 복잡도가 가장 낮아 보인다. CLI는 있으나 의도된 production profile, 서비스/로그인 자동 시작, n8n command-node 허용 설정은 미확인; 운영 스케줄은 비활성으로 남겨야 한다.
- **B안: 학원 원격 n8n + 로컬 실행 agent.** 원격 workflow가 이 PC의 PowerShell/파일을 직접 실행할 수 없으므로 인증된 queue/polling bridge, 명령 allowlist, 승인·중복 방지·agent 상태 감시가 추가로 필요하다. 학원 endpoint 접근성과 비용/라이선스는 미확인. 원격 환경이 이미 있어도 브리지 개발·보안 검증 시간이 A안보다 길다.
- **비교 한계:** A는 기존 장비를 쓴다면 추가 hosting 비용을 가정하지 않지만 Windows 가동·백업·n8n edition/license는 확인 필요. B는 기존 학원 n8n 비용을 재사용할 수 있는지 알 수 없고 bridge/agent 운영·감시 비용이 추가될 수 있다. 수치 공수/비용은 입력이 없어 산정하지 않았다. A는 동일 Windows account 안의 command node 실행 권한을 좁혀야 하고, B는 원격 인증 queue와 local agent라는 공격 표면 및 장애 도메인이 더 생긴다.
- **임시 권고:** 실행 위치를 한 곳에 두는 A안이 최소 구현 경로로 보이나, 실제 production host를 확정한 결정은 아니다. 외부 학원 n8n endpoint 또는 기존 운영 관례를 사용해야 한다면 그 URL/실행 위치 정보가 필요하다. 어떤 안도 아직 production에 배포·활성화하지 않았다.

## 현재 변경 및 인수인계 상태

- `raw_v2/p50_n8n_tick.ps1`: P50 engine expected SHA를 `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`로 갱신.
- `tests/test_n8n_p50_export.py`: 같은 pin을 검증.
- `tests/test_three_api_p50_n8n_tick.py`: Windows 경로 별칭 픽스처를 `str(raw_v2.resolve())` 기준으로 수정 (`6d096d8`).
- `docs/P50_N8N_COMPLETION_TODO.md`: 격리 E2E 및 pin 검증 상태를 기록.

위 수정은 `6d096d8`, `dea9ecc`, `39e1e3a`에 반영·push됐고 마지막 fetch에서 local/origin SHA가 같았다. `39e1e3a`의 모든 GitHub check-runs는 완료 PASS다. 보고서·handoff에 인용된 PASS는 각각 해당 commit/test command 범위로 한정한다.

## 운영 상태 / 금지 사항

- P50 n8n workflow는 비활성 상태로 유지한다. 기존 workflow의 5분 schedule은 승인된 실행 예산으로 검증되지 않았으므로 활성화하지 않는다.
- 운영 n8n 호스트/프로필은 확정되지 않았다. 로컬 CLI 2.40.7만으로 운영 환경을 증명할 수 없다.
- 경고 채널은 사용자가 자격증명 기반 방식을 선택했지만, 구체적인 채널은 “나중에”로 보류됐다.
- 다른 엔진 소유자의 완료 및 quota group 간 사용량 독립성은 확정 전이다.
- P50의 한 페이지 live proof 이후 추가 Meta 요청은 하지 않았다. 새 Meta 실호출, 운영 DB 변경, 예약 활성화에는 별도 승인이 필요하다.
- P294 활성화는 보류하고, P50/P294 저장소·커서·수집 이력은 분리한다. Google Sheets는 n8n 핵심 및 P50 검증 뒤 단계다.
- 토큰·계정 ID·비공개 RAW 데이터는 보고서에 포함하지 않았다.

## 다음 작업

1. `39e1e3a` GitHub Actions 완료를 확인하고 실패 원인을 정확히 분리한다.
2. Project Book main index에 `39e1e3a` 및 검증/미검증 상태를 기록하고 GitHub에서 재조회한다.
3. 학원 remote n8n endpoint가 있는지 확인하거나 A안 사용 여부를 결정한다. Production profile/command allowlist/schedule 예산/경고 credential 준비 전까지 workflow 활성화 금지.
4. 최신 P50 엔진 history interface가 안정된 뒤 pin을 갱신하고 mock latest→history→next-day resume 통합 테스트를 작성한다.
5. 별도 승인된 P50 실수집과 n8n 운영 검증 후 P294 확장, 그 다음 Google Sheets 연동을 진행한다.

## GPT 다음 담당자 안내

시작 시 `AGENTS.md` → `DESIGN_FREEZE_NOTICE.md` → frozen P6 design → 최신 `HANDOFF.md` / `STATUS.md` → `docs/P6_TROUBLESHOOTING_LEDGER.md` 순서로 읽는다. 최신 브랜치/HEAD를 fetch해 확인한다. 우선 `39e1e3a`의 Actions 결과를 확인하고 Project Book 색인을 동기화한다. 현재 n8n pin `10ebaf8`과 P50 upstream `52ed20a`의 차이를 보존하고, P50 history contract를 검토하지 않은 채 pin을 이동하지 않는다. 원격 n8n endpoint 확인 전 운영 instance를 가정하지 않는다. P6 내부·운영 DB·schedule·Meta 호출은 건드리지 않는다.
