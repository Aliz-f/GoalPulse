from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.jobs import DiscoveryJobs
from goalpulse_discovery.source_checks import SourceCheck, SourceCheckJobs
from goalpulse_discovery.workflow import Runtime

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


class JobDatabase:
    def __init__(self, row):
        self.row, self.calls = row, []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def test_discovery_job_honors_provider_retry_after_and_bounded_deadline():
    db = JobDatabase((uuid4(), uuid4(), 1, uuid4(), 0, NOW + timedelta(hours=1)))
    worker = Mock()
    worker.run.side_effect = ProviderError("RATE_LIMITED", retry_after_seconds=120)
    assert DiscoveryJobs(db, clock=lambda: NOW).run_once(worker) == "pending"
    assert db.calls[-1][1][2] == NOW + timedelta(seconds=120)
    db.row = (*db.row[:-2], 4, NOW + timedelta(hours=1))
    assert DiscoveryJobs(db, clock=lambda: NOW).run_once(worker) == "dead"


def test_expired_discovery_job_performs_no_provider_work():
    db = JobDatabase((uuid4(), uuid4(), 1, uuid4(), 0, NOW - timedelta(seconds=1)))
    worker = Mock()
    assert DiscoveryJobs(db, clock=lambda: NOW).run_once(worker) == "dead"
    worker.run.assert_not_called()


def test_invalid_source_check_is_dead_without_poison_retry():
    db = JobDatabase((uuid4(), uuid4(), uuid4(), 0, NOW + timedelta(hours=1)))
    worker = Mock()
    worker.run.side_effect = ProviderError("UNSAFE_TARGET")
    assert SourceCheckJobs(db, clock=lambda: NOW).run_once(worker) == "dead"


def test_source_check_rechecks_pause_after_scrape_before_submission():
    goals, registry, provider, evidence = Mock(), Mock(), Mock(), Mock()
    goal_id, source_id = uuid4(), uuid4()
    registry.goals_for_source.return_value = [goal_id]
    goals.runtime.side_effect = [Runtime(goal_id, 1, "active"), Runtime(goal_id, 1, "paused")]
    registry.subscriptions.side_effect = [[(goal_id, 1)], []]
    assert SourceCheck(goals, registry, provider, evidence).run(source_id, uuid4()) == "inactive"
    provider.scrape.assert_called_once()
    evidence.submit.assert_not_called()


def test_source_check_runtime_failure_never_fetches_or_submits():
    goals, registry, provider, evidence = Mock(), Mock(), Mock(), Mock()
    registry.goals_for_source.return_value = [uuid4()]
    goals.runtime.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        SourceCheck(goals, registry, provider, evidence).run(uuid4(), uuid4())
    provider.scrape.assert_not_called()
    evidence.submit.assert_not_called()
