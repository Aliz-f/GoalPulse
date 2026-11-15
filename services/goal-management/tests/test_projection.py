from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.repository import GoalRepository


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.command = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters):
        self.command = (statement, parameters)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.cursor_instance = Cursor(rows)

    def cursor(self):
        return self.cursor_instance


def test_goal_summary_uses_only_current_version_projection_and_event_time():
    goal_id = uuid.uuid4()
    observed = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    connection = Connection([(goal_id, 2, "active", "GPU goal", "verified", observed)])
    result = GoalRepository(connection).list_for_owner(uuid.uuid4(), "active")
    assert result == [{
        "goal_id": str(goal_id),
        "version": 2,
        "lifecycle": "active",
        "title": "GPU goal",
        "opportunity_status": "verified",
        "last_evaluated_at": "2026-11-14T10:00:00Z",
    }]
    sql, parameters = connection.cursor_instance.command
    assert "p.version=g.current_version" in sql
    assert "COALESCE(p.opportunity_status,'not_found')" in sql
    assert parameters[1] == "active"
