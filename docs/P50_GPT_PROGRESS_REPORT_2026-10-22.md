# OHORA P50/n8n 진행 보고서 — 2026-10-22

## 현재 인계 요약

- 작업 범위: P50 기존 수집 엔진을 재사용하는 n8n 연동과 오프라인 검증. P6 수집기 내부 및 커서 복구 기능은 다른 작업자의 소유 영역으로 둔다.
- 저장소: `hibi89/ohora-trend-catcher`
- 브랜치: `codex/p6-readiness-hardening-20261019`
- 원격과 로컬의 확인된 기준 HEAD: `4088bad78b384c83cb7085842ae981717a376d4b` (`origin`도 동일; 아직 이번 변경 미포함)
- 작업 트리에 미커밋 변경이 있다. 아래 “현재 미커밋 변경” 참조.
- `main` 병합은 하지 않았다.

## 지금까지 확인·완료된 작업

1. 원격을 fetch해 P50 엔진 최신 후보와 설계 자료를 확인했다. P50 엔진 최신 SHA는 `8548ecb650bb17a7f671829b740609f6a72cac5a`이며, 두 줄로 된 중복 Capture/RAW receipt 식별자 차단만 이전 `990e0cd`에서 추가됐다. 해당 SHA의 P50 GitHub 오프라인/frozen-design 체크 PASS를 확인했다.
2. P50 n8n launcher가 구 엔진 SHA를 고정하던 문제를 찾아 현재 후보 SHA로 로컬 변경했다. 고정 SHA가 테스트에서도 검증되도록 export 테스트 기대값도 갱신했다.
3. 오프라인 테스트 실행기에서 저장소 루트를 `sys.path`에 넣지 않아 `src.collection_schedule` 검색이 실패하는 문제를 수정했다. 이 수정은 `4088bad`에 커밋·push되어 로컬/원격 HEAD 일치를 확인했다.
4. Windows 테스트에서 짧은 경로 별칭과 정규화 경로 문자열을 직접 비교하는 픽스처 문제를 확인했다. 테스트가 `_load_pilot()`의 실제 정규화 동작과 같은 `Path.resolve()` 기준을 사용하도록 로컬 수정했다.
5. 업데이트된 P50 엔진으로 격리 n8n CLI mock 실행을 수행했다. n8n 2.40.7의 임시 프로필에서 실행 레코드가 `success`로 저장됐다. launcher 단독 오프라인 결과는 `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, 시도 0, 페이지 0, `live_meta_called=false`였다. 이는 n8n mock 경로의 오프라인 확인이며 예약 운영 실행이나 Meta API 성공을 뜻하지 않는다.
6. 사용자 제공 50행 로스터의 오프라인 정규화 및 핀된 P50 validator 통과, 기존 한 페이지 P50 live proof는 기존 HANDOFF에 기록돼 있다. 그 proof는 한 번의 승인된 A lane 요청에 한정되며, 예약 실행 검증이 아니다.

## 검증 결과와 현재 CI

- `python -m unittest discover -s tests -p test_collection_schedule.py -v`: 15/15 PASS.
- P50 엔진 `8548ecb` focused suite: 44개 실행, 43 PASS, 1 SKIP.
- P50 n8n adapter/export focused suite: 13/13 PASS. 경로 별칭 픽스처 수정 반영 후 재실행한 결과다.
- `python tests/run_offline.py --list-groups`: 1,015 distinct tests 발견.
- `git diff --check`: PASS (줄바꿈 변환 경고만 있음).
- 전체 오프라인 테스트의 로컬 실행은 10분 넘게 완료되지 않아 중단했다. **CANCELLED이며 전체 로컬 suite PASS로 집계하지 않는다.**
- GitHub run `39725883303` (`4088bad`): Linux runtime PASS. Windows `remaining`에서 경로 별칭 fixture 1건이 FAIL했고 나머지 shards PASS. Fixture assertion has been corrected locally; new n8n-branch SHA CI has not run yet.
- P50 latest SHA `8548ecb` has its engine offline/frozen checks PASS. The P50-branch broader PR checks included Windows shards still in progress at the latest check-runs query.
- 과거 CI `39723488710`에서 확인한 import root 문제는 `4088bad`로 수정했다.

## 현재 미커밋 변경

- `raw_v2/p50_n8n_tick.ps1`: P50 engine expected SHA를 `8548ecb650bb17a7f671829b740609f6a72cac5a`로 갱신.
- `tests/test_n8n_p50_export.py`: 같은 pin을 검증.
- `tests/test_three_api_p50_n8n_tick.py`: Windows 경로 별칭 픽스처를 `str(raw_v2.resolve())` 기준으로 수정.
- `docs/P50_N8N_COMPLETION_TODO.md`: 격리 E2E 및 pin 검증 상태를 기록.

이 4개 변경과 이 보고서는 현재 로컬에만 있고 커밋·push되지 않았다. GitHub CI는 이 변경들을 아직 검증하지 않았다.

## 운영 상태 / 금지 사항

- P50 n8n workflow는 비활성 상태로 유지한다. 기존 workflow의 5분 schedule은 승인된 실행 예산으로 검증되지 않았으므로 활성화하지 않는다.
- 운영 n8n 호스트/프로필은 확정되지 않았다. 로컬에서 발견한 n8n CLI 2.40.7이 운영 대상이라고 단정할 수 없다.
- 경고 채널은 사용자가 자격증명 기반 방식을 선택했지만, 구체적인 채널은 “나중에”로 보류됐다.
- 다른 엔진 소유자의 완료 및 quota group 간 사용량 독립성은 확정 전이다.
- P50의 한 페이지 live proof 이후 추가 Meta 요청은 하지 않았다. 새 Meta 실호출, 운영 DB 변경, 예약 활성화에는 별도 승인이 필요하다.
- P294 활성화는 보류하고, P50/P294 저장소·커서·수집 이력은 분리한다. Google Sheets는 n8n 핵심 및 P50 검증 뒤 단계다.
- 토큰·계정 ID·비공개 RAW 데이터는 보고서에 포함하지 않았다.

## 다음 작업

1. 로컬 4개 변경을 재검토하고 새 커밋을 만든다.
2. 기존 브랜치에 push하고 동일 SHA 기준 GitHub CI 전체 결과를 기다려 확인한다. 실패하면 테스트/제품 문제를 구분해 수정하고 ledger를 갱신한다.
3. `P6_TROUBLESHOOTING_LEDGER.md`, `HANDOFF.md`, `STATUS.md`에 이 세션의 CI fixture 회귀 및 해결 경과를 기록·동기화하고, Project Book 색인도 갱신한다.
4. P50 production n8n 프로필, schedule/page budget, credential-backed alert channel, engine handoff/quota ownership을 확정한다.
5. 별도 승인된 P50 실수집과 n8n 검증 후 P294 확장, 그 다음 Google Sheets 연동을 진행한다.

## GPT 다음 담당자 안내

시작 시 `AGENTS.md` → `DESIGN_FREEZE_NOTICE.md` → frozen P6 design → 최신 `HANDOFF.md` / `STATUS.md` → `docs/P6_TROUBLESHOOTING_LEDGER.md` 순서로 읽는다. 현재 미커밋 파일을 보존하고, P6 내부·운영 DB·schedule·Meta 호출은 건드리지 않는다. 우선 로컬 diff와 run `39725883303` 결과를 확인하고, 새 SHA의 CI를 검증한 뒤 인수인계 문서를 동기화한다.
