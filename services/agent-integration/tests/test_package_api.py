"""In-process HTTP tests using synthetic authenticated identities, not real JWTs."""
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from agent_integration.api import create_app
from agent_integration.service import Principal
from test_packages import inputs, setup, create, NOW


@pytest.fixture
def http(setup):
    s = setup
    owner, other = s.inputs['runtime'].owner_user_id, str(uuid4())
    def authenticate(authorization: str | None = Header(default=None)):
        # Only a test identity adapter. Production must validate issuer/signature/audience.
        subjects = {'Bearer fixture-owner': owner, 'Bearer fixture-other': other}
        if authorization not in subjects:
            raise HTTPException(401, 'Authentication required')
        return Principal(subjects[authorization])
    return s, TestClient(create_app(s.reader, authenticate))


def test_owner_get_copy_ready_json_with_revalidation_and_no_side_effect(http):
    s, client = http
    package = create(s)
    before = len(s.store.packages)
    response = client.get('/v1/action-packages/' + package['package_id'], headers={'Authorization': 'Bearer fixture-owner'})
    assert response.status_code == 200
    assert response.json() == package
    assert response.json()['requires_checkout_revalidation']
    assert response.json()['next_action'] == 'request_user_approval'
    assert response.headers['cache-control'] == 'no-store'
    assert len(s.store.packages) == before


def test_other_user_unknown_and_unsigned_header_denied(http):
    s, client = http
    package = create(s)
    path = '/v1/action-packages/' + package['package_id']
    other = client.get(path, headers={'Authorization': 'Bearer fixture-other'})
    missing = client.get('/v1/action-packages/' + str(uuid4()), headers={'Authorization': 'Bearer fixture-other'})
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    response = client.get(path, headers={'X-User-Id': s.inputs['runtime'].owner_user_id})
    assert response.status_code == 401
    assert other.headers['cache-control'] == 'no-store'


def test_expired_package_410_without_package_payload(http):
    s, client = http
    package = create(s)
    s.clock.return_value = NOW + timedelta(hours=1)
    response = client.get('/v1/action-packages/' + package['package_id'], headers={'Authorization': 'Bearer fixture-owner'})
    assert response.status_code == 410
    assert 'seller_orders' not in response.json()
    assert 'revalidation' in response.json()['detail']


def test_dependency_failure_returns_safe_503_not_fake_package(http):
    s, client = http
    package = create(s)
    s.goals.runtime.side_effect = ConnectionError('secret credentials in upstream error')
    response = client.get('/v1/action-packages/' + package['package_id'], headers={'Authorization': 'Bearer fixture-owner'})
    assert response.status_code == 503
    assert 'secret' not in response.text


def test_get_never_refreshes_or_exposes_checkout_endpoint(http):
    s, client = http
    package = create(s)
    headers = {'Authorization': 'Bearer fixture-owner'}
    assert client.get('/v1/action-packages/' + package['package_id'], headers=headers).status_code == 200
    assert client.post('/v1/action-packages/' + package['package_id'], headers=headers).status_code == 405
    assert client.post('/checkout', json={'auto_execute': True}, headers=headers).status_code == 404
    assert len(s.store.packages) == 1


def test_legacy_stored_credential_url_is_not_exported(http):
    s, client = http
    p = create(s)
    s.store.packages[p['package_id']][1]['seller_orders'][0]['items'][0]['source_url'] = (
        'https://example.com/gpu?api_key=FIXTURE_CREDENTIAL_SENTINEL')
    response = client.get('/v1/action-packages/' + p['package_id'],
                          headers={'Authorization': 'Bearer fixture-owner'})
    assert response.status_code == 503
    assert 'FIXTURE_CREDENTIAL_SENTINEL' not in response.text
    assert 'seller_orders' not in response.json()
