"""Recorded ICP-01 responses, with no live network or shared private imports."""

import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from goal_evaluation.domain import Contracts
from goal_evaluation.processing import RetryableUpstreamError, Subscription
from goal_evaluation.readers import (
    EvidenceHttpReader, GoalHttpReader, HttpTransport, InternalClient,
    InternalNotFound, InternalReadError, SubscriptionHttpReader,
)

CORRELATION = str(uuid4())


def example(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


@pytest.fixture
def rig():
    transport = Mock()
    tokens = Mock(return_value='signed-service-jwt')
    client = InternalClient('http://service.internal:8000', tokens, transport)
    contracts = Contracts(ROOT / 'contracts/schemas')
    return transport, tokens, client, contracts


def respond(rig, row, status=200):
    rig[0].get.return_value = status, json.dumps(row).encode()


def test_goal_reads_validate_exact_identity_and_propagate_scoped_auth(rig):
    goal = example('goal-spec.product.json')
    reader = GoalHttpReader(rig[2], rig[3])
    respond(rig, dict(goal_id=goal['goal_id'], owner_user_id=goal['owner_user_id'],
                      current_version=goal['version'], lifecycle='active'))
    assert reader.runtime(goal['goal_id'], CORRELATION).owner_user_id == goal['owner_user_id']
    rig[1].assert_called_with('gm.runtime.read')
    assert rig[0].get.call_args.args[1] == {
        'Authorization': 'Bearer signed-service-jwt',
        'X-Correlation-ID': CORRELATION, 'Accept': 'application/json',
    }
    respond(rig, goal)
    assert reader.version(Subscription(goal['goal_id'], goal['version']), CORRELATION) == goal
    rig[1].assert_called_with('gm.goal-version.read')
    assert '/versions/' in rig[0].get.call_args.args[0]
    wrong = copy.deepcopy(goal)
    wrong['version'] += 1
    respond(rig, wrong)
    with pytest.raises(InternalReadError, match='goal_version_mismatch'):
        reader.version(Subscription(goal['goal_id'], goal['version']), CORRELATION)


def test_source_links_are_bounded_and_strict(rig):
    source, goal = str(uuid4()), str(uuid4())
    reader = SubscriptionHttpReader(rig[2], max_entries=1)
    row = {'source_id': source, 'subscriptions': [{'goal_id': goal, 'goal_version': 1}]}
    respond(rig, row)
    assert reader.for_source(source, CORRELATION) == [Subscription(goal, 1)]
    rig[1].assert_called_with('dm.sources.read')
    row['subscriptions'] *= 2
    respond(rig, row)
    with pytest.raises(InternalReadError):
        reader.for_source(source, CORRELATION)
    row['subscriptions'] = [{'goal_id': goal, 'goal_version': True}]
    respond(rig, row)
    with pytest.raises(ValueError):
        reader.for_source(source, CORRELATION)


def test_evidence_reads_keep_unknowns_and_original_capture_time(rig):
    observation = example('observation.verified.fixture.json')
    observation['shipping_eur_cents'] = None
    reader = EvidenceHttpReader(rig[2], rig[3], profile='test')
    respond(rig, observation)
    assert reader.observation(observation['observation_id'], CORRELATION) == observation
    rig[1].assert_called_with('de.observations.read')
    respond(rig, {'observations': [observation]})
    assert reader.latest_for_source(observation['source_id'], CORRELATION) == [observation]
    assert 'limit=1' in rig[0].get.call_args.args[0]
    respond(rig, {'observations': []})
    assert reader.latest_for_source(observation['source_id'], CORRELATION) == []


@pytest.mark.parametrize('field', ['provider', 'extraction_method'])
def test_demo_rejects_fixture_provenance(rig, field):
    observation = example('observation.verified.fixture.json')
    observation.update(provider='context_dev', extraction_method='scrape')
    observation[field] = 'fixture'
    respond(rig, observation)
    reader = EvidenceHttpReader(rig[2], rig[3], profile='demo')
    with pytest.raises(InternalReadError, match='fixture_provenance_forbidden'):
        reader.observation(observation['observation_id'], CORRELATION)


def test_wrong_observation_or_source_identity_is_rejected(rig):
    observation = example('observation.verified.fixture.json')
    reader = EvidenceHttpReader(rig[2], rig[3], profile='test')
    respond(rig, observation)
    with pytest.raises(InternalReadError, match='observation_identity_mismatch'):
        reader.observation(str(uuid4()), CORRELATION)
    respond(rig, {'observations': [observation]})
    with pytest.raises(InternalReadError, match='source_identity_mismatch'):
        reader.latest_for_source(str(uuid4()), CORRELATION)


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_only_transient_status_is_retryable(rig, status):
    respond(rig, {'secret': 'never include this response'}, status)
    with pytest.raises(RetryableUpstreamError, match='^internal_read_unavailable$'):
        rig[2].get('/internal/v1/test', 'read', CORRELATION)
    assert rig[0].get.call_count == 1  # orchestration owns bounded retries


@pytest.mark.parametrize('status', [301, 302, 307, 400, 401, 403, 409, 422])
def test_permanent_status_never_falls_back_or_leaks_response(rig, status):
    respond(rig, {'secret': 'sensitive'}, status)
    with pytest.raises(InternalReadError, match='^internal_read_rejected$'):
        rig[2].get('/internal/v1/test', 'read', CORRELATION)


def test_not_found_is_typed_without_leaking_response(rig):
    respond(rig, {'secret': 'sensitive'}, 404)
    with pytest.raises(InternalNotFound, match='^internal_resource_not_found$'):
        rig[2].get('/internal/v1/test', 'read', CORRELATION)


@pytest.mark.parametrize('body', [b'null', b'[]', b'not-json', b'\xff'])
def test_invalid_json_fails_closed(rig, body):
    rig[0].get.return_value = 200, body
    with pytest.raises(InternalReadError):
        rig[2].get('/internal/v1/test', 'read', CORRELATION)


@pytest.mark.parametrize('url', ['https://user:secret@host', 'http://host/path',
                                'http://host?token=x', 'http://host#x', 'file:///tmp/x'])
def test_origin_cannot_contain_credentials_or_arbitrary_path(url):
    with pytest.raises(ValueError):
        InternalClient(url, Mock(), Mock())


def test_bad_correlation_never_sends_request(rig):
    with pytest.raises(ValueError):
        rig[2].get('/internal/v1/test', 'read', 'bad-id')
    rig[0].get.assert_not_called()


def test_transport_caps_body_without_logging_it():
    transport = HttpTransport(max_bytes=10)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'x' * 11
    transport.opener = Mock()
    transport.opener.open.return_value = response
    with pytest.raises(InternalReadError, match='internal_response_too_large'):
        transport.get('http://service.internal', {})
    response.read.assert_called_once_with(11)
