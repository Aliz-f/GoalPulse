# Developer 1 — Platform, Gateway and Goal Management

**Role:** Tech lead and integrator. **Services:** `goal-management`, Gateway/infra. **Owned paths:** `services/goal-management/`, `infrastructure/`, approved gateway path, protected files **only after team signoff**. **Read first:** root `AGENTS.md`, `docs/implementation/{01-goal-management,08-frontend-gateway,09-dependencies-workflows,10-internal-contracts,11-testing-and-acceptance,12-contract-gaps-and-decisions}.md`, frozen GoalSpec/OpenAPI/Event schemas. Never implement another owner's business logic just to unblock integration; provide mocks.

## Gate A — contracts and bootstrap (before parallel integration)

### D1-PL-01 — Review and freeze cross-service internal API proposal [P0, FIRST]
- **Dependencies:** none; do this with D2, D3, D4 for review.
- **Allowed files:** `docs/adr/`, `docs/implementation/DECISIONS.md`, `contracts/openapi/` in a separately approved PR, tests for contracts.
- **Do:** review `internal.proposed.v1.yaml`, confirm principal/auth model, failure responses, GETs/POSTs, event naming; file ICP-01/08; collect affected-owner approvals; publish an accepted contract artifact and machine-readable examples. Do not mutate frozen existing v1.
- **Acceptance:** every consumer can generate a mock request/response from same accepted spec; service identity rules and consent are documented; contract checks pass.

### D1-PL-02 — Local platform, DB isolation and RabbitMQ queues [P0]
- **Dependencies:** D1-PL-01 design signoff on auth/transport (can scaffold beforehand).
- **Allowed files:** `infrastructure/`, root approved env examples, `services/goal-management/` infra test config.
- **Do:** Docker Compose startup for RabbitMQ and PostgreSQL; separate logical DB and per-service least-privileged credentials (no shared production admin); configure exchange `goalpulse.events`, four queues and DLQ policy, durable messages, publisher confirms, manual ACK; add health/runbook.
- **Acceptance:** `docker compose ... config/up/ps` healthy on team machine; each service role denied SQL access to other DBs; published fixture routes to correct consumer queue; re-delivery observable.

### D1-PL-03 — JWT authentication and service-to-service authorization [P0]
- **Dependencies:** D1-PL-01 (ICP-08), can run alongside PL-02.
- **Allowed files:** approved Gateway path, `infrastructure/`, security test files.
- **Do:** select external issuer or signed dev test issuer; validate issuer/audience/signature/expiry; map claims to canonical owner UUID; authenticate internal services separately and permit only their scoped methods; correlation IDs.
- **Acceptance:** invalid/expired JWT=401, forged user header ignored, unauthenticated internal read forbidden, test user B cannot access user A goal.

## Gate B — service implementation (parallel with other developers)

### D1-GM-01 — Goal DB tables and migration [P0]
- **Dependencies:** D1-PL-02, frozen GoalSpec.
- **Allowed files:** `services/goal-management/`.
- **Do:** service app skeleton, Alembic or chosen migration tooling by approved choice, tables `goals`, `goal_versions`, `projection`, `inbox`, `outbox`, constraints and repositories.
- **Acceptance:** up/down reversible migration in isolated dev DB, unique confirmed_session_id and `(goal_id,version)` enforced, repo tests pass.

### D1-GM-02 — Authenticated internal GoalSpec/runtime read API [P0]
- **Dependencies:** D1-PL-01, D1-PL-03, D1-GM-01.
- **Allowed files:** `services/goal-management/`.
- **Do:** implement accepted `/internal/v1/goals/{id}/versions/{v}`, `/runtime`, `/active` pagination; strict DTO and service auth.
- **Acceptance:** fixture GoalSpec loads by version; runtime active/paused correct; DM/GE mock consumer contract tests pass; 403 on unauthorized M2M.

### D1-GM-03 — Confirm draft revision & atomic activation outbox [P0]
- **Dependencies:** D1-GM-01, D1-PL-03, accepted GI candidate interface; coordinate with D2-GI-04.
- **Allowed files:** `services/goal-management/`.
- **Do:** public POST confirm handler; fetch GI ready candidate, compare owner/revision, deterministic construction/validation of GoalSpec, immutable storage + outbox in single DB transaction, concurrent idempotency by session ID.
- **Acceptance:** valid confirm=201 + outbox event; stale/incomplete=409, invalid=422, foreign session=404; two concurrent confirms create one goal; RabbitMQ outage doesn't lose activation.

### D1-GM-04 — User Goal read/list and lifecycle [P0]
- **Dependencies:** D1-GM-01, D1-PL-03.
- **Allowed files:** `services/goal-management/`.
- **Do:** frozen `GET /v1/goals`, `GET /v1/goals/{id}`, `PATCH .../lifecycle` with allowed state transition matrix; optimized owner filtering.
- **Acceptance:** 200/204, valid pagination/filter behavior per frozen contract, invalid terminal transition=409; cross-user access denied.

### D1-GM-05 — Outbox publisher and `goal.evaluated` projection consumer [P0]
- **Dependencies:** D1-GM-01, D1-PL-02.
- **Allowed files:** `services/goal-management/`.
- **Do:** publish exact `goal.activated.v1` envelope with publisher confirm, retry; process `goal.evaluated.v1` with inbox dedup; update current-version projection only.
- **Acceptance:** duplicate events safe, old version ignored, persisted outbox eventually delivered after broker restart, never marks goal completed automatically.

### D1-GW-01 — Public API Gateway routing, errors and security [P0]
- **Dependencies:** D1-PL-03, frozen gateway OpenAPI and service mock ports.
- **Allowed files:** approved Gateway path, `infrastructure/`, Gateway tests.
- **Do:** route exactly seven frozen public operations to GI/GM; enforce JWT/CORS/body limits, propagate request IDs, no internal endpoint exposure.
- **Acceptance:** all seven documented methods route correctly through mocks; invalid bearer=401; private internal paths=404/403; no Context.dev key in gateway.

## Gate C–D — integrated behavior and hardening

### D1-INT-01 — First mock browser-to-notification vertical slice [P0]
- **Dependencies:** D1-GM-03/05, D1-GW-01, D2-GI-04, D2-FE-01/02, D3-DM-02, D4-GE-03 and notification mock.
- **Allowed files:** `infrastructure/`, integration test directory, plus cross-owner files only via coordinated reviewed PR.
- **Do:** run full stack with fixture extraction→DE/GE→notification; automate smoke test with fixed UUIDs; one command from README.
- **Acceptance:** browser/HTTP path makes approved goal and verified fixture opportunity, one in-app notification; no manual DB edits; reproducible from clean boot.

### D1-INT-02 — Cross-service failure/security test and release automation [P0]
- **Dependencies:** D1-INT-01 and live integration.
- **Allowed files:** `infrastructure/`, `.github/` with approval, cross-service `tests/integration/`.
- **Do:** CI contract check + service test commands, broker-down recovery, replay, stale-version/paused-suppression, security header/token checks, logs/correlation; record NOT RUN for unavailable Docker.
- **Acceptance:** acceptance matrix from `11-testing-and-acceptance.md` for P0 all green; no secret leaks; repeatable README; freeze release candidate.

### D1-DOC-01 — Demo/deployment runbook and decision ledger [P1]
- **Dependencies:** D1-INT-01; can write skeleton earlier.
- **Allowed files:** `docs/`, `README.md`, approved configuration.
- **Do:** one-command startup, env var inventory no secrets, diagram ports, healthcheck, contract approval records, fallback fixture demo.
- **Acceptance:** another team member follows runbook on clean machine and independently completes first end-to-end scenario.

## Your code-agent task boundary

> Implement only the assigned D1 task; list dependencies, allowed paths, expected published/consumed contracts and tests before coding. Never alter frozen `contracts/*.v1` silently. For task completion: PR with test outputs, migrations, new variables, explicit handoff to affected service owners.
