# Interface Change Proposals and decisions requiring team signoff

**Status:** design issues found by analyzing the existing frozen v1 contracts; this document intentionally **does not** change them. Developer 1 coordinates explicit approval with affected owners BEFORE writing dependent implementation.

| ID | Decision / gap | Impact / proposed resolution | Approval owners | Priority |
|---|---|---|---|---|
| ICP-01 | Missing internal HTTP contract | Approve `contracts/openapi/internal.proposed.v1.yaml` (GI candidate, GM version/lifecycle, DM subscription lookup, DE ingest/get, GE evaluation fetch, NS and AI reads). Use authenticated service identity and explicit type schemas. | D1 + D2 + D3 + D4 | **Blocker P0** |
| ICP-02 | Public dashboard reads absent | Review `contracts/openapi/gateway.extensions.proposed.v1.yaml` for additive `/v1/goals/{goal_id}/evaluations`, `/v1/notifications`, mark-read, `/v1/action-packages/{package_id}` or defer UI history/handoff detail. No silent edits to existing frozen paths. | D1 + D2 + D4 | P1 |
| ICP-03 | Lifecycle events absent | In MVP, use GM internal current-state reads + DM reconciliation poll and final recheck in GE/NS/AI; propose `goal.lifecycle_changed.v2` / `goal.version_activated.v2` after hackathon instead of inventing v1 key. | D1 + D3 + D4 | P0 behavior |
| ICP-04 | Observation.v1 only `delivery_to_fi` and EUR | Business validator restricts executable MVP to `delivery_country=FI`, EUR sources. Multi-country travel and currencies need new versioned evidence schemas and Evaluator. | D1 + D2 + D3 + D4 | P0 |
| ICP-05 | Observation.v1 requires `unit_price_eur_cents` | Partial/missing price must be stored as `extraction_attempt`/source error, **not** a fake priced observation. New `ObservationV2` could allow unknown price if needed. | D3 + D4 | P0 |
| ICP-06 | Seller IDs, packaging, tax semantics absent | Seller identity from normalized source domain for MVP; unit prices per `pcs/kg/L`, package-size equivalence validated; additional tax only. If packaging or tax treatment cannot be established mark unknown. Consider v2 seller/packaging/tax fields. | D3 + D4 | P0 |
| ICP-07 | No goal editing endpoint | Do not implement arbitrary edit in MVP; pausing/resuming allowed. Later implement signed-off `PATCH /v1/goals/{goal_id}/criteria` with new immutable version and lifecycle reconciling. | D1 + D2 + D3 + D4 | P2 |
| ICP-08 | Authentication provider and service auth unspecified | D1 chooses OIDC issuer/JWKS or demo-compatible signed auth, defines test identities; internal endpoints use private network + authenticated service credentials. No unsigned browser-supplied user ID. | D1 + D2 | **Blocker P0** |
| ICP-09 | Successful observation need not be reliable | Explicit policy: `evidence.reliability=checked`, non-stale, source matches target, every mandatory criterion checked; do not assume extraction alone qualifies. | D3 + D4 | P0 |
| ICP-10 | Duplicate opportunity identity not in event | Dedup notification/AI by `goal_id + version + canonical seller/product/offer snapshot fingerprint`, fetched from Evaluation/Evidence. Store stable fingerprint and unique index. | D4 | P0 |
| ICP-11 | Unclear credits/recheck and scheduling | D3 imposes bounded rate, quotas and reasonable schedule; Context.dev monitor initial run baseline ≠ event; initial direct scrape always executed. | D3 + D1 | P0 |
| ICP-12 | Source authentication / SSRF | D3 validates public HTTPS URLs, DNS + redirect destinations; only signed provider callbacks, no client-provided arbitrary webhook URLs. | D3 + D1 | P0 |

## Approval procedure

1. Developer 1 opens an Interface Change Proposal issue using `docs/agent-task-template.md` with ID(s), exact API/schema change diff, producer, consumers and migration order.
2. Affected owners review request/response examples, error codes, security, idempotency and test fixtures. Sign off in PR/issue.
3. Add accepted ADR under `docs/adr/`. Rename proposed draft to approved internal contract (or mark approved by decision) in a coordinated PR.
4. Consumer mocks/contract tests are updated **before** implementation depends on new interface. Protected `contracts/` modification requires review; additive contract files do not implicitly supersede v1.
5. Log decisions in `docs/implementation/DECISIONS.md`, with author/date/PR link; existing files remain source of truth until accepted.

## Proposed initial decisions for MVP

- **Approve** ICP-01 and ICP-08 as the first meeting; all four developers blocked without a stable internal read/identity model.
- **Accept temporary workaround** ICP-03 (reconciliation) with mandatory pause/resume tests.
- **Defer** arbitrary goal editing and executable travel to later phases.
- **Require** live evidence transparency: a screenshot/demo may use synthetic injected change, but it must be explicitly marked fixture; never present synthetic prices as web-retrieved.
