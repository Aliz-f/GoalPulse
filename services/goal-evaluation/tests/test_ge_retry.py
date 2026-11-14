"""Durable-attempt policy doubles; live DB/broker tests are opt-in separately."""

import copy
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_persistence_outbox import NOW, setup, load
from goal_evaluation.consumer import EvidenceConsumer
from goal_evaluation.processing import EvaluationContext
from goal_evaluation.retry import Decision, Lease, PostgresRetryTransaction, RetryCoordinator


class Journal:
    def __init__(self):
        self.rows, self.rejections = {}, []
        self.fail_finish = False
        self.finishing = False

    @contextmanager
    def transaction(self):
        before = copy.deepcopy((self.rows, self.rejections))
        try:
            yield self
            if self.finishing and self.fail_finish:
                raise RuntimeError('journal commit failed')
        except BaseException:
            self.rows, self.rejections = before
            raise
        finally:
            self.finishing = False

    def claim_processing(self, event_id, now, token, lease_seconds, max_attempts):
        row = self.rows.setdefault(event_id, dict(state='retry', attempts=0, due=now, token=None))
        if row['state'] in {'succeeded', 'dead_letter'}:
            return Lease('complete' if row['state'] == 'succeeded' else 'terminal', row['attempts'])
        if row['state'] == 'processing' and row['due'] > now:
            return Lease('busy', row['attempts'], due_at=row['due'])
        if row['state'] == 'retry' and row['due'] > now:
            return Lease('not_due', row['attempts'], due_at=row['due'])
        if row['attempts'] >= max_attempts:
            row.update(state='dead_letter', token=None)
            return Lease('terminal', row['attempts'])
        row.update(state='processing', attempts=row['attempts'] + 1, token=token,
                   due=now + timedelta(seconds=lease_seconds))
        return Lease('claimed', row['attempts'], token, row['due'])

    def finish_processing(self, event_id, token, state, due, reason):
        row = self.rows[event_id]
        if row['state'] != 'processing' or row['token'] != token:
            return False
        row.update(state=state, due=due, reason=reason, token=None)
        self.finishing = True
        return True

    def record_rejection(self, event_id, now):
        self.rejections.append((event_id, now))


def make(s, consumer=None, **kwargs):
    consumer = consumer or Mock(handle=Mock(return_value=True))
    journal = Journal()
    return RetryCoordinator(s.contracts, journal, consumer, s.clock, **kwargs), journal, consumer


def test_successful_redelivery_does_not_repeat_processing(setup):
    coordinator, journal, consumer = make(setup)
    event = load('event.evidence-updated.json')
    assert coordinator.run(event) == Decision('ack')
    assert coordinator.run(event) == Decision('ack')
    consumer.handle.assert_called_once()
    assert journal.rows[event['event_id']]['attempts'] == 1


def test_transient_failure_has_bounded_backoff_and_terminal_outcome(setup):
    s = setup
    coordinator, journal, consumer = make(s, max_attempts=3)
    event = load('event.evidence-updated.json')
    consumer.handle.side_effect = RuntimeError('sensitive connection details')
    for delay in (1, 2):
        assert coordinator.run(event) == Decision('retry', delay)
        s.clock.return_value += timedelta(seconds=delay)
    assert coordinator.run(event) == Decision('dead_letter')
    row = journal.rows[event['event_id']]
    assert row['attempts'] == 3 and row['reason'] == 'read_or_storage_failed'
    assert 'sensitive' not in repr(journal.rows)
    assert coordinator.run(event) == Decision('dead_letter')
    assert consumer.handle.call_count == 3


@pytest.mark.parametrize('error', [ValueError, PermissionError, NotImplementedError])
def test_permanent_failure_never_retries_forever(setup, error):
    coordinator, _, consumer = make(setup)
    consumer.handle.side_effect = error('permanent')
    assert coordinator.run(load('event.evidence-updated.json')) == Decision('dead_letter')
    consumer.handle.assert_called_once()


def test_invalid_event_is_durably_rejected_without_domain_processing(setup):
    coordinator, journal, consumer = make(setup)
    event = load('event.evidence-updated.json')
    event['aggregate_id'] = str(uuid4())
    assert coordinator.run(event) == Decision('dead_letter')
    assert len(journal.rejections) == 1
    assert not journal.rows
    consumer.handle.assert_not_called()


def test_active_lease_prevents_a_second_worker_from_processing(setup):
    coordinator, journal, consumer = make(setup)
    event = load('event.evidence-updated.json')
    journal.claim_processing(event['event_id'], NOW, str(uuid4()), 300, 5)
    assert coordinator.run(event) == Decision('retry', 300)
    consumer.handle.assert_not_called()


def test_domain_commit_followed_by_journal_failure_recovers_without_extra_events(setup):
    s = setup
    def process(event, transaction):
        s.sink(s.result, EvaluationContext(s.goal, [s.observation], event['correlation_id']), transaction)
    consumer = EvidenceConsumer(s.contracts, s.store, process)
    coordinator, journal, _ = make(s, consumer)
    event = load('event.evidence-updated.json')
    journal.fail_finish = True
    with pytest.raises(RuntimeError, match='journal commit'):
        coordinator.run(event)
    assert len(s.store.data['evaluations']) == 1
    assert len(s.store.data['outbox']) == 2
    journal.fail_finish = False
    s.clock.return_value += timedelta(seconds=301)
    restarted = RetryCoordinator(s.contracts, journal, consumer, s.clock)
    assert restarted.run(event) == Decision('ack')
    assert len(s.store.data['evaluations']) == 1
    assert len(s.store.data['outbox']) == 2


def test_sql_lease_is_locked_and_completion_is_token_scoped():
    cursor = Mock()
    tx = PostgresRetryTransaction(cursor)
    event_id, token = str(uuid4()), str(uuid4())
    cursor.fetchone.return_value = ('retry', 0, None, NOW)
    assert tx.claim_processing(event_id, NOW, token, 300, 5).token == token
    assert any('FOR UPDATE' in call.args[0] for call in cursor.execute.call_args_list)
    cursor.fetchone.return_value = (event_id,)
    assert tx.finish_processing(event_id, token, 'succeeded', NOW, None)
    assert 'lease_token=%s' in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1][-1] == token
