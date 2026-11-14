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
from uuid import UUID

from .ledger import Attempt, AttemptConflict, Capture, timestamp


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

    def lock_source(self, source_id: str) -> None:
        """Serialize source ingestion and checking within the owning DB only."""
        self.cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,))

    def get_observation(self, observation_id: str) -> dict | None:
        self.cursor.execute("SELECT payload FROM observations WHERE observation_id = %s", (observation_id,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def get_attempt_id(self, observation_id: str) -> str:
        self.cursor.execute("SELECT extraction_attempt_id FROM observations WHERE observation_id = %s",
                            (observation_id,))
        row = self.cursor.fetchone()
        if row is None:
            raise ValueError("Observation does not exist")
        return str(row[0])

    def get_capture(self, observation_id: str) -> Capture | None:
        self.cursor.execute(
            """SELECT a.source_id, a.source_url, a.provider, a.extraction_method,
                      a.fetched_at, p.provider_request_id, a.provider_observed_at,
                      p.cache_age_ms, p.parser_version, p.field_sources
               FROM observations o
               JOIN extraction_attempts a ON a.attempt_id = o.extraction_attempt_id
               JOIN observation_provenance p ON p.observation_id = o.observation_id
               WHERE o.observation_id = %s""", (observation_id,))
        row = self.cursor.fetchone()
        if row is None:
            return None
        return Capture(source_id=UUID(str(row[0])), source_url=row[1], provider=row[2],
                       extraction_method=row[3], fetched_at=row[4], provider_request_id=row[5],
                       provider_observed_at=row[6], cache_age_ms=row[7], parser_version=row[8],
                       field_sources=row[9])

    def get_check(self, check_id: str) -> dict | None:
        self.cursor.execute(
            """SELECT check_id, original_observation_id, result_observation_id, decision,
                      reasons, policy_version, normalizer_version, request_fingerprint,
                      input_digest, proof_digest, proof, correlation_id, checked_at
               FROM evidence_checks WHERE check_id = %s""", (check_id,))
        row = self.cursor.fetchone()
        if row is None:
            return None
        audit = dict(zip(("check_id", "original_observation_id", "result_observation_id", "decision",
                          "reasons", "policy_version", "normalizer_version", "request_fingerprint",
                          "input_digest", "proof_digest", "proof", "correlation_id", "checked_at"), row))
        for key in ("check_id", "original_observation_id", "result_observation_id", "correlation_id"):
            if audit[key] is not None:
                audit[key] = str(audit[key])
        audit["checked_at"] = timestamp(audit["checked_at"])
        return audit

    def proof_used(self, proof_digest: str) -> bool:
        self.cursor.execute("SELECT 1 FROM evidence_checks WHERE proof_digest = %s AND decision = 'checked'",
                            (proof_digest,))
        return self.cursor.fetchone() is not None

    def source_observations(self, source_id: str, since: datetime) -> list[dict]:
        self.cursor.execute(
            """SELECT o.payload FROM observations o
               WHERE o.source_id = %s AND o.observed_at >= %s
                 AND NOT EXISTS (SELECT 1 FROM evidence_checks c
                                 WHERE c.original_observation_id = o.observation_id
                                   AND c.result_observation_id IS NOT NULL)
               ORDER BY o.observed_at DESC, o.created_at DESC, o.observation_id DESC""", (source_id, since))
        return [row[0] for row in self.cursor.fetchall()]

    def current_claims(self, source_id: str, observed_at: datetime) -> list[dict]:
        """Return current checked audit claims for one immutable capture time."""
        self.cursor.execute(
            """SELECT c.proof->'claims' FROM evidence_checks c
               JOIN observations o ON o.observation_id = c.result_observation_id
               WHERE c.decision = 'checked' AND o.source_id = %s AND o.observed_at = %s
                 AND jsonb_typeof(c.proof->'claims') = 'object'
                 AND NOT EXISTS (SELECT 1 FROM evidence_checks newer
                                 WHERE newer.original_observation_id = o.observation_id
                                   AND newer.result_observation_id IS NOT NULL)
               ORDER BY c.checked_at DESC, c.check_id DESC""", (source_id, observed_at))
        return [row[0] for row in self.cursor.fetchall() if isinstance(row[0], dict)]

    def add_check(self, audit: dict) -> None:
        self.cursor.execute(
            """INSERT INTO evidence_checks
               (check_id, original_observation_id, result_observation_id, decision, reasons,
                policy_version, normalizer_version, request_fingerprint, input_digest,
                proof_digest, proof, correlation_id, checked_at)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (audit["check_id"], audit["original_observation_id"], audit["result_observation_id"],
             audit["decision"], encoded(audit["reasons"]), audit["policy_version"],
             audit["normalizer_version"], audit["request_fingerprint"], audit["input_digest"],
             audit["proof_digest"], encoded(audit["proof"]), audit["correlation_id"], audit["checked_at"]))

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
