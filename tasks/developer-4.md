# Developer 4 — Goal Evaluation, Notification, Agent Integration

**Role:** deterministic decision engine + prepare-only downstream UX. **Owns:** `services/goal-evaluation/`, `services/notification/`, `services/agent-integration/`. **Read:** `AGENTS.md`, specs 05–07, frozen GoalSpec, Observation, GoalEvaluation and Event contracts, accepted internal contract. No access to Context.dev credentials or another service DB.

## Gate A/B — first evaluator with fixed fixtures

### D4-GE-01 — Evaluation DB, pure evaluator architecture and event adapter [P0]
- **Dependencies:** frozen schemas/Event routing, RabbitMQ mock; none of live Context.dev.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** migrations for evaluations/condition_results/fingerprints/inbox/outbox; pure `GoalEvaluator` base interface and dispatcher; exact schema validation; event consume with injectable HTTP clients.
- **Accept:** module importable/testable without network, goal type dispatch strict, duplicate event inbox safe, no direct cross-service SQL.

### D4-GE-02 — Source subscription + GoalSpec + evidence consumers [P0]
- **Dependencies:** accepted ICP-01, D3-DM-02 and D3-DE-03 consumer mocks, D1-GM-02.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** consume `evidence.updated.v1`, fetch DM source subscriber versions, GM runtime/latest version, DE observations and strict GoalSpec; reject old/paused/terminal goals.
- **Accept:** for source shared by two active goals evaluate both; for paused or old version emit no verified event; failure/retry bounded and never query another DB.

### D4-GE-03 — ProductPurchaseEvaluator [P0, FIRST DOMAIN]
- **Dependencies:** D4-GE-01, frozen specs and fixture observations.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** deterministic model/attribute matching, condition, seller allow/block, availability, FI shipping, tax/shipping known, quantity total, evidence freshness/reliability, tri-state reason list.
- **Accept:** good GPU fixture verifies; >budget/wrong model=unsatisfied; unknown shipping/stock/tax, stale/unverified=unknown; strict GoalEvaluation schema and observation IDs.

### D4-GE-04 — Persist evaluation, outbox events and opportunity fingerprint [P0]
- **Dependencies:** D4-GE-01/02/03, RabbitMQ baseline.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** persist immutable evaluation + `goal.evaluated.v1` outbox every meaningful result; verified fingerprint unique and outbox `goal.opportunity_verified.v1` only once for same offer; publish with confirms/retry; M2M GET `/internal/v1/evaluations/{id}`.
- **Accept:** event envelope validates; same event/offer replay→one logical verification; broker restart recovers; NS/AI retrieve immutable evaluation.

### D4-GE-05 — RecurringProcurementEvaluator [P1]
- **Dependencies:** D4-GE-03, D3-DE-02 packaging/unit semantics, multi-seller fixtures.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** deterministic bounded basket optimizer for list of items, compatible quality/unit, max sellers, delivery/tax, per-merchant shipping once, cents rounding; progress metric `savings_progress` only if semantically valid.
- **Accept:** cheapest complete basket found under constraints; missing item or unknown shipping not verified; 3-vendor vs 1-vendor compares full totals; tie-break deterministic; computation bounded for MVP.

### D4-GE-06 — Evaluation edge/security/reliability tests [P0]
- **Dependencies:** D4-GE-02/03/04, D4-GE-05 for basket tests.
- **Allowed:** `services/goal-evaluation/`.
- **Implement:** stale version, paused during evaluate, duplicate events, freshness, cache timestamp, quantity rounding, tax/shipping, faulty provider/HTTP, forced outbox retry.
- **Accept:** all P0 test matrix cases for evaluator green; no false verified opportunity on incomplete info.

## Gate C — downstream services

### D4-NS-01 — Notification DB, event consumer and dedup policy [P0]
- **Dependencies:** D4-GE-04 evaluation read, D1-GM-02 runtime, accepted internal API.
- **Allowed:** `services/notification/`.
- **Implement:** tables, consume exact verified event, retrieve runtime/evaluation, stable offer fingerprint, DB uniqueness and inbox, in-app notification status.
- **Accept:** one event→one notification, repeated event→zero duplicates, stale/paused version→zero notifications, goal owner derived from GM.

### D4-NS-02 — In-app read channel with approved Gateway extension [P1]
- **Dependencies:** D4-NS-01, ICP-02 signoff, D1 Gateway, D2 UI.
- **Allowed:** `services/notification/` (Gateway modifications only coordinated D1-owned PR).
- **Implement:** protected list and mark-read handlers, sorted pagination, robust failure state; read-only storage query for own user.
- **Accept:** frontend lists own notification and marks read, different user denied; no undocumented public route merged.

### D4-NS-03 — Channel retry/preference policy & tests [P1]
- **Dependencies:** D4-NS-01/02.
- **Allowed:** `services/notification/`.
- **Implement:** preferences for in-app, optional deferred email opt-in, delivery attempts, bounded retry and status metrics.
- **Accept:** email never sent without user opt-in; channel failures stored; duplicate event does not repeat side effects; no order completion claim.

### D4-AI-01 — Immutable read-only Action Package builder [P1]
- **Dependencies:** D4-GE-04 + D1-GM-02 + D3-DE-03, accepted package shape via ICP-01/02.
- **Allowed:** `services/agent-integration/`.
- **Implement:** DB migrations, consume verified event, fetch goal/version/evidence, compute safe payload, expiry, condition IDs, requires approval, auto_execute=false, unique goal/version/evaluation.
- **Accept:** known verified fixture builds correct package, incomplete/stale/paused blocked, no payment secrets or checkout execution, repeat event idempotent.

### D4-AI-02 — Secure Agent Package read endpoint [P1]
- **Dependencies:** D4-AI-01, D1 identity, ICP-02 public API approval.
- **Allowed:** `services/agent-integration/` (Gateway owner coordinates route addition).
- **Implement:** authenticated package GET, owner validation, expiry semantics, safe source URLs/evidence references, copy-ready JSON.
- **Accept:** owner sees package with revalidate notice, user B gets denied, expired package not presented as actionable.

### D4-AI-03 — Handoff, notification and non-purchase safety integration tests [P1]
- **Dependencies:** D4-NS-01/02, D4-AI-01/02.
- **Allowed:** `services/notification/`, `services/agent-integration/`, their test scopes.
- **Implement:** event replay, goal pause race, out-of-order version, package expiry, no purchase/credential calls, preparation semantics, test against fake GM/GE/DE.
- **Accept:** one verified event→one safe notification + one safe package; repeated/old event→no duplicate/no transaction.

## Integration handoff requirements

Supply D1 full Event fixture including published keys and reasons, D2 a stable sample `GoalEvaluation`, notification summary and package DTO; D3 exact evidence field requirements and current unknown blockers. All calculations must be reproducible with fixtures, not just LLM explanations.
