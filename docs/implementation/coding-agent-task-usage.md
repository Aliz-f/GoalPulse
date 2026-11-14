# Launching Codex / Claude Code tasks without integration drift

## Canonical prompt — copy for EACH task and change only task ID

```text
You are implementing task <TASK_ID> in the GoalPulse monorepo.
Read AGENTS.md and docs/implementation/README.md first.
Read the full task entry in tasks/developer-<N>.md, all linked service
specification files, exact frozen contracts/schemas and the approved
internal API contract. Do NOT assume proposed internal APIs are approved;
check docs/implementation/DECISIONS.md and the PR/ADR status.

Before touching files, report:
- assigned owner and service;
- exact files/paths you expect to change;
- upstream and downstream dependency contracts;
- acceptance tests you will run;
- any open ICP blocker.

Implement ONLY this task in allowed paths. Use injectable mocks for services
not yet delivered. No cross-service SQL, undocumented HTTP route, invented
event, guessed price, hidden default or checkout action. Do not change
frozen v1 schemas, event routing or public OpenAPI without an approved ICP.

Run exact contract checks, pytest and task-specific tests; if Docker or
Context.dev live access isn't available, explicitly report NOT RUN.
Return: changed files, behavior delivered, test output, interface impact,
known risks, and handoff to the next task owner.
```

## Rules for concurrent coding agents

- One branch / PR per task ID. Each coding agent has exclusive write ownership of its service paths and tests. Keep shared docs/interfaces under D1-coordinated PR.
- Do not ask several agents to modify `contracts/`, `.github/`, top-level dependency lists, or `docker-compose.yml` at once.
- Build **mocks early**: D2 provides candidate response, D3 provides subscription+Observation response, D4 provides evaluation response; D1 provides GM runtime/auth response.
- Merge order for integration dependency: approved ICP → fixture provider mocks → service code+tests → consumer contract test → integrated PR. No "works only on my machine" manual DB edits.
- If a missing interface/field is found, submit an ICP with exact producer, consumer, shape, error behavior, version and migration. Do not invent an endpoint even if it makes local code easier.
- Never automatically accept a generated tool's claim of tests passing; inspect stdout, read test output and check the final diff.

## Suggested PR title

`[D4-GE-03] Deterministic product purchase constraint evaluator`

## Suggested agent review prompt

```text
Review this PR against AGENTS.md, the exact task acceptance criteria,
and frozen schemas. Check for unauthorized paths, missing tests,
cross-service DB access, invented API/event names, lost idempotency,
unknown treated as satisfied, incorrect price/shipping/tax arithmetic,
missing provenance, unauthenticated reads and any automatic purchase.
Report findings with file:line, severity and a reproducible failure case.
```
