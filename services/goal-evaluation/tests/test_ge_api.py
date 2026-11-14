"""Internal endpoint integration with actual signed ICP-08 service tokens."""

import base64
import copy
import hashlib
import hmac
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_persistence_outbox import NOW, setup, save
from goal_evaluation.api import OwnerPrincipal, create_app
from goal_evaluation.auth import ServiceAuthorizer
from goal_evaluation.persistence import ImmutableEvaluationReader
from goal_evaluation.processing import Runtime
from goal_evaluation.readers import InternalNotFound

SECRET = 'synthetic-service-secret-for-tests-only-123'
SCOPE = 'fixture-ge-evaluation-read'


def bearer(*, secret=SECRET, header=None, **changes):
    claims = dict(sub=str(uuid4()), iss='fixture-issuer', aud='fixture-audience',
                  token_kind='service', scope=SCOPE, exp=int(NOW.timestamp()) + 60)
    claims.update(changes)
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    prefix = encode(header or {'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode(claims)
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), prefix.encode(), hashlib.sha256).digest()).decode().rstrip('=')
    return 'Bearer ' + prefix + '.' + signature


@pytest.fixture
def api(setup):
    s = setup
    save(s)
    reader = ImmutableEvaluationReader(s.contracts, s.store)
    auth = ServiceAuthorizer(secret=SECRET, issuer='fixture-issuer', audience='fixture-audience',
                              required_scope=SCOPE, clock=lambda: NOW)
    context = {'owner': s.goal['owner_user_id']}
    owner_authorizer = lambda: OwnerPrincipal(context['owner']) if context['owner'] else None
    goals = Mock()
    goals.runtime.return_value = Runtime(s.goal['goal_id'], s.goal['owner_user_id'], 1, 'active')
    ready = Mock(return_value=True)
    client = TestClient(create_app(reader=reader, authorizer=auth,
        owner_authorizer=owner_authorizer, goals=goals, ready=ready))
    path = '/internal/v1/evaluations/' + s.result['evaluation_id']
    headers = {'Authorization': bearer(), 'X-Correlation-ID': s.context.correlation_id}
    return s, reader, ready, client, path, headers, context, goals


def test_returns_exact_saved_evaluation_with_correlation_and_no_store(api):
    s, _, _, client, path, headers, _, _ = api
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == s.result
    assert response.headers['x-correlation-id'] == s.context.correlation_id
    assert response.headers['cache-control'] == 'no-store'
    s.contracts.validate('goal-evaluation', response.json())
    assert len(s.store.data['evaluations']) == 1
    assert len(s.store.data['outbox']) == 2


@pytest.mark.parametrize('changes', [
    {'token_kind': 'user'}, {'scope': 'gm.runtime.read'}, {'iss': 'wrong'}, {'aud': 'wrong'},
    {'sub': 'not-a-uuid'}, {'exp': int(NOW.timestamp())}, {'exp': True},
    {'nbf': int(NOW.timestamp()) + 1}, {'scope': [123]},
])
def test_rejects_invalid_claims_and_wrong_operation_scope(api, changes):
    _, _, _, client, path, headers, _, _ = api
    headers['Authorization'] = bearer(**changes)
    assert client.get(path, headers=headers).status_code == 403


def test_missing_forged_or_unsigned_identity_never_reads_storage(api):
    _, reader, _, client, path, headers, _, _ = api
    reader.get = Mock()
    for token in (None, 'Bearer invalid', bearer(secret='forged-secret'),
                  bearer(header={'alg': 'none', 'typ': 'JWT'})):
        request_headers = {'X-Correlation-ID': headers['X-Correlation-ID'], 'X-User-Id': str(uuid4())}
        if token:
            request_headers['Authorization'] = token
        assert client.get(path, headers=request_headers).status_code == 403
    reader.get.assert_not_called()


def test_even_correctly_signed_duplicate_claims_are_rejected(api):
    _, _, _, client, path, headers, _, _ = api
    header, payload, _ = bearer()[7:].split('.')
    raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)).decode()
    raw = raw[:-1] + ',"iss":"fixture-issuer"}'
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
    prefix = header + '.' + payload
    signature = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), prefix.encode(), hashlib.sha256).digest()).decode().rstrip('=')
    headers['Authorization'] = 'Bearer ' + prefix + '.' + signature
    assert client.get(path, headers=headers).status_code == 403


def test_unknown_evaluation_is_404_and_storage_failure_is_redacted_503(api):
    _, reader, _, client, _, headers, _, _ = api
    assert client.get('/internal/v1/evaluations/' + str(uuid4()), headers=headers).status_code == 404
    reader.get = Mock(side_effect=RuntimeError('sensitive DB credentials'))
    response = client.get('/internal/v1/evaluations/' + str(uuid4()), headers=headers)
    assert response.status_code == 503
    assert 'credentials' not in response.text


def test_correlation_must_be_present_and_uuid(api):
    _, _, _, client, path, headers, _, _ = api
    assert client.get(path, headers={'Authorization': headers['Authorization']}).status_code == 422
    headers['X-Correlation-ID'] = 'bad-correlation'
    assert client.get(path, headers=headers).status_code == 422


def test_owner_scoped_history_returns_only_current_version_and_propagates_correlation(api):
    s, _, _, client, _, _, _, goals = api
    obsolete = copy.deepcopy(s.result)
    obsolete['evaluation_id'] = str(uuid4())
    obsolete['goal_version'] = 2
    s.store.data['evaluations'][obsolete['evaluation_id']] = obsolete
    response = client.get(f'/v1/goals/{s.goal["goal_id"]}/evaluations?limit=20',
                          headers={'X-Correlation-ID': s.context.correlation_id})
    assert response.status_code == 200
    assert response.json() == [s.result]
    assert response.headers['x-correlation-id'] == s.context.correlation_id
    goals.runtime.assert_called_with(s.goal['goal_id'], s.context.correlation_id)


def test_history_hides_cross_owner_missing_goal_and_missing_identity(api):
    s, _, _, client, _, _, context, goals = api
    path = f'/v1/goals/{s.goal["goal_id"]}/evaluations'
    headers = {'X-Correlation-ID': s.context.correlation_id}
    context['owner'] = str(uuid4())
    assert client.get(path, headers=headers).status_code == 404
    context['owner'] = s.goal['owner_user_id']
    goals.runtime.side_effect = InternalNotFound('internal_resource_not_found')
    assert client.get(path, headers=headers).status_code == 404
    goals.runtime.side_effect = None
    context['owner'] = None
    assert client.get(path, headers=headers).status_code == 401


def test_history_requires_correlation_and_bounds_limit(api):
    s, _, _, client, _, _, _, _ = api
    path = f'/v1/goals/{s.goal["goal_id"]}/evaluations'
    assert client.get(path).status_code == 422
    headers = {'X-Correlation-ID': s.context.correlation_id}
    assert client.get(path + '?limit=0', headers=headers).status_code == 422
    assert client.get(path + '?limit=101', headers=headers).status_code == 422


def test_readiness_requires_explicit_success(api):
    _, _, ready, client, _, _, _, _ = api
    assert client.get('/health/live').status_code == 200
    assert client.get('/health/ready').status_code == 200
    ready.return_value = False
    assert client.get('/health/ready').status_code == 503
    ready.side_effect = RuntimeError('private DSN')
    assert client.get('/health/ready').status_code == 503
