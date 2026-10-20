# P6 PR #6 병합 충돌 사전 감사 — 2026-10-20

**범위:** 승인된 오프라인 P1~P5 개발 브랜치 `codex/p6-readiness-hardening-20261019`와 `main` 비교. 이 문서는 충돌 원인 분석 및 해결 순서만 기록한다. **main 병합·동결 설계 수정·실 Meta 호출·운영 DB 변경 승인 아님.**

## 8개 독립 점검 결과

1. **PR 상태:** PR #6 OPEN/DRAFT, `mergeable=false`, `mergeable_state=dirty`, `rebaseable=false`. GitHub가 병합 충돌로 판단.
2. **분기 차이:** `compare codex/p6-readiness-hardening-20261019...main` 결과 diverged, main-only 8 commits, p6-only 449 commits(조회 시점 기준). PR이 자동 병합되지 않는 원인은 단순 CI 실패가 아님.
3. **동결 원문 동일성:** `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`의 main/p6 blob 모두 `9ba0893b2d4691ee4e808d7a7fb176188a24100b`. 동결 설계 본문 차이 없음.
4. **동결 공지 동일성:** `DESIGN_FREEZE_NOTICE.md` main/p6 blob 모두 `e01883fd5f1625656ea67ced3e4a48546366f618`.
5. **CODEOWNERS 동일성:** `.github/CODEOWNERS` main/p6 blob 모두 `f3135bfe78316077f56bfe523980f9ff6b3349a7`.
6. **Guard workflow 차이:** `.github/workflows/p6-frozen-design-guard.yml` main blob `be386bcc19ce67607d37016d4a0a5420a9c58c16`, p6 blob `c662c8f069d8059f878a3ba11fefd59a2d2d5e2f`. 병합 시 guard 내용 및 트리거 검증 필요; 두 파일의 충돌 여부 자체는 미확정.
7. **통합 CI:** Runtime run `39403014275` commit `fce1c0a`: Linux 1 + Windows 4 jobs 5/5 SUCCESS. Three API `39402632062`, RAW v2 `39402626109`, Frozen Guard `39403053579` SUCCESS. 이는 오프라인 검증이며 운영 검증이 아님.
8. **문서 분기:** `AGENTS.md`, `README.md`는 main/p6 본문 동일. `HANDOFF.md`, `STATUS.md`는 양쪽에서 수정되었고, main에는 최상위 동결 공지, p6에는 상세 구현·테스트 이력이 추가됨. 충돌 해결 시 **main 최상위 동결 공지와 p6 검증 이력을 모두 보존**해야 한다. 정확한 충돌 hunk는 아직 확인되지 않았으므로 해당 두 파일을 확정 충돌 파일이라고 단정하지 않는다.

## 다음 안전 작업

- 격리된 로컬 임시 worktree에서 `git fetch` 후 `git merge --no-commit --no-ff origin/main`으로 충돌 파일 및 hunk를 **읽기 전용 목적**으로 확인하고, 테스트 뒤 `git merge --abort`. 기존 작업 트리와 main은 변경하지 않는다.
- 해결 브랜치가 필요하면 p6의 별도 임시 브랜치에서만 수정하고, Frozen Design Guard·Three API·RAW·Runtime 전체 재검증. 사용자의 **main 병합 별도 승인**을 받기 전 PR merge/auto-merge 금지.
- `STATUS.md`/`HANDOFF.md`의 최상위 동결 공지와 상세 이력 양쪽 보존, 동결 원문 blob 동일성 재검증.

**판정:** 8개 점검 완료, 충돌 원인 범주 식별. 실제 충돌 파일·hunk 확정 및 충돌 해결은 미완료. 추가 운영 권한 사용 없음.

## 2026-10-20 후속 점검: 공통 조상 기준 3-way 변경 경로 확인

공통 조상: `bcdb84b640738fdfceaf833dd831a26d401a2427`. PR #6 최신 조회 `mergeable=false`, `mergeable_state=dirty` (HEAD `c193076`). 실제 `git merge-tree`/worktree 실행은 **아직 수행하지 못했으므로** 아래는 확정 conflict hunk가 아니라 3-way 충돌 후보 목록이다.

| 경로 | 공통 조상 | main | p6 | 해석 |
|---|---|---|---|---|
| `AGENTS.md` | `6a40d95` | `a5aff41` | `a5aff41` | 양쪽 최종 내용 동일; 해결 대상 아님 |
| `README.md` | `e4fee14` | `49f45ba` | `49f45ba` | 양쪽 최종 내용 동일; 해결 대상 아님 |
| `HANDOFF.md` | `24b210b` | `e3c833e` | `a78d696` | 양쪽 별도 변경; 충돌 가능 |
| `STATUS.md` | `838e9e8` | `43b7cb7` | `575666b` | 양쪽 별도 변경; 충돌 가능 |
| `.github/workflows/p6-frozen-design-guard.yml` | 없음 | `be386bc` | `c662c8f` | **양쪽 독립 신규 생성(add/add)**; 내용 다름, 충돌 우선 후보 |

**확인된 실질 위험 3경로:** `HANDOFF.md`, `STATUS.md`, `.github/workflows/p6-frozen-design-guard.yml`. 특히 마지막 파일은 공통 조상에 없고 양쪽에서 서로 다른 내용으로 신규 생성된 것이므로 통상 Git merge에서 add/add conflict가 발생한다. 이 검사는 main/p6의 세 버전 blob을 직접 비교한 결과다.

**보존 원칙:** guard workflow는 main의 frozen SHA 검증과 p6의 CI 변경을 병합할 때 함께 유지. STATUS/HANDOFF는 main의 동결 최상위 공지와 p6의 개발·테스트 이력을 모두 유지. 동결 설계 원문 및 CODEOWNERS 수정 금지. GitHub 병합은 별도 승인 필요.
