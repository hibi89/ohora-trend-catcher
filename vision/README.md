# OHORA — 이미지+캡션 AI 디자인 분류 (독립 실행)

> **상태 (2026-10-16):** 소스 코드·오프라인 테스트 구현. 로컬 실제 수집 CSV 1,880건/미디어 5,307건 **읽기 확인**. 모델 서버의 인증, 정확한 모델 ID, 이미지 URL 접근성, 실제 분류 정확도/비용은 **미검증**. `--live` 이전에는 외부 API 호출이 전혀 없음.

## 입력 데이터와 결과

- 읽기 전용 소스: `experiments/results/full_294/collection.sqlite` (기존 77개 성공 계정의 전수 배치 실험), 또는 `experiments/results/latest_5/collection.sqlite` (새벽/아침 최신 5개 실험), 또는 구형 `kq3_294_collection/`의 `posts.csv`+`media.csv`. **원본 SQLite·CSV는 수정하지 않는다.**
- 출력: `experiments/results/vision/vision.sqlite`, `post_analysis.csv`, `selected_media.csv`, `vision_summary.json` (Git에서 무시). **분석 결과는 게시물 단위**이며 이미지별 세부 태그를 따로 추론했다고 표시하지 않는다.
- 입력: 이미지 게시물은 원본 이미지 URL, 동영상은 썸네일 URL, 캐러셀은 대표 1~3개의 자식 미디어를 사용. 캡션은 보조 맥락으로만 사용하며, 전체 영상 내용을 분석했다고 주장하지 않는다.
- 모델 태그: `is_design_post`, `color`, `technique`, `texture`, `motif`, `season`, `use`, 신뢰도, 시각적 요약, 한계. `like_count` 결측은 0으로 대체하지 않는다.
- 중복 방지: `post_id + model + prompt_version`과 캡션·선택한 미디어 ID를 지문으로 저장. 성공 건은 다음 실행에서 건너뛴다. CDN URL이 갱신되더라도 재분류하지 않는다. 실패·미디어 부재 건은 기본 건너뛰며 `--retry-failed`로 명시적으로 재시도할 수 있다.

## 아침에 그대로 실행할 순서 — PowerShell

프로젝트 루트에서 실행한다.

```powershell
cd "C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\ohora-trend-catcher"
git pull origin main
python -m unittest discover -s vision -p test_vision_runner.py -v

# API 키는 채팅이나 GitHub가 아니라 PC에만 저장. URL과 모델 ID, 키를 순서대로 입력.
powershell -ExecutionPolicy Bypass -File .\vision\setup_env.ps1

# 먼저 기존 수집 77계정 데이터로 분석 가능 항목 확인 (API 호출 없음)
python .\vision\vision_runner.py --source ".\experiments\results\full_294" --env-file ".\vision\.env.local"

# 사용자가 승인한 뒤 실제 비용 발생 가능: 우선 1건만 검증
python .\vision\vision_runner.py --source ".\experiments\results\full_294" --env-file ".\vision\.env.local" --limit 1 --live
```

첫 유료 요청 후 `post_analysis.csv`, `vision_summary.json`을 확인하고 응답이 타당하면 `--limit 10 --live`로 확대한다. 문제가 없다면 `--limit`을 제거하고 재실행해 남은 미분류 데이터를 분석한다. 이미 성공한 게시물은 자동 건너뛴다.

**새 최신 5개 데이터 분석**은 `--source ".\experiments\results\latest_5"`로 바꾸면 된다. **구형 수집 CSV** 분석은 `--source "C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\2차 프로젝트 홍주님께 제공할 수집데이터\kq3_294_collection"`로 바꾼다.

## 모델 연결·오류 대응

- `VISION_BASE_URL`은 사용자가 제공한 OpenAI 호환 게이트웨이의 `/v1` URL로 설정한다. `VISION_MODEL`은 해당 **제공자에게 실제로 허용된 모델 ID**를 입력한다. `gemini-3.8-flash`는 사용자가 말한 이름을 초기 예시로 표시했을 뿐 **게이트웨이에서 실측 인증/호환성 확인이 안 됐다.** URL 끝에 `/chat/completions`를 붙여 요청한다.
- `provider_http_401/403`: 해당 제공자의 인증/접근 문제로 중단. `provider_http_400/404/422`: 모델 ID, 입력 형식 또는 해당 제공자 프로토콜 오류일 수 있어 중단. 에러 본문·키·서명된 이미지 URL은 출력하지 않는다. `provider_http_429`: 과금/쿼터 확인 후 재개. 잇따라 두 건 실패해도 중단(Stop & Pivot).
- 이미지 URL을 서버가 가져오지 못하는 경우, 원인을 확인하고 **로컬에서 이미지를 임시로 내려받아 Base64로 보내는** `--image-transfer inline --retry-failed --limit 1 --live` 모드를 시험한다. 이때도 원본 이미지 파일은 디스크에 저장하지 않으며, JPEG/PNG/WebP 최대 5MB만 허용한다. URL 자체가 만료되었다면 Meta 재수집 없이 고칠 수 없다.
- 엘리스 등 다른 제공자 자격정보가 *이미 사용자의 PC에 별도로 설정*돼 있고 데이터 외부 전송에 동의한 경우 `VISION_FALLBACK_BASE_URL`, `VISION_FALLBACK_MODEL`, `VISION_FALLBACK_API_KEY`를 `.env.local`에 추가하고 `--provider fallback`을 **명시적으로** 선택한다. 자동 무단 전송/재시도는 하지 않는다.
- API 키를 채팅에 올렸다면 **해당 키는 노출된 것으로 간주하고 제공자 페이지에서 새로 발급**하는 것이 안전하다. GitHub에 넣지 않는다.

## 재현성·안전장치

- 라이브 실행은 반드시 `--live` 플래그를 넣어야 한다. 미포함 기본 모드는 상태 조회만 한다.
- 독립 SQLite 저장소에 게시물당 트랜잭션으로 확정 저장. 중간 종료 시 기존 성공 결과 유지. 두 프로세스 동시 실행 잠금. 별도 실패·중단 이력 `run_log` 제공.
- 모델 응답을 JSON 스키마로 검사. 실패·한계·숫자형 결측을 구분한다. 무제한 재시도 없음.
- 아직 **n8n Schedule Trigger 연결/90분 자동 실행, 과거 게시물 페이지 확장, 대시보드 웹 프런트엔드, 정식 이미지 분류 정확도 검증**은 완료되지 않았다. 먼저 최소 1~10건 모델 실요청 후 품질을 확인한다.
