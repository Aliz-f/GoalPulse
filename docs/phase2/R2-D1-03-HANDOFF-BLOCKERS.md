# R2-D1-03 resolved cross-owner blocker record

These failures were reproduced and resolved by D1 in an isolated local
PostgreSQL 16 / RabbitMQ 4 stack on 2026-11-15 after project-wide repair scope
was explicitly granted. There are no remaining cross-owner durability blockers.

## D3: persisted submission receipt expectation — RESOLVED

- Owner: Developer 3 (`services/discovery-monitoring/`)
- Scenario: B1/D1/D4 durable restart lane
- Command: durable release lane, suite `discovery_postgres`
- Initial result: 6 passed, 1 failed
- Failure: `test_postgres_initial_plan_and_submission_intent_are_immutable`
  expected `SubmissionStore.put()` to return only `submission` and `receipt`,
  while the real persisted result also contains the immutable
  `capture_metadata.requested_at` written by the current implementation.
- Resolution: migration `0006_initial_pipeline.sql`, `SubmissionStore`, the
  recorded pipeline tests and service documentation all establish immutable
  capture provenance as intentional. The PostgreSQL acceptance test now asserts
  that `capture_metadata` survives both creation and receipt completion.
- Verified result: the complete Discovery durable suite passes (7 passed).

Reproduce with the D3 disposable test DSN and:

```bash
python3.12 -m pytest -q -rs \
  services/discovery-monitoring/tests/test_discovery_postgres_live.py \
  services/discovery-monitoring/tests/test_initial_pipeline_postgres.py \
  services/discovery-monitoring/tests/test_scheduler_postgres.py
```

## D4: committed GE outbox rows not initially eligible — RESOLVED

- Owner: Developer 4 (`services/goal-evaluation/`)
- Scenario: D1 real-broker replay and confirmed outbox
- Command: durable release lane, suite `evaluation_postgres_rabbit`
- Initial result: 0 passed, 2 failed
- Failure: `test_real_rabbit_manual_ack_replay_and_confirmed_sql_outbox` stored
  one deduplicated evaluation, but `outbox WHERE status='published'` was `0`;
  the acceptance expectation is `2` declared GE events after the worker drain.
- Root cause: initial outbox `next_retry_at` used PostgreSQL's wall clock while
  relay selection used the worker's injected clock. Negative worker clock skew
  made a never-attempted row appear not due, so publication was never attempted.
- Resolution: pending rows with `attempts=0` are immediately eligible; only
  attempted rows honor their application-calculated retry deadline. Locking,
  broker confirmation, manual ACK ordering and bounded retry semantics remain
  unchanged. SQL regression coverage and durable state diagnostics were added.
- Verified result: recorded PostgreSQL and real RabbitMQ cases both pass (2
  passed), and the full Goal Evaluation unit suite passes in the root run.

Reproduce with disposable `GOALPULSE_GE_TEST_DSN` and
`GOALPULSE_GE_TEST_AMQP_URL`:

```bash
python3.12 -m pytest -q -rs \
  services/goal-evaluation/tests/test_ge_postgres_broker_live.py
```

The full durable release lane now passes all seven suites. No provider calls,
secrets, raw goals, page content, checkout, payment, booking, or purchase actions
were involved in either failure or fix.
