# Phase 2 runtime and configuration — safe starter specification

**Do not copy actual secrets into this document.** Environment key names below include audited existing keys and new proposed settings. Codex should map exact names in current source before implementation, and D1 must own the config contract; don't invent extra equivalents.

## Existing observed names in audit

`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, each service-specific DB password key; `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `GATEWAY_DEV_JWT_SECRET`, `GATEWAY_JWT_ISSUER`, `GATEWAY_JWT_AUDIENCE`, `SERVICE_JWT_SECRET`, `SERVICE_JWT_ISSUER`, `SERVICE_JWT_AUDIENCE`, `CONTEXT_DEV_API_KEY`, `NEXT_PUBLIC_API_MODE`, `NEXT_PUBLIC_GATEWAY_URL`, `GI_LLM_PROVIDER`, `GI_LLM_MODEL`, `ANTHROPIC_API_KEY`.

## Accepted Phase 2 configuration keys

| Name | Example non-secret setting | Behavior |
|---|---|---|
| `GOALPULSE_PROFILE` | `demo` | `demo` fail-closed if provider/mock/auth incomplete. `dev`/`test` allow explicit fixture |
| `GATEWAY_DEMO_ALLOWED_ORIGIN` | `http://localhost:3000` | Exact same-origin allow value for the isolated demo browser |
| `GATEWAY_DEMO_SESSION_SECRET` | empty in committed example | Required server-side secret in demo; no default and never exposed to browser code |
| `GATEWAY_DEMO_SESSION_TTL_SECONDS` | `1800` | Short-lived session lifetime; D1-01 validates an approved bound |
| `GATEWAY_DEMO_USER_A_ID`, `GATEWAY_DEMO_USER_B_ID` | UUID, supplied privately | Exactly two pre-provisioned demo owner identities |
| `GATEWAY_DEMO_USER_A_USERNAME`, `GATEWAY_DEMO_USER_B_USERNAME` | `developer1`, `reviewer` | Pre-provisioned isolated-demo identifiers, compiled into the demo frontend under ADR-0007 |
| `DEMO_USER_A_PUBLIC_PASSWORD`, `DEMO_USER_B_PUBLIC_PASSWORD` | public demo-only values | Intentionally displayed by the demo frontend; never reuse a private or production password |
| `GATEWAY_DEMO_USER_A_PASSWORD_HASH`, `GATEWAY_DEMO_USER_B_PASSWORD_HASH` | manager-derived | Salted hashes derived from the public demo passwords; only these reach Gateway |
| `GATEWAY_DEMO_USER_A_DISPLAY_NAME`, `GATEWAY_DEMO_USER_B_DISPLAY_NAME` | supplied privately | Non-secret labels returned by the demo-session response |
| `DM_SCAN_MIN_MINUTES` | `30` | Demo scheduling floor; user approves any adjustment before activation |
| `DM_MAX_ACTIVE_GOALS_PER_USER` | `2` | Spending guard |
| `DM_MAX_SOURCES_PER_GOAL` | `3` | Search breadth guard |
| `DM_MAX_REQUESTS_PER_USER_PER_DAY` | `100` | Provider quota budget |
| `DE_PRICE_MAX_AGE_MINUTES` | `30` | Evidence freshness ceiling |
| `DE_ALERT_RECHECK_MAX_AGE_MINUTES` | `10` | Latest proof before verified alert |
| `NOTIFY_GOAL_RATE_LIMIT_MINUTES` | `60` | No alert spam |

Frontend demo build: `NEXT_PUBLIC_API_MODE=gateway`; reject `mock` and undefined mode in `demo`. `GI_LLM_PROVIDER=anthropic`, valid non-empty `GI_LLM_MODEL` and `ANTHROPIC_API_KEY`. `CONTEXT_DEV_API_KEY` must be set for DM worker. Private credential values never enter build logs. Only the ADR-0007 public demo login pairs are compiled into the browser; provider and platform secrets remain server-side.

The committed `.env.example` leaves private secrets, user IDs, and derived
password hashes as placeholders. Demo usernames and public demo-only passwords
have non-secret defaults. `dev` may start with explicit fixtures; `demo` must
reject incomplete required values. There is no `DEMO_AUTH_ENABLED` switch: the
accepted gate is `GOALPULSE_PROFILE=demo`, and the demo-session routes return
`503` in every other profile.

## Boot sequence for Developer 1 to implement

1. Validate env modes and required secrets **without printing values**.
2. Start Postgres and RabbitMQ; isolate service networks and bound ports to loopback where possible.
3. Run migrations of seven services in deterministic, documented order (no cross-service SQL).
4. Start P0 service API processes (GI, GM, DM, DE, GE, NS), outbox publishers, Rabbit workers and DM periodic scheduler. Agent Integration runtime is enabled only if P1 task is completed. Ensure health/ready checks.
5. Start Gateway with accepted owner/service JWT exchange.
6. Start Next.js in gateway mode.
7. Run `scripts/phase2-smoke.sh` or a documented equivalent added in implementation; **this pack does not claim that script currently exists**.

## Tests and spend control

- CI uses recorded provider responses, local Rabbit/Postgres and explicit fixtures; no chargeable LLM/Context calls.
- Controlled live smoke requires explicit opt-in environment flag or one-time script and max ~1 LLM call + 2 Context calls per scenario; log request IDs/latency/error not raw data.
- Test DB must be disposable. Never point migration/integration test at user's existing production data.
- Log correlation ID, event ID, source ID, goal ID/version, business outcome; redact raw goal text/URLs containing credentials.
