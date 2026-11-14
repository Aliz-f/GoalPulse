# GoalPulse — Phase 2 Implementation Blueprint

**Status:** Design specification / proposed implementation plan. **Not** a claim that services are implemented. This design is additive to, and subordinate to, the approved `contracts/` baseline and root `AGENTS.md`. New interfaces identified as PROPOSED must be approved by Developer 1 and affected owners before code relies on them.

**Audience:** Four developers and their Codex / Claude Code coding agents. **MVP:** natural-language goal definition → explicit approval → active goal → live Context.dev evidence → deterministic evaluation → user notification → **prepare-only** agent payload. Goal types: `product_purchase` (first) and `recurring_procurement` (second). Travel and arbitrary goal types are *not executable* in v1.

## Non-negotiable engineering decisions

| Concern | Decision |
|---|---|
| Repo & services | Monorepo, seven independently deployable Python/FastAPI services; gateway is infrastructure, frontend is Next.js. Worker processes may share service code/image but no other service DB. |
| Auth | Gateway validates external JWT; internal HTTP uses authenticated service-to-service credentials; goal ownership always from validated identity. No public internal endpoints. |
| Broker | RabbitMQ topic exchange `goalpulse.events`; the **four existing routing keys** and exact `domain-event.v1` payloads are frozen. Durable per-consumer queues, manual ACK, publisher confirms, inbox+outbox and DLQ. |
| Persistence | Seven logical PostgreSQL databases, one owner each; migrations are service-owned. Locally may share Postgres instance. |
| Money/time | Integer EUR cents at external boundaries; exact decimal internal calculations for fractional kg/L; UTC, timezone-aware RFC3339 timestamps; no assumed FX or tax. |
| Goal types | `product_purchase` first, `recurring_procurement` next. Unsupported requests return unsupported/clarification, never secretly coerced. |
| Satisfaction | `satisfied`, `unsatisfied`, `unknown` per condition. `unknown` blocks verified opportunity. Verified opportunity ≠ purchase/booking/completed goal. |
| LLM | Drafting and semantic candidate suggestions only; schema and business validators govern readiness, and human approval is required. Provider text cannot instruct agents. |
| Context.dev | Only Discovery & Monitoring owns API key, provider monitor IDs and webhook secrets. Baseline is not change; initial scrape produces initial observation. Cached results are not falsely fresh. |
| Purchase | No financial execution; `agent_handoff=prepare_only`, `auto_purchase=false`. |
| CI | Contract checks, unit tests, per-service tests, broker/integration tests where available, protected PR review. |

## Canonical components and responsibilities

1. [Goal Management](01-goal-management.md) — Developer 1: user confirmation, immutable goal versions, lifecycle, dashboard projection.
2. [Goal Intelligence](02-goal-intelligence.md) — Developer 2: conversational clarification, draft revision, validated canonical candidate.
3. [Discovery & Monitoring](03-discovery-monitoring.md) — Developer 3: source discovery, subscriptions, Context.dev API, monitor/webhook lifecycle.
4. [Data & Evidence](04-data-evidence.md) — Developer 3: normalize/store observable facts, provenance, evidence query and event publication.
5. [Goal Evaluation](05-goal-evaluation.md) — Developer 4: deterministic product and recurring procurement evaluators, evaluation persistence, verified opportunities.
6. [Notification](06-notification.md) — Developer 4: in-app notifications, dedup; email optional.
7. [Agent Integration](07-agent-integration.md) — Developer 4: read-only evidence-backed handoff packages; never executes purchases.
8. [Frontend, Gateway, Identity](08-frontend-gateway.md) — Developer 1 Gateway / Developer 2 Frontend.
9. [Cross-service workflows, dependency/sequence, blockers](09-dependencies-workflows.md).
10. [Internal HTTP contracts](10-internal-contracts.md) plus *proposed* formal OpenAPI: `contracts/openapi/internal.proposed.v1.yaml`.
11. [Test matrix, reliability and definition of done](11-testing-and-acceptance.md).
12. [Changes requiring approval](12-contract-gaps-and-decisions.md).

## Directories and ownership

| Owner | Write scope | Protected/shared paths |
|---|---|---|
| Dev 1 | `services/goal-management/`, `infrastructure/`, gateway if created | `contracts/`, `.github/`, ADRs, root config (coordinate with all) |
| Dev 2 | `services/goal-intelligence/`, `frontend/` | No edits to Dev 1/3/4 sources |
| Dev 3 | `services/discovery-monitoring/`, `services/data-evidence/` | No edits to GoalSpec or event routes without approval |
| Dev 4 | `services/goal-evaluation/`, `services/notification/`, `services/agent-integration/` | No edits to source registry / intelligence persistence |

See task ledger `tasks/MASTER_BACKLOG.md`, `tasks/task-index.yaml`, 44 individual agent-ready briefs under `tasks/issues/`, and `tasks/developer-{1,2,3,4}.md`. See `docs/implementation/48H-EXECUTION.md` for time-boxed prioritization. All task descriptions mandate **allowed paths, dependencies, acceptance checks**. The team may start non-conflicting tasks in parallel **after** interface signoff.

## Source-of-truth and contract freeze

- **FROZEN:** `contracts/schemas/*.v1.schema.json`, `contracts/openapi/gateway.v1.yaml`, `contracts/event-routing.yaml` and exact examples. Do not silently change these because prose in this implementation design is more detailed.
- **PROPOSED, NEEDS SIGNOFF:** `contracts/openapi/internal.proposed.v1.yaml`; additive public APIs for history/notifications/action packages in `contracts/openapi/gateway.extensions.proposed.v1.yaml`, documented in `12-contract-gaps-and-decisions.md`. Before implementation of these interfaces, file an interface change proposal and approve with Dev 1 and consumers.
- **Internal request models:** Until a draft internal OpenAPI is approved, consumers use explicit mock adapters from `10-internal-contracts.md` and do not guess paths or payload shape.
- `GoalSpec` currently permits non-FI delivery countries but `Observation.v1` records only `delivery_to_fi`. **MVP product verification is restricted by business validation to `delivery_country=FI`** until a future compatible contract version adds multi-country delivery evidence.
- `Observation.v1` requires a numeric unit price. Failed extractions/price-missing pages are logged as an `extraction_attempt` and **must not** be coerced into a fake Observation.
- Goal amendment by user, pause/resume reconciliation and extended UI reads need explicit designs; see blocker list. Avoid fabricating new v1 event names.

## End-to-end MVP success criteria

A non-technical reviewer can: submit incomplete goal → answer clarification(s) → inspect the exact canonical summary → approve revision → see active goal → trigger initial real scrape for an allowed source → inspect dated source-backed observation → see conditions/unknowns → get **one** in-app notification on verified offer → inspect a safe JSON handoff. A stale draft confirmation must return 409; replayed webhook or RabbitMQ message must not cause duplicate user-visible effects; stale goal version or missing delivery/tax information cannot verify a match.

## Evidence provider note

Official sources: https://docs.context.dev/quickstart and https://docs.context.dev/monitors/overview . Scrape output may be unsuccessful even for HTTP 200; monitor creation first records baseline and emits no change for that initial snapshot. Verify signature, server-side keys, time and cache provenance.
