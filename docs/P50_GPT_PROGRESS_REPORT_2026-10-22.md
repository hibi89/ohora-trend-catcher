# OHORA P50/n8n 진행 보고서 — 2026-10-22

## 현재 인계 요약

- 작업 범위: P50 기존 수집 엔진을 재사용하는 n8n 연동과 오프라인 검증. P6 수집기 내부 및 커서 복구 기능은 다른 작업자의 소유 영역으로 둔다.
- 저장소: `hibi89/ohora-trend-catcher`
- 브랜치: `codex/p6-readiness-hardening-20261019`
- 보고서 기준 HEAD: `39e1e3a1aa222a25678d538bfec4ee388bb1b2d7`; local HEAD와 origin branch SHA 일치를 push/fetch로 확인.
- 이번 커밋: `6d096d8` Windows fixture correction, `dea9ecc` reviewed P50 pin, `39e1e3a` ledger + HANDOFF/STATUS sync. `main` merge 없음.
- `main` 병합은 하지 않았다.

## 지금까지 확인·완료된 작업

1. n8n이 사용할 정확한 엔진 SHA를 고정했다: 마지막으로 검토·테스트한 P50 SHA `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`. 해당 엔진은 Capture/RAW 증거 기반 latest-tail history anchor와 receipt ID 차단을 포함하고 focused suite 48개 중 47 PASS, 1 SKIP. P50 upstream은 fetch 시점 `52ed20a187dfc25abe725cfacee1a74efe99147c`까지 history request/commit, backlog, recovery 코드를 추가했으나 n8n 계약 검토/통합 테스트가 안 되어 pin에 반영하지 않았다. n8n에서 history를 실행하는 기능은 미구현이다.
2. P50 n8n launcher가 구 엔진 SHA를 고정하던 문제를 찾아 현재 후보 SHA로 로컬 변경했다. 고정 SHA가 테스트에서도 검증되도록 export 테스트 기대값도 갱신했다.
3. 오프라인 테스트 실행기에서 저장소 루트를 `sys.path`에 넣지 않아 `src.collection_schedule` 검색이 실패하는 문제를 수정했다. 이 수정은 `4088bad`에 커밋·push되어 로컬/원격 HEAD 일치를 확인했다.
4. Windows 테스트에서 짧은 경로 별칭과 정규화 경로 문자열을 직접 비교하는 픽스처 문제를 확인했다. 테스트가 `_load_pilot()`의 실제 정규화 동작과 같은 `Path.resolve()` 기준을 사용하도록 로컬 수정했다.
5. n8n 2.40.7의 격리 임시 프로필에서 이전 P50 mock workflow 실행 레코드 ID 3이 `success`로 저장된 것을 read-only 조회로 확인했다. 이후 현재 pinned engine `10ebaf8`으로 launcher를 직접 오프라인 실행해 `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, 시도 0, 페이지 0, `live_meta_called=false`를 재확인했다. n8n workflow 자체를 현재 pin으로 다시 end-to-end 실행한 것은 아니며, 이 결과는 예약 운영이나 Meta API 성공을 뜻하지 않는다.
6. 사용자 제공 50행 로스터의 오프라인 정규화 및 핀된 P50 validator 통과, 기존 한 페이지 P50 live proof는 기존 HANDOFF에 기록돼 있다. 그 proof는 한 번의 승인된 A lane 요청에 한정되며, 예약 실행 검증이 아니다.

## 검증 결과와 현재 CI

- `python -m unittest discover -s tests -p test_collection_schedule.py -v`: 15/15 PASS.
- P50 엔진 `10ebaf8` focused suite: 48개 실행, 47 PASS, 1 SKIP.
- P50 n8n adapter/export focused suite: 13/13 PASS. 최신 launcher pin 반영 후 재실행했다.
- Handoff guard + n8n focused tests: 17 PASS and 2 guard subtests PASS.
- `python tests/run_offline.py --list-groups`: 1,015 distinct tests 발견.
- `git diff --check`: PASS (줄바꿈 변환 경고만 있음).
- 전체 오프라인 테스트의 로컬 실행은 10분 넘게 완료되지 않아 중단했다. **CANCELLED이며 전체 로컬 suite PASS로 집계하지 않는다.**
- GitHub run `39725883303` (`4088bad`): Linux runtime PASS; Windows `remaining` failed one fixture path-alias assertion. Fixture corrected in `6d096d8`. `6d096d8` CI was superseded and Windows shards were cancelled, so it is not a full PASS.
- n8n `dea9ecc`: runtime, `tests`, `offline-tests`, contract and frozen-design checks PASS; several Windows shards were cancelled or superseded. Latest n8n `39e1e3a`: all reported GitHub check-runs completed SUCCESS, including offline-tests, Linux runtime, every Windows shard, contract and frozen-design.
- P50 latest remote SHA `52ed20a`: P50 offline, contract and frozen-design checks PASS; commits after pinned `10ebaf8` add history request/commit/backlog/recovery modules and have not been integrated into n8n. The n8n pin stays at the focused-tested `10ebaf8` pending contract review.
- 과거 CI `39723488710`에서 확인한 import root 문제는 `4088bad`로 수정했다.

## n8n 실행 환경 및 선택지

- 현재 Windows에서 `n8n` CLI는 설치되어 있고 버전은 2.40.7이다. n8n/Node/Docker process 없음, 포트 5678/5680/5681 listening 없음, Docker service 미설치. 저장소 검색에서도 학원 n8n URL/host/endpoint를 찾지 못했다. 이는 로컬 n8n 상태만 증명하며 외부 학원 n8n 상태는 **미확인**이다.
- **A안: 같은 Windows PC에서 n8n + P50 엔진.** 인증 파일·엔진·P50 저장소가 로컬에 있으므로 브리지/원격 인증 작업 큐가 필요 없고 운영 복잡도가 가장 낮아 보인다. CLI는 있으나 의도된 production profile, 서비스/로그인 자동 시작, n8n command-node 허용 설정은 미확인; 운영 스케줄은 비활성으로 남겨야 한다.
- **B안: 학원 원격 n8n + 로컬 실행 agent.** 원격 workflow가 이 PC의 PowerShell/파일을 직접 실행할 수 없으므로 인증된 queue/polling bridge, 명령 allowlist, 승인·중복 방지·agent 상태 감시가 추가로 필요하다. 학원 endpoint 접근성과 비용/라이선스는 미확인. 원격 환경이 이미 있어도 브리지 개발·보안 검증 시간이 A안보다 길다.
- **비교 한계:** A는 기존 장비를 쓴다면 추가 hosting 비용을 가정하지 않지만 Windows 가동·백업·n8n edition/license는 확인 필요. B는 기존 학원 n8n 비용을 재사용할 수 있는지 알 수 없고 bridge/agent 운영·감시 비용이 추가될 수 있다. 수치 공수/비용은 입력이 없어 산정하지 않았다. A는 동일 Windows account 안의 command node 실행 권한을 좁혀야 하고, B는 원격 인증 queue와 local agent라는 공격 표면 및 장애 도메인이 더 생긴다.
- **임시 권고:** 실행 위치를 한 곳에 두는 A안이 최소 구현 경로로 보이나, 실제 production host를 확정한 결정은 아니다. 외부 학원 n8n endpoint 또는 기존 운영 관례를 사용해야 한다면 그 URL/실행 위치 정보가 필요하다. 어떤 안도 아직 production에 배포·활성화하지 않았다.

## 현재 변경 및 인수인계 상태

- `raw_v2/p50_n8n_tick.ps1`: P50 engine expected SHA를 `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`로 갱신.
- `tests/test_n8n_p50_export.py`: 같은 pin을 검증.
- `tests/test_three_api_p50_n8n_tick.py`: Windows 경로 별칭 픽스처를 `str(raw_v2.resolve())` 기준으로 수정 (`6d096d8`).
- `docs/P50_N8N_COMPLETION_TODO.md`: 격리 E2E 및 pin 검증 상태를 기록.

위 수정은 `6d096d8`, `dea9ecc`, `39e1e3a`에 반영·push됐고 마지막 fetch에서 local/origin SHA가 같았다. `39e1e3a`의 모든 GitHub check-runs는 완료 PASS다. 보고서·handoff에 인용된 PASS는 각각 해당 commit/test command 범위로 한정한다.

## 운영 상태 / 금지 사항

- P50 n8n workflow는 비활성 상태로 유지한다. 기존 workflow의 5분 schedule은 승인된 실행 예산으로 검증되지 않았으므로 활성화하지 않는다.
- 운영 n8n 호스트/프로필은 확정되지 않았다. 로컬 CLI 2.40.7만으로 운영 환경을 증명할 수 없다.
- 경고 채널은 사용자가 자격증명 기반 방식을 선택했지만, 구체적인 채널은 “나중에”로 보류됐다.
- 다른 엔진 소유자의 완료 및 quota group 간 사용량 독립성은 확정 전이다.
- P50의 한 페이지 live proof 이후 추가 Meta 요청은 하지 않았다. 새 Meta 실호출, 운영 DB 변경, 예약 활성화에는 별도 승인이 필요하다.
- P294 활성화는 보류하고, P50/P294 저장소·커서·수집 이력은 분리한다. Google Sheets는 n8n 핵심 및 P50 검증 뒤 단계다.
- 토큰·계정 ID·비공개 RAW 데이터는 보고서에 포함하지 않았다.

## 다음 작업

1. `39e1e3a` GitHub Actions 완료를 확인하고 실패 원인을 정확히 분리한다.
2. Project Book main index에 `39e1e3a` 및 검증/미검증 상태를 기록하고 GitHub에서 재조회한다.
3. 학원 remote n8n endpoint가 있는지 확인하거나 A안 사용 여부를 결정한다. Production profile/command allowlist/schedule 예산/경고 credential 준비 전까지 workflow 활성화 금지.
4. 최신 P50 엔진 history interface가 안정된 뒤 pin을 갱신하고 mock latest→history→next-day resume 통합 테스트를 작성한다.
5. 별도 승인된 P50 실수집과 n8n 운영 검증 후 P294 확장, 그 다음 Google Sheets 연동을 진행한다.

## GPT 다음 담당자 안내

시작 시 `AGENTS.md` → `DESIGN_FREEZE_NOTICE.md` → frozen P6 design → 최신 `HANDOFF.md` / `STATUS.md` → `docs/P6_TROUBLESHOOTING_LEDGER.md` 순서로 읽는다. 최신 브랜치/HEAD를 fetch해 확인한다. 우선 `39e1e3a`의 Actions 결과를 확인하고 Project Book 색인을 동기화한다. 현재 n8n pin `10ebaf8`과 P50 upstream `52ed20a`의 차이를 보존하고, P50 history contract를 검토하지 않은 채 pin을 이동하지 않는다. 원격 n8n endpoint 확인 전 운영 instance를 가정하지 않는다. P6 내부·운영 DB·schedule·Meta 호출은 건드리지 않는다.
