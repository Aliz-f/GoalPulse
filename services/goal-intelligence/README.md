# goal-intelligence — GoalPulse service (Developer 2)

**Implementation status:** D2-GI-01..05 implemented as a first pass, pending team PR review. ICP-01 and ICP-08 are accepted under ADR-0002; the service validates the accepted signed service-JWT profile and operation-specific scopes.

- [Service specification](../../docs/implementation/02-goal-intelligence.md) · [Tasks](../../tasks/developer-2.md) · [Rules](../../AGENTS.md)

## What it does

Natural-language goal → strict `GoalDraft.v1` revisions → one clarifying question per turn → canonical summary with every proposed default → explicit "yes" → `ready_for_confirmation`. Goal Management (not GI) confirms and activates.

| Layer | Path | Notes |
|---|---|---|
| Domain | `goal_intelligence/domain/` | `money.py` Decimal EUR → int cents; `extraction.py` untrusted patch model (`extra="forbid"`); `compiler.py` missing fields, questions, readiness, GoalSpec schema check |
| LLM | `goal_intelligence/llm/` | `base.py` retries/backoff/timeout/input limit; `fixture.py` deterministic offline extractor (default, used in CI); `anthropic.py` optional Messages API adapter (stdlib HTTP) |
| Persistence | `goal_intelligence/persistence/repository.py` + `migrations/0001_init.sql` | Compare-and-set revisions, immutable `draft_revisions` (DB trigger), `llm_attempts` metadata only |
| Use cases | `goal_intelligence/service.py` | Framework-free; typed service errors and exact revision handling |
| HTTP | `goal_intelligence/api/app.py` | Frozen public `/v1/goal-sessions*`; internal `/internal/v1/goal-sessions/{id}/candidate` and `/mark-confirmed` (GM only) |
| Auth | `goal_intelligence/auth.py` | Signed service JWTs with issuer/audience/expiry/UUID-subject validation and per-operation scopes; user UUID trusted only from the authenticated gateway |

## Safety rules enforced in code

- Only `product_purchase` and `recurring_procurement` can become ready; travel/jobs/etc. stay `collecting` with an explanation.
- No hidden defaults: condition, match policy, quantity, budget, shipping/tax and destination must come from the user. Monitoring frequency/evidence age, notifications and seller count are *proposals* shown in the summary; readiness requires an explicit yes to that summary, and any later change withdraws the approval.
- Non-EUR amounts are refused (no FX); delivery is limited to `FI` (ICP-04); budgets must include shipping and tax.
- `agent_handoff` is always `prepare_only`, `auto_purchase` always `false`; asking for auto-buy is explained and resets approval.
- The model's output is validated against a closed schema, so it cannot set owner, state, revision, events or auto-purchase. A failed model call applies nothing: the message is recorded and the draft asks the user to resend (the frozen API has no 503).
- `ready_for_confirmation=true` only after the candidate (plus GM's server-managed fields) validates against `goal-spec.v1.schema.json`.
- Logs carry error codes only, never message text, prompts or keys.

## Run locally

```bash
cd services/goal-intelligence
python -m pip install -r requirements.txt
export GI_DATABASE_URL=postgresql://goalpulse_admin:<pw>@127.0.0.1:5432/goal_intelligence   # or sqlite:///gi.sqlite3 (no migrate step needed)
python -m goal_intelligence.persistence.migrate              # applies migrations/*.sql once each (Postgres)
export SERVICE_JWT_SECRET="<same 32+ char local issuer secret used by D1>"
export SERVICE_JWT_ISSUER=goalpulse-internal-dev
export SERVICE_JWT_AUDIENCE=goalpulse-internal
export GI_LLM_PROVIDER=fixture          # or: anthropic (+ ANTHROPIC_API_KEY, optional GI_LLM_MODEL)
uvicorn goal_intelligence.api.app:create_app --factory --port 8102
```

| Variable | Default | Meaning |
|---|---|---|
| `GI_DATABASE_URL` | — (required) | `postgresql://…` or `sqlite:///path` |
| `SERVICE_JWT_SECRET` / `SERVICE_JWT_ISSUER` / `SERVICE_JWT_AUDIENCE` | — (required) | Accepted ICP-08 service-JWT verifier configuration; shared with the local D1 issuer |
| `GI_LLM_PROVIDER` | `fixture` | `fixture` or `anthropic` |
| `GI_LLM_MODEL` | `claude-haiku-4-5-20251001` | model for the anthropic provider |
| `ANTHROPIC_API_KEY` | — | only for `anthropic` |
| `GI_LLM_TIMEOUT_S` / `GI_LLM_MAX_ATTEMPTS` | `20` / `3` | per-attempt timeout, bounded retries |
| `GI_MAX_MESSAGE_CHARS` / `GI_MAX_USER_TURNS` | `2000` / `20` | input and conversation bounds |

Gateway contract (for Dev 1): forward public calls with a service JWT carrying `gi.session.write` and `X-Authenticated-User-Id: <uuid from the validated end-user JWT>`, and **strip any client-supplied copy of that header**. Pass a valid UUID `X-Correlation-ID` through; GI mints one only when absent and rejects malformed values. Never route `/internal/*` publicly. Goal Management uses `gi.candidate.read` for candidate reads and `gi.confirmation.write` for confirmation marks.

## HTTP behavior and the frozen Gateway gap

| Route | Service responses |
|---|---|
| `POST /v1/goal-sessions` | 201, 401, 422 |
| `POST /v1/goal-sessions/{id}/messages` | 200, 401, 404, 409 (stale revision, or the session is confirmed/abandoned), 422 |
| `GET /v1/goal-sessions/{id}` | 200, 401, 404 |
| `GET /internal/v1/goal-sessions/{id}/candidate` | 200, 403 (any service-identity failure), 404, 409 (stale or not ready) |
| `POST /internal/v1/goal-sessions/{id}/mark-confirmed` | 204, 403, 409 (mismatch, incl. unknown session) |

Error bodies use the ICP-01 `Error` shape `{"code": ..., "message": ...}`. A 409 carries no revision number; clients re-read the session. Sessions past their expiry or turn limit are reported as `state: "abandoned"` (GoalDraft.v1 enum). Tables follow `docs/data-model.md`: `sessions`, `conversation_messages`, `draft_revisions` (+ `llm_attempts`).

Frozen `contracts/openapi/gateway.v1.yaml` does not enumerate every authentication and validation failure above (notably several 401/404/422 responses). These service responses are documented here rather than silently described as frozen-contract responses. Developer 1 must preserve or formally version the public error contract when D1-GW-01 exposes the routes; this service change does not edit frozen v1.

## Tests

```bash
python scripts/validate_contracts.py && python -m pytest -q          # from repo root
```

`tests/test_gi_http.py` needs FastAPI + httpx; without them it is skipped and reported as NOT RUN.

Postgres integration (`tests/test_gi_postgres.py`) runs when `GI_TEST_POSTGRES_URL` points at a server where the user may create databases. Each run creates and drops a throwaway database. With the compose stack:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d postgres
GI_TEST_POSTGRES_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:5432/postgres" python -m pytest -q -rs services/goal-intelligence/tests
```

CI runs the same suite against a Postgres 16 service container (`.github/workflows/goal-intelligence.yml`).

## Handoff fixtures

`fixtures/` (regenerate with `python services/goal-intelligence/scripts/export_fixtures.py`):
ready drafts for both goal types, a collecting draft, an unsupported draft, the 409 body (for D1), and the GoalSpecs GM would assemble from the ready drafts (for D4). All are schema-checked in `tests/test_gi_fixtures.py`.

## Known gaps

- The fixture extractor is English-only and rule-based; use the anthropic provider for free-form demo input.
- Gateway rate limiting is Dev 1's (D1-GW-01); GI only bounds message size and turns.
