# R2-D4-03 — independent preparation and integration handoff

**Status: implemented and exercised with recorded peers; live gate remains open.**
The user explicitly authorized P1 code/tests with recorded dependencies before
P0 live stability. That authorization does not claim the shared extension is
merged, the other services are deployed, or any offer was checked live.

## Implemented behavior and changed files

- `agent_integration/api.py`: private FastAPI app factory for the Phase 2
  owner-only GET `/v1/action-packages/{package_id}`. Its required authentication
  dependency must return a validated server-side `Principal`; there is no
  unsigned owner-header fallback. Missing/unowned IDs both return 404, expiry/
  invalidation returns 410, missing authentication 401, dependency errors fixed
  redacted 503. UUID correlation is preserved/generated and returned. Responses
  use no-store. The app refuses a legacy reader without current-evidence and
  frozen-contract validation. No execution, approval mutation, purchase or
  payment route exists.
- `readers.py`: actual bounded HTTP reads of accepted GM runtime/version, GE
  saved evaluation and DE exact/latest Observation operations. Each call asks
  D1's injected issuer for its operation scope and propagates correlation.
  The GE scope is supplied explicitly by D1 rather than invented. Redirects and
  ambient proxies are disabled; timeouts/body limits and fixed errors apply.
  Demo refuses fixture provenance. Missing costs/timestamps are not rewritten.
- `runtime.py`: wires the real adapters, owned SQL store, PackageBuilder,
  PackageConsumer, evidence-backed PackageReader, durable RetryCoordinator and
  Rabbit PackageWorker. D1 supplies actual connection factories, scope/token
  configuration, private identity forwarding, queues and readiness prerequisites.
  SQL tables/migration columns and broker/channel readiness are probed. Explicit
  in-memory store/journal injection is rejected outside `profile=test`.
- `worker.py`, `retry.py`, `migrations/002_processing_journal.sql`: durable
  verified-event processing with owned inbox/package transactions and separate
  durable attempt/lease journal. Manual ACK follows committed handling; malformed
  input/validation/permission failures dead-letter, transient failures back off
  with five attempts by default. Sleep services broker heartbeats; the same
  event is requeued. D1 supplies durable DLX/DLQ settings and bounded reconnect/
  processing budgets. No alternate domain events are introduced. Crash recovery
  after package commit uses inbox dedup; an old lease cannot overwrite a new one.
- `eligibility.py`, `service.py`: product lifetime is capped at the stricter of
  goal freshness and 30 minutes. Evidence-backed reads check original immutable
  fields, costs, stock, delivery, reliability and current source snapshots, then
  recheck GM ownership/version/lifecycle and time after the potentially slow I/O.
  A newer unchanged capture cannot replace citations or extend expiry. Disputed,
  changed, future/stale and unsafe current evidence blocks use. Legacy payloads
  exceeding current proof deadlines are refused rather than silently rewritten.
- `fingerprint.py`, `store.py`: local P0 business-key implementation agrees with
  GE/NS's ordered ADR-0003 fields, excludes capture/listing IDs and recognizes an
  exact legacy offer key. Unique goal/version/evaluation and business-offer SQL
  guards remain the final replay safeguards. Same-offer recaptures do not create
  successors even after expiry. GET never creates or renews a package. Already
  populated Phase 1 histories still require an audited fingerprint cutover;
  arbitrary old monetary facts cannot be reconstructed from missing data.
- `requirements*.txt`: existing approved FastAPI/Pydantic/JSON Schema/psycopg/
  pika/uvicorn stack, and existing root PyYAML/httpx2 test packages. No installation
  was performed and no root dependency/configuration file changed.
- `tests/recorded_support.py`, `recorded_smoke.py`, `test_phase2_*.py`: independent
  recorded HTTP peers, actual worker/API flow, ownership/expiry/schema tests,
  durable retry/crash and fail-closed profile checks. Optional disposable SQL/
  Rabbit smoke covers real persistence, replay and rejection-to-DLQ.
  Product fixture expiry expectations now reflect the 30-minute policy; the
  original prepare-only assertions remain in place. Procurement policy remains
  unchanged and its normalization is not enabled by the runtime builder.

## Contract state

Frozen GoalSpec, Observation, GoalEvaluation and domain-event schemas, four
event routes, and Gateway v1 are unchanged. Actual handler responses validate
against the grouped ActionPackage in `gateway.phase2.extension.v1.yaml`; no
`seller_offers` field or owner ID leaks into the exported DTO. Both line totals
and grouped seller shipping are validated by the existing local PackageContract.
Packages always require approval and checkout revalidation; auto_execute=false.

D1 owns publication/merge of the shared extension and Gateway/D2 client wiring.
The old internal package sketch still describes flat `seller_offers`; this task
does not expose that internal route or pretend it matches the grouped DTO.
Package-link discovery for the UI remains a coordinated D1/D2 concern; no extra
event, guessed public endpoint or cross-database backfill was added.

## Reproduced and fixed P0 safety follow-up

While adding current-evidence package reads, review exposed the same historical
snapshot-selection issue in existing D4 GE/NS code. Read-only fixture reproduction
showed GE allowing publication and NS creating a notification when the canonical
current capture was disputed at the same observed time but had a lower UUID.
Reinserting the old citation into the latest-candidate set masked the downgrade.

The fixes were applied on their existing P0 task branches, then work returned to
the Agent Integration branch. GE processing/publication and NS notification
selection now respect the nonempty DE current result instead of restoring an
older citation by UUID ordering. GE detects a lagging checked index behind a
newer trigger and retries rather than restoring an old price. Same-time dispute
and price-change regressions cover all three D4 services. No shared contract,
other developer's source or previous work was overwritten; these corrective
edits are confined to the already assigned GE/NS paths and their tests.

## Independent recorded run

With the existing test environment:

```bash
/private/tmp/goalpulse-d4-venv/bin/python services/agent-integration/tests/recorded_smoke.py
```

Optional export uses `--output <new-file.json>` and refuses to overwrite an
existing artifact. The executed run exported synthetic evidence to
`/private/tmp/goalpulse-r2-d4-03-recorded-evidence.json` and reported:

```text
mode=recorded_test; live_deployment=false
package_count=1; acknowledged_deliveries=2
owner_get=200; other_owner_get=404; expired_get=410
external_network=blocked
```

The runner opens no port, connects to no real peer/merchant/database/broker, and
uses in-process HTTP handlers. It exercises the actual worker/coordinator,
consumer, validators, evidence-backed read and API. In-memory test stores do not
prove SQL durability. The exact-model/any-condition synthetic goal is separate
from the MVP new-product goal: Observation v1 still cannot prove `new`; that
absence remains unknown and cannot be approved by an LLM or fixture fallback.

## Acceptance and validation

| Criterion | Result | Evidence / remaining work |
|---|---|---|
| Grouped seller_orders, approval and revalidation flags | PASS recorded | Actual worker/API output validates the local and D1 extension schemas |
| Replay/recapture creates one immutable package | PASS recorded | Standalone smoke, business relist key and commit/journal-failure recovery |
| Other user cannot read; expired package unusable | PASS handler integration | 404 isolation, 410 expiry and no mutation on GET |
| Disputed/changed/unsafe/stale evidence blocks use | PASS recorded | Current-evidence API/consumer tests, UUID-downgrade regressions |
| Demo cannot silently select recorded stores/provenance | PASS unit | Explicit test-store guard and fixture-provenance rejection |
| Real SQL persistence and broker ACK/DLQ/replay | NOT RUN | Optional tests need disposable AI DB/Rabbit; Docker/psql unavailable |
| Real Gateway owner authentication / UI client / deployment | NOT RUN | D1 binding/routing and P0 live gates remain pending |

From repository root, the default `python scripts/validate_contracts.py` and
`python -m pytest -q` failed before checks because that interpreter lacks
PyYAML/pytest. Executed using the existing venv and its native-readline workaround:

```bash
/private/tmp/goalpulse-d4-venv/bin/python scripts/validate_contracts.py
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q", "-rs"]; runpy.run_module("pytest", run_name="__main__")'
```

Results: contract validator PASS (5 schemas, 13 examples, 4 event routes);
repository suite **1023 passed, 19 skipped**, one existing Starlette/httpx
deprecation warning; `git diff --check` PASS. Skips are the explicitly
unconfigured live PostgreSQL tests. The optional AI tests are:

```bash
python -m pytest -q services/agent-integration/tests/test_phase2_postgres_live.py
```

Configure test-only GOALPULSE_AI_TEST_DSN privately; add
GOALPULSE_AI_TEST_AMQP_URL for the real broker case. Tests create/remove only
their own random schema/queues. No new production environment keys were added.
Apply migration 001 then 002 only to Agent Integration's owned database before
worker startup. No cross-service SQL/migration is performed.

## Remaining release gates

Close the real P0 path, obtain D1's coordinated contract/identity/routing
integration, verify the grouped client DTO and approved package-link path,
then run live owned-DB/broker, restart and timeout smoke. Bind a real Principal
dependency validated by service JWT and trusted Gateway owner context, never an
unsigned user header or the synthetic smoke dependency. No real offers,
checkout, purchasing or credentials are used in this preparation.

No commit, push, PR or merge was performed. Existing uncommitted work remains
preserved; the explicit P0 trust-downgrade corrections above are included in the
current working tree alongside this task.

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
