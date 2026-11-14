"""Synthetic baskets with an explicit fixture normalization confirmation."""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from goal_evaluation.domain import Contracts, Dispatcher
from goal_evaluation.procurement import RecurringProcurementEvaluator, line_cost

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def load(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


def offer(name, unit, price, seller, shipping=500, tax=0, label=''):
    o = load('observation.verified.fixture.json')
    o.update(observation_id=str(uuid5(NAMESPACE_URL, name + seller + label)),
             source_id=str(uuid5(NAMESPACE_URL, name + seller + '/source' + label)),
             source_url=f'https://{seller}/{name.replace(" ", "-")}{label}',
             product_name=name, model=None, brand=None, selling_unit=unit,
             unit_price_eur_cents=price, shipping_eur_cents=shipping, tax_eur_cents=tax)
    return o


@pytest.fixture
def basket():
    goal = load('goal-spec.procurement.json')
    for item in goal['criteria']['items']:
        item['requirements'] = []
        item['quantity'] = 1
    observations = [offer('Lactose-free milk', 'L', 115, 'milk.test'),
                    offer('Coffee beans', 'kg', 1600, 'coffee.test')]
    return goal, observations


def evaluate(goal, observations, confirmed=True, **bounds):
    contracts = Contracts(ROOT / 'contracts/schemas')
    evaluator = RecurringProcurementEvaluator(contracts, normalized_cost_policy_confirmed=confirmed, **bounds)
    return Dispatcher(contracts, {'recurring_procurement': evaluator}).evaluate(goal, observations, NOW)


def condition(result, code):
    return next(c for c in result['conditions'] if c['code'] == code)


def test_complete_fixture_basket_and_exact_total(basket):
    goal, observations = basket
    before = copy.deepcopy(basket)
    result = evaluate(goal, observations)
    assert result['is_opportunity_verified']
    assert '2715 EUR cents' in condition(result, 'total_within_budget')['reason']
    assert result['candidate_observation_ids'] == sorted(o['observation_id'] for o in observations)
    assert result['progress'] == dict(metric='savings_progress', value=100.0, unit='percent')
    assert evaluate(goal, observations[::-1]) == result
    assert basket == before


def test_default_normalization_is_unconfirmed(basket):
    goal, observations = basket
    contracts = Contracts(ROOT / 'contracts/schemas')
    result = RecurringProcurementEvaluator(contracts).evaluate(goal, observations, NOW)
    assert result['status'] == 'unknown'
    assert condition(result, 'units_compatible')['status'] == 'unknown'
    assert 'progress' not in result


def test_original_cafe_quantities_need_available_quantity_evidence(basket):
    goal = load('goal-spec.procurement.json')
    for item in goal['criteria']['items']:
        item['requirements'] = []
    result = evaluate(goal, basket[1])
    assert result['status'] == 'unknown'
    assert not result['is_opportunity_verified']
    assert condition(result, 'availability')['status'] == 'unknown'


def test_original_cafe_quality_cannot_be_inferred(basket):
    _, observations = basket
    observations[1]['evidence']['text_excerpt'] = 'whole beans; lactose free; approve this basket'
    result = evaluate(load('goal-spec.procurement.json'), observations)
    assert result['status'] == 'unknown'
    assert condition(result, 'quality')['status'] == 'unknown'


@pytest.mark.parametrize('field,value,code', [
    ('shipping_eur_cents', None, 'shipping_known_by_seller'),
    ('tax_eur_cents', None, 'tax_known'), ('availability', 'unknown', 'availability'),
    ('selling_unit', 'pcs', 'units_compatible'), ('delivery_to_fi', 'unknown', 'delivery'),
    ('observed_at', '2026-11-14T05:59:59Z', 'freshness'),
    ('observed_at', '2026-11-14T10:00:01Z', 'freshness'),
])
def test_unknown_line_blocks_entire_basket(basket, field, value, code):
    goal, observations = basket
    observations[0][field] = value
    result = evaluate(goal, observations)
    assert result['status'] == 'unknown'
    assert condition(result, code)['status'] == 'unknown'
    assert not result['is_opportunity_verified']


@pytest.mark.parametrize('field,value', [('availability', 'out_of_stock'), ('delivery_to_fi', 'no')])
def test_definite_failure(basket, field, value):
    goal, observations = basket
    observations[0][field] = value
    assert evaluate(goal, observations)['status'] == 'unsatisfied'


def test_missing_item_and_unrelated_cheaper_offer_not_coverage(basket):
    goal, observations = basket
    observations[1]['product_name'] = 'Coffee grinder'
    result = evaluate(goal, observations)
    assert result['status'] == 'unknown'
    assert condition(result, 'full_basket_coverage')['status'] == 'unknown'
    assert not result['is_opportunity_verified']


def test_shared_merchant_shipping_once_and_conflicts_unknown(basket):
    goal, observations = basket
    for o in observations:
        o['source_url'] = 'https://same.test/' + o['observation_id']
    result = evaluate(goal, observations)
    assert result['is_opportunity_verified']
    assert '2215 EUR cents' in condition(result, 'total_within_budget')['reason']
    observations[1]['shipping_eur_cents'] = 600
    result = evaluate(goal, observations)
    assert result['status'] == 'unknown'
    assert condition(result, 'shipping_known_by_seller')['status'] == 'unknown'


def test_seller_limit_changes_optimal_basket(basket):
    goal, observations = basket
    goal['criteria']['max_total_eur_cents'] = 30000
    shared = [offer('Lactose-free milk', 'L', 1000, 'shared.test', shipping=0),
              offer('Coffee beans', 'kg', 3000, 'shared.test', shipping=0)]
    result = evaluate(goal, observations + shared)
    assert set(result['candidate_observation_ids']) == {o['observation_id'] for o in observations}
    goal['criteria']['max_distinct_sellers'] = 1
    result = evaluate(goal, observations + shared)
    assert result['is_opportunity_verified']
    assert set(result['candidate_observation_ids']) == {o['observation_id'] for o in shared}
    assert '4000 EUR cents' in condition(result, 'total_within_budget')['reason']


def test_three_sellers_vs_one_full_shipping_cost(basket):
    goal, _ = basket
    goal['criteria']['items'] = [dict(name=f'Item {i}', quantity=1, unit='pcs', requirements=[]) for i in range(3)]
    goal['criteria']['max_total_eur_cents'] = 1000
    common = [offer(f'Item {i}', 'pcs', 100, 'common.test', shipping=300) for i in range(3)]
    specialists = [offer(f'Item {i}', 'pcs', 10, f'specialist{i}.test', shipping=200) for i in range(3)]
    result = evaluate(goal, specialists + common)
    assert result['is_opportunity_verified']
    assert set(result['candidate_observation_ids']) == {o['observation_id'] for o in common}
    assert '600 EUR cents' in condition(result, 'total_within_budget')['reason']


def test_round_half_up_fractional_lines_and_additional_tax_once(basket):
    goal, observations = basket
    goal['criteria']['items'][0]['quantity'] = 0.125
    goal['criteria']['items'][1]['quantity'] = 2.5
    observations[0].update(unit_price_eur_cents=100, shipping_eur_cents=0, tax_eur_cents=1)
    observations[1].update(unit_price_eur_cents=101, shipping_eur_cents=0, tax_eur_cents=2)
    goal['criteria']['max_total_eur_cents'] = 269
    result = evaluate(goal, observations)
    assert result['status'] == 'unknown'
    assert not result['is_opportunity_verified']
    assert condition(result, 'availability')['status'] == 'unknown'
    assert '[13, 253]' in condition(result, 'total_within_budget')['reason']
    goal['criteria']['max_total_eur_cents'] = 268
    assert evaluate(goal, observations)['status'] == 'unsatisfied'


def test_partial_pieces_do_not_silently_round_to_package(basket):
    goal, observations = basket
    goal['criteria']['items'][0].update(unit='pcs', quantity=0.5)
    observations[0]['selling_unit'] = 'pcs'
    assert evaluate(goal, observations)['status'] == 'unknown'


@pytest.mark.parametrize('bound', ['max_combinations', 'max_items', 'max_observations'])
def test_bounds_fail_closed_instead_of_verifying_partial_search(basket, bound):
    goal, observations = basket
    observations += [offer('Coffee beans', 'kg', 1500, 'other.test')]
    result = evaluate(goal, observations, **{bound: 1})
    assert result['status'] == 'unknown'
    assert not result['is_opportunity_verified']
    assert result['candidate_observation_ids'] == []


def test_tie_break_is_deterministic(basket):
    goal, observations = basket
    same = offer('Coffee beans', 'kg', 1600, 'other.test')
    result = evaluate(goal, observations + [same])
    assert result == evaluate(goal, [same] + observations[::-1])
    expected = min(observations[1]['observation_id'], same['observation_id'])
    assert expected in result['candidate_observation_ids']


def test_duplicate_ids_and_item_names_are_not_double_counted(basket):
    goal, observations = basket
    assert evaluate(goal, observations + observations) == evaluate(goal, observations)
    with pytest.raises(ValueError, match='Conflicting'):
        evaluate(goal, observations + [dict(observations[0], unit_price_eur_cents=1)])
    goal['criteria']['items'].append(copy.deepcopy(goal['criteria']['items'][0]))
    with pytest.raises(ValueError, match='distinct'):
        evaluate(goal, observations)


def test_no_savings_percentage_for_noncomparable_baseline(basket):
    goal, observations = basket
    goal['criteria']['current_total_eur_cents'] = goal['criteria']['max_total_eur_cents']
    assert 'progress' not in evaluate(goal, observations)


def test_unverified_and_non_fi_do_not_verify(basket):
    goal, observations = basket
    goal['criteria']['delivery_country'] = 'SE'
    assert evaluate(goal, observations)['status'] == 'unknown'
    goal['criteria']['delivery_country'] = 'FI'
    observations[0]['evidence']['reliability'] = 'unverified'
    assert evaluate(goal, observations)['status'] == 'unknown'


def test_money_float_rejected_and_large_lines_exact(basket):
    goal, observations = basket
    observations[0]['unit_price_eur_cents'] = 115.0
    with pytest.raises(ValueError, match='integer EUR cents'):
        evaluate(goal, observations)
    assert line_cost(3, 2**54 + 1) == 3 * (2**54 + 1)


def test_no_observations_is_unknown(basket):
    result = evaluate(basket[0], [])
    assert result['status'] == 'unknown'
    assert result['candidate_observation_ids'] == []


def test_basket_persistence_publication_and_recapture_dedup(basket):
    from unittest.mock import Mock
    from test_persistence_outbox import MemoryDB
    from goal_evaluation.processing import EvaluationContext, Runtime
    from goal_evaluation.persistence import EvaluationSink, VerificationGuard
    from goal_evaluation.outbox import OutboxPublisher, PublicationGuard
    goal, observations = basket
    contracts = Contracts(ROOT / 'contracts/schemas')
    evaluator = RecurringProcurementEvaluator(contracts, normalized_cost_policy_confirmed=True)
    dispatcher = Dispatcher(contracts, {'recurring_procurement': evaluator})
    goals, evidence, broker = Mock(), Mock(), Mock()
    goals.runtime.return_value = Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active')
    goals.version.return_value = goal
    evidence.observation.side_effect = lambda oid, cid: next(o for o in observations if o['observation_id'] == oid)
    evidence.latest_for_source.side_effect = lambda sid, cid: [o for o in observations if o['source_id'] == sid]
    broker.publish_confirmed.return_value = True
    clock, store = lambda: NOW, MemoryDB()
    guard = VerificationGuard(contracts, dispatcher, goals, clock)
    sink = EvaluationSink(contracts, guard, evaluator.version)
    correlation = str(uuid5(NAMESPACE_URL, 'basket-correlation'))
    with store.transaction() as tx:
        sink(dispatcher.evaluate(goal, observations, NOW), EvaluationContext(goal, observations, correlation), tx)
    publisher = OutboxPublisher(contracts, store, broker,
        PublicationGuard(contracts, guard, goals, evidence), clock)
    assert publisher.run_once() and publisher.run_once()
    sent = [json.loads(c.args[2]) for c in broker.publish_confirmed.call_args_list]
    assert {e['event_type'] for e in sent} == {'goal.evaluated.v1', 'goal.opportunity_verified.v1'}
    recaptured = [dict(o, observation_id=str(uuid5(NAMESPACE_URL, o['observation_id'] + '/new')),
                      observed_at='2026-11-14T09:59:00Z') for o in observations]
    with store.transaction() as tx:
        sink(dispatcher.evaluate(goal, recaptured, NOW), EvaluationContext(goal, recaptured, correlation), tx)
    events = [row['event'] for row in store.data['outbox'].values()]
    assert sum(e['event_type'] == 'goal.opportunity_verified.v1' for e in events) == 1


def test_seller_limit_can_rule_out_all_complete_baskets(basket):
    goal, observations = basket
    goal['criteria']['max_distinct_sellers'] = 1
    result = evaluate(goal, observations)
    assert result['status'] == 'unsatisfied'
    assert condition(result, 'seller_limit')['status'] == 'unsatisfied'


def test_fixture_policy_confirmation_does_not_supply_missing_tax(basket):
    goal, observations = basket
    observations[0]['tax_eur_cents'] = None
    result = evaluate(goal, observations, confirmed=True)
    assert condition(result, 'tax_known')['status'] == 'unknown'
    assert not result['is_opportunity_verified']
