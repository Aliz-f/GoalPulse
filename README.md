# GoalPulse — Phase 1: Architecture and Contracts

**Status:** design + contracts + local infrastructure starter. This package is **not** a running GoalPulse application. Service implementations begin in Phase 2.

## Full implementation plan for four developers (design only)

- [`docs/implementation/README.md`](docs/implementation/README.md) — complete boundaries and technical decisions for all seven services.
- [`tasks/MASTER_BACKLOG.md`](tasks/MASTER_BACKLOG.md) — gates, dependency graph and task order. 44 copy/paste-ready task briefs live in `tasks/issues/`, with machine-readable `tasks/task-index.yaml`.
- [`tasks/developer-1.md`](tasks/developer-1.md), [`developer-2.md`](tasks/developer-2.md), [`developer-3.md`](tasks/developer-3.md), [`developer-4.md`](tasks/developer-4.md) — owner-specific task IDs, allowed paths, dependencies and acceptance criteria.
- [`docs/implementation/coding-agent-task-usage.md`](docs/implementation/coding-agent-task-usage.md) — Codex/Claude task prompts and review checklist.
- [`contracts/openapi/internal.proposed.v1.yaml`](contracts/openapi/internal.proposed.v1.yaml) — accepted private API contract (the historical filename is retained); see ADR-0002. Public v1 and four event routes stay frozen.
- [`docs/implementation/12-contract-gaps-and-decisions.md`](docs/implementation/12-contract-gaps-and-decisions.md) — interface changes requiring team signoff.

The implementation blueprint does NOT mean these seven microservices are implemented. ICP-01 and ICP-08 are accepted as recorded in ADR-0002; all other proposed changes still require their stated approvals.

## Quickstart

```bash
cp .env.example .env
# Change development passwords in .env (never commit it)
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d
python -m pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python -m pytest -q
```

Docker commands require a working Docker Engine + Compose plugin. See `docs/phase-1-checklist.md` for exact team steps.

- PostgreSQL: `127.0.0.1:5432` (DEV ONLY)
- RabbitMQ: `127.0.0.1:5672` (DEV ONLY)
- RabbitMQ Management: `http://127.0.0.1:15672` (DEV ONLY)
- DBs: one **database** per service in a shared local PostgreSQL instance. Development bootstrap uses a shared admin password for initial connectivity only; services must receive their own scoped DB credentials before deployed beyond local development.

## Coding-agent and collaboration rules

- **Codex and other agents:** read the root [`AGENTS.md`](AGENTS.md) before editing code.
- **Claude Code:** the root [`CLAUDE.md`](CLAUDE.md) imports `AGENTS.md`, keeping **one shared policy**.
- Each assigned task should use [`docs/agent-task-template.md`](docs/agent-task-template.md) with explicit allowed paths and acceptance criteria.
- Pull requests should use [`.github/pull_request_template.md`](.github/pull_request_template.md).
- Team leads should follow [`docs/team-onboarding.md`](docs/team-onboarding.md) to enable branch protection and reviews; instruction files alone are not enforcement.
- Contract or cross-service changes require Developer 1 and affected-owner review; see [`docs/adr/0001-microservices-contracts.md`](docs/adr/0001-microservices-contracts.md).

## Contract sources of truth

| File | Owner / purpose |
|---|---|
| `contracts/schemas/goal-spec.v1.schema.json` | Immutable confirmed goal | 
| `contracts/schemas/goal-draft.v1.schema.json` | In-progress LLM conversation outcome |
| `contracts/schemas/observation.v1.schema.json` | Source observation, not necessarily verified |
| `contracts/schemas/goal-evaluation.v1.schema.json` | Deterministic evaluation and reasons |
| `contracts/schemas/domain-event.v1.schema.json` | RabbitMQ event envelope and typed payloads |
| `contracts/openapi/gateway.v1.yaml` | External API contract only, not implemented |
| `docs/architecture.md` | Boundaries, ownership, event flow, state machine |
| `docs/phase-1-checklist.md` | Ownership, sequencing, exit criteria |

## Scope constraints

- MVP supports only `product_purchase` and `recurring_procurement`, not arbitrary executable goals. New goal domains require a typed validator + evaluator.
- Money is integer **EUR cents**. Do not use floats for financial decision logic.
- `unknown` is **not** `satisfied`.
- `opportunity_verified` means conditions were evidenced at observation time. It does not mean a purchase was placed or a trip completed.
- No automatic purchase / transfer. Handoff is **prepare_only**.
- Context.dev availability and extraction quality must be tested independently; it is not a guaranteed commerce inventory API.

## Reference docs

- Context.dev: https://docs.context.dev/quickstart ; https://docs.context.dev/monitors/overview
- RabbitMQ reliability: https://www.rabbitmq.com/docs/confirms
