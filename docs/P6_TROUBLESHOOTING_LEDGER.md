# P6 트러블슈팅·기술부채·설계 판단 기록 (Living Incident Ledger)

> 최초 작성: 2026-10-21 · 범위: OHORA Trend Catcher p6의 확인 가능한 Git 이력, `STATUS.md`/`HANDOFF.md` 기록, 해당 대화에서 확인된 CI 로그 및 사용자 보고.
>
> **목적:** 지금 성공/실패한 실험 모두를 미래의 레거시 분석, 재설계, API 증설 및 인수인계에 재사용한다. **완전한 전수 감사 기록이 아니다.** 문서화되지 않은 과거 작업은 Git/Actions/기존 인수인계 자료를 대조하여 계속 보강한다.
>
> **위계:** [동결 공지](../DESIGN_FREEZE_NOTICE.md) 및 [최종 수집 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)가 승인된 정책의 기준이다. 이 문서는 정책·구현 완료·운영 승인을 대체하지 않는다. 설계 원문은 수정하지 않는다.
>
> **기본 구분:** 사용자 보고 / 코드로 확인 / CI로 검증 / 미검증 가설 / 후속 작업. 서로 혼동하지 말 것. CI 성공은 기록된 **특정 commit SHA**에만 유효하다.

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

## 사건별 상세

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
