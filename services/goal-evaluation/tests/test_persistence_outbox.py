"""Transaction/fault scenarios with an in-memory DB double and fake broker.

These do not establish PostgreSQL or RabbitMQ operational compatibility.
"""
import copy
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from goal_evaluation.domain import Contracts, Dispatcher
from goal_evaluation.product import ProductPurchaseEvaluator
from goal_evaluation.processing import EvaluationContext, Runtime
from goal_evaluation.persistence import (
    EvaluationSink, VerificationGuard, PostgresEvaluationTransaction,
    evaluation_fingerprint, offer_fingerprint,
)
from goal_evaluation.outbox import OutboxPublisher, PublicationGuard

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def load(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


class MemoryDB:
    def __init__(self):
        self.data = dict(evaluations={}, fingerprints=set(), offers=set(), outbox={}, inbox=set(),
                         offer_events={}, confirmed=set(), evaluator_versions={})
        self.fail_commit = False
        self.fail_enqueue = False

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.data)
        try:
            yield self
            if self.fail_commit:
                raise RuntimeError('commit failure')
        except BaseException:
            self.data = before
            raise

    def claim_event(self, event_id):
        if event_id in self.data['inbox']:
            return False
        self.data['inbox'].add(event_id)
        return True

    def insert_evaluation(self, result, fingerprint, version):
        if fingerprint in self.data['fingerprints']:
            return False
        if result['evaluation_id'] in self.data['evaluations']:
            raise ValueError('immutable ID collision')
        self.data['evaluations'][result['evaluation_id']] = copy.deepcopy(result)
        self.data['evaluator_versions'][result['evaluation_id']] = version
        self.data['fingerprints'].add(fingerprint)
        return True

    def reserve_offer(self, result, fingerprint, event_id):
        key = (result['goal_id'], result['goal_version'], fingerprint)
        if key in self.data['offers']:
            previous = self.data['offer_events'][key]
            if key in self.data['confirmed'] or self.data['outbox'][previous]['status'] != 'failed':
                return False
        self.data['offers'].add(key)
        self.data['offer_events'][key] = event_id
        return True

    def legacy_offer_reserved(self, result, fingerprint):
        key = (result['goal_id'], result['goal_version'], fingerprint)
        if key not in self.data['offers']:
            return False
        event_id = self.data['offer_events'].get(key)
        return (key in self.data['confirmed'] or event_id is None
                or self.data['outbox'][event_id]['status'] != 'failed')

    def enqueue_event(self, event):
        if self.fail_enqueue:
            raise RuntimeError('outbox insert failure')
        self.data['outbox'][event['event_id']] = dict(event=copy.deepcopy(event), status='pending',
                                                    attempts=0, due=NOW)

    def get_evaluation(self, evaluation_id):
        return copy.deepcopy(self.data['evaluations'].get(evaluation_id))

    def list_evaluations(self, goal_id, goal_version, limit):
        rows = [copy.deepcopy(result) for result in self.data['evaluations'].values()
                if (result['goal_id'], result['goal_version']) == (goal_id, goal_version)]
        rows.sort(key=lambda result: (result['evaluated_at'], result['evaluation_id']), reverse=True)
        return rows[:limit]

    def next_pending(self, now):
        for event_id, row in sorted(self.data['outbox'].items()):
            if row['status'] == 'pending' and row['due'] <= now:
                return event_id, copy.deepcopy(row['event']), row['attempts']
        return None

    def mark_published(self, event_id, now):
        row = self.data['outbox'][event_id]
        row.update(status='published', attempts=row['attempts'] + 1, published_at=now)
        self.data['confirmed'].update(key for key, eid in self.data['offer_events'].items() if eid == event_id)

    def mark_failed(self, event_id, attempts, retry_at, terminal, reason):
        self.data['outbox'][event_id].update(status='failed' if terminal else 'pending',
                                             attempts=attempts, due=retry_at, reason=reason)


@pytest.fixture
def setup():
    contracts = Contracts(ROOT / 'contracts/schemas')
    goal = load('goal-spec.product.json')
    goal['criteria']['product'] = {'query': 'RTX 5070', 'match_policy': 'exact_model', 'condition': 'any'}
    observation = load('observation.verified.fixture.json')
    clock = Mock(return_value=NOW)
    dispatcher = Dispatcher(contracts, {'product_purchase': ProductPurchaseEvaluator(contracts)})
    result = dispatcher.evaluate(goal, [observation], NOW)
    context = EvaluationContext(goal, [observation], str(uuid4()))
    goals, evidence = Mock(), Mock()
    goals.runtime.return_value = Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active')
    goals.version.return_value = goal
    evidence.observation.return_value = observation
    evidence.latest_for_source.return_value = [observation]
    guard = VerificationGuard(contracts, dispatcher, goals, clock)
    sink = EvaluationSink(contracts, guard, ProductPurchaseEvaluator.version)
    store, broker = MemoryDB(), Mock()
    broker.publish_confirmed.return_value = True
    publication_guard = PublicationGuard(contracts, guard, goals, evidence)
    publisher = OutboxPublisher(contracts, store, broker, publication_guard, clock)
    return SimpleNamespace(**locals())


def save(s):
    with s.store.transaction() as tx:
        s.sink(s.result, s.context, tx)


def events(s):
    return [row['event'] for row in s.store.data['outbox'].values()]


def verified_events(s):
    return [event for event in events(s) if event['event_type'] == 'goal.opportunity_verified.v1']


def test_atomic_evaluation_and_two_valid_events(setup):
    s = setup
    save(s)
    assert len(s.store.data['evaluations']) == 1
    assert {e['event_type'] for e in events(s)} == {'goal.evaluated.v1', 'goal.opportunity_verified.v1'}
    for event in events(s):
        s.contracts.validate('domain-event', event)
        assert event['correlation_id'] == s.context.correlation_id
        assert event['aggregate_id'] == s.goal['goal_id']
        assert event['payload']['evaluation_id'] == s.result['evaluation_id']


def test_same_evaluation_and_same_offer_recapture_dedup(setup):
    s = setup
    save(s)
    save(s)
    assert len(events(s)) == 2
    newer = copy.deepcopy(s.observation)
    newer.update(observation_id=str(uuid4()), observed_at='2026-11-14T09:59:00Z')
    result = s.dispatcher.evaluate(s.goal, [newer], NOW)
    context = EvaluationContext(s.goal, [newer], str(uuid4()))
    with s.store.transaction() as tx:
        s.sink(result, context, tx)
    assert len(s.store.data['evaluations']) == 2
    assert len(events(s)) == 3
    assert len(verified_events(s)) == 1


def test_re_evaluation_time_only_is_not_meaningful(setup):
    s = setup
    save(s)
    result = s.dispatcher.evaluate(s.goal, [s.observation], NOW + timedelta(seconds=1))
    assert result['evaluation_id'] != s.result['evaluation_id']
    assert evaluation_fingerprint(result, 'v1') == evaluation_fingerprint(s.result, 'v1')
    s.clock.return_value = NOW + timedelta(seconds=1)
    with s.store.transaction() as tx:
        s.sink(result, s.context, tx)
    assert len(events(s)) == 2


def test_changed_price_is_new_offer(setup):
    s = setup
    save(s)
    changed = dict(s.observation, observation_id=str(uuid4()), unit_price_eur_cents=57000)
    result = s.dispatcher.evaluate(s.goal, [changed], NOW)
    with s.store.transaction() as tx:
        s.sink(result, EvaluationContext(s.goal, [changed], s.context.correlation_id), tx)
    assert len(verified_events(s)) == 2


def test_offer_identity_goal_version_scoped_and_capture_independent(setup):
    s = setup
    original = offer_fingerprint(s.result, s.context)
    observation = dict(s.observation, observation_id=str(uuid4()), observed_at='2026-11-14T09:59:00Z',
                       provider_request_id='new-capture')
    result = s.dispatcher.evaluate(s.goal, [observation], NOW)
    assert offer_fingerprint(result, EvaluationContext(s.goal, [observation], 'unused')) == original
    changed_goal = dict(s.goal, version=2)
    changed_result = s.dispatcher.evaluate(changed_goal, [observation], NOW)
    assert offer_fingerprint(changed_result, EvaluationContext(changed_goal, [observation], 'unused')) != original


@pytest.mark.parametrize('fault', ['fail_enqueue', 'fail_commit'])
def test_storage_fault_rolls_back_inbox_evaluation_and_outbox(setup, fault):
    s = setup
    setattr(s.store, fault, True)
    with pytest.raises(RuntimeError):
        with s.store.transaction() as tx:
            tx.claim_event('input-event')
            s.sink(s.result, s.context, tx)
    assert not any(s.store.data.values())
    setattr(s.store, fault, False)
    save(s)
    assert len(events(s)) == 2


@pytest.mark.parametrize('change', ['paused', 'version', 'owner', 'stale'])
def test_enqueue_guard_suppresses_verification(setup, change):
    s = setup
    if change == 'stale':
        s.clock.return_value = NOW + timedelta(hours=2)
    else:
        s.goals.runtime.return_value = Runtime(s.goal['goal_id'],
            str(uuid4()) if change == 'owner' else s.goal['owner_user_id'],
            2 if change == 'version' else 1, 'paused' if change == 'paused' else 'active')
    save(s)
    assert len(events(s)) == 1
    assert not verified_events(s)
    assert not s.store.data['offers']


def test_unknown_evaluation_persisted_but_never_verified(setup):
    s = setup
    s.observation['shipping_eur_cents'] = None
    s.result = s.dispatcher.evaluate(s.goal, [s.observation], NOW)
    save(s)
    assert len(events(s)) == 1
    assert not verified_events(s)


def test_sink_rejects_missing_evidence_and_duplicate_codes(setup):
    s = setup
    with pytest.raises(ValueError, match='missing evidence'):
        with s.store.transaction() as tx:
            s.sink(s.result, EvaluationContext(s.goal, [], s.context.correlation_id), tx)
    s.result['conditions'].append(copy.deepcopy(s.result['conditions'][0]))
    with pytest.raises(ValueError, match='Duplicate condition'):
        save(s)
    assert not any(s.store.data.values())


def test_confirmed_publish_persistent_exact_envelope(setup):
    s = setup
    save(s)
    assert s.publisher.run_once()
    assert s.publisher.run_once()
    assert not s.publisher.run_once()
    assert s.broker.publish_confirmed.call_count == 2
    for call in s.broker.publish_confirmed.call_args_list:
        assert call.args[0] == 'goalpulse.events'
        assert call.kwargs['persistent'] is True
        event = json.loads(call.args[2])
        assert call.args[1] == event['event_type']
        s.contracts.validate('domain-event', event)
    assert all(row['status'] == 'published' for row in s.store.data['outbox'].values())


def test_no_confirm_bounded_retry_and_recovery(setup):
    s = setup
    save(s)
    s.broker.publish_confirmed.return_value = False
    s.publisher.max_attempts = 2
    assert s.publisher.run_once()
    row = next(row for row in s.store.data['outbox'].values() if row['attempts'] == 1)
    assert row['status'] == 'pending'
    assert row['due'] == NOW + timedelta(seconds=1)
    s.clock.return_value = NOW + timedelta(seconds=1)
    # Process both rows until their bounded retry policy is exhausted.
    for _ in range(4):
        s.publisher.run_once()
    assert any(row['status'] == 'failed' for row in s.store.data['outbox'].values())
    assert s.broker.publish_confirmed.call_count <= 4


def test_broker_restart_keeps_pending_event_identity(setup):
    s = setup
    save(s)
    s.broker.publish_confirmed.side_effect = ConnectionError('broker stopped')
    s.publisher.run_once()
    sent = json.loads(s.broker.publish_confirmed.call_args.args[2])
    s.broker.publish_confirmed.side_effect = None
    s.clock.return_value = NOW + timedelta(seconds=2)
    for _ in range(3):
        s.publisher.run_once()
    published_ids = [json.loads(c.args[2])['event_id'] for c in s.broker.publish_confirmed.call_args_list]
    assert published_ids.count(sent['event_id']) == 2
    assert all(row['status'] == 'published' for row in s.store.data['outbox'].values())


def test_confirm_then_db_commit_failure_replays_same_id(setup):
    s = setup
    save(s)
    s.store.fail_commit = True
    with pytest.raises(RuntimeError, match='commit failure'):
        s.publisher.run_once()
    first = s.broker.publish_confirmed.call_args.args[2]
    s.store.fail_commit = False
    s.publisher.run_once()
    assert s.broker.publish_confirmed.call_args.args[2] == first


@pytest.mark.parametrize('change', ['paused', 'stale', 'newer_evidence'])
def test_queued_verification_suppressed_before_publish(setup, change):
    s = setup
    save(s)
    if change == 'paused':
        s.goals.runtime.return_value = Runtime(s.goal['goal_id'], s.goal['owner_user_id'], 1, 'paused')
    elif change == 'stale':
        s.clock.return_value = NOW + timedelta(hours=2)
    else:
        s.evidence.latest_for_source.return_value = [dict(s.observation, observation_id=str(uuid4()),
            observed_at='2026-11-14T10:00:01Z', availability='out_of_stock')]
    for _ in range(3):
        s.publisher.run_once()
    sent = [json.loads(c.args[2]) for c in s.broker.publish_confirmed.call_args_list]
    assert len(sent) == 1 and sent[0]['event_type'] == 'goal.evaluated.v1'
    assert any(row.get('reason') == 'verification_suppressed' for row in s.store.data['outbox'].values())


def test_malformed_outbox_never_published(setup):
    s = setup
    save(s)
    for row in s.store.data['outbox'].values():
        row['event']['producer'] = 'data-evidence'
    s.publisher.run_once()
    s.publisher.run_once()
    s.broker.publish_confirmed.assert_not_called()
    assert all(row['status'] == 'failed' for row in s.store.data['outbox'].values())


def test_postgres_parameterized_writes_and_immutable_reads(setup):
    s = setup
    cursor = Mock()
    cursor.fetchone.return_value = ('inserted',)
    tx = PostgresEvaluationTransaction(cursor)
    assert tx.insert_evaluation(s.result, 'fingerprint', 'product-v1.1')
    assert cursor.execute.call_count == 1 + len(s.result['conditions'])
    assert tx.reserve_offer(s.result, 'offer', s.result['evaluation_id'])
    save(s)
    tx.enqueue_event(events(s)[0])
    cursor.fetchone.return_value = (copy.deepcopy(s.result),)
    fetched = tx.get_evaluation(s.result['evaluation_id'])
    fetched['conditions'].clear()
    assert cursor.fetchone.return_value[0] == s.result
    cursor.fetchone.return_value = None
    assert tx.get_evaluation(s.result['evaluation_id']) is None
    assert not tx.insert_evaluation(s.result, 'fingerprint', 'product-v1.1')
    assert not tx.reserve_offer(s.result, 'offer', s.result['evaluation_id'])
    for call in cursor.execute.call_args_list:
        assert len(call.args) == 2  # every SQL statement uses bound parameters
        assert 'UPDATE evaluations' not in call.args[0]
        assert 'DELETE FROM evaluations' not in call.args[0]


def test_outbox_sql_skips_locked_and_tracks_terminal_failures():
    cursor = Mock()
    cursor.fetchone.return_value = None
    tx = PostgresEvaluationTransaction(cursor)
    assert tx.next_pending(NOW) is None
    pending_sql = cursor.execute.call_args.args[0]
    assert 'FOR UPDATE SKIP LOCKED' in pending_sql
    assert 'attempts=0 OR next_retry_at<=%s' in pending_sql
    tx.mark_failed('event', 5, NOW, True, 'publish_or_read_failed')
    assert cursor.execute.call_args.args[1][0] == 'failed'
    tx.mark_published('event', NOW)
    assert 'published' in cursor.execute.call_args.args[0]


def test_full_processor_consumer_sink_transaction_and_replay(setup):
    from goal_evaluation.consumer import EvidenceConsumer
    from goal_evaluation.processing import EvidenceProcessor, Subscription
    s = setup
    subscriptions = Mock()
    subscriptions.for_source.return_value = [Subscription(s.goal['goal_id'], 1)]
    subscriptions.sources_for_goal.return_value = [s.observation['source_id']]
    processor = EvidenceProcessor(s.contracts, s.dispatcher, subscriptions, s.goals,
                                  s.evidence, s.sink, s.clock)
    consumer = EvidenceConsumer(s.contracts, s.store, processor)
    event = load('event.evidence-updated.json')
    assert consumer.handle(event)
    assert not consumer.handle(event)
    assert len(s.store.data['evaluations']) == 1
    assert len(s.store.data['inbox']) == 1
    assert len(events(s)) == 2
    s.publisher.run_once()
    s.publisher.run_once()
    assert s.broker.publish_confirmed.call_count == 2


def test_validated_immutable_read_boundary(setup):
    from goal_evaluation.persistence import ImmutableEvaluationReader
    s = setup
    reader = ImmutableEvaluationReader(s.contracts, s.store)
    assert reader.get(str(uuid4())) is None
    save(s)
    result = reader.get(s.result['evaluation_id'])
    assert result == s.result
    result['conditions'].clear()
    assert reader.get(s.result['evaluation_id']) == s.result
    with pytest.raises(ValueError):
        reader.get('invalid')
    s.store.data['evaluations'][s.result['evaluation_id']]['evaluation_id'] = str(uuid4())
    with pytest.raises(ValueError, match='identity mismatch'):
        reader.get(s.result['evaluation_id'])


def test_permission_failure_terminal_and_no_secret_in_error_metadata(setup):
    s = setup
    save(s)
    s.broker.publish_confirmed.side_effect = PermissionError('secret broker credentials')
    s.publisher.run_once()
    row = next(row for row in s.store.data['outbox'].values() if row['attempts'])
    assert row['status'] == 'failed'
    assert row['reason'] == 'invalid_or_unauthorized'
    assert 'secret' not in row['reason']


# D4-GE-06: regression cases across the final verification boundary.
def test_runtime_latency_cannot_make_expired_evidence_publishable(setup):
    s = setup
    s.clock.return_value = NOW + timedelta(minutes=59, seconds=59)
    def slow_runtime(*args):
        s.clock.return_value = NOW + timedelta(minutes=61)
        return Runtime(s.goal['goal_id'], s.goal['owner_user_id'], 1, 'active')
    s.goals.runtime.side_effect = slow_runtime
    save(s)
    assert not verified_events(s)


def test_immutable_read_wrong_evaluation_id_never_verifies(setup):
    s = setup
    save(s)
    event = verified_events(s)[0]
    wrong = dict(s.result, evaluation_id=str(uuid4()))
    tx = Mock()
    tx.get_evaluation.return_value = wrong
    with pytest.raises(ValueError, match='identity mismatch'):
        s.publication_guard(event, tx)


def test_same_model_case_whitespace_does_not_create_new_offer(setup):
    s = setup
    first = offer_fingerprint(s.result, s.context)
    changed = dict(s.observation, product_name='  graphics   CARD RTX 5070 ',
                   model=' rtx   5070 ', brand=' EXAMPLE ')
    context = EvaluationContext(s.goal, [changed], s.context.correlation_id)
    assert offer_fingerprint(s.result, context) == first


@pytest.mark.parametrize('lifecycle', ['paused', 'completed', 'cancelled'])
def test_delayed_queued_event_terminal_lifecycle_suppressed(setup, lifecycle):
    s = setup
    save(s)
    s.goals.runtime.return_value = Runtime(s.goal['goal_id'], s.goal['owner_user_id'], 1, lifecycle)
    for _ in range(3):
        s.publisher.run_once()
    assert all(json.loads(c.args[2])['event_type'] != 'goal.opportunity_verified.v1'
               for c in s.broker.publish_confirmed.call_args_list)


@pytest.mark.parametrize('identity_change', ['owner', 'version'])
def test_delayed_queued_event_owner_or_version_changed(setup, identity_change):
    s = setup
    save(s)
    s.goals.runtime.return_value = Runtime(s.goal['goal_id'],
        str(uuid4()) if identity_change == 'owner' else s.goal['owner_user_id'],
        2 if identity_change == 'version' else 1, 'active')
    for _ in range(3):
        s.publisher.run_once()
    assert all(json.loads(c.args[2])['event_type'] != 'goal.opportunity_verified.v1'
               for c in s.broker.publish_confirmed.call_args_list)


def test_publication_guard_transient_read_retried_without_reserving_new_offer(setup):
    from goal_evaluation.processing import RetryableUpstreamError
    s = setup
    save(s)
    # Isolate the verified row so retry timing is not affected by other events.
    s.store.data['outbox'] = {k: v for k, v in s.store.data['outbox'].items()
                              if v['event']['event_type'] == 'goal.opportunity_verified.v1'}
    s.evidence.observation.side_effect = RetryableUpstreamError('temporary provider problem')
    s.publisher.run_once()
    row = next(iter(s.store.data['outbox'].values()))
    assert row['status'] == 'pending' and row['attempts'] == 1
    s.broker.publish_confirmed.assert_not_called()
    s.evidence.observation.side_effect = None
    s.clock.return_value = NOW + timedelta(seconds=1)
    s.publisher.run_once()
    assert row['status'] == 'published'
    assert len(s.store.data['offers']) == 1


def test_unroutable_or_unconfirmed_failure_never_marks_published(setup):
    s = setup
    save(s)
    s.publisher.max_attempts = 3
    s.store.data['outbox'] = {k: v for k, v in s.store.data['outbox'].items()
                              if v['event']['event_type'] == 'goal.evaluated.v1'}
    s.broker.publish_confirmed.return_value = False
    for seconds in [0, 1, 3, 20]:
        s.clock.return_value = NOW + timedelta(seconds=seconds)
        s.publisher.run_once()
    row = next(iter(s.store.data['outbox'].values()))
    assert row['status'] == 'failed' and row['attempts'] == 3
    assert 'published_at' not in row
    assert s.broker.publish_confirmed.call_count == 3


def test_pending_backoff_prevents_immediate_busy_retry(setup):
    s = setup
    save(s)
    s.store.data['outbox'] = {k: v for k, v in s.store.data['outbox'].items()
                              if v['event']['event_type'] == 'goal.evaluated.v1'}
    s.broker.publish_confirmed.return_value = False
    assert s.publisher.run_once()
    assert not s.publisher.run_once()
    assert s.broker.publish_confirmed.call_count == 1


def test_retry_due_time_is_measured_after_slow_failed_publish(setup):
    s = setup
    save(s)
    s.store.data['outbox'] = {k: v for k, v in s.store.data['outbox'].items()
                              if v['event']['event_type'] == 'goal.evaluated.v1'}
    def slow_failure(*args, **kwargs):
        s.clock.return_value = NOW + timedelta(seconds=30)
        raise TimeoutError('publish timed out')
    s.broker.publish_confirmed.side_effect = slow_failure
    s.publisher.run_once()
    row = next(iter(s.store.data['outbox'].values()))
    assert row['due'] == NOW + timedelta(seconds=31)
    assert not s.publisher.run_once()


@pytest.mark.parametrize('change', ['unknown_stock', 'unknown_shipping', 'unknown_tax', 'unverified', 'future_time'])
def test_publisher_revalidates_incomplete_evidence_before_broker_call(setup, change):
    s = setup
    save(s)
    if change == 'unknown_stock':
        s.observation['availability'] = 'unknown'
    elif change == 'unknown_shipping':
        s.observation['shipping_eur_cents'] = None
    elif change == 'unknown_tax':
        s.observation['tax_eur_cents'] = None
    elif change == 'unverified':
        s.observation['evidence']['reliability'] = 'unverified'
    else:
        s.observation['observed_at'] = '2026-11-14T10:00:01Z'
    for _ in range(3):
        s.publisher.run_once()
    assert all(json.loads(c.args[2])['event_type'] != 'goal.opportunity_verified.v1'
               for c in s.broker.publish_confirmed.call_args_list)


def test_unchanged_capture_before_relay_publishes_one_verification(setup):
    s = setup
    save(s)
    recaptured = dict(s.observation, observation_id=str(uuid4()), observed_at='2026-11-14T10:00:01Z')
    s.clock.return_value = NOW + timedelta(seconds=2)
    s.evidence.latest_for_source.return_value = [recaptured]
    old = s.observation
    s.evidence.observation.side_effect = lambda oid, cid: old if oid == old['observation_id'] else recaptured
    result = s.dispatcher.evaluate(s.goal, [recaptured], s.clock())
    with s.store.transaction() as tx:
        s.sink(result, EvaluationContext(s.goal, [recaptured], s.context.correlation_id), tx)
    for _ in range(4):
        s.publisher.run_once()
    sent = [json.loads(call.args[2]) for call in s.broker.publish_confirmed.call_args_list]
    verified = [e for e in sent if e['event_type'] == 'goal.opportunity_verified.v1']
    assert len(verified) == 1
    assert verified[0]['payload']['evaluation_id'] == s.result['evaluation_id']
    assert len(s.store.data['offers']) == len(s.store.data['confirmed']) == 1


def test_terminal_unconfirmed_reservation_can_recover_on_fresh_capture(setup):
    s = setup
    save(s)
    s.clock.return_value = NOW + timedelta(hours=2)
    for _ in range(3):
        s.publisher.run_once()
    assert not s.store.data['confirmed']
    fresh = dict(s.observation, observation_id=str(uuid4()), observed_at='2026-11-14T12:00:00Z')
    s.evidence.observation.return_value = fresh
    s.evidence.latest_for_source.return_value = [fresh]
    result = s.dispatcher.evaluate(s.goal, [fresh], s.clock())
    with s.store.transaction() as tx:
        s.sink(result, EvaluationContext(s.goal, [fresh], s.context.correlation_id), tx)
    for _ in range(3):
        s.publisher.run_once()
    verified = [json.loads(c.args[2]) for c in s.broker.publish_confirmed.call_args_list
                if json.loads(c.args[2])['event_type'] == 'goal.opportunity_verified.v1']
    assert len(verified) == 1
    assert verified[0]['payload']['evaluation_id'] == result['evaluation_id']
    assert len(s.store.data['offers']) == len(s.store.data['confirmed']) == 1
    later = dict(fresh, observation_id=str(uuid4()))
    with s.store.transaction() as tx:
        s.sink(s.dispatcher.evaluate(s.goal, [later], s.clock()),
               EvaluationContext(s.goal, [later], s.context.correlation_id), tx)
    assert len(verified_events(s)) == 2  # original failed intent + one successful replacement


@pytest.mark.parametrize('change', ['price', 'stock', 'shipping', 'tax', 'reliability', 'future'])
def test_newer_changed_or_untrustworthy_capture_still_blocks_relay(setup, change):
    s = setup
    save(s)
    newer = copy.deepcopy(s.observation)
    newer.update(observation_id=str(uuid4()), observed_at='2026-11-14T10:00:01Z')
    if change == 'price': newer['unit_price_eur_cents'] += 1
    elif change == 'stock': newer['availability'] = 'out_of_stock'
    elif change == 'shipping': newer['shipping_eur_cents'] = None
    elif change == 'tax': newer['tax_eur_cents'] = None
    elif change == 'reliability': newer['evidence']['reliability'] = 'unverified'
    s.evidence.latest_for_source.return_value = [newer]
    s.clock.return_value = NOW if change == 'future' else NOW + timedelta(seconds=2)
    for _ in range(3): s.publisher.run_once()
    assert all(json.loads(c.args[2])['event_type'] != 'goal.opportunity_verified.v1'
               for c in s.broker.publish_confirmed.call_args_list)


def test_recapture_never_refreshes_expired_original_proof(setup):
    s = setup
    save(s)
    s.clock.return_value = NOW + timedelta(hours=2)
    s.evidence.latest_for_source.return_value = [dict(s.observation, observation_id=str(uuid4()),
                                                    observed_at='2026-11-14T12:00:00Z')]
    for _ in range(3): s.publisher.run_once()
    assert not s.store.data['confirmed']
    assert all(json.loads(c.args[2])['event_type'] != 'goal.opportunity_verified.v1'
               for c in s.broker.publish_confirmed.call_args_list)
