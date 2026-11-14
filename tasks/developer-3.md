# Developer 3 — Discovery & Monitoring + Data & Evidence

**Role:** Web data specialist / Context.dev integration. **Owns:** `services/discovery-monitoring/`, `services/data-evidence/` and their private databases. **Read:** root `AGENTS.md`, DM and DE specs, frozen Observation/Event schemas, accepted internal API proposal, Context.dev docs. Never access another service DB or publish `evidence.updated.v1` from DM.

## Gate A — prove source feasibility and mock contracts

### D3-DM-01 — Context.dev provider spike and adapter interface [P0, EARLY]
- **Dependencies:** user API key in local env (already tested connection), no other service code.
- **Allowed:** `services/discovery-monitoring/`, sanitized local test reports in assigned directory.
- **Implement:** injectable provider client (search, scrape, create monitor, fetch run), auth/timeout/backoff; run one real public product URL and record redacted `success`/cache/extraction coverage; wrap requests behind test fake.
- **Accept:** `example.com` auth smoke + >=1 real product with named, priced data or documented extraction limitation; no key in logs/repo; tests run without network.

### D3-DM-02 — Source registry, subscriptions and source internal APIs [P0]
- **Dependencies:** accepted ICP-01, D1-GM-02 mocks.
- **Allowed:** `services/discovery-monitoring/`.
- **Implement:** schema/migrations `sources`,`goal_source_links`, URL canonicalization+public DNS guard, subscribe `(goal_id,version,source_id)` dedup, internal GET subscriptions/source metadata.
- **Accept:** source shared between two goals as one source; active subscriptions list correct versions; SSRF and redirect private-IP blocked; no secret in API response.

### D3-DM-03 — GoalActivated handler, discovery and initial scrape [P0]
- **Dependencies:** D3-DM-01/02, D1-GM-02, D3-DE-02 endpoint mock.
- **Allowed:** `services/discovery-monitoring/`.
- **Implement:** consume exact `goal.activated.v1`, fetch GoalSpec, bounded product discovery, source links, first fresh extraction submitted to DE. Create baseline separately.
- **Accept:** double event creates no duplicate links; one activation yields initial DE extraction independently of monitor change; unsupported delivery country rejected/unknown, no fabricated prices.

### D3-DM-04 — Context monitor scheduler and subscription reconciliation [P1]
- **Dependencies:** D3-DM-02/03, approved M2M active-goal list; Context.dev docs.
- **Allowed:** `services/discovery-monitoring/`.
- **Implement:** schedule page/search monitors only as supported, persist provider IDs+secrets, dedup shared monitors, re-sync active/paused/cancelled goal links from GM until lifecycle events approved.
- **Accept:** provider initial baseline recorded, not reported as change; pause one of two subscribed goals leaves monitor for other; once all links inactive, monitor paused safely.

### D3-DM-05 — Provider signed webhook receiver and retry [P0]
- **Dependencies:** D3-DM-01, D3-DM-04 monitor secret model.
- **Allowed:** `services/discovery-monitoring/` and associated test fixtures.
- **Implement:** signature/timestamp verification, per-monitor secret mapping, unique delivery inbox, queue follow-up extraction, bounded retry+DLQ and request-body limits.
- **Accept:** valid signed delivery stored once and quickly 2xx, invalid signature rejected, duplicates produce one job, failures don't create successful evidence.

### D3-DM-06 — Monitoring health/cost controls and Check Now internal dev hook [P1]
- **Dependencies:** D3-DM-03/04, signed-off service-only Check Now API design.
- **Allowed:** `services/discovery-monitoring/`.
- **Implement:** credit counters, bounded concurrent requests, source TTL, cost-aware scheduling and rate limit, explicit development-only 'check now' control (not public).
- **Accept:** 429 respects Retry-After; no unbounded rescrapes; invocation triggers one evaluated source check and exposes accurate health/error state.

## Gate B — normalize observations, publish evidence

### D3-DE-01 — Data & Evidence migration/storage + extractor failure ledger [P0]
- **Dependencies:** frozen Observation.v1, D1 platform DB setup.
- **Allowed:** `services/data-evidence/`.
- **Implement:** attempts, immutable observations, provenance, outbox, dedup fingerprint and indexes; migrations.
- **Accept:** known-good Observation passes schema; missing unit price creates attempt error, no fake observation; idempotent submission.

### D3-DE-02 — Internal POST extractions + normalization rules [P0]
- **Dependencies:** D3-DE-01, internal API approval ICP-01.
- **Allowed:** `services/data-evidence/`.
- **Implement:** typed ExtractionSubmission, Decimal money, normalized per `kg/L/pcs` unit, stock/delivery/tax/shipping null semantics, provenance and reliability evaluation; store valid strict Observation.
- **Accept:** pack size conversions tested, tax inclusive not added twice, missing tax/shipping remains unknown, invalid country/currency gracefully rejected, 201/202 correct.

### D3-DE-03 — Observation read endpoints and authorization [P0]
- **Dependencies:** D3-DE-01 and approved internal API.
- **Allowed:** `services/data-evidence/`.
- **Implement:** GET by observation_id and by source_id with bounded pagination/newest ordering, M2M authorization, strict DTO.
- **Accept:** GE consumer mocks and AI fetch exactly strict schema, cross-service DB unused, 403 on unauthenticated request.

### D3-DE-04 — EvidenceUpdated transactional outbox/publisher [P0]
- **Dependencies:** D3-DE-01/02, RabbitMQ baseline D1-PL-02.
- **Allowed:** `services/data-evidence/`.
- **Implement:** atomic observation+outbox, publisher confirms, retries and inbox/attempt dedup, exact `evidence.updated.v1` envelope.
- **Accept:** observed ID exists before event, one logical event per unique observation, broker outage recovered, event validates frozen contract.

### D3-DE-05 — Evidence quality/freshness and edge-case tests [P0]
- **Dependencies:** D3-DE-02/03/04.
- **Allowed:** `services/data-evidence/`.
- **Implement:** tests for cached provider time, no price, unknown availability, seller tax ambiguity, packaging, unverified/disputed evidence, duplicate provider request, mixed source signals.
- **Accept:** GE never sees false fresh/checked price; real URL extraction provides provenance and audited limitations.

## Integration handoff requirements

Provide D4 reproducible fixture Observations, source subscription response and evidence GET responses. Supply live redacted provider test report for judges; clearly label fixture vs live. Do not assume all Finnish merchants expose real checkout stock/shipping; persist `unknown`/partial rather than fake success.
