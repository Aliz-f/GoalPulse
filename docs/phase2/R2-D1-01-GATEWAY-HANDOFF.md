# R2-D1-01 Gateway/auth handoff and current R1 blockers

**Owner:** Developer 1. **Contract baseline:** frozen
`contracts/openapi/gateway.v1.yaml`, accepted
`contracts/openapi/gateway.phase2.extension.v1.yaml`, and ICP-08 in ADR-0002/0003.
No contract file was changed by this task.

## Implemented Gateway contract

The Gateway is an exact-path FastAPI proxy; it has no catch-all and cannot
expose `/internal/` routes. These frozen routes are wired:

| Public operation | Private destination | Downstream identity |
|---|---|---|
| Create/message/get goal session | Goal Intelligence, same path | 60-second ServiceBearer JWT with `gi.session.write`, plus Gateway-derived `X-Authenticated-User-Id` |
| Confirm session | Goal Management, same path | 60-second user-context JWT with owner UUID `sub` |
| List/get/lifecycle goal | Goal Management, same path | 60-second user-context JWT with owner UUID `sub` |

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

## Runtime state and blockers

| Acceptance item | State | Evidence / blocker |
|---|---|---|
| Seven frozen routes with fake downstream | PASS | `infrastructure/gateway/tests/test_gateway.py` exercises all seven and status preservation. |
| Two owners, unauthenticated denial, cross-owner masked as 404 | PASS | Gateway tests use independent cookie jars and downstream owner checks. |
| Demo auth, CSRF, rate limit, no token response | PASS | Gateway unit/security tests. |
| Demo rejects FE mock, GI fixture, DE fixture, and missing secrets | PASS | `GatewaySettings.from_env` fail-closed tests. |
| Private service ports and deterministic GI migration | PASS | Compose topology tests and isolated Docker smoke. |
| One command starts all P0 APIs/workers | BLOCKED | GM runtime composition belongs to R2-D1-02. DM and DE require injected production factories; GE and Notification lack runnable owner-provided API/worker entrypoints. Adding fake health processes would violate truthful readiness. |
| Full demo-profile live provider smoke | BLOCKED | Depends on D2/D3 live runtime tasks and provisioned provider credentials; no live paid calls were made here. |

Owners should publish executable module names, health/readiness paths,
requirements, migration commands, worker commands, and required environment
keys. D1 can then add them to the generic private Python service image and
Compose without changing their source trees.

## Commands and redacted smoke evidence

```bash
python -m pytest -q infrastructure/gateway/tests infrastructure/tests
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

An isolated project smoke used alternate loopback ports and fresh named volumes.
PostgreSQL, RabbitMQ, and GI reported healthy; GI migration exited zero and
`GET /health/live` returned `{"status":"live"}`. Gateway readiness correctly
returns `503 {"status":"not_ready"}` while the R2-D1-02 Goal Management runtime
is absent. The existing default PostgreSQL volume was not deleted or reset when
its historical password differed from the current `.env`.
