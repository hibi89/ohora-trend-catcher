# P6 School n8n bridge — 2026-10-21

This document is **implementation status**, not a replacement for the frozen design in `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`.

## Implemented
- `src/pipeline.py`: CLI contract for existing school n8n JSON (`--config --tick --json --max-pages --max-seconds --dry-run|--live`).
- Sanitary last-line result: `schema_version=1`, `ok`, `state`, `needs_human`, `ready_at`, `pages_committed`, `http_requests`.
- Local **read-only** CSV/source SHA-256 validation for official 294 rows in KR100/US101/JP93 order, three credential file paths A/B/C, data directory.
- Fail closed: `--live` is **always rejected** until approved audited live execution code is connected. This is **not a live collector**.
- No automatic Meta requests, DB writes, scheduler, or legacy data migration. Never upload credentials or `.env` to GitHub.

## Offline validation
`python -m unittest discover -s tests -p 'test_n8n_pipeline_preflight.py' -v`

Initial development environment: 6 cases passed locally. GitHub CI and server runtime for this branch are unverified.

## School deployment dependencies
1. Academy-provided supported mechanism to place code and the three private `.env` files into the n8n runtime.
2. Persisted raw DB and state volume with backup policy verified by operator.
3. The official local `account_list.csv` (294 rows); its two user-local copies have matching SHA-256, but it has not yet been transferred to the school server.
4. Fully implemented live three-API dispatcher / durable raw capture / cursor commit / quota and crash recovery, followed by separately approved Meta live pilot. The existing `three_api_daily_orchestrator.py` remains synthetic only.

## Rollback
Delete the isolated feature branch or remove only its added bridge/tests/docs. Do not touch main, operational DB, frozen design, or the school n8n workflow.
