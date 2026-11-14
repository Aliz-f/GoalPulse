"""Verified-event preparation and owner-only reads with injected authenticated adapters."""
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Callable, ContextManager, Protocol, Sequence
from uuid import UUID

from .eligibility import Contracts, Document, Runtime
from .fingerprint import business_fingerprint, offer_fingerprint, same_offer_capture
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
    def has_offer(self, goal_id: str, version: int, fingerprint: str) -> bool: ...


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
                # The DE latest result is the current canonical snapshot. Do
                # not replace a downgrade/changed offer with the older citation
                # merely because the historical UUID sorts later.
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
            fingerprint = business_fingerprint(goal, observations)
            legacy = offer_fingerprint(goal, observations)
            if legacy != fingerprint and transaction.has_offer(goal['goal_id'], goal['version'], legacy):
                return 'deduplicated'
            return 'created' if transaction.insert_package(package, initial.owner_user_id,
                fingerprint, correlation) else 'deduplicated'


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
    def __init__(self, contract: PackageContract, store: Store, goals: Goals, clock: Callable[[], datetime],
                 evidence: Evidence | None = None, contracts: Contracts | None = None):
        if evidence is not None and contracts is None:
            raise ValueError('Evidence-backed reads require the frozen contracts')
        self.contract, self.store, self.goals, self.clock = contract, store, goals, clock
        self.evidence, self.contracts = evidence, contracts

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
        if self.evidence is not None:
            goal = self.goals.version(package['goal_id'], package['goal_version'], correlation_id)
            self.contracts.validate('goal-spec', goal)
            if (goal['goal_id'], goal['version'], goal['owner_user_id']) != (
                    package['goal_id'], package['goal_version'], principal.subject):
                raise PackageNotFound()
            age_limit = goal['monitoring']['max_evidence_age_minutes']
            if goal['goal_type'] == 'product_purchase':
                age_limit = min(age_limit, 30)
            deadlines = []
            # Runtime composition always supplies the authenticated DE reader.
            # Preserve the immutable original citation; never replace it with a
            # newer capture to renew the package's lifetime.
            for order in package['seller_orders']:
                for item in order['items']:
                    observation = self.evidence.get(item['observation_id'], correlation_id)
                    self.contracts.validate('observation', observation)
                    expected = dict(observation_id=item['observation_id'], source_url=item['source_url'],
                        observed_at=item['observed_at'], product_name=item['item_name'], selling_unit=item['unit'],
                        unit_price_eur_cents=item['unit_price_eur_cents'], tax_eur_cents=item['additional_tax_eur_cents'],
                        shipping_eur_cents=order['shipping_eur_cents'], availability='in_stock', delivery_to_fi='yes')
                    if any(observation.get(key) != value for key, value in expected.items()):
                        raise PackageGone()
                    if any(type(observation[key]) is not int for key in (
                            'unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents')):
                        raise PackageGone()
                    deadlines.append(datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
                                     + timedelta(minutes=age_limit))
                    rows = self.evidence.latest(observation['source_id'], correlation_id)
                    if not rows or len(rows) > 1000:
                        raise PackageGone()
                    seen = {}
                    for row in rows:
                        self.contracts.validate('observation', row)
                        if row['source_id'] != observation['source_id']:
                            raise PackageGone()
                        if any(type(row[key]) is not int for key in (
                                'unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents')):
                            raise PackageGone()
                        oid = row['observation_id']
                        if oid in seen and seen[oid] != row:
                            raise PackageGone()
                        seen[oid] = row
                    if item['observation_id'] in seen and seen[item['observation_id']] != observation:
                        raise PackageGone()
                    latest = max(rows, key=lambda row: (
                        datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')), row['observation_id']))
                    now = utc(self.clock)
                    latest_observed = datetime.fromisoformat(latest['observed_at'].replace('Z', '+00:00'))
                    if (not same_offer_capture(observation, latest)
                            or not timedelta(0) <= now - latest_observed < timedelta(minutes=age_limit)):
                        raise PackageGone()
                    deadlines.append(latest_observed + timedelta(minutes=age_limit))
            final = self.goals.runtime(package['goal_id'], correlation_id)
            expires = datetime.fromisoformat(package['expires_at'].replace('Z', '+00:00'))
            if final != runtime or expires > min(deadlines) or utc(self.clock) >= min(expires, min(deadlines)):
                raise PackageGone()
        return copy.deepcopy(package)
