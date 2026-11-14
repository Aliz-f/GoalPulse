"""One-message transactional outbox relay using an injected confirmed publisher."""

from datetime import datetime, timedelta
from typing import Callable, Protocol

from jsonschema import ValidationError

from .consumer import Store
from .domain import Contracts, Document
from .persistence import VerificationGuard, canonical, utc_now
from .processing import EvaluationContext, EvidenceReader, GoalReader, Subscription
from .offer_identity import same_offer_capture


class ConfirmedBroker(Protocol):
    """Adapter must set deadlines, enable confirms and fail on unroutable returns.

    RabbitMQ exchange must be durable topic goalpulse.events. Infrastructure owns
    durable queues and bindings. This interface adds no broker library dependency.
    """

    def publish_confirmed(self, exchange: str, routing_key: str, body: bytes, persistent: bool) -> bool: ...


class PublicationGuard:
    """Fetch immutable evaluation/evidence and recheck current state before relay."""

    def __init__(self, contracts: Contracts, guard: VerificationGuard,
                 goals: GoalReader, evidence: EvidenceReader):
        self.contracts, self.guard, self.goals, self.evidence = contracts, guard, goals, evidence

    def __call__(self, event: Document, transaction) -> bool:
        result = transaction.get_evaluation(event['payload']['evaluation_id'])
        if result is None:
            raise ValueError('Outbox evaluation is missing')
        self.contracts.validate('goal-evaluation', result)
        if (result['evaluation_id'], result['goal_id'], result['goal_version']) != (
            event['payload']['evaluation_id'], event['payload']['goal_id'], event['payload']['goal_version'],
        ):
            raise ValueError('Outbox evaluation identity mismatch')
        correlation = event['correlation_id']
        goal = self.goals.version(Subscription(result['goal_id'], result['goal_version']), correlation)
        observations = []
        for oid in result['candidate_observation_ids']:
            observation = self.evidence.observation(oid, correlation)
            self.contracts.validate('observation', observation)
            if observation['observation_id'] != oid:
                raise ValueError('Publication evidence identity mismatch')
            # A newer contradictory snapshot must not be replaced by this old offer.
            rows = self.evidence.latest_for_source(observation['source_id'], correlation)
            if not rows or len(rows) > 1000:
                return False
            seen = {}
            for row in rows:
                self.contracts.validate('observation', row)
                if row['source_id'] != observation['source_id']:
                    raise ValueError('Publication evidence source mismatch')
                if row['observation_id'] in seen and seen[row['observation_id']] != row:
                    raise ValueError('Publication evidence snapshot conflict')
                seen[row['observation_id']] = row
            if oid in seen and seen[oid] != observation:
                raise ValueError('Publication evidence snapshot conflict')
            # Validate the cited record, but choose current state only from the
            # DE latest read. Re-inserting history can hide a same-time downgrade.
            latest = max(seen.values(), key=lambda row: (
                datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')), row['observation_id'],
            ))
            if (not same_offer_capture(observation, latest) or
                datetime.fromisoformat(latest['observed_at'].replace('Z', '+00:00')) > utc_now(self.guard.clock)):
                return False
            observations.append(observation)
        return self.guard.eligible(result, EvaluationContext(goal, observations, correlation))


class UnconfirmedPublish(Exception):
    pass


class OutboxPublisher:
    """Lock one due row until confirm/commit. A crash may replay the SAME event ID.

    This is at-least-once publication. Consumers still need durable inbox dedup.
    An unconfirmed/failed publish remains pending with bounded exponential retry;
    terminal failures stay stored for operator inspection, never silently deleted.
    Store must yield a PostgresEvaluationTransaction-compatible transaction.
    """

    def __init__(self, contracts: Contracts, store: Store, broker: ConfirmedBroker,
                 eligible: Callable, clock: Callable[[], datetime], max_attempts: int = 5):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError('Publish attempts must be between 1 and 20')
        self.contracts, self.store, self.broker = contracts, store, broker
        self.eligible, self.clock, self.max_attempts = eligible, clock, max_attempts

    def run_once(self) -> bool:
        now = utc_now(self.clock)
        with self.store.transaction() as transaction:
            row = transaction.next_pending(now)
            if row is None:
                return False
            event_id, event, previous_attempts = row
            attempts = previous_attempts + 1
            try:
                self.contracts.validate('domain-event', event)
                if event['producer'] != 'goal-evaluation' or event['event_type'] not in {
                    'goal.evaluated.v1', 'goal.opportunity_verified.v1',
                } or event['event_id'] != event_id:
                    raise ValueError('Invalid outbox producer, type or identity')
                if attempts > self.max_attempts:
                    transaction.mark_failed(event_id, previous_attempts, now, True, 'attempt_limit')
                    return True
                if event['event_type'] == 'goal.opportunity_verified.v1' and not self.eligible(event, transaction):
                    transaction.mark_failed(event_id, attempts, now, True, 'verification_suppressed')
                    return True
                confirmed = self.broker.publish_confirmed(
                    'goalpulse.events', event['event_type'], canonical(event).encode(), persistent=True,
                )
                if confirmed is not True:
                    raise UnconfirmedPublish()
            except (ValueError, ValidationError, PermissionError):
                transaction.mark_failed(event_id, attempts, now, True, 'invalid_or_unauthorized')
            except Exception:
                retry_at = utc_now(self.clock) + timedelta(seconds=min(2**min(attempts - 1, 9), 300))
                transaction.mark_failed(event_id, attempts, retry_at,
                                        attempts >= self.max_attempts, 'publish_or_read_failed')
            else:
                # Commit failure propagates: a broker-confirmed event may be replayed.
                transaction.mark_published(event_id, utc_now(self.clock))
        return True
