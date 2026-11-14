# 05 — Goal Evaluation Service (Developer 4)

**Ownership:** `services/goal-evaluation/`, DB `goal_evaluation`. **Criticality:** P0. Sole authoritative evaluator for `satisfied|unsatisfied|unknown`, owns `GoalEvaluation` and verified-opportunity event.

## Inputs/outputs and boundaries

- Consume `evidence.updated.v1` with exactly source_id/observation_id. Resolve subscribed goals via DM internal subscriptions API; retrieve exact GoalSpec/current lifecycle via GM internal API and Observation(s) via DE.
- Evaluate independently of LLM/Context.dev availability by accepting injected typed fixtures; LLM can help candidate discovery but never final Boolean truth.
- Publish `goal.evaluated.v1` on each meaningful evaluation, and `goal.opportunity_verified.v1` **only** when all mandatory conditions fresh/checked/true and current goal active.
- No direct DB reads of other services, no user notifications or purchase actions.

## Evaluator architecture

- `GoalEvaluator.evaluate(goal: GoalSpec, observations: Sequence[Observation], now_utc) -> GoalEvaluation` pure/domain function; dispatch by `goal_type`.
- `ProductPurchaseEvaluator`: exact/equivalent model; new/any condition; required_attributes; quantity; allowed/blocked seller domains; stock; delivery to FI; total payable EUR cents; evidence reliability+age. `equivalent_allowed` requires explicit policy and checkable matching evidence; unsupported equivalence → `unknown`.
- `RecurringProcurementEvaluator`: cover each requested item/quantity/quality by observations from supported suppliers, choose at most `max_distinct_sellers` and minimize total cost within constrained dataset. Cost = sum(item quantity × price per normalized unit) + shipping **once per seller** + additional tax. Do not double count. `current_total` and `max_total` used for savings progress; choose deterministic tie-break. A partial basket never verifies. Model seller domain from source record until verified merchant identity is available.
- Handle quantity decimals using `Decimal(str(quantity))`, exact cents internal, defined rounding per line (`ROUND_HALF_UP`) and explanation; avoid floating point for pricing.

## Required conditional checks

For product: `identity_match`, `condition`, `availability`, `delivery`, `seller_policy`, `freshness`, `reliability`, `tax_known`, `shipping_known`, `total_within_budget`.

For procurement: `full_basket_coverage`, `units_compatible`, `quality`, `availability`, `delivery`, `seller_limit`, `freshness`, `reliability`, `shipping_known_by_seller`, `tax_known`, `total_within_budget`.

Tri-state aggregation: if **any definitely unsatisfied mandatory condition**, overall `unsatisfied`; else if **any unknown**, overall `unknown`; else `satisfied`. Verified iff overall satisfied **and** candidate IDs >=1 **and** all evidence freshness/reliability gates pass. `unknown` should never be counted as false hard failure or green success. Explainers reference observation IDs and conditions.

## Time, provenance and freshness

- `now_utc - observation.observed_at <= goal.monitoring.max_evidence_age_minutes`, else `unknown` freshness; provider cached age must already be accounted for by DE.
- `evidence.reliability == checked` required for verified; `disputed` and `unverified` cannot verify.
- Stock `unknown`, FI shipping eligibility `unknown`, shipping/tax null or identity mismatch ambiguity prevent verified opportunity. For shipping/tax, 0 is valid **only** with verified evidence that no additional charge applies.
- On pause/cancel or stale goal version, discard or record non-verified evaluation; **recheck authoritative GM runtime immediately before producing verified event**. Consumers recheck too.

## Data model

- `evaluations(evaluation_id PK, goal_id, goal_version, evaluated_at, status, payload jsonb, fingerprint UNIQUE, created_at)` immutable.
- `condition_results(id PK, evaluation_id FK, code, status, reason, observation_ids jsonb)`.
- `offer_fingerprints(goal_id, goal_version, fingerprint, last_verified_at, notification_emitted, UNIQUE(goal_id,goal_version,fingerprint))` to suppress redundant opportunities.
- `inbox(event_id PK, processed_at)` and `outbox(... event_json, status, attempts ...)`. Keep evaluator version/hash for reproducibility.

## Proposed internal HTTP

- `GET /internal/v1/evaluations/{evaluation_id}` → strict GoalEvaluation for NS/AI, authenticated service only.
- Optionally `GET /internal/v1/evaluations?goal_id=...&limit=N` (additive approval for frontend history). No private SQL sharing.

## Event processing (required ordering)

1. Check event structure + inbox duplicate; lookup active subscriptions and for each goal its authoritative current runtime+immutable version.
2. Fetch latest relevant Observation per canonical source; avoid mixing stale/old supplier snapshots with fresh new ones. Evaluate exactly one active version per triggered source.
3. Pure evaluation returns strict GoalEvaluation; persist it and `goal.evaluated.v1` outbox atomically.
4. For a newly verified **offer fingerprint**, persist `goal.opportunity_verified.v1` outbox atomically; suppressed if fingerprint previously notified, unless approved price-improvement policy.
5. Ack source event only after durable inbox/evaluation/outbox transaction. Outbox publisher retries independently.
6. NS/AI fetch evaluated data by ID, verify active goal again before action. Downstream GET does not imply actual checkout reservation.

## Business examples

- GPU: one product at €579, shipping €10, additional tax €0, requested qty 1 ⇒ €589 = 58,900 cents, within €600; **only** if match, stock, shipping, fresh source and evidence checked.
- Café: 20 L milk @ €1.15/L + 10 kg coffee @ €16/kg + 2 sellers × €5 verified shipping + no additional tax ⇒ €193 total; demonstrate basket needs all lines, correct quantities and shared seller shipping. All sample amounts synthetic for tests.

## Acceptance tests

- Product happy path returns strict satisfied evaluation; emits both events exactly once per fresh opportunity fingerprint.
- Price 60001 cents for 60000 cap → unsatisfied (or higher after shipping); no verified event.
- Unknown stock/delivery/shipping/tax, stale evidence or unverified reliability → unknown, no verified event.
- Incorrect model/new condition / blocked vendor → unsatisfied even when cheap.
- Two sources update for same goal → choose valid cheapest option deterministically; no duplicate alerts on replay.
- Café full basket optimizer selects lowest legal total with at most max sellers; counts shipping once per seller and handles fractional quantities correctly.
- Partial basket, unknown package conversion, or unknown merchant shipping → cannot verify.
- Paused/old-version goal suppresses verification despite old event.
- Consumer event redelivery + DB/broker fault injection → exactly one logical evaluation and no duplicate verified event.

**Task IDs:** `D4-GE-01`..`D4-GE-06`.
