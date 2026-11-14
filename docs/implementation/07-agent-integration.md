# 07 — Agent Integration Service (Developer 4)

**Ownership:** `services/agent-integration/`, DB `agent_integration`. **Criticality:** P1. Exports a typed, read-only evidence-backed action package. It **never** executes checkout or connects to seller credentials.

## Inputs and outputs

- Consume `goal.opportunity_verified.v1` with goal ID/version/evaluation ID. Retrieve current runtime from GM, evaluation from GE, and Observations from DE with service-authenticated HTTP.
- Build immutable package for an *external* client/agent: requested goal, chosen offer/basket, quantity, price components, source links, timestamp, freshness/policy, conditions verified, and next action `request_user_approval`.
- Expose package to the **owning user** through signed-off Gateway read endpoint. Outbound webhook to an external agent is deferred unless explicitly configured with authenticated endpoint and user consent. Do not expose arbitrary provider credentials/payment data.

## Proposed package model (internal; not frozen v1 contract)

```json
{
  "package_id": "<uuid>",
  "schema_version": 1,
  "goal_id": "<uuid>",
  "goal_version": 1,
  "evaluation_id": "<uuid>",
  "owner_user_id": "<server-identity-uuid>",
  "created_at": "2026-11-14T10:00:00Z",
  "expires_at": "2026-11-14T11:00:00Z",
  "action_type": "prepare_purchase",
  "approval_required": true,
  "auto_execute": false,
  "seller_offers": [{
    "seller_domain": "example.com",
    "source_url": "https://example.com/product",
    "observation_id": "<uuid>",
    "item_name": "RTX 5070",
    "quantity": "1",
    "unit": "pcs",
    "total_item_eur_cents": 57900,
    "shipping_eur_cents": 1000,
    "additional_tax_eur_cents": 0
  }],
  "total_eur_cents": 58900,
  "condition_codes": ["identity_match", "availability", "delivery", "total_within_budget"],
  "next_action": "request_user_approval",
  "requires_checkout_revalidation": true
}
```

IDs/times above are illustrative schema notation, not a valid ready-to-send example. Formal accepted JSON Schema can be added in a separately approved PR; do **not** alter frozen Observation or GoalSpec for it.

## Owned database

- `action_packages(package_id PK, goal_id,goal_version,evaluation_id UNIQUE, owner_user_id, offer_fingerprint, payload jsonb, created_at, expires_at, status)`.
- `handoff_deliveries(id PK, package_id, destination_ref, status, attempt, created_at)` optional if external delivery enabled.
- `inbox(event_id PK, processed_at)`; UNIQUE `(goal_id, goal_version, evaluation_id)`.

## Algorithm

1. Verify event producer/schema, inbox dedup and read GM runtime. Suppress paused/completed/cancelled or obsolete goal-version events.
2. GET immutable GoalEvaluation from GE; require `is_opportunity_verified=true`, `status=satisfied`, nonempty observation IDs.
3. Fetch DE observations, ensure source URL, observed_at, product match and price components are present. Expose **only** citations/data required for proposed action, no secrets/personal notes. Pick expiry <= minimal remaining source freshness interval.
4. Persist package immutable; dedup repeated same evaluation/fingerprint. Do not send to an external endpoint without consent/purpose allowlist, signed transport and rate limiting.
5. External purchasing agent must separately refresh price/availability and obtain user authorization; no auto-purchase in GoalPulse MVP.

## Internal / public reads

- `GET /internal/v1/action-packages/{package_id}` (proposed, service-authenticated) for safe backend use.
- `GET /v1/action-packages/{package_id}` (proposed additive public Gateway API, ICP-02); only owner may retrieve. A GET does not trigger transaction.

## Acceptance tests

- Valid event creates one package whose referenced Observation IDs and URLs exist, and whose goal/version matches source evaluation.
- Old/paused goal cannot produce a package; duplicate event returns same package ID.
- Missing or stale evidence blocks a fresh package even if an event exists.
- Package contains `approval_required=true`, `auto_execute=false`, no credentials; proposed action is informational.
- Different authenticated user receives 404 on guessed package ID; expired package is visibly expired and not actionable.
- Any future callback uses explicit allowlist + signed request + user opt-in, with no order placement in the app.

**Task IDs:** `D4-AI-01`..`D4-AI-03`.
