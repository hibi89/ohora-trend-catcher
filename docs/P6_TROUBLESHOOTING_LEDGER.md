# P6 트러블슈팅·기술부채·설계 판단 기록 (Living Incident Ledger)

### P6-045 — 2026-10-22 · n8n tick 처리량을 정책 상한 3페이지로 조정
- **근거:** 실제 수동 1페이지 실행과 n8n 스케줄 1페이지 실행이 성공했고, 이후 연속 스케줄에서도 추가 페이지가 영속 수집됐다. n8n이 계속 작동 중인데 tick당 1페이지만 실행하면 전체 294계정 목표가 불필요하게 늘어진다.
- **수정:** adapter 기본값과 PowerShell launcher를 tick당 최대 3페이지로 설정. n8n Schedule Trigger는 매 5분이며, P6 coordinator는 각 response 뒤 85% 사용량/쿨다운 상태를 확인하고 제한에 도달하면 즉시 중단한다. Cursor recovery는 이 자동 tick에 포함되지 않는다.
- **검증:** `tests/test_three_api_n8n_tick.py`에서 launcher 상한 3을 확인하고 targeted 76개 오프라인 테스트를 재실행. 현재 확인 시점 기준 오늘 3페이지 / 12개 신규 게시물 관측, 106 durable receipts / 424 observations / 414 unique posts. B/C는 계속 `live_pending_usage` hold, A만 이용 가능.
- **경계:** 294계정 전체 수집과 B/C 사용량 확인은 여전히 미완료. 매 tick 최대 3페이지이지 한 번에 무제한 수집이 아니다. Legacy 49건과 cursor recovery는 미수정/미실행.

### P6-044 — 2026-10-22 · n8n KST scheduler와 P6 단일 tick 연결 준비
- **변경:** `src/collection_schedule.py`를 현재 작업 브랜치에 반영하고, `raw_v2/three_api_n8n_tick.py`가 일정 판단·읽기 전용 coordinator quota/hold 확인·명시적 live gate 뒤 기존 `run_manual_pilot`을 호출하도록 연결했다. PowerShell launcher와 비활성 5분 n8n workflow export를 추가했다. n8n CLI 테스트 실행을 위해 schedule trigger와 함께 `Execute Workflow Trigger`도 둔다.
- **안전:** 각 tick은 기본 1페이지, 상한 3페이지다. Unknown Capture intent는 `manual_review`; B/C hold/cooldown은 날짜가 바뀌어도 이어진다. 토큰은 파일에서 프로세스 메모리로만 로드되고 출력/상태파일에는 기록되지 않는다. 시작 기준시각은 커밋된 Capture→RAW 성공 결과의 검증된 응답시각만 원자적으로 저장한다. cursor recovery는 자동 스케줄 대상이 아니며 별도 승인과 3페이지 상한을 유지한다.
- **검증:** schedule, n8n adapter, manual pilot, paging, recovery, credential diagnostic, handoff guard **76/76 PASS**; Python compile PASS. n8n 2.44.5 loopback runtime, import and published schedule verified. Initial CLI issues (required Execute Workflow Trigger, default disabled Execute Command node, Windows non-ASCII path, duplicate official roster candidates) were fixed without issuing Meta requests; `NODES_EXCLUDE` is restricted to exclude only Local File Trigger, enabling Execute Command. Manual CLI execution and first five-minute schedule execution both succeeded, each capped to one page. Read-only P6 verification: current-day 2 pages / 8 post observations; 105 durable receipts total / 420 observations / 413 unique posts; latest HTTP 200, safe usage 0%; A open, B/C still `live_pending_usage` held. The first-success timestamp was persisted only after the manual page's Capture→RAW commit.
- **운영 경계:** This verifies bounded live collection and the active local schedule, not full 294-account completion, B/C credential validity, or live cursor recovery. B/C holds were not reset; no automatic retries or recovery runs. Old inactive duplicate drafts may exist in the isolated n8n local DB from CLI re-import; only the final corrected workflow is published. Local n8n must remain running for scheduled ticks and currently is bound only to 127.0.0.1.
- **다음 단계:** leave local n8n running so bounded ticks continue; monitor usage gates, B/C quota verification, and completion of eligible accounts. Read-only status command from the repository root: `$py='C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'; & $py raw_v2/three_api_manual_pilot.py --workspace (Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT') --status --day (Get-Date -Format yyyy-MM-dd)`. Cursor recovery remains separate and requires operator approval. No user-facing or Git credential values were written. Current feature branch is pushed; `main` was not merged and its handoff index has not been synchronized to avoid violating the no-main-merge instruction.

> 최초 작성: 2026-10-21 · 범위: OHORA Trend Catcher p6의 확인 가능한 Git 이력, `STATUS.md`/`HANDOFF.md` 기록, 해당 대화에서 확인된 CI 로그 및 사용자 보고.
>
> **목적:** 지금 성공/실패한 실험 모두를 미래의 레거시 분석, 재설계, API 증설 및 인수인계에 재사용한다. **완전한 전수 감사 기록이 아니다.** 문서화되지 않은 과거 작업은 Git/Actions/기존 인수인계 자료를 대조하여 계속 보강한다.
>
> **위계:** [동결 공지](../DESIGN_FREEZE_NOTICE.md) 및 [최종 수집 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)가 승인된 정책의 기준이다. 이 문서는 정책·구현 완료·운영 승인을 대체하지 않는다. 설계 원문은 수정하지 않는다.
>
> **기본 구분:** 사용자 보고 / 코드로 확인 / CI로 검증 / 미검증 가설 / 후속 작업. 서로 혼동하지 말 것. CI 성공은 기록된 **특정 commit SHA**에만 유효하다.

### P6-035 — 2026-10-21 · after-only cursor 종료 오판 및 무손실 재앵커 계획

- **증상·원인:** `three_api_paging.validate_page_edge` returned `after` only when `paging.next` existed, and marked any no-next response terminal. `three_api_meta_normalizer.normalize_meta_page` retained cursor data only when `next` existed. Thus a full 4-item Business Discovery page with `paging.cursors.after` but no `paging.next` lost the cursor and was marked terminal. A full page with no cursor had also been treated as terminal despite ambiguity.
- **수정:** `after` without `next` is now `cursor_candidate`; `paging.next` is never persisted raw; short (<4) page without cursor is `terminal_verified`; full page without usable cursor is `pagination_ambiguous`; an empty page with a cursor fails closed. `raw_v2/three_api_paging_recovery.py` adds (a) read-only classification of existing terminal+after evidence with Capture/RAW/digest chain checks and cursor redaction to SHA-256, and (b) separate recovery-round re-anchoring from newest to oldest with a hard 3-page bound. It compares prior media IDs, preserves duplicate observations but dedupes the analysis list, uses a new returned cursor only after an existing-ID overlap, and marks `coverage_gap` when no overlap/continuation proof is available. It never confirms deletion, retries an existing request, writes a DB, or sends HTTP. Exact cursor error category must be provided by caller; generic HTTP 400 stays operator review.
- **수정 commit:** `7632788`.
- **49-row status:** Read-only local inspection of `%LOCALAPPDATA%\OHORA_P6_PILOT\p6_pilot_raw.sqlite` found 97 RAW receipts/contract receipts/request logs/query observations. It has no `response_evidence` or `page_evidence` table; saved receipt payload is normalized data without original paging. Therefore the 49 actual Capture/RAW/request/cursor links and their digest agreement cannot be established from the accessible pilot DB. No actual repair was applied. A synthetic 49-row terminal+after plan was produced with zero mutations and zero network requests; all 49 were marked `cursor_candidate_review_required` in simulation only.
- **Additional user requirement:** after a separately approved new round, latest→older exploration is limited to three pages. A matched old ID plus valid new cursor gives `resume_ready`; missing/deleted boundary post can be bridged by a neighboring known ID; no overlap by the cap remains `coverage_gap`. Quota 85%/cooldown, frozen roster/lane allocation, page limit 4, and no failed-request retry remain unchanged. This is an offline planner, not a production recovery runner.
- **Tests:** `tests/test_three_api_paging.py`, `tests/test_three_api_meta_normalizer.py`, `tests/test_three_api_paging_recovery.py`. Latest targeted run: 29 paging/recovery + 11 normalizer = **40 PASS**. Full `tests/run_offline.py` discovered 958 tests (before one final page-cap case) and is in progress at time of this entry; update exact final outcome before handoff. No Meta call or operating DB write.
- **Unresolved:** Actual original 49 safe response bodies, Capture hashes, RAW receipt links, and coordinator terminal rows need their correct local store(s) to be available for a read-only audit. Until then their recoverability and exact count are unverified. A new Meta recovery round requires explicit user approval.
- **Rollback:** Revert only the paging interpretation/planner/tests/docs on this feature branch. No operational artifact needs rollback; never patch existing terminal rows by inference or change immutable Capture/RAW evidence.

### P6-036 — 2026-10-21 · page-limit 회귀 fixture가 terminal 근거를 잘못 구성

- **실패 증거:** `test_anchor_on_third_page_does_not_dispatch_a_fourth_page` 최초 실행에서 expected `page_limit_reached`, actual `terminal_verified`로 1건 실패.
- **확정 원인:** 테스트의 세 번째 응답에 게시물 3개와 cursor 없는 paging을 넣었다. 이 응답은 규칙상 4개 미만·후속 표시 없음이므로 `terminal_verified`가 맞으며, 코드 결함이 아니었다.
- **수정:** fixture를 4개 게시물+유효 after cursor로 바꿔 세 페이지째에도 후속 후보가 있는 경계를 구성. 이제 검증 대상이 정확히 “세 번째 page 뒤 page4 dispatch 금지”다.
- **변경/검증:** `tests/test_three_api_paging_recovery.py`만 수정. Fixture 수정 후 최신 paging/recovery targeted 29/29 PASS, combined with normalizer 40/40 PASS. Meta/운영 DB/RAW/Capture 영향 없음.
- **수정 commit:** `7632788`.
- **롤백:** 해당 synthetic fixture만 되돌려도 운영 데이터 영향 없음. 코드의 full-page ambiguous/short-terminal 판정은 유지한다.

### B/C 인증 설정 비교 (오프라인) — 2026-10-21

- **정적 호출 방식:** P6 three_api_manual_pilot.py는 P6_META_TOKEN_B/C 및 P6_META_IG_ID_B/C를 현재 프로세스 환경에서 읽고, 페이지당 단일 Graph GET을 Bearer 헤더로 보낸다. RAW collector.py는 --env 파일에서 META_ACCESS_TOKEN·META_IG_USER_ID·선택 META_GRAPH_VERSION을 읽고, URL-encoded batch POST 본문으로 요청한다. 양쪽 Graph 버전 기본값은 v26.0; account/media 요청 필드는 완전히 같지 않다.
- **기존 실측:** P6-033의 사용자 보고에 B 완료 48페이지와 계정별 HTTP 400 세 건이 함께 있다. 오류 응답은 영속 저장되지 않았으므로 토큰 만료·호출자 불일치·권한·필드 거부·특정 대상 계정 오류 중 무엇인지 원본 코드/하위 코드로 판정할 수 없다. 2207013은 일반적인 missing-user 코드 분류를 위한 후속 처리이지, 기존 세 오류에 확인된 코드가 아니다. C 성공/실패 요청은 기록에서 확인하지 못했다.
- **오프라인 진단:** raw_v2/three_api_credential_diagnostic.py는 지정 폴더의 .env.api_b·.env.api_c만 읽어 P6 프로세스 환경과 토큰/호출자 ID/Graph 버전을 비교한다. 비밀값과 ID를 출력하지 않고 형식 및 일치 여부만 출력한다. 현재 실행 컨텍스트에서는 두 파일과 P6 B/C 환경값이 없어 비교 결과는 unavailable; 실제 토큰 유효성 및 Meta 권한은 확인하지 못했다.
- **검증 및 경계:** tests/test_three_api_credential_diagnostic.py 8/8 PASS, tests/test_p6_troubleshooting_handoff_guard.py 4/4 PASS, AST compile check PASS. 같은 P6-033 사건의 영어 요약 heading을 구분해 인덱스 검사와 일치시켰다. 합성 설정만 사용했다. Meta 0회, 기존 RAW·DB·CSV·실패 바인딩 및 quota 상태 변경 없음. 결과만으로 B/C 실수집 재개를 승인하지 않으며, 기존 실패 재전송/65분 hold 해제/85% 제한 우회 금지.
- **후속:** 진단을 실제 인증파일 디렉터리와 그 PowerShell 프로세스에서 실행해 match/mismatch를 확인한다. match도 active token·scope·app mode를 입증하지 않는다. 별도 승인된 live probe 없이 B/C Meta 권한과 실제 필드 호환성은 미확인 상태로 둔다.


### P6-034 — 2026-10-21 · 세 레인 공통 인증파일과 P6 메모리 매핑

- **요청 범위/결정:** 한 세트의 로컬 .env.api_a/b/c에서 RAW와 P6를 설정한다. 공통 파일 규격은 META_ACCESS_TOKEN, META_IG_USER_ID, META_GRAPH_VERSION=v26.0. RAW는 --env로 기존 키를 직접 사용하고, P6의 새 --credentials-dir 로더는 P6_META_TOKEN_A/B/C, P6_META_IG_ID_A/B/C, P6_META_GRAPH_VERSION로 메모리 내 매핑한다. 토큰을 코드·명령행 인자·로그로 복사하지 않는다.
- **검사:** UTF-8 키 집합, 필수값/형식, 각 토큰과 호출자 ID의 A/B/C 중복, v26.0을 확인한다. 심볼릭 링크 파일은 거부한다. 이는 값의 모양과 설정 일관성만 검사하며 토큰 유효성, 토큰-호출자 실제 연결, Meta 권한/앱 모드/쿼터를 증명하지 않는다.
- **Desktop 결과:** 요청 경로를 재검사했을 때 기존 .env.api_a/b/c가 없었다. 따라서 기존 인증정보를 백업하거나 재사용하지 않고 빈 템플릿 3개만 생성했다. 파일들은 저장소 밖에 있고 **/.env.api_* Git ignore 규칙을 추가했다.
- **검증:** test_three_api_env_loader.py 9건 중 8 PASS·심볼릭 링크 생성 제한으로 1 SKIP; manual pilot 12/12; credential diagnostic 8/8; handoff guard 4/4. 총 32 PASS, 1 SKIP. Synthetic/offline만 실행했고 Graph HTTP 0회.
- **안전/미검증:** 운영 DB·CSV·Capture·quota/bindings 변경 없음. 48페이지 B 이력은 과거 관측이며 현재 B/C 자격증명이나 재개 가능성을 증명하지 않는다. live collection, n8n 연결, main 병합 미실행.
- **롤백:** 코드/테스트/docs/ignore 변경만 이 브랜치에서 되돌릴 수 있다. Desktop 템플릿은 로컬 설정 파일로 유지하며, rollback 시에도 토큰을 Git에 추가하지 않는다. 코드·테스트 구현 commit: 1646629 (문서 동기화는 후속 commit).

### P6-033 — 2026-10-21 · Live pilot 계정별 HTTP400 후 전체 레인 정지와 Meta error_subcode 유실

- **사용자 실측:** 2026-10-21 KST 분리 파일럿에서 `durable_receipts=79`, `posts=316`, A 완료 31, B 완료 48. 일괄 `--max-pages 50` 호출 도중 27페이지 커밋 후 종료. 기존 terminal HTTP400 요청은 계정 ordinal 32(A), 151/152/201(B)의 총 4건. 해당 요청은 `failed_no_retry`, Capture/response evidence 없음. 원본 사용자 데이터는 GitHub에 전송·수정하지 않음.
- **확정된 코드 원인:** `three_api_graph_http._read_envelope`가 numeric `error_subcode`를 1,000,000까지만 허용해 Meta의 사용자 없음 하위 코드 `2207013`을 삭제했다. `MetaTransport.send`가 모든 비성공 HTTP 상태에 대해 영속 가능한 account error를 던졌고 `LiveSerialStep`은 실패할 때마다 lane을 최소 65분 영속 중단시켰다. `run_bounded_serial_steps`는 첫 예외에서 전체 batch 종료. 계정 차단을 quota 위반과 동치로 해석하는 것은 근거가 없음.
- **수정(작업 브랜치 전용):** `three_api_graph_http.py`에서 토큰과 free-form 텍스트는 계속 삭제하고 정수 에러 코드/하위 코드 범위만 1e9까지 확장(`a310065`). `three_api_transport.py`에서 **정확히** HTTP400+code110+subcode2207013인 경우를 분류하고, 신뢰되는 quota 헤더가 있으면 상향 반올림한 0~100%만 실패 메시지에 저장(`3f7e146`); 없는 값은 추측 금지. `three_api_live_serial_step.py`에서 성공 이력이 아닌 **영속 실패 증거 + 원래 binding**과 정확히 일치하는 경우만 `account_failed` 반환, 해당 요청 재호출 없음. 오류 사용량 <85%이면 lane 해제, >=85%면 quota 중단, 없으면 기존 65분 hold 유지(`da8c562`, `2473cf2`). 단순 HTTP400, rate/auth/global, outcome_unknown 또는 누락 증거는 절대 자동 건너뛰거나 quota gate 해제하지 않음. `three_api_manual_pilot.py`는 실패 건수를 보고하며 예외시 read-only 누적 RAW 상태만 표시(`7eecb57`, `029717f`).
- **테스트 작성:** `tests/test_three_api_live_serial_step.py`의 계정 오류/사용량 20, 85, 누락/미확인 HTTP400/배치 결과 mock 안전 테스트(`f5fb57b`). `tests/test_three_api_transport.py`에 subcode2207013과 x-app-usage 21.3% 반올림/헤더 부재 테스트(`e919425`). 모든 테스트는 synthetic in-memory sender, 실제 Meta 0회.
- **검증 시점:** 코드 HEAD `a310065`에서 GitHub Actions `P6 Frozen Design Guard` 및 `P6 C Contract Offline` **PASS**. `P6 Runtime Integration Offline` 및 `Three API offline tests`는 장부 갱신 직전 진행 중, 최종 판정은 run completion에서 확인. **실제 사용자의 201번 오류가 코드110/2207013이었는지는 미확인**; 기존 실패를 임의 재시도하거나 소급 수정할 근거 없음.
- **사용자 재개 지침:** 기존 65분 quota hold가 지난 뒤 명시적인 운영자 reset을 거쳐야 한다. 그 전에 `git pull --ff-only`와 오프라인 suite 통과를 확인. 기존 RAW 316건과 4개 immutable 실패 요청을 유지하고, 추가 실호출은 사용자가 별도 승인한 `--live --confirm-owner-approval` 경로에서만.
- **범위/롤백:** 지정 branch 코드·offline tests·이 장부만 변경. 동결 설계/3 API·limit4·2 rounds/운영 DB/main 미변경. 오류 분류와 bounded driver를 해당 커밋 단위로 revert 가능; 원본 RAW/DB/CSV 재작성 금지.

### English summary for P6-033 — 2026-10-21 · Meta missing-user 400 classification and serial continuation

- **Observed:** User's isolated live pilot returned HTTP 400 for account-specific jobs; immutable failed requests were retained without Capture, while A/B lanes remained held for 65 minutes. The live code could not distinguish a known missing-user error from other 400 responses and stopped the bounded serial driver on any failure.
- **Confirmed code issue:** Graph HTTP sanitization bounded numeric error subcodes to 1,000,000; Meta missing-user subcode 2207013 was discarded. Live serial step halted its lane before HTTP and only released it after successful RAW commit; a known account failure always left a hold.
- **Changes:** `a310065` preserves numeric Meta subcodes up to 1,000,000,000 in the sanitized error. `3f7e146` preserves only a conservative integer usage percentage for the exact Meta code=110/subcode=2207013. `2473cf2`/ `da8c562` handle only the exact persisted terminal account failure, never retry its request, and release the lane only with a verified usage percentage below 85; missing/invalid/high usage remains held. `7eecb57` reports the skipped count. Synthetic regression cases were added at `f5fb57b` and `e919425`.
- **Verification at code HEAD `a310065`:** P6 Frozen Design Guard and P6 C Contract Offline SUCCESS; prior runs for Three API, RAW v2, and Runtime report SUCCESS, while newer overlapping Three API and Runtime runs were CANCELLED by CI concurrency. **Do not call the entire exact-SHA suite fully verified from this mixed result.** No live Meta calls were made by the assistant; user pilot behavior after this patch remains unverified.
- **Safety:** Frozen A/B/C identities, limit=4, two rounds, 65-minute cooldown policy, immutable failed bindings, RAW, operating DB, and main branch unchanged. The exception applies only to the exact Meta account-not-found error with persisted failure evidence; all other errors remain fail-closed. No automatic cooldown reset was introduced.
- **Next:** Confirm the exact-HEAD Windows/Linux tests, then user can pull feature branch and run status diagnostics. The user's existing A/B cooldown holds require their original verified expiry and explicit reset; this patch does not clear old holds. If the account 400 error is a different Meta code/subcode, investigate instead of forcing a skip.
- **Rollback:** Revert only this branch's code/test commits. Never delete/recreate user pilot SQLite or retry immutable failed jobs.

### P6-032 — 2026-10-21 · Windows pilot status SQLite read-only handle leak

- **Observed:** At commit `db110cdd`, [Runtime #39546089085](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39546089085) Linux complete offline suite **915/915 PASS**, C Contract/Three API/Frozen Guard PASS, but Windows `offline-windows (remaining)` **756 tests / errors=1**. The `test_status_is_read_only_and_reports_lanes_without_meta` temporary-directory cleanup raised `PermissionError [WinError 32]` for `coordinator.sqlite`. Other Runtime jobs 11/11 PASS.
- **Confirmed cause:** `read_pilot_status` and the companion `read_pilot_failures` used `with sqlite3.connect(...) as connection`. Python's SQLite connection context manager controls a transaction but does **not** guarantee `close()`; a lingering handle prevents temporary SQLite deletion on Windows. This is not a Meta HTTP failure or evidence of user pilot DB corruption.
- **Fix:** Use `contextlib.closing` around both status and failure diagnostic read-only SQLite connections. Preserve `?mode=ro`, no secrets, and the existing failure/usage semantics. Existing Windows regression exercises a real temp SQLite file and cleanup.
- **Rejected:** Ignoring Windows cleanup errors, deleting/recreating a user DB, changing quota release, or retrying HTTP requests.
- **Risk/rollback:** Only diagnostic connection lifecycle; no operating DB access, no live Meta calls, no user RAW/451 changes, no scheduler/main/frozen-design edits. Revert this feature-branch diagnostic-only code change if the new exact-SHA tests show regression.
- **Verification:** New SHA CI pending. The previous `db110cdd` Runtime overall verdict is **FAIL**, despite 915 Linux PASS; do not report full CI completion from that SHA.

### P6-031 — 2026-10-21 · Terminal-bound recovery SQL / Mock fixture CI regression

- **Evidence:** `5d55a1f8` RAW v2 Actions [#39544886165](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39544886165) returned 3 failures and 3 errors, mainly six tests in `test_three_api_live_serial_step.py`. The new mandatory read `SELECT job_id,request_id FROM job_request_bindings` made `Mock.fetchall()` return non-iterable Mock and increased recorded `db.execute` calls.
- **Cause:** Incomplete mock DB contract and assertions that counted read-only SELECT as a database mutation. `hasattr(Mock, 'fetchall')` is always true for ordinary Mock objects. This was a regression of test fidelity, not evidence of a Meta transport or live SQLite corruption.
- **Repair (this branch, SHA to be verified):** Keep a mandatory real SQLite terminal-binding read and remove the ineffective `hasattr` fallback; explicitly model an empty iterable SELECT result in mocked tests; assert SQL mutations separately from reads. Add a temporary real-SQLite test with synthetic failed job 451 and a separate B-lane bound job, preserving 451 binding and stale lease, preventing request dispatch, and keeping the 85% quota hold.
- **Rejected:** Dropping the binding scan, ignoring scan exceptions, deleting/resetting failed job 451, or retriggering failed HTTP. These bypass the immutable no-resend invariant.
- **Data safety:** Offline code/test only; the real user's pilot DB, 12 posts, API credentials, quota holds and request 451 are not accessed. Real Meta HTTP 0 calls. Frozen design, scheduler and main remain unchanged.
- **Post-repair regression evidence:** `d5e51b0` [RAW v2 #39545731858](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39545731858): 915 tests run, 2 FAIL, 0 ERROR; all failures in `test_current_handoff_links_survive_chat_restart` for `HANDOFF.md` and `STATUS.md` because new first-line P6-031 summary omitted mandatory `AGENTS.md` in first 2,000 characters. No terminal-scan/451 recovery test failure reported. Fixed both top-level links, plus ledger index/details consistency, without changing collector behavior.
- **Verification:** Follow-up exact-SHA CI pending; earlier `d5e51b0` is **FAIL**, not PASS. Roll back via normal feature-branch revert of this repair only, after preserving the terminal exclusion safety invariant.

## P6-030 · 2026-10-21 · 실 Meta 파일럿 12건 보존 및 A 레인 편중 해소

- **실운영 관측 — 사용자 직접 실행·보고:** 공식 계정 CSV 294행 preflight 및 분리된 API A/B/C 자격증명 검증 성공. 명시 승인된 단일 페이지 1회, 이어서 2페이지 수동 실행에서 모두 `page_committed`. pilot SQLite 읽기 전용 결과 `posts=12`, `post_observations=12`, `account_observations=3`. 동일 날짜 coordinator에서 `jobs`: A done=3, B done=0, C done=0. 원본 SQLite/응답은 사용자 PC에 있으며 GitHub에서 직접 감사한 데이터가 아님.
- **원인:** 기존 `LiveSerialStep.step`가 각 단위 호출에 A→B→C 고정 순회하여 A의 신규 페이지1 작업이 남는 동안 B에 기회가 없었음. 동결 배정에서 B는 151~294 계정의 page1, C는 page2 선행 커서가 확보된 계정에만 dispatch 가능. 세 토큰의 등록 자체는 세 API의 실사용 증거가 아님.
- **수정 (`eae4ce77f92c21540f43f8953e6674a6ffa72914`):** coordinator에 읽기 전용 `dispatch_lane_order(now)` 추가. 기존 quota/lease/immutable binding/cursor proof를 모두 존중하는 `dispatch_ready_diagnostics`로 후보를 제한하고, `dispatch_diagnostics`의 레인별 완료 페이지 수 오름차순으로 선택. 동률은 A/B/C 순서, 작업 자체의 lane·계정·페이지 재배정 없음. 별도 스케줄러, HTTP 자동 재시도 없음.
- **오프라인 회귀:** 기존 저장소의 mock API 송신 두 호출/별도 프로세스 재개 시 예상 레인을 A→B로 변경. C는 A page1 유효 cursor가 있을 때만 후보, B가 quota로 차단되면 C/A 선택, 882 원래 jobs와 page evidence 유지 시험 추가. **CI (`eae4ce77`):** 3 API, C 계약, 동결, RAW v2 성공; Linux Runtime 성공, Windows 일부 통과/나머지 진행 중으로 최종 전체 성공은 별도 확인 필요.
- **범위/미검증:** 사용자의 실제 RAW 12건과 로컬 credential은 GitHub에 업로드하지 않음. 코드 변경은 독립 개발 브랜치에만 반영. 사용자가 `git pull --ff-only` 하기 전까지 로컬 실행기는 구버전임. B/C 실 Meta 호출, 294계정 전수·365일 백필, 운영 자동화는 아직 미검증이며 호출 시 사용자 수동 승인·감사 필요. 실험 실패/unknown 전송은 재시도하지 말 것.

## P6-029 · 2026-10-21 · 첫 실수집 파일럿 연결 및 Business Discovery ID 오판

- **문제:** 직렬 수집기가 오프라인 staging evidence만 수용해 원본 Capture/RAW가 실제 커서와 연결되지 않았음. 별개로 Business Discovery 응답의 조회 대상 `business_discovery.id`를 요청 URL의 API 호출자 IG ID와 동일해야 한다고 검사해 정상 Meta 응답을 실패로 오판할 수 있었음.
- **구현:** `raw_v2/three_api_verified_page_evidence.py`에서 원본 Capture와 영속 RAW receipt를 검증한 후 coordinator 커서를 확정. `three_api_serial_bootstrap.py`로 직렬 runner 구성, `three_api_manual_pilot.py`로 기본 offline 294계정 검증/별도 승인 1페이지(limit=4) 경로 제공. 각 SQLite DB 격리, 비밀값 비노출, no-resend crash recovery 회귀 테스트 추가.
- **검증:** `ee91ca96`에서 Linux 전체 **902/902**, Windows Runtime 11/11, 3 API/C 계약/동결 검사 모두 성공. Business Discovery ID 계약 2곳 수정 `810b6b8` + `057fc785`, 잘못된 테스트 2건을 사용자명 불일치 테스트로 전환 `a6f1c4f` + `ee91ca96`.
- **현재 상태:** 원본/RAW/커서의 **오프라인 연결 검증 완료**, 실제 Meta HTTP 호출·운영 DB 변경·main 병합·scheduler 활성화 **없음**. 최신 진단/테스트 `6318a3e`/`cc6ccdd` 이후 CI 재확인 필요. 실 Meta 수집 성공을 주장하지 않는다.
- **다음:** 공식 로컬 계정 CSV로 preflight, 사용자의 별도 명시 승인 후 1페이지 실수집과 영수증 확인, 이어서 365일 history/backfill 검증. 동일 계정 반복수집의 원본 불변/관측 이력은 지속 확인.

## 빠른 탐색 — 사건 색인

| ID | 분야 | 사건·결정 | 현재 결론 |
|---|---|---|---|
| P6-001 | 개발운영 | 병렬 작업·웹 채팅 사용량 소진·원복 어려움 | 격리 브랜치/워크트리·복원지점·인수인계 필요 |
| P6-002 | 인수인계 | 기획/계약은 Git에 있으나 STATUS/HANDOFF 미동기화 | 두 문서 동시 갱신 필수 |
| P6-003 | Meta 사용량 | 배치 수집은 빨랐지만 API #4/시간 한도 대기 병목 | 속도보다 예산/쿨다운이 제약 |
| P6-004 | Meta 데이터 | 일부 Business Discovery username/미디어 타입 조회 실패 | 누락을 성공·VIDEO를 REELS로 단정 금지 |
| P6-005 | 수집 설계 | 병렬 3레인 의존성과 단계별 커서·RAW | 데이터 안전성은 직렬에서도 필수 |
| P6-006 | 데이터 계약 | NULL/미요청/빈응답/변경없는 재관측 처리 불일치 | 보존/관측/분석 의미를 분리 |
| P6-007 | CI | Python unittest import 경로 실패 | discovery 환경 재현 및 검증 |
| P6-008 | CI·RAW | datetime 객체를 문자열 인자로 사용한 테스트 실패 | 호출 계약에 맞춘 회귀 수정 |
| P6-009 | CI | Windows 전체 테스트 180초 watchdog 초과·후속 run 취소 | shard 세분화 및 성공 SHA 명시 |
| P6-010 | 전수검증 | 294번째 계정 누락·재시작·중복 reconcile | 불완전 커버리지 완료 처리 금지 |
| P6-011 | CI 회귀 | API B 테스트 예상 RAW 4건 vs 실제 1건 | fixture/expected cardinality 일치 |
| P6-012 | 아키텍처 | 사용자 승인: 3 API 호출 직렬화 | 오프라인 수동 경로만 추가, 실 HTTP 미전환 |
| P6-013 | 변경 관리 | 잦은 커밋이 in-progress CI를 cancel | 완료 전 CI PASS 주장 금지 |
| P6-014 | 교대/지식보존 | 사건 기록은 있었으나 새 AI 작업자에 대한 명시적 의무 누락 | AGENTS + 작업 프로토콜에 기록 의무 명문화 |
| P6-015 | 직렬 회귀/CI | 재시작 테스트 fixture 기본값 오인으로 실제 1건 저장 vs 누락 기대 | 명시적 빈 fixture를 재시작에도 전달 |
| P6-016 | Catch-up 직렬 | front와 catch-up의 서로 다른 API 제한 상태 명칭으로 fallback 누락 위험 | 두 제한 상태 모두 다음 레인으로 이동 |
| P6-017 | 동시 기록 충돌 | 두 작업자가 같은 P6-016 사건 번호로 각각 기록 | 사실 통합 + 중복 감지 검사·번호 할당 규칙 |
| P6-018 | 직렬 성능 | 85% 차단 레인을 건너뛸 때마다 294계정 복구·상태 검사 반복 | 1회 재조정 후 최대 1개 합성 페이지 실행 |
| P6-019 | 직렬 안전성 | 여러 프로세스가 각자 step_serial을 실행할 수 있음 | synthetic 단계에 OS 프로세스 공통 잠금 추가 |
| P6-020 | 계약 직렬 경계 | OS 잠금만으로는 계약 기반 요청 예약과 오프라인 전송을 한 구역으로 묶지 못함 | fixture-only bridge·잠금·예약 검증 분리 |
| P6-021 | CI·인수인계 | 최신 오프라인 Runtime 실패: STATUS/HANDOFF 상단 AGENTS 링크 누락, Windows 8분 watchdog | 문서 링크 복구; Windows orchestrator 실행시간 별도 추적 |
| P6-022 | Windows CI | 294계정 roster 5개 테스트 480초 초과 | 2/2/1 샤딩, SHA 5dfae40 Runtime 12/12 PASS |
| P6-023 | 직렬·영속 통합 | fixture-only 직렬 전송 후 Capture/RAW·cursor 확정에 별도 잠금 경계가 없었음 | captured-only page commit을 동일 OS gate에 연결; 8개 격리 SQLite 통합 회귀, CI 판정 대기 |
| P6-024 | 단일 진입점 설계 | fixture 송신과 페이지·커서 확정을 서로 다른 OS 잠금 구간에서 처리 | 단일 잠금 run_fixture_page_once, 최초 송신/기존 바인딩 replay 분기 및 A/B/C 합성 회귀; 신규 CI 판정 대기 |
| P6-025 | 직렬 작업선택·사용량 | API 레인 선택과 fixture 송신·정확한 사용량 증거/복구 연결 부족 | 오프라인 job lease + A/B/C turn + pre-send durable quota hold + read-only 진단; 17개 합성 회귀, 신규 CI 판정 대기 |

| P6-026 | 영속 복구·쿼터 | 85% 차단 시 기존 Capture의 HTTP 없는 복구까지 lease 거부 | bound recovery만 quota gate 우회, 신규 dispatch는 차단; CI 미확정 |
| P6-027 | CI 회귀 | quota 중 bound-only 복구·C page2 선행 조건의 구형 테스트 기대 충돌 | 오프라인 테스트 4건 정정, 새 SHA CI 재검증 필요 |
| P6-036 | 회귀 fixture | page3의 짧은 응답을 후속 커서가 있는 page-limit 상태로 잘못 만들었음 | fixture를 4개+after로 수정, paging/recovery 29/29 PASS |
| P6-037 | 문서 인수인계 가드 | 신규 사건 상세는 추가했으나 장부 색인표에서 P6-035 누락 | 색인 보완, 후속 guard 4/4 PASS; 전체 재실행 미수행 |
| P6-038 | 전체 오프라인 재검증 | P6-037 수정 이후 전체 회귀 결과 및 cross-store 근거 현황 미갱신 | 전체 955 selected exit 0, targeted 40/40; 97 links stored digest 일치, 재계산 및 실제 repair 미실시 |
| P6-039 | Bounded pilot 중단·인수인계 | C page2가 Capture에 저장됐으나 RAW 미처리; n8n endpoint 미구성 | A durable 2 pages; C capture-only recovery later completed under P6-040; n8n exports inactive |
| P6-040 | RAW Capture replay 오판 | sqlite3.Row가 tuple과 값이 같아도 직접 비교에서 unequal, 기존 상태를 digest collision으로 거부 | compare `tuple(row)` for post/account states; C capture-only replay succeeds; full offline 956 selected exit 0 |
| P6-045 | 자동 수집 처리량 | 검증 후에도 n8n tick이 1페이지만 처리해 진행이 지연됨 | 페이지당 quota/cooldown gate를 유지하며 tick 상한 3으로 상향; 76/76 offline PASS, 실제 스케줄 tick 증거 확인 |
| P6-044 | n8n KST tick 연결 | 일정판단은 있었으나 로컬 n8n→bounded P6 runner 경로가 없었음 | read-only gate + 명시적 live 플래그 + 활성 workflow; 76/76 offline PASS, 1회 manual 및 scheduled live 성공 |
| P6-043 | 날짜 전환 시 quota hold 초기화 위험 | manual pilot 날짜별 coordinator가 새 `lanes` 행을 기본 unhalted로 생성 | 새 daily plan seed 전 최신 prior coordinator의 halt/reason/cooldown을 read-only로 승계; malformed state fail-closed; 13/13 manual-pilot tests PASS |
| P6-042 | 무효 커서 재앵커 인수인계 | 기존 3-page planner가 이미 구현돼 있었으나 신규 요구를 top-level handoff/테스트 인덱스에 명시하지 않음 | invalid cursor → resume_required → known-ID overlap → replacement cursor 오프라인 회귀 추가; production runner/live recovery 미구현 |
| P6-041 | Bounded live progress / n8n setup | A/B/C gates diverged after safe pilot; n8n CLI/runtime unavailable | bounded A batch 3/3 committed, P6 total 103/412; B/C holds unchanged; Docker service start denied |
| P6-035 | 페이징·복구 | after-only cursor가 정상 종료 처리에서 소실 | cursor candidate 보존·49건 read-only 계획·3-page ID 재앵커 |
| P6-034 | A/B/C 인증 설정 | RAW META_*와 P6 P6_META_* 형식 분리 | Desktop 3개 빈 파일 + 오프라인 메모리 매핑 로더; 실 유효성·권한은 미검증 |
| P6-033 | Meta 계정별 HTTP 400 | P6 pilot에서 B 48페이지 성공과 계정별 400 실패가 공존, 실패 응답 본문 미보존 | 기존 실패의 토큰·권한·대상 계정 원인 미확정; 후속 코드는 미래 subcode 진단만 개선 |
| P6-032 | Windows 임시 파일 핸들 | 읽기 전용 SQLite context manager의 미닫힘으로 WinError 32 | 연결 명시 종료, 새 SHA CI 검증 대기 |
| P6-031 | CI SQL Mock·인수인계 | terminal 바인딩 SELECT Mock 반환과 상단 AGENTS 링크 누락 | 코드/테스트 수정 후 RAW 915 중 문서 가드 2건 FAIL; 상단 링크 복구 및 전체 SHA CI 재확인 |
| P6-028 | CI 사전검증·인수인계 | 294계정 검증 도입 뒤 구형 무효 fixture와 문서 링크 누락으로 Linux/Windows 9건 실패 | fail-closed 검사 순서·모의 명단·인수인계 링크 복구, CI 재검증 대기 |

## 사건별 상세

### P6-028 — 공식 명단 검사 도입 뒤 모의 fixture와 인수인계 CI 회귀 (2026-10-21)

- **증상·증거:** Runtime Actions #39529205568 Linux 870개 테스트 중 failures=7/errors=2; Windows remaining 동일 9건. RAW #39529165958도 실패. 실 Meta/운영 DB 영향 없음.
- **확정 원인:** 294행 계정 key/username/ordinal 필수검사 후 기존 recovery/no-send 테스트 3건이 빈 객체 294개를 계속 사용하여 후속 게이트를 실행하지 못함. STATUS/HANDOFF 상단 AGENTS.md 링크 밀림, 프로토콜에서 채팅방 인수인계 표현 누락.
- **수정:** 264c414: A/B/C 및 offline evidence 게이트 우선 확인. 778c5b1: mock recovery/dispatch의 합성 294개를 유효하게. 1db225e: 채팅방 후임자 안내 복구. e236d86/d77bf1d: STATUS/HANDOFF 필수 링크 상단 고정. e3b04a9: RAW/cursor 확정 실패 시 lane halt 유지하는 행동 테스트 추가.
- **검증:** 최신 SHA의 전체 Linux/Windows, RAW v2, Three API, C Contract, Frozen Guard 완료 전 PASS 미주장. 코드 저장과 CI 성공은 구별.
- **보존:** 동결 3 API/limit=4/2라운드/85%/65분/원본 불변 유지. 실 Meta 호출·운영 SQLite·스케줄러·main 병합 없음.
- **남은 리스크:** 운영 Evidence/RAW 저장 포트와 365일 backfill 미연결. fail-closed staging 차단 유지.
- **롤백:** 기능 브랜치에서 위 변경만 개별 revert, 운영 CSV/RAW/DB를 건드리지 않음.

### P6-027 — P6-026 쿼터 복구 정책 변경 뒤 구형 테스트 기대값 충돌 (2026-10-21)

- **실패 증거:** SHA `10e4b6e8ae517302d3620e3ae4f8006eb39a27dd` [RAW v2 #39526851689](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39526851689)와 [Runtime #39526867524](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39526867524) Linux/Windows에서 아래 4개 주장 중복 실패. RAW 4 FAIL; Runtime Linux 4 FAIL / Windows 일부 실패. 특정 workflow의 pending/cancelled 상태와 구분.
- **기대/실제 ①:** `test_recovery_ready_diagnostics_are_read_only_and_gate_aware`, `test_recovery_respects_lane_halt_and_verified_cooldown`는 quota 85% 뒤 bound recovery = 0/None 기대했으나 현재 `P6-026` 구현은 1/leased. 정책상 **Capture-only 로컬 복구 허용·신규 HTTP dispatch 금지**가 올바름.
- **기대/실제 ②:** `test_transport_unknown_outcome_never_clears_durable_usage_hold`는 bound 후보 수 0 기대, 실제 1. 진단값은 **영속 binding과 lease 자격의 수**이지 durable Capture 존재 또는 RAW replay 성공의 증거가 아님. Missing capture는 별도 fail-closed로 남기며 자동 재전송하지 않음.
- **기대/실제 ③:** `test_quota_halt_on_one_lane_preserves_other_lanes`에서 B 사용량 차단 후 A와 C가 동시에 first-page lease를 받는다고 기대했으나 실제 C=None. 동결 레인 배정상 C는 page2 담당이므로 A/page1 receipt·cursor 없이는 정상적으로 대기해야 함. A lane 신규 dispatch 가능 여부와 C predecessor readiness를 분리 검증.
- **선택 및 대안:** (A) 정책에 맞춘 네 테스트 정정 **채택**. (B) P6-026 quota recovery 코드를 구형 기대에 맞게 롤백하면 동결 설계의 HTTP 없는 복구와 충돌하므로 **기각**. 설계·API 호출·운영 DB·스케줄러 변경 없음.
- **수정 범위:** `tests/test_three_api_job_binding.py`, `tests/test_three_api_serial_selection.py`, `tests/test_three_api_live_serial_step.py`. 복구 시 신규 송신 차단과 C 선행 커서 검증을 유지.
- **검증 계획:** GitHub Actions 최신 HEAD의 RAW v2, Runtime Linux/Windows 12개 shard, Three API, C Contract, Frozen Guard를 각각 판정. **기록 시점 CI 미검증**; 코드 저장을 성공으로 대체하지 않음.
- **보존·복구:** live Meta 0회, 운영 `collection.sqlite`·CSV·원본 미변경. 실패 시 GitHub 기준 부모 SHA `10e4b6e`에서 이 테스트/문서 변경만 revert. 운영 데이터 롤백 불필요.
- **재발 방지:** quota halt 이후 dispatch/recovery 기대를 분리하고, 294계정 page2/3 lease 검증 시 upstream page receipt·cursor를 먼저 준비.


### P6-001 — 병렬 AI 작업 후 한도 소진 및 원복 위험 (2026-10-19)

- **징후:** 사용자 보고에 따르면 작업반장+A/B/C 병렬 진행 중 웹 ChatGPT 사용량 제한으로 다수 작업이 중단되었고, 통합 과정의 상태와 원복 위치 파악이 어려웠음.
- **원인 구분:** 여러 작업 세션이 서로 다른 상태를 추적하고 있었음은 보고된 사실. 개별 Git 파일 충돌의 정확한 원인은 여기서 전부 재현하지 않았음.
- **당시 교훈:** 작업자별 branch 또는 Git worktree 격리, 공통 기준 SHA/상태 저장, 통합 전 CI와 복원 tag·SHA 확보, 가역적 통합, 메인 원본 미변경. `main` 전체를 무조건 지우거나 브랜치 일괄 삭제하지 말 것.
- **증거:** 기존 [HANDOFF](../HANDOFF.md), [STATUS](../STATUS.md) 및 Git 이력. **사용자 보고 기반** 사건으로 인과의 상세는 미검증.
- **재발 방지:** 단일 담당이면 불필요한 동시 작업을 피하고, 병렬 작업 시 매 작업자 branch·검수 책임·충돌 복구 순서를 명시.

### P6-002 — 새 채팅방이 최종 계획을 복원하지 못함 (2026-10-19)

- **징후:** 3 API 설계/계약 내용은 별도 파일로 커밋됐으나 `HANDOFF.md`·`STATUS.md` 상단이 따라가지 못해 새 작업자가 최신 계획을 놓칠 위험.
- **근거:** [AGENTS.md 11~14항](../AGENTS.md)에서 이 실패를 명시적으로 기록함.
- **조치:** 중요 변경 시 두 문서를 같은 세션에 갱신하고 상단에 구현/검증/미구현/금지 범위를 구분.
- **재발 방지:** 전문 기록은 이 ledger에, **현재 행동에 필요한 짧은 색인은 STATUS/HANDOFF 상단**에 남김. 메인 병합 없이 feature 브랜치만 갱신했으면 그 사실을 표시.

### P6-003 — Meta 배치가 빨라도 쿼터·1시간 대기가 병목 (2026-10-15~21)

- **징후:** 사용자 실험에서 Meta Batch API는 빠르게 동작했으나 시간 기반 제한 및 Meta error #4 경험. 게시물 20건→5건→최종 `limit=4`로 조정해 API 사용량을 관리.
- **확인된 정책:** 294계정, A/B/C 호출 주체 3개, `limit=4`, 2 daily rounds, 계정당 최대 3페이지, usage 85% 중단과 65분 최소 제약. [동결 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md).
- **아직 모르는 것:** 인증 주체별 쿼터가 실제로 독립적인지, 운영 계정 전수 조회 평균시간, 65분 뒤 자동 재개 가능 여부. 속도 산술을 실운영 보장으로 취급하지 않음.
- **교훈:** HTTP 병렬성으로 해결할 문제가 아닐 수 있다. 기록해야 할 지표는 API별 사용량, ready_at, 성공/실패, 신규/중복, cursor 연속성, 실제 wall time. 반복 #4 즉시 재시도 금지.
- **후속:** 유효한 quota-group 정책을 확인하기 전에는 독립 API 쿼터를 가정해 전역 중단을 무단 완화하지 말 것.

### P6-004 — Business Discovery 조회·미디어 유형 경계 (2026-10-20)

- **징후:** 사용자 보고상 특정 사용자명 조회에서 Meta OAuthException code 110, subcode 2207013 (`Invalid user id`) 발생; 일반 VIDEO 게시물 테스트 표본이 충분하지 않고 릴스와 일반 게시물의 응답 가능성 차이가 있었음.
- **조치/판단:** 실패를 게시물이 없다는 뜻으로 확정하지 않는다. IMAGE/CAROUSEL/VIDEO/REELS 유형, 미디어 URL/썸네일/게시물 permalink, reels view count와 미반환 사유를 구별해 데이터 계약에 보존.
- **증거:** 사용자 보고 및 [동결 설계의 미디어 요구](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md). 전체 유형에 대한 실제 API 성공률 검증은 **미완료**.
- **재발 방지:** `VIDEO`라고 자동 `REELS` 판정하지 말고, 조회 실패·필드 미반환은 NULL/상태로 남긴다. 미디어 다운로드/프레임 분석은 별도 n8n 단계.

### P6-005 — 세 레인·커서·원본 보존 경계 (2026-10-20)

- **징후:** page2/3은 직전 페이지의 durable receipt와 검증된 cursor가 있어야 하며, 수집 후 원본/체크포인트와 별도 SQLite 커밋을 조정해야 한다.
- **결정:** [최종 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)의 영속 단계 `JOB_BOUND → INTENT_DURABLE → RESPONSE_DURABLE → PROJECTED_WITH_RECEIPT → PAGE_COMMITTED → ACKED` 유지.
- **근본 제약:** transport를 직렬화해도 **서로 다른 DB 트랜잭션·재시작 복구·중복 방지·커서 무결성 문제는 남는다.**
- **재발 방지:** 원본/receipt 우선, HTTP 없이 replay, unknown outcome 자동 재전송 금지. 증거 없는 성공·coverage 주장 금지.

### P6-006 — 데이터 타입과 관측 의미의 불일치 (2026-10-20)

- **징후:** 설계 검토 당시 `like_count=None`이 계약상 NULL 가능인데 일부 normalizer에서 잘못된 engagement 수치로 거부하는 불일치, followers_count/media_count 요청/수집의 불일치, 미디어 순서 변화 충돌 등이 확인됨.
- **증거:** [동결 설계 3절](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md#3-목표-정합성과-실제-차이) — 당시 정적 검토·합성 재현 기준.
- **안전 기준:** `0`/`NULL`/미반환/미요청/실패 구별; 게시시각과 관측시각 별도; 동일 조회 다시 성공해도 별도 관측 기록; URL만 변경됐다고 새로운 디자인 아님.
- **검증 상태:** **당시 이슈 기록**이며 현재 모든 계층에서 수정·운영 검증됐다는 선언이 아님. 실제 소스·테스트별 확인 필요.

### P6-007 — unittest discovery import 실패 (2026-10-20)

- **징후:** RAW offline GitHub Actions `39399759236`에서 `ModuleNotFoundError` 발생.
- **원인/수정:** 테스트 discovery 실행 환경 import 구성 오류. 기록상 수정 커밋 `57b150d`, `6d80902`.
- **증거:** [STATUS.md 해당 CI 항목](../STATUS.md)와 run `39399759236`.
- **재발 방지:** 단일 파일 직접 실행뿐 아니라 CI와 동일한 unittest discovery 경로로 실행; import 경로를 test-local 우연성에 의존하지 않도록 관리.

### P6-008 — SQLite RAW mirror stage_page 타입 불일치 (2026-10-20)

- **징후:** Three API CI `39401729620`, `39401748522` 실패.
- **확인된 원인:** 테스트가 `raw_mirror.stage_page`에 `datetime` 객체를 전달했으나 해당 인터페이스는 ISO 날짜 문자열을 요구.
- **조치:** 계약에 맞춘 수정 `a041893`; 이후 반복 reconcile 관측 중복 방지 `aa16653`, stage fixture 상충 재등록 차단 `17774e9` 회귀 기록.
- **증거:** [STATUS.md](../STATUS.md) 해당 2026-10-20 CI 복구 항목.
- **교훈:** 테스트 fixture도 production API/DB 경계와 동일한 인자 타입 계약을 지켜야 한다. 실패한 CI만으로 수집 로직에 버그가 있다고 단정하지 않음.

### P6-009 — Windows shard 실행시간 / CI 취소 (2026-10-20~21)

- **징후:** Runtime Integration의 Windows 테스트 그룹이 기존 watchdog **180초 초과**로 실패한 기록. 이후 `projection` 분리 및 Windows matrix 6그룹, watchdog 480초로 조정.
- **관련 커밋:** `0d518ab` (projection 분리), `63331ed` (shard 회귀), `f72661c` (Windows matrix/watchdog). 과거 일부 CI는 후속 커밋의 concurrency 설정으로 `cancelled`됨.
- **교훈:** `cancelled`는 pass도 코드 오류의 확정 증거도 아니다. Linux 통과만으로 Windows 통과를 주장하지 말고, 비싼 294계정 통합 테스트는 shard 시간에 미치는 영향을 계산한다.
- **후속:** 같은 SHA의 모든 required workflow + Linux/Windows jobs 최종 성공 확인. 과도한 문서 전용 커밋도 최신 CI를 취소시킬 수 있으므로 작업 배치 후 검증.

### P6-010 — 294계정 누락과 재시작·RAW 중복 회귀 (2026-10-20~21)

- **징후:** 마지막 294번째 계정 또는 fixture가 누락되었을 때, 293개 수집을 294개 완료로 표시하면 거짓 coverage 발생. 반복 reconcile이 RAW 행을 늘리는지도 확인해야 함.
- **관련 테스트/커밋:** `tests/test_three_api_full_roster_batch.py`; `143f31a` (반복 reconcile 중복 방지), `a4f106a` (재시작 누락 계정), `1daad86` (누락 fixture 담당 레인 교정).
- **상태:** 회귀 테스트를 추가·교정했지만 **각 테스트의 현 시점 최종 CI 통과 여부는 정확한 commit/run을 다시 확인해야 함**.
- **교훈:** 전체 계정 커버리지·RAW 게시물 수·원본 receipt 수를 별도 확인하고 불완전한 상태에서 catch-up/history 성공을 표시하지 말 것.

### P6-011 — API B 테스트 가정 오류: RAW 4건 예상 vs 실제 1건 (2026-10-21)

- **징후:** Three API offline CI `39490317875`에서 `test_quota_stop_does_not_prevent_other_lane_commits` 실패. `AssertionError: 1 != 4` — 반환 RAW 1건, 테스트는 4건 기대.
- **근본 원인:** 해당 B lane에 부여한 synthetic fixture는 게시물 **1건**인데 테스트 기대값이 4건이었음. 엔진 자체의 4건 누락으로 판정할 근거 없음.
- **수정 이력:** `6e6f5cc` 테스트 추가 → `adee960` B lane fixture 보완 → `6e1c6e1` expected cardinality를 1로 수정.
- **검증:** `6e1c6e1` 기준 Three API offline run `39490490719` **SUCCESS**; 동 커밋의 Runtime은 `cancelled`. 이전 실패와 이후 성공을 혼동하지 않음.
- **교훈:** `limit=4`는 **최대 요청 개수**이지 API가 항상 정확히 네 개를 반환한다는 뜻이 아니다. fixture 반환 개수와 기대 관측 개수를 일치시킨다.

### P6-012 — 사용자 승인에 따른 직렬 실행 전환 (2026-10-21)

- **변경 동기:** 사용자 실측 판단상 배치 호출의 처리시간은 충분하고, **API 사용량 소진 후 한 시간대 대기**가 병목. API 추가 때 동시성 관리 비용도 피하고자 요청 순서를 직렬화하도록 명시 승인.
- **범위:** A/B/C 3개 호출 주체, 기존 할당, `limit=4`, 2라운드, RAW/커서/복구 안전성을 유지. 동시 발송을 제거하되 불확실한 응답 재전송·쿼터 정책은 완화하지 않음.
- **구현:** `raw_v2/three_api_daily_orchestrator.py`의 **synthetic manual** `step_serial(now=...)` (`ddb7abb`); 테스트 `1d98e02`, `STATUS.md`/`HANDOFF.md` 동기화 `d48fd7d`, `5bcff81`.
- **현재 제한:** **실 Meta HTTP 호출 경로 또는 운영 스케줄러가 직렬화된 것은 아님.** `step_serial`은 한 호출에서 최대 한 합성 페이지를 처리하며, 여러 프로세스 사이의 실제 HTTP 동시 실행을 막는 전역 잠금은 아직 없다. `front_fixture_missing`/복구 실패는 교차 레인 재시도의 근거가 아니므로 결과를 노출·중단. 따라서 실운영 연결 전에는 실제 호출 경로의 동시 실행 방지와 데이터/쿼터 증거를 별도로 검증해야 함.
- **2026-10-21 후속 설계 결정:** A만 먼저 반복 처리하면 B/C가 불필요하게 지연되므로 **성공한 synthetic 페이지마다 A→B→C 순환**하고 마지막 성공 레인을 격리 source SQLite의 `offline_serial_turn`에 기록해 재시작 후에도 순서를 이어감 (`db32b06`). 대안은 항상 A 우선(간단하지만 레인 편중) 및 프로세스별 메모리 커서(재시작 시 우선순위 초기화)였으며 모두 채택하지 않음. 기존 계정/페이지 A/B/C **고정 배정과 커서 선행 증거는 변경하지 않음**. `front_lane_unavailable` 및 catch-up의 `lane_blocked`는 다음 레인으로 넘어가되, 실패한 fixture나 복구는 사용자 검토용으로 정지. 신규 회귀 `1be6e01`, `3b039fb`(Catch-up). **최신 SHA 전체 CI 결과 확인 전이며 라이브 검증 아님.**
- **검증:** `5bcff81` CI 기준 Frozen Design Guard / C Contract **SUCCESS**, Three API / Runtime 확인 당시 `in_progress`. 이후 완료 결과 별도 확인 필요.
- **되돌리기:** 이력 보존 상태에서 feature branch의 `step_serial` 코드·테스트만 역커밋 가능. **사용자 RAW나 운영 DB를 원복 목적으로 덮어쓰지 않는다.**

### P6-013 — 지속적인 작은 커밋으로 CI 실행 취소 (2026-10-20~21)

- **징후:** 신규 테스트/문서 커밋이 잇달아 push되면서 이전 `queued/in_progress` CI 실행이 concurrency 정책상 `cancelled`되고 최종 승인을 위한 녹색 SHA를 확보하기 어려움.
- **근거:** 기존 [STATUS.md](../STATUS.md) 및 GitHub Actions에서 `143f31a`/`a4f106a` 등 후속 실행 취소 관측.
- **대응:** 변경사항을 의미 있는 묶음으로 커밋한 다음 CI를 완료까지 관찰. 문서 상태에 완료/실패/취소/진행 중을 따로 기록. 전체 오프라인 검증이 끝나기 전 실제 Meta API 운영 준비 완료로 표현하지 않음.

### P6-014 — 새 채팅방 작업자에게 기록 의무가 전파되지 않음 (2026-10-21)

- **징후:** p6 `STATUS.md`/`HANDOFF.md`와 새 ledger는 존재했지만, **`AGENTS.md`와 `docs/AI_WORK_UNIT_PROTOCOL.md`에 모든 후속 AI가 사건/설계결정을 갱신해야 한다는 명령이 명시되지 않았음**. 새 채팅방이 문서를 찾아 읽더라도 기록을 생략할 여지가 있음.
- **추가 발견:** `docs/RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md`라는 **RAW v2.1의 기존 상세 트러블슈팅 문서가 이미 존재**함. 새 p6 ledger는 그 기록을 대체하거나 '프로젝트 최초 트러블슈팅'이라고 주장해서는 안 됨.
- **수정:** [AGENTS 의무 규칙](../AGENTS.md) 추가 (`f10fe06`), [N-unit 교대 절차](AI_WORK_UNIT_PROTOCOL.md) 추가 (`765a6ef`); 본 장부에 OPEN·해결·CI 구분 및 신입 작업자 필독 체인을 유지.
- **검증:** GitHub 파일 업데이트 결과 커밋 확인; 실제 새 채팅방/타 에이전트가 수행하는지는 **미검증**. 규칙은 사회적·절차적 구속이며 GitHub가 기록 누락을 자동 감지·강제하는 것은 아님.
- **재발 방지:** 시작 전 `AGENTS → 현재 브랜치 HANDOFF/STATUS → p6 ledger → RAW v2 chronology` 조회, 사건 발생 때 본 장부 갱신, 세션 종료 전 저장소 재조회; 기록 실패 시 미반영 명시. 미래에 CI/PR로 기록 검증을 강화하려면 별도 신뢰성 검토가 필요함.
- **롤백:** 기능·운영 데이터와 분리된 문서 변경이므로 승인된 경우 해당 문서 커밋만 revert. Frozen 설계와 기존 사고 이력은 보존.

### P6-015 — 직렬 재시작 회귀 테스트의 fixture 경계 오류 (2026-10-21)

- **증상/CI 증거:** `1be6e01`의 Three API workflow run `39493808092`, test `test_serial_missing_fixture_does_not_advance_durable_turn` 한 건 **FAIL**. 기대 `(A, front_fixture_missing)`, 실제 `(A, front_committed)`.
- **확인된 원인:** 재시작 fixture 전달에서 `self.create()`를 호출해 테스트 기본 사전(계정 1 첫 페이지 데이터 포함)이 적용됐다. 첫 실행의 의도적인 빈 fixture(`front={}`)가 재시작에는 전달되지 않았음. 실제 수집 엔진의 잘못된 API 호출/데이터 누락으로 볼 근거 없음.
- **수정/예방:** 재시작 호출을 `self.create(front={})`로 명시 (`cd6a1ff`). 동일 증상 재현 때 코드 수정 전에 fixture·mock·expected cardinality부터 확인하고, 런타임 버그와 분리할 것.
- **데이터 영향:** 합성 독립 SQLite만 사용; 실 Meta 호출·운영 RAW 변경 없음.
- **검증 상태:** 위 SHA의 이전 CI 실패는 확정. 수정 뒤 신규 CI는 이전 실행 일부가 `cancelled`됐으며 최종 실행 결과는 별도 확인 필요. `cancelled`를 PASS로 표기하지 않음.
- **롤백:** 신규 synthetic 회귀 테스트 수정을 해당 커밋 단위로 revert 가능. 사용자 DB/CSV/원본은 절대 덮어쓰지 않는다.

### P6-016 — 직렬 Catch-up에서 차단된 레인 이후 B/C 가용 API로 이어지지 않음 (2026-10-21)

- **증상/발견:** `OfflineDailyOrchestrator.step_serial()`은 front 단계의 `front_lane_unavailable`만 다음 레인으로 건너뛰도록 구현되어 있었다. 반면 실제 Catch-up runner는 동일한 quota/cooldown 조건을 `lane_blocked`로 반환한다. 따라서 Catch-up 단계에서 A가 85% 사용량으로 중단돼도 B/C의 가용 여부를 시도하지 않는 **오프라인 제어흐름 결함을 정적 검토로 발견**했다. 실 Meta 장애를 관측했다는 뜻이 아니다.
- **확정 원인:** 서로 다른 두 단계의 레인 불가 상태 이름을 직렬 wrapper가 하나로 통일하지 않음. 할당 계정 변경이나 cursor 오류가 아닌 상태 매핑 누락.
- **수정:** `lane_blocked`도 다음 레인으로 진행하도록 제한적인 상태 분기를 확장 (`1e93561`). `front_fixture_missing`·`recovery_blocked`·`payload_projection_blocked`는 자동 우회하지 않는다. 기존 A/B/C 작업 배정, `limit=4`, 원본/증거·커서 불변.
- **회귀 증거:** [`tests/test_three_api_serial_catchup.py`](../tests/test_three_api_serial_catchup.py) (`3b039fb`)에서 A만 차단→B 적용, A/B 차단→C 적용, 3레인 모두 차단→RAW·catchup 증거 0 유지의 3개 합성 시나리오를 작성. **작성됨 ≠ CI 통과**. 최종 해당 SHA의 CI/Windows 완료 상태는 별도 확인.
- **데이터 영향:** 독립 synthetic SQLite만, HTTP/Meta·운영 원본·main 병합 없음. 운영 스케줄러에는 아직 연결하지 않음.
- **추가 관련 검증:** front/RAW 재시작·잘못된 B fixture 차단 회귀는 `e1ad01f`에 별도로 기록되어 있다. 이 사건의 직접적인 Catch-up fallback 검증과 구분한다.
- **미해결/예방:** 레인 사용량 독립성 미검증, 현재 오프라인 직렬 경로는 블록된 각 레인을 다시 호출하며 reconcile 비용이 발생한다. 추후 최적화 시 성공한 RAW 영수증/커서 순서 훼손 방지. 기술적 기록 발견성 보강 테스트 [`test_p6_troubleshooting_handoff_guard.py`](../tests/test_p6_troubleshooting_handoff_guard.py) (`b83d66c` 및 정규식 수정 `cfcc663`) 추가.
- **안전한 롤백:** 기능 `1e93561`과 합성 회귀 `3b039fb`를 단독 revert할 수 있으나 임의 운영 DB 되돌리기 금지. 원인과 해결 기록은 유지.

### P6-017 — 여러 AI 작업자가 P6-016 번호를 동시에 사용한 인수인계 충돌 (2026-10-21)

- **증상:** 같은 개발 브랜치에 병행 작업이 진행되는 동안 서로 다른 작업자가 Catch-up fallback 문제를 각각 `P6-016`으로 기록해, 사건 색인과 상세 항목에 **동일 ID가 2회 존재**함. 사건 장부의 검색·번호 참조·이후 레거시 추적을 왜곡할 수 있음.
- **확정 원인:** 사건 번호를 할당할 때 동시 작성자 사이에 원자적 ID 예약이 없었고, 최종 문서 기준의 유일성 검사를 커밋 전에 하지 않음. 두 항목은 같은 원인/수정 흐름을 설명하는 중복 서술이었음.
- **조치:** 기술적 원인·회귀는 기존 `P6-016`으로 통합. 중복 상세 내용에 있던 관련 front/RAW 재시작 테스트 `e1ad01f`의 참조도 P6-016에 보존. 두 번째 사건 번호 `P6-017`은 **이 문서 충돌 자체**를 기록하는 데 사용해 구별한다. 관련 방지 테스트: `tests/test_p6_troubleshooting_handoff_guard.py` (`b83d66c`, `cfcc663`)의 색인/상세 중복 검사.
- **검증:** 동일 브랜치의 문서를 재조회해 001~017 ID 중복 0·색인/상세 일치 여부를 확인해야 한다. Python CI/Windows 전체 완료 여부는 별도 확인 전이며, 녹색 성공으로 간주하지 않는다.
- **후속 규칙:** 사건 신규 생성 직전 항상 최신 feature ledger를 GitHub에서 다시 읽고 번호 중복을 확인한다. 저장 충돌이 나면 **새로운 번호를 임의 재사용하지 말고** 최신본과 병합해 재시도한다. 파일별 SHA 조건 확인만으로 의미상 중복을 막을 수는 없으므로 최종 장부의 유일성 검사도 실행한다.
- **영향/롤백:** 지침·문서상의 번호 충돌. Meta HTTP/운영 RAW·DB 변경 없음. 과거 실패·해결의 사실 기록을 삭제하지 않고 하나의 사건에 보존한다.

### P6-018 — 직렬 fallback 중 중복 294계정 reconcile 처리 (2026-10-21)

- **발견:** 기존 `step_serial()`이 `A→B→C` 상태 조회마다 `step(lane)`을 호출해, 앞선 API가 85% usage 또는 65분 쿨다운으로 unavailable일 때도 매번 전체 원본/ledger/RAW 재조정과 294계정 상태 검사를 다시 수행함. 추론된 오프라인 처리 병목이며 실 Meta 지연 시간의 측정값은 아님.
- **원인:** `step()`이 개별 안전 함수로 자체 reconcile을 필수 실행하는데 직렬 wrapper가 이를 세 번 호출했기 때문.
- **개선:** 기존 `step()`의 외부 안전 계약은 유지하면서, 내부의 단계 선택을 `_step_with_phases()`로 추출(`40c1e89`). 직렬 `step_serial()`은 최초 **1회** `reconcile()` 수행 후 해당 일관된 action snapshot으로 순서대로 레인 하나만 실행(`3850f3f`). 실패/원본 불일치는 다음 레인으로 넘기지 않음.
- **회귀:** 합성 Catch-up B/C quota fallback에서 reconcile 호출 수=1과 복구 exception 때 RAW/커서 무변경 검사 `9d4d7f6` (`tests/test_three_api_serial_catchup.py`). CI PASS는 **해당 SHA 실행 결과 확인 전까지 미확정**.
- **경계/롤백:** Meta API·사용자 DB/CSV 원본·동결 설계/main 미변경. 위험하면 `40c1e89`, `3850f3f`를 feature branch에서 revert하되 기존 원본 파일은 건드리지 않음.

### P6-019 — 직렬 wrapper만으로는 프로세스 간 동시 실행을 막지 못함 (2026-10-21)

- **발견:** `step_serial()`의 순차 `for`문은 **하나의 Python 호출** 안에서만 동시 실행을 제거한다. 독립 채팅방/작업자가 각각 별도 프로세스로 같은 SQLite를 열고 tick을 실행하면 상호간 직렬성이 보장되지 않는 아키텍처 위험이 있었다. 실제 다중 프로세스 데이터 손상은 관측하지 않았음.
- **대안 판단:** `BEGIN IMMEDIATE`를 전체 RAW·control 다중 DB 작업 동안 점유하면 내부 nested SQLite transaction 및 단계별 무결성 복구와 충돌하기 쉽다. 원자적 파일 생성 플래그는 crash 후 남은 파일의 안전한 해제 절차가 어렵다. **OS-owned 파일 잠금**은 프로세스 종료 시 자동 해제되며 DB별 전역 잠금 경계를 제공하므로 채택.
- **구현:** `raw_v2/three_api_serial_lock.py` (`f5bc1b3`)에 POSIX `flock`/Windows `msvcrt.locking` 비차단 lock 추가. 같은 synthetic coordinator DB의 `step_serial()` **및** 기존 `step()`를 공통 lock decorator로 보호(`5defcb4`); 이미 실행 중이면 `serial_busy`, 새 RAW 저장/커서 승격·공정성 turn 갱신 없음. 잠금 파일 자체는 남을 수 있지만 잠금 권한은 OS가 소멸시키므로 수동 stale-file 삭제 금지.
- **회귀:** `tests/test_three_api_serial_lock.py` (`5e11b76`, `a9ce108`)에서 동일 DB 중복 lock 차단, 예외 후 lock 해제, 다른 DB 독립성, 잠금 중 수집/RAW 불변, 별도 **자식 프로세스 lock 차단 및 해제 뒤 성공**을 테스트하도록 작성. 프로세스가 lock을 보유한 채 `os._exit(17)`로 비정상 종료해 Python `finally`가 생략돼도 **운영체제 잠금이 자동 해제**되는지 확인하는 회귀(`22a9773`)를 추가했다.
- **한계:** 이 잠금은 **새 synthetic 오케스트레이터의 두 entrypoint에만 적용**한다. 다른 legacy/운영 수집기나 향후 전송 adapter가 같은 gate를 사용한다고 가정하면 안 된다. 실제 Meta 3 API 호출 런타임 직렬화는 여전히 별도 작업/검증이 필요하다. 실 API별 quota 격리는 미검증.
- **검증/롤백:** 현재 테스트/가드 코드 GitHub 커밋 반영, 오프라인 CI 최종 판정 대기. 필요 시 feature commit만 되돌리고 운영 데이터/동결 설계/main 병합 절대 변경 금지.

> **P6-019 추가 회귀 (2026-10-21):** 동일한 control DB 경로를 `nested/../source.sqlite`처럼 별칭으로 표현해도 잠금을 우회하지 못하는지, lock 파일이 디스크에 남아도 다음 실행이 정상 재획득하는지 검사 2건을 `tests/test_three_api_serial_lock.py`에 추가(`67e5aa3`). 코드로 확인한 경로 정규화 `Path.resolve()` 동작을 대상으로 하며, **새 SHA CI 검증 전**이다. 잠금 파일을 지우는 stale-file 복구는 의도적으로 수행하지 않는다.

> **P6-019 CI 회귀 연결 (2026-10-21):** 이전 `p6-runtime-integration-offline.yml`의 push/PR path 필터가 새 `three_api_serial_lock.py` 및 두 serial 회귀 테스트 파일을 누락했다. 이 때문에 해당 파일만 수정할 때 Runtime CI가 시작되지 않을 수 있었다. 두 path 필터에 3개 파일 추가(`c982a47`), Linux `py_compile`에 lock/serial tests 추가(`6300ee0`), 새 두 모듈을 Windows `orchestrator` shard로 명시 배정(`d81b09e`). **workflow 변경 자체가 테스트 PASS를 의미하지 않는다.** 소켓 차단 전체 `tests/run_offline.py`는 이미 discovery로 새 테스트를 찾으며, SHA별 Actions 최종 결과는 별도 확인할 것. 이전 도구 안전 검사 차단은 변경 내용을 축소·재검토한 뒤 이 단계에서 해결됐다.

### P6-020 — 오프라인 직렬 계약 경계와 실제 HTTP 송신 분리 (2026-10-21)

- **발견/위험:** 기존 `step_serial()`은 synthetic page 수집 전용이고, `ThreeApiContractExecutor`·`JobRequestBindingStore`의 계약 기반 요청 예약과 공통 OS lock 경계가 연결되지 않았다. 서로 다른 진입점의 직렬성을 혼동하면 Meta 송신 중복을 허용할 수 있다. 실제 운영 중복 송신을 관측한 것은 아님.
- **설계 선택:** `raw_v2/three_api_serial_contract_bridge.py` (`fabdb8a`)에 격리 control DB lock을 획득한 상태에서 **기존 영속 request binding → contract executor** 순서로 단일 합성 요청을 넘기는 `OfflineSerialContractBridge` 추가. 임의의 `offline_fixture_only=True` 마커만으로 우회할 위험을 줄이기 위해 **정확한 `OfflineFixtureTransport` 타입만** 허용하도록 강화(`9a3e004`). 외부 네트워크 sender/토큰/스케줄러 생성 기능 없음.
- **거부 정책:** 시간대 없는 `now`, A/B/C 레인 불일치, history 전송, 다른 coordinator의 binding, 이미 다른 프로세스가 lock을 보유한 경우 실행 금지. lock 경합 시 `serial_busy` 반환. 불명확한 HTTP 결과 자동 재송신 없음.
- **회귀:** `tests/test_three_api_serial_contract_bridge.py` (`4400798`, `bced22b`)에 합성 단일 요청, 잠금 경합, 비fixture 전송 거부, `fetch` 속임수 거부, 레인/History 거부, 시간대·coordinator 검증 7건. Runtime CI path·Linux compile(`067519e`)과 Windows orchestrator shard(`075673a`) 연결. **새 SHA의 CI 최종 통과 여부는 확인 전까지 미확정.**
- **미해결:** 이 경로는 mock binding과 in-memory fixture 기반의 오프라인 안전 어댑터일 뿐, 실제 `JobRequestBindingStore`→`CaptureJournal`→`ContractRawWriter`의 통합 end-to-end 검증 및 실제 Meta `GraphHTTPClient` 직렬 연결이 아니다. quota 사용량 독립성, 장애 후 복구, 294계정 전수 검증은 별도. 운영 DB·main·동결 원문 불변.
- **예방/롤백:** 실제 live entrypoint 승인 전에 OS lock 경계·영속 예약·RAW receipt·HTTP 불확실 결과 차단을 통합 테스트하고, 실 Meta 호출/운영 DB 변경은 별도 승인. 문제 시 관련 feature commit만 revert하고 기존 RAW/커서는 보존.

### P6-021 — CI 최신 실패 판정: 인수인계 링크 누락 및 Windows 장시간 실행 (2026-10-21 KST)

- **증상/확정 근거:** feature SHA `bcb335dc687950754ee221bc5ebd847483e898f6`의 [Runtime Integration Offline #39499556675](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39499556675)은 **completed/failure**. Linux 792 tests 중 2건 실패; Windows remaining 667 tests 중 동일 2건 실패; Windows orchestrator는 8분 watchdog timeout. 세 그룹의 정확한 결과를 완료/성공으로 바꿔 적지 않는다.
- **인수인계 결함 확정:** `tests/test_p6_troubleshooting_handoff_guard.py::test_current_handoff_links_survive_chat_restart`가 STATUS.md 및 HANDOFF.md의 **맨 앞 2,000문자** 안에서 `AGENTS.md`와 `docs/P6_TROUBLESHOOTING_LEDGER.md`를 찾도록 강제한다. 여러 상단 요약을 추가하면서 `AGENTS.md` 링크가 잘려 두 파일 각각 AssertionError가 발생했다. 원인은 실행 코드/RAW 변조가 아니라 상단 탐색 규칙 미준수.
- **수정 전략:** STATUS/HANDOFF 첫 문단 앞에 짧은 필독 연결 인덱스를 각각 넣고 최신 CI 실패와 Meta/운영 DB·main 미변경 상태를 구분한다. 테스트 자체를 느슨하게 하거나 테스트를 제외하지 않는다.
- **Windows 별도 문제:** [Runtime #39499556675](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39499556675) Windows orchestrator 70개 테스트를 실행하다 8분 timeout. 마지막 로그 `test_p4_three_db_audit_staged_receipt_is_not_auto_promoted` 다음 stack trace는 `three_api_evidence.py::commit_page`에서 실행 중이었음을 보여준다. **데드락·논리 결함으로 확정하지 않는다.** Windows 전체 테스트가 지속적으로 10초 이상 걸리는 항목 다수 포함, 원인(실행시간/잠금/파일 I/O) 정밀 측정은 별도.
- **이미 확인된 통과:** 같은 SHA의 [Three API #39499556645](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39499556645), [C Contract #39499556483](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39499556483), [Frozen Guard #39499556744](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39499556744)는 SUCCESS. 이는 **Runtime 성공 증거가 아님**.
- **예방:** 새로운 STATUS/HANDOFF 상단을 편집하면 반드시 동일한 cross-chat 링크 검사를 Linux/Windows에서 실행한다. CI가 실패하면 로그상 실패 위치·SHA를 적고 이전 성공 체크를 새 SHA의 결과로 사용하지 않는다.
- **미해결/검증:** 문서 수정 후 새로운 SHA에서 `python -m unittest discover -s tests -p 'test_p6_troubleshooting_handoff_guard.py' -v` 및 전체 Runtime CI 확인. Windows timeout은 별도 작업으로 원인 측정·shard/timeout 변경 대안 비교 후 안전한 최소 변경. 해결 전 Runtime 최종 PASS를 주장하지 않는다.
- **롤백:** 상단 필독 인덱스와 이 사건 추가만 feature branch에서 revert 가능. 동결 설계·운영 collection.sqlite/RAW/CSV·main/Meta HTTP 변경 없음.

> **P6-021 후속 원인·최소 수정 (2026-10-21, 신규 CI 검증 대기):** Windows #39499556675의 `orchestrator`는 70개 중 약 30개 합성 테스트를 통과한 시점에 480초 watchdog에 도달했다. 로그상 여러 SQLite 테스트가 각각 약 12~28초 소요되어 단일 그룹의 소요시간이 예산을 초과한 근거가 있다. 마지막 `commit_page` stack은 시간 초과 당시 실행 위치이며 데드락/원본 결함이라는 확정 근거는 아니다. 대안 A) watchdog만 900초로 상향하면 CI 지연과 실제 정지 탐지 지연이 커진다. **채택 B)** `tests/run_offline.py`에서 4개 오케스트레이터 그룹에 전체 unittest ID의 안정적인 CRC32으로 배정, Windows matrix에 3개 작업을 더해 기존 그룹과 병렬 실행. `tests/test_offline_shards.py`에서 1회씩 정확하게 배정·전그룹 비어 있지 않음·발견 순서 독립성 검증. 전체 테스트와 watchdog 480초는 그대로 보존한다. 신규 코드의 Linux/Windows CI는 실행 결과를 확인해야 하며 합성 전수 PASS나 성능 개선을 사전 주장하지 않는다. 변경 범위는 테스트 실행기/테스트/CI 워크플로뿐이며, 실제 Meta/운영 DB/main·동결 설계 불변. 롤백은 본 변경의 CI/테스트 샤딩 항목만 revert하고 운영 원본은 복원 대상으로 삼지 않는다.

### P6-022 — Windows full-roster CI 480초 초과 / 3그룹 분리 (2026-10-21 KST)

**사건 증거·대안·검증:** [Runtime #39504471845](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39504471845)에서 Linux 및 Windows 9개 중 9개 작업이 성공하고 `offline-windows (roster)`만 실패했다. roster 5개 테스트 중 4개 PASS, 5번째 실행 중 `Timeout (0:08:00)!` 발생. 앞선 테스트가 각각 약 94~132초를 소요한 것이 확인되며, stack `three_api_coordinator.py::lease`는 제한 시점 실행 위치일 뿐 데드락 확정 증거는 아니다. 대안 A) watchdog을 900초로 늘리면 정지 탐지가 늦어진다. **채택 B)** `tests/run_offline.py`의 알려진 5개 294계정 fixture를 `roster`/`roster-2`/`roster-3`에 2/2/1 분리, 신규 테스트는 기본 roster로 fail-visible 배정. `tests/test_offline_shards.py`에서 전수·중복 없는 배정 회귀, Windows CI matrix에 2그룹 추가. 기존 480초 감시·소켓 차단 유지. 커밋 `c7038c2`, `e2f6ad8`, `526736a`. **검증 완료:** commit `5dfae4007a4abcfc0f7b148fcd93e6a4695c384e`의 [Runtime #39510506884](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39510506884) Linux 1/1, Windows 11/11, 총 12/12 성공. [Three API #39510506964](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39510506964), [C Contract #39510506960](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39510506960), [Frozen Guard #39510506972](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39510506972) 모두 같은 SHA에서 성공. **P6-022 오프라인 CI 병목 해결 판정.** 실제 Meta 운영 성공 판정 아님. 실 Meta/운영 DB/CSV/main 불변.

### P6-023 — fixture 직렬 수집→Capture/RAW→커서 확정의 단일 잠금 경계 (2026-10-21 KST)

- **관찰·범위:** 기존 `OfflineSerialContractBridge.dispatch_fixture_once`는 OS 잠금 안에서 영속 JobRequestBindingStore 예약 후 fixture 전송→Capture→RAW까지 수행한다. 그러나 해당 객체에는 수신 뒤의 `PageEvidenceStore.commit_page`까지 동일 serial gate 아래 검증하는 public 경로가 없었다. 기존 `JobRequestBindingStore.commit_captured_page`를 별도로 호출하는 것은 가능했으나 bridge 차원의 직렬 경계와 end-to-end 검증은 부족했다. **실제 동시 커서 오염 사례가 관측된 것은 아니며, 사전 통합/안전성 보강 결정이다.**
- **채택·대안:** `raw_v2/three_api_serial_contract_bridge.py`에 `commit_captured_fixture_page` 추가(`918dd35`). 이미 저장된 durable Capture/RAW 포트를 실행기로부터 재사용하고, 동일 coordinator SQLite OS gate 잠금·유효 레인/시간대·정확한 `OfflineFixtureTransport` 타입·잠금 대기 중 포트 바꿔치기 여부를 검사한다. 기존 `bindings.commit_captured_page`의 request digest·lease·RAW receipt·cursor 선행 증거 검증을 재사용한다. **HTTP 전송/신규 intent 생성/재시도는 이 메서드에서 일절 수행하지 않는다.** 대안인 새 live runner 일괄 구현은 미승인 네트워크/DB 위험과 테스트 범위 확대로 배제; 모든 단계 하나의 SQLite 트랜잭션으로 합치기는 이미 분리된 durable DB 경계와 충돌하므로 배제.
- **테스트:** `tests/test_three_api_serial_contract_bridge.py`에서 실제 격리 coordinator+capture+합성 RAW SQLite, 원래 request binding·executor·fixture transport·page evidence 경로를 사용하는 테스트 5개(`6eb1040`) 추가: 성공 후 페이지 확정, capture 없는 완료 금지, OS gate 점유 중 거절/해제 후 확정, transport 교체 거절, SQLite 파일 재오픈/expired lease recovery 시 두 번째 송신 0. 이후 3개(`e01ae62`) 추가: 잘못된 응답의 안전한 수신 증거 보존·재발송 차단, RAW acknowledgement 중단 이후 재확정, 검증된 cursor로 C레인 page2 unlock. 실호출 없음.
- **검증 상태:** 코드 커밋 `918dd35df1dd12895a99eb0ad17e38c5ec202521`, 테스트 최종 변경 `e01ae625086b62dff5c9e62bd3bdd46085dc42ef`; **이 단계에서는 신규 HEAD 전체 CI 결과 미확정**. 이전 녹색 `58971b3`의 [Runtime 12/12](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39514325379)는 새 코드 통과 근거가 아니다. 새 SHA의 Linux/Windows Runtime 및 Three API·C Contract·Frozen Guard 결과를 확인해야 한다.
- **보존 위험·잔여 차단:** fixture 전용이며 운영 자격증명·Meta Graph·운영 SQLite/CSV/RAW·scheduler·main에 접근/변경하지 않는다. 실제 live-transport serial gate·한도 독립성·운영 파일/백업·365일 History 연결은 **미구현/미검증**. Capture 없는 예약은 재송신하지 않고 operator review에 남는다. 응답 수신과 RAW 확정은 여전히 별도 DB이며 중간 실패가 가능하나, durable capture 기반 replay로 복구한다.
- **롤백·재발 방지:** feature branch에서 신규 commit path/테스트만 revert, 기존 원본/운영 DB를 덮거나 재설정하지 않는다. 신규 직렬 컴포넌트는 transport send·capture 영속화·RAW receipt·page/cursor 확정을 나누어 회귀, 재시작 후 `transport.calls=0` 확인을 필수로 한다.

> **P6-024 CI 후속 확인 (2026-10-21 KST):** `e517436f8bd259f4ccd3826f630e73b7f2a08461` [Runtime #39519036382](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39519036382) `completed/success` Linux 1 + Windows 11 = 12/12; [Three API #39519036531](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39519036531), [C Contract #39519036478](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39519036478), [Frozen Guard #39519036411](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39519036411) 동일 SHA `completed/success`. 이 판정은 이후 P6-025 변경에는 소급하지 않는다.

### P6-024 — fixture-only 단일 직렬 진입점에서 binding→Capture/RAW→커서 원자적 직렬화 (2026-10-21 KST)

- **관찰·원인 분리:** P6-020/023의 `dispatch_fixture_once`와 `commit_captured_fixture_page`는 각각 OS 직렬 잠금을 보유했지만 **수신과 커서 확정 사이에는 잠금 해제 구간**이 존재했다. 바인딩으로 이중 HTTP는 차단했으나, 전체 작업을 한 차례의 serial gate 안에서 실행하는 사전 합격 조건은 충족하지 않았다. **실제 데이터 손상·경합 장애는 관측되지 않았고, 아키텍처 연결 보강이다.**
- **채택한 최소 변경:** `raw_v2/three_api_serial_contract_bridge.py`에 `OfflineSerialContractBridge.run_fixture_page_once(job, request, evidence, now=...)` 추가(`98416ee`). 정확한 `OfflineFixtureTransport`, `CaptureJournal`, `ContractRawWriter`, 동일 coordinator에 연결된 `PageEvidenceStore`, timezone-aware now, lane/phase/lease를 검사한다. **단일 OS gate 안에서** 요청 binding을 조회해 최초라면 `dispatch_once`가 영속 예약→fixture 1회 송신→Capture/RAW를 수행하고, 예약이 이미 있으면 **전송 단계를 건너뛴다**. 마지막에 기존 `commit_captured_page`로 RAW 영수증 및 선행 cursor를 검증하고 page evidence를 커밋한다. 중단됐으나 Capture가 없는 예약은 재송신하지 않고 실패 차단한다. 기존 분리 API는 레거시 호환을 위해 그대로 둔다.
- **대안·제약:** 기존 두 메서드를 중첩 호출하면 nonreentrant OS lock에서 실패하므로 단일 gate 안의 원자적 *진입* 메서드로 구성. 여러 SQLite DB의 commit 자체를 하나의 트랜잭션으로 합칠 수는 없으며, 차단 후 영속 Capture 기반 재개를 유지한다. job 선택/로스터·쿼터 판단은 호출자 역할로 남고, 이 메서드는 **API 레인 회전 스케줄러가 아니다**.
- **합성 검증 추가:** `tests/test_three_api_serial_contract_bridge.py` `629e758` 7개 사례: 최초 fixture 한 번만 송신, 기존 capture replay no-send, RAW/커서 commit 중단 후 복구, Capture 없는 기예약 상태 송신 0, OS gate 경합 거절, 실제 cursor commit까지 lock 유지, 다른 coordinator evidence/가짜 transport 거절. `cf25395` 2개 사례: 고정 A(계정1 page1)→C(page2) 연속 커서와 고정 B(계정151 page1) 신규 작업을 각각 단일 진입점으로 확인. 합계 **9개 테스트 작성**; 실행 성공은 별도 CI 확인.
- **검증 근거·진행:** 코드 `98416ee0d613f26aa71f767f94d04b419216afe5`, 테스트 `629e75846f997440b8bca1cf030264330072ca01` / `cf25395e90da0ef9ef71dcfbf7b5321733e41e4b`. **기록 시점 최신 전체 CI PASS 미확정**, 변경 SHA와 최종 문서 HEAD가 다르므로 새 Runtime Linux·Windows와 Three API/C Contract/Frozen Guard가 정확한 최종 SHA를 완료할 때만 PASS로 변경. 이전 `b2272de` 성공을 새 코드 성공으로 대용하지 않는다.
- **안전 경계·잔여 작업:** 단일 tick 처리 자체가 fixture-only이고 `MetaTransport` 및 인증 credential을 받지 않는다. 실 Meta 3 API 호출/독립 쿼터 검증, 294계정 실시간 스케줄 자동선택, 운영 DB/CSV, 365일 History, 배포, `main` 병합은 **미구현·미승인**. 테스트 작성은 운영 데이터 검증이 아니다.
- **롤백/회귀 방지:** feature 브랜치의 `98416ee`/추가 회귀만 git revert; 운영 원본/토큰/수집 이력은 건드리지 않는다. 후속 개발자는 작업 성공과 중간 중단·중복 발송 0·실패 차단을 별도 검증하고, 85% usage 차단·최소 65분 규칙을 새 runner에서 우회하지 않는다.

### P6-025 — 3 API fixture 직렬 선택·85% quota 회복가능 영속 게이트 (2026-10-21 KST)

- **확인된 기존 공백:** P6-024의 `run_fixture_page_once(job, request, evidence)`는 호출자가 이미 lease/job을 선택해야 했다. 따라서 3개 A/B/C 레인 선택, quota 사용량 기록, durable fairness 순서, 기존 bound capture 복구 우선권은 아직 단일 실행 진입점에 연결되지 않았다. **운영 결함이 발생했다는 보고가 아니라 사전 구현 부족 사항이다.**
- **구현·선택 정책:** `raw_v2/three_api_serial_contract_bridge.py`에 `step_fixture_serial(fixtures, evidence, now, lane_order)` 추가(`9829596`). **정확히 1개의** 합성 coordinator job을 동일 OS lock 아래 lease→증거 검증→영속 binding→fixture response→Capture/RAW→receipt→cursor로 진행. 초기 순서 A/B/C, 성공 레인 `offline_contract_serial_turn`에 SQLite 보존, 재시작 후 성공한 레인 다음부터 순환. `lease_for_recovery` 우선 → `lease_for_dispatch`; 기존 binding은 원래 Capture를 읽고 **전송 0**, Capture 없으면 operator review 차단. `PageEvidenceStore`의 선행 cursor가 없는 page2/3은 Coordinator가 lease하지 않는다. 잘못됐거나 없는 fixture는 **binding·전송 전** 해당 미사용 lease만 원복 후 fail-closed 처리. `limit=4`는 RequestEnvelope/RequestSpec 기존 계약 검증을 사용하며 공식 294계정 리스트·원본 RAW 입력은 하지 않는다.
- **quota 사고 차단 개선:** 사용량 안전증거가 유실되는 프로세스 강제 종료를 고려하여 **send 직전** lane에 `fixture_pending_usage` / `halted=1` / 최소 65분 `ready_at`을 영속 기록(`ed09b97`). 정상적인 **safe response + durable Capture**의 verified usage가 85 미만인 경우에만 임시 보류를 해제(`5b90f1f`), 85 이상이면 `usage_limit`, 사용량 누락·전송 결과 불명·schema 실패·Capture 미영속이면 `missing_usage`로 65분 이상 차단. 단순 시간 경과로 자동 해제하지 않고 명시적 operator `reset_after_verified_cooldown`을 유지한다. 이 규칙은 실 Meta quota 독립성을 증명하지 않는다.
- **진단·테스트:** `fixture_tick_diagnostics(now)` read-only(`7e6a00a`): 레인 차단 사유/해제 가능시각/신규 lease 후보/복구 lease 후보/마지막 성공 레인 조회, 무인 실행·Meta 호출·수집 변경 없음. `tests/test_three_api_serial_selection.py`의 **17개 합성 회귀 작성**(`9dafe40`, `ac946dc`, `011ae6f`, `153160c`): A→B→C cursor 연결, 85%와 무사용량 차단, 예외 응답 재전송 금지, 영속 Capture 재시작 replay, 잃어버린 fixture lease 원복, 잠금 경합, 65분 후에도 operator reset 전 차단, 전송 중 선차단 관찰, schema 실패 시 차단 유지, 무변경 read-only 진단. 신규 테스트가 Windows `remaining` 480초 그룹에 누적되지 않도록 `tests/run_offline.py`(`6b8dd9a`)의 4개 orchestrator CRC32 shard에 분산하고 `tests/test_offline_shards.py`(`c5cf881`)에 배정 회귀 추가, `p6-runtime-integration-offline.yml`(`c8c2350`)에 테스트 파일 path trigger/compile 포함.
- **검증 상태:** 변경된 모든 커밋은 `codex/p6-readiness-hardening-20261019` 개발 브랜치에만 존재하며 본 장부 작성 시점 최종 HEAD의 **전체 Runtime Linux/Windows 12개 및 3 API/C Contract/Frozen Guard 결과는 미확정**. 이전 `e517436` [Runtime #39519036382](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39519036382) 12/12 PASS는 이 변경 전 증거. 최신 HEAD의 실제 Actions 결과로 판정할 것; 이전 run의 queued/cancelled는 성공으로 간주하지 말 것.
- **위험·미구현:** 수동 fixture dict만 사용하고 실제 Meta 전송, 가동 중 API 주체 인증/독립 quota, 운영 DB·RAW/CSV, 원본 294계정 실계정 ID fingerprint, 2라운드/365일 History 자동 정책, 스케줄러, `main` 병합에 연결되지 않았다. quota 분리는 **합성 상태 모델**일 뿐 Meta 비즈니스 자격증명별 quota 분리를 입증하지 않는다. 서로 다른 영속 DB 사이를 단일 원자적 트랜잭션이라고 주장하지 말 것.
- **롤백·재발방지:** feature branch의 `step_fixture_serial`, `fixture_tick_diagnostics`, 새 합성 테스트 및 샤딩 경로만 순차 revert, 동결 원문·운영 원본 RAW/CSV/DB는 건드리지 말 것. 합성 fixture 없이도 bound Capture는 복구 가능해야 하고, 실제 HTTP의 send 재시도는 별도 승인·재검증 전까지 불허한다.

## 새 사건을 추가할 때의 고정 서식

```markdown
### P6-NNN — 짧은 제목 (발생일/KST)
- 증상·최초 발견 경로:
- 사용자 영향·보존 위험:
- 재현 조건/명령 (토큰·PII·사용자 RAW 제외):
- 확정 원인 / 미확인 가설:
- 해결 방법과 대안 배제 이유:
- 수정 파일·commit SHA·PR:
- 검증: 테스트명, CI run ID, 정확한 SHA, PASS/FAIL/CANCELLED/PENDING:
- 남은 리스크·후속 조치:
- 롤백 방법 (원본 DB/CSV 덮어쓰기 금지):
- 재발 방지 규칙:
```

## 미래 개발자가 지킬 규칙

1. **코드보다 재현 조건이 중요하다.** 같은 에러를 새 이름으로 다시 고치지 않도록 에러 메시지, 원인, 검증 commit을 연결한다.
2. 비밀·토큰·사용자명/식별 데이터·원본 RAW payload는 문서에 직접 붙이지 않는다. 이 기록은 public repository에서 볼 수 있다고 가정한다.
3. 새 아키텍처가 도입돼도 구 버전의 오류/우회 방법과 대안 비용을 삭제하지 않는다. 사건은 append 또는 상태변경으로 이어 쓰고, 기존 서술은 근거 없이 덮어쓰지 않는다.
4. **설계 결정(왜?)**, **구현 상태(무엇이 실제로 동작?)**, **검증 증거(어느 SHA에서 통과?)**를 분리한다.
5. 오프라인 테스트 성공 ≠ Meta API 실제 운영 성공 ≠ 원본 데이터 무결성 보증. 금지된 실호출/운영 DB 편집/main 병합은 별도 명시 승인 필수.

## 참고할 원본

- [현재 상태](../STATUS.md) · [인수인계](../HANDOFF.md) · [개발 규칙](../AGENTS.md)
- [동결 기준](../DESIGN_FREEZE_NOTICE.md) · [최종 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)
- [AI 작업단위 규칙](AI_WORK_UNIT_PROTOCOL.md)
- [기존 RAW v2.1 트러블슈팅 상세 연대기](RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md) · [오류 대응 SOP](RAW_V2_ERROR_HANDLING_RUNBOOK.md)
- [Git 커밋 기록](https://github.com/hibi89/ohora-trend-catcher/commits/codex/p6-readiness-hardening-20261019)
- [GitHub Actions](https://github.com/hibi89/ohora-trend-catcher/actions) · [Draft PR #6](https://github.com/hibi89/ohora-trend-catcher/pull/6)


### P6-026 — Quota gate blocked local Capture recovery (2026-10-21)

- **Confirmed in code:** Coordinator.lease checked lane.halted/ready_at before filtering bound-only jobs; a 85% usage stop therefore denied both first-time HTTP dispatch and capture-only recovery.
- **Fix:** `3a271e1` makes the lane gate apply only to non-bound-only leases; recovery still requires an existing immutable job binding and an eligible lease, and the runner still refuses to resend when Capture is absent.
- **Regression:** `ee3ed6c` and `414e189` add isolated SQLite quota/lease tests. The blocked lane must refuse new dispatch while a pre-bound page remains recoverable; a different lane can continue. The test does not make a real Meta request.
- **Verification:** GitHub commits confirmed; full CI on latest documentation HEAD remains unverified. Do not claim production readiness or new-post collection success.
- **Policy:** Frozen 3 callers, limit=4, 2 daily rounds, 65-minute minimum cooldown and immutable RAW unchanged; no operational DB, scheduler, main merge or Meta call.

### P6-037 — 2026-10-21 · 신규 페이징 사건의 장부 색인 누락

- **실패 증거:** 전체 offline 실행 954 tests, 1 failure / 1 skip. 실패는 test_incident_index_matches_unique_detailed_records: 상세 사건 set에는 P6-035가 있으나 index table에는 없었다.
- **확정 원인:** P6-035/036 상세 내용을 작성했지만 중앙 P6 incident index 표에 두 사건을 추가하지 않았다. 코드·페이징 기능 실패가 아니다.
- **수정:** index 표에 P6-035/036을 등록했고 별도 P6-037을 추가해 이 절차 회귀도 기록했다.
- **수정 commit:** `7632788`.
- **후속 검증:** 수정 후 test_p6_troubleshooting_handoff_guard.py 4/4 PASS, paging/recovery 29/29 PASS, normalizer 11/11 PASS. 전체 954개 실행은 수정 후 반복하지 않았다. 이전 전체 결과 952 PASS / 1 FAIL / 1 SKIP이며 전체 재실행은 미검증이다.
- **안전·롤백:** 문서 색인만 수정. Meta·운영 DB/RAW/Capture/quota 상태 변경 없음. 문서 수정은 필요한 경우 표 행만 되돌릴 수 있으며 상세 사건 기록을 삭제하지 않는다.

### P6-038 — 2026-10-21 · 전체 오프라인 재검증 및 cross-store 증거 경계

- **재검증:** `tests/run_offline.py` selected 955, exit code 0. 직접 실행한 `test_three_api_paging.py` 16/16, `test_three_api_paging_recovery.py` 13/13, `test_three_api_meta_normalizer.py` 11/11: **40/40 PASS**.
- **기존 증거 읽기 상태:** 앞선 read-only cross-store join에서 페이지/응답/Capture/RAW receipt 97개 연결과 저장 digest 필드 equality가 확인되었고 lane 집계는 A=49, B=48이었다. 이 확인은 cryptographic body/Capture digest 재계산을 포함하지 않았고, 기존 문서가 지칭한 49개 대상과도 일치 여부가 확인되지 않았다. 따라서 연결·무결성 전체 검증 또는 복구 가능 판정으로 취급하지 않는다.
- **미실시:** 기존 coordinator/Capture/RAW DB는 수정하지 않았고, 별도 복구 라운드나 Meta 호출도 실행하지 않았다. 기존 failed-no-retry 요청을 재전송하지 않았으며 3페이지·85%·쿨다운 정책을 바꾸지 않았다.
- **Git:** 이 기록은 기존 작업 브랜치에서만 관리한다. 인증파일·토큰·계정 ID, 사용자 RAW는 포함하지 않는다. main 병합은 하지 않는다.
- **다음:** 연결된 세 DB의 읽기 전용 digest 재검증과 원래 49건 대상 식별은 여전히 선행 조건이다. 실제 재탐색/수집은 별도 승인된 recovery round와 quota gate 아래에서만 수행한다.

### P6-039 — 2026-10-21 · bounded live pilot partial progress and n8n blocker

- **범위:** `three_api_manual_pilot.py` only; official 294-account roster, credentials directory stored locally, maximum 3 steps in the first invocation and 1 in a follow-up. No main/legacy collector DB used.
- **Durable result:** Two A pages were committed after the prior 97-receipt status, resulting in 99 durable receipts and 396 posts/observations. This is partial live collection, not full 294-account completion.
- **Unprocessed Capture:** A C page2 bound response produced HTTP 200 safe evidence, 4 media items, a recomputed-valid response digest, a recomputed-valid Capture digest, and matching response/Capture cursor. It has no RAW receipt and remains an unprocessed Capture under a `live_pending_usage` lane hold. The run stopped safely; no further attempt was made. Do not resend this bound request. Root cause of the first runner exception is not established; stored evidence is internally valid but RAW promotion has not been verified.
- **Existing B failures:** Four B HTTP400 jobs remain `failed_no_retry`; B `live_pending_usage` hold remains. No cooldown/usage reset performed. Do not retry them.
- **Quota and calls:** No hold was manually cleared and no 85% threshold bypassed. The runner's first failure summary exposed `live_meta_called=unknown`; actual request count from that summary is therefore not asserted. No further live call after post-run evidence review.
- **n8n:** No local n8n process, CLI, or listener on 5678. The two checked export JSON files are inactive, two-node drafts, without credentials or webhook trigger. No activation/connection performed.
- **Next safe actions:** After lease expiry, diagnose C RAW promotion using only its existing immutable Capture and an HTTP-disabled path; maintain C usage hold unless verified safe usage permits normal gate release. Reconcile B credentials/permissions/fields offline; existing failed requests stay excluded. n8n needs a reachable instance, user-approved credential binding, and a workflow that consumes verified P6 RAW output. Continue only on this branch, no `main` merge.

### P6-040 — 2026-10-21 · SQLite Row/tuple false digest-collision during Capture replay

- **증상:** A C page2 response had valid safe response digest and immutable Capture digest, 4 posts and a cursor matching the captured response, but remained unprocessed without a RAW receipt. Replaying its Capture through the production RAW projection stopped safely before coordinator completion.
- **확정 원인:** `raw_v2/three_api_observation_versions.py::_write_state` fetched `sqlite3.Row` values then compared the Row object directly with a tuple. In production `collector.connect` sets `row_factory=sqlite3.Row`; the same stored state therefore compares unequal to its equal tuple, raising a false `Account state digest collision` (and similarly affected post states). Existing offline tests used tuple rows and missed it.
- **최소 수정:** convert both fetched rows to tuples before comparing persisted state JSON. Added `test_unchanged_recheck_reuses_states_with_sqlite_row_factory`.
- **증거:** The immutable C Capture failed `write_capture` in a read-only-source/in-memory clone of the current pilot RAW, tracing to `_write_state` account-state comparison. After the fix, the same Capture successfully wrote and validated a receipt in that memory clone. Then, after the existing lease naturally expired, one bounded runner step returned `recovered_pages=1`; no Meta HTTP was needed. Read-only postcheck: 100 durable receipts / 400 posts; the C Capture is processed and its RAW receipt digest validates. C lane remains held at `live_pending_usage`; no manual reset.
- **검증:** full `tests/run_offline.py` 956 selected, exit 0; P2 observations 15/15, RAW writer 14/14, paging recovery 13/13, handoff guard 4/4. No real cursor invalidation was observed, so the re-anchor flow remains synthetic/offline-only.
- **커밋:** `28612ec` code/test. Handoff documentation is in the following docs-only commit.
- **보존/다음:** Existing RAW source DB/CSV/Capture untouched; only new verified P6 pilot observations were appended. B's four and A's one `failed_no_retry` jobs remain excluded. Reconcile credential/permission/field cause for existing HTTP400s offline; n8n remains unconnected because no local server/daemon is available. Do not retry or manually release usage hold.

### P6-041 — 2026-10-21 · bounded A progress and unavailable n8n runtime

- **Bounded live result:** With the read-only preflight showing lane A open and B/C held, one `--max-pages 3` invocation completed all 3/3 page steps. Postcheck: 103 durable receipts / 412 post observations; A=54 done, B=48, C=1. No jobs for B/C were dispatched and all previously failed requests remain `failed_no_retry`.
- **Safety:** This was the final live batch in this work session. No further API request was made, no lane hold was manually cleared, and existing production RAW files remain unchanged. The P6 pilot store contains only verified append observations. Continue future calls only after the required cooldown and fresh usage gate.
- **n8n runtime check:** No n8n CLI/process/local listener. Docker CLI and service are installed, but the daemon was unavailable. A user-authorized attempt to start the service failed with Windows service permission denial; no container/data was created. Existing two exports remain inactive 2-node manual-trigger/execute-command drafts without credential refs or webhook trigger. Automation integration remains incomplete.
- **Git:** This state and the preceding P6-040 fix are documented on `codex/p6-readiness-hardening-20261019`; branch push/remote SHA verification follows this docs commit.
- **Next:** Continue P6 collection only under the 65-minute cooldown and existing 85% stop gate, starting with read-only status/quota. Diagnose B's existing 400s offline; never replay them. n8n requires a locally available runtime or user-provided reachable endpoint and safe credential binding before activation.

### P6-042 — 2026-10-22 · 커서 무효화 재앵커 요구사항 재검증 및 인수인계

- **요구사항 반영:** 사용자는 무효 커서 오류에서 기존 요청을 재전송하지 않고 `resume_required`로 멈춘 뒤, 별도 승인 라운드에서 최신 페이지부터 기존 media ID 겹침을 찾고 새 커서로 이어갈 것을 추가 요청했다. 계정 배정/순서 294, 페이지당 4개, 라운드당 3페이지, 85% quota stop, cooldown, Capture/RAW 불변성은 그대로다.
- **코드 상태:** 이 로직은 기존 `raw_v2/three_api_paging_recovery.py`에 이미 존재했다. `cursor_failure_disposition`은 정확히 분류된 `cursor_invalid`에만 `resume_required`를 반환하고 기존 요청 재전송을 거부한다. `plan_cursor_recovery`는 최대 세 개의 synthetic page만 받아 기존 ID 겹침 없이는 `coverage_gap`, 겹침과 새 `after`가 있으면 `resume_ready`를 낸다. 중복 관측 ID는 보존하고 분석 ID만 중복 제거한다. 삭제를 확정하지 않고 HTTP/DB/Capture/RAW 변경도 하지 않는다.
- **최소 회귀 추가:** `tests/test_three_api_paging_recovery.py`에 무효 커서 → 새 첫 페이지 겹침 → 교체 커서 `resume_ready`까지 잇는 오프라인 planner 테스트를 추가했다. 기존 paging 테스트는 정상 `after` 커서 보존도 확인한다.
- **한계:** 이는 계획 함수이며 실제 오류 본문 분류, 승인 프롬프트, Meta first-page fetch와 resume dispatch에 배선된 운영 복구 runner가 아니다. 실제 커서 무효화도 관측되지 않았다. API 호출과 운영 복구는 별도 승인 대기다.
- **Git/P7:** 현재 사본은 fetch 후 원격 작업 브랜치 `188bc2f`까지 fast-forward했고, P6-042 테스트/문서 commit `713160e98a1c2c272383f3a84a9f6a19b034551e`를 origin에 push했다. 재-fetch 후 로컬과 원격 작업 브랜치 SHA 일치 확인. 최신 origin/main `2e55b90a60892fb721295b1faa5207c84b48586b`에는 이 recovery source/top handoff가 없어 main-level handoff sync는 미완료로 명시한다. main 병합 금지.
- **n8n:** n8n `2.44.5`를 별도 `%LOCALAPPDATA%\OHORA_N8N_RUNTIME`에 설치했으나 workflow import/연결/활성화는 수행하지 않았다. 기존 P6 상태 CLI는 운영 pilot DB를 SQLite read-only로 읽으려 했으나 OS access denied가 재현되어 안전하게 중단됐다. 기존 RAW workflow draft는 legacy collector를 호출하므로 실행하지 않는다. Automation은 미완료다.
- **검증:** Full offline rerun selected 956 but was interrupted during the slow 294-account stress tests; prior full 956/956 on the earlier verified snapshot remains historical evidence. Current targeted reruns: paging recovery 14/14, paging 16/16, handoff guard 4/4 (34/34 PASS). 실 Meta HTTP 0회, 운영 DB/CSV/Capture 0회 변경.

### P6-043 — 2026-10-22 · 날짜 전환 시 quota hold 초기화 위험

- **근본 원인:** `three_api_manual_pilot.run_manual_pilot`은 KST 날짜별 `coordinator.sqlite`를 연다. 새 날짜에는 `Coordinator`가 A/B/C lane 행을 기본값(unhalted, epoch cooldown)으로 만들고 294×3 jobs를 seed하므로, 전날의 `live_pending_usage` hold가 새 날짜에 승계되지 않았다. 5분 주기 n8n이 날짜를 넘어 계속 호출되면 85% 사용량 확인 gate를 우회할 위험이 있었다.
- **최소 수정:** 새 날짜 coordinator에 아직 jobs가 없을 때만 가장 최근의 이전 날짜 coordinator를 SQLite `mode=ro`로 검사한다. 3개 lane 상태가 모두 유효할 때만 halted/reason 및 향후 `ready_at`를 새 coordinator에 복사한다. 이전 DB는 쓰지 않는다. 상태가 손상됐거나 읽을 수 없으면 dispatch 전 fail-closed한다. 같은 날짜에서 이후 수동 검증 reset을 다음 tick이 되돌리지 않도록 이미 jobs가 있으면 재승계하지 않는다.
- **검증:** `tests/test_three_api_manual_pilot.py` 13/13 (신규 cross-day hold/cooldown 회귀 포함), paging 16/16, cursor recovery 14/14, env loader 8 PASS + 1 SKIP, handoff guard 4/4; total **55 PASS / 1 SKIP**. py_compile PASS. Meta/HTTP 0회, 운영 RAW/CSV/Capture 변경 0회.
- **운영 상태:** 2026-10-21 read-only coordinator status showed B/C held `live_pending_usage`; last persisted successful-response usage values were below 85 but came from prior-day observations and do not prove current usage. No lane reset or live request is recorded for this update. The prior cursor errors remain classified only as account-scoped HTTP 400; missing raw subcodes prevent root-cause attribution.
- **실패 코드 세분화(read-only 집계):** A의 HTTP 400 1건은 Graph code 110; B의 4건은 code 110 2건, code 100 1건, 코드 미보존 1건이었다. 저장된 실패에는 code 190 인증 거부나 code 10/200 권한 거부가 없었다. **subcode는 보존되지 않아** code 110이 특정 target-user 문제인지, code 100이 어떤 field/parameter 때문인지 확정할 수 없다. C는 기록된 HTTP 400 실패가 없고 lane usage hold만 있다. P6 경로는 Batch API가 아닌 단일 serial GET이다. 계정별 실패는 토큰 인증 오류라고 단정할 근거가 없다.
- **제약/다음:** n8n 2.44.5 exists in an isolated local folder, but workflow remains unconnected. Only after a verified P6 page receipt should an inactive 5-minute workflow be imported and tested. Any activation is subject to the same current quota gates; do not reset B/C from elapsed cooldown alone.
