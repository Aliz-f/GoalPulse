"""Real HTTP reader adapters with recorded responses, never peer imports."""

import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from recorded_support import recorded_runtime
from agent_integration.readers import EvidenceHttpReader, InternalClient, InternalReadError
from agent_integration.service import RetryableReadError


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_only_transient_status_is_retryable(status):
    transport = Mock()
    transport.get.return_value = status, b'{"private":"not logged"}'
    client = InternalClient('http://peer.internal', lambda scope: 'fixture-token', transport)
    with pytest.raises(RetryableReadError, match='^internal_read_unavailable$'):
        client.get('/internal/v1/test', 'fixture-scope', str(uuid4()))


@pytest.mark.parametrize('status', [301, 302, 307, 400, 401, 403, 404, 409])
def test_permanent_and_redirect_status_does_not_fallback(status):
    transport = Mock()
    transport.get.return_value = status, b'{"private":"not logged"}'
    client = InternalClient('http://peer.internal', lambda scope: 'fixture-token', transport)
    with pytest.raises(InternalReadError, match='^internal_read_rejected$'):
        client.get('/internal/v1/test', 'fixture-scope', str(uuid4()))


def test_scopes_and_correlation_are_requested_per_operation():
    s = recorded_runtime()
    requested = []
    for client in (s.runtime.consumer.goals.client, s.runtime.consumer.evaluations.client,
                   s.runtime.consumer.evidence.client):
        client.tokens = lambda scope: requested.append(scope) or 'recorded-token'
    s.runtime.worker.run_once()
    assert set(requested) == {'gm.runtime.read', 'gm.goal-version.read', 'fixture-ge-read', 'de.observations.read'}
    assert all(correlation == s.data['event']['correlation_id'] for url, correlation in s.transport.calls)


@pytest.mark.parametrize('field', ['provider', 'extraction_method'])
def test_demo_rejects_fixture_provenance(field):
    s = recorded_runtime()
    row = dict(s.data['observation'], provider='context_dev', extraction_method='scrape')
    row[field] = 'fixture'
    transport = Mock()
    transport.get.return_value = 200, json.dumps(row).encode()
    client = InternalClient('http://de.internal', lambda scope: 'fixture-token', transport)
    reader = EvidenceHttpReader(client, s.runtime.consumer.contracts, profile='demo')
    with pytest.raises(InternalReadError, match='fixture_provenance_forbidden'):
        reader.get(row['observation_id'], str(uuid4()))
