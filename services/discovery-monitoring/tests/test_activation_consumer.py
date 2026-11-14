"""Rabbit activation acknowledgements follow durable inbox/retry publication."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from goalpulse_discovery.consumer import ACTIVATED_QUEUE, ActivationConsumer
from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.workflow import ActivationInbox

ROOT = Path(__file__).resolve().parents[3]
RETRY_HEADER = "x-goalpulse-processing-attempt"


def activation():
    return json.loads((ROOT / "contracts/examples/event.goal-activated.json").read_text(encoding="utf-8"))


def setup(*, headers=None, maximum=3):
    store, channel = Mock(), Mock()
    store.accept.return_value = True
    channel.basic_publish.return_value = True
    inbox = ActivationInbox(store, schema_directory=ROOT / "contracts/schemas")
    waits = []
    consumer = ActivationConsumer(inbox, properties_factory=lambda **kwargs: SimpleNamespace(**kwargs),
                                  wait=waits.append, max_retries=maximum)
    method, properties = SimpleNamespace(delivery_tag=17), SimpleNamespace(headers=headers or {})
    return consumer, store, channel, method, properties, waits


@pytest.mark.parametrize("accepted", [True, False])
def test_durable_acceptance_and_safe_duplicate_ack_only_after_commit(accepted):
    consumer, store, channel, method, properties, _waits = setup()
    order = []
    store.accept.side_effect = lambda _event: order.append("durable") or accepted
    channel.basic_ack.side_effect = lambda **_kwargs: order.append("ack")
    consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    assert order == ["durable", "ack"]
    channel.basic_ack.assert_called_once_with(delivery_tag=17)
    channel.basic_nack.assert_not_called()
    channel.basic_publish.assert_not_called()


@pytest.mark.parametrize("body", [b"{invalid", b"[]", b"null", b'{"value":NaN}', b'{"value":Infinity}',
                                  b" " * 65537, b"\xff"])
def test_malformed_oversized_and_nonfinite_json_deadletters_without_persistence(body):
    consumer, store, channel, method, properties, _waits = setup()
    consumer.on_message(channel, method, properties, body)
    store.accept.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_ack.assert_not_called()
    channel.basic_publish.assert_not_called()


@pytest.mark.parametrize("field,value", [("goal_version", 1.0), ("goal_version", True),
                                         ("occurred_at", "2026-11-14T10:00:00"),
                                         ("producer", "data-evidence")])
def test_invalid_frozen_event_contract_is_deadlettered_without_requeue(field, value):
    consumer, store, channel, method, properties, _waits = setup()
    event = activation()
    (event["payload"] if field == "goal_version" else event)[field] = value
    consumer.on_message(channel, method, properties, json.dumps(event).encode())
    store.accept.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_publish.assert_not_called()


def test_reused_event_identity_conflict_is_permanent():
    consumer, store, channel, method, properties, _waits = setup()
    store.accept.side_effect = ValueError("Conflicting event identity")
    consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_publish.assert_not_called()
    channel.basic_ack.assert_not_called()


@pytest.mark.parametrize("confirmed", [True, None])
def test_transient_persistence_failure_is_confirmed_republished_before_ack(confirmed, capsys):
    consumer, store, channel, method, properties, waits = setup(headers={"trace": "preserved"})
    store.accept.side_effect = OSError("sensitive-token-must-not-log")
    order = []
    channel.basic_publish.side_effect = lambda **_kwargs: order.append("confirmed") or confirmed
    channel.basic_ack.side_effect = lambda **_kwargs: order.append("ack")
    body = json.dumps(activation()).encode()
    consumer.on_message(channel, method, properties, body)
    kwargs = channel.basic_publish.call_args.kwargs
    assert kwargs["exchange"] == "" and kwargs["routing_key"] == ACTIVATED_QUEUE
    assert kwargs["mandatory"] is True and kwargs["body"] == body
    assert kwargs["properties"].delivery_mode == 2
    assert kwargs["properties"].headers[RETRY_HEADER] == 1
    assert kwargs["properties"].headers["trace"] == "preserved"
    assert order == ["confirmed", "ack"]
    assert len(waits) == 1 and 0 < waits[0] <= 30
    assert "sensitive-token-must-not-log" not in capsys.readouterr().out


@pytest.mark.parametrize("behavior", [False, OSError("sensitive-broker-secret")])
def test_unconfirmed_retry_keeps_original_unacked_and_raises_redacted_error(behavior):
    consumer, store, channel, method, properties, _waits = setup()
    store.accept.side_effect = OSError("sensitive-db-secret")
    if isinstance(behavior, Exception):
        channel.basic_publish.side_effect = behavior
    else:
        channel.basic_publish.return_value = behavior
    with pytest.raises(ProviderError, match="BROKER_UNCONFIRMED") as caught:
        consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    assert "sensitive" not in str(caught.value)
    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_not_called()


def test_retry_budget_is_exhausted_once_then_deadlettered():
    consumer, store, channel, method, properties, waits = setup(headers={RETRY_HEADER: 3})
    store.accept.side_effect = OSError("unavailable")
    consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_publish.assert_not_called()
    channel.basic_ack.assert_not_called()
    assert not waits


@pytest.mark.parametrize("attempt", [-1, True, "1", None])
def test_invalid_retry_header_cannot_reset_or_bypass_retry_budget(attempt):
    consumer, store, channel, method, properties, waits = setup(headers={RETRY_HEADER: attempt})
    store.accept.side_effect = OSError("unavailable")
    consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_ack.assert_not_called()
    channel.basic_publish.assert_not_called()
    assert not waits


def test_second_retry_increments_existing_header_and_waits_within_bound():
    consumer, store, channel, method, properties, waits = setup(headers={RETRY_HEADER: 1})
    store.accept.side_effect = OSError("unavailable")
    consumer.on_message(channel, method, properties, json.dumps(activation()).encode())
    assert channel.basic_publish.call_args.kwargs["properties"].headers[RETRY_HEADER] == 2
    assert len(waits) == 1 and 0 < waits[0] <= 8
    channel.basic_ack.assert_called_once_with(delivery_tag=17)


def test_consumer_configuration_keeps_existing_topology_and_manual_ack():
    consumer, _store, channel, _method, _properties, _waits = setup()
    consumer.configure(channel)
    channel.confirm_delivery.assert_called_once()
    channel.basic_qos.assert_called_once_with(prefetch_count=1)
    assert channel.exchange_declare.call_args.kwargs["exchange"] == "goalpulse.events"
    assert channel.exchange_declare.call_args.kwargs["passive"] is True
    assert channel.queue_declare.call_args.kwargs["queue"] == ACTIVATED_QUEUE
    assert channel.queue_declare.call_args.kwargs["passive"] is True
    channel.basic_consume.assert_called_once_with(queue=ACTIVATED_QUEUE,
                                                  on_message_callback=consumer.on_message, auto_ack=False)
