# Junction 48-hour implementation execution plan (pragmatic)

The **44 tasks** describe the full initial build and safety/quality work; **this is larger than one guaranteed 48-hour outcome**. Treat mandatory P0 pathways as a priority and P1 as stretch. Do not claim "all 7 deployed" without tests.

| Time from start | Lead (serial decision) | Parallel work | Integration / exit |
|---|---|---|---|
| 0–3h | Dev 1 coordinates ICP-01 and ICP-08 with all | Dev 3 tests Context.dev real product, Dev 2 drafts UI, Dev 4 writes evaluator fixture tests | Frozen schema and accepted internal mock interfaces; provider feasibility recorded |
| 3–10h | Dev 1 builds GM DB, auth and Rabbit baseline | Dev 2 GI compiler and Next.js shell; Dev 3 DE normalization / DM source registry; Dev 4 pure ProductPurchaseEvaluator | Standalone tests pass; mocks use accepted interface |
| 10–17h | Dev 1 leads first integration | Each owner fixes their own handlers: confirm, activated event, fixture observation, evaluation, notification stub | **Gate B: mock E2E works before live data** |
| 17–29h | Dev 3 leads real source pipeline | Dev 1 outbox / gateway; Dev 2 UI goal list; Dev 4 event dedup / notification | **Gate C: real Context.dev product extract→DE→GE** |
| 29–39h | Dev 4 leads downstream | Dev 3 signed webhook / cache/freshness; Dev 2 notification UI; Dev 1 operational hardening | **Gate D: one verified opportunity→one notification; handoff if stable** |
| 39–48h | Dev 1 release/freeze | Dev 2 demo UX; Dev 3 redacted provider proof; Dev 4 basket optimizer only if critical path green | Test/rehearse, no new risky dependencies, honest fixture/live labels |

**Drop order if behind schedule:** email, complex dashboards, multi-seller basket optimization, extensive background scheduling, external agent callback, travel, complex goal editing. **Do not drop:** user approval, immutable goal version, unknown-fact safety, source provenance, current-goal runtime check, auth, no-autopurchase, event idempotency.

## Minimal reliable live demo path

Natural language product goal → clarification (one missing field) → human confirm → GoalActivated event → one public product extraction through Context.dev → DE Observation with source+timestamp → GE tri-state checks → one verified opportunity only if complete evidence → in-app user notification → prepare-only package (if delivered). One source which cannot reveal FI shipping/checkout cost must show `unknown`, not a false green success. Demonstrate a verified transition with an explicitly labelled synthetic fixture if the live page does not expose all data.
