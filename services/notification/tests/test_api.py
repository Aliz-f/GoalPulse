"""Private handler integration with explicit synthetic identity dependencies."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from test_owned_reads import (
    NOW, USER_A, USER_B, MemoryReadStore, identifier, stored,
)
from notification.api import create_app
from notification.reads import VerifiedPrincipal

CORRELATION = identifier(500)


@pytest.fixture
def rig():
    store = MemoryReadStore([stored(1), stored(2, USER_B), stored(3)])
    context = {'user': USER_A, 'write_allowed': True}

    def read():
        return VerifiedPrincipal(context['user']) if context['user'] else None

    def write():
        if not context['write_allowed']:
            raise HTTPException(403, 'forwarding policy required')
        return read()

    ready = Mock(return_value=True)
    app = create_app(store=store, authorize_read=read, authorize_write=write,
                     clock=lambda: NOW, ready=ready)
    return store, context, ready, TestClient(app, headers={'X-Correlation-ID': CORRELATION})


def test_lists_owner_only_with_exact_extension_schema(rig):
    store, context, _, client = rig
    response = client.get('/v1/notifications?owner_user_id=' + USER_B)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-correlation-id'] == CORRELATION
    assert [row['notification_id'] for row in response.json()] == [identifier(3), identifier(1)]
    root = Path(__file__).resolve().parents[3]
    schema = yaml.safe_load((root / 'contracts/openapi/gateway.phase2.extension.v1.yaml').read_text())
    validator = Draft202012Validator(schema['components']['schemas']['Notification'],
                                    format_checker=FormatChecker())
    for row in response.json():
        validator.validate(row)
        assert 'user_id' not in row and 'read_at' not in row
    context['user'] = USER_B
    assert [row['notification_id'] for row in client.get('/v1/notifications').json()] == [identifier(2)]


def test_mark_read_preserves_first_time_and_returns_empty_204(rig):
    store, context, _, client = rig
    path = f'/v1/notifications/{identifier(1)}/read'
    for _ in range(2):
        response = client.patch(path)
        assert response.status_code == 204 and response.content == b''
        assert response.headers['x-correlation-id'] == CORRELATION
    assert store.rows[identifier(1)].item.read_at == NOW
    assert any(row['is_read'] for row in client.get('/v1/notifications').json())


def test_cross_owner_and_missing_are_identical(rig):
    store, context, _, client = rig
    context['user'] = USER_B
    unowned = client.patch(f'/v1/notifications/{identifier(1)}/read')
    missing = client.patch(f'/v1/notifications/{identifier(999)}/read')
    assert unowned.status_code == missing.status_code == 404
    assert unowned.json() == missing.json()
    assert not store.rows[identifier(1)].item.is_read


def test_unauthenticated_reads_and_writes_never_touch_store(rig):
    store, context, _, client = rig
    context['user'] = None
    assert client.get('/v1/notifications').status_code == 401
    assert client.patch(f'/v1/notifications/{identifier(1)}/read').status_code == 401
    assert store.calls == 0


def test_required_write_policy_cannot_be_bypassed_with_read_auth(rig):
    store, context, _, client = rig
    context['write_allowed'] = False
    assert client.get('/v1/notifications').status_code == 200
    assert client.patch(f'/v1/notifications/{identifier(1)}/read').status_code == 403
    assert not store.rows[identifier(1)].item.is_read


@pytest.mark.parametrize('limit', [0, 101, 'invalid'])
def test_invalid_page_limit_rejected(rig, limit):
    store, _, _, client = rig
    assert client.get(f'/v1/notifications?limit={limit}').status_code == 422
    assert store.calls == 0


def test_correlation_is_required_and_must_be_uuid(rig):
    store, _, _, client = rig
    correlation = client.headers.pop('x-correlation-id')
    try:
        assert client.get('/v1/notifications').status_code == 422
        assert client.patch(f'/v1/notifications/{identifier(1)}/read').status_code == 422
    finally:
        client.headers['x-correlation-id'] = correlation
    assert client.get('/v1/notifications', headers={'X-Correlation-ID': 'invalid'}).status_code == 422
    assert store.calls == 0


def test_storage_failure_is_visible_without_exception_details(rig):
    store, _, _, client = rig
    store.fail_read = True
    response = client.get('/v1/notifications')
    assert response.status_code == 503
    assert response.json() == {'detail': 'notification storage unavailable'}
    store.fail_read, store.fail_commit = False, True
    assert client.patch(f'/v1/notifications/{identifier(1)}/read').status_code == 503
    assert not store.rows[identifier(1)].item.is_read


def test_readiness_requires_explicit_success(rig):
    _, _, ready, client = rig
    assert client.get('/health/live').status_code == 200
    assert client.get('/health/ready').status_code == 200
    for value in (False, None, 'true'):
        ready.return_value = value
        assert client.get('/health/ready').status_code == 503
    ready.side_effect = RuntimeError('secret connection URL')
    response = client.get('/health/ready')
    assert response.status_code == 503
    assert 'secret' not in response.text
