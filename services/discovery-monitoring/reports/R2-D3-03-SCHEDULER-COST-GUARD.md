# R2-D3-03 — periodic scans, operator checks and cost guard

Date: 2026-11-14. Owner: Developer 3. Branch:
`feat/r2-d3-03-scheduler-cost-guard`. New work is restricted to
`services/discovery-monitoring/`. Both preceding tasks' uncommitted work was
preserved; no Data & Evidence/shared/other-owner file was edited in this ticket.

Authority: accepted ADR-0003/MVP-13, existing R2-D3-01 runtime composition and
frozen v1 interfaces. The prior ticket's actual-stack acceptance remains pending;
this report does not promote a recorded test to live evidence or certify R3 release.

## Implemented

- A durable scheduler polls known activation jobs, schedules and source links,
  reconciles authoritative GM lifecycle/version/ownership, and admits at most two
  monitored active goals per owner. Uncertain GM reads retain occupied slots and
  spend no provider credit. Pausing releases a slot; blocked monitoring suspends
  source subscriptions without changing GM lifecycle or another service's data.
- Initial discovery registers the approved interval and first due time. Periodic
  and operator scans share DiscoveryWorker, authenticated adapters, immutable HTTP
  intents, retry/deadline handling and outbox behavior. New scans get new durable
  identities/plans, while retries reuse their exact capture/submission.
- Schedule-row locks, immutable plans, deterministic due identities, request-ID
  audit records and a unique pending-goal index prevent duplicate work. Restarts
  resume pending scans; overdue intervals coalesce rather than generating a burst.
  Initial pending jobs coalesce with operator/periodic requests too.
- The policy default is 60 minutes; an explicit approved interval is honored.
  Values below 30 are blocked with `INTERVAL_RECONFIRMATION_REQUIRED`, preserving
  the requested value and preventing even the initial provider call. No operator
  override or silent rounding to 30 exists.
- Every Context request rechecks current GM state and consumes a reservation
  against the authenticated owner's UTC-day counter, capped at 100. Reservation
  and call intent commit atomically with global quota checks. Failed/uncertain
  calls remain charged. Denied/cooldown calls are not executed; receipt replays
  consume no Context call. Existing service-wide budgets may stop work sooner.
- Subscription writes serialize per goal and keep at most three active URLs.
  Reconciliation restores only the three most recently subscribed current-version
  sources, so pause/resume cannot reactivate evicted candidates over the cap.
- Operator check-now/status are local CLI commands. OS process-token SID on
  Windows or effective UID on POSIX must match the trusted `DM_OPERATOR_IDS`
  allowlist. Empty/mismatched identity is denied before loading runtime/credentials.
  No caller username, owner argument, role flag or public admin route grants access.
- Worker output and local schedule status expose fixed policy/result codes and
  next due time. Native Context monitors/webhooks remain outside the running P0
  path; no webhook or check-now HTTP endpoint was registered.

## Acceptance evidence

| Criterion | Result | Evidence |
|---|---|---|
| First scan and second due scan with restart/dedup | PASS, recorded/offline | Clock-controlled main runtime/worker and authenticated GM/DE apps: initial + due 60-minute scan create two observations/four Context transport requests, duplicate tick queues zero jobs, reconstructed worker completes once. |
| Approved cadence and unsupported shorter cadence | PASS, recorded/offline | 30/90/1440-minute boundaries are honored. 29/10-minute goals retain the value, block with reconfirmation reason and make zero provider calls. Policy helper defaults to 60. |
| Cost/admission limits and visible denial | PASS, recorded/offline | 100-call boundary, separate owners, UTC-day reset, failed-call accounting, two active slots, pause release and worker/status reason assertions. SQL boundary checks verify atomic reservations and owner scope. |
| Operator-only check-now | PASS, deterministic tests | Actual platform identity acquisition exercised without logging identity; unauthorized CLI denied before configuration load; allowlisted test identity queues the common job, request replay deduplicates and pending scans coalesce. Actual privileged live enqueue remains NOT RUN. |
| No unauthenticated webhook/public check-now | PASS | Route-set regression; new commands are local only. No native monitor is created by the runtime. |
| Actual PostgreSQL concurrency/foreign-key locking | NOT RUN | Test DSN absent; three opt-in tests added for concurrent due enqueue/nested plan write, per-user limit, source/admission limits and resume. |

Recorded persistence doubles deliberately retain durable-state contents across
runtime reconstruction. They are not a substitute production database. The real
SQL tests append fixtures to the dedicated test DB when provisioned.

## Changed files

All paths below are relative to `services/discovery-monitoring/`.

| Files | Purpose |
|---|---|
| `goalpulse_discovery/scheduling.py` | Durable schedules/scan jobs/plans, admission, reconciliation, due enqueue, manual request idempotency and local status. |
| `goalpulse_discovery/operator.py` | OS-derived local principal and fail-closed operator allowlist. |
| `goalpulse_discovery/jobs.py` | Share bounded attempt/backoff handling with periodic jobs; retain a fixed visible result code. |
| `goalpulse_discovery/limits.py` | Atomic owner/day call reservation, independent user scopes and shared provider guards. |
| `goalpulse_discovery/workflow.py` | Per-scan stable submission identity alongside preserved initial identities. |
| `goalpulse_discovery/registry.py` | Serialized three-source cap, bounded resume and owned subscription suspension. |
| `goalpulse_discovery/runtime.py`, `__main__.py` | Real scheduler/worker composition, owner-bound provider calls, new local CLI modes and diagnostic output. |
| `migrations/0007_periodic_scans.sql` | Owned schedules/jobs/plans, immutable operator request records and per-user budget tables/operation owner metadata. Earlier migrations unchanged. |
| `tests/test_periodic_scans.py`, `test_operator_and_schedule_sql.py`, `test_scheduler_postgres.py`, `fake_schedules.py` | Clock/restart/replay/cadence/quota/operator/source-lock coverage and opt-in real SQL acceptance. |
| `tests/test_runtime_pipeline.py`, `test_workflow.py` | Inject explicit schedule-store doubles into existing recorded composition; add suspension behavior without weakening expectations. |
| `README.md`, `.env.example`, this report | Updated runtime/configuration and evidence-based handoff. The real ignored `.env` was not edited. |

## Tests

Executed from repository root with the existing `.venv` (Python 3.13.5):

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe scripts/validate_contracts.py` | PASS: 5 schemas, 13 examples, 4 event routes and frozen Gateway OpenAPI. |
| `.venv/Scripts/python.exe -m pytest -q -rs` | PASS: **1101 passed, 18 skipped**. |
| `.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests --tb=short` | PASS: **261 passed, 7 skipped**. |
| `python -m goalpulse_discovery --help` using repository venv from service directory | PASS: scheduler/check-now/status modes load. |
| Local `check-now` with no operator allowlist | PASS denial: `operator=forbidden`; no runtime/provider work. |
| `git diff --check` | PASS. |

All skips are unconfigured PostgreSQL integrations: seven DM, five DE, six GI.
For actual scheduler SQL acceptance, provision `discovery_monitoring_test`, set
`GOALPULSE_DM_TEST_DSN` only in the local environment and run from root:

```powershell
.venv/Scripts/python.exe -m pytest -q services/discovery-monitoring/tests/test_scheduler_postgres.py
```

Live PostgreSQL/RabbitMQ/Context.dev and privileged operator end-to-end smoke are
**NOT RUN** because the runtime stack/test DSN/operator provisioning is unavailable.
No new dependency was installed, service started, port exposed, provider credit
spent, database dropped, or shared contract changed by this ticket.

## Executed redacted smoke logs

Explicit recorded test transports/persistence, not live provider output:

```text
RECORDED_SYNTHETIC initial=done due_60m=1 duplicate_due=0 restart=done observations=2 context_calls=4
RECORDED_SYNTHETIC quota=dead reason=USER_DAILY_CALL_LIMIT context_calls=2
RECORDED_SYNTHETIC requested_minutes=29 reason=INTERVAL_RECONFIRMATION_REQUIRED context_calls=0
```

Actual local CLI denial, before configuration loading:

```text
operator=forbidden
```

## Contracts, deployment and handoff

Frozen GoalSpec/Observation/event/HTTP/routing contracts are unchanged. No new
public or internal HTTP API/event was introduced. DE/GE behavior is unchanged;
new scan evidence uses the existing authenticated submission and `evidence.updated.v1`
path, stays unverified without independent proof and retains its immutable version.

Apply forward migration 0007 before starting the updated runtime. Use scoped
credentials for the owned DM database; disallow TRUNCATE/DDL bypass of immutable
plans/request history. Start the existing consumer/worker/API plus the new
`scheduler` process. `scheduler --once` performs one bounded reconciliation/enqueue
pass. The scheduler's five-second poll and worker poll introduce normal scheduling
jitter; due timestamps, not UI progress, define eligibility.

Newly consumed configuration: existing MVP names
`DM_MAX_ACTIVE_GOALS_PER_USER` (default/max 2) and
`DM_MAX_REQUESTS_PER_USER_PER_DAY` (default/max 100), plus new trusted
`DM_OPERATOR_IDS` (empty denies). These process settings must be provisioned with
the existing DSNs/JWT/provider credentials; only the Context key auto-loads from
the service-local ignored `.env`. No dependency declaration changed in this ticket.

Operators call `check-now --goal-id <UUID> --request-id <UUID>` and retry using the
same request ID. `schedule-status --goal-id <UUID>` exposes reason/result/due time.
The worker executes the queued common job; privilege does not bypass cost,
admission, current-version or cadence gates. Provision and protect the OS identity
allowlist as deployment configuration, not a user-editable request setting.

Two boundaries require D1/D2 coordination rather than unauthorized edits here:
GM owns activation/lifecycle, so this service limits monitoring admission to two
goals rather than rejecting a third GM creation. Also frozen GoalSpec v1 requires
`monitoring.frequency_minutes`: GI/GM must place 60 in the reviewed draft/spec
when unspecified. DM honors an explicit approved value and cannot infer that a
daily value meant 'unspecified'. A sub-30-minute goal needs a newly reviewed,
approved supported cadence through the goal workflow; local scheduling never
rewrites immutable criteria.

The R2-D3-01 live-stack/proof-transport/provider-egress limitations remain. These
local tests do not certify a running full stack or provider-side SSRF enforcement.
