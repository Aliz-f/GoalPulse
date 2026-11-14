# discovery-monitoring — GoalPulse service

**Implementation status: private components for D3-DM-01..06 implemented and tested offline.**
Provider adapter, PostgreSQL registry/jobs, discovery worker, monitor lifecycle,
webhook verification/enqueue core and direct-operation limits are present. HTTP
routes/auth and production worker/broker wiring remain pending accepted contracts
and platform setup. Live acceptance has not passed; ICP-01 approvals are still pending.

- [Service specification](../../docs/implementation/03-discovery-monitoring.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` needs formal signoff before becoming an implementation contract. Do not guess interfaces or read other services' databases.

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
- Initial target DNS must resolve entirely to public addresses. Unsafe returned
  URLs are rejected. Provider-side DNS/redirect egress enforcement still needs
  validation before DM-02's complete SSRF acceptance can be claimed.
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

Set `CONTEXT_DEV_API_KEY` only in the local process environment. The opt-in probe
spends provider credits on an example.com smoke request and one product scrape;
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

GoalReader and EvidenceSink are explicitly injected mock ports while ICP-01 is
pending. They do not implement proposed paths or send an ad hoc request payload.
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
