"""Verified-event preparation and owner-only reads with injected authenticated adapters."""
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Callable, ContextManager, Protocol, Sequence
from uuid import UUID

from .eligibility import Contracts, Document, Runtime
from .fingerprint import offer_fingerprint, same_offer_capture
from .packages import PackageBuilder, PackageContract


class Goals(Protocol):
    def runtime(self, goal_id: str, correlation_id: str) -> Runtime: ...
    def version(self, goal_id: str, version: int, correlation_id: str) -> Document: ...


class Evaluations(Protocol):
    def get(self, evaluation_id: str, correlation_id: str) -> Document: ...


class Evidence(Protocol):
    def get(self, observation_id: str, correlation_id: str) -> Document: ...
    def latest(self, source_id: str, correlation_id: str) -> Sequence[Document]: ...


class Transaction(Protocol):
    def claim_event(self, event_id: str) -> bool: ...
    def insert_package(self, package: Document, owner: str, fingerprint: str, correlation: str) -> bool: ...
    def get_owned(self, package_id: str, owner: str) -> Document | None: ...


class Store(Protocol):
    def transaction(self) -> ContextManager[Transaction]: ...


def utc(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
        raise ValueError('Clock must be timezone-aware UTC')
    return now


class RetryableReadError(Exception):
    pass


class PackageConsumer:
    def __init__(self, contracts: Contracts, builder: PackageBuilder, store: Store,
                 goals: Goals, evaluations: Evaluations, evidence: Evidence,
                 clock: Callable[[], datetime], wait: Callable[[float], None] = sleep):
        self.contracts, self.builder, self.store = contracts, builder, store
        self.goals, self.evaluations, self.evidence = goals, evaluations, evidence
        self.clock, self.wait = clock, wait

    def _read(self, read):
        for attempt in range(3):
            try:
                return read()
            except RetryableReadError:
                if attempt == 2:
                    raise
                self.wait(0.1 * 2**attempt)

    def handle(self, event: Document) -> str:
        self.contracts.validate('domain-event', event)
        if event['event_type'] != 'goal.opportunity_verified.v1':
            raise ValueError('Expected verified event')
        p, correlation = event['payload'], event['correlation_id']
        if event['aggregate_id'] != p['goal_id']:
            raise ValueError('Aggregate mismatch')
        with self.store.transaction() as transaction:
            if not transaction.claim_event(event['event_id']):
                return 'duplicate'
            initial = self._read(lambda: self.goals.runtime(p['goal_id'], correlation))
            if not isinstance(initial, Runtime) or initial.goal_id != p['goal_id']:
                raise ValueError('Runtime identity mismatch')
            if initial.lifecycle != 'active' or initial.current_version != p['goal_version']:
                return 'suppressed'
            evaluation = self._read(lambda: self.evaluations.get(p['evaluation_id'], correlation))
            self.contracts.validate('goal-evaluation', evaluation)
            if (evaluation['evaluation_id'], evaluation['goal_id'], evaluation['goal_version']) != (
                p['evaluation_id'], p['goal_id'], p['goal_version']):
                raise ValueError('Evaluation identity mismatch')
            if not evaluation['is_opportunity_verified']:
                return 'suppressed'
            goal = self._read(lambda: self.goals.version(p['goal_id'], p['goal_version'], correlation))
            self.contracts.validate('goal-spec', goal)
            if (goal['goal_id'], goal['version'], goal['owner_user_id']) != (
                p['goal_id'], p['goal_version'], initial.owner_user_id):
                raise ValueError('Goal identity/owner mismatch')
            if not 1 <= len(evaluation['candidate_observation_ids']) <= 200:
                return 'suppressed'
            observations, corroborating_captures = [], []
            for oid in sorted(evaluation['candidate_observation_ids']):
                observation = self._read(lambda: self.evidence.get(oid, correlation))
                self.contracts.validate('observation', observation)
                if observation['observation_id'] != oid:
                    raise ValueError('Observation identity mismatch')
                rows = self._read(lambda: self.evidence.latest(observation['source_id'], correlation))
                if not rows or len(rows) > 1000:
                    return 'suppressed'
                seen = {}
                for row in rows:
                    self.contracts.validate('observation', row)
                    rid = row['observation_id']
                    if row['source_id'] != observation['source_id'] or (rid in seen and seen[rid] != row):
                        raise ValueError('Source identity or immutable snapshot mismatch')
                    seen[rid] = row
                if oid in seen and seen[oid] != observation:
                    raise ValueError('Immutable observation mismatch')
                seen[oid] = observation
                latest = max(seen.values(), key=lambda o: (
                    datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00')), o['observation_id']))
                if not same_offer_capture(observation, latest):
                    return 'suppressed'
                corroborating_captures.append(latest)
                observations.append(observation)
            final = self._read(lambda: self.goals.runtime(p['goal_id'], correlation))
            if not isinstance(final, Runtime) or final != initial:
                return 'suppressed'
            now = utc(self.clock)
            if any(not timedelta(0) <= now - datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00')) <=
                   timedelta(minutes=goal['monitoring']['max_evidence_age_minutes']) for o in corroborating_captures):
                return 'suppressed'
            package = self.builder.build(event, goal, evaluation, observations, final, now)
            if package is None or utc(self.clock) >= datetime.fromisoformat(package['expires_at'].replace('Z', '+00:00')):
                return 'suppressed'
            return 'created' if transaction.insert_package(package, initial.owner_user_id,
                offer_fingerprint(goal, observations), correlation) else 'deduplicated'


@dataclass(frozen=True)
class Principal:
    """Construct only after validating server-side claims; UUID validation is not auth."""
    subject: str

    def __post_init__(self):
        if not isinstance(self.subject, str):
            raise ValueError('Invalid subject')
        object.__setattr__(self, 'subject', str(UUID(self.subject)))


class AuthenticationRequired(Exception):
    pass


class PackageNotFound(Exception):
    pass


class PackageGone(Exception):
    pass


class PackageReader:
    def __init__(self, contract: PackageContract, store: Store, goals: Goals, clock: Callable[[], datetime]):
        self.contract, self.store, self.goals, self.clock = contract, store, goals, clock

    def get(self, package_id: str, principal: Principal, correlation_id: str) -> Document:
        if not isinstance(principal, Principal):
            raise AuthenticationRequired()
        package_id = str(UUID(package_id))
        correlation_id = str(UUID(correlation_id))
        with self.store.transaction() as transaction:
            package = transaction.get_owned(package_id, principal.subject)
        if package is None:
            raise PackageNotFound()
        self.contract.validate(package)
        if package['package_id'] != package_id:
            raise ValueError('Stored package identity mismatch')
        runtime = self.goals.runtime(package['goal_id'], correlation_id)
        if not isinstance(runtime, Runtime) or runtime.goal_id != package['goal_id'] or runtime.owner_user_id != principal.subject:
            raise PackageNotFound()
        if (runtime.lifecycle != 'active' or runtime.current_version != package['goal_version'] or
            utc(self.clock) >= datetime.fromisoformat(package['expires_at'].replace('Z', '+00:00'))):
            raise PackageGone()
        return copy.deepcopy(package)
