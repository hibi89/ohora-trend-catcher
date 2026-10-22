# OHORA Trend Catcher P50 — 목표 역산·변수·가상 운영 기반 최종 보완 설계 v3.0

> **2026-10-22 KST · 공식 구현 기준안 / DESIGN ONLY — NOT CODE IMPLEMENTED, NOT LIVE VERIFIED**
>
> 사용자 지시: `simulation` v2.0 스킬로 **최종 목적물이 실제 운용될 때** 입력변수·상호의존성·처리량·병목·임계값·운영 기간·오류·복구까지 계산한 다음 설계를 새로 작성한다.
>
> 스킬: [`.agents/skills/simulation/SKILL.md`](../.agents/skills/simulation/SKILL.md) (v2.0). 이전 [P50 v2 문서](P50_MANUAL_CYCLE_SIMULATION_DESIGN_V2_2026-10-22.md)는 세부 장애 목록으로 보존하며, **P50의 목표/용량/구현 우선순위에 관한 상위 계약은 본 v3을 우선**한다.
>
> 확인 기준: P50 `feature/p6-50-account-pilot-20261022` @ `f22d81b`; n8n `codex/p6-readiness-hardening-20261019` @ `db1c93b`; 문서 작성 후 변경 SHA는 다시 검토. 코드 읽기·수식/시나리오에 근거한 **THOUGHT-ONLY** 분석이다. GitHub/데이터/Meta 실행 없이 도출한 가상 결과를 PASS나 실측치로 보고하지 않는다.

## 0. 범위·우선권 및 완료 상태

- **변경 불가**: `DESIGN_FREEZE_NOTICE.md`, `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`의 294 계정 정책. 기존 P294 저장소/인증/운영 데이터와 Batch POST 수집기 `raw_v2/collector.py`는 손대지 않는다.
- P50 파일럿은 미국 18, 한국 16, 일본 16 계정의 **독립 50계정 스냅샷**, A/B/C 3레인, `limit=4`, 최신 2라운드·최대 3페이지/계정, 최신→필수 catchup→history, 원본/각 관측 영속 저장, 85% 정지와 65분 안전 지연, 무근거 HTTP 재전송 금지.
- 이번 산출물은 **운영 구현 설계**다. 실제 API 호출, 기존 운영 DB/RAW 수정, 쿼터 hold 해제, n8n 활성화, `main` 병합, 동결 설계 변경은 포함하지 않는다. 실운영은 별도 사용자 승인과 Gate 검증이 있어야 한다.
- 별도 정의 없는 기간 목표는 **“오늘부터 무손실 수동 사이클을 시작하고, 이후 API가 허용하는 날에 축적하여 초기 기준일 직전 365일의 API-visible 게시물까지 연속성 증명”**이다. 365일 전체를 오늘/일주일 내 완료한다는 확약은 없다.
- 원본 API가 반환하지 않는 삭제·비공개·접근 불가 게시물까지 복원하는 시스템이 아니며, “모든 과거 게시물”이라는 표현 대신 **API에서 접근 가능하고 계정별 cursor-chain으로 확인한 기간 coverage**라고 명시한다.

## 1. 최종 목적의 역산: KPI와 증명

| 목적 ID | 사용자에게 필요한 결과 | 입증 근거 | 실패/미확인 표현 |
| --- | --- | --- | --- |
| G1 오늘 최신 | 50개 중 접근 가능한 계정의 새 게시물·현재 지표 획득 | 계정별 유효 page, request_id↔Capture↔RAW/observation 영수증, 예외 목록 | `completed`/`unavailable`/`blocked` 개별 |
| G2 오늘 과거 시작 | 최신 tail에서 검증된 cursor로 **실제 첫 history page** 획득 | 검증된 latest anchor + 첫 history Capture/RAW + `history.sqlite` checkpoint | 엔진 미구현이면 `NO-GO`, 빈 plan은 실행 성공이 아님 |
| G3 다음 날 반복 | 새 KST 날짜 latest/필수 catchup 뒤 **같은 과거 chain의 다음 페이지부터 재개** | 원래 history epoch, seq, last receipt, cursor, 날짜별 최신 상태 독립 | 재시작 뒤 cursor gap/unknown이면 차단 |
| G4 365일 coverage | 계정별 검증된 연속 chain이 고정 cutoff 이전 게시물 또는 명확한 terminal까지 도달 | 계정별 root→edge→terminal/cutoff chain 및 저장 영수증 | `50/50 visits`·`N posts`를 coverage 완료로 오인 금지 |
| G5 무손실/안전 | 원본 영수증·이전 관측 불변, 중복 요청·쿼터·권한 위반 0 | fail-closed 감사, 각 고장 경계 테스트, 운영 영수증 | unknown HTTP는 수동 개입; 자동 재요청 금지 |
| G6 운영 효율 | 최신/필수 catchup 이후 남는 안전 처리용량으로 history 진행 | 그룹별 호출 여력·성공 페이지·처리시간 실측 | 여력 0이면 **코드 무오류여도 목표 정체** |

**정확한 달성률**: `account_history_coverage = Σ_i verified_365d(i) / 50`. 단, permission/terminal 예외를 분모에서 임의 제거하면 안 된다. 운영 지원을 위한 별도 `eligible_account_coverage`를 병기한다. 최신 수행률 `latest_receipted/eligible_latest_jobs`, 기간 coverage, API 성공률은 서로 다른 지표다. **증명 없는 목표 달성 확률은 산출하지 않는다.**

## 2. 핵심 변수 모델 — 값·분포·상관관계

`[F]` 코드/동결 계약, `[D]` 논리 파생, `[H]` 시나리오 가정, `[U]` 미관측이다. 분포 미확인 변수는 확률분포를 만들어내지 않는다.

| 인자 (단위) | 값 또는 가능한 범위 | 연동 변수·결과 | 계측/신뢰 |
| --- | --- | --- | --- |
| `A` 계정 수 | 50 (US18/KR16/JP16) | account별 접근권한, 국가별 피드/행동 | [F] roster 계약 |
| `m` 페이지당 게시물 | 최대 4; 실제 0~4 | 게시물 수/요청·추가 page 수 | [F] 요청 limit, 실제 분포 [U] |
| `L_t` 당일 최신 조회 페이지 | 0~150(50×최대3); terminal/차단 시 감소 | 당일 history 여력 감소 | [F] 상한, 관측 [U] |
| `C_t` 필수 누락복구 페이지 | 0 이상; 실제 변동 | 전일 anchor까지 신규 게시물 발생량 의존 | [D], 실제 [U] |
| `N_i` 계정 i의 cutoff까지 접근 가능한 게시물 수 | 0 이상, 계정별 분포 [U] | history 필요 page, terminal·기간 coverage | Meta paging 관측 필요 |
| `Q_g(t)` quota group g의 안전 호출 용량 | **미확인**. A/B/C 독립성도 [U] | 실제 가능한 latest/catchup/history 요청 | usage 헤더·공유자 증거·승인 테스트 필요 |
| `q_i` 계정 i의 history 유효 페이지율 | 0~1; 분포 [U] | 원본당 재시도 불가·zero posts·중복 영향 | receipt/coverage 계측 필요 |
| `p_i(t)` 신규 포스팅 수 | 0 이상·시기/국가/계정별 편차 [U] | `L_t,C_t`, history 창·시간 | 7~30일 계정 관측 필요 |
| `x_g(t)` 타 수집기의 공통 쿼터 사용 | 0 이상·그룹 공유 [U] | P50 가용 `Q_g` 감소 | P294/C/emergency 동시 실행 확인 |
| `d_i(t)` 페이지 지연 (초) | [U], 네트워크·API·원본 크기 따라 변동 | serial wall-clock과 종료기한 | 계측 후 P50/P95 참고, 분포 근거 필요 |
| `s_i` 영속 저장 시간 (초) | [U], WAL/디스크/RAW/Capture 크기 | 요청 간 시간, IO 실패/복구 | Windows 계측 필요 |
| `h_t` API hold/쿨다운 | 85% 정지, 최소 65분 안전 규칙 | 진행 가능 시각, 단순 sleep 자동 해제 불가 | [F] 안전 계약 |
| `u_t` 운영자/PC 가동 시간 | 당일/익일 수동, 야간 절전·강제종료 [U] | 실제 가능한 연속 실행 시간 | 사용자 PC 전원/권한 |
| `r_t` 복구/검증 오버헤드 | 정상 0 이상, 장애 시 증가 [U] | 안전 처리량 감소 | 복구 로그 기반 |
| `f_i(t)` 삭제·비공개·cursor 만료 | 확률/빈도 [U] | history 연결 단절·재앵커·coverage gap | Graph 상태/영수증 |
| `W_t` 저장 공간/권한 | 용량·파일 잠금·디스크 I/O [U] | RAW/WAL/영수증 누락, 모든 작업 차단 | Windows 사전점검 |

**관측 우선순위**: `Q_g`(실제 그룹별 가용량) → `L_t,C_t`(일일 필수 작업량) → `N_i`(실제 과거 페이지 깊이) → `d_i,s_i`(벽시계 지연) → `q_i,f_i`(유효한 시간·coverage), 순으로 불확실성을 축소한다.

### 2.1 인과관계와 공유 원인

```text
계정 게시 빈도 p_i ──→ 신규 게시물/anchor gap ──→ C_t ─┐
         └────────────→ N_i (역사 수요) ───────────────┤
Q_g 안전 한도 ─┬── x_g (P294/긴급 수집기 경쟁) ────────┤
              └── 85% hold / quota reset 증거 ──────────┤
토큰/권한·cursor 만료 ──→ 성공 영수증 q_i / 복구 r_t ───┤
Windows 절전/DB I/O ───→ 실제 실행 시간 u_t,d_i,s_i ───┤
                                                      ↓
                           실제 안전한 history 일일 처리량 H_t
                                                      ↓
                     계정별 검증된 365일 coverage·완료 기간
```

**A/B/C가 3개라는 사실만으로 quota가 3배라는 가정 금지**. token이 달라도 Meta 앱·Business 사용량이 공유될 수 있다. 또한 `Q_t`가 충분해도 레인별 작업 할당, page1→page2 선행 의존, Windows 종료 등이 낮은 단계 병목이 될 수 있다.

## 3. 시간축 이산 사건 시뮬레이션 — 실행 모델

각 `t`(KST 날짜)의 상태를 `(D_t, L_t, C_t, B_t, Q_g(t), lanes_t, holds_t, cursors_i, receipts_i, running)`으로 표현한다.

```text
t0: 50개 로스터 hash/스키마 고정, 각 계정 별 latest/history 및 quota-group 상태 읽기
→ 이미 저장된 미확정 Capture/RAW 무HTTP 복구, unknown HTTP 분류
→ 새 KST 최신 page1..3, A/B/C 및 2라운드 의존성 검증
→ 필요한 전일 anchor catchup; 실패 계정은 원인·coverage 제외 이유 기록
→ history cursor seed (terminal이면 요청 생성하지 않음)
→ eligible 계정에 공정한 history job 분배
→ quota/권한/동시실행/영수증 검증 후 bounded 직렬 전송
→ 성공 Capture→RAW→history checkpoint; 실패/unknown·85%는 안전 홀드
→ 현재 시간·잔여 workload·모든 checkpoint 영속화
→ 00:00 KST 새 latest 우선, history cursor/epoch는 보존
→ 다음 날 같은 수명주기 반복; 50개 계정별 cutoff/terminal 확인될 때만 목표 종료
```

**수요 보존식** (동일 quota group에 단위 page cost 1이라는 가상 단순조건):
- `B_{t+1} = max(0, B_t + new_history_eligible_t − verified_history_pages_t)`. 초기 365일 과거 backlog `B_0`가 존재하더라도 매일 latest 유입을 history 완료로 잘못 빼지 않는다.
- `verified_history_pages_t ≤ max(0, Q_t − L_t − C_t − R_t)` (R_t = quota를 실제 소비하는 안전 요청 예비/복구; 로컬 증거만 재생하는 작업은 HTTP 비용 0이지만 실행 시간 소비).
- quota group이 나뉜 경우 `Σ_{jobs in g} cost_j ≤ Q_g(t) − other_consumption_g(t)` **모든 그룹 제약**을 충족하는 job 배정이 필요. 위의 단일 Q 수식은 상한 추정일 뿐 운영 보장이 아니다.
- 실제 완료일 `T`는 `Σ_{t=1}^T verified_history_pages_t ≥ required_history_pages`와 **계정별 연속 coverage 증명**이 모두 있어야 산정 가능.
- CPU/HTTP/디스크 지연이 별도 병목이면 `Σ(d_i+s_i)+holds+operator_pauses ≤ available_wall_time`가 추가 제약이다.

**운영자가 지정하지 않은 확률분포를 Monte Carlo에 임의 주입하지 않는다.** 범위 시나리오·조건부 임계값부터 계산한다. 나중에 실제 7~30일 운영 데이터가 쌓이면 posting/latency/quota 관측 분포 기반으로 추가 확률 실험 가능.

## 4. 정량 시뮬레이션 — 실제 Meta 용량과 혼동 금지

**모든 숫자는 설명용 입력 가정 [H]이다. 현재 실 Meta 성공률, 토큰당 쿼터, 게시 빈도, 스케줄/쿨다운 성능을 뜻하지 않는다.** 계정별 게시물수가 일정하고 반환 페이지가 중복 없이 꽉 차며, history 영역이 latest와 겹치지 않고, 영수증/커서가 모두 검증된다는 낙관 단순화이다.

### 4.1 게시 빈도 민감도

| 각 계정의 가정 빈도 | 1년간 50계정 posts `50×52×주당 수` | 요청 `Σceil(N_i/4)` |
| --- | ---: | ---: |
| 낮음: 주당 1개 | 2,600 | 650 pages |
| 기준: 주당 3개 | 7,800 | 1,950 pages |
| 높음: 주당 10개 | 26,000 | 6,500 pages |

실 계정마다 게시 수가 다르면 **`Σ_i ceil(N_i/4)`를 계정별로 계산**해야 한다. `ceil(ΣN_i/4)`를 대체식으로 쓰면 계정별 페이지 반올림을 과소평가할 수 있다.

### 4.2 일일 안전 용량에 따른 목표 달성 임계점

기준 `B_0=1,950` history pages, `L=150`, `C=R=0`, 모든 quota group/시간 제약 해소, 유효 성공률 100%라는 계산 예.

| 가상의 하루 총 가용 page 요청 `Q` | 최신 `L` | 과거 여력 `H=Q-L` | 1,950페이지 예상 최단 일수 |
| --- | ---: | ---: | ---: |
| 150 | 150 | 0 | **원칙적으로 history 진행 0** |
| 200 | 150 | 50 | **39일** |
| 300 | 150 | 150 | **13일** |

변수 연쇄반응: `Q=200, L=150, C=20, R=10`인 날 `H=20`, 따라서 **98일**(1950/20 올림)로 지연. 실제 `C_t`, `R_t`는 매일 바뀌므로 한 번 계산한 일수를 고정된 약속으로 사용하지 않는다.

### 4.3 마감일 충족 임계용량

`C=R=0`, `L=150`, `B_0=1950`, 균일 유효 완료를 가정했을 때 **이보다 낮으면 지정된 기간에 완료 불가능**:

| 가상 목표기간 | 필요한 history 페이지/일 이상 | 필요한 총 `Q` 이상 |
| --- | ---: | ---: |
| 7일 | 279 | 429 pages/day |
| 14일 | 140 | 290 pages/day |
| 30일 | 65 | 215 pages/day |

실제 현장에서는 상기 임계치에 누락복구 `C_t`, 실패/예비 요청 `R_t`, 계정별 비균일 page, API 공유 그룹·벽시계 제약의 여유를 별도 추가한다. **Q를 Meta가 실제 허용하는지 현재 [U]**다.

### 4.4 주요 위험에 대한 감도·임계점

| 변수 증가/악화 | 인과적 변화 | 운영상 임계결과 |
| --- | --- | --- |
| `Q_t` 감소 또는 `x_g` 증가 | latest/catchup 이후 잔여 history 감소 | `Q≤L+C+R` 지속 시 **history 영구 정체** |
| `p_i` 증가 | `L,C,N` 동시 증가 | 최신 누락복구 필요 + 365일 완료 지연 |
| `q_i` 감소 | HTTP 성공해도 유효 coverage 처리량 감소 | 미검증 게시물·cursor는 완료에 포함 불가 |
| `d_i,s_i` 증가/PC 절전 | serial 처리 가능량 감소 | 충분한 Q에도 시간이 먼저 소진 |
| history cursor 만료/삭제 | 재앵커·중복 탐색 작업 필요 | chain 증명 불가 시 `coverage_unknown` |
| Windows 전원 종료·RAW partial | no-HTTP 복구 또는 unknown hold | 중단 자체는 허용하되 false success 금지 |

**민감도 순위는 환경에 의존**한다. `Q≤L+C+R`인 상황에서는 Q가 제1 병목이지만, Q가 충분하면 디스크/직렬 시간 또는 cursor continuity가 제1 병목으로 이동한다. 따라서 임의의 전역 중요도 순위를 붙이지 않는다.

## 5. 수정된 목표 최적화 정책

**우선순위는 속도가 아니라 조건부 목표 실현 가능성**으로 결정한다:

1. 원본·승인·불명 HTTP 안전성 위반은 무조건 `STOP`. 데이터 유실을 감수하는 속도 최적화는 허용하지 않는다.
2. 그다음 `G1 최신→전일 anchor 누락복구` 완료. 마감 시간이 촉박해도 어제의 post 경계를 누락하고 history만 진행하지 않는다.
3. 잔여 `Q_g`와 `wall-time`를 **계정별 history fairness**에 분배한다. 50개 중 빠른/먼저 선택되는 계정만 영구 진행하는 정책 금지.
4. quota 부족일 때 history가 0이면 문서를 더 만드는 대신 **실측 quota 공유성 및 누락복구 비용**을 조사하고 목표기간을 조정한다. P50 Batch POST 도입은 호출 지연 병목이 실측으로 입증된 경우에만 후속 제안; Batch POST가 API quota 자체를 3배 늘리지 않는다.
5. `Q`가 안전 제한인지, `CPU/I/O`가 병목인지 분리해 진단한다. 잘못된 병목 최적화는 개발시간만 늘린다.

## 6. 구현 아키텍처: 분리된 latest / history / evidence

```text
PowerShell manual commands: latest / history / status / recover
    │ 명시적 승인, 대상 roster hash, 전역 로컬 실행 락
    ▼
P50 Operation Controller (새 최소 어댑터)
    ├─ latest day_epoch jobs (기존 50×3, A/B/C)
    ├─ REQUIRED_CATCHUP (별도 누락 구간, latest 일일 3페이지 제한 유지)
    ├─ history_epoch jobs / fair queue (신규 P50-only, page4+ 개념)
    └─ quota_group & cross-engine ownership gate
    │
    ▼
Existing strict API transport + intent → Capture → RAW
    ├─ p50/YYYY-MM-DD/{coordinator.sqlite,capture.sqlite}
    ├─ p50/p50_raw.sqlite [원본/관측/receipts]
    └─ p50/history.sqlite [epoch, account cursor, history jobs, receipts, cutoff]
    │
    ▼
Read-only validated status/export → (향후) n8n → Google Sheets
```

### 6.1 계약·스키마·권한 경계

- 기존 `P50Coordinator.seed()`가 50×3 최신만 생성하는 점을 유지한다. 공통 P294 `Coordinator.jobs`의 `page BETWEEN 1 AND 3`, `phase IN ('front','catchup')` 제한을 무단 제거하지 말 것.
- 별도 `P50HistoryStore` 기반 `history_jobs`와 독립 `request_id`/cursor/epoch/continuity/lease를 확장하고, P50 전용 history 실행 capability를 명시적 타입·계약으로 구성한다.
- 현재 `RequestEnvelope.phase=history`는 `history_offline_only=True`와 비-null cursor를 요구하므로 **그 상태 그대로 LIVE에 넣거나 검증 플래그만 억지 변경하는 것은 금지**. P50-only live history 포트와 immutable contract 분리를 설계하고 오프라인 거부 테스트로 P294 범위 확대를 막는다. 동결 공통 계약 수정이 불가피하다면 사전 소유자 승인 후 최소 변경으로 처리.
- 최초 `history cursor=None`은 live 요청 아님. 최신 마지막 검증된 비terminal page의 **실제 next_cursor + request/RAW 영수증**을 seed해야 한다. 최신 마지막 페이지가 terminal이면 terminal 완료, 최신 chain 불명/부족이면 `BLOCKED_ANCHOR`.
- history cutoff는 **최초 승인된 기준일 -365일**로 고정. 매일 새 cutoff 계산 금지. 이후 rolling 365일은 별도 운영제품 설계.
- 동일 `account_key`, `history_epoch`, `verified_seq`, `request_id`, `requested_cursor`, `next_cursor`, `last_raw_receipt`, `root_anchor`, `quota_group`을 연결하는 단일 출처를 명시한다.
- `status`는 `ready/continuation_ready/blocked_unknown_http/blocked_cursor/blocked_unverified_page/window_covered/terminal_verified` 등의 의미를 구분하고, 더 많은 상태가 필요하면 **가법적 DB 마이그레이션 + 복원 테스트**로 확장한다.
- `RAW `↔ `Capture`↔ `history checkpoint` 독립 DB 트랜잭션을 하나라고 주장하지 않는다.

### 6.2 영수증 기반 정확한 진행·충돌 복구

1. 요청 전 검증: 계정/로스터/epoch·전일 anchor·quota usage·다른 수집기의 공유 레인·중복 실행·사용자 승인.
2. `history_jobs` lease + intent/request binding **HTTP 전에 영속화**.
3. API 응답의 안전 정제 evidence를 정상화 전 보존; Capture와 RAW 원본·필드/관측 영수증 검증.
4. `history_checkpoint`는 `expected_cursor` compare-and-swap, 실제 저장소에서 재검증한 Capture/RAW digest·identity·post timestamp·next cursor에 의해서만 전진. 호출자가 준 `verified=true` 불리언만 신뢰 금지.
5. `Capture 존재/RAW 없음`: HTTP **0회** replay와 영수증 readback.
6. `RAW 존재/checkpoint 없음`: HTTP **0회** history checkpoint 재구성; 동일 `capture_id`/RAW ID 중복 적용 금지.
7. `bound intent 존재/응답 없음`: `unknown_http` 유지, 재전송/새 ID 우회 금지; 원본 확인 가능한 경우만 사람이 별도 승인한 절차.
8. `checkpoint 있음/RAW 증거 없음`: 불변식 위반, 새로운 HTTP·커서 전진 중단, 감사.
9. Ctrl+C, 디스크 용량 부족, 파일 잠금, 시스템 종료, 자정 전후 모두 동일 원칙. **의도한 중단은 데이터 손실이 아니지만, 조용한 무한대기나 FALSE PASS는 금지**.

### 6.3 다음 날 전환, 연속성·cursor 안전

- KST 자정이 되면 **날짜별 latest epoch만 신규**; 전날 bound/captured history 상태를 영수증 기반 선복구. 기존 `history_cursor`는 절대 newest front cursor로 덮어쓰지 않는다.
- 당일 최신 12개 내에 어제 저장한 latest anchor ID가 없다면 별도 `REQUIRED_CATCHUP`로 전일 앵커까지 가면서 원본 연속성 증명. 하루 20개 신규라는 가상 상황에서 최대 12개 최신으로 20개 전체를 가져왔다고 주장하지 않는다.
- cursor 없음/삭제/만료/다른 계정, cycle(A→B→A), page overlap, `next`만 반복, 부분 카루셀/게시 시각 역순 모두 chain 무결성 검증. 무결성 실패 시 `coverage_unknown`.
- Graph가 invalid cursor를 명시한 경우에도 최신 head와 **이미 저장된 verified post/anchor** 연결을 bounded overlap으로 증명하지 못하면 재앵커 성공이 아니다. API 제공 불가/삭제 게시물은 실측 범위 밖으로 분리.
- history 작업은 50개 계정에 공정 round-robin. `max_pages=1` 실행을 반복해도 항상 첫 계정만 수행하지 않도록 `last_served`/persisted queue 기준 변경.

### 6.4 API 안전·시간 관리

- quota group 확인 전에는 token A/B/C가 독립이라고 가정하지 않고 **P50/P294/긴급 C 공용 소유자 게이트**를 설치하거나, 동일 그룹 동시 실행을 금지한다.
- 그룹당 사용량 85% 도달 또는 헤더/결과 불명, 인증/권한 오류 시 보존 가능한 수신 증거를 보존한 뒤 추가 요청 정지. 기존 65분 정책은 **최소 제한**이지 자동 hold 강제 해제 허가가 아니다.
- CLI 수동 모드는 n8n의 기존 5분 주기와 독립. 승인된 `max_pages` bounded 직렬 실행을 지원; overnight 로컬 대기는 쿨다운 증거·OS 실행상태·토큰을 확인한 후에만 재개. 별도 기계·프로세스 동시 실행은 막는다.
- 시간 측정: `queue_wait, http_wall, capture_wall, raw_wall, checkpoint_wall, hold_minutes, verified_pages/hour`를 영수증 ID 중심의 **민감정보 없는 관측값**으로 출력. 운영자는 용량 `Q_g`와 SLA 가능성만 나중에 산정한다.

## 7. 복합 시뮬레이션과 안전 결과

| 사고 주입 | 시간/자원에 미치는 영향까지 포함한 안전 결과 |
| --- | --- |
| 23:59 history 발송 → 00:01 응답 전 PC 종료 → 다음 날 latest | unknown HTTP 자동 재발송 0, 증거 우선 복구, 미해결 history 중단; 최신 작업이 과거 cursor를 덮어쓰지 않음 |
| Capture 저장 뒤 RAW 전 종료 | 동일 request/epoch에 HTTP 0회 RAW 재처리; 복구 소요시간은 history 잔여 wall-time에서 차감 |
| RAW 성공 뒤 history checkpoint 전 종료 | 영수증 readback으로 HTTP 0회 checkpoint CAS; 중복 게시물/관측이 새 완료로 중복 집계되지 않음 |
| B 레인 쿼터 정지 + 공용 C 수집기 85% | 당일 최신/catchup 지연 → history 여력 재계산. 그룹 불명은 일단 HOLD, 3배 용량 가정 금지 |
| 고빈도 계정 하루 20개 새 게시물 + 기존 anchor 발견 불가 | 추가 catchup 작업 생성, `C_t` 증가와 history 예상 완료일 자동 갱신, unknown gap 표시 |
| cursor A→B→A 반복 + empty-page next | 무한요청 0, persisted cycle 지문으로 BLOCK, 365일 완료율 변화 0 |
| n8n이 구형 P50 pinned SHA 실행 시도 | 인터페이스/DB 스키마 불일치로 fail-closed, 운영 스케줄 자동 활성 금지 |
| Windows temp/WAL lock 또는 저장공간 소진 | 새로운 HTTP 전송 차단·checkpoint 전진 0, 이미 영속화된 RAW 보존, 복구 시간 별도 계측 |

## 8. 테스트·목표달성 검증 게이트

**현재 모두 DESIGN TEST SPEC / NOT RUN.** 각 테스트는 `SHA + fixture + 실행 명령 + 기대/실제 + PASS/FAIL/UNKNOWN`으로 확인한다. 원래 v2 T01~T18 전체를 유지하고, v3 목표 성능 조건을 추가한다.

| 테스트 | 입력·조건 | 필수 오라클 |
| --- | --- | --- |
| CAP-01 | `Q=150,L=150,C=0,R=0` 가상 30일 | history 수집 0, 완료일 `INFEASIBLE` |
| CAP-02 | `Q=200,L=150`, target1950 단위 | 50 pages/day, 이상적 최소 39일 |
| CAP-03 | `Q=200,L=150,C=20,R=10` | 20 pages/day, 이상적 최소 98일 |
| CAP-04 | 계정별 1/3/10 posts/week 가상 | 650/1950/6500 초기 history page 모델 |
| CAP-05 | A/B/C 토큰 3개지만 동일 quota group | 세 배 요청 허용하는 할당·시간 계산 금지 |
| CAP-06 | 하루 20개 신규 & 전일 anchor 없음 | catchup 우선, history 처리량 감소·목표 갱신 |
| CAP-07 | 365일 과거 완료되지 않았는데 50계정 모두 latest 완료 | latest KPI만 완료, historical coverage 0..미확인 |
| CAP-08 | RAW만 있고 history checkpoint 없으며 다음 날 latest | HTTP 0회 replay, epoch 불변, 커서 정확히 한 번 전진 |
| CAP-09 | 성능은 빠르나 Windows 절전/85% hold 발생 | 시간축에 WAIT 기록, 목표기한 불가능 시 명시 |
| CAP-10 | 데이터 분포·q_i·quota unknown | 근거 없는 `확률 95%`, `완료 13일 보장` 출력 0 |

**기존 장애 시나리오 T01~T18**: latest tail→첫 history; null cursor 거부; 각 crash cut; no HTTP retransmit; 날짜 전환; 반복 cursor; 12개 초과 catchup; quota 경쟁; CLI/n8n double run; source/roster/schema drift; API 85%/missing usage; cutoff/terminal; Win ACL/WAL; n8n pin; Sheets idempotence; round-robin.

## 9. 구현 우선순위: 오늘 수동 실행 가능 경로 먼저

| 단계 | P50 담당 작업 | 다음 게이트 |
| --- | --- | --- |
| S0 | 현 최신 HEAD·CI 이슈 분리, 운영 데이터 무변경, offline failure 수정 | 소스/CI evidence |
| S1 | 50계정 latest 수동 CLI + RAW·Capture·완료 증거 + 계정별 예외보고 | Windows offline PASS |
| S2 | latest tail → 최초 history seed, P50 전용 history live contract/job | CAP-08/T01/T02/T03 |
| S3 | 첫 history 수집→RAW/Capture→cursor→재시작 0-HTTP, 원본 증거 | T03~T07/T11 |
| S4 | KST 다음 날 latest, 필요한 catchup, history resume·fairness | T06/T08/T18 |
| S5 | 그룹 쿼터 경쟁/동시 실행/overnight 안전 WAIT·정확한 status/ETA(조건부) | CAP-01~10, T09/T10 |
| S6 | **별도 승인** 실제 3레인 소량 Meta+영수증 readback; 확장 시 사용량 측정 | `LIVE RECEIPT VERIFIED` |
| S7 | n8n 작업자와 pinned SHA/schema 합치고 승인 후 자동화·Sheets | 독립 통합 검증 |

**담당자 역할**: P50 구현자는 history·최신 엔진과 로컬 CLI만, n8n Codex는 스케줄/다운스트림 브리지·신버전 pin 및 동시 실행 차단만. 작업자들이 공통 엔진을 별도로 재작성하지 않는다. 두 작업자 모두 본 문서와 [공시 Issue #15](https://github.com/hibi89/ohora-trend-catcher/issues/15) 링크를 `HANDOFF.md`/`STATUS.md` 상단에 동기화하되 각자 작업 브랜치만 수정한다.

## 10. 목표 달성 여부에 따른 판정 (두 축)

- **기능/안전 축**: `DESIGN BLOCKED` (history live 경로 부재/영수증 불일치) / `READY FOR OFFLINE TEST` / `OFFLINE PASS` / `separately authorized LIVE VERIFIED`.
- **운영·용량 축**: `INFEASIBLE UNDER ASSUMPTIONS` (예: 지속 `Q≤L+C+R`) / `CONDITIONALLY FEASIBLE` (안전한 유효 history 용량>0, cursor chain 입증) / `UNKNOWN` (quota, 게시물 수, 가용 시간 미실측).
- **현재 판정**: `DESIGN PUBLISHED, IMPLEMENTATION BLOCKED` + `OPERATING CAPACITY UNKNOWN`. 최신 수집 50계정/과거 365일/실 Meta/Windows 가동의 실제 PASS를 주장할 수 없다.
- 가정 변화 감시: `Q_g, L_t, C_t, B_t, verified_pages/hour, oldest_verified_date, held_groups, unknown_http_count`. 새 실측 데이터에 따라 범위 시나리오를 계속 갱신한다.
- **마감시간을 맞추기 위한 안전 규칙 해제·원본 삭제·커서 조작·동결 설계 우회 불가**. 본 설계의 S0~S5는 작업자 구현 지시이며 운영 승인 아님.

---

**공시 요지:** 커서/RAW 오류를 다 막아도 `Q≤L+C+R`이면 과거 수집은 영원히 진행하지 않는다. 그러므로 새 P50 설계의 완료 조건은 **“손실 없이 안전하게 실행되는 코드 + 실측으로 확인한 양의 history 처리용량 + 모든 대상 계정의 검증된 365일 연속 coverage”** 세 축이다. 한 축이라도 증거가 없으면 완료라고 선언하지 않는다.
