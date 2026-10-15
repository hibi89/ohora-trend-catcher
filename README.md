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
