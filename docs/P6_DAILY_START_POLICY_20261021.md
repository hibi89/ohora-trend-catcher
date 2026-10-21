# P6 daily start policy — 2026-10-21 (KST)

## Owner decision and priority
- **Best case tonight:** finish live integration and collect/verify some of today's latest Meta data **before 2026-10-22 00:00 KST**; this is a goal, not a claim of completed collection.
- **Otherwise:** start the first new daily epoch immediately once the collector is genuinely live-ready, even after midnight. Do not backdate collection times or pretend yesterday's first success happened.
- Temporary proposed start is **00:10 KST**, but it is not an enforced wait before the very first collection.
- After the **first verified successful n8n-triggered real Meta HTTP→sanitized evidence→RAW receipt→cursor commit**, adopt that successful run's KST **HH:MM** as the recurring daily start clock. The integration layer must persist that verified timestamp, rather than choosing a time from a dry run or a planning decision. If the first success is 2026-10-21 23:47:39 KST, the next day's first daily window is eligible at 23:47 KST. A five-minute n8n tick may start it a few minutes after that clock.
- Evaluate pending state at most every **5 minutes** (n8n Schedule Trigger later). Do not start 294 new jobs at every tick: reuse a single idempotent daily KST epoch for that day; resume each unfinished request only when its immutable binding and cursor proof permit.
- A day transition prioritizes the new daily front; preserve unresolved old evidence and hold any manual-review/unknown outcome instead of resending it.

## Frozen assignment (unchanged)
- Official roster **294** accounts, **3** API A/B/C lanes, **limit=4**, **two planned rounds**, each account up to **three pages = 12 media**; theoretical planned requests 294×3 = **882**. These are **not** 12 new unique posts guaranteed and are **not** two full daily scrapes.
- Round 1: A 1..150/page1, B 151..294/page1, C 1..150/page2.
- Round 2: A 151..294/page2, B 1..150/page3, C 151..294/page3.
- A predecessor's verified RAW receipt and cursor are required before each dependent page. If 2 planned quota windows are insufficient, the remaining requests carry into a later available window (potentially a third); the assignment itself does not change.
- Preserve 85% Meta usage stop, 65-minute minimum cooldown, **explicit verified coordinator reset**, serial transport lock, immutable RAW and no uncertain HTTP retries. Passing the wall-clock cooldown does not itself unblock a lane.

## Current implementation
- New **src/collection_schedule.py** is a **pure OFFLINE decision engine**, with provisional 00:10 and bootstrap-immediate semantics. It exposes decide(now, live_runner_verified, first_verified_success_at, daily_day_kst, daily_state, lane_dispatch_allowed, ready_at) and a read-only CLI:
  
  ```bash
  python -m src.collection_schedule --now 2026-10-21T23:55:00+09:00 --live-runner-verified --lane-dispatch-allowed
  ```
  
  The flags above simulate verified readiness **only for preview**; do not interpret BOOTSTRAP_DUE as live authorization.
- **tests/test_collection_schedule.py** covers late-evening same-day bootstrap, midnight rollover, first-success anniversary, once/day, 5-minute ticks, old cursor safety via state (not real DB proof), cooldown/quota hold, invalid input and read-only CLI. These are not Meta or n8n end-to-end tests.
- School n8n **src/pipeline.py** still deliberately rejects --live. The policy module is **not yet connected to a Schedule Trigger or real live collector**. School server credentials/persistent storage, B/C identity and real API permissions still need separate verification.
- No Meta HTTP requests, no operational DB writes, no n8n activation, no main merge or deployment were performed by this change.

## Tonight acceptance sequence and safety
1. Securely verify pilot-success B/C token + IG caller combinations; protect secret files and retain RAW evidence.
2. Verify one approved real page via the existing bounded P6 live collector and durable capture/RAW receipt. Never replay an outcome-unknown previously bound request.
3. Connect the safe schedule gate to a reviewed n8n live adapter, with one persistent shared data volume and serial locking. Before the first verified n8n success, keep the daily anniversary unset.
4. After the first successful automated collection, persist its observed KST timestamp and the corresponding verified RAW receipt, then use the timestamp for daily start. Validate a second tick leaves the same day's jobs unchanged.
5. Only then enable the 5-minute scheduler. Record observed HTTP/RAW success, per-account progress and actual 85%/65-minute behavior; do not call 882 requests complete until evidence proves it.

Rollback: revert the isolated schedule branch only; do not delete or modify existing Windows RAW v2, pilot capture/RAW, the 294-account CSV or credential files.
