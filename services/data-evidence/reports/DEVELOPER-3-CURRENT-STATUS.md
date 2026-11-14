# Developer 3 current status — 2026-11-14

## Completion status

Components cover every Developer 3 task area. **The eleven tasks are not all
operationally accepted/integrated.** ICP-01/08 are accepted and their D3-owned
authenticated source/extraction/observation routes are implemented. Live provider,
database, broker and multi-service infrastructure acceptance remains unavailable.

| Task | Implemented and tested offline | Remaining acceptance / integration |
|---|---|---|
| D3-DM-01 | Injectable provider adapter, documented REST operations, bounded retry, cache provenance, redacted probe. | Real example.com + product feasibility checks; local API key absent. |
| D3-DM-02 | PostgreSQL sources/subscriptions, variant-preserving canonicalization, public DNS checks and authenticated accepted source reads. | Actual SQL checks; provider egress/redirect validation. |
| D3-DM-03 | Strict activation validation, atomic inbox/jobs, runtime/version rechecks, bounded initial discovery/scrape. | Approved GM/DE HTTP adapters and DTOs; RabbitMQ consumer startup/manual ACK/retry/DLQ; live vertical slice. |
| D3-DM-04 | Shared monitor reservation, secret references, baseline handling, active-source checks, pause/resume reconciliation, provider interval/status calls. | Real secret backend, polling/scheduler entrypoint, approved runtime/active-goal adapters and live monitor acceptance. |
| D3-DM-05 | Raw-body HMAC, freshness/body/header/monitor validation, event/run deduplication, bounded follow-up jobs. | Admitted HTTP ingress/trusted receiver config, actual secret storage and live delivery/retry tests. |
| D3-DM-06 | Durable daily operation budgets/cost reservations, resource TTL, process concurrency, unknown/pending usage, Retry-After propagation. | Signed-off Check Now API, runtime wiring and provider account/scheduled-monitor usage reconciliation. |
| D3-DE-01 | Immutable snapshots/provenance, failure ledger, stable fingerprints, atomic outbox/migrations. | Actual PostgreSQL migration/transaction/immutability checks. |
| D3-DE-02 | Explicit facts, exact EUR cents, confirmed packaging conversions, accepted ExtractionSubmission route, attempt idempotency and no caller trust promotion. | Reviewed policy before checked evidence promotion; live producer acceptance. |
| D3-DE-03 | By-ID/source SQL queries, bounded newest ordering, strict revalidation and authenticated accepted GET routes. | Consumer HTTP acceptance against a live service. |
| D3-DE-04 | Outbox worker, stable IDs, confirms, persistent mandatory publication, bounded retry/dead state, Pika channel adapter. | Platform-created channel/queues/timeouts, driver/runtime packaging and live broker recovery tests. |
| D3-DE-05 | Missing facts, packaging/tax ambiguity, cache time, disputed evidence, duplicate and ICP-13 regressions. | Actual GE consumer integration and audited live source provenance. |

## Changed code and purpose

All source changes are within Developer 3's two service directories. Existing
schemas, routes, task files, shared setup, infrastructure, ADRs and other owners'
services are unchanged. Local task branches/commits are stacked; nothing is pushed
or merged into main.

- Discovery `goalpulse_discovery/`: `registry.py`/`persistence.py` own sources and
  links; `workflow.py`/`validation.py`/`jobs.py` validate activations and discover
  initial evidence; `monitoring.py` owns shared monitor lifecycle; `webhooks.py`/
  `source_checks.py` verify/enqueue provider triggers; `limits.py` tracks budgets.
  `context_provider.py` adds provider scheduling/status and usage instrumentation.
- Discovery `migrations/0001_sources.sql` through `0005_provider_limits.sql` create
  the owned registry, jobs, monitor registry, webhook inbox and usage tables.
- Evidence `normalization.py` parses Decimal EUR amounts and uses exact rational
  arithmetic so long decimal values cannot round away fractional cents. It stores
  missing/unsupported facts only as failure records, never fake observations.
- Evidence `reads.py` returns strict, bounded snapshots. `publishing.py` handles
  confirmed outbox publication and replay with stable event IDs.
- Each service's tests and README document offline behavior and opt-in actual SQL
  acceptance. The initial evidence migration remains the sole DE migration.

## Contract and domain invariants

Frozen v1 schemas/event routing/Gateway OpenAPI are unchanged. DE alone produces
existing `evidence.updated.v1`; DM never publishes it. Private mock ports are not
claimed as approved HTTP contracts. No user or service authentication is invented.

ICP-01 and ICP-08 are accepted in the decision log and ADR-0002. D3 implements
only its accepted routes and duplicates the D1-selected ServiceBearer verification
without importing another service's private modules. Check Now remains a separate
unapproved interface and is not exposed.

ICP-13 Phase 1 handling is approved by Developers 1 and 4; the versioned evidence
extension remains deferred. No v1 condition or arbitrary attribute fields were
added. Explicit `model` is usable only through an explicit exact-model mapping;
missing condition evidence remains `unknown`. The fixtures now separate a safe
`condition:any` exact-model satisfied example from a `condition:new` unknown example.

## Integration impact and risks

- Apply the six owned forward SQL migrations through each service's checksummed
  runner against its own DB. No migration has been executed in this environment.
- No production dependency installed. Runtime needs platform-approved psycopg 3
  factories, a Pika BlockingChannel/BasicProperties factory, bounded connection
  timeouts, scoped DB/broker credentials and an actual server secret-store adapter.
- No server, exposed port, worker process, webhook ingress or purchase was started.
- Production code takes injected connections. Test-only `GOALPULSE_DE_TEST_DSN`
  must name `data_evidence_test`; `GOALPULSE_DM_TEST_DSN` must name
  `discovery_monitoring_test`. Existing `CONTEXT_DEV_API_KEY` is needed for the probe.
- Outbox/job transactions assume read-committed PostgreSQL and bounded external
  calls. A crash after broker confirm but before DB commit may redeliver the same
  event ID; consumers must durably deduplicate it.
- Local DNS validation cannot pin remote provider DNS or stop its redirects.
  Provider-side egress policy validation is required for full SSRF acceptance.
- Usage metrics describe logical direct operations, not all network retries or
  account spending. Missing/retried usage stays unknown and retains its reservation;
  scheduled monitor usage is explicitly unknown until provider reconciliation.
- D4 can reuse frozen synthetic Observations and the accepted authenticated read
  handlers. No service process or verified live offer was started by this change.

## Verification

Latest remediation verification ran from the repository root with Python 3.11.5.
Python 3.12 was unavailable locally, so the same commands still require the
repository's Python 3.12 CI job before merge.

| Command | Result |
|---|---|
| `scripts/validate_contracts.py` | PASS: 5 schemas, 11 examples, 4 event routes, Gateway OpenAPI. |
| `-m pytest -q` | **233 passed, 7 skipped**. |
| `-m compileall -q services/discovery-monitoring/goalpulse_discovery services/data-evidence/goalpulse_evidence` | PASS. |
| `git diff --check` | PASS. |

**NOT RUN / skipped:** four DE and three DM actual PostgreSQL cases. Test DSNs,
driver/database and PostgreSQL/Docker tools are unavailable. After provisioning
the dedicated databases and approved driver, run:

```powershell
.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests/test_postgres_live.py
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_discovery_postgres_live.py
```

**NOT RUN:** Context.dev smoke/product acceptance; no local key. Set the key only
in the local process environment, never in chat/source/reports, then run:

```powershell
.venv/Scripts/python.exe services/discovery-monitoring/probe_context.py https://PUBLIC-MERCHANT/PRODUCT
```

**NOT RUN:** real RabbitMQ confirms/recovery, real monitor/webhook delivery, HTTP
M2M authorization and multi-service acceptance. These require the approved
contracts/platform wiring above. Offline tests are not operational proof.
