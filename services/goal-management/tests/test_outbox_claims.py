from contextlib import nullcontext
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.repository import GoalRepository


class Cursor:
    def __init__(self):
        self.commands = []
        self.rows = [("ae000000-0000-4000-8000-000000000011", "goal.activated.v1", {"payload": {}})]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters=None):
        self.commands.append((statement, parameters))

    def executemany(self, statement, parameters):
        self.commands.append((statement, list(parameters)))

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.cursor_instance = Cursor()

    def transaction(self):
        return nullcontext()

    def cursor(self):
        return self.cursor_instance


def test_outbox_claim_reclaims_expired_publish_lease_and_sets_new_lease():
    connection = Connection()
    events = GoalRepository(connection).claim_outbox()

    assert events == [{"event_id": "ae000000-0000-4000-8000-000000000011", "event_type": "goal.activated.v1", "payload": {}}]
    statements = [command[0] for command in connection.cursor_instance.commands]
    assert "status='publishing'" in statements[0]
    assert "claimed_at<=now()" in statements[0]
    assert "claimed_at=now()" in statements[-1]
