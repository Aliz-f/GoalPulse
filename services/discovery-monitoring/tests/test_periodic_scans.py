"""Clock-controlled production polling path; all HTTP/provider inputs recorded."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from fake_schedules import MemoryScanStore, MemoryUserUsage
from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.limits import GuardedProvider
from goalpulse_discovery.runtime import build_runtime
from goalpulse_discovery.scheduling import ScanScheduler, effective_interval
from goalpulse_discovery.workflow import Runtime
from test_runtime_pipeline import PipelineScenario, public_resolver
from test_workflow import MemoryRegistry


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def advance(self, **delta):
        self.value += timedelta(**delta)


def restart(s, clock):
    s.runtime = build_runtime(s.settings, provider_transport=s.provider, resolver=public_resolver,
        http_client=s.http, registry=s.registry, jobs=s.jobs, usage=s.usage, journal=s.journal,
        scans=s.scans, clock=clock, migrate=False)


def scenario(monkeypatch, *, interval=60):
    s, clock = PipelineScenario(monkeypatch), Clock()
    s.goals.goal["monitoring"]["frequency_minutes"] = interval
    s.scans.clock, s.ledger.clock, s.usage = clock, clock, MemoryUserUsage()
    restart(s, clock)
    return s, clock


def test_initial_and_second_due_scan_restart_dedup_and_fresh_attempts(monkeypatch):
    s, clock = scenario(monkeypatch)
    s.activate()
    s.activate(tag=2)
    assert s.runtime.process_once() == "done"
    goal_id = UUID(s.goals.goal["goal_id"])
    assert s.scans.status(goal_id)["next_due_at"] == clock() + timedelta(minutes=60)
    assert len(s.provider.calls) == 2
    clock.advance(minutes=59, seconds=59)
    assert s.runtime.scheduler.tick()["queued"] == 0
    clock.advance(seconds=1)
    assert s.runtime.scheduler.tick()["queued"] == 1
    assert s.runtime.scheduler.tick()["queued"] == 0
    s.provider.scrape["request_id"] = "recorded-second-capture"
    restart(s, clock)
    assert s.runtime.process_once() == "done"
    assert s.runtime.process_once() == "idle"
    assert len(s.provider.calls) == 4  # Search+Scrape for each real scan identity.
    assert len(s.repository.state["observations"]) == len(s.repository.state["outbox"]) == 2
    bodies = s.services.submissions
    assert bodies[0]["attempt_id"] != bodies[1]["attempt_id"]
    assert bodies[0]["fetched_at"] != bodies[1]["fetched_at"]
    assert all(b["extracted"]["evidence_reliability"] == "unverified" for b in bodies)
    owner = UUID(s.goals.goal["owner_user_id"])
    assert s.usage.counts[(owner, clock().date())] == 4


@pytest.mark.parametrize("interval", [30, 90, 1440])
def test_confirmed_interval_is_honored_exactly(monkeypatch, interval):
    s, clock = scenario(monkeypatch, interval=interval)
    s.activate()
    assert s.runtime.process_once() == "done"
    clock.advance(minutes=interval, microseconds=-1)
    assert s.runtime.scheduler.tick()["queued"] == 0
    clock.advance(microseconds=1)
    assert s.runtime.scheduler.tick()["queued"] == 1


def test_unspecified_private_policy_default_is_sixty_not_a_wire_contract_change():
    assert effective_interval({}) == 60


@pytest.mark.parametrize("interval", [10, 29])
def test_short_confirmed_interval_requires_reconfirmation_without_initial_provider_call(monkeypatch, interval):
    s, _ = scenario(monkeypatch, interval=interval)
    s.activate()
    assert s.runtime.process_once() == "done"
    status = s.scans.status(UUID(s.goals.goal["goal_id"]))
    assert status["reason"] == "INTERVAL_RECONFIRMATION_REQUIRED"
    assert status["interval_minutes"] == interval and status["next_due_at"] is None
    assert not s.provider.calls
    assert s.runtime.scheduler.tick()["queued"] == 0
    result = s.runtime.scheduler.check_now(UUID(s.goals.goal["goal_id"]), uuid4(), "sid:test-operator")
    assert result == {"status": "blocked", "reason": "INTERVAL_RECONFIRMATION_REQUIRED"}


@pytest.mark.parametrize("lifecycle,version", [("paused", 1), ("cancelled", 1), ("active", 2)])
def test_pending_scan_rechecks_gm_before_spending_and_skips_obsolete_goal(monkeypatch, lifecycle, version):
    s, clock = scenario(monkeypatch)
    s.activate()
    s.runtime.process_once()
    clock.advance(minutes=60)
    assert s.runtime.scheduler.tick()["queued"] == 1
    s.goals.lifecycle, s.goals.version = lifecycle, version
    assert s.runtime.process_once() == "done"
    assert s.runtime.last_result == "obsolete"
    assert len(s.provider.calls) == 2 and len(s.services.submissions) == 1
    assert not s.registry.links


def test_periodic_lost_reply_reuses_same_capture_after_restart(monkeypatch):
    s, clock = scenario(monkeypatch)
    s.activate()
    s.runtime.process_once()
    clock.advance(minutes=60)
    s.runtime.scheduler.tick()
    s.services.lose_next_de_reply = True
    assert s.runtime.process_once() == "pending"
    original = deepcopy(s.services.submissions[-1])
    clock.advance(seconds=3)
    restart(s, clock)
    assert s.runtime.process_once() == "done"
    assert s.services.submissions[-1] == original
    assert len(s.provider.calls) == 4
    assert len(s.repository.state["observations"]) == 2


def test_operator_request_dedup_coalesces_with_due_scan(monkeypatch):
    s, clock = scenario(monkeypatch)
    s.activate()
    s.runtime.process_once()
    goal_id, request_id = UUID(s.goals.goal["goal_id"]), uuid4()
    queued = s.runtime.scheduler.check_now(goal_id, request_id, "sid:operator")
    assert queued["status"] == "queued"
    assert s.runtime.scheduler.check_now(goal_id, request_id, "sid:operator")["status"] == "duplicate"
    assert s.runtime.scheduler.check_now(goal_id, uuid4(), "sid:operator")["status"] == "coalesced"
    assert s.runtime.scheduler.tick()["queued"] == 0
    assert len(s.scans.scans) == 1
    assert s.runtime.process_once() == "done"
    assert len(s.provider.calls) == 4


def test_per_user_quota_stops_before_network_and_reports_reason(monkeypatch):
    s, clock = scenario(monkeypatch)
    s.usage = MemoryUserUsage(maximum=2)
    restart(s, clock)
    s.activate()
    assert s.runtime.process_once() == "done"
    clock.advance(minutes=60)
    s.runtime.scheduler.tick()
    assert s.runtime.process_once() == "dead"
    assert s.runtime.last_result == "USER_DAILY_CALL_LIMIT"
    assert s.scans.status(UUID(s.goals.goal["goal_id"]))["last_result"] == "USER_DAILY_CALL_LIMIT"
    assert len(s.provider.calls) == 2


def test_hundred_calls_per_user_utc_reset_and_failed_call_accounting():
    clock, usage, provider = Clock(), MemoryUserUsage(), Mock()
    provider.search.return_value = {"results": []}
    guarded = GuardedProvider(provider, usage, clock=clock)
    one, two = uuid4(), uuid4()
    user = guarded.for_owner(one, before_request=lambda: None)
    for _ in range(100):
        user.search("recorded query")
    with pytest.raises(ProviderError, match="USER_DAILY_CALL_LIMIT"):
        user.search("recorded query")
    assert provider.search.call_count == 100
    guarded.for_owner(two, before_request=lambda: None).search("recorded query")
    clock.advance(days=1)
    provider.search.side_effect = ProviderError("NETWORK_ERROR")
    with pytest.raises(ProviderError, match="NETWORK_ERROR"):
        user.search("recorded query")
    assert usage.counts[(one, clock().date())] == 1  # No refund for an uncertain request.


def test_two_active_goals_per_owner_and_release_after_authoritative_pause():
    clock, store, registry = Clock(), MemoryScanStore(), MemoryRegistry()
    store.clock = clock
    from test_runtime_pipeline import GoalReads
    template = GoalReads().goal
    goals = {}
    for _ in range(3):
        goal = deepcopy(template)
        goal["goal_id"] = str(uuid4())
        goals[UUID(goal["goal_id"])] = goal
    identifiers = list(goals)
    assert store.admit(goals[identifiers[0]]) is None
    assert store.admit(goals[identifiers[1]]) is None
    assert store.admit(goals[identifiers[2]]) == "ACTIVE_GOAL_LIMIT"
    other_owner = deepcopy(template)
    other_owner.update(goal_id=str(uuid4()), owner_user_id=str(uuid4()))
    assert store.admit(other_owner) is None
    goals[UUID(other_owner["goal_id"])] = other_owner
    paused = identifiers[0]
    class Reader:
        def runtime(self, goal_id):
            return Runtime(goal_id, 1, "paused" if goal_id == paused else "active", UUID(goals[goal_id]["owner_user_id"]))
        def version(self, goal_id, version):
            return goals[goal_id]
    ScanScheduler(store, lambda _: Reader(), registry).tick()
    assert store.status(identifiers[2])["state"] == "active"
    assert sum(s["state"] == "active" and s["owner"] == template["owner_user_id"] for s in store.schedules.values()) == 2
