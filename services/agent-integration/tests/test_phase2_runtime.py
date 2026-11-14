"""Independent recorded runtime, retry recovery and business dedup checks."""

import copy
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from recorded_support import NOW, ROOT, recorded_runtime
from recorded_smoke import smoke
from agent_integration.runtime import build_runtime
from agent_integration.retry import Decision


def test_independent_recorded_worker_and_http_smoke():
    result = smoke()
    assert result['mode'] == 'recorded_test' and not result['live_deployment']
    assert result['package_count'] == 1 and result['acknowledged_deliveries'] == 2
    assert (result['owner_get'], result['other_owner_get'], result['expired_get']) == (200, 404, 410)
    assert result['package']['approval_required'] is True
    assert result['package']['auto_execute'] is False


def test_valid_pipe_model_can_create_package():
    s = recorded_runtime()
    s.data['goal']['criteria']['product']['query'] = s.data['observation']['model'] = 'Model | Edition'
    assert s.runtime.consumer.handle(s.data['event']) == 'created'


def test_package_commit_then_journal_failure_recovers_without_second_package():
    s = recorded_runtime()
    s.journal.fail_finish = True
    with pytest.raises(RuntimeError, match='journal commit'):
        s.runtime.worker.run_once()
    assert len(s.store.packages) == 1
    s.channel.basic_ack.assert_not_called()
    s.journal.fail_finish = False
    s.clock.return_value += timedelta(seconds=301)
    assert s.runtime.worker.run_once()
    assert len(s.store.packages) == 1
    assert len(s.store.inbox) == 1
    s.channel.basic_ack.assert_called_once()


def test_transient_provider_failure_is_bounded_and_error_details_stay_private():
    s = recorded_runtime()
    from agent_integration.service import RetryableReadError
    s.runtime.consumer.goals.runtime = Mock(side_effect=RetryableReadError('sensitive detail'))
    s.runtime.consumer.wait = Mock()
    for attempt in range(5):
        result = s.runtime.worker.coordinator.run(s.data['event'])
        if attempt < 4:
            assert result.action == 'retry'
            s.clock.return_value += timedelta(seconds=result.delay_seconds)
        else:
            assert result == Decision('dead_letter')
    assert not s.store.packages and not s.store.inbox
    assert 'sensitive' not in repr(s.journal.rows)
    assert s.journal.rows[s.data['event']['event_id']]['attempts'] == 5


def test_relisted_capture_uses_same_business_key_and_original_package():
    s = recorded_runtime()
    s.runtime.worker.run_once()
    original = copy.deepcopy(s.store.packages)
    oid, eid = str(uuid4()), str(uuid4())
    s.data['event']['event_id'] = str(uuid4())
    s.data['event']['payload']['evaluation_id'] = s.data['evaluation']['evaluation_id'] = eid
    s.data['observation'].update(observation_id=oid, source_url='https://example.com/relisted?same-model=1')
    s.data['evaluation']['candidate_observation_ids'] = [oid]
    for condition in s.data['evaluation']['conditions']:
        condition['observation_ids'] = [oid]
    assert s.runtime.consumer.handle(s.data['event']) == 'deduplicated'
    assert s.store.packages == original


@pytest.mark.parametrize('profile', ['dev', 'demo'])
def test_recorded_stores_cannot_be_selected_outside_test_profile(profile):
    s = recorded_runtime()
    broker, connect = Mock(), Mock()
    with pytest.raises(ValueError, match='explicit test profile'):
        build_runtime(connect_database=connect, broker_connection=broker,
            queue='fixture-queue', dead_letter_exchange='', dead_letter_routing_key='fixture-dlq',
            schemas=ROOT / 'contracts/schemas', gm_origin='http://gm.internal',
            ge_origin='http://ge.internal', de_origin='http://de.internal',
            tokens=Mock(), evaluation_read_scope='fixture-ge-read', profile=profile,
            dependencies_ready=lambda: True, test_stores=(s.store, s.journal))
    connect.assert_not_called()
    broker.channel.assert_not_called()


def test_readiness_false_prevents_delivery_processing():
    s = recorded_runtime()
    s.runtime.worker.ready = lambda: False
    with pytest.raises(RuntimeError, match='not ready'):
        s.runtime.worker.run_once()
    s.channel.basic_get.assert_not_called()


def test_invalid_json_is_recorded_before_dead_letter_nack():
    s = recorded_runtime()
    method, properties, body = s.channel.basic_get.return_value
    s.channel.basic_get.return_value = method, properties, b'invalid JSON'
    assert s.runtime.worker.run_once()
    assert len(s.journal.rejections) == 1
    assert not s.store.packages
    s.channel.basic_nack.assert_called_once_with(delivery_tag=method.delivery_tag, requeue=False)


@pytest.mark.parametrize('change', ['disputed', 'price'])
def test_canonical_current_change_is_not_overridden_by_historical_uuid(change):
    s = recorded_runtime()
    latest = copy.deepcopy(s.data['observation'])
    latest['observation_id'] = '00000000-0000-4000-8000-000000000000'
    if change == 'disputed':
        latest['evidence']['reliability'] = 'disputed'
    else:
        latest['unit_price_eur_cents'] += 1
    s.runtime.consumer.evidence.latest = Mock(return_value=[latest])
    assert s.runtime.consumer.handle(s.data['event']) == 'suppressed'
    assert not s.store.packages
