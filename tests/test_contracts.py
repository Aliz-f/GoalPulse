from pathlib import Path
import copy
import json

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
def data(rel):
    return json.loads((ROOT / rel).read_text(encoding='utf-8'))
def valid(schema, obj):
    return Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(obj)

@pytest.fixture
def goal_schema():
    return data('contracts/schemas/goal-spec.v1.schema.json')

@pytest.fixture
def ev_schema():
    return data('contracts/schemas/goal-evaluation.v1.schema.json')

@pytest.fixture
def event_schema():
    return data('contracts/schemas/domain-event.v1.schema.json')


def test_two_supported_goal_types(goal_schema):
    assert valid(goal_schema, data('contracts/examples/goal-spec.product.json'))
    assert valid(goal_schema, data('contracts/examples/goal-spec.product-new.json'))
    assert valid(goal_schema, data('contracts/examples/goal-spec.procurement.json'))


def test_reject_incomplete_goal(goal_schema):
    obj = data('contracts/examples/goal-spec.product.json')
    del obj['criteria']['max_total_eur_cents']
    assert not valid(goal_schema, obj)


def test_reject_price_float(goal_schema):
    obj = data('contracts/examples/goal-spec.product.json')
    obj['criteria']['max_total_eur_cents'] = 599.99
    assert not valid(goal_schema, obj)


def test_reject_auto_purchase(goal_schema):
    obj = data('contracts/examples/goal-spec.product.json')
    obj['actions']['auto_purchase'] = True
    assert not valid(goal_schema, obj)


def test_invalid_goal_type_criteria_pair(goal_schema):
    obj = data('contracts/examples/goal-spec.product.json')
    obj['goal_type'] = 'recurring_procurement'
    assert not valid(goal_schema, obj)


def test_unknown_cannot_be_verified(ev_schema):
    obj = data('contracts/examples/goal-evaluation.unknown.fixture.json')
    obj['is_opportunity_verified'] = True
    assert not valid(ev_schema, obj)


def test_event_payload_type_is_checked(event_schema):
    obj = data('contracts/examples/event.goal-activated.json')
    obj['payload']['unexpected_field'] = 'not allowed'
    assert not valid(event_schema, obj)


def test_wrong_event_producer(event_schema):
    obj = data('contracts/examples/event.goal-activated.json')
    obj['producer'] = 'data-evidence'
    assert not valid(event_schema, obj)


def test_verified_requires_all_conditions_satisfied(ev_schema):
    obj = data('contracts/examples/goal-evaluation.satisfied.fixture.json')
    obj['conditions'][1]['status'] = 'unknown'
    assert not valid(ev_schema, obj)


def test_new_condition_without_structured_evidence_is_unknown(goal_schema, ev_schema):
    goal = data('contracts/examples/goal-spec.product-new.json')
    observation = data('contracts/examples/observation.verified.fixture.json')
    evaluation = data('contracts/examples/goal-evaluation.condition-unknown.fixture.json')
    assert valid(goal_schema, goal) and valid(ev_schema, evaluation)
    assert goal['criteria']['product']['condition'] == 'new'
    assert 'condition' not in observation
    condition = next(item for item in evaluation['conditions'] if item['code'] == 'product_condition')
    assert condition['status'] == 'unknown'
    assert evaluation['status'] == 'unknown'
    assert evaluation['is_opportunity_verified'] is False


def test_satisfied_exact_model_fixture_uses_explicit_mapping(goal_schema, ev_schema):
    goal = data('contracts/examples/goal-spec.product.json')
    observation = data('contracts/examples/observation.verified.fixture.json')
    evaluation = data('contracts/examples/goal-evaluation.satisfied.fixture.json')
    assert valid(goal_schema, goal) and valid(ev_schema, evaluation)
    assert goal['criteria']['product'] == {
        'query': 'NVIDIA GeForce RTX 5070',
        'match_policy': 'exact_model',
        'condition': 'any',
    }
    assert observation['model'] == 'RTX 5070'
    assert any(item['code'] == 'exact_model_match' and item['status'] == 'satisfied'
               for item in evaluation['conditions'])
