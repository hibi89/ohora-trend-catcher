# OHORA P6 / n8n handoff — 2026-10-21 KST (user-reported runtime evidence)

## Source of truth and restrictions
- Frozen design: `docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`. Do not alter fixed three Meta API lanes, limit=4, two daily rounds, RAW-first receipt/cursor durability, or merge main without explicit approval.
- Official 294-account roster is the only source of account identities. Do not commit CSV, tokens, .env, raw Meta responses, private user/customer data, or production DB to GitHub or any unauthorized external service.
- School n8n: `/home/node/work/ohora-team02-p6`, checkout at `9780ce9` on this branch at last user verification. Private path `private/account_list.csv`; `private/ohora.json` present; `private/meta_A.env`, `meta_B.env`, `meta_C.env` were missing at last check.
- School `src/pipeline.py` is **offline preflight only** and explicitly blocks live collection; do not enable schedule or pretend it collects.
- Customer/school guidance as user relayed: final submission GitLab link; external uploading of customer/raw data prohibited outside operator-controlled environment. Google Sheets usage was discussed with coach; do not assume approval to export customer data without confirming scope.

## Verified user-visible school n8n results (not new CI runs here)
- One-time official CSV upload `OHORA_P6_Official_Roster_OneTime_Upload_20261021` completed: `ROSTER_DEPLOY_OK`, 294 accounts, expected roster SHA256 `FB539DA915034DF23FD6FACCD598CC4CAF7FEABA631F9F8351C1AFA2B88C2E8C`.
- P6 offline roster preflight completed `STEP44_COMPLETE`: 294 accounts, 3 lanes, limit 4, no live Meta.
- Private directory prepared `CREDENTIAL_DIRECTORY_READY`. One-time credential upload workflow `OHORA_P6_Meta_Credentials_OneTime` created; user reported disabling saved manual/production execution histories. No evidence that credentials were uploaded. Do not put tokens in node parameters, Git, chat, or execution logs.
- Offline tests reported PASS: STEP33 12+7+14 tests, STEP35 backup/cooldown tests, STEP37 25+12+24 tests. These are offline/mock tests, not proof of live collection.

## Urgent real-world collection evidence: separate Windows RAW v2, NOT P6
- User executed `raw_v2/collector.py` locally on Windows with `--mode front --limit 4 --batch-size 5 --max-batches 30 --stop-usage 85 --live` against `%LOCALAPPDATA%/OHORA_RAW_v2`.
- Run `20261021T080509Z_6548_62f87d0d`: 120 distinct accounts succeeded, 132 **new posts**, cumulative 4,082 posts. Meta HTTP 200 examples; API usage reached 85%; process exit 2 is intentional safety halt.
- Latest slot status `cooldown`; `ready_at` **2026-10-21 18:10:09 KST**. Do not bypass cooldown. Read-only `select_tasks(db,'front',294)` showed 166 eligible accounts, beginning `JP_033`; 8 of 294 not eligible (do not assume why without inspecting per-account DB status).
- This is **RAW v2**, not P6 field-complete collection. RAW v2 lacks an actual requested `view_count` in its usual request path, despite having a schema/CSV field. P6 request spec explicitly requests `view_count`, but real Meta return and persisted field status remain to be validated. Never backdate newly observed likes/views to an earlier observation.
- Preserve existing 4,082 local posts, all run/raw files and SQLite, and avoid overwrites/migrations. Existing v2 and P6 stores are separate; import/reconciliation must be designed and tested before merging. If a resumed v2 run is needed, follow actual DB cooldown, locks and unresolved batch checks, not a hardcoded blind retry.

## Current priorities
1. **Today: latest posts first**. At/after recorded cooldown, use approved existing collector to cover remaining eligible 166 accounts, observing quota and existing queue; actual counts may differ. Verify and report account success, failures, and new posts.
2. Independently prepare P6 real request/response validation for `view_count`, media/product type, carousel children, profile metrics and immutable RAW; require explicit authorization and secure A/B/C credentials. Keep current school n8n bridge offline until an approved live integration is tested.
3. Design lossless RAW v2 -> P6 reconciliation by post ID + actual observed_at, preserving source provenance and field-state `absent/null/not_tested`. Do not overwrite original records or invent historical engagement snapshots.
4. Continue n8n workflow setup, later AI tagging -> Google Sheets (only if permitted) -> read-only dashboard. Do not claim automatic collection works yet.
5. Ask coach/owner for persistent school volume and approved secrets transfer policy before scheduling. School n8n and GitLab are operator-controlled; user’s local machine is distinct.

## Practical operator notes
- One-time roster upload workflow is separate from the actual P6 collector; user opted to leave it un-published/inactive, not delete it.
- Local `%LOCALAPPDATA%/OHORA_RAW_v2/collection.sqlite` is not the school DB; running school n8n commands does not change the Windows local DB.
- No credentials or customer/raw data are included in this handoff.
