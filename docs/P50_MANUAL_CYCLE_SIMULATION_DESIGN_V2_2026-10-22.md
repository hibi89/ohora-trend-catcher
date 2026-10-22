> **SUPERSEDED FOR P50 GOAL/THROUGHPUT DECISIONS — 2026-10-22:** [목표 역산·변수/인과/민감도 기반 최종 v3](P50_GOAL_DRIVEN_SIMULATION_ARCHITECTURE_V3_2026-10-22.md)가 현재 기준이다. 본 문서의 이전 10개 장애 시나리오·테스트 명세는 후속 호환 참조로 보존한다. 기존 동결 설계는 유효하며 실제 코드 구현/Meta 검증은 완료되지 않았다.

# OHORA Trend Catcher P50 — 수동 최신·365일 과거 수집 설계 보완 v2
> **공시용 구현 계약 / 2026-10-22 KST / 시뮬레이션 도출 결함 10개 반영**  
> **상태: 설계 작성·공시, 구현 및 운영 검증 대기. 실 Meta 성공·실제 수집 완료를 의미하지 않음.**  
> 기준: P50 브랜치 `feature/p6-50-account-pilot-20261022` (문서 작성 기준 `f22d81b`), n8n 브랜치 `codex/p6-readiness-hardening-20261019` (문서 작성 기준 `c835bcb`). 이후 커밋은 재검토한다.  
> 방법: [Simulation skill](https://github.com/hibi89/ohora-trend-catcher/blob/feature/simulation-skill-20261022/.agents/skills/simulation/SKILL.md), 코드 읽기 및 상태 전이 사고 실험. 실행·데이터 수정·Meta 호출 없음.

## 0. 승인 경계, 동결 정책, 목적

**목적**: 소유자가 오늘 Windows PowerShell로 50개 계정(US18/KR16/JP16)의 최신 수집을 실행하고, 365일 과거 수집을 수행하다 중단한 뒤, 내일 새 KST 날짜의 최신화를 완료하고 **바로 이전의 검증된 과거 커서**에서 이어 받는 사이클을 반복할 수 있게 한다. 실제 365일 전체 수집을 오늘 완료한다는 뜻이 아니다.

- 기존 `DESIGN_FREEZE_NOTICE.md` 및 `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`는 **수정·대체·해제하지 않는다**. 본 문서는 해당 설계를 실현하는 **P50 전용 구현 보완 계약**이다. 충돌이 발견되면 구현을 멈추고 소유자의 추가 판단을 받는다.
- 원본 294계정 및 공식 `account_list.csv`, P294 운영 DB, 기존 `raw_v2/collector.py` 배치 POST 엔진, 기존 Capture/RAW는 그대로 보존한다.
- Meta A/B/C **3개 레인**, 게시물 요청 `limit=4`, 매일 최신 **2라운드 / 계정당 최대 3페이지(12개)**, 최신→필수 누락 복구→과거 백필, 원본 무손실, 85% 정지, 최소 65분 쿨다운, 불명확 HTTP 자동 재전송 금지 고정.
- **Batch POST의 P50 통합은 이번 긴급 작업에서 제외**한다. 기존 직렬 실행기는 n8n 5분 주기에 묶이지 않으므로 CLI의 승인된 `max_pages` 범위 내에서 연속 실행할 수 있다.
- 이 문서의 **설계 승인/공시 ≠ 소스 구현 완료 ≠ 오프라인 PASS ≠ 실 Meta 검증 ≠ 자동 스케줄 승인**이다.
- 실제 Meta 요청, 운영 DB/RAW 변경, 기존 hold 수동 해제, n8n 스케줄 활성화, main 병합은 각각 별도의 명시적 운영 승인과 사전 안전검사가 필요하다. 문서 자체는 어떤 실행도 허용하지 않는다.

## 1. 현재 구현과 구조적 공백

| 항목 | 실제 경로 | 2026-10-22 판정 |
| --- | --- | --- |
| P50 최신 수동 실행 | `raw_v2/three_api_p50_manual_pilot.py`, `three_api_live_serial_step.py` | A/B/C 직렬 최신 호출 경로 구현, 실 Meta 미검증 |
| 최신 작업 생성 | `raw_v2/three_api_p50_coordinator.py` | 50×3 작업만 생성; history page4+ 생성 불가 |
| 공통 작업 계약 | `raw_v2/three_api_coordinator.py`, `three_api_runtime_contract.py` | jobs page1..3, phase=front/catchup; history는 현재 **offline-only** |
| 과거 수집 상태 | `raw_v2/three_api_p50_history_store.py` | `history.sqlite` 초기화/영수증 기반 변경 함수 존재; 실요청 어댑터 미연결 |
| 과거 수집 계획 | `raw_v2/three_api_p50_history_plan.py` | `network_requests=0`; 상태 탐색 전용 |
| Capture→RAW | `three_api_capture.py`, `three_api_raw_writer.py`, `three_api_job_binding.py` | 영수증 증거 구조 보존; **과거 요청 통합 경로** 미완성 |
| n8n | 별도 Codex 작업 브랜치 | P50 브리지/읽기 재검증 준비, 실제 새 history runner 연결 미완성 |
| 오프라인 CI | GitHub Actions | 각 SHA별로 결과 확인; 하나의 부분 PASS로 전체 PASS 주장 금지 |

**최우선 BLOCK**: 지금 있는 history 계획/상태 저장소에 실 Meta history 요청을 붙이는 안전한 P50 전용 실행 경로가 없으므로 오늘 저녁 무인 history CLI가 동작한다고 선언하면 안 된다.

## 2. 사용자 운영 여정과 증거 기반 완료 정의

```text
[사용자 latest 명령 + 명시적 승인]
  → (전날 미완료/불명확 intent 스캔; Capture/RAW는 재호출 없이 복구)
  → [현재 KST 날짜의 P50 latest page1~3: A/B/C]
  → [어제 latest anchor까지 REQUIRED_CATCHUP 또는 계정별 예외표시]
  → [저장된 새 latest 증거 + history 시작경계 확인]
  → [사용자 history 명령 + 명시적 승인]
  → [별도 history 작업/영속 cursor → bounded serial pages]
  → [쿼터·승인·외부 경쟁·디스크·커서 오류 시 안전 중단]
  → [내일 KST latest 우선 → history 독립 cursor로 재개]
  → [계정별 365일 cutoff 검증/terminal 확인 시 개별 완료]
```

각 **성공 페이지**는 `intent + capture_id + RAW receipt_id + 관측 증거 + 작업/커서 영속 커밋`의 일치로만 판정한다. HTTP 200, 50/50 계정 방문, SQLite 파일 생성, test fixture, n8n import 성공만으로 페이지 성공이라 하지 않는다. 관측 시점의 현재 반응 수치를 과거 게시 당시의 값으로 오인하지 않는다.

**운영 결과**는 `latest_eligible/complete/exception`, `catchup_pending/complete/exception`, `history_ready/continuation/held/covered/terminal`, `quota_wait/unknown_http/needs_operator`를 분리해 출력한다. 오류 계정을 숨긴 `50/50 완료`는 금지한다.

## 3. 수정된 데이터/상태 모델

### 3.1 분리된 지속 상태

- `p50/p50_raw.sqlite`: 현재 원본/게시물/관측/영수증의 기준 저장소. 기보존 행을 삭제·덮어쓰기하지 않는다.
- `p50/YYYY-MM-DD/coordinator.sqlite` + `capture.sqlite`: 해당 KST 일자 latest 및 명확한 요청 의도·레인 게이트·수신 결과. 과거 일자의 bound/capture를 삭제하지 않는다.
- `p50/history.sqlite`: **365일 고정 컷오프**·계정별 verified history cursor·연속성 증거·history 전용 영속 요청 작업과 레인 할당·재개 대기열. 현재 저장소를 확장하되 마이그레이션은 백업·오프라인 테스트 후에만.
- history 실행은 `account_key`, `history_epoch`, `history_seq`, `request_id`, `lane`, `requested_cursor`, `next_cursor`, `anchor_capture_id`, `capture_id`, `raw_receipt_id`, `status`, `quota_group` 등의 명시적 관계를 보유한다. 현재 `history_receipts`만으로 충분하다고 가정하지 않는다.
- 같은 계정의 `latest anchor`와 `history cursor`는 물리적으로 별개다. history 커서는 오직 검증된 history page의 commit에서만 전진한다.
- `history_epoch`는 중단·다음 날 실행으로 증가시키지 않는다. 커서 무효화 후 승인된 재앵커를 시작하는 경우에만 새 복구 체인을 추가하고 이전 영수증·이력은 그대로 남긴다.
- 모든 장기 상태에는 roster digest, 계정 identity, 최초 고정 cutoff(초기 KST 기준 365일 전), 스키마 버전이 묶인다. 명단/순서/스키마 변경은 조용한 커서 재사용이 아니라 정지 + 호환성 검토.
- KST 날짜와 UTC 요청·수신·관측 시각은 각각 저장한다. cutoff는 처음 확정한 기준으로 고정하고 매일 전진시키지 않는다.

### 3.2 history 최초 앵커의 정합성 (P50-02/05)

- history에 들어갈 첫 request cursor는 임의 `null`이 아니다. **당일 최신 page1→page2→page3의 실제 검증된 연속 마지막 페이지가 제공한 `next_cursor`**를 history seed로 사용한다.
- 앞 페이지가 terminal이면 이어갈 HTTP 요청 자체가 없다; terminal 근거를 history 완료로 투영한다. 미완료·앵커 누락·다른 레인에서 받은 커서의 재사용 가능성 미검증은 `BLOCKED_ANCHOR`로 둔다.
- 기존 `history_plan`이 반환하는 `ready,cursor=null`은 **seed 필요 상태**이지, 바로 history live 전송해도 된다는 뜻이 아니다.
- 기존 `RequestEnvelope(history)`는 `history_offline_only=True`와 비-null cursor가 요구되므로 그대로 실 Meta 호출을 시도해서는 안 된다.
- 구현 방안: P50 전용 **역할이 분리된 history request 어댑터/typed capability**를 만들고 기존 런타임 계약·필드·영수증의 엄격한 검증을 재사용한다. 신규 실행 권한은 `P50 전용 workspace + 명시적 live 승인 + 검증된 anchor + 영속 request binding`에만 부여한다. **P294 또는 임의 offline fixture에 실호출 권한이 번지지 않도록 거부 테스트 필수.**
- `Coordinator.jobs`의 page<=3과 phase front/catchup을 무단 완화하지 않고, P50-history 전용 job/lease namespace를 사용한다. 영구 백필용 page4+가 일일 snapshot jobs를 오염시키지 않는다.

### 3.3 요청·응답·복구 (P50-13/14/15)

정확한 순서:
1. `history_job`을 원자적으로 lease하고 계정, 날짜, 레인, cutoff, cursor, quota/승인/독점 상태 확인.
2. 불변 `request_id` 및 메타 정보를 **HTTP 전에** history request binding으로 영속 기록. 프로세스가 이 지점 이후 종료되면 추정해서 재발송하지 않는다.
3. 기존 Capture의 `intent`와 전송 결과 경계를 재사용. HTTP 반환 직후 안전 정제된 response evidence를 우선 영속화하고 정상화/필드검증 실패 시에도 가능한 증거를 보존한다.
4. 검증된 Capture → RAW 영수증 → 관측 생성 → history_checkpoint 순으로 진행하되, DB 사이에 단일 ACID 트랜잭션이 없다는 사실을 인정하고 **idempotent reconciliation**을 구현한다.
5. `Capture만 존재`: 같은 request로 HTTP **0회** RAW 재처리 후 checkpoint.
6. `RAW 영수증 존재, history checkpoint 없음`: Capture/RAW digest, request_id, account_key, epoch, prior cursor가 모두 일치하는지 확인하고 HTTP **0회** checkpoint 재적용.
7. `intent만 존재/HTTP 결과 불명`: `blocked_unknown_http`; 소유자 별도 명시 판단 없이는 재전송, 새 request_id 우회, 자동 cursor 변경 금지.
8. `checkpoint만 존재, RAW 없음`: 일관성 실패·전체 쓰기 차단 + 원본 수동 감사. false 완료 금지.
9. 재시작 시 `reconcile_existing → latest due? → required catchup → history` 순서. 로컬 증거 재처리는 가능한 경우 quota hold 중에도 HTTP 없이 수행.
10. 커서 전진은 `expected_cursor` CAS, `capture_id`와 `raw_receipt_id` 중복 unique, 영수증에서 **직접** 확인한 타입·요청 바인딩에 의해 이루어진다. 호출자가 건넨 `raw_receipt_verified=True` 같은 불리언 **하나만 믿어서는 안 된다**.

### 3.4 커서 무효·삭제·반복 (P50-05/06/19)

- `next_cursor==current_cursor`, 동일 epoch에서 과거 등장한 cursor fingerprint 재등장(A→B→A 포함), 잘못된 계정/caller/cutoff/epoch cursor, 페이지 순서 역행, 무한 empty-page 체인은 **즉시 BLOCK**.
- 복구는 이전 Capture→RAW 영수증 chain을 readback하여 마지막 검증된 edge부터. 단순한 파일 내 커서 삭제가 감지되면 신규 front `after` 값으로 덮어쓰지 않는다.
- Meta Graph가 커서를 만료했다고 **명시적으로** 반환한 경우만 제한된 재앵커 후보로 분류. 최신 head에서 bounded overlap scan으로 저장된 과거의 검증 anchor/post ID에 도달했음을 증명한 뒤 별도 `history_epoch`로 이어간다. 증명이 불가능하면 coverage gap 표시 + 운영자 검토. 무근거 "이어졌다" 금지.
- 고정 cutoff 날짜 이전 게시물 도달, 검증 terminal 및 zero posts, 빠진 timestamp, 동일 timestamp, 삭제로 인한 페이지 축소를 구분. `oldest_post_at < cutoff` 판정은 **해당 계정의 루트부터 연속된 검증 chain**에서만 적용한다.
- 결측 시각이나 커서 불명은 `coverage_unknown`이지 `history_complete`가 아니다.

### 3.5 날짜 전환·12개 초과 catchup (P50-03/17/28)

- KST 새로운 날짜에는 **새로운 일일 latest 작업 epoch**을 만든다. 이전 `history.sqlite` cutoff/cursor/epoch/영수증을 초기화하지 않는다.
- 자정 이전 이미 발송한 요청은 `day_kst`와 관측시간을 별도로 기록해 어제 작업으로 조정/복구; 오늘 최신 성공으로 전용하지 않는다.
- latest 3페이지에서 전일 저장 최신 게시물의 고유 ID(anchor)가 확인되지 않으면 `required_catchup` 별도 상태를 만든다. 실제 예: 밤새 20개 게시 시 최신 12개만으로 전일 anchor 연결 불가.
- 기존 3페이지의 한계를 최신 작업에서 몰래 늘리지 않고, 추가 누락 구간은 **별도 bounded catchup**에서 원본·커서·영수증으로 증명한다. anchor 도달 또는 명확한 coverage exception 이전에는 해당 계정의 daily continuity를 "완료"로 선언하지 않는다.
- 필수 catchup은 history보다 우선한다. 한 계정의 계정별 terminal 실패는 예외로 표시하여 다른 정상 계정의 과거 수집을 무기한 굶기지 않도록 정책상 제외한다. 전체 무손실 증명은 실패 계정까지 성공으로 세지 않는다.

### 3.6 API 레인·쿼터·동시 실행 (P50-08/09/16/27)

- 기존 각 레인의 85% 사용량 정지, 65분 cooldown, 명시적 안전한 재개 증거 규칙 보존. 시간 경과만으로 강제 해제 금지.
- 정상 **HTTP 200 + 85%** 응답은 저장 증거를 남기고 다음 호출만 금지한다. 안전한 usage header가 없으면 증거는 보존하되 추가 호출은 차단.
- A/B/C와 P50/P294/긴급수집기 간 **실제 token/app/business quota group 독립성은 확인 전 UNKNOWN**으로 취급. 같은 quota group을 공유할 가능성이 있으면 하나의 소유자/운영 제어 경로만 활성. SQLite P50 락만으로 다른 작업 폴더를 락했다고 주장하지 않는다.
- P50 latest와 history에는 **동일한 cross-mode P50 실행 lock** + 동일 계정·레인 전송 게이트 사용. 수동 명령/두 PowerShell 창/n8n 중복 실행 시 두 번째 실행은 명확히 거부.
- history 작업 분배는 미완료 50계정 **영속 round-robin**. 매번 첫 정상 계정부터 `max_pages=1`로 선택하면 뒷 계정 starvation이 생기므로 `last_served`/순환 순번을 저장한다. blocked 계정은 별도 표시하고 나머지 진행.
- Windows에서 무인으로 오래 실행하려면 명시적으로 시작한 **local bounded runner**만 사용. 예: 1회 호출→영수증 검증→다음 가능 작업 탐색→승인된 쿼터/시간 제약에 맞는 WAIT. unknown HTTP/사용량 미확인/인증 실패는 자동 루프 중단·운영자 개입. 단순 무조건 sleep 65분 뒤 강제 reset 금지.
- 기존 Batch POST는 보존하되 P50에 오늘 통합하지 않는다. 운용 실측과 호환성 검증이 완료되기 전까지 새로운 최적화는 P2.

### 3.7 n8n/Sheets/배포 버전 (P50-24)

- 오늘 CLI 수동 운영 목표는 n8n/Sheets 인증·스케줄에 의존하지 않는다. P50이 만든 데이터는 RAW/SQLite의 검증된 조회 인터페이스로 이후 공급한다.
- n8n 별도 작업자가 P50 pinned checkout/엔진 SHA를 관리하므로 **한 번 실행 시 단일 pinned SHA + 해당 테스트 결과**를 기록하고, 이후 버전 변경 시 인터페이스/스토어 버전 회귀검증을 한다. 버전 불일치 중에는 live n8n 전송을 차단한다.
- n8n 5분당 1페이지는 **해당 스케줄 설정**이지 P50 수동 직렬 실행기의 의무 지연이 아니다. 기존 워크플로 중복 활성 여부 확인 없이 n8n을 켜지 않는다.
- Sheets는 데이터 소비자 전용. observation history는 `request_id:post_id` 기준으로 유지하고 latest view는 `post_id` 기반 파생물이다. RAW receipt 검증 없는 관측을 외부로 전송하지 않고, checkpoint는 Sheets write ack **이후에만** 올린다.
- P294 확장·main 병합은 별도의 검증 단계.

## 4. 사고 실험: 최악의 복합 타임라인

| SIM | 사고 가정 | 안전 결과 / 구현 수용조건 |
| --- | --- | --- |
| S01 | 23:59 history HTTP 발송 → 00:01 응답 확인 전 PC 종료 → 다음 날 latest 먼저 요청 | 이전 bound 불명 요청 자동 재전송 금지; 증거 조사 후 안전한 latest, history 이어받기 |
| S02 | Capture 저장 후 RAW 직전 Ctrl+C → 재실행 | 동일 request_id·digest로 HTTP 0회 RAW 처리, 영수증/커서 1회만 갱신 |
| S03 | RAW 저장 후 history.sqlite commit 전 종료 → 다음 날 3페이지 최신 수행 | history request replay 0회, RAW 재검증으로 커서 복구; 최신과 history cursor 격리 |
| S04 | history cursor A→B→A 반복 + 게시물 0 + Graph next 존재 | 진행도 가짜 증가 금지, cycle/empty-loop 탐지, coverage_unknown으로 차단 |
| S05 | 기존 API C emergency collector가 85%, 새 P50 명령·n8n 동시 실행 | 같은 쿼터 그룹의 추가 전송 중지, 중복 실행 거부, 승인 없는 reset 없음 |

이 표는 정적 추론으로 만든 **기대 결과**이며 실제 테스트 완료가 아니다.

## 5. 위험별 수정 추적표 — 이전 시뮬레이션 TOP10 전부 수용

| 위험 | 수정된 설계 계약 | 담당 | 실구현 상태 |
| --- | --- | --- | --- |
| P50-02 과거 실행기 부재 | P50 전용 history job, request, runner, CLI 실행 경로 | P50 | **TODO** |
| P50-05 최초 null cursor | 검증된 last latest page `next_cursor`로 history seed; offline-only 계약 격리 | P50 | **TODO** |
| P50-13/14 RAW와 history 간 종료 | request binding + no-HTTP reconciliation + receipt readback | P50 | **TODO** |
| P50-03/17 날짜 변경 | KST daily epoch, 과거 bound 회복 후 latest, history cursor 독립 | P50 | **TODO** |
| P50-19 cursor A→B→A | epoch 전체 커서 fingerprint/cycle 검증 + strict stop | P50 | **TODO** |
| P50-28 신규 12개 초과 | latest anchor 확인 및 별도 필수 catchup | P50 | **TODO** |
| P50-08/09 쿼터 공유 | 운영 주체 단일화 + cross-engine/모드 실행 게이트 + quota-group 증거 | P50+n8n | **TODO** |
| P50-15 결과 불명 | 재전송 금지 유지 + 진단/수동 복구·관측상태 표기 | P50 | 기존 차단 있음, CLI 검증 TODO |
| P50-27 수동 연속 수집 | bounded serial loop와 안전 WAIT, 최신/history CLI 분리 | P50 | 최신 bounded 있음, overnight TODO |
| P50-24 SHA 불일치 | 단일 pinned 버전, migration/contract CI 및 activation gate | n8n | 기존 pin 있음, 최신 버전 호환 TODO |

**설계 보완은 이 표의 TODO를 PASS로 바꾸지 않는다.** 완료 주장 전 합성 테스트, Windows 실환경, 승인된 Meta 영수증이 각각 필요하다.

## 6. 단위/통합 검증 게이트 (모두 실행 전에는 NOT RUN)

| 테스트 ID | 준비 데이터·사고 주입 | 필수 PASS 조건 |
| --- | --- | --- |
| T01 | 50개 latest 3페이지 terminal/nonterminal 조합 | account별 올바른 verified history seed; raw receipt 없는 seed 거부 |
| T02 | `ready,cursor=null` + history live 시도 | 임의 무커서 history 금지; seed 또는 terminal만 처리 |
| T03 | RAW commit 다음 줄에서 강제 종료 | 재실행 시 HTTP 0회, 같은 capture/RAW로 checkpoint 1회 |
| T04 | Capture commit 다음 줄에서 강제 종료 | HTTP 0회 RAW와 checkpoint 복구 |
| T05 | 전송 의도 뒤 response unknown | 새 request_id로 우회 전송 0회; explicit operator blocked |
| T06 | 10 history pages → KST 변경 → 3 latest pages | history 11번째 연속 지점 재개, 증거/이력 보존 |
| T07 | cursor A→B→A / empty+next 순환 | 무한루프 0, 상태 block, 마지막 verified cursor 보존 |
| T08 | 당일 신규 게시물 20개 | latest 최대 12개 + 별도 catchup으로 전일 anchor 연결 |
| T09 | A 정상 B held C 85% / 경쟁 P294 C 활성 | quota group 소유권 확인 전 중복 또는 C 요청 0회 |
| T10 | 두 PowerShell + n8n 같은 P50 store | 하나만 lease/dispatch, 나머지 nonzero/busy |
| T11 | 23:59 request, 00:01 Capture | job date와 observed_at 구별, 다음 날 미확정 우선 복구 |
| T12 | roster reorder/username 변경/legacy DB | 데이터·커서 재할당 0회, fail-closed |
| T13 | HTTP200 + usage85, usage null, HTTP400 110/2207013 | 수신 증거 보존 후 안전 중단, known account failure만 격리 |
| T14 | valid history 365일 terminal / missing timestamp / partial carousel | 완료 vs 예외·부분 분리, 현재 반응을 과거 반응으로 오기 금지 |
| T15 | SQL WAL lock/disk full/process kill/Windows ACL | DB 손상 시 쓰기 차단, 확인된 원본 보존, 복구 경로 |
| T16 | n8n pinned SHA 구형 + 새 history schema | 계약 불일치 시 runtime fail-closed |
| T17 | Sheets 부분 write 후 ack 없음 | 재실행 upsert idempotent, history observation 누락 0 |
| T18 | `max_pages=50` history round-robin | 한 계정 편중 0, 모든 eligible 계정에 공정 기회 |

**테스트 보고**: 항상 `branch + exact SHA + command + PASS/FAIL/SKIP + 실제 사용한 fixture + source code/CI URL`을 남긴다. socket-blocked mock PASS는 실 Meta PASS가 아니다. 한국/미국/일본의 실제 승인된 A/B/C 호출과 RAW/Capture readback은 **별도 live gate**다.

## 7. 실제 구현 순서, 팀 분리

**P50 엔진 담당 우선순위**
1. 기존 실패 CI 및 Windows SQLite fixture/진단 정리. 원본 파괴·무근거 hardcode 불가.
2. 최신 50×3 page1/2/3 영수증·성공/실패/terminal·재시작 증거 확인.
3. 신규 독립 history jobs + verified latest seed + P50-only live opt-in contract.
4. 최초 history page → Capture/RAW → history.sqlite checkpoint 실연결.
5. Capture-only / RAW-only/no-checkpoint / unknown HTTP/no-resend 복구, CAS, 지문 cycle 검사.
6. KST daily epoch + REQUIRED_CATCHUP + history 커서 유지·round robin.
7. PowerShell `latest`, `history`, `status`, `recover`에 해당하는 **실제 실행 가능한** 명령 제공. 존재하지 않는 CLI 인자를 문서에서 실행 가능하다고 표시하지 말 것.
8. T01~T15, T18을 오프라인/Windows에서 검사한 뒤 정확한 GitHub HEAD + 롤백 지점 기록.

**n8n 담당 우선순위**
1. P50 manual 수집기 작업 브랜치의 engine/DB/API 계약과 충돌 방지. 당장 중복 history runner 제작 금지.
2. P50/P294/긴급 C 공유 쿼터 소유권 및 동시 스케줄 안전 정지 경로 점검.
3. Pin SHA/receipt readback, n8n 중복 워크플로 차단, T09/T10/T16 검증.
4. n8n 스케줄·알림·Sheets는 수동 수집의 실제 검증 완료 **후** 운영 승인 요청.

**감독자 승인 게이트**
- Gate A: design contract 및 위험 10개 매핑 완료 (**이 문서 단계**).
- Gate B: P50 offline T01~T15/T18 PASS; 회귀 CI PASS; Windows 명령 재현.
- Gate C: 별도 승인 받은 최소 Meta 실호출로 `request_id↔capture_id↔raw_receipt_id↔cursor` 증거 1페이지 이상 확인; A/B/C 쿼터/권한 분류.
- Gate D: 수동 최신 50계정 및 실제 history 시작 + 재시작 검증. 오류·미수집 구분.
- Gate E: P50 n8n/Sheets 통합 및 사용자 승인 후 스케줄 활성화.
- `main` 병합/원본 294 확장은 별도 단계.

## 8. 실행/복구 안전 규칙과 롤백

- 개발 변경은 각 작업자 전담 브랜치에서 수행. 기존 `main`, 원본 SQLite, 운영 RAW, 토큰, 인증 파일, P294 active runtime을 수정하지 않는다.
- history schema 진화 시 쓰기 전 보존본/버전 호환 검사 및 **비파괴 추가 마이그레이션**만 허용. 검사 실패는 모든 새 HTTP/DB write 중단.
- 중단 전 `next cursor`, 요청 의도, Capture, RAW, history checkpoint를 분리해 읽는 진단 명령으로 불일치를 보고한다. 스스로 `DELETE`/cursor reset/`git reset --hard` 금지.
- Graph unknown/permission/rate/invalid cursor에서 강제 재시도 금지. 복구 가능성을 미확인으로 표기하고 승인된 운영자 절차로만 재시도·재앵커 판단.
- 미완성·불명확 상태가 있으면 명시적 `NO-GO`와 이유를 출력하여 밤새 조용히 아무것도 하지 않는 성공 종료로 포장하지 않는다.
- GitHub 공개 저장소이므로 실제 토큰, 계정 식별자, 응답 본문, 로컬 운영 DB/WAL 또는 민감한 로그는 절대 업로드하지 않는다.
- 같은 문제를 두 번 시도해도 개선 없으면 근본 상태 전이 원인을 다시 분석하고 레저 `docs/P6_TROUBLESHOOTING_LEDGER.md`에 기록한다.

## 9. 작업자에게 알리는 상태 선언

> 본 문서의 10개 취약점 수정은 **설계상 요구사항과 검증 게이트로 확정**됐다. 원래의 Meta A/B/C, limit=4, 2라운드, 85%/65분, 안전한 원본 영수증과 기존 P294 동결 설계는 유지한다. **아직 코드로 모두 수정된 것이 아니다.** P50 개발자는 우선순위 1~8에 따라 구현을 진행하며 각 TODO에 영수증/코드/테스트 SHA를 첨부한다. n8n 개발자는 담당 경계를 지키고 실운영 스케줄을 임의로 활성화하지 않는다.

실제 적용 전 소유자 승인/CI·Windows·Meta 증거를 누락한 `READY` 또는 `LIVE SUCCESS` 보고는 허용하지 않는다.
