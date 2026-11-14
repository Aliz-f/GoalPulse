"""Owner-filtered local read operations; no HTTP route or unsigned user header.

The identity adapter is a trust boundary. Only a future approved authentication
adapter may construct VerifiedPrincipal from validated server-side claims. Tests
inject synthetic principals. No JWT provider/configuration is invented here.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, ContextManager, Protocol, Sequence
from uuid import UUID


def uuid_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('Identifier must be a UUID string')
    return str(UUID(value))


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('Timestamp must be timezone-aware')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class VerifiedPrincipal:
    subject: str

    def __post_init__(self):
        object.__setattr__(self, 'subject', uuid_text(self.subject))


@dataclass(frozen=True)
class Cursor:
    """Local keyset position, not an approved serialized API cursor."""
    created_at: datetime
    notification_id: str

    def __post_init__(self):
        object.__setattr__(self, 'created_at', aware_utc(self.created_at))
        object.__setattr__(self, 'notification_id', uuid_text(self.notification_id))


@dataclass(frozen=True)
class NotificationItem:
    notification_id: str
    goal_id: str
    goal_version: int
    title: str
    summary: str
    created_at: datetime
    read_at: datetime | None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


@dataclass(frozen=True)
class OwnedNotification:
    user_id: str
    item: NotificationItem


@dataclass(frozen=True)
class Page:
    items: tuple[NotificationItem, ...]
    next_cursor: Cursor | None


class ReadTransaction(Protocol):
    def list_notifications(self, user_id: str, limit: int, before: Cursor | None) -> Sequence[OwnedNotification]: ...
    def mark_read(self, user_id: str, notification_id: str, read_at: datetime) -> bool: ...


class ReadStore(Protocol):
    def transaction(self) -> ContextManager[ReadTransaction]: ...


class AuthenticationRequired(Exception):
    pass


class NotificationNotFound(Exception):
    """Identical result for nonexistent notifications and another user's ID."""


class NotificationAccess:
    """No caller-provided owner parameter. Storage failures propagate, never []."""

    def __init__(self, store: ReadStore, identity: Callable[[], VerifiedPrincipal | None],
                 clock: Callable[[], datetime]):
        self.store, self.identity, self.clock = store, identity, clock

    def _subject(self) -> str:
        principal = self.identity()
        if not isinstance(principal, VerifiedPrincipal):
            raise AuthenticationRequired()
        return principal.subject

    def list(self, *, limit: int = 20, before: Cursor | None = None) -> Page:
        subject = self._subject()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Limit must be between 1 and 100')
        if before is not None and not isinstance(before, Cursor):
            raise ValueError('Invalid local cursor')
        with self.store.transaction() as transaction:
            rows = transaction.list_notifications(subject, limit + 1, before)
            if len(rows) > limit + 1:
                raise ValueError('Storage exceeded requested bound')
            if any(row.user_id != subject for row in rows):
                raise PermissionError('Storage returned an unowned notification')
            items = tuple(row.item for row in rows[:limit])
            next_cursor = (Cursor(items[-1].created_at, items[-1].notification_id)
                           if len(rows) > limit else None)
            return Page(items, next_cursor)

    def mark_read(self, notification_id: str) -> None:
        subject = self._subject()
        notification_id = uuid_text(notification_id)
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError('Clock must be timezone-aware UTC')
        with self.store.transaction() as transaction:
            if not transaction.mark_read(subject, notification_id, now):
                raise NotificationNotFound()
