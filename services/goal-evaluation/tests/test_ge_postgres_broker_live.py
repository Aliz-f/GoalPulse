"""Opt-in tests against a disposable GE database and optional test RabbitMQ.

Configure GOALPULSE_GE_TEST_DSN; for real broker smoke additionally set
GOALPULSE_GE_TEST_AMQP_URL. Provider HTTP responses remain explicitly recorded.
Each test owns a random schema and uniquely named test queues, removed afterward.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_persistence_outbox import ROOT, setup, load
from goal_evaluation.runtime import build_runtime


@pytest.fixture
def postgres():
    dsn = os.environ.get('GOALPULSE_GE_TEST_DSN')
    if not dsn:
        pytest.skip('PostgreSQL NOT RUN: GOALPULSE_GE_TEST_DSN is not configured')
    import psycopg
    from psycopg import sql

    schema = 'ge_test_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            for path in sorted((Path(__file__).resolve().parents[1] / 'migrations').glob('*.sql')):
                admin.execute(path.read_text())

            def connect():
                return psycopg.connect(dsn, options=f'-c search_path={schema}')

            yield connect
        finally:
            admin.rollback()
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


class RecordedTransport:
    def __init__(self, s):
        self.s = s

    def get(self, url, headers):
        s = self.s
        assert headers['X-Correlation-ID'] == load('event.evidence-updated.json')['correlation_id']
        if url.endswith('/runtime'):
            row = vars(s.goals.runtime.return_value)
        elif '/versions/' in url:
            row = s.goal
        elif url.endswith('/subscriptions'):
            row = {'source_id': s.observation['source_id'],
                   'subscriptions': [{'goal_id': s.goal['goal_id'], 'goal_version': 1}]}
        elif '/observations?' in url:
            row = {'observations': [s.observation]}
        elif '/observations/' in url:
            row = s.observation
        else:
            raise AssertionError('Unexpected internal operation')
        return 200, json.dumps(row).encode()


def build(s, connect, broker, queue, dlq, **kwargs):
    return build_runtime(connect_database=connect, broker_connection=broker,
        queue=queue, dead_letter_exchange='', dead_letter_routing_key=dlq,
        schemas=ROOT / 'contracts/schemas', gm_origin='http://gm.internal',
        dm_origin='http://dm.internal', de_origin='http://de.internal',
        tokens=lambda scope: 'explicit-recorded-token', profile='test',
        sources_for_goal=lambda sub, correlation: [s.observation['source_id']],
        dependencies_ready=lambda: True, clock=s.clock, transport=RecordedTransport(s), **kwargs)


def test_recorded_worker_sql_persistence_and_duplicate_restart(postgres, setup):
    s = setup
    broker = Mock()
    broker.is_open = broker.channel.return_value.is_open = True
    channel = broker.channel.return_value
    channel.basic_publish.return_value = None
    method = SimpleNamespace(exchange='goalpulse.events', routing_key='evidence.updated.v1', delivery_tag=1)
    channel.basic_get.return_value = method, None, json.dumps(load('event.evidence-updated.json')).encode()
    runtime = build(s, postgres, broker, 'fixture-queue', 'fixture-dlq', properties_factory=Mock())
    assert runtime.ready()
    runtime.worker.run_once()
    runtime.worker.run_once()
    with postgres() as connection:
        row = connection.execute('SELECT payload FROM evaluations').fetchone()[0]
        assert connection.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox WHERE status='published'").fetchone()[0] == 2
    restarted = build(s, postgres, broker, 'fixture-queue', 'fixture-dlq', properties_factory=Mock())
    restarted.worker.run_once()
    assert restarted.reader.get(row['evaluation_id']) == row
    with postgres() as connection:
        assert connection.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2


def test_real_rabbit_manual_ack_replay_and_confirmed_sql_outbox(postgres, setup):
    amqp_url = os.environ.get('GOALPULSE_GE_TEST_AMQP_URL')
    if not amqp_url:
        pytest.skip('RabbitMQ NOT RUN: GOALPULSE_GE_TEST_AMQP_URL is not configured')
    import pika

    params = pika.URLParameters(amqp_url)
    params.socket_timeout, params.stack_timeout, params.blocked_connection_timeout = 5, 10, 5
    broker = pika.BlockingConnection(params)
    queue, dlq, output = ('ge_test_' + uuid4().hex for _ in range(3))
    channel = broker.channel()
    try:
        channel.queue_declare(queue=dlq, durable=True)
        channel.queue_declare(queue=output, durable=True)
        runtime = build(setup, postgres, broker, queue, dlq)
        for key in ('goal.evaluated.v1', 'goal.opportunity_verified.v1'):
            channel.queue_bind(queue=output, exchange='goalpulse.events', routing_key=key)
        channel.confirm_delivery()
        event = load('event.evidence-updated.json')
        for _ in range(2):
            channel.basic_publish(exchange='goalpulse.events', routing_key='evidence.updated.v1',
                body=json.dumps(event).encode(), properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
            assert runtime.worker.run_once()
        # Any remaining intent must drain even with no new evidence delivery.
        runtime.worker.run_once()
        with postgres() as connection:
            assert connection.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM outbox WHERE status='published'").fetchone()[0] == 2
        assert channel.queue_declare(queue=queue, passive=True).method.message_count == 0
        assert channel.queue_declare(queue=output, passive=True).method.message_count == 2
        assert channel.queue_declare(queue=dlq, passive=True).method.message_count == 0
    finally:
        if broker.is_open:
            for name in (queue, dlq, output):
                channel.queue_delete(queue=name)
            broker.close()
