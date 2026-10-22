> [!IMPORTANT]
> **작업 시작 필독:** [회초리.md](회초리.md) — 사용자 최신 실행 규율. [설계자에게 전하는 말.md](설계자에게%20전하는%20말.md) — 지침 변경 공시 및 설계자 인수인계. 모든 P6 작업자·Codex·새 채팅방은 작업 전 두 문서를 확인하고, 중요한 지침 변경 시 공시 문서를 갱신한다. 동결 설계와 별도 운영 승인 게이트는 유지한다.

## ABSOLUTE EXECUTION PRIORITY — owner instruction (2026-10-22)

**When the owner says `ㄱ`, `진행`, or asks to move quickly: perform the next substantial, independently executable work immediately.** Do not stop simply because another task is queued, pending CI, waiting for external access, rate-limited, or blocked by the owner's unavailable PC.

1. **Park, then advance:** Record each blocked task's exact blocker, status, and next recheck trigger in a compact pending queue. Move immediately to the highest-impact safe, unblocked step. Revisit the parked task when evidence or prerequisites change; do not poll it repeatedly as the only action.
2. **One command = useful work:** Prefer a meaningful implementation, root-cause diagnosis, consolidated tests, or deliverable over status-only replies, repeated CI queries, tiny cosmetic commits, and documentation churn. Batch related edits; avoid triggering the entire CI matrix for every trivial change.
3. **No artificial waiting:** A slow/queued CI run, inaccessible Windows workspace, absent Meta credentials, unavailable external service, or an unrelated error must not stall independent offline work. Skip or defer the blocked portion and proceed with another available chapter.
4. **Report evidence precisely:** Distinguish committed vs tested, pending vs PASS, synthetic vs real collection. Report concrete changes, SHA, blockers, and next independent task briefly. Do not claim an unexecuted step is complete.
5. **Preserve hard safety boundaries:** This priority rule never authorizes live Meta API calls, quota bypass/reset, operational DB/RAW mutation, data restoration, scheduler activation, main merge, or frozen-design changes without the separately required owner approval. Never circumvent a fail-closed guard just to show progress.
6. **When truly no safe independent work remains:** State the specific external prerequisite once and stop creating filler work. Do not repeatedly ask for the same PC output after it has been identified as unavailable.

This is a standing execution instruction for all P6 workers, Codex sessions, and future chat handoffs. It supersedes slower workflow habits, **not** frozen design, safety requirements, or explicit user restrictions.

> [!CAUTION]
> **FROZEN P6 DESIGN — USER EXPLICIT APPROVAL REQUIRED TO CHANGE (2026-10-20).**
> Read [DESIGN_FREEZE_NOTICE.md](DESIGN_FREEZE_NOTICE.md) and the complete [P6 final collection design, including section 15](docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md) **before making any design, API-lane, scheduler, storage, or workflow changes**. See [global policy Issue #7](https://github.com/hibi89/ohora-trend-catcher/issues/7). No worker/agent may alter, overwrite, delete, weaken, replace, or silently deviate from the frozen design without the owner's explicit subsequent instruction. **294 accounts; A/B/C 3 API lanes; per-request limit=4; 2 daily front rounds/3 pages×4; then catchup→durable history; immutable captured raw; all checks/change history; video/reels URL metadata.** This notice does **not** authorize P1–P5 implementation, live Meta calls, operational DB edits, unattended scheduling, or main feature merges. Old contradictory latest5/4+1 text is superseded.

## MANDATORY — Troubleshooting and legacy knowledge preservation (owner instruction, 2026-10-21)

**Every AI worker, every new ChatGPT conversation, and every code-writing agent on this p6 branch MUST follow these steps.** Changing chat rooms or agents does not reset the obligation.

1. **Before the first edit:** Read this file, `DESIGN_FREEZE_NOTICE.md`, the frozen design, the **working branch's** latest `HANDOFF.md` and `STATUS.md`, and [the p6 troubleshooting ledger](docs/P6_TROUBLESHOOTING_LEDGER.md). For legacy RAW-v2 incidents also read [the older full incident chronology](docs/RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md) and [error runbook](docs/RAW_V2_ERROR_HANDLING_RUNBOOK.md). The default `main` may lag behind this feature branch; do not mistake old main instructions for current implementation status.
2. **After EVERY new meaningful failure or workaround:** Before reporting the corresponding unit complete, create or update a uniquely numbered incident **P6-NNN** in the ledger. Include the observed error/symptoms, reproduction scope, confirmed cause versus hypotheses, affected files, rejected alternatives when material, repair commit SHA, test/CI run ID with exact PASS/FAIL/CANCELLED/PENDING state, data-safety impact, prevention and safe rollback. If unresolved, record it immediately as **OPEN / cause unconfirmed**; update rather than erase when resolved.
3. **After EVERY architecture/API-lane/collection-policy choice or change of direction:** Record the `why`, alternatives, expected consequences and verification status in the ledger **even if no exception occurred**. The 3-API serialisation decision is an example. Never edit the frozen design to disguise a policy change; seek owner approval where required.
4. **After confirmed regressions or test-fixture mistakes:** Record exact expected/actual values and the root cause; do not attribute test failures to the engine without evidence. Do not count a pending/cancelled workflow as PASS or infer success from a different SHA.
5. **Before handoff, before saying 'done', and before a new chat takes over:** Make sure ledger changes are actually committed and linked from the working branch's `STATUS.md` and `HANDOFF.md`; maintain the central `hibi89/project-book/main/BOOK.md` index for material milestones. Re-fetch the files and verify the entries are visible. If tools/permissions prevent this, say **"트러블슈팅 기록 미반영"** and include the missing entry in the handoff rather than falsely claiming it was stored.
6. **Sensitive material:** This repository may be public. Do not commit access tokens, cookies, live usernames/IDs, proprietary source data, RAW response payloads, operational DB contents or personal identifiers. Use anonymized descriptions and Git/CI links. Preserve existing historical records; append corrections with provenance instead of silently rewriting the past.
7. **Avoid noise:** Ordinary clean changes that caused no incident or meaningful design decision need no dummy ledger entry. The ledger is an incident/decision knowledge base, not a replacement for commit history.

**This is an obligatory agent workflow instruction, not a technical guarantee that every AI agent will obey it.** The owner has not approved operational API calls, scheduler deployment, main feature merges, or changes to the frozen design.

# AI development working agreement

1. The source of truth for client objectives is `REQUIREMENTS.md` and the user's actual customer materials. **KQ3 means Key Question 3, not 2026 Q3.** Resolve ambiguous labels rather than guessing.
2. The **official local** `account_list.csv` (294 entries) is authoritative for target account identifiers. Never silently edit, overwrite or substitute identities.
3. Never log, upload, commit or quote access tokens or `.env.local`. Scan staged changes and keep real source data local unless the user specifically approves another destination.
4. Preserve successful collection states and run history. Never overwrite or reset `posts.csv`, `media.csv`, `accounts.csv`, `collection_status.csv`, `manifest.json`, and logs without explicit confirmation and a verified backup.
5. `collector.py` currently rate-limits itself with configurable interval but has hit Meta error #4. Never issue live Meta requests as part of synthetic tests or just to diagnose code; check any API execution with user first. Stop on #4 instead of rapid repeated retries.
6. Understand that previous failures (KR_032 etc.) are not always retried on resume; consult code for the exact CLI and eligibility criteria. Distinguish tested behavior from claims made only in docs.
7. Keep data ingestion and image classification separate from dashboard retrieval. Dashboard reads DB; no mass re-analysis on refresh.
8. Distinguish raw collection completeness, field completeness, analysis eligibility, and customer claim validity. Keep absent values NULL.
9. With each code edit: run offline tests, report affected files, tested checks, untested live integrations, exact run commands and rollback steps. Avoid repeating the same failed troubleshooting technique more than twice without a new diagnosis.
10. GitHub tracks code and reviewed documentation. Use ChatGPT Library/locally held files for user-approved large dataset exchange; consider container working directories temporary rather than persistent storage.

## Mandatory cross-chat handoff synchronization (2026-10-19 incident prevention)

11. **A feature or operational design is not fully handed off merely because a code or docs file was committed.** Whenever a material plan, API lane assignment, implementation status, operational policy, known blocker, or next action changes, update **both root `HANDOFF.md` and `STATUS.md` in the same work session**, with a top-of-file dated summary and direct links to the authoritative design/code. Include what is implemented, tested, not implemented, and what must not be run.
12. Before reporting 'GitHub reflected', 'handoff complete', or telling the user a new chat can restore the project, **fetch the latest `main` versions of `HANDOFF.md`, `STATUS.md`, and the linked source document back from GitHub** and verify that the current decision is discoverable from the top sections. Provide the commit SHA and explicitly distinguish **design committed / handoff synchronized / runtime implemented / live verified**. If any step is missing, report it as missing; never imply completion.
13. For handoffs, prefer a short **current-state index** at the top of both documents over buried historical notes. Older contradictory information must be marked superseded, not silently trusted. New chat restoration should read the index and follow the links before acting.
14. **Incident record:** On 2026-10-19, three-API planner and implementation-gate docs were committed, but `HANDOFF.md`/`STATUS.md` were not simultaneously updated; a new chat therefore missed the plan. Later handoff synchronization fixed the omission. Prevent recurrence using rules 11-13. This is a documentation/workflow failure, **not evidence that live three-API collection was implemented**.
