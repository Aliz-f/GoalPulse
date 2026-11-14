# Contract and decision migration plan — D1 owns the actual PR

**Do not overwrite** the audited repository's accepted v1 contracts. On commit `5186edf`, accepted ICP-01/08 and missing-as-unknown handling ICP-13 exist; ICP-02/09/10/04/06/11/12 were pending. The product owner has now selected Phase 2 policies in ADR-0003; D1 must record/merge decisions and versioned public additions. Merely unzipping this package does not modify the repo.

## Exact migrations

1. Add ADR-0003 to `docs/adr/`. Update `docs/implementation/DECISIONS.md` recording `MVP-01..MVP-20` and resolution of ICP-02/03/04/05/06/07/09/10/11/12 plus auth/privacy; link to the merge PR. Existing ADR-0002 stays untouched.
2. Preserve existing `contracts/schemas/{goal-spec,goal-draft,observation,goal-evaluation,domain-event}.v1.schema.json`, `contracts/event-routing.yaml`, and `contracts/openapi/gateway.v1.yaml`. Do not add event names; don't enlarge goal enum for travel.
3. Create **new additive public extension**, e.g. `contracts/openapi/gateway.phase2.extension.v1.yaml`, with owner-only:
   - `GET /v1/goals/{goal_id}/evaluations` → versioned GoalEvaluation array.
   - `GET /v1/notifications` and `PATCH /v1/notifications/{notification_id}/read` → versioned Notification DTO.
   - `GET /v1/action-packages/{package_id}` → **P1** grouped seller_orders ActionPackage DTO. Base precise field structure on D4 existing `services/agent-integration/schemas/action-package.v1.schema.json`, not the outdated proposed `seller_offers` sketch.
   - Demo auth **`POST /v2/demo/sessions`**, separately marked local/demo-only and never public-production, with server-validated preprovisioned credentials, rate limiting, HttpOnly cookie. Needs gateway integration review; do not allow self-registered arbitrary identities.
4. **Identity:** Gateway issues authenticated principal claims and accepted ICP-08 short-lived M2M token to service. Internal owner validation must not trust user-supplied user_id header. Add two-owner authorization tests.
5. **Evidence:** don't change frozen Observation.v1 enum; internal DE ledger/validator controls transition from `unverified` to `checked` with audit trail. If implementation requires a new method/field for double-check proof, put it in DE-owned internal DB/table or **new additive versioned internal API**, not vendor-supplied reliability in a public DTO. GE reads current canonical Observation.
6. **Fingerprint:** shared deterministic canonicalization must be documented/tested; domain event payloads remain frozen (no new fingerprint in event). NS/AI resolve evaluation and derive stable business key under shared semantics.
7. **DB migrations:** one new migration per affected owner-service when needed; migration reversible where safe; no direct cross-service DB access; run migrations before readiness.
8. Test schema/consumer compatibility: old v1 examples still validate; new extension schema validates new real server DTO; no seller_orders/seller_offers ambiguity remains.

## OpenAPI extension integration process

Do not auto-promote `contracts/openapi/gateway.extensions.proposed.v1.yaml`; its `seller_offers` conflicts with D4 implementation. Use exact current D4 schema in a new versioned additive spec and wire Gateway + FE typed client in one coordinated PR. If D4's schema has unsafe/unknown fields, prefer P1 deferral to invented transformations. The product choice is fixed: **grouped seller_orders**.

## Contract review checklist

- [ ] New ADR + DECISIONS updated and Git merged
- [ ] Frozen v1 diff is empty
- [ ] Two users + cross-owner 401/403/404 tests
- [ ] No new event names
- [ ] New notification read DTO validated against actual NS response
- [ ] Package extension reflects existing D4 schema, `auto_execute=false`
- [ ] Browser + Gateway use same exact new routes and version
- [ ] Auth session enabled only for isolated demo profile
