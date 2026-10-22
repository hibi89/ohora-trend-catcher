# P50 3-API pilot — local runbook (2026-10-22)

**Scope:** 50 selected accounts (US 18, KR 16, JP 16), isolated from the original 294-account engine. Do not merge the draft PR into main.

## 1. Offline checks (no Meta calls)

From repository root, PowerShell:

```powershell
python -m compileall -q raw_v2
python -m unittest discover -s tests -p "test_three_api_p50_*.py" -v
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
