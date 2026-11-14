"""Standalone fixture runner for cross-service tests. Reads JSON from stdin only.

Imports Notification code only; no GE/AI private module is shared or imported.
No server, external transport, credentials or live provider is used.
"""
import copy
import json
import socket
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from notification.contracts import Contracts
from notification.consumer import NotificationConsumer, Runtime


def forbidden(*args, **kwargs):
    raise AssertionError('External network/checkout/credential effects are forbidden in this fixture')


class Store:
    def __init__(self):
        self.inbox, self.notes = set(), {}

    @contextmanager
    def transaction(self):
        before = copy.deepcopy((self.inbox, self.notes))
        try:
            yield self
        except BaseException:
            self.inbox, self.notes = before
            raise

    def claim_event(self, event_id):
        if event_id in self.inbox:
            return False
        self.inbox.add(event_id)
        return True

    def in_app_enabled(self, user_id, *, lock=False):
        return True

    def lock_goal_notifications(self, user_id, goal_id):
        pass

    def notification_exists(self, user_id, goal_id, version, fingerprint):
        return (user_id, goal_id, version, fingerprint) in self.notes

    def last_notification_at(self, user_id, goal_id):
        times = [datetime.fromisoformat(n['created_at'].replace('Z', '+00:00'))
                 for n in self.notes.values()
                 if (n['user_id'], n['goal_id']) == (user_id, goal_id)]
        return max(times) if times else None

    def insert_notification(self, note):
        key = (note['user_id'], note['goal_id'], note['goal_version'], note['offer_fingerprint'])
        if key in self.notes:
            return False
        self.notes[key] = note
        return True


def main():
    data = json.load(sys.stdin)
    socket.socket = forbidden
    socket.create_connection = forbidden
    store = Store()
    current = dict(data['runtime'])
    def get_observation(oid, correlation):
        if data['scenario'] == 'pause_race':
            current['lifecycle'] = 'paused'
        return next(o for o in data['observations'] if o['observation_id'] == oid)
    goals = SimpleNamespace(runtime=lambda gid, cid: Runtime(**current),
                            version=lambda gid, version, cid: data['goal'])
    evaluations = SimpleNamespace(get=lambda eid, cid: data['evaluation'])
    evidence = SimpleNamespace(get=get_observation,
        latest=lambda sid, cid: [o for o in data.get('latest_observations', data['observations']) if o['source_id'] == sid])
    consumer = NotificationConsumer(Contracts(SERVICE.parents[1] / 'contracts/schemas'), store,
        goals, evaluations, evidence, lambda: datetime.fromisoformat(data['now']))
    outcomes = [consumer.handle(data['event']), consumer.handle(data['event'])]
    print(json.dumps(dict(count=len(store.notes), outcomes=outcomes, notifications=list(store.notes.values()))))


if __name__ == '__main__':
    main()
