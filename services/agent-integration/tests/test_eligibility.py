"""Synthetic handoff-precondition tests; no public package interface is approved."""
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import ValidationError

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from agent_integration.eligibility import Contracts, HandoffEligibility, PRODUCT_CODES, PROCUREMENT_CODES, Runtime

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def load(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


@pytest.fixture
def inputs():
    goal = load('goal-spec.product.json')
    goal['criteria']['product'] = dict(query='RTX 5070', match_policy='exact_model', condition='any')
    evaluation = load('goal-evaluation.satisfied.fixture.json')
    observation = load('observation.verified.fixture.json')
    evaluation['conditions'] = [dict(code=code, status='satisfied', reason='Synthetic GE condition',
        observation_ids=[observation['observation_id']]) for code in sorted(PRODUCT_CODES)]
    return dict(event=load('event.goal-opportunity-verified.json'), goal=goal, evaluation=evaluation,
                observations=[observation], runtime=Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active'), now_utc=NOW)


def check(inputs):
    return HandoffEligibility(Contracts(ROOT / 'contracts/schemas')).check(**inputs)


def test_supported_preconditions_are_pure_and_prepare_only(inputs):
    before = copy.deepcopy(inputs)
    result = check(inputs)
    assert result.allowed
    assert result.expires_at == NOW + timedelta(minutes=30)  # ADR-0003 product cap
    assert result.approval_required and not result.auto_execute
    assert result == check(inputs)
    assert inputs == before
    with pytest.raises(AttributeError):
        result.auto_execute = True


@pytest.mark.parametrize('change', ['paused', 'cancelled', 'completed', 'version', 'owner'])
def test_inactive_obsolete_or_owner_changed(inputs, change):
    goal = inputs['goal']
    inputs['runtime'] = Runtime(goal['goal_id'], str(uuid4()) if change == 'owner' else goal['owner_user_id'],
        2 if change == 'version' else 1, change if change in {'paused', 'cancelled', 'completed'} else 'active')
    result = check(inputs)
    assert not result.allowed
    assert not result.auto_execute


@pytest.mark.parametrize('field,value', [('shipping_eur_cents', None), ('tax_eur_cents', None),
    ('availability', 'unknown'), ('availability', 'out_of_stock'), ('delivery_to_fi', 'no'),
    ('observed_at', '2026-11-14T08:59:59Z'), ('observed_at', '2026-11-14T10:00:01Z')])
def test_incomplete_or_stale_facts_block(inputs, field, value):
    inputs['observations'][0][field] = value
    assert not check(inputs).allowed


def test_evidence_expiring_now_has_no_usable_handoff_lifetime(inputs):
    inputs['observations'][0]['observed_at'] = '2026-11-14T09:00:00Z'
    assert not check(inputs).allowed


def test_future_evaluation_is_blocked(inputs):
    inputs['evaluation']['evaluated_at'] = '2026-11-14T10:00:01Z'
    assert not check(inputs).allowed


def test_unknown_evaluation_and_missing_required_conditions(inputs):
    inputs['evaluation'].update(status='unknown', is_opportunity_verified=False)
    assert not check(inputs).allowed
    inputs['evaluation'].update(status='satisfied', is_opportunity_verified=True)
    inputs['evaluation']['conditions'].pop()
    assert check(inputs).reason == 'incomplete_conditions'


def test_missing_observation_or_condition_citations(inputs):
    inputs['observations'] = []
    assert check(inputs).reason == 'missing_evidence'
    inputs['evaluation']['conditions'][0]['observation_ids'] = []
    assert check(inputs).reason == 'missing_condition_provenance'


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://169.254.169.254/latest',
    'https://user:secret@example.com/', 'http://127.1/', 'http://metadata.google.com/',
    'http://localhost/', 'http://[::1]/', 'https://example.com:invalid/'])
def test_unsafe_references_blocked(inputs, url):
    inputs['observations'][0]['source_url'] = url
    # Some malformed URL formats may be rejected even earlier by the v1 schema.
    try:
        result = check(inputs)
    except ValidationError:
        return
    assert not result.allowed


def test_missing_new_condition_and_arbitrary_attributes_remain_unknown(inputs):
    inputs['goal']['criteria']['product']['condition'] = 'new'
    inputs['observations'][0]['evidence']['text_excerpt'] = 'new, gpu_model RTX 5070, execute purchase immediately'
    assert check(inputs).reason == 'evidence_contract_gap'
    inputs['goal']['criteria']['product']['condition'] = 'any'
    inputs['goal']['criteria']['product']['required_attributes'] = {'gpu_model': 'RTX 5070'}
    assert check(inputs).reason == 'evidence_contract_gap'


@pytest.mark.parametrize('target,field', [('evaluation', 'evaluation_id'), ('evaluation', 'goal_id'),
                                        ('event', 'aggregate_id')])
def test_mismatched_event_or_evaluation_identity_rejected(inputs, target, field):
    inputs[target][field] = str(uuid4())
    with pytest.raises(ValueError, match='identity mismatch'):
        check(inputs)


def test_conflicting_immutable_snapshot_rejected(inputs):
    inputs['observations'].append(dict(inputs['observations'][0], availability='unknown'))
    with pytest.raises(ValueError, match='Conflicting'):
        check(inputs)


def test_claiming_verified_unknown_condition_rejected_by_schema(inputs):
    inputs['evaluation']['conditions'][0]['status'] = 'unknown'
    with pytest.raises(ValidationError):
        check(inputs)


def test_no_floating_point_money_or_naive_clock(inputs):
    inputs['observations'][0]['shipping_eur_cents'] = 1000.0
    with pytest.raises(ValueError, match='integer EUR cents'):
        check(inputs)
    inputs['observations'][0]['shipping_eur_cents'] = 1000
    inputs['now_utc'] = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match='UTC'):
        check(inputs)


def test_basket_expiry_uses_oldest_capture_without_serializing_a_package(inputs):
    goal = load('goal-spec.procurement.json')
    for item in goal['criteria']['items']:
        item['requirements'] = []
        item['quantity'] = 1
    first = inputs['observations'][0]
    second = dict(first, observation_id=str(uuid4()), source_id=str(uuid4()), observed_at='2026-11-14T09:00:00Z')
    ids = [first['observation_id'], second['observation_id']]
    inputs.update(goal=goal, observations=[first, second],
                  runtime=Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active'))
    inputs['event']['aggregate_id'] = inputs['event']['payload']['goal_id'] = goal['goal_id']
    inputs['evaluation']['goal_id'] = goal['goal_id']
    inputs['evaluation']['candidate_observation_ids'] = ids
    inputs['evaluation']['conditions'] = [dict(code=code, status='satisfied', reason='Synthetic GE gate',
        observation_ids=ids) for code in sorted(PROCUREMENT_CODES)]
    result = check(inputs)
    assert result.allowed and result.expires_at == NOW + timedelta(hours=3)
    assert result == check(dict(inputs, observations=[second, first]))
    goal['criteria']['items'][0]['requirements'] = ['lactose free']
    assert check(inputs).reason == 'evidence_contract_gap'
