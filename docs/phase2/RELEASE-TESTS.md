# Phase 2 MVP test & release gates

**Truthfulness rule:** No one can call a demo "real" if the UI uses `frontend/src/lib/api/mock.ts`, GI fixture provider, or DE fixture evidence on the user-facing path. Recorded-provider tests are necessary for CI but not a substitute for a controlled live smoke. A live scrape that returns Unknown is **not a failure** if the constraints truly cannot be verified.

## Test infrastructure

- Python >=3.12 (audit host Python 3.11.5 prevented GI suite). Node 22 (audit `npm run build` and Playwright failed). PostgreSQL + Rabbit in **isolated disposable** containers. No old personal DB mutations.
- Baseline audit: `python scripts/validate_contracts.py` passed; 717 back-end tests outside GI passed / 7 Postgres skipped; 5 FE unit test files, typecheck, lint passed; build/e2e failed; **live providers NOT RUN**.
- CI always uses deterministic recorded provider responses; external network tests separate, manual, redacted and cost-bounded.

## Required scenarios

| ID | Scenario | Evidence / required result |
|---|---|---|
| A1 | Goal creation | Text incomplete → **real** Anthropic dynamic question; answers → exact-revision Draft complete → explicit confirm creates version 1. No auto-confirm. |
| A2 | Identity | Two demo users; one cannot read, confirm or change another's goal; unauthenticated denied. Cookie protected; direct service port not publicly bound. |
| A3 | Persistence | Restart Browser/GI/GM/DB connection; confirmed goal still exists, revision unchanged. |
| B1 | Actual activation | Confirm → `goal.activated.v1` Rabbit → DM job record → **real Context.dev adapter in main workflow** → DE Observation/failure and source ID, timestamp, request metadata. |
| B2 | Unknown is truthful | No shipping/fee, stock, tax, freshness or condition => unknown or unverified; **never** `is_opportunity_verified=true`. UI says exactly what's missing. |
| B3 | Above budget | Fresh known price above threshold => unsatisfied; no Verified event. |
| B4 | Checked policy | Internal checker: supported first-party matching extraction → checked; caller-provided `checked` ignored; ambiguous/stale/disputed rejected. Cross-check must be provable, not just duplicate copy. |
| B5 | Verified controlled case | Complete internally checked observation and confirmed FI shipping, tax, stock, current version => verified event. This case may use **explicitly labeled recorded fixture** in CI if no live seller exposes all facts. |
| C1 | Notification | Verified event => one owner notification persisted; same event delivered twice or re-scanned same business offer => no duplicate. |
| C2 | Rate + ownership | Two users isolated; excessive verified alerts throttled; paused goal suppresses new positives. |
| C3 | Action package P1 | Only if implemented: owner-only, source-backed, expiry, grouped seller_orders, `auto_execute=false`, human approval required, checkout revalidation. |
| D1 | At-least-once/restart | Rabbit manual ACK after DB commit, replay event doesn't duplicate outcome; DLQ observable. |
| D2 | Provider unavailable | LLM 429/timeout => visible retriable error no fixture; Context 429/timeout => bounded retries and unknown/failure. |
| D3 | Stale version | Update/paused version or outdated event never generates new Verified/Notification. |
| D4 | Scheduler | Goal activated gets initial scan; due job scans again within configured interval; per-user/day quota prevents excess; process restart preserves schedule/idempotency. |
| D5 | Builds | Python 3.12 all tests including GI; Node 22 `npm ci`, `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, `npm run e2e`; Compose app stack starts, all required readiness green. |

## Controlled LIVE smoke (run manually, do not put in default CI)

1. Use **synthetic goal** (e.g. fictional product budget) with real LLM; use one request/clarification and record provider selected, HTTP result, latency, sanitized summary; never prompts or key.
2. Confirm goal in authenticated browser. Confirm GM DB persistence and correct activation event.
3. DM worker calls Context.dev through existing `ContextProvider`, **not `probe.py` only**. Limit URLs to approved public product URLs, fresh as allowed, log only source/request ID/time/status/cache age.
4. DE saves observation or documented failure. GE result appears in My Goals and is **not fabricated**.
5. Where live source has incomplete shipping/stock/tax: screenshot **Unknown/Candidate**, not fake Verified. For notification demo use CI-labeled Recorded scenario and explain difference.
6. Repeat event to demonstrate NS dedup. Show read-only agent package only if actual P1 scope implemented.

## Definition of Done P0

- [ ] ADR decisions applied/merged into actual repo; frozen v1 unchanged; additions typed/validated.
- [ ] P0 services have documented runnable process/worker startup as appropriate (Agent Integration P1); no direct exposure.
- [ ] Real Anthropic + real Context.dev available and manually proven end-to-end with redacted evidence.
- [ ] One product goal created with real LLM and stored in DB; no hidden in-memory mock.
- [ ] Honest GE tri-state + DE checked policy; verified pathway demonstrable with recorded full facts, UNKNOWN live when facts missing.
- [ ] Notification real worker + UI read, durable, owner isolation and replay dedup.
- [ ] Scheduler recurring scan + quota; pause prevents future work; provider outages visible.
- [ ] Tests and build pass in correct Python/Node versions; exact commands/results recorded.
- [ ] No passwords, keys, source page raw text or private user goals in CI screenshots/logs.

## Release disclaimer

This is a **hackathon MVP**, not a security-certified production purchasing product. Do not claim to track all sites or offer real-time guaranteed inventory; user must recheck actual seller checkout terms.
