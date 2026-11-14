"""Local identity/ownership tests; no claim of HTTP/JWT integration coverage."""
import copy
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from notification.reads import (
    AuthenticationRequired, Cursor, NotificationAccess, NotificationItem,
    NotificationNotFound, OwnedNotification, VerifiedPrincipal,
)
from notification.store import PostgresTransaction

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
USER_A = 'ae000000-0000-4000-8000-000000000001'
USER_B = 'ae000000-0000-4000-8000-000000000002'
GOAL = 'ae000000-0000-4000-8000-000000000003'


def identifier(number):
    return str(UUID(int=number))


def stored(number, user=USER_A, created=NOW):
    return OwnedNotification(user, NotificationItem(identifier(number), GOAL, 1,
        'Verified opportunity available', 'Offer may change at checkout.', created, None))


class MemoryReadStore:
    def __init__(self, rows):
        self.rows = {row.item.notification_id: row for row in rows}
        self.fail_read, self.fail_commit = False, False
        self.calls = 0

    @contextmanager
    def transaction(self):
        self.calls += 1
        before = copy.deepcopy(self.rows)
        try:
            yield self
            if self.fail_commit:
                raise RuntimeError('storage commit failed')
        except BaseException:
            self.rows = before
            raise

    def list_notifications(self, user_id, limit, before):
        if self.fail_read:
            raise RuntimeError('storage unavailable')
        rows = [r for r in self.rows.values() if r.user_id == user_id]
        if before is not None:
            rows = [r for r in rows if (r.item.created_at, r.item.notification_id) <
                    (before.created_at, before.notification_id)]
        return sorted(rows, key=lambda r: (r.item.created_at, r.item.notification_id), reverse=True)[:limit]

    def mark_read(self, user_id, notification_id, read_at):
        row = self.rows.get(notification_id)
        if row is None or row.user_id != user_id:
            return False
        self.rows[notification_id] = replace(row, item=replace(row.item, read_at=row.item.read_at or read_at))
        return True


def access(store, user=USER_A, clock=lambda: NOW):
    return NotificationAccess(store, lambda: VerifiedPrincipal(user), clock)


def test_each_user_lists_only_own_notifications():
    store = MemoryReadStore([stored(1), stored(2, USER_B), stored(3)])
    assert [i.notification_id for i in access(store).list().items] == [identifier(3), identifier(1)]
    assert [i.notification_id for i in access(store, USER_B).list().items] == [identifier(2)]
    assert access(MemoryReadStore([])).list().items == ()


def test_missing_and_other_user_mark_read_indistinguishable():
    store = MemoryReadStore([stored(1)])
    for nid in (identifier(1), identifier(999)):
        with pytest.raises(NotificationNotFound):
            access(store, USER_B).mark_read(nid)
    assert not store.rows[identifier(1)].item.is_read


def test_mark_read_idempotent_preserves_first_timestamp():
    store = MemoryReadStore([stored(1)])
    access(store).mark_read(identifier(1))
    access(store, clock=lambda: NOW + timedelta(hours=1)).mark_read(identifier(1))
    result = access(store).list().items[0]
    assert result.is_read
    assert result.read_at == NOW


def test_keyset_pages_same_timestamp_no_duplicates_or_missing_rows():
    store = MemoryReadStore([stored(n) for n in range(1, 8)] + [stored(99, USER_B)])
    service = access(store)
    first = service.list(limit=3)
    assert first.next_cursor is not None
    second = service.list(limit=3, before=first.next_cursor)
    third = service.list(limit=3, before=second.next_cursor)
    seen = [i.notification_id for p in (first, second, third) for i in p.items]
    assert seen == [identifier(n) for n in range(7, 0, -1)]
    assert third.next_cursor is None


def test_insert_between_pages_does_not_shift_already_read_rows():
    store = MemoryReadStore([stored(n) for n in range(1, 5)])
    service = access(store)
    first = service.list(limit=2)
    store.rows[identifier(5)] = stored(5, created=NOW + timedelta(seconds=1))
    second = service.list(limit=2, before=first.next_cursor)
    assert [i.notification_id for i in first.items + second.items] == [identifier(n) for n in (4, 3, 2, 1)]


def test_another_users_cursor_cannot_disclose_their_rows():
    store = MemoryReadStore([stored(10), stored(9), stored(8, USER_B), stored(7, USER_B)])
    cursor = access(store).list(limit=1).next_cursor
    assert [i.notification_id for i in access(store, USER_B).list(before=cursor).items] == [identifier(8), identifier(7)]


@pytest.mark.parametrize('identity', [lambda: None, lambda: USER_A, lambda: {'sub': USER_A}])
def test_unsigned_identity_rejected_before_storage(identity):
    store = MemoryReadStore([stored(1)])
    service = NotificationAccess(store, identity, lambda: NOW)
    with pytest.raises(AuthenticationRequired):
        service.list()
    with pytest.raises(AuthenticationRequired):
        service.mark_read(identifier(1))
    assert store.calls == 0


@pytest.mark.parametrize('limit', [0, -1, 101, True, 1.0, '20'])
def test_page_bounds_rejected_before_storage(limit):
    store = MemoryReadStore([])
    with pytest.raises(ValueError):
        access(store).list(limit=limit)
    assert store.calls == 0


def test_invalid_cursor_and_identifiers_rejected_before_storage():
    store = MemoryReadStore([])
    with pytest.raises(ValueError):
        access(store).list(before='forged-string')
    with pytest.raises(ValueError):
        access(store).mark_read("x' OR true --")
    with pytest.raises(ValueError):
        Cursor(NOW.replace(tzinfo=None), identifier(1))
    with pytest.raises(ValueError):
        VerifiedPrincipal('not-a-uuid')
    assert store.calls == 0


def test_storage_failure_does_not_become_empty_success():
    store = MemoryReadStore([stored(1)])
    store.fail_read = True
    with pytest.raises(RuntimeError, match='unavailable'):
        access(store).list()
    store.fail_read = False
    store.fail_commit = True
    with pytest.raises(RuntimeError, match='commit failed'):
        access(store).mark_read(identifier(1))
    assert not store.rows[identifier(1)].item.is_read


def test_wrong_owner_returned_by_broken_adapter_is_not_disclosed():
    store = MemoryReadStore([stored(1, USER_B)])
    store.list_notifications = Mock(return_value=list(store.rows.values()))
    with pytest.raises(PermissionError):
        access(store).list()


def test_mark_read_rejects_naive_clock():
    store = MemoryReadStore([stored(1)])
    with pytest.raises(ValueError, match='UTC'):
        access(store, clock=lambda: NOW.replace(tzinfo=None)).mark_read(identifier(1))
    assert not store.rows[identifier(1)].item.is_read


def test_postgres_queries_bind_owner_on_every_page_and_update():
    cursor = Mock()
    cursor.fetchall.return_value = [(USER_A, identifier(1), GOAL, 1, 'title', 'summary', NOW, None)]
    tx = PostgresTransaction(cursor)
    rows = tx.list_notifications(USER_A, 21, None)
    assert rows[0].user_id == USER_A
    assert rows[0].item.notification_id == identifier(1)
    assert cursor.execute.call_args.args[1] == (USER_A, 21)
    tx.list_notifications(USER_A, 21, Cursor(NOW, identifier(1)))
    query, parameters = cursor.execute.call_args.args
    assert 'WHERE user_id=%s' in query and 'notification_id DESC' in query
    assert parameters == (USER_A, NOW, identifier(1), 21)
    cursor.fetchone.return_value = None
    assert not tx.mark_read(USER_B, identifier(1), NOW)
    query, parameters = cursor.execute.call_args.args
    assert 'WHERE user_id=%s AND notification_id=%s' in query
    assert 'COALESCE' in query
    assert parameters == (NOW, USER_B, identifier(1))
    cursor.fetchone.return_value = (identifier(1),)
    assert tx.mark_read(USER_A, identifier(1), NOW)


def test_offset_cursor_is_normalized_to_utc():
    cursor = Cursor(NOW.astimezone(timezone(timedelta(hours=2))), identifier(1))
    assert cursor.created_at == NOW
    assert cursor.created_at.utcoffset() == timedelta(0)
