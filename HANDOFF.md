# OHORA Trend Catcher — HANDOFF

## ① 목표·용도·확정 요구·기술
- 사용자 프로젝트: 엘리스 KDT OHORA 네일 트렌드(Instagram Business Discovery, 294 official accounts KR100/US101/JP93).
- 기존 고객 KQ1–KQ3 설명은 `REQUIREMENTS.md` 참조; 기존 공식 계정 목록과 로컬 원본은 단일 입력 기준으로 보호한다.
- **2026-10-18 사용자 목표(아직 설계 토론 중):** 각 계정에서 최신부터 50개 확보(최대 14,700개), 신규 게시물을 계속 추가하고 과거 이력 1년→가능하면 2년까지 확대. 향후 4개 대시보드 탭(원본 탐색 / 가중치 트렌드 / AI 데이터 비서 / 수집 관제).
- 기술 현황: Python, Meta Graph Batch API/Business Discovery, 로컬 SQLite+CSV, 구형 커서 백필/분리된 AI vision. 새로운 원본 JSON/CSV와 관측 이력 DB 설계는 **미구현**.

## ② 완료 기능·변경·결정 근거
- 기존 구현·실험은 `STATUS.md`, `README.md`, `experiments/`에 보존. 사용자에게 과거 286/294 성공 보고가 있었으나 새로운 RAW 프로젝트 결과가 아님.
- 2026-10-18에 `docs/LONG_TERM_RAW_COLLECTION_VISION_2026-10-18.md`를 **논의용 목표**로 GitHub에 기록하고 README/STATUS에서 링크. 이 문서는 실행 승인 문서가 아님.
- 권장 구조: 최신 동기화/과거 확장/지표 재관측 분리; 실행별 비밀값 제거한 RAW 응답 보존 + 누적 조회 DB + 파생 트렌드/AI 분리. 사용자에게 후속 설명을 들은 뒤 설계 확정할 것.

## ③ 테스트 명령·실적·미검증
- **이번 인수인계 범위는 문서 저장뿐**: 실제 수집·코드 수정·로컬 테스트/유료 API 호출 **0건**.
- GitHub 문서 생성/재조회, README·STATUS 갱신 확인. 새 설계의 동작·API 버전/필드·커서·이미지/한도는 전부 **미검증**.
- 기존 과거 오프라인 테스트에 대해 과장하지 않음. 기존 코드 검증 가이드는 `experiments/README_CURSOR_BACKFILL_90M.md`, `experiments/README_TWO_APP_LATEST10.md`. 사용자 Windows 새 설계 테스트는 아직 없음.

## ④ 완료/진행/대기/차단·중단 지점
- 완료: GitHub에 장기 비전 기록.
- 진행: 사용자와 설계 토론 중. 사용자가 **'시행하지 말고 할 말이 있다'**고 명시.
- 대기: 추가 사용자 요구 → 설계 변경점 비교/합의 → 명시적 실행 승인.
- 차단: 과거 기록상 두 번째 Meta 앱 인증 만료; 독립 앱 한도/Graph 실조회 미검증.

## ⑤ 오류 근거·원인·시도·재시도 금지·대안
- 현행 `rolling_backfill.py`의 50개 종료 및 최신 수집 기능 별도 부재는 소스/README 확인 사실. 신규 스키마·API는 시도하지 않았으므로 성공/실패를 주장하지 않는다.
- Meta 과거 error #4/#110 및 인증/제한은 `STATUS.md` 참조. 유효한 토큰 확인 전 실호출 금지, 비용/권한 우회 금지.
- 대안: 승인 후 기존 코드를 수정하거나 별도 RAW 엔진을 신규 구축. 현재 제안은 기존 원본을 보호하는 별도 엔진 + 공통 API 기능 재사용.

## ⑥ 저장소·브랜치·SHA·주요 경로
- 저장소: `hibi89/ohora-trend-catcher`; 브랜치: `main`.
- **2026-10-18 계획 기록 직전 확인 커밋:** `71fa917aba523d9d95f074f5607a6433128b13bd`.
- 장기 비전 문서 생성 커밋: `a4464aac881a126ab47d43d3d5095dfbdfa01b24`; README 링크: `3d9aa46c0cf1a5525ccfb5dcce82a4499ca1c866`; STATUS 기록: `c69f492f3eb80572b15b4b0d2bc3420e6f44a2ce`.
- 경로: `docs/LONG_TERM_RAW_COLLECTION_VISION_2026-10-18.md`; `STATUS.md`, `REQUIREMENTS.md`, `AGENTS.md`, `README.md`; `experiments/full_batch_collector.py`, `experiments/rolling_backfill.py`, `experiments/run_90m_cycle.py`.

## ⑦ Production URL·Vercel 프로젝트·실행 명령·환경
- Vercel: 이 수집 프로젝트에는 **미적용**. UI/4탭 대시보드 구현·배포 없음.
- 기본 실행은 Windows 로컬 Python + 인증된 Meta 토큰/IG ID의 기존 코드만 가능; 이번 설계 기록에 관련된 **실행 명령 없음**. 기존 예제는 `experiments/README_CURSOR_BACKFILL_90M.md` 참조하되 승인 없이 `--live` 금지.

## ⑧ 원본 위치·백업·인증 관리·수정 금지
- 공식 `account_list.csv`, 기존 SQLite/CSV 스냅샷, 로컬 `.env.local`/토큰은 사용자 PC에 보관. GitHub에서는 코드·문서만 보관하고 원본/토큰 업로드 금지.
- 기존 `latest_5`, `latest_1_pending`, `latest_10_owner_slot2_151_294`와 기존 `kq3_294_collection` 원본 절대 덮어쓰기/자동 병합 금지.
- 사용자 첨부 `OHORA_RAW_COLLECTION_EXECUTION(1).md` (2026-10-18)는 토론 참고 초안. 최신 사용자 메시지가 명시적 실행을 보류함.

## ⑨ 다음 작업 우선순위·절차·완료 기준·승인
1. 사용자의 이어지는 말을 먼저 청취하고 반영.
2. 현행 API/RAW 코드와 목표 사이 차이를 문서로 토론·수정. 중요 설계 2안 비교, 비용·한도·복구·중복·권한 검토.
3. 사용자 명시적 실행 승인 후에만 기존 원본 백업/원격 SHA 확인 → 코드 개발 → 오프라인 테스트 → 제한된 실제 API 검증 → 허용 범위 수집 → 데이터 검증 → 상태/인수인계 기록.
4. 이 문서가 존재한다는 이유로 API 수집, 예약, DB 변환, 새 호스팅, 프로젝트/권한 변경을 시작하지 않는다.

## ⑩ 갱신 일시(KST)·확인 시점·범위·문서 기준
- 2026-10-18 21:46 KST: 문서화·GitHub 기록 시점.
- GitHub 소스/README/STATUS 및 프로젝트 등록부 읽기 수행. 실제 PC 파일/인증/Meta 프로덕션/API 성공 여부는 미검증.
- 인수인계 문서 기준 코드 상태: 기존 `main`에 **설계 문서·목표 기록만 추가**. 후속 저장 및 BOOK SHA는 실제 원격 재조회로 확인한다.
