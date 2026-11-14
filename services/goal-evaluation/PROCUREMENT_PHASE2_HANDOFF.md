# R2-D4-04 — recurring procurement preparation

**Status: recorded implementation/testing complete; live acceptance blocked.**
This continues the independent recorded preparation used for the preceding D4
task. The task's live P0/D3 gates remain open. No production normalization policy
or new evidence fields were assumed to make a café basket verify.

## Implemented and changed files

- `goal_evaluation/runtime.py`: registers both ProductPurchaseEvaluator and
  RecurringProcurementEvaluator. Default basket normalization is unconfirmed, so
  supported recurring requests produce truthful unknown results rather than an
  unsupported-domain worker failure. The explicit confirmation override is
  accepted only in the test profile while D3's handoff is pending. In-memory
  store/journal injection is likewise test-only; default runtime uses owned SQL
  and existing authenticated GM/DM/DE readers. No fallback is selected on errors.
- `persistence.py`: EvaluationSink accepts a validated per-goal-type evaluator
  version map and records the correct version/fingerprint for each result.
  Existing callers supplying a single evaluator version remain supported.
- `procurement.py`: version `procurement-v1.2-phase2`; existing bounded solver
  and exact matching remain. Condition reasons identify covered/requested item
  counts, missing names, unit mismatches, unsupported quality requirements,
  requested quantities lacking stock proof, and sellers with missing/inconsistent
  shipping. Counts measure distinct requested lines in the supplied basket, not
  scraping progress or a fulfillment percentage. The public line-cost helper
  rejects negative, boolean and floating-point prices. Costs remain integer EUR
  cents with Decimal ROUND_HALF_UP per line; shipping is once per seller and
  additional tax once per line. No substitute, conversion, quality inference or
  partial-basket savings percentage was added.
- `tests/procurement_recorded.py`, `procurement_recorded_smoke.py`: self-contained
  recorded peers, explicit test stores, actual worker/solver/outbox composition
  and actual signed-service-JWT saved-evaluation API. No other developer's code
  or running peer service is needed. External connections are blocked.
- `tests/test_phase2_procurement.py`: kg/L/pcs multi-seller totals, missing facts,
  original café uncertainty, evaluator-version persistence, replay, default
  unknown behavior, test-only policy guard and integer-price validation.
- `tests/test_persistence_outbox.py`: the transaction double records the supplied
  evaluator version so the mixed-domain runtime's persistence selection is tested.

No shared contracts, routing, requirements, production environment keys or DB
migrations changed in this task. Existing GE migrations 001..004 still apply.
Other developers' source and earlier uncommitted D4 work remain preserved.

## Recorded evidence

Executed independently:

```bash
/private/tmp/goalpulse-d4-venv/bin/python services/goal-evaluation/tests/procurement_recorded_smoke.py --output /private/tmp/goalpulse-r2-d4-04-recorded.json
```

Observed PASS; output identifies `mode=recorded_test`, `live_deployment=false`
and `external_network=blocked`. Export refuses to overwrite an existing file.

| Scenario | Result |
|---|---|
| Explicit normalized one-unit L/kg/pcs fixture | Satisfied; 2720 EUR cents; one saved evaluation, two declared event intents under replay; saved API 200 |
| Same basket with default unconfirmed normalization | Unknown; goal.evaluated only; no verified event |
| Original 20 L / 10 kg café goal, with quality requirements | Unknown even when fixture cost semantics are confirmed; no verified event or progress percentage |

The 2720-cent fixture is 115 + 1600 + 300 in lines, 500 + 200 in seller shipping
(the 500 fee appears on two same-seller observations but is counted once), and
2 + 3 in additional line tax. These are synthetic fixture facts, not live offers.
The API returns the exact saved immutable evaluation and its correct version.

The original café request may have a computable fixture cost, but that does not
prove the requested quantity or quality is available. Names/excerpts such as
“lactose free” never substitute for structured quality proof. Missing facts stay
unknown; a separate definite failure such as a known over-budget total still
takes precedence as unsatisfied under the approved tri-state aggregation.

## Contract blockers and handoff

Observation v1 has no available quantity, typed quality/equivalence assertions,
or basket-specific shipping/tax quote. The private D3 normalizer is not a shared
contract and must not be imported. A global flag cannot manufacture these facts.

D1/D3 need to coordinate the versioned evidence extension already anticipated by
ICP-13 and the normalized-cost handoff. At minimum, the approved release must
establish available quantity with units, explicit requested-quality mappings,
unit/package/price meaning, merchant order identity, request-specific shipping
and additional-tax treatment, and quote/provenance freshness. D1 chooses the exact
new versioned DTO/API and migration with affected GE/NS/AI owners. No speculative
fields, quality codes, event names or v1 schema changes are implemented here.

Migration order: approve the typed contract/policy and example fixtures; implement
D3's authenticated producer/read path and checker; update GE and downstream
consumers with contract tests; audit existing data; enable live normalization
only after recorded and real-source evidence meet those requirements. Keep v1
consumers fail-closed during rollout. The existing missing goal-version source
lookup and D1 private runtime/auth deployment gates also remain open.

## Acceptance and verification

| Criterion | Status |
|---|---|
| Recorded kg/L/pcs multi-seller total incl shipping once and taxes | PASS |
| Missing item/shipping/tax/quality/quantity proof cannot verify | PASS |
| No silent unit conversion, arbitrary equivalent or partial savings metric | PASS recorded regression suite |
| Actual worker replay and exact saved-evaluation API without peers | PASS recorded |
| Confirmed test normalization unavailable in dev/demo | PASS |
| Real DE normalized basket, quantity/quality and checkout-cost proof | NOT RUN / blocked by evidence contract and D3 handoff |
| Live PostgreSQL/Rabbit/Gateway/source integration | NOT RUN / existing infrastructure and P0 gates open |

Executed from repository root:

```bash
python scripts/validate_contracts.py
python -m pytest -q
```

Default Python lacks PyYAML/pytest and failed before running those checks. Using
the existing venv and native-readline workaround:

```bash
/private/tmp/goalpulse-d4-venv/bin/python scripts/validate_contracts.py
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys,runpy;sys.modules["readline"]=None;sys.argv=["pytest","-q","-rs"];runpy.run_module("pytest",run_name="__main__")'
```

Results: **1039 passed, 19 skipped**, one existing Starlette/httpx deprecation
warning. Skipped tests require explicitly configured disposable PostgreSQL/
Rabbit services; no regression was skipped. Contract validator PASS: five schemas,
13 examples, four event routes. `git diff --check` PASS. No provider calls,
dependency installation, service port opening, commit, push or PR occurred.
