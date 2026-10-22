# P6 → Google Sheets: 수동 승인형 n8n 파일 (2026-10-22)

- 파일: `kq3_294_collection/n8n_p6_sheets_manual_pilot.json`. **미배포·미실행·비활성**. n8n 버전에 따라 Google Sheets node 파라미터 수정이 필요할 수 있다.
- **목적:** 이미 수집된 P50 SQLite의 검증된 관측치를 읽고 Sheets의 `observation_key` 기준으로 upsert. Meta API 호출 없음.
- **필수 선행조건:** n8n 실행 호스트에서 `Execute Command`, `python`, 저장소 파일 및 실제 RAW SQLite 읽기 권한. n8n Cloud에서는 Execute Command를 사용할 수 없거나 제한될 수 있다. 서버 컨테이너에서 Windows PowerShell을 실행할 수 없다면 해당 노드를 서버용 명령으로 **수동 교체**해야 한다.
- 환경변수 `OHORA_P6_REPO_ROOT` = 실제 저장소 경로, `OHORA_P6_RAW_DB` = 실제 P50 RAW DB 경로, `OHORA_P6_SHEETS_AFTER_ID` = 마지막으로 **전체 Sheets 저장을 확인한** 정수 커서(최초 0). 환경변수 접근은 n8n 배포 설정에 따라 차단될 수 있다.
- `REPLACE_WITH_SPREADSHEET_ID`를 실제 Google Spreadsheet ID로 교체, `P50_observations` 탭 생성, Google Sheets OAuth credential을 n8n에서 직접 연결. 탭에는 `observation_key`, `observation_id`, `account_key`, `post_id`, `country`, `published_at`, `collected_at`, `media_type`, `media_product_type`, `caption`, `like_count`, `comments_count`, `reels_view_count`, `reels_view_count_status`, `post_url`, `collection_batch`, `request_id`, `account_id` 컬럼이 필요하다. Google Sheets node의 자동 매핑 시 `_batch_*` 보조 컬럼은 제거하거나 별도 매핑으로 제외한다.
- **중요:** 이 파일은 안전한 수동 시험용 골격이다. 성공 후 자동 체크포인트 갱신은 **의도적으로 구현하지 않았다**. 일부 행만 성공했을 때 커서를 자동으로 진행시키면 데이터 유실 위험이 있다. 모든 행의 upsert 성공을 검증한 뒤에만 외부 환경변수 커서를 갱신한다. 같은 커서로 재실행해도 `observation_key`로 중복을 방지한다.
- `rows=[]` 및 `has_more=true`는 RAW 무결성 gap이므로 실패·보류. `rows=[]` 및 `has_more=false`는 할 일 없음. `reels_view_count=0`과 `null`을 구분한다.
- **금지:** 자동 활성화, 294계정 확장, 운영 DB 변경, Meta API 호출, 검증 전 커서 전진, 구형 `n8n_meta_collection.json` 활성화.
- **남은 단계:** 실제 n8n 버전/호스트 명령 호환성 확인 → Sheets 노드 필드 매핑 확인 → 샘플 1~2건 수동 E2E → 부분 실패 복구 검증 → 별도 승인 후 자동 체크포인트와 스케줄링 설계.

## 2026-10-22 보정
Sheets로 보내는 행에서 배치 커서 보조 필드를 제거했다. 동기화 커서는 Execute Command 노드의 JSON 출력에서 별도로 확인하고 전체 저장 검증 후에만 수동 갱신한다.

## 실행 환경별 명령 선택 및 사전 점검

현재 JSON의 `Read-only RAW export` 노드는 **Windows PowerShell용**이다. 실제 n8n 호스트가 Linux/Docker라면 아래처럼 해당 노드의 명령만 교체한다. Docker의 컨테이너 내부에서 Python, 저장소, SQLite가 모두 접근 가능한지 확인해야 한다. 컨테이너가 다른 머신에 있으면 로컬 PC 경로를 직접 읽을 수 없다.

Linux/Docker용 명령 예시(운영자가 실제 경로·환경변수 설정 후 적용):

```sh
set -eu
: "${OHORA_P6_REPO_ROOT:?missing repo root}"
: "${OHORA_P6_RAW_DB:?missing RAW SQLite path}"
after="${OHORA_P6_SHEETS_AFTER_ID:-0}"
case "$after" in *[!0-9]*|'') echo 'invalid cursor' >&2; exit 2;; esac
test -f "$OHORA_P6_REPO_ROOT/raw_v2/three_api_sheets_export.py"
test -f "$OHORA_P6_RAW_DB"
python "$OHORA_P6_REPO_ROOT/raw_v2/three_api_sheets_export.py" \
  --db "$OHORA_P6_RAW_DB" --after-id "$after" --limit 500
```

**주의:** 이 명령은 n8n의 실행 호스트 내부에서만 유효하며, 원본 SQLite를 수정하지 않는다. n8n의 Execute Command 노드가 비활성화되었거나 실행 호스트에 Python이 없다면 실행하지 말고, 읽기 전용 exporter를 별도 승인된 실행 환경으로 분리하는 방식을 검토한다.

### 수동 E2E 완료 기준

1. 스프레드시트 탭 헤더와 OAuth credential을 연결한다. `observation_key`는 유일키로 취급한다.
2. RAW 데이터베이스 **사본**과 테스트용 스프레드시트를 사용한다. 원본 P50 운영 DB를 임의로 이동하거나 수정하지 않는다.
3. 처음에는 `--limit 2`로 변경하여 최대 2건만 시험한다. 2건이 모두 저장되었는지 확인한다.
4. 동일한 `after_id`로 다시 실행하고, `observation_key` 기준으로 시트 행이 중복 생성되지 않는지 확인한다.
5. 일부 Sheets 저장 실패를 모의하거나 관찰하면 체크포인트를 유지하고, 같은 페이지를 재실행한다.
6. `rows=[]`와 `has_more=true`인 경우 즉시 중단하고 RAW 무결성 문제를 조사한다.
7. 전 행 저장·재실행 검증 후에만 운영자가 `next_after_id`를 기록한다. 자동 갱신 기능은 미구현 상태다.

**주의:** `Manual checkpoint approval required` 노드가 내보내는 `NOT_AUTOMATED` 표시는 실제 승인이나 영구 저장을 뜻하지 않는다. 자동 운영 전환에는 별도의 실패 복구·체크포인트 저장 로직과 승인 절차가 필요하다.
