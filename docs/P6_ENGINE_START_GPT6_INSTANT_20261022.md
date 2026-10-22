# GPT-6 추론 1단계 — P6 엔진 즉시 시작 인수인계 (2026-10-22)

> **이 문서는 새 GPT-6 작업자를 위한 실제 실행 지시문이다.** 지금은 별도 도큐먼트 브랜치에 있다. 엔진 구현은 본인의 **새 독립 feature 브랜치/worktree**에서 진행하고, n8n Codex와 직접 공유 파일을 수정하지 않는다.

## 읽기 순서 (빠뜨리지 말 것)

1. `DESIGN_FREEZE_NOTICE.md`
2. `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md` — **1~15절 전체**
3. `AGENTS.md`
4. 해당 작업 기준 HEAD의 `HANDOFF.md`, `STATUS.md`, `docs/P6_TROUBLESHOOTING_LEDGER.md` 최신 상단
5. **[59개 세부 작업 지시서](P6_ENGINE_WORKBREAKDOWN_GPT6_INSTANT_20261022.md)**
6. **[P6 → n8n 상태 인터페이스](P6_ENGINE_N8N_CONTRACT_20261022.md)**
7. [기존 P6 49건 안전 복구 Draft PR #11](https://github.com/hibi89/ohora-trend-catcher/pull/11) — 코드 후보만 검수, 기존 `P6-078` ledger 번호와 현재 B 안전화 `P6-078`의 충돌 주의

## 사용자 확정 역할

- **GPT-6 추론 1단계(너)**: 기존 294계정 P6 엔진/3 API/커서·Capture·RAW·coordinator 일관성/49건 및 invalid cursor 복구/오프라인 테스트.
- **Codex**: n8n 예약 실행, `three_api_n8n_tick.py`, `p6_n8n_tick.ps1`, 워크플로 JSON/UI/스케줄과 운영 연결.
- **사용자**: 실제 Meta 호출, 운영 SQLite 변경, 실패 요청 재전송(기본 금지), quota/cooldown 정책 변경, 설계 변경, 최종 `main` 병합 등 승인권.

## 시작 1회차 수행 지시

**질문하지 말고 지금 G01~G08(8개 단위)부터 작업한다.** 이 지시는 사용자에게 이미 제공된 정보 재질문이 아니라 GitHub/로컬 원본 검증을 요구한다. 로컬/도구 접근이 제한되면 해당 G 단위는 접근 불가로 기록하고 가능한 GitHub 읽기 전용 검증을 이어간다.

1. `hibi89/ohora-trend-catcher` 의 **최신** `codex/p6-readiness-hardening-20261019` HEAD SHA 재조회. 계획 당시 SHA `080f66a8a2d947462fed27ad77833a44cfb3c965`를 고정 정답으로 사용하지 말 것.
2. `git status --porcelain=v1` / 현재 브랜치 / `git worktree list` / 다른 작업자 변경 범위 확인. 무단 `reset/clean` 금지. git에서 원본 보존.
3. 위 읽기 순서 문서와 59개 단위 체크리스트를 읽고 **현재 구현돼 있는 코드만** 표시. 특히 `plan_cursor_recovery`의 overlap 알고리즘 재작성 금지.
4. **새 독립 코드 브랜치/worktree** 만들기. 별도 작업 브랜치 코드로 커밋. n8n 파일 수정 금지.
5. 기존 `three_api_paging.py`, `three_api_meta_normalizer.py`, `three_api_paging_recovery.py` 테스트를 **HTTP 차단 합성 모드**에서 baseline 실행. 실패 시 원인과 기존 HEAD 기록; 허위 통과 금지.
6. PR #11의 중첩 `business_discovery.media` 처리 패치가 원본에 필요한지 diff 검토; 필요한 코드·테스트만 선택적으로 재사용. PR #11 전체 자동 병합·`P6-078` 문서 충돌 불가.
7. **G01~G08 실행 결과**를 한 번 보고: 원본 HEAD/코드 브랜치 SHA, 수행한 G ID, 실제 검증 커맨드와 PASS/FAIL/SKIP, 외부 운영 호출 0, 남은 일, 수정 파일, 충돌 위험, 다음 단위 N01부터 예정.
8. 이후 N/Q/R/L/I/V를 지시서 순서대로 **5~8단위씩 연속 진행**. 가상 성공을 실운영 검증으로 오인하지 말 것.

## 진행 일정 / 완료 정의

- 개발·오프라인 통합: **14~24시간 추정 작업량** (실제 경과시간 약속이 아님), 상세 59단위 구분.
- P6 정상 수집 경로 **먼저**, 이후 cursor invalid→`failed_no_retry`→별도 신규 복구 라운드(3페이지/4건)→기존 RAW ID overlap→실제 새 cursor 또는 `coverage_gap`.
- 기존 49건은 after만 있고 terminal=1로 기록된 **불일치**. 증거 보호, 변경 없는 입력 경로 검수, 합성/복제 환경 복구만 먼저. **운영 49건 직접 UPDATE/마이그레이션은 미승인**.
- 안정적인 P6 결과 계약을 Codex에 전달. 실제 step 상태/guard 이유/복구 planner 상태를 혼동하지 말 것.
- 오프라인 구현 완료라도 운영 실 Meta, 49건 수리 적용, 294계정 전량, 365일 history 완료는 **승인과 실검증이 없으면 미완료**.

## 작업 분쟁/안전 중단 규칙

- Codex가 엔진 파일까지 동시에 수정하고 있으면 해당 파일 즉시 중단하고 **누가 어느 파일 소유인지 확인**, 다른 독립 단위부터 계속 진행. 타인 로컬 변경 보존.
- 일일 데이터/토큰/비밀키/계정명/원본 커서·응답은 Github과 채팅에 출력 금지.
- 동일 작업 1~2회 실패하면 반복 시도 대신 원인, 실패가 생긴 계층, 대안과 검증 방법을 기록한다.
- 단 하나의 새로운 Meta 호출도 사용자 명시 승인 없이 불가. 만료된 쿨다운 자체는 API quota 안전 증거가 아니다.
- GitHub 사고번호 `P6-078`은 이미 메인 P6 코드 브랜치에서 **B quota-resume hardening**이 사용한다. 우리 이전 별도 PR #11의 같은 번호 충돌 기록을 **재사용하지 말 것**.
- 공유 P6 작업 브랜치와 `main`에 직접 push하지 않는다. 자기 브랜치에서 Draft PR 작성, 실제 코드 적용은 검수 후.

## 복사용 최초 지시문 (새 채팅)

> 너는 **OHORA P6 수집 엔진 전담 GPT-6 추론 1단계**다. 저장소 `hibi89/ohora-trend-catcher`. 계획 브랜치 `plan/p6-engine-gpt6-instant-20261022` 의 `docs/P6_ENGINE_START_GPT6_INSTANT_20261022.md`, `docs/P6_ENGINE_WORKBREAKDOWN_GPT6_INSTANT_20261022.md`, `docs/P6_ENGINE_N8N_CONTRACT_20261022.md`를 읽고, 동결 설계·AGENTS·현재 P6 브랜치 HEAD·HANDOFF·STATUS·ledger를 최신화 확인하라. **G01~G08부터 즉시 실행해 8단위 묶음으로 결과 보고한 뒤 N/Q/R/L/I/V를 계속해라.** 구현은 네 독립 브랜치/worktree에서만, Codex의 n8n 파일은 수정하지 마라. 기존 `plan_cursor_recovery()` 재사용, 정상 일일 수집 경로 우선, 기존 49건 Capture/RAW 무손실 보호. Meta 실호출·운영 DB/커서 변경·쿨다운 리셋·기존 실패 재요청·`main` 병합 금지. 테스트·커밋 SHA를 근거로 구현/미구현/실검증을 구분하고, 동일 실패 반복하지 마라. 막힌 단위만 보류하고 나머지는 계속 진행하라.

**작업자가 자신의 워크플로우를 n8n 담당 Codex에 넘겨 중복 구현하는 것은 금지한다.**
