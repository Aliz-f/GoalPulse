"""Network-free consumer integration using explicitly injected mock adapters."""
import copy
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import ValidationError

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from goal_evaluation.consumer import EvidenceConsumer
from goal_evaluation.domain import Contracts, Dispatcher
from goal_evaluation.product import ProductPurchaseEvaluator
from goal_evaluation.processing import EvidenceProcessor, ReadRetry, RetryableUpstreamError, Runtime, Subscription

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def load(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


class TestStore:
    __test__ = False

    def __init__(self):
        self.inbox, self.results = set(), []

    @contextmanager
    def transaction(self):
        before = copy.deepcopy((self.inbox, self.results))
        try:
            yield self
        except BaseException:
            self.inbox, self.results = before
            raise

    def claim_event(self, event_id):
        if event_id in self.inbox:
            return False
        self.inbox.add(event_id)
        return True


@pytest.fixture
def rig():
    event, observation = load('event.evidence-updated.json'), load('observation.verified.fixture.json')
    goal = load('goal-spec.product.json')
    goal['criteria']['product'] = {'query': 'RTX 5070', 'match_policy': 'exact_model', 'condition': 'any'}
    subscription = Subscription(goal['goal_id'], goal['version'])
    runtime = Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active')
    subscriptions, goals, evidence, wait = Mock(), Mock(), Mock(), Mock()
    subscriptions.for_source.return_value = [subscription]
    subscriptions.sources_for_goal.return_value = [observation['source_id']]
    goals.runtime.return_value = runtime
    goals.version.return_value = goal
    evidence.observation.return_value = observation
    evidence.latest_for_source.return_value = [observation]
    contracts, store = Contracts(ROOT / 'contracts/schemas'), TestStore()
    dispatcher = Dispatcher(contracts, {'product_purchase': ProductPurchaseEvaluator(contracts)})
    def sink(result, context, transaction):
        transaction.results.append((result, context.correlation_id))
    processor = EvidenceProcessor(contracts, dispatcher, subscriptions, goals, evidence,
                                  sink, lambda: NOW, wait=wait)
    return SimpleNamespace(**locals(), consumer=EvidenceConsumer(contracts, store, processor))


def test_valid_flow_propagates_correlation_and_deduplicates(rig):
    assert rig.consumer.handle(rig.event)
    assert len(rig.store.results) == 1
    result, correlation = rig.store.results[0]
    assert result['is_opportunity_verified']
    assert correlation == rig.event['correlation_id']
    assert not rig.consumer.handle(rig.event)
    rig.subscriptions.for_source.assert_called_once_with(rig.observation['source_id'], correlation)
    assert all(call.args[-1] == correlation for call in rig.goals.mock_calls)
    assert all(call.args[-1] == correlation for call in rig.evidence.mock_calls)


def test_two_active_goals_shared_source(rig):
    second_goal = copy.deepcopy(rig.goal)
    second_goal['goal_id'] = 'ae000000-0000-4000-8000-000000000099'
    second = Subscription(second_goal['goal_id'], 1)
    rig.subscriptions.for_source.return_value = [second, rig.subscription, second]
    rig.goals.runtime.side_effect = lambda gid, correlation: Runtime(gid, rig.goal['owner_user_id'], 1, 'active')
    rig.goals.version.side_effect = lambda sub, correlation: second_goal if sub == second else rig.goal
    assert rig.consumer.handle(rig.event)
    assert {r['goal_id'] for r, _ in rig.store.results} == {rig.goal['goal_id'], second.goal_id}
    assert len(rig.store.results) == 2
    rig.evidence.latest_for_source.assert_called_once()


@pytest.mark.parametrize('lifecycle,version', [('paused', 1), ('completed', 1), ('cancelled', 1), ('active', 2)])
def test_initial_ineligible_goal_skipped(rig, lifecycle, version):
    rig.goals.runtime.return_value = Runtime(rig.goal['goal_id'], rig.goal['owner_user_id'], version, lifecycle)
    assert rig.consumer.handle(rig.event)
    assert not rig.store.results
    rig.goals.version.assert_not_called()
    rig.evidence.observation.assert_not_called()


@pytest.mark.parametrize('lifecycle,version', [('paused', 1), ('completed', 1), ('cancelled', 1), ('active', 2)])
def test_change_during_evaluation_suppresses_result(rig, lifecycle, version):
    rig.goals.runtime.side_effect = [rig.runtime, Runtime(rig.goal['goal_id'], rig.goal['owner_user_id'], version, lifecycle)]
    rig.consumer.handle(rig.event)
    assert not rig.store.results


def test_owner_change_suppresses_result(rig):
    rig.goals.runtime.side_effect = [rig.runtime, Runtime(rig.goal['goal_id'],
        'ae000000-0000-4000-8000-000000000098', 1, 'active')]
    rig.consumer.handle(rig.event)
    assert not rig.store.results


@pytest.mark.parametrize('field,value', [
    ('owner_user_id', 'ae000000-0000-4000-8000-000000000098'),
    ('goal_id', 'ae000000-0000-4000-8000-000000000098'), ('version', 2),
])
def test_immutable_goal_mismatch_rolls_back(rig, field, value):
    rig.goal[field] = value
    with pytest.raises(ValueError, match='mismatch'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox and not rig.store.results


def test_wrong_runtime_id_rejected(rig):
    rig.goals.runtime.return_value = Runtime('ae000000-0000-4000-8000-000000000098',
                                             rig.goal['owner_user_id'], 1, 'active')
    with pytest.raises(ValueError, match='Runtime'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


@pytest.mark.parametrize('field', ['source_id', 'observation_id'])
def test_trigger_identity_mismatch(rig, field):
    rig.observation[field] = 'ae000000-0000-4000-8000-000000000098'
    with pytest.raises(ValueError):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


def test_unlinked_trigger_fails_closed(rig):
    rig.subscriptions.sources_for_goal.return_value = []
    with pytest.raises(ValueError, match='not linked'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


def test_latest_snapshot_prevents_old_offer_verification(rig):
    latest = copy.deepcopy(rig.observation)
    latest.update(observation_id='ae000000-0000-4000-8000-000000000098',
                  observed_at='2026-11-14T10:00:00Z', availability='out_of_stock')
    rig.observation['observed_at'] = '2026-11-14T09:59:00Z'
    rig.evidence.latest_for_source.return_value = [latest, rig.observation]
    rig.consumer.handle(rig.event)
    result = rig.store.results[0][0]
    assert result['status'] == 'unsatisfied'
    assert result['candidate_observation_ids'] == [latest['observation_id']]


def test_stale_cache_not_refreshed_by_event_time(rig):
    rig.observation['observed_at'] = '2026-11-14T08:00:00Z'
    rig.consumer.handle(rig.event)
    assert rig.store.results[0][0]['status'] == 'unknown'


def test_transient_read_retries_with_bounded_backoff(rig):
    rig.goals.version.side_effect = [RetryableUpstreamError(), RetryableUpstreamError(), rig.goal]
    rig.consumer.handle(rig.event)
    assert rig.goals.version.call_count == 3
    assert [c.args[0] for c in rig.wait.call_args_list] == [0.1, 0.2]
    assert len(rig.store.results) == 1


def test_exhausted_read_rolls_back_and_redelivery_can_succeed(rig):
    rig.goals.version.side_effect = RetryableUpstreamError()
    with pytest.raises(RetryableUpstreamError):
        rig.consumer.handle(rig.event)
    assert rig.goals.version.call_count == 3
    assert not rig.store.inbox
    rig.goals.version.side_effect = None
    assert rig.consumer.handle(rig.event)


def test_invalid_schema_never_retried(rig):
    del rig.goal['actions']
    with pytest.raises(ValidationError):
        rig.consumer.handle(rig.event)
    rig.goals.version.assert_called_once()
    rig.wait.assert_not_called()
    assert not rig.store.inbox


def test_sink_failure_rolls_back_all_goals(rig):
    second_goal = copy.deepcopy(rig.goal)
    second_goal['goal_id'] = 'ae000000-0000-4000-8000-000000000099'
    second = Subscription(second_goal['goal_id'], 1)
    rig.subscriptions.for_source.return_value = [rig.subscription, second]
    rig.goals.runtime.side_effect = lambda gid, cid: Runtime(gid, rig.goal['owner_user_id'], 1, 'active')
    rig.goals.version.side_effect = lambda sub, cid: second_goal if sub == second else rig.goal
    original_sink = rig.processor.sink
    def failing_sink(result, correlation, transaction):
        original_sink(result, correlation, transaction)
        if result['goal_id'] == second.goal_id:
            raise RuntimeError('storage failure')
    rig.processor.sink = failing_sink
    with pytest.raises(RuntimeError):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox and not rig.store.results
    rig.processor.sink = original_sink
    assert rig.consumer.handle(rig.event)
    assert len(rig.store.results) == 2


def test_wrong_source_and_conflicting_evidence_rejected(rig):
    bad = copy.deepcopy(rig.observation)
    bad['source_id'] = 'ae000000-0000-4000-8000-000000000099'
    rig.evidence.latest_for_source.return_value = [bad]
    with pytest.raises(ValueError, match='different source'):
        rig.consumer.handle(rig.event)
    bad = dict(rig.observation, availability='unknown')
    rig.evidence.latest_for_source.return_value = [rig.observation, bad]
    with pytest.raises(ValueError, match='Conflicting'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


def test_result_bounds_do_not_silently_drop_goals(rig):
    rig.processor.max_entries = 1
    rig.subscriptions.for_source.return_value = [rig.subscription, rig.subscription]
    with pytest.raises(ValueError, match='bound'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


def test_no_subscribers_commits_noop(rig):
    rig.subscriptions.for_source.return_value = []
    rig.consumer.handle(rig.event)
    assert not rig.store.results
    rig.goals.runtime.assert_not_called()


@pytest.mark.parametrize('attempts,delay', [(0, 0), (6, 0), (2, -1), (2, float('nan'))])
def test_invalid_retry_policy(attempts, delay):
    with pytest.raises(ValueError):
        ReadRetry(attempts, delay)


def test_bad_local_adapter_values_rejected():
    with pytest.raises(ValueError):
        Subscription('bad-id', 1)
    with pytest.raises(ValueError):
        Subscription('ae000000-0000-4000-8000-000000000099', True)
    with pytest.raises(ValueError):
        Runtime('ae000000-0000-4000-8000-000000000099',
                'ae000000-0000-4000-8000-000000000098', 1, 'invented')


def test_all_linked_sources_supply_alternative_offers(rig):
    other = copy.deepcopy(rig.observation)
    other.update(source_id='ae000000-0000-4000-8000-000000000097',
                 observation_id='ae000000-0000-4000-8000-000000000098',
                 unit_price_eur_cents=50000)
    rig.subscriptions.sources_for_goal.return_value = [other['source_id'], rig.observation['source_id']]
    rig.evidence.latest_for_source.side_effect = lambda source, cid: [other] if source == other['source_id'] else [rig.observation]
    rig.consumer.handle(rig.event)
    assert rig.store.results[0][0]['candidate_observation_ids'] == [other['observation_id']]
    assert rig.evidence.latest_for_source.call_count == 2


def test_missing_linked_source_evidence_fails_closed(rig):
    rig.evidence.latest_for_source.return_value = []
    with pytest.raises(ValueError, match='no evidence'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox and not rig.store.results


def test_trigger_snapshot_conflict_fails_closed(rig):
    rig.evidence.latest_for_source.return_value = [dict(rig.observation, unit_price_eur_cents=1)]
    with pytest.raises(ValueError, match='snapshot differs'):
        rig.consumer.handle(rig.event)
    assert not rig.store.inbox


def test_nontransient_failure_is_not_retried(rig):
    rig.goals.version.side_effect = PermissionError('access denied')
    with pytest.raises(PermissionError):
        rig.consumer.handle(rig.event)
    assert rig.goals.version.call_count == 1
    rig.wait.assert_not_called()
    assert not rig.store.inbox
