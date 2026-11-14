# Cross-service quality gates, test plan and Definition of Done

## Required gates (both developers and coding agents)

1. **Contract**: `python scripts/validate_contracts.py`; `python -m pytest -q`. Accepted internal OpenAPI must also pass YAML+OpenAPI structure validation; Pydantic tests against JSON fixtures.
2. **Service unit**: pure business logic without network or DB; tests for invalid/missing data, deterministic calculations, decimal rounding, authorization and stale revision.
3. **Service integration**: isolated owned database migrations, read/write, outbox+inbox, RabbitMQ publisher/consumer fixture, authenticated internal mock server; no other service's DB.
4. **Cross-service mock E2E**: user message→draft→confirm→activated event→fixture evidence→evaluation→notification/package. Must pass when Context.dev disconnected.
5. **Live provider smoke**: successful Context.dev scrape of explicitly selected public allowed product page. Save redacted reproducible report (request ID, source, observed timestamp, success per requested output, omissions).
6. **Monitoring**: provider monitor baseline works; signed webhook triggers recheck; provider duplicate delivery does not cause duplicate offer.
7. **Security**: JWT auth/ownership, service auth internal, webhook signature, SSRF, prompt injection, secret leak scan, protected env files.
8. **Resilience**: broker down after GM commit, stale event after pause, webhook replay, 429/5xx, cache age, missing tax/shipping. Durable recovery and safe suppression.
9. **Demo freeze**: no critical failures; if live source changes unavailable, use separate explicitly labelled synthetic injection to demonstrate reaction, with at least one independently proven live provider extraction.

## Exact acceptance matrix

| Case | Expected result | Test owner |
|---|---|---|
| Invalid product goal / missing budget | GI asks a question; confirm disabled | D2 |
| Session revision stale | 409, no activation | D1+D2 |
| Two concurrent confirms | one goal/version/outbox intent | D1 |
| User B reads User A data | 404 or 403, no disclosure | D1+D2+D4 |
| Rabbit down during goal confirm | goal persists + outbox retry; eventual activation | D1 |
| Duplicate `goal.activated` | one logical link/monitor per source | D3 |
| Provider scrape returns `success:false` | failure record, no fake Observation | D3 |
| Source price unknown | no Observation (v1 requires integer price) | D3 |
| Stock / shipping / tax unknown | GE `unknown`, no verified opportunity | D4 |
| Wrong GPU model or condition | GE `unsatisfied`, no verified opportunity | D4 |
| Price + shipping + verified tax above cap | GE `unsatisfied`, no notification | D4 |
| Source cache older than goal's age | GE `unknown`, no verified opportunity | D3+D4 |
| Multi-seller café basket | full coverage, unit normalization, shipping per seller once | D3+D4 |
| Pause while delayed event in-flight | GE/NS/AI suppress from GM runtime | D1+D3+D4 |
| Replay same verified event | one notification and one package | D4 |
| Repeated same offer fresh observation | dedup by offer fingerprint according to policy | D4 |
| Agent package GET by another user | denied | D1+D4 |
| Request auto purchase | rejected; always prepare-only | D2+D4 |
| Simulated demo data | flagged fixture, never claimed live | Entire team |

## Contract tests and fixtures

- Keep `contracts/examples/*.json` frozen. Add new fixtures under the owning service `tests/fixtures/`, and only change `contracts/` via reviewed PR.
- Agent tests must demonstrate at least one positive, one negative and one unknown path for every evaluator.
- Consumer-driven contract tests: DM subscription JSON consumed by GE; DE observations consumed by GE/AI; GE evaluation consumed by GM/NS/AI; GI candidate consumed by GM.
- Version, correlation ID, immutable goal ID and evidence source provenance must survive across hop boundaries.
- Inject fake Context.dev adapter; never use live API keys in CI unit tests.

## Minimum observability

- Structured JSON logs: `service`, `trace_id`/`correlation_id`, `goal_id`, `goal_version`, `event_id`, `source_id`, `request_id`, `status`, latency. No secrets/PII/raw scraped HTML.
- Health: `/health/live` no downstream requirement and `/health/ready` tests owned DB/broker/required dependency state; don't claim ready if dependencies broken. Prometheus metrics optional for 48h MVP.
- Count: `goals_activated_total`, `scrapes_succeeded_total`, `scrapes_failed_total`, `evaluations_total{status}`, `opportunities_verified_total`, `notifications_delivered_total`, `duplicate_events_total`, `provider_credits_estimate`.
- Correlation ID propagation on every HTTP/event hop; inspect one goal's full path in logs.

## Task Definition of Done (DoD)

**Every task PR requires:** task ID + owner, paths changed in authorized scope, intended behavior, contract reference, tests run/results, failure modes, migrations/ENV additions, evidence of acceptance checks, and explicit integration handoff. Do not claim green Docker/integration tests if not run. Never merge without reviewer(s) for protected interfaces.

**Release DoD:** MVP runs from README local setup; 2 supported goal types can be constructed and evaluated (live product as P0, café basket as P1), no undocumented cross-service DB access; all P0 security gates pass; per-source live evidence and user-facing no-autobuy disclaimer; repeatable demo flow.
