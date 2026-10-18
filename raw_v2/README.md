# OHORA RAW v2 — 독립 수집기 (Python 3.11+)

> 상태: 소스/오프라인 테스트 구현 완료. 실제 Meta 130계정 시도(기존 5 + 추가 125), 신규 변경 4개/계정은 Actions 오프라인 28/28 PASS·Windows/Meta 실수집 검증 전. 이 명령만으로 예약 실행이 설치되지 않음. 기존 `experiments/results/`, `kq3_294_collection/`을 덮어쓰지 않는다.

## 원칙과 검증 범위

- 공식 294개 계정(KR100/US101/JP93) `account_list.csv`만 입력. 단일 Meta 앱으로 작동 가능하나 호출 한도는 별도이며 294계정/회차가 보장되지 않는다.
- 기본 `balanced`: 새 계정은 최근 4개(시험 기본값). 이후 *최신 재조회는 대략 하루에 한 번*, 나머지 회차는 기존 `after` 커서로 과거 4개씩 확장. 이미 저장된 최초 5개 데이터와 과거 커서는 재설정하지 않는다. 최근 페이지에서 기존 게시물을 재관측하면 `post_observations`에 매번 저장. 한 작업 단위(slot)는 최대 150개 **서로 다른 계정**(기본: 계정당 4개 게시물)이고, 150계정 처리 또는 API 안전중단 시 종료된 시점부터 60분 대기한다. 시험으로 일부만 실행했으면 같은 slot의 나머지 계정을 이어서 실행할 수 있다. 150+144=294계정이므로 한도에 걸리지 않는 이상 두 슬롯으로 한 번의 전체 순회가 가능하다. 4개의 Invalid user id는 별도 검토 대상으로 남긴다. 70분 시작 간격은 10분 안에 수집 완료됐을 때의 예상치이며 강제 API 리셋 시각이 아니다.
- 날짜는 서버가 아니라 로컬에서 검사한다. `--window-days 7`은 과거 확보 날짜의 **논리적 주간 구간**을 나타낸다. 기준 날짜는 최초 수집 시각(기존 DB는 가장 오래된 run의 시작 시각)으로 고정해 365일 목표가 매 실행마다 미끄러지지 않게 한다. Meta Business Discovery에서 `since/until` 서버 필터가 작동한다는 가정은 하지 않는다. 오래된 게시물의 탐색 종료는 `--history-days 365`(또는 730) 기준으로 판정하지만 목록이 완전하다는 보장은 별도로 검증해야 한다.
- 게시물 ID를 유일키로 두고 최초 응답의 caption 등을 보존한다. 이후 좋아요/댓글/계정 팔로워는 `post_observations` 및 `account_observations`로 매 회차 기록한다. 과거에 관측하지 않은 날의 값은 복원되지 않는다.
- 기본 쿼리는 GitHub에서 이전에 사용한 Business Discovery 필드만 요청. `--extended-fields`는 미검증 프로필 필드를 추가하는 **별도 승인된 시험용** 옵션. `shortcode`, `view_count`는 현재 요청하지 않고 `not_tested`로 표기한다.
- 캐러셀 children 첫 페이지를 별도 미디어로 저장. **다음 children 페이지가 있거나 50개가 반환되면 `carousel_status.coverage=partial`**로 기록하고 완전 확보라고 주장하지 않는다. 별도 children 후속 페이지 수집 기능은 미구현.
- CSV 원문 공백/이모지/문자열 ID 보존. Excel에서 ID는 **텍스트 자료형으로 가져오기**. 프로필·게시물에 대해 응답에 없는 필드 NULL과 0은 구분한다.
- API 전체 요청 실패, 내부 subrequest 실패, 필드 개별 미반환과 NULL을 구분한다. `like_missing_audit.csv`·`field_support.csv`는 **현상 진단**이며 필드 누락의 원인을 증명하는 자료는 아니다. 누락이 토큰/권한/미지원 때문인지 실제 소량 필드 분리 실험으로 확인해야 한다.


## 2026-10-19 — 4개 × 150계정 운영 시험 설계

- 변경 이유: 과거 `limit(15~25)` 시험은 사용자 측에서 이미 진행했고, 현재는 **294계정 전체 1회 순회에 필요한 150+144 구조**를 우선한다. `5→4`가 같은 API 하위요청 수에 대한 `total_time`을 줄이는지는 가설이며 실험으로 확인한다. 게시물 수를 4로 바꾸어도 API 서브요청 수는 줄지 않는다.
- **첫 조회** `media.limit(4)` → `backfill_cursor` 저장; **다음 과거 조회** `media.limit(4).after(backfill_cursor)` → 과거 4개씩 이동. `front`와 `history`는 독립, `front` 재조회는 과거 커서를 덮어쓰지 않는다.
- 예전 `limit(5)`로 수집된 630개는 그대로이고 재조회 시 게시물 ID는 중복 저장되지 않는다. 기존 DB 및 스키마 버전 `raw-v2.1` 호환; `config.backfill_anchor_utc`만 추가로 저장하는 additive migration. **로컬 SQLite를 삭제하거나 새 output-dir를 쓰지 말 것.**
- 일부 계정의 게시물이 4개 미만이면 받은 것만 기록한다. 다음 커서가 있을 때만 과거를 이어간다. 날짜 구간은 메타 서버 요청 조건이 아닌 저장 후 로컬 분류다.
- 150계정을 모두 처리하거나 API 사용률 70% 기준에 도달하면 slot을 닫는다. 마무리 후 60분 쿨다운 유지(시작~시작 70분은 수집 약 10분 전제의 운영 예상). 실제 요청이 150까지 완주한다는 보장은 없다.
- 다음 초기화 뒤 명시적으로 실행하는 단발성 4×150 테스트:

```powershell
python .\raw_v2\collector.py `
  --source $src `
  --env-file $envFile `
  --output-dir "$env:LOCALAPPDATA\OHORA_RAW_v2" `
  --mode balanced `
  --limit 4 `
  --batch-size 5 `
  --max-batches 30 `
  --stop-usage 70 `
  --live
```

- **GitHub Actions Python 3.11 합성 테스트 28/28 PASS**. Windows 테스트 재실행·Meta 실 4개 요청·실 과거 커서 역행은 아직 미검증. 반복 실행 스크립트는 `--limit 4`, 최소 시작 간격 70분과 종료 후 60분 대기를 모두 적용했으나 취침 중 자동 실행을 등록하거나 시작한 것은 아니다.

## Windows PowerShell 1차 검증 (API 호출 없음)

```powershell
# 기존 저장소의 root (git pull 이후)
python -m unittest discover -s tests -p 'test_raw_v2.py' -v
$src = '.\account_list.csv' # 기존 실제 294개 목록 위치로 조정
Test-Path $src
python .\raw_v2\collector.py --source $src --output-dir '.\raw_v2_results'
```

`Test-Path $src=False`이면 공식 원본을 안전하게 찾아 경로만 수정하되 원본을 GitHub에 올리지 않는다. 드라이런은 토큰을 읽지 않으며 Meta에 요청하지 않는다.

## 현장 소량 호출 — **유효한 로컬 인증정보와 Meta 사용량을 확인한 후만**

```powershell
$envFile = '.\kq3_294_collection\.env.local' # 실제 본인 로컬 인증파일만; 토큰 공유 금지
Test-Path $envFile
python .\raw_v2\collector.py --source $src --env-file $envFile --output-dir "$env:LOCALAPPDATA\OHORA_RAW_v2" --batch-size 5 --max-batches 1 --limit 4 --stop-usage 70 --live
```

첫 실 호출에 5계정이 참여하며, 계정당 최대 4개 게시물을 요청한다. 종료 코드 `2`는 일부 데이터 저장 후 API 사용량 또는 인증/응답 오류로 중단됐다는 의미다. 출력 `runs/<run_id>/run_manifest.json`, `field_support.csv`, `like_missing_audit.csv`, `collection_log.csv`, 원본 JSON을 확인하고 계속할지 판단한다. 앱 사용량 헤더가 빠지면 즉시 중단한다. 70%는 **요청 뒤** 평가하므로 선제 방어가 아니다. 단일 slot이 150계정에 도달하지 않았다면 재실행 시 즉시 이어받는다. API 중단·150계정 처리 후에는 종료 시각 기준 60분 쿨다운을 적용한다.

확인 후 동일 명령에서 `--max-batches 1`만 `--max-batches 30`으로 변경하면 최대 150계정 slot의 잔여분을 이어 수집한다. **사용자가 요청한 다음 회차의 실험값은 `--limit 4 --max-batches 30`**이며 기본 API 한도 중단선은 70%다. 이미 수집한 계정만 계속 호출하거나 원본을 덮어쓰지 않는다. 실패한 Meta 110 계정은 `blocked=1`로 기록되며 수동 원인 검토 전 무의미하게 재시도하지 않는다.

## 종료 후 60분 쿨다운 반복

이 CLI는 **한 번 실행하면 종료**된다. 코드가 관리하는 한 slot의 최대 요청 대상은 서로 다른 150계정이며, 종료 후 60분 쿨다운만 강제한다. 일부만 실행한 slot은 즉시 이어서 실행 가능하다. 사용자의 Windows 작업 스케줄러 등 외부에서 **각 slot의 종료 이후 60분**을 고려해 호출해야 하며, 자동 작업은 아직 설치되지 않았다. 사용자 PC에서 일정·프로세스/전원·인증 파일 접근 권한을 확인하고 등록할 것. 동시에 두 프로세스를 실행하지 말 것(동일 출력 디렉터리에서 프로세스 잠금 구현).

### 로컬 파일 레이아웃

```
raw_v2_results/                   # .gitignore 대상, 로컬에만 보관
  collection.sqlite              # 누적 DB 및 재개 커서, 수정 금지
  runs/<run_id>/
    input_accounts.csv
    accounts.csv                 # 이 run에서 응답된 계정
    posts.csv                    # 이 run에서 새로 발견한 원본 게시물
    post_media.csv               # 이 run에서 새로 발견한 구성 미디어
    field_support.csv            # 필드별 returned/null/absent/not_tested 샘플 통계
    like_missing_audit.csv       # 실제 like_count 미반환/NULL 관측 사례
    collection_log.csv           # API 호출 단위 기록
    run_manifest.json            # 전체 계정 상태/주간 범위/파일 SHA
    raw/*.json                   # 토큰/secret 문자열 제거한 API 응답 본문
```

`collection.sqlite`를 복사/이동하기 전 프로세스를 종료하고, WAL/SHM 파일까지 일관되게 백업해야 한다. 출력 폴더를 바꾸면 누적 기록과 커서도 새로 시작한다. GitHub/배포 서비스에 원본을 올리지 않는다.

## 완료 주장에 필요한 아직 남은 검증

1. KR/US/JP, FEED/REELS/CAROUSEL 각각 유효 토큰 소량 실 조회 및 사용자명 매칭.
2. Meta의 실제 중첩 `media.after` 커서가 2회 이상 재개되고 기존 포스트 ID를 건너뛰지 않는지 확인.
3. `like_count` 누락 사례를 응답 JSON과 `media_type`, `media_product_type`, 토큰 권한, 필드별 단독 시험으로 분석. 인증 오류/필드 단독 미지원/서버측 미반환은 서로 구분.
4. 권한이 허용되는 실제 게시물/이미지 조회. 이미지 **URL 저장과 이미지 파일 다운로드는 다름**.
5. 정확한 7일 구간/1년 커버리지 및 `children` 추가 페이지 완전 수집 보장은 미검증.
6. 70분 운영 안정성·Meta 사용률 회복 패턴은 장기간 관찰이 필요. 현재 원격에서 일회성 스케줄러 설치 없음.

## 저장 후 읽기 전용 검증

```powershell
python .\raw_v2\verify.py --output-dir '.\raw_v2_results'
```

파일별 SHA와 CSV 행 수, SQLite 참조 무결성/중복/PRAGMA integrity_check를 실제로 재열어 확인한다. 스냅샷을 손상시키거나 API 요청을 하지 않는다. 검증에 성공해도 Meta API 필드 완전성까지 보장하는 것은 아니다.

### 실험용 150계정 연속 수집 + 종료 후 60분 대기(로컬 명시 실행, 최대 10회)

```powershell
.\raw_v2\run_70m_loop.ps1 -Source $src -EnvFile $envFile -Cycles 10 -BatchSize 5 -MaxBatches 30 -OutputDir "$env:LOCALAPPDATA\OHORA_RAW_v2"
```

PowerShell 창이 열린 동안에만 실행된다. **Windows 예약 작업을 설치하거나 운영을 자동 개시하지 않는다.** `-MaxBatches 30`이면 한 slot 최대 150계정(기본 계정당 4개 요청)이며, 사용률이 70% 이상이거나 API 제한 오류면 더 일찍 중단한다. 반복 스크립트는 **종료 후 최소 60분**, **시작과 다음 시작 사이 최소 70분**을 모두 지킨다. 어느 간격도 실제 API 사용률 회복을 보장하지는 않는다. 사용자는 PowerShell에서 Ctrl+C로 중단할 수 있다.
