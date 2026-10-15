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