"""Owned PostgreSQL persistence using an injected psycopg 3 connection factory.

No driver is imported or installed by this module. Caller supplies connections to
the Data & Evidence database only. A transaction owns and closes its connection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any, Protocol

from .ledger import Attempt, AttemptConflict, Capture


class Cursor(Protocol):
    def __enter__(self) -> Cursor: ...
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


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


class PostgresRepository:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self.connect = connect

    @contextmanager
    def transaction(self) -> Iterator[PostgresTransaction]:
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError("Evidence writes require autocommit=False")
            with connection.cursor() as cursor:
                yield PostgresTransaction(cursor)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgresTransaction:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor = cursor

    def add_attempt(self, attempt: Attempt) -> tuple[str, bool]:
        capture = attempt.capture
        self.cursor.execute(
            """INSERT INTO extraction_attempts
               (attempt_id, source_id, provider, provider_request_id, extraction_method,
                source_url, fetched_at, provider_observed_at, cache_age_ms,
                outcome, error_code, fingerprint, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING attempt_id""",
            (attempt.attempt_id, str(capture.source_id), capture.provider, capture.provider_request_id,
             capture.extraction_method, capture.source_url, capture.fetched_at,
             capture.provider_observed_at, capture.cache_age_ms, attempt.outcome,
             attempt.error_code, attempt.fingerprint, attempt.created_at),
        )
        row = self.cursor.fetchone()
        if row:
            return str(row[0]), True
        self.cursor.execute("SELECT attempt_id,fingerprint FROM extraction_attempts WHERE attempt_id = %s",
                            (attempt.attempt_id,))
        row = self.cursor.fetchone()
        if row:
            if row[1] != attempt.fingerprint:
                raise AttemptConflict("attempt_id was reused for different extraction data")
            return str(row[0]), False
        self.cursor.execute("SELECT attempt_id FROM extraction_attempts WHERE fingerprint = %s",
                            (attempt.fingerprint,))
        row = self.cursor.fetchone()
        if row is None:
            raise RuntimeError("Extraction attempt conflict could not be resolved")
        return str(row[0]), False

    def add_observation(self, observation: dict, attempt_id: str,
                        fingerprint: str, created_at: datetime) -> tuple[str, bool]:
        self.cursor.execute(
            """INSERT INTO observations
               (observation_id, source_id, source_url, observed_at, payload, reliability,
                extraction_attempt_id, normalized_fingerprint, created_at)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
               ON CONFLICT (normalized_fingerprint) DO NOTHING RETURNING observation_id""",
            (observation["observation_id"], observation["source_id"], observation["source_url"],
             observation["observed_at"], encoded(observation), observation["evidence"]["reliability"],
             attempt_id, fingerprint, created_at),
        )
        row = self.cursor.fetchone()
        if row:
            return str(row[0]), True
        self.cursor.execute("SELECT observation_id FROM observations WHERE normalized_fingerprint = %s",
                            (fingerprint,))
        return str(self.cursor.fetchone()[0]), False

    def add_provenance(self, observation_id: str, capture: Capture) -> None:
        self.cursor.execute(
            """INSERT INTO observation_provenance
               (observation_id, provider_request_id, page_url, cache_age_ms,
                field_sources, parser_version, extraction_version)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (observation_id, capture.provider_request_id, capture.source_url, capture.cache_age_ms,
             encoded(dict(capture.field_sources)), capture.parser_version, "observation.v1"),
        )

    def enqueue(self, event: dict, created_at: datetime) -> None:
        self.cursor.execute(
            """INSERT INTO outbox
               (event_id, observation_id, payload, status, attempts, next_attempt_at, created_at)
               VALUES (%s,%s,%s::jsonb,'pending',0,%s,%s)""",
            (event["event_id"], event["payload"]["observation_id"], encoded(event), created_at, created_at),
        )
