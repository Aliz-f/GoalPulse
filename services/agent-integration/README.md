# Agent Integration — Developer 4

**Status:** grouped prepare-only packages, authenticated HTTP readers, private
owner-read app factory, durable Rabbit worker/retry composition and an independent
recorded smoke runner are implemented. Live deployment remains unverified.

## Phase 2 — R2-D4-03 preparation

The user explicitly authorized preparing this P1 task with recorded dependencies
before the live P0 integration gate closes. `runtime.build_runtime` wires owned
SQL persistence and service-token readers; `api.create_app` supplies the
owner-only `/v1/action-packages/{package_id}` handler with a required D1 identity
dependency. Demo rejects fixture provenance; in-memory stores are accepted only
through explicit test-profile injection. No service port is automatically opened.

You can exercise the actual worker and HTTP handler without any peer service:

```bash
python services/agent-integration/tests/recorded_smoke.py
```

Use an environment with root and service development requirements already
installed. The JSON output identifies `mode=recorded_test` and
`live_deployment=false`. The runner blocks outbound network connections and
proves one package under replay, owner 200, other owner 404, expired 410, and
immutable prepare-only output. It uses synthetic checked facts, not live offers.

Apply owned migrations 001 then 002 before real SQL worker startup. Products
expire no later than min(goal freshness, 30 minutes). Reads recheck current DE
evidence and GM state without renewing citations or expiry. Current disputed or
changed evidence blocks use. See [Phase 2 handoff](PHASE2_HANDOFF.md) for schema
compatibility, replay/cutover limitations, tested evidence and deployment gates.

ADR-0003 selects the existing grouped `seller_orders` shape, and D1's Phase 2
extension contains the accepted public DTO. Gateway routing and real owner
authentication remain D1-owned. The older internal package sketch has a flat
shape; no conflicting internal route was implemented here.

The following sections preserve the Phase 1 implementation record. Current
Phase 2 behavior and handoff above supersede earlier no-handler/no-worker notes.

ICP-01/08 are accepted under ADR-0002; ICP-02/MVP-07/08 and the grouped package
shape are accepted under ADR-0003. The service-local schema is
[`schemas/action-package.v1.schema.json`](schemas/action-package.v1.schema.json).
[Contract rationale and release handoff](PACKAGE_CONTRACT_PROPOSAL.md) records
the shape and compatibility policy. Frozen v1 contracts remain unchanged. D1
owns Gateway alignment and authenticated forwarding of the accepted response.

## Prepared packages (AI-01 prerequisite)

`PackageBuilder` accepts strict frozen goal/evaluation/observation/event inputs
and produces only the proposed whitelist. It requires active/latest ownership,
verified satisfied conditions, evidence provenance, checked in-stock data,
known charges, FI delivery and remaining freshness. Product matching uses an
explicit exact model/brand-plus-model comparison and supported attributes.
Absent new-condition/quality attributes stay unknown; neither source excerpts
nor LLM text establish those facts.
Observation.v1 also has no available-quantity fact. Product or procurement
requests for anything other than exactly one normalized unit are therefore not
eligible for a package, even when a listing reports `in_stock`.

Seller orders group lines by normalized source hostname. Each order has shipping
once; each line has exact quantity, per-unit integer EUR cents, ROUND_HALF_UP
line total and additional line tax. Conflicting merchant shipping, unsupported
units, ambiguous item allocation, missing facts or over-budget totals suppress
construction. Procurement normalization remains unconfirmed by default until
D3's handoff; synthetic basket tests explicitly confirm it. Whole pieces are
required for pcs. No optimizer, package conversion or seller substitution is
invented by this service; GE selects the candidate evidence.

Expiry is no later than the earliest selected capture plus the goal's maximum
age. Packages always contain `approval_required=true`, `auto_execute=false`,
`next_action=request_user_approval` and `requires_checkout_revalidation=true`.
Only selected evidence and price facts are serialized: no owner identity,
provider tokens, excerpts, transcripts, credentials or personal notes.
Source links undergo literal safety and credential-parameter checks. Recognized
authentication/payment parameters in queries/fragments are rejected, including
case/encoding aliases. Safe sku/variant/size/color selectors are preserved.
Stored package validation applies the same policy before reads. D3 owns DNS/redirect SSRF
controls. This service never fetches source URLs or contacts a merchant.

`PackageConsumer` consumes only declared `goal.opportunity_verified.v1`, using
injected read-only GM/GE/DE adapters. It verifies IDs, rejects changed or untrusted
source snapshots, rechecks runtime and freshness immediately before storage, and writes
inbox plus package atomically. Transient reads have three attempts with 0.1/0.2
second delays. The Phase 2 worker performs manual ACK only after durable success
and uses the configured retry/DLQ topology; D1 still owns deployed RabbitMQ
provisioning and supervision.

A newer checked capture of identical offer facts does not invalidate an otherwise
fresh verification. Package citations and expiry continue using the original
immutable capture. Future, stale or changed captures and downgraded reliability
remain blocked; recapture cannot renew an expired package.

Deduplication is durable by event ID, goal/version/evaluation, and
 goal/version/offer fingerprint. Same-offer recaptures do not produce a second
package, even after expiration. The original payload is immutable; GET cannot
renew it. A future refresh/successor policy requires a coordinated change and
trigger design. Package IDs are stable for an immutable goal/version/evaluation.
The local fingerprint copy follows GE/Notification policy without importing
another service's private modules.

## Owner-only reads (AI-02)

`PackageReader.get(id, principal, correlation_id)` requires a `Principal` constructed from
validated server-side claims, filters ownership in SQL, validates the stored
package, propagates the caller's UUID correlation ID to the GM runtime read, and
rechecks current runtime and expiry. UUID validation alone is not
authentication. Missing/unowned IDs are indistinguishable. Obsolete, paused or
expired packages are never returned as actionable. Reads return detached JSON
and never mutate, create, refresh or execute a package.

`agent_integration.api.create_app` exposes `/v1/action-packages/{package_id}` for
private Gateway forwarding. It requires D1's validated owner-auth dependency and
a propagated UUID correlation ID, and returns the accepted grouped
`seller_orders` DTO. ICP-02/MVP-07/08 and the Phase 2 extension are accepted;
D1 identity/Gateway integration remains the deployment gate.

## Persistence and dependencies

Apply `migrations/001_initial.sql` and then
`migrations/002_processing_journal.sql` once to Agent Integration's owned
PostgreSQL DB. They create inbox/action_packages, unique dedup constraints,
prepare-only payload checks, the immutable update/delete trigger and the durable
worker retry/rejection journal. There are no cross-service database reads/foreign
keys. `PostgresStore(connect)` accepts an injected DB-API connection factory with
autocommit disabled.

When PostgreSQL tools and the scoped service database are available, run from
the repository root (replace the placeholder with the owned service DSN):

```bash
psql --dbname='<agent-integration service DSN>' --file=services/agent-integration/migrations/001_initial.sql
psql --dbname='<agent-integration service DSN>' --file=services/agent-integration/migrations/002_processing_journal.sql
```

Service dependencies declare the existing approved JSON Schema, FastAPI,
Pydantic, psycopg, pika and Uvicorn stack. No root dependency file or environment
variable was changed. Deployment must package the unchanged frozen schemas plus
the service-local package schema that matches the accepted Phase 2 extension.

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -r services/agent-integration/requirements-dev.txt
python scripts/validate_contracts.py
python -m pytest -q
python -m pytest -q services/agent-integration/tests
```

Keep service manifests aligned in future dependency changes; shared CI files are
outside this task's write scope.

## Cross-service and safety checks (AI-03)

Tests pass the same frozen event/goal/evaluation/observation JSON into the actual
Notification and Agent Integration consumers, running Notification in a separate
process. Each imports only its own code; no private modules or databases are
shared. One event creates one notification and one safe package; replay creates
no additional effects. Paused/old versions, a pause during reads, unknown tax and
stale evidence suppress both. External networking is blocked in these tests.
Additional tests cover owner isolation, expiry after a slow runtime
read, immutable copies, rollback/recovery, shared seller shipping, decimal lines,
secret/excerpt exclusion, and schema rejection of execution flags or extra fields.

A copy-ready **synthetic** handoff artifact is
[`tests/fixtures/action-package.product.fixture.json`](tests/fixtures/action-package.product.fixture.json).
It uses a separate exact-model/any-condition fixture goal. It does not claim the
original new-condition GPU goal has evidence for its missing facts.

PostgreSQL migrations/locking/trigger execution, live RabbitMQ recovery, real
JWT authentication and public Gateway/frontend integration remain **NOT RUN**.
These are the remaining D1/D3 integration
and review gates; they are not replaced by mock tests or the session approval.
No notification-to-package ID backfill or discovery route was invented. D1/D2
must coordinate how the UI obtains approved package links through service APIs;
neither direct DB access nor an extra undeclared event is introduced here.

- [Agent Integration specification](../../docs/implementation/07-agent-integration.md)
- [Developer 4 tasks](../../tasks/developer-4.md)
- [Repository rules](../../AGENTS.md)
