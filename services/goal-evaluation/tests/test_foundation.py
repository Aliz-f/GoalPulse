"""Network-free boundary tests; domain results here are injected fixtures."""
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import ValidationError

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
from goal_evaluation.domain import Contracts, Dispatcher
from goal_evaluation.consumer import EvidenceConsumer, PostgresStore


def fixture(name):
    return json.loads((ROOT / 'contracts/examples' / name).read_text())


@pytest.fixture
def contracts():
    return Contracts(ROOT / 'contracts/schemas')


def dispatch(contracts, result=None, goal=None, observations=None, now=None):
    evaluator = Mock()
    evaluator.evaluate.return_value = result or fixture('goal-evaluation.satisfied.fixture.json')
    return Dispatcher(contracts, {'product_purchase': evaluator}).evaluate(
        goal or fixture('goal-spec.product.json'),
        observations if observations is not None else [fixture('observation.verified.fixture.json')],
        now or datetime(2026, 11, 14, 10, tzinfo=timezone.utc),
    )


def test_dispatch_validated_result(contracts):
    assert dispatch(contracts)['is_opportunity_verified'] is True


def test_unimplemented_and_unsupported_dispatch(contracts):
    with pytest.raises(NotImplementedError):
        Dispatcher(contracts, {}).evaluate(fixture('goal-spec.product.json'), [], datetime.now(timezone.utc))
    with pytest.raises(ValueError):
        Dispatcher(contracts, {'travel': Mock()})


@pytest.mark.parametrize('field,value', [('goal_type', 'travel'), ('goal_id', 'bad-uuid')])
def test_invalid_goal(contracts, field, value):
    goal = fixture('goal-spec.product.json')
    goal[field] = value
    with pytest.raises(ValidationError):
        dispatch(contracts, goal=goal)


def test_unknown_cannot_verify(contracts):
    result = fixture('goal-evaluation.satisfied.fixture.json')
    result['conditions'][0]['status'] = 'unknown'
    with pytest.raises(ValidationError):
        dispatch(contracts, result=result)


def test_wrong_goal_version_and_unsupplied_evidence(contracts):
    result = fixture('goal-evaluation.satisfied.fixture.json')
    result['goal_version'] = 2
    with pytest.raises(ValueError, match='version'):
        dispatch(contracts, result=result)
    with pytest.raises(ValueError, match='evidence'):
        dispatch(contracts, observations=[])


def test_clock_and_observation_validation(contracts):
    with pytest.raises(ValueError, match='UTC'):
        dispatch(contracts, now=datetime(2026, 11, 14))
    observation = fixture('observation.verified.fixture.json')
    observation['unit_price_eur_cents'] = 1.25
    with pytest.raises(ValidationError):
        dispatch(contracts, observations=[observation])


class MemoryStore:
    """Test-only rollback-capable store; production uses PostgreSQL uniqueness."""
    def __init__(self):
        self.ids = set()

    @contextmanager
    def transaction(self):
        before = self.ids.copy()
        try:
            yield self
        except BaseException:
            self.ids = before
            raise

    def claim_event(self, event_id):
        if event_id in self.ids:
            return False
        self.ids.add(event_id)
        return True


def test_replay_and_correlation(contracts):
    process, store = Mock(), MemoryStore()
    consumer = EvidenceConsumer(contracts, store, process)
    event = fixture('event.evidence-updated.json')
    assert consumer.handle(event)
    assert not consumer.handle(event)
    process.assert_called_once_with(event, store)


def test_failure_rolls_back_claim(contracts):
    process, store = Mock(side_effect=RuntimeError('transient')), MemoryStore()
    consumer = EvidenceConsumer(contracts, store, process)
    event = fixture('event.evidence-updated.json')
    with pytest.raises(RuntimeError):
        consumer.handle(event)
    assert not store.ids
    process.side_effect = None
    assert consumer.handle(event)


def test_invalid_event_never_claimed(contracts):
    store = MemoryStore()
    consumer = EvidenceConsumer(contracts, store, Mock())
    event = fixture('event.evidence-updated.json')
    event['producer'] = 'goal-management'
    with pytest.raises(ValidationError):
        consumer.handle(event)
    with pytest.raises(ValueError):
        consumer.handle(fixture('event.goal-activated.json'))
    assert not store.ids


@pytest.mark.parametrize('failure', [False, True])
def test_postgres_commit_rollback_and_parameterized_claim(failure):
    connection, cursor = Mock(), Mock()
    connection.autocommit = False
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = ('id',)
    store = PostgresStore(lambda: connection)
    try:
        with store.transaction() as tx:
            assert tx.claim_event('id')
            assert cursor.execute.call_args.args[1] == ('id',)
            if failure:
                raise RuntimeError('rollback')
    except RuntimeError:
        pass
    assert connection.commit.call_count == (0 if failure else 1)
    assert connection.rollback.call_count == (1 if failure else 0)
    connection.close.assert_called_once()


def test_postgres_duplicate_is_noop():
    from goal_evaluation.consumer import PostgresTransaction
    cursor = Mock()
    cursor.fetchone.return_value = None
    assert PostgresTransaction(cursor).claim_event('duplicate') is False


def test_postgres_commit_failure_propagates_and_rolls_back():
    connection = Mock()
    connection.autocommit = False
    connection.cursor.return_value.__enter__ = Mock(return_value=Mock())
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    connection.commit.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError, match='database unavailable'):
        with PostgresStore(lambda: connection).transaction():
            pass
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_autocommit_rejected():
    connection = Mock(autocommit=True)
    with pytest.raises(ValueError, match='autocommit'):
        with PostgresStore(lambda: connection).transaction():
            pytest.fail('Unsafe transaction entered')
    connection.close.assert_called_once()
