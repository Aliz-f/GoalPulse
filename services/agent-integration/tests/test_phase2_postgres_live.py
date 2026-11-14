"""Opt-in owned SQL/Rabbit tests with recorded peers; no live provider calls.

GOALPULSE_AI_TEST_DSN must identify a disposable Agent Integration database.
GOALPULSE_AI_TEST_AMQP_URL enables the real-broker case as well. Each test owns
its temporary schema/queues. No connection string is logged or committed.
"""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from recorded_support import ROOT, recorded_runtime
from agent_integration.runtime import build_runtime
from agent_integration.service import PackageNotFound, Principal


@pytest.fixture
def postgres():
    dsn = os.environ.get('GOALPULSE_AI_TEST_DSN')
    if not dsn:
        pytest.skip('PostgreSQL NOT RUN: GOALPULSE_AI_TEST_DSN is not configured')
    import psycopg
    from psycopg import sql
    schema = 'ai_test_' + uuid4().hex
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


def sql_runtime(s, connect, broker, queue='fixture-ai-queue', dlq='fixture-ai-dlq'):
    return build_runtime(connect_database=connect, broker_connection=broker, queue=queue,
        dead_letter_exchange='', dead_letter_routing_key=dlq, schemas=ROOT / 'contracts/schemas',
        gm_origin='http://gm.internal', ge_origin='http://ge.internal', de_origin='http://de.internal',
        tokens=lambda scope: 'recorded-test-token', evaluation_read_scope='fixture-ge-read',
        profile='test', dependencies_ready=lambda: True, clock=s.clock, transport=s.transport)


def test_fresh_sql_connections_preserve_package_and_owned_reads(postgres):
    s = recorded_runtime()
    runtime = sql_runtime(s, postgres, s.broker)
    assert runtime.ready()
    runtime.worker.run_once()
    runtime.worker.run_once()
    with postgres() as connection:
        package = connection.execute('SELECT payload FROM action_packages').fetchone()[0]
        assert connection.execute('SELECT count(*) FROM action_packages').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
    restarted = sql_runtime(s, postgres, s.broker)
    principal, correlation = Principal(s.data['goal']['owner_user_id']), str(uuid4())
    assert restarted.reader.get(package['package_id'], principal, correlation) == package
    with pytest.raises(PackageNotFound):
        restarted.reader.get(package['package_id'], Principal(str(uuid4())), correlation)
    restarted.worker.run_once()
    with postgres() as connection:
        assert connection.execute('SELECT count(*) FROM action_packages').fetchone()[0] == 1


def test_real_broker_replay_manual_ack_and_rejection_dlq(postgres):
    amqp_url = os.environ.get('GOALPULSE_AI_TEST_AMQP_URL')
    if not amqp_url:
        pytest.skip('RabbitMQ NOT RUN: GOALPULSE_AI_TEST_AMQP_URL is not configured')
    import pika
    parameters = pika.URLParameters(amqp_url)
    parameters.socket_timeout, parameters.stack_timeout, parameters.blocked_connection_timeout = 5, 10, 5
    broker = pika.BlockingConnection(parameters)
    queue, dlq = ('ai_test_' + uuid4().hex for _ in range(2))
    channel = broker.channel()
    try:
        channel.queue_declare(queue=dlq, durable=True)
        s = recorded_runtime()
        runtime = sql_runtime(s, postgres, broker, queue, dlq)
        channel.confirm_delivery()
        for _ in range(2):
            channel.basic_publish(exchange='goalpulse.events', routing_key='goal.opportunity_verified.v1',
                body=json.dumps(s.data['event']).encode(), properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
            assert runtime.worker.run_once()
        with postgres() as connection:
            assert connection.execute('SELECT count(*) FROM action_packages').fetchone()[0] == 1
            assert connection.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
        channel.basic_publish(exchange='goalpulse.events', routing_key='goal.opportunity_verified.v1',
            body=b'invalid JSON', properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
        runtime.worker.run_once()
        deadline = time.monotonic() + 5
        while True:
            method, properties, body = channel.basic_get(queue=dlq, auto_ack=False)
            if method is not None:
                assert body == b'invalid JSON'
                channel.basic_ack(delivery_tag=method.delivery_tag)
                break
            if time.monotonic() >= deadline:
                pytest.fail('Expected durable rejected delivery in test DLQ')
            broker.process_data_events(time_limit=0.1)
    finally:
        if broker.is_open:
            for name in (queue, dlq):
                channel.queue_delete(queue=name)
            broker.close()
