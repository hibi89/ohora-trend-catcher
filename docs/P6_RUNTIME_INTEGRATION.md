> **2026-10-19 확정 수집 정책 (기존 5개/4+1 논의보다 우선):** 공식 294계정, Meta A/B/C 3개 레인, 2라운드, 계정별 최대 3페이지 × 페이지당 4개 = 최신 게시물 최대 12개. 라운드 1: A 1–150/page1, B 151–294/page1, C 1–150/page2. 라운드 2: A 151–294/page2, B 1–150/page3, C 151–294/page3. 반드시 계정별 실제 opaque cursor와 선행 페이지 증거를 사용한다. 150계정/레인은 프로젝트 운영 정책이며 Meta 공식 한도 증명이 아니다. 기존 5개·4+1 비교 문단은 폐기된 의사결정 이력이며 현재 미결 정책이 아니다. 이 변경은 **문서 정정만**으로, 294계정 live runner/실 Meta/운영 DB 검증을 뜻하지 않는다. 사용자 승인 전 실 API·토큰·운영 DB/CSV·main 병합 금지.

# P6 Runtime Integration — 검증 기준 및 운영 전환 차단 (2026-10-19 KST)

> **현재 상태: 단일 요청 오프라인 통합 완료 / 실운영 미승인.** 기준 코드 SHA `40ae73bdb33338fc90d37442f64453945ae7bcd5`, [Draft PR #6](https://github.com/hibi89/ohora-trend-catcher/pull/6). `main` 기준 `bcdb84b640738fdfceaf833dd831a26d401a2427`. 이 문서는 구현 완료와 실운영 준비를 구분한다. PR은 Draft, main 미병합.

## 1. 구현·검증 완료 (해당 코드 SHA에 한함)

- `raw_v2/three_api_runtime_contract.py`: A/B/C v1.0 요청·응답·캡처·RAW 영수증 및 Port 계약, 승인 없는 자동 재시도 차단.
- `raw_v2/three_api_transport.py`: 기존 `GraphHTTPClient` 주입형 단일 HTTP 시도 어댑터. 직접 자격증명 조회·자동 네트워크 활성화 없음. 실 Meta 전송은 **실행하거나 검증하지 않음**.
- `raw_v2/three_api_capture.py`: 사전 request intent 저장, 캡처·실패·불확실한 결과의 격리된 영속 기록과 재조회.
- `raw_v2/three_api_contract_executor.py`: 단일 요청의 intent → 주입 transport 1회 → 정규화/ID·cursor·usage 검사 → durable capture → RAW receipt → acknowledge. 영속 캡처가 남은 경우 HTTP 없이 RAW 회복. 모호한 응답은 수동 조정 전 재전송 금지.
- `raw_v2/three_api_raw_writer.py`: 계약형 `ContractRawWriter` 추가, 기존 dict API 유지, 독립 격리 SQLite에 원자적 RAW 행/영수증 처리. `collection.sqlite` 파일명 접근 차단.
- `raw_v2/three_api_live_readiness.py`: G0–G6 증거 순수 진단. 모든 게이트 참이어도 `runtime_authorized=false` 반환.
- 기존 `raw_v2/three_api_daily_orchestrator.py`의 **294/294 합성** 배치는 별개인 manual step + fixture 파이프라인이다. 새 단일 요청 contract executor와 연결된 **294계정 실운영 실행기**로 오인 금지.

## 2. 확인된 CI 증거

| 검증 | 코드 SHA | 결과 |
|---|---|---|
| [P6 Runtime Integration Offline #39261103712](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39261103712) | `40ae73b` | success, 489/489 테스트 (PR 설명·검증 기록) |
| [Three API offline #39261103742](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39261103742) | `40ae73b` | success |
| [P6 C Contract Offline #39261103691](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39261103691) | `40ae73b` | success, 22개 계약 테스트 (PR 설명·검증 기록) |

GitHub workflow 상태를 직접 재조회했다. 세부 테스트 수는 해당 SHA의 PR/Actions 기록에 의존한다. 문서 후속 커밋의 CI 결과를 이전 SHA 성공과 혼동하지 않는다. 로컬 `git clone`은 실행 환경 DNS 제한으로 실패하여 동일 checkout 재실행은 미검증.

## 3. 5개 최신 게시물 요구 대 4개/page 계약 — 미확정 정책

**원본 요구:** 계정당 최신 게시물 최대 5개. **검증한 제약:** `raw_v2/three_api_request_spec.py`의 `build_request_spec(...limit=4)`만 허용; 다른 limit는 `ValueError`. 따라서 현 단일 요청 경로는 계정당 5개를 만족한다고 주장할 수 없다.

| 후보 | 구현 의미 | 장점 | 비용·위험 | 상태 |
|---|---|---|---|---|
| A. **4+1 두 페이지** | 첫 4개 수집, 검증된 opaque after로 둘째 페이지 조회해 고유 1개 선택. 중복/정렬/삭제/불완전 결과를 추적, 5개 미달은 NULL/누락 상태 | 고객의 최신 5개 목적 유지, 한 페이지 최대 4 제약 존중 | 계정당 최대 2회 논리 요청으로 quota 증가; 실제 Meta pagination과 5번째 콘텐츠 신뢰성 미검증 | **권고 후보, 승인 대기** |
| B. 최대 4개로 요구 변경 | 1계정 1페이지의 4개만 표준으로 수집하고 5개 미충족 명시 | 단순·저호출 | 고객 요구 하향, 적용 전 팀/고객사 합의 필요 | 미승인 |

주의: A는 2번째 페이지 실제 응답·cursor·중복 제거와 원자적 누적·관측시각·인수인계가 충분히 검증되기 전 구현 완료가 아니다. 294계정 기준 최초 논리 요청 294회 vs 2페이지 최대 588회(추가/재시도 제외)라는 **요청 계획량 비교**일 뿐 실제 Meta 호출량·rate 예측은 아니다. 실 A/B/C 독립 한도 확인 전 속도나 완료 시각을 보장하지 않는다. 5개 정책 확정 전에는 `limit=4` 상수를 변경하지 않는다.

## 4. 운영 게이트 — 현재 모두 미검증/차단

| 게이트 | 승인 전 증거 / 완료 조건 | 현재 |
|---|---|---|
| G0 | 원본 DB·CSV·manifest·로그 위치/백업/복구 검증 | 실운영 자료 접근·변경 없음 |
| G1 | 공식 로컬 294계정 목록 digest/매핑/권한 검증 | 실제 원본 현재 상태 미검증 |
| G2 | 별도 승인된 최소 Meta probe에서 권한·nested `children`·페이지 응답·cursor 포맷 확인 | 미실행 |
| G3 | A/B/C 앱·토큰·사용량 헤더별 quota 격리 증명; Meta #4/missing usage 즉시 차단 | 실 독립성 미검증 |
| G4 | **비운영 격리 복제 DB** migration/dry run/충돌·전원중단·복구·멱등 검증 | 단일 요청 격리 writer까지만 검증 |
| G5 | 계정별 raw 게시물/관측/미디어·NULL·누락·첫 페이지 source/receipt 증거 검증 | 합성 증거만 존재 |
| G6 | 별도 승인 후 294계정 runner 및 scheduler의 중복실행 방지·중단·알림·quota 정책 검증 | 미구현·비활성 |

게이트 정의 원본: [THREE_API_LIVE_READINESS_GATE_2026-10-19.md](THREE_API_LIVE_READINESS_GATE_2026-10-19.md). 순수 진단 결과는 실행 승인 토큰이 아니다.

## 5. 다음 안전한 구현·검증 우선순위

1. **정책 결정 전:** 4+1 또는 4개로 요구 변경 여부를 고객/사용자와 명시적으로 합의. 현재 default 4 유지.
2. **승인 없이 가능:** 실 HTTP 없이 정해진 4-page 계약, 합성 page2(cursor)·중복/누락/변조·restart·unknown outcome·RAW receipt 불변성의 추가 오프라인 경계 테스트. 기존 489개를 중복 구현하지 않음.
3. **승인 없이 가능:** 294계정 루프가 아닌 단일 요청 executor와 **기존 합성** 일일 오케스트레이터 간 인터페이스 차이·DB migration 계획 비교/사전 설계. live 활성화 코드/운영 복제본 변경은 금지.
4. **별도 승인 필요:** 실제 Meta 실권한/필드/쿼터 최소 probe → 격리 운영 복제본 검증 → 제한 파일럿 → scheduler/최종 main 병합을 각기 승인.

오프라인 회귀 명령: `python tests/run_offline.py` 및 `python -m unittest discover -s tests -p 'test_three_api_contract_executor.py' -v`. 기록 기준 커밋의 CI 성공과 신규 커밋의 결과는 분리 관리.

## 6. 보안·롤백·인수인계

- **금지:** 실 Meta API, 토큰/환경변수 열람·기록, 운영 `collection.sqlite`/CSV 쓰기, 무인 스케줄러, PR Draft 해제, main 병합. 위 동작은 사용자 개별 승인 후에만.
- PR #6 브랜치에 한정해 문서와 **격리 합성 테스트**만 변경할 수 있다. 배포 대상은 해당 없음(Vercel Production 미적용).
- 기존 코드 되돌리기 없이 이 신규 문서만 삭제하거나 개별 문서 커밋을 revert하여 복구 가능. 기존 원본/운영 DB 변경 없음.
- 최신 상태 진입점: 루트 `HANDOFF.md`, `STATUS.md`, 본 문서, PR #6 HEAD SHA 및 workflow 결과. 문서 커밋은 런타임 코드 변경/실검증이 아니다.

## 7. p6 최종 개발 기반 결정 및 Codex 구현 경계 (2026-10-19)

- **최종 개발 저장소는 p6** `hibi89/ohora-trend-catcher`, 작업 브랜치는 Draft PR #6. p7 `hibi89/ohora-trend-catcher-classic`은 과거 실수집 코드의 비교 기준이며 통째 병합하거나 중복 디렉터리를 복사하지 않는다.
- 확인된 실제 코드: p6 `three_api_daily_orchestrator.py`의 `OfflineDailyOrchestrator`는 수동 합성 fixture `step()` 실행기이며 Meta live scheduler가 아니다. p6 `three_api_contract_executor.py`의 `ThreeApiContractExecutor`는 단일 요청의 intent/transport/capture/receipt 경로이다. p7 `daily_scheduler.py`는 기존 단일 Graph API 운영 수집 진입점이며 `--live` opt-in과 front/catchup/history 정책을 갖는다. p7 `three_api_plan.py`는 294계정 3페이지 2라운드 계획만 제공하며 실 병렬 collector가 아니다.
- **선행 설계 결정:** 단일 요청 executor와 일일 계정/페이지 작업 선택기 사이에 얇은 adapter를 정의한다. 각 `(roster_digest, account_key, page)` 작업의 선행 페이지 RAW receipt 및 실제 opaque cursor를 검증한 후에만 다음 페이지 요청. lane A/B/C 독립 quota/credential/context 및 공통 durable job lease의 소유권을 명확히 한다. 기존 `slots`, `accounts.backfill_cursor`, `collection.sqlite`를 3개 legacy collector가 동시에 공유하게 하지 않는다.
- **실행 순서:** (1) 현재 모듈 입출력·DB schema·lock 경계 표 작성, (2) 합성 격리 DB에서 adapter와 page1→2→3 상태 전이 구현, (3) 중단/재시작·unknown outcome·cursor 변조·중복 receipt·quota 차단·race 회귀 추가, (4) `python tests/run_offline.py` 전체 재실행, (5) HANDOFF/STATUS/PR에 코드 SHA와 실제 CI 결과 기록. 기존 489 테스트를 다시 작성하지 않는다.
- **금지/미검증:** 운영 자격증명/토큰 열람, 실 Meta 요청, 운영 DB·CSV·RAW 변경, 기존 수집 경로 삭제, 3개 collector의 동시 실행, unattended scheduler 활성화, Draft 해제, main 병합. 새 오프라인 adapter가 완성되어도 Meta 권한·quota 독립성·실 운영 성공은 별도 승인/검증 전까지 미확인이다.
- **인수인계 품질 기준:** 코드/문서 수정마다 실제 커밋 SHA·테스트 명령·결과를 구분하고, 실패나 접근 불가 항목을 미검증으로 표시한다. 최종 목표는 p6 하나의 운영 가능한 294계정 수집 시스템이며 p7 별도 제품을 만들지 않는다.
