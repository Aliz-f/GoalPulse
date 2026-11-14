"""Composition for D1's private runtime builder; no connections on import."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .consumer import NotificationConsumer
from .contracts import Contracts
from .readers import (
    EvidenceHttpReader, EvaluationHttpReader, GoalHttpReader, HttpTransport, InternalClient,
)
from .retry import RetryCoordinator
from .store import PostgresStore
from .worker import NotificationWorker


def build_worker(*, connect_database: Callable, broker_connection, queue: str,
                 dead_letter_exchange: str, dead_letter_routing_key: str,
                 schemas: Path, gm_origin: str, ge_origin: str, de_origin: str,
                 tokens: Callable[[str], str], evaluation_read_scope: str,
                 profile: str, rate_limit_minutes: int = 60,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 transport=None) -> NotificationWorker:
    """Wire real readers, owned SQL store, durable journal and Rabbit delivery.

    D1 supplies DB/broker credentials, the provisioned DLQ-backed durable queue,
    and accepted service-token issuer. This function never creates a database,
    applies migrations, installs packages or falls back to fixtures. An injected
    transport supports explicitly recorded tests; deployment uses HttpTransport.
    """
    contracts = Contracts(schemas)
    transport = transport if transport is not None else HttpTransport()
    goals = GoalHttpReader(InternalClient(gm_origin, tokens, transport), contracts)
    evaluations = EvaluationHttpReader(InternalClient(ge_origin, tokens, transport),
                                       contracts, scope=evaluation_read_scope)
    evidence = EvidenceHttpReader(InternalClient(de_origin, tokens, transport),
                                  contracts, profile=profile)
    store = PostgresStore(connect_database)
    consumer = NotificationConsumer(contracts, store, goals, evaluations, evidence,
                                    clock, rate_limit_minutes=rate_limit_minutes)
    coordinator = RetryCoordinator(contracts, store, consumer, clock)
    return NotificationWorker(broker_connection, coordinator, queue=queue,
                               dead_letter_exchange=dead_letter_exchange,
                               dead_letter_routing_key=dead_letter_routing_key)
