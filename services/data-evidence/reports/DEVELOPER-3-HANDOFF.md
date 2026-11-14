# Developer 3 handoff — 2026-11-14

**Historical initial handoff.** Implementation has progressed since this report.
See [current status and acceptance gates](DEVELOPER-3-CURRENT-STATUS.md) for the
latest code, task-by-task status, verification results and remaining dependencies.

## Implemented within allowed service paths

- **D3-DM-01:** standard-library Context.dev adapter with injectable transport,
  DNS/clock/sleep, bounded retries, cache provenance, output success checks and
  a redacted live capability probe. Live acceptance is still pending; see the
  [provider report](../../discovery-monitoring/reports/D3-DM-01.md).
- **D3-DE-01:** frozen-schema validation at the private storage boundary, extraction
  failure ledger, immutable observations/provenance, stable attempt/snapshot
  fingerprints, PostgreSQL migration and atomic observation/outbox writes.
  PostgreSQL operational acceptance remains pending the actual database check.

All added code and tests are inside `services/discovery-monitoring/` or
`services/data-evidence/`. Frozen schemas, routes, proposed APIs, shared setup,
infrastructure, task status files and other owners' services are unchanged.

## Changed files and purpose

- `services/discovery-monitoring/goalpulse_discovery/`: provider adapter, typed
  results, transport/URL checks and redacted probe reporting.
- `services/discovery-monitoring/probe_context.py`: opt-in live probe entrypoint.
- `services/discovery-monitoring/tests/`: offline deterministic provider fixtures
  and success/failure/cache/retry/URL/reporting tests.
- `services/discovery-monitoring/README.md` and `reports/D3-DM-01.md`: execution
  instructions, completed scope, live limitation and next integration requirements.
- `services/data-evidence/goalpulse_evidence/`: capture/ledger validation,
  PostgreSQL adapter and checksummed forward migration runner.
- `services/data-evidence/migrations/0001_evidence_ledger.sql`: owned attempt,
  observation, provenance, inbox/outbox tables, uniqueness, indexes and immutability.
- `services/data-evidence/tests/`: ledger and transaction fault tests, migration
  bookkeeping tests and opt-in actual PostgreSQL acceptance cases.
- `services/data-evidence/README.md` and this report: use, verification, remaining
  task status and downstream integration handoff.

## Task status and dependencies

| Task | Status / next required input |
|---|---|
| D3-DM-01 | Adapter/tests implemented; live smoke + public product probe NOT RUN (no local API key). |
| D3-DM-02 | Source registry and accepted authenticated source reads implemented; actual PostgreSQL and provider-side SSRF acceptance remain. |
| D3-DM-03 | Activation inbox/job and bounded initial discovery implemented; production RabbitMQ/GM/DE client startup remains. |
| D3-DM-04 | Pending approved active-goal reads, DM-02/03 and secure monitor-secret storage. |
| D3-DM-05 | Signed receiver/inbox/follow-up jobs implemented; admitted HTTP ingress, secret backend and live delivery remain. |
| D3-DM-06 | Pending DM-03/04 and signed-off service-only Check Now design. |
| D3-DE-01 | Storage/tests implemented; apply/verify migration on the owned PostgreSQL test DB. |
| D3-DE-02 | Accepted authenticated ExtractionSubmission route, strict normalization/storage and attempt-id conflict handling implemented. |
| D3-DE-03 | Accepted authenticated by-ID/source reads implemented; live consumer acceptance remains. |
| D3-DE-04 | Atomic outbox and confirmed bounded publisher implemented; live D1 broker/channel acceptance remains. |
| D3-DE-05 | Storage edge cases covered; full normalization/consumer/live-provenance checks await DE-02/03/04. |

ICP-01 and ICP-08 are accepted by the decision log and ADR-0002. The implemented
D3 routes match that accepted OpenAPI and require operation-specific service JWT
scopes. No additional public or private interface was invented.

Developers 1 and 4 approved the ICP-13 Phase 1 remediation. The protected fixtures
now use explicit exact-model mapping only for a `condition:any` satisfied example
and provide a separate `condition:new` evaluation that remains `unknown`. The
versioned evidence-contract extension remains a follow-up and v1 is unchanged.

## Integration impact

- No new API, event type, service consumer or publisher was deployed. The only
  staged event type is existing `evidence.updated.v1` from Data & Evidence.
- Apply `migrations/0001_evidence_ledger.sql` through the service migration runner
  to the owned DB before storage use. SQL remains unexecuted in this environment.
- No production dependencies installed. The provider uses the standard library;
  storage uses existing jsonschema and an injected psycopg 3 compatible connection.
  Coordinate deployment driver/credentials with Developer 1.
- Existing `CONTEXT_DEV_API_KEY` is required only for the opt-in provider probe.
  `GOALPULSE_DE_TEST_DSN` is a test-only setting for a disposable database named
  `data_evidence_test`; it is not read by production storage code.
- D4 can reuse the existing checked/unknown fixtures and accepted authenticated
  handlers. Live service startup remains an integration step. No goal evaluation,
  checkout action or purchase occurs here.

## Verification limitations

Latest remediation verification ran from the repository root with Python 3.11.5.
Python 3.12 was unavailable locally; rerun these gates in the repository's 3.12 CI.

| Command | Result |
|---|---|
| `scripts/validate_contracts.py` | PASS: 5 schemas, 11 examples, 4 event routes, Gateway OpenAPI. |
| `-m pytest -q` | **233 passed, 7 skipped**. |
| `-m pytest -q -rs services/data-evidence/tests/test_postgres_live.py` | **4 skipped**, explicit missing test-DSN reasons. |
| `services/discovery-monitoring/probe_context.py https://example.com` | Exit 2, **NOT RUN**, no key configured; no request sent. |
| `-m compileall -q services/discovery-monitoring/goalpulse_discovery services/data-evidence/goalpulse_evidence` | PASS. |
| `git diff --check` | PASS. |

- Live Context.dev example.com/product probes: **NOT RUN**, API key absent.
- PostgreSQL integration suite (four cases): **NOT RUN / skipped**, no test DSN,
  PostgreSQL tooling or driver installed. Run the documented service command after
  provisioning the disposable owned test DB and approved driver.
- RabbitMQ recovery and full cross-service acceptance: **NOT RUN**; publisher and
  accepted handlers are implemented but no live broker/services were started.
- Offline unit tests are not operational proof of PostgreSQL or merchant extraction.
