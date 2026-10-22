# P6 front collection MVP

Offline CI is green at 8c3b995b. Live collection remains unverified.

1. Wire an explicitly approved MetaTransport into a separate serial coordinator runner. Do not weaken OfflineSerialContractBridge's fixture-only restriction.
2. Verify A/B/C credentials locally without exposing token values.
3. Persist request intent before transport; preserve response and RAW before committing page evidence. Never resend uncertain requests.
4. Respect limit=4, serial execution, 85% usage halt and 65-minute cooldown.
5. With explicit live approval, collect one front page and verify its raw capture and observations before expanding to the official 294-account roster.

Do not claim live success from offline CI.
