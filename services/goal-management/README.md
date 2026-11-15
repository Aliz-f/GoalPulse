# goal-management — GoalPulse service

**Implementation status: Phase 2 Goal Management runtime implemented.** The
following documents remain the development specification; only the implemented
files and tests define current behavior.

- [Service specification](../../docs/implementation/01-goal-management.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` is accepted under ADR-0002 despite its historical filename; do not guess interfaces or read other services' databases.

## Runtime processes

Run these as separate processes from the repository root with
`services/goal-management/src` on `PYTHONPATH`:

```bash
python -m goal_management.migrate
uvicorn goal_management.runtime:create_app --factory --host 0.0.0.0 --port 8000
python -m goal_management.worker
python -m goal_management.consumer
```

The API exposes `/health/live` and dependency-aware `/health/ready`. The latter
requires both the owned PostgreSQL database and Goal Intelligence health check.
The worker publishes the transactional activation outbox with publisher
confirms. The consumer manually acknowledges `goal.evaluated.v1` only after
durable deduplication and current-version projection.

Required configuration:

- `GOAL_MANAGEMENT_DATABASE_URL`
- `GOAL_INTELLIGENCE_URL`
- `RABBITMQ_URL` (publisher and consumer)
- `GATEWAY_DEV_JWT_SECRET`, `GATEWAY_JWT_ISSUER`, `GATEWAY_JWT_AUDIENCE`
- `SERVICE_JWT_SECRET`, `SERVICE_JWT_ISSUER`, `SERVICE_JWT_AUDIENCE`
- `GOAL_MANAGEMENT_INTERNAL_TIMEOUT_SECONDS` (optional, default `5`)

Goal summaries project the evaluation for the current immutable goal version.
The frozen `GET /v1/goals/{goal_id}` contract remains an exact `GoalSpec`; the
additive Phase 2 evaluation-detail route is owned by Goal Evaluation and is not
silently embedded into this response.

The optional PostgreSQL acceptance test requires an explicitly disposable,
already-migrated database:

```bash
GOAL_MANAGEMENT_TEST_DATABASE_URL=postgresql://... \
  python -m pytest -q services/goal-management/tests/test_goal_management_postgres_live.py
```
