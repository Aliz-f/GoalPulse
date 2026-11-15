# Internal API integration contracts — accepted under ICP-01

The frozen `contracts/openapi/gateway.v1.yaml` covers **public** endpoints only. The system cannot integrate correctly without internal authenticated reads. `contracts/openapi/internal.proposed.v1.yaml` is the accepted machine-readable internal contract under ADR-0002; its historical filename is retained for compatibility.

## Rules common to every internal endpoint

- Private service network; **machine-to-machine authentication** with per-service identity and explicit permissions. `X-User-Id` alone is never authentication.
- All IDs UUIDs, timestamps UTC RFC3339, JSON `Content-Type`, explicit DTO, bounded pagination, 200/201/202/204 documented; failures 400/401/403/404/409/422/429/503 as appropriate.
- Pass `X-Correlation-ID` UUID across request chain; never expose raw provider secrets, session transcripts, full scraped HTML or unfiltered user data.
- Idempotency required for writes: stable idempotency key (client request id/source attempt id), unique DB constraint and safe replay semantics.
- Requests are versioned `/internal/v1/`; no cross-service DB access. External user routes remain Gateway-only.

## GI ↔ GM: candidate consistency

- GM GET `/internal/v1/goal-sessions/{session_id}/candidate?expected_revision=2`: GI returns strict `GoalDraft.v1`, but only to GM with authorized service token. Must be ready and exact revision; stale returns 409.
- GM POST `/internal/v1/goal-sessions/{session_id}/mark-confirmed` with `{goal_id, expected_revision}`: GI marks state confirmed idempotently. Failure does **not** roll back already committed GM goal; retried by reconciliation.
- GM validates that `owner_user_id` from GI equals authenticated end-user principal, and builds GoalSpec itself.

## GM → DM/GE/NS/AI: source of truth

- GET `/internal/v1/goals/{goal_id}/versions/{version}`: immutable `GoalSpec.v1`.
- GET `/internal/v1/goals/{goal_id}/runtime`: `{goal_id, owner_user_id, current_version, lifecycle}`.
- GET `/internal/v1/goals/active?cursor=&limit=`: active goal ID/version pages used for source subscription reconciliation. Rate-limit and avoid leaking owner details.
- These **runtime** reads must be made at verification/notification time to reject paused or obsolete goal versions.

Goal Management authorizes these operations with separate service-token scopes:

| Scope | Allowed operation | Intended consumers |
|---|---|---|
| `gm.goal-version.read` | Immutable version read | Discovery & Monitoring, Goal Evaluation |
| `gm.runtime.read` | Current lifecycle/version read | Discovery & Monitoring, Goal Evaluation, Notification, Agent Integration |
| `gm.active.read` | Active-goal reconciliation page | Discovery & Monitoring |

A valid service UUID subject is required in addition to the operation scope;
the generic `gm.read` scope grants no access.

## DM → GE: source fan-out

- GET `/internal/v1/sources/{source_id}/subscriptions`: `{source_id, subscriptions:[{goal_id,goal_version}]}`.
- ADR-0005 additive extension: GET `/internal/v1/goals/{goal_id}/versions/{version}/sources`
  returns the complete UUID-sorted set of at most three active source IDs for an
  exact DM-known activation version. See `contracts/openapi/internal.goal-sources.v1.yaml`.
- Do not trust this alone for active status; DM may be temporarily behind lifecycle change, and GM remains authoritative.
- GET `/internal/v1/sources/{source_id}`: `{source_id,source_url,canonical_url,hostname}` only (no provider secret).

## DM → DE: ingestion

- POST `/internal/v1/extractions`: `ExtractionSubmission` source_id, source_url, provider `context_dev`, method `search|scrape|monitor`, provider request ID, `fetched_at`, optional `cache_age_ms`, optional extracted structured product data with nullable unknown fields, short source snippet and correlation UUID.
- Respond `201 {accepted:true,observation_id}` if normalized strict Observation saved, or `202 {accepted:false,reason}` if price is unavailable and only extraction attempt was stored. Duplicate attempt returns same durable result. Never silently set missing price/tax/shipping to zero.
- DE is responsible for applying normalized `Observation.v1` schema and publishing `evidence.updated.v1` from outbox.

## DE → GE/AI: evidence read

- GET `/internal/v1/observations/{observation_id}` → exact frozen `Observation.v1`.
- GET `/internal/v1/observations?source_id=...&limit=...` → latest strict Observations. GE merges valid latest observations by product/seller and checks timestamps.

## GE → NS/AI: decision read

- GET `/internal/v1/evaluations/{evaluation_id}` → exact frozen `GoalEvaluation.v1` with per-condition reasons and candidate observation IDs.

## AI → other internal consumers: package read

- GET `/internal/v1/action-packages/{package_id}` → typed immutable prepare-only package with validity window. Only internal service with allowed role, not public by default.

## Approval checklist

- [x] Dev 1 verifies JWT principal and service-auth model.
- [x] Dev 2 confirms candidate read/revision and frontend error model.
- [x] Dev 3 confirms source fan-out and extraction DTO can carry missing price without fake Observation.
- [x] Dev 4 confirms evaluation/notification/agent read interfaces and stable offer dedup.
- [x] Developer 1 approved `contracts/openapi/internal.proposed.v1.yaml`; ADR-0002 and contract tests record the acceptance.
