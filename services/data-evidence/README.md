# data-evidence — GoalPulse service

**Implementation status: D3-DE-01 storage foundation implemented; PostgreSQL execution pending.**
There is no HTTP ingestion/read service or RabbitMQ publisher yet. ICP-01 is still
awaiting the affected owners' approvals and an accepted ADR.

- [Service specification](../../docs/implementation/04-data-evidence.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` needs formal signoff before becoming an implementation contract. Do not guess interfaces or read other services' databases.

## D3-DE-01 storage boundary

`goalpulse_evidence.ledger.EvidenceLedger` accepts an already-normalized
`Observation.v1` candidate and private server capture metadata. This is a Python
storage interface, **not** an implementation of the proposed ExtractionSubmission
HTTP contract. It uses the authoritative JSON schemas supplied through an explicit
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
- The storage layer preserves `unverified`/`checked`/`disputed`; it never upgrades
  trust or evaluates goal satisfaction. Only future trusted normalizer code may
  supply checked candidates after corroboration; callers cannot submit HTTP here.

## PostgreSQL migration and connection ownership

`migrations/0001_evidence_ledger.sql` creates service-owned tables and indexes.
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

See [Developer 3 handoff](reports/DEVELOPER-3-HANDOFF.md) for task status and blockers.
