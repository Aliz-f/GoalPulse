import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.consumer import EVALUATED_QUEUE, MAX_PROCESSING_RETRIES, process_delivery


ROOT = Path(__file__).parents[3]
EVENT = (ROOT / "contracts/examples/event.goal-evaluated.json").read_bytes()


class Channel:
    def __init__(self):
        self.acks = []
        self.nacks = []
        self.published = []

    def basic_ack(self, **kwargs):
        self.acks.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacks.append(kwargs)

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return True


class Store:
    def __init__(self, outcome="projected", failure=None):
        self.outcome = outcome
        self.failure = failure

    def process_evaluated(self, **_kwargs):
        if self.failure:
            raise self.failure
        return self.outcome


def test_successful_and_duplicate_deliveries_are_manually_acked():
    for outcome in ("projected", "duplicate", "stale", "inactive"):
        channel = Channel()
        process_delivery(
            channel,
            SimpleNamespace(delivery_tag=7),
            SimpleNamespace(headers={}),
            EVENT,
            Store(outcome=outcome),
        )
        assert channel.acks == [{"delivery_tag": 7}]
        assert not channel.nacks


def test_invalid_event_is_dead_lettered_without_retry():
    event = json.loads(EVENT)
    event["schema_version"] = 2
    channel = Channel()
    process_delivery(
        channel,
        SimpleNamespace(delivery_tag=8),
        SimpleNamespace(headers={}),
        json.dumps(event).encode(),
        Store(),
    )
    assert channel.nacks == [{"delivery_tag": 8, "requeue": False}]
    assert not channel.published


def test_transient_failure_republishes_persistent_retry_then_acks_original():
    channel = Channel()
    waits = []
    process_delivery(
        channel,
        SimpleNamespace(delivery_tag=9),
        SimpleNamespace(headers={"x-goalpulse-processing-attempt": 1}),
        EVENT,
        Store(failure=RuntimeError("database unavailable")),
        wait=waits.append,
    )
    assert waits == [2]
    assert channel.published[0]["routing_key"] == EVALUATED_QUEUE
    assert channel.published[0]["properties"].delivery_mode == 2
    assert channel.published[0]["properties"].headers["x-goalpulse-processing-attempt"] == 2
    assert channel.acks == [{"delivery_tag": 9}]


def test_exhausted_transient_failure_is_dead_lettered():
    channel = Channel()
    process_delivery(
        channel,
        SimpleNamespace(delivery_tag=10),
        SimpleNamespace(headers={"x-goalpulse-processing-attempt": MAX_PROCESSING_RETRIES}),
        EVENT,
        Store(failure=RuntimeError("database unavailable")),
        wait=lambda _seconds: None,
    )
    assert channel.nacks == [{"delivery_tag": 10, "requeue": False}]
    assert not channel.acks
