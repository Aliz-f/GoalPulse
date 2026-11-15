from contextlib import contextmanager
from uuid import uuid4

import pytest

from goalpulse_discovery.registry import SourceRegistry


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, parameters):
        self.calls.append((" ".join(sql.split()), parameters))

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self, rows):
        self.cursor = Cursor(rows)

    @contextmanager
    def transaction(self):
        yield self.cursor


def test_complete_goal_sources_are_one_snapshot_sorted_and_bounded():
    goal_id, first, second = uuid4(), uuid4(), uuid4()
    expected = sorted([first, second], key=str)
    database = Database([(True, str(value)) for value in expected])
    assert SourceRegistry(database).sources_for_goal(goal_id, 4) == expected
    assert len(database.cursor.calls) == 1
    sql, parameters = database.cursor.calls[0]
    assert "WITH known AS" in sql
    assert "state='active'" in sql
    assert "ORDER BY active.source_id" in sql
    assert parameters == (str(goal_id), 4, str(goal_id), 4)


def test_goal_sources_distinguish_unknown_from_known_empty():
    goal_id = uuid4()
    assert SourceRegistry(Database([(False, None)])).sources_for_goal(goal_id, 1) is None
    assert SourceRegistry(Database([(True, None)])).sources_for_goal(goal_id, 1) == []


def test_goal_sources_never_truncate_corrupted_state():
    rows = [(True, str(uuid4())) for _ in range(4)]
    with pytest.raises(RuntimeError, match="approved bound"):
        SourceRegistry(Database(rows)).sources_for_goal(uuid4(), 1)
