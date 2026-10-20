# P6 릴스 조회수 확장 — 실측 근거·호환성 검토·구현 준비안 (2026-10-20 KST)

> 상태: **변경 제안/구현 준비**, 런타임 미구현, 새 기능 오프라인 검증 미실시. 동결 기준선의 수정 또는 실 Meta 수집 승인 아님.
> 소스 오브 트루스: [동결 공지](../DESIGN_FREEZE_NOTICE.md), [P6 최종 설계 1~15절](P6_FINAL_COLLECTION_DESIGN_2026-10-20.md). 본 문서는 별도 확장안이며 동결 원문을 대체·수정하지 않음.

## 1. 2026-10-20 Graph API Explorer 수동 실측

- 연결된 Instagram Business Discovery 요청: `17841438211608317?fields=business_discovery.username(nailmc.nail){media.limit(20){id,media_type,media_product_type,view_count}}` / Graph API v26.0.
- `VIDEO + REELS`: 반환된 릴스 **19/19건에 정수 `view_count` 포함**. 첫 4건 재조회에서 +4/+9/+1/+4 증가를 관측함. 이는 **서로 다른 관측값 2개**의 존재를 입증할 뿐, 운영 시계열 저장이 이미 구현됐다는 뜻은 아님.
- `IMAGE + FEED`: 표본 1건에서 `view_count` 키 미반환. `CAROUSEL_ALBUM + FEED`: 사용자 수동 검증 스크린샷의 표본에서 `view_count` 미반환(정확한 전체 표본 수 미확정).
- `VIDEO + FEED`: **미검증**. 일반 동영상도 수집 불가능하다는 결론 금지.
- 이 관측은 사용자가 수행한 **수동 라이브 API 요청** 결과이며, 자동 수집기/294계정/운영 DB에 대한 검증은 아니다.

## 2. 실제 코드 점검으로 찾은 연결 문제 (2026-10-20 feature 브랜치)

- `raw_v2/three_api_request_spec.py`의 `_FIELDS`에 `view_count`, `media_product_type` 없음. 명시적 opt-in 전까지 현재 4-post 요청/캐노니컬 증거는 그대로 보존할 것.
- `raw_v2/three_api_meta_normalizer.py`의 `FIELDS`가 `view_count` 및 `media_product_type`을 누락하여 정규화 때 값을 삭제함.
- `raw_v2/three_api_runtime_contract.py`의 `_POST_FIELDS`, `_POST_TEXT_FIELDS`, 계량값 타입 검증 및 `CaptureRecord.post_field_status`가 like/comments만 다룸. `sanitize_meta_response`도 같은 allowlist를 사용하므로 안전한 pre-normalization capture라도 `view_count`를 보존하지 않음.
- `raw_v2/three_api_raw_writer.py`의 `field_observations`는 `view_count`를 무조건 `not_tested`로 기록. `post_observations`에는 조회수 칼럼이 없고, typed `post_field_status` 집합은 현재 like/comments 고정.
- `raw_v2/three_api_observation_versions.py`의 metric component는 `like_count,comments_count`만 포함. 시점별 views, field-state, change-events, state-version 누적에는 **추가 연결 필요**. `structure`에도 `media_product_type` 포함 여부를 명시해야 함.
- 요청 문자열 하나만 변경하면 영구 저장 누락/기존 검증 거부가 발생할 수 있으므로 **전 계층 일관 변경** 필요.

## 3. 제안하는 기능 계약 (불변 수집 정책 유지)

1. **요청 명세: 선택적 확장.** `build_request_spec(..., include_view_count=False)` 형태의 opt-in을 우선 검토. 최초 구현은 기본 `False`로 두어 이전 요청 내용·필드 증거·v1.0 canonical bytes/hash를 변경하지 않는다. `True`인 새 요청에는 `view_count`를, 확정된 타입 분류에는 `media_product_type`를 명시적으로 포함한다. `RequestSpec`의 새 필드 추가보다는 기존 `media_fields` 문자열에 opt-in 반영하여 기존 digest 호환성을 우선 검증한다.
2. **한 페이지 요청의 제약.** Meta에 보내는 4개 게시물 `media{...}`은 요청 당시 IMAGE/REELS를 미리 필터링하지 못한다. 따라서 **필드는 해당 페이지 전체에 요청**, 반환값을 `media_type=VIDEO` AND `media_product_type=REELS`에 한정해 트렌드 지표에 사용한다. IMAGE/CAROUSEL에서 값이 없어도 오류나 0으로 만들지 않는다. 미래에 비릴스 반환값이 있다면 안전 RAW 원본에는 유지하되 정식 지표 적용은 별도 검증.
3. **원본·스키마:** HTTP 성공 응답의 안전한 수집 증거에 반환된 `view_count` 숫자를 보존하며, schema validation은 `null`, 0, 미반환, 미요청을 구분하고 음수/문자열/bool을 거부. 정수의 비정상적인 거대값도 안전 범위를 검토한다. 원래 CaptureRecord/RAW 영수증 재작성 금지.
4. **필드 상태와 출처:** `post_field_status.view_count`를 `returned | null | absent | not_tested`로 동작시키고 응답 `null`과 필드 부재를 혼동하지 않는다. API가 오류를 반환하면 성공 관측으로 기록하지 말고 기존 정지/복구 정책을 따른다.
5. **시계열:** `post_id, observed_at, request_id`별 관측에 조회수를 추가한다. 실제 숫자 변화가 없더라도 조회 영수증/관측을 남기고 기존 like/comments 및 P2 4분할 state-version·change-events 모델을 유지한다. 필요한 스키마 확장은 **새로운 격리된 RAW 클론에서 additive migration**으로 시험하며 구식 RAW/운영 원본에는 절대 직접 적용하지 않는다.
6. **분석(별도 계층):** 릴스 조회수 증가량 `views(t2)-views(t1)`, 관측 시간차에 대한 증가속도, 조회수 대비 좋아요 비율을 계산할 수 있다. 기준은 관측 시각이지 게시 시각이 아니다. 2개 이상의 유효 연속 관측이 없으면 속도는 `NULL`; 감소/수정/비정상값은 기록·플래그 처리하고 무조건 0 보정하지 않는다. 릴스와 이미지 인기 지표를 동일 기준으로 직접 순위화하지 않는다.
7. **수집 불변:** 공식 294계정, 3 Meta API 레인 A/B/C, `limit=4`, **일일 최신 2라운드(합계 3페이지/계정)**, 안전 응답 선저장, 85% 중단, 65분, 로컬-only replay, 커서/백필 규칙 그대로. 새 API 호출 횟수를 추가하지 않고 요청 필드 구성만 확장하는 방향. 단, Meta 요청 필드 증가가 응답 크기/usage/rate에 미치는 영향은 **실운영 미검증**.
8. **실패 시 안전한 롤백:** Feature flag off → 기존 요청 스펙으로 복귀. 기존 raw/capture/관측 행 삭제·재작성 금지. 실패 응답에 대해 묵시적 자동 재호출 금지. 이전 버전 계약 영수증 검증과 동시에 통과해야 함.
9. **활성화 경계:** live Meta 호출, 기존 운영 DB/CSV 변경, 스케줄러 활성화, `main` 병합은 **각각 별도 사용자 승인**. 구현 테스트는 synthetic fixture·격리 SQLite에서만 허용.

## 4. 구현 순서와 오프라인 합격 기준

- **Gate 0 — 기존 실패 정리:** Windows runtime CI에서 `test_two_page_history_receipts_survive_three_db_restart_and_kst_rollover`가 `WinError 32`로 임시 P3 SQLite 정리 실패. Windows teardown 연결 종료 순서 수정/CI 재확인 후 새 기능 추진. Linux 715/715 단독 PASS를 전체 CI PASS로 표현 금지.
- **Gate 1 — 요청·normalizer:** opt-in false 기존 digest와 전부 동일, opt-in true 안전 문자열에 `view_count` 포함, `media_product_type` 분류 보존, Meta-like JSON 릴스/IMAGE/CAROUSEL/VIDEO+FEED 4종 입력에서 안전 allowlist·필드 상태 일치. `limit=4` 보존.
- **Gate 2 — 계약/capture:** `view_count` returned(0 포함)/null/absent/not_tested/invalid 형식 및 API 실패를 각각 검증. `CaptureRecord` 원본 증거/후속 정규화/field-status 일치, 캐노니컬 구버전 호환과 digest tamper 차단.
- **Gate 3 — RAW & P2 시계열:** 격리 DB에서 기존 like/comments 상태 그대로, 릴스 view 시점별 2회/3회 관측, 변화 이벤트, 변경 없는 체크, 충돌/중복 request idempotence, crash/restart/checkpoint 증거, legacy 스키마 클론 안전 migration 검증.
- **Gate 4 — 기존 전체 회귀:** Linux 및 Windows CI 전 그룹 성공, frozen design SHA guard PASS, 신규 실 Meta 호출 **0회**, 로컬 운영 원본 변경 **0건**, `main` 미병합. 실패하면 확장 활성화 보류.
- **Gate 5 — 실운영 별도 승인:** 승인 후 소규모 계정/미디어 종류로 요청 성공·전체 raw/field status·quota 검증. 전체 294계정 적용은 별도 절차로 결정.

## 5. 구현 착수 전 명시해야 할 사실

- `view_count`의 실측 반환은 `VIDEO + REELS` 19건에서 확인됐지만, 일반 사진/캐러셀/일반 동영상에 대한 보편적인 Meta 정책을 단정할 증거는 없다.
- Graph API Explorer의 수동 요청 성공은 P6의 실제 request/transport/capture/RAW 파이프라인이 작동했다는 증거가 아니다.
- 본 문서와 GitHub 기록은 **제안 및 준비**다. 위 Gate 1~5의 구현이나 실운영 성공을 표시하지 않는다.
