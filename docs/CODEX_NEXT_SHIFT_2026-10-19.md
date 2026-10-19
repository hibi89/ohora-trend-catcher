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

# P6 — Codex 6 Luna Extra High: 다음 교대 개발 지시서

작성: 2026-10-19 KST
저장소: hibi89/ohora-trend-catcher (main)
기준 기능 커밋: ab547ab3a1679979b554c6aee2c4565f14765344
오프라인 CI: Three API 296/296 PASS https://github.com/hibi89/ohora-trend-catcher/actions/runs/39237519823
RAW v2 394/394 PASS https://github.com/hibi89/ohora-trend-catcher/actions/runs/39237519913
중앙 등록: hibi89/project-book / BOOK.md 의 p6.

## 목표와 작업 범위

**사용자가 수면 중 명시적으로 Codex에 이 지시를 전달한 뒤** 수행할 다음 작업이다. 이 문서가 GitHub에 있다는 사실만으로 자동 실행을 허가하는 것이 아니다.

가장 중요한 목표는 294개 공식 계정을 최신순 수집하고 누락 없이 RAW까지 저장하는 시스템을 실서비스 조건에 가깝게 검증하는 것. 현재 완성된 것은 합성 오프라인 실행 경로이며 실제 서비스 수집은 검증되지 않았다. 새 아키텍처·별도 프로젝트·대시보드 미관 작업을 시작하지 말라.

**P0 — 294/294 합성 배치는 완료: 반복 구현하지 말고 새로운 취약점 검증**
1. 최신 main, AGENTS.md, HANDOFF.md, STATUS.md, REQUIREMENTS.md, docs/THREE_API_COLLECTION_IMPLEMENTATION.md와 다음 소스를 읽어라.
   - raw_v2/three_api_daily_orchestrator.py
   - raw_v2/three_api_daily_gap.py
   - raw_v2/three_api_daily_projection.py
   - raw_v2/three_api_catchup_offline.py
   - raw_v2/three_api_catchup_payload.py
   - raw_v2/three_api_raw_projection.py
   - raw_v2/three_api_raw_mirror.py
   - tests/test_three_api_daily_orchestrator.py
2. **공식 local account_list.csv를 수정·업로드·대체하지 마라.** CI에서는 별도로 만들어진 가상의 294계정 목록만 사용한다.
3. 294계정의 전 계정에 대해 page1→필요 시 page2, page3, 4페이지 이후 gap, 중단/복구를 실제 코드 경로로 실행하는 합성 배치 통합 테스트/검증 하네스를 만들고, 전체 294의 상태·RAW 수·중복 수·보존율을 실제 SQLite에서 조회해 증빙하라. 빠짐없이 294/294를 기계적으로 확인하되 API가 제공하지 않은 필드는 보완 추정하지 마라.
4. A/B/C lane별 할당, 선행 cursor 검증, 85% usage 경계, lease 만료, 65분 cooldown 및 재시작을 각각 합성 테스트한다. 배치가 멈추거나 무한 반복된다면 이유를 기록하고 fail-closed로 종료하라.
5. 기존 **전 계정 front 우선→과거 gap catch-up→history 후보** 정책 준수. 커서 anchor 직접 확인 없이 gap verified 선언 금지. 실수집하지 않은 historical data를 수집했다고 기록하지 마라.

**P1 — 장애·내구성 강화**
1. 소스 page commit과 gap proof 투영 사이, gap proof와 RAW mirror 투영 사이, Catch-up inbox staged/ack 사이, 실행기 재시작 시 끊기는 지점을 테스트하라. 저장된 원본·시각·SHA 증거로 재개하며 API fixture 호출을 불필요하게 반복하지 말 것.
2. 294계정 일부의 fixture 부재, malformed child, 429/usage-block 시 해당 계정과 lane을 명확히 분리하여 나머지 데이터 손실을 방지하라. 단 **global front 우선 정책을 건너뛰거나 블로커를 성공으로 위장하지 마라.**
3. 장시간 테스트 속도 및 SQLite 잦은 재조정의 병목을 정량화하라. 안전이 증명되기 전에는 성능 개선을 위해 트랜잭션이나 멱등 검사 제거 금지.
4. 테스트를 위해 임의로 기존 test expectations만 완화하지 말 것. 명확한 버그와 테스트 구성 오류를 구별해서 수정하라. 같은 방법 2회 실패 시 원인·제약·대안 기술 후 중지.

**P2 — 운영 전환 체크리스트 (코드 준비만 가능)**
1. Meta A/B/C 토큰, IG 사용자 ID, required scopes, 실제 app별 rate quota가 서로 독립인지, Meta API의 children nested fields / paging cursor 지원 여부는 아직 실증되지 않았다. 현장 호출 전 승인 받아야 한다.
2. 사용자 로컬 기존 collection.sqlite와 실제 CSV, 운영 백업·restore 정책이 검증되기 전에는 실운영 마이그레이션 구현을 자동 활성화하지 마라. 기존 RAW collector.py / daily_scheduler.py를 절대 덮어쓰지 말 것.
3. 운영 dry-run 명령과 비밀정보 주입 파일 경로는 실제로 확인된 것만 문서화. 경로·키·실행 성공을 추정하거나 .env.local을 commit 하지 말 것.
4. 실 API 호출/실토큰 로드/운영 DB 쓰기/프로덕션 배포는 **이번 교대에서 금지**. 별도의 사용자 승인 필요.

## 반드시 통과할 검증·납품 기준

- 기본 python -m unittest discover -s tests -p 'test_three_api_daily_orchestrator.py' -v.
- 전체 Three API CI와 RAW v2 python tests/run_offline.py, 기존 보호 테스트 포함 모든 오프라인 회귀 PASS. Python 3.11 compile PASS. 테스트 개수 산정 시 새 테스트와 기존 테스트 중복 합산하지 말 것.
- 294 전체 배치 실험은 실행한 경우에만 정확한 분자/분모, 누락 건수, 계정별 last captured page, RAW 중복률, 처리 시각을 보고. 미실행 시 294 실증으로 표기 금지.
- 최근 합성 proof / RAW SQLite를 제외한 실제 collection.sqlite, 토큰, .env, 사용자 데이터의 변경·로그·GitHub 업로드 0회.
- 신규 소스·테스트·성능 결과·알려진 blocker·다음 1단위와 rollback 경로를 HANDOFF.md와 STATUS.md **양쪽 상단**, 설계 문서에 동시 기재한다.
- GitHub Actions 각 실제 run ID, conclusion, 테스트 수, 검증 source SHA를 확인한 뒤에만 완료 선언한다.
- 중앙 hibi89/project-book/BOOK.md p6 행도 최종 main commit과 CI 결과로 최신화한 다음 모든 항목 재조회.
- 문제를 발견하면 fix+test+docs까지만 수행하되 live 변경 없이 종료하라. 임의로 새로운 기능·대시보드 개발 시작하지 마라.

## 현 시점 구현 상태 및 경고

- 이미 구현: 3개 lane scheduler intent, offline protected front 1~3 paging/staging, gap policy, ID-only legacy guard, full allowlisted synthetic Catch-up payload inbox, stable RAW mirror, nested carousel opt-in, SHA/관측시간/멱등성, 수동 294 roster orchestration.
- 신규 OfflineDailyOrchestrator.step(lane, now=aware_time)는 **수동 synthetic fixture 1페이지 단계 실행**이며 daemon, 실 HTTP dispatcher, 실제 Meta token allocator, 실제 production scheduler가 아니다.
- 이번 교대에서 294 전 계정의 실제 운영 API 수집 검증을 했다는 주장을 절대 하지 말 것. 오프라인 테스트 성공은 live 수집 정확도·quota 독립성의 증거가 아니다.

## Codex 시작 메시지 (복사용)

"p6 저장소 hibi89/ohora-trend-catcher 최신 main을 복원하고 docs/CODEX_NEXT_SHIFT_2026-10-19.md 및 AGENTS.md를 읽어라. P0의 294계정 합성 완전 배치·front→catch-up 우선순위·중단 복구를 최우선으로 자율 구현/실제 오프라인 테스트한 후 CI와 HANDOFF/STATUS/중앙 BOOK 동기화까지 하라. 실 Meta/토큰/운영 collection.sqlite는 절대 사용하지 말고, 실행한 결과와 미검증 사항을 구분하여 보고하라."
