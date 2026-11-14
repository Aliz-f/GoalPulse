from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.repository import GoalRepository, GoalVersionRecord


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
    connection = Connection()
    goal_id = uuid.uuid4()
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
    assert event["correlation_id"] == str(correlation_id)
    assert event["payload"] == {"goal_id": str(goal_id), "goal_version": 1}


def test_evaluation_inbox_runtime_gate_and_projection_share_one_transaction():
    event_id = str(uuid.uuid4())
    goal_id = str(uuid.uuid4())
    evaluation_id = str(uuid.uuid4())
    connection = Connection(responses=[(event_id,), (1, "active")])
    result = GoalRepository(connection).process_evaluated(
        event_id=event_id,
        event_type="goal.evaluated.v1",
        goal_id=goal_id,
        goal_version=1,
        evaluation_id=evaluation_id,
        status="satisfied",
    )
    assert result == "projected"
    assert connection.transactions == 1
    statements = [statement for statement, _parameters in connection.cursor_instance.commands]
    assert "INSERT INTO inbox" in statements[0]
    assert "FOR UPDATE" in statements[1]
    assert "goal_evaluation_projection" in statements[2]


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
        )
        assert result == expected
        assert all("goal_evaluation_projection" not in statement for statement, _ in connection.cursor_instance.commands)
