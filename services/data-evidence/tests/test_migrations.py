"""Migration bookkeeping tests with a connection double, not SQL engine proof."""

from copy import deepcopy
from hashlib import sha256

import pytest

from goalpulse_evidence.migrations import apply_migrations


class MigrationConnection:
    autocommit = False

    def __init__(self):
        self.committed = {}
        self.pending = {}
        self.last_row = None
        self.commits = self.rollbacks = self.ddl_runs = 0
        self.fail_ddl = False

    def cursor(self):
        self.pending = deepcopy(self.committed)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        if sql.startswith("SELECT checksum"):
            checksum = self.pending.get(params[0])
            self.last_row = (checksum,) if checksum else None
        elif sql.startswith("INSERT INTO de_schema_migrations"):
            self.pending[params[0]] = params[1]
        elif sql.startswith("-- synthetic migration"):
            self.ddl_runs += 1
            if self.fail_ddl:
                raise OSError("Injected DDL failure")

    def fetchone(self):
        return self.last_row

    def commit(self):
        self.commits += 1
        self.committed = deepcopy(self.pending)

    def rollback(self):
        self.rollbacks += 1
        self.pending = deepcopy(self.committed)


def test_repeated_migration_is_idempotent_and_checksummed(tmp_path):
    migration = tmp_path / "0001_test.sql"
    migration.write_bytes(b"-- synthetic migration\nSELECT 1;")
    connection = MigrationConnection()
    assert apply_migrations(connection, tmp_path) == [migration.name]
    assert connection.committed[migration.name] == sha256(migration.read_bytes()).hexdigest()
    assert apply_migrations(connection, tmp_path) == []
    migration.write_bytes(b"-- synthetic migration\r\nSELECT 1;")
    assert apply_migrations(connection, tmp_path) == []
    assert connection.ddl_runs == 1


def test_changed_applied_migration_is_rejected_and_rolled_back(tmp_path):
    migration = tmp_path / "0001_test.sql"
    migration.write_text("-- synthetic migration\nSELECT 1;", encoding="utf-8")
    connection = MigrationConnection()
    apply_migrations(connection, tmp_path)
    original = deepcopy(connection.committed)
    migration.write_text("-- synthetic migration\nSELECT 2;", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum changed"):
        apply_migrations(connection, tmp_path)
    assert connection.committed == original and connection.rollbacks == 1


def test_ddl_failure_does_not_mark_migration_applied(tmp_path):
    (tmp_path / "0001_test.sql").write_text("-- synthetic migration\nSELECT 1;", encoding="utf-8")
    connection = MigrationConnection()
    connection.fail_ddl = True
    with pytest.raises(OSError, match="DDL failure"):
        apply_migrations(connection, tmp_path)
    assert not connection.committed and connection.rollbacks == 1 and connection.commits == 0
