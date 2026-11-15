# R2-D1-03 release gate runbook

This gate coordinates existing service-owner tests. It does not change frozen
v1 schemas, routing keys, public endpoints, service databases, or provider trust
rules. Recorded, durable, frontend, Compose, and controlled-live results remain
separately labelled.

## Deterministic recorded lane

Use Python 3.12 with the repository and all service requirements installed:

```bash
python3.12 infrastructure/release/run_phase2_gate.py \
  --lane recorded --output /tmp/r2-d1-03-recorded.json
```

The runner clears `ANTHROPIC_API_KEY` and `CONTEXT_DEV_API_KEY`. Service tests
must inject their recorded transports; an accidental provider call fails rather
than switching to a fixture. The JSON artifact contains the commit, command,
duration, result, output digest, and a bounded redacted diagnostic tail. It must
not contain bearer values, DSNs, prompts, raw goals, provider bodies, or secrets.

## Disposable PostgreSQL and RabbitMQ lane

Use only loopback services created for testing. Configure the variables used by
the opt-in service tests (`GI_TEST_POSTGRES_URL`,
`GOAL_MANAGEMENT_TEST_DATABASE_URL`, `GOALPULSE_DM_TEST_DSN`,
`GOALPULSE_DE_TEST_DSN`, `GOALPULSE_GE_TEST_DSN`,
`GOALPULSE_GE_TEST_AMQP_URL`, `GOALPULSE_NS_TEST_DSN`,
`GOALPULSE_AI_TEST_DSN`, and `GOALPULSE_AI_TEST_AMQP_URL`). The CI workflow is
the executable reference and creates only fixed disposable databases plus
temporary per-test schemas/queues.

After applying Goal Management's migration to its dedicated test database:

```bash
python3.12 infrastructure/release/run_phase2_gate.py \
  --lane durable --output /tmp/r2-d1-03-durable.json
```

For the isolated local Compose project created by this task, the wrapper reads
the protected `/tmp` environment without echoing connection values, provisions
the fixed test databases/exchanges, applies the GM migration, and invokes the
same lane:

```bash
python3.12 infrastructure/release/run_local_durable.py \
  --env-file /tmp/goalpulse-release.env \
  --output /tmp/r2-d1-03-durable.json
```

This lane proves persistence across fresh connections, concurrent deduplication,
manual Rabbit acknowledgements, persistent messages, replay, and rejection/DLQ
behavior. It does not call Anthropic or Context.dev.

## Frontend and Compose lanes

The Phase 2 release workflow selects Node 22, runs the browser/build lane, and
retains its redacted JSON report. The existing frontend no-silent-mock checks
still prove that `demo + mock` and missing mode settings fail. The Compose job
generates a non-live `dev` environment below `/tmp`, validates expansion, builds
the stack, waits for required readiness, restarts PostgreSQL and RabbitMQ, and
requires the stack to recover readiness. Frontend and Gateway are published on
loopback for host Nginx; all service APIs remain container-private.

## Controlled live smoke (separate authorization required)

Do not run this from default CI. Obtain explicit approval for one synthetic,
non-sensitive goal and bounded provider cost, then use privately supplied demo
credentials, `GI_LLM_PROVIDER=anthropic`, a provisioned model, a Context.dev key
and access-policy IDs. Never place any value in command arguments or artifacts.

1. Start the isolated `demo` Compose stack with the frontend in `gateway` mode. Startup
   must fail if a provider, auth setting, or real-mode requirement is absent.
2. Sign in as demo user A in the browser. Submit the approved synthetic prompt,
   record only provider name, status, latency and request/correlation IDs, and
   confirm the exact displayed revision.
3. Observe the actual activation queue and DM main worker. Record only source ID,
   provider request ID, timestamps, cache age and sanitized outcome—not URL query
   credentials, page text, prompt, or provider response.
4. Verify that DE stored an observation or durable failure and that GE/UI shows
   the truthful result. Missing shipping, stock, tax, condition or freshness must
   remain Candidate/Unknown. Do not manufacture a live Verified result.
5. Replay the event and confirm notification business deduplication. Do not call
   checkout, payment, booking, email, or merchant-order endpoints.
6. Save a reviewed redacted artifact outside the repository. Record the command,
   commit, UTC time and PASS/FAIL/NOT RUN in the release evidence report.

Until both provider calls and the browser main path are observed, A1/B1 and the
overall Phase 2 release remain blocked even when all automated lanes pass.
