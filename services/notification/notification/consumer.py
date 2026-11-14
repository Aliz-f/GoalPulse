"""Verified-event processing using local read-only adapter seams, not HTTP DTOs."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Callable, ContextManager, Protocol, Sequence, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import Contracts, Document
from .fingerprint import offer_fingerprint, public_url, same_offer_capture

T = TypeVar('T')
PRODUCT_CODES = {'identity_match', 'condition', 'availability', 'delivery', 'seller_policy',
                 'freshness', 'reliability', 'tax_known', 'shipping_known', 'total_within_budget'}
PROCUREMENT_CODES = {'full_basket_coverage', 'units_compatible', 'quality', 'availability',
                     'delivery', 'seller_limit', 'freshness', 'reliability',
                     'shipping_known_by_seller', 'tax_known', 'total_within_budget'}


@dataclass(frozen=True)
class Runtime:
    goal_id: str
    owner_user_id: str
    current_version: int
    lifecycle: str

    def __post_init__(self):
        UUID(self.goal_id)
        UUID(self.owner_user_id)
        if type(self.current_version) is not int or self.current_version < 1:
            raise ValueError('Invalid runtime version')
        if self.lifecycle not in {'active', 'paused', 'completed', 'cancelled'}:
            raise ValueError('Invalid runtime lifecycle')

    def active(self, goal_id: str, version: int) -> bool:
        return self.goal_id == goal_id and self.current_version == version and self.lifecycle == 'active'


class GoalReader(Protocol):
    def runtime(self, goal_id: str, correlation_id: str) -> Runtime: ...
    def version(self, goal_id: str, version: int, correlation_id: str) -> Document: ...


class EvaluationReader(Protocol):
    def get(self, evaluation_id: str, correlation_id: str) -> Document: ...


class EvidenceReader(Protocol):
    def get(self, observation_id: str, correlation_id: str) -> Document: ...
    def latest(self, source_id: str, correlation_id: str) -> Sequence[Document]: ...


class Transaction(Protocol):
    def claim_event(self, event_id: str) -> bool: ...
    def in_app_enabled(self, user_id: str, *, lock: bool = False) -> bool: ...
    def insert_notification(self, notification: Document) -> bool: ...


class Store(Protocol):
    def transaction(self) -> ContextManager[Transaction]: ...


class RetryableReadError(Exception):
    """Adapters map only transient failures to this type; validation never retries."""


class NotificationConsumer:
    def __init__(self, contracts: Contracts, store: Store, goals: GoalReader,
                 evaluations: EvaluationReader, evidence: EvidenceReader,
                 clock: Callable[[], datetime], wait: Callable[[float], None] = sleep,
                 read_attempts: int = 3):
        if type(read_attempts) is not int or not 1 <= read_attempts <= 5:
            raise ValueError('Read attempts must be between 1 and 5')
        self.contracts, self.store, self.goals = contracts, store, goals
        self.evaluations, self.evidence, self.clock = evaluations, evidence, clock
        self.wait, self.read_attempts = wait, read_attempts

    def _read(self, read: Callable[[], T]) -> T:
        for attempt in range(self.read_attempts):
            try:
                return read()
            except RetryableReadError:
                if attempt + 1 == self.read_attempts:
                    raise
                self.wait(min(0.1 * 2**attempt, 1))
        raise AssertionError('Unreachable')

    def _runtime(self, goal_id: str, correlation: str) -> Runtime:
        result = self._read(lambda: self.goals.runtime(goal_id, correlation))
        if not isinstance(result, Runtime) or result.goal_id != goal_id:
            raise ValueError('Runtime identity mismatch')
        return result

    def handle(self, event: Document) -> str:
        """Return only after commit. Broker ACK belongs after this method returns.

        Suppressed/duplicate events are durable no-ops. Exceptions roll back the
        inbox and notification, and propagate to the broker's bounded retry/DLQ.
        """
        self.contracts.validate('domain-event', event)
        if event['event_type'] != 'goal.opportunity_verified.v1':
            raise ValueError('Notification consumes only goal.opportunity_verified.v1')
        payload, correlation = event['payload'], event['correlation_id']
        gid, version, eid = payload['goal_id'], payload['goal_version'], payload['evaluation_id']
        if event['aggregate_id'] != gid:
            raise ValueError('Event aggregate identity mismatch')
        with self.store.transaction() as transaction:
            if not transaction.claim_event(event['event_id']):
                return 'duplicate'
            runtime = self._runtime(gid, correlation)
            if not runtime.active(gid, version):
                return 'suppressed'
            result = self._read(lambda: self.evaluations.get(eid, correlation))
            self.contracts.validate('goal-evaluation', result)
            if (result['evaluation_id'], result['goal_id'], result['goal_version']) != (eid, gid, version):
                raise ValueError('Evaluation identity mismatch')
            if not result['is_opportunity_verified'] or result['status'] != 'satisfied':
                return 'suppressed'
            goal = self._read(lambda: self.goals.version(gid, version, correlation))
            self.contracts.validate('goal-spec', goal)
            if (goal['goal_id'], goal['version'], goal['owner_user_id']) != (gid, version, runtime.owner_user_id):
                raise ValueError('Goal identity or owner mismatch')
            if not goal['actions']['notify_user'] or not transaction.in_app_enabled(runtime.owner_user_id):
                return 'disabled'
            required = PRODUCT_CODES if goal['goal_type'] == 'product_purchase' else PROCUREMENT_CODES
            codes = [c['code'] for c in result['conditions']]
            candidates = set(result['candidate_observation_ids'])
            if (not required <= set(codes) or len(codes) != len(set(codes)) or not candidates or len(candidates) > 200 or
                any(not c['observation_ids'] or not set(c['observation_ids']) <= candidates for c in result['conditions'])):
                return 'suppressed'
            observations, corroborating_captures = [], []
            for oid in sorted(candidates):
                observation = self._read(lambda: self.evidence.get(oid, correlation))
                self.contracts.validate('observation', observation)
                if observation['observation_id'] != oid:
                    raise ValueError('Evidence identity mismatch')
                for key in ('unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'):
                    if observation[key] is not None and type(observation[key]) is not int:
                        raise ValueError('Money must use integer EUR cents')
                if (observation['availability'] != 'in_stock' or observation['delivery_to_fi'] != 'yes' or
                    observation['shipping_eur_cents'] is None or observation['tax_eur_cents'] is None or
                    observation['evidence']['reliability'] != 'checked' or public_url(observation['source_url']) is None):
                    return 'suppressed'
                rows = self._read(lambda: self.evidence.latest(observation['source_id'], correlation))
                if not rows or len(rows) > 1000:
                    return 'suppressed'
                seen = {}
                for row in rows:
                    self.contracts.validate('observation', row)
                    if row['source_id'] != observation['source_id']:
                        raise ValueError('Latest evidence source mismatch')
                    rid = row['observation_id']
                    if rid in seen and seen[rid] != row:
                        raise ValueError('Conflicting immutable observation')
                    seen[rid] = row
                if oid in seen and seen[oid] != observation:
                    raise ValueError('Conflicting immutable observation')
                seen[oid] = observation
                latest = max(seen.values(), key=lambda row: (
                    datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')), row['observation_id']))
                if not same_offer_capture(observation, latest):
                    return 'suppressed'
                corroborating_captures.append(latest)
                observations.append(observation)
            # Lock only at the final delivery boundary, after evidence reads.
            # Preference updates then serialize with creation of the in-app row.
            if not transaction.in_app_enabled(runtime.owner_user_id, lock=True):
                return 'disabled'
            final = self._runtime(gid, correlation)
            if not final.active(gid, version) or final.owner_user_id != runtime.owner_user_id:
                return 'suppressed'
            now = self.clock()
            if now.utcoffset() != timedelta(0):
                raise ValueError('Clock must be timezone-aware UTC')
            if (goal['criteria']['delivery_country'] != 'FI' or
                datetime.fromisoformat(result['evaluated_at'].replace('Z', '+00:00')) > now or
                any(not timedelta(0) <= now - datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00')) <=
                    timedelta(minutes=goal['monitoring']['max_evidence_age_minutes'])
                    for o in observations + corroborating_captures)):
                return 'suppressed'
            fingerprint = offer_fingerprint(goal, observations)
            note = dict(notification_id=str(uuid5(NAMESPACE_URL,
                        f'in_app:{runtime.owner_user_id}:{gid}:{version}:{fingerprint}')),
                        user_id=runtime.owner_user_id, goal_id=gid, goal_version=version,
                        evaluation_id=eid, offer_fingerprint=fingerprint, channel='in_app', status='unread',
                        title='Verified opportunity available',
                        summary='Evidence supports an available option. Price and availability may change at checkout; revalidation and your approval are required.',
                        evaluated_at=result['evaluated_at'], created_at=now.isoformat().replace('+00:00', 'Z'),
                        correlation_id=correlation,
                        evidence=[dict(observation_id=o['observation_id'], source_url=public_url(o['source_url']),
                                       observed_at=o['observed_at']) for o in observations])
            return 'created' if transaction.insert_notification(note) else 'deduplicated'
