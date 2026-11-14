"""Opt-in PostgreSQL recurrence/concurrency/quota acceptance; no provider calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.jobs import DiscoveryJobs
from goalpulse_discovery.limits import UsageStore
from goalpulse_discovery.registry import SourceRegistry
from goalpulse_discovery.scheduling import ScanStore
from test_discovery_postgres_live import database
from test_runtime_pipeline import GoalReads


def test_postgres_due_slot_concurrency_and_nested_scan_plan_survive_restart(database):
    clock = [datetime(2026, 11, 14, 12, tzinfo=timezone.utc)]
    goal = deepcopy(GoalReads().goal)
    goal.update(goal_id=str(uuid4()), owner_user_id=str(uuid4()))
    goal["monitoring"]["frequency_minutes"] = 60
    goal_id = UUID(goal["goal_id"])
    store = ScanStore(database, clock=lambda: clock[0])
    assert store.admit(goal, initial=True) is None
    store.initial_finished(goal_id, 1, "extracted")
    assert store.enqueue(goal_id, 1)["status"] == "not_due"
    clock[0] += timedelta(minutes=60)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.enqueue(goal_id, 1), range(2)))
    assert sorted(r["status"] for r in results) == ["coalesced", "queued"]
    scan_id = UUID(results[0]["scan_id"])
    store = ScanStore(database, clock=lambda: clock[0])  # New process/connection factories, same durable rows.
    class Worker:
        def run(self, got_goal, version, correlation, *, scan_id):
            # FK check uses another connection while the outer scan row is
            # claimed: this specifically proves the lock-mode compatibility.
            assert got_goal == goal_id and version == 1
            plans = store.plans(scan_id)
            assert plans.save_plan(goal_id, version, ["https://example.com/recorded"]) == ["https://example.com/recorded"]
            return "extracted"
    assert store.run_once(Worker()) == "done"
    assert store.run_once(Worker()) == "idle"
    assert store.plans(scan_id).load_plan(goal_id, 1) == ["https://example.com/recorded"]
    assert store.status(goal_id)["next_due_at"] == clock[0] + timedelta(minutes=60)


def test_postgres_per_user_day_limit_never_reserves_call_101(database):
    now = datetime(3000, 1, 1, tzinfo=timezone.utc) + timedelta(days=uuid4().int % 100000)
    owner, other = uuid4(), uuid4()
    # An independent global budget lets this test isolate the per-user policy.
    usage = UsageStore(database, max_operations=200, max_credits=1000)
    def resource():
        return uuid4().hex + uuid4().hex
    for _ in range(100):
        usage.reserve(resource(), now, ttl_seconds=1, owner_user_id=owner)
    with pytest.raises(ProviderError, match="USER_DAILY_CALL_LIMIT"):
        usage.reserve(resource(), now, ttl_seconds=1, owner_user_id=owner)
    usage.reserve(resource(), now, ttl_seconds=1, owner_user_id=other)
    usage.reserve(resource(), now + timedelta(days=1), ttl_seconds=1, owner_user_id=owner)
    with database.transaction() as c:
        c.execute("SELECT operations FROM provider_user_day WHERE owner_user_id=%s AND day=%s", (str(owner), now.date()))
        assert c.fetchone()[0] == 100


def test_postgres_active_goal_and_source_limits_are_scoped_to_authenticated_owner(database):
    goal, owner = GoalReads().goal, str(uuid4())
    store = ScanStore(database)
    ids = [str(uuid4()) for _ in range(3)]
    assert store.admit(dict(goal, goal_id=ids[0], owner_user_id=owner)) is None
    assert store.admit(dict(goal, goal_id=ids[1], owner_user_id=owner)) is None
    assert store.admit(dict(goal, goal_id=ids[2], owner_user_id=owner)) == "ACTIVE_GOAL_LIMIT"
    registry = SourceRegistry(database, resolver=lambda _: ["93.184.216.34"])
    for _ in range(4):
        source = registry.register(f"https://example.com/recorded/{uuid4()}")
        registry.subscribe(UUID(ids[0]), 1, source.source_id)
    registry.reconcile(UUID(ids[0]), current_version=1, lifecycle="paused")
    registry.reconcile(UUID(ids[0]), current_version=1, lifecycle="active")
    with database.transaction() as c:
        c.execute("SELECT count(*) FROM goal_source_links WHERE goal_id=%s AND state='active'", (ids[0],))
        assert c.fetchone()[0] == 3
