# R2-D1-01 Gateway/auth and seven-service runtime handoff

**Owner:** Developer 1. **Contract baseline:** frozen
`contracts/openapi/gateway.v1.yaml`, accepted
`contracts/openapi/gateway.phase2.extension.v1.yaml`, and ICP-08 in ADR-0002/0003.
Frozen public schemas, routes and events are unchanged. The coordinated D1/D3/D4
completion adds only `contracts/openapi/internal.goal-sources.v1.yaml` under
ADR-0005.

## Implemented Gateway contract

The Gateway is an exact-path FastAPI proxy; it has no catch-all and cannot
expose `/internal/` routes. These frozen routes are wired:

| Public operation | Private destination | Downstream identity |
|---|---|---|
| Create/message/get goal session | Goal Intelligence, same path | 60-second ServiceBearer JWT with `gi.session.write`, plus Gateway-derived `X-Authenticated-User-Id` |
| Confirm session | Goal Management, same path | 60-second user-context JWT with owner UUID `sub` |
| List/get/lifecycle goal | Goal Management, same path | 60-second user-context JWT with owner UUID `sub` |

The accepted additive Phase 2 evaluation, notification and package paths are
also explicit. They receive a 60-second service JWT whose UUID subject is the
Gateway-derived owner and whose scope permits exactly one operation. Browser
identity headers and tokens are never forwarded.

Gateway preserves downstream HTTP status and JSON body, passes only a validated
or minted UUID correlation ID, and does not forward browser authorization or
identity headers. Requests have a 64 KiB body cap and five-second downstream
timeout. Only the configured browser origin receives credentialed CORS access.

Demo authentication implements all three accepted operations:

- `POST /v2/demo/sessions`: exact allowed Origin, one of exactly two configured
  users, PBKDF2-SHA256 password hashes, five failed attempts per peer per minute.
- `GET /v2/demo/session`: returns only `user_id`, `display_name`, and expiry.
- `POST /v2/demo/sessions/logout`: requires allowed Origin and the CSRF header.

The session is signed, expires in 5–60 minutes (default 30), is HttpOnly,
SameSite=Lax, and Secure for an HTTPS allowed origin. A signed, session-bound
`goalpulse_csrf` double-submit cookie must match `X-CSRF-Token` on every
cookie-authenticated mutation. Outside `demo`, every demo-session operation is
`503`. Bearer-authenticated API calls do not use cookie CSRF.

## D2 frontend integration

Use `credentials: "include"` for Gateway requests. After login, read the
non-HttpOnly `goalpulse_csrf` cookie and send its value as `X-CSRF-Token` for
message, confirm, lifecycle, and logout mutations. Never read, copy, log, or
store `goalpulse_session`; it is deliberately HttpOnly. A user switch must
logout and clear frontend caches before the next login.

The accepted origin is exact (`GATEWAY_DEMO_ALLOWED_ORIGIN`); other origins get
no credentialed CORS response. Login itself needs Origin but not a prior CSRF
token. Invalid credentials are `401`, integrity failure `403`, throttle `429`,
and disabled demo profile `503`.

## Runtime acceptance state

| Acceptance item | State | Evidence |
|---|---|---|
| Seven frozen routes with fake downstream | PASS | `infrastructure/gateway/tests/test_gateway.py` exercises all seven and status preservation. |
| Two owners, unauthenticated denial, cross-owner masked as 404 | PASS | Gateway tests use independent cookie jars and downstream owner checks. |
| Demo auth, CSRF, rate limit, no token response | PASS | Gateway unit/security tests. |
| Demo rejects FE mock, GI fixture, DE fixture, and missing secrets | PASS | `GatewaySettings.from_env` fail-closed tests. |
| Private ports, ordered migrations, seven APIs and workers | PASS | Compose tests and fresh live status show every declared process; service APIs expose container port 8000 only and Gateway is the sole host application port. |
| One command reaches green readiness | PASS | Fresh project `goalpulse-r2-full-live2`: all seven APIs and all workers healthy, migrations/init exited zero, Gateway returned `{"status":"ready"}`. Only Gateway exposed an application port. |
| Full demo-profile live provider smoke | NOT RUN | Requires provisioned Anthropic/Context.dev credentials and approved Context access-policy ID; no paid call is made by tests. |

The former source-binding blocker is resolved by ADR-0005 and
`contracts/openapi/internal.goal-sources.v1.yaml`. Discovery Monitoring returns
the complete bounded active set for an exact known activation; Goal Evaluation
validates identity, UUID ordering, uniqueness and the three-source limit.
The live private-network smoke returned two sorted sources through GE's scoped
reader and returned 403 without ServiceBearer authentication.

## Commands and redacted smoke evidence

```bash
python -m pytest -q infrastructure/gateway/tests infrastructure/tests
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

The final isolated project smoke used alternate loopback ports and fresh named
volumes. PostgreSQL, RabbitMQ, all seven APIs, all workers and Gateway were
healthy; every one-shot migration/init container exited zero. Gateway readiness
returned `{"status":"ready"}`. A first smoke exposed and then fixed a RabbitMQ
queue-type mismatch: D1's definitions now use durable classic queues, matching
the shipped consumer declarations while retaining persistent messages, confirms,
manual acknowledgements and dedicated DLQs. No volume was deleted or reset.

Final verification: contract validator PASS; targeted Gateway acceptance 9
passed; platform/contracts 39 passed; Discovery 276 passed/7 opt-in skipped;
Goal Management 36 passed/1 opt-in skipped plus its live PostgreSQL test passed;
official Python 3.12 full suite 1414 passed/25 opt-in skipped.
