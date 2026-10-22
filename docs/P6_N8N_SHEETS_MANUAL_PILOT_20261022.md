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
