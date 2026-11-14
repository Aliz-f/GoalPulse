from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from jsonschema import ValidationError

from goalpulse_discovery.context_provider import Output, ScrapeResult
from goalpulse_discovery.registry import Source
from goalpulse_discovery.workflow import ActivationInbox, DiscoveryWorker, Runtime

ROOT = Path(__file__).resolve().parents[3]


class MemoryRegistry:
    resolver = staticmethod(lambda _: ["93.184.216.34"])

    def __init__(self):
        self.sources, self.links = {}, set()

    def register(self, url):
        from urllib.parse import urlsplit
        if url not in self.sources:
            self.sources[url] = Source(uuid4(), url, url, urlsplit(url).hostname)
        return self.sources[url]

    def subscribe(self, goal_id, version, source_id):
        self.links.add((goal_id, version, source_id))

    def reconcile(self, goal_id, **kwargs):
        self.links = {link for link in self.links if link[0] != goal_id}


def setup_worker(*, active=True, product_success=True):
    goal = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text(encoding="utf-8"))
    identifier = UUID(goal["goal_id"])
    reader = Mock()
    reader.version.return_value = goal
    reader.runtime.return_value = Runtime(identifier, 1, "active" if active else "paused")
    provider = Mock()
    provider.search.return_value = {"results": [{"url": "https://example.com/item"}, {"url": "https://example.com/item?utm_source=x"}]}
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    provider.scrape.return_value = ScrapeResult("https://example.com/item", "fixture-request", now, 0, now,
                                              Output(product_success, {} if product_success else None), Output(True, "fixture"))
    registry, sink = MemoryRegistry(), Mock()
    worker = DiscoveryWorker(reader, provider, registry, sink, schema_directory=ROOT / "contracts/schemas")
    return worker, reader, provider, registry, sink, goal


def test_activation_initial_extraction_does_not_wait_for_monitor_change():
    worker, reader, provider, registry, sink, goal = setup_worker()
    correlation = uuid4()
    assert worker.run(UUID(goal["goal_id"]), 1, correlation) == "extracted"
    assert len(registry.sources) == len(registry.links) == 1
    sink.submit.assert_called_once()
    assert sink.submit.call_args.kwargs["correlation_id"] == correlation
    provider.create_monitor.assert_not_called()


def test_shared_source_has_two_independent_goal_links():
    worker, reader, provider, registry, sink, goal = setup_worker()
    worker.run(UUID(goal["goal_id"]), 1, uuid4())
    second = uuid4()
    reader.runtime.return_value = Runtime(second, 1, "active")
    reader.version.return_value = {**goal, "goal_id": str(second)}
    worker.run(second, 1, uuid4())
    assert len(registry.sources) == 1 and len(registry.links) == 2


@pytest.mark.parametrize("lifecycle,version", [("paused", 1), ("cancelled", 1), ("active", 2)])
def test_obsolete_or_inactive_goal_performs_no_provider_work(lifecycle, version):
    worker, reader, provider, registry, sink, goal = setup_worker()
    reader.runtime.return_value = Runtime(UUID(goal["goal_id"]), version, lifecycle)
    assert worker.run(UUID(goal["goal_id"]), 1, uuid4()) == "obsolete"
    provider.search.assert_not_called()
    sink.submit.assert_not_called()


def test_unsupported_destination_performs_no_discovery():
    worker, reader, provider, registry, sink, goal = setup_worker()
    goal["criteria"]["delivery_country"] = "DE"
    assert worker.run(UUID(goal["goal_id"]), 1, uuid4()) == "unsupported_destination"
    provider.search.assert_not_called()


def test_failed_output_reaches_failure_sink_without_fake_product():
    worker, reader, provider, registry, sink, goal = setup_worker(product_success=False)
    assert worker.run(UUID(goal["goal_id"]), 1, uuid4()) == "no_evidence"
    assert sink.submit.call_args.args[1].product.data is None


def test_invalid_and_wrong_type_events_are_rejected_before_persistence():
    store = Mock()
    inbox = ActivationInbox(store, schema_directory=ROOT / "contracts/schemas")
    event = json.loads((ROOT / "contracts/examples/event.goal-activated.json").read_text(encoding="utf-8"))
    event["payload"]["extra"] = "not allowed"
    with pytest.raises(ValidationError):
        inbox.accept(event)
    store.accept.assert_not_called()


def test_event_is_persisted_before_consumer_can_ack():
    event = json.loads((ROOT / "contracts/examples/event.goal-activated.json").read_text(encoding="utf-8"))
    store = Mock()
    store.accept.side_effect = [True, False, OSError("DB unavailable")]
    inbox = ActivationInbox(store, schema_directory=ROOT / "contracts/schemas")
    assert inbox.accept(event) is True
    assert inbox.accept(event) is False
    with pytest.raises(OSError):
        inbox.accept(event)
