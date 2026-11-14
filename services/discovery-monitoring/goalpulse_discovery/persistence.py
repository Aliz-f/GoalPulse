"""PostgreSQL transaction helper for the owned Discovery & Monitoring database."""

from contextlib import contextmanager
from collections.abc import Callable, Iterator
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol


class Cursor(Protocol):
    def __enter__(self) -> "Cursor": ...
    def __exit__(self, *args: Any) -> Any: ...
    def execute(self, query: str, params: tuple = ()) -> Any: ...
    def fetchone(self) -> tuple | None: ...
    def fetchall(self) -> list[tuple]: ...


class Connection(Protocol):
    autocommit: bool
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Database:
    """Inject a fresh psycopg 3 connection factory; no driver is installed here."""

    def __init__(self, connect: Callable[[], Connection]) -> None:
        self.connect = connect

    @contextmanager
    def transaction(self) -> Iterator[Cursor]:
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError("Discovery writes require autocommit=False")
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self, directory: Path) -> list[str]:
        applied = []
        with self.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(734901602)")
            cursor.execute("""CREATE TABLE IF NOT EXISTS dm_schema_migrations (
                version text PRIMARY KEY, checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())""")
            for path in sorted(directory.glob("[0-9]*.sql")):
                sql = path.read_text(encoding="utf-8")
                checksum = sha256(sql.encode()).hexdigest()
                cursor.execute("SELECT checksum FROM dm_schema_migrations WHERE version=%s", (path.name,))
                row = cursor.fetchone()
                if row:
                    if row[0] != checksum:
                        raise ValueError("Applied discovery migration checksum changed")
                    continue
                cursor.execute(sql)
                cursor.execute("INSERT INTO dm_schema_migrations (version,checksum) VALUES (%s,%s)",
                               (path.name, checksum))
                applied.append(path.name)
        return applied
