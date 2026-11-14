"""Durable, broker-independent retry decisions for NotificationConsumer.

No messages are sent here. A future broker adapter applies the returned ACK,
retry or DLQ decision using approved infrastructure. Leases bound concurrent
attempts and recover after process crashes; the consumer inbox is still required.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, ContextManager, Protocol
from uuid import UUID, uuid4

from jsonschema import ValidationError

from .contracts import Contracts, Document


@dataclass(frozen=True)
class Lease:
    state: str  # claimed, busy, not_due, complete, terminal
    attempts: int
    token: str | None = None
    due_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    action: str  # ack, retry, dead_letter
    delay_seconds: float = 0


class RetryTransaction(Protocol):
    def claim_processing(self, event_id: str, now: datetime, token: str,
                         lease_seconds: int, max_attempts: int) -> Lease: ...
    def finish_processing(self, event_id: str, token: str, state: str,
                          due_at: datetime, reason: str | None) -> bool: ...
    def record_rejection(self, event_id: str | None, now: datetime) -> None: ...
    def processing_counts(self) -> dict[str, int]: ...


class RetryStore(Protocol):
    def transaction(self) -> ContextManager[RetryTransaction]: ...


class EventConsumer(Protocol):
    def handle(self, event: Document) -> str: ...


class RetryCoordinator:
    def __init__(self, contracts: Contracts, store: RetryStore, consumer: EventConsumer,
                 clock: Callable[[], datetime], max_attempts: int = 5, lease_seconds: int = 60):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError('Invalid processing attempt bound')
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError('Invalid processing lease')
        self.contracts, self.store, self.consumer, self.clock = contracts, store, consumer, clock
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError('Clock must be timezone-aware UTC')
        return now

    def run(self, event: Document) -> Decision:
        now = self._now()
        try:
            self.contracts.validate('domain-event', event)
            if event['event_type'] != 'goal.opportunity_verified.v1':
                raise ValueError('Unsupported event')
        except (ValidationError, ValueError):
            event_id = None
            if isinstance(event, dict) and isinstance(event.get('event_id'), str):
                try:
                    event_id = str(UUID(event['event_id']))
                except ValueError:
                    pass
            with self.store.transaction() as transaction:
                transaction.record_rejection(event_id, now)
            return Decision('dead_letter')
        event_id, token = event['event_id'], str(uuid4())
        with self.store.transaction() as transaction:
            lease = transaction.claim_processing(event_id, now, token, self.lease_seconds, self.max_attempts)
        if lease.state == 'complete':
            return Decision('ack')
        if lease.state == 'terminal':
            return Decision('dead_letter')
        if lease.state in {'busy', 'not_due'}:
            return Decision('retry', max(0.1, (lease.due_at - self._now()).total_seconds()))
        if lease.state != 'claimed' or lease.token != token:
            raise ValueError('Invalid processing lease')
        try:
            outcome = self.consumer.handle(event)
        except (ValueError, ValidationError, PermissionError):
            state, reason = 'dead_letter', 'invalid_or_unauthorized'
        except Exception:
            state = 'dead_letter' if lease.attempts >= self.max_attempts else 'retry'
            reason = 'read_or_storage_failed'
        else:
            if outcome not in {'created', 'duplicate', 'deduplicated', 'suppressed', 'disabled', 'rate_limited'}:
                state, reason = 'dead_letter', 'invalid_consumer_outcome'
            else:
                state = 'suppressed' if outcome in {'suppressed', 'disabled', 'rate_limited'} else 'succeeded'
                reason = None
        finished = self._now()
        delay = min(2**min(lease.attempts - 1, 9), 300) if state == 'retry' else 0
        due = finished + timedelta(seconds=delay)
        with self.store.transaction() as transaction:
            recorded = transaction.finish_processing(event_id, token, state, due, reason)
        if not recorded:
            return Decision('retry', 1)  # expired/stolen lease; never overwrite another owner
        return Decision('retry', delay) if state == 'retry' else Decision(
            'dead_letter' if state == 'dead_letter' else 'ack')

    def status_counts(self) -> dict[str, int]:
        """Internal aggregate snapshot, no user IDs or raw error payloads."""
        with self.store.transaction() as transaction:
            return transaction.processing_counts()
