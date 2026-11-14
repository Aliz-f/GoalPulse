# goal-evaluation — GoalPulse service

**Implementation status: evaluator, mock processing, PostgreSQL persistence and outbox relay adapters implemented; service not runnable yet.**

The `goal_evaluation` package provides a pure `GoalEvaluator` protocol, strict
dispatch and local frozen-schema validation, and an injectable evidence-event
consumer. No evaluator is registered by default: unimplemented domains raise
`NotImplementedError`, never a fabricated decision. Importing the package opens
no network or database connections.

`migrations/001_initial.sql` creates service-owned evaluations, condition results,
offer fingerprints, inbox and outbox tables. Apply it once with a migration runner
to the **goal_evaluation** database only. No migration is automatically applied.
`PostgresStore` accepts a DB-API connection factory with autocommit disabled;
its transaction cursor lets a handler persist results/outbox in the same
transaction as inbox claiming. Exceptions roll back, including the inbox claim.
The broker adapter must ACK only after `handle` returns; failures require bounded
retry and dead-letter handling. Concurrent duplicates rely on PostgreSQL's inbox
primary key and `INSERT ... ON CONFLICT`, not an in-memory lock.

Run from the repository root after installing existing `requirements-dev.txt`:

```bash
python scripts/validate_contracts.py
python -m pytest -q
python -m pytest -q services/goal-evaluation/tests
```

For local imports, add `services/goal-evaluation` to `PYTHONPATH` and construct
`Contracts(Path("contracts/schemas"))`. Deployment packaging must include the
unchanged frozen schema artifacts and supply their explicit directory.

No new environment variables or production dependencies are introduced. The
existing `jsonschema` dependency is required. A PostgreSQL driver, runtime
packaging and broker adapter still require coordinated dependency decisions.
Live database/broker behavior is not established by the unit test doubles.

### Product evaluator (D4-GE-03)

Register explicitly with the dispatcher:

```python
from pathlib import Path
from goal_evaluation.domain import Contracts, Dispatcher
from goal_evaluation.product import ProductPurchaseEvaluator

contracts = Contracts(Path("contracts/schemas"))
dispatcher = Dispatcher(contracts, {
    "product_purchase": ProductPurchaseEvaluator(contracts),
})
# result = dispatcher.evaluate(goal_spec, observations, now_utc)
```

The pure evaluator validates direct calls too. It checks exact structured model
or brand-plus-model equality after case/whitespace normalization, supported
attributes (`model`, `brand`, `product_name`), seller hostname allow/block policy,
stock, FI delivery, freshness, reliability, known charges and integer quantity
totals. Domains match exactly, including subdomains; block overrides allow.
Any definite failed condition makes an offer unsatisfied; otherwise unknown
blocks satisfaction. Alternatives are ranked satisfied, unknown, unsatisfied,
then by known lowest total and observation UUID. Facts are never mixed across
offers. Identical input snapshots and clock produce identical evaluation IDs;
the evaluator version is included in their identity. Conflicting snapshots for
one observation ID are rejected.

Payable cost is `quantity * unit_price + shipping + additional tax`, with
offer-level shipping and tax applied once. Only `pcs` has a known product-unit
conversion. Money uses Python integers; even integral floating point inputs are
rejected. Missing charges stay unknown; checked explicit zero is valid.
Freshness uses the original `observed_at`, including timezone offsets; future
timestamps and stale/cached timestamps cannot verify. DE must preserve actual
capture time. The caller must supply the latest relevant snapshots; selecting
those and checking goal lifecycle remain D4-GE-02/04 responsibilities.

**Acceptance blocker:** frozen Observation.v1 has no product condition or general
attributes. The historical request requiring `new` and `gpu_model` is preserved
in `tests/fixtures/goal-spec.product.requires-new.fixture.json` and returns
unknown. The shared product example was subsequently changed upstream to accept
any condition with exact-model policy; it is not the regression input for this
missing-evidence case. No facts are extracted from free text to bypass that gap. Supported
tests use a distinct goal accepting `condition=any`, exact model and supported
attributes. Different-model equivalence is unknown without an approved policy.
See [contract gap and approval request](CONTRACT_GAPS.md). D4-GE-03 cannot be
declared fully accepted until Developer 1 coordinates the missing evidence
contract/policy with affected owners. No existing fixture was changed.

ICP-01/08 are accepted upstream under ADR-0002. D4-GE-02 still needs concrete
authenticated clients for those approved operations; local adapters remain in use. D4-GE-04 adapters below supply result writes, lifecycle
checks, fingerprints and confirmed-publication orchestration. Notification and Agent
Integration cores are included separately; live wiring remains incomplete. No external
interface, routing key or contract has changed.

### Mock source/evidence processing (D4-GE-02)

`processing.EvidenceProcessor` plugs into `EvidenceConsumer` as its `process`
callable. Inject mock `SubscriptionReader`, `GoalReader` and `EvidenceReader`,
the validated dispatcher, a UTC clock, and a transaction-scoped result sink.
`Subscription` and `Runtime` are local adapter values, not approved wire DTOs.
No private HTTP path, service authentication implementation, or external
producer/consumer interface is introduced. Map them to an accepted contract
using accepted ICP-01/ADR-0002 and the D1/D3 implementation handoff.

For each evidence event, processing resolves all subscribed goal versions,
deduplicates subscriptions, reads authoritative runtime, skips inactive/obsolete
versions, validates immutable GoalSpec identity and owner, and fetches the
trigger observation and all linked sources. Source and observation identities
must match. It selects the newest timestamp per source, with observation UUID
as deterministic tie-break, so delayed triggers do not overwrite newer offers.
Conflicting immutable records, missing source linkage and invalid schemas fail
closed. Observation time is preserved; cached evidence cannot become fresh due
to event receipt. A final runtime read after evaluation suppresses results if
the goal was paused, terminated, changed version or changed owner.

Every reader receives the original correlation ID. Only explicit
`RetryableUpstreamError` failures are retried: three attempts by default with
0.1/0.2-second exponential delays, configurable up to five attempts and a
one-second delay cap. Validation and permission failures are not retried here.
Reader results are bounded (1,000 entries by default); exceeding the bound
fails instead of silently omitting goals. Future transport adapters must enforce
timeouts and bounded pagination. The broker worker must separately cap delivery
attempts and dead-letter permanent failures; it is not implemented yet.

The sink receives `(evaluation, EvaluationContext, transaction)` and must perform
only writes through that transaction. All goals and the inbox claim commit
together; a failed read or sink rolls everything back for redelivery. Never
send external side effects from the sink. `EvaluationContext` retains the exact
goal, observations and original correlation ID for the persistence checks below.
The runtime check is a snapshot, not an atomic lock across services: downstream
consumers must recheck active/latest state, and publication must revalidate
freshness. No running PostgreSQL/RabbitMQ or real HTTP integration is proven by
these mock tests. There are no new migrations, dependencies or environment
variables for this part. Live D4-GE-02 acceptance still needs the concrete D4
clients, D3 services and end-to-end verification; ICP-01 approval is complete.

### Persistence and outgoing events (D4-GE-04)

`persistence.EvaluationSink` persists the immutable evaluation, its condition
rows, and `goal.evaluated.v1` publication intent together with the input inbox
claim. It reserves a unique goal/version/offer fingerprint before enqueueing
`goal.opportunity_verified.v1`. Every generated event validates against the frozen
envelope, uses the declared producer/key and preserves the input correlation ID.
No external effects occur in this transaction. Wire the PostgreSQL store with:

```python
from goal_evaluation.consumer import PostgresStore
from goal_evaluation.persistence import PostgresEvaluationTransaction

# connect must return a service-owned DB-API connection, autocommit=False.
# store = PostgresStore(connect, transaction_factory=PostgresEvaluationTransaction)
```

Construct `VerificationGuard(contracts, dispatcher, goals, clock)` and then
`EvaluationSink(contracts, guard, ProductPurchaseEvaluator.version)` as the
processor's sink. The guard recomputes domain checks with current time and reads
authoritative runtime before reserving a verified opportunity. Non-publishable
evaluations remain historical records without a verified event.

Evaluation deduplication excludes evaluation UUID/time but retains the actual
decision, cited evidence and evaluator version. An identical decision for the
same observations creates no new result/event; a different immutable capture can
create a new evaluation. Offer deduplication excludes observation/capture IDs and
timestamps, and includes goal version, criteria, normalized source hostname and
listing path/query, product identity and payable components. Repeated captures
of the same offer create one logical verification. Changed price/components are
different offers. URL fragments are excluded; variant query strings are retained.
New reservations keep `notification_emitted=false` until the associated
verification event receives a broker confirmation. This confirms publication,
not delivery to the user. A `verification_event_id` binds the reservation to its
outbox row; a terminal, unconfirmed row may be replaced by a later fresh verified
capture. Pending or confirmed reservations still suppress duplicate intents.

Apply `001_initial.sql`, `002_immutable_evaluations.sql` and
`003_offer_publication_tracking.sql` once, in order, to the owned PostgreSQL
database. The third migration is required before running this version of the
sink/publisher. It retains legacy reservations without guessing whether they
were published; any existing legacy data needs an outbox audit before manual
repair. On an initialized DB, apply only the new unapplied migration:

```bash
psql --dbname='<goal-evaluation service DSN>' --file=services/goal-evaluation/migrations/003_offer_publication_tracking.sql
```

The second migration rejects updates/deletes to
evaluations and condition rows. `ImmutableEvaluationReader.get(id)` performs a
validated local read and returns a detached result or None. It exposes no HTTP
route; implementing it requires wiring the accepted ICP-01 operation to D1 service identity.

`outbox.OutboxPublisher.run_once()` locks one due row with `FOR UPDATE SKIP LOCKED`,
validates it, and calls an injected broker's `publish_confirmed` with
`goalpulse.events`, the exact routing key and persistent message flag. Marking
published occurs only after a positive confirmation. The broker adapter must
enable publisher confirms, handle unroutable returns and enforce deadlines;
durable topic exchange, queues and bindings remain infrastructure prerequisites.
No concrete RabbitMQ client or worker startup is supplied here.

Wire `PublicationGuard(contracts, guard, goals, evidence)` as the publisher's
eligibility callback. Before verified publication it reloads immutable data,
rejects changed or untrustworthy snapshots, recomputes freshness/domain gates and
reads runtime. A newer checked capture of identical offer facts is corroborating
evidence, not a change in opportunity identity. The event still cites the original
immutable evaluation and observations, which must remain fresh. New captures
never extend the original proof's lifetime. Future captures, changed offer facts
or downgraded reliability still suppress publication.
Historical evaluated events can publish for obsolete versions; downstream
projections must still respect versions. This check cannot create an atomic lock
across GM and RabbitMQ, so Notification/Agent Integration must recheck too.

Unconfirmed messages and transient failures retry with 1, 2, 4, ... second delays
capped at 300 seconds and five attempts by default. Invalid/unauthorized messages
and suppressed verifications become terminal immediately. Terminal rows stay
stored for operator review. A later fresh verified capture may acquire a
replacement reservation only when the associated old outbox event is terminal
and unconfirmed. Confirmed offers remain deduplicated. No GET or timer invents
fresh evidence or rewrites an old event. Review/re-drive policy remains a
deployment handoff and must revalidate eligibility. Error metadata contains fixed reason
codes rather than raw exceptions or credentials. Broker success followed by DB
commit failure can replay the **same event ID**: downstream inbox deduplication is
mandatory. Exactly-once delivery is not claimed.

Source-link policy rejects recognized authentication/payment credential query or
fragment parameters rather than redacting them and changing the offer URL.
Checks normalize parameter case, punctuation and bounded percent encoding;
ordinary sku, variant, size and color selectors remain unchanged. The same local
policy is used by all three D4 services. No URL is fetched or resolved. Opaque
secrets under arbitrary unrelated parameter names still require upstream
provenance controls; unknown values cannot automatically be classified as secrets.

No production dependency or environment variable was introduced. Live PostgreSQL
migrations/concurrency, RabbitMQ restart recovery and authenticated HTTP reads
remain **NOT RUN / not implemented**: this environment lacks PostgreSQL/Docker
tools and concrete D4 transport wiring are unavailable. The internal interface is accepted. Unit tests use transaction and
broker doubles for rollback, replay, retry, confirm loss, immutability and stale
suppression. D4-GE-04 is not fully accepted operationally. Product reliability
coverage and fixes are documented in [D4-GE-06 test coverage](TEST_COVERAGE.md),
including operational checks still NOT RUN. D1/D3 integration remains unwired
against the accepted internal contract. Fixture-confirmed procurement/basket checks are described below; the
live D3 normalization dependency remains missing.

### Recurring procurement (D4-GE-05)

Register `procurement.RecurringProcurementEvaluator(contracts)` under
`recurring_procurement` in the dispatcher. This pure evaluator validates the
frozen GoalSpec and Observation schemas and performs a bounded exhaustive search
over one complete offer per requested line. Exact normalized item names identify
candidate observations; arbitrary substitutes and package conversions are not
inferred. Duplicate normalized goal item names are rejected to avoid allocating
one observation to overlapping demand. Quantities are not split across offers.

**Default normalization is unconfirmed and cannot verify.** D3-DE-02 is not
implemented in this checkout. Tests explicitly pass
`normalized_cost_policy_confirmed=True` only for synthetic fixtures with agreed
normalized semantics. A live adapter must not enable that flag until D3 confirms
price per selling unit, additional tax per requested line, and shipping per
merchant order for the requested basket. This is a local policy input, not a new
contract field, and it never fills in missing facts. Free-text quality
requirements remain unknown pending structured evidence (ICP-13 coordination).
The original café goal therefore remains unknown even with synthetic prices.

For confirmed fixtures, each line cost is
`ROUND_HALF_UP(Decimal(str(quantity)) * integer_unit_price)` to EUR cents. Exact
matching kg/L/pcs units are required; fractional pieces do not imply package
conversion. Additional tax is applied once per requested line. Shipping is
applied once per normalized source hostname, and all selected observations for
that seller must report the same known charge. Inconsistent or missing merchant
charges remain unknown; no minimum/maximum shipping guess is used.

Search ranks fully satisfied baskets ahead of unknown/unsatisfied alternatives,
then by complete payable total and stable observation IDs. Seller limits, FI
delivery, stock, checked reliability and original observation age are mandatory
gates. Missing items or quality/units/tax/shipping cannot verify. The optimum is
only within the supplied finite dataset and this one-offer-per-line model.
Defaults cap input at 20 items, 200 observations and 10,000 combinations. An
exceeded bound returns an unknown result without verifying a partial search.

`savings_progress` is emitted only for a verified complete basket when the
user-provided current baseline exceeds the target cap. Reaching the cap is 100%
of the requested savings target, capped at 100; no scraping/page-count percentage
or partial-basket progress is generated. Monetary decisions remain integer/
Decimal; the schema percentage is presentation data.

Persistence/outbox guards work with an explicitly registered procurement
evaluator. Configure its sink with `RecurringProcurementEvaluator.version` for
reproducibility. Tests cover shared shipping, multi-seller optimization, seller
limits, fractional rounding, sparse/unknown baskets, deterministic ties,
bounded computation and safe persisted-event publication/deduplication. No new
schema, event, migration, dependency or environment variable is introduced.
Live D4-GE-05 acceptance remains blocked on D3 normalization and structured
quality evidence; no live provider or database/broker acceptance is claimed.

- [Service specification](../../docs/implementation/05-goal-evaluation.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` needs formal signoff before becoming an implementation contract. Do not guess interfaces or read other services' databases.
