# D4-GE-06 reliability handoff

Scope: Developer 4, `services/goal-evaluation/` only. Tests use synthetic
fixtures and injected adapters; they do not establish live integration.

| Requirement | Service test coverage | Remaining limitation |
|---|---|---|
| Unknown facts block verification | Unknown stock/charges/reliability, quantity-charge matrix, publication revalidation | Live DE extraction is D3-owned |
| Model and seller checks | Exact matching, attributes, hostname policy and injected-text tests | New condition/arbitrary attributes await ICP-13; unsupported equivalence is unknown |
| Money/quantity correctness | Integer representation, large cents, equality/one-cent-over, over-budget lower bounds; D4-GE-05 adds fixture-confirmed fractional basket rounding | Live packaging/tax semantics await D3 confirmation |
| Freshness/cache time | Offset/boundary/future timestamps, stale event time, slow runtime read | DE must preserve original capture time |
| Stale/paused/terminal/owner mismatch | Initial/final processing, enqueue and publication guards | Concrete D4 client/auth wiring to accepted ICP-01/08 remains pending |
| Source fan-out | Two goals, duplicate subscriptions, full rollback | Live DM pagination/linkage remain upstream work |
| Malformed evidence | Goal/source/observation identity, snapshot conflicts and invalid facts | Authenticated HTTP adapters unimplemented |
| Bounded failures | Transient reads, exhausted retry, permission/schema no retry | Actual HTTP 429/5xx mapping and deadlines await approved adapters |
| Deduplication | Input replay, same-offer captures, formatting normalization, price/version changes | Real PostgreSQL concurrency NOT RUN |
| Atomic persistence | Full processor-consumer-sink flow; enqueue/commit failure rollback | PostgreSQL migrations and immutable trigger enforcement NOT RUN |
| Outbox recovery | Stable-ID replay, confirm loss, broker outage, retry timing and confirm-then-commit failure | Actual RabbitMQ restart/durability/concurrent workers NOT RUN |
| Immutable reads | Detached copy, missing ID, wrong returned ID rejection | Accepted M2M evaluation HTTP operation and service-auth wiring remain unimplemented |

## Fixes established by failing regression tests

1. Freshness is recomputed after the GM runtime request; latency cannot extend
   an observation's usable lifetime.
2. Publication checks evaluation UUID as well as goal/version.
3. Product identity fingerprinting normalizes case/whitespace to avoid duplicate
   opportunities for formatting-only changes.
4. Backoff starts when a slow attempt fails, rather than when it began.
5. Known nonnegative payable components above the cap mean unsatisfied. Missing
   charges remain unknown and are never fabricated as zero.

Product evaluator implementation version is `product-v1.2` for reproducibility,
not a contract version. This task adds no contracts, events, dependencies,
migrations or environment variables. If fingerprints have already been stored
outside this unintegrated checkout, coordinate deployment of the normalization
change; this code does not rewrite existing records.

## Verification and remaining gates

Run from the repository root:

```bash
python scripts/validate_contracts.py
python -m pytest -q
python -m pytest -q services/goal-evaluation/tests
```

This machine's Miniconda native `readline` module crashes before pytest
collection. The process-local workaround used in the temporary dev environment:

```bash
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q"]; runpy.run_module("pytest", run_name="__main__")'
```

D4-GE-06 product/mock coverage and D4-GE-05 fixture basket coverage do not
complete operational acceptance or live procurement evidence gates.
D1/D3 must supply approved APIs, service authentication, database driver/credentials
and a RabbitMQ adapter/topology. Apply both owned-DB migrations and run the
database/broker fault scenarios in that environment. Notification/package
deduplication, user HTTP ownership and non-purchase tests belong to their later
tasks and are not claimed as passing here.

`test_procurement.py` additionally covers complete and sparse baskets, one vs
multiple sellers with full shipping, maximum sellers, fractional ROUND_HALF_UP
line costs, quality/normalization gates, bounded exhaustive search, deterministic
ties and basket persistence/publication/deduplication. Default unconfirmed
normalization and the original quality-constrained café fixture remain unknown.

## Pre-PR review fixes

Unchanged checked recaptures now corroborate original still-fresh proof across
GE relay, Notification and Agent Integration. Changed price/stock/charges,
downgraded reliability, future captures and expired original proof remain blocked.
Capture IDs and timestamps are never substituted into the original evaluation
or package. A terminal unconfirmed reservation can recover on a new fresh capture;
confirmed publication still deduplicates later identical offers. New migration
003 records the outbox event associated with each reservation; its real SQL and
concurrency execution are NOT RUN without PostgreSQL.

Service-local URL policies reject recognized credential parameters in query and
fragment components, including encoded/case aliases, while preserving product
selectors. Regression tests cover filtering, builder/read validation, notification
suppression and separate-process unchanged-capture/credential-URL handoffs.
