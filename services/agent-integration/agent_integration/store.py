"""DB-API adapter for the Agent Integration owned PostgreSQL database only."""
import copy
import json
from contextlib import contextmanager
from typing import Callable
from .eligibility import Document


class PostgresStore:
    def __init__(self, connect: Callable, transaction_factory: Callable | None = None):
        self.connect = connect
        self.transaction_factory = transaction_factory or PostgresTransaction

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError('Package processing requires autocommit disabled')
            with connection.cursor() as cursor:
                yield self.transaction_factory(cursor)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgresTransaction:
    def __init__(self, cursor):
        self.cursor = cursor

    def claim_event(self, event_id: str) -> bool:
        self.cursor.execute('INSERT INTO inbox (event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING event_id', (event_id,))
        return self.cursor.fetchone() is not None

    def insert_package(self, package: Document, owner: str, fingerprint: str, correlation: str) -> bool:
        p = package
        self.cursor.execute('INSERT INTO action_packages (package_id,goal_id,goal_version,evaluation_id,owner_user_id,'
            'offer_fingerprint,payload,created_at,expires_at,correlation_id) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) '
            'ON CONFLICT DO NOTHING RETURNING package_id',
            (p['package_id'], p['goal_id'], p['goal_version'], p['evaluation_id'], owner, fingerprint,
             json.dumps(p, sort_keys=True, allow_nan=False), p['created_at'], p['expires_at'], correlation))
        return self.cursor.fetchone() is not None

    def get_owned(self, package_id: str, owner: str) -> Document | None:
        self.cursor.execute('SELECT payload FROM action_packages WHERE package_id=%s AND owner_user_id=%s', (package_id, owner))
        row = self.cursor.fetchone()
        if row is None:
            return None
        return copy.deepcopy(json.loads(row[0]) if isinstance(row[0], str) else row[0])

    def has_offer(self, goal_id: str, version: int, fingerprint: str) -> bool:
        self.cursor.execute('SELECT 1 FROM action_packages WHERE goal_id=%s AND goal_version=%s '
                            'AND offer_fingerprint=%s', (goal_id, version, fingerprint))
        return self.cursor.fetchone() is not None
