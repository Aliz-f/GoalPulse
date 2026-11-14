"""Connection lifecycle tests; these do not claim PostgreSQL SQL execution."""

import pytest

from goalpulse_evidence.postgres import PostgresRepository


class ConnectionDouble:
    def __init__(self, *, autocommit=False, fail_commit=False):
        self.autocommit, self.fail_commit = autocommit, fail_commit
        self.commits = self.rollbacks = self.closes = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        if self.fail_commit:
            raise OSError("Injected commit failure")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def test_transaction_commits_only_after_successful_exit():
    connection = ConnectionDouble()
    with PostgresRepository(lambda: connection).transaction():
        assert connection.commits == 0
    assert (connection.commits, connection.rollbacks, connection.closes) == (1, 0, 1)


def test_failed_transaction_rolls_back_and_closes():
    connection = ConnectionDouble()
    with pytest.raises(OSError):
        with PostgresRepository(lambda: connection).transaction():
            raise OSError("Injected failure")
    assert (connection.commits, connection.rollbacks, connection.closes) == (0, 1, 1)


def test_commit_failure_rolls_back_and_closes():
    connection = ConnectionDouble(fail_commit=True)
    with pytest.raises(OSError, match="commit"):
        with PostgresRepository(lambda: connection).transaction():
            pass
    assert (connection.commits, connection.rollbacks, connection.closes) == (0, 1, 1)


def test_autocommit_connection_is_rejected_before_any_write():
    connection = ConnectionDouble(autocommit=True)
    with pytest.raises(ValueError, match="autocommit"):
        with PostgresRepository(lambda: connection).transaction():
            pytest.fail("Must not allow non-atomic writes")
    assert connection.closes == 1 and connection.commits == 0
