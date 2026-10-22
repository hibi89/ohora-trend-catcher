# P6/P50 → n8n → Sheets: 오프라인 연동 준비 체크리스트 (2026-10-22)

> **상태:** 코드/계약 정적 검토 완료. n8n 실행·Sheets API 호출·294계정 실제 확장·사용자 PC SQLite 검증 **미실시**. 기존 P50 동결 수집 계약을 변경하지 않는다.

## 검토한 실제 소스
- `raw_v2/three_api_sheets_export.py`: `export_observations(db_path, after_id, limit)`, SQLite `mode=ro`, JSON 출력, Sheets 호출·P6 cursor 쓰기 없음.
- `docs/P6_N8N_SHEETS_EXPORT_CONTRACT.md`: n8n 측 upsert/checkpoint 책임, 원본 RAW/SQLite 유지.
- `raw_v2/three_api_p50_roster.py`: P50은 정확히 US18/KR16/JP16=50; 294개 전체 수집 대상과 혼용 금지.
- `raw_v2/three_api_n8n_tick.py`: 별도 스케줄 어댑터가 존재하지만 P50 이력 복구·쿼터 해제 권한을 자동으로 부여하지 않는다.

## n8n 담당자에게 전달할 구현 계약
1. 읽기 전용 내보내기 실행은 **실제 확인된** P50 RAW DB 경로에만 연결. `--after-id`는 n8n이 마지막 성공 응답으로 확인한 값, `--limit`는 1..5000.
2. 응답 `rows[]`의 `observation_key=request_id:post_id`로 Sheets **upsert**. 같은 `post_id`도 재수집 시 다른 관측치이므로 덮어쓸 단위는 `observation_key`다.
3. `reels_view_count=null`과 실제 0을 구분하고 `reels_view_count_status`를 함께 보관. 좋아요/댓글 결측도 0으로 대체하지 않는다.
4. 전체 페이지 쓰기 확인 **후에만** n8n 자체 checkpoint를 `next_after_id`로 갱신. 중간 실패 시 동일 페이지를 재생.
5. `rows=[]`이면서 `has_more=true`이고 `next_after_id`가 그대로면 **RAW 증거/무결성 gap**으로 간주. busy-loop, ID 증가, 미확인 행 건너뛰기 금지. 보류·알림 후 수동 진단.
6. P50/294, DB 교체·복원본마다 서로 다른 checkpoint namespace 및 Sheets 탭 사용. F5 대시보드 조회는 **저장된 결과만 읽고** Meta 재수집/LLM 재태깅을 실행하지 않는다.
7. `schema_version=p6-sheets-export-v1` 확인. 스키마 불일치·중복 `observation_key`·모호한 RAW receipt·원본 불일치는 **실패로 중단**, 누락 행을 성공 처리하지 않는다.

## 294계정 확장 전 승인 게이트
- [ ] 공식 294계정 원본 roster 및 P50과의 계정 ID 매핑을 **사용자 PC에서** 검증
- [ ] 50계정 원본/관측/영수증 및 5개 SQLite·RAW 백업 읽기 전용 점검
- [ ] `quota_hold` 실제 lane/reason/ready_at 확인; 안전 사용량 새 증거 및 별도 사용자 승인
- [ ] 3 API 직렬·`limit=4`·2라운드·85% 보호·65분 제약 유지 검증
- [ ] n8n 측 Sheets 부분 실패→재시도→중복 없는 upsert 오프라인 시뮬레이션
- [ ] 294개 실제 Meta 호출, 운영 스케줄 활성화, main 병합은 **각각 별도 승인**

## 완료 판정
이번 단계는 **연동 사양 정리만 완료**. `8ddbfd0` 오프라인 CI는 모두 통과했으나 실제 Meta 수집·n8n/Sheets E2E 성공을 의미하지 않는다. 운영 복귀는 `docs/P50_CLOSEOUT_AND_SAFE_RESUME_2026-10-22.md`의 P0 로컬 점검부터 시작한다.
