"""Phase 2 notification freshness, throttle and transaction boundaries."""

from datetime import timedelta
import copy
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_notifications import NOW, setup
from notification.consumer import NotificationConsumer
from notification.store import PostgresTransaction
from notification.retry import Decision
from test_retry_preferences import worker


def new_offer(s, price_delta=1):
    s.event['event_id'] = str(uuid4())
    s.observation['unit_price_eur_cents'] += price_delta


def fresh_at(s, now):
    s.observation['observed_at'] = now.isoformat()
    s.result['evaluated_at'] = now.isoformat()
    s.clock.return_value = now


def test_changed_offer_is_rate_limited_and_replay_stays_suppressed(setup):
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    new_offer(s)
    assert s.consumer.handle(s.event) == 'rate_limited'
    assert len(s.store.notifications) == 1
    fresh_at(s, NOW + timedelta(hours=1))
    assert s.consumer.handle(s.event) == 'duplicate'
    s.event['event_id'] = str(uuid4())
    assert s.consumer.handle(s.event) == 'created'
    assert len(s.store.notifications) == 2


def test_rate_limit_slides_from_last_created_time_not_clock_hour(setup):
    s = setup
    fresh_at(s, NOW + timedelta(minutes=59))
    assert s.consumer.handle(s.event) == 'created'
    new_offer(s)
    fresh_at(s, NOW + timedelta(hours=1))
    assert s.consumer.handle(s.event) == 'rate_limited'
    s.event['event_id'] = str(uuid4())
    fresh_at(s, NOW + timedelta(hours=1, minutes=59))
    assert s.consumer.handle(s.event) == 'created'


def test_unchanged_offer_remains_deduplicated_after_hour(setup):
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    s.event['event_id'] = str(uuid4())
    fresh_at(s, NOW + timedelta(hours=2))
    assert s.consumer.handle(s.event) == 'deduplicated'
    assert len(s.store.notifications) == 1


@pytest.mark.parametrize('age,expected', [(600, 'created'), (601, 'suppressed')])
def test_alert_proof_is_at_most_ten_minutes_old(setup, age, expected):
    s = setup
    s.observation['observed_at'] = (NOW - timedelta(seconds=age)).isoformat()
    assert s.consumer.handle(s.event) == expected


def test_goal_freshness_can_be_stricter_than_alert_cap(setup):
    s = setup
    s.goal['monitoring']['max_evidence_age_minutes'] = 5
    s.observation['observed_at'] = (NOW - timedelta(minutes=6)).isoformat()
    assert s.consumer.handle(s.event) == 'suppressed'


def test_new_capture_does_not_renew_original_proof_age(setup):
    s = setup
    s.observation['observed_at'] = (NOW - timedelta(minutes=11)).isoformat()
    s.evidence.latest.return_value = [dict(s.observation, observation_id=str(uuid4()),
                                          observed_at=NOW.isoformat())]
    assert s.consumer.handle(s.event) == 'suppressed'


def test_lock_wait_is_followed_by_time_and_lifecycle_recheck(setup):
    s = setup
    s.store.lock_goal_notifications = Mock(side_effect=lambda *args:
        setattr(s.clock, 'return_value', NOW + timedelta(minutes=11)))
    assert s.consumer.handle(s.event) == 'suppressed'
    s.store.lock_goal_notifications.assert_called_once_with(s.runtime.owner_user_id, s.runtime.goal_id)


def test_failed_insert_does_not_consume_rate_slot(setup):
    s = setup
    s.store.fail_insert = True
    with pytest.raises(RuntimeError):
        s.consumer.handle(s.event)
    assert not s.store.notifications
    s.store.fail_insert = False
    assert s.consumer.handle(s.event) == 'created'


def test_rate_cap_applies_across_goal_versions(setup):
    from notification.consumer import Runtime
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    new_offer(s)
    s.goal['version'] = 2
    s.result['goal_version'] = 2
    s.event['payload']['goal_version'] = 2
    s.goals.runtime.return_value = Runtime(s.goal['goal_id'], s.goal['owner_user_id'], 2, 'active')
    assert s.consumer.handle(s.event) == 'rate_limited'
    assert len(s.store.notifications) == 1


def test_another_goals_notification_does_not_consume_this_goals_slot(setup):
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    existing = next(iter(s.store.notifications.values()))
    existing['goal_id'] = str(uuid4())
    # The test dictionary represents an independently stored different goal;
    # remove its old lookup key as the actual SQL owner/goal predicate does.
    s.store.notifications = {('different-goal',): existing}
    new_offer(s)
    assert s.consumer.handle(s.event) == 'created'


def test_postgres_lock_and_rate_queries_are_owner_scoped():
    cursor = Mock()
    tx = PostgresTransaction(cursor)
    user, goal = str(uuid4()), str(uuid4())
    tx.lock_goal_notifications(user, goal)
    assert 'pg_advisory_xact_lock' in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1] == (f'notification:{user}:{goal}',)
    cursor.fetchone.return_value = (1,)
    assert tx.notification_exists(user, goal, 2, 'fingerprint')
    assert cursor.execute.call_args.args[1] == (user, goal, 2, 'fingerprint')
    cursor.fetchone.return_value = (NOW,)
    assert tx.last_notification_at(user, goal) == NOW
    assert cursor.execute.call_args.args[1] == (user, goal)
    cursor.fetchone.return_value = None
    assert tx.last_notification_at(user, goal) is None


def test_uuid_text_case_cannot_bypass_the_same_goal_lock():
    cursor = Mock()
    tx = PostgresTransaction(cursor)
    user, goal = str(uuid4()), str(uuid4())
    tx.lock_goal_notifications(user, goal)
    expected = cursor.execute.call_args.args
    tx.lock_goal_notifications(user.upper(), goal.upper())
    assert cursor.execute.call_args.args == expected


def test_valid_pipe_model_can_create_notification(setup):
    s = setup
    s.goal['criteria']['product']['query'] = s.observation['model'] = 'Model | Edition'
    assert s.consumer.handle(s.event) == 'created'


@pytest.mark.parametrize('change', ['disputed', 'price'])
def test_current_capture_cannot_be_overridden_by_old_citation_uuid(setup, change):
    s = setup
    latest = copy.deepcopy(s.observation)
    latest['observation_id'] = '00000000-0000-4000-8000-000000000000'
    if change == 'disputed':
        latest['evidence']['reliability'] = 'disputed'
    else:
        latest['unit_price_eur_cents'] += 1
    s.evidence.latest.return_value = [latest]
    assert s.consumer.handle(s.event) == 'suppressed'
    assert not s.store.notifications


def test_rate_limited_is_durable_suppression_in_retry_journal():
    consumer = Mock()
    consumer.handle.return_value = 'rate_limited'
    coordinator, journal, _ = worker(consumer)
    from test_notifications import load
    event = load('event.goal-opportunity-verified.json')
    assert coordinator.run(event) == Decision('ack')
    assert journal.rows[event['event_id']]['state'] == 'suppressed'
    assert coordinator.run(event) == Decision('ack')
    consumer.handle.assert_called_once()


@pytest.mark.parametrize('limit', [0, 59, True, 60.0, 1441])
def test_rate_limit_cannot_disable_hourly_guard(setup, limit):
    s = setup
    with pytest.raises(ValueError):
        NotificationConsumer(s.contracts, s.store, s.goals, s.evaluations, s.evidence,
                             s.clock, rate_limit_minutes=limit)
