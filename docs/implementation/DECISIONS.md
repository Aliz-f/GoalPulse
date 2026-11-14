# GoalPulse implementation decision log

No proposed changes in the implementation blueprint have been approved merely by being included in this starter kit. Record human/team signoff here.

| Date (UTC) | Decision ID | Choice made | Approved by | PR / Issue |
|---|---|---|---|---|
| 2026-11-14 | ICP-01 | Accepted: D1, D2, D3 and D4 approved the reviewed internal HTTP contract. `internal.proposed.v1.yaml` is retained under its historical filename but has accepted version 1.0.0 status; see ADR-0002. | Developers 1–4 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-08 | Accepted: D1 and D2 approved ServiceBearer JWT machine-to-machine transport. D1-PL-03 owns the concrete issuer/audience/signature/expiry implementation and security tests. | Developers 1–2 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-13 | Accepted Phase 1 handling: `Observation.v1` cannot prove product condition or arbitrary required attributes. D4 returns `unknown` for missing condition evidence and never verifies a `new` offer from v1 alone. `Observation.model` is usable only with an explicit exact-model mapping. A future versioned evidence extension requires a separate ICP; no v1 schema change is approved. | Developers 1 and 4 | Approved remediation for PR #6; versioned extension follow-up pending |

## Phase 2 MVP activation record

The product owner delegated the Phase 2 MVP policy choices recorded by ADR-0003. Developer 1 accepted the compatible decisions below on branch `phase-2` through task `R2-D1-00`. The PR/merge reference is **pending**; this log does not claim that runtime implementation or release tests have passed. Frozen v1 schemas, Gateway v1, and event routing remain unchanged.

| Date (UTC) | Decision ID | Choice made | Approved by | PR / Issue |
|---|---|---|---|---|
| 2026-11-14 | MVP-01 | P0 is one-unit `product_purchase` for a new physical product deliverable to FI. Procurement is P1; travel/arbitrary goals and checkout are unsupported. | Product owner; D1 integration | R2-D1-00; PR pending |
| 2026-11-14 | MVP-02 | Retain Python 3.12+, Node 22, PostgreSQL 16, RabbitMQ 4, FastAPI, Next.js, isolated service databases, and service-owned workers/migrations. | Product owner; D1 integration | R2-D1-00; PR pending |
| 2026-11-14 | MVP-03 | Runtime profiles are `test`, `dev`, and `demo`; demo fails closed on FE/GI/DE fixture mode or missing provider/auth configuration. | Product owner; D1 integration | R2-D1-00; PR pending |
| 2026-11-14 | MVP-04 | Demo uses the existing Anthropic adapter with validated structured drafts; no OpenAI P0 adapter and no provider-to-fixture fallback. | Product owner; D1 integration | R2-D2-01; PR pending |
| 2026-11-14 | MVP-05 | Demo uses synthetic/non-sensitive prompts, discloses Anthropic transfer, avoids sensitive logs, and documents demo-user cleanup without claiming production GDPR compliance. | Product owner; D1 integration | R2-D2-01/R2-D1-03; PR pending |
| 2026-11-14 | MVP-06 | Isolated demo identity uses two pre-provisioned users and a short-lived HttpOnly same-origin cookie; no browser token paste or hard-coded browser identity. | Product owner; D1 integration | R2-D1-01; PR pending |
| 2026-11-14 | MVP-07 / ICP-02 | Accept additive, owner-scoped evaluation, notification/read-ack, and P1 action-package Gateway reads in `gateway.phase2.extension.v1.yaml`; frozen routes stay unchanged. | Product owner; D1 integration; D2/D4 review required in PR | R2-D1-00; PR pending |
| 2026-11-14 | MVP-08 | P1 ActionPackage uses D4's strict grouped `seller_orders` DTO, expiry/revalidation, `approval_required=true`, and `auto_execute=false`; the old flat proposal is rejected. | Product owner; D1 integration; D4 implementation owner | R2-D1-00/R2-D4-03; PR pending |
| 2026-11-14 | MVP-09 / ICP-09 | Only the internal Data & Evidence deterministic checker may promote evidence to `checked`; external/provider assertions enter unverified, conflicts are disputed, and incomplete proof remains candidate/unknown. | Product owner; D1 integration; D3 implementation owner | R2-D3-02; PR pending |
| 2026-11-14 | MVP-10 / ICP-04 | FI delivery, zero shipping, and included tax require explicit offer evidence; otherwise the field is unknown. Non-EUR is outside P0. | Product owner; D1 integration | R2-D3-02/R2-D4-01; PR pending |
| 2026-11-14 | MVP-11 / ICP-06 | Retain request/observation/cache provenance; product evidence age is bounded by `min(goal limit, 30 minutes)` and Verified alert recheck by 10 minutes. | Product owner; D1 integration | R2-D3-02/R2-D4-01; PR pending |
| 2026-11-14 | MVP-12 / ICP-05 | Missing price/stock/shipping/tax is failure or unknown, never zero; retries are bounded and permanent failures are terminal and durable. | Product owner; D1 integration | R2-D3-01/R2-D3-02; PR pending |
| 2026-11-14 | MVP-13 / ICP-03/ICP-11 | Use initial search/scrape plus periodic reconciliation without new events; default 60-minute interval, minimum 30 minutes, two active goals/user, three sources/goal, and 100 Context requests/user/day for demo. | Product owner; D1 integration | R2-D3-01/R2-D3-03; PR pending |
| 2026-11-14 | MVP-14 / ICP-12 | Public Context webhook ingress is deferred from P0. Any P1 ingress requires signature, replay-window, dedup, rotation, body-cap, redirect/DNS, and SSRF controls. | Product owner; D1 integration | P1 scoped follow-up; PR pending |
| 2026-11-14 | MVP-15 / ICP-10 | Opportunity dedup uses the ADR-0003 canonical SHA-256 business fingerprint, excluding event/observation/time IDs; one verified notification per goal per hour in demo. | Product owner; D1 integration; D4 implementation owner | R2-D4-01/R2-D4-02; PR pending |
| 2026-11-14 | MVP-16 | Goal lifecycle, evidence trust, evaluation state, and opportunity state remain distinct; dashboard shows evidence and time, never a fabricated coverage percentage. | Product owner; D1 integration | All Phase 2 tasks; PR pending |
| 2026-11-14 | MVP-17 | Keep the four frozen event names and require outbox/inbox, manual ACK after commit, bounded retry/DLQ, current-version/owner checks, and scheduler reconciliation. | Product owner; D1 integration | D1/D3/D4 runtime tasks; PR pending |
| 2026-11-14 | MVP-18 / ICP-07 | Interactive edit/version endpoint is deferred; pause and new-goal creation are supported and GoalSpec versions remain immutable. | Product owner; D1 integration | Deferred; PR pending |
| 2026-11-14 | MVP-19 | Demo is local/isolated, service and database ports remain private, secrets stay server-side, outbound URLs are screened, and public deployment needs a separate security review. | Product owner; D1 integration | R2-D1-01/R2-D1-03; PR pending |
| 2026-11-14 | MVP-20 | P0 release requires the full truthful live path and honest Unknown; recorded fixtures separately prove Verified/Notification and must be labeled. | Product owner; D1 integration | R2-D1-03; PR pending |

### ICP resolution summary for Phase 2

| ICP | Phase 2 resolution | Authority |
|---|---|---|
| ICP-01 | Remains accepted without modification. | ADR-0002 |
| ICP-02 | Accepted as an additive versioned public extension; grouped `seller_orders` is P1. | MVP-07/08 + new OpenAPI extension |
| ICP-03 | Accepted reconciliation/current-state approach; no new v1 event. | MVP-13/17 |
| ICP-04 | Accepted FI-only, explicit-delivery-evidence P0 rule. | MVP-01/10 |
| ICP-05 | Accepted durable failure/unknown semantics; never fabricate price zero. | MVP-12 |
| ICP-06 | Accepted explicit tax/shipping and bounded-freshness semantics; incomplete facts remain unknown. | MVP-10/11 |
| ICP-07 | Deferred; no interactive edit endpoint. | MVP-18 |
| ICP-08 | Remains accepted; demo cookie terminates at Gateway and downstream identity uses signed server-side tokens. | ADR-0002 + MVP-06 |
| ICP-09 | Accepted internal deterministic DE checker policy; requester/provider cannot self-assert `checked`. | MVP-09 |
| ICP-10 | Accepted canonical business fingerprint and notification throttle. | MVP-15 |
| ICP-11 | Accepted scheduler defaults and configurable demo quotas. | MVP-13 |
| ICP-12 | Public webhook deferred from P0; P1 security requirements fixed. | MVP-14/19 |
| ICP-13 | Remains accepted; unavailable typed condition/attributes are `unknown`. | Existing decision + MVP-16 |
