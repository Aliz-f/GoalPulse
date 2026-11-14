"""Inject a DB-API factory scoped to Notification's owned PostgreSQL DB only."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Callable
from .contracts import Document
from .fingerprint import canonical
from .reads import Cursor, NotificationItem, OwnedNotification, aware_utc
from .preferences import Preferences
from .retry import Lease


class PostgresStore:
    def __init__(self, connect: Callable):
        self.connect = connect

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError('Notification processing requires autocommit disabled')
            with connection.cursor() as cursor:
                yield PostgresTransaction(cursor)
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
        self.cursor.execute('INSERT INTO inbox (event_id) VALUES (%s) '
                            'ON CONFLICT (event_id) DO NOTHING RETURNING event_id', (event_id,))
        return self.cursor.fetchone() is not None

    def in_app_enabled(self, user_id: str, *, lock: bool = False) -> bool:
        if lock:
            self.cursor.execute('INSERT INTO preferences (user_id) VALUES (%s) '
                                'ON CONFLICT (user_id) DO NOTHING', (user_id,))
        self.cursor.execute('SELECT in_app_enabled FROM preferences WHERE user_id=%s' +
                            (' FOR SHARE' if lock else ''), (user_id,))
        row = self.cursor.fetchone()
        return row is None or row[0] is True

    def insert_notification(self, notification: Document) -> bool:
        n = notification
        self.cursor.execute(
            'INSERT INTO notifications (notification_id,user_id,goal_id,goal_version,evaluation_id,'
            'offer_fingerprint,channel,title,summary,evaluated_at,created_at,correlation_id,evidence,status) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) '
            'ON CONFLICT (user_id,goal_id,goal_version,offer_fingerprint,channel) DO NOTHING '
            'RETURNING notification_id',
            (n['notification_id'], n['user_id'], n['goal_id'], n['goal_version'], n['evaluation_id'],
             n['offer_fingerprint'], n['channel'], n['title'], n['summary'], n['evaluated_at'],
             n['created_at'], n['correlation_id'], canonical(n['evidence']), n['status']),
        )
        if self.cursor.fetchone() is None:
            return False
        # In-app availability is a durable stored row; no outbound delivery occurs.
        self.cursor.execute('INSERT INTO deliveries (notification_id,channel,state,attempts) '
                            "VALUES (%s,'in_app','available',0)", (n['notification_id'],))
        return True

    def list_notifications(self, user_id: str, limit: int, before: Cursor | None) -> list[OwnedNotification]:
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError('Invalid storage page bound')
        fields = ('user_id,notification_id,goal_id,goal_version,title,summary,created_at,read_at')
        if before is None:
            self.cursor.execute(
                f'SELECT {fields} FROM notifications WHERE user_id=%s '
                'ORDER BY created_at DESC,notification_id DESC LIMIT %s', (user_id, limit),
            )
        else:
            self.cursor.execute(
                f'SELECT {fields} FROM notifications WHERE user_id=%s '
                'AND (created_at,notification_id)<(%s,%s::uuid) '
                'ORDER BY created_at DESC,notification_id DESC LIMIT %s',
                (user_id, before.created_at, before.notification_id, limit),
            )
        return [OwnedNotification(str(row[0]), NotificationItem(
            notification_id=str(row[1]), goal_id=str(row[2]), goal_version=row[3],
            title=row[4], summary=row[5], created_at=aware_utc(row[6]),
            read_at=aware_utc(row[7]) if row[7] is not None else None,
        )) for row in self.cursor.fetchall()]

    def mark_read(self, user_id: str, notification_id: str, read_at: datetime) -> bool:
        self.cursor.execute(
            "UPDATE notifications SET status='read',read_at=COALESCE(read_at,%s) "
            'WHERE user_id=%s AND notification_id=%s RETURNING notification_id',
            (read_at, user_id, notification_id),
        )
        return self.cursor.fetchone() is not None

    def get_preferences(self, user_id: str) -> Preferences:
        self.cursor.execute('SELECT in_app_enabled,email_enabled FROM preferences WHERE user_id=%s', (user_id,))
        row = self.cursor.fetchone()
        return Preferences(*row) if row is not None else Preferences()

    def set_in_app_preference(self, user_id: str, enabled: bool, now: datetime) -> None:
        self.cursor.execute(
            'INSERT INTO preferences (user_id,in_app_enabled,email_enabled,created_at,updated_at) '
            'VALUES (%s,%s,false,%s,%s) ON CONFLICT (user_id) DO UPDATE '
            'SET in_app_enabled=EXCLUDED.in_app_enabled,updated_at=EXCLUDED.updated_at',
            (user_id, enabled, now, now),
        )

    def claim_processing(self, event_id: str, now: datetime, token: str,
                         lease_seconds: int, max_attempts: int) -> Lease:
        self.cursor.execute(
            'INSERT INTO notification_processing (event_id,state,next_retry_at,updated_at) '
            "VALUES (%s,'retry',%s,%s) ON CONFLICT (event_id) DO NOTHING", (event_id, now, now),
        )
        self.cursor.execute('SELECT state,attempts,lease_until,next_retry_at FROM notification_processing '
                            'WHERE event_id=%s FOR UPDATE', (event_id,))
        state, attempts, lease_until, next_retry = self.cursor.fetchone()
        if state in {'succeeded', 'suppressed'}:
            return Lease('complete', attempts)
        if state == 'dead_letter':
            return Lease('terminal', attempts)
        if state == 'processing' and lease_until > now:
            return Lease('busy', attempts, due_at=lease_until)
        if state == 'retry' and next_retry > now:
            return Lease('not_due', attempts, due_at=next_retry)
        if attempts >= max_attempts:
            self.cursor.execute("UPDATE notification_processing SET state='dead_letter',lease_token=NULL,"
                                "lease_until=NULL,last_error='attempt_limit',updated_at=%s WHERE event_id=%s",
                                (now, event_id))
            return Lease('terminal', attempts)
        until = now + timedelta(seconds=lease_seconds)
        self.cursor.execute("UPDATE notification_processing SET state='processing',attempts=attempts+1,"
                            'lease_token=%s,lease_until=%s,updated_at=%s WHERE event_id=%s',
                            (token, until, now, event_id))
        return Lease('claimed', attempts + 1, token, until)

    def finish_processing(self, event_id: str, token: str, state: str,
                          due_at: datetime, reason: str | None) -> bool:
        if state not in {'retry', 'succeeded', 'suppressed', 'dead_letter'}:
            raise ValueError('Invalid processing result')
        self.cursor.execute('UPDATE notification_processing SET state=%s,next_retry_at=%s,last_error=%s,'
                            'lease_token=NULL,lease_until=NULL,updated_at=now() '
                            "WHERE event_id=%s AND state='processing' AND lease_token=%s RETURNING event_id",
                            (state, due_at, reason, event_id, token))
        return self.cursor.fetchone() is not None

    def record_rejection(self, event_id: str | None, now: datetime) -> None:
        self.cursor.execute('INSERT INTO processing_rejections (rejection_id,event_id,reason,rejected_at) '
                            "VALUES (%s,%s,'invalid_event',%s) ON CONFLICT (event_id) DO NOTHING",
                            (str(uuid4()), event_id, now))

    def processing_counts(self) -> dict[str, int]:
        self.cursor.execute('SELECT state,count(*) FROM notification_processing GROUP BY state')
        result = {row[0]: row[1] for row in self.cursor.fetchall()}
        self.cursor.execute('SELECT count(*) FROM processing_rejections')
        result['rejected'] = self.cursor.fetchone()[0]
        self.cursor.execute('SELECT channel,state,count(*) FROM deliveries GROUP BY channel,state')
        for channel, state, count in self.cursor.fetchall():
            result[f'{channel}_{state}'] = count
        return result
