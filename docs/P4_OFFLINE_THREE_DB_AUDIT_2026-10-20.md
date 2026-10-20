# P4 — Synthetic three-DB integrity audit (offline only)

Status: **partial P4 implementation, not P4 acceptance**. Source branch:
`codex/p6-readiness-hardening-20261019`. Frozen P6 constraints remain unchanged:
294 official accounts, 3 API lanes, `limit=4`, exactly two daily front rounds,
source-first retention, review isolation, no production DB writes, no live Meta
requests, no unattended history dispatch and no main merge.

## Scope and invocation

`OfflineDailyOrchestrator.audit_storage()` provides an explicit **read-only**
proof audit of the existing **synthetic** coordinator, KST daily ledger and RAW
mirror. It does **not** repair or promote anything. From an already initialized,
offline-only `OfflineDailyOrchestrator`, either call:

```python
audit = runner.audit_storage()
report = runner.operator_report(now=aware_utc_datetime,
                                include_storage_audit=True)
audit = report["storage_integrity"]
```

Ordinary `operator_report(now=...)` and `step()` do **not** run the expensive
full audit automatically; the new argument must be exactly a boolean.

## Verification performed

For each **existing committed front-page source proof** (not for unobserved
accounts), compare:

1. Immutable coordinator page proof, ordered staged post bodies and KST daily
   page/cursor/ID evidence.
2. Immutable offline fixture capture clock against the RAW page receipt clock.
3. RAW receipt account, exact canonical normalized payload and SHA-256 digest.
4. RAW receipt promotion state, per-post engagement observations, parent
   account ownership, and parent/carousel-child identity and order.

Also flag front-shaped RAW receipts for the current day which lack a matching
coordinator proof. The diagnostics do not emit media IDs, cursors or page text.

Counters: `source_pages`, `verified_pages`, `missing_clock`,
`unverified_source`, `missing_receipt`, `staged_receipts`,
`receipt_mismatch`, `observation_mismatch`,
`orphan_front_receipts`, `pages_requiring_attention`.

`storage_verified` means only that **at least one recorded source page** was
validated and all such recorded pages matched their stored synthetic evidence,
with no current-day orphan front receipt. It **does not** mean 294 accounts,
882 front-page jobs, full RAW coverage, historical backfill or Meta collection
completed. An empty source explicitly yields `storage_verified=False`.
`history_dispatch_enabled` and `unattended_execution_enabled` remain false.

## Recovery boundary

Source-first capture, daily ledger projection and mirror promotion are three
independent transactions. Immediately after the first source commit, the
audit must report `unverified_source` rather than silently marking success.
A subsequent explicit `reconcile()` can replay already stored staged
evidence into the ledger/mirror, and the repeated audit must then verify those
pages **without fetching new media**. A missing observation or corrupted
committed receipt is reported and is never automatically rewritten by audit.

## Safety, limitations and remaining P4 work

- Offline **synthetic mirror only**; it is not the real RAW v2 production writer
  and this code is not authorization to call Meta.
- No cross-file atomic SQLite commit is claimed. Audit observes three files
  sequentially; to obtain a consistent inspection use quiescent offline stores,
  not concurrent writes.
- Existing fixture capture clocks are checked for presence/time equality; the
  fixture's original full Meta response digest cannot be independently
  regenerated from a normalized page-only staging record.
- The audit is **O(stored pages and contained posts/media)** and should be run
  on explicit demand, not on each dispatch or UI refresh.
- Existing **catch-up payloads and P3 epoch history receipts** are not covered
  by this front-page audit. Implement and prove actual P3/P4 durable history
  integration, full 294/882 synthetic E2E, cross-DB crash-boundary matrix,
  P5 Windows / restore drills and approved live tests separately.

Regression evidence is in `tests/test_three_api_daily_orchestrator.py`
including clean, empty, tampered payload, deleted observation, staged receipt,
missing clock/orphan receipt, media deletion, restart and source-to-ledger-to-RAW
interruption/reconcile scenarios. GitHub Actions terminal success (including
Windows) must be inspected before marking acceptance.
