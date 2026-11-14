"""Explicit test-only fixtures for the standalone worker/API smoke runner."""

import copy
import json
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from agent_integration.eligibility import PRODUCT_CODES, Runtime
from agent_integration.retry import Lease
from agent_integration.runtime import build_runtime
from test_eligibility import NOW, load
from test_packages import MemoryStore


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


class RecordedTransport:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, url, headers):
        self.calls.append((url, headers['X-Correlation-ID']))
        if url.endswith('/runtime'):
            row = vars(self.data['runtime'])
        elif '/versions/' in url:
            row = self.data['goal']
        elif '/evaluations/' in url:
            row = self.data['evaluation']
        elif '/observations?' in url:
            row = {'observations': [self.data['observation']]}
        elif '/observations/' in url:
            row = self.data['observation']
        else:
            raise AssertionError('Unexpected recorded internal operation')
        return 200, json.dumps(row).encode()


def recorded_runtime():
    """Synthetic checked facts, never live evidence or a deployment fallback."""
    goal = load('goal-spec.product.json')
    goal['criteria']['product'] = dict(query='RTX 5070', match_policy='exact_model', condition='any')
    observation = load('observation.verified.fixture.json')
    evaluation = load('goal-evaluation.satisfied.fixture.json')
    evaluation['conditions'] = [dict(code=code, status='satisfied', reason='Synthetic recorded condition',
        observation_ids=[observation['observation_id']]) for code in sorted(PRODUCT_CODES)]
    event = load('event.goal-opportunity-verified.json')
    data = dict(goal=goal, observation=observation, evaluation=evaluation, event=event,
                runtime=Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active'))
    store, journal, clock = MemoryStore(), Journal(), Mock(return_value=NOW)
    broker = Mock()
    broker.is_open = broker.channel.return_value.is_open = True
    channel = broker.channel.return_value
    method = SimpleNamespace(exchange='goalpulse.events', routing_key='goal.opportunity_verified.v1', delivery_tag=1)
    channel.basic_get.return_value = method, None, json.dumps(event).encode()
    transport = RecordedTransport(data)
    connect = Mock(side_effect=AssertionError('Recorded mode must not open a database'))
    runtime = build_runtime(connect_database=connect, broker_connection=broker,
        queue='recorded-test-package-queue', dead_letter_exchange='', dead_letter_routing_key='recorded-test-dlq',
        schemas=ROOT / 'contracts/schemas', gm_origin='http://recorded-gm.internal',
        ge_origin='http://recorded-ge.internal', de_origin='http://recorded-de.internal',
        tokens=lambda scope: 'explicit-recorded-test-token', evaluation_read_scope='fixture-ge-read',
        profile='test', dependencies_ready=lambda: True, clock=clock, transport=transport,
        test_stores=(store, journal))
    return SimpleNamespace(**locals())
