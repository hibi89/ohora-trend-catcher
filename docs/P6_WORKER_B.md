# p6 worker B — isolated RAW v2 writer (provisional)

Base main: bcdb84b640738fdfceaf833dd831a26d401a2427.
Owned branch: feature/p6-b-raw-writer-20261019.

## Schema mapping
| Capture field | Existing RAW v2 destination | Rule |
|---|---|---|
| capture_id | three_api_raw_receipts.capture_id; request_log.request_id | Immutable replay key; additive receipt table |
| account_key | accounts FK, posts/account_observations | Existing account required; never edit account cursor |
| observed_at | post_observations, account_observations, request_log | Original capture clock; never synthesize replay time |
| posts[].id | posts.post_id | Reject cross-account reuse |
| posts[].children | post_media | Only child rows for carousel, no container media row |
| post like/comments | post_observations | Preserve missing/null/zero; flags indicate key presence |
| field presence | field_observations | returned/null/absent/not_tested |
| profile | account_observations | Only if captured |
| capture receipt | three_api_raw_receipts | Same RAW transaction as data |
| run | runs | Dedicated synthetic capture run; no legacy run mutation |
| front/history/catchup cursors | accounts, evidence, daily_account_state | Legacy-owned, never modified |
| slots.ready_at | slots | Legacy-owned, never modified |
| carousel completeness | carousel_status | Unknown by default; no inferred complete status |

## Boundaries and recovery
- **Provisional interface**: C's three_api_runtime_contract.py not present on base main. Envelope: capture_id, account_key, observed_at, posts; optional profile and phase. C must reconcile the exact contract before integration.
- Writer accepts an existing SQLite connection and does not open a DB path. Caller must establish trusted **isolated synthetic DB provenance**; filename alone is not sufficient protection. Do not pass any operating DB connection.
- Source capture must be durably retained before calling writer. RAW commit and receipt are atomic in one SQLite database only. Source acknowledgement is separate and not atomic.
- On failed RAW commit: keep saved capture, investigate, replay through replay_saved with source_verified=True. After RAW commit but before source ack: replay identical capture, expect already_committed, then ack. Any receipt/payload/account/clock collision must stop new collection and require manual reconciliation.
- Existing collector recovery, backup, audit, and verify are not modified. Legacy audit compatibility is **not yet verified**. Additive receipt table does not migrate existing data.
- Rollback: stop use, restore untouched synthetic snapshot; remove new writer/recovery modules and additive receipt table only on disposable test copies. Never remove receipt table from a live store.
- Not validated: actual Meta calls, production DB, C's contract, live source verification, full offline test suite, audit/verify compatibility. No live use authorized.
