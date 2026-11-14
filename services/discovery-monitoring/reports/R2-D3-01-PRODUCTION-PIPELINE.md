# R2-D3-01 — real activation-to-evidence runtime

Date: 2026-11-14. Owner: Developer 3. Branch:
`feat/r2-d3-01-production-pipeline`. Allowed paths: Discovery & Monitoring and
Data & Evidence only. Existing uncommitted R2-D3-02 work was preserved. D1's
accepted R2-D1-00 baseline and ADR-0002/0003 are present; contracts were not edited.

**Status:** implementation and recorded acceptance PASS. The required controlled
live provider/DB/broker/GE acceptance is **NOT RUN**, pending the running stack
and its provisioned configuration. This report does not mark live acceptance done.

## Implemented behavior

- Normal DM startup instantiates the existing real ContextProvider, authenticated
  GM GoalReader and DE EvidenceSink, owned PostgreSQL registry/jobs/plans/intents,
  and durable quota guards. GM scopes are `gm.runtime.read` and
  `gm.goal-version.read`; DE uses `de.extractions.write`. Short-lived signed
  service JWTs and the original activation correlation ID propagate through HTTP.
- The main Rabbit callback validates the frozen activation and commits inbox/job
  state before manual ACK. Invalid events/exhausted retries dead-letter through
  D1's existing queue; a persistence retry is ACKed only after a confirmed,
  persistent, mandatory retry copy. Initial provider work runs in the job worker,
  independently of monitor baselines.
- Discovery rechecks current goal/version/owner and FI support. It caps initial
  sources at three, filters seller domains, screens candidate and replay URLs
  against public DNS, and rejects unsafe returned targets/cross-host redirects.
  Same-host final product URLs receive their canonical source identity.
- Accepted Context product responses map to the exact frozen ExtractionSubmission.
  EUR prices become integer cents using exact decimal/rational arithmetic; product
  SKU is not guessed to be model. Missing stock, FI delivery, shipping and tax stay
  unknown. Failed/non-product/missing-price outputs become DE 202 failure records.
  Provider checked assertions and instructions in Markdown cannot promote trust.
- Original requested/fetched/provider-observed/cache/request metadata is retained
  privately. The v1 cache field conservatively encodes the oldest known capture
  time without introducing another wire field or relabeling cache as fresh.
- Immutable source plans and stable per-goal/version/URL attempt IDs survive
  restarts. Exact submission JSON commits before HTTP. A lost DE reply or failed
  local receipt commit replays that same JSON; completed receipts are reused,
  avoiding another scrape or conflicting payload under an existing attempt ID.
- Provider calls have one HTTP attempt per quota reservation and bounded durable
  job retries/backoff/deadlines. Existing concurrency, cooldown, credit and daily
  operation guards apply. This initial service-wide cap is not the per-user
  scheduler/quota feature assigned to R2-D3-03.
- DM now has private app/consumer/worker/readiness entrypoints. DE now has a
  PostgreSQL API factory, migration-aware readiness and a confirmed Rabbit outbox
  publisher entrypoint. Startup fails closed on missing config/fixture mode;
  dev/demo reject fixture observations. Operational errors are redacted before
  HTTP server logging, and access logs are disabled.

## Acceptance results

| Ticket criterion | Result | Evidence |
|---|---|---|
| Recorded HTTP activation -> durable job -> DE immutable observation/failure, duplicate-safe | PASS, recorded/offline | Main callback + worker wiring; real authenticated GM and DE ASGI apps; actual ContextProvider with recorded transport; immutable evidence/outbox and 202 failure assertions. |
| Actual Context.dev through main activation persists real metadata and is visible to running GE | NOT RUN | Key is configured, but no local PostgreSQL/RabbitMQ/GM/DE/GE stack or service configuration was available. Existing GE has no runnable adapters here. |
| Missing shipping/tax/stock stays unknown; unsafe/private targets denied; keys not logged | PASS, recorded/offline | Exact-mapping, private-DNS/final-URL, redaction, profile, ownership/version and GE-unknown tests. Provider-side egress enforcement remains a separate live check. |

The recorded test calls the real GE evaluator against an observation fetched
through authenticated DE HTTP, and verifies `unknown`/no opportunity. That is
consumer compatibility evidence, not proof of a running GE consumer.

## Changed files and purpose

| Service-relative files | Purpose |
|---|---|
| DM `goalpulse_discovery/adapters.py` | Accepted service JWT/HTTP clients and exact conservative extraction mapping. |
| DM `context_provider.py` | Retain request-start timestamp in the private ScrapeResult without breaking existing constructors. |
| DM `workflow.py` | Durable plans, stable attempt IDs, resume-before-scrape, source/final-URL validation, and receipt-based outcomes. |
| DM `deliveries.py`, `migrations/0006_initial_pipeline.sql` | Immutable source plans, exact submission intents, raw capture provenance and receipt persistence. Prior migrations unchanged. |
| DM `consumer.py` | Main manual-ACK callback, confirmed bounded retry, passive existing topology checks. |
| DM `runtime.py`, `__main__.py`, `api.py` | Real runtime/app factory, scoped bounded connections, private HTTP/consumer/worker CLI and readiness/redaction. |
| DM `requirements.txt` | Declare Pika for this service's new broker entrypoint; it is already an approved repository driver and installed locally. |
| DM `tests/test_adapters.py`, `test_runtime_pipeline.py`, `fake_pipeline.py` | Recorded authenticated HTTP pipeline, exact mapping, identity, dedup, unknowns and lost-reply recovery. |
| DM `tests/test_activation_consumer.py`, `test_submission_store.py`, `test_initial_pipeline_postgres.py`, `test_runtime_settings.py` | ACK/confirm/poison handling, immutable intents, opt-in real SQL, config/readiness/redaction guards. |
| DM `tests/test_context_provider.py`, `test_workflow.py` | Request timestamps and corrected recorded scrape URL fixtures for strict final-source validation. Expectations were not loosened. |
| DE `goalpulse_evidence/runtime.py`, `__main__.py`, `api.py` | Production API/publisher wiring and non-test fixture rejection, preserving R2-D3-02 checked protections. |
| DE `tests/test_evidence_runtime.py` | Startup/migration/readiness/profile, redaction and confirmed-publisher tests. |
| DM/DE `README.md`, DM `.env.example`, DE current-status report, this report | Runtime/configuration, evidence and owner handoff. The real ignored `.env` was not edited. |

## Validation

Executed from repository root with the existing `.venv`, Python 3.13.5. No
dependency was installed during this ticket. No external service was started,
port exposed, provider credit spent, shared file changed or database dropped.

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe scripts/validate_contracts.py` | PASS: 5 schemas, 13 examples, 4 event types and frozen Gateway OpenAPI. |
| `.venv/Scripts/python.exe -m pytest -q -rs` | PASS: **1072 passed, 15 skipped**. |
| `.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests` | PASS: **232 passed, 4 skipped**. |
| `.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests` | PASS: **201 passed, 5 skipped**. |
| `.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_runtime_pipeline.py` | PASS: **17 recorded pipeline tests**. |
| `python -m goalpulse_discovery --help` (service directory, repository venv) | PASS: API/consumer/worker/ready CLI loads. |
| `git diff --check` | PASS. |

All 15 skips are unconfigured PostgreSQL tests: five DE, four DM, six GI. Actual
PostgreSQL trigger/transaction/concurrency checks and RabbitMQ confirms/recovery
are **NOT RUN**. After provisioning the owned test databases, run from root:

```powershell
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_discovery_postgres_live.py services/discovery-monitoring/tests/test_initial_pipeline_postgres.py
.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests/test_postgres_live.py
```

Set `GOALPULSE_DM_TEST_DSN` to `discovery_monitoring_test` and
`GOALPULSE_DE_TEST_DSN` to `data_evidence_test` in the local process environment.
These tests append fixtures/apply forward migrations without dropping databases.

## Executed redacted smoke logs

The recorded smoke uses the explicit test-only Scenario transport/persistence,
real production composition and real authenticated HTTP handlers. It makes no
live network call and prints no credentials or raw payloads:

```text
RECORDED_SYNTHETIC activation_acks=2 worker=done next=idle observations=1 outbox=1 de_posts=1 context_requests=2
RECORDED_SYNTHETIC reliability=unverified stock=unknown FI=unknown shipping=None tax=None model=None
RECORDED_SYNTHETIC lost_reply=pending restart=done observations=1 context_requests=2 identical_payload=True
RECORDED_SYNTHETIC failed_product attempts=1 observations=0 http=202
```

Real startup was attempted through both main CLIs, with the currently available
configuration; neither silently selected fixtures or contacted Context.dev:

```text
DM worker --once: exit 1
discovery_runtime=unavailable
DE publisher --once: exit 1
{"component": "data-evidence", "status": "startup_failed", "reason": "GOALPULSE_PROFILE must be test, dev, or demo"}
```

Context key presence was checked through the existing loader as a boolean only.
No configured service URLs/DSNs/JWTs or running standard local service ports were
found; Docker/psql were unavailable. The missing runtime configuration was asked
for while independent implementation continued. Actual live acceptance remains
pending that external state, rather than being replaced by a fixture claim.

## Contracts, integration and handoff

Frozen schemas, HTTP contracts, event routing and other owners' source files are
unchanged. DM consumes only `goal.activated.v1`; DE alone emits the existing
`evidence.updated.v1`. Manual ACK and transactional outbox semantics remain intact.

Apply DM migration 0006 and the existing DE migrations through their owned runners
before readiness. Deployment roles must disallow TRUNCATE/DDL bypass of immutable
records. DM now declares existing Pika; no new framework, broker or shared library
was added. New DM configuration names are `DISCOVERY_MONITORING_DATABASE_URL`,
`GOAL_MANAGEMENT_BASE_URL`, `DATA_EVIDENCE_BASE_URL`, `DISCOVERY_SERVICE_ID`,
`DM_INITIAL_MAX_OPERATIONS_PER_DAY` and optional `DM_PROVIDER=context_dev` /
`GOALPULSE_SCHEMA_DIRECTORY`. DE adds `DATA_EVIDENCE_DATABASE_URL` and optional
`DE_EVIDENCE_MODE=real`; existing shared profile/JWT/Rabbit settings are reused.

D1 must supply actual private origins, scoped DB credentials, provisioned service
UUID/JWT configuration and existing Rabbit topology. Follow each README to run
DM API/consumer/worker and DE API/publisher. For an explicitly approved real goal
already activated through GM, run `consume --once`, then `worker --once`, then
`publisher --once`; inspect source-backed DE data and the D4 running evaluator.
These commands do not create an approval or invent activation events.

Two accepted-interface limits remain: ExtractionSubmission has no independent
seller representation/condition/proof field, and DM has no goal-to-source public
or internal route. This task does not invent either. HTTP evidence therefore
remains unverified until D1 approves a proof-transport extension or a genuinely
internal capture integration. GE's new-condition unknown gate is preserved.
D4 owns actual runtime/consumer wiring and its approved source projection.

Public-URL/DNS checks run locally and validate returned final URLs; Context.dev
resolves independently. Its remote redirect/DNS egress policy must still be
validated during live acceptance. A local preflight alone is not full provider
SSRF enforcement.

Periodic scheduling, user-specific quotas, active-goal limits and operator check
now remain R2-D3-03. This task's initial discovery/job/operation bounds must not
be represented as those later features.

Provider mapping is based on the official [Product response](https://docs.context.dev/scrape/product)
and [Scrape API](https://docs.context.dev/scrape/overview); provider outputs remain
untrusted candidates, never guaranteed checkout availability.
