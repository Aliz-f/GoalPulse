"""P0 composition; D1 owns process startup, secrets and private deployment."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .consumer import EvidenceConsumer, PostgresStore
from .domain import Contracts, Dispatcher
from .outbox import OutboxPublisher, PublicationGuard
from .persistence import (
    EvaluationSink, ImmutableEvaluationReader, PostgresEvaluationTransaction, VerificationGuard,
)
from .processing import EvidenceProcessor, Subscription
from .product import ProductPurchaseEvaluator
from .procurement import RecurringProcurementEvaluator
from .readers import EvidenceHttpReader, GoalHttpReader, HttpTransport, InternalClient, SubscriptionHttpReader
from .retry import PostgresRetryTransaction, RetryCoordinator
from .worker import ConfirmedPublisher, EvaluationWorker


class CompleteSubscriptions:
    """Combine accepted source→goals HTTP with D1/D3's approved reverse binding.

    No reverse URL or DTO is invented here. A caller must supply the complete
    exact-version lookup once approved, or explicit recorded test input.
    """

    def __init__(self, reader: SubscriptionHttpReader, reverse: Callable):
        if not callable(reverse):
            raise ValueError('Approved complete goal-to-sources binding required')
        self.reader, self.reverse = reader, reverse

    def for_source(self, source_id: str, correlation_id: str):
        return self.reader.for_source(source_id, correlation_id)

    def sources_for_goal(self, subscription: Subscription, correlation_id: str):
        return self.reverse(subscription, correlation_id)


@dataclass(frozen=True)
class EvaluationRuntime:
    worker: EvaluationWorker
    reader: ImmutableEvaluationReader
    consumer: EvidenceConsumer
    publisher: OutboxPublisher
    ready: Callable[[], bool]
    goals: GoalHttpReader


def build_runtime(*, connect_database: Callable, broker_connection, queue: str,
                  dead_letter_exchange: str, dead_letter_routing_key: str,
                  schemas: Path, gm_origin: str, dm_origin: str, de_origin: str,
                  tokens: Callable[[str], str], profile: str,
                  sources_for_goal: Callable[[Subscription, str], Sequence[str]],
                  dependencies_ready: Callable[[], bool],
                  clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                  transport=None, properties_factory=None, test_stores: tuple | None = None,
                  procurement_normalization_confirmed: bool = False) -> EvaluationRuntime:
    """Build the actual consumer/sink/outbox graph, with no implicit fixtures.

    Missing reverse lookup is a startup error. D1's dependencies_ready must also
    verify the approved binding and required upstream configuration. Live
    readiness stays false until that integration exists. D1 provisions DLX/DLQ
    bindings and connection deadlines; apply owned migrations 001..004 first.
    """
    if not callable(sources_for_goal) or not callable(dependencies_ready):
        raise ValueError('Complete approved source lookup and dependency readiness required')
    if test_stores is not None and (profile != 'test' or not isinstance(test_stores, tuple) or len(test_stores) != 2):
        raise ValueError('Recorded stores require explicit test profile and store/journal pair')
    if type(procurement_normalization_confirmed) is not bool:
        raise ValueError('Explicit boolean normalization policy required')
    if procurement_normalization_confirmed and profile != 'test':
        raise ValueError('Confirmed live basket normalization awaits the approved D3 handoff')
    contracts = Contracts(schemas)
    transport = transport if transport is not None else HttpTransport()
    goals = GoalHttpReader(InternalClient(gm_origin, tokens, transport), contracts)
    evidence = EvidenceHttpReader(InternalClient(de_origin, tokens, transport), contracts, profile=profile)
    subscriptions = CompleteSubscriptions(SubscriptionHttpReader(InternalClient(dm_origin, tokens, transport)),
                                          sources_for_goal)
    if test_stores is None:
        store = PostgresStore(connect_database, transaction_factory=PostgresEvaluationTransaction)
        journal = PostgresStore(connect_database, transaction_factory=PostgresRetryTransaction)
    else:
        store, journal = test_stores
    evaluator = ProductPurchaseEvaluator(contracts)
    procurement = RecurringProcurementEvaluator(contracts,
        normalized_cost_policy_confirmed=procurement_normalization_confirmed)
    dispatcher = Dispatcher(contracts, {'product_purchase': evaluator, 'recurring_procurement': procurement})
    guard = VerificationGuard(contracts, dispatcher, goals, clock)
    sink = EvaluationSink(contracts, guard, {'product_purchase': evaluator.version,
                                           'recurring_procurement': procurement.version})
    consumer = EvidenceConsumer(contracts, store, EvidenceProcessor(
        contracts, dispatcher, subscriptions, goals, evidence, sink, clock))
    coordinator = RetryCoordinator(contracts, journal, consumer, clock)
    broker = ConfirmedPublisher(broker_connection.channel(), properties_factory)
    publisher = OutboxPublisher(contracts, store, broker,
                               PublicationGuard(contracts, guard, goals, evidence), clock)

    def ready() -> bool:
        try:
            if (not broker.usable or dependencies_ready() is not True or broker_connection.is_open is not True
                    or broker.channel.is_open is not True or worker.channel.is_open is not True):
                return False
            if test_stores is not None:
                return True  # Explicit recorded mode; no SQL readiness claim.
            with store.transaction() as transaction:
                transaction.cursor.execute('SELECT verification_event_id FROM offer_fingerprints LIMIT 0')
                transaction.cursor.execute('SELECT event_id,lease_token FROM evaluation_processing LIMIT 0')
                transaction.cursor.execute('SELECT evaluation_id FROM evaluations LIMIT 0')
                transaction.cursor.execute('SELECT event_id FROM outbox LIMIT 0')
            return True
        except Exception:
            return False

    worker = EvaluationWorker(broker_connection, coordinator, publisher, queue=queue,
        dead_letter_exchange=dead_letter_exchange, dead_letter_routing_key=dead_letter_routing_key, ready=ready)
    return EvaluationRuntime(worker, ImmutableEvaluationReader(contracts, store), consumer, publisher, ready, goals)
