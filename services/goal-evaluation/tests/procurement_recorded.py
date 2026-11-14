"""Explicit recorded basket inputs for independent GE worker/solver tests."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from goal_evaluation.processing import Runtime
from goal_evaluation.runtime import build_runtime
from test_persistence_outbox import MemoryDB
from test_ge_retry import Journal
from test_procurement import NOW, load, offer


def fixture(*, original_cafe=False):
    goal = load('goal-spec.procurement.json')
    if not original_cafe:
        for item in goal['criteria']['items']:
            item.update(quantity=1, requirements=[])
        goal['criteria']['items'].append(dict(name='Coffee filters', quantity=1, unit='pcs', requirements=[]))
        goal['criteria'].update(current_total_eur_cents=4000, max_total_eur_cents=3000)
    observations = [
        offer('Lactose-free milk', 'L', 115, 'dairy.test', shipping=500, tax=2),
        offer('Coffee beans', 'kg', 1600, 'coffee.test', shipping=200, tax=3),
        offer('Coffee filters', 'pcs', 300, 'dairy.test', shipping=500, tax=0),
    ]
    event = load('event.evidence-updated.json')
    event['aggregate_id'] = event['payload']['source_id'] = observations[0]['source_id']
    event['payload']['observation_id'] = observations[0]['observation_id']
    return dict(goal=goal, observations=observations, event=event,
                runtime=Runtime(goal['goal_id'], goal['owner_user_id'], 1, 'active'),
                sources=[o['source_id'] for o in observations])


class RecordedTransport:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, url, headers):
        from urllib.parse import parse_qs, urlsplit
        self.calls.append((url, headers['X-Correlation-ID']))
        path = urlsplit(url).path
        if path.endswith('/runtime'):
            row = vars(self.data['runtime'])
        elif '/versions/' in path:
            row = self.data['goal']
        elif path.endswith('/subscriptions'):
            row = dict(source_id=path.split('/')[-2], subscriptions=[dict(
                goal_id=self.data['goal']['goal_id'], goal_version=self.data['goal']['version'])])
        elif path == '/internal/v1/observations':
            source = parse_qs(urlsplit(url).query)['source_id'][0]
            row = {'observations': [o for o in self.data['observations'] if o['source_id'] == source]}
        elif '/observations/' in path:
            row = next(o for o in self.data['observations'] if o['observation_id'] == path.split('/')[-1])
        else:
            raise AssertionError('Unexpected recorded internal operation')
        return 200, json.dumps(row).encode()


def recorded_runtime(*, original_cafe=False, confirmed=False, data=None):
    data = copy.deepcopy(data) if data is not None else fixture(original_cafe=original_cafe)
    store, journal, clock = MemoryDB(), Journal(), Mock(return_value=NOW)
    broker = Mock()
    broker.is_open = broker.channel.return_value.is_open = True
    channel = broker.channel.return_value
    channel.basic_publish.return_value = None
    method = SimpleNamespace(exchange='goalpulse.events', routing_key='evidence.updated.v1', delivery_tag=1)
    channel.basic_get.return_value = method, None, json.dumps(data['event']).encode()
    transport = RecordedTransport(data)
    connect = Mock(side_effect=AssertionError('Recorded mode must not open SQL connections'))
    runtime = build_runtime(connect_database=connect, broker_connection=broker,
        queue='recorded-basket-ge-queue', dead_letter_exchange='', dead_letter_routing_key='recorded-basket-dlq',
        schemas=ROOT / 'contracts/schemas', gm_origin='http://recorded-gm.internal',
        dm_origin='http://recorded-dm.internal', de_origin='http://recorded-de.internal',
        tokens=lambda scope: 'recorded-test-service-token', profile='test',
        sources_for_goal=lambda subscription, correlation: list(data['sources']),
        dependencies_ready=lambda: True, clock=clock, transport=transport, properties_factory=Mock(),
        test_stores=(store, journal), procurement_normalization_confirmed=confirmed)
    return SimpleNamespace(**locals())
