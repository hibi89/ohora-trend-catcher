# OHORA Trend Catcher — 2026-10-22 교차 채팅 인수인계 (P6 + Emergency 50)

> **자료 성격**: 사용자 채팅에 제공된 Windows 실행 로그 + GitHub P6-071 문서 스냅샷에 근거한 운영 현황. 모든 수치는 아래 명시된 관측 시각 기준이며, 이후 실시간 상태는 미확인. **신규 Meta 실호출·운영 DB 변경·main 병합을 승인하는 문서가 아니다.**

## 0. 새 채팅 시작 순서
1. `DESIGN_FREEZE_NOTICE.md`, `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`, `AGENTS.md`, 이 문서, 최신 `HANDOFF.md`, `STATUS.md`, `docs/P6_TROUBLESHOOTING_LEDGER.md`를 먼저 읽는다.
2. 반드시 **P6 본수집기**와 **긴급 50계정 독립 수집기**를 구분한다. 어느 쪽이 실행 중인지 Windows에서 확인하기 전 Meta 요청/수동 tick을 중복 실행하지 않는다.
3. 기록상 성공한 수집과 오프라인 테스트, 계획/추정을 명확히 구분한다. 사용자의 새 로그가 있으면 그것이 이 문서보다 최신이다.

## 1. GitHub / P6 본수집기 (294계정, 3 API A/B/C)
- 저장소: `hibi89/ohora-trend-catcher`; 작업 브랜치 `codex/p6-readiness-hardening-20261019`. **main 미병합**, 동결 설계 변경 금지. Codex가 같은 브랜치에서 계속 작업할 수 있으므로 항상 origin 최신 ref부터 재조회.
- 2026-10-22 **08:36 KST** P6-071 읽기 전용 관측: **318 durable receipts / 1,260 post observations / 1,041 unique**; **A 146 done / 4 failed leased / 144 pending**, **B 0 done / 294 pending**, **C 69 done / 225 pending**. 이후 실시간 계수 미확인.
- n8n 격리 DB에 동일 이름 P6 workflow 기록 3개, **활성 1개**. 활성 workflow의 **08:35:15 KST trigger success**와 C +3페이지/+12관측의 시간적 일치 확인. **프로세스 존재만으로 성공 단정 금지**. 중복 비활성 레코드는 변경하지 않았다.
- A의 HTTP400 네 건은 Capture/response 근거 부재, `failed_no_retry`: 자동 재요청 절대 금지. **B `live_pending_usage` hold**, 신선한 안전 usage 근거 부족; 대기 이유의 최종 진단은 Codex가 조사 중. **C는 예약 tick으로 일부 진행**. P6 기존 저장소에 대해 읽기 전용 조회 외 수동 tick 금지.
- Cursor-invalid가 **명시적으로** 확인될 때 `resume_required`/no-retry로 기록하고 중단하는 코드는 존재. **최신에서 최대 3페이지 overlap을 확인하는 recovery planner는 오프라인 테스트만 통과**; 실제 API re-anchor, recovery runner의 예약 실행 연결은 **미구현·미검증**. overlap 없으면 `coverage_gap`, 게시물 삭제 단정 금지.
- Codex가 보고한 회귀 테스트: **988건 발견, 987 PASS / 1 SKIP / 0 FAIL**, cursor/recovery **60/60**, handoff guard **4/4**. 마지막 별도 문서 커밋 `960d1fd44714f6af9ceed251f82ef7f7fd42f910` 및 그 후 후속 문서 업데이트가 있어 **항상 최신 SHA 재조회**. 이는 실 Meta 복구 검증이 아니다.
- 현재 P6 작업자 마지막 보고: **B hold 원인 조사 중**. API A/B/C 상태, 새로운 실패와 최신 수치는 read-only로 재확인해야 한다.

## 2. 독립 긴급 50계정 수집기 (P6와 혼합 금지)
- 공식 294계정 P6와 **별도**: 미국 18 + 한국 16 + 일본 16 = 50개. **API C 단일 환경**. Windows 로컬 별도 배포본 (V3~V10), **GitHub P6 branch의 코드/운영 DB와 동일하다고 가정 금지**.
- 공통 데이터 경로: `$env:LOCALAPPDATA\OHORA_EMERGENCY_50_2026-10-21_C`; 중심 DB `emergency_latest.sqlite`. 버전별 Downloads 폴더와 관계없이 같은 DB/커서를 이어받음. 기존 P6 RAW 및 개인 백업 보존.
- **10/21 첫 최신 스냅샷**: 50/50 성공, 200건. 초기 백필 시 348건, 이후 1회 추가 페이지 수집해 352건(사용률 66→67%)이 실 Meta 응답으로 검증됨.
- **10/22 아침 이전** 과거 백필 수집기 7라운드 후 사용자 화면으로 **총 1,648 unique(그중 historical 1,448)**, 이력 수집 요청 362회, **2계정 complete / 48 pending** 보고. 이는 해당 시점의 스냅샷이지 지금 총량이 아님.
- **10/22 오전 최신 V10** 사용자 실호출 로그: **50/50 success, failed 0, latest 200 observation**, 국가별 18/16/16; 릴스 52, 릴스 좋아요 집계 33, 릴스 조회수 집계 44, carousel child 405; 종료 마지막 usage 50%. 오늘 200건은 어제 자료와 겹칠 수 있으므로 전체 고유건수에 200을 단순 합산하면 안 됨. `NULL`은 0이 아니다.
- **10/22 오전 V10 과거 이어받기**: 계정 page10 `HISTORY_OK` 계속, `ROUND_DONE: 2 USAGE_MAX: 85.0 PAUSE_REASON: quota_85_pause`, `WAIT_COOLDOWN_UNTIL_KST: 2026-10-22T09:38:30+09:00`까지 사용자 로그로 확인. **그 뒤 자동 재개·현재 저장건수는 미확인**.
- 로컬 버전 V8에서 66→67% 딱 1회 Meta 호출 성공했고 V8 운영에서는 85%에서 중단됨. V9 `--continuous`는 라운드 7/오전 8시 강제 종료를 제거하는 방향으로 변경되어 오프라인 9라운드/450회 가상 응답 테스트만 확인됨. V10 최신 수집 50/50은 **실 API 성공**. **독립 긴급 수집기의 장기 완전 백필 종료는 아직 증명되지 않음**.
- 긴급 저장 포맷: 최신/과거 post CSV, carousel children CSV, account/history status CSV, SQLite. 게시물 `permalink`, 반환된 경우의 `media_url`, `thumbnail_url`, media ID/순서 등. **동영상 MP4 파일 다운로드가 아니라 URL 메타데이터만 보존**; URL 결측/만료 가능. 게시 당시 반응값 재구성으로 오인하지 않는다.
- **중요 위험**: P6 자동 예약 수집의 C lane과 긴급 수집기 API C의 **동일한 실제 rate bucket/토큰 공유 여부 미검증**. 동시 요청/85% 초과가 없도록 확인 전에는 두 경로를 병렬 수동 가동하지 말 것. P6 coordinator 상태를 긴급 DB 기준으로 임의 reset하거나 반대로 긴급 수집기 게이트를 P6에 적용하지 말 것.

## 3. 사용자 확정 정책 / 금지
- **P6 동결:** 공식 294개, A/B/C 3레인, 게시물 limit=4, KST 일일 최신 2라운드/계정 최대 3페이지, 이후 catchup→365일 history; API 85% 안전중단·65분 제약(동결 설계 우선), Capture→RAW→영수증→커서 전진, 원본/측정 이력 보존, 중복 관측 보존·분석에서만 dedup. 메타 URL/릴스 URL 보존. 기존 RAW 원본과 main 유지.
- **별도 긴급 50계정 운영:** API C 하나, 85% 도달 시 중단→쿨타임 후 재개→미완료 50계정 수집 반복; 오전 최신 수집 후 이력 커서 이어받기. 원본 데이터는 공통 DB에 유지.
- P6 독립 디버깅/실호출/운영 DB 변경, A/B 실패 요청 재전송, cursor 복구 실호출, gate override, main 병합은 **별도 명시 승인 없이는 금지**. 긴급 모드에서 사용자가 승인했던 API C 호출을 P6 새 live 호출 승인으로 확대 해석하지 않는다.
- 보안: 환경파일/토큰/호출자 ID/대상 계정 실명·원본 데이터는 public GitHub 문서에 업로드하지 않는다. 이 문서에는 기밀값이 없다.

## 4. 새 채팅에서 먼저 할 일 (읽기 전용)
1. GitHub 최신 `HANDOFF.md`, `STATUS.md`, `docs/P6_TROUBLESHOOTING_LEDGER.md`의 맨 위를 읽고 Codex의 B hold 조사 결과, 최신 C tick, 최신 SHA 확인.
2. 사용자에게 실행 중인 **P6 n8n**과 **V10 emergency 수집기**를 중복 실행하지 않게 유지. 가능하면 각자의 로컬 **읽기 전용 status**로 실제 게시물 수/완료계정/쿨타임 확인. 사용자에게 이미 받은 로그를 재요청하지 말 것.
3. P6 잔여: B 안전 usage 증거와 hold 원인, A의 네 HTTP400 각각의 근거, C 완료 진척, scheduler 내 cursor recovery runner 미연결.
4. 긴급 잔여: 09:38:30 이후 정상 재개 여부, 48 pending 계정의 과거 완료까지 필요한 페이지, 저장 URL 누락율(반환값 기준), 백업/데이터 무결성. 검증 안 된 진척을 완료로 말하지 말 것.

### 읽기 전용 Windows 명령 참고
```powershell
# P6 저장소 루트에서만: Meta 호출 없음
$py='C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py raw_v2/three_api_manual_pilot.py --workspace (Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT') --status --day (Get-Date -Format yyyy-MM-dd)

# 별도 V10 폴더에서 상태만 확인: 실제 수집기를 새로 실행하지 않음
python .\overnight_backfill_50.py --source-day 2026-10-21 --status
```
