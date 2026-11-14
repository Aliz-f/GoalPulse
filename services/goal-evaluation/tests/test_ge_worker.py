"""Transport adapter tests; actual RabbitMQ/PostgreSQL smoke is opt-in."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_persistence_outbox import load
from goal_evaluation.retry import Decision
from goal_evaluation.worker import ConfirmedPublisher, EvaluationWorker


@pytest.fixture
def rig():
    connection, coordinator, publisher, ready = Mock(), Mock(), Mock(), Mock(return_value=True)
    worker = EvaluationWorker(connection, coordinator, publisher, queue='fixture-ge-queue',
        dead_letter_exchange='fixture-dlx', dead_letter_routing_key='fixture-dlq', ready=ready)
    channel = connection.channel.return_value
    method = SimpleNamespace(exchange='goalpulse.events', routing_key='evidence.updated.v1', delivery_tag=5)
    event = load('event.evidence-updated.json')
    channel.basic_get.return_value = method, None, json.dumps(event).encode()
    publisher.run_once.return_value = False
    return worker, connection, coordinator, publisher, ready, channel, method, event


def test_ack_follows_durable_processing_and_outbox_runs(rig):
    worker, _, coordinator, publisher, _, channel, _, event = rig
    def process(payload):
        assert payload == event
        channel.basic_ack.assert_not_called()
        return Decision('ack')
    coordinator.run.side_effect = process
    assert worker.run_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=5)
    channel.basic_get.assert_called_once_with(queue='fixture-ge-queue', auto_ack=False)
    publisher.run_once.assert_called_once()
    channel.queue_declare.assert_called_once_with(queue='fixture-ge-queue', durable=True, arguments={
        'x-dead-letter-exchange': 'fixture-dlx', 'x-dead-letter-routing-key': 'fixture-dlq'})


@pytest.mark.parametrize('action,requeue', [('retry', True), ('dead_letter', False)])
def test_retry_and_dead_letter_decisions_are_applied(rig, action, requeue):
    worker, connection, coordinator, _, _, channel, _, _ = rig
    coordinator.run.return_value = Decision(action, 2 if action == 'retry' else 0)
    worker.run_once()
    channel.basic_nack.assert_called_once_with(delivery_tag=5, requeue=requeue)
    channel.basic_ack.assert_not_called()
    if requeue:
        connection.sleep.assert_called_once_with(2)


def test_storage_failure_closes_connection_without_ack_or_nack(rig):
    worker, connection, coordinator, _, _, channel, _, _ = rig
    coordinator.run.side_effect = RuntimeError('DB unavailable')
    with pytest.raises(RuntimeError):
        worker.run(lambda: False)
    connection.close.assert_called_once()
    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_not_called()


def test_empty_queue_still_drains_saved_outbox_after_restart(rig):
    worker, _, coordinator, publisher, _, channel, _, _ = rig
    channel.basic_get.return_value = None, None, None
    publisher.run_once.return_value = True
    assert worker.run_once()
    coordinator.run.assert_not_called()
    publisher.run_once.assert_called_once()


def test_missing_dependency_binding_prevents_consumption(rig):
    worker, _, coordinator, _, ready, channel, _, _ = rig
    ready.return_value = False
    with pytest.raises(RuntimeError, match='not ready'):
        worker.run_once()
    channel.basic_get.assert_not_called()


def test_invalid_message_goes_to_durable_rejection_path(rig):
    worker, _, coordinator, _, _, channel, method, _ = rig
    channel.basic_get.return_value = method, None, b'invalid json'
    coordinator.run.return_value = Decision('dead_letter')
    worker.run_once()
    coordinator.run.assert_called_once_with({})


def test_confirmed_adapter_uses_persistent_mandatory_publish():
    channel, factory = Mock(), Mock()
    publisher = ConfirmedPublisher(channel, factory)
    channel.basic_publish.return_value = None  # Pika confirmed-success convention
    assert publisher.publish_confirmed('goalpulse.events', 'goal.evaluated.v1', b'{}', True)
    channel.confirm_delivery.assert_called_once()
    factory.assert_called_once_with(content_type='application/json', delivery_mode=2)
    assert channel.basic_publish.call_args.kwargs['mandatory'] is True
    channel.basic_publish.return_value = False
    assert not publisher.publish_confirmed('goalpulse.events', 'goal.evaluated.v1', b'{}', True)
    channel.basic_publish.side_effect = RuntimeError('broker nack or Basic.Return')
    with pytest.raises(RuntimeError):
        publisher.publish_confirmed('goalpulse.events', 'goal.opportunity_verified.v1', b'{}', True)


def test_confirmed_adapter_cannot_publish_undeclared_or_nonpersistent_message():
    channel = Mock()
    publisher = ConfirmedPublisher(channel, Mock())
    with pytest.raises(ValueError):
        publisher.publish_confirmed('goalpulse.events', 'GoalSatisfied', b'{}', True)
    with pytest.raises(ValueError):
        publisher.publish_confirmed('goalpulse.events', 'goal.evaluated.v1', b'{}', False)
    channel.basic_publish.assert_not_called()
