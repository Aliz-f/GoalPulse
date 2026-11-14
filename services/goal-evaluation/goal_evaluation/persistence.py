"""Service-owned PostgreSQL evaluation/outbox persistence; no driver or HTTP I/O."""

import copy
import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from .consumer import PostgresTransaction, Store
from .domain import Contracts, Dispatcher, Document
from .processing import EvaluationContext, GoalReader, Runtime, Subscription, uuid_string
from .offer_identity import offer_fingerprint as snapshot_fingerprint


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.utcoffset() != timedelta(0):
        raise ValueError('Clock must be timezone-aware UTC')
    return now


def evaluation_fingerprint(result: Document, evaluator_version: str) -> str:
    """Same evidence/decision is meaningful once, even if evaluated again later."""
    return digest({'evaluator_version': evaluator_version, 'result': {
        key: value for key, value in result.items() if key not in {'evaluation_id', 'evaluated_at'}
    }})


def offer_fingerprint(result: Document, context: EvaluationContext) -> str:
    """Exclude capture IDs/time; repeated observations of the same offer dedup.

    Keep full listing path/query: variants can be different offers. Seller is the
    normalized source hostname, not an invented merchant identifier. Any changed
    payable component creates a different offer, scoped to the immutable goal.
    """
    selected = set(result['candidate_observation_ids'])
    observations = [o for o in context.observations if o['observation_id'] in selected]
    if len(observations) != len(selected) or not selected:
        raise ValueError('Offer fingerprint requires every selected observation exactly once')
    return snapshot_fingerprint(context.goal, observations)


class EvaluationTransaction(Protocol):
    def insert_evaluation(self, result: Document, fingerprint: str, evaluator_version: str) -> bool: ...
    def reserve_offer(self, result: Document, fingerprint: str, event_id: str) -> bool: ...
    def enqueue_event(self, event: Document) -> None: ...
    def get_evaluation(self, evaluation_id: str) -> Document | None: ...


class ImmutableEvaluationReader:
    """Validated local read boundary, not an authenticated HTTP endpoint."""

    def __init__(self, contracts: Contracts, store: Store):
        self.contracts, self.store = contracts, store

    def get(self, evaluation_id: str) -> Document | None:
        uuid_string(evaluation_id)
        with self.store.transaction() as transaction:
            result = transaction.get_evaluation(evaluation_id)
            if result is not None:
                self.contracts.validate('goal-evaluation', result)
                if result['evaluation_id'] != evaluation_id:
                    raise ValueError('Stored evaluation identity mismatch')
            return copy.deepcopy(result)


class VerificationGuard:
    """Recompute domain gates with a current clock, then check authoritative state."""

    def __init__(self, contracts: Contracts, dispatcher: Dispatcher, goals: GoalReader,
                 clock: Callable[[], datetime]):
        self.contracts, self.dispatcher, self.goals, self.clock = contracts, dispatcher, goals, clock

    def eligible(self, result: Document, context: EvaluationContext) -> bool:
        self.contracts.validate('goal-evaluation', result)
        self.contracts.validate('goal-spec', context.goal)
        if (result['goal_id'], result['goal_version']) != (context.goal['goal_id'], context.goal['version']):
            raise ValueError('Verification goal/version mismatch')
        if not result['is_opportunity_verified']:
            return False
        runtime = self.goals.runtime(result['goal_id'], context.correlation_id)
        if not (isinstance(runtime, Runtime) and runtime.permits(
            Subscription(result['goal_id'], result['goal_version'])) and
            runtime.owner_user_id == context.goal['owner_user_id']):
            return False
        # Read the clock after potentially slow GM I/O, so latency cannot extend
        # the usable lifetime of an observation checked just before expiration.
        now = utc_now(self.clock)
        if datetime.fromisoformat(result['evaluated_at'].replace('Z', '+00:00')) > now:
            return False
        recomputed = self.dispatcher.evaluate(context.goal, context.observations, now)
        if not recomputed['is_opportunity_verified'] or (
            recomputed['candidate_observation_ids'] != result['candidate_observation_ids']
        ):
            return False
        return True


class EvaluationSink:
    """Atomically write evaluation, conditions, dedup reservation and event intent.

    Plug directly into EvidenceProcessor. No publish occurs inside this sink.
    Its guard must be wired with the same domain evaluator and authoritative GM
    reader. A failed guard suppresses verified publication; the dated evaluation
    can still be persisted as historical information.
    """

    def __init__(self, contracts: Contracts, guard: VerificationGuard, evaluator_version: str):
        if not evaluator_version:
            raise ValueError('An evaluator version is required')
        self.contracts, self.guard, self.evaluator_version = contracts, guard, evaluator_version

    def __call__(self, result: Document, context: EvaluationContext, transaction: EvaluationTransaction) -> None:
        self.contracts.validate('goal-evaluation', result)
        self.contracts.validate('goal-spec', context.goal)
        uuid_string(context.correlation_id)
        if (result['goal_id'], result['goal_version']) != (context.goal['goal_id'], context.goal['version']):
            raise ValueError('Persistence goal/version mismatch')
        if len({condition['code'] for condition in result['conditions']}) != len(result['conditions']):
            raise ValueError('Duplicate condition codes')
        observations = {}
        for observation in context.observations:
            self.contracts.validate('observation', observation)
            oid = observation['observation_id']
            if oid in observations:
                raise ValueError('Duplicate observation IDs in persistence context')
            observations[oid] = observation
        referenced = set(result['candidate_observation_ids'])
        for condition in result['conditions']:
            referenced.update(condition['observation_ids'])
        if not referenced <= observations.keys():
            raise ValueError('Evaluation references missing evidence')
        verified = self.guard.eligible(result, context)
        fingerprint = offer_fingerprint(result, context) if verified else None
        if not transaction.insert_evaluation(result, evaluation_fingerprint(result, self.evaluator_version),
                                             self.evaluator_version):
            return
        transaction.enqueue_event(self._event('goal.evaluated.v1', result, context.correlation_id))
        if fingerprint is not None:
            verified_event = self._event('goal.opportunity_verified.v1', result, context.correlation_id)
            if transaction.reserve_offer(result, fingerprint, verified_event['event_id']):
                transaction.enqueue_event(verified_event)

    def _event(self, event_type: str, result: Document, correlation: str) -> Document:
        payload = {key: result[key] for key in ('goal_id', 'goal_version', 'evaluation_id')}
        if event_type == 'goal.evaluated.v1':
            payload['status'] = result['status']
        event = dict(event_id=str(uuid5(NAMESPACE_URL, event_type + ':' + result['evaluation_id'])),
                     event_type=event_type, schema_version=1, occurred_at=result['evaluated_at'],
                     producer='goal-evaluation', correlation_id=correlation,
                     aggregate_id=result['goal_id'], payload=payload)
        self.contracts.validate('domain-event', event)
        return event


class PostgresEvaluationTransaction(PostgresTransaction):
    """Use as PostgresStore's transaction_factory. All SQL is in the owned DB."""

    def insert_evaluation(self, result: Document, fingerprint: str, evaluator_version: str) -> bool:
        self.cursor.execute(
            'INSERT INTO evaluations (evaluation_id, goal_id, goal_version, evaluated_at, status, '
            'payload, evaluator_version, fingerprint) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) '
            'ON CONFLICT (fingerprint) DO NOTHING RETURNING evaluation_id',
            (result['evaluation_id'], result['goal_id'], result['goal_version'], result['evaluated_at'],
             result['status'], canonical(result), evaluator_version, fingerprint),
        )
        if self.cursor.fetchone() is None:
            return False
        for condition in result['conditions']:
            self.cursor.execute(
                'INSERT INTO condition_results (evaluation_id, code, status, reason, observation_ids) '
                'VALUES (%s,%s,%s,%s,%s::jsonb)',
                (result['evaluation_id'], condition['code'], condition['status'], condition['reason'],
                 canonical(condition['observation_ids'])),
            )
        return True

    def reserve_offer(self, result: Document, fingerprint: str, event_id: str) -> bool:
        self.cursor.execute(
            'INSERT INTO offer_fingerprints (goal_id, goal_version, fingerprint, last_verified_at, '
            'notification_emitted,verification_event_id) VALUES (%s,%s,%s,%s,false,%s) '
            'ON CONFLICT (goal_id,goal_version,fingerprint) DO UPDATE '
            'SET last_verified_at=EXCLUDED.last_verified_at,verification_event_id=EXCLUDED.verification_event_id '
            'WHERE NOT offer_fingerprints.notification_emitted AND EXISTS '
            "(SELECT 1 FROM outbox WHERE event_id=offer_fingerprints.verification_event_id AND status='failed') "
            'RETURNING fingerprint',
            (result['goal_id'], result['goal_version'], fingerprint, result['evaluated_at'], event_id),
        )
        return self.cursor.fetchone() is not None

    def enqueue_event(self, event: Document) -> None:
        self.cursor.execute('INSERT INTO outbox (event_id,event_json) VALUES (%s,%s::jsonb)',
                            (event['event_id'], canonical(event)))

    def get_evaluation(self, evaluation_id: str) -> Document | None:
        uuid_string(evaluation_id)
        self.cursor.execute('SELECT payload FROM evaluations WHERE evaluation_id=%s', (evaluation_id,))
        row = self.cursor.fetchone()
        if row is None:
            return None
        return copy.deepcopy(json.loads(row[0]) if isinstance(row[0], str) else row[0])

    def next_pending(self, now: datetime):
        self.cursor.execute(
            "SELECT event_id, event_json, attempts FROM outbox WHERE status='pending' AND next_retry_at<=%s "
            'ORDER BY next_retry_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED', (now,),
        )
        row = self.cursor.fetchone()
        if row is None:
            return None
        return (str(row[0]), json.loads(row[1]) if isinstance(row[1], str) else row[1], row[2])

    def mark_published(self, event_id: str, now: datetime) -> None:
        self.cursor.execute('UPDATE offer_fingerprints SET notification_emitted=true '
                            'WHERE verification_event_id=%s', (event_id,))
        self.cursor.execute("UPDATE outbox SET status='published', attempts=attempts+1, published_at=%s, "
                            'last_error=NULL WHERE event_id=%s', (now, event_id))

    def mark_failed(self, event_id: str, attempts: int, retry_at: datetime, terminal: bool, reason: str) -> None:
        self.cursor.execute('UPDATE outbox SET status=%s,attempts=%s,next_retry_at=%s,last_error=%s WHERE event_id=%s',
                            ('failed' if terminal else 'pending', attempts, retry_at, reason, event_id))
