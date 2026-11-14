from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.repository import ConfirmationConflict, GoalRepository, GoalVersionRecord


class Cursor:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters=None):
        self.commands.append((statement, parameters))

    def fetchone(self):
        return self.responses.pop(0)


class Connection:
    def __init__(self, responses=None):
        self.cursor_instance = Cursor(responses)
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def cursor(self):
        return self.cursor_instance


def test_goal_version_and_activation_outbox_are_inserted_in_one_transaction():
    goal_id = uuid.uuid4()
    connection = Connection(responses=[(goal_id,)])
    correlation_id = uuid.uuid4()
    approved_at = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    GoalRepository(connection).create_version_one(
        GoalVersionRecord(
            goal_id=goal_id,
            owner_user_id=uuid.uuid4(),
            confirmed_session_id=uuid.uuid4(),
            version=1,
            spec={"goal_id": str(goal_id)},
            approved_at=approved_at,
            approved_draft_revision=2,
            correlation_id=correlation_id,
        )
    )
    assert connection.transactions == 1
    statements = [statement for statement, _parameters in connection.cursor_instance.commands]
    assert len(statements) == 3
    assert "INSERT INTO goals" in statements[0]
    assert "INSERT INTO goal_versions" in statements[1]
    assert "INSERT INTO outbox" in statements[2]
    outbox_parameters = connection.cursor_instance.commands[2][1]
    event = outbox_parameters[2].obj
    assert event["event_id"] == str(outbox_parameters[0])
    assert event["event_type"] == "goal.activated.v1"
    assert event["correlation_id"] == str(correlation_id)
    assert event["payload"] == {"goal_id": str(goal_id), "goal_version": 1}


def test_evaluation_inbox_runtime_gate_and_projection_share_one_transaction():
    event_id = str(uuid.uuid4())
    goal_id = str(uuid.uuid4())
    evaluation_id = str(uuid.uuid4())
    occurred_at = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    connection = Connection(responses=[(event_id,), (1, "active"), (goal_id,)])
    result = GoalRepository(connection).process_evaluated(
        event_id=event_id,
        event_type="goal.evaluated.v1",
        goal_id=goal_id,
        goal_version=1,
        evaluation_id=evaluation_id,
        status="satisfied",
        occurred_at=occurred_at,
    )
    assert result == "projected"
    assert connection.transactions == 1
    statements = [statement for statement, _parameters in connection.cursor_instance.commands]
    assert "INSERT INTO inbox" in statements[0]
    assert "FOR UPDATE" in statements[1]
    assert "goal_evaluation_projection" in statements[2]
    projection_parameters = connection.cursor_instance.commands[2][1]
    assert projection_parameters[4] == occurred_at
    assert projection_parameters[5:] == ("satisfied", "satisfied")


def test_duplicate_stale_and_inactive_evaluations_do_not_update_projection():
    cases = [([None], "duplicate"), ([("event",), (2, "active")], "stale"), ([("event",), (1, "paused")], "inactive")]
    for responses, expected in cases:
        connection = Connection(responses=responses)
        result = GoalRepository(connection).process_evaluated(
            event_id=str(uuid.uuid4()),
            event_type="goal.evaluated.v1",
            goal_id=str(uuid.uuid4()),
            goal_version=1,
            evaluation_id=str(uuid.uuid4()),
            status="unknown",
            occurred_at=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        )
        assert result == expected
        assert all("goal_evaluation_projection" not in statement for statement, _ in connection.cursor_instance.commands)


def record(owner=None):
    selected_owner = uuid.uuid4() if owner is None else owner
    return GoalVersionRecord(
        goal_id=uuid.uuid4(),
        owner_user_id=selected_owner,
        confirmed_session_id=uuid.uuid4(),
        version=1,
        spec={"goal_id": str(uuid.uuid4()), "owner_user_id": str(selected_owner)},
        approved_at=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        approved_draft_revision=2,
        correlation_id=uuid.uuid4(),
    )


def test_exact_concurrent_confirmation_replay_reuses_spec_without_new_outbox():
    item = record()
    existing_spec = {"goal_id": str(uuid.uuid4()), "owner_user_id": str(item.owner_user_id)}
    connection = Connection(responses=[None, (item.owner_user_id, 2, existing_spec)])
    assert GoalRepository(connection).create_version_one(item) == existing_spec
    assert len(connection.cursor_instance.commands) == 2
    assert all("outbox" not in statement for statement, _ in connection.cursor_instance.commands)


def test_incompatible_confirmation_replay_is_rejected():
    item = record()
    connection = Connection(
        responses=[None, (uuid.uuid4(), 2, {"goal_id": str(uuid.uuid4())})]
    )
    with pytest.raises(ConfirmationConflict):
        GoalRepository(connection).create_version_one(item)


def test_older_same_version_evaluation_does_not_regress_projection():
    connection = Connection(responses=[("event",), (1, "active"), None])
    result = GoalRepository(connection).process_evaluated(
        event_id=str(uuid.uuid4()),
        event_type="goal.evaluated.v1",
        goal_id=str(uuid.uuid4()),
        goal_version=1,
        evaluation_id=str(uuid.uuid4()),
        status="unknown",
        occurred_at=datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
    )
    assert result == "older"
