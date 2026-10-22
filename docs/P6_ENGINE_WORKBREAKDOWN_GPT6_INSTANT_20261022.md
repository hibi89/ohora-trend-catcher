# OHORA P6 — GPT-6 추론 1단계 실행 작업 분해표 (2026-10-22)

> **실행 기준**: 공식 P6 엔진 담당 GPT-6; n8n 담당 Codex와 소유 파일 분리. 이 문서는 **작업 지시와 미완료 체크리스트**이지 개발 완료 기록이 아니다. 기존 P6 작업 브랜치에서 파생된 별도 **계획 전용 브랜치**에 보관한다. 실 Meta/운영 DB 변경/main merge 허가가 아니다.
>
> 공식 저장소: `hibi89/ohora-trend-catcher`
> 현재 계획 기준 원본: `codex/p6-readiness-hardening-20261019` @ `080f66a8a2d947462fed27ad77833a44cfb3c965` (기준 시점 SHA, 작업 시작 시 최신 ref 반드시 재조회)
> 계획 브랜치: `plan/p6-engine-gpt6-instant-20261022`
> 기존 별도 코드 후보: [Draft PR #11](https://github.com/hibi89/ohora-trend-catcher/pull/11) — 실 응답 `business_discovery.media` 복구 planner 입력 대응. **미병합/일부 검증 미완료**, 자동 병합 금지.
> 자세한 n8n 계약: [P6_ENGINE_N8N_CONTRACT_20261022.md](P6_ENGINE_N8N_CONTRACT_20261022.md)
> 빠른 인수인계: [P6_ENGINE_START_GPT6_INSTANT_20261022.md](P6_ENGINE_START_GPT6_INSTANT_20261022.md)

## 0. 완료 목표 / 추정 / 범위

- **개발+오프라인 검증 작업량 14~24시간 추정**. API 사용량 대기, 실제 294계정 실수집 시간, 365일 백필 완료, 사용자 승인/DB 이관은 별도다. 잔여 확인 결과에 따라 변동 가능.
- 완료의 의미: **정상 페이지 저장 및 커서 연속성 확인**, B/C 안전 차단 증거 판정, **기존 `plan_cursor_recovery()` 재사용으로 오류 후 승인된 별도 라운드의 안전한 연결 로직**, 이전 49건의 **무재호출 복구 가능성 판정과 운영 적용 전 명시 승인 게이트**, P6→n8n 결과 계약 및 오프라인 회귀 통과.
- **상태를 분리해서 보고**: 구현됨 / 오프라인 테스트 PASS / 로컬 운영 데이터로 읽기 전용 확인 / 실 Meta 검증 / 운영 적용. 앞 단계 PASS를 다음 단계 완료로 표시하지 않는다.
- 우선순위: **정상 일일 수집 안정화 > API 차단 안전성 > 커서 무효 오류 분류 > 승인형 복구 runner > 기존 49건 복구 > n8n 상태 계약**. 위험한 수집 재개를 위해 안전장치를 완화하지 않는다.
- 이번 실행은 엔진/오프라인 테스트/문서만. **아래 승인 게이트를 만나면 그 지점만 보류하고 나머지 오프라인 단위는 진행**.

### 소유권 / 충돌 금지
| 범위 | 담당 | 제한 |
|---|---|---|
| P6 엔진: `three_api_paging.py`, `three_api_meta_normalizer.py`, `three_api_transport.py`, `three_api_paging_recovery.py`, `three_api_live_serial_step.py`, `three_api_job_binding.py`, `three_api_evidence.py`, `three_api_verified_page_evidence.py`, 관련 엔진 테스트 | GPT-6 | 이미 있는 구현을 재사용; 최소 수정만 |
| `three_api_manual_pilot.py`, `p6_resume_b_guarded.py`, 관련 테스트 | GPT-6 검토/수정 후보 | Codex 또는 타 작업자 최신 diff 확인 후 파일 소유권 충돌 시 독립 분기에서만 작업 |
| `raw_v2/three_api_n8n_tick.py`, `raw_v2/p6_n8n_tick.ps1`, `kq3_294_collection/n8n_p6_daily_collection.json`, n8n UI·스케줄·DB | **Codex 단독** | GPT-6 **수정 금지**; 계약 제안과 엔진 반환 정리만 |
| `HANDOFF.md` / `STATUS.md` / `docs/P6_TROUBLESHOOTING_LEDGER.md` | 각자의 **독립 브랜치에만** 갱신 | 공유 작업 브랜치 동시 수정 금지, 인수 시 cherry-pick/리뷰. 이미 다른 곳에 존재하는 `P6-078` 사고번호 **재사용 금지** |
| GitHub `main` / 실 Meta 토큰 / 운영 SQLite / 기존 성공 Capture·RAW·요청 바인딩 | 사용자 승인 영역 | 임의 API 호출·쿼터 해제·수정·병합 금지 |

### 절대 유지할 불변식
`DESIGN_FREEZE_NOTICE.md`와 `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md` 1~15절, `AGENTS.md` 우선.
**공식 294계정**, A/B/C 3 레인, **`limit=4`**, KST 최신 2라운드/계정 3페이지 상한, 이후 catchup→365일 history, 직렬/원본 무손실, 85% 중단·65분 원칙, unknown/failed 요청 자동 재전송 금지, 성공 observation 중복은 시간별로 보존, 분석 단계에서만 dedup. 하루 294×3=882은 **job 최대치**이지 실제 요청/고유 게시물 보증이 아니다.
동결 설계에 관한 추가 변경은 사용자의 구체적 승인(변경 절, 이유, 영향, 롤백) 필요.

## 1. 실행 절차 (작업 단위)

작업 단위 **1개 = 1개 명확한 검증 가능한 산출물**, 과잉 보고 없이 **5~8단위 완료 시 묶음 보고**. 처리 완료된 항목은 독립 작업 브랜치의 이 체크박스만 표시하고 해당 커밋 SHA/오프라인 테스트를 근거로 남긴다. 파일 변경 없더라도 검수 표에서 상태와 증거를 구분한다.

### G — 시작 게이트 / 최신 상태 고정 (약 1~2시간)

- [ ] **G01** (10~15분, P0): `git fetch origin`, `git status --porcelain=v1`, `git branch --show-current`, `git worktree list` 읽기 전용 진단; **로컬 미커밋 파일·활성 worktree** 목록 비밀 없는 요약. 사용자가 승인하지 않은 `git reset --hard` / `clean` / `checkout --force` 금지.
- [ ] **G02** (10~15분, G01): 원격 P6 기준 최신 SHA 확인; 현재 계획 문서의 기준 `080f66a...`와 비교, 차이 파일만 검토. 기준이 앞서면 최신 원본으로 안전하게 재기반하되 타인 변경 보존.
- [ ] **G03** (10~20분, G02): `DESIGN_FREEZE_NOTICE.md`, 동결 설계 전체, `AGENTS.md`, `HANDOFF.md`, `STATUS.md`, ledger 최신 상단을 순서대로 읽고 충돌 규칙/원본 SHA 확인.
- [ ] **G04** (10~15분, G01~03): 엔진 전담 **독립 feature 브랜치+git worktree** 준비. 현재 운영 데이터 폴더, n8n 담당 변경 파일 제외. worktree 생성 실패 시 원인 기록 후 같은 방법 3회 반복 금지.
- [ ] **G05** (10~15분, G04): Codex와 파일 소유권 교차 검증(특히 `three_api_manual_pilot.py`, `p6_resume_b_guarded.py`). Codex 소유 n8n 파일에 대한 변경 없음 확인.
- [ ] **G06** (15~25분, G04): 오프라인 소켓 차단을 확인한 뒤 **현 기준선 테스트**를 수행. `python -m unittest tests.test_three_api_paging tests.test_three_api_meta_normalizer tests.test_three_api_paging_recovery -v`. 정확한 결과 개수·exit code 기록. 실제 Meta나 운영 DB 경로 접근을 유발하는 테스트 실행 금지.
- [ ] **G07** (10~15분, G02): Draft PR #11 변경사항을 현행 소스와 diff 비교. 동일 수정이 이미 적용돼 있다면 중복 구현 생략; 필요하면 **코드/테스트만** 검토 후 작업자 feature 브랜치로 반영. PR #11의 과거 `P6-078` 문서는 현재 원본의 B quota `P6-078`과 번호 충돌하므로 **그대로 병합하지 말 것**.
- [ ] **G08** (10~15분, G01~07): 기존 운영 49건·B/C 제한·n8n 프로세스/스케줄 활성 여부는 **GitHub 최신 기록으로만 예비 판단**하고, Windows 로컬 원본이 필요할 때 별도 읽기 전용 확인. 진단 결과를 새 실 Meta 승인으로 오인하지 않는다.

**게이트 G 완료 조건**: 기준 SHA, 소유 파일, 테스트 baseline, 위험 목록을 하나의 짧은 보고로 기록. 여기까지 실행하면 코드를 쓰기 전에도 재개 가능.

### N — 정상 페이지 수집/커서/RAW 경로 (약 2.5~4시간)

- [ ] **N01** (15~20분, G): 실제 정상 흐름을 정적 추적: `MetaTransport.send` → `ResponseEnvelope` → `CaptureJournal` → `ContractRawWriter` → `commit_captured_page` → `PageEvidenceStore`; 같은 HTTP 재호출 없는 crash boundary 표 작성.
- [ ] **N02** (15~20분, N01): `paging.next` 없는 `paging.cursors.after` 전용 **4개짜리 Business Discovery 페이지**가 `cursor_candidate`가 되고 캡처의 `next_cursor`로 전달되는지 오프라인 fixture 검증.
- [ ] **N03** (15~20분, N02): 실제 응답 `business_discovery.media`, 정규화 `data`, 부분/빈 페이지를 각각 검증. `after` 존재는 *후속 조회 후보*이지 추가 게시물 존재 증명이 아님.
- [ ] **N04** (15~20분, N02): 커서 공백/잘못된 형식/재사용/순환/`paging.next`만 있음/4개인데 커서 없음에서 **silent terminal 금지** 회귀 테스트.
- [ ] **N05** (20~30분, N01): 원본 안전 응답이 Capture보다 먼저 저장되는 경로, 민감 `next URL` 제거, SHA/digest·request_id·RAW receipt 검증을 synthetic SQLite에서 확인. 응답/메트릭 null·absent 구분.
- [ ] **N06** (20~30분, N01~05): `page=1`→`page=2`→`page=3` 동일 계정 커서 선행 증거, 다른 레인 호출 주체, 최대 4개, 커밋 후 `done` 원자성 오프라인 통합 테스트.
- [ ] **N07** (20~30분, N06): 장애 주입: response 영속 직후/RAW 저장 직후/영수증 검증 전/커서 커밋 직전 종료 시 같은 `request_id` 재전송 없는 복구 테스트.
- [ ] **N08** (10~20분, N05~07): `like_count`/`comments_count`/`view_count` 누락과 명시적 0을 구별, 영상·릴스 media type, 미디어 URL/영수증 경로에 변경 의도 없는지 확인. 지원 안 되는 필드를 0으로 채우지 않는다.
- [ ] **N09** (15~25분, N01~08): 필요 최소 코드 수정 + 관련 단위 테스트 PASS; 정상 경로 상태는 **`page_committed`/`recovered`/`no_ready_work`/`serial_busy`** 실제 코드 반환값과 일치하게 기록.

### Q — A/B/C 차단·권한·실패 보호 (약 1.5~2.5시간)

- [ ] **Q01** (15~20분, G): A의 이전 계정 HTTP400 code=110/subcode=2207013 네 건은 **기존 `failed_no_retry`**로 유지; 인위적 계정 삭제 확정이나 재요청 금지.
- [ ] **Q02** (15~20분, G): B `live_pending_usage`는 전날 오류 이후 **새로운 safe usage 증거 부재**가 원인임을 확인; 토큰 만료 확정이라고 쓰지 않는다.
- [ ] **Q03** (15~20분, G): C `usage_limit` 85% 정지 + cooldown 경과만으로 자동 재개 불가 검증. 긴급 50계정 수집기 API-C rate bucket과 공유 여부 미검증 시 재개 보류.
- [ ] **Q04** (20~25분, Q02~03): `p6_resume_b_guarded.py` 현재 B/C 수동 증거 경로의 **읽기 전용 preview** 및 코드의 `>=85` 차단, timestamp-aware evidence 조건과 잘못된 값 거부 테스트. 운영 `--approve-resume` 절대 실행 금지.
- [ ] **Q05** (20~25분, Q02~04): 에러 응답에 헤더가 없어도 차단 유지, 보안 정제된 quota 정수만 저장, 임의 보정·0으로 대체·차단 해제 금지 오프라인 검증.
- [ ] **Q06** (15~20분, Q01~05): B/C 보류, A 실패, 인증/권한/계정 오류/사용량 오류/`outcome_unknown`을 구분하여 안전하게 분류; 기존 요청 바인딩과 실패 결과 보존.
- [ ] **Q07** (10~20분, Q01~06): 리스크·남은 사용자 증거 요구사항만 보고. 실제 Meta dashboard 사용량 확인/운영 lane reset은 **사용자 승인 시 별도 실행**.

### R — 커서 무효 오류 및 최소 Recovery runner 연결 (약 3.5~6시간)

- [ ] **R01** (15~20분, N): `three_api_graph_http.py`가 오류 문구를 보존하지 않고 *명시적* invalid cursor만 표시하는지 확인. 일반 code=100/HTTP400을 무효 커서로 단정 금지.
- [ ] **R02** (15~20분, R01): `three_api_transport.py`의 `cursor_invalid` 분류→`failed_no_retry`, `resume_required`까지 영속/재시작 시 전달되는지 synthetic 실패 검증.
- [ ] **R03** (15~20분, R02): 기존 요청 재전송 0회 검증(재시작, 만료 lease, 중복 n8n tick 포함). 새로운 복구 라운드는 **새 승인·새 job/request**만.
- [ ] **R04** (20~30분, R02): `plan_cursor_recovery()` 입력/출력 계약 그대로 재사용. 기존 ID와 새 응답 ID의 overlap 알고리즘을 다시 구현하지 않는다.
- [ ] **R05** (20~30분, R04): 원본 RAW에서 연결 후보 post ID를 읽는 **읽기 전용 어댑터**를 설계/구현. `account_key` 일치, 이전 request/observation provenance 검증; 데이터·원본 덮어쓰기 금지.
- [ ] **R06** (25~40분, R04~05): **순수 함수/가짜 응답** 기반 복구 runner: 최신 페이지부터 최대 3페이지 순차 수집을 모델링; 실제 HTTP는 injected transport+별도 승인 없는 상태에서 불가능하도록 default-off. n8n 복구 로직 추가 금지.
- [ ] **R07** (20~30분, R06): 1~3페이지 ID overlap 발견 시 그 **응답에 실제 반환된 새 `after`**만 continuation 후보로 전달. 삭제 추정, offset 산출, 커서 문자열 생성 금지.
- [ ] **R08** (20~30분, R06~07): no overlap ⇒ `coverage_gap`, 3페이지째 앵커 ⇒ 같은 라운드의 4페이지 요청 금지. 공백 페이지·terminal_verified·후보 커서 부족도 합성 테스트.
- [ ] **R09** (15~25분, R06~08): 게시물 순서 변경, 접근 불가, 누락, 동일 ID 재등장, 중복 관측; `deleted_media_ids_confirmed=[]` 유지, 결측을 삭제 확정으로 오인하지 않음.
- [ ] **R10** (25~40분, R06~09): crash/restart 시 복구 계획·진행 상태를 별도 **감사 가능한 신작업**으로 영속화하는 계약을 설계하고 **격리 temporary SQLite**에서 테스트. 기존 실패 binding/RAW cursor 행 갱신 금지. 운영 DB 새 테이블/스키마 변경 필요 시 **승인 보류**.
- [ ] **R11** (20~30분, R10): 정상 흐름과 복구 흐름의 상태 출력 연결 (`resume_required`, `resume_ready`, `coverage_gap`, `manual_review`)은 P6 엔진 모듈에서만 구현. Codex 소유 n8n adapter 파일은 수정하지 않는다.
- [ ] **R12** (15~25분, R01~11): 별도 승인 없이는 transport를 호출하지 않는 **default-off 승인 gate** 테스트. 사용량 85%/쿨타임/레인 공용쿼터/최대 3페이지/실패 요청 불변식 회귀.

### L — 2026-10-21 선행 페이지 49건의 무재호출 복구 (약 2.5~4시간)

- [ ] **L01** (15~20분, G07): Draft PR #11의 실제 응답 `business_discovery.media` 래핑 입력 수정과 현재 원본 비교. 유효하면 *코드·테스트만* 최소 반영; 기존 브랜치의 `P6-078` 번호 충돌 문서는 제외.
- [ ] **L02** (15~20분, L01): `build_paging_recovery_plan`에 safe body JSON 중첩 49개와 직접 `data` 구조 fixture 회귀 추가/실행. 반환 상태는 후보이지 자동 수리 완료가 아님.
- [ ] **L03** (15~25분, L02): 49개 운영 데이터는 현재 로컬 Windows에 있음. **허가받은 읽기 전용 경로만** 사용해 job/request binding→safe response evidence→Capture digest→RAW receipt 대응 건수와 불일치 카운트 확인; cursor 원문·토큰·계정명 외부 출력 금지.
- [ ] **L04** (20~30분, L03): **49건 각각** terminal=1/response_cursor=NULL와 safe evidence after 존재를 재검증; 각 건의 chain incomplete·capture cursor absent 등 보류 원인 분류.
- [ ] **L05** (25~40분, L04): 확정된 원본 Capture/RAW 행 자체를 바꾸지 않는 복구 방식 비교: append-only correction proof / 새로운 epoch + 연속성 링크 / 안전한 별도 materialization. 기존 evidence의 직접 UPDATE는 기본 거부, 설계 동결과 DB 계약 영향 있으면 **정확한 조항·영향·롤백을 사용자에게 승인 요청**.
- [ ] **L06** (20~30분, L05): **복제된 임시 DB**에서만 receipt와 cursor 증거 연결/선행 page2 개방/일회성 멱등성/재실행 0중복 검증. 실제 49건 원본 DB 수정·커서 재전송 금지.
- [ ] **L07** (15~25분, L06): 정상 종료 확정이 아닌 after 후보임을 구분. 후속 2페이지 실제 게시물 수는 미확인으로 표시; 잘못된 terminal=1을 근거 없이 49건 일괄 UPDATE 금지.
- [ ] **L08** (10~15분, L01~07): **적격 후보 x/49, 근거 부족 y/49, 격리 복제 검증 z/49, 운영 반영 0/49(미승인)**를 명시하고 사용자 승인 필요 여부 보고.

### I — P6→n8n 읽기 전용 결과 계약 / Codex 인수 (약 1.5~2.5시간)

- [ ] **I01** (10~15분, G): 현행 `LiveSerialStep.step` 실제 상태: `page_committed`/`recovered`/`account_failed`/`no_ready_work`/`serial_busy` 확인.
- [ ] **I02** (10~15분, I01): `three_api_n8n_tick.tick` 실제 `action` (`LIVE_TICK_COMPLETE`, `WAIT_EXPLICIT_LIVE_APPROVAL`, `MANUAL_REVIEW` 등) 및 반환 `status`와 coordinator `reason` 구분. 실재하지 않는 top-level status를 이미 지원한다고 주장 금지.
- [ ] **I03** (20~30분, I01~02): 독립 P6 **순수 결과 변환기/DTO** 최소 추가 또는 기존 engine 결과 제약 검증. 기존 n8n 키를 삭제/바꾸지 않고 `schema_version`/상태 정의 문서화. Codex가 endpoint/adapter에서 소비하도록 한다.
- [ ] **I04** (15~20분, I03): 매핑: `page_committed`/`no_ready_work` 직접, `usage_limit`/`live_pending_usage`는 lane gate reason, `resume_required`는 durable failure, `resume_ready`/`coverage_gap`는 별도 planner 결과, `manual_review`는 fail-closed action. `recovered`/`account_failed`/`serial_busy`도 보존.
- [ ] **I05** (15~20분, I03~04): 허용 필드만 출력: `action`, `status`, `reason`, `day_kst`, `lane`, `committed_pages`, `recovered_pages`, `approval_required`, `live_meta_called` (unknown 허용). **토큰·원본 커서·계정명·Meta URL·request_id 직접 출력 금지.**
- [ ] **I06** (15~20분, I05): offline schema fixtures 7+상태에 대한 정상/불명/오류/리스트 실패 케이스 PASS. 실제 n8n 예약 워크플로 검증은 Codex 담당.
- [ ] **I07** (10~20분, I01~06): Codex에 **정확한 JSON 예시(가상 값), exit code 0/2, 상태 매핑, 변경 필요 인터페이스** 문서 전달. `three_api_n8n_tick.py`/JSON/PS1/스케줄을 직접 편집하지 않는다.

### V — 오프라인 검증/기록/인수 (약 1.5~3시간)

- [ ] **V01** (15~25분, N/Q/R/L/I): 부분 회귀: `python -m unittest tests.test_three_api_paging tests.test_three_api_meta_normalizer tests.test_three_api_paging_recovery -v`. 기타 수정 파일의 정확한 `tests.*` 추가 실행; 종합 PASS/FAIL 개수 기록.
- [ ] **V02** (15~25분, V01): `python tests/run_offline.py` 실행 전 **오프라인/소켓차단/가짜 DB만 사용**하는지 확인. full suite 실행 결과와 duration/exit code 보고.
- [ ] **V03** (15~25분, V01): 294 계정·A/B/C 직렬·limit4·max3·85%·65분·KST 날짜 전환을 합성 데이터로 확인. 잘못된 상태 초기화·중복 binding 금지.
- [ ] **V04** (15~25분, R/L): synthetic 49 repair·invalid cursor·삭제 의심·ID 순서 변경·overlap 있음/없음·3페이지 상한·공유 quota로 인한 중단을 모두 포함해 테스트.
- [ ] **V05** (10~20분, V01~04): 코드 changed paths/상태 확인: `git diff --check`, 민감정보 diff 스캔, `git status`. n8n 소유 파일과 운영 DB 경로 변경 0 증빙.
- [ ] **V06** (15~20분, V01~05): 작업자 독립 branch에서만 GitHub commit/push. PR은 **Draft**, Codex/shared P6 브랜치/main 직접 push·자동 병합 금지. PR #11과 충돌 확인.
- [ ] **V07** (10~20분, V06): `HANDOFF.md`/`STATUS.md`/`docs/P6_TROUBLESHOOTING_LEDGER.md`는 **본인 독립 브랜치**에서 업데이트, 새 사고번호는 최신 원본 재조회 후 중복 없이 발급. 정확한 SHA, 테스트 증빙, 미검증 사실 명시.
- [ ] **V08** (10~15분, V07): Codex의 n8n 팀에 `P6_ENGINE_N8N_CONTRACT_20261022.md` 링크와 status 계약을 전달. 사용자에게 **완료/보류/승인 필요** 3갈래 결과 보고.

## 2. 실행 묶음 및 병목 최소화

| 실행 묶음 | 작업 | 병행 가능 조건 | 보고 |
|---|---|---|---|
| 체크포인트 1 | G01~G08 | 먼저 순차 | 원본 SHA, 소유권, baseline |
| 체크포인트 2 | N01~N09 | Q/L의 읽기 전용 검증 병행 가능 | 정상 경로 PASS/불일치 |
| 체크포인트 3 | Q01~Q07 | DB 운영 변경은 금지 | A/B/C gate 정리 |
| 체크포인트 4 | R01~R06 | 기존 planner 그대로 재사용 | 실패→별도 복구 계획 테스트 |
| 체크포인트 5 | R07~R12 | 3페이지/무재요청 테스트 우선 | overlap·coverage_gap·충돌 상태 |
| 체크포인트 6 | L01~L08 | 임시 DB만 | 49건 무재호출 적격·운영 미반영 |
| 체크포인트 7 | I01~I07, V01~V08 | Codex 별도 담당 n8n 파일 미변경 | DTO/테스트/PR/인수 |

작업자에게 "ㄱ"처럼 짧은 계속 지시가 오면 **다음 미완료 체크포인트부터 진행**하되 원본 SHA와 다른 작업자의 commit을 먼저 확인한다. 테스트 반복 지시보다 신규 근거를 우선. 1~2회 동일 방법 실패면 즉시 원인/대안 기록.

## 3. Meta / 운영 DB / 설계 승인 게이트

**별도 명시 승인 전 절대 금지**: 실 Meta API 호출; 기존 운영 SQLite·WAL·커서 수정; 기존 `failed_no_retry`/`outcome_unknown` 요청 재전송; quota/cooldown gate 임의 해제; `Capture`/`RAW` 원본 덮어쓰기·삭제; 공식 294 CSV 변경; P6 실 스케줄 시작·종료; n8n JSON 수정; 동결 설계 수정; `main` 병합.
**특히** 사용자가 긴급 50계정 API-C 수집을 승인한 적 있다고 P6-C 승인으로 확대하지 말 것.

승인 요청은 반드시 **(1) 정확한 대상 DB/호출 수, (2) 사전 안전 증거, (3) 실패 시 손실 없는 중단/롤백, (4) 최대 비용·쿼터 영향, (5) 수행 명령, (6) n8n/긴급 50 동시 호출 위험**을 제시한다. 미승인 단위는 PENDING으로 남기고 나머지 오프라인 업무 계속.

## 4. Definition of Done

### 완료 판정(오프라인 엔진)
- 기존 294×3/limit4/3page/2라운드/frozen design 불변; 소유권 충돌·비밀노출·강제 reset 0.
- 최신 브랜치 코드 기준 정상 페이지/커서 후보/중단/후속 유효 커서·빈 페이지·terminal 경계 테스트 통과.
- 영속 바인딩/SAFE response/Capture/RAW receipt/control 커서 저장 무결성 검증.
- 커서 무효 응답 **실패 기록 + 무재전송 + 별도 승인형 3페이지 recovery fixture + overlap→새 커서/없음→coverage_gap**.
- 기존 49건은 사용자 승인 전 **실제 운영 적용 0**, 증거 기반 적격 분류와 임시 복제본 성공 수치 분리.
- n8n DTO 계약은 실제 구현 상태만 표시; Codex에 handoff, scheduler 파일 편집 0.
- 정확한 테스트/코드 SHA + Draft PR + 롤백 방안, 미검증 실 Meta 상태 명시.

### 아직 오프라인 완료만으로 인정할 수 없는 항목
- 294계정 **실제 전량 성공**, B/C 쿼터 재개·긴급 C rate bucket 분리 증명, 커서 invalid 실서비스 재앵커, 기존 49 운영 수리 적용, 무인 장기 History 365일 완료, n8n 실제 24시간 재시작 안정성.
- 동결 설계의 장기 catchup→history와 변경 이력/자산 URL까지 **전제품 생산 완료**는 이 계획의 단계별 범위보다 크므로 위를 완료라고 말하지 않는다.

## 5. 최종 보고 양식

`[checkpoint] 원본 P6 HEAD: ... / 내 브랜치 HEAD: ... / 완료 ID: G01-G08... / 변경 파일: ... / 검증: 총 n PASS, m FAIL, k SKIP, exit... / 운영 Meta 호출 0 / 운영 DB 변경 0 / n8n 편집 0 / 기존 실패 재전송 0 / 남은 ID: ... / 사용자 추가 승인 필요: ... / 정확한 Draft PR URL: ...`

**첫 실행 명령은 인수인계 문서의 “즉시 시작 지시문”을 따른다.**
