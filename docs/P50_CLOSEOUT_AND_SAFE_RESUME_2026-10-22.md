# OHORA P50 — 2026-10-22 최종 마감·안전 재개 인수인계

> **현재 기준 문서.** 2026-10-22 KST. 작업 브랜치 `feature/p6-50-account-pilot-20261022`. 금일 사용자 퇴근 후 **Meta API 요청·Windows 실행·운영 데이터 변경·쿼터 해제·main 병합·n8n 활성화를 모두 중지한다.** 이 문서는 다음 작업에 대한 실행 승인서가 아니다.
>
> 과거 `STATUS.md`, `HANDOFF.md` 및 `docs/P50_LOCAL_RUNBOOK_20261022.md`의 “history 미구현/실수집 0건/Windows 미검증” 문구는 **해당 작성 시점 기록**이며 최신 사용자 제공 결과를 대체할 수 없다. 기존 기록은 감사 추적을 위해 삭제하지 않는다.

## 1. 10월 21일 vs 22일 동일 50계정 수집 비교

| 검증 항목 | 2026-10-21 | 2026-10-22 |
|---|---:|---:|
| 대상 계정 | 동일한 50계정 | 동일한 50계정 |
| 해당 날짜 서로 다른 관측 게시물 | **164건** | **1,140건** |
| 수집 성격 | 최신 게시물 중심 | 최신 **600** + 과거 추가 **540** |
| 근거 저장소 | `OHORA_RAW_v2/collection.sqlite` | `OHORA_P50` 전용 RAW+SQLite |
| 계정 일치 근거 | 사용자: P50 50개 username을 10/21 DB에 **50/50 매칭** | 사용자: P50 50개 수집 결과 |

- 사용자 제공 검증에 따르면 10월 21일 **공식 294계정 전체 DB의 당일 관측은 960건**, 그중 동일 50계정의 당일 고유 관측은 **164건**이다. 960건 전체를 P50 결과와 섞지 않는다.
- 10월 22일 사용자 보고 고유 **1,140건 = 최신 150페이지/600건 + 과거 135페이지/540건**이다. 실제 사용자 원본 DB를 이번 GitHub-only 세션에서 재조회한 것은 아니다.
- **양일 간 게시물 ID 중복 제거 미실시.** 164와 1,140은 각 날짜 안의 관측 고유값이다. 단순 합계 **1,304건을 양일 통합 고유값으로 확정할 수 없다**. 22일 관측량 6.95배도 *수집 범위가 달라진 결과*이지 네일 트렌드의 증가율이 아니다.
- 21일과 22일의 원본 저장소, 수집 범위 및 날짜가 다르다. 21일 커서/RAW를 22일 P50 history DB에 임의 이식하지 않는다.

## 2. 10월 22일 실제 사용자 보고 종료 스냅샷

- 고정 대상: US 18, KR 16, JP 16. Meta API A/B/C 직렬, 요청당 `limit=4`.
- 최신 150/150페이지, 과거 135페이지; 전체 고유 게시물 1,140건.
- 과거 365일 범위: **1계정 window_covered / 49계정 continuation_ready**. 전체 50계정 과거 수집 **미완료**.
- 최종 출력: `{"status":"quota_hold","attempted_steps":136,"pages_committed":135,"last_lane":"A","last_account":null}`. 보호 종료 코드 경로와 일치하지만 **실제 레인 중단 사유와 마지막 프로세스 종료 코드는 로컬 미검증**.
- 사용자 보고 백업: `%USERPROFILE%\Downloads\OHORA_P50_BACKUP_20261022_173524`에 SQLite 5개와 `status.json`. 별도 RAW 파일 포함 여부, WAL/SHM 정합성, 원본 대비 해시·파일 수 검증은 대기.
- 외부 GitHub에서는 실제 Meta HTTP/토큰·운영 SQLite·RAW payload를 확인하거나 변경하지 않았다.

## 3. `quota_hold` 조건과 승인 전 안전 검증

소스 근거: `raw_v2/three_api_p50_history_live_runner.py` (66~161, 189~298, 328~405), `raw_v2/three_api_coordinator.py` (210~236), `raw_v2/three_api_p50_cycle.py` (83~123), `p50.ps1`.

| 레인 원인/상태 | 코드 동작 | 허용되는 후속 조치 |
|---|---|---|
| `usage_limit` | `observe_usage`: 사용량 `>=85`일 때 halt; `ready_at` 최소 65분 뒤 | 경과시간만으로 자동 해제 **금지**; 새 안전사용량 증거·경쟁 수집기 부재·승인·검증된 해제 경로 필요 |
| `missing_usage` | 사용량 `None`이면 보호 중단 | 안전 수치가 없으므로 자동/추정 해제 **금지** |
| `history_pending:<request_id>` | HTTP 전에 독립 history request binding과 레인 보류 기록 | 같은 요청의 **committed binding, Capture, RAW, history receipt, 응답의 0~84% 안전 사용량**이 전부 맞을 때 `_release_verified_history_hold`가 기존 보류를 복구할 수 있음. 새로운 HTTP 발송은 별개 |
| `blocked_unknown_http`·`blocked_missing_cursor` | 응답 불확실 또는 완전한 4건 페이지의 커서 증거 부족 | 같은 HTTP 재전송·커서 추측 금지; 별도 증거 심사 |
| 모든 레인의 `halted` 중 하나라도 true | `history_step` 208~210행에서 바로 `quota_hold`; 이어지는 HTTP 중단 | A/B/C 사용량 독립성은 미확정. 다른 레인으로 우회 금지 |

`Coordinator.reset_after_verified_cooldown`은 `ready_at <= now`일 때 **명시적 호출**로만 동작한다. **`p50.ps1`에는 안전한 quota reset 명령이 현재 없다.** 단순 시간 경과·기존 명령 재입력이 허가나 증거가 될 수 없다. `quota_hold`와 Ctrl+C(`KeyboardInterrupt`, exit 130)는 별도 경로다. 마지막 JSON만으로 85%에 정확히 도달했다고 단정하지 않는다.

**향후 운영 재개 7개 선행 조건:** ① 단일 P50 작업 브랜치·50계정 roster 대조, ② 동일 워크스페이스의 RAW/Capture/history/checkpoint 진단, ③ 레인별 `halted/reason/ready_at` 확인, ④ 불확실 HTTP와 중복 저장 여부 확인, ⑤ **65분 제약 및 현재 API 사용률 <85%의 별도 증거**와 A/B/C 공용 쿼터·경쟁 프로세스 점검, ⑥ 안전한 해제 방법의 오프라인 테스트, ⑦ 사용자 명시 승인. 충족 전에는 **그대로 보류**.

SQL 직접 `halted=0` 수정, `reason` 삭제, 강제 cursor reset, 기존 receipt 삭제, 모호한 요청 재시도, 다른 레인 우회는 금지한다. 앞으로는 기존 영수증 복구·승인된 최소 안전 절차를 우선 검토하고 DB 직접 편집은 채택하지 않는다.

## 4. PC 복귀 이후에만 실행할 **읽기 전용** 감사 명령

**오늘은 실행하지 않는다.** 아래 명령은 로컬 상태를 조회하며 `-Live`, Git checkout/pull, 비밀 출력, DB update를 포함하지 않는다.

```powershell
cd "$env:USERPROFILE\ohora-trend-catcher"
git branch --show-current
git rev-parse HEAD
git status --short
.\p50.ps1 status
```

레인별 원인은 `history_pending` 요청 ID를 숨기고 출력한다. 모든 SQLite 연결은 `mode=ro`이고 연결 종료를 보장한다.

```powershell
$root = Join-Path $env:LOCALAPPDATA 'OHORA_P50\p50'
@'
import sqlite3, sys
from pathlib import Path
from contextlib import closing
root = Path(sys.argv[1])
if not root.is_dir() or root.is_symlink() or root.name.lower() != "p50":
    raise SystemExit("STOP: invalid P50 root")
candidates = sorted(root.glob("????-??-??/coordinator.sqlite"))
if not candidates: raise SystemExit("STOP: no coordinator")
def ro(path):
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("STOP: missing/symlink DB "+path.name)
    return closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True))
coordinator = candidates[-1]
with ro(coordinator) as db:
    print("day:",coordinator.parent.name)
    for lane,halted,reason,ready_at in db.execute(
        "SELECT lane,halted,reason,ready_at FROM lanes ORDER BY lane"):
        category = "history_pending" if isinstance(reason,str) and reason.startswith("history_pending:") else reason
        print("lane:",lane,"halted:",halted,"reason:",category,"ready_at:",ready_at)
history = root/"history.sqlite"
if history.is_file():
    with ro(history) as db:
        print("history states:",db.execute(
            "SELECT state,COUNT(*) FROM history_accounts GROUP BY state").fetchall())
        print("history pages:",db.execute(
            "SELECT COALESCE(SUM(pages_verified),0) FROM history_accounts").fetchone()[0])
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='history_requests'").fetchone():
            print("request states:",db.execute(
                "SELECT status,COUNT(*) FROM history_requests GROUP BY status").fetchall())
raw = root/"p50_raw.sqlite"
if raw.is_file():
    with ro(raw) as db:
        for t in ("posts","post_observations","three_api_contract_receipts"):
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
                print(t,db.execute("SELECT COUNT(*) FROM "+t).fetchone()[0])
'@ | python - $root
$backup = "$env:USERPROFILE\Downloads\OHORA_P50_BACKUP_20261022_173524"
if (Test-Path -LiteralPath $backup) {
    Get-ChildItem -LiteralPath $backup -File | Select-Object Name,Length
} else { Write-Output "Backup not found" }
```

이 진단은 기본 **행 수·상태**만 확인한다. 현재 Meta 예산, SHA 기반 RAW 전수 검증, WAL-safe 백업 일치, 21·22일 게시물 ID 교집합/합집합은 추후 별도 검증 항목이다. 오류가 나면 로컬 원본 변경이나 반복 무단 조회 없이 중단한다. 출력에 실계정명·토큰·응답 원문이 보이면 GitHub에 올리지 않는다.

## 5. 다음 작업 우선순위와 완료 기준

| 우선 | 작업 | 완료 기준 |
|---|---|---|
| P0 | 로컬 레인 quota·기존 영수증·5 SQLite 및 RAW 백업 확인 | 읽기 전용 결과와 누락 내역 확보, 원본 불변 |
| P1 | 별도 승인 및 오프라인 안전 확인 후 49계정 과거 수집 재개 | 85%/65분/실제 쿼터/커서/중복 요청 게이트 만족 |
| P2 | 50계정 최대 365일 확보 및 원본 무결성 검증 | 50계정 각각 365일 cutoff/window 증거, terminal 또는 receipt-backed cutoff cursor, 요청→Capture→RAW→history receipt 연결을 확인하고 차단/누락/unknown을 공개. `continuation_ready`는 미완료 |
| P3 | 공식 294계정 확장 | 동결 설계·공식 roster·용량·비용 검토 후 승인 |
| P4 | n8n, Sheets, 태깅, 대시보드 연동 | API 수집·분석·조회 분리; 갱신 시 F5 재수집 금지 |

## 6. GitHub 이력과 검증 경계

- 앞선 테스트 수정: `b6f7346` (Windows Sheets test SQLite 연결 정리), 인수인계: `e579451`. 사용자 로컬 실행 결과 ≠ 이 환경의 실데이터 확인.
- `e579451`의 P50/Three API/C Contract/Frozen Guard는 성공, Runtime Integration [run 39750398082](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39750398082)은 **실패**. Linux 1,164건·Windows remaining 1,004건의 테스트에서 각각 동일한 **P6-089 상세 기록의 빠른 색인 누락** 1건이 실패했으며 기존 WinError32 문제와 구분한다. 마감 커밋은 이 색인을 바로잡는다.
- 오늘 마감 커밋의 검증은 문서·가드 및 GitHub Actions 결과로 보고하며 새 Meta HTTP, 운영 SQLite, Vercel Production 검증은 수행하지 않는다.
- 신규 채팅 복원: 본 문서 → 브랜치 `HANDOFF.md` → `STATUS.md` → `AGENTS.md` → `REQUIREMENTS.md` → 코드·Actions → 비공개 `hibi89/project-book/main/BOOK.md`의 p6 등록 기록. 다른 n8n 브랜치를 임의 병합하지 않는다.

## 7. 2026-10-22 백업 메인 파일 읽기 전용 교차검증

이 보충은 [P6-091](P6_TROUBLESHOOTING_LEDGER.md)의 후속 증거다. 조사 범위는 `%USERPROFILE%\Downloads\OHORA_P50_BACKUP_20261022_173524`의 다섯 SQLite 메인 파일과 `status.json`뿐이다. Capture/coordinator 두 파일은 `2026-10-22` 하위 폴더에 있다. 모든 SQLite 연결은 URI `mode=ro&immutable=1`로 열고 `quick_check`와 식별자 비출력 집합 비교만 수행했다.

- 다섯 메인 파일 모두 `PRAGMA quick_check=ok`.
- backup RAW 집계: 50 accounts, 1,140 posts/observations, contract/raw receipt 각 285.
- daily capture/coordinator: 150 captures, captured intents, request bindings, jobs가 서로 맞음.
- history: 50 accounts, 135 requests가 모두 `committed`; history Capture도 135 captures/intents. 49 `continuation_ready`, 1 `window_covered`.
- daily+history의 285 capture IDs는 contract/raw receipt 집합과 일치하며 receipt 참조도 연결됨.
- 백업 coordinator lane A는 `halted=1, usage_limit`; B/C는 unhalted. 이는 이 백업 스냅샷에 사용량 보호 중단이 저장됐음을 보이지만 현재 운영 레인 상태나 정확한 API 사용률을 확정하지 않는다.
- 백업의 WAL-mode 파일에 동반 `-wal`/`-shm`가 없었다. [SQLite WAL 규칙](https://www.sqlite.org/wal.html)에 따라 WAL 파일은 DB의 영구 상태에 포함될 수 있으며, `immutable=1` 검사는 main DB만 읽고 sidecar 상태를 증명하지 않는다. 따라서 `quick_check=ok`는 빠진 WAL이 없었음 또는 스냅샷의 최신성 증명이 아니다. active workspace와 backup 간 불일치도 여전히 미해결이다.

**운영 판단: P50은 재개 준비 완료가 아니다.** 49계정 resume와 quota 변경은 금지 상태로 유지한다. 귀가 후 canonical root와 백업 생성 방식/시각을 확인하고, SQLite가 지원하는 일관된 read-only 상태에서 sidecar를 포함한 검증을 수행한다. 다음날 최신 요청 격리는 합성 테스트로 확인됐지만 이 결과가 live DB의 분리·무손상을 증명하지는 않는다. CI는 코드 검증이며 실제 수집 성공이 아니다.

이후 읽기 전용 ` .\p50.ps1 status` 재조회에서 `history=not_initialized`, `latest.raw_exists=false`가 반환됐고 기본 AppData P50 폴더가 없었다. Downloads 백업은 존재한다. 이는 앞선 active-workspace 관측과 시간/환경이 다르므로, 자동 restore·초기화·복사를 하지 말고 복귀 시 canonical 위치를 먼저 확인한다.

다음날 최신 수집의 비파괴 기준은 기존 synthetic `test_next_day_latest_preserves_existing_history_state`: 다음 KST 날짜 coordinator/capture를 사용하고 history account rows, history receipts, history Capture bytes가 변경되지 않는 것을 확인한다. Restart/dedup 기준은 crash-window close/reopen 테스트가 같은 미확정 HTTP를 재전송하지 않고 기존 Capture/RAW 증거를 복구하는지 확인한다. 두 검증 모두 offline/injected sender 범위다.
