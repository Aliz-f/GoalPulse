# Developer 4 — Phase 2 task cards

**Rule:** Read `AGENTS.md`, ADR-0003 and accepted current contracts before editing; no cross-owner edits without D1 contract PR.

## R2-D4-01 — Runnable GE worker, real readers, exact current-version evaluation API [P0]

**Gate:** R1/R2
**Depends on:** `R2-D1-00`, `R2-D3-02`
**Allowed paths:** `services/goal-evaluation/`

### Implementation
1. Bind authenticated GM current GoalSpec, DM goal subscriptions and DE Observation readers; implement accepted internal GE GET and readiness.
2. Connect Rabbit evidence.updated consumer to inbox/outbox + transactional evaluator, publisher confirm/manual ACK/DLQ.
3. Keep all tri-state constraints, fresh checked proof, goal version, pause and owner guards; fingerprint harmonizes with ADR-0003.

### Acceptance criteria
- [ ] Broker+DB test from recorded EvidenceUpdated stores evaluation, goal.evaluated.v1 once, verified only when fully checked and all constraints pass.
- [ ] Unknown shipping/tax/stock and stale/wrong goal version do not verify; duplicate delivery/restart yields one outcome.
- [ ] Authenticated internal API returns exact saved evaluation; no invented event names.

**Handoff / scope note:** Can build clients using recorded fakes before D1/D3 runtime is ready.

---

## R2-D4-02 — Real in-app notification worker, API and business dedup [P0]

**Gate:** R3
**Depends on:** `R2-D4-01`, `R2-D1-00`
**Allowed paths:** `services/notification/`

### Implementation
1. Run Rabbit consumer on goal.opportunity_verified.v1, authenticated current-goal/GE readers and owned persisted DB.
2. Generate stable fingerprint excluding event/observation IDs/timestamps, applied uniqueness + per-goal rate cap.
3. Expose versioned owner-only list/mark read via D1-approved public extension with Gateway; no emails/phone integrations required.

### Acceptance criteria
- [ ] Recorded checked verified event creates exactly one in-app notification; duplicate same business offer after rescrape sends none.
- [ ] Paused/stale/unverified opportunity never creates positive alert; two users cannot read each other’s notifications.
- [ ] Restart preserves notification and read state; no misleading purchase-success claim.

**Handoff / scope note:** D2 handles UI; D1 handles extension Gateway routing.

---

## R2-D4-03 — Wire read-only Agent Integration and grouped seller_orders DTO [P1]

**Gate:** R3/R4
**Depends on:** `R2-D4-01`, `R2-D1-00`
**Allowed paths:** `services/agent-integration/`

### Implementation
1. Use service-local existing grouped seller_orders schema as source and publish matching versioned public extension via D1 contract PR.
2. Run verified event worker, read current verified evidence from internal API, create owner-only immutable package with expiry and checkout revalidation.
3. Never automatically order/pay; include owner approval required and auto_execute false; block stale/disputed/unsafe links.

### Acceptance criteria
- [ ] Schema validation at Gateway and client passes without seller_offers field; replay builds one package.
- [ ] Other user cannot read; expired package unusable; checkout revalidation flagged.

**Handoff / scope note:** P1 only after real Goal path and Notification are stable.

---

## R2-D4-04 — Real recurring café procurement normalization and solver [P1]

**Gate:** R4+
**Depends on:** `R2-D4-01`, `R2-D3-01`, `R2-D3-02`
**Allowed paths:** `services/goal-evaluation/`

### Implementation
1. Verify per-item units, quantities, explicit quality equivalence and EUR costs from DE, including tax and seller shipping once.
2. Use existing recurring solver with real normalized Evidence; expose correct missing/unknown coverage.
3. No arbitrary substitute product or silent unit conversion and no fictitious savings percentage.

### Acceptance criteria
- [ ] Recorded kg/L/pcs multi-seller test computes correct total incl shipping once per seller and taxes.
- [ ] Any missing item/shipping/tax/quality evidence yields unknown, not verified.

**Handoff / scope note:** Only do after P0 end-to-end live goal is stable.

---
