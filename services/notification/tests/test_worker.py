"""Rabbit adapter tests: no ACK before durable decision, no live broker."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from notification.worker import NotificationWorker
from notification.retry import Decision
from test_notifications import load


@pytest.fixture
def rig():
    connection, coordinator = Mock(), Mock()
    worker = NotificationWorker(connection, coordinator, queue='owned-notification-queue',
                                 dead_letter_exchange='fixture-dlx', dead_letter_routing_key='fixture-dlq')
    channel = connection.channel.return_value
    method = SimpleNamespace(delivery_tag=7, exchange='goalpulse.events',
                             routing_key='goal.opportunity_verified.v1')
    event = load('event.goal-opportunity-verified.json')
    channel.basic_get.return_value = method, None, json.dumps(event).encode()
    return worker, connection, channel, coordinator, method, event


def test_success_ack_only_after_coordinator_returns(rig):
    worker, _, channel, coordinator, _, event = rig
    def run(payload):
        assert payload == event
        channel.basic_ack.assert_not_called()
        return Decision('ack')
    coordinator.run.side_effect = run
    assert worker.run_once()
    channel.basic_get.assert_called_once_with(queue='owned-notification-queue', auto_ack=False)
    channel.basic_ack.assert_called_once_with(delivery_tag=7)
    channel.queue_declare.assert_called_once_with(queue='owned-notification-queue', durable=True,
        arguments={'x-dead-letter-exchange': 'fixture-dlx', 'x-dead-letter-routing-key': 'fixture-dlq'})
    channel.queue_bind.assert_called_once_with(queue='owned-notification-queue', exchange='goalpulse.events',
                                               routing_key='goal.opportunity_verified.v1')


def test_retry_waits_with_heartbeats_then_requeues(rig):
    worker, connection, channel, coordinator, _, _ = rig
    coordinator.run.return_value = Decision('retry', 2)
    connection.sleep.side_effect = lambda seconds: channel.basic_nack.assert_not_called()
    assert worker.run_once()
    connection.sleep.assert_called_once_with(2)
    channel.basic_nack.assert_called_once_with(delivery_tag=7, requeue=True)
    channel.basic_ack.assert_not_called()


def test_permanent_failure_dead_letters_without_requeue(rig):
    worker, _, channel, coordinator, _, _ = rig
    coordinator.run.return_value = Decision('dead_letter')
    worker.run_once()
    channel.basic_nack.assert_called_once_with(delivery_tag=7, requeue=False)
    channel.basic_ack.assert_not_called()


def test_storage_or_commit_failure_leaves_message_unacknowledged(rig):
    worker, connection, channel, coordinator, _, _ = rig
    coordinator.run.side_effect = RuntimeError('storage unavailable')
    with pytest.raises(RuntimeError):
        worker.run(lambda: False)
    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize('body', [b'invalid JSON', b'\xff', b'x' * 100])
def test_invalid_or_large_body_is_recorded_as_rejection(rig, body):
    worker, _, channel, coordinator, method, _ = rig
    worker.max_body_bytes = 50
    channel.basic_get.return_value = method, None, body
    coordinator.run.return_value = Decision('dead_letter')
    worker.run_once()
    coordinator.run.assert_called_once_with({})


def test_wrong_domain_routing_cannot_invoke_normal_processing(rig):
    worker, _, _, coordinator, method, _ = rig
    method.routing_key = 'goal.activated.v1'
    coordinator.run.return_value = Decision('dead_letter')
    worker.run_once()
    coordinator.run.assert_called_once_with({})


def test_idle_queue_has_no_ack(rig):
    worker, _, channel, coordinator, _, _ = rig
    channel.basic_get.return_value = None, None, None
    assert not worker.run_once()
    coordinator.run.assert_not_called()
    channel.basic_ack.assert_not_called()
