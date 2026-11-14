"""Broker-independent durable inbox boundary; adapters own ACK/retry policy."""

from typing import Callable, ContextManager, Protocol

from .domain import Contracts, Document


class Transaction(Protocol):
    def claim_event(self, event_id: str) -> bool:
        """Atomically insert inbox ID; return false for an already committed ID."""
        ...


class Store(Protocol):
    def transaction(self) -> ContextManager[Transaction]:
        """Commit on success; rollback inbox and domain writes on exception."""
        ...


class EvidenceConsumer:
    """Invoke injected processing within the same transaction as inbox claiming.

    The handler receives correlation_id and source/observation IDs in the exact
    event. It may use injected mock clients until internal APIs are approved.
    Return only after commit; broker adapters must ACK after this returns.
    Exceptions propagate for bounded retry/dead-letter handling by that adapter.
    """

    def __init__(
        self, contracts: Contracts, store: Store,
        process: Callable[[Document, Transaction], None],
    ):
        self.contracts, self.store, self.process = contracts, store, process

    def handle(self, event: Document) -> bool:
        self.contracts.validate("domain-event", event)
        if event["event_type"] != "evidence.updated.v1":
            raise ValueError("Goal Evaluation consumes only evidence.updated.v1")
        if event['aggregate_id'] != event['payload']['source_id']:
            raise ValueError('Evidence event aggregate/source mismatch')
        with self.store.transaction() as transaction:
            if not transaction.claim_event(event["event_id"]):
                return False
            self.process(event, transaction)
        return True


class PostgresStore:
    """Inject a DB-API connection factory scoped to the goal_evaluation DB.

    No driver is installed or connection opened by this module. Connections must
    have autocommit disabled. The transaction cursor is available to handlers so
    evaluation/outbox writes share the inbox transaction.
    """

    def __init__(self, connect: Callable, transaction_factory: Callable | None = None):
        self.connect = connect
        self.transaction_factory = transaction_factory or PostgresTransaction

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            connection = self.connect()
            try:
                if connection.autocommit:
                    raise ValueError("Inbox processing requires autocommit disabled")
                with connection.cursor() as cursor:
                    yield self.transaction_factory(cursor)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

        return scope()


class PostgresTransaction:
    def __init__(self, cursor):
        self.cursor = cursor

    def claim_event(self, event_id: str) -> bool:
        self.cursor.execute(
            "INSERT INTO inbox (event_id) VALUES (%s) "
            "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
            (event_id,),
        )
        return self.cursor.fetchone() is not None
