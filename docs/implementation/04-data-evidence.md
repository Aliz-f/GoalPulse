# 04 — Data & Evidence Service (Developer 3)

**Ownership:** `services/data-evidence/`, DB `data_evidence`. **Criticality:** P0. Input raw extraction submission from DM; output strict `Observation.v1` plus source provenance. Sole publisher of `evidence.updated.v1`.

## Service responsibilities

- Validate source metadata and extract normalized product facts, product identity, units, price in EUR cents, stock, FI delivery, additional shipping and tax, source timestamp/cache provenance.
- Store immutable observations with source/evidence link; preserve extraction failures and missing required facts separately.
- Provide internal observation reads by ID/source for GE and AI; do **not** decide goal satisfaction, discover URLs or manage Context.dev API key.
- Escalate ambiguous evidence as `unverified`/`disputed` and/or extraction failure. **Schema cannot carry missing price**, so store an `extraction_attempt`, not fabricated Observation.

## Internal API (proposed: ICP-01)

| Method / path | Request | Response | Failure |
|---|---|---|---|
| POST `/internal/v1/extractions` | `ExtractionSubmission` from DM including `source_id`, canonical URL, `provider=context_dev`, extraction_method, timestamps, cache age, parsed fields + snippets | 201 `{accepted, observation_id}` or 202 `{accepted:false, reason}` for missing price | 400 invalid, 401/403 service auth, 409 dedup key |
| GET `/internal/v1/observations/{observation_id}` | observation UUID | 200 strict `Observation.v1` | 404 |
| GET `/internal/v1/observations?source_id=...&limit=N` | source UUID, bounded N | 200 `{observations:[Observation]}` newest-first | 400,401,404 |

Do not accept arbitrary `owner_user_id` or client-supplied checked reliability. DE may upgrade to `checked` only under explicit automated validation policy; LLM textual certainty alone never qualifies.

## Owned data model

- `extraction_attempts(attempt_id PK, source_id, provider, provider_request_id, extraction_method, source_url, fetched_at, provider_observed_at, cache_age_ms, outcome, error_code, raw_excerpt_ref, fingerprint, created_at)`; raw content retention bounded.
- `observations(observation_id PK, source_id indexed, source_url, observed_at indexed, payload jsonb, reliability, extraction_attempt_id FK, normalized_fingerprint UNIQUE, created_at)`.
- `provenance(observation_id PK FK, provider_request_id, page_url, cache_age_ms, field_sources jsonb, parser_version, extraction_version)`; no personally identifying scrape storage.
- `inbox(event_id PK, processed_at)` if consuming async submissions later; `outbox(id PK, event_id UNIQUE, payload_jsonb, status, attempts, next_attempt_at)` for `evidence.updated.v1`.

## Normalization policy

- **Unit:** `Observation.selling_unit` is `pcs`, `kg` or `L`; `unit_price_eur_cents` is **price PER that unit**, not per package unless 1 unit. Example: 1.5 L milk pack priced €3.00 ⇒ 200 cents/L **only when package size is confirmed**. If size unknown, extraction attempt with reason `UNKNOWN_PACKAGING` or unverified observation that cannot pass evaluator.
- **Money:** parsed EUR amount using Decimal. EUR cents are rounded only as documented; no float. Refuse ambiguous currency. `tax_eur_cents` is **additional tax not already included in displayed unit price**, else 0 only if inclusive treatment has been verified. If tax treatment unknown, null. Shipping is per order per merchant, not multiplied per line item; mark unknown if merchant shipping to FI not established.
- **Availability:** explicit `in_stock`, `out_of_stock`, `unknown` (no inference from "add to cart" alone if unreliable). `delivery_to_fi` yes/no/unknown is specifically FI; other countries unsupported in v1.
- **Product identity:** normalize brand/model and preserve original names. Product matching with requirements performed by GE; use deterministic aliases and evidence, do not let LLM invent model attributes.
- **Observed time:** provider capture time = fetched_at minus cache age when available; never use current webhook receipt as source evidence time. Strict UTC timezone aware. Preserve source URL, request ID, snippet for audit.
- **Reliability:** `checked` means parser fields corroborated by source text/structured data and internally consistent; this is not guaranteed merchant checkout availability. If discrepancy `disputed` blocks verification; plain extraction may remain `unverified`.
- If any mandatory field cannot be represented by `Observation.v1` (e.g. missing numeric price), log failed/partial attempt; don't populate the field as zero.

## Event and write mechanics

After valid immutable Observation saved, in same transaction persist outbox event:

```json
{"event_type":"evidence.updated.v1","payload":{"source_id":"<uuid>","observation_id":"<uuid>"}}
```

Wrap in canonical `domain-event.v1` with generated UUID `event_id`, UTC timestamp, `producer=data-evidence`, correlation and aggregate UUID. Publisher confirms; redelivery dedup by stable observation fingerprint. Never publish event before observation exists.

## Acceptance tests

- Valid Context.dev product extract normalizes to strict Observation and emits a single evidence event.
- Unknown price is stored as failed/partial extraction; no Observation/event/false zero.
- Extract unit conversion to kg/L for known package size and rejects mismatched ambiguous packaging.
- Inclusive tax vs additional tax separated; no double counting; unknown tax remains null.
- Cached output uses true source observation time and fails strict age gating downstream.
- Duplicate provider request + same normalized fingerprint does not create second logical observation.
- Read by ID/source returns strict schema; denies unauthenticated caller; raw scraped instructions never executed.
- Fault injection: DB success but RabbitMQ failure recovered by outbox and event eventually delivered.

**Task IDs:** `D3-DE-01`..`D3-DE-05`.
