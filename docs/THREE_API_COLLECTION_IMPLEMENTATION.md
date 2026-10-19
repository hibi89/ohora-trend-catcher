# Three-API collection — implementation gate (2026-10-19)

## Status
**Offline planner, coordinator, cursor validator and transactional evidence state are implemented** (`raw_v2/three_api_plan.py`, `three_api_coordinator.py`, `three_api_adapter.py`, `three_api_checkpoints.py`, `three_api_paging.py`, `three_api_evidence.py`). **Live three-token execution NOT implemented or authorized.** The existing foreground `daily_scheduler.py` remains unchanged and is the only currently validated live path. Do not launch three copies of it against the same SQLite store.


## 2026-10-19 offline implementation checkpoint (supersedes planner-only status)
- Coordinator distributes 294 × 3 logical jobs; per-lane persisted cooldown and leases. Evidence-aware leases require a nonterminal, committed predecessor page. Bare `Coordinator.complete()` is blocked when a `PageEvidenceStore` is active.
- `PageEvidenceStore.commit_page()` validates actual Meta cursor-edge shape and lease expiry; stores the predecessor cursor, next cursor, nonterminal flag and opaque receipt in one transaction with job completion. It rejects stale leases, missing/terminal predecessors, duplicate receipts and cursor cycles. **Receipt is supplied by the caller and is NOT independently proven to reflect a completed external data write.**
- `tests/test_three_api_evidence.py` includes simulated rollback, expired lease, direct-completion bypass, terminal boundary and page-2 chain cases; GitHub CI workflow includes these tests. **CI result for the latest commit is not yet verified.** All tests are synthetic and make no Meta requests.
- **Remaining blockers:** fully integrated staging writer and receipt reconciliation, account-level post/head anchor continuity, cross-app quota confirmation, daily-first/catchup/history scheduler, live B/C credential validation, supervised isolated-DB pilot, and user approval before any production DB switch.
- A's historical `.env.local` path was found on the user's PC; user prepared B/C credential files on Desktop but neither was validated in this stage. Never upload or expose tokens. Original single-API operation is unchanged.

## Requested schedule
- Three separately credentialed Meta identities A/B/C, subject to verification that quota limits are actually independent.
- Round 1: A accounts 1–150 page 1; B 151–294 page 1; C 1–150 page 2.
- Round 2: A 151–294 page 2; B 1–150 page 3; C 151–294 page 3.
- Round 3 onward: prioritize pending catchup, then history toward 365-day boundary; never overwrite history cursor with front cursor.
- Four media records per page; stop each lane at observed X-App-Usage >=85% or when usage header unavailable; each lane has its own persisted cooldown.
- A daily first pass must check *new* posts for all 294 accounts. Page numbers 1–3 describe initial snapshot positions only; a moving feed cannot safely be indexed by offset. Track observed post IDs, prior head anchor and real Meta cursors; if anchor not found, enqueue catchup until connected.

## Safety requirements before any live activation
1. Validate all three Meta credentials without storing tokens in GitHub, logs or DB; confirm their X-App-Usage counters are independent. Token count alone does not imply quota multiplication.
2. One **single coordinator** must hold the existing cross-process DB lock while leasing accounts and persisting page progress; concurrent `collector.run` calls cannot safely share the current `slots` table (single global slot) or current `accounts.backfill_cursor`.
3. Add per-lane cooldown and run/slot identifiers; preserve legacy global cooldown and all existing checkpoints during migration. A restart must never reset `ready_at`.
4. Persist account + phase + page lease and expected cursor before API call; commit sanitized response and new cursor atomically; recover unknown responses via idempotent post IDs and request IDs.
5. Reconcile page 2/3 requests with anchor IDs so newly posted content cannot shift offsets. Preserve all queued catchup gaps and never mark historical completion without rooted cursor evidence.
6. On any failure, missing usage header, identity mismatch or WARN audit without explicit approval, halt safely. Do not auto-bypass safeguards.
7. Offline tests: exact 294 × 3 page coverage, overlapping workers, interrupted writes, usage 84/85/missing, day rollover, cursor shift, new >12 posts/day, 8 blocked accounts, partial pages, quota sharing, existing DB migration. Then single-lane live pilot and independent lane pilot before full rollout.

## Plan-only command
```powershell
python .\raw_v2\three_api_plan.py --source $src
```
This only reads the official CSV and prints a JSON schedule. It makes no API requests or DB writes.

## Existing collector
Keep the existing live scheduler running until a separate tested implementation has been approved. Do not alter the 4-post limit or 85% stop rule in the current live process.
