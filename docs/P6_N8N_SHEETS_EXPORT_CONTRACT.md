# P6 → n8n → Google Sheets: read-only export contract (v1)

**Scope:** downstream interface only. SQLite RAW/Capture/control remain authoritative.
This does not authorize Meta live calls, alter collection cadence, change P50/294
rosters, or implement Google Sheets credentials/API. n8n Codex owns Sheets upsert.

## Local invocation

```powershell
python raw_v2/three_api_sheets_export.py --db "$env:LOCALAPPDATA\OHORA_P50\p50_raw.sqlite" --after-id 0 --limit 500
```

The path is illustrative: use the **actual** P50 RAW database path from the local
runbook. The command reads SQLite in `mode=ro` and prints one JSON object.
`rows` contains individual immutable post **observations**, not just unique
posts. No Sheets connector, network call, cursor table, or database writes.

## Fields

- `account_key`: P50 roster account key (always present).
- `account_id`: Meta IG account ID if recorded in RAW; otherwise `null`.
  **Do not** confuse this with `account_key`.
- `post_id`, `country`, `published_at`, `collected_at`,
  `media_type`, `media_product_type`, `caption`, `post_url`.
- `like_count`, `comments_count`, `reels_view_count`;
  missing values are `null`, never fabricated as zero.
- `reels_view_count_status`: `returned`, `absent`, `null`,
  `not_tested`, or `error`. Reels view counts are supplied only if Meta
  returned them in the captured observation.
- `collection_batch`: RAW run ID; `request_id`: original request ID.
- `observation_id`: monotonically increasing local RAW observation row ID.
- `observation_key`: `request_id:post_id`, the idempotent Sheets upsert key.
  It uniquely identifies **one observed version** of a post. Use `post_id`
  separately if a latest-per-post view is desired.

## Sync protocol

1. n8n stores its **last successfully acknowledged** `next_after_id`
   externally, e.g. in its own persistent workflow state. P6 never writes it.
2. Call export with `after_id=checkpoint` and a bounded `limit`.
3. Upsert **all** rows by `observation_key`. Keep `collected_at` and
   `observation_id`; repeated runs must not append duplicates.
4. **Only after all Sheets writes are confirmed** persist `next_after_id`.
   On partial failure, replay the page; the upsert key makes it idempotent.
5. Repeat while `has_more=true`; empty rows mean nothing new to export.

The cursor is **local to one immutable RAW DB**; do not share checkpoints
across P50/294, DB resets or restored snapshots. Use separate destination
spreadsheet/tab and checkpoint namespace for each source database.
A committed P6 RAW receipt is required; legacy records lacking such receipts
are not silently presented as verified P6 captures. All RAW observations remain
in SQLite, including historical metric changes. Existing first-seen `posts`
fields (e.g. caption/permalink) are identity projections; historical versions
of those fields must be sourced from versioned observation tables in a future
explicit schema version if downstream requires their change history.

## Ownership and constraints

P6 owns the read-only export format, quality and replay tests. n8n Codex owns
Google authentication, rate limits, batching, Sheets schema and destination
updates. Do not make Sheets the source of truth or a cursor store for P6.
No automatic API retries, no original RAW mutation, no engine redesign.
