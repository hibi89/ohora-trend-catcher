# OHORA P6 Engine → n8n 결과 계약 및 소유권 협의서 (2026-10-22)

> **문서 상태: PROPOSED / 현재 코드와의 차이 검토 필수.** n8n 팀 Codex가 이 문서대로 이미 구현했다고 가정하지 말 것. GPT-6는 엔진 DTO/검증만 담당하고 Codex가 n8n 호출·표시·스케줄을 책임진다. 이 문서 자체는 운영 실행 승인 아님.
>
> 공식 브랜치 기준 SHA(문서 작성 당시): `080f66a8a2d947462fed27ad77833a44cfb3c965`. 새 작업 전 ref 재조회.
> 상세 59단위 작업: [P6_ENGINE_WORKBREAKDOWN_GPT6_INSTANT_20261022.md](P6_ENGINE_WORKBREAKDOWN_GPT6_INSTANT_20261022.md)

## 1. 현재 구현의 실제 사실 — 이름 혼동 금지

| 레이어 | 현행 실제 이름/상태 | 한계 |
|---|---|---|
| `LiveSerialStep.step` | `page_committed`, `recovered`, `account_failed`, `no_ready_work`, `serial_busy` | **이 5개만** 실제 step 반환 status |
| `run_bounded_serial_steps` | `last_status`, `attempted_steps`, `committed_pages`, `recovered_pages`, `account_failures`, `results` | 복구 이벤트 전체를 n8n 상태로 평탄화하지 않음 |
| `run_manual_pilot` | `mode=EXPLICIT_BOUNDED_SERIAL_ATTEMPT`, `status=last_status`, page counts | `--live` + 명시 승인 플래그 없으면 Meta 접근 불가 |
| `three_api_n8n_tick.tick` | `action=LIVE_TICK_COMPLETE`(+`status`), `WAIT_EXPLICIT_LIVE_APPROVAL`, 각종 스케줄 `action` | n8n action과 engine status를 혼동 금지; `LIVE_TICK_COMPLETE`는 페이지 저장의 보장이 아님 |
| `three_api_n8n_tick.main` 예외 | `action=MANUAL_REVIEW`, `live_meta_call_status=unknown_after_live_error` 가능, exit 2 | 명시 live 시작 뒤 예외면 Meta 호출 여부 **unknown**. 0회라고 보고 금지 |
| `Coordinator.lanes` | `reason=usage_limit` 또는 `live_pending_usage` 등, `halted`, `ready_at` | **사용량 보류 이유이지 step status가 아니다** |
| `read_pilot_failures` | `recovery_status=resume_required` 또는 `operator_review`, `failed_no_retry` 보존 | 별도 읽기 전용 진단 결과. 원요청 재전송 경로가 아님 |
| `plan_cursor_recovery` | `status=resume_ready` / `coverage_gap` / `terminal_verified` / `page_limit_reached` | **오프라인 planner의 결과**. 실제 승인형 recovery runner 미연결 |

## 2. 제안 통합 결과 계약 (DTO v1) — 아직 미구현 가능

기존 n8n JSON 출력의 기존 키를 바꾸지 말고, P6 엔진이 **옵션의 부가 DTO**를 순수 함수로 제공하는 방식을 우선한다. Codex가 adapter에서 읽고 표현할 책임을 가진다. 제안 필드:
- `schema_version`: 신규 제안 `"p6-engine-event-v1"`. 실제 적용 전 Codex와 합의.
- `action`: 스케줄러가 판단한 현재의 기존 action을 그대로 유지; 엔진이 재명명하지 않음.
- `status`: 이벤트 상태. 실제 출처 `source` 필드로 구분: `step`/`coordinator_gate`/`capture_failure`/`recovery_planner`/`operator_review`.
- `reason`: 제한된 enum 문자열만. 민감한 free text 미출력.
- `day_kst`, `lane`(A/B/C, 또는 null), `committed_pages`, `recovered_pages`, `account_failures`, `approval_required`, `live_meta_called`(true/false/null), `data_writes` 또는 `writes_status`(verified / none / unknown), `terminal`(필요시).
- `cursor_present` 같은 boolean은 가능하지만 **cursor 원문, paging.next URL, token, 대상 사용자명, request_id**를 n8n 로그로 출력하지 말 것.
- 범용 `error_details` 임의 JSON, Meta 응답 본문, 실패 안전 메시지 원문 출력 금지.

### 상태 매핑 (Codex와 합의 전에는 현재 출력에 임의 대입 금지)

| 요청한 표시 | 진짜 출처 / 생성조건 | n8n 권장 행동 |
|---|---|---|
| `page_committed` | verified step `page_committed` / RAW receipt 검증 | 성공 페이지 건수만 카운트 |
| `no_ready_work` | step `no_ready_work` | 다음 예약까지 종료; 결과 0페이지가 오류는 아님 |
| `usage_limit` | coordinator `halted=1` + `reason=usage_limit` | 쿨다운·새 안전 증거 전 HTTP 금지 |
| `live_pending_usage` | coordinator `halted=1` + `reason=live_pending_usage` | 수동 검증 전 레인 보류 |
| `resume_required` | cursor invalid + durable `failed_no_retry` | 기존 요청 재발송 금지, 승인형 recovery job 필요 |
| `resume_ready` | `plan_cursor_recovery`가 실제 중복 ID 기준으로 안전 후보를 산출 | 승인 게이트 검토 후 별도 이어받기 |
| `coverage_gap` | 최대 3페이지 overlap 실패 등 | 임의 '완료' 보고 금지; 수동 검토 |
| `manual_review` | unknown/blocked/incomplete/ambiguous 등 fail-closed | 사용자 검수. 실제 CLI action은 대문자 `MANUAL_REVIEW` |
| `recovered` | 기존 durable Capture로 RAW/커서 commit 복구(무HTTP) | 처리 성공 기록; 신규 Meta 요청 아님 |
| `account_failed` | 계정 단위 종결 실패, 기존 binding 보존 | 계정 예외로 표기, 자동 재전송 금지 |
| `serial_busy` | 동시 실행 잠금 | 중복 tick 방지, 안전 종료 |
| `terminal_verified`/`page_limit_reached` | planner 내부 상태 | DTO에서 유지할지 결정, 다른 성공 상태로 왜곡 금지 |

### 예시(JSON은 목표형 샘플, 현행 실제 출력 아님)

```json
{
  "schema_version": "p6-engine-event-v1",
  "action": "LIVE_TICK_COMPLETE",
  "status": "page_committed",
  "source": "step",
  "day_kst": "2026-10-22",
  "lane": "A",
  "committed_pages": 1,
  "recovered_pages": 0,
  "account_failures": 0,
  "approval_required": false,
  "live_meta_called": true
}
```

```json
{
  "schema_version": "p6-engine-event-v1",
  "action": "MANUAL_REVIEW",
  "status": "resume_required",
  "source": "capture_failure",
  "lane": null,
  "committed_pages": 0,
  "approval_required": true,
  "live_meta_called": null,
  "reason": "cursor_invalid"
}
```

```json
{
  "schema_version": "p6-engine-event-v1",
  "action": "MANUAL_REVIEW",
  "status": "coverage_gap",
  "source": "recovery_planner",
  "committed_pages": 0,
  "approval_required": true,
  "live_meta_called": false,
  "reason": "overlap_not_found"
}
```

주의: `live_meta_called=true`는 실제 호출 증거가 있을 때만. 명시적 live 동작 중 예외는 `null`/`unknown`. `action=LIVE_TICK_COMPLETE`와 `status=no_ready_work`가 함께 나타날 수 있다.

## 3. 기존 CLI: 사용자에게 안전하게 안내할 수 있는 명령

다음 둘은 **기존 Windows P6 루트에서의 읽기 전용 조회**이고, 인수인계 당시 코드 기준임. 실행자가 현재 `--help`, 경로, 런타임을 먼저 확인해야 한다.

```powershell
# P6 실제 HTTP 0, 운영 DB 수정 0, 토큰 불필요
$py = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py raw_v2/three_api_manual_pilot.py --workspace (Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT') --status --day (Get-Date -Format yyyy-MM-dd)
& $py raw_v2/three_api_manual_pilot.py --workspace (Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT') --failures
```

오프라인 테스트 명령:

```powershell
python -m unittest tests.test_three_api_paging tests.test_three_api_meta_normalizer tests.test_three_api_paging_recovery -v
```

현재 n8n CLI 진입점 **정보 제공만**: `python -m raw_v2.three_api_n8n_tick`, 인수된 `raw_v2/p6_n8n_tick.ps1`. 이들은 live 플래그가 있으면 실제 Meta 호출 가능하므로 **사용자 승인 없이 실행 금지**. `--confirm-owner-approval` 문자열이 파일에 존재한다고 현재 채팅 사용자가 새 운영 실호출을 승인한 것은 아니다. 위 파워셸 예약 스크립트는 Codex 소유.

## 4. 별도 복구 라운드의 승인 조건

`cursor_invalid` → durable failure 저장 → 기존 원요청 `failed_no_retry` → 신규 recovery 작업 명세 생성 → 사용자 승인(토큰/운영 스키마/쿼터/실호출) → 최신 페이지부터 **최대 3페이지**, `limit=4`, overlap 확인 → 새 실제 커서로 이어받기 **후보** 또는 `coverage_gap`.

승인형 runner가 작성돼도 **운영 연결·API 호출·기존 DB 변경은 별도 승인 전까지 비활성**. n8n은 복구 세부 단계를 몰라도 된다. n8n에는 **상태 및 사람이 승인해야 하는지 여부만** 제공.

## 5. 버전 변경/호환성 검증 절차

1. GPT-6가 P6 내부의 반환 모델과 안전 enum을 테스트하고 자신의 **독립 브랜치**에 커밋.
2. Codex는 `three_api_n8n_tick.py`/PS1/workflow JSON의 변환·전달을 자기 브랜치에서 구현하고 오프라인 테스트. 두 작업자 모두 동시 같은 파일 수정 금지.
3. 두 담당자가 필드/상태 매핑·exit code·동일 JSON 계약을 **합성 입력만으로** 교차 검수.
4. 통합자는 diff·진짜 SHA·CI 통과·secret 스캔·라이브 미검증을 확인한 뒤 PR 병합 검토. `main`은 소유자 명시 승인 없이 병합 불가.
5. 실제 실수집과 n8n 스케줄러 재시작/장기 구동·quota reset은 사용자의 **별도 승인**.

## 6. Codex 전달문(요약)

> P6 내부 정상/복구 로직은 GPT-6가 맡는다. Codex는 기존 n8n 실행 진입점·스케줄·JSON 변환만 관리한다. 현행 engine step 반환은 `page_committed`/`recovered`/`account_failed`/`no_ready_work`/`serial_busy`이며, `usage_limit`/`live_pending_usage`는 coordinator gate, `resume_ready`/`coverage_gap`는 오프라인 planner, `resume_required`는 durable failure 진단이다. 전부 top-level tick 상태로 구현됐다고 가정하지 말고, 본 계약 문서의 `source`와 `status`를 구분해 작업하라. P6 코드 owner와 병합·운영 DB·live API 승인 경계를 유지하라.
