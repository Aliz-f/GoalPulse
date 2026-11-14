"""Recorded owner-auth dependencies; no running peer services or Gateway."""

import copy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from test_packages import setup, create, NOW
from test_eligibility import inputs
from agent_integration.api import create_app
from agent_integration.service import PackageReader, Principal

CORRELATION = 'ae000000-0000-4000-8000-000000000099'


@pytest.fixture
def api(setup):
    s = setup
    package = create(s)
    reader = PackageReader(s.contract, s.store, s.goals, s.clock,
                            evidence=s.evidence, contracts=s.contracts)
    context = {'owner': s.inputs['goal']['owner_user_id']}
    def authenticate():
        return Principal(context['owner']) if context['owner'] else None
    client = TestClient(create_app(reader=reader, authenticate=authenticate, ready=lambda: True),
                        headers={'X-Correlation-ID': CORRELATION})
    path = '/v1/action-packages/' + package['package_id']
    return s, package, context, client, path


def test_exact_grouped_dto_matches_phase2_extension_schema(api):
    s, package, _, client, path = api
    correlation = str(uuid4())
    response = client.get(path, headers={'X-Correlation-ID': correlation})
    assert response.status_code == 200
    assert response.json() == package
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-correlation-id'] == correlation
    spec = yaml.safe_load((Path(__file__).resolve().parents[3] /
                          'contracts/openapi/gateway.phase2.extension.v1.yaml').read_text())
    validator = Draft202012Validator({'$ref': '#/components/schemas/ActionPackage',
        'components': spec['components']}, format_checker=FormatChecker())
    validator.validate(response.json())
    assert 'seller_orders' in response.json() and 'seller_offers' not in response.json()
    assert 'owner_user_id' not in response.json()
    s.goals.runtime.assert_called_with(package['goal_id'], correlation)


def test_other_owner_and_missing_package_are_indistinguishable(api):
    s, _, context, client, path = api
    context['owner'] = str(uuid4())
    existing = client.get(path)
    missing = client.get('/v1/action-packages/' + str(uuid4()))
    assert existing.status_code == missing.status_code == 404
    assert existing.json() == missing.json()
    assert existing.headers['cache-control'] == 'no-store'


def test_unauthenticated_request_cannot_choose_owner_in_headers(api):
    s, _, context, client, path = api
    context['owner'] = None
    response = client.get(path, headers={'X-User-Id': s.inputs['goal']['owner_user_id']})
    assert response.status_code == 401


def test_correlation_is_required_and_must_be_uuid(api):
    _, _, _, client, path = api
    correlation = client.headers.pop('x-correlation-id')
    try:
        assert client.get(path).status_code == 422
    finally:
        client.headers['x-correlation-id'] = correlation
    assert client.get(path, headers={'X-Correlation-ID': 'invalid'}).status_code == 422


def test_expired_package_is_410_and_get_never_renews_it(api):
    s, package, _, client, path = api
    before = copy.deepcopy(s.store.packages)
    s.clock.return_value = NOW + timedelta(minutes=30)
    response = client.get(path)
    assert response.status_code == 410
    assert s.store.packages == before


@pytest.mark.parametrize('change', ['disputed', 'price', 'unsafe', 'future', 'stale'])
def test_changed_or_disputed_latest_evidence_blocks_read(api, change):
    s, package, _, client, path = api
    latest = copy.deepcopy(s.inputs['observations'][0])
    latest['observation_id'] = str(uuid4())
    if change == 'disputed': latest['evidence']['reliability'] = 'disputed'
    elif change == 'price': latest['unit_price_eur_cents'] += 1
    elif change == 'unsafe': latest['source_url'] = 'https://example.com/item?access_token=FIXTURE_SECRET'
    elif change == 'future': latest['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    else: latest['observed_at'] = (NOW - timedelta(minutes=31)).isoformat()
    s.evidence.latest.side_effect = None
    s.evidence.latest.return_value = [latest]
    assert client.get(path).status_code == 410


def test_unchanged_recapture_preserves_original_citations_and_expiry(api):
    s, package, _, client, path = api
    latest = dict(s.inputs['observations'][0], observation_id=str(uuid4()),
                  observed_at=(NOW + timedelta(seconds=1)).isoformat())
    s.evidence.latest.side_effect = None
    s.evidence.latest.return_value = [latest]
    s.clock.return_value = NOW + timedelta(seconds=2)
    assert client.get(path).json() == package


def test_provider_failure_is_visible_and_does_not_return_cached_success(api):
    s, _, _, client, path = api
    s.evidence.get.side_effect = RuntimeError('sensitive credential details')
    response = client.get(path)
    assert response.status_code == 503
    assert 'credential' not in response.text


def test_no_execution_or_mutation_endpoint_is_exposed(api):
    _, _, _, client, path = api
    assert client.post(path).status_code == 405
    assert client.post(path + '/execute').status_code == 404
    assert client.post(path + '/approve').status_code == 404


def test_legacy_reader_cannot_be_accidentally_exposed_as_current_http_api(setup):
    s = setup
    with pytest.raises(ValueError, match='current-evidence validation'):
        create_app(reader=s.reader, authenticate=lambda: Principal(s.inputs['goal']['owner_user_id']),
                   ready=lambda: True)
