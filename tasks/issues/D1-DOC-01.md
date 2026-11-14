# D1-DOC-01 — Demo/deployment runbook and decision ledger

**Owner:** Developer 1  
**Priority:** P1  
**Status:** TODO  
**Dependencies:** D1-INT-01

## Mandatory preflight

1. Read `AGENTS.md`, `docs/implementation/README.md` and the exact owner task section in `tasks/developer-1.md`.
2. Read your relevant service specification in `docs/implementation/0N-*.md` and the specific frozen schemas/events/OpenAPI touched by the task.
3. Verify any relevant `contracts/openapi/*.proposed*.yaml` has an accepted ICP/ADR. A proposal is **not** implementation authority.
4. Check `git status` and another developer's work before editing.

## Objective and concrete work

one-command startup, env var inventory no secrets, diagram ports, healthcheck, contract approval records, fallback fixture demo.

## Allowed write scope

`docs/`, `README.md`, approved configuration.

No changes to other services, existing `contracts/*.v1`, event routing, shared infrastructure or root dependencies unless a separately approved coordinated PR explicitly permits them.

## Acceptance criteria

another team member follows runbook on clean machine and independently completes first end-to-end scenario.

## Required tests and handoff

```bash
python scripts/validate_contracts.py
python -m pytest -q
```

Add task-specific unit/integration tests; test failure paths, idempotency, ownership and stale data where applicable. If Docker or real Context.dev wasn't available, say **NOT RUN** rather than claiming passing tests.

At the end, report changed files, test output, event/API/schema impacts, migrations and new env vars, remaining risks and exactly which downstream task can now proceed.
