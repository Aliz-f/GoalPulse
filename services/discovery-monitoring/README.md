# discovery-monitoring — GoalPulse service

**Implementation status: service components for D3-DM-01..06 implemented and tested offline.**
Provider adapter, PostgreSQL registry/jobs, discovery worker, monitor lifecycle,
webhook verification/enqueue core, direct-operation limits, and the accepted
authenticated source read routes are present. Production worker/broker wiring and
live acceptance remain pending platform setup. R2-D3-01 now provides production
API/consumer/worker entrypoints and authenticated GM/DE adapters; see below.

- [Service specification](../../docs/implementation/03-discovery-monitoring.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task
file. Follow versioned frozen contracts. The historically named
`internal.proposed.v1.yaml` is accepted under ICP-01/08 and ADR-0002. Do not guess
additional interfaces or read other services' databases.

## D3-DM-01 provider adapter

`goalpulse_discovery/context_provider.py` uses Python 3.12+ and the standard library;
it adds no runtime dependency. Inject `Transport`, DNS resolver, clock and sleep in
tests. Operations: bounded Search (up to five results), fresh product + Markdown
Scrape, page monitor creation, and monitor-run retrieval. The REST requests follow
the [official quickstart](https://docs.context.dev/quickstart),
[Search](https://docs.context.dev/search/overview),
[Product](https://docs.context.dev/scrape/product), and
[Monitors](https://docs.context.dev/monitors/overview) documentation.

- API requests use TLS to fixed `api.context.dev`; API redirects are rejected.
- Initial and returned target DNS must resolve entirely to public addresses.
  Demo startup additionally requires a provisioned Context.dev allowlist access
  policy, and its ID is sent on Search, Scrape and Monitor requests. Deployment
  must still validate the provider-side policy content; an opaque ID is not proof.
- Retries are bounded (three attempts by default), with exponential backoff and
  `Retry-After` support. Long delays are returned to the future scheduler instead
  of retrying earlier than the provider allows. Permanent 4xx errors are not retried.
- Monitor creation is deliberately attempted once: the documented API supplies no
  idempotency key, so ambiguous timeouts require future registry reconciliation.
- HTTP 200 is not format success. Failed outputs expose no product data. Cache
  age determines observation time; absent cache provenance remains unknown.
- Creation returns a baseline run ID, never an evidence event. Webhook secrets
  are excluded from representations; storing them securely belongs to DM-04.

From repository root, after installing the existing development requirements:

```powershell
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests
.venv/Scripts/python.exe services/discovery-monitoring/probe_context.py https://PUBLIC-MERCHANT/PRODUCT
```

For local configuration, edit `services/discovery-monitoring/.env` and replace
`replace_with_your_context_dev_api_key` with your real key:

```dotenv
CONTEXT_DEV_API_KEY=replace_with_your_context_dev_api_key
```

The probe automatically reads this service-local file regardless of its working
directory. `.env` is ignored by Git; `.env.example` contains only the placeholder
template for fresh checkouts. An existing process environment variable takes
precedence (including an explicitly empty value). The loader reads only this key;
it does not execute shell commands or expand variable references. Keep comments
on separate lines. No third-party configuration dependency is required.
`CONTEXT_DEV_ACCESS_POLICY_IDS` is a runtime variable injected/exported by D1;
the standalone probe does not read it from the service-local file or claim the
demo provider-side SSRF acceptance.

The opt-in probe spends provider credits on an example.com smoke request and one product scrape;
it creates no monitors. Its JSON output contains coverage flags and cache age,
never raw page text, product values, secrets or query-bearing URLs. Exit 2 means
NOT RUN (missing key); exit 1 means extraction limited/failed; exit 0 means a
named/priced product was returned, without asserting checkout availability.

See [D3-DM-01 report](reports/D3-DM-01.md) for the live-test limitation and handoff.

## D3-DM-02..06 private components

- `registry.py` + `migrations/0001_sources.sql`: canonical public HTTPS sources,
  variant-preserving URL deduplication, versioned subscriptions and reconciliation.
- `workflow.py`, `jobs.py` + `0002_activation_jobs.sql`: strict frozen activation
  validation, transactional inbox/job creation, bounded discovery and an immediate
  initial scrape. Goal/version runtime is checked before provider work and again
  before submission. Permanent errors/deadlines go to durable `dead` job state;
  transient errors use bounded backoff and respect provider Retry-After.
- `monitoring.py` + `0003_monitors.sql`: durable reservations prevent blind duplicate
  creation, store only secret-manager references and keep uncertain creation for
  reconciliation. Source eligibility must be rechecked through the injected
  `source_active` callback (e.g. `SourceCheck.active`). Baselines are stored separately.
  Reconciliation refreshes every linked goal, preserving shared monitor subscribers.
- `webhooks.py`, `source_checks.py` + `0004_webhook_jobs.sql`: raw-byte HMAC-SHA256,
  300-second timestamp tolerance, body limits, header/body and expected-monitor
  matching, durable event-ID deduplication and one follow-up job per monitor/run.
  No expensive fetch occurs in receipt handling. The trusted receiver config chooses
  the secret reference; body claims never choose identity or a target URL.
- `limits.py` + `0005_provider_limits.sql`: PostgreSQL-coordinated daily operation
  limits/credit reservations, resource cooldowns, bounded per-process concurrency,
  sanitized errors and explicit unknown usage. Unknown cost remains reserved until
  reviewed reconciliation; scheduled monitor usage is explicitly unknown. Counts
  are logical direct operations, not network attempts or total account charges.

`persistence.Database(connect).migrate(service_migrations_directory)` applies these
owned migrations with checksums and an advisory lock. Inject a fresh psycopg 3
connection factory with autocommit disabled, scoped to `discovery_monitoring`.
No driver, HTTP server, credential storage backend or background process is
installed/started automatically. `SecretStore` needs a real server-side secret
manager before webhook-enabled monitor creation can be used operationally.

`goalpulse_discovery.api.create_app` exposes the accepted authenticated source
metadata/subscription reads plus ADR-0005's additive complete goal-version source
lookup. It requires a signed service JWT with
`dm.sources.read`; token issuer, audience and secret use the D1-owned
`SERVICE_JWT_*` settings. GoalReader and EvidenceSink remain injected ports for
production client/startup wiring; they do not invent alternate wire shapes.
The provider receipt core also has no HTTP ingress yet: coordinated routing must
provide trusted per-monitor receiver configuration, raw-body limits and error
status translation. RabbitMQ queue/manual-ACK/retry/DLQ startup wiring belongs to
the platform integration. ACK only after ActivationInbox.accept returns durably.
The service-only Check Now HTTP hook remains held pending its signed-off design.

Provider signature/baseline behavior follows the official
[webhook protocol](https://docs.context.dev/webhooks),
[monitor event reference](https://docs.context.dev/monitors/webhooks) and
[schedule API](https://docs.context.dev/monitors/schedules).

After approved psycopg provisioning and a disposable owned test DB, set the
test-only `GOALPULSE_DM_TEST_DSN` locally to database `discovery_monitoring_test`:

```powershell
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_discovery_postgres_live.py
```

These three opt-in tests apply forward migrations and append synthetic fixtures.
They do not create/drop/truncate databases or contact Context.dev. Without the
test DSN they skip explicitly. Full status is in the
[Developer 3 handoff](../data-evidence/reports/DEVELOPER-3-HANDOFF.md).

## R2-D3-01 production initial-evidence pipeline

`goalpulse_discovery.runtime.build_runtime` composes the real `ContextProvider`,
owned PostgreSQL registry/jobs/quotas, authenticated GM reads and DE submissions.
`runtime.app_from_env` is the private Uvicorn app factory. No mock provider,
in-memory database or fixture fallback is selected by normal startup. Explicit
infrastructure doubles are accepted only by the test profile.

Provide these server-side process environment values through D1's private runtime
configuration. Only `CONTEXT_DEV_API_KEY` also reads the ignored service-local
`.env`; the other settings must be exported/injected, not left only in that file.

| Setting | Meaning |
|---|---|
| `GOALPULSE_PROFILE` | `test`, `dev`, or `demo`; required. |
| `DISCOVERY_MONITORING_DATABASE_URL` | Scoped PostgreSQL DSN selecting `discovery_monitoring`; test may select `discovery_monitoring_test`. |
| `RABBITMQ_URL` | D1-provisioned broker/vhost with the existing durable topology. |
| `GOAL_MANAGEMENT_BASE_URL`, `DATA_EVIDENCE_BASE_URL` | Actual private service origins supplied by D1; no URL credentials, query or path. |
| `DISCOVERY_SERVICE_ID` | Provisioned UUID machine subject. |
| `SERVICE_JWT_SECRET`, `SERVICE_JWT_ISSUER`, `SERVICE_JWT_AUDIENCE` | Existing accepted ICP-08 credentials; signing secret at least 32 bytes, no repository placeholder. |
| `CONTEXT_DEV_API_KEY` | Real provider key, server-side only; existing local key loader remains supported. |
| `CONTEXT_DEV_ACCESS_POLICY_IDS` | Comma-separated IDs for D1-provisioned Context.dev allowlist policies; required in demo unless the ADR-0006 exception is explicitly enabled. |
| `DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY` | Default `false`. Set `true` only for the controlled Junction demo while provider policies are unavailable; resulting captures cannot become checked. |
| `DM_MAX_SOURCES_PER_GOAL` | Initial source cap, default 3, maximum 3. |
| `DM_INITIAL_MAX_OPERATIONS_PER_DAY` | Initial service-wide direct-call cap, default/max 100; every Context HTTP attempt consumes a reservation. |
| `GOALPULSE_SCHEMA_DIRECTORY` | Optional installed location of the unchanged shared JSON Schemas. |

`DM_PROVIDER`, if set, must be `context_dev`. Runtime uses one HTTP attempt per
provider reservation, with bounded durable job backoff instead of hidden HTTP
retries. Existing resource cooldown, concurrent-call and credit reservation guards
also apply. R2-D3-03 adds the per-user limits, active-goal admission, periodic scans
and operator checks described below; this initial service-wide budget is separate.

Run each component in its own process from `services/discovery-monitoring/`, using
the existing repository virtual environment. APIs bind loopback by default; D1
controls admission/private container networking.

```powershell
../../.venv/Scripts/python.exe -m goalpulse_discovery api
../../.venv/Scripts/python.exe -m goalpulse_discovery consume
../../.venv/Scripts/python.exe -m goalpulse_discovery worker
```

Startup applies all owned forward migrations before readiness. Private
`/health/live` reports process health; `/health/ready` checks DB access and exact
migration checksums. Readiness makes no provider call or claim that all upstream
services/broker consumers are running. Missing credentials/configuration and
startup failures produce fixed redacted errors. Uvicorn access logs are disabled.

The activation consumer passively checks D1's existing
`goalpulse.discovery-monitoring.goal.activated.v1` quorum queue and its topology;
it does not create a competing queue. Manual ACK follows durable inbox/job commit,
or a confirmed persistent retry copy on a transient persistence failure. Invalid
or exhausted deliveries use the configured DLQ. Provider work runs separately
from the broker callback. Duplicate activation never schedules a second initial
job for the same goal/version.

Migration `0006_initial_pipeline.sql` stores immutable source plans and exact
ExtractionSubmission intents plus private request/capture/cache provenance.
Stable attempt IDs survive worker restarts. The intent commits before HTTP; after
an ambiguous response or receipt-commit failure, the next worker reuses that
payload instead of scraping again or fabricating a new timestamp. Completed
receipts are reused. Missing price/non-product/failed extraction results reach
DE as durable failures. Missing shipping/tax/FI eligibility/stock stays unknown.
A merchant SKU is emitted as an unverified model candidate only when separately
captured first-party JSON-LD has an exact matching model, and can become checked
only after DE independently validates that proof.

Initial and persisted source URLs are checked against public DNS and seller-domain
constraints. Returned private targets/cross-host redirects are rejected; accepted
same-host final URLs get their own canonical source identity. The remote provider
resolves independently, so demo fails closed by default without configured
Context.dev allowlist access-policy IDs. ADR-0006 permits an explicit
controlled-demo exception: the real request omits `policies`, and the capture
stays unverified because no provider-policy proof exists. A normal live
deployment must verify configured policies cover redirects; local DNS and an
opaque policy ID are not sufficient.

For a controlled real activation already queued by GM, use `consume --once` then
`worker --once`, with the real DE API/outbox publisher running. `--once` neither
creates a goal nor bypasses approval. Run the recorded production-path acceptance
tests from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_runtime_pipeline.py
```

See [R2-D3-01 evidence and handoff](reports/R2-D3-01-PRODUCTION-PIPELINE.md) for
actual results, live-stack blockers and contract limitations.

## R2-D3-03 periodic scans and operator checks

`scheduling.ScanScheduler` now reconciles known goal/job/source state through
authenticated GM reads, admits at most **two active monitored goals per owner**,
and queues durable due scans. The owner comes from GM, never an operator/user
argument. GM still owns its lifecycle and acceptance limits; DM admission can
block monitoring for an additional active GM goal without rewriting that goal.

Run the scheduler alongside the existing API, consumer and worker processes:

```powershell
../../.venv/Scripts/python.exe -m goalpulse_discovery scheduler
../../.venv/Scripts/python.exe -m goalpulse_discovery scheduler --once
```

The initial worker registers the approved interval and first due time after its
initial scan. Due scans and manual checks use the same discovery/submission worker
and bounded retry logic. Each scan gets its own durable identity and immutable
source plan; retries reuse it and the exact staged HTTP payload. Row locks and
a unique pending-goal index prevent duplicate enqueue/claims across processes.
Missed intervals coalesce into one due scan rather than a catch-up request burst.
The scheduler polls every five seconds; jobs run on the next worker poll.

The policy default is **60 minutes**, minimum **30 minutes**. Frozen GoalSpec v1
requires an explicit monitoring frequency, which DM always honors. GI/GM must
put the default 60 into the reviewed draft/spec when the user did not specify a
cadence. A confirmed value below 30 produces
`INTERVAL_RECONFIRMATION_REQUIRED`, blocks all scans, and leaves the requested
value intact. A newly approved supported interval is required; neither the
scheduler nor an operator silently substitutes 30. Longer approved intervals,
including existing daily goals, stay unchanged.

Current GM reads occur before each Context request and before evidence submission.
Paused/cancelled/stale versions perform no further provider work. Reconciliation
releases monitoring admission slots on an authoritative pause. Source replacement
and pause/resume reconciliation retain at most **three active seller URLs** per
goal/current version; blocked monitoring suspends subscriptions without inventing
a GM lifecycle change.

`UsageStore` atomically reserves each actual Context HTTP attempt against the
server-derived owner's UTC-day counter, capped at **100**. Failed/uncertain calls
remain charged; DE receipt replays consume no Context calls. The existing global
daily operation/credit and resource cooldown guards still apply, so a service-wide
budget can stop work earlier. `USER_DAILY_CALL_LIMIT`, `ACTIVE_GOAL_LIMIT` and other
fixed reasons are persisted and available through worker output/local status.
No rate-limit denial is hidden by a fixture fallback.

Additional process configuration (also listed in `.env.example`):

- `DM_MAX_ACTIVE_GOALS_PER_USER`: default/max 2; a lower deployment cap is allowed.
- `DM_MAX_REQUESTS_PER_USER_PER_DAY`: default/max 100; a lower cap is allowed.
- `DM_OPERATOR_IDS`: trusted deployment allowlist of OS principals, comma-separated;
  empty denies operator commands. Windows uses `sid:<SID>` from the process token;
  POSIX uses `uid:<effective UID>`. Provision and protect this alongside service
  credentials. A username/environment role or `--operator-id` is not accepted.

Operators use the local CLI; no admin/public HTTP route was added. Authorization
runs before runtime/credential loading. Run from the service directory, replacing
the UUID placeholders and reusing the same request ID when retrying a command:

```powershell
../../.venv/Scripts/python.exe -m goalpulse_discovery check-now --goal-id <GOAL_UUID> --request-id <REQUEST_UUID>
../../.venv/Scripts/python.exe -m goalpulse_discovery schedule-status --goal-id <GOAL_UUID>
```

Check-now queues/coalesces the common job and needs the normal worker process.
Repeated request IDs return their prior scan identity; a pending initial/periodic
scan is reused. Privilege does not bypass cadence, lifecycle, source or cost guards.
Status shows policy reason, last result and next due time. Native Context monitors
and public provider webhooks remain deferred P1; no webhook route is enabled.

Apply owned migration `0007_periodic_scans.sql` before starting any new runtime.
It adds schedules, scan jobs/plans, immutable operator request records and per-user
call counters. Earlier migrations and all shared contracts remain unchanged.

See [R2-D3-03 evidence and handoff](reports/R2-D3-03-SCHEDULER-COST-GUARD.md) for
the recorded restart/clock tests, actual test results and live-DB limitations.
