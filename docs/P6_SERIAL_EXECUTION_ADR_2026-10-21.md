# ADR P6-SERIAL-01 — 3개 Meta API를 직렬 실행하는 구조로 전환

작성일: 2026-10-21 KST · **사용자 승인 결정 기록** · 상태: `오프라인 부분 구현 / 실제 Meta 연동·운영 승인 대기`

> 이 ADR은 [기존 동결 설계](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md)를 덮어쓰지 않는다. 사용자가 동시 실행에서 순차 실행으로의 전환을 명시적으로 지시한 근거와 구현 범위를 별도로 보존한다. 저장된 동결 설계의 다른 불변 조건은 그대로다.

## 1. 변경 이유

- 사용자 실험 결과 배치 조회 자체의 속도는 충분했다. 병목은 수집 요청 CPU/네트워크 병렬성이 아니라 **Meta API별 시간 한도 소진 후 대기 구간**이었다.
- 앞으로 호출 주체 API가 늘어날 가능성을 염두에 두면 동시 스케줄 관리·다중 쓰기 조정보다 모듈별 순차 호출과 단일 RAW writer를 우선하는 편이 작업·복구·트러블슈팅에 유리하다.
- **시간 한도는 직렬화해도 증가하지 않는다.** 독립적인 쿼터 여부를 검증하지 않은 상태에서 A 차단 뒤 B 사용이 항상 안전하다고 단정할 수 없다.

## 2. 유지할 불변 조건

| 영역 | 결정 |
|---|---|
| 대상 | 공식 294개 계정 식별자/순서 불변 |
| 호출 주체 | A/B/C **3개 API 레인** 유지, 실제 자격증명·쿼터 범위는 분리 검증 |
| 요청 수 | `limit=4` 고정 |
| 최신/과거 | KST 최신 우선 2라운드, 계정당 최대 3페이지 후 필수 Catch-up 및 영속 365일 History |
| 선행 조건 | 커서·페이지 선행 증거 없는 page2/3·History 전진 금지 |
| RAW | 안전한 응답 증거 먼저 보존, 모든 관측 기록, 불변 원본, 장애 후 API 재호출 없는 재처리 |
| 사용량 | 85% 중단·최소 65분 제약, 쿨다운 해제 자동 가정 금지 |
| 분석 | 대시보드 F5로 재수집·AI 재분석 금지 |

**직렬 실행은 속도 제어 정책이며 데이터를 재배정하거나 기존 A/B/C 작업 할당을 무시하는 허가가 아니다.**

## 3. 오프라인 구현된 경로

1. `raw_v2/three_api_daily_orchestrator.py`의 `step_serial(now=...)`: **호출당 최대 1개 synthetic 페이지** 실행. 성공한 레인 뒤에서 다음 우선순위를 이어가며 SQLite `offline_serial_turn`에 마지막 성공 레인을 보존한다. 재시작 후에도 순서를 복구한다.
2. `front_lane_unavailable` 및 Catch-up `lane_blocked` 상태에서 **이미 가용으로 인정된 다른 synthetic 레인**을 순서대로 확인. RAW 실패·fixture 누락·복구 무결성 실패는 다음 레인으로 무작정 전환하지 않는다.
3. `_step_with_phases`: 각 `step_serial` 호출은 전체 원본·ledger·RAW reconcile을 **1회만** 수행. 별도 수동 `step(lane)`은 기존처럼 스스로 reconcile한다.
4. `raw_v2/three_api_serial_lock.py`: 동일한 **격리 오프라인 control SQLite 경로**를 사용하는 모든 `step`/`step_serial`에 파일 기반 OS 잠금을 적용한다. 잠금 불가 시 `serial_busy`. POSIX `flock` 및 Windows `msvcrt.locking`을 사용하고 프로세스가 죽어도 lock 소유권은 OS가 해제한다.
5. `tests/test_three_api_daily_orchestrator.py`, `tests/test_three_api_serial_catchup.py`, `tests/test_three_api_serial_lock.py`에 가짜 데이터·재시작·중복·B/C fallback·사용량 차단·별도 프로세스 lock 회귀. 네트워크 차단 오프라인 CI로만 실행.

관련 결정/장애: [P6-012, P6-016, P6-018, P6-019 사건장부](P6_TROUBLESHOOTING_LEDGER.md).

## 4. 아직 연결되지 않은 경로 — 절대 운영 완료로 주장하지 말 것

- 기존 `raw_v2/collector.py` / `daily_scheduler.py`는 **단일 호출 주체의 과거 RAW v2.1 수집기**이며 이 ADR의 3 API serial gate에 연결되지 않았다. 그 코드를 호출해 새 A/B/C 직렬 정책이 적용된다고 안내하지 않는다.
- `ThreeApiContractExecutor`, 실제 `MetaTransport`, `JobRequestBindingStore`, capture/RAW writer, epoch/history 상태 저장소를 사용하는 **새 live 수집 runner는 미구현/미검증**. 현재 `step_serial`은 API 호출 자체를 못하는 합성 전용 함수다.
- 운영 credentials, Meta 실제 quota group 독립성, 294개 전수 성공률·쿨타임 회복, Windows 실머신 DB locking, 크론/무인 운영, main 병합 모두 완료가 아님.
- 이 문서·테스트를 근거로 Meta API 실호출, 프로덕션 DB 변경, 자동 배포/스케줄러, main 병합을 실행하지 않는다. 각각 별도 명시 승인 필요.

## 5. 실제 연결 시 사전 합격 조건

1. **단 하나의 reviewed 실행 진입점**이 A/B/C 인증 주체 선택→요청 바인딩→HTTP 1회→안전한 응답 영속화→RAW receipt 검증→cursor 진행을 순차 제어해야 한다.
2. 동일한 control DB와 writer를 공유하는 다른 프로세스·기존 runner가 동시에 송신하지 못하도록 OS lock 경계까지 포함해야 한다. 지금 synthetic lock만으로는 live 경계가 보장되지 않는다.
3. 일일 294×3 페이지와 KST rollover, Catch-up/History 전환, 85%/65분 중단·재개, 빈/NULL/부분 캐러셀/영상·릴스 조회수와 URL, 예외 계정 명시적 제외가 각각 합성 근거로 검증돼야 한다.
4. 송신 후 crash·timeout·결과 불확실 시 **자동 HTTP 재발송 금지**. 이미 받은 안전한 응답은 HTTP 없이 replay. 사용량 헤더를 잃어버렸더라도 수신 증거는 보존하고 전송만 중단.
5. FakeTransport로 **동시에 2개 send가 진행되지 않음**을 Linux/Windows/별도 프로세스 테스트에서 확인하고, 마지막 유효 commit 전체 CI 성공 SHA를 확보한다.
6. 사용자 승인 뒤에만 최소 계정/요청 수로 Meta 실호출·사용량·커서 검증을 실시하고, 운영 원본 백업 및 사후 read-only audit을 확인한다.

## 6. 실패 시 원복

- feature 코드/ADR/테스트를 git revert로 되돌리고 동결된 원본 설계 SHA가 보존되는지 확인.
- 사용자 운영 `collection.sqlite`, `runs/raw`, `account_list.csv`, 기존 커서·모든 관측 원본 **삭제/초기화/덮어쓰기 금지**.
- 롤백 이유/영향/SHA는 [장부](P6_TROUBLESHOOTING_LEDGER.md)에 남긴다.
