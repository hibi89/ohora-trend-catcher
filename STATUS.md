# OHORA Trend Catcher — current implementation status

## Windows test fix — 2026-10-16

- User's Windows Python 3.11 run of `test_full_batch_collector.py` reported 16 tests, 2 errors: WinError 32 when `TemporaryDirectory` tried to unlink `collection.sqlite`.
- Root cause in tests: `with sqlite3.connect(...)` commits/rolls back but does **not** close the SQLite connection. Two occurrences replaced by `with contextlib.closing(sqlite3.connect(...))` in GitHub commit `dd375a18bb9b0cdb8354e98ba88cc0b97dab60de`.
- GitHub file content verified after patch; **Windows retest still pending**, do not claim that 16/16 now passes on user's machine.
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
