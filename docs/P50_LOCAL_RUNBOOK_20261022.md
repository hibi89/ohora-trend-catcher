# P50 3-API pilot — local runbook (2026-10-22)

**Scope:** 50 selected accounts (US 18, KR 16, JP 16), isolated from the original 294-account engine. Do not merge the draft PR into main.

## 1. Offline checks (no Meta calls)

From repository root, PowerShell:

```powershell
python -m compileall -q raw_v2
python -m unittest discover -s tests -p "test_three_api_p50_*.py" -v
python -m unittest discover -s tests -p "test_three_api_sheets_export.py" -v
python raw_v2/three_api_p50_preflight.py --source "$env:USERPROFILE\Downloads\ohora_p50_roster_20261022.csv"
python raw_v2/three_api_p50_manual_pilot.py --source "$env:USERPROFILE\Downloads\ohora_p50_roster_20261022.csv" --workspace "$env:LOCALAPPDATA\OHORA_P50\p50"
```

The roster CSV must contain `account_key,username,country` and exactly 50 rows. The 50-account roster is a **separate file**; do not replace `account_list.csv`.

The offline pilot must report `live_meta_called: false` and must not create workspace files.

## 2. First authorized Meta page (only after offline tests pass)

Credentials remain outside Git in the three existing local `.env.api_a`, `.env.api_b`, `.env.api_c` files. The explicit live flag is an operational approval, not something the test suite supplies.

```powershell
python raw_v2/three_api_p50_manual_pilot.py --source "$env:USERPROFILE\Downloads\ohora_p50_roster_20261022.csv" --workspace "$env:LOCALAPPDATA\OHORA_P50\p50" --credentials-dir "$env:USERPROFILE\OneDrive\Desktop" --max-pages 1 --live --confirm-owner-approval
```

**Do not run this live command until the operator has independently approved the Meta call.** Confirm actual credential directory before executing. If the result is `no_ready_work` or a lane is held, do not override the quota gate. Never copy credentials or error response bodies into Git.

## 3. Verify durable evidence

After an authorized live attempt, verify the **actual** RAW, coordinator and Capture SQLite files, not just a success message:

- `p50/p50_raw.sqlite`
- `p50/YYYY-MM-DD/coordinator.sqlite` (KST day)
- `p50/YYYY-MM-DD/capture.sqlite`

Verify at least one immutable Capture receipt, a matching RAW receipt, and a completed coordinator job before reporting first-page success. Distinguish `account_failed`, `no_ready_work`, and `page_committed`. Do not treat a missing Reels view count as zero.

## 4. Known unverified items

- The GitHub Actions workflow has not yet returned a passing run.
- Live Meta credentials, permissions, rate limits, actual RAW write, and Reels view-count coverage have not been validated in this branch.
- Historical backfill completion is **not** proven by a first-page success.

## 5. Read-only status and Sheets export safety (2026-10-22)

The initialized P50 status returns `live_meta_called: null` because persisted row counts cannot prove whether a Meta HTTP call occurred. An uninitialized workspace still reports `false`. The status reader rejects a symlinked `p50` root; the Sheets exporter rejects a symlinked RAW database. The exporter excludes observations without matching RAW receipts and rejects ambiguous duplicate receipt matches instead of silently emitting duplicate rows. No SQLite or Google Sheets write is performed by these readers.

The two focused test commands in section 1 are required before an operational approval request. GitHub commits alone are **not** test evidence; if DNS/network restrictions prevent repository checkout, record offline test status as **UNVERIFIED**, not PASS. Never treat the first page or an export fixture as evidence of 50-account live collection.

## 6. Fail-closed malformed RAW receipt checks

The read-only Sheets exporter rejects malformed RAW `posts` containers, non-object post entries, and invalid `post_field_status` mappings. The focused Sheets export test suite includes parameterized fixtures for these cases, as well as duplicate receipt pagination and Reels view status/value mismatches. These are defensive read-path changes only; they do not repair, discard, or rewrite RAW evidence. Execute the focused suite locally before claiming PASS; GitHub commit existence is not a test result.

## 7. Incremental export cursor integrity

When observation ID 3 has no matching RAW receipt but ID 4 does, the Sheets exporter must not advance `next_after_id` past ID 3. It returns no rows after checkpoint 2 until the missing receipt is reconciled; once the receipt arrives, the same checkpoint exports IDs 3 and 4 in order. This is intentionally fail-closed and can pause downstream Sheets sync on legacy gaps. Never manually skip IDs or alter the authoritative RAW SQLite to make the dashboard advance. See `docs/P6_N8N_SHEETS_EXPORT_CONTRACT.md` for n8n consumer requirements. **Regression tests committed; execution still unverified.**

## 8. Strict RAW metric and status validation (2026-10-22)

Empty list/string RAW `post_field_status` containers (including nested per-post values) are malformed and now rejected instead of being treated as absent. A Reels `view_count` marked `returned` must be a nonnegative JSON integer: `0` remains valid, but `true`, negative numbers, numeric strings and fractional values are rejected. Focused regression fixtures cover these cases. The implementation and tests are committed, **not yet confirmed PASS by the full Python suite**. The GitHub checkout attempt from this runtime returned `Could not resolve host: github.com`; use a reachable local checkout for actual unit execution.
