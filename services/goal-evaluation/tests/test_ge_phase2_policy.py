"""ADR-0003 policy boundaries and JSON-only cross-service fingerprint proof."""

import hashlib
import copy
import json
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest

from test_persistence_outbox import ROOT, NOW, setup, save, verified_events
from goal_evaluation.offer_identity import business_fingerprint, offer_fingerprint as legacy_fingerprint
from goal_evaluation.persistence import offer_fingerprint
from goal_evaluation.processing import EvaluationContext


@pytest.mark.parametrize('age,verified', [(30, True), (31, False)])
def test_product_freshness_ceiling_is_thirty_minutes(setup, age, verified):
    s = setup
    s.observation['observed_at'] = (NOW - timedelta(minutes=age)).isoformat()
    result = s.dispatcher.evaluate(s.goal, [s.observation], NOW)
    assert result['is_opportunity_verified'] is verified
    assert next(c for c in result['conditions'] if c['code'] == 'freshness')['status'] == (
        'satisfied' if verified else 'unknown')


def test_stricter_goal_freshness_is_preserved(setup):
    s = setup
    s.goal['monitoring']['max_evidence_age_minutes'] = 5
    s.observation['observed_at'] = (NOW - timedelta(minutes=6)).isoformat()
    result = s.dispatcher.evaluate(s.goal, [s.observation], NOW)
    assert result['status'] == 'unknown'
    assert not result['is_opportunity_verified']


@pytest.mark.parametrize('age,emitted', [(10, True), (10.01, False)])
def test_sink_uses_ten_minute_alert_proof_limit(setup, age, emitted):
    s = setup
    s.observation['observed_at'] = (NOW - timedelta(minutes=age)).isoformat()
    s.result = s.dispatcher.evaluate(s.goal, [s.observation], NOW)
    s.context = EvaluationContext(s.goal, [s.observation], s.context.correlation_id)
    save(s)
    assert bool(verified_events(s)) is emitted
    assert len(s.store.data['evaluations']) == 1


def test_publisher_rechecks_ten_minute_boundary_after_queue_delay(setup):
    s = setup
    save(s)
    event = verified_events(s)[0]
    s.clock.return_value = NOW + timedelta(minutes=10, microseconds=1)
    with s.store.transaction() as tx:
        assert not s.publication_guard(event, tx)


def test_canonical_product_key_and_normalization_are_explicit(setup):
    s = setup
    o = s.observation
    text = '|'.join([s.goal['goal_id'], '1', 'example.com', 'rtx 5070', 'EUR',
        str(o['unit_price_eur_cents'] + o['shipping_eur_cents'] + o['tax_eur_cents']),
        str(o['shipping_eur_cents']), str(o['tax_eur_cents']), 'unknown', 'FI'])
    assert business_fingerprint(s.goal, [o]) == hashlib.sha256(text.encode()).hexdigest()
    changed = dict(o, observation_id=str(uuid4()), source_id=str(uuid4()),
                   source_url='https://EXAMPLE.com/relisted?tracking=x', observed_at=NOW.isoformat())
    assert business_fingerprint(s.goal, [o]) == business_fingerprint(s.goal, [changed])
    assert offer_fingerprint(s.result, s.context) == business_fingerprint(s.goal, [o])


def test_actual_notification_consumer_derives_same_key_in_separate_process(setup):
    s = setup
    save(s)
    payload = dict(runtime=vars(s.goals.runtime.return_value), goal=s.goal,
        evaluation=s.result, observations=[s.observation], now=NOW.isoformat(),
        event=verified_events(s)[0], scenario='verified')
    completed = subprocess.run([sys.executable,
        str(ROOT / 'services/notification/tests/handoff_probe.py')], input=json.dumps(payload),
        text=True, capture_output=True, check=True, timeout=20)
    response = json.loads(completed.stdout)
    assert response['count'] == 1
    assert response['outcomes'] == ['created', 'duplicate']
    assert response['notifications'][0]['offer_fingerprint'] == offer_fingerprint(s.result, s.context)


def test_existing_legacy_reservation_suppresses_duplicate_new_policy_event(setup):
    s = setup
    legacy = legacy_fingerprint(s.goal, [s.observation])
    event = s.sink._event('goal.opportunity_verified.v1', s.result, s.context.correlation_id)
    s.store.data['offers'].add((s.result['goal_id'], 1, legacy))
    s.store.data['offer_events'][(s.result['goal_id'], 1, legacy)] = event['event_id']
    s.store.data['outbox'][event['event_id']] = dict(event=event, status='published', attempts=1, due=NOW)
    s.store.data['confirmed'].add((s.result['goal_id'], 1, legacy))
    before = len(s.store.data['outbox'])
    save(s)
    # Only the historical goal.evaluated event is added, not another verification.
    assert len(s.store.data['outbox']) == before + 1


@pytest.mark.parametrize('change', ['disputed', 'price'])
def test_publication_honors_current_snapshot_even_when_historical_uuid_sorts_later(setup, change):
    s = setup
    save(s)
    latest = copy.deepcopy(s.observation)
    latest['observation_id'] = '00000000-0000-4000-8000-000000000000'
    if change == 'disputed':
        latest['evidence']['reliability'] = 'disputed'
    else:
        latest['unit_price_eur_cents'] += 1
    s.evidence.latest_for_source.return_value = [latest]
    with s.store.transaction() as tx:
        assert not s.publication_guard(verified_events(s)[0], tx)
