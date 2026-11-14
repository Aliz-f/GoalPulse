# 06 — Notification Service (Developer 4)

**Ownership:** `services/notification/`, DB `notification`. **Criticality:** P0 for in-app, P2 for email/push. Sole service responsible for user-facing notifications.

## Input and output

- Consume `goal.opportunity_verified.v1` from RabbitMQ; its v1 payload contains only `{goal_id,goal_version,evaluation_id}`.
- Retrieve evaluation from GE and goal current runtime/owner from GM through authenticated internal HTTP; do not trust event alone for active/latest status.
- Create deduplicated **in-app** notification for verified opportunity. Deliver via frontend retrieval after approved additive public API (ICP-02), optionally SSE later. Email optional, default off until confirmed address and consent.
- Never send a purchase request, mark goal completed, expose seller secrets or notify another user's account.

## Owned DB

- `preferences(user_id PK, in_app_enabled bool default true, email_enabled bool default false, created_at, updated_at)`.
- `notifications(id PK, user_id, goal_id,goal_version,evaluation_id,offer_fingerprint, title, summary, action_package_id nullable, created_at, read_at nullable, status)`.
- `deliveries(id PK, notification_id FK, channel, state, attempts, next_retry_at, provider_message_id nullable)`.
- `inbox(event_id PK, processed_at)`, `outbox` only if later notification channel requires async bridge.
- UNIQUE `(user_id, goal_id, goal_version, offer_fingerprint, channel)` or equivalent dedup enforced at DB level.

## Algorithm

1. Consume event, strict validate, inbox dedup.
2. GET current GM runtime + GE evaluation. Reject if paused/completed/cancelled, stale version, evaluation not strictly verified, missing evidence.
3. Compute deterministic normalized fingerprint from immutable evidence + seller + product + goal version and agreed policy (ICP-10); avoid using event_id as only dedup.
4. Persist notification atomically with durable inbox. A repeated event yields same notification ID or no-op.
5. Return notification via protected user-owned read path; the public read endpoint is not yet in the frozen Gateway contract, so approve ICP-02 before exposing it. For first mocked vertical slice D2 can view it via approved dev fixture (not unprotected admin endpoint).
6. Optional email requires user opt-in, validated recipient and safe destination; retries are idempotent, bounded.

## HTTP and security

- Proposed public `GET /v1/notifications` and `PATCH /v1/notifications/{id}/read` are **additions** needing approval (ICP-02). If team defers, implement in-app feed behind approved gateway contract extension.
- All user reads filtered by JWT-derived owner. Notification channel secrets remain server-side; sanitize URLs/text from external sources.

## Acceptance tests

- One valid verified event for active latest goal creates one user-owned notification.
- Duplicate RabbitMQ deliveries/replayed evaluation cause no duplicate notifications.
- Paused/cancelled/stale-version goal or `unknown` evaluation causes no notification.
- Read-list and mark-read reject other users; no email without opt-in.
- In-app notification contains title, goal ID, evaluated time, evidence link (if safe) and clarification that offer may change at checkout.
- Delivery failure persists retry metadata and doesn't corrupt goal data.

**Task IDs:** `D4-NS-01`..`D4-NS-03`.
