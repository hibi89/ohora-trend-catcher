# OHORA P50/n8n 진행 보고서 — 2026-10-22

## 현재 인계 요약

- 작업 범위: P50 기존 수집 엔진을 재사용하는 n8n 연동과 오프라인 검증. P6 수집기 내부 및 커서 복구 기능은 다른 작업자의 소유 영역으로 둔다.
- 저장소: `hibi89/ohora-trend-catcher`
- 브랜치: `codex/p6-readiness-hardening-20261019`
- 보고서의 현재 기준: 기존 브랜치에 n8n 수정 커밋 `6d096d845e8b05fef220eb95eff502f03a6aedbd`, `dea9ecc31fa677273839074e0dc363ca15ef0da0`를 push했고 각 직후 fetch 결과 로컬/원격 SHA 일치.
- 인수인계 문서·ledger 변경은 지금 별도 docs commit 준비 중이며, CI는 그 전 코드 SHA `dea9ecc`에서 진행 중.
- `main` 병합은 하지 않았다.

## 지금까지 확인·완료된 작업

1. n8n이 사용할 정확한 엔진 SHA를 고정했다: 마지막으로 검토·테스트한 P50 SHA `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`. 해당 엔진은 history receipt 중복 차단 및 Capture/RAW 영수증을 요구하는 latest-tail anchor를 포함하고 focused suite 48개 중 47 PASS, 1 SKIP. P50 엔진 브랜치는 이후 `5090ef0`까지 추가 history request/commit/evidence 코드를 올렸지만 n8n과의 계약 검토/테스트가 안 되어 pin에는 반영하지 않았다. n8n의 history 실행은 미구현이다.
2. P50 n8n launcher가 구 엔진 SHA를 고정하던 문제를 찾아 현재 후보 SHA로 로컬 변경했다. 고정 SHA가 테스트에서도 검증되도록 export 테스트 기대값도 갱신했다.
3. 오프라인 테스트 실행기에서 저장소 루트를 `sys.path`에 넣지 않아 `src.collection_schedule` 검색이 실패하는 문제를 수정했다. 이 수정은 `4088bad`에 커밋·push되어 로컬/원격 HEAD 일치를 확인했다.
4. Windows 테스트에서 짧은 경로 별칭과 정규화 경로 문자열을 직접 비교하는 픽스처 문제를 확인했다. 테스트가 `_load_pilot()`의 실제 정규화 동작과 같은 `Path.resolve()` 기준을 사용하도록 로컬 수정했다.
5. 업데이트된 P50 엔진으로 격리 n8n CLI mock 실행을 수행했다. n8n 2.40.7의 임시 프로필에서 실행 레코드가 `success`로 저장됐다. launcher 단독 오프라인 결과는 `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, 시도 0, 페이지 0, `live_meta_called=false`였다. 이는 n8n mock 경로의 오프라인 확인이며 예약 운영 실행이나 Meta API 성공을 뜻하지 않는다.
6. 사용자 제공 50행 로스터의 오프라인 정규화 및 핀된 P50 validator 통과, 기존 한 페이지 P50 live proof는 기존 HANDOFF에 기록돼 있다. 그 proof는 한 번의 승인된 A lane 요청에 한정되며, 예약 실행 검증이 아니다.

## 검증 결과와 현재 CI

- `python -m unittest discover -s tests -p test_collection_schedule.py -v`: 15/15 PASS.
- P50 엔진 `10ebaf8` focused suite: 48개 실행, 47 PASS, 1 SKIP.
- P50 n8n adapter/export focused suite: 13/13 PASS. 경로 별칭 픽스처 수정 반영 후 재실행한 결과다.
- `python tests/run_offline.py --list-groups`: 1,015 distinct tests 발견.
- `git diff --check`: PASS (줄바꿈 변환 경고만 있음).
- 전체 오프라인 테스트의 로컬 실행은 10분 넘게 완료되지 않아 중단했다. **CANCELLED이며 전체 로컬 suite PASS로 집계하지 않는다.**
- GitHub run `39725883303` (`4088bad`): Linux runtime PASS. Windows `remaining`에서 경로 별칭 fixture 1건이 FAIL했고 나머지 shards PASS. Fixture assertion has been corrected locally; new n8n-branch SHA CI has not run yet.
- P50 pinned SHA `10ebaf8` has its P50 offline and frozen-design checks PASS; broader P50 PR checks were in progress when checked. P50 branch now has subsequent commits through `5090ef0`, which have not been integrated into n8n.
- 과거 CI `39723488710`에서 확인한 import root 문제는 `4088bad`로 수정했다.

## 현재 변경 및 인수인계 상태

- `raw_v2/p50_n8n_tick.ps1`: P50 engine expected SHA를 `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`로 갱신.
- `tests/test_n8n_p50_export.py`: 같은 pin을 검증.
- `tests/test_three_api_p50_n8n_tick.py`: Windows 경로 별칭 픽스처를 `str(raw_v2.resolve())` 기준으로 수정 (`6d096d8`).
- `docs/P50_N8N_COMPLETION_TODO.md`: 격리 E2E 및 pin 검증 상태를 기록.

`6d096d8`의 CI는 후속 SHA로 대체되어 일부 shard가 CANCELLED였으며, 전체 성공을 의미하지 않는다. `dea9ecc`에는 fixture 보정, engine pin 및 focused tests가 포함되고 GitHub CI가 진행 중이다. 현재 docs/index 변경은 이 CI 관찰 후 별도 커밋한다.

## 운영 상태 / 금지 사항

- P50 n8n workflow는 비활성 상태로 유지한다. 기존 workflow의 5분 schedule은 승인된 실행 예산으로 검증되지 않았으므로 활성화하지 않는다.
- 운영 n8n 호스트/프로필은 확정되지 않았다. 로컬에서 발견한 n8n CLI 2.40.7이 운영 대상이라고 단정할 수 없다.
- 경고 채널은 사용자가 자격증명 기반 방식을 선택했지만, 구체적인 채널은 “나중에”로 보류됐다.
- 다른 엔진 소유자의 완료 및 quota group 간 사용량 독립성은 확정 전이다.
- P50의 한 페이지 live proof 이후 추가 Meta 요청은 하지 않았다. 새 Meta 실호출, 운영 DB 변경, 예약 활성화에는 별도 승인이 필요하다.
- P294 활성화는 보류하고, P50/P294 저장소·커서·수집 이력은 분리한다. Google Sheets는 n8n 핵심 및 P50 검증 뒤 단계다.
- 토큰·계정 ID·비공개 RAW 데이터는 보고서에 포함하지 않았다.

## 다음 작업

1. `dea9ecc`의 모든 GitHub Actions 완료를 확인한다. 실패하면 테스트/제품 문제를 구분해 수정하고 ledger를 갱신한다.
2. `P6_TROUBLESHOOTING_LEDGER.md`, `HANDOFF.md`, `STATUS.md`에 CI fixture 회귀/해결 경과를 동기화하고 Project Book 색인을 갱신한다.
4. P50 production n8n 프로필, schedule/page budget, credential-backed alert channel, engine handoff/quota ownership을 확정한다.
5. 별도 승인된 P50 실수집과 n8n 검증 후 P294 확장, 그 다음 Google Sheets 연동을 진행한다.

## GPT 다음 담당자 안내

시작 시 `AGENTS.md` → `DESIGN_FREEZE_NOTICE.md` → frozen P6 design → 최신 `HANDOFF.md` / `STATUS.md` → `docs/P6_TROUBLESHOOTING_LEDGER.md` 순서로 읽는다. 현재 미커밋 파일을 보존하고, P6 내부·운영 DB·schedule·Meta 호출은 건드리지 않는다. 우선 로컬 diff와 run `39725883303` 결과를 확인하고, 새 SHA의 CI를 검증한 뒤 인수인계 문서를 동기화한다.
