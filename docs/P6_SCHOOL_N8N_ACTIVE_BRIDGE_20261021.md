# P6 school n8n adapter on active 3-API pilot baseline — 2026-10-21

Source baseline: `codex/p6-readiness-hardening-20261019` at `c105c70f` (read via GitHub tree at adaptation time). Separate branch only. Do not merge or activate automatically.

## Implemented
- `src/pipeline.py` matches school workflow's `--config/--tick/--json/--max-pages/--max-seconds/--dry-run|--live` contract.
- Validates the official immutable 294-row CSV and SHA256, KR100/US101/JP93 order, duplicate identities, three credential **file paths only** without reading tokens, data-directory presence.
- In dry-run, delegates to current P6 `raw_v2/three_api_manual_pilot.py::run_manual_pilot(live=False)` for official CSV and 3-lane/limit=4 invariants; no live Graph sender or DB construction.
- `--live` is **unconditionally rejected** (`CONFIG_REQUIRED`). Although this branch contains the standalone manual pilot live implementation, it is **not** a reviewed n8n unattended collector, history pipeline or full production integration.
- Sanitized n8n result: `schema_version=1`, `ok`, `state`, `needs_human`, `ready_at`, `pages_committed`, `http_requests`.

## Verification state
- School server previously passed 6 bridge tests on an **older branch** `feature/p6-school-n8n-bridge-20261021` (`65ab912`) — that success does **not** validate this new bridge version.
- Current branch has an additional integration test invoking P6 pilot's offline preflight. **School server tests and CI have not yet been run** on this current branch.
- The active P6 source's own handoff records user-reported successful Meta A-lane pilot requests and 12 post observations; B/C live transport validation incomplete. These are **not school-server live results**.

## Next steps
1. Test this branch separately in the existing team02 school code working directory, with no real CSV or credentials required for the synthetic unittest suite.
2. Obtain academy-approved file/.env delivery mechanism, confirm `/home/node/work` persistence and backup policy.
3. Transfer verified official CSV and three independently approved credential files through that mechanism; never echo secrets in n8n logs or GitHub.
4. Approve and implement a separately gated serial live adapter with durable original response evidence → RAW receipt → cursor advancement, verified 85%/65min global quotas, no HTTP replay on unknown outcomes.
5. Perform one explicit manual acceptance run on known persistent storage before any scheduled n8n activation. Full daily/catchup/history coverage remains separate.

Rollback: detach/checkout prior `65ab912` school bridge or remove this isolated feature branch. Never touch operating RAW, original CSV, main or frozen design.
