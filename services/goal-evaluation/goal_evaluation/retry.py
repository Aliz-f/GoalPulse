"""Durable processing leases/retries in the GE-owned DB, separate from inbox.

Business failures roll back inbox/evaluations/outbox. Attempt history survives;
a crash after business commit can recover via inbox dedup without a new outcome.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

from jsonschema import ValidationError

from .consumer import PostgresTransaction
from .persistence import utc_now


@dataclass(frozen=True)
class Lease:
    state: str
    attempts: int
    token: str | None = None
    due_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    action: str
    delay_seconds: float = 0


class PostgresRetryTransaction(PostgresTransaction):
    def claim_processing(self, event_id: str, now: datetime, token: str,
                         lease_seconds: int, max_attempts: int) -> Lease:
        self.cursor.execute('INSERT INTO evaluation_processing (event_id,state,next_retry_at,updated_at) '
                            "VALUES (%s,'retry',%s,%s) ON CONFLICT (event_id) DO NOTHING", (event_id, now, now))
        self.cursor.execute('SELECT state,attempts,lease_until,next_retry_at FROM evaluation_processing '
                            'WHERE event_id=%s FOR UPDATE', (event_id,))
        state, attempts, lease_until, next_retry = self.cursor.fetchone()
        if state in {'succeeded', 'dead_letter'}:
            return Lease('complete' if state == 'succeeded' else 'terminal', attempts)
        if state == 'processing' and lease_until > now:
            return Lease('busy', attempts, due_at=lease_until)
        if state == 'retry' and next_retry > now:
            return Lease('not_due', attempts, due_at=next_retry)
        if attempts >= max_attempts:
            self.cursor.execute("UPDATE evaluation_processing SET state='dead_letter',lease_token=NULL,"
                                "lease_until=NULL,last_error='attempt_limit',updated_at=%s WHERE event_id=%s",
                                (now, event_id))
            return Lease('terminal', attempts)
        until = now + timedelta(seconds=lease_seconds)
        self.cursor.execute("UPDATE evaluation_processing SET state='processing',attempts=attempts+1,"
                            'lease_token=%s,lease_until=%s,updated_at=%s WHERE event_id=%s',
                            (token, until, now, event_id))
        return Lease('claimed', attempts + 1, token, until)

    def finish_processing(self, event_id: str, token: str, state: str,
                          due_at: datetime, reason: str | None) -> bool:
        if state not in {'retry', 'succeeded', 'dead_letter'}:
            raise ValueError('Invalid processing state')
        self.cursor.execute('UPDATE evaluation_processing SET state=%s,next_retry_at=%s,last_error=%s,'
                            'lease_token=NULL,lease_until=NULL,updated_at=now() '
                            "WHERE event_id=%s AND state='processing' AND lease_token=%s RETURNING event_id",
                            (state, due_at, reason, event_id, token))
        return self.cursor.fetchone() is not None

    def record_rejection(self, event_id: str | None, now: datetime) -> None:
        self.cursor.execute('INSERT INTO evaluation_rejections (rejection_id,event_id,reason,rejected_at) '
                            "VALUES (%s,%s,'invalid_event',%s) ON CONFLICT (event_id) DO NOTHING",
                            (str(uuid4()), event_id, now))


class RetryCoordinator:
    def __init__(self, contracts, store, consumer, clock: Callable[[], datetime],
                 max_attempts: int = 5, lease_seconds: int = 300):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError('Invalid attempt limit')
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError('Invalid processing lease')
        self.contracts, self.store, self.consumer, self.clock = contracts, store, consumer, clock
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds

    def run(self, event) -> Decision:
        now = utc_now(self.clock)
        try:
            self.contracts.validate('domain-event', event)
            if (event['event_type'] != 'evidence.updated.v1'
                    or event['aggregate_id'] != event['payload']['source_id']):
                raise ValueError('Unsupported event identity')
        except (ValidationError, ValueError):
            event_id = None
            if isinstance(event, dict) and isinstance(event.get('event_id'), str):
                try:
                    event_id = str(UUID(event['event_id']))
                except ValueError:
                    pass
            with self.store.transaction() as tx:
                tx.record_rejection(event_id, now)
            return Decision('dead_letter')
        event_id, token = event['event_id'], str(uuid4())
        with self.store.transaction() as tx:
            lease = tx.claim_processing(event_id, now, token, self.lease_seconds, self.max_attempts)
        if lease.state in {'complete', 'terminal'}:
            return Decision('ack' if lease.state == 'complete' else 'dead_letter')
        if lease.state in {'busy', 'not_due'}:
            return Decision('retry', max(0.1, (lease.due_at - utc_now(self.clock)).total_seconds()))
        if lease.state != 'claimed' or lease.token != token:
            raise ValueError('Invalid processing lease')
        try:
            outcome = self.consumer.handle(event)
            if type(outcome) is not bool:
                raise ValueError('Invalid evidence-consumer outcome')
        except (ValueError, ValidationError, PermissionError, NotImplementedError):
            state, reason = 'dead_letter', 'invalid_or_unauthorized'
        except Exception:
            state = 'dead_letter' if lease.attempts >= self.max_attempts else 'retry'
            reason = 'read_or_storage_failed'
        else:
            state, reason = 'succeeded', None
        delay = min(2**min(lease.attempts - 1, 9), 300) if state == 'retry' else 0
        due = utc_now(self.clock) + timedelta(seconds=delay)
        with self.store.transaction() as tx:
            recorded = tx.finish_processing(event_id, token, state, due, reason)
        if not recorded:
            return Decision('retry', 1)
        return Decision('retry', delay) if state == 'retry' else Decision(
            'ack' if state == 'succeeded' else 'dead_letter')
