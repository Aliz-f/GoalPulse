"""Service-owned PostgreSQL evaluation/outbox persistence; no driver or HTTP I/O."""

import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from .consumer import PostgresTransaction, Store
from .domain import Contracts, Dispatcher, Document
from .processing import EvaluationContext, GoalReader, Runtime, Subscription, uuid_string
from .offer_identity import business_fingerprint, offer_fingerprint as snapshot_fingerprint


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

    P0 uses the ADR-0003 business key, harmonized with Notification. Seller is
    the normalized hostname; payable components and exact selected product
    distinguish offers without capture IDs or tracking/listing URLs.
    """
    selected = set(result['candidate_observation_ids'])
    observations = [o for o in context.observations if o['observation_id'] in selected]
    if len(observations) != len(selected) or not selected:
        raise ValueError('Offer fingerprint requires every selected observation exactly once')
    return business_fingerprint(context.goal, observations)


class EvaluationTransaction(Protocol):
    def insert_evaluation(self, result: Document, fingerprint: str, evaluator_version: str) -> bool: ...
    def reserve_offer(self, result: Document, fingerprint: str, event_id: str) -> bool: ...
    def enqueue_event(self, event: Document) -> None: ...
    def get_evaluation(self, evaluation_id: str) -> Document | None: ...
    def list_evaluations(self, goal_id: str, goal_version: int, limit: int) -> list[Document]: ...
    def legacy_offer_reserved(self, result: Document, fingerprint: str) -> bool: ...


class ImmutableEvaluationReader:
    """Validated immutable reads used by authenticated HTTP adapters."""

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

    def list_for_goal(self, goal_id: str, goal_version: int, limit: int) -> list[Document]:
        uuid_string(goal_id)
        if type(goal_version) is not int or goal_version < 1:
            raise ValueError('Invalid goal version')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid evaluation history limit')
        with self.store.transaction() as transaction:
            results = transaction.list_evaluations(goal_id, goal_version, limit)
            if not isinstance(results, list) or len(results) > limit:
                raise ValueError('Invalid stored evaluation history')
            for result in results:
                self.contracts.validate('goal-evaluation', result)
                if (result['goal_id'], result['goal_version']) != (goal_id, goal_version):
                    raise ValueError('Stored evaluation history identity mismatch')
            return copy.deepcopy(results)


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
        selected = set(result['candidate_observation_ids'])
        if any(not timedelta(0) <= now - datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00')) <=
               timedelta(minutes=min(context.goal['monitoring']['max_evidence_age_minutes'], 10))
               for o in context.observations if o['observation_id'] in selected):
            return False
        return True


class EvaluationSink:
    """Atomically write evaluation, conditions, dedup reservation and event intent.

    Plug directly into EvidenceProcessor. No publish occurs inside this sink.
    Its guard must be wired with the same domain evaluator and authoritative GM
    reader. A failed guard suppresses verified publication; the dated evaluation
    can still be persisted as historical information.
    """

    def __init__(self, contracts: Contracts, guard: VerificationGuard,
                 evaluator_version: str | Mapping[str, str]):
        if isinstance(evaluator_version, Mapping):
            if (not evaluator_version or set(evaluator_version) - {'product_purchase', 'recurring_procurement'}
                    or any(not isinstance(v, str) or not v for v in evaluator_version.values())):
                raise ValueError('Explicit evaluator version per registered goal type required')
            evaluator_version = dict(evaluator_version)
        elif not isinstance(evaluator_version, str) or not evaluator_version:
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
        version = (self.evaluator_version if isinstance(self.evaluator_version, str) else
                   self.evaluator_version.get(context.goal['goal_type']))
        if not version:
            raise ValueError('No persistence evaluator version for this goal type')
        if not transaction.insert_evaluation(result, evaluation_fingerprint(result, version), version):
            return
        transaction.enqueue_event(self._event('goal.evaluated.v1', result, context.correlation_id))
        if fingerprint is not None:
            selected = set(result['candidate_observation_ids'])
            legacy = snapshot_fingerprint(context.goal, [o for o in context.observations
                                                         if o['observation_id'] in selected])
            if legacy != fingerprint and transaction.legacy_offer_reserved(result, legacy):
                return
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

    def legacy_offer_reserved(self, result: Document, fingerprint: str) -> bool:
        self.cursor.execute(
            'SELECT 1 FROM offer_fingerprints f LEFT JOIN outbox o ON o.event_id=f.verification_event_id '
            'WHERE f.goal_id=%s AND f.goal_version=%s AND f.fingerprint=%s AND '
            "(f.notification_emitted OR f.verification_event_id IS NULL OR o.status IS DISTINCT FROM 'failed')",
            (result['goal_id'], result['goal_version'], fingerprint))
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

    def list_evaluations(self, goal_id: str, goal_version: int, limit: int) -> list[Document]:
        uuid_string(goal_id)
        if type(goal_version) is not int or goal_version < 1:
            raise ValueError('Invalid goal version')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid evaluation history limit')
        self.cursor.execute(
            'SELECT payload FROM evaluations WHERE goal_id=%s AND goal_version=%s '
            'ORDER BY evaluated_at DESC,evaluation_id DESC LIMIT %s',
            (goal_id, goal_version, limit),
        )
        return [copy.deepcopy(json.loads(row[0]) if isinstance(row[0], str) else row[0])
                for row in self.cursor.fetchall()]

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
