# OHORA Trend Catcher — current implementation status

## 2026-10-16 — Collector blocked by missing local Meta env; delivery test Windows lock fixed

- User confirmed official 294-account CSV path exists (`Test-Path $src=True`), but suggested local `kq3_294_collection/.env.local` does **not** exist (`False`). The collector therefore cannot authenticate without locating another secure env file or configuring `META_ACCESS_TOKEN`, `META_IG_USER_ID` and optional graph version locally.
- User ran Friday handoff test: `Ran 9 tests ... FAILED (errors=1)` on Windows with `PermissionError [WinError 32]` for the temporary `experiment.sqlite` file. Cause: `with sqlite3.connect(...)` is a transaction manager but not an explicit connection closer. Fixed **both** fixture and exporter with `contextlib.closing` in commits `5602e192adda3488f5ae16a4e2597c24815d2207` and `2b61b77b81ef03a304a454ba95c0b2557535f813`. Windows user retest pending.
- Urgent next action: find existing .env.local by **file path only** and run one guarded latest-5 batch, then resume remaining batches when safe. Do not paste API token. Delivery tests can be rerun before packaging after collection; no need to block Meta collector on unrelated ZIP test failure.
- No new Meta run was performed by assistant; real API collection must run on user's Windows PC.


## 2026-10-16 — P-1 Friday Hongju delivery prioritized over P1

- Deadline-driven priority: send analysis-ready Instagram collected data to Hongju during Friday classes before building incremental P1.
- Added delivery/build_hongju_bundle.py: **offline only**, read-only SQLite or CSV source, creates timestamped ZIP with both all available posts and most recent 5 per account, media, account coverage, dataset manifest and instructions. No source mutation, credential collection or image model call. Refuses empty datasets. Protects against spreadsheet formula injection in handoff copy.
- Added 9 offline tests in delivery/test_build_hongju_bundle.py (still **pending user Windows execution**), and delivery/README.md handoff runbook.
- Existing 294 latest-5 collection is not known to have succeeded (Meta code4 exhausted usage); preferred existing fallback legacy snapshot is ~94 covered accounts/1,880 posts, and another experimental snapshot ~77 covered accounts/1,540 posts. Exact current counts must come from manifest, not assumptions.
- Next user action after rest: git pull; run `python -m unittest discover -s delivery -p test_build_hongju_bundle.py -v`, select valid data source; generate ZIP; confirm coverage and deliver. If the Meta app actually recovered, optionally test one latest-5 batch before expanding; never let the full 294 goal prevent same-day partial handoff.
- P1 latest-refresh, P2 historical expansion and scheduled n8n remain unchanged in architecture but temporarily postponed until handoff.


## 2026-10-16 — P0 verified on user's Windows PC

- **User Windows offline suite:** `Ran 27 tests in 0.806s`, final `OK` (27/27 tests pass) after `git pull origin main`. In-test log messages including `provider_http_403`/`invalid_schema`/`Provider stopped safely` come from deliberately mocked adverse conditions, **not live model calls**.
- **Previously failed real image post:** User's earlier live exact-post rerun reported `attempted=1, success=1, failed=0` for `17994612878841451`, and local CSV row `status=success`, blank error code, model confidence 0.90.
- **P0 completed for functional/incident regression scope only:** one actual error recovered + offline suite all green; **not** proof of accuracy, high-volume reliability, or zero future failures. Do not pay to retry the already successful post.
- **Next P1:** durable append-only/UPSERT DB and recurring latest-5 collector; preserve original existing 20-post result set and make no live Meta calls without user approval and usage headroom checks.


## 2026-10-16 — Vision failed post recovered; one mocked test corrected

- **User-verified live recovery:** `--post-id 17994612878841451 --retry-failed --limit 1 --live` reported `attempted=1, success=1, failed=0`. The exported `post_analysis.csv` row for that exact post is now `status=success`, `error_code` empty, model confidence 0.90, visual summary about the 'Muffler II' gel collection display. This establishes technical recovery, not ground-truth image accuracy.
- **Windows test status:** prior 27-test suite yielded `FAILED (errors=1)` in `test_format_error_retried_once_and_cached`. Its final mock used an untargeted `--limit 1` run; the worker therefore correctly processed a *different* uncached post but received a `MagicMock` result, which `json.dumps` cannot serialize.
- **Fix:** `vision/test_vision_runner.py` now specifies `--post-id P1` for the final cache assertion and verifies that `analyze()` was never called. Production runner unchanged. Code committed as `170c8f3e6f0f7d11261c29f9addfbe684f375899`.
- **Next:** `git pull origin main` and `python -m unittest discover -s vision -p test_vision_runner.py -v`. Target: 27/27 tests OK. No further paid API retry is required for this recovered post; do not run the live retry again.


## 2026-10-16 — Target exact failed image post before proceeding to P1

- User correctly flagged that **one live `invalid_json` error remains unresolved**. A code patch for malformed JSON is not equivalent to successfully reclassifying the failed post.
- Discovered safety bug in prior CLI advice: `--retry-failed --limit 1` can bill an unrelated, newer *unanalyzed* post instead of the known failed post, because normal selection is newest-first.
- GitHub commits `2d72562296f7efe9578e5c5d5170582558fae86a`, `ceed2256e47874c1fab8aff6dbd36d2eac011ba0`: add `--post-id` to restrict all analysis to the exact post, and two mock regression tests for targeted recovery/unknown IDs. Expected offline suite **27 tests; user Windows execution pending**.
- **Recovery command AFTER offline suite passes:** `python .\\vision\\vision_runner.py --post-id 17994612878841451 --retry-failed --limit 1 --live` (uses local MLAPI key, may make up to two paid calls if malformed output persists). Do NOT claim the error is solved until the post row becomes `status=success` and is visually reviewed.
- Leave previous 20 cached successes untouched. Pause P1 until P0 user live retest verified.


## 2026-10-16 — V1 settings formally locked (design only)

- User confirmed **latest 5**, **backfill 5**, **50 unique posts/account target**, **max 30-minute collection session**, **>=60-minute cooldown after finish**, **>=90-minute gap between starts**, and **GPT-6.1 Sol API insights weekly**.
- Delivery roadmap: [docs/V1_DELIVERY_ROADMAP.md](docs/V1_DELIVERY_ROADMAP.md). Operating windows and Sol vendor API model/reasoning parameters are **not specified/verified yet**; no n8n scheduler or weekly AI job is active.
- Development: user + ChatGPT directly; Gemini 3.8 Flash API for image/caption tags; GPT-6.1 Sol separate paid API in n8n for cached weekly narrative interpretations, NOT the ChatGPT model or Codex. No Codex/Luna EH.
- Current phase: offline 25-test vision retest + single malformed-output retry validation, then durable post-ID-based upsert/latest refresh, backfill and analysis queue, DB-only dashboard, weekly Sol and n8n schedule. Existing initial 5-post collector alone **cannot** repeatedly refresh without replacing or skipping successful account data.


## 2026-10-16 — vision JSON safety improvements (Windows + live retest pending)

- Observed user live paid MLAPI vision results: 21 post attempts, 20 success and 1 `invalid_json` (post ID stored locally); image-and-caption model service works, but output reliability needs improvement. This is a small sample, not a measured production failure rate.
- Code commit `d4de3e4f6d7abbb53c94b1dde20ec71771365641`: recover a SINGLE complete JSON object when prose surrounds it; never fabricate truncated JSON. Detect model finish_reason length; allow exactly ONE extra model call on limited schema/format errors with larger max_tokens; no auto retry on 401/403/429 or transport faults; preserve cached successes and failed rows.
- Test commit `d253ee456b9079cf37d85804f5072f65b306afa6`: 4 new offline tests for wrapped/duplicate/truncated JSON and bounded retry, and updated old two-invalid-output expectations. **25 total tests must be rerun on user's Windows PC; not yet verified after the new patch.**
- Next user actions: `git pull origin main`, `python -m unittest discover -s vision -p test_vision_runner.py -v`, then one explicit real retry: `python .\vision\vision_runner.py --retry-failed --limit 1 --live`. Only if those pass consider larger analysis in batches.
- Demo safeguard: PRECOMPUTE and store all classification before presentation; dashboard should only read cached success rows, disclose coverage/failures and operate without model/API availability. **Actual dashboard interface remains unimplemented**.


## 2026-10-16 — Image+caption vision pipeline ready for local provider validation

**Code added:**
- `vision/vision_runner.py`: read-only import from full_294/latest_5 SQLite or original posts/media CSV; select representative image/carousel/video thumbnail plus caption; schema-validated `is_design_post` and color/technique/texture/motif/season/use labels; one SQLite transaction per post; CSV exports and caching; manual provider switch; safe stop on auth/rate/format errors; explicit paid `--live` only.
- `vision/test_vision_runner.py`: **21/21 offline tests passed** in the development environment. No external provider requests.
- `vision/setup_env.ps1` prompts for endpoint/model/key and writes *only local* gitignored `vision/.env.local`. The actual user-supplied API key is **not** in GitHub, Library or outputs.
- `vision/README.md`: PowerShell morning runbook; run one paid API call only after user reviews provider setup.
- Real legacy source dataset **1,880 posts / 5,307 media records** was parsed offline; mocked model integration processed three posts, exported analysis rows, and SHA-256 of original CSVs was unchanged.
- External `mlapi.run` endpoint authorization, accepted model ID, remote image URL fetch behavior, *actual AI inference quality*, cost, and Windows user run are **not yet validated**. Do not call this completely done.
- Already observed: user's Windows original 16/16 collector tests passed after SQLite closing fix. First live latest-5 Meta test hit code 4 due to lingering 101% usage; no successful new posts from that test.
- Next: user pulls GitHub code, runs vision tests locally, configures fresh API key securely, offline preview from `experiments/results/full_294`, then one small `--live --limit 1` check before any bulk analyses. Collector runs only after Meta usage sufficiently recovers.
- 90-minute n8n scheduling, recurring latest upserts and cursor backfill, dashboard UI remain *separate future work*.


## Windows test fix — 2026-10-16

- User's Windows Python 3.11 run of `test_full_batch_collector.py` reported 16 tests, 2 errors: WinError 32 when `TemporaryDirectory` tried to unlink `collection.sqlite`.
- Root cause in tests: `with sqlite3.connect(...)` commits/rolls back but does **not** close the SQLite connection. Two occurrences replaced by `with contextlib.closing(sqlite3.connect(...))` in GitHub commit `dd375a18bb9b0cdb8354e98ba88cc0b97dab60de`.
- GitHub file content verified after patch; **user's subsequent Windows retest passed 16/16** (observed 2026-10-16 chat log).
- User's `--posts 5` dry run succeeded: 294 accounts, 10 per batch, 30 batches; no API requests or file writes.
- Next: `git pull origin main`; `python -m unittest discover -s experiments -p test_full_batch_collector.py -v`; only proceed to live 5-post acquisition if tests pass and Meta app usage has recovered.


## 2026-10-16 — latest-five acquisition preparation

- **Decision:** Build latest 5 initial acquisition now; postpone hourly-window scheduling, recurring post-ID upsert, older-page backfill, and vision until staged verification. Plan: `docs/INCREMENTAL_V1_ARCHITECTURE.md`.
- **Implemented:** `experiments/full_batch_collector.py` now supports `--posts 1..20`; 20 remains the default. `--posts 5` MUST use `--output-dir experiments/results/latest_5` or another empty isolated output path. Original 20-post database and old collector remain untouched. Resume skips completed accounts; no periodic refresh implemented yet.
- **Verified offline:** 16/16 tests, including 5-post mocked live batch and changed-manifest rejection; actual official 294-row source accepted with dry-run 5 posts/10 accounts x 30 batches, no writes/no calls. This is **not** a live five-post API validation.
- **Observed live prior:** user reported real 20-post full-list run hit HTTP 403 / Meta code 4 at batch 9 with 77 successful accounts (13 failed; 204 unattempted). Do not assume a fixed 60-minute cooldown resets quota; confirm recovery and run a small staged batch before large collection.
- **Next:** after app usage safely recovers, user runs `git pull` and offline test then the 5-post dry run and optionally one explicit live batch for confirmation. Reusable latest-refresh/backfill must be implemented later on a non-destructive upsert schema, followed by image analysis endpoint testing.



Updated: 2026-10-15 KST

## Business scope
- KQ3 is *Key Question 3*, not calendar quarter Q3.
- Official account list: 294 targets, KR 100 / US 101 / JP 93.
- Goal: preserve latest 20 posts/account (up to 5,880) and child media, followed by separate vision classification and DB-backed dashboard queries.

## Confirmed collection history
- Existing original sequential collector succeeded for 94 accounts (1,880 posts, 5,307 media rows) in the previously inspected snapshot, with Meta usage/limit issues. Original raw datasets remain on the user's PC; they are **not** committed.
- User-operated Meta Graph Batch probe: one target in 1.265s; two targets in 2.063s; five targets x10 posts in 2.703s; same five x20 in 5.797s; ten targets x10 in 2.828s. All tested subrequests returned HTTP 200. Usage header percentage readings are **snapshots** and not per-call costs.
- No 294-account live batch collection has been executed or verified yet.

## Code
- `kq3_294_collection/collector.py`: original legacy collector, unchanged.
- `experiments/meta_batch_probe.py`: original stat-only single-batch probe, unchanged.
- `experiments/full_batch_collector.py`: isolated full 294-target batch collector (10/account batch, 20 latest posts/account), SQLite transactions + CSV exports, no automatic retries, recorded usage/errors, resume after interruption and stop upon Meta rate/auth errors. Dry run is default; **only --live sends API requests**.
- `experiments/test_full_batch_collector.py`: 15 network-free unit/integration tests passed during development.
- `experiments/FULL_294_README.md`: exact PowerShell instructions, operational limits, file map, and exit codes.
- A separate fully mocked 294-target run verified 30 batches ending with four accounts, 5,880 unique posts and 8,820 media rows (fabricated fixtures, not real Meta records).

## Decisions / non-goals
- Full experiment intentionally starts at the beginning of the official list, including 94 previously collected accounts, but saves under `experiments/results/full_294/`; **never overwrite legacy CSV data**.
- Default run has no speculative 80% cutoff because the user wants to observe the actual Meta limit. First recognized limit/auth error stops future batches after preserving the successful subresponses. Optional `--stop-usage 80` is available.
- URLs and metadata are stored; image binaries, AI vision tags, n8n automation of this new collector, and frontend/DB dashboard integration are outside this experiment.
- Any runtime incident or errors should be diagnosed from `summary.json`, `batch_log.csv`, and `collection_status.csv`; avoid rerunning same method blindly. Never share real access tokens.

## Next step
User: `git pull origin main`, run the full collector dry run then its explicit `--live` command locally, and share sanitized logs / summary. Assistant: review quota progression and exact stop point, decide whether to retain 10-subrequest batches or optimize further based on real 294-run evidence.
