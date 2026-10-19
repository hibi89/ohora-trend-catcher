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

> **2026-10-19 — p6 HTTP 오프라인 사전검증 구현 (후속):**
> - [`three_api_http_preflight.py`](../raw_v2/three_api_http_preflight.py): `RequestSpec`+A/B/C `CredentialReference`+숫자형 IG User ID+Graph 버전(기본 `v26.0`, 기존 collector 기본값)을 검증해 **토큰 없는 GET URL/fields 쿼리 명세** 생성. `repr`/상태 정보에서는 커서·사용자 ID·환경변수명을 노출하지 않으며, 임의로 만든 가짜 `RequestSpec` 입력은 재검증한다. `OfflineFixtureTransport`는 데이터 전용 합성 응답 저장소로 HTTP 기능이 없다.
> - [`test_three_api_http_preflight.py`](../tests/test_three_api_http_preflight.py) 신규 **11개** 포함, 인증 참조 7개 + 요청 명세 5개까지 격리 로컬 **23/23 PASS** (Python 3.13). 기존 Windows **68/68 PASS**는 이전 스냅샷 기준. 전체 현재 **91개 테스트**의 CI 실행 성공 여부는 별도 확인 필요.
> - **미구현:** 토큰값/환경변수 로딩, 실제 HTTP 클라이언트, Meta API 호출, A/B/C 토큰·쿼터 독립성 검증, 운영 RAW DB 연동. `unsigned_parts()`는 실제 전송용 인증 헤더가 없는 순수 명세이며 전송 가능 상태가 아니다.
> - **실행 금지:** 승인 전 Meta live 요청, 기존 `collection.sqlite` 접근·변경. 인증 토큰/환경파일 업로드·로그·커밋 금지.

> **2026-10-19 p6 정책 보강:** `estimated_time_to_regain_access`를 사용률에서 제외하고 HTTP 2xx라도 Meta `error` 본문이면 저장 없이 레인 중단. `tests/test_three_api_policy_hardening.py` 2개 추가, CI 연결. **기존 Windows 66/66 PASS, 신규 포함 68개는 아직 재검증 전.** 실제 Meta API/토큰/운영 DB 미사용. 실수집 전 HTTP 클라이언트·인증 분리·API 헤더 실제 검증·쿼터 공유 여부 확인 필수.

> **2026-10-19 p6 통합:** `raw_v2/three_api_offline_runner.py`의 `envelope_fixture=True` 모드에서 A/B/C lease→모의 HTTP 응답→사용량 85% 사전 게이트→Meta 정규화→원자적 staging→검증 커서 인계까지 연결. `tests/test_three_api_envelope_runner.py` 4개 신규 및 CI 추가. **직전 56/56 Windows PASS**, 이후 신규 6+4개를 포함한 **66개 테스트는 재검증 전**. 실제 Meta API/운영 DB/토큰 미사용. 주의: 기존 `meta_fixture=False, envelope_fixture=False`는 오프라인 회귀용으로 사용량 헤더 게이트 없이 합성 데이터 저장하므로 실제 수집 경로로 절대 사용 금지.

> **2026-10-19 p6 HTTP 합성 응답 게이트:** `raw_v2/three_api_http_fixture.py`에서 모의 HTTP 상태·사용량 헤더·Meta 미디어 응답을 연결해 **쿼터 확인 후 원자적 staging 저장**하도록 구현했다. HTTP 오류/사용량 미확인/85% 이상이면 레인 쿨다운과 저장 차단. `tests/test_three_api_http_fixture.py` 6개 추가 및 CI 등록. **이전 56/56 Windows PASS 확인, 신규 포함 62개는 아직 재검증 전.** 라이브 네트워크·토큰·운영 DB 미사용. 실제 HTTP 클라이언트와 인증 검증은 미구현.

> **2026-10-19 p6 사용량 정책:** `raw_v2/three_api_usage.py`에서 `X-App-Usage`·`X-Business-Use-Case-Usage` 합성 헤더를 해석하고 사용량 미확인 또는 85% 이상이면 레인별 fail-closed 쿨다운을 적용하는 오프라인 정책을 추가했다. HTTP 429/서버 오류/인증·권한 오류 분류 포함. 신규 테스트 7개와 CI 연결, **전체 56개 테스트는 재검증 전**(사용자가 이전 실행에서 49개 실행 보고, 마지막 `OK` 확인되지 않음). 실제 Meta 호출·운영 DB 변경 없음. 라이브 HTTP 어댑터 및 에러 응답 연동은 미구현.

> **2026-10-19 p6 Meta 응답 정규화:** `raw_v2/three_api_meta_normalizer.py`에 Business Discovery 응답 정규화(허용 필드만 저장, `paging.next` URL 제거, 오류/중복 아닌 유효성 검사)를 구현하고 오프라인 실행기에 `meta_fixture=True`로 연결했다. 신규 테스트 7개 및 CI 등록. 기존 Windows 37/37 PASS, 이후 신규 12개 포함 **49개 재검증 전**. 실제 API 호출·토큰 사용·운영 DB 변경 없음. Meta 실제 응답과의 호환성은 미검증.

## 2026-10-19 offline runner integration milestone
- Added `raw_v2/three_api_offline_runner.py` with injectable synthetic fixture `fetch_page(intent,cursor)`; leases A/B/C, builds official 294-account intent, resolves the verified prior cursor, atomically stages normalized posts and cursor evidence. No credentials, network calls, or production DB access.
- Five new `tests/test_three_api_offline_runner.py` cover first page, A-to-C cursor chain, invalid response rollback, fixture failure, and terminal page. CI includes them. **Windows regression now totals 42 tests pending rerun** (prior 37/37 PASS).
- This is **NOT** a live 3-API engine: fixture injection is not a Meta client, and current coordinator snapshot plan does not implement daily front/catchup/history or anchoring against concurrent new posts. Do not use it to collect live data without separately approved adapter and isolated pilot.


## 2026-10-19 atomic offline staging milestone
- `PageEvidenceStore.commit_staged_page()` now writes up to four normalized post payloads to `staging_posts` **in the same coordinator SQLite transaction** as page cursor evidence and job completion. It rejects duplicate/invalid post IDs, oversized pages, invalid cursor edges and stale leases; failures roll back staged rows and job status.
- Added five synthetic staging tests to `tests/test_three_api_evidence.py`; existing CI test discovery pattern includes them. **These tests have not yet been rerun or verified on Windows/GitHub Actions.**
- `staging_posts` is an isolated proof-of-concept schema, **not** RAW v2 production data storage. There is still no Meta live-call adapter, token validation, durable cross-run snapshot anchoring, or production migration approval. No real API calls or production DB writes were made.

# Three-API collection — implementation gate (2026-10-19)

## Status
**Offline planner, coordinator, cursor validator and transactional evidence state are implemented** (`raw_v2/three_api_plan.py`, `three_api_coordinator.py`, `three_api_adapter.py`, `three_api_checkpoints.py`, `three_api_paging.py`, `three_api_evidence.py`). **Live three-token execution NOT implemented or authorized.** The existing foreground `daily_scheduler.py` remains unchanged and is the only currently validated live path. Do not launch three copies of it against the same SQLite store.


## 2026-10-19 offline implementation checkpoint (supersedes planner-only status)
- Coordinator distributes 294 × 3 logical jobs; per-lane persisted cooldown and leases. Evidence-aware leases require a nonterminal, committed predecessor page. Bare `Coordinator.complete()` is blocked when a `PageEvidenceStore` is active.
- `PageEvidenceStore.commit_page()` validates actual Meta cursor-edge shape and lease expiry; stores the predecessor cursor, next cursor, nonterminal flag and opaque receipt in one transaction with job completion. It rejects stale leases, missing/terminal predecessors, duplicate receipts and cursor cycles. **Receipt is supplied by the caller and is NOT independently proven to reflect a completed external data write.**
- `tests/test_three_api_evidence.py` includes simulated rollback, expired lease, direct-completion bypass, terminal boundary and page-2 chain cases; GitHub CI workflow includes these tests. **CI result for the latest commit is not yet verified.** All tests are synthetic and make no Meta requests.
- **Remaining blockers:** fully integrated staging writer and receipt reconciliation, account-level post/head anchor continuity, cross-app quota confirmation, daily-first/catchup/history scheduler, live B/C credential validation, supervised isolated-DB pilot, and user approval before any production DB switch.
- A's historical `.env.local` path was found on the user's PC; user prepared B/C credential files on Desktop but neither was validated in this stage. Never upload or expose tokens. Original single-API operation is unchanged.

## Requested schedule
- Three separately credentialed Meta identities A/B/C, subject to verification that quota limits are actually independent.
- Round 1: A accounts 1–150 page 1; B 151–294 page 1; C 1–150 page 2.
- Round 2: A 151–294 page 2; B 1–150 page 3; C 151–294 page 3.
- Round 3 onward: prioritize pending catchup, then history toward 365-day boundary; never overwrite history cursor with front cursor.
- Four media records per page; stop each lane at observed X-App-Usage >=85% or when usage header unavailable; each lane has its own persisted cooldown.
- A daily first pass must check *new* posts for all 294 accounts. Page numbers 1–3 describe initial snapshot positions only; a moving feed cannot safely be indexed by offset. Track observed post IDs, prior head anchor and real Meta cursors; if anchor not found, enqueue catchup until connected.

## Safety requirements before any live activation
1. Validate all three Meta credentials without storing tokens in GitHub, logs or DB; confirm their X-App-Usage counters are independent. Token count alone does not imply quota multiplication.
2. One **single coordinator** must hold the existing cross-process DB lock while leasing accounts and persisting page progress; concurrent `collector.run` calls cannot safely share the current `slots` table (single global slot) or current `accounts.backfill_cursor`.
3. Add per-lane cooldown and run/slot identifiers; preserve legacy global cooldown and all existing checkpoints during migration. A restart must never reset `ready_at`.
4. Persist account + phase + page lease and expected cursor before API call; commit sanitized response and new cursor atomically; recover unknown responses via idempotent post IDs and request IDs.
5. Reconcile page 2/3 requests with anchor IDs so newly posted content cannot shift offsets. Preserve all queued catchup gaps and never mark historical completion without rooted cursor evidence.
6. On any failure, missing usage header, identity mismatch or WARN audit without explicit approval, halt safely. Do not auto-bypass safeguards.
7. Offline tests: exact 294 × 3 page coverage, overlapping workers, interrupted writes, usage 84/85/missing, day rollover, cursor shift, new >12 posts/day, 8 blocked accounts, partial pages, quota sharing, existing DB migration. Then single-lane live pilot and independent lane pilot before full rollout.

## Plan-only command
```powershell
python .\raw_v2\three_api_plan.py --source $src
```
This only reads the official CSV and prints a JSON schedule. It makes no API requests or DB writes.

## Existing collector
Keep the existing live scheduler running until a separate tested implementation has been approved. Do not alter the 4-post limit or 85% stop rule in the current live process.
