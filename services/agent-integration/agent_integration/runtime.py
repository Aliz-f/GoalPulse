"""Agent Integration composition with real adapters and explicit test seams."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .eligibility import Contracts, HandoffEligibility
from .packages import PackageBuilder, PackageContract
from .readers import EvidenceHttpReader, EvaluationHttpReader, GoalHttpReader, HttpTransport, InternalClient
from .retry import PostgresRetryTransaction, RetryCoordinator
from .service import PackageConsumer, PackageReader
from .store import PostgresStore
from .worker import PackageWorker


@dataclass(frozen=True)
class PackageRuntime:
    worker: PackageWorker
    consumer: PackageConsumer
    reader: PackageReader
    ready: Callable[[], bool]


def build_runtime(*, connect_database: Callable, broker_connection, queue: str,
                  dead_letter_exchange: str, dead_letter_routing_key: str,
                  schemas: Path, gm_origin: str, ge_origin: str, de_origin: str,
                  tokens: Callable[[str], str], evaluation_read_scope: str,
                  profile: str, dependencies_ready: Callable[[], bool],
                  clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                  transport=None, test_stores: tuple | None = None) -> PackageRuntime:
    """Construct owned SQL + verified worker + current-evidence package reads.

    D1 binds actual connection factories, service-token issuer/operation scope,
    private Gateway owner auth, DLQ destination and supervisor/timeouts. Recorded
    tests inject transports explicitly; demo cannot accept fixture provenance.
    No connection is opened on import and no automatic fixture fallback exists.
    """
    if not callable(dependencies_ready):
        raise ValueError('Explicit dependency readiness required')
    if test_stores is not None and (profile != 'test' or not isinstance(test_stores, tuple) or len(test_stores) != 2):
        raise ValueError('Recorded stores require an explicit test profile and store/journal pair')
    contracts, package_contract = Contracts(schemas), PackageContract()
    transport = transport if transport is not None else HttpTransport()
    goals = GoalHttpReader(InternalClient(gm_origin, tokens, transport), contracts)
    evaluations = EvaluationHttpReader(InternalClient(ge_origin, tokens, transport),
                                       contracts, scope=evaluation_read_scope)
    evidence = EvidenceHttpReader(InternalClient(de_origin, tokens, transport), contracts, profile=profile)
    if test_stores is None:
        store = PostgresStore(connect_database)
        journal = PostgresStore(connect_database, transaction_factory=PostgresRetryTransaction)
    else:
        store, journal = test_stores
    builder = PackageBuilder(HandoffEligibility(contracts), package_contract)
    consumer = PackageConsumer(contracts, builder, store, goals, evaluations, evidence, clock)
    coordinator = RetryCoordinator(contracts, journal, consumer, clock)

    def ready() -> bool:
        try:
            if (dependencies_ready() is not True or broker_connection.is_open is not True
                    or worker.channel.is_open is not True):
                return False
            if test_stores is not None:
                return True  # Explicit test mode only; no SQL readiness claim.
            with store.transaction() as tx:
                tx.cursor.execute('SELECT event_id,lease_token FROM package_processing LIMIT 0')
                tx.cursor.execute('SELECT package_id,owner_user_id,offer_fingerprint FROM action_packages LIMIT 0')
                tx.cursor.execute('SELECT event_id FROM inbox LIMIT 0')
            return True
        except Exception:
            return False

    worker = PackageWorker(broker_connection, coordinator, queue=queue,
        dead_letter_exchange=dead_letter_exchange, dead_letter_routing_key=dead_letter_routing_key, ready=ready)
    reader = PackageReader(package_contract, store, goals, clock, evidence=evidence, contracts=contracts)
    return PackageRuntime(worker, consumer, reader, ready)
