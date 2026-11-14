# 01 — Goal Management Service (Developer 1)

**Ownership:** `services/goal-management/`, DB `goal_management`. **Criticality:** P0. **Input:** server-authenticated confirmation of a ready draft. **Output:** immutable approved `GoalSpec`, authoritative lifecycle, dashboard projection, `goal.activated.v1` and consumers of `goal.evaluated.v1`.

## Scope and invariants

Own goal identity and lifecycle only. Do **not** run LLM, search web, scrape, calculate offers, send notifications or store other services' raw datasets. No activation without a human confirmation of the **exact** draft revision; a valid `GoalDraft` is necessary but insufficient. Generate `goal_id`, `owner_user_id`, `approved_at` on server. `GoalSpec` version 1 is immutable, with lifecycle stored in separate mutable table. Guard with JWT identity and ownership. Only `delivery_country=FI` is executable in MVP under existing evidence contract.

## Public API (already frozen, MUST implement verbatim)

| Method & path | Input | Success | Failure |
|---|---|---|---|
| POST `/v1/goal-sessions/{session_id}/confirm` | `{expected_revision:int, approved:true}` | 201 strict GoalSpec | 401, 403/404 masked, 409 stale/unready/already confirmed, 422 invalid candidate |
| GET `/v1/goals?status=active...` | status filter | 200 `GoalSummary[]` | 401 |
| GET `/v1/goals/{goal_id}` | uuid | 200 latest immutable GoalSpec | 401,404 |
| PATCH `/v1/goals/{goal_id}/lifecycle` | `{action:pause|resume|cancel|complete}` | 204 | 401,404,409 invalid transition |

Exact public schema: `contracts/openapi/gateway.v1.yaml`. No public criteria edit or status/history endpoint exists there; those are **proposals** (ICP-02/07).

## Internal API (accepted under ICP-01; historical filename retained)

- `GET /internal/v1/goals/{goal_id}/versions/{version}` → exact GoalSpec; protected service credentials; returns 404 on unknown. Used by DM/GE.
- `GET /internal/v1/goals/{goal_id}/runtime` → `{goal_id,current_version,lifecycle,owner_user_id}`. Used by GE/NS/AI as authoritative final gate. Do not expose owner ID to arbitrary callers.
- `GET /internal/v1/goals/active?cursor=...` → paginated compact goal IDs/current version. Used by DM for reconciliation after pause/resume/cancel. Internal only.
- GI candidate data retrieval is **initiated by GM** using GET `/internal/v1/goal-sessions/{session_id}/candidate?expected_revision=N` with GM identity.

## Owned tables and indexes

- `goals(goal_id PK uuid, owner_user_id uuid indexed, lifecycle enum, current_version int, confirmed_session_id uuid UNIQUE, created_at, updated_at)`
- `goal_versions(goal_id uuid, version int, spec jsonb, approved_at timestamptz, approved_draft_revision int, PRIMARY KEY(goal_id,version))` — immutable rows; schema check at write.
- `goal_evaluation_projection(goal_id PK, version, evaluation_id, status, last_evaluated_at, opportunity_status, updated_at)` — consumed materialized read model, not authoritative evaluator record.
- `inbox(event_id PK, event_type, processed_at)`; `outbox(id PK, event_id UNIQUE, event_type, payload_jsonb, status, attempts, next_attempt_at, created_at)`.
- Users live in external identity provider; no independent `users` table necessary for P0. Ownership derived from validated JWT subject mapped to canonical UUID; define identity mapping once with D1 gateway.

## Business logic / pseudocode

1. On confirm: authenticate user; fetch GI candidate with `expected_revision`; ensure GI `ready_for_confirmation`, no missing fields and owner matches. Convert candidate to strict GoalSpec, enforce type-specific requirements, check `delivery_country='FI'`, persist atomically.
2. SQL unique constraint on `confirmed_session_id` blocks duplicate goals even after retries. For same session/revision, return original result if safe idempotent replay is explicitly documented; for incompatible replay return 409. Never create second version for repeated POST.
3. In DB transaction, insert `goals`, `goal_versions`, `outbox(goal.activated.v1)`. Publisher reads outbox with bounded retries, publisher confirms and delivery persistence; mark delivered only on confirmation. Request must not depend on RabbitMQ availability to commit goal.
4. Consumer `goal.evaluated.v1`: validate event schema and `producer`, durable inbox dedup, check `payload.goal_version == goals.current_version` and lifecycle active. Update projection. Never set lifecycle=completed just because evaluated satisfied.
5. Lifecycle: active→paused, active/paused→cancelled or completed, paused→active. Invalid transitions 409; terminal states immutable. Version changes deferred (ICP-07). Resumed goals need DM reconciliation until agreed lifecycle events exist (ICP-03).
6. Data minimization: list/get only for own goals; internal calls check service identity and purpose.

## Event contracts

- Produce: `goal.activated.v1` payload `{goal_id,goal_version}` (exact frozen schema).
- Consume: `goal.evaluated.v1` payload `{goal_id,goal_version,evaluation_id,status}` (projection only).
- Do **not** publish ad hoc goal pause/cancel/update routing keys in v1.

## Error mapping and reliability

GI unavailable→503 with retry guidance, no partial activation; malformed candidate→422; stale revision→409; RabbitMQ down→outbox pending (goal persists); outbox retries with backoff and DLQ/alert on exhausted retry. Process duplicate evaluation event once; old version ignored. Transaction safety test required.

## Acceptance tests

- Confirm complete exact revision returns 201 and strict schema; `goal.activated` delivered eventually once logically.
- Incomplete/stale/foreign draft returns 409/404; no goal or monitor activated.
- Simultaneous same-session confirmations yield exactly one DB goal and one logical activation.
- Verified evaluation updates projection without completing goal; stale-version event ignored.
- Pause suppresses downstream notification via downstream runtime recheck; resume discoverable via reconciliation.
- List/get enforce owner even if `goal_id` guessed; database tests validate immutable `goal_versions`.
- RabbitMQ unavailable on confirmation leaves durable pending outbox; replay recovers.

**Task IDs:** `D1-GM-01`..`D1-GM-05` in `tasks/developer-1.md`.
