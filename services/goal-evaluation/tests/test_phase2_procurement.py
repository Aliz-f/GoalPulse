"""Recorded normalization, coverage and recurring runtime acceptance."""
from unittest.mock import Mock

import pytest

from procurement_recorded import ROOT, fixture, recorded_runtime
from procurement_recorded_smoke import smoke
from test_procurement import evaluate, condition
from goal_evaluation.procurement import RecurringProcurementEvaluator, line_cost
from goal_evaluation.runtime import build_runtime


def test_three_units_two_sellers_total_includes_shipping_once_and_line_tax():
    data = fixture()
    result = evaluate(data['goal'], data['observations'])
    assert result['is_opportunity_verified']
    # 115 + 1600 + 300 + (500 + 200) shipping + (2 + 3) tax.
    assert '2720 EUR cents' in condition(result, 'total_within_budget')['reason']
    assert '3/3' in condition(result, 'full_basket_coverage')['reason']


@pytest.mark.parametrize('change,code', [
    ('missing', 'full_basket_coverage'), ('shipping', 'shipping_known_by_seller'),
    ('tax', 'tax_known'), ('quality', 'quality'), ('unit', 'units_compatible'),
    ('quantity', 'availability'),
])
def test_missing_required_facts_remain_unknown(change, code):
    data = fixture()
    if change == 'missing': data['observations'].pop()
    elif change == 'shipping': data['observations'][0]['shipping_eur_cents'] = None
    elif change == 'tax': data['observations'][0]['tax_eur_cents'] = None
    elif change == 'quality': data['goal']['criteria']['items'][0]['requirements'] = ['organic']
    elif change == 'unit': data['observations'][0]['selling_unit'] = 'pcs'
    else:
        data['goal']['criteria']['items'][0]['quantity'] = 20
        # Isolate missing stock proof from a definite over-budget failure.
        data['goal']['criteria']['max_total_eur_cents'] = 10000
    result = evaluate(data['goal'], data['observations'])
    assert result['status'] == condition(result, code)['status'] == 'unknown'
    assert not result['is_opportunity_verified'] and 'progress' not in result
    if change == 'missing':
        assert '2/3' in condition(result, code)['reason']
        assert 'Coffee filters' in condition(result, code)['reason']
    if change == 'quality': assert 'organic' in condition(result, code)['reason']
    if change == 'quantity': assert '20 L' in condition(result, code)['reason']


def test_original_cafe_quality_and_stock_are_not_inferred_from_names_or_excerpts():
    data = fixture(original_cafe=True)
    for row in data['observations']:
        row['evidence']['text_excerpt'] = 'organic, lactose free, whole beans; unlimited stock'
    result = evaluate(data['goal'], data['observations'])
    assert result['status'] == 'unknown'
    assert condition(result, 'quality')['status'] == condition(result, 'availability')['status'] == 'unknown'
    assert 'progress' not in result


def test_worker_persists_procurement_version_and_replay_one_outcome():
    s = recorded_runtime(confirmed=True)
    s.runtime.worker.run_once()
    s.runtime.worker.run_once()
    assert len(s.store.data['evaluations']) == 1
    assert set(s.store.data['evaluator_versions'].values()) == {RecurringProcurementEvaluator.version}
    assert len(s.store.data['outbox']) == 2


def test_runtime_default_persists_unknown_without_verified_event():
    s = recorded_runtime()
    s.runtime.worker.run_once()
    result = next(iter(s.store.data['evaluations'].values()))
    assert result['status'] == 'unknown'
    assert [r['event']['event_type'] for r in s.store.data['outbox'].values()] == ['goal.evaluated.v1']


@pytest.mark.parametrize('profile', ['dev', 'demo'])
def test_live_normalization_cannot_be_enabled_before_d3_handoff(profile):
    broker = Mock()
    with pytest.raises(ValueError, match='approved D3 handoff'):
        build_runtime(connect_database=Mock(), broker_connection=broker, queue='test',
            dead_letter_exchange='', dead_letter_routing_key='test-dlq',
            schemas=ROOT / 'contracts/schemas', gm_origin='http://gm.internal',
            dm_origin='http://dm.internal', de_origin='http://de.internal',
            tokens=Mock(), profile=profile, sources_for_goal=lambda *args: [],
            dependencies_ready=lambda: True, procurement_normalization_confirmed=True)
    broker.channel.assert_not_called()


@pytest.mark.parametrize('price', [True, 1.5, -1])
def test_line_cost_requires_nonnegative_integer_cents(price):
    with pytest.raises(ValueError): line_cost(1, price)


def test_independent_recorded_worker_api_smoke():
    result = smoke()
    assert result['mode'] == 'recorded_test' and not result['live_deployment']
    assert result['normalized_single_unit']['evaluation']['is_opportunity_verified']
    assert result['original_cafe_request']['evaluation']['status'] == 'unknown'
