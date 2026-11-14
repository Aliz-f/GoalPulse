# Action Package contract — grouped DTO rationale and release handoff

**Phase 2 update:** ADR-0003/MVP-08 selects the existing grouped `seller_orders`
schema, and `contracts/openapi/gateway.phase2.extension.v1.yaml` contains that
shape. Its coordinated merge evidence is still pending. The private app factory
and recorded schema tests are now implemented; real Gateway routing, identity
binding and shared contract publication remain D1-owned. The user authorized
recorded P1 preparation before the live P0 gate. See `PHASE2_HANDOFF.md`.

The remainder records the original Phase 1 proposal/rationale. Its earlier
statements that no handler or grouped extension exists are historical.

Status: the grouped `seller_orders` shape is implemented only as a service-local
proposal and persistence artifact. ICP-02, the shared schema, public route and
Gateway response remain unapproved. Developer 1 owns a coordinated contract/ADR
release with D2/D3/D4 review. No frozen v1 interface or approval ledger is
modified here, and no `/v1/action-packages/{package_id}` handler is supplied.

Implementation policy: no automatic successor/refresh for an identical offer.
Duplicate evaluation or goal/version/offer returns no second package, including
after expiry. Changing that policy requires an explicit coordinated change;
GET never creates or renews a package.

## Shared-release handoff to Developer 1

Review and, if accepted, publish the proposed package shape and selected conservative dedup rule
in the coordinated D1/D2/D3/D4 contract release, with its ADR and review record.
The public Gateway draft still needs alignment with grouped seller_orders.

### Existing authoritative artifacts

- The proposed schema is bundled under this service; its shared contracts/
  publication remains D1-owned.
- Frozen GoalSpec.v1, Observation.v1, GoalEvaluation.v1, domain-event.v1 and event
  routing remain unchanged.
- Inputs use the declared `goal.opportunity_verified.v1` reference payload.
- `docs/implementation/07-agent-integration.md` and the Gateway extension draft
  show a proposed flat `seller_offers` shape, not an accepted interface.

### Motivation and reproducible ambiguity

A two-line basket from one seller currently has two observations, each carrying
merchant-order shipping. Copying that amount into each proposed offer risks
charging twice; copying a zero into one line invents a shipping statement. The
contract must distinguish order shipping from line costs. The quantity and
additional-tax semantics must also match D3 normalization. Repeated captures of
one offer can have different evidence expiry times, so fingerprint deduplication
needs an explicit refresh/expiry rule.

### Proposed schema/API shape

Add a **new** `contracts/schemas/action-package.v1.schema.json` only through the
approved coordinated contract PR. Do not edit existing v1 artifacts. Required top-level fields:

`package_id`, `schema_version=1`, `goal_id`, `goal_version`, `evaluation_id`,
`created_at`, `expires_at`, `action_type=prepare_purchase`,
`approval_required=true`, `auto_execute=false`, `seller_orders`,
`total_eur_cents`, `condition_codes`, `next_action=request_user_approval`,
`requires_checkout_revalidation=true`.

Selected change from the earlier unapproved illustration: replace flat `seller_offers`
with `seller_orders`. Each order has a normalized `seller_domain`, exactly one
known `shipping_eur_cents`, and `items`. Each item has:

`source_url`, `observation_id`, `observed_at`, `item_name`, `quantity` (positive
exact decimal string), `unit` (`pcs|kg|L`), `unit_price_eur_cents`,
`total_item_eur_cents`, `additional_tax_eur_cents`.

All money is nonnegative integer EUR cents. Each item total is quantity times
normalized unit price rounded once with ROUND_HALF_UP. Additional tax is a
verified amount for the requested line, not included tax counted again. Total
is the sum of line totals, additional line taxes and merchant shipping once per
order. Conflicting/unknown merchant charges block package construction. D3 must
confirm normalization and quantity-dependent charges before live use.

Synthetic product example of the proposed shape (values are fixtures):

```json
{
  "package_id": "ae000000-0000-4000-8000-000000000101",
  "schema_version": 1,
  "goal_id": "ae000000-0000-4000-8000-000000000002",
  "goal_version": 1,
  "evaluation_id": "ae000000-0000-4000-8000-000000000007",
  "created_at": "2026-11-14T10:00:00Z",
  "expires_at": "2026-11-14T11:00:00Z",
  "action_type": "prepare_purchase",
  "approval_required": true,
  "auto_execute": false,
  "seller_orders": [{
    "seller_domain": "example.com",
    "shipping_eur_cents": 1000,
    "items": [{
      "source_url": "https://example.com/gpu",
      "observation_id": "ae000000-0000-4000-8000-000000000004",
      "observed_at": "2026-11-14T10:00:00Z",
      "item_name": "Graphics Card RTX 5070",
      "quantity": "1",
      "unit": "pcs",
      "unit_price_eur_cents": 57900,
      "total_item_eur_cents": 57900,
      "additional_tax_eur_cents": 0
    }]
  }],
  "total_eur_cents": 58900,
  "condition_codes": ["identity_match", "condition", "availability", "delivery", "seller_policy", "freshness", "reliability", "tax_known", "shipping_known", "total_within_budget"],
  "next_action": "request_user_approval",
  "requires_checkout_revalidation": true
}
```

Ownership remains private storage metadata derived from authenticated GM reads;
this example does not export owner identity, personal notes, provider transcripts,
credentials or payment information. Public read access must enforce the owner
independently. The synthetic example assumes a separately confirmed exact-model,
any-condition goal whose necessary facts exist in v1; the original new-condition
GPU goal remains blocked by missing evidence.

### Expiry and selected conservative dedup rules

- Expiry <= the minimum of selected `observed_at + max_evidence_age_minutes`.
  Zero remaining lifetime, future evidence, stale evidence or a paused/obsolete
  goal cannot produce an actionable package. Never extend an old capture's age
  because it was fetched again.
- Duplicate event ID and `(goal_id, goal_version, evaluation_id)` create no second
  package. Payloads are immutable; an expired payload is never rewritten.
- Selected implementation: suppress the same goal/version/offer fingerprint
  until the offer changes, even after expiry. An immutable successor policy
  would require a later coordinated triggering/GE change; GET never refreshes.
- All reads and any future handoff recheck current owner/lifecycle/version and
  expiry. Preparing or reading does not reserve stock or authorize a purchase.

### Compatibility and affected owners

This adds a new package interface without modifying existing frozen v1 DTOs.
The seller grouping changes the **proposal**, so D2/frontend and external agent
consumers must approve before any code depends on it. D3 confirms cost and
provenance semantics; D4 implements/validates packages; D1 owns Gateway/auth and
contract release. Notification should link only approved package IDs, without
private DB reads. Public/internal endpoints remain separately gated by ICP-01/02.

### Rollout and acceptance

1. Review and record the shape/dedup policy in a coordinated shared release.
2. Record accepted ADR, schema, examples and producer/consumer contract tests.
3. Approve the serializer and owned-DB immutable persistence/inbox/dedup migration.
4. Test duplicate events, multi-seller totals, missing/new-condition/quality
   evidence, stale/paused/old versions, expiry and owner-only retrieval with mocks.
5. Coordinate authenticated reads and deployment with D1; run PostgreSQL/broker
   and Gateway tests before enabling export. No unsolicited external callback or
   checkout/payment/credential capability is part of this rollout.

No ICP-02 approval, shared ADR or approval PR is recorded. Service-local schema:
`schemas/action-package.v1.schema.json`. Shared contract publication and Gateway
alignment remain a D1 handoff. The design rationale above is retained, with its
conservative dedup alternative proposed for review.
