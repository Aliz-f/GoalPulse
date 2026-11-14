"""Opt-in owned-DB tests, isolated in a fresh randomly named test schema.

GOALPULSE_NS_TEST_DSN must identify a disposable Notification test database.
No credentials or DSN contents are emitted. These tests require psycopg already
installed by the runtime/test environment; they never install dependencies.
"""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_notifications import NOW, setup
from notification.consumer import NotificationConsumer
from notification.reads import NotificationAccess, VerifiedPrincipal
from notification.store import PostgresStore


@pytest.fixture
def postgres():
    dsn = os.environ.get('GOALPULSE_NS_TEST_DSN')
    if not dsn:
        pytest.skip('PostgreSQL NOT RUN: GOALPULSE_NS_TEST_DSN is not configured')
    import psycopg
    from psycopg import sql

    schema = 'ns_test_' + uuid4().hex
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
            # Only the temporary schema this fixture just created is removed.
            admin.rollback()
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_connection_restart_preserves_notification_and_read_state(postgres, setup):
    s = setup
    s.consumer.store = PostgresStore(postgres)
    assert s.consumer.handle(s.event) == 'created'
    # A fresh store opens new physical connections; no in-memory notification
    # state is shared across reads or processing instances.
    access = NotificationAccess(PostgresStore(postgres),
        lambda: VerifiedPrincipal(s.goal['owner_user_id']), lambda: NOW)
    note = access.list().items[0]
    access.mark_read(note.notification_id)
    restarted = NotificationAccess(PostgresStore(postgres),
        lambda: VerifiedPrincipal(s.goal['owner_user_id']), lambda: NOW)
    assert restarted.list().items[0].is_read
    s.consumer.store = PostgresStore(postgres)
    assert s.consumer.handle(s.event) == 'duplicate'
    assert len(restarted.list().items) == 1


def test_concurrent_distinct_offers_share_one_hourly_slot(postgres, setup):
    s = setup
    barrier = Barrier(2)
    workers = []
    for delta in (0, 1):
        event, result, observation = copy.deepcopy((s.event, s.result, s.observation))
        event['event_id'] = str(uuid4())
        result['evaluation_id'] = event['payload']['evaluation_id'] = str(uuid4())
        observation['observation_id'] = str(uuid4())
        result['candidate_observation_ids'] = [observation['observation_id']]
        for condition in result['conditions']:
            condition['observation_ids'] = [observation['observation_id']]
        observation['unit_price_eur_cents'] += delta

        def goals_reader():
            calls = 0

            def runtime(*args):
                nonlocal calls
                calls += 1
                if calls == 1:
                    barrier.wait(timeout=10)
                return s.runtime

            return SimpleNamespace(runtime=runtime, version=lambda *args: s.goal)

        consumer = NotificationConsumer(s.contracts, PostgresStore(postgres), goals_reader(),
            SimpleNamespace(get=lambda *args, row=result: row),
            SimpleNamespace(get=lambda *args, row=observation: row,
                            latest=lambda *args, row=observation: [row]), lambda: NOW)
        workers.append((consumer, event))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consumer.handle, event) for consumer, event in workers]
        outcomes = [future.result(timeout=20) for future in futures]
    assert sorted(outcomes) == ['created', 'rate_limited']
    with postgres() as connection:
        assert connection.execute('SELECT count(*) FROM notifications').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM inbox').fetchone()[0] == 2
