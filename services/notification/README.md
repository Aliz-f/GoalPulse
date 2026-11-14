# notification — GoalPulse service

**Implementation status: notification core, private HTTP app factory,
authenticated HTTP readers, Rabbit delivery adapter and worker composition
implemented. Live deployment/acceptance remains pending.**

### Phase 2 — R2-D4-02

`notification.api.create_app` supplies the owner-scoped list/mark-read handlers
using required D1 authorization dependencies. `notification.runtime.build_worker`
wires the SQL store, service-token readers, consumer and durable retry journal to
RabbitMQ. P0 product notifications use the ADR-0003 business fingerprint, require
proof at most ten minutes old (or the stricter goal limit), and share a sliding
one-hour rate cap across versions of the same owner/goal. Apply
`migrations/003_goal_rate_limit.sql` after the existing migrations. Transactions
explicitly use READ COMMITTED so the rate check sees preceding lock holders'
committed notifications.

The API factory opens no ports; D1 must bind accepted service authentication and
trusted owner forwarding before routing through Gateway. The Phase 2 extension's
merge approval, GE runtime, live database/broker recovery and cross-service
fingerprint alignment remain integration gates. See
[Phase 2 implementation and acceptance evidence](PHASE2_HANDOFF.md) for exact
interfaces, migration/upgrade caveats and PASS/NOT RUN results.

The sections below preserve the Phase 1 implementation record. The Phase 2
handoff above describes the current adapters and supersedes the earlier notes
that HTTP/readers/broker adapters had not yet been implemented.

The local `notification` package consumes only the frozen
`goal.opportunity_verified.v1` event. It validates producer/envelope and goal
aggregate identity, deduplicates the inbox, and reads authoritative GM runtime,
immutable GoalSpec, GE evaluation and DE evidence through injected read-only
adapters. These local adapter methods are not approved HTTP paths or wire DTOs.
ICP-01/08 are accepted upstream under ADR-0002; concrete D4 authenticated
upstream adapters and live wiring remain pending.

Processing requires active/latest goal version, consistent ownership and
evaluation identity, verified/satisfied evaluation, every required domain gate,
nonempty evidence citations, checked in-stock observations, FI delivery, known
shipping/tax and original capture freshness. It rejects a changed or untrustworthy
source snapshot. A newer checked capture with identical offer
facts is allowed while the original cited capture remains fresh; no citation or
timestamp is replaced to extend its lifetime. The final GM check catches
owner/lifecycle/version changes during
processing; time is read after that potentially slow request. The recipient is
always taken from GM, not event/client input. GE remains authoritative for domain
evaluation; Notification independently checks the downstream safety gates.
Because Observation.v1 has no available-quantity fact, product or procurement
requests for anything other than exactly one normalized unit are suppressed even
if an older or malformed evaluation claims they are verified.

Within one owned-DB transaction, processing stores the inbox claim, an unread
notification and an in-app delivery row in state `available`. Available means
stored for a future in-app read, not proof that a user saw it. A failed read,
write or commit rolls back the entire operation. `handle()` returns only after
commit; a future broker worker must ACK afterward. Duplicate/suppressed/disabled
events are durable no-ops. Only explicit `RetryableReadError` reads retry (three
attempts, 0.1/0.2 second exponential delays by default, max five attempts).
Other failures propagate. Broker delivery retry limits/DLQ and read timeouts are
still the responsibility of future concrete adapters.

Deduplication is enforced by SQL uniqueness on
`user_id + goal_id + goal_version + offer_fingerprint + channel`.
The fingerprint policy matches GE's goal/version/criteria and normalized offer
snapshots, retaining listing variant queries and payable components while
excluding capture IDs/times. A golden handoff test pins compatibility without
importing GE's private modules. Repeated captures of an identical offer produce
one logical notification; changed offer costs are distinct snapshots. Notification
IDs are stable for that owner/goal/version/fingerprint/channel.

Notifications contain constant safe title/summary text, evaluated time, original
evidence timestamps and display links, with an explicit checkout-revalidation
notice. Scraped text, goal text and credentials do not become notification copy.
Unsafe literal/private/credentialed links are rejected. No URL is fetched or DNS
resolved here; this display filter is not a substitute for D3 SSRF/redirect
controls. Recognized authentication/payment credential parameters in query strings
or fragments are rejected, including case/encoding variants. Product variant
parameters retain their exact values; no redaction changes the offer being
referenced. The policy also applies to newer corroborating captures. All adapter
calls carry the original correlation ID.

In-app defaults enabled when no preference exists and honors the immutable goal's
`notify_user` action. Email defaults disabled in the migration and no email,
purchase or external delivery adapter exists. The private HTTP handler implements
the accepted ICP-02/MVP-07 list/mark-read routes; D1 must still supply authenticated
Gateway forwarding. Two-owner handler isolation is covered with synthetic trusted
principals, not claimed as deployed identity integration.

### Setup and verification

Apply `migrations/001_initial.sql`, `002_processing_attempts.sql` and
`003_goal_rate_limit.sql` once, in order, to the **notification-owned** PostgreSQL database only. Tables include
preferences, notifications, deliveries, inbox and processing journals, with
foreign keys only within this database. No cross-service SQL is performed.
`PostgresStore(connect)` accepts an injected DB-API connection factory with
autocommit disabled; importing modules does not connect. Service manifests declare
the existing approved PostgreSQL, broker and HTTP runtime stack; no new framework
or environment variable was added.

For local imports, add `services/notification` to `PYTHONPATH` and construct
`Contracts(Path("contracts/schemas"))`. Package the unchanged schema artifacts
alongside the service before deployment. Existing root development requirements
provide `jsonschema`/`pytest` for the tests. From the repository root:

```bash
python scripts/validate_contracts.py
python -m pytest -q
python -m pytest -q services/notification/tests
```

This machine's native Miniconda `readline` import crashes before pytest
collection. The process-local workaround used for verification is:

```bash
/private/tmp/goalpulse-d4-venv/bin/python -c 'import sys, runpy; sys.modules["readline"] = None; sys.argv = ["pytest", "-q"]; runpy.run_module("pytest", run_name="__main__")'
```

Tests cover valid creation, replay and capture deduplication, source/owner/version
mismatches, final-state races, stale/incomplete evidence, preferences, safe
content, transient-read backoff and transaction failures. PostgreSQL migration
execution/concurrency, real RabbitMQ recovery and deployed Gateway authentication
remain **NOT RUN** in the recorded environment. The Phase 2 worker/API adapters
are implemented, but unit doubles do not prove those operational gates.

### Local list and mark-read operations (D4-NS-02)

`reads.NotificationAccess(store, identity, clock)` has two local operations:
`list(limit=20, before=None)` and `mark_read(notification_id)`. Neither accepts
a caller-supplied owner. The required `identity` adapter returns a
`VerifiedPrincipal` only after validating server-side authentication claims;
there is no default/header-based identity. Current tests inject synthetic
principals. The dataclass itself performs UUID validation, **not authentication**.
D1 must provide the approved authentication adapter before exposing these reads.

SQL always filters by that subject. Lists sort by `created_at DESC` and UUID
descending, with a stable keyset position, an extra row to detect more results,
and limits of 1–100. A `Cursor` is a typed local position, not a proposed wire
format or an approved public pagination extension. A cursor from another user's
page never removes the owner predicate. Returned items omit internal ownership,
fingerprints, delivery metadata and provider excerpts.

Mark-read uses a single owner-filtered UPDATE, preserves the original read time
with COALESCE, and reports the same local `NotificationNotFound` for absent and
unowned IDs. Transaction failure rolls back. Missing or unrecognized principals
raise `AuthenticationRequired` before storage access. Read/storage failures
propagate instead of returning a fabricated empty feed or successful update.
`notification.api.create_app` maps these operations to the accepted owner-scoped
extension, requires a propagated UUID correlation ID and returns fixed safe errors
without exposing database exceptions.

No shared contract or Gateway file is edited by this service change; the handler
implements the already accepted additive Phase 2 route. Tests cover two-user isolation, guessed
IDs, synthetic/invalid identity values, stable multi-page ordering, insertion
between pages, cursor bounds, first-read timestamp preservation and failure
rollback. PostgreSQL execution and deployed JWT/Gateway/frontend integration
remain NOT RUN. ICP-02/MVP-07 is accepted in
`contracts/openapi/gateway.phase2.extension.v1.yaml`; D1 still owns Gateway
routing/authentication and D2 owns the client. Local dataclasses do not become
additional external DTOs merely by being included in service code.

### Preferences and processing retry policy (D4-NS-03)

`preferences.PreferenceAccess(store, identity, clock)` gets the authenticated
owner's preferences and updates only `in_app_enabled`. It uses the same required
trusted identity adapter as local reads, accepts no caller-selected owner, and
requires a strict boolean. New rows default to in-app enabled/email disabled.
Opting out affects future processing, not already-stored notifications. The
consumer rechecks the preference after evidence reads and locks the owned
preference row with `FOR SHARE` until its transaction finishes. A concurrent
preference update serializes with notification creation; freshness is checked
after any lock/GM wait. Re-enabling does not replay a previously suppressed event.

Email transport, address verification and opt-in setup remain deferred. A stored
email preference by itself grants no delivery capability:
`Preferences.email_delivery_available` is always false, there is no email setter,
sender or email delivery row, and new rows keep email disabled. No purchase or
credential action is added. Preference dataclasses are local values, not public
DTOs or approved preference endpoints.

`retry.RetryCoordinator(contracts, store, consumer, clock)` wraps the existing
consumer. `run(event)` returns a local `Decision`: ack, retry with a delay, or
dead_letter. `NotificationWorker` applies that decision with manual ACK/NACK and
the configured durable retry/DLQ topology; raised storage failures remain
unacknowledged when the worker connection is retired. No routing keys are invented.

The coordinator first commits an attempt reservation with an expiring lease
(60 seconds by default), then invokes the consumer in its own atomic business
transaction, then commits the outcome. An active lease prevents a second worker
from reserving the same event. Expired leases recover after crashes; attempt
counts remain durable. Completion checks the token so an old worker cannot
overwrite a replacement owner's result. Configure real adapter deadlines and
total processing budgets below the lease; tests do not establish those transport
budgets or PostgreSQL locking behavior.

Default processing attempts are capped at five. Retry delays are 1, 2, 4, ...
seconds capped at 300, measured after failure. Schema/identity/permission failures
are terminal immediately; other read/storage failures get bounded retries.
Failures are stored with fixed reason codes and no raw payload, credentials or
provider exception text. Invalid envelopes get safe rejection metadata only.
The journal is independent of the rolled-back consumer transaction so failure
history survives when no notification/delivery row exists yet. `deliveries`
continues to describe stored in-app availability; no fake external send occurs.

If notification commit succeeds but journal commit fails, redelivery waits for
lease recovery and the durable inbox makes consumption a no-op. Terminal results
are retained for operator inspection, not silently requeued. Suppressed/disabled
events are ACK decisions and are not resurrected by preference changes. Inbox and
offer uniqueness still provide the final duplicate-effect safeguards.

`status_counts()` is an internal aggregate snapshot of processing states,
rejections and stored delivery states; it returns no user/event IDs. There is no
new metrics HTTP endpoint. `002_processing_attempts.sql` adds the processing and
safe rejection tables, and `003_goal_rate_limit.sql` adds the owner/goal/time
index used by the serialized hourly cap. Apply both after `001_initial.sql`.
The service requirements declare the existing approved runtime stack. Tests cover
retries/backoff/exhaustion, crash leases, stale-worker
tokens, commit failures, durable failure history, duplicate effects, authenticated
preferences and an opt-out during evidence fetching.

Live migration/concurrency, RabbitMQ restart/DLQ and authenticated preference UX
remain NOT RUN. D4-NS-03's policy and worker adapter are implemented locally,
while operational acceptance still needs D1 deployment/identity coordination.

### Handoff

Frozen v1 schemas and routing remain unchanged; the service implements the
accepted additive Phase 2 notification API. Use accepted ICP-01/08 and D1 service
identity, coordinate packaging/driver and durable broker topology, then run all
three owned migrations and operational tests. D3 supplies immutable evidence
and latest-source reads; GE supplies complete strict evaluations. A minimal
schema-valid satisfied fixture lacking required domain gates is deliberately
suppressed. D2 can consume the accepted notification DTO through Gateway.
D4-NS-02 still requires D1 Gateway and D2 client wiring; D4-NS-03 local
channel/preference policy is implemented; live deployment remains pending.

- [Service specification](../../docs/implementation/06-notification.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` needs formal signoff before becoming an implementation contract. Do not guess interfaces or read other services' databases.
