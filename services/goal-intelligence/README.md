# goal-intelligence — GoalPulse service (Developer 2)

**Implementation status:** D2-GI-01..05 implemented as a first pass, pending team PR review. ICP-01 (internal API) and ICP-08 (auth) are **not yet accepted** (see `docs/implementation/DECISIONS.md`), so the internal routes and the auth module are built against the *proposal* and marked interim.

- [Service specification](../../docs/implementation/02-goal-intelligence.md) · [Tasks](../../tasks/developer-2.md) · [Rules](../../AGENTS.md)

## What it does

Natural-language goal → strict `GoalDraft.v1` revisions → one clarifying question per turn → canonical summary with every proposed default → explicit "yes" → `ready_for_confirmation`. Goal Management (not GI) confirms and activates.

| Layer | Path | Notes |
|---|---|---|
| Domain | `goal_intelligence/domain/` | `money.py` Decimal EUR → int cents; `extraction.py` untrusted patch model (`extra="forbid"`); `compiler.py` missing fields, questions, readiness, GoalSpec schema check |
| LLM | `goal_intelligence/llm/` | `base.py` retries/backoff/timeout/input limit; `fixture.py` deterministic offline extractor (default, used in CI); `anthropic.py` optional Messages API adapter (stdlib HTTP) |
| Persistence | `goal_intelligence/persistence/repository.py` + `migrations/0001_init.sql` | Compare-and-set revisions, immutable `draft_revisions` (DB trigger), `llm_attempts` metadata only |
| Use cases | `goal_intelligence/service.py` | Framework-free; maps to 404/409/422/503 |
| HTTP | `goal_intelligence/api/app.py` | Frozen public `/v1/goal-sessions*`; internal `/internal/v1/goal-sessions/{id}/candidate` and `/mark-confirmed` (GM only) |
| Auth | `goal_intelligence/auth.py` | **Interim until ICP-08**: per-service tokens; user UUID trusted only from the gateway |

## Safety rules enforced in code

- Only `product_purchase` and `recurring_procurement` can become ready; travel/jobs/etc. stay `collecting` with an explanation.
- No hidden defaults: condition, match policy, quantity, budget, shipping/tax and destination must come from the user. Monitoring frequency/evidence age, notifications and seller count are *proposals* shown in the summary; readiness requires an explicit yes to that summary, and any later change withdraws the approval.
- Non-EUR amounts are refused (no FX); delivery is limited to `FI` (ICP-04); budgets must include shipping and tax.
- `agent_handoff` is always `prepare_only`, `auto_purchase` always `false`; asking for auto-buy is explained and resets approval.
- The model's output is validated against a closed schema, so it cannot set owner, state, revision, events or auto-purchase. A failed model call changes nothing (503, same revision).
- `ready_for_confirmation=true` only after the candidate (plus GM's server-managed fields) validates against `goal-spec.v1.schema.json`.
- Logs carry error codes only, never message text, prompts or keys.

## Run locally

```bash
cd services/goal-intelligence
python -m pip install -r requirements.txt
psql "$GI_DATABASE_URL" -f migrations/0001_init.sql          # or use sqlite:///gi.sqlite3 for a quick try
export GI_DATABASE_URL=postgresql://goal_intelligence:...@127.0.0.1:5432/goal_intelligence
export GI_SERVICE_TOKENS="gateway=<16+ chars>,goal-management=<16+ chars>"
export GI_LLM_PROVIDER=fixture          # or: anthropic (+ ANTHROPIC_API_KEY, optional GI_LLM_MODEL)
uvicorn goal_intelligence.api.app:create_app --factory --port 8102
```

| Variable | Default | Meaning |
|---|---|---|
| `GI_DATABASE_URL` | — (required) | `postgresql://…` or `sqlite:///path` |
| `GI_SERVICE_TOKENS` | — (required) | `name=secret` pairs; `gateway` for public routes, `goal-management` for internal |
| `GI_LLM_PROVIDER` | `fixture` | `fixture` or `anthropic` |
| `GI_LLM_MODEL` | `claude-haiku-5-5` | model for the anthropic provider |
| `ANTHROPIC_API_KEY` | — | only for `anthropic` |
| `GI_LLM_TIMEOUT_S` / `GI_LLM_MAX_ATTEMPTS` | `20` / `3` | per-attempt timeout, bounded retries |
| `GI_MAX_MESSAGE_CHARS` / `GI_MAX_USER_TURNS` | `2000` / `20` | input and conversation bounds |

Gateway contract (for Dev 1): forward public calls with `X-Service-Token: <gateway secret>` and `X-Authenticated-User-Id: <uuid from validated JWT>`, and **strip any client-supplied copy of that header**. Never route `/internal/*` publicly.

## Tests

```bash
python scripts/validate_contracts.py && python -m pytest -q          # from repo root
```

`tests/test_gi_http.py` needs FastAPI + httpx; without them it is skipped and reported as NOT RUN.

## Handoff fixtures

`fixtures/` (regenerate with `python services/goal-intelligence/scripts/export_fixtures.py`):
ready drafts for both goal types, a collecting draft, an unsupported draft, the 409 body (for D1), and the GoalSpecs GM would assemble from the ready drafts (for D4). All are schema-checked in `tests/test_gi_fixtures.py`.

## Known gaps

- ICP-01 / ICP-08 acceptance pending; swap `auth.py` for the agreed JWT/mTLS scheme when D1 lands it.
- Postgres repository path and the FastAPI layer were not executed in the authoring environment (no packages); run the commands above before merging.
- The fixture extractor is English-only and rule-based; use the anthropic provider for free-form demo input.
- Gateway rate limiting is Dev 1's (D1-GW-01); GI only bounds message size and turns.
