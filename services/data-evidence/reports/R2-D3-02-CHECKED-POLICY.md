# R2-D3-02 — internal checked evidence policy

Date: 2026-11-14. Owner: Developer 3. Branch:
`feat/r2-d3-02-internal-checked`. Changes are restricted to `services/data-evidence/`.
R2-D1-00 is present in ancestry through merged PR #9 (`57eef52`); ADR-0003/MVP-09
authorizes this internal policy. No new interface approval or frozen-contract edit
is required for this implementation.

## Implemented

- Ordinary HTTP extraction remains unverified; the checked suggestion is preserved
  only as provenance. Private ledger callers also cannot assert checked: the
  extraction attempt is rejected with `CALLER_CHECKED`.
- `EvidenceChecker` reads the immutable observation/capture and independently
  parses a separately recorded seller Product/Offer representation. It requires
  an explicitly configured first-party HTTPS merchant, public recorded capture
  addresses, exact name/model, integer EUR cents, explicit stock/condition,
  matching source/request/cache metadata and a capture no older than 30 minutes.
- Each check appends its reasons, timestamps, input/proof digests, bounded
  reproducible seller proof and policy/normalizer versions. Promotion/dispute
  creates a new immutable observation with the original observed time/provenance.
  Snapshot, provenance, exact v1 outbox event and decision commit atomically.
- Source, check-ID and proof-digest locks plus DB uniqueness prevent concurrent
  replay promotions. Check-ID replay returns the original decision; it never
  refreshes evidence or emits another event. Conflicting same-time interpretations
  supersede earlier checked snapshots with audited disputes. Genuine later price
  changes can still qualify.
- Source reads omit superseded snapshots and immediately suppress checked rows
  contradicted by current assertions. Historical ID reads remain immutable.
- Unknown stock cannot promote. Unknown brand/FI delivery/shipping/tax is never
  filled from an excerpt. Uncorroborated known assertions are removed from the
  checked derivative; explicit contradictions are disputed. The capture link is
  bound to the verified source. Condition proof remains internal, since frozen
  Observation.v1 has no condition field.
- Invalid JSON, duplicate keys, malformed stock/condition types, nonfinite
  numbers, invalid Unicode and oversized fragments produce audited unknowns.
  Provider/capture-reader failure never substitutes fixture evidence. Fixture
  promotion requires an explicit test profile; dev/demo reject fixture provenance.

## Acceptance evidence

| Criterion | Result | Evidence |
|---|---|---|
| Fresh first-party recorded observation can become checked with reproducible proof | PASS, offline | Separate assertion/representation inputs; strict schema, provenance and atomic outbox assertions in `test_checking.py`. |
| Single assertions and forged checked never promote | PASS, offline | Authenticated HTTP downgrading, private-ledger rejection and missing-representation tests. |
| Tampered/stale/cache-misbound/replayed/conflicting observations never promote | PASS, offline | Exact-model/price/stock/brand/cost/condition conflicts, replay IDs/proofs, metadata mismatch and clock-controlled age tests. |
| Conflict decisions and original timestamps remain immutable | PASS, offline | Original payload/provenance preserved; disputed derivatives, copied audit replay and rollback tests. Actual DB triggers remain NOT RUN. |
| GE still blocks missing FI delivery, stock, shipping, tax and required condition | PASS, existing GE unit tests | 107 product evaluator tests passed; GE source was not modified. |
| Frozen v1 unchanged | PASS | Contract validation passes; no changed path outside the assigned service. |
| Actual PostgreSQL/real provider/full running pipeline | NOT RUN | DE test DSN absent; real representation capture/runtime wiring belongs to R2-D3-01. |

## Changed files

All paths below are relative to `services/data-evidence/`.

| Files | Purpose |
|---|---|
| `goalpulse_evidence/checking.py` | Internal deterministic policy, seller representation adapter port, replay/conflict decisions and atomic derived snapshots. |
| `goalpulse_evidence/ledger.py`, `goalpulse_evidence/api.py` | Block private checked assertions, serialize ingestion and preserve HTTP reliability suggestions as provenance. |
| `goalpulse_evidence/postgres.py` | Owned checker/audit/provenance queries, advisory locks and append-only writes. |
| `goalpulse_evidence/reads.py` | Return current canonical snapshots and suppress conflicting checked interpretations while preserving ID reads. |
| `migrations/0002_evidence_checks.sql` | Immutable audit table, original/result references, bounded proof and unique checked-proof guard. Existing migration is unchanged. |
| `tests/test_checking.py` | 85 deterministic policy acceptance cases using explicitly synthetic recorded inputs. |
| `tests/test_check_postgres.py`, `tests/test_postgres_live.py`, `tests/test_reads.py` | Parameterization, capture/audit reconstruction, current claims, canonical reads and opt-in SQL immutability/replay coverage. |
| `tests/fake_evidence_repository.py`, `tests/test_evidence_ledger.py` | Transactional checker test support, audit failure injection and ingestion checked-rejection regression. |
| `README.md`, `reports/DEVELOPER-3-CURRENT-STATUS.md`, this report | Current policy, migration, evidence and integration handoff. |

## Validation

Executed from the repository root using the existing virtual environment,
Python **3.13.5** (the system `python` is 3.11 without the required packages).
Already-declared requirements were installed into `.venv` with approval for tests;
no dependency declaration was added or changed.

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe scripts/validate_contracts.py` | PASS: 5 schemas, 13 examples, 4 event types, frozen Gateway OpenAPI with 7 paths. |
| `.venv/Scripts/python.exe -m pytest -q -rs` | PASS: **917 passed, 14 skipped**. All skips are unconfigured PostgreSQL integration tests (5 DE, 3 DM, 6 GI). |
| `.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests` | PASS: **176 passed, 5 skipped**. |
| `.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests/test_checking.py` | PASS: **85 passed**. |
| `.venv/Scripts/python.exe -m pytest -q services/goal-evaluation/tests/test_product.py` | PASS: **107 passed**. |
| `git diff --check` | PASS. |

PostgreSQL is **NOT RUN** because `GOALPULSE_DE_TEST_DSN` is not configured.
After provisioning the dedicated `data_evidence_test` database, run:

```powershell
.venv/Scripts/python.exe -m pytest -q services/data-evidence/tests/test_postgres_live.py
```

No service was started, port exposed, provider called, DB dropped or shared file
changed. Live Context.dev/RabbitMQ/full-stack smoke is NOT RUN in this task;
recorded tests are not live operational evidence.

## Redacted recorded smoke output

Executed with the synthetic `Scenario` helper in `tests/test_checking.py`; no
credentials, real merchant response, personal data or real provider call involved.
The output is intentionally labeled recorded/synthetic:

```text
RECORDED_SYNTHETIC fresh: checked | original: unverified | observed_at: 2026-11-14T12:00:00Z
RECORDED_SYNTHETIC missing costs: unknown None None
RECORDED_SYNTHETIC single assertion: unverified
RECORDED_SYNTHETIC stale: unverified
RECORDED_SYNTHETIC conflicting price: disputed | audit: CONTRADICTORY_UNIT_PRICE_EUR_CENTS
Live provider/database: NOT RUN
```

## Contracts, integration and handoff

Contracts are unchanged: frozen Observation.v1, domain-event.v1, four routing
keys and accepted internal/public HTTP shapes all remain intact. Derived checked
or disputed observations use new IDs and the existing `evidence.updated.v1` event.
Consumers must use current source reads when selecting evidence; ID reads retain
the immutable historical state at that observation ID.

Apply migration 0002 through the owned checksummed runner before using checker or
source reads. Runtime DB credentials must disallow bypassing immutable triggers
through TRUNCATE/DDL. No new environment variable or dependency is introduced.

For R2-D3-01, wire an actual trusted recorded-representation reader and the
private checker invocation. Preserve original provider request/time/cache and
vetted capture URL/address metadata; do not build proof from submitted normalized
fields or invent metadata that Context.dev does not expose. Sources without the
required proof stay unverified. This policy supports one explicit JSON-LD
Product/Offer per `pcs`; alternate formats and kg/L proof require separately
specified parser behavior rather than fuzzy fallback.

For D4, checked means source claims were internally corroborated at the original
capture time, not checkout availability or independent-merchant confirmation.
GE continues tri-state checks; `condition=new` remains unknown with Observation.v1.
The checker enforces the 30-minute promotion cap; the tighter goal-specific limit
and the ADR's 10-minute alert recheck remain D4 runtime responsibilities. No GE
logic, notification behavior or purchase capability was weakened or added.
