from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from goalpulse_evidence.publishing import OutboxPublisher, RabbitChannelBroker

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def event():
    return json.loads((ROOT / "contracts/examples/event.evidence-updated.json").read_text(encoding="utf-8"))


class OutboxConnection:
    autocommit = False

    def __init__(self, payload=None, attempts=0):
        self.row = (payload["event_id"], payload, attempts) if payload else None
        self.calls = []
        self.commits = self.rollbacks = self.closes = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


@pytest.mark.parametrize("failure", [False, OSError("synthetic secret must never be stored")])
def test_unconfirmed_publish_retries_same_event_then_recovers(failure):
    payload = event()
    connections = [OutboxConnection(payload), OutboxConnection(payload, 1)]
    broker = Mock()
    broker.publish.side_effect = [failure, True]
    publisher = OutboxPublisher(lambda: connections.pop(0), broker,
                                schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    assert publisher.run_once() == "pending"
    assert publisher.run_once() == "published"
    messages = broker.publish.call_args_list
    assert messages[0].kwargs["message_id"] == messages[1].kwargs["message_id"] == payload["event_id"]
    assert messages[0].kwargs["correlation_id"] == payload["correlation_id"]


def test_permanent_failure_is_dead_after_bounded_attempts():
    connection = OutboxConnection(event(), attempts=4)
    publisher = OutboxPublisher(lambda: connection, Mock(publish=Mock(return_value=False)),
                                schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    assert publisher.run_once() == "dead"
    assert connection.calls[-1][1][0:2] == ("dead", 5)
    assert connection.commits == 1 and connection.closes == 1


def test_invalid_event_is_dead_without_broker_call():
    payload = event()
    payload["payload"]["condition"] = "new"
    broker = Mock()
    connection = OutboxConnection(payload)
    assert OutboxPublisher(lambda: connection, broker, schema_directory=ROOT / "contracts/schemas").run_once() == "dead"
    broker.publish.assert_not_called()


def test_idle_worker_sends_nothing():
    broker = Mock()
    connection = OutboxConnection()
    assert OutboxPublisher(lambda: connection, broker, schema_directory=ROOT / "contracts/schemas").run_once() == "idle"
    broker.publish.assert_not_called()
    assert connection.closes == 1


def test_rabbit_channel_uses_durable_exchange_persistence_confirms_and_mandatory():
    channel = Mock()
    channel.basic_publish.return_value = None
    broker = RabbitChannelBroker(channel, lambda **kwargs: SimpleNamespace(**kwargs))
    assert broker.publish(exchange="goalpulse.events", routing_key="evidence.updated.v1", body=b"{}",
                          message_id="fixture-id", correlation_id="fixture-correlation")
    channel.exchange_declare.assert_called_once_with(exchange="goalpulse.events", exchange_type="topic", durable=True)
    channel.confirm_delivery.assert_called_once()
    assert channel.basic_publish.call_args.kwargs["mandatory"] is True
    assert channel.basic_publish.call_args.kwargs["properties"].delivery_mode == 2
    channel.basic_publish.side_effect = OSError("broker unavailable")
    with pytest.raises(OSError):
        broker.publish(exchange="goalpulse.events", routing_key="evidence.updated.v1", body=b"{}",
                       message_id="fixture-id", correlation_id="fixture-correlation")
