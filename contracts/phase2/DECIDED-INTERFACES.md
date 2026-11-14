# DECIDED integration interfaces — specification for coordinated versioned PR

**Decision is fixed, implementation still requires a D1-owned approved contract PR in the current repo.** Existing `contracts/openapi/gateway.v1.yaml` and `contracts/event-routing.yaml` must NOT be edited in place.

| Capability | Contract decision | Ownership |
|---|---|---|
| Core 7 public routes | Keep frozen `/v1/goal-sessions`, message, get session, goal confirm/list/detail/patch as actually defined in Gateway v1 YAML. | D1 Gateway, D2 FE, D1 GM, D2 GI |
| Evaluation history | `GET /v1/goals/{goal_id}/evaluations`, owner-scoped, return actual GoalEvaluation v1 schema | D4 GE + D1 Gateway/D2 UI |
| Notifications | `GET /v1/notifications`, `PATCH /v1/notifications/{notification_id}/read`, owner-scoped strict DTO. | D4 NS + D1 Gateway/D2 UI |
| Demo browser auth | `POST /v2/demo/sessions`, credentials via HTTPS POST + HttpOnly cookie, local demo only. No production IdP claim. | D1 Gateway/D2 UI |
| Package (P1) | `GET /v1/action-packages/{package_id}`, owner-only; grouped `seller_orders`, map local D4 schema, don't use old `seller_offers`. | D4 AI + D1 Gateway/D2 UI |
| Service JWT | Already accepted ICP-08; user owner claims server-side, no user-id trust from request body. | D1, each service |
| Events | Exactly `goal.activated.v1`, `evidence.updated.v1`, `goal.evaluated.v1`, `goal.opportunity_verified.v1`; payloads unchanged. | GM, DE, GE, NS, AI |
| Reliable evidence | HTTP/Context input initially unverified; internal DE checker may promote; no caller self-attestation. | D3 |
| Source/goal resolution | Use accepted ICP-01 internal APIs + authenticated readers, not DB queries across services. | D1/D3/D4 |

## Additional interface issue for implementers

Observation.v1 does **not** expose a typed product condition/newness field, nor explicit seller VAT-included semantics. A page/LLM guess is not equivalent to a verified typed attribute. **Do not silently assume `condition=new`, zero VAT, or FI delivery from merchant domain.** GE must treat these as UNKNOWN unless proven by current approved internal provenance/reader contract or a NEW versioned additive field approved under D1 contract PR. For the P0 scenario, select first-party pages with explicit new-product and VAT/delivery wording; if it cannot be represented to GE, record a scoped contract extension before claiming Verified.

Source URL alone does not guarantee a seller quote. To produce a live Verified product opportunity, GE needs complete numeric total and checked provenance, and shipping/tax/stock/condition from demonstrable support. If unavailable, display Candidate/Unknown and use labeled recorded fixture to demonstrate Verified→Notification logic.

## Event fingerprint without adding fields to v1 payload

On `goal.opportunity_verified.v1`, NS/AI fetch exact saved `evaluation_id`, goal/version, observations via accepted authenticated APIs; deterministically recompute fingerprint from ADR-0003 (excluding event_id/observation_id/time). Consumers ignore paused/stale versions. No new field in frozen domain event schema.
