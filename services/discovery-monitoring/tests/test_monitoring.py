from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from goalpulse_discovery.context_provider import MonitorCreated
from goalpulse_discovery.monitoring import Monitor, MonitorManager, SubscriptionReconciler
from goalpulse_discovery.registry import Source
from goalpulse_discovery.workflow import Runtime


def test_shared_monitor_reserved_once_and_only_secret_reference_persisted():
    identifier, source_id = uuid4(), uuid4()
    source = Source(source_id, "https://example.com/item", "https://example.com/item", "example.com")
    store, provider, secrets = Mock(), Mock(), Mock()
    store.reserve.side_effect = [(identifier, True), (identifier, False)]
    provider.create_monitor.return_value = MonitorCreated("mon_fixture", "run_baseline", "fixture-secret")
    secrets.put.return_value = "secret-manager/fixture-reference"
    manager = MonitorManager(store, provider, secrets, source_active=lambda _: True)
    assert manager.ensure(source, webhook_url="https://example.com/hook") == identifier
    assert manager.ensure(source, webhook_url="https://example.com/hook") == identifier
    provider.create_monitor.assert_called_once()
    assert "fixture-secret" not in repr(store.created.call_args)
    secrets.put.assert_called_once_with("fixture-secret")


def test_ambiguous_creation_is_marked_uncertain_without_second_create():
    source = Source(uuid4(), "https://example.com/item", "https://example.com/item", "example.com")
    store, provider = Mock(), Mock()
    store.reserve.return_value = (uuid4(), True)
    provider.create_monitor.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        MonitorManager(store, provider, Mock(), source_active=lambda _: True).ensure(source)
    assert store.state.call_args.args[1:] == ("uncertain", "CREATION_REQUIRES_RECONCILIATION")
    provider.create_monitor.assert_called_once()


def test_baseline_is_recorded_without_evidence_or_change_callback():
    monitor = Monitor(uuid4(), uuid4(), "mon_fixture", "run_baseline", "secret/ref", "active")
    store, provider = Mock(), Mock()
    provider.fetch_run.return_value = {"id": "run_baseline", "monitor_id": "mon_fixture",
                                      "status": "completed", "baseline_created": True}
    assert MonitorManager(store, provider, Mock(), source_active=lambda _: True).record_baseline(monitor)
    store.baseline.assert_called_once()
    assert len(provider.method_calls) == 1


@pytest.mark.parametrize("remaining,expected", [([(uuid4(), 1)], "active"), ([], "paused")])
def test_pause_one_goal_keeps_shared_monitor_until_all_links_inactive(remaining, expected):
    monitor = Monitor(uuid4(), uuid4(), "mon_fixture", None, None, "active")
    registry, store, provider, goals = Mock(), Mock(), Mock(), Mock()
    first, second = uuid4(), uuid4()
    registry.goals_for_source.return_value = [first, second]
    goals.runtime.side_effect = [Runtime(first, 1, "paused"), Runtime(second, 1, "active" if remaining else "cancelled")]
    registry.subscriptions.return_value = remaining
    assert SubscriptionReconciler(goals, registry, store, provider).reconcile_monitor(monitor) == expected
    assert registry.reconcile.call_count == 2
    if remaining:
        provider.set_monitor_status.assert_not_called()
    else:
        provider.set_monitor_status.assert_called_once_with("mon_fixture", "paused")


def test_failed_runtime_read_does_not_pause_shared_monitor():
    monitor = Monitor(uuid4(), uuid4(), "mon_fixture", None, None, "active")
    registry, store, provider, goals = Mock(), Mock(), Mock(), Mock()
    registry.goals_for_source.return_value = [uuid4()]
    goals.runtime.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        SubscriptionReconciler(goals, registry, store, provider).reconcile_monitor(monitor)
    provider.set_monitor_status.assert_not_called()


def test_monitor_creation_requires_current_active_subscriber():
    source = Source(uuid4(), "https://example.com/item", "https://example.com/item", "example.com")
    store, provider = Mock(), Mock()
    manager = MonitorManager(store, provider, Mock(), source_active=lambda _: False)
    with pytest.raises(ValueError, match="active subscriber"):
        manager.ensure(source)
    store.reserve.assert_not_called()
    provider.create_monitor.assert_not_called()
