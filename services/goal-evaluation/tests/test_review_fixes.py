"""Regressions from the Phase 2 pre-PR review."""
import signal
import time
from unittest.mock import Mock

import pytest

from test_persistence_outbox import setup, save
from goal_evaluation.processing import EvaluationContext
from goal_evaluation.worker import ConfirmedPublisher, PublishDeadlineExceeded
from goal_evaluation.offer_identity import business_fingerprint


def test_missing_confirmation_is_interrupted_and_channel_cannot_be_reused():
    channel = Mock()
    channel.basic_publish.side_effect = lambda **kwargs: time.sleep(1)
    publisher = ConfirmedPublisher(channel, Mock(), timeout_seconds=0.02)
    previous = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    with pytest.raises(PublishDeadlineExceeded):
        publisher.publish_confirmed('goalpulse.events', 'goal.evaluated.v1', b'{}', True)
    assert time.monotonic() - started < 0.5
    assert not publisher.usable
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    with pytest.raises(ConnectionError):
        publisher.publish_confirmed('goalpulse.events', 'goal.evaluated.v1', b'{}', True)


def test_valid_pipe_model_persists_and_escape_sequences_do_not_collide(setup):
    s = setup
    keys = set()
    for model in ('Model | Edition', 'Model %7C Edition', 'Model % Edition'):
        s.goal['criteria']['product']['query'] = s.observation['model'] = model
        keys.add(business_fingerprint(s.goal, [s.observation]))
    assert len(keys) == 3
    s.goal['criteria']['product']['query'] = s.observation['model'] = 'Model | Edition'
    s.result = s.dispatcher.evaluate(s.goal, [s.observation], s.clock())
    s.context = EvaluationContext(s.goal, [s.observation], s.context.correlation_id)
    save(s)
    assert len(s.store.data['evaluations']) == 1
    assert len(s.store.data['outbox']) == 2
