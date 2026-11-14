# D4-GE-03 evidence gap — approval request, not an accepted interface

Owner: Developer 4. Required reviewers: Developer 1, Developer 2 (goal criteria)
and Developer 3 (evidence extraction). Frozen v1 contracts remain unchanged.

## Current behavior and conflict

The task requires the original GPU fixture to verify and product condition to be
checked. The original `goal-spec.product.json` required condition `new`, arbitrary attribute
`gpu_model=RTX 5070`, and permits equivalents for a natural-language query.
Observation.v1 exposes model/brand but no product condition or general attributes.
Its checked reliability flag alone does not establish omitted facts. Parsing an
unstructured excerpt or accepting a substring as model identity would invent a
matching policy and could produce false verification.

The shared example changed in the approved D3 remediation to `condition=any`
and `match_policy=exact_model`. The historical missing-evidence premise now has
an explicit D4-owned copy at
`tests/fixtures/goal-spec.product.requires-new.fixture.json`; its regression keeps
the same unknown/no-verification assertions regardless of shared-example updates.
ICP-13 Phase 1 handling is accepted upstream; a richer evidence version remains
a separate proposal, not permission to infer missing facts.

The implementation therefore returns unknown for absent facts and unsupported
equivalence. It can verify a separate goal accepting any condition and an exact
structured model with only attributes represented in v1. This is an honest
limitation, not completion of the original fixture acceptance criterion.

## Proposed change for team review

Developer 1 should coordinate a versioned evidence contract, for example an
Observation v2 containing an explicit product condition (`new`, `used`,
`refurbished`, `unknown`) and an attributes map of extracted string facts. An
illustrative fragment is `{"condition":"new","attributes":{"gpu_model":"RTX 5070"}}`.
Extraction must preserve provenance and missing facts; these are proposed fields,
not implemented or accepted DTOs. Approve canonical target-model/attribute
semantics with Developer 2 and 3 as well. Different-model equivalence stays
disabled until a separate deterministic policy and evidence tests are approved.

## Compatibility and migration

Adding fields to strict Observation.v1 would break existing consumers. Publish a
new version with approved event/reference and internal read compatibility; do not
silently add these fields to v1 or change any v1 routing key. D3 produces the new
evidence, GE consumes it, and AI must preserve it in package evidence. D2 must
agree how user intent maps to canonical targets; GM must approve corresponding
goal contract implications. Deploy compatible consumers/read adapters before
enabling new producers. Existing v1 records keep unknown condition/attributes;
no backfill from assumptions. D1 records the accepted ADR, exact contract diffs,
approval and release order before integration work begins.

## Required fixtures and tests for acceptance

- An explicitly new, matching GPU with checked attribute evidence can verify.
- Used/refurbished, wrong model or contradictory attributes fail as appropriate.
- Absent/unknown condition or attributes never verify; text instructions do not
  supply structured facts or permissions.
- Current v1 fixtures continue validating and retain their missing-fact behavior.
- Producer/consumer tests cover version negotiation, provenance and mixed-version
  rollout before the new evidence contract is activated.

Existing implementation also assumes the specification's offer-level shipping
and additional-tax semantics. D3 must confirm those normalized amounts apply to
the requested quantity. Stock count is not represented in v1, so `in_stock`
proves availability only for a request of exactly one normalized unit. Larger or
fractional requested quantities remain `unknown` and cannot produce a verified
event, notification or package. No checkout availability or reservation is asserted.

## D4-GE-05 follow-up: procurement quality and normalized costs

The frozen café goal asks for `lactose free` and `whole beans`. Observation.v1
cannot carry structured quality assertions, so those conditions remain unknown;
item names and excerpts do not establish them. Coordinate this with ICP-13.
D3-DE-02 is also absent. A local normalization-confirmation input defaults false
in the procurement evaluator. Only explicit synthetic fixtures enable it for
testing per-unit prices, per-line additional tax and merchant-order shipping.
D3 must confirm these semantics and provenance for real requested baskets before
live verification is enabled. No new Observation field or internal wire DTO is
implemented to bypass either dependency.
