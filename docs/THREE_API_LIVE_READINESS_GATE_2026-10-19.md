# OHORA 3-API 실운영 전환 게이트 — 설계 전용

> 상태: **승인 전 설계 + 오프라인 증거 검증 순수 함수 구현**. `raw_v2/three_api_live_readiness.py`의 `evaluate_live_readiness(evidence)`는 G0~G6를 모두 엄격한 `True`로 제출하더라도 `runtime_authorized=False`를 반환한다. 입력 누락·추가 키·문자열/숫자 truthy 값은 전부 차단한다. **실제 수집 실행 경로에 연결된 승인 게이트가 아니며, 실 Meta 호출·권한 변경·토큰 사용·운영 RAW DB 연결·무인 실행은 수행하지 않음.** 이 문서는 실증 완료 증거가 아니다.

## 현재 확인된 범위

- `b6cc27b` 기준 합성 3-API CI **326/326 PASS** ([run 39244069568](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39244069568)), RAW v2 **424/424 PASS** ([run 39244069487](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39244069487)).
- `OfflineDailyOrchestrator.operator_report()`는 합성 SQLite 세 개의 읽기 전용 진단이다. `front_window_closed`는 수집 성공 증거가 아니다.
- **미검증**: 실 Meta children/캐러셀 문법과 권한, 앱·토큰·계정별 실제 사용량 헤더 및 A/B/C 독립 quota, page4+ HTTP, 운영 RAW migration, 무인 스케줄러, 실제 294계정 완주.

## 승인 전 차단 조건

| 게이트 | 사전 확인 항목 | 미충족 시 동작 |
|---|---|---|
| G0 데이터 보존 | 운영 `collection.sqlite` 및 기존 CSV·manifest·logs의 백업과 복구 방법을 사용자와 확인 | 운영 파일 쓰기 금지 |
| G1 계정 정합성 | 공식 **로컬** `account_list.csv` 294개(KR100/US101/JP93)와 실제 승인된 대상 매핑을 비교; GitHub에 토큰/개인 데이터 업로드 금지 | 실 호출 중지 |
| G2 Meta 권한·필드 | 최소 범위 단일 계정·단일 요청의 승인 후 수동 검증, nested `children`/미디어·페이지네이션 응답 확인 | children 또는 page4+ 구현 검증 주장 금지 |
| G3 Rate 정책 | A/B/C가 실제 독립 한도인지 헤더·앱 ID·토큰 정책 확인. #4·사용량 미확인 시 해당 lane 즉시 중단; 65분은 **합성 정책**일 뿐 Meta 보장 아님 | 재시도 루프·무인 재개 금지 |
| G4 쓰기·복구 | 격리된 복제본에서 source→ledger→RAW 적재와 충돌·중단·재시작·idempotency·rollback 검증 | 운영 RAW 쓰기 금지 |
| G5 검증 증거 | 게시물/관측/미디어 수, NULL 처리, 시간별 관측 시각, 계정별 누락 및 source/page1 증거를 분리 보고 | `294/294 실수집 성공` 주장 금지 |
| G6 스케줄러 | G0~G5 충족 후 별도 사용자 승인; 중복 실행 방지, 수동 중단, quota 회복, 감사로그·알림 점검 | 무인 실행 비활성 유지 |

## 단계적 실행 순서 (향후 승인 필요)

1. **읽기 전용**: 권한·스코프·필드·레이트·DB 스키마 계약을 문서로 점검한다. 운영 자격증명 값은 문서·로그에 쓰지 않는다.
2. **승인된 소규모 live probe**: 사전에 합의한 계정 수·호출 수·시간창으로만 테스트한다. 응답 원문과 토큰을 GitHub에 저장하지 않는다.
3. **격리된 RAW 복제본**: page1→page4+ 및 children 수집을 재생·중복·중단 시나리오와 비교한다. 실 운영 DB를 테스트 대상으로 사용하지 않는다.
4. **제한된 운영 파일럿**: 사용자 별도 승인 이후 quota·증거 누락·비용·실패율을 기록하고 즉시 중단 가능한 상태에서 진행한다.
5. **무인화**: 294계정 전체와 무인 스케줄러는 별도 검토·승인 후에만 활성화한다.

## 운영자 보고 판정 규칙

- `front_window_closed=294` 단독으로는 성공 아님. `first_page_proof_gap_in_closed_windows>0`이면 즉시 미완료로 표기.
- `first_page_committed=294`라도 RAW receipt/관측·미디어·원본 digest 전체 검증 없이 실수집 완결 주장 금지.
- `integrity=not_fully_revalidated`, `live_meta_verified=false`는 운영 완료 증거가 아님.
- `recovery_attention=true` 또는 staged/충돌/누락 receipt가 있으면 자동 수집 재개 금지, 원본 증거 확인 우선.
- `reset_time_reached=true`는 수동 재검토 가능 시점일 뿐 자동 reset 승인 아님.

## 다음 구현 단위 후보

- 승인 전: 실제 HTTP/Meta transport 없이 **합성용 live-readiness validator**를 별도 pure function으로 구현하고 G0~G6 승인값이 없으면 항상 차단하는 테스트를 작성한다.
- 승인 후: G2의 최소 호출 수와 대상 계정·시간창을 명시해 권한 및 필드만 검증한다. 다른 게이트를 통과한 것으로 해석하지 않는다.

**롤백:** 이 문서만 삭제하면 된다. 실제 자격증명·운영 데이터·스케줄러 변경은 없음.

## 오프라인 증거 검증 API

`evaluate_live_readiness({"G0": False, "G1": False, "G2": False, "G3": False, "G4": False, "G5": False, "G6": False})`는 승인 여부와 무관하게 실행 권한을 부여하지 않는 **진단 함수**다. 모든 게이트가 `True`여도 별도의 사용자 승인과 실제 API 권한 검증이 필요하다. `tests/test_three_api_live_readiness.py` 및 Three API CI가 누락·변조·truthy 문자열·임의 추가 필드·민감 입력 비반향을 검사한다. 기존 collector/transport에 자동 연결하지 않는다.


### 2026-10-19 fail-closed mapping 보강

The evaluator snapshots a Mapping once. If enumeration or value retrieval raises, the input is treated as invalid; every gate remains blocked, and input values are not echoed. Offline regression coverage now includes a stateful Mapping and a Mapping that raises during iteration/access. This does not grant runtime authorization or connect the evaluator to any Meta/RAW execution path. GitHub Actions #39256533261 passed 30 suites / 332 tests, including Python 3.11 compilation, for code commit 26410e665d7a73607abd427772a1cb13d1cd706f.