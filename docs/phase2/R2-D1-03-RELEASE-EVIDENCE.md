# R2-D1-03 release evidence

**Automated release decision: PASS. Overall release remains BLOCKED only pending
the separately authorized controlled live Anthropic + Context.dev smoke.**

- Branch: `r2-d1-03-release-gate`
- Baseline commit: `9cfe6bd`
- Evidence date: 2026-11-15 (Europe/Helsinki)
- Scope: recorded A1–D5 coordination, disposable durability lane, Node 22
  frontend gate, full Compose readiness/recovery, centralized environment,
  frontend release image, and host-Nginx VPS runbook
- Data classification: recorded/synthetic only; no raw goal, provider response,
  bearer value, API key, password, or credential-bearing URL is included

## Current results

| Evidence | Result | Command / reason |
|---|---|---|
| Contract validation under Python 3.12 | PASS | `python3.12 scripts/validate_contracts.py` in the isolated test environment: 5 schemas, 13 examples, 4 event routes and all accepted OpenAPI documents passed. |
| Release-manifest/topology/env/Nginx tests | PASS | Python 3.12 deployment, release-gate and Compose topology selection — 24 passed. The root template exactly covers all Compose inputs and no Nginx container exists. |
| Central environment validation | PASS | `python3 infrastructure/release/validate_env.py --env-file .env` — 63 configured variables validated without displaying values; quiet Compose expansion passed. |
| No-silent-mock deployment configuration | PASS | Demo + mock and missing frontend mode builds fail as required; Gateway/platform tests also fail closed for fixture evidence and missing provider/auth configuration. |
| Recorded service lane | PASS | Contracts, platform/Gateway and all seven service groups passed under Python 3.12. |
| Required root pytest | PASS | Python 3.12 `python -m pytest -q` — 1429 passed, 25 skipped; the skipped opt-in integrations were run separately in the durable lane. The suite ran outside the filesystem/network sandbox so FastAPI's AnyIO worker threads matched CI/VPS execution. |
| Disposable PostgreSQL/Rabbit lane | PASS | GI, GM, Discovery, Data & Evidence, Goal Evaluation, Notification and Agent Integration all passed against PostgreSQL 16/RabbitMQ 4. The resolved failures are recorded in `R2-D1-03-HANDOFF-BLOCKERS.md`. |
| Node 22 frontend lane | PASS | Typecheck, lint, unit tests, production Gateway build, both fail-closed build checks, recorded Playwright and Gateway-mode Playwright passed with Node 22.21.0. |
| Frontend release image | PASS | Node 22.21.0 standalone Next.js image built, runs as UID/GID 1001, health returned 200, and `X-Powered-By` is disabled. Production `npm audit --omit=dev` found 0 vulnerabilities. |
| Compose config/start/readiness/recovery | PASS | `docker compose ... up -d --build --wait --wait-timeout 300`; frontend, Gateway and every API became healthy, workers stayed running, and migrations/init exited 0. PostgreSQL/RabbitMQ were restarted without volume deletion and the entire stack recovered to healthy. |
| Host Nginx/VPS operations | PASS (static/docs) | Host-only reverse-proxy template and step-by-step DNS, TLS, firewall, backup, upgrade, rollback and rotation runbook added. VPS-specific `nginx -t`, Certbot and external HTTPS smoke remain operator steps because this workstation is not the target VPS. |
| Live Anthropic + Context main path | NOT RUN / BLOCKER | Anthropic and Context.dev keys are configured locally, but the required provider-issued Context.dev `pol_...` access-policy ID is still unavailable. No provider call was made and recorded input cannot substitute. |

## Scenario status

A1–D5 are defined in `infrastructure/release/scenarios.json`. A scenario is not
release-PASS merely because a mapped unit test exists: its recorded, durable,
frontend and/or live evidence requirements must all be satisfied. In particular,
A1, B1, B2, D2 and D5 retain a live-required marker, and the overall release is
blocked until the controlled smoke in the runbook succeeds. C3 remains optional
P1 but is exercised when Agent Integration is enabled.

Local redacted artifacts are `/tmp/r2-d1-03-recorded-final.json`,
`/tmp/r2-d1-03-durable-final.json` and `/tmp/r2-d1-03-frontend-final.json`.
They contain no configured test password and are intentionally not committed;
CI generates equivalent per-commit artifacts using the standard filenames.

## Safety assertions

- Frozen v1 contracts and the four routing keys are unchanged.
- Recorded runner removes live provider credentials and never labels itself live.
- Live execution is manual and separately authorized; missing credentials become
  `NOT RUN`, never fixture success.
- Diagnostic artifacts redact secrets, authorization, DSNs, prompts, raw goals,
  and provider response bodies.
- No checkout, payment, booking, purchase, or external delivery action is added.
- Frontend and Gateway are loopback-only; private service APIs are not published.
- One root `.env` is the Compose source and the frontend build receives only
  public profile/mode/origin values.

## Residual dependency note

`npm audit --omit=dev` reports zero production vulnerabilities. The complete
audit currently reports seven moderate `minimatch` findings in the
ESLint/TypeScript development-only chain. The current `typescript-eslint`
release constrains `minimatch` to the affected 10.x major while the patched
release is 11.x. No unreviewed major override was forced into the build. Track
the upstream `typescript-eslint` update; this code is absent from the standalone
runtime image and does not process remote production input.
