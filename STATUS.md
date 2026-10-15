# OHORA Trend Catcher — current implementation status

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
