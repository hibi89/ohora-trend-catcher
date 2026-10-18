# OHORA Trend Catcher — HANDOFF (2026-10-18 22:49 KST)

## ① 목표·용도·확정 요구·기술
- KDT 2차 OHORA Instagram Trend Catcher. Official 294 accounts KR100/US101/JP93; immutable source account keys; prospective 4-tab dashboard concept (raw explorer / weighted trend / AI assistant / collection observability), not developed here.
- Newly approved collection intent: from most recent toward historical 1 year (ideally 2), five media items maximum per account API request, 7-day client-side logical periods, 70-minute cycles, resumable account+page checkpoints, time-stamped likes/comments/followers observations.
- Python 3.11 stdlib, Meta Facebook Graph API Business Discovery Batch, SQLite WAL, CSV, sanitized JSON; source docs/RAW_V2_SCHEMA.md and docs/OHORA_RAW_COLLECTION_EXECUTION.md.

## ② 완료 기능·변경·결정
- Opt-in isolated raw_v2/collector.py added. Do not modify experiments/rolling_backfill.py, full_batch_collector.py, legacy DB/CSV or stored data. Separate output raw_v2_results/ excluded by .gitignore.
- For one app, prioritizes due latest front pages (roughly daily) and otherwise older cursor; per-account queue fairness and post ID overlap/catchup, blocked known #110 accounts. Max one account subrequest per pass, API usage halts after batch; real throughput not guaranteed.
- Separate persisted posts/post_media, per-observation likes/comments/metric NULL flags, account snapshots, field status, per-run original-list copy, raw JSON and file hashes. Carousel additional children pages flagged partial; no silent completion.
- raw_v2/verify.py offline read-only SHA/row-count/SQLite-integrity checker; run_70m_loop.ps1 bounded foreground opt-in Windows runner. Add tests/test_raw_v2.py and CI workflow. Original user instructions preserved at docs/OHORA_RAW_COLLECTION_EXECUTION.md.

## ③ 테스트 명령·결과/미검증
- PASS: Linux Python 3.11+ local python -m unittest discover -s tests -p test_raw_v2.py -v => 20/20 OK; py_compile all three Python files OK; no real Meta HTTP.
- Fake client proves 5 subrequests mocked success, repeat likes changes, missing/null/0/not_tested, 7-day bucket metadata, carousel partial, checksum tamper failure, account blocked/tested errors, safe post-request 70% stop, 70-minute cooldown and lock.
- NOT VERIFIED: Windows PowerShell runner/test, Meta live field support/rate/cursor/like root cause, 1/2 year completeness, CI workflow actual run status. None of this should be called production-successful.

## ④ 상태와 중단 지점
- Implemented + offline-tested, GitHub feature branch reviewed. Main promotion only when commit SHA and docs validated.
- Live pipeline remains STOPPED: user's local account_list.csv and .env.local are not available to this environment, so cannot honestly start collecting. New scheduler is not installed or running.

## ⑤ 오류·근거·시도·재시도 금지·대안
- Prior verified snapshot in REQUIREMENTS.md 743/1880 posts missing like_count; old API quota error4/110 may be separate. New field_observations distinguishes requested and absent/null/returned vs not_tested, and request_log distinguishes subrequest API failure. Missing key with HTTP 200 does not prove quota, permission, media-type or Meta policy as root cause.
- Existing historical cursor support is code-level only; a real two-page continuation test is necessary. If Meta refuses embedded after, do not silently advance or repeatedly call with same failing cursor.
- Alternative designs considered: two subrequests per account per 70m (more history but greater cost) versus adaptive one subrequest per pass (selected until measured). Stop at 70% post-response, no quota bypass or automatic exponential retries.

## ⑥ GitHub 브랜치·SHA·경로
- Repo: hibi89/ohora-trend-catcher; implementation branch: feature/raw-v2-20261018, based on pre-work main afc6e01c7c5490cf9003c09e2709416522d70901.
- Core files: raw_v2/collector.py, raw_v2/verify.py, raw_v2/README.md, raw_v2/run_70m_loop.ps1, tests/test_raw_v2.py, docs/RAW_V2_SCHEMA.md, docs/OHORA_RAW_COLLECTION_EXECUTION.md, .github/workflows/raw-v2-tests.yml; README, STATUS.md, HANDOFF.md, .gitignore.
- Latest pushed SHA and main promotion must be checked from GitHub rather than inferred from documentation; refer to BOOK.md for verified SHA.

## ⑦ 실행 명령·환경·Production
- Runbook: raw_v2/README.md; Python -m unittest discover -s tests -p test_raw_v2.py -v; python raw_v2/collector.py --source account_list.csv (DRY RUN).
- Live after real credentials and quota approval only: python raw_v2/collector.py --source account_list.csv --env-file <local-secret-file> --output-dir raw_v2_results --batch-size 5 --max-batches 1 --stop-usage 70 --live.
- Vercel Production URL: not applicable; dashboard was not built/deployed, this work only adds local RAW collector.

## ⑧ 원본·백업·인증 관리
- Authoritative source official account_list.csv and older latest5/latest1/latest10 SQLite/CSV remain user-held on Windows. These are not in GitHub; content/source bytes unverified remotely. No migration/overwriting.
- Actual token, IG ID, Meta app ID stored only in user's local credential file, never print/commit. New raw_v2_results/ local separate SQLite+run JSON/CSV; backup DB consistently including WAL/SHM after stopping process.
- This implementation uses network solely in opt-in --live runs; no media image binary downloads or external AI requests.

## ⑨ 우선순위·절차·완료 기준·승인 사항
1. GitHub pull main, test 20/20 on Windows (API free) and dry run source roster. Inspect that the source path and token file exist, without printing secrets.
2. Check Meta dashboard quota and caller professional account permissions. Pilot --live with batch size5/max-batches1 only. Inspect raw payload, field_support, like_missing audit, usage header, exact missing-field cases and verify.py.
3. Test two different cycles / nesting after cursor without skipping or refreshing old data incorrectly; scrutinize Reel/carousel like_count absence and field-specific controlled probes; do not assert causal diagnosis before controlled evidence.
4. Only after pilot success set a bounded 70m loop on user's PC and adjust MaxBatches by observed total_time; hourly recovery is not guaranteed. Track date coverage, per-account count, blocked states and archive results locally.
5. Feature completion requires actual Meta results and Windows runtime/credential/permission evidence; if unavailable status = partial, not done.

## ⑩ 갱신 일시(KST)·확인 시점·검증 범위
- Drafted 2026-10-18 22:49 KST. Local Python tests PASS; GitHub source files re-read confirmed; actual Windows/Meta access is unavailable here. Last remote commit to be verified after recording BOOK.
