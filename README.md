# OHORA — Trend Catcher / KQ3 (294 Instagram accounts)

Private project source mirror, prepared from the original locally collected ZIP on **2026-10-15**. This repository is **source code and requirements only**. Do not commit actual collection CSVs, source account list, access tokens, screenshots, or client media without deliberate review.

## Structure

- `REQUIREMENTS.md`: customer KQ1–KQ3 meaning, approved scope, data-quality caveats.
- `AGENTS.md`: safe development and verification rules.
- `kq3_294_collection/collector.py`: **original imported collector, unmodified**.
- `kq3_294_collection/test_step4_synthetic.py`: existing 9 synthetic offline tests.
- `kq3_294_collection/n8n_*.json`: local-only workflow wrappers, containing a machine-specific absolute path; adjust deliberately before import on another PC.
- `analysis/`: original diagnosis text, dated snapshot; it does not reflect future collections.
- `experiments/full_batch_collector.py`: isolated 294-account Meta Batch collection; `--posts 5` supported; run only after app usage recovers.
- `vision/vision_runner.py`: **new image+caption AI analysis** from collected SQLite or CSV, idempotent per post, separately exported. See [vision/README.md](vision/README.md) for morning setup, tests and the explicit `--live --limit 1` provider check.
- `docs/INCREMENTAL_V1_ARCHITECTURE.md`: latest-5/backfill-5/90-minute schedule planned but not yet implemented.
- **[docs/V1_DELIVERY_ROADMAP.md](docs/V1_DELIVERY_ROADMAP.md)**: **confirmed V1 settings**, implementation stages P0–P6, acceptance checks, Gemini vision and separate weekly GPT-6.1 Sol API roles.

## 2026-10-18: 장기 RAW 수집·4탭 대시보드 목표 (기록만, 실행 금지)

- [**장기 데이터 자산 구축 목표·3개 수집 작업·4탭 구상·미검증 사항**](docs/LONG_TERM_RAW_COLLECTION_VISION_2026-10-18.md)
- 계정당 최근 50개(294계정 최대 14,700개)를 초기 목표로, 최신 동기화·과거 1~2년 확장·참여 지표 재관측을 분리하는 **논의 중인 설계**. 기존 수집기·스케줄러에는 아직 구현되지 않았음. 현재 API 호출·개발 실행·데이터 병합·배포는 승인되지 않음.

## Local setup / using `git pull`

The collection code expects its original folder structure with a **local-only official** `account_list.csv` in this repository root. Do not replace or change that CSV after the original collector state is initialized; its SHA256 is stored in `manifest.json`. Copy the source file from the user's existing archive locally — it is ignored by Git.

The existing collector's generated local CSVs, status and `.env.local` should also remain on the same PC; they are ignored by Git. **Do not overwrite a live collector or its state files with a fresh clone**. Move to a newly cloned repository only as a separately verified migration, with process stopped and backups taken.

For an initial *code-only* review/test:

```powershell
python .\kq3_294_collection\test_step4_synthetic.py
```

No actual Meta calls are made by those tests. Production collection is a separate explicit command; do not launch it while another collection session runs.

For continued edits: GitHub hosts code and documents, local PC hosts real data and API credentials. Prefer branch + review over untested changes to `main`.

## 금요일 홍주님 인계 우선 작업

- [delivery/README.md](delivery/README.md): 2026-10-16 데이터 전달 우선 워크플로, 오늘 인계 ZIP 실행법, Meta rate-limit 대안.
- [delivery/build_hongju_bundle.py](delivery/build_hongju_bundle.py): API 호출 없는 읽기 전용 SQLite/CSV → 최신5·전체 게시물·계정별 커버리지 포함 ZIP 생성.
- [delivery/test_build_hongju_bundle.py](delivery/test_build_hongju_bundle.py): 9개 오프라인 회귀 테스트. 사용자 Windows 검증 전.

- [experiments/README_ONE_POST_PENDING_PROBE.md](experiments/README_ONE_POST_PENDING_PROBE.md): read-only selection of pending accounts for separate one-post quota pilot (requires offline tests and explicit small live batch).

## Meta 앱별 150/144계정 수동 수집 (10개씩)

- [실행 가이드: 2개 이상의 승인된 Meta 앱, 담당 범위, PowerShell, 재개/병합](experiments/README_TWO_APP_LATEST10.md)
- [수집기](experiments/two_app_runner.py) · [오프라인 검증 테스트](experiments/test_two_app_runner.py)
- 수집 행 번호 1–150/151–294, 앱별 독립 저장소. 스케줄러는 아직 구현하지 않음. API 호출은 명시적인 `--live`로만 실행.

## 2026-10-16: 90-minute cursor backfill (disabled until verified)

- **Latest design**: distinct properly authorized Meta apps A (official account ordinals 1–150) and B (151–294), five media posts per account each 90-minute cycle, per-account cursor advancement to **50 unique historical posts**. Re-querying latest five is NOT 50 unique.
- **New code**: [cursor collector](experiments/rolling_backfill.py), [90-minute one-shot A/B dispatcher](experiments/run_90m_cycle.py), [disabled configuration example](experiments/backfill_schedule.example.json), plus 10 collector + 7 scheduler **offline tests (user Windows verification pending)**.
- **Setup, token requirements, one-account two-cycle live pagination pilot**: [90-minute backfill guide](experiments/README_CURSOR_BACKFILL_90M.md). Scheduler remains disabled until the valid Hongju token/IG ID and distinct Meta App IDs are confirmed. Existing real data remains local/untouched.
