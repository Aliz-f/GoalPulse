"""Recorded upstream HTTP contracts and fail-closed runtime composition."""

import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_notifications import ROOT, setup
from notification.consumer import RetryableReadError
from notification.readers import (
    EvidenceHttpReader, EvaluationHttpReader, GoalHttpReader, HttpTransport,
    InternalClient, InternalReadError,
)
from notification.runtime import build_worker


@pytest.fixture
def readers(setup):
    s = setup
    transport, tokens = Mock(), Mock(return_value='service-jwt')
    client = InternalClient('http://upstream.internal:8000', tokens, transport)
    return s, transport, tokens, client


def respond(transport, row, status=200):
    transport.get.return_value = status, json.dumps(row).encode()


def test_reads_use_exact_routes_scopes_and_correlation(readers):
    s, transport, tokens, client = readers
    correlation = s.event['correlation_id']
    goals = GoalHttpReader(client, s.contracts)
    respond(transport, vars(s.runtime))
    assert goals.runtime(s.goal['goal_id'], correlation) == s.runtime
    tokens.assert_called_with('gm.runtime.read')
    respond(transport, s.goal)
    assert goals.version(s.goal['goal_id'], 1, correlation) == s.goal
    tokens.assert_called_with('gm.goal-version.read')
    respond(transport, s.result)
    evaluations = EvaluationHttpReader(client, s.contracts, scope='fixture-evaluation-scope')
    assert evaluations.get(s.result['evaluation_id'], correlation) == s.result
    tokens.assert_called_with('fixture-evaluation-scope')
    respond(transport, s.observation)
    evidence = EvidenceHttpReader(client, s.contracts, profile='test')
    assert evidence.get(s.observation['observation_id'], correlation) == s.observation
    tokens.assert_called_with('de.observations.read')
    respond(transport, {'observations': [s.observation]})
    assert evidence.latest(s.observation['source_id'], correlation) == [s.observation]
    assert 'limit=1' in transport.get.call_args.args[0]
    for call in transport.get.call_args_list:
        headers = call.args[1]
        assert headers['X-Correlation-ID'] == correlation
        assert headers['Authorization'] == 'Bearer service-jwt'


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_transient_upstream_status_is_retried_by_existing_consumer_only(readers, status):
    s, transport, _, client = readers
    respond(transport, {'credentials': 'must not appear in error'}, status)
    with pytest.raises(RetryableReadError, match='^internal_read_unavailable$'):
        client.get('/internal/v1/test', 'fixture-scope', s.event['correlation_id'])
    assert transport.get.call_count == 1


@pytest.mark.parametrize('status', [301, 302, 307, 400, 401, 403, 404, 409])
def test_auth_and_invalid_requests_do_not_fallback(readers, status):
    s, transport, _, client = readers
    respond(transport, {'credentials': 'must not appear in error'}, status)
    with pytest.raises(InternalReadError, match='^internal_read_rejected$'):
        client.get('/internal/v1/test', 'fixture-scope', s.event['correlation_id'])


def test_wrong_goal_version_and_evaluation_ids_rejected(readers):
    s, transport, _, client = readers
    respond(transport, dict(s.goal, version=2))
    with pytest.raises(InternalReadError, match='goal_version_mismatch'):
        GoalHttpReader(client, s.contracts).version(s.goal['goal_id'], 1, s.event['correlation_id'])
    respond(transport, dict(s.result, evaluation_id=str(uuid4())))
    with pytest.raises(InternalReadError, match='evaluation_identity_mismatch'):
        EvaluationHttpReader(client, s.contracts, scope='fixture-scope').get(
            s.result['evaluation_id'], s.event['correlation_id'])


@pytest.mark.parametrize('field', ['provider', 'extraction_method'])
def test_demo_rejects_fixture_provenance(readers, field):
    s, transport, _, client = readers
    observation = dict(s.observation, provider='context_dev', extraction_method='scrape')
    observation[field] = 'fixture'
    respond(transport, observation)
    with pytest.raises(InternalReadError, match='fixture_provenance_forbidden'):
        EvidenceHttpReader(client, s.contracts, profile='demo').get(
            observation['observation_id'], s.event['correlation_id'])


def test_unknown_costs_and_capture_timestamp_never_rewritten(readers):
    s, transport, _, client = readers
    observation = dict(s.observation, shipping_eur_cents=None, tax_eur_cents=None)
    respond(transport, observation)
    assert EvidenceHttpReader(client, s.contracts, profile='test').get(
        observation['observation_id'], s.event['correlation_id']) == observation


def test_real_readers_drive_existing_consumer_without_private_imports(readers):
    s, transport, tokens, _ = readers
    def recorded(url, headers):
        if '/runtime' in url:
            row = vars(s.runtime)
        elif '/versions/' in url:
            row = s.goal
        elif '/evaluations/' in url:
            row = s.result
        elif '/observations?' in url:
            row = {'observations': [s.observation]}
        else:
            row = s.observation
        return 200, json.dumps(row).encode()
    transport.get.side_effect = recorded
    broker, connect = Mock(), Mock()
    worker = build_worker(connect_database=connect, broker_connection=broker,
        queue='fixture-queue', dead_letter_exchange='fixture-dlx', dead_letter_routing_key='fixture-dlq',
        schemas=ROOT / 'contracts/schemas',
        gm_origin='http://gm.internal', ge_origin='http://ge.internal', de_origin='http://de.internal',
        tokens=tokens, evaluation_read_scope='fixture-evaluation-scope', profile='test',
        clock=s.clock, transport=transport)
    # Use the explicit transactional fixture store for this recorded-reader
    # test. Live PostgreSQL persistence/concurrency is a separate acceptance gate.
    consumer = worker.coordinator.consumer
    consumer.store = s.store
    assert consumer.handle(s.event) == 'created'
    assert consumer.handle(s.event) == 'duplicate'
    assert len(s.store.notifications) == 1
    connect.assert_not_called()
    broker.channel.return_value.basic_qos.assert_called_once_with(prefetch_count=1)


def test_invalid_profile_fails_before_any_broker_operation(readers):
    s, transport, tokens, _ = readers
    broker = Mock()
    with pytest.raises(ValueError, match='profile'):
        build_worker(connect_database=Mock(), broker_connection=broker,
            queue='fixture-queue', dead_letter_exchange='fixture-dlx', dead_letter_routing_key='fixture-dlq',
            schemas=ROOT / 'contracts/schemas',
            gm_origin='http://gm.internal', ge_origin='http://ge.internal', de_origin='http://de.internal',
            tokens=tokens, evaluation_read_scope='fixture-scope', profile='prod', transport=transport)
    broker.channel.assert_not_called()


def test_oversize_transport_response_is_rejected():
    transport = HttpTransport(max_bytes=5)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'123456'
    transport.opener = Mock()
    transport.opener.open.return_value = response
    with pytest.raises(InternalReadError, match='internal_response_too_large'):
        transport.get('http://upstream.internal', {})
    response.read.assert_called_once_with(6)
