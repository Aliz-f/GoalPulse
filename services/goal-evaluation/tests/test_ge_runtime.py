"""Composition and readiness checks with explicit test transports/connections."""

from unittest.mock import Mock

import pytest

from test_persistence_outbox import ROOT, setup
from goal_evaluation.runtime import build_runtime


def options(s):
    connection, database, cursor = Mock(), Mock(), Mock()
    connection.is_open = True
    connection.channel.return_value.is_open = True
    database.autocommit = False
    database.cursor.return_value.__enter__ = Mock(return_value=cursor)
    database.cursor.return_value.__exit__ = Mock(return_value=False)
    return dict(connect_database=Mock(return_value=database), broker_connection=connection,
        queue='fixture-ge-queue', dead_letter_exchange='fixture-dlx', dead_letter_routing_key='fixture-dlq',
        schemas=ROOT / 'contracts/schemas', gm_origin='http://gm.internal', dm_origin='http://dm.internal',
        de_origin='http://de.internal', tokens=Mock(return_value='service-token'), profile='test',
        sources_for_goal=lambda subscription, correlation: [s.observation['source_id']],
        dependencies_ready=Mock(return_value=True), clock=s.clock, transport=Mock(), properties_factory=Mock())


def test_complete_graph_uses_sql_stores_and_requires_owned_schema_readiness(setup):
    opts = options(setup)
    runtime = build_runtime(**opts)
    assert runtime.worker.coordinator.consumer is runtime.consumer
    assert runtime.worker.publisher is runtime.publisher
    assert runtime.consumer.store is runtime.publisher.store
    assert runtime.ready()
    database = opts['connect_database'].return_value
    cursor = database.cursor.return_value.__enter__.return_value
    assert any('evaluation_processing' in call.args[0] for call in cursor.execute.call_args_list)
    opts['dependencies_ready'].return_value = False
    assert not runtime.ready()
    opts['dependencies_ready'].return_value = True
    opts['broker_connection'].channel.return_value.is_open = False
    assert not runtime.ready()


def test_missing_reverse_lookup_fails_before_broker_or_db_access(setup):
    opts = options(setup)
    opts['sources_for_goal'] = None
    with pytest.raises(ValueError, match='source lookup'):
        build_runtime(**opts)
    opts['broker_connection'].channel.assert_not_called()
    opts['connect_database'].assert_not_called()


def test_failed_schema_probe_cannot_claim_readiness(setup):
    opts = options(setup)
    runtime = build_runtime(**opts)
    opts['connect_database'].side_effect = RuntimeError('private DSN')
    assert not runtime.ready()
