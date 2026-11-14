# discovery-monitoring — GoalPulse service

**Implementation status: D3-DM-01 adapter implemented; live acceptance pending.**
No HTTP service, activation consumer, source registry, scheduler or webhook receiver
is implemented yet. ICP-01 approvals are still pending.

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
