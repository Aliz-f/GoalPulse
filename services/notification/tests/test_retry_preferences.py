"""Notification policy and recovery tests; journal/DB/broker are test doubles."""
import copy
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from notification.contracts import Contracts
from notification.preferences import Preferences, PreferenceAccess
from notification.reads import AuthenticationRequired, VerifiedPrincipal
from notification.retry import Lease, RetryCoordinator
from notification.store import PostgresTransaction
from test_notifications import setup, load

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
USER_A = 'ae000000-0000-4000-8000-000000000001'
USER_B = 'ae000000-0000-4000-8000-000000000002'


class Journal:
    def __init__(self):
        self.rows, self.rejections, self.preferences = {}, [], {}
        self.fail_finish_commit = False
        self.finishing = False

    @contextmanager
    def transaction(self):
        before = copy.deepcopy((self.rows, self.rejections, self.preferences))
        try:
            yield self
            if self.finishing and self.fail_finish_commit:
                raise RuntimeError('journal commit failed')
        except BaseException:
            self.rows, self.rejections, self.preferences = before
            raise
        finally:
            self.finishing = False

    def claim_processing(self, event_id, now, token, seconds, limit):
        row = self.rows.setdefault(event_id, dict(state='retry', attempts=0, due=now, token=None))
        if row['state'] in {'succeeded', 'suppressed'}:
            return Lease('complete', row['attempts'])
        if row['state'] == 'dead_letter':
            return Lease('terminal', row['attempts'])
        if row['state'] == 'processing' and row['due'] > now:
            return Lease('busy', row['attempts'], due_at=row['due'])
        if row['state'] == 'retry' and row['due'] > now:
            return Lease('not_due', row['attempts'], due_at=row['due'])
        if row['attempts'] >= limit:
            row['state'] = 'dead_letter'
            return Lease('terminal', row['attempts'])
        row.update(state='processing', attempts=row['attempts'] + 1,
                   due=now + timedelta(seconds=seconds), token=token)
        return Lease('claimed', row['attempts'], token, row['due'])

    def finish_processing(self, event_id, token, state, due, reason):
        row = self.rows[event_id]
        if row['state'] != 'processing' or row['token'] != token:
            return False
        row.update(state=state, due=due, reason=reason, token=None)
        self.finishing = True
        return True

    def record_rejection(self, event_id, now):
        self.rejections.append((event_id, now, 'invalid_event'))

    def processing_counts(self):
        result = {'rejected': len(self.rejections)}
        for row in self.rows.values():
            result[row['state']] = result.get(row['state'], 0) + 1
        return result

    def get_preferences(self, user_id):
        return self.preferences.get(user_id, Preferences())

    def set_in_app_preference(self, user_id, enabled, now):
        old = self.get_preferences(user_id)
        self.preferences[user_id] = Preferences(enabled, old.email_enabled)


def worker(consumer, journal=None, clock=None, **kwargs):
    journal = journal or Journal()
    clock = clock or Mock(return_value=NOW)
    contracts = Contracts(SERVICE.parents[1] / 'contracts/schemas')
    return RetryCoordinator(contracts, journal, consumer, clock, **kwargs), journal, clock


def test_success_replay_does_not_repeat_consumer_side_effects():
    consumer = Mock()
    consumer.handle.return_value = 'created'
    coordinator, journal, _ = worker(consumer)
    event = load('event.goal-opportunity-verified.json')
    assert coordinator.run(event).action == 'ack'
    assert coordinator.run(event).action == 'ack'
    consumer.handle.assert_called_once()
    assert coordinator.status_counts() == {'succeeded': 1, 'rejected': 0}


def test_failures_stored_backoff_bounded_and_terminal_not_retried():
    consumer = Mock()
    consumer.handle.side_effect = ConnectionError('private credential details')
    coordinator, journal, clock = worker(consumer, max_attempts=3)
    event = load('event.goal-opportunity-verified.json')
    assert coordinator.run(event).delay_seconds == 1
    row = journal.rows[event['event_id']]
    assert row['reason'] == 'read_or_storage_failed'
    assert coordinator.run(event).action == 'retry'
    assert consumer.handle.call_count == 1
    clock.return_value = NOW + timedelta(seconds=1)
    assert coordinator.run(event).delay_seconds == 2
    clock.return_value = NOW + timedelta(seconds=3)
    assert coordinator.run(event).action == 'dead_letter'
    assert coordinator.run(event).action == 'dead_letter'
    assert consumer.handle.call_count == 3
    assert coordinator.status_counts()['dead_letter'] == 1
    assert 'credential' not in str(journal.rows)


def test_slow_failure_backoff_starts_when_attempt_finishes():
    consumer = Mock()
    coordinator, journal, clock = worker(consumer)
    def fail(event):
        clock.return_value = NOW + timedelta(seconds=30)
        raise TimeoutError()
    consumer.handle.side_effect = fail
    event = load('event.goal-opportunity-verified.json')
    coordinator.run(event)
    assert journal.rows[event['event_id']]['due'] == NOW + timedelta(seconds=31)
    coordinator.run(event)
    assert consumer.handle.call_count == 1


@pytest.mark.parametrize('error', [ValueError, PermissionError])
def test_permanent_failure_is_terminal_without_retry(error):
    consumer = Mock()
    consumer.handle.side_effect = error('sensitive raw data')
    coordinator, journal, _ = worker(consumer)
    event = load('event.goal-opportunity-verified.json')
    assert coordinator.run(event).action == 'dead_letter'
    assert coordinator.run(event).action == 'dead_letter'
    consumer.handle.assert_called_once()
    assert journal.rows[event['event_id']]['reason'] == 'invalid_or_unauthorized'


@pytest.mark.parametrize('event_id', [None, 'bad-id'])
def test_invalid_event_rejected_and_only_safe_metadata_stored(event_id):
    consumer = Mock()
    coordinator, journal, _ = worker(consumer)
    assert coordinator.run({'event_id': event_id, 'secret': 'never store me'}).action == 'dead_letter'
    consumer.handle.assert_not_called()
    assert journal.rejections == [(None, NOW, 'invalid_event')]


def test_active_lease_prevents_concurrent_duplicate_attempt():
    consumer = Mock()
    coordinator, journal, _ = worker(consumer)
    event = load('event.goal-opportunity-verified.json')
    with journal.transaction() as tx:
        tx.claim_processing(event['event_id'], NOW, 'other-worker', 60, 5)
    decision = coordinator.run(event)
    assert decision.action == 'retry' and decision.delay_seconds == 60
    consumer.handle.assert_not_called()
    assert journal.rows[event['event_id']]['attempts'] == 1


def test_crashed_lease_recovers_and_attempt_limit_remains_durable():
    consumer = Mock()
    consumer.handle.return_value = 'created'
    coordinator, journal, clock = worker(consumer, max_attempts=2)
    event = load('event.goal-opportunity-verified.json')
    with journal.transaction() as tx:
        tx.claim_processing(event['event_id'], NOW, 'crashed', 60, 2)
    clock.return_value = NOW + timedelta(seconds=61)
    assert coordinator.run(event).action == 'ack'
    assert journal.rows[event['event_id']]['attempts'] == 2
    consumer.handle.assert_called_once()


def test_exhausted_crash_lease_goes_terminal_without_new_consumer_call():
    consumer = Mock()
    coordinator, journal, clock = worker(consumer, max_attempts=1)
    event = load('event.goal-opportunity-verified.json')
    with journal.transaction() as tx:
        tx.claim_processing(event['event_id'], NOW, 'crashed', 1, 1)
    clock.return_value = NOW + timedelta(seconds=2)
    assert coordinator.run(event).action == 'dead_letter'
    consumer.handle.assert_not_called()


def test_stolen_lease_cannot_be_overwritten_by_old_worker():
    consumer = Mock()
    coordinator, journal, clock = worker(consumer)
    event = load('event.goal-opportunity-verified.json')
    def takeover(data):
        clock.return_value = NOW + timedelta(seconds=61)
        with journal.transaction() as tx:
            tx.claim_processing(event['event_id'], clock(), 'new-owner', 60, 5)
        return 'created'
    consumer.handle.side_effect = takeover
    assert coordinator.run(event).action == 'retry'
    assert journal.rows[event['event_id']]['token'] == 'new-owner'


def test_notification_commit_then_journal_failure_recovers_via_inbox(setup):
    s = setup
    coordinator, journal, clock = worker(s.consumer)
    journal.fail_finish_commit = True
    with pytest.raises(RuntimeError, match='journal commit'):
        coordinator.run(s.event)
    assert len(s.store.notifications) == 1
    assert journal.rows[s.event['event_id']]['state'] == 'processing'
    journal.fail_finish_commit = False
    clock.return_value = NOW + timedelta(seconds=61)
    assert coordinator.run(s.event).action == 'ack'
    assert len(s.store.notifications) == 1
    assert journal.rows[s.event['event_id']]['state'] == 'succeeded'


def test_full_consumer_storage_failure_preserves_retry_journal(setup):
    s = setup
    coordinator, journal, clock = worker(s.consumer)
    s.store.fail_insert = True
    assert coordinator.run(s.event).action == 'retry'
    assert not s.store.inbox and not s.store.notifications
    assert journal.rows[s.event['event_id']]['attempts'] == 1
    s.store.fail_insert = False
    clock.return_value = NOW + timedelta(seconds=1)
    assert coordinator.run(s.event).action == 'ack'
    assert len(s.store.notifications) == 1


def test_owner_scoped_preferences_no_email_sender_or_optin_setter():
    journal = Journal()
    a = PreferenceAccess(journal, lambda: VerifiedPrincipal(USER_A), lambda: NOW)
    b = PreferenceAccess(journal, lambda: VerifiedPrincipal(USER_B), lambda: NOW)
    assert a.get() == Preferences(True, False)
    a.set_in_app(False)
    assert not a.get().in_app_enabled
    assert b.get().in_app_enabled
    assert not a.get().email_enabled and not a.get().email_delivery_available
    a.set_in_app(True)
    assert a.get().in_app_enabled
    assert not hasattr(a, 'set_email')


def test_preference_policy_applies_to_new_events_without_replaying_old_ones(setup):
    s = setup
    journal = Journal()
    preference = PreferenceAccess(journal, lambda: VerifiedPrincipal(USER_A), lambda: NOW)
    s.store.in_app_enabled = lambda uid, **kwargs: journal.get_preferences(uid).in_app_enabled
    preference.set_in_app(False)
    coordinator, _, _ = worker(s.consumer, journal)
    assert coordinator.run(s.event).action == 'ack'
    assert not s.store.notifications
    preference.set_in_app(True)
    assert coordinator.run(s.event).action == 'ack'
    assert not s.store.notifications  # old suppressed event is not resurrected
    from uuid import uuid4
    s.event['event_id'] = str(uuid4())
    assert coordinator.run(s.event).action == 'ack'
    assert len(s.store.notifications) == 1


@pytest.mark.parametrize('enabled', [1, 'false', None])
def test_preference_validation(enabled):
    access = PreferenceAccess(Journal(), lambda: VerifiedPrincipal(USER_A), lambda: NOW)
    with pytest.raises(ValueError):
        access.set_in_app(enabled)


def test_preference_missing_identity_rejected_before_storage():
    access = PreferenceAccess(Mock(), lambda: None, lambda: NOW)
    with pytest.raises(AuthenticationRequired):
        access.get()
    with pytest.raises(AuthenticationRequired):
        access.set_in_app(False)
    access.store.transaction.assert_not_called()


def test_postgres_journal_lease_locking_and_conditional_finish():
    cursor = Mock()
    tx = PostgresTransaction(cursor)
    cursor.fetchone.return_value = ('retry', 0, None, NOW)
    lease = tx.claim_processing(USER_A, NOW, USER_B, 60, 5)
    assert lease.state == 'claimed' and lease.attempts == 1
    assert 'FOR UPDATE' in cursor.execute.call_args_list[1].args[0]
    cursor.fetchone.return_value = None
    assert not tx.finish_processing(USER_A, USER_B, 'retry', NOW, 'read_or_storage_failed')
    assert 'lease_token=%s' in cursor.execute.call_args.args[0]
    cursor.fetchone.return_value = (USER_A,)
    assert tx.finish_processing(USER_A, USER_B, 'succeeded', NOW, None)
    cursor.fetchone.return_value = None
    assert tx.get_preferences(USER_A) == Preferences()
    tx.set_in_app_preference(USER_A, False, NOW)
    assert cursor.execute.call_args.args[1] == (USER_A, False, NOW, NOW)
    assert 'false' in cursor.execute.call_args.args[0]


def test_opt_out_during_evidence_read_blocks_notification(setup):
    s = setup
    def evidence_read(oid, correlation):
        s.store.enabled = False
        return s.observation
    s.evidence.get.side_effect = evidence_read
    assert s.consumer.handle(s.event) == 'disabled'
    assert not s.store.notifications


def test_final_preference_check_locks_owned_row_before_delivery():
    cursor = Mock()
    cursor.fetchone.return_value = (True,)
    assert PostgresTransaction(cursor).in_app_enabled(USER_A, lock=True)
    assert 'ON CONFLICT' in cursor.execute.call_args_list[0].args[0]
    query, parameters = cursor.execute.call_args.args
    assert 'FOR SHARE' in query and parameters == (USER_A,)


def test_stored_email_preference_does_not_enable_delivery():
    prefs = Preferences(True, True)
    assert prefs.email_enabled
    assert not prefs.email_delivery_available


def test_status_snapshot_has_only_aggregate_processing_and_delivery_counts():
    cursor = Mock()
    cursor.fetchall.side_effect = [[('retry', 2), ('succeeded', 4)], [('in_app', 'available', 4)]]
    cursor.fetchone.return_value = (1,)
    assert PostgresTransaction(cursor).processing_counts() == {
        'retry': 2, 'succeeded': 4, 'rejected': 1, 'in_app_available': 4,
    }


@pytest.mark.parametrize('row,expected', [
    (('processing', 1, NOW + timedelta(seconds=60), NOW), 'busy'),
    (('retry', 1, None, NOW + timedelta(seconds=5)), 'not_due'),
    (('succeeded', 1, None, NOW), 'complete'),
    (('dead_letter', 5, None, NOW), 'terminal'),
])
def test_postgres_claim_respects_existing_journal_state(row, expected):
    cursor = Mock()
    cursor.fetchone.return_value = row
    lease = PostgresTransaction(cursor).claim_processing(USER_A, NOW, USER_B, 60, 5)
    assert lease.state == expected
    assert lease.attempts == row[1]
    assert cursor.execute.call_count == 2  # no new reservation or attempt consumed
