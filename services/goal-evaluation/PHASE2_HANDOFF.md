# R2-D4-01 — implementation and integration handoff

Status: **P0 service adapters/composition implemented; live acceptance blocked**.
Approved ICP-01 readers and the authenticated immutable-read endpoint are
implemented. Worker composition fails closed without the complete reverse
subscription binding. This does not establish a running end-to-end service.

## Implemented

`goal_evaluation.readers` implements GM immutable-version/runtime reads, DM
source subscriptions and DE exact/latest Observation reads. Each operation
requests its existing service scope from an injected token issuer and forwards
the validated correlation UUID. The standard-library HTTP transport has bounded
timeouts/body size, disables ambient proxies and redirects, and never includes
upstream bodies in diagnostic errors. Transient status/transport failures use
the existing processor's bounded retry mechanism; auth, missing resources,
redirects and invalid data fail closed. No fixture fallback is supplied.
Demo rejects fixture provenance. Returned missing costs and capture timestamps
are preserved exactly; these adapters cannot promote evidence to checked.

Token issuance must be bound to D1's accepted ICP-08 issuer before deployment.
Frozen schemas, HTTP operations and event routing are unchanged. The continuation
below adds owned runtime requirements and migration 004; it supersedes the
original reader-only increment's dependency/migration notes.

## P0 API, worker and policy continuation

- `api.py` exposes only the accepted private
  `GET /internal/v1/evaluations/{evaluation_id}`, plus private health/readiness.
  It uses `ImmutableEvaluationReader` to validate/detach the exact saved result,
  including its original goal version and timestamps. GET does not recompute
  history or make old evidence actionable. Required UUID correlation is returned;
  no-store applies to responses. Missing IDs return 404 and storage faults return
  a fixed, redacted 503. No proposed public evaluation-history operation was
  invented or routed.
- `auth.py` validates the existing D1 HS256 ICP-08 profile: explicit >=32-character
  secret, issuer, audience, service token kind, UUID subject, expiry/not-before
  and operation scope. Missing/forged/user/wrong-scope tokens return 403. It
  rejects unsigned algorithms and duplicate JWT fields, and never fetches keys
  from a token. D1 supplies the approved evaluation scope; no scope name/default
  signing secret is invented. Existing SERVICE_JWT_SECRET/ISSUER/AUDIENCE values
  can be bound by D1's process builder without introducing new production keys.
- `runtime.py` builds the P0 product dispatcher, existing EvidenceProcessor,
  VerificationGuard, EvaluationSink, owned SQL inbox/outbox store, durable retry
  coordinator, authenticated GM/DM/DE readers, publication guard and broker
  adapters. `sources_for_goal` is a required injected complete lookup; missing
  binding is a startup error. D1's required `dependencies_ready` must check the
  approved binding and upstream configuration. Runtime readiness additionally
  checks the broker connection/channels and owned migration-dependent tables.
  False readiness prevents consumption. Tests explicitly supply recorded
  lookups/transports; demo has no automatic fixture fallback and DE fixture
  provenance is rejected.
- `worker.py` asserts the existing durable topic exchange, a D1-named durable
  consumer queue with explicit DLX settings, and the evidence.updated binding.
  D1 provisions the DLX/DLQ destination/binding. Consumers use manual ACK after
  business/journal commit. Malformed/oversized/wrong-routing deliveries go through
  durable rejection before dead-letter NACK. Transient retries wait while
  servicing broker heartbeats, then requeue the same event. `run()` closes its
  connection on failure/shutdown. A supervisor must implement bounded reconnect
  backoff and broker/processing deadlines; no raw payload/exception is logged.
  Outbox processing also runs when the input queue is empty, allowing restart
  recovery of committed intent.
- `ConfirmedPublisher` enables confirms, persistent JSON properties and
  mandatory routing for only goal.evaluated and goal.opportunity_verified on
  goalpulse.events. Pika returns None after successful confirmed publication and
  raises on broker Nack/Basic.Return; these remain pending/terminal according to
  existing bounded outbox policy. A crash after broker ACK but before SQL commit
  may replay the same event ID; downstream durable inbox dedup is required.
- `retry.py` plus `004_processing_journal.sql` persist processing attempt count,
  next retry, bounded lease/token and fixed diagnostic codes independently of the
  rolled-back business transaction. Default max is five processing attempts,
  with 1/2/4/... second delays capped at 300. Validation/permission/unsupported
  domain failures are terminal. Lease recovery after domain commit uses the
  existing inbox and does not create another result/outbox. Stale workers cannot
  overwrite another lease token's journal result.
- `product.py` now has evaluator version `product-v1.3-phase2`; immutable
  evaluation identity includes this version. Product age is capped at 30 minutes.
  `VerificationGuard` additionally requires selected original proof no older than
  ten minutes (or a stricter goal limit) on enqueue and publication. A 20-minute
  checked offer can still be historically satisfied under the 30-minute domain
  policy, but cannot emit a current Verified alert without fresh proof. Missing
  shipping/tax/stock, future/stale/disputed evidence, inactive/obsolete ownership
  and missing `new` facts remain blocked.
- `offer_identity.business_fingerprint` now matches Notification's ordered
  ADR-0003 P0 SHA-256 fields and normalization. Source/listing paths and capture
  identifiers do not create a new business offer. v1's absent condition stays the
  explicit `unknown` component and never proves `new`. A JSON-only separate
  process test passes a real GE result/event to the actual Notification consumer
  and asserts its stored key equals GE's key. No service-private imports are
  shared. P1 procurement retains its existing policy pending its task.
- `EvaluationSink` recognizes an exact existing legacy reservation before making
  a new-policy reservation. Confirmed/pending/ambiguous legacy records suppress
  another event; terminal failed/unconfirmed records may allow fresh replacement.
  Arbitrary legacy relist/backfill cannot be inferred from incomplete old facts:
  an already-populated Phase 1 database still requires an audited cutover.
- `requirements.txt` lists the existing repository's FastAPI/Pydantic/JSON Schema,
  psycopg, pika and uvicorn ranges. No dependency installation, port opening or
  live provider call was performed.

Apply migrations 001..004 in order to **goal_evaluation only**. Migration 004
introduces processing/rejection tables and an index; it does not rewrite old
evaluations/reservations or change shared data. No cross-service SQL occurs.
D1 owns process commands, packaging the frozen schemas, JWT issuance/config,
connection credentials, reconnect policy, timeouts/watchdog budgets and private
Gateway/network integration. Blocking publisher confirms require a bounded
deployment budget even if the broker remains connected; this is part of the
unproven operational gate, not established by a mock ACK.

## Interface change proposal: complete goal source lookup (D1 + D3 approval)

Current behavior: `EvidenceProcessor` needs
`sources_for_goal(Subscription(goal_id, goal_version), correlation_id)` to load
the complete candidate set. The accepted internal API exposes only
`GET /internal/v1/sources/{source_id}/subscriptions`, which provides the reverse
direction. No accepted operation lists all source IDs for an exact goal version.
The four events contain no complete source list. Reading DM's database or
assuming the triggering source is the only source would violate ownership or
silently evaluate an incomplete set.

Proposed additive operation for D1/D3 review, **not implemented or authoritative**:

```text
GET /internal/v1/goals/{goal_id}/versions/{version}/sources  (DM private API)
ServiceBearer, existing dm.sources.read scope, X-Correlation-ID UUID
200: {"goal_id": "<uuid>", "goal_version": 1, "source_ids": ["<uuid>"]}
```

Require exact keys, positive integer version, unique UUID source IDs and explicit
empty list for a known goal with no links; distinguish nonexistent version (404)
from an empty set. Return the entire linked set or a defined bounded/paginated
response; never truncate. D1/D3 must settle ordering, cardinality and failure
semantics in the coordinated contract PR. Example IDs here are placeholders,
not schema fixtures.

Affected services: DM producer, GE consumer; D1 coordinates the shared OpenAPI
and ADR addition. Additive compatibility: all existing routes/events and frozen
v1 JSON Schemas remain unchanged. No cross-service database migration or data
copy is required; D3 decides whether its owned link indexes need a migration.

Migration order: approve and publish the additive contract/ADR, implement DM
with operation auth and complete link retrieval, then wire GE's reader and
runtime. Contract tests must cover two sources, zero sources, obsolete versions,
forbidden caller, identity mismatch, oversized result and concurrent link
changes; verify existing v1 fixtures still validate.

## Remaining acceptance gates

- Bind the approved complete subscription reader and D1 token issuer. API/worker
  composition is implemented, but no production reverse URL is guessed.
- Run owned migrations and actual Rabbit/PostgreSQL tests; verify DLQ routing,
  restart/concurrency recovery, connection deadlines and deployment budgets.
- Audit any previously populated Phase 1 fingerprint history before cutover;
  do not guess old monetary facts or rewrite immutable evaluations.
- D1 records coordinated Phase 2 extension merge approval; current ADR states
  merge/PR evidence pending. Public reads must not ship against a proposal.
- D3 checker implementation and reproducible provenance are required before
  claiming live checked-evidence acceptance.

The P0 API tests verify real signed service JWTs and the exact persisted DTO.
Live DB/broker, restart and deployed two-service auth smoke remain **NOT RUN**.
The next P1 task stays gated on a stable real P0 path.

## Validation of the original reader increment

- PASS: recorded reader tests, **32 passed**.
- PASS: contract validator, **5 schemas, 13 examples, 4 event routes**.
- PASS: repository suite, **859 passed, 13 skipped**, one existing Starlette
  TestClient deprecation warning. The skipped live PostgreSQL tests require
  `GOALPULSE_DE_TEST_DSN`, `GOALPULSE_DM_TEST_DSN`, and `GI_TEST_POSTGRES_URL`.
- PASS: `git diff --check`.
- Default `python scripts/validate_contracts.py` and `python -m pytest -q` fail
  before running checks because that interpreter lacks PyYAML/pytest. Executed
  validation using the existing `/private/tmp/goalpulse-d4-venv/bin/python`.
  Full pytest invocation also disables the environment's crashing native
  readline import, without disabling tests:

```bash
/private/tmp/goalpulse-d4-venv/bin/python scripts/validate_contracts.py
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q"]; runpy.run_module("pytest", run_name="__main__")'
```

## Continuation validation (2026-11-14)

| Acceptance | Result | Evidence |
|---|---|---|
| Exact saved internal evaluation, scoped service auth | PASS handler integration | Actual signed service JWTs, invalid kind/signature/scope/expiry, strict saved DTO, fixed errors |
| Unknown shipping/tax/stock, stale version/pause cannot verify | PASS recorded | Existing regression suite plus 30/10-minute policy boundaries |
| GE/NS same product business key | PASS separate-process test | Actual GE output consumed by actual Notification in its own process |
| Manual ACK, confirms, durable retry decisions and crash replay | PASS adapter/transaction doubles | ACK ordering, mandatory/persistent publication, lease recovery after business commit |
| Missing complete source lookup prevents startup/readiness | PASS unit | Required callback, migration probes, closed broker channels |
| PostgreSQL recorded-worker persistence/restart | NOT RUN | GOALPULSE_GE_TEST_DSN absent; Docker/psql unavailable |
| Rabbit + DB confirmed outbox, manual ACK and replay | NOT RUN | Requires disposable DB plus GOALPULSE_GE_TEST_AMQP_URL |
| Actual DM reverse lookup / D3 checker / D1 private deployment | NOT RUN | Coordinated bindings and live operational evidence pending |

Root commands were executed again. Default `python` still lacks PyYAML/pytest;
using the existing venv and readline workaround above:

- Contract validator: **PASS**, 5 schemas, 13 examples, 4 routes.
- Repository suite: **976 passed, 17 skipped**, one existing Starlette/httpx
  deprecation warning. Skips are unconfigured live PostgreSQL tests (including
  two new GE tests). No failing test was skipped to obtain this result.
- `git diff --check`: **PASS**.

New tests live under `services/goal-evaluation/tests/test_ge_*.py`. The optional
SQL and Rabbit checks use only recorded provider responses and a new random
test schema/owned test queues in explicitly configured disposable services:

```bash
python -m pytest -q services/goal-evaluation/tests/test_ge_postgres_broker_live.py
```

Configure GOALPULSE_GE_TEST_DSN privately; configure
GOALPULSE_GE_TEST_AMQP_URL as well for the actual broker case. These are test-only
keys. Credentials are not committed or emitted. The tests remove only their own
temporary schema/queues. A fresh store/connection exercises persisted state;
container/process kill/restart and timeout smoke still require D1 integration.

No Notification file was edited by this GE continuation. Earlier uncommitted GE
and Notification increments remain preserved. No commit, push, PR or merge was
performed in this continuation.

A redacted recorded-test evidence summary is saved in
`RECORDED_TEST_EVIDENCE.txt`. The earlier Notification handoff recorded GE policy
alignment as pending; the separate-process test in this continuation now proves
P0 product-key alignment. Legacy-history audit and live deployment remain open.

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
