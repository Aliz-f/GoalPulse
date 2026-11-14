# 03 — Discovery & Monitoring Service (Developer 3)

**Ownership:** `services/discovery-monitoring/`, DB `discovery_monitoring`, `CONTEXT_DEV_API_KEY` and Context.dev per-monitor webhook secrets. **Criticality:** P0. Does not decide goal satisfaction.

## Inputs, outputs, and events

- Consume ONLY declared `goal.activated.v1` from RabbitMQ; fetch exact GoalSpec via GM internal authenticated API.
- For initial activation, discover, store, subscribe and immediately run initial search/scrape of relevant sources. Monitor baseline alone is insufficient.
- Context.dev sends signed webhooks → DM validates and deduplicates, then schedules fetch/extraction.
- Submit untrusted extractions via DE internal HTTP; DE owns Observation schema, validation and `evidence.updated.v1` publication. DM must not publish `evidence.updated.v1` itself.
- Internal read `GET /internal/v1/sources/{source_id}/subscriptions` returns active `goal_id,goal_version` links for GE to resolve impacted goals; `GET /internal/v1/sources/{source_id}` returns canonical source and monitor metadata without secrets.

## Owned tables

- `sources(source_id PK, canonical_url UNIQUE, hostname, source_type, source_fingerprint UNIQUE, created_at, last_attempt_at)`.
- `goal_source_links(goal_id, goal_version, source_id, state, created_at, updated_at, PK(goal_id,goal_version,source_id))`.
- `monitors(id PK, source_id, provider_monitor_id UNIQUE, target_type, policy_fingerprint, state, webhook_secret_ciphertext/ref, schedule, last_run_at, next_run_at, last_error)`.
- `monitor_runs(id PK, provider_run_id UNIQUE, monitor_id, observed_at, status, provider_request_id, cache_age_ms, error_code)`.
- `webhook_inbox(delivery_id PK, monitor_id, received_at, processed_at, signature_valid, payload_hash)`; `inbox(event_id PK, processed_at)` for RabbitMQ.
- `discovery_jobs(id PK, goal_id,goal_version,status,attempts,deadline_at,last_error,created_at)`.

## Source discovery

1. Parse type and product queries from GoalSpec, restrict to supported `delivery_country=FI` and required categories.
2. Use official `Context.dev` Search where viable; prefer credible publicly accessible merchant product pages; rank by relevance/source coverage, not imaginary prices.
3. Canonicalize URLs (remove tracking params, normalize host, reject query variants hiding different products); dedup by URL+monitor policy fingerprint. Store raw source URL and canonical URL; vendor/site robots/legal constraints respected.
4. Restrict to bounded number of sources (e.g. max 5 initial sources per goal and max 2 concurrent scrapes in development, **configurable**, not product contract). Budget controls protect API credits and rate limits.
5. No assumption that web Search is purchase-date/stock truth. Initial fetch must check `success` for each requested output, cache metadata and provider time; fallback to `unknown`/logged extraction failure.

## Context.dev monitor lifecycle

- Follow API docs: https://docs.context.dev/monitors/overview . Support `page` target with semantic instructions or `search` for topic discovery; first run establishes baseline and does **not** emit a change.
- Create monitor only for eligible publicly retrievable sources, store `provider_monitor_id` and **secret server side**. Multiple goal subscriptions may reuse it. Shared monitor is deleted/paused only if no active subscribers and permitted policy dictates.
- Webhook endpoint `POST /internal-provider/webhooks/context` (private/admitted external ingress, explicitly route only this endpoint); validate exact signed delivery according to provider docs, timestamp / anti-replay, per-monitor secret, body limits. Return 2xx only after durable inbox/enqueue; no expensive scrape in request thread.
- If webhook body says change detected, enqueue run/scrape and submit result to DE. Provider event is a trigger, **not an Observation**.
- Provide `Check Now` internal operation in a **PROPOSED** signed service API (not a new public `/v1` path by stealth); admin/demo-only, rate limited.
- Pause/resume/cancel: because frozen event v1 has no lifecycle change keys, DM reconciliation worker calls GM current-state API periodically and deactivates obsolete subscription links. GE/NS perform their own final runtime checks.

## Inter-service contracts (ICP-01 draft)

- `GET /internal/v1/sources/{source_id}/subscriptions` returns `{source_id,subscriptions:[{goal_id,goal_version}]}`; only active links per DM's last reconciliation, GE must still recheck GM runtime.
- `GET /internal/v1/sources/{source_id}` returns public URL, source_id, type, last_attempt_at (no webhook secret).
- `POST /internal/v1/extractions` to DE: provider metadata, URL, captured_at, cache provenance, original extracted product attributes with explicit unknown/null, source_id, requested event correlation ID. Exact draft in `internal.proposed.v1.yaml`.

## Reliability, data safety

- Provider 200 does not guarantee success of requested `product`/`markdown` format. Detect `success:false`; store `monitor_run` error and do not invent `unit_price_eur_cents`.
- Cache: differentiate `fetched_at` and source `observed_at`, use `cache_metadata.age_ms` to derive provider observation time if needed; never mark cached snapshot as newly observed.
- SSRF: allow only public HTTPS targets; DNS resolve and validate after redirects, block RFC1918/private/link-local/metadata destinations, set timeout and max response limits; no bypass of login/paywalls.
- API key and webhook secrets never in frontend, events, logs or source DB plain text. Per-provider bounded backoff (`429` respects Retry-After), DLQ for unprocessable events, cost/usage counters.
- Webhooks can arrive out of order; dedup provider delivery IDs and provider run IDs. Avoid one webhook spawning unlimited recursive jobs.

## Acceptance tests

- On `goal.activated.v1`, DM discovers at least one allowed source and initial extraction occurs independent of monitor baseline.
- Same activated event twice creates no duplicate subscriptions or physical monitors.
- Two goals for same source reuse one monitor; pause one goal keeps the other subscribed.
- Valid signed webhook enqueue accepted once; invalid/replayed signature rejected and no fetch.
- Source returns 200 but product format `success=false`: logged failure, no fabricated Observation.
- Outbound unsafe URL blocked before fetching; redirects to internal IP blocked.
- Context.dev network timeout/rate limit uses bounded retry; invalid payload to DLQ without poison loop.
- Internal subscription API serves correct goal_version and excludes links reconciled inactive.

**Task IDs:** `D3-DM-01`..`D3-DM-06`. **Provider source:** https://docs.context.dev/monitors/overview and https://docs.context.dev/quickstart .
