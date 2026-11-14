# R2-D4-02 — Notification implementation and acceptance evidence

**Status: code/recorded checks implemented; integration acceptance remains open.**
Owner: Developer 4. Changes are confined to `services/notification/`; the prior
GE reader increment remains untouched and uncommitted. No commit/push/merge or
external message was performed for this work.

## Behavior and changed paths

- `notification/api.py`: private FastAPI factory implementing the Phase 2
  `GET /v1/notifications?limit=1..100` array DTO and idempotent
  `PATCH /v1/notifications/{notification_id}/read` → empty 204. Both require
  trusted server-side identity dependencies; mutation authorization is a
  separate required dependency. Owner IDs are never taken from request data.
  Cross-owner/missing IDs both return 404. DTOs omit internal owner IDs, evidence
  details and fingerprints. Responses use no-store; unavailable storage returns
  a fixed 503, never a fabricated empty feed. `/health/live` and `/health/ready`
  are private deployment probes; readiness requires an explicit successful
  injected check and fails on exceptions.
- `notification/readers.py`: actual bounded HTTP transport and ICP-01 GM,
  GE and DE readers. Existing GM/DE scopes are requested per operation;
  D1/GE supplies the evaluation read scope rather than guessing one. Tokens are
  supplied by D1's ICP-08 issuer. All calls propagate the event correlation UUID.
  Redirects/ambient proxies are disabled, request timeout defaults to 5 seconds,
  response size is bounded, and errors contain fixed codes. Demo rejects fixture
  provenance; missing charges and original observed timestamps are preserved.
- `notification/worker.py` + `runtime.py`: composition of real readers, owned
  PostgreSQL store, consumer and retry coordinator. D1 supplies the configured
  queue, DLX/routing key and connection factories. The worker asserts a durable
  topic exchange and durable queue with explicit DLX arguments, and binds only
  `goal.opportunity_verified.v1`. A pre-existing incompatible queue fails rather
  than silently using transient settings. D1 must provision the DLX and its DLQ
  binding (or the explicit default-exchange DLQ destination). ACK occurs after
  durable success/duplicate/suppression. Permanent failures NACK without requeue;
  retries preserve the event and durable journal, wait with broker heartbeats,
  then NACK/requeue. `run()` closes the connection on failure or shutdown. D1's
  supervisor owns bounded reconnect backoff. Imports never open connections.
- `consumer.py`, `store.py`, `retry.py`: serialize creation for each owner/goal
  using a transaction-scoped PostgreSQL advisory lock. Check runtime and time
  after lock waits. Count prior created notifications across goal versions in a
  sliding window; default 60 minutes, configured values must be 60..1440.
  Rate-limited deliveries commit durable suppression and do not automatically
  replay later. A later new evidence event must still pass all safety checks.
  Failed inserts/commits roll back the inbox and rate-slot creation. Original and
  corroborating captures must both be within min(goal freshness, 10 minutes);
  new captures cannot renew expired original citations.
- `fingerprint.py`: P0 product key follows the ADR's ordered business fields;
  exact Phase 1 keys remain recognized for upgrade compatibility. P1 procurement
  continues its previous basket policy pending its dedicated task.
- `migrations/003_goal_rate_limit.sql`: partial owner/goal/time index for the
  sliding-window query. Existing records count towards the cap; no history is
  deleted or rewritten. `PostgresStore` explicitly sets READ COMMITTED before
  inbox/read work so a competing lock holder's commit is visible.
- `requirements.txt`: existing approved FastAPI/Pydantic/JSON Schema stack plus
  the existing repository's psycopg, pika and uvicorn runtime packages, matching
  Goal Management's ranges. No installation was performed.
- Tests cover HTTP ownership/DTO/error behavior, recorded readers, queue/ACK/
  retry decisions, freshness, fingerprint vectors, sliding rate limits and
  rollback. Optional PostgreSQL tests cover concurrent distinct offers and
  persistence through fresh store/connection instances. Existing standalone
  handoff fixture stores were extended for the transaction interface.

## Canonical product fingerprint handoff to GE/D1

The UTF-8 SHA-256 input joins these fields with literal `|`, in this exact order:

```text
goal_id|version|seller_domain|normalized_product_key|EUR|total_eur_cents|shipping_eur_cents|tax_eur_cents|condition|delivery_country
```

For P0 one-piece products, total is unit price + shipping + additional tax,
all known integer cents. Seller is the lowercased, trailing-dot-normalized URL
hostname. Product key is the immutable selected query, casefolded with whitespace
collapsed, consistent with the evaluator's exact model mapping. Delimiter and escape characters are percent-escaped consistently before joining. Capture IDs, source IDs, URLs,
paths, tracking parameters, display wording and timestamps are excluded.
Observation v1 has no condition fact, so that component is explicitly
`unknown`; this never satisfies a required `condition=new`. GE remains
authoritative for the domain checks. If a future approved evidence version adds
condition, coordinate a new fingerprint policy/migration rather than changing
existing records silently.

`tests/test_business_fingerprint.py` pins field order and semantics without
importing GE private modules. D1/GE must adopt and contract-test the same
canonicalization as part of the remaining GE runtime task before claiming
cross-service policy alignment. GE's Phase 1 fingerprint is currently different;
NS independently deduplicates the resulting events, but harmonization is open.

Upgrade limitation: an existing record with its exact current Phase 1 fingerprint
is recognized. Old rows lack enough monetary facts to reconstruct every new key
after a listing URL/wording change. An already-populated Phase 1 database therefore
requires an explicit history audit/backfill or coordinated cutover before live
rollout; this code does not guess a backfill or promise arbitrary legacy relists
will deduplicate. The current service had no established live deployment.

## Contracts and runtime binding

Frozen schemas, Gateway v1 and the four event routes are unchanged. Handler DTO
tests validate the actual JSON against `gateway.phase2.extension.v1.yaml`.
ADR-0003 accepts this additive extension, but this factory must remain private.
D1 owns Gateway routing, service-JWT/owner-forwarding dependencies and browser
CSRF/Origin checks. No unsigned identity adapter or browser cookie parser is
provided by Notification. Synthetic principals are used only by explicit tests.

The private HTTP factory requires `store`, `authorize_read`, `authorize_write`,
UTC `clock` and `ready` callbacks. D1 must check migrations/owned DB/broker and
required upstream configuration in readiness; no default ready=True exists.
The worker factory requires schemas path, explicit test/dev/demo profile,
GM/GE/DE origins, token issuer, approved evaluation scope, database connection
factory and Rabbit connection/topology. It uses `HttpTransport` unless a test
explicitly injects recorded responses. No silent fixture selection exists.
Malformed/oversized/wrong-routing messages enter the durable rejection/DLQ path.

D1 may map accepted `NOTIFY_GOAL_RATE_LIMIT_MINUTES` to `rate_limit_minutes`
(default 60); no new production environment key is introduced here. Apply
migrations 001, 002 and 003 in order to the Notification-owned database before
starting the factory. No cross-service SQL or data migration is performed.

## Acceptance evidence (2026-11-14)

| Criterion | Result | Evidence / remaining work |
|---|---|---|
| Checked recorded event, replay and rescrape produce one notification | PASS recorded | Consumer and recorded HTTP reader tests; standalone handoff scenarios |
| Unknown shipping/tax/stock, stale/paused/old version cannot notify | PASS recorded | Existing regression suite plus ten-minute boundary/lock-wait tests |
| Stable P0 business fingerprint and hourly cap | PASS recorded | ADR field-order vectors, relisting/price/version tests and sliding window tests |
| Two users cannot read/mark each other's notifications | PASS handler integration | Required synthetic auth dependencies; exact DTO tests; identical missing/unowned 404 |
| Manual ACK/retry/dead-letter ordering | PASS adapter tests | Injected broker; failed journal/commit leaves delivery unacknowledged |
| Persisted read state and concurrent cap through PostgreSQL | NOT RUN | Two optional live tests require GOALPULSE_NS_TEST_DSN; no local Docker/psql |
| Real broker/DB restart recovery and end-to-end authenticated Gateway | NOT RUN | Needs D1 stack/identity binding and complete live GE runtime |
| No purchase-success or external delivery claims | PASS recorded | Constant opportunity/checkout-revalidation copy; no purchase/email adapter |

Executed from repository root:

```bash
/private/tmp/goalpulse-d4-venv/bin/python scripts/validate_contracts.py
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q", "-rs"]; runpy.run_module("pytest", run_name="__main__")'
```

Results: contract validation PASS (5 schemas, 13 examples, 4 routes); repository
suite **931 passed, 15 skipped**, one existing Starlette/httpx deprecation warning.
The skips are live DB tests without configured DE/DM/GI/NS test DSNs.
`git diff --check` passed. Default `python scripts/validate_contracts.py` and
`python -m pytest -q` could not run their checks because that interpreter lacks
PyYAML/pytest; the existing test venv was used. The readline workaround prevents
the environment's native import crash and does not disable tests.

To run the new DB checks, configure `GOALPULSE_NS_TEST_DSN` privately for a
disposable Notification test database and run:

```bash
python -m pytest -q services/notification/tests/test_notification_postgres_live.py
```

These tests create/remove only a fresh randomly named test schema. Live broker
smoke must additionally verify D1's DLQ binding, queue durability, replay after
worker/connection restart and actual service-JWT identity. Do not mark R2-D4-02
fully accepted until these operational gates and fingerprint cutover are proven.

## Pre-PR review corrections

Publisher confirmation now has an enforced five-second POSIX main-thread deadline.
A timeout retires the uncertain channel; readiness becomes false and the worker
closes the connection for supervisor restart. The event remains retryable with
its original ID. Existing process alarms and non-main-thread publishing fail
closed rather than silently disabling the deadline. Docker/Linux workers and
macOS support this mechanism; other platforms require a supported worker process.
Product fingerprint components now escape `%` as `%25` and `|` as `%7C` in
all three services; ordinary keys are unchanged. This supersedes the earlier
pipe-rejection note. Synthetic regression tests cover the accepted pipe model,
escape collision avoidance and a blocked confirmation with restored alarm state.
The Phase 2 baseline artifacts were merged by PR #9 (57eef52); earlier pending
merge notes reflect the original handoff. Real routing/auth/runtime integration
and live evidence gates remain open. Latest root validation: 1041 passed,
19 skipped (unconfigured live services), one existing deprecation warning.
