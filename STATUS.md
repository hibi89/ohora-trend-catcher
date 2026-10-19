> **p6 CI VERIFIED — 2026-10-19 KST · 운영 진단 수집완료 오인 방지 변경 검증 완료.** 테스트 포함 소스 커밋 [`b6cc27b`](https://github.com/hibi89/ohora-trend-catcher/commit/b6cc27b7af0452e1fbf98429fcfec6857f9feaeb)에 대해 [3-API Actions #39244069568](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39244069568) **29 suites / 326/326 PASS**, Python 3.11 컴파일 PASS, 신규 `test_single_closed_window_without_source_proof_is_not_completion` 포함 `test_three_api_operator_report.py` **16/16 PASS** (job 로그 확인). [RAW v2 Actions #39244069487](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39244069487) **424/424 PASS** (job 로그 확인). 이 시점 이후 변경은 문서 및 BOOK만이며 실행 코드는 변경되지 않음. `front_window_closed`와 source page1 증거 불일치 계정 수 및 합성/실운영 미검증 경고를 읽기 전용으로 반환한다. **실 Meta 호출·권한/실 quota·page4+ HTTP·운영 RAW·무인 스케줄러는 미검증/미구현**, 실제 운영 전환 승인 아님. 운영 `collection.sqlite`, 토큰, 공식 account_list.csv 변경 없음. 향후 안전 단위: 오프라인 운영자 요약 표현 개선 또는 승인 전 실운영 연결 상세 설계. 롤백: `09a37a1` 코드 변경과 `b6cc27b` 테스트 변경을 되돌리며 생산 데이터 복구 불필요.

> **p6 VERIFICATION UPDATE — 2026-10-19 KST · 신규 경고 분기 로직의 독립 재현 스모크 5/5 PASS, 저장소 CI 미확인.** GitHub `main`의 `operator_report()` 구현·테스트 파일을 재조회하고 `DailyGapLedger.record_page()`의 `bootstrap_history` 윈도우 종료 조건을 직접 점검했다. 격리된 Python 3 환경에서 새 경고 분기를 동일 조건식·문구로 재현하여 ① 모두 open ② 1개 closed/source 없음 ③ 294개 closed/source 없음 ④ 294개 closed/source 있음 ⑤ source 있음/open의 5개 케이스 통과. **이는 저장소 원본 테스트 실행이 아니라 신규 분기 부분 재현 테스트**이므로 `tests/test_three_api_operator_report.py` 16/16, 3-API 전체 325+1, RAW 전체 423+1 또는 CI 통과를 주장하지 않는다. GitHub 커넥터의 현재 PR 기반 Actions 조회와 commit statuses 조회에서 신규 main-push 체크 결과가 나오지 않아 CI를 확인할 수 없으며, 외부 git clone은 DNS 제한으로 불가했다. 코드는 이번 검증 단계에서 추가 변경 없음. **다음 필수 검증:** 실제 체크아웃에서 `python -m unittest discover -s tests -p 'test_three_api_operator_report.py' -v` 및 `python tests/run_offline.py`, GitHub Actions 로그 확인. 테스트 전 메인 운영 변경·Meta 실호출 금지.

> **p6 CURRENT — 2026-10-19 KST · 합성 운영 진단의 수집완료 오인 방지 경고 구현, CI 확인 대기.** `operator_report()`가 `front_window_closed`와 실제 source page1 확정 증거를 비교해 `first_page_proof_gap_in_closed_windows` 및 한국어 `completion_warning`을 반환한다. 종료된 윈도우 1개/294개에 source page1이 없는 테스트를 추가했으며, SQLite 읽기 전용·실 Meta 미검증 경계를 유지한다. **테스트는 GitHub Actions 검증 전이므로 완료 주장 금지.** 관련: [진단 가이드](docs/THREE_API_OPERATOR_DIAGNOSTICS.md), [구현](raw_v2/three_api_daily_orchestrator.py), [회귀 테스트](tests/test_three_api_operator_report.py). 기존 합성 294계정/RAW 검증 이력은 유지, 운영 `collection.sqlite`·토큰·공식 CSV·실 Meta 호출 변경 없음. 다음: 본 커밋의 Three API + RAW v2 CI 확인 후 상태 갱신. 라이브 전환·스케줄러·운영 RAW writer는 별도 승인 전 금지. 롤백: 이 단위 구현·테스트·가이드 변경만 원복, 생산 데이터 복원 불필요.

> **p6 CURRENT — 2026-10-19 KST · 읽기 전용 운영 진단 보고 구현/합성 오프라인 검증 완료 (이전 이력보다 우선).** [raw_v2/three_api_daily_orchestrator.py](raw_v2/three_api_daily_orchestrator.py)의 \`OfflineDailyOrchestrator.operator_report(now=timezone_aware_datetime)\`는 이미 열린 세 개의 **격리된 합성 SQLite 연결**에서 상태만 읽는다. 신규 [tests/test_three_api_operator_report.py](tests/test_three_api_operator_report.py) 15/15건(스냅샷 전체 SQLite dump 호출 전후 동일 확인, 294계정 page1 미수집 건수와 최대 20개 샘플, A/B/C quota 중단/65분 수동 reset 대기, 임대 15분 만료 건수, raw receipt 미투영/스테이징, 계정 충돌, 오래된 catch-up/manual-review, 출처·권한 오인/민감 데이터 안전)을 검증했다. 상태 항목: \`daily_front\`, \`lanes\`, \`gaps\`, \`storage\`, \`policy\`; 중단 사유는 허용값 \`missing_usage\`·\`usage_limit\`·\`unknown\`만 표시. 함수는 \`step\`, \`reconcile\`, 토큰/API/RAW 변경, 수동 reset을 호출하지 않는다. **\`integrity=not_fully_revalidated\`**, \`live_meta_verified=false\`; \`recovery_attention=false\`만으로 SHA 검증 완료 또는 실계정 완결을 주장할 수 없음.
>
> **GitHub 검증:** source [\`1df3065\`](https://github.com/hibi89/ohora-trend-catcher/commit/1df3065b15bfc67d6fb123028be4c214b5336148), [3-API Actions #39241519144](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39241519144) **29 suites / 325/325 PASS**, [RAW v2 Actions #39241519241](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39241519241) **423/423 PASS**, Python 3.11 \`py_compile\` 통과. 사용법·각 지표 의미·제약: [docs/THREE_API_OPERATOR_DIAGNOSTICS.md](docs/THREE_API_OPERATOR_DIAGNOSTICS.md). 이전 294/294 **합성** 전체 배치, source page296 + catch-up1, RAW 309건과 lane 장애 격리 테스트는 유지/회귀 통과. **실 Meta 호출 0회, 실제 토큰 0회, 기존 운영 \`collection.sqlite\` 변경 0회, 공식 local \`account_list.csv\` 변경 0회.** 이는 실제 294계정의 실수집 증거가 아니다.
>
> **다음 안전 개발 단위:** 진단 결과의 operator-readable 출력/장애 대응 문구를 추가하거나 QA하되 합성 경계 유지, 즉시 실 API/운영 DB 통합을 암묵적으로 활성화하지 않음. 실제 Meta \`children\` 필드·권한·A/B/C 독립 quota·real page4+ HTTP·무인 daily scheduler·production RAW writer는 여전히 미구현/미검증, **사용자 별도 승인 필요**. 테스트: \`python -m unittest discover -s tests -p 'test_three_api_operator_report.py' -v\`, \`python tests/run_offline.py\`. 롤백: 코드 \`1df3065\`의 operator_report/테스트/CI/진단 가이드 제거; 생산 데이터 복원 불필요. 후속 Codex 인계는 [docs/CODEX_NEXT_SHIFT_2026-10-19.md](docs/CODEX_NEXT_SHIFT_2026-10-19.md)를 참조하며 이미 통과한 기능을 중복 구현하지 말 것.
>

> **p6 LATEST — 2026-10-19 KST · per-lane/account failure-isolation regression CI VERIFIED.** Source [d51981c](https://github.com/hibi89/ohora-trend-catcher/commit/d51981c1d109fa8f68f87c52ad0dfec89780b183); [Three API Actions 39240138751](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39240138751) **28 suites / 310/310 PASS**, [RAW v2 Actions 39240138723](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39240138723) **408/408 PASS**, new 5/5. Added to tests/test_three_api_daily_orchestrator.py: (1) malformed A account2 -> A halted, B accounts151/152 preserved in RAW, 65m verified manual reset restores A without duplicates; (2) malformed B -> A still captures accounts1/2; (3) transient A mirror promotion failure -> persisted source replayed without refetch and B continues; (4) **cross-account post ID collision** -> all existing RAW records intact, conflicting page remains staged, fail-closed global recovery barrier; (5) malformed B carousel children -> only B halted, C continues valid page2, unapproved paging URL not persisted. Ordinary lane failures isolate; unresolvable committed RAW conflicts correctly prevent all subsequent captures (not silently skipped). Existing synthetic 294/294 batch and previous tamper tests remain green. **NO live Meta calls, real credentials, official roster CSV mutation or operating collection.sqlite writes**. These tests DO NOT validate actual A/B/C rate quota independence, live Meta child syntax/permissions, page4+ real HTTP, live unattended scheduler, production RAW migration, or actual 294-account live collection.
>
> **NEXT SAFE UNIT:** inspect synthetic lane failure receipts and operator-facing blocked/recovery status without relaxing the global RAW integrity barrier; run additional performance tests only where justified. Codex handoff docs/CODEX_NEXT_SHIFT_2026-10-19.md retains the full synthetic batch as already completed; do not rebuild it. Test with python -m unittest discover -s tests -p 'test_three_api_daily_orchestrator.py' -v, then GitHub Actions Three API + RAW v2. Rollback this unit: revert d51981c (test additions only; no production data).
>

> **p6 LATEST — 2026-10-19 KST — Synthetic 294/294 daily batch + evidence tamper fail-closed verified.** Source [ed74bd1](https://github.com/hibi89/ohora-trend-catcher/commit/ed74bd158fcd9c4f7add2346fcb44c7a8d791a1a); [Three API Actions 39239206405](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39239206405) **28 suites, 305/305 PASS**; [RAW v2 Actions 39239206392](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39239206392) **403/403 PASS**. Three latest tests in tests/test_three_api_daily_orchestrator.py cover staged post payload tampering, missing immutable capture clock and changed capture timestamp: step() fails closed without a new fixture request. Previously 294/294 **synthetic** full-roster integration verified 296 front proofs + one catch-up proof, RAW mirror 309 posts/309 observations/309 media, restart idempotence, and front gap when account294 is missing. This does **not** prove 294 real Instagram accounts or real Meta A/B/C quota/permissions. Zero live Meta calls, real tokens, operating collection.sqlite edits or official roster CSV modifications. Scheduler remains manual synthetic-only.
>
> **Next safe unit:** focused failure-isolation/immutable receipt and timestamp audit, missing/corrupt source proof edge coverage, quota/cursor restart boundaries and synthetic throughput only. DO NOT redo the already passing full 294-account batch as if unfinished. Codex successor instructions: docs/CODEX_NEXT_SHIFT_2026-10-19.md. Actual Meta nested children field syntax/permissions, independent app quotas, page4+ real HTTP, unattended schedule and production RAW migration remain unverified and require separate user authorization. Rollback newest test-only change: revert ed74bd1; no production data were changed.
>

> **CI FINAL VERIFIED · 2026-10-19 KST:** source [08b4d4e](https://github.com/hibi89/ohora-trend-catcher/commit/08b4d4e2266ff2e7e2e14479c014d56c0715c113) / [Three API Actions 39238776626](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238776626) **28 suites, 302/302 PASS**, [RAW v2 Actions 39238776861](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238776861) **400/400 PASS**. 추가 4개 lane cooldown/lease/restart 테스트 포함. 294개 가상 계정 front 완주 + 한 계정 page2/page3/page4 Catch-up + RAW mirror 309/309/309 및 재시작 중복방지, 한 계정 누락 시 catch-up 차단 검증. 실 Meta/실토큰/운영 collection.sqlite 호출·변경 0회. **이것은 실제 294개 계정 실수집의 증거가 아님.** 이전 본문에 'CI 재검증 중'으로 적힌 것은 과거 시점 기록이며 이 최신 확인을 우선한다. Codex handoff: docs/CODEX_NEXT_SHIFT_2026-10-19.md.
>

> **p6 CURRENT · 2026-10-19 KST · 294/294 합성 배치 및 3-lane 중단·복구 추가 검증 (이전 항목보다 우선).** 전체 synthetic 294계정 page1, KR_001 page2·page3, page4 Catch-up, 총 296 front proofs+1 catchup proof, RAW mirror 309 posts/309 observations/309 media 및 재시작 후 멱등성, 계정294 fixture 누락 시 전체 gap 차단을 [tests/test_three_api_full_roster_batch.py](tests/test_three_api_full_roster_batch.py)에서 검증. [3-API Actions 298/298 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238559498) / [RAW v2 Actions 396/396 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238559592), 기준 source [2a54fed](https://github.com/hibi89/ohora-trend-catcher/commit/2a54fed1b8a5e67671a7df9f683d5cdc9d1d517e). 신규 [tests/test_three_api_daily_orchestrator.py](tests/test_three_api_daily_orchestrator.py) 4개 추가: 85% halt→65m explicit reset, usage 미수신 단일 lane halt, 15m lease expiry, 재시작 후 cooldown 유지. 해당 4개 테스트는 **현재 CI 재검증 중이므로 완료 주장 금지**: source [08b4d4e](https://github.com/hibi89/ohora-trend-catcher/commit/08b4d4e2266ff2e7e2e14479c014d56c0715c113), [3-API run 39238776626](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238776626), [RAW run 39238776861](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39238776861). 실 Meta 호출 0, 실토큰 0, 운영 collection.sqlite 변경 0. 합성 294/294는 **실제 공식 294계정 실수집 검증이 아님**. 현장 Meta children 지원·앱별 독립 quota·실 HTTP page4+·운영 RAW 마이그레이션·무인 시간 트리거는 미검증/미구현. Codex 교대 문서: [docs/CODEX_NEXT_SHIFT_2026-10-19.md](docs/CODEX_NEXT_SHIFT_2026-10-19.md). 다음 단위는 CI 최종 결과 반영 및 경계조건 추가 보강, 안전 승인 후 live 전환 설계.
>

> **p6 CURRENT · 2026-10-19 KST · 294개 roster 기반 합성 일일 수집 오케스트레이터 완료/검증 (이전 기록보다 우선).** 새 [raw_v2/three_api_daily_orchestrator.py](raw_v2/three_api_daily_orchestrator.py)와 [tests/test_three_api_daily_orchestrator.py](tests/test_three_api_daily_orchestrator.py) (신규 17/17). 격리된 3개 SQLite source/gap/mirror와 294계정 KST snapshot을 불변으로 바인딩한 뒤 **명시적 수동 step(lane, now)** 한 번에 합성 fixture 한 페이지 이하 처리. **전 294계정 front 우선→미완료 Catch-up→history는 hold/검토**; 매 step 시작 시 완료된 source ID/커서·RAW mirror 증거를 재검증/복구하고, RAW 투영 실패 시 새 fixture를 읽지 않음. immutable fixture capture clock과 SHA 기반 source replay, missing fixture는 lease만 반납하고 누락을 성공 처리하지 않음. front 누락·gap review·history hold와 계정별 페이지/RAW 현황 조회 기능 포함. **실 API sender, daemon, 스케줄 시계 자동실행, 실토큰/운영 DB writer는 없음.**
>
> **검증 근거:** source [ab547ab](https://github.com/hibi89/ohora-trend-catcher/commit/ab547ab3a1679979b554c6aee2c4565f14765344), [Three API GitHub Actions #39237519823](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39237519823) **27 suites / 296/296 PASS**, [RAW v2 Actions #39237519913](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39237519913) **394/394 PASS**; Python 3.11 py_compile 포함. tests/test_three_api_daily_orchestrator.py는 17/17 PASS. **주의:** 테스트는 294개 로스터 불변성, 일부 synthetic fixture 페이지의 front/gap 진행·중단복구를 검증한 것이며, **294/294 전 계정의 끝까지 합성 배치나 실 Meta 수집을 완료했다는 뜻이 아니다.** 이전 279/377 테스트 수는 과거 체크포인트. Meta 실호출 0회, 실토큰 0회, 운영 collection.sqlite 변경 0회, 공식 local account_list.csv 변경 0회.
>
> **다음 실행 문서:** [docs/CODEX_NEXT_SHIFT_2026-10-19.md](docs/CODEX_NEXT_SHIFT_2026-10-19.md) (Codex 6 Luna Extra High에 사용자 직접 전달할 P0/P1/P2 지시서). 가장 중요한 다음 오프라인 단계는 **294계정 완전 합성 배치 E2E / lane·quota·lease·중단복구 부하 테스트와 검증 가능한 계정별 수집·누락 수치**. 자식 미디어 실제 Meta fields/permissions, A/B/C 앱 quota 독립성, 294계정 live 자동화, page4+ 실제 HTTP, 운영 RAW DB 통합은 미검증/미구현, 사용자 승인 전 실호출·운영 변경 금지. 백업·롤백: 신규 orchestrator 파일+테스트+CI 참조만 제거; 운영 source 변경 없음. 검사 명령: python -m unittest discover -s tests -p 'test_three_api_daily_orchestrator.py' -v; python tests/run_offline.py; .github/workflows/three-api-coordinator.yml.
>

> **p6 CURRENT — 2026-10-19 19:14 KST · 캐러셀 자식 필드 명시적 opt-in 요청 규격 / 모의 HTTP→RAW 통합 검증 완료 (이전 항목보다 우선).** [`raw_v2/three_api_request_spec.py`](raw_v2/three_api_request_spec.py)의 `include_carousel_children=False` 기본값은 기존 `media.limit(4)` 필드 문자열을 변경하지 않는다. `True`(정확한 bool)일 때만 모의 Meta Business Discovery의 `children{id,media_type,media_url,thumbnail_url}` 필드 표현식을 추가한다. [`raw_v2/three_api_http_preflight.py`](raw_v2/three_api_http_preflight.py) → [`raw_v2/three_api_graph_http.py`](raw_v2/three_api_graph_http.py) → [`raw_v2/three_api_guarded_executor.py`](raw_v2/three_api_guarded_executor.py) → [`raw_v2/three_api_daily_projection.py`](raw_v2/three_api_daily_projection.py) 전 경로에서 옵션을 명시적으로 전달하고 원 요청 명세와 비교하여 조작을 거부한다. [`tests/test_three_api_carousel_optin.py`](tests/test_three_api_carousel_optin.py)에서 가짜 HTTP sender→정규화→front staging→KST ledger→별도 RAW mirror 자식 미디어 보관, opt-out 기본 경로, nested cursor 오류 차단, 무HTTP 중단 복구를 검증했다. Catch-up은 아직 별도 **synthetic fixture** 실행이며 실제 HTTP 경로 아님.
>
> **검증:** 소스 [`d10b206`](https://github.com/hibi89/ohora-trend-catcher/commit/d10b2067fd35a0516a1c46605db23531aa5fe394), [3-API 오프라인 Actions 279/279 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39236004070) (26 suites, 신규 13/13), [RAW v2 전체 Actions 377/377 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39236004122), Python 3.11 compile PASS. 최초 CI에서 테스트가 캡션 `without children` 문자열을 JSON 키로 오인하여 한 건 실패했고, 실제 JSON key 검사로 변경 후 동일 전체 회귀 통과. 과거 266/364 체크포인트는 이전 검증 수치. **실제 Meta 호출 0회, 실제 토큰 사용 0회, 운영 `collection.sqlite` 변경 0회.** 외부 실 Meta에서 현재 버전의 nested children 문법·권한·반환 범위, 하위 페이지 및 추가 quota 비용은 미검증이다. 캐러셀 자식의 이미지 바이너리 다운로드/실제 완전 수집 주장 금지. `GraphHTTPClient` 기본값은 계속 전송 차단. 실제 Meta와 운영 DB 통합은 사용자 별도 승인 전 금지.
>
> **다음 안전 개발 단위:** 294계정 일일 수집/정규 front 우선 → 미완료 Catch-up → history 스케줄러의 **합성·오프라인 실행 계획과 재시작 내구성** 검증(실 API 호출 없음). 운영 RAW DB 통합, A/B/C 별도 실제 quota/인증, 실 page4+ HTTP 및 294계정 현장 수집은 미구현/미검증. 테스트: `python -m unittest discover -s tests -p 'test_three_api_carousel_optin.py' -v`; CI: `.github/workflows/three-api-coordinator.yml` 및 `.github/workflows/raw-v2-tests.yml`. 롤백: `38e6536` 선택형 필드 규격·옵션 전달·신규 테스트/CI 변경 되돌림, 생산 DB 복구 불필요.
>

> **p6 CURRENT — 2026-10-19 19:04 KST · 캐러셀 개별 미디어 정규화/RAW 미러 투영 오프라인 검증 완료 (이전 기록보다 우선).** `raw_v2/three_api_meta_normalizer.py`는 사용자가 주입한 합성 Meta-shaped `children.data`가 있을 때만 CAROUSEL_ALBUM 자식의 `id, media_type, media_url, thumbnail_url`을 허용목록으로 추출해 순서를 보존한다. 이를 기존 검증된 front(1~3페이지)와 Catch-up(page4+) staging→SHA-256 증거→격리형 `raw_post_media` (parent/child, item_order)와 연결한 무네트워크 통합 테스트를 추가했다. 자식 최대 20개, 중복/부모 ID 충돌, 잘못된 형식, 남은 하위 페이지(next/has_next), 증거 변조는 fail-closed. 중단 뒤 원본 증거로 무재수집 replay하며 좋아요 NULL과 0 구분을 유지한다. **자식 정보가 없는 응답은 '자식이 0개' 혹은 '완전 수집' 증거가 아니다. 실제 자식 리스트 완전성은 공급된 fixture 증거 수준에서만 검증.** 자식 media URL·이미지 실제 바이너리는 다운로드/보관하지 않는다.
>
> **CI 검증:** 코드 [`77ccb8b`](https://github.com/hibi89/ohora-trend-catcher/commit/77ccb8bd9385525ff3b0d8035e309177437b8d82) 기준 [3-API 266/266 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39234826306) (25 suites, 신규 carousel 14/14), [RAW v2 364/364 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39234826354), Python 3.11 컴파일 통과. 기존 252/350은 과거 체크포인트. **Meta 실호출 0회·실토큰 0회·운영 `collection.sqlite` 변경 0회.** 기존 단일 API 수집기 및 294 공식 원본 목록 변경 없음. **미구현·차단:** 현재 `three_api_request_spec.py` 기본 필드 목록은 자식 `children`을 요청하지 않는다. 실 Business Discovery nested children 필드의 가용성/권한/요청 문법·응답 크기 및 pagination은 미검증이며 자동 변경하거나 실 Meta 호출하지 않았다. A/B/C 실제 quota 독립성, 일일 스케줄러, 운영 RAW DB 통합·백업도 미검증. 다음 안전 단위는 **기본값을 바꾸지 않는 캐러셀 자식 필드 요청 규격(명시적 opt-in)과 fake HTTP→front/Catch-up 원본 투영 회귀**, 그 이후 실 API 검증은 별도 승인 필요. 테스트: `python -m unittest discover -s tests -p 'test_three_api_carousel_children.py' -v`. 롤백: `77ccb8b`의 normalizer 변경·신규 테스트·워크플로 참조만 원복(운영 DB 복구 불필요).
>

> **p6 CURRENT | 2026-10-19 18:53 KST | page4+ synthetic Catch-up RAW mirror completed and CI verified.** Modules: [raw_v2/three_api_catchup_offline.py](raw_v2/three_api_catchup_offline.py) now stores an allowlisted post payload + original captured UTC timestamp + sealed SHA-256 along with the ID/cursor proof in ONE isolated gap SQLite transaction (versioned inbox, safe nullable schema upgrade). [raw_v2/three_api_catchup_payload.py](raw_v2/three_api_catchup_payload.py) checks inbox seal and the **committed** KST catch-up page_proof, then stages/promotes each page into separate synthetic `three_api_raw_mirror.sqlite` with stable receipt; if mirror fails, it blocks additional fixture reads until replay succeeds. No network, tokens, or operational DB access. Legacy v0 ID-only rows remain valid as ID proof but explicitly **unprojectable as full post contents**. `normalize_meta_page` retains only currently approved fields (id, caption, media_type, media_url, permalink, timestamp, like_count, comments_count, thumbnail_url), **not** full unfiltered Graph response or nested carousel children. NULL engagement stays different from 0. Cross-DB commits are **not atomic**; durable revalidation/reconcile repairs crashes without HTTP. Synthetic tests include tampering, observed_at, cursor mismatch, legacy row, mirror fail/restart, two-page replay, deduplication, and secrets filtering.

> **Verified GitHub Actions source [fa5ccfa](https://github.com/hibi89/ohora-trend-catcher/commit/fa5ccfabe36753dce9c1bb19e6f64ac56edbea59):** [Three API 252/252 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39233407814) (24 suites, new 20/20) and [RAW v2 350/350 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39233407741), Python 3.11. Previous 232/330 checkpoint below is superseded. No live Meta request, token usage, existing RAW collector edit, or production `collection.sqlite` write. Missing: verified nested carousel-child payload collection, real Meta sender and cursor/permission behavior, B/C actual credential/quota independence, 294-account automatic daily scheduler, production RAW database migration and operating recovery checks; none authorized for live operations. Next safe unit: offline carousel-child media normalization/storage coverage and fixture-only integrity regression (do not claim the API supplies children until verified). Test: `python -m unittest discover -s tests -p 'test_three_api_catchup_payload.py' -v`. Rollback current `fa5ccfa` new module/test, workflow references and catch-up code changes; preserve all operational data.

> **p6 CURRENT 2026-10-19 18:41 KST — verified synthetic RAW projection milestone.** [raw_v2/three_api_raw_projection.py](raw_v2/three_api_raw_projection.py) now validates immutable 294-account/day snapshot, committed pages 1-3 and ordered original payload with source SHA-256/cursor/ledger evidence before projecting to a separate `three_api_raw_mirror.sqlite`. Explicit observation timestamps, stable page receipts, atomic mirror promotion and no-HTTP restart replay; new 17/17 tests PASS. GitHub source [089c75f](https://github.com/hibi89/ohora-trend-catcher/commit/089c75f81b0ccb8c28c5229f3e1a99def95292bf): [Three API 232/232 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39231980884), [RAW v2 330/330 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39231980999). Initial test setup error corrected. **Synthetic/offline ONLY**. No Meta calls/tokens/production DB writes; existing collector intact. Catch-up page4+ remains ID/cursor-only, NOT full-payload integrated; live scheduler, production storage integration, B/C auth and actual independent quotas not verified/implemented and require explicit user approval before testing live. Rollback new module/test and CI references; next unit is offline page4+ full-payload staging and crash-safe replay. Tests: `python -m unittest discover -s tests -p 'test_three_api_raw_projection.py' -v`.

> **p6 2026-10-19 · 격리형 RAW 미러 신규 코드/테스트 등록 (CI 검증 대기):** `raw_v2/three_api_raw_mirror.py`와 `tests/test_three_api_raw_mirror.py` 신규. 실제 RAW v2 `posts`/`post_media`/`post_observations`와 필드 의미가 유사하지만 **별도 `three_api_raw_mirror.sqlite` 파일 전용 synthetic schema**. 4건 이하 fixture와 명시적 관측 시각/receipt를 allowlist 정규화한 뒤 페이지를 durable stage, 별도 원자적 promote; 재시작시 staged receipt만 다시 적용하며 `(post_id,request_id)` 관측 중복 차단, 게시물 소유자 충돌·미디어 ID 충돌·미검증/변조 evidence 차단, 미제공 좋아요는 NULL/존재 플래그 0. 독립 fixture 단위 테스트 20개 및 전체 CI 추가. **기존 `collection.sqlite`, `raw_v2/collector.py`, `daily_scheduler.py` 변경 없음, Meta 호출·실토큰 0회.** 아직 기존 `three_api_daily_projection.py`/page4+ inbox와 실제 full-payload 자동 배선 없음; 실제 운영 RAW DB 쓰기·schedule·API 인증 미구현/승인 대기. 테스트: `python -m unittest discover -s tests -p 'test_three_api_raw_mirror.py' -v`; CI 전체 `.github/workflows/three-api-coordinator.yml`, `.github/workflows/raw-v2-tests.yml`. 롤백: 신규 모듈·테스트·CI 참조 제거, 격리 시험 DB 별도 폐기 가능. 이전 테스트 수는 과거 스냅샷.

> **p6 2026-10-19 18:19 KST · 1차 Catch-up 오프라인 실행기 최종 회귀 확인:** `raw_v2/three_api_catchup_offline.py`의 page4+ 합성 응답→durable inbox→`DailyGapLedger` ID·cursor 증거 투영과 중복 방지/중단 복구가 구현됨. 신규 14/14, 전체 [Three API 195/195 PASS #39228770707](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39228770707) (21 suites, CI source `5cdb2b1596528e55a6c47c5fb30f63fd30b9e073`); 전체 [RAW v2 293/293 PASS #39229112183](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39229112183) (source `13376a2d1f09f513d3c88866931c52cf9af7f5d5`). 초기 test string quoting 오류는 수정 및 재검증 완료. 테스트는 모두 Python 3.11 / 합성 offline. **실제 Meta 호출 0회, 운영 `collection.sqlite` 변경 0회, 실제 토큰 사용 0회.** 다음 단위: 별도 합성 RAW-style payload 저장소에 페이지/미디어/관측 멱등 저장·복구 테스트. `GuardedThreeApiExecutor`의 page4+ HTTP 연결, 294계정 일일 scheduler, 실 API/인증/독립 quota 및 운영 DB 전환은 여전히 미구현·승인 대기. 수정 전 백업 대상인 기존 로컬 운영 DB/원본 수집기는 변경하지 않았으므로 데이터 롤백 불필요; 코드 롤백은 `a68440d`의 신규 모듈·테스트·CI 참조 제거.

> **p6 2026-10-19 18:16 KST · Catch-up 오프라인 실행기 검증 완료 (아래 'CI 확인 전' 기록보다 우선):** `raw_v2/three_api_catchup_offline.py` + `tests/test_three_api_catchup_offline.py` + 3-API CI 연결. GitHub [Actions #39228770707](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39228770707), 코드 [`5cdb2b1`](https://github.com/hibi89/ohora-trend-catcher/commit/5cdb2b1596528e55a6c47c5fb30f63fd30b9e073) **21 suite, 195/195 PASS (신규 14/14)**, Python 3.11 py_compile PASS. 첫 CI의 신규 테스트 문자열 이스케이프 실패는 수정·재검증 완료. RAW v2 전체 회귀의 이번 변경 CI는 아직 확인 전(아래 workflow에 신규 테스트 변경 트리거 추가).
> **구현·범위:** fixture-only 실행기. front→gap catch-up→history 우선순위, lane halt/쿨다운, 15분 lease, page4+ ID·cursor inbox, 재시작시 무재조회 reconcile, anchor 직접 확인·미발견시 review. **미연결:** 실제 `GuardedThreeApiExecutor` HTTP sender의 page4+ 연결, 운영 RAW post/media DB 쓰기, 294계정 일일 scheduler, 실 B/C 인증·Meta 커서·A/B/C quota 검증. 운영 `collection.sqlite`, 실토큰, `.env`, 단일 API 수집기 변경 0회. 다음 단위는 별도 합성 DB에 원본 payload 멱등 적재·복구 설계 및 테스트. 원본 단일 API 데이터 보존.
> **테스트 명령:** `python -m unittest discover -s tests -p 'test_three_api_catchup_offline.py' -v`; `.github/workflows/three-api-coordinator.yml`. **롤백:** 새 모듈·테스트·워크플로 참조 제거(코드 `a68440d`, 수정 `5cdb2b1`)하며 기존 `raw_v2/collector.py`, `daily_scheduler.py` 보존. **Meta 실제 호출/토큰/운영 DB 변경은 승인 전 금지.**

> **p6 2026-10-19 · 오프라인 Catch-up 실행기 작업 (CI 확인 전):** `raw_v2/three_api_catchup_offline.py`는 기존 `DailyGapLedger.next_actions()`와 lane readiness를 사용해 정규 front 3페이지 이후 미완료 gap의 page4+ 합성 응답을 별도 `catchup_inbox`에 기록한 후 ID/커서 증거로 투영한다. lease 중복 방지·15분 만료·장애 후 staged inbox 재조정·앵커 ID 발견 여부·사용량 중단 lane 보존을 검증하는 테스트 14개와 GitHub Actions workflow 연결을 추가했다. **본 변경의 CI는 아직 실행/확인 전이며 실 API 테스트는 0회.** 실제 HTTP sender/인증·실제 294계정 page4+ 수집·운영 RAW payload 적재·일일 스케줄러는 미구현. 기존 `collection.sqlite`, A/B/C 토큰 및 기존 단일 API 수집기는 수정하지 않음. 롤백: 새 모듈·테스트·workflow 참조를 제거. 원본 결정을 추적할 문서: `docs/THREE_API_COLLECTION_IMPLEMENTATION.md`.

> **p6 최신 상태 · 2026-10-19 17:44 KST:** [3-API 181/181 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39224854113), [RAW v2 279/279 PASS](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39224854085), 동일 검증 코드 `4858e04` (CI 로그 확인). `raw_v2/three_api_daily_projection.py`가 완료된 3-API staging ID/게시물 원순서/커서 증거를 KST 일일 ledger에 **별도 SQLite로 멱등 투영**, 크래시 뒤 API 재호출 없이 `reconcile_all()` 가능. 증거는 별도 DB이므로 cross-DB 원자적 commit은 아님. 최초 lease 전에 294계정 roster와 snapshot 날짜를 불변으로 바인딩하며, 과거 데이터에 날짜를 임의 부여하지 않는다. `three_api_reanchor.py`는 운영자 수동 개시 후 최신 head에서 원래 anchor를 재확인해야 gap 완료를 인정하며 옛 증거를 보존. 이전 날 미완료 front의 History 승격 금지.
> **미완료:** 실제 Meta API/토큰 인증, A/B/C quota 독립성, live daily scheduler, page4+ Catch-up HTTP, 실제 게시물 운영 RAW DB 적재·백업/복구는 미구현·미검증. **실제 API 호출/운영 DB 변경 없음.** 기존 `raw_v2/collector.py`/운영 단일 API 경로 변경 없음. 아래 이전 진행상태·테스트 수는 역사 기록.

> **2026-10-19 p6 최신 — 일일 12개 정규수집 + 초과분 Catch-up 증거 저장소 구현 (이전 항목보다 우선)**
> - **구현:** [`raw_v2/three_api_daily_gap.py`](raw_v2/three_api_daily_gap.py) 신규 **격리형 오프라인 정책 모듈**. KST 날짜별 계정의 이전 최신 게시물 ID(anchor), 최대 **3페이지 × 4개 = 12개** front 페이지, 검증된 next cursor, 완료되지 않은 `catchup_pending` 구간과 수집 증거를 별도 SQLite 트랜잭션에 저장. 15개 신규 게시물 시 12개 front 이후 다음 페이지의 3개 + **이전 최신 게시물 ID**를 직접 확인한 경우에만 `verified` 처리. 새 KST 날짜의 front 기록은 과거 미완료 gap을 삭제하지 않는다. 재시작 후 동일 gap 이어받기, 중복·커서 불일치/순환 거부, terminal인데 anchor 미발견 시 `needs_review` 처리. **모든 계정의 정규 front → 미완료 Catch-up → History** 순서의 *결정만 반환*하며 실행하지 않는다.
> - **검증:** [GitHub Actions #39222475792](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39222475792), CI SHA `b6e802866c154462dda86b12bcd93fafc18c76c6`: Python 3.11 컴파일 및 전체 **18개 스위트 157/157 PASS** (job 로그 직접 확인). 신규 [`tests/test_three_api_daily_gap.py`](tests/test_three_api_daily_gap.py) 17/17 PASS, 독립 로컬에서도 17/17 PASS. 신규 코드 `346d048`, 신규 테스트 `550f95f`, CI `b6e8028`.
> - **중요한 범위 제한:** 아직 `three_api_daily_gap.py`는 `GuardedThreeApiExecutor`/기존 `daily_scheduler.py`와 **연결되지 않았다**. 게시물 payload가 아닌 ID·커서 증거만 다루는 정책 프로토타입이므로 라이브 수집·데이터 유실 방지의 실증이 아니다. 다른 날의 API paging cursor가 계속 유효하다는 보장도 없고, cursor 무효화 시 무한 재시도/‘완료’ 처리하지 말고 head부터 다시 확인하는 별도 재앵커 설계가 필요하다. 하루 12개보다 많이 게시되는 계정은 별도 추가 요청/용량이 없으면 backlog가 증가할 수 있으며 ‘무조건 다음 날 3개 수집’도 보장하지 않는다.
> - **기존 코드 확인:** 단일 API `raw_v2/collector.py`에는 `catchup_cursor`/`catchup_anchor`/`catchup_jobs` 및 anchor 발견 후 gap 완료 처리가 이미 있다. **이번 코드는 이를 대체하지 않으며 기존 운영 DB, 수집기, 토큰, Meta API를 건드리지 않았다.** 향후 배선 전 운영 승인·백업·실 API 커서 동작·3 lane quota 독립성 검증 필수. 현재 실행할 실수집 명령 없음.
> - **롤백:** 신규 파일 `raw_v2/three_api_daily_gap.py`, `tests/test_three_api_daily_gap.py`, CI 관련 신규 참조 제거. 기존 수집기나 운영 데이터 변경 없음.

> **2026-10-19 p6 최신 최상단 — 보호형 HTTP→A/B/C 실행기 연결, 140/140 CI PASS (이전 기록보다 우선)**
> - **구현:** [`raw_v2/three_api_guarded_executor.py`](raw_v2/three_api_guarded_executor.py): `GuardedThreeApiExecutor`는 GraphHTTPClient → 기존 `OfflineThreeApiRunner(envelope_fixture=True)` → 사용량(85%) 검사 → Meta 응답 정규화 → 별도 SQLite staging/커서 증거를 한 오프라인 실행 경로로 연결한다. 기본 `GraphHTTPClient()`는 승인·sender가 없어 **호출 불가능**. 실토큰·운영 DB·실제 Meta 호출을 하지 않는 **합성 sender**로만 테스트했다. 상태 DB 이름이 `collection.sqlite`이면 차단한다.
> - **오류·복구:** HTTP 전송/인증 사전검증/응답·커서 이상 시 **해당 lane만 영구 중단(halted) + 최소 65분 cooldown**, 작업 lease/증거는 미완료로 남기고 다른 lane은 계속 실행 가능. 자동 재시도/자동 해제 없음. 시간이 지나도 명시적 `reset_after_verified_cooldown` 호출 전에는 동작하지 않음; 리셋은 운영자가 쿼터·계정상태 확인 후에만 해야 함. 15분 lease 만료 후 같은 페이지부터 재시도 가능. 실제 quota 독립 여부는 **입증되지 않음**.
> - **테스트:** [GitHub Actions run #39220095882](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39220095882), CI SHA `cb16a723b149031998d0c12e5d9077152e480221`: Python 3.11 컴파일 PASS, **17개 스위트 합계 140/140 PASS**(CI job 로그 직접 확인). 신규 [`tests/test_three_api_guarded_executor.py`](tests/test_three_api_guarded_executor.py) 16/16 PASS. 코드 `f0408d4`, 신규 테스트 `f2bc897`, CI `cb16a72`.
> - **금지·미구현:** 사용자 별도 승인 없이 실 Meta API 호출·실제 sender 연결·`.env`/토큰 업로드·운영 `collection.sqlite` 변경 금지. 실제 Meta 앱 A/B/C 별도 인증·쿼터 관측, 공식 294계정 실운영 스케줄러/중복 방지/운영 DB 변환 및 복구는 **미구현·미검증**. 단일 API 기존 RAW v2 collector는 변경 없음. 다음 채팅은 이 최신 인덱스와 [`docs/THREE_API_COLLECTION_IMPLEMENTATION.md`](docs/THREE_API_COLLECTION_IMPLEMENTATION.md)을 우선 볼 것.
> - **롤백:** 신규 파일 `raw_v2/three_api_guarded_executor.py`, `tests/test_three_api_guarded_executor.py`, CI 신규 항목 제거(커밋 `f0408d4`, `f2bc897`, `cb16a72`). 기존 수집 상태 건드리지 않음.

> **2026-10-19 p6 최상단 최신 상태 — 보호형 Graph HTTP 클라이언트 / 124개 오프라인 CI 통과 (아래 과거 상태보다 우선)**
> - **새 코드:** [`raw_v2/three_api_graph_http.py`](raw_v2/three_api_graph_http.py): `GraphHTTPClient`는 **기본 비활성**, `approved=True`와 명시적 `sender`가 모두 없으면 **전송 불가능**. HTTPS `graph.facebook.com` GET 경로·정규화된 fields만 허용하고, 요청 토큰은 Authorization Bearer 헤더에만 둠. 최대 응답 512 KiB, 기본 15초/상한 60초, 허용된 quota 헤더만 반환. HTTP 오류의 message/토큰·기타 헤더를 제거하고, 전송 예외도 민감정보 없는 메시지로 처리. `make_strict_sender()`는 리다이렉트를 거부하고 프록시를 비활성화하는 sender를 **생성만** 하며 자동 전송하지 않는다. **코드 존재는 라이브 실행 승인이나 실수집 활성화를 의미하지 않음.**
> - **검증:** [GitHub Actions Three API offline tests #39219274206](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39219274206), 커밋 `c9db92e2a541b096a00e757eedc20ed89665bcc7`: Python 3.11 컴파일 + **16개 스위트 총 124/124 PASS** (job 로그로 합계 검증). 신규 [`tests/test_three_api_graph_http.py`](tests/test_three_api_graph_http.py) 17/17 PASS: fake sender로 요청·429·응답 크기·오류 비식별화·쿼터/staging·기본 차단 테스트. **실제 Meta HTTP 통신을 한 적 없음.**
> - **미완료·실행금지:** 승인된 실토큰 로딩과 실제 HTTP sender 연결, live 전용 실행기, 실제 Meta API 인증/권한 테스트, A/B/C 앱 별도 쿼터 입증, 시간 경과·배치·일일 스케줄러 검증, 운영 RAW `collection.sqlite` 적재·복구 설계. 승인 전 실제 호출/운영 DB 변경 금지. 기존 단일 API collector는 변경하지 않음. 다음 채팅방은 이 인덱스와 [`docs/THREE_API_COLLECTION_IMPLEMENTATION.md`](docs/THREE_API_COLLECTION_IMPLEMENTATION.md)을 먼저 확인.
> - **복구·롤백:** 신규 모듈 커밋 `3642a5b`, 신규 테스트 `301643c`, CI `c9db92e`. 이를 되돌리면 이번 단계 신규 구현만 제거 가능. 아래 이전 스냅샷의 테스트 수·‘실제 HTTP 클라이언트 미구현’은 구 상태 기록.

> **2026-10-19 p6 최신 상태 — 인증값 주입·모의 HTTP 전송까지 검증 (이전 항목보다 우선)**
> - **구현:** [`raw_v2/three_api_runtime_boundary.py`](raw_v2/three_api_runtime_boundary.py)의 `OfflineCredentialedFetcher`는 호출자가 **명시적으로 제공한 매핑**에서 lane별 토큰·IG User ID를 실행 직전에 읽고, [`three_api_http_preflight.py`](raw_v2/three_api_http_preflight.py)의 사전검증 명세를 기반으로 모의 Bearer 헤더를 만든 뒤, **네트워크 기능 없는 메모리 전송기**에만 전달한다. `OfflineThreeApiRunner(..., envelope_fixture=True)`로 A→C 커서 인계·사용량 85% 차단·분리 SQLite staging을 연동 검증. 토큰을 결과·상태 출력·staging DB에 남기지 않는 회귀 테스트 포함. `os.environ` 자동 접근과 `.env` 파싱은 구현하지 않았다.
> - **검증:** [GitHub Actions #39218398995](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39218398995), CI 트리거 SHA `89f4084fffcd5d79699576d8b9366ea8c3b2e9ee`: **15개 스위트·107/107 PASS**, Python 3.11 컴파일 PASS (job 로그 직접 확인). 신규 `tests/test_three_api_runtime_boundary.py` **16/16 PASS**.
> - **아직 미구현/미검증:** 실제 HTTP 전송 구현·실제 Meta API 인증/호출, 운영 DB 적재, A/B/C 세 앱/토큰의 진짜 독립 rate limit, 일일 실운영 스케줄러 통합 및 장시간 운영. 별도 승인 없이 실수집·운영 `collection.sqlite` 변경 금지. 토큰/환경파일 업로드·로그·커밋 금지.
> - **롤백:** 코드 변경 `03ad2c8`(신규 모듈), `84f944d`(테스트), `89f4084`(CI)을 각각 되돌릴 수 있다. 기존 단일-API 수집기는 변경하지 않았다. 아래 과거 시점의 ‘CI 미확인/토큰 읽기 미구현’은 이후 최신 상태로 갱신된 기록이다.

> **2026-10-19 p6 자동 검증 확인 (최신):** GitHub Actions [Three API offline tests #39217147308](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39217147308), 코드·테스트 CI 변경 커밋 `1d6e71c344bca9c5181fcc29a89cb0c1c38adc08`에서 **14개 테스트 스위트 합계 91/91 PASS**, Python 3.11 컴파일 PASS. 실제 job 로그까지 확인. 독립 로컬 Python 3.13 관련 테스트 23/23 PASS. 이는 **오프라인 회귀 검증**이지 실제 Meta 인증·수집·쿼터 격리 검증이 아니다. 아래 ‘CI 미확인’은 **해당 기록 당시의 과거 상태**이며 최신 기준으로는 해결됨. 코드: `raw_v2/three_api_http_preflight.py`; 운영 `collection.sqlite`/토큰/실제 API 호출 없음.

> **2026-10-19 p6 최신 인수인계 — HTTP 오프라인 사전검증**
> - **구현:** [`raw_v2/three_api_http_preflight.py`](raw_v2/three_api_http_preflight.py)에서 A/B/C 인증 **환경변수 참조** + `RequestSpec` + IG User ID + `v26.0`을 검증해 인증정보 없는 Graph GET 요청을 준비한다. 데이터 전용 `OfflineFixtureTransport`는 실제 HTTP를 지원하지 않는다. 임의 구성한 명세/인증 참조, 잘못된 버전·ID, 잘못된 cursor, 인증값·ID가 디버그 repr에 노출되는 문제를 검사한다.
> - **검증:** 격리된 로컬 Python 3.13에서 관련 3개 모듈의 오프라인 테스트 **23/23 PASS** (인증 참조 7 + 요청 명세 5 + 신규 HTTP preflight 11). 이전 사용자 Windows 68/68 PASS는 유지되나, 전체 **91개 현재 테스트**의 GitHub CI 결과는 **미확인**. 최신 CI에 새 테스트 등록.
> - **미구현·금지:** 토큰 읽기/실제 HTTP 요청/Meta live 수집/세 lane 실제 인증·쿼터 분리/RAW v2 운영 DB 연동은 **아직 미구현·미검증**. 사용자 승인 없이 Meta 호출, `.env.local` 업로드, 운영 `collection.sqlite` 수정 금지.
> - **상세:** [`docs/THREE_API_COLLECTION_IMPLEMENTATION.md`](docs/THREE_API_COLLECTION_IMPLEMENTATION.md) 및 상기 코드·테스트. 새 채팅방에서는 이 최신 인덱스를 우선하고 아래 과거 시점의 재검증 대기 기록은 최신 상태로 오인하지 말 것.

> **2026-10-19 p6 인증 경계:** [`raw_v2/three_api_credentials.py`](raw_v2/three_api_credentials.py)는 A/B/C에 대해 서로 다른 6개 환경변수 **이름만** 검증한다. 실제 토큰·IG ID를 읽거나 저장하지 않으며 HTTP 요청을 실행하지 않는다. [`tests/test_three_api_credentials.py`](tests/test_three_api_credentials.py) 7개는 별도 격리 로컬 테스트에서 **7/7 PASS** 후 코드와 동일한 내용으로 GitHub 반영, CI 스위트 등록. 직전 사용자 확인 68/68 PASS 이후 신규 5+7개를 포함한 **총 80개 GitHub 전체 CI 결과는 별도 확인 필요**. 이 단계는 요청 명세·인증 참조 구조로서 **실제 HTTP 클라이언트/실수집/쿼터 격리 검증 미완료**. 실제 토큰·.env·운영 DB를 건드리지 말 것.

> **2026-10-19 p6 요청 명세 준비:** `raw_v2/three_api_request_spec.py` 추가. 토큰·HTTP·환경변수 없이 A/B/C lane, 사용자명, 4개 고정 페이지 크기, 검증된 커서로 Business Discovery 필드 표현식만 생성. URL 직접 추적 금지, 안전한 진단 dict는 커서 원문 미노출. `tests/test_three_api_request_spec.py` 신규 5개, CI 등록. **기존 Windows 68/68 PASS; 신규 포함 73개 테스트는 아직 검증 전.** 실제 Meta 요청 구문·권한·응답은 실환경 미검증이며 현재 모듈은 오프라인 요청 명세만 생성함.

> **2026-10-19 p6 정책 보강:** `estimated_time_to_regain_access`를 사용률에서 제외하고 HTTP 2xx라도 Meta `error` 본문이면 저장 없이 레인 중단. `tests/test_three_api_policy_hardening.py` 2개 추가, CI 연결. **기존 Windows 66/66 PASS, 신규 포함 68개는 아직 재검증 전.** 실제 Meta API/토큰/운영 DB 미사용. 실수집 전 HTTP 클라이언트·인증 분리·API 헤더 실제 검증·쿼터 공유 여부 확인 필수.

> **2026-10-19 p6 통합:** `raw_v2/three_api_offline_runner.py`의 `envelope_fixture=True` 모드에서 A/B/C lease→모의 HTTP 응답→사용량 85% 사전 게이트→Meta 정규화→원자적 staging→검증 커서 인계까지 연결. `tests/test_three_api_envelope_runner.py` 4개 신규 및 CI 추가. **직전 56/56 Windows PASS**, 이후 신규 6+4개를 포함한 **66개 테스트는 재검증 전**. 실제 Meta API/운영 DB/토큰 미사용. 주의: 기존 `meta_fixture=False, envelope_fixture=False`는 오프라인 회귀용으로 사용량 헤더 게이트 없이 합성 데이터 저장하므로 실제 수집 경로로 절대 사용 금지.

> **2026-10-19 p6 HTTP 합성 응답 게이트:** `raw_v2/three_api_http_fixture.py`에서 모의 HTTP 상태·사용량 헤더·Meta 미디어 응답을 연결해 **쿼터 확인 후 원자적 staging 저장**하도록 구현했다. HTTP 오류/사용량 미확인/85% 이상이면 레인 쿨다운과 저장 차단. `tests/test_three_api_http_fixture.py` 6개 추가 및 CI 등록. **이전 56/56 Windows PASS 확인, 신규 포함 62개는 아직 재검증 전.** 라이브 네트워크·토큰·운영 DB 미사용. 실제 HTTP 클라이언트와 인증 검증은 미구현.

> **2026-10-19 p6 사용량 정책:** `raw_v2/three_api_usage.py`에서 `X-App-Usage`·`X-Business-Use-Case-Usage` 합성 헤더를 해석하고 사용량 미확인 또는 85% 이상이면 레인별 fail-closed 쿨다운을 적용하는 오프라인 정책을 추가했다. HTTP 429/서버 오류/인증·권한 오류 분류 포함. 신규 테스트 7개와 CI 연결, **전체 56개 테스트는 재검증 전**(사용자가 이전 실행에서 49개 실행 보고, 마지막 `OK` 확인되지 않음). 실제 Meta 호출·운영 DB 변경 없음. 라이브 HTTP 어댑터 및 에러 응답 연동은 미구현.

> **2026-10-19 p6 Meta 응답 정규화:** `raw_v2/three_api_meta_normalizer.py`에 Business Discovery 응답 정규화(허용 필드만 저장, `paging.next` URL 제거, 오류/중복 아닌 유효성 검사)를 구현하고 오프라인 실행기에 `meta_fixture=True`로 연결했다. 신규 테스트 7개 및 CI 등록. 기존 Windows 37/37 PASS, 이후 신규 12개 포함 **49개 재검증 전**. 실제 API 호출·토큰 사용·운영 DB 변경 없음. Meta 실제 응답과의 호환성은 미검증.

> **2026-10-19 p6 추가 구현:** `raw_v2/three_api_offline_runner.py`에서 A/B/C lease → 공식 294계정 intent → 주입된 합성 페이지 응답 → `PageEvidenceStore.commit_staged_page()`를 연결했다. `tests/test_three_api_offline_runner.py`에 오프라인 통합 5개를 추가하고 GitHub Actions에 등록했다. 사용자 Windows에서 기존 37/37 PASS 확인, **새 통합 포함 총 42개는 아직 재검증 전**. Meta 호출·토큰 읽기·운영 DB 변경 없음. 주입식 fetcher는 합성 테스트용이며 live adapter/일일 스케줄러는 미구현. `docs/THREE_API_COLLECTION_IMPLEMENTATION.md` 참조.

> **2026-10-19 p6 최신 상태 (아래 이전 요약보다 우선):** 3-API 오프라인 코디네이터·어댑터·커서 검증·증거 트랜잭션 코드를 GitHub `main`에 구현했다. [설계·실행 게이트](docs/THREE_API_COLLECTION_IMPLEMENTATION.md) · [증거 저장](raw_v2/three_api_evidence.py) · [Coordinator](raw_v2/three_api_coordinator.py) · [오프라인 CI](.github/workflows/three-api-coordinator.yml). 증거 저장 모드에서는 직전 페이지의 유효한 비종료 커서가 있어야 다음 페이지를 배정하고, 증거 없는 단순 완료를 차단한다. 만료 lease·잘못된 커서·중복 receipt의 커밋을 거부한다. **신규 테스트 코드는 작성되고 CI에 연결됐으나 최신 CI 성공은 아직 확인되지 않았다.** 실제 3개 API 수집 실행 경로·스테이징 저장 영수증 검증·일일 front/catchup/history 통합·앵커 연속성은 **미구현/미검증**. API A 기존 인증파일 경로는 사용자 로컬에서 존재 확인(True), B/C 파일은 사용자 바탕화면에 준비됐으나 권한/유효성/독립 quota는 미검증. **운영 `collection.sqlite`와 기존 수집기는 변경 금지; Meta API 실호출 및 운영 전환 금지(별도 승인·pilot 필요).** 코드 커밋 `f6e57ea`, 설계 상태 업데이트 `0a71bcf`.

> **2026-10-19 인수인계 누락 재발 방지:** 기능·설계 문서만 GitHub에 커밋했다고 새 채팅방 인수인계가 완료된 것은 아니다. 변경 시 `HANDOFF.md`와 `STATUS.md` 최상단을 함께 갱신하고, GitHub 최신 `main`에서 두 문서와 원문 링크를 **재조회·검증**한 뒤에만 인수인계 완료라고 보고한다. 완료 보고는 설계 저장 / 인수인계 동기화 / 런타임 구현 / 실수집 검증을 구분한다. 규칙: [AGENTS.md](AGENTS.md) 11~14항.

# 2026-10-19 최우선 인수인계 — 3개 Meta API 운영 설계 / 구현 상태 (신규 채팅방 필독)

> **최신 사용자 결정:** 현재 검증된 단일 API A 수집을 계속 유지한다. API B 토큰은 실제 인증 시 HTTP 401, Meta OAuthException `190/463`(만료)로 확인되어 재발급 대기 중이다. API C는 아직 유효 인증 검증 전이다. **B/C 인증이 확보되면 소규모 권한·실제 수집·독립 quota 테스트 후에만 3-API 전환을 검토한다. 지금 3개 수집기를 실행하지 않는다.**
>
> **설계 원문(반드시 읽기):** [3-API 수집·실행 전 안전 기준](docs/THREE_API_COLLECTION_IMPLEMENTATION.md) / **계획 전용 코드:** [raw_v2/three_api_plan.py](raw_v2/three_api_plan.py). 두 파일은 GitHub `main`에 존재하지만 **라이브 다중 토큰 수집기는 미구현**이다. 계획 출력은 실제 API/DB 호출을 하지 않는다.
>
> **사용자가 제안한 초기 배분(2라운드, 65분 간격 목표, 계정별 페이지당 4개):**
>
> | 라운드 | API A | API B | API C |
> |---|---|---|---|
> | 1 | 공식 1~150번, 페이지 1 (1~4) | 공식 151~294번, 페이지 1 (1~4) | 공식 1~150번, 페이지 2 (5~8) |
> | 2 | 공식 151~294번, 페이지 2 (5~8) | 공식 1~150번, 페이지 3 (9~12) | 공식 151~294번, 페이지 3 (9~12) |
>
> 라운드 3 이후에는 **매일 KST 최신 front 우선 → 누락 catchup → 과거 history**를 유지하고, 가용 API 용량으로 과거 6개월 이상/기존 365일 경계 확보를 지향한다. 위 페이지 숫자는 **초기 배분을 설명하는 논리적 위치**이지 실제 운영에서 고정 오프셋으로 요청해도 된다는 뜻이 아니다. 실제 Meta cursor·게시물 ID 앵커를 계정별로 보존해야 한다.
>
> **API 안전 기준:** lane별 X-App-Usage 관측값 `>=85%` 또는 신뢰 가능한 사용률 헤더 누락 시 해당 lane 후속 API 요청 중단; lane별 영속 쿨타임 및 기존 `ready_at` 보존. 단순히 토큰이 3개라고 사용량 한도가 독립인 것은 아니다. 실제 응답을 통해 앱/할당량 공유 여부 확인 후 실행. **현재 DB에는 공통 슬롯·커서가 있으므로 3개의 기존 `daily_scheduler.py`를 동일 SQLite에서 병렬 실행 금지.** 단일 coordinator, lane별 lease·쿨타임·재시작·멱등성 설계와 오프라인·소규모 실수집 검증이 선행되어야 한다.
>
> **현행 운영은 그대로:** 기존 Python RAW v2.1 `daily_scheduler.py --policy bootstrap-history --live --acknowledge-warnings`와 사용자 Windows 로컬 `%LOCALAPPDATA%\OHORA_RAW_v2\collection.sqlite`의 수집 상태·cursor·백업을 수정하거나 초기화하지 말 것. WARN을 PASS로 표기하지 말 것. DB·인증·Meta live 테스트는 사용자 승인 없이 실행하지 말 것.
>
> **후속 작업:** (1) B/C 인증 새 토큰 발급·유효기간 확인 (2) 각 인증 조회/Business Discovery 권한 검증 (3) X-App-Usage 독립성 검증 (4) 안전한 멀티-lane coordinator 구현·오프라인 테스트 (5) 별도 시험 DB 소규모 live pilot (6) 승인 후 본 수집 전환. **기존 계획 문서만 보고 '3-API 구현 완료'라고 말하지 말 것.**
>
---

> **2026-10-19 계정별 수집 현황표 추가:** 독립 읽기 전용 `raw_v2/account_collection_status.py`와 합성 테스트를 추가했다. 운영 우선순위는 KST 일일 front → catchup → 기존 커서 history, 6개월 이상은 목표이며 365일 고정 완료 정책은 변경하지 않았다. 계정별 고유 게시물 날짜 범위, 마지막 성공 front, KST 오늘 조회, catchup, history 커서 증거를 CSV/MD로 출력한다. 자세한 실행법·필드 정의: [계정별 수집 현황표](docs/RAW_V2_ACCOUNT_COLLECTION_STATUS.md). **운영 DB 실검증은 아직 수행하지 않았다.**\n\n> **2026-10-19 최신 로컬 운영 기록:** 사용자가 Windows에서 10계정 History 실수집(10/10 HTTP 200, 신규 40) 및 직후 audit **PASS** 확인. 공식 294계정 중 성공 286·조회 실패 8, 누적 고유 게시물 1,330·관측 1,350, integrity/observation/coverage 0/0/0, 미해결 배치 0. **무인 운영 미승인, 실제 365일 전량 완전성 미검증.** GitHub 문서는 사용자 로컬 DB를 대신하지 않는다.
>
> **먼저 읽을 문서:** [전체 장애·해결 기록](docs/RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md) · [오류 분류/발견 시 대응 SOP](docs/RAW_V2_ERROR_HANDLING_RUNBOOK.md) · [향후 작업 및 인수 기준](docs/RAW_V2_NEXT_TASKS_AND_ACCEPTANCE.md) · [실행 가이드](raw_v2/README.md). 기존 날짜별 60/70분 또는 이전 게시물 개수는 당시의 과거 스냅샷이다.

## 2026-10-19 RAW v2.1 검수 완료 — 아래 과거 기록보다 우선

- 기준 HEAD `500523b1c18e11863c025c0418f84d6be00e80b4`, 시작 브랜치 main, 미커밋 변경 없음. 검수 브랜치 `codex/raw-v2-safety-review`에서 확인된 잔여 결함만 수정.
- Windows Python 3.11: `python .\tests\run_offline.py` **93/93 PASS**. 러너에서 외부 소켓 연결 차단. PowerShell 구문 검사·Python 컴파일·공식 294계정 dry run 통과.
- 전체 rooted history 커서 증거와 원본 대조, 완료+NULL 커서 재검증 대상 선택, history-only 당일 최신화 선행 조건, 복구의 전체 백업/상태 검증 및 중단 경계 수정.
- 공통 대기 정책: `max(슬롯 종료+63분, 슬롯 시작+65분, 저장된 ready_at)`. 기존 ready_at을 덮어쓰거나 앞당기지 않음. 65분 종료 후 대기는 채택하지 않음.
- 운영 DB 읽기 전용 진단 **WARN**: 294계정, 관측263계정, 게시물1,178, 미디어3,152, 관측1,178. 무결성/관측 오류0, 미검증 완료10. 완료+NULL 커서는 실제 DB0건이며 합성 회귀로 검증.
- SQLite backup API 복제본에서만 호환 마이그레이션 시험: 기존13개 테이블의 원래 행 변경/누락0, 무결성ok, 원본 DB SHA-256 전후동일. 운영 DB 복구·Meta 호출 없음. 실제 API/장기 운영/1년 완전성은 미검증, **제한된 시험만 가능**.
- 실행·중단 조건·롤백: [raw_v2/README.md](raw_v2/README.md). 결함·검증·남은 제한: [검수 보고서](docs/RAW_V2_REVIEW_2026-10-19.md). project-book 등록 경로/대상은 확인되지 않아 갱신하지 않음.

---

# OHORA Trend Catcher — current implementation status

> **최신 기준(2026-10-19):** 아래 장의 이전 60분·70%·70분 시작 간격 등은 과거 기록이며 새 daily-first 정책은 다음 최신 항목을 따른다.

## 2026-10-19 최신 운영 정책 — DAILY-FIRST (아래 과거 기록보다 우선)

**권장 엔트리:** `raw_v2/daily_scheduler.py`. 기존 `collector.py`는 수집 엔진으로 계속 사용하며,
**사용자 로컬 `collection.sqlite`와 수집·커서·관측 이력은 삭제하거나 초기화하지 않는다.**

- **1순위** KST 오늘 공식 목록 1~150번의 최신 최대 4개, **2순위** 151~294번의 최신 최대 4개. 성공한 계정은 오늘 다시 최신 조회하지 않으며, 기존 수집기의 오늘 성공한 `front` 요청 로그도 반영한다. 한도/오류로 슬롯이 일찍 끝나면 미완료부터 재개한다.
- **3순위** 전체 정상 대상 최신화 후 `catchup` 누락 구간 회복. **4순위** `backfill_cursor` 기준 과거 365일 미완료 계정 수집. 접근 불가/일일 재시도 소진 계정은 별도 예외로 보고하고, catchup/history 반복 실패는 계정별·일별 최대 2회 후 다음 날 재시도하며, 294/294 성공이라고 표시하지 않는다.
- Graph 배치당 5 하위요청, 최대 150 **서로 다른** 계정/슬롯, Meta **X-App-Usage** 제공 수치 중 최댓값 85% 도달 또는 사용량 헤더 누락 시 후속 배치를 중단. 사용량은 요청 **후** 확인하므로 초과 가능하고, 63분 경과가 실제 API 한도 회복을 보증하지 않는다.
- 슬롯 종료 `finished_at + 63분` 뒤 재개. **기존 `slots.ready_at`은 소급 변경하지 않는다.** 신규 스케줄러는 이 DB 시각 하나로 전경 대기한다.
- DB에 `daily_account_state`·`catchup_jobs` 테이블을 호환 추가한다. 새 요청 ID에 `run_id`를 포함해 실행 간 충돌을 방지하고, 성공 게시물·관측·커서·로그·슬롯·일일 상태를 하나의 트랜잭션으로 기록한다. 이전부터 있던 catchup이 새로운 최신 조회 때문에 사라지지 않도록 대기열에 보관한다.
- **검증 범위:** GitHub Actions Python 3.11 오프라인 38/38 PASS. 기존 Windows DB를 사용한 마이그레이션·Meta 실호출·실제 1년 완전성은 **미검증**이다. 별도 Windows 예약 작업·서비스는 설치하지 않았다.

### Windows PowerShell 실행

```powershell
git pull origin main
python -m unittest discover -s tests -p 'test_*.py' -v
$src = 'C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\2차 프로젝트 데이터\account_list.csv'
$envFile = 'C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\2차 프로젝트 홍주님께 제공할 수집데이터\kq3_294_collection\.env.local'
$out = Join-Path $env:LOCALAPPDATA 'OHORA_RAW_v2'
Test-Path $src
Test-Path $envFile
python .\raw_v2\daily_scheduler.py --source $src --output-dir $out
# 실수집을 시작하기로 결정한 경우에만 아래 명령을 직접 실행:
python .\raw_v2\daily_scheduler.py --source $src --env-file $envFile --output-dir $out --live
```

- `--live` 없이 실행하면 공식 목록 검증 및 정책 출력만 하며 DB/Meta를 변경하지 않는다.
- `--live`는 전경에서 **계속 동작**하며 63분 쿨타임 동안 대기한다. 창을 열어두고 PC가 켜져 있어야 하며, Ctrl+C로 안전하게 중단한다.
- `--once --live`는 요청된 수집 실행을 최대 한 번 수행하고 대기하지 않는다. 사전에 API 사용량/인증 상태를 확인한다.
- 최초 실수집 전 `collection.sqlite`를 **일관된 SQLite 백업**으로 저장할 것. 과거 이력의 실제 이상 여부는 로컬 DB로 별도 검사해야 한다.
- 예전 `run_70m_loop.ps1`은 남겨둔 **legacy balanced 모드**이므로 일일 최신화 정책 운영에는 사용하지 않는다.


---


## 2026-10-19 — user-selected 4 posts × 150 accounts (next API window, NOT yet live-tested)

- User tested larger pages (15–25) earlier and prioritizes **150 distinct accounts per collection slot**, so a 150+144-slot scheme can cover the official 294-account first sweep if API headroom allows; 4 attempted accounts already returned Meta 110 / 2207013 and must remain reviewable, not silently deleted. The first 130 attempts already exist, with 630 cumulative unique posts.
- Live run after 5-account pilot: 125 account requests, 121 successes, 4 Invalid user id errors, 605 newly saved posts, cumulative 630, total_time_max 76%, stopped safely at configured 70% threshold. Stop after 125 does NOT imply 150 calls will fit if page size is 4; requires actual trial.
- New code sets CLI default `--limit 4` (5 remains allowed for rollback); 150 unique accounts per slot still enforced; 4 posts requested per account; current/latest front and historical backfill cursor independent; history uses persisted `backfill_cursor` and pages of 4, not a date filter. Old 5-post data preserved in existing v2.1 SQLite; no DB reset or schema version breaking change.
- Fixed `backfill_anchor_utc` persisted in config: for existing DB choose earliest prior recorded run; age threshold no longer slides with each execution. Guard front-gap when old latest ID absent, treat terminal 4-post history pages correctly and one-page accounts as done; preserve atomic posts+cursor transaction. This does NOT prove actual Meta chronological completeness; cursor time and history live navigation remain unverified.
- Run logging records actual requested limit=4 in new CSV, and 4-mode synthetic tests cover legacy 5→4 + front/history independence, terminal pages, frozen cutoff, no duplicate posts, SHA verification. **GitHub Actions Python 3.11 offline 28/28 PASS** at `aa440d7e64511d9347b6ec7e8ccc246fb474b53e` (run `39140956504`). More recent documentation/PowerShell only changes not part of that test run.
- `run_70m_loop.ps1`: `--limit 4`; when manually opted in, wait at least **60 minutes after finish** and at least **70 minutes start-to-start**; no Windows scheduled task installed. Next step: user PowerShell `git pull origin main`, offline `python -m unittest discover -s tests -p "test_raw_v2.py" -v`, then when cooldown ends explicit local 4×150 `--limit 4 --batch-size 5 --max-batches 30 --stop-usage 70 --live`, inspect runs and Meta dashboard. Do NOT call API on developer machine or set recurring user schedule without confirmation.

## 2026-10-18 late — Real 5-account validation and 150-account/60-minute policy restoration

- Windows Python 3.11: after CP949 read and SQLite test cleanup fixes, offline test 20/20 PASS (user log). User's local live pilot run `20261018T143306Z_2840`: 5/5 requests HTTP 200, 25 new posts, 25 observations, SHA/SQLite integrity PASS. `like_count` absent on 6/25 posts, all VIDEO/REELS (2 KR_001, 1 KR_003, 3 KR_004); cause still unproven; `usage_max=0.0` observed, NOT equivalent to zero request cost.
- User corrected cadence: **up to 150 distinct accounts continuously per slot, max 5 posts per account; wait 60 minutes after slot finishes** (or safely halts). ~70-minute start-to-start interval is only an estimate if collection lasts ~10 minutes. NOT a blanket 70-minute wait after a 5-account pilot.
- Implemented `slots` and `slot_accounts` bookkeeping in isolated cumulative SQLite; one partial run can be immediately continued until 150 accounts requested. Previous 5-account pilot is automatically adopted into the new slot if recent (<70 minutes) so the next run can do remaining 145. On API halt or reaching 150, cool down for 60 minutes after finish; skip until then; preserve original per-account/page checkpoints.
- Updated Windows opt-in loop to 30 batches × 5 accounts by default and 60-minute post-finish sleep; default local output under LOCALAPPDATA. Code/runner/docs/tests pushed to main. **Linux Python 3.13.5 synthetic 22/22 unit tests PASS** incl. 5→150, old-pilot adoption, slot cooldown. GitHub Actions/Windows revised tests and actual 150-account API run **NOT YET VERIFIED**.
- Next from user's existing Git repository root: `git pull origin main`, `python -m unittest discover -s tests -p "test_raw_v2.py" -v`, then explicit `--max-batches 30 --batch-size 5 --live` to resume when ready and observe actual `X-App-Usage`. Do NOT reset/move existing `%LOCALAPPDATA%\\OHORA_RAW_v2` DB, and do not commit credentials or official source CSV.

## 2026-10-18 — RAW v2 implementation, offline verified; Meta live not yet tested

- **Current approved direction:** 294 official accounts, account/page resume, 7-day client-side logical periods, maximum 5 media per account API page, 70-minute minimum start interval, 365 days (option 730) historical coverage, repeated likes/comments/followers observations.
- **Implemented**: raw_v2/collector.py, raw_v2/verify.py, raw_v2/run_70m_loop.ps1, raw_v2/README.md, docs/RAW_V2_SCHEMA.md, docs/OHORA_RAW_COLLECTION_EXECUTION.md (uploaded source retained), tests/test_raw_v2.py, .github/workflows/raw-v2-tests.yml. Added new-data ignore rule and README links; legacy collectors and databases untouched.
- **API cost design**: requesting both current and historical pages every cycle may double subrequests; chosen default uses at most one page per account per pass, first/newly due latest approximately every 24 hours, otherwise historical cursor. This trades per-cycle new post count for API budget. It is not guaranteed to deliver 5 unique posts every 70 minutes.
- **Data and diagnosis**: separate SQLite, run-scoped sanitized JSON/CSV/hash manifest, id-based post dedupe, immutable first-seen content, historical observations of counts, original source copy, status of incomplete carousels, field returned/null/absent/not_tested, like_missing_audit. Missing likes from a successful response remain an unresolved provider cause until controlled live test.
- **Locally verified:** Linux Python 3.13.5 20/20 synthetic unit tests PASS, py_compile collector/verify/tests PASS. Tested mocked batches/errors, missing/zero values, long IDs, Korean/unicode, repeat measurements, pagination catchup, safe quota stop, DB checksum/foreign keys, per-process lock.
- **Not verified:** real Meta token/permission/API field support, nested cursor across two real pages, independent quota, Windows PowerShell loop, GitHub Actions run status, actual missing-like root cause. No real API requests, image file downloads, Windows schedule activation or Vercel deploy.
- **Known limitations:** children extra pagination is logged partial, but not fully fetched. The 7-day range is a client-side grouping, not an assumed Meta since/until server filter. Real 1-year completeness cannot be asserted from the current data.
- **Next**: on user's Windows, pull main, offline tests, no-network dry run, check source and secure token paths, perform one approved max-batches=1 / batch-size=5 live pilot with quota headroom. Inspect field_support.csv, like_missing_audit.csv, raw JSON, manifest and verifier before increasing budget or starting user-opted 70-minute loop.

## 2026-10-18 — Planning-only long-term RAW/data asset vision (NO EXECUTION)

- User's current direction: official 294 accounts (KR100/US101/JP93), each **latest 50 posts as the initial target** (up to 14,700 if accessible), then maintain new posts while independently extending historical coverage to 1 year and ideally 2 years. This is an **aspirational target, not a collected result**.
- Future dashboard *concept*, not frozen screen specification: **tab 1** RAW explorer (period/sort/filter), **tab 2** weighted trend rankings, **tab 3** AI natural-language DB search/sort/tables/charts, **tab 4** collection coverage/attempt/success/failure/oldest-newest/status observability.
- Recommended separation under discussion: front refresh, older cursor backfill, engagement/account metric observation history; immutable per-run RAW JSON/CSV + cumulative SQLite, separate derived analysis/AI. RAW fields and source distinctions follow user-provided 2026-10-18 proposal where verified.
- **Saved design record only:** `docs/LONG_TERM_RAW_COLLECTION_VISION_2026-10-18.md`, linked from README. No implementation, unit tests, Meta live calls, scheduler activation, collection or data migration for this new plan. The document `OHORA_RAW_COLLECTION_EXECUTION(1).md` was supplied in chat and informed this discussion; it is *not* a separate executable approval. User explicitly asked to record the goals and pause for further discussion.
- Existing `experiments/rolling_backfill.py` stops at 50 and lacks continuing newest-post synchronization; its current behavior and existing collection stores **unchanged**. Existing app B credential expiry, 2-app independent budget, Graph cursor/API field support, completeness and image URL retention are **unresolved**.
- Next: listen to user's additional requirements; discuss architecture and approval boundaries first. **Do not start coding/API collection simply because this planning record is committed.**

## 2026-10-16 — Two-app five-post ninety-minute historical backfill coded

- Updated decision: **5 posts per account × 150 account app A + 144 account app B**, 90-minute recurring **historical cursor backfill**, goal **50 unique posts/account** in ideal 10 cycles, not repeated newest-five scans. 900 minutes ≈ 15 hours of cycle cadence; for ten start ticks there are nine 90-min gaps (13.5h between first and last starts). Two ~9h working days possible only if 150/144 accounts fully processed each tick, valid media cursors/permissions and quota recovery.
- **New GitHub code** `experiments/rolling_backfill.py` (append-only posts/media via unique IDs, read Graph `media.paging.cursors.after`, next page media.limit(5).after(cursor), stops at 50 or exhausted, blocks invalid/missing cursor, fair least-pages prioritization, per-batch usage logs, fixed A/B partitions, per-slot dedupe, 90m start/60m finish cooldown, 70% quota threshold, 30-minute max). Existing original collection experiments are NEVER modified.
- **New GitHub code** `experiments/run_90m_cycle.py` one-shot approved business-hour dispatcher, explicitly OFF by default, requires two distinct authorized Meta app IDs/tokens/local credential files and business-hour window; `experiments/backfill_schedule.example.json` kept disabled and `.gitignore` excludes local config.
- **New OFFLINE tests** `experiments/test_rolling_backfill.py` (10 cases) + `experiments/test_run_90m_cycle.py` (7 cases). User Windows run NOT YET DONE; do not claim passing tests until logs confirm. One-account two-cycle live pagination probe also NOT performed. Manual probe `--probe-ordinal 1` must be tested 90m apart in a NEW directory, separate from prior DBs.
- Hongju original token expired and caller Instagram ID unavailable; app B CANNOT be enabled yet. No n8n/Windows actual 90-minute schedule created or turned on. Real two-app rate quota independence remains unverified. Detailed instructions `experiments/README_CURSOR_BACKFILL_90M.md`.
- Previous Meta latest5+latest1+latest10 collection was reported **286 of 294 success, 8 Meta110 errors** in user screenshot; raw data in separate local SQLite. Those results are not yet merged into new history-backfill DB or delivery ZIP.


## 2026-10-16 — Slot2 151..294 prepared with read-only cross-source skip

- User has their own valid local Meta token/IG ID but Hongju's earlier token was confirmed expired. User proposes reusing their own token/IG ID temporarily in two logical slots, then replacing slot2 with Hongju credentials after renewal. **Two logical slots with one token are NOT two independent API budgets**; do not perform parallel runs or assume exactly 60-minute reset.
- Implemented repeatable `--skip-collected-from` read-only snapshot filters and combined it with `--only-pending-from` and existing `--start-ordinal 151 --end-ordinal 294 --assigned-app owner_slot2_probe`. Both prior 5-post + 1-post successful/partial statuses excluded; original 5-post failed statuses also excluded by pending-only gate; own destination DB already marks completed accounts and naturally resumes without re-request.
- From user reports, predicted 144 candidate ordinals in slot2, 30 already complete +1 Meta110 failed in original, 15 complete in supplemental, therefore **98 eligible**, starting at ordinal **197**; local dry-run is authority.
- `--posts 10` in brand-new `experiments/results/latest_10_owner_slot2_151_294/` DB; own original `$envFile` temporarily used. No credentials committed and no live Meta requests made by assistant. Model only updated GitHub source and tests. Dry run first, expected **22** total offline test cases; user Windows test pending.
- Small live pilot only AFTER Meta dashboard indicates sufficient recovery, `--batch-size 10 --max-batches 1 --stop-usage 70 --require-usage`; quota threshold evaluated AFTER request. If tested and approved, rerun sans max batches, stopping on actual rate limit. `--only-pending-from` and `--skip-collected-from` flags must remain on every invocation.
- Full instructions in `experiments/README_SLOT2_151_294.md`. Later Hongju auth must use separate credential file and output DB for provenance; 3-way delivery ZIP merging remains unimplemented.


## 2026-10-16 — Two authorized Meta apps manual 10-post collector implemented (Windows acceptance pending)

- New `experiments/two_app_runner.py` assigns original rows 1–150 to haein and 151–294 to hongju (current 294, 6 spare seats before 300), configurable `--capacity 150`, `--posts 10`, `--app NAME=ENV`, per-app input credential files and separate results. Does NOT implement scheduling or automated retries.
- `experiments/full_batch_collector.py` now accepts `--start-ordinal/--end-ordinal/--assigned-app/--require-usage`; account range and app label are locked into each database manifest; per-app quota headers must be present, stop-usage default 70% in outer runner (evaluated post-batch). Supports append-only future CSV growth while refusing reordering or mutation of earlier accounts. Maintains 294-row fixed official prefix.
- `--only-app hongju` / `--only-app haein` supports separate manual resumes when one app is paused, without crossing allocated ranges. Credential preflight rejects duplicate tokens, missing keys and duplicate declared app IDs; no token values logged. Declared app IDs are not proof of real Meta independent quota. Use only properly authorized apps/access.
- New `experiments/test_two_app_runner.py` adds 14 fully offline tests to existing collector's 19 and delivery merge's 15: **48 expected**. GitHub workflow `.github/workflows/offline-collector-tests.yml` created (run status not confirmed by connector). **Do not claim tests passed yet**; user's Windows run required.
- Full Windows PowerShell instructions in `experiments/README_TWO_APP_LATEST10.md`: update, tests, locally create Hongju env file, dry-run, app-by-app 10-account quota pilot, only then manually run pending 150/144 and create combined handoff ZIP with `delivery/build_hongju_bundle.py --supplement`. 5-minute completion and independent 2x quota are not guaranteed.
- Existing 5-post/1-post data stores untouched; no assistant-run Meta API calls and no app credentials accessed.


## 2026-10-16 — User proposes 10-post, 150/144 authorized two-app division

- User revised optimization hypothesis: each Business Discovery username is one Graph subrequest even if that request returns 10 posts; propose ~150 accounts per Meta app across **two separate, properly authorized apps** belonging to user and Hongju. Official file has 294 accounts, so use 150 + 144.
- Do NOT equate 10 Graph batch operations in one outer HTTP request with one rate-limit call: they count as 10 subrequests, with processing total_time/call_count tracked by app/token, and possible user scope limits. Do NOT assume two personal logins guarantee independent app quota or treat extra apps as a workaround for rate limiting.
- Still **unverified**: Meta 10-post total_time per account, Hongju app/token and permissions, app-level quota independence and 150/144 throughput. Need at most one small authorized 10×10 trial on recovered quota for each permitted independent app; protect at most 70% usage headroom, stop on code4.
- Status of Hongju ZIP helper: original standalone 9/9 tests passed on Windows. New dual-source merge code supports five-post **primary precedence on account overlap**, skips prior one-post row duplicates, preserves earlier one-post successful data if five-post retry failed, and records source/overlap counts; 15 offline tests added but **NOT YET rerun on user's Windows**. Read-only source files. GitHub changes committed, no real Meta request or local ZIP created by assistant.
- User now prioritizes idea over previous paired 10×5 test; do not auto-run any live test or change confirmed collection settings until the new design is validated.


## 2026-10-16 — 1-post 10-account comparison; five-post follow-up prepared

- User Windows pending-only batch 2 (original ordinals 187..196) succeeded **10/10**, response 1.859s, Meta app total_time moved **75% to 79%**, call_count=43%, resulting in 15/113 supplemental successes. Original 5-post database remains 176 successes/880 posts, original errors 5 (110), original pending 113. Combined coverage 191 unique accounts, 895 posts; 98 distinct official accounts remain uncollected.
- User now requests **one 10-account × 5-post** comparison against 10×1, and expects to schedule batches with rest windows later. This run must use the existing original 5-post output directory, one batch only (`--max-batches 1`, `--batch-size 10`, `--posts 5`), with current app usage ~79%. Its likely next account ordinals are 182..191. No assistant-side live Meta calls; the Windows user must run it.
- WARNING: 182..191 were already fetched by the one-post experiment. Merely concatenating original and supplement would duplicate accounts and posts. Handoff ZIP merge now **prefers five-post primary on whole-account overlaps**, keeps any nonoverlapping one-post accounts, validates account roster/IDs, and reports skipped/added counts. Source SQLite untouched. 15 offline delivery tests authored; user's Windows run pending.
- If all requested 10 original accounts succeed with five posts, expected combined data after merge: 5-post primary **186 accounts/930 posts**, supplemental net **5 accounts/5 posts**, combined **191 accounts/935 posts**. If failures occur, recompute using actual DB counts. Existing handoff ZIP remains its earlier immutable 175-account snapshot.
- Observed 10×1 app total_time change of +4 percentage points is NOT a controlled measurement of fixed cost. 10×5 may have a different rolling-window state. Cooling 50–60 minutes may not restore quota; confirm recent app usage before each separate run.


## 2026-10-16 — 1-post pending-only pilot tested on Windows

- User's Windows run: **20/20 offline tests passed** for pending-only batch collector.
- Live request on original pending account ordinals **182..186** (5 accounts) with `--posts 1 --batch-size 5 --max-batches 1` succeeded: **HTTP 200, success=5, failed=0, 5 posts / 7 media, 0.703s**.
- Meta response snapshot: `total_time=75.0%`, `call_count=41.0%`, `total_cputime=0.0%`. Previous dashboard 75.8%; lower snapshot does **not** imply zero or negative incremental API cost; rolling-window decay can offset additional requests.
- Current combined distinct account statuses across two isolated stores: old `latest_5`: 176 success, 5 failed code 110, original 113 pending; new restricted `latest_1_pending`: 5 success (all from those 113), **108 remain pending**. Overall 181 covered accounts/885 post records (880+5); no merged delivery ZIP yet.
- Next: controlled 10-account 1-post batch (original status allowlist+new own checkpoint) with `--max-batches 1 --stop-usage 85`; reassess time and call-count usage before larger run. Ensure old `latest_5` records remain untouched. `--stop-usage` evaluated AFTER batch, not a preventative API gate.


## 2026-10-16 — Pending-only latest1 Meta quota experiment prepared (user run pending)

- User's Meta dashboard displayed app total_time 75.8% and prior latest5 database contains **176 success, 5 failed (Meta 110), 113 pending**.
- New read-only source filter --only-pending-from added to experiments/full_batch_collector.py: selects original pending/rate_limited accounts only, while storing one-post results in **independent** experiments/results/latest_1_pending. Old 880 post rows remain intact; schema/official-list hash verified, mismatch refused.
- Added three offline unit tests for pending-only targeting, source mismatches and database path collision (19 tests expected; **Windows retest pending**). No live API request was made by assistant.
- Documented cautious **5-account pilot**, --posts 1 --batch-size 5 --max-batches 1 --stop-usage 85, in experiments/README_ONE_POST_PENDING_PROBE.md. Never auto-run remaining 113 until usage delta measured.
- Hypothetical threshold: starting 75.8%, all remaining 113 require <=0.214 percentage points/account before a 100% limit, assuming no quota recovery. Observed moving app-usage rates cannot identify fixed/per-post cost from a single pilot.


## 2026-10-16 — 294-account latest-5 collection stopped safely at usage 72%

- User Windows resumed `experiments/full_batch_collector.py --posts 5 --batch-size 10 --stop-usage 70` in `experiments/results/latest_5`. Last shown batch 19 (account ordinals 171..180) completed with HTTP 200 and usage **72%**; collector stopped per configured safety threshold.
- **Verified from shown console log:** 175 accounts `success`, 114 `pending`, 5 `failed` (individual Meta error `110` / HTTP 400); sum 294, zero `rate_limited` accounts shown. At most 875 successful-account posts, **actual total posts must be read from `summary.json`**. Existing successes saved transactionally; failed account statuses preserved.
- Meta code `110` commonly corresponds to invalid/not found target user ID on Business Discovery, but this is **not confirmed for these five accounts**; inspect `collection_status.csv` `error_message` and verify usernames/professional eligibility before considering any retry. The collector only resumes pending/rate_limited statuses; it does not auto-retry the 5 failed accounts.
- **Do not immediately restart at 72%.** Wait for documented usage headroom / rolling quota recovery. Keep `--stop-usage 70` and resume with same source/env/output once headroom is demonstrated. A 60-minute wall clock delay is not proof of quota reset.
- Friday deadline: while waiting, test delivery ZIP exporter (Windows SQLite closing fix pending user retest) and generate partial handoff ZIP from latest_5 with actual country/account coverage. If further collection becomes available before handoff, rebuild ZIP once final collection is done. Do not mistake 175/294 for complete official coverage.


## 2026-10-16 — Collector blocked by missing local Meta env; delivery test Windows lock fixed

- User confirmed official 294-account CSV path exists (`Test-Path $src=True`), but suggested local `kq3_294_collection/.env.local` does **not** exist (`False`). The collector therefore cannot authenticate without locating another secure env file or configuring `META_ACCESS_TOKEN`, `META_IG_USER_ID` and optional graph version locally.
- User ran Friday handoff test: `Ran 9 tests ... FAILED (errors=1)` on Windows with `PermissionError [WinError 32]` for the temporary `experiment.sqlite` file. Cause: `with sqlite3.connect(...)` is a transaction manager but not an explicit connection closer. Fixed **both** fixture and exporter with `contextlib.closing` in commits `5602e192adda3488f5ae16a4e2597c24815d2207` and `2b61b77b81ef03a304a454ba95c0b2557535f813`. Windows user retest pending.
- Urgent next action: find existing .env.local by **file path only** and run one guarded latest-5 batch, then resume remaining batches when safe. Do not paste API token. Delivery tests can be rerun before packaging after collection; no need to block Meta collector on unrelated ZIP test failure.
- No new Meta run was performed by assistant; real API collection must run on user's Windows PC.


## 2026-10-16 — P-1 Friday Hongju delivery prioritized over P1

- Deadline-driven priority: send analysis-ready Instagram collected data to Hongju during Friday classes before building incremental P1.
- Added delivery/build_hongju_bundle.py: **offline only**, read-only SQLite or CSV source, creates timestamped ZIP with both all available posts and most recent 5 per account, media, account coverage, dataset manifest and instructions. No source mutation, credential collection or image model call. Refuses empty datasets. Protects against spreadsheet formula injection in handoff copy.
- Added 9 offline tests in delivery/test_build_hongju_bundle.py (still **pending user Windows execution**), and delivery/README.md handoff runbook.
- Existing 294 latest-5 collection is not known to have succeeded (Meta code4 exhausted usage); preferred existing fallback legacy snapshot is ~94 covered accounts/1,880 posts, and another experimental snapshot ~77 covered accounts/1,540 posts. Exact current counts must come from manifest, not assumptions.
- Next user action after rest: git pull; run `python -m unittest discover -s delivery -p test_build_hongju_bundle.py -v`, select valid data source; generate ZIP; confirm coverage and deliver. If the Meta app actually recovered, optionally test one latest-5 batch before expanding; never let the full 294 goal prevent same-day partial handoff.
- P1 latest-refresh, P2 historical expansion and scheduled n8n remain unchanged in architecture but temporarily postponed until handoff.


## 2026-10-16 — P0 verified on user's Windows PC

- **User Windows offline suite:** `Ran 27 tests in 0.806s`, final `OK` (27/27 tests pass) after `git pull origin main`. In-test log messages including `provider_http_403`/`invalid_schema`/`Provider stopped safely` come from deliberately mocked adverse conditions, **not live model calls**.
- **Previously failed real image post:** User's earlier live exact-post rerun reported `attempted=1, success=1, failed=0` for `17994612878841451`, and local CSV row `status=success`, blank error code, model confidence 0.90.
- **P0 completed for functional/incident regression scope only:** one actual error recovered + offline suite all green; **not** proof of accuracy, high-volume reliability, or zero future failures. Do not pay to retry the already successful post.
- **Next P1:** durable append-only/UPSERT DB and recurring latest-5 collector; preserve original existing 20-post result set and make no live Meta calls without user approval and usage headroom checks.


## 2026-10-16 — Vision failed post recovered; one mocked test corrected

- **User-verified live recovery:** `--post-id 17994612878841451 --retry-failed --limit 1 --live` reported `attempted=1, success=1, failed=0`. The exported `post_analysis.csv` row for that exact post is now `status=success`, `error_code` empty, model confidence 0.90, visual summary about the 'Muffler II' gel collection display. This establishes technical recovery, not ground-truth image accuracy.
- **Windows test status:** prior 27-test suite yielded `FAILED (errors=1)` in `test_format_error_retried_once_and_cached`. Its final mock used an untargeted `--limit 1` run; the worker therefore correctly processed a *different* uncached post but received a `MagicMock` result, which `json.dumps` cannot serialize.
- **Fix:** `vision/test_vision_runner.py` now specifies `--post-id P1` for the final cache assertion and verifies that `analyze()` was never called. Production runner unchanged. Code committed as `170c8f3e6f0f7d11261c29f9addfbe684f375899`.
- **Next:** `git pull origin main` and `python -m unittest discover -s vision -p test_vision_runner.py -v`. Target: 27/27 tests OK. No further paid API retry is required for this recovered post; do not run the live retry again.


## 2026-10-16 — Target exact failed image post before proceeding to P1

- User correctly flagged that **one live `invalid_json` error remains unresolved**. A code patch for malformed JSON is not equivalent to successfully reclassifying the failed post.
- Discovered safety bug in prior CLI advice: `--retry-failed --limit 1` can bill an unrelated, newer *unanalyzed* post instead of the known failed post, because normal selection is newest-first.
- GitHub commits `2d72562296f7efe9578e5c5d5170582558fae86a`, `ceed2256e47874c1fab8aff6dbd36d2eac011ba0`: add `--post-id` to restrict all analysis to the exact post, and two mock regression tests for targeted recovery/unknown IDs. Expected offline suite **27 tests; user Windows execution pending**.
- **Recovery command AFTER offline suite passes:** `python .\\vision\\vision_runner.py --post-id 17994612878841451 --retry-failed --limit 1 --live` (uses local MLAPI key, may make up to two paid calls if malformed output persists). Do NOT claim the error is solved until the post row becomes `status=success` and is visually reviewed.
- Leave previous 20 cached successes untouched. Pause P1 until P0 user live retest verified.


## 2026-10-16 — V1 settings formally locked (design only)

- User confirmed **latest 5**, **backfill 5**, **50 unique posts/account target**, **max 30-minute collection session**, **>=60-minute cooldown after finish**, **>=90-minute gap between starts**, and **GPT-6.1 Sol API insights weekly**.
- Delivery roadmap: [docs/V1_DELIVERY_ROADMAP.md](docs/V1_DELIVERY_ROADMAP.md). Operating windows and Sol vendor API model/reasoning parameters are **not specified/verified yet**; no n8n scheduler or weekly AI job is active.
- Development: user + ChatGPT directly; Gemini 3.8 Flash API for image/caption tags; GPT-6.1 Sol separate paid API in n8n for cached weekly narrative interpretations, NOT the ChatGPT model or Codex. No Codex/Luna EH.
- Current phase: offline 25-test vision retest + single malformed-output retry validation, then durable post-ID-based upsert/latest refresh, backfill and analysis queue, DB-only dashboard, weekly Sol and n8n schedule. Existing initial 5-post collector alone **cannot** repeatedly refresh without replacing or skipping successful account data.


## 2026-10-16 — vision JSON safety improvements (Windows + live retest pending)

- Observed user live paid MLAPI vision results: 21 post attempts, 20 success and 1 `invalid_json` (post ID stored locally); image-and-caption model service works, but output reliability needs improvement. This is a small sample, not a measured production failure rate.
- Code commit `d4de3e4f6d7abbb53c94b1dde20ec71771365641`: recover a SINGLE complete JSON object when prose surrounds it; never fabricate truncated JSON. Detect model finish_reason length; allow exactly ONE extra model call on limited schema/format errors with larger max_tokens; no auto retry on 401/403/429 or transport faults; preserve cached successes and failed rows.
- Test commit `d253ee456b9079cf37d85804f5072f65b306afa6`: 4 new offline tests for wrapped/duplicate/truncated JSON and bounded retry, and updated old two-invalid-output expectations. **25 total tests must be rerun on user's Windows PC; not yet verified after the new patch.**
- Next user actions: `git pull origin main`, `python -m unittest discover -s vision -p test_vision_runner.py -v`, then one explicit real retry: `python .\vision\vision_runner.py --retry-failed --limit 1 --live`. Only if those pass consider larger analysis in batches.
- Demo safeguard: PRECOMPUTE and store all classification before presentation; dashboard should only read cached success rows, disclose coverage/failures and operate without model/API availability. **Actual dashboard interface remains unimplemented**.


## 2026-10-16 — Image+caption vision pipeline ready for local provider validation

**Code added:**
- `vision/vision_runner.py`: read-only import from full_294/latest_5 SQLite or original posts/media CSV; select representative image/carousel/video thumbnail plus caption; schema-validated `is_design_post` and color/technique/texture/motif/season/use labels; one SQLite transaction per post; CSV exports and caching; manual provider switch; safe stop on auth/rate/format errors; explicit paid `--live` only.
- `vision/test_vision_runner.py`: **21/21 offline tests passed** in the development environment. No external provider requests.
- `vision/setup_env.ps1` prompts for endpoint/model/key and writes *only local* gitignored `vision/.env.local`. The actual user-supplied API key is **not** in GitHub, Library or outputs.
- `vision/README.md`: PowerShell morning runbook; run one paid API call only after user reviews provider setup.
- Real legacy source dataset **1,880 posts / 5,307 media records** was parsed offline; mocked model integration processed three posts, exported analysis rows, and SHA-256 of original CSVs was unchanged.
- External `mlapi.run` endpoint authorization, accepted model ID, remote image URL fetch behavior, *actual AI inference quality*, cost, and Windows user run are **not yet validated**. Do not call this completely done.
- Already observed: user's Windows original 16/16 collector tests passed after SQLite closing fix. First live latest-5 Meta test hit code 4 due to lingering 101% usage; no successful new posts from that test.
- Next: user pulls GitHub code, runs vision tests locally, configures fresh API key securely, offline preview from `experiments/results/full_294`, then one small `--live --limit 1` check before any bulk analyses. Collector runs only after Meta usage sufficiently recovers.
- 90-minute n8n scheduling, recurring latest upserts and cursor backfill, dashboard UI remain *separate future work*.


## Windows test fix — 2026-10-16

- User's Windows Python 3.11 run of `test_full_batch_collector.py` reported 16 tests, 2 errors: WinError 32 when `TemporaryDirectory` tried to unlink `collection.sqlite`.
- Root cause in tests: `with sqlite3.connect(...)` commits/rolls back but does **not** close the SQLite connection. Two occurrences replaced by `with contextlib.closing(sqlite3.connect(...))` in GitHub commit `dd375a18bb9b0cdb8354e98ba88cc0b97dab60de`.
- GitHub file content verified after patch; **user's subsequent Windows retest passed 16/16** (observed 2026-10-16 chat log).
- User's `--posts 5` dry run succeeded: 294 accounts, 10 per batch, 30 batches; no API requests or file writes.
- Next: `git pull origin main`; `python -m unittest discover -s experiments -p test_full_batch_collector.py -v`; only proceed to live 5-post acquisition if tests pass and Meta app usage has recovered.


## 2026-10-16 — latest-five acquisition preparation

- **Decision:** Build latest 5 initial acquisition now; postpone hourly-window scheduling, recurring post-ID upsert, older-page backfill, and vision until staged verification. Plan: `docs/INCREMENTAL_V1_ARCHITECTURE.md`.
- **Implemented:** `experiments/full_batch_collector.py` now supports `--posts 1..20`; 20 remains the default. `--posts 5` MUST use `--output-dir experiments/results/latest_5` or another empty isolated output path. Original 20-post database and old collector remain untouched. Resume skips completed accounts; no periodic refresh implemented yet.
- **Verified offline:** 16/16 tests, including 5-post mocked live batch and changed-manifest rejection; actual official 294-row source accepted with dry-run 5 posts/10 accounts x 30 batches, no writes/no calls. This is **not** a live five-post API validation.
- **Observed live prior:** user reported real 20-post full-list run hit HTTP 403 / Meta code 4 at batch 9 with 77 successful accounts (13 failed; 204 unattempted). Do not assume a fixed 60-minute cooldown resets quota; confirm recovery and run a small staged batch before large collection.
- **Next:** after app usage safely recovers, user runs `git pull` and offline test then the 5-post dry run and optionally one explicit live batch for confirmation. Reusable latest-refresh/backfill must be implemented later on a non-destructive upsert schema, followed by image analysis endpoint testing.



Updated: 2026-10-15 KST

## Business scope
- KQ3 is *Key Question 3*, not calendar quarter Q3.
- Official account list: 294 targets, KR 100 / US 101 / JP 93.
- Goal: preserve latest 20 posts/account (up to 5,880) and child media, followed by separate vision classification and DB-backed dashboard queries.

## Confirmed collection history
- Existing original sequential collector succeeded for 94 accounts (1,880 posts, 5,307 media rows) in the previously inspected snapshot, with Meta usage/limit issues. Original raw datasets remain on the user's PC; they are **not** committed.
- User-operated Meta Graph Batch probe: one target in 1.265s; two targets in 2.063s; five targets x10 posts in 2.703s; same five x20 in 5.797s; ten targets x10 in 2.828s. All tested subrequests returned HTTP 200. Usage header percentage readings are **snapshots** and not per-call costs.
- No 294-account live batch collection has been executed or verified yet.

## Code
- `kq3_294_collection/collector.py`: original legacy collector, unchanged.
- `experiments/meta_batch_probe.py`: original stat-only single-batch probe, unchanged.
- `experiments/full_batch_collector.py`: isolated full 294-target batch collector (10/account batch, 20 latest posts/account), SQLite transactions + CSV exports, no automatic retries, recorded usage/errors, resume after interruption and stop upon Meta rate/auth errors. Dry run is default; **only --live sends API requests**.
- `experiments/test_full_batch_collector.py`: 15 network-free unit/integration tests passed during development.
- `experiments/FULL_294_README.md`: exact PowerShell instructions, operational limits, file map, and exit codes.
- A separate fully mocked 294-target run verified 30 batches ending with four accounts, 5,880 unique posts and 8,820 media rows (fabricated fixtures, not real Meta records).

## Decisions / non-goals
- Full experiment intentionally starts at the beginning of the official list, including 94 previously collected accounts, but saves under `experiments/results/full_294/`; **never overwrite legacy CSV data**.
- Default run has no speculative 80% cutoff because the user wants to observe the actual Meta limit. First recognized limit/auth error stops future batches after preserving the successful subresponses. Optional `--stop-usage 80` is available.
- URLs and metadata are stored; image binaries, AI vision tags, n8n automation of this new collector, and frontend/DB dashboard integration are outside this experiment.
- Any runtime incident or errors should be diagnosed from `summary.json`, `batch_log.csv`, and `collection_status.csv`; avoid rerunning same method blindly. Never share real access tokens.

## Next step
User: `git pull origin main`, run the full collector dry run then its explicit `--live` command locally, and share sanitized logs / summary. Assistant: review quota progression and exact stop point, decide whether to retain 10-subrequest batches or optimize further based on real 294-run evidence.
