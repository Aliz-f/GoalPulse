"""Mock-adapter orchestration, not an approved HTTP API or broker worker.

All adapter methods are read-only, service-local seams. Wire DTOs, URLs and
service authentication must be supplied only after ICP-01 acceptance.
"""

from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Callable, Protocol, Sequence, TypeVar
from uuid import UUID

from .consumer import Transaction
from .domain import Contracts, Dispatcher, Document

T = TypeVar('T')


def uuid_string(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError('Identifier must be a UUID string')
    UUID(value)


@dataclass(frozen=True, order=True)
class Subscription:
    goal_id: str
    goal_version: int

    def __post_init__(self):
        uuid_string(self.goal_id)
        if type(self.goal_version) is not int or self.goal_version < 1:
            raise ValueError('Invalid subscription goal version')


@dataclass(frozen=True)
class Runtime:
    goal_id: str
    owner_user_id: str
    current_version: int
    lifecycle: str

    def __post_init__(self):
        uuid_string(self.goal_id)
        uuid_string(self.owner_user_id)
        if type(self.current_version) is not int or self.current_version < 1:
            raise ValueError('Invalid runtime goal version')
        if self.lifecycle not in {'active', 'paused', 'completed', 'cancelled'}:
            raise ValueError('Invalid runtime lifecycle')

    def permits(self, subscription: Subscription) -> bool:
        return (self.goal_id == subscription.goal_id and
                self.current_version == subscription.goal_version and
                self.lifecycle == 'active')


class SubscriptionReader(Protocol):
    def for_source(self, source_id: str, correlation_id: str) -> Sequence[Subscription]: ...
    def sources_for_goal(self, subscription: Subscription, correlation_id: str) -> Sequence[str]: ...


class GoalReader(Protocol):
    def runtime(self, goal_id: str, correlation_id: str) -> Runtime: ...
    def version(self, subscription: Subscription, correlation_id: str) -> Document: ...


class EvidenceReader(Protocol):
    def observation(self, observation_id: str, correlation_id: str) -> Document: ...
    def latest_for_source(self, source_id: str, correlation_id: str) -> Sequence[Document]: ...


@dataclass(frozen=True)
class EvaluationContext:
    """Local sink input retaining the exact goal/evidence used in evaluation."""

    goal: Document
    observations: Sequence[Document]
    correlation_id: str


class RetryableUpstreamError(Exception):
    """Adapters use this only for transient read failures, never invalid data."""


@dataclass(frozen=True)
class ReadRetry:
    attempts: int = 3
    initial_delay_seconds: float = 0.1

    def __post_init__(self):
        if type(self.attempts) is not int or not 1 <= self.attempts <= 5:
            raise ValueError('Read attempts must be between 1 and 5')
        if not 0 <= self.initial_delay_seconds <= 1:
            raise ValueError('Initial retry delay must be between 0 and 1 second')

    def call(self, read: Callable[[], T], wait: Callable[[float], None]) -> T:
        for attempt in range(self.attempts):
            try:
                return read()
            except RetryableUpstreamError:
                if attempt + 1 == self.attempts:
                    raise
                wait(min(self.initial_delay_seconds * 2**attempt, 1.0))
        raise AssertionError('Unreachable retry state')


class EvidenceProcessor:
    """Callable handler for EvidenceConsumer using injected mock readers.

    The sink must persist only through the supplied transaction, never publish or
    send notifications directly. Failure propagates to roll back the entire event
    (all subscribed goals and inbox claim). D4-GE-04 supplies a durable sink.
    Readers must impose their own transport deadlines and bounded pagination.
    """

    def __init__(
        self, contracts: Contracts, dispatcher: Dispatcher,
        subscriptions: SubscriptionReader, goals: GoalReader, evidence: EvidenceReader,
        sink: Callable[[Document, EvaluationContext, Transaction], None], clock: Callable[[], datetime],
        retry: ReadRetry = ReadRetry(), wait: Callable[[float], None] = sleep,
        max_entries: int = 1000,
    ):
        if type(max_entries) is not int or not 1 <= max_entries <= 10000:
            raise ValueError('Invalid processing bound')
        self.contracts, self.dispatcher = contracts, dispatcher
        self.subscriptions, self.goals, self.evidence = subscriptions, goals, evidence
        self.sink, self.clock, self.retry, self.wait = sink, clock, retry, wait
        self.max_entries = max_entries

    def _read(self, action: Callable[[], T]) -> T:
        return self.retry.call(action, self.wait)

    def _bounded(self, entries: Sequence[T]) -> Sequence[T]:
        if len(entries) > self.max_entries:
            raise ValueError('Adapter result exceeds processing bound; do not truncate silently')
        return entries

    def _runtime(self, subscription: Subscription, correlation: str) -> Runtime:
        runtime = self._read(lambda: self.goals.runtime(subscription.goal_id, correlation))
        if not isinstance(runtime, Runtime) or runtime.goal_id != subscription.goal_id:
            raise ValueError('Runtime does not match requested goal')
        return runtime

    def __call__(self, event: Document, transaction: Transaction) -> None:
        self.contracts.validate('domain-event', event)
        if event['event_type'] != 'evidence.updated.v1':
            raise ValueError('Expected evidence.updated.v1')
        source, oid = event['payload']['source_id'], event['payload']['observation_id']
        correlation = event['correlation_id']
        subscriptions = self._bounded(self._read(lambda: self.subscriptions.for_source(source, correlation)))
        if any(not isinstance(s, Subscription) for s in subscriptions):
            raise ValueError('Invalid subscription adapter result')
        triggered = None
        cache: dict[str, Document | None] = {}
        for subscription in sorted(set(subscriptions)):
            runtime = self._runtime(subscription, correlation)
            if not runtime.permits(subscription):
                continue
            goal = self._read(lambda: self.goals.version(subscription, correlation))
            self.contracts.validate('goal-spec', goal)
            if (goal['goal_id'], goal['version'], goal['owner_user_id']) != (
                subscription.goal_id, subscription.goal_version, runtime.owner_user_id,
            ):
                raise ValueError('Goal identity, owner or version mismatch')
            sources = self._bounded(self._read(lambda: self.subscriptions.sources_for_goal(subscription, correlation)))
            for linked_source in sources:
                uuid_string(linked_source)
            if source not in sources:
                raise ValueError('Trigger source is not linked to subscribed goal')
            if triggered is None:
                triggered = self._read(lambda: self.evidence.observation(oid, correlation))
                self.contracts.validate('observation', triggered)
                if (triggered['source_id'], triggered['observation_id']) != (source, oid):
                    raise ValueError('Trigger observation identity mismatch')
            observations = []
            for linked_source in sorted(set(sources)):
                if linked_source not in cache:
                    rows = self._bounded(self._read(lambda: self.evidence.latest_for_source(linked_source, correlation)))
                    seen: dict[str, Document] = {}
                    for row in rows:
                        self.contracts.validate('observation', row)
                        if row['source_id'] != linked_source:
                            raise ValueError('Evidence belongs to a different source')
                        if row['observation_id'] in seen and seen[row['observation_id']] != row:
                            raise ValueError('Conflicting immutable observation')
                        seen[row['observation_id']] = row
                    if linked_source == source:
                        if oid in seen and seen[oid] != triggered:
                            raise ValueError('Trigger snapshot differs from immutable observation')
                        # A nonempty DE latest read is canonical. An older
                        # citation must not mask a current downgrade/changed
                        # snapshot because its UUID happens to sort later.
                        if not seen:
                            seen[oid] = triggered
                        else:
                            newest = max(seen.values(), key=lambda row: (
                                datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')),
                                row['observation_id']))
                            if (newest['evidence']['reliability'] == 'checked'
                                    and datetime.fromisoformat(triggered['observed_at'].replace('Z', '+00:00')) >
                                    datetime.fromisoformat(newest['observed_at'].replace('Z', '+00:00'))):
                                # Do not restore an old cheap offer while the
                                # newest-first read lags behind a newer trigger.
                                raise RetryableUpstreamError('canonical_evidence_not_current')
                    cache[linked_source] = (max(seen.values(), key=lambda row: (
                        datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')),
                        row['observation_id'],
                    )) if seen else None)
                if cache[linked_source] is not None:
                    observations.append(cache[linked_source])
            result = self.dispatcher.evaluate(goal, observations, self.clock())
            # Final authoritative check also catches pause/version changes during evaluation.
            final = self._runtime(subscription, correlation)
            if final.permits(subscription) and final.owner_user_id == runtime.owner_user_id:
                self.sink(result, EvaluationContext(goal, observations, correlation), transaction)
