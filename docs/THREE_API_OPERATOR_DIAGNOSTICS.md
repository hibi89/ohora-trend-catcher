# 3-API 수집 운영 진단 — 오프라인 합성 환경 전용

상태: **구현 · 합성 오프라인 테스트 대상으로 추가**. 실 Meta API와 운영 DB 연동은 없음.

## 목적

`OfflineDailyOrchestrator.operator_report(now=timezone_aware_datetime)`는 이미 열린 격리된 source/gap/RAW mirror SQLite 연결의 상태만 **읽기**한다. 새 수집 요청·자동 재시도·수집 시각 조작·RAW 복구·lane reset을 수행하지 않는다. `step()`·`reconcile()`과 명확히 분리한다.

실행 예:

```python
from datetime import datetime, timezone
report = orchestrator.operator_report(now=datetime.now(timezone.utc))
print(report["daily_front"])
print(report["lanes"])
print(report["storage"])
print(report["policy"])
```

위 예의 `orchestrator`는 294개의 **합성 테스트 계정**과 세 개의 분리된 합성 DB를 전달해 생성된 객체다. 실제 인증정보, .env 파일, 공식 CSV, `collection.sqlite`를 로드하거나 조작하는 CLI가 아니다.

## 필드 의미

| 필드 | 의미·주의점 |
|---|---|
| `mode`, `read_only`, `live_meta_verified` | 항상 합성·읽기전용·실운영 미검증으로 표시 |
| `evaluated_at_utc` | 호출자가 입력한 UTC 기준 관찰 시각(실제 API 관측 시각 아님) |
| `daily_front.roster_accounts` | 고정 294개 합성 스냅샷 |
| `first_page_committed` / `first_page_missing` | coordinator의 page1 확정 증거 존재 여부. 294개 실제 계정을 조회했다는 의미가 아님 |
| `first_page_missing_sample` | 누락 계정의 익명/표준 account_key **최대 20개**; 잘렸다면 `first_page_missing_sample_truncated=True` |
| `front_window_open` / `front_window_closed` | KST ledger 윈도우 단계. 열린 윈도우는 첫 페이지 수집 후에도 미완료일 수 있음 |
| `gaps.catchup_pending_windows` | 오늘까지 확인 가능한 미완료 gap 윈도우 개수 |
| `gaps.manual_review_windows` | `needs_review` 상태, 자동 복구 금지 |
| `lanes[].halted` / `reason` | A/B/C 각 lane의 영속 중단 상태. 사유는 허용된 `usage_limit`, `missing_usage` 또는 `unknown`만 반환 |
| `lanes[].ready_at_utc` / `reset_time_reached` | 중단 이후 최소 대기시간 도달 여부. **자동 재개 허가가 아님** |
| `lanes[].requires_manual_reset` | 중단 상태면 항상 true. 운영자 확인 뒤 별도 수동 reset 필요 |
| `lanes[].inflight_leases` / `expired_leases` | 실행 예약 상태 및 임대 시각 만료된 항목의 조회 전용 숫자 |
| `storage.source_pages_without_capture_clock` | source 증거는 있으나 최초 관측 시각 근거가 없는 페이지 |
| `storage.source_pages_without_raw_receipt` | source 페이지는 확정됐으나 대응되는 RAW receipt 자체가 없는 건 |
| `storage.source_pages_with_staged_raw_receipt` | source 페이지에 해당하는 RAW receipt가 staged인 건 |
| `storage.raw_staged_receipts` | front와 catch-up을 통틀어 승격 대기인 receipt 건 |
| `storage.recovery_attention` / `policy.must_reconcile_before_dispatch` | 미러 미투영·시각 증거 누락·pending 영수증 존재 → 후속 작업 전에 증거 검토 필요 |
| `policy.next_phase_if_storage_verified` | front / catchup / manual_review / history_held / none. 진단 결과만으로 수집 실행 허가하지 않음 |
| `integrity` | `not_fully_revalidated`. 전체 원본 SHA·cross-DB 정합성은 이 함수에서 재검증하지 않음 |

## 안전 원칙

1. `operator_report`는 `reconcile`, `step`, Meta transport, token reader를 호출하지 않는다. 같은 호출 전·후 SQLite dump가 완전히 같은지 회귀 테스트한다.
2. 수집이 종료되지 않은 계정과 **page1이 아직 확정되지 않은 계정**을 분리 보고한다. `first_page_missing=0`이라도 `front_window_open>0`이면 완료가 아니다.
3. 타인 계정 게시물 ID 충돌처럼 RAW 미러가 복구를 거부하면 `staged`/미승격 원본은 대기 중으로 표기한다. 정상 복구라고 주장하거나 다른 작업을 자동 실행하지 않는다.
4. Meta 권한·레이트리밋 독립성·실 page4+ HTTP·캐러셀 field 문법·사용자 단말의 운영 RAW 통합은 모두 **실증되지 않음**.
5. `recovery_attention=False`는 완전 무결성 보증이 아니다. 원본 SHA, 연결된 증거·권한 등은 별도 검증과 승인 과정을 거친다.

테스트 명령:
```bash
python -m unittest discover -s tests -p 'test_three_api_operator_report.py' -v
python tests/run_offline.py
```

CI: `.github/workflows/three-api-coordinator.yml`, `.github/workflows/raw-v2-tests.yml`.
