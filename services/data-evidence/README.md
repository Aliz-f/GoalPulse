# data-evidence — GoalPulse service

**Implementation status: service components for D3-DE-01..05 implemented and tested offline.**
Storage, normalization, evidence queries, outbox publication and quality tests are
present. Accepted authenticated HTTP ingestion/reads are implemented; worker startup
and live PostgreSQL/RabbitMQ acceptance remain pending platform setup.

- [Service specification](../../docs/implementation/04-data-evidence.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task
file. Follow versioned frozen contracts. The historically named
`internal.proposed.v1.yaml` is accepted under ICP-01/08 and ADR-0002. Do not guess
additional interfaces or read other services' databases.

## D3-DE-01 storage boundary

`goalpulse_evidence.ledger.EvidenceLedger` accepts an already-normalized
`Observation.v1` candidate and private server capture metadata. The authenticated
`api.py` adapter implements the accepted ExtractionSubmission HTTP contract and
passes exact attempt idempotency keys into this storage boundary. It uses the
authoritative JSON schemas supplied through an explicit
`schema_directory`; it does not copy or modify v1 schemas.

- Missing price records `MISSING_UNIT_PRICE` in `extraction_attempts`; no observation
  or event is created. Other invalid candidates record deterministic reasons and a
  digest, without retaining raw failed payloads. Money must be actual Python
  integers; `0.0` is rejected even where JSON Schema would call it an integer.
- The source, provider and extraction method must match capture metadata. The
  observation time must match the oldest available provider time/cache-derived
  capture time. Missing provenance time stays unknown; future times are rejected.
  Historical observations remain historical; the evaluator owns freshness gating.
- Stable fingerprints exclude generated IDs, correlation ID, request ID and
  extraction method from the observation identity. Source, capture time, facts,
  evidence and reliability remain part of it. Exact attempt replays deduplicate;
  separate provider requests can reference one logical snapshot.
- One transaction inserts the attempt, immutable observation, provenance, and
  one exact `evidence.updated.v1` outbox envelope. A duplicate returns the original
  observation ID and preserves the first event/correlation. No broker calls occur.
- PostgreSQL constraints prevent duplicate fingerprints and duplicate outbox
  records for an observation. Row triggers reject UPDATE/DELETE on observations,
  provenance and attempts. Deployment credentials must also disallow TRUNCATE/DDL.
- Ingestion preserves `unverified`/`disputed` and rejects a private caller's
  `checked` candidate with `CALLER_CHECKED`. HTTP suggestions are retained in
  provenance but enter unverified (or disputed), never checked. Only the internal
  ICP-09 checker can append a checked snapshot; it never evaluates satisfaction.

## PostgreSQL migration and connection ownership

`migrations/0001_evidence_ledger.sql` creates service-owned tables and indexes;
`0002_evidence_checks.sql` adds immutable checker decisions and proof replay guards.
`goalpulse_evidence.migrations.apply_migrations(connection, directory)` applies
forward migrations atomically under an advisory lock, checksums applied versions,
and refuses changed migration history. No database creation/drop is performed.

`PostgresRepository(connect)` takes a psycopg 3 compatible factory that returns
a new connection to the **data_evidence** database, with `autocommit=False` and
default read-committed isolation. It closes the connection after commit/rollback.
The migration runner commits/rolls back but leaves connection closure to its caller.

No new production dependency was installed. JSON Schema validation uses the
repository's existing `jsonschema` dependency. The injectable SQL adapter does
not import a driver; Developer 1 must coordinate the deployment's psycopg 3 driver,
scoped DB credentials and runtime packaging. Python 3.12+ is required.

## Verification from repository root

```powershell
.venv/Scripts/python.exe scripts/validate_contracts.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests
```

Offline tests use an explicit transactional test double, not another production
database engine. PostgreSQL tests skip when `GOALPULSE_DE_TEST_DSN` is absent. To
run them after the approved driver and a disposable DB are provisioned, set that
test-only variable locally to a DSN for **data_evidence_test**, then run:

```powershell
.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests/test_postgres_live.py
```

These tests append synthetic fixtures and apply the forward migration; they never
drop/truncate databases or modify another service's data. They verify actual SQL,
migration reruns, concurrent deduplication, immutable row triggers and rollback
after a staged observation write fails. A configured integration run fails if its
driver is missing. No service is started or network port exposed by this task.

See [Developer 3 current status](reports/DEVELOPER-3-CURRENT-STATUS.md) for task status and blockers.

## D3-DE-02..05 private components

- `normalization.ProductFacts` is a service-private fact model, not the proposed
  ExtractionSubmission wire DTO. It normalizes confirmed pcs/kg/L packaging,
  including g/ml conversions, and exact decimal EUR amounts. Rational arithmetic
  preserves decimal precision; fractional-cent unit prices are rejected instead
  of rounded. Inclusive tax contributes zero additional tax only when explicitly
  established; additional/unknown tax and unknown shipping remain distinct.
- `FactIngestor` stores normalizer rejection codes in the failure ledger and valid
  strict snapshots through the atomic observation/outbox path.
- `reads.EvidenceReads` queries the owned DB by ID or bounded source/newest order
  and revalidates every returned snapshot. `api.create_app` exposes the accepted
  POST/GET routes with `de.extractions.write` and `de.observations.read` service scopes.
- `publishing.OutboxPublisher` claims one row with a PostgreSQL lock, publishes the
  exact event and marks it published only after confirmation. Unconfirmed sends
  retry with a stable event ID, then go to durable dead state after a bounded
  number of attempts. A crash after confirm may redeliver; consumers must dedup.
- `RabbitChannelBroker` accepts a platform-created Pika BlockingChannel with
  bounded timeouts and `pika.BasicProperties` factory. It declares the durable
  topic exchange, enables confirms and sends persistent, mandatory messages.
  Driver installation, credentials, queues and worker startup remain platform
  integration work. [Pika's BlockingChannel documentation](https://pika.github.io/pika/latest/modules/adapters/blocking/)
  defines confirmation and unroutable/NACK behavior.

ICP-13 Phase 1 handling is approved while its versioned contract extension remains
deferred. `Observation.v1` has no product condition or arbitrary attribute fields.
An explicitly extracted `model` is preserved and may be used only with an explicit
exact-model mapping; a missing or non-mapped model stays unknown. Excerpts never
create condition/model/shipping/tax/stock proof. Normalization remains unverified by
default, or disputed on conflicting signals, with no automatic checked upgrade.
GE keeps unsupported mandatory condition/attribute checks unknown, so v1 evidence
cannot verify a `condition=new` opportunity.

## R2-D3-02 internal checked policy

`checking.EvidenceChecker.check(observation_id, check_id=..., correlation_id=...)`
reads an existing observation and its original capture from the owned ledger. It
has no HTTP route. The injected `RepresentationReader.load(UUID)` reads a recorded
seller JSON-LD fragment through a separate trusted capture-storage path; HTTP
extracted fields and excerpts cannot supply this proof. The explicit server-owned
`merchant_hosts` registry defaults to no implicit merchant trust.

The `icp09-v1` policy supports a single first-party HTTPS Product/Offer sold per
`pcs`, with exact product name/model, integer EUR cents, explicit stock and product
condition, matching request/cache provenance, public recorded capture addresses,
and evidence at most 30 minutes old. `seller-jsonld-v1` independently parses that
bounded representation. Aggregate/search cards, missing facts, malformed data,
stale proof and proof replay cannot promote. Fixture provenance is permitted only
with explicit `profile="test"`; dev/demo never substitute it for real evidence.

Every decision records policy/normalizer versions, input/proof digests, reasons,
original and derived IDs, correlation, check time and reproducible bounded proof.
A checked/disputed result appends a new immutable Observation.v1 with the original
observed timestamp and capture provenance, plus an exact `evidence.updated.v1`
outbox event in the same transaction. Reusing a check ID returns its original
decision; it cannot refresh evidence. Original observations remain readable by ID.
Source reads omit superseded originals and suppress a checked snapshot when a
current contradictory assertion exists. Same-capture conflicts append disputes
for earlier checked interpretations too; genuine later price changes are allowed.

Brand, FI delivery, shipping and tax remain unknown when absent. Known assertions
without matching structured seller proof are removed from the checked derivative;
explicit contradictions are disputed. A verified capture URL replaces any caller
link. Product condition stays in the internal audit because v1 has no condition
field. GE therefore retains its existing hard unknown gates; checked does not mean
checkout availability, a completed goal, or purchase approval.

Apply migration 0002 before using checker/source reads. R2-D3-01 must wire a real
recorded-representation adapter and invoke the checker from the private pipeline;
without that proof, observations remain unverified. The adapter must preserve
original provider times/request IDs and verify capture URL/DNS/redirect provenance;
it must never reconstruct proof from the submitted normalized assertion. This
task adds no public/internal HTTP API, dependency declaration or environment variable.

See [R2-D3-02 evidence and handoff](reports/R2-D3-02-CHECKED-POLICY.md) for checks,
integration limits and redacted recorded smoke evidence.

## R2-D3-01 private API and publisher runtime

`goalpulse_evidence.runtime.from_env` is the Uvicorn app factory. It composes
scoped PostgreSQL connections, the immutable ledger and canonical reads, and
applies all owned migrations before serving. `GOALPULSE_PROFILE` must be
`test`, `dev` or `demo`; `DATA_EVIDENCE_DATABASE_URL` must name `data_evidence`
(tests may use `data_evidence_test`). Configure the existing `SERVICE_JWT_SECRET`,
`SERVICE_JWT_ISSUER` and `SERVICE_JWT_AUDIENCE`; the signing secret must contain at
least 32 bytes. The publisher also requires the existing `RABBITMQ_URL`.

From `services/data-evidence/`, run separate private processes:

```powershell
../../.venv/Scripts/python.exe -m goalpulse_evidence api
../../.venv/Scripts/python.exe -m goalpulse_evidence publisher
```

The API binds loopback port 8004 by default. `/health/live` checks the process and
`/health/ready` checks DB/migration checksums. Connections and statements have
bounded timeouts; operational failures return fixed redacted 503 errors. The
publisher uses the existing confirmed, persistent, mandatory outbox path and
bounded broker connection/retry settings. `publisher --once` processes one due row.

Dev/demo reject fixture submissions and fixture reads. `DE_EVIDENCE_MODE`, if
configured outside tests, must be `real`. There is no fixture fallback and no
external provider access in DE. The accepted ExtractionSubmission cannot carry
independent seller-representation proof, so runtime HTTP observations remain
unverified; no ad hoc JSON-LD/condition fields or checked claims were added.

See the [R2-D3-01 pipeline report](../discovery-monitoring/reports/R2-D3-01-PRODUCTION-PIPELINE.md)
for tested integration behavior and the remaining actual-stack acceptance check.
