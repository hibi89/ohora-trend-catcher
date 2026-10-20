# OHORA Trend Catcher — 원본 보존·연속 수집·관측 이력 최종 설계안

작성: 2026-10-20 KST. 상태: **설계 검토 완료 / 구현 승인 대기**.
이 문서는 구현 완료 선언이 아니다. 실행 코드·운영 데이터·Meta 호출·main 병합은 이번 검토에서 변경하지 않는다.

## 1. 결론과 고정 조건

**p6의 기존 수집·복구 코드를 살리고, 응답 원본 보존을 가장 앞에 추가한다. 그 위에 날짜별 작업과 영속 백필, 관측·상태 버전·변경 이벤트를 연결한다.** classic으로 돌아가거나 A/B/C 작업물을 통째로 지우는 방식은 추천하지 않는다.

- A/B/C **3개 API 계정/레인**, 요청의 **게시물 limit=4**는 변경하지 않는다. 공식 수집 대상 294계정과 API 실행 계정 3개는 서로 다른 개념이다.
- 매일 최신 수집은 **2라운드에 걸쳐 계정당 3페이지**, 최대 12개를 관측하는 하나의 일일 작업이다. 최신 12개를 하루 두 번 조회한다는 뜻이 아니다.
- 최신 → 필수 누락 복구 → 남은 시간 과거 백필. KST 날짜가 바뀌면 새 최신 작업이 우선한다.
- 85% 중단, 65분 정책, 실제 커서 검증, 불확실한 HTTP 자동 재발송 금지는 유지한다.
- 일일 12개는 수집 상한이 아니라 최신 우선 작업 범위다. 백필은 별도의 영속 커서로 계속 진행한다.
- 원본 최초 상태, 매번 관측한 상태, 변경 이력 모두 보존한다. 조회 화면은 저장된 DB만 읽는다.
- 목표 365일, 추후 730일 확장. 오늘 가져온 과거 게시물의 반응값은 **오늘 관측값**이며 과거 반응의 복원이 아니다.

## 2. 실제 저장소 확인 결과

| 구분 | 확인한 기준 | 판정 |
|---|---|---|
| p6 main | `bcdb84b640738fdfceaf833dd831a26d401a2427` | 기존 기반. PR #6은 미병합 |
| p6 최신 검토 | `77bde11a31833c298134d0215ea8aa7c539067f4`, `codex/p6-readiness-hardening-20261019` | 개발 기준으로 추천. Draft PR #6 open |
| classic | `ee3979bc64a09bf94d65da54459be385f909996f` | `ohora-trend-catcher-classic-safe/` 아래 복원 소스 존재. README만 있다는 BOOK 기록은 오래됨 |
| 기존 운영 원본 | 사용자 Windows의 SQLite·CSV·공식 account_list.csv | 이번 환경에 없음. 실제 행수·백업 상태·완전성 미검증 |

PR #6은 main 대비 39파일, +4,835/-19줄이다. 대부분 추가이며 폴더가 더러워졌다는 이유만으로 전부 버리면 요청 바인딩·영속 capture·재처리 안전장치도 잃는다.

classic의 일반 파일 94개 경로는 모두 현재 p6에도 있다. Python 56개를 AST로 비교하면 49개 동일, 7개 차이. 바이트 차이 다수는 줄바꿈 영향이 있으므로 바이트 불일치를 기능 변경으로 오인하지 않았다. classic의 `raw_v2/collector.py`는 현재 p6 파일과 줄바꿈 정규화 후 같다. **classic은 완전히 별개의 깨끗한 신제품 기반이 아니다.** p6는 Git 추적 파일 161개(숨김 설정 포함)를 확인했다.

검토 범위: 두 저장소 트리, p6 main/PR 차이, 최신 인수인계·요구사항, 수집·스케줄·계약·transport·capture·writer·커서·복구 코드 및 오프라인 전체 회귀. vision/대시보드의 실서비스 품질을 검증했다는 의미는 아니다.

현재 GitHub API 조회 결과 p6는 **public**이다. 비공개 저장소를 기준으로 삼는 사용자 원칙과 차이가 있다. 이번 작업에서 공개 범위는 변경하지 않는다. 비공개 전환은 별도 승인 사항이며, 어떤 경우에도 데이터·토큰을 코드 저장소에 넣지 않는다.

## 3. 목표 정합성과 실제 차이

원래 RAW 축적 목적과 이번 요구는 일치한다. 일일 실행 범위, 모든 필드 변경 이력, 원본 보존 순서를 구체화하는 확장이다. AI 태깅·디자인 ID·대시보드는 연결점만 마련한다.

| 요구 | 현 상태 및 소스 근거 | 추가할 일 |
|---|---|---|
| 3레인·4개 요청 | `three_api_plan.build_plan`, `RequestSpec`에 존재, 오프라인 검증 | 그대로 유지 |
| 294×3 페이지 | `Coordinator.seed()` 882 jobs; page1 front, page2/3 catchup | 실제 runner 연결, 날짜별 작업 키 도입 |
| 다음 날 새 일일 작업 | jobs의 UNIQUE(account_ordinal,page), page 1~3 제한 | 기존 완료행 초기화 대신 day/epoch가 포함된 새 jobs |
| 지속 과거 수집 | legacy `collector.select_tasks`, `history_chain_status` 존재 | p6 경로는 미연결. RequestEnvelope는 history를 명시 거부, writer도 front/catchup만 수용, orchestrator는 history_held_for_review 반환 |
| 단일 요청 복구 | `JobRequestBindingStore.dispatch_once`, `CaptureJournal`, `ThreeApiContractExecutor`, `commit_captured_page` | 재사용. 일일/백필 jobs로 일반화 |
| 변경 없는 재관측 | request별 `post_observations` 및 request_log 존재 | 모든 성공 조회·빈 페이지·계정 관측으로 일관되게 확장 |
| 모든 필드 변경 | writer의 posts/post_media는 최초 INSERT 중심, carousel_status는 충돌 무시 | 구조·내용·지표·URL 버전 및 변경 이벤트 추가 |
| 팔로워 관측 | 저장 테이블/계약 일부 존재 | 실제 `business_discovery_field()`는 id,username,media만 요청. 계정 요청 필드와 테스트 여부 모델 분리 필요 |
| NULL 구분 | 계약은 null 허용 | `normalize_meta_page()`는 명시적 like_count=None도 거부. 계층 간 불일치 수정 |
| 받은 응답 보존 | 정규화 성공 후 capture 저장 | 정규화 이전에 안전한 응답 원본 저널 도입 |
| 캐러셀 부분 수집 | `_normalize_children`에서 부분 children 거부 | 부모와 받은 children 보존 + partial 표시. 전체 수집 완료로 오인 금지 |
| 레인 장애 격리 | coordinator는 레인 상태, 계약 CATEGORY_SCOPE는 usage/auth를 global | 데이터 격리와 쿼터 격리를 분리. 쿼터 독립 미확인 때 전역 정지는 유지 |
| 실운영 | 문서상 A레인 1회 HTTP200/4개 사용자 보고 | 294개·3레인·실커서·장기 운영·운영 DB 이관은 미검증 |

### 우선 수정할 결함

1. **85%에 도달한 바로 그 성공 응답이 capture 전에 거부됨.** `MetaTransport.send()`가 usage>=85 또는 헤더 없음에서 예외를 낸다. 이미 받은 데이터 저장과 다음 호출 중단을 분리해야 한다. 커서 검증 실패·부분 캐러셀도 원본 보존을 막아서는 안 된다.
2. **NULL 계약 불일치.** 합성 `like_count=None, comments_count=0` 입력은 normalizer에서 `Invalid engagement count`로 거부됨을 이번에 재현했다. 명시적 NULL은 정상 관측 상태이고 음수·문자열·bool은 invalid로 분리한다.
3. **계정 필드 요청 누락.** 현재 요청식에 followers_count/media_count가 없고 executor는 media_fields를 보고 account_fields_tested를 추론한다. 저장 테이블 존재만으로 팔로워 수집이 된다고 볼 수 없다. 별도 account_fields 설정과 API 반환 검증이 필요하다. 요청 limit/레인 수는 그대로다.
4. **작업 키에 날짜·백필 epoch가 없음.** seed 재호출만으로 다음 날 작업이나 page4+가 생성되지 않는다. jobs, binding, evidence를 함께 확장해야 한다.
5. **미디어 순서 변경을 충돌로 처리.** writer는 같은 media_id의 item_order가 달라도 identity collision을 낸다. 소유 게시물 충돌과 관측된 순서 변경을 구별해야 한다.
6. **receipt 통계가 요청별 증가량이 아님.** `media_inserted`는 first_seen_run 전체 COUNT이므로 동일 run 후속 페이지에서 누적치를 반환한다. 실제 INSERT 증가량 또는 request 연결을 사용해야 한다.

위 판정은 정적 코드 검토 및 명시한 합성 재현 근거다. 운영 데이터가 실제로 유실됐다는 판정은 아니다.

## 4. 대안 비교와 선택

| 기준 | A: 3개 저장소 역할 유지 + 원본 저널/버전 추가 (추천) | B: 단일 새 SQLite로 전면 통합 |
|---|---|---|
| 유실 위험 | 응답 선저장과 단계별 재처리로 낮춤. DB 간 복구 필요 | DB 내부 원자성은 단순, 기존 데이터/코드 이전 위험 큼 |
| API 비용 | 동일 3레인·4개. 재처리는 API 0회 | 동일. DB 통합만으로 호출 감소 없음 |
| 공간 | 원본+버전+관측. 일부 기존 capture 중복 존재 | 중복을 줄일 여지는 있으나 원본 자체는 필요 |
| 유지보수 | 기존 계약 재사용, 명확한 복구 상태 필요 | 장기적으로 단순하지만 지금 대규모 재검증 |
| 구현 난도 | 중간. 기존 안전 경로를 순차 확장 | 높음. 이관과 실행 경로 재작성 |
| 기존 코드 재사용 | 높음. coordinator/binding/capture/writer 중심 | 부분 재사용, DB 연계 대부분 교체 |
| 복구 | 각 단계 영수증 대조 후 무HTTP replay | 단일 DB replay는 쉬움, 이관 실패 영향 큼 |
| AI 확장 | 읽기 전용 관측 API/뷰로 충분 | 동등. 분석 DB는 어느 쪽이든 분리 가능 |

추천 A는 영원히 3개 DB를 고집한다는 뜻이 아니다. 지금 실제 데이터를 지키며 수집을 완성하고, 나중에 실측 병목이 있을 때 통합한다. **레인마다 독립 RAW DB 3개를 만드는 것이 아니라, control/capture/data 역할별 DB 3개**다. 한 프로세스의 단일 writer가 RAW에 직렬 기록하고, A/B/C 호출은 기존 허용 범위 안에서 병행한다.

### 관측 저장 방식 비교

- 매 조회마다 전체 필드 복사: 단순하고 복구 쉬움. 캡션/URL/children 중복이 크다.
- 변경된 필드 patch만 연결: 공간은 작지만 긴 patch 체인을 재생해야 현재/과거 상태를 얻는다.
- **선택: 모든 조회 observation + 내용별 완전한 상태 버전 참조 + 변경 이벤트.** 관측 시점 상태를 직접 조회하고 변경 없음은 같은 버전을 가리킨다. 원본 응답 저널은 별도로 남긴다. 해시는 중복 상태 식별용이며 원본 증거의 대체물이 아니다.
- 상태 버전은 content / metrics / media_structure / locator로 나눠 좋아요만 바뀌어도 큰 캡션 전체를 복제하지 않는다. A→B→A일 때 상태 A는 재사용해도 두 변화 이벤트는 모두 남는다.

## 5. 수집 스케줄

### 일일 고정 배정 — 변경 없음

| 라운드 | A | B | C |
|---|---|---|---|
| 1 | 1~150 / page1 | 151~294 / page1 | 1~150 / page2 |
| 2 | 151~294 / page2 | 1~150 / page3 | 151~294 / page3 |

C의 page2는 해당 계정 A/page1의 영속 RAW receipt와 커서가 확보된 뒤에만 실행한다. 라운드2 C/page3도 해당 A/page2를 기다린다. 세 레인이라고 의존 페이지까지 동시에 보내지 않는다. 실제 terminal이면 남은 페이지를 `not_required`로 기록하고 가짜 성공 observation을 만들지 않는다.

### 상태 전이

`RECOVER_LOCAL → DAILY_FRONT → REQUIRED_CATCHUP → HISTORY → WAIT`

- 모든 전송 전에 KST day와 lane ready_at, budget, predecessor 증거를 다시 확인한다.
- 복구는 HTTP 없이 이미 저장된 응답을 처리한다. 호출 제한 중에도 디스크 복구는 가능해야 한다. 기존 lease_for_recovery의 lane cooldown 제약은 로컬 replay와 HTTP 차단을 분리하는 방식으로 개선한다.
- latest page1~3은 새 스키마에서 모두 DAILY_FRONT의 depth로 표현한다. 기존 catchup(page2/3) 명명은 호환 어댑터에만 둔다. 실제 REQUIRED_CATCHUP은 어제 최신 anchor와 연결이 끊겼을 때의 별도 작업이다.
- 계정별 실패는 `blocked/excluded_with_reason`로 남기고, 매일 재시도 소진 여부를 기록한다. 미해결 계정 하나가 전 계정 백필을 영구 차단하지 않도록 명시적 예외 상태를 둔다. 화면에는 `완전 완료`와 `예외 포함 종료`를 구분한다.
- HISTORY는 계정별 마지막 **검증·커밋된** cursor/anchor/epoch부터 계속한다. 새 front cursor로 덮어쓰지 않는다.
- 최초 백필 cursor가 없으면 최신 page1~3의 연결된 증거를 백필 시작 경계로 재사용한다. 기존 백필 cursor가 있으면 보존한다.
- 새 날짜에 미전송 history jobs는 pause한다. 이미 전송된 응답은 정상 저장·복구한 뒤 새 front로 전환한다. 어제의 binding/unknown/capture를 삭제하지 않는다.
- 실제 관측 시각이 자정 이후라면 observed_at은 오늘, 작업 day_kst는 생성일일 수 있다. 둘을 분리해 어제 요청을 오늘의 최신 완료로 임의 계산하지 않는다.
- 백필 계정 배정은 라운드마다 미완료 계정을 공정 순환한다. 기본 계획은 한 cycle에 전체 294계정 각 1페이지를 지향한다. 더 많은 여유 호출을 쓴다는 가정은 운영 실측 후 별도 결정한다.

### 쿨다운과 장애 범위

기존 coordinator는 한도/헤더 실패 시 관측 시각+65분을 저장하고 명시적인 reset을 요구한다. legacy collector의 `max(종료+63분, 시작+65분, 기존 ready_at)`과는 다르다. 이 차이를 숨기고 65분마다 무조건 실행한다고 설계하면 안 된다.

신규 runner는 **기존 ready_at을 앞당기지 않고**, 65분을 최소 안전 제약으로 유지한다. 정상 라운드 사이의 승인된 cadence와 한도 사건 뒤의 중단은 별개다. 한도 사건은 시간이 지났다는 사실만으로 자동 해제하지 않으며 검증된 reset 경로를 사용한다. 따라서 무인 20시간은 아직 검증된 기능이 아니다.

계정 permission 오류는 계정 격리, 로컬 디스크/무결성 실패는 전체 정지. 쿼터 독립 확인 전 usage/auth 오류의 기존 global 정지를 유지한다. 독립성이 입증된 후에만 인증 주체·app별 quota group 범위를 적용한다. 레인별 데이터 진행 상태는 언제나 별도 보존한다.

## 6. 20시간 수집량 산정

`1200 / 65 = 18.46`은 시간 예산의 비율이지 완료 사이클 정수가 아니다. 시작/종료 정의와 실행 시간에 따라 18회 완료 또는 경계에서 추가 착수가 가능하므로 **보수적인 계획값은 최대 18회 완료**로 둔다. 실제 cooldown/장애/의존성은 이보다 줄일 수 있다.

정의: cycle은 A/B/C의 한 차례 가용 예산 묶음이다. 최신 작업은 2cycle에 882페이지, 백필은 이후 cycle당 294페이지(계정별 1페이지)를 계획한다. 150×3=450 요청 용량이 실제 보장된다는 뜻이 아니다.

`계정당 새 과거 게시물 기대치 = max(0, C-2) × 4 × q`

q는 성공률×비중복률×실제 페이지 충족률을 합친 가정이며 측정 전 수치다. 필수 catchup·재관측이 소비한 cycle 상당량은 C에서 추가 차감한다.

| 시나리오(가정) | 20시간 내 완료 C | 최신 이후 cycle | q | 계정당 새 과거 예상 | 최신 관측 포함 상한성 계획값 |
|---|---:|---:|---:|---:|---:|
| 보수 | 12 | 10 | 0.60 | 24 | 최대 36 |
| 기준 | 16 | 14 | 0.80 | 44.8 | 최대 56.8 |
| 낙관 | 18 | 16 | 1.00 | 64 | 최대 76 |

낙관 페이지 요청: 882 + 16×294 = **5,586**, 반환 관측 슬롯 최대 **22,344**. 이것은 신규 고유 게시물 수가 아니다. 기존 12개 재조회, 페이지 중복, 짧은 계정, terminal, 실패, catchup이 있으면 신규 수는 더 작다. 목표가 72라는 계산은 대략적인 규모 감각으로는 가능하지만 **2라운드 소비와 최신 12개를 섞지 않고 측정해야 한다.** 18회 모두를 과거 cycle로 간주해 72개를 보장할 수 없다.

매 cycle 보고: 요청/성공/실패, unique new, reobserved, changed, empty, lane usage, 실제 소요분, oldest published_at, continuity gaps. 3레인 quota 독립성과 294계정 한 cycle 완료는 파일이나 계획표로 입증되지 않는다.

## 7. 원본과 데이터베이스 설계

### 원본의 정의와 보존 순서

1. 기존 SQLite·CSV·공식 명단·기존 RAW 파일은 그대로 보관한다. 새 이관 결과와 분리한다.
2. 새 HTTP 응답은 **의미 검증 전에** credential 제거를 거친 응답 증거로 영속 저장한다. 이 단계는 business schema allowlist로 미지 필드를 버리는 정규화와 다르다.
3. 인증 토큰·인증 헤더·token 포함 paging URL은 저장하지 않는다. 안전한 opaque cursor와 next 존재 여부를 남긴다. 제거 항목·sanitizer 버전을 기록한다. 보안 정제 후의 원본이며 원래 HTTP byte-identical 사본이라고 부르지 않는다.
4. 정제할 수 없는 응답은 원문을 로그에 노출하지 않고 안전한 실패 메타데이터만 남겨 `evidence_unavailable`로 표시한다. 원문을 무조건 저장한다는 약속은 하지 않는다.
5. 안전하게 저장한 응답은 parser 오류가 있어도 격리 보존한다. 파서 개선 후 HTTP 없이 재처리한다. 응답 영속화 직전 전원 차단 구간은 완전히 제거할 수 없으며 unknown으로 남긴다.

### 물리 저장 역할

| DB | 역할 | 갱신 방식 |
|---|---|---|
| control.sqlite | 일정, jobs, lease, binding, lane budget, 커서, coverage | 상태 갱신 가능. 상태변경 audit 보존 |
| capture.sqlite | intent, 안전한 response evidence, capture, failure, processing receipt | 원본 내용 append-only. 처리표시만 별도 갱신 |
| raw.sqlite | 기존 테이블 호환 + 모든 관측/버전/변경/receipt | 이력 append-only, 최신 조회용 projection만 갱신 |

DB 간 외래키/단일 트랜잭션을 가정하지 않는다. 각 DB 내부 FK를 활성화하고, DB 간 request/capture ID와 digest를 검증한다. 단일 인스턴스 process lock과 writer 직렬화, 명시적 SQLite durability 설정을 검증한다. WAL 파일만 빼고 복사하는 백업은 금지한다.

### 주요 테이블 계약(신규 또는 확장)

| 테이블 | 핵심 열 / 키·제약 / 인덱스 |
|---|---|
| roster_versions / roster_members | roster_digest PK; member PK(digest,account_key), UNIQUE(digest,ordinal); 원본 명단 hash·294개·국가 수 검증 |
| collection_epochs | epoch_id PK, kind daily/history/catchup, day_kst nullable, anchor_utc, cutoff_utc, roster_digest, status; daily UNIQUE(day_kst,roster_digest) |
| jobs_v2 | job_id PK, epoch_id FK, account_key, phase, page_seq, lane, predecessor_job_id FK, state; UNIQUE(epoch_id,account_key,phase,page_seq); index(state,lane,priority) |
| request_bindings | job_id FK, attempt_no, request_id UNIQUE, spec_digest; PK(job_id,attempt_no). 기본 1회, 추가 시 승인 근거·retry_of 필요 |
| cursor_checkpoints | PK(account_key,stream,epoch_id), cursor, anchor_post_id, last_receipt, chain_hash, updated_at; history와 daily/catchup 분리 |
| page_evidence_v2 | request_id UNIQUE, job_id UNIQUE, cursor_in/out, observed_at, ordered_ids, terminal_basis, source_evidence_id, raw_receipt; 순환 탐지 index(epoch,account,cursor_in_hash) |
| response_evidence | evidence_id PK, request_id UNIQUE, received_at, http_status, safe_body, body_digest, safe_usage, sanitizer_version, classification; 원본 변경 금지 |
| projection_attempts | PK(evidence_id,parser_version,attempt_no), status/error/finished_at; 실패 재처리 기록 |
| query_observations | request_id PK, evidence_id UNIQUE, account_key, checked_at, outcome, returned_count, field_schema_version, quality_status; index(account_key,checked_at). 성공 빈 페이지도 1행 |
| post_identity | post_id PK, account_key FK, first_seen_observation; account 충돌은 quarantine. 기존 posts는 first-seen 호환으로 유지 |
| post_states | state_id PK, post_id FK, component, schema_version, canonical_hash, full_values_json, field_states_json; UNIQUE(post_id,component,schema_version,canonical_hash) |
| post_checks | check_id PK, request_id FK, post_id FK, observed_at, content/metrics/structure/locator_state_id FK, comparison_status; UNIQUE(request_id,post_id), index(post_id,observed_at,check_id) |
| account_states / account_checks | account_key 기준 위와 동일; 요청마다 profile 미제공도 status 보존; UNIQUE(request_id,account_key) |
| media_structure_members | PK(structure_state_id,position), media_id, post_id; UNIQUE(structure_state_id,media_id). 순서는 버전에 속함 |
| change_events | event_id PK, check_id FK, previous_check_id FK, field_path, old_state/value, new_state/value, kind; UNIQUE(check_id,field_path,kind), index(post_id,observed_at) |
| raw_receipts | capture_id UNIQUE, request_id UNIQUE, evidence_digest, projector_version, committed_at, 정확한 요청별 row_counts |
| coverage_ledger | account_key, epoch_id, root 증거, verified_oldest, terminal_basis, gaps, status; checkpoint와 같은 control transaction에 갱신 |
| migration_ledger | source_database_id, source_table, source_pk, source_digest, target_id; UNIQUE(source_database_id,source_table,source_pk) |

post_states의 state_id가 해당 post/component 소속인지 writer에서 검증하거나 복합 FK로 강제한다. 상태 해시가 같으면 canonical bytes도 대조하고 불일치는 충돌로 격리한다. 시간은 UTC aware로 저장하고 일일 작업 날짜만 KST로 계산한다.

관측 순서는 observed_at + request_id를 사용하되 시계 이상을 quality flag로 남긴다. 뒤늦게 복구한 오래된 응답은 최신 projection을 과거로 되돌리지 않는다. change_events는 재생성 가능한 파생물로 두고 계산 version을 기록한다. 지연 도착이 중간에 삽입되면 영향 구간의 derived change chain을 새 version으로 계산한다. 원래 observation/state는 고치지 않는다.

### 상태 비교 규칙

- returned(0), returned(양수), explicit_null, absent, not_requested를 구별한다. unsupported는 capability 근거가 있을 때만 별도 사유로 기록한다.
- HTTP 실패는 query outcome이며 게시물 필드에 모두 NULL을 쓰는 사건이 아니다.
- absent/not_requested로 과거 값을 삭제하지 않는다. 관측 원형은 그대로 남기고 last-known view에 별도 출처/시각을 붙인다.
- returned→absent는 availability_change이지 caption_deleted나 like_count=0이 아니다. null도 관측된 null이며 실제 삭제라고 단정하지 않는다.
- 값 변경은 모든 추적 필드에 대해 기록한다. caption은 공백·대소문자를 임의 정규화해 변경을 숨기지 않는다. ID/타입/숫자 표현은 schema 규칙에 따라 안정적으로 비교한다.
- content change, metric change, media structure change, locator change를 분리한다. URL만 달라졌다고 새로운 네일 디자인으로 판정하지 않는다.
- profile followers와 post metrics는 같은 request observation 연결을 우선한다. 없으면 분모 미확인, 근접 시각 대체는 파생 분석에서 허용오차·출처를 명시한다.

### INSERT/UPDATE 예시

| 상황 | 실제 저장 동작 |
|---|---|
| 최초 10/20, 좋아요1200·댓글42·캡션A | evidence/query INSERT, identity INSERT, component states INSERT, post_check INSERT. first_seen 표시 |
| 10/21 완전히 동일 | 새 evidence/query/check INSERT, 기존 state FK 재사용, changed=false. 조회 시각 보존 |
| 10/22 좋아요1850·댓글63·캡션B | 새 metric/content state INSERT, 새 check INSERT, 세 필드 change_events INSERT, 이전 state 불변 |
| 같은 request 장애 재처리 | evidence/request digest 대조, 기존 receipt 반환. observation/event 중복 INSERT 없음 |
| 다른 정상 요청으로 같은 페이지 | 새 query/check INSERT. 고유 posts 증가는 0일 수 있음 |
| 성공 빈 페이지 | query INSERT returned_count=0. 게시물 관측 생성 없음. cursor 구조까지 검증돼야 terminal 증거로 사용 |
| 요청 실패/응답 불확실 | failure/query outcome 기록. 성공 observation·커서 전진 없음 |
| 과거 게시물 최초 조회 | published_at은 과거, observed_at은 지금. 지금 반응을 과거 시계열에 삽입하지 않음 |
| partial carousel | 받은 부모/children·순서·partial evidence 저장. 전체 멤버 삭제/완료 추론 금지 |

### 미디어 보관

URL·메타데이터 저장은 이미지 파일 장기 보관을 보장하지 않는다. 추천은 수집기 1차 완성 후 **관측된 접근 가능 이미지/썸네일을 별도 자산 저장소에 보관**하는 단계다. 플랫폼의 압축 이미지일 수 있으므로 업로더 원본 파일이라고 부르지 않는다. 다운로드는 이번에 실행하지 않으며 정책·보관량 승인 후 적용한다.

자산은 content SHA-256, byte_count, mime, downloaded_at, source locator state, 실패 사유를 저장하고 동일 byte 중복 제거. URL만으로 checksum을 추정하지 않는다. 릴스는 전체 영상 저장을 기본 포함하지 않는다. 10만 이미지×평균300KB라면 약30GB(단순 가정), 100만이면 약300GB이며 백업은 별도다.

### 용량과 성능 가정

최신만 매일 294×12=3,528 post checks, 365일에 1,287,720 checks. check+index 평균0.5~1KB 가정 시 약0.64~1.29GB. 상태/계정/이벤트/RAW를 더해야 한다. 최신 page 요청 882×365=321,930개, 안전한 응답 평균10~30KB 가정이면 약3.2~9.7GB/년. history와 기존 capture 중복, WAL, 백업은 별도다. 이 값은 실제 DB 측정 결과가 아니다.

조회는 post_id+observed_at 인덱스, 기간별 observation 집계, change_events 인덱스를 사용한다. 큰 JSON 원본 전체 스캔을 대시보드 기본 쿼리로 쓰지 않는다. 버전 dedup 이득은 실제 1일 샘플의 평균 캡션/children/변경률을 측정해 산정한다. SQLite 처리량·디스크 용량을 이번 설계만으로 보장하지 않는다.

## 8. 커밋·장애 복구 프로토콜

`JOB_BOUND → INTENT_DURABLE → RESPONSE_DURABLE → PROJECTED_WITH_RECEIPT → PAGE_COMMITTED → ACKED`

| 중단 지점 | 재시작 처리 |
|---|---|
| binding 후 intent 전 | HTTP를 보냈다는 증거가 없음에도 자동 재발송하지 않음. binding/intents 원자성 또는 명시적 never_sent 증거를 갖춘 경로만 dispatch 가능 |
| 전송 후 response 영속화 전 | outcome_unknown, 자동 HTTP 금지. 재조회해도 잃은 과거 관측값 복구는 보장 불가 |
| response 저장 후 정규화 실패 | 격리 응답 유지, parser 수정 후 로컬 재처리 |
| capture 저장 후 RAW 실패 | 같은 capture로 트랜잭션 재실행. API 0회 |
| RAW receipt 후 control 실패 | receipt/digest 검증 후 page/checkpoint만 commit. 관측 중복 0 |
| page commit 후 ack 실패 | 기존 receipt/evidence 일치 확인 후 ack만 갱신 |
| 잘못된/순환 cursor | 원본 보존, 다음 페이지 차단, continuity_gap 생성. cursor 임의 조합 금지 |
| rate/auth/usage 실패 | 받은 응답 우선 보존, 후속 HTTP 차단. 복구와 중단 보고는 가능 |
| 자정 전환 | 전송중 요청을 보존 완료 후 우선순위 전환. history checkpoint 유지 |

`raw.sqlite`에서 observations/states/events/receipt는 같은 트랜잭션이다. control의 page evidence/checkpoint/job done 역시 같은 트랜잭션이다. 그 사이의 실패는 동일 ID로 복구하며 세 DB의 한 번짜리 atomic commit을 주장하지 않는다.

request_id는 실행마다 임의 생성해 unknown을 우회하는 키가 아니다. job에 영속 결합한다. 다른 날 정상 재조회는 새 daily epoch/job/request이며 같은 요청 replay와 구별된다. 같은 post_id가 다른 account_key로 나타나면 두 원본 evidence는 보존하되 정규화된 identity 병합은 보류한다.

365일 완료는 고정 anchor-cutoff, rooted cursor chain, 중간 receipt 누락 없음, timestamp 검증 및 실제 cutoff 통과/검증 terminal 근거로 판단한다. API가 그 시점에 제공한 범위의 완전성이지 삭제·비공개 게시물까지 포함한 플랫폼 전체의 보장이 아니다. 단순 MIN(published_at)이나 cursor=NULL만으로 완료하지 않는다. 730일 확장은 새 목표 epoch와 기존 증거 재사용으로 처리하며 기존 365일 결과를 삭제하지 않는다.

## 9. 기존 데이터 이관·백업

1. writer 정지, 기존 원본 경로·파일 크기·schema·행수·hash·SQLite integrity를 기록한다. 실제 원본은 사용자 PC에 있으므로 현재는 대기다.
2. SQLite backup API 등 일관된 snapshot으로 복제한다. 원본 DB/WAL/CSV를 이름만 바꿔 새 운영 DB로 사용하지 않는다.
3. 복제본에서 additive migration. 기존 테이블/행을 삭제하거나 덮어쓰지 않는다. 기존 observations에 실제 있는 시간·request만 이관한다.
4. 과거 snapshot 없는 캡션 변경 이력은 만들어내지 않는다. legacy first-seen 행과 현재값을 구분하고 provenance를 붙인다. 과거 raw response가 있으면 근거 연결 후 재구성한다.
5. migration ledger로 재실행 중복 차단. 원래 각 테이블 행의 정규화 digest multiset·행수·FK·중복·원본 파일 hash를 전후 비교한다.
6. 새 DB를 별도 경로에서 dry-run 및 read-only export 비교. 새 운영 활성화는 승인 후 한 프로세스만 수행한다. 기존 collector와 신 runner 동시 실행 금지.
7. 롤백은 실행 경로를 검증된 이전 버전/스냅샷으로 전환하되 전환 이후 얻은 새 evidence를 별도 보존한다. 이전 DB로 돌아간다고 새 관측을 삭제하지 않는다.

백업은 매일 일관된 snapshot+checksum manifest, 로컬 다른 디스크 및 별도 보관처를 권장한다. 주기적으로 복원본에 integrity/FK/receipt 검사를 수행한다. 외부 업로드 대상·비용·보존기간은 사용자 승인 후 결정한다. 성공 상태·원본 삭제 자동화는 기본 비활성이다.

## 10. 폴더 구조 정리 — 먼저 실행 경로를 하나로

**이번에는 이동·삭제하지 않는다.** import가 파일명에 의존하므로 폴더 미관을 위해 먼저 옮기면 회귀 위험이 커진다.

| 경로 | 최종 역할 및 정리안 |
|---|---|
| raw_v2/ | 현재 코드 위치 유지. public 진입점은 향후 `runtime.py` 하나로 정함 |
| raw_v2/collector.py, daily_scheduler.py | 기존 단일 레인 호환/복구. 새 runner와 동시 실행 금지 표시 |
| raw_v2/three_api_* | 기존 모듈을 역할별 index로 정리. wrapper/중복 기능은 호출 그래프·테스트 확인 후 단계적으로 통합 |
| tests/ | unit, integration, recovery, fixtures로 후속 정리. 기존 runner는 호환 |
| docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md | 이번 설계의 단일 기준 |
| HANDOFF.md / STATUS.md | 짧은 현재 상태 index. 누적 옛 기록은 승인된 문서 정리 단계에서 docs/archive로 이동 |
| REQUIREMENTS.md | latest5 등 오래된 pipeline 문구를 승인된 3×4/2라운드 조건으로 정정할 대상 |
| docs/P6_WORKER_* | 과거 협업 기록. 현재 실행 지침이 아님을 표시하고 추후 archive |
| experiments/, kq3_294_collection/ | 기존 실험/원형 보존. 운영 진입점에서 제외 |
| vision/, taxonomy/, delivery/ | 후속 분석/배포물. RAW 수집과 분리 유지 |
| classic 저장소 | 읽기 전용 비교 기준으로 취급. 자동 동기화·재병합·삭제 안 함 |

새 패키지 전체 재작성이나 src/로의 일괄 이사는 수집기 인수 후 별도 작업이다. 데이터 디렉터리는 Git 밖 `%LOCALAPPDATA%/OHORA_RAW...` 등 승인된 로컬 위치. 이름 변경만으로 기존 안전 거부(`collection.sqlite`)를 우회하지 않는다. 운영 권한·명시적 config·검증된 migration 경로를 설계해 정식 활성화한다.

## 11. 재관측 정책과 분석 연결

| 방식 | 효용 | 비용/제약 |
|---|---|---|
| 최신12개 반복(필수) | 최근 게시물 변화 기본 확보 | 범위를 벗어난 게시물 변화는 계속 추적되지 않음 |
| 최근 N일 전량 | 시계열 균일 | 다작 계정의 페이지 호출 증가, 백필 감소 |
| 급성장 후보 선택 | 제한 예산에 높은 효용 | 후보 밖 트렌드 누락·선택 편향 |
| 혼합 예산 | 신규 과거와 반응 추적 절충 | budget 정책과 실제 지원 조회 경로 검증 필요 |

**현재 필수 범위는 최신12개 + 백필로 유지한다.** 최신 밖 장기 재관측은 후속 기능으로 분리하고 묵시적으로 백필 예산을 전용하지 않는다. 추천 확장은 최근30일 및 급성장 후보에 남은 예산 일부를 배정하는 방식이며 비율은 실측 후 승인한다. 오래된 개별 타 계정 게시물을 직접 ID로 조회할 수 있다고 가정하지 않으며, Business Discovery 페이지 재탐색 비용까지 포함한다.

향후 analysis DB는 post_id/media_id, source_state_id 또는 asset_sha256, model/version, taxonomy_version, design_pattern_id, analyzed_at을 연결한다. 재분석 결과는 새로운 analysis version이며 RAW 수정 권한이 없다. 매번 좋아요만 바뀌거나 F5를 누른다고 이미지 분석을 재실행하지 않는다. 디자인 생애주기는 관측 시작 전 구간의 검열/결측을 명시한다.

## 12. 구현 작업 단위·검증·예상 공수

아래는 **집중 개발·검수 시간의 거친 범위**이며 완료 약속이 아니다. 실제 Meta 대기·사용자 PC 접근·3레인 권한 이슈는 별도다. 먼저 P1/P2를 끝내고 실측 재산정한다.

| 단위 | 내용 | 예상 | 완료 기준 |
|---|---|---:|---|
| P1 | response 선보존, usage stop 분리, null/partial/계정 필드 계약 | 3~5시간 | invalid/85%/missing usage도 증거 보존, 후속 HTTP0, 3×4 불변 |
| P2 | 관측·상태·변경 모델 및 clone migration | 4~7시간 | 동일/변경/누락/A→B→A/미디어 순서/지연 replay 회귀, 기존행 보존 |
| P3 | 날짜별 jobs, history contract, 영속 커서/coverage | 4~7시간 | 3일 rollover·중단/재개·365/730 cutoff 합성 검증 |
| P4 | 실제 runner wiring, 3DB recovery, operator summary | 3~5시간 | 각 commit 경계 장애주입, 294/882 합성 E2E, 중복HTTP0 |
| P5 | 문서·실행 경로 통합, Windows 및 이관 인수 | 2~4시간 | offline full suite, Windows, 원본 복제본 비교, 복원 drill |
| P6 | 승인 후 제한 실운영 → 294개 →20시간 | 작업 2~4시간 + 관측기간 | 실제 cursor/usage/필드/재시작 증거, 20시간 실행은 최소20시간 경과 필요 |

P1~P5 약16~28시간, 실운영 인수는 별도. 현재 문서가 제시한 537 테스트는 새 설계의 완료율이 아니며 테스트 개수로 공수를 역산하지 않는다.

필수 인수 시나리오: 정상 새 게시물, 동일값 재조회, 필드 1개 변경, 수치 감소, 0/null/absent/not_requested, partial children, empty page, duplicate request, cross-account ID 충돌, cursor 누락/순환/위조, 계정 실패와 전체 최신 예외 종료, usage85/missing header, token 오류, 응답 직후 전원 차단, capture→RAW/RAW→control crash, 자정 전후, old response 지연 복구, disk full, backup restore, 명단 hash 변경 차단.

전체 기능 인수는 GitHub 반영·오프라인/Windows 회귀·운영 복제본 원본 보존·승인된 실제 수집/복구 정상까지다. 이 수집기는 Vercel 배포 대상이 아니므로 Production URL/SHA는 **미적용**. 나중의 대시보드 배포와 혼동하지 않는다.

## 13. 승인 항목과 다음 단계

필요한 결정은 우선 **이 설계를 기준으로 P1부터 구현할지** 하나다. 3레인·4개, 기존 원본 유지, p6 단일 제품, latest→catchup→history는 결정된 조건으로 재질문하지 않는다.

별도 시점 승인: 실제 Meta 호출/운영 DB 전환/main 병합, 저장소 비공개 전환, 이미지 파일 다운로드·외부 백업 비용/전송, 최신 밖 재관측 예산 변경. 지금 설계에 비용 발생 서비스나 새 배포 프로젝트는 필요 없다.

설계 승인 전에는 기존 코드를 수정·정리·실행하지 않는다. 문서 기록은 구현 승인으로 해석하지 않는다.

## 14. 검증 기록과 인수인계 기준

- 검토 소스 SHA: `77bde11a31833c298134d0215ea8aa7c539067f4` (문서 포함); 기존 CI 코드 `c788ef3437c8f7e28d8dc193013361dedaa38123`의 537/537은 저장소 문서상 Ubuntu/Windows 증거.
- 이번 재실행: Linux Python 3.12.14, `python tests/run_offline.py`, 소켓 차단 전체 suite. **537/537 PASS, 40.870s, 실패/오류 0**, 프로세스 exit 0. 새 설계 미구현에 대한 통과가 아니다.
- 별도 합성 확인: build_request_spec 요청식에 계정 followers/media_count 없음; explicit NULL normalizer 거부 재현. 실제 Meta 요청 없음.
- 변경 금지: 운영 SQLite/CSV/RAW·명단·토큰, main 코드, classic 소스. 이번 산출물은 설계/상태 문서만.
- 다음 채팅: 이 문서 → 최신 HANDOFF/STATUS → AGENTS/REQUIREMENTS → 실제 branch HEAD 순으로 대조. 설계 승인 상태부터 확인한다.
- 원본 위치는 사용자 로컬이며 이 환경에서 백업·이미지 파일 보존·실수집 검증을 수행했다고 주장하지 않는다.

## 근거 소스

- `raw_v2/three_api_plan.py`, `three_api_coordinator.py`, `three_api_daily_orchestrator.py`
- `raw_v2/three_api_runtime_contract.py`, `three_api_contract_executor.py`, `three_api_request_spec.py`
- `raw_v2/three_api_transport.py`, `three_api_graph_http.py`, `three_api_meta_normalizer.py`
- `raw_v2/three_api_capture.py`, `three_api_job_binding.py`, `three_api_adapter_guard.py`
- `raw_v2/three_api_raw_writer.py`, `three_api_evidence.py`, `collector.py`, `daily_scheduler.py`
- `tests/run_offline.py` 및 대응 `tests/test_three_api_*.py`
- 사용자 첨부 `gpt6와 나의 의견 아스트라검토 후 재설계.txt` 전체 및 대화 이미지

## 15. 확정 추가 요구 — 동영상·릴스 URL 보존과 향후 n8n 연결 (2026-10-20)

사용자 확정: **현재 수집기 설계에 동영상·릴스 URL 보존을 포함한다. 영상 다운로드·프레임 추출·네일 판별은 후속 n8n 작업에서 구현한다.** 3 API 레인 및 게시물 limit=4는 불변. 지금 영상 기능을 이유로 별도 Meta 요청이나 CDN 접근을 실행하지 않는다.

### 수집·저장할 항목

| 항목 | 보존 의미 |
|---|---|
| post_id, account_key, media_id | 게시물·계정·개별 영상의 안정적인 연결 키 |
| media_type | API가 반환한 IMAGE/VIDEO/CAROUSEL_ALBUM 등 원래 값 |
| media_product_type | 반환 가능성이 확인된 경우에만 원래 값 보존. VIDEO만으로 REELS라고 단정하지 않음 |
| media_url | API가 실제 반환한 영상 미디어 주소. 다운로드 성공이나 영구 접근을 뜻하지 않음 |
| thumbnail_url | 실제 반환된 대표 이미지 주소. 영상 URL과 별개로 보존 |
| permalink | 사람이 게시물을 열 수 있는 주소. 영상 파일 주소 대신 사용하지 않음 |
| published_at, observed_at | 게시 시각과 URL을 확인한 시각을 분리 |
| parent_post_id, item_order | 캐러셀 속 영상도 개별 media_id와 당시 순서로 추적 |
| request_id, evidence_id, locator_state_id | 어떤 응답에서 얻은 주소인지 원본·관측·URL 버전과 연결 |
| field_status, availability_reason | 주소별 returned/null/absent/not_requested; 검증된 unsupported와 원인 미확인을 구분 |

이미 요청하는 media_url/thumbnail_url/permalink를 우선 재사용한다. media_product_type은 현재 RequestSpec/정규화 allowlist에 없으므로 기존에 수집된다고 주장하지 않는다. 관련 API 버전·권한·필드 지원을 확인한 후 호환 변경을 적용하고, 미지원이면 unknown으로 둔다. duration·해상도·파일 크기·코덱은 현재 Meta 필수 요청에 추가하지 않으며, 향후 n8n 다운로드 후 측정값으로 별도 저장한다.

### 저장 규칙

- 모든 성공 재조회는 observation을 생성한다. 같은 URL이면 locator state 재사용, URL이 달라지면 새 state와 locator_change를 추가한다. 이전 주소를 덮어쓰지 않는다.
- URL이 빠졌다고 이전 URL을 삭제하지 않는다. 최신 관측의 누락 상태와 last-known URL/관측 시각을 별도로 제공한다. last-known을 현재 접근 가능 주소로 표시하지 않는다.
- media_url/thumbnail_url/permalink 및 안전한 서명 파라미터는 로컬 데이터 영역에만 보관한다. 임의로 URL query를 제거해 접근 주소를 망가뜨리지 않는다. 반면 Graph access_token·인증 헤더가 포함된 값은 비밀정보 제거 정책을 적용하며, 사용 가능한 형태로 보존할 수 없으면 credential_redacted_unusable로 표시한다. 실제 URL은 GitHub·공개 로그·설계서에 넣지 않는다.
- URL 만료 시각은 API가 신뢰할 수 있게 제공했을 때만 기록한다. 임의 TTL을 부여하지 않으며 expires_at 기본값은 NULL이다.
- 수집기는 URL 확인용 HEAD/GET을 보내지 않는다. download_status 기본값은 not_attempted, access_status는 unchecked다. returned는 URL 필드가 왔다는 뜻이지 다운로드 검증 완료가 아니다.
- URL 저장은 영상 파일 보존이 아니다. n8n 작업을 훗날 시작할 때 URL이 만료됐을 수 있으므로 장기 영상 보존을 보장하지 않는다. 분석 단계가 가동된 뒤에는 새 관측 URL을 가능한 한 신속히 처리하는 구조가 적합하다.
- 캐러셀에 이미지와 영상이 섞이면 각각 별도 locator를 보존한다. 부분 children에서도 받은 영상 주소는 남기되 전체 수집 완료로 표시하지 않는다.

### 향후 n8n 입력 계약 — 현재는 연결 명세만

읽기 전용 video_candidates view/export를 설계한다:
post_id, media_id, account_key, parent_post_id, item_order, media_type,
media_product_type, media_url, thumbnail_url, permalink, observed_at,
locator_state_id, request_id, url_field_status, access_status.

VIDEO이면서 사용 가능한 형태의 media_url이 반환된 항목을 처리 후보로 제공한다. URL 없는 VIDEO도 unavailable 목록에 남긴다. 썸네일만 있는 항목은 thumbnail_only로 구별하며 영상 분석 완료로 처리하지 않는다. 최신 후보는 저장 commit 시각이 아닌 observed_at 기준으로 선택해 지연 복구가 과거 URL로 되돌리지 않게 한다.

향후 n8n은 별도 analysis DB에 다운로드 시도/오류/실제 자산 SHA-256/프레임 시각/분석 버전을 기록한다. 영상 다운로드 실패가 RAW 수집을 중단시키거나 RAW 이력을 수정하지 않는다. 만료 의심은 refresh_requested로 남기되 n8n이 직접 Meta를 무제한 재호출하지 않는다. 추가 조회가 필요하면 기존 collector 예산·중복 방지·승인 정책을 거친다. URL 변경만으로 같은 영상 분석을 반복하지 않도록, 파일 확보 후 asset hash와 분석 버전으로 중복을 판단한다.

### 추가 인수 기준 및 범위

구현 시 회귀 항목: VIDEO URL 반환/누락/NULL, thumbnail_only, 캐러셀 영상 순서 변경, 동일 URL 재관측, URL 교체, 과거 URL 지연 복구, 비밀정보 URL 차단, 미확인 만료 상태, REELS 식별 미지원. 모두 합성 fixture로 우선 검증한다. **이번 변경은 설계 문서만이며 신규 코드·영상 다운로드·n8n 분석·실 Meta 검증은 미구현/미실행**이다. 기존 P1 요청/정규화 계약과 P2 locator 관측 저장 작업에 포함한다.
