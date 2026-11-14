# Agent Integration — Developer 4

**Status:** session-approved package builder, immutable PostgreSQL adapter,
verified-event consumer, owner-only GET app factory and AI-03 mock integration
checks implemented. Real database/broker/Gateway/JWT deployment is not verified.

ICP-01/08 are accepted upstream under ADR-0002. The user confirmed the grouped
package contract approved on 2026-11-14 and
requested AI-02/03 implementation. The service-local schema is
[`schemas/action-package.v1.schema.json`](schemas/action-package.v1.schema.json).
[Contract rationale and release handoff](PACKAGE_CONTRACT_PROPOSAL.md) records
that session instruction without inventing named approvers or an approval PR.
Existing frozen contracts, shared approval ledger, Gateway configuration and ADRs
are unchanged. D1 owns the coordinated publication of the shared contract and
Gateway alignment with the approved `seller_orders` response.

## Prepared packages (AI-01 prerequisite)

`PackageBuilder` accepts strict frozen goal/evaluation/observation/event inputs
and produces only the approved whitelist. It requires active/latest ownership,
verified satisfied conditions, evidence provenance, checked in-stock data,
known charges, FI delivery and remaining freshness. Product matching uses an
explicit exact model/brand-plus-model comparison and supported attributes.
Absent new-condition/quality attributes stay unknown; neither source excerpts
nor LLM text establish those facts.

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
second delays; broker ACK must occur only after successful return. The broker
adapter still needs durable retry/DLQ wiring. No RabbitMQ client is installed.

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

`PackageReader.get(id, principal)` requires a `Principal` constructed from
validated server-side claims, filters ownership in SQL, validates the stored
package, and rechecks current runtime and expiry. UUID validation alone is not
authentication. Missing/unowned IDs are indistinguishable. Obsolete, paused or
expired packages are never returned as actionable. Reads return detached JSON
and never mutate, create, refresh or execute a package.

`api.create_app(reader, authenticate)` provides the service implementation of
`GET /v1/action-packages/{package_id}` for D1 to mount through Gateway. The
required authentication dependency has no default user/header bypass. Tests
inject fixture bearer identities; production must validate issuer, signature,
audience and expiry using D1's identity setup. This task does not select a JWT
provider, start a listener, expose a service or modify Gateway configuration.

Responses are 200 with the approved copy-ready package, 401 without authenticated
identity, 404 for absent/unowned packages, 410 when no longer actionable, and
safe 503 on dependency/storage failure. Every response is non-cacheable. No
checkout, order, credential-forwarding or external agent callback route exists.
The public response's grouped seller_orders must be published in the shared
contract release before D1 connects this service to the public Gateway; the old
unapproved seller_offers draft is not silently edited here.

## Persistence and dependencies

Apply `migrations/001_initial.sql` once to Agent Integration's owned PostgreSQL
DB. It creates inbox/action_packages, unique dedup constraints, owner index,
prepare-only payload checks and an immutable update/delete trigger. There are
no cross-service database reads/foreign keys. `PostgresStore(connect)` accepts
an injected DB-API connection factory with autocommit disabled. A concrete scoped
PostgreSQL driver/configuration remains a deployment dependency.

When PostgreSQL tools and the scoped service database are available, run from
the repository root (replace the placeholder with the owned service DSN):

```bash
psql --dbname='<agent-integration service DSN>' --file=services/agent-integration/migrations/001_initial.sql
```

Service dependencies use the approved stack: FastAPI, Pydantic v2 and the existing
JSON Schema validator. HTTPx is test-only. The service requirements were installed
in the temporary test environment with explicit tool approval. No root dependency
file or environment variable was changed. Deployment must package the unchanged
frozen schemas plus this service's approved package schema.

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -r services/agent-integration/requirements-dev.txt
python scripts/validate_contracts.py
python -m pytest -q
python -m pytest -q services/agent-integration/tests
```

Upstream D1 CI now installs GM/Gateway requirements, which provide this service's
current FastAPI/Pydantic/HTTPx dependencies. Keep service manifests aligned in
future dependency changes; shared CI files are outside this task's write scope. This machine's Miniconda
readline import crashes before pytest collection. The temporary-environment
process-local workaround used is:

```bash
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q"]; runpy.run_module("pytest", run_name="__main__")'
```

## Cross-service and safety checks (AI-03)

Tests pass the same frozen event/goal/evaluation/observation JSON into the actual
Notification and Agent Integration consumers, running Notification in a separate
process. Each imports only its own code; no private modules or databases are
shared. One event creates one notification and one safe package; replay creates
no additional effects. Paused/old versions, a pause during reads, unknown tax and
stale evidence suppress both. External networking is blocked in these tests.
Additional tests cover owner isolation, 401/404/410/503, expiry after a slow runtime
read, immutable copies, rollback/recovery, shared seller shipping, decimal lines,
secret/excerpt exclusion, and schema rejection of execution flags or extra fields.

A copy-ready **synthetic** handoff artifact is
[`tests/fixtures/action-package.product.fixture.json`](tests/fixtures/action-package.product.fixture.json).
It uses a separate exact-model/any-condition fixture goal. It does not claim the
original new-condition GPU goal has evidence for its missing facts.

PostgreSQL migrations/locking/trigger execution, live RabbitMQ recovery, real
JWT authentication and public Gateway/frontend integration remain **NOT RUN**.
The in-process HTTP tests validate the supplied endpoint and injected identity
boundary, not a deployed login provider. These are the remaining D1/D3 integration
and review gates; they are not replaced by mock tests or the session approval.
No notification-to-package ID backfill or discovery route was invented. D1/D2
must coordinate how the UI obtains approved package links through service APIs;
neither direct DB access nor an extra undeclared event is introduced here.

- [Agent Integration specification](../../docs/implementation/07-agent-integration.md)
- [Developer 4 tasks](../../tasks/developer-4.md)
- [Repository rules](../../AGENTS.md)
