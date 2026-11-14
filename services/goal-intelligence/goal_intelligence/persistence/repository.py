"""Draft session persistence with optimistic concurrency (D2-GI-01).

One SQL implementation serves PostgreSQL (production, psycopg 3) and SQLite
(tests, stdlib). Each accepted user turn appends an immutable revision; the
session row's ``current_revision`` is advanced with a compare-and-set
``UPDATE ... WHERE current_revision = :expected``, so two writers holding the
same expected revision yield exactly one success and one ``StaleRevision``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

OPEN_STATES = ("collecting", "ready_for_confirmation")


class RepositoryError(Exception):
    pass


class SessionNotFound(RepositoryError):
    """Unknown session, or one owned by somebody else (indistinguishable on purpose)."""


class StaleRevision(RepositoryError):
    def __init__(self, current_revision: int):
        super().__init__(f"stale revision; current is {current_revision}")
        self.current_revision = current_revision


class SessionClosed(RepositoryError):
    """Confirmed, abandoned or expired sessions accept no more turns."""


class ConfirmConflict(RepositoryError):
    pass


@dataclass(frozen=True)
class RevisionData:
    state: str
    candidate_goal: dict[str, Any]
    missing_fields: list[str]
    next_question: str | None
    ready: bool
    working_state: dict[str, Any]


@dataclass(frozen=True)
class DraftRecord:
    session_id: str
    owner_user_id: str
    state: str
    revision: int
    candidate_goal: dict[str, Any]
    missing_fields: list[str]
    next_question: str | None
    ready_for_confirmation: bool
    working_state: dict[str, Any]
    confirmed_goal_id: str | None
    expires_at: datetime


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True)
class Attempt:
    model_id: str
    error_code: str | None
    latency_ms: int


@dataclass
class SqlDraftRepository:
    """``connect`` returns a fresh DB-API connection (sqlite3 or psycopg)."""

    connect: Callable[[], Any]
    dialect: Literal["sqlite", "postgres"] = "postgres"
    session_ttl: timedelta = timedelta(hours=24)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ helpers

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            if self.dialect == "sqlite":
                cur.execute("BEGIN IMMEDIATE")  # take the write lock up front: no upgrade deadlocks
            yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _load(value: Any) -> Any:
        return json.loads(value) if isinstance(value, (str, bytes)) else value

    @staticmethod
    def _ts(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value))

    def _now(self) -> datetime:
        return self.clock()

    def _insert_revision(self, cur: Any, session_id: str, revision: int, data: RevisionData, now: datetime) -> None:
        cur.execute(
            self._sql(
                "INSERT INTO draft_revisions (session_id, revision, state, candidate_goal, missing_fields, "
                "next_question, ready, working_state, created_at) VALUES (?,?,?,?,?,?,?,?,?)"
            ),
            (
                session_id, revision, data.state, self._json(data.candidate_goal), self._json(data.missing_fields),
                data.next_question, data.ready, self._json(data.working_state), now.isoformat(),
            ),
        )

    def _insert_messages(self, cur: Any, session_id: str, revision: int, messages: Sequence[Message], now: datetime) -> None:
        for offset, m in enumerate(messages):
            cur.execute(
                self._sql(
                    "INSERT INTO messages (message_id, session_id, revision, role, content, created_at) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (str(uuid.uuid4()), session_id, revision, m.role, m.content,
                 (now + timedelta(microseconds=offset)).isoformat()),
            )

    # ------------------------------------------------------------------ commands

    def create_session(self, owner_user_id: str, data: RevisionData, messages: Sequence[Message]) -> DraftRecord:
        now = self._now()
        session_id = str(uuid.uuid4())
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "INSERT INTO sessions (session_id, owner_user_id, state, current_revision, ready_for_confirmation, "
                    "confirmed_goal_id, created_at, updated_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)"
                ),
                (session_id, owner_user_id, data.state, 1, data.ready, None, now.isoformat(), now.isoformat(),
                 (now + self.session_ttl).isoformat()),
            )
            self._insert_revision(cur, session_id, 1, data, now)
            self._insert_messages(cur, session_id, 1, messages, now)
        return self.get_current(session_id, owner_user_id)

    def append_revision(
        self,
        session_id: str,
        owner_user_id: str,
        expected_revision: int,
        data: RevisionData,
        messages: Sequence[Message],
    ) -> DraftRecord:
        now = self._now()
        new_revision = expected_revision + 1
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE sessions SET current_revision = ?, state = ?, ready_for_confirmation = ?, updated_at = ? "
                    "WHERE session_id = ? AND owner_user_id = ? AND current_revision = ? "
                    "AND state IN ('collecting','ready_for_confirmation') AND expires_at > ?"
                ),
                (new_revision, data.state, data.ready, now.isoformat(), session_id, owner_user_id,
                 expected_revision, now.isoformat()),
            )
            if cur.rowcount != 1:
                self._raise_for_failed_write(cur, session_id, owner_user_id, now)
            self._insert_revision(cur, session_id, new_revision, data, now)
            self._insert_messages(cur, session_id, new_revision, messages, now)
        return self.get_current(session_id, owner_user_id)

    def _raise_for_failed_write(self, cur: Any, session_id: str, owner_user_id: str, now: datetime) -> None:
        cur.execute(
            self._sql("SELECT state, current_revision, expires_at FROM sessions WHERE session_id = ? AND owner_user_id = ?"),
            (session_id, owner_user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        state, current, expires_at = row
        if state not in OPEN_STATES or self._ts(expires_at) <= now:
            raise SessionClosed(state)
        raise StaleRevision(int(current))

    def mark_confirmed(self, session_id: str, expected_revision: int, goal_id: str) -> None:
        """Idempotent: same goal at same revision is a no-op; anything else conflicts."""
        now = self._now()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE sessions SET state = 'confirmed', confirmed_goal_id = ?, ready_for_confirmation = false, "
                    "updated_at = ? WHERE session_id = ? AND current_revision = ? AND state = 'ready_for_confirmation'"
                ),
                (goal_id, now.isoformat(), session_id, expected_revision),
            )
            if cur.rowcount == 1:
                return
            cur.execute(
                self._sql("SELECT state, current_revision, confirmed_goal_id FROM sessions WHERE session_id = ?"),
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            state, current, confirmed_goal = row
            if state == "confirmed" and int(current) == expected_revision and str(confirmed_goal) == goal_id:
                return
            raise ConfirmConflict(f"session is {state} at revision {current}")

    def record_attempts(self, session_id: str | None, revision: int | None, attempts: Sequence[Attempt]) -> None:
        if not attempts:
            return
        now = self._now()
        with self._tx() as cur:
            for a in attempts:
                cur.execute(
                    self._sql(
                        "INSERT INTO llm_attempts (id, session_id, revision, model_id, error_code, latency_ms, created_at) "
                        "VALUES (?,?,?,?,?,?,?)"
                    ),
                    (str(uuid.uuid4()), session_id, revision, a.model_id, a.error_code, a.latency_ms, now.isoformat()),
                )

    # ------------------------------------------------------------------ queries

    def get_current(self, session_id: str, owner_user_id: str | None) -> DraftRecord:
        """``owner_user_id=None`` is reserved for authenticated GM internal reads."""
        conn = self.connect()
        try:
            cur = conn.cursor()
            sql = (
                "SELECT s.session_id, s.owner_user_id, s.state, s.current_revision, s.ready_for_confirmation, "
                "s.confirmed_goal_id, s.expires_at, r.candidate_goal, r.missing_fields, r.next_question, r.working_state "
                "FROM sessions s JOIN draft_revisions r ON r.session_id = s.session_id AND r.revision = s.current_revision "
                "WHERE s.session_id = ?"
            )
            params: tuple[Any, ...] = (session_id,)
            if owner_user_id is not None:
                sql += " AND s.owner_user_id = ?"
                params += (owner_user_id,)
            cur.execute(self._sql(sql), params)
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            raise SessionNotFound(session_id)
        (sid, owner, state, revision, ready, goal_id, expires_at, candidate, missing, question, working) = row
        return DraftRecord(
            session_id=str(sid), owner_user_id=str(owner), state=state, revision=int(revision),
            candidate_goal=self._load(candidate), missing_fields=self._load(missing), next_question=question,
            ready_for_confirmation=bool(ready), working_state=self._load(working),
            confirmed_goal_id=str(goal_id) if goal_id else None, expires_at=self._ts(expires_at),
        )

    def count_user_messages(self, session_id: str) -> int:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(self._sql("SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'"), (session_id,))
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def transcript(self, session_id: str) -> list[Message]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                self._sql("SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at, message_id"),
                (session_id,),
            )
            return [Message(role=r, content=c) for r, c in cur.fetchall()]
        finally:
            conn.close()


# --------------------------------------------------------------------------- SQLite (tests/dev)

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('collecting','ready_for_confirmation','confirmed','abandoned')),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1),
    ready_for_confirmation BOOLEAN NOT NULL DEFAULT 0, confirmed_goal_id TEXT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    CHECK (state <> 'ready_for_confirmation' OR ready_for_confirmation),
    CHECK ((state = 'confirmed') = (confirmed_goal_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id), revision INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')), content TEXT NOT NULL,
    token_count INTEGER NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_revisions (
    session_id TEXT NOT NULL REFERENCES sessions(session_id), revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL, candidate_goal TEXT NOT NULL, missing_fields TEXT NOT NULL, next_question TEXT NULL,
    ready BOOLEAN NOT NULL, working_state TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, revision)
);
CREATE TRIGGER IF NOT EXISTS draft_revisions_no_update BEFORE UPDATE ON draft_revisions
BEGIN SELECT RAISE(ABORT, 'draft_revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS draft_revisions_no_delete BEFORE DELETE ON draft_revisions
BEGIN SELECT RAISE(ABORT, 'draft_revisions are immutable'); END;
CREATE TABLE IF NOT EXISTS llm_attempts (
    id TEXT PRIMARY KEY, session_id TEXT NULL, revision INTEGER NULL, model_id TEXT NOT NULL,
    error_code TEXT NULL, latency_ms INTEGER NOT NULL, created_at TEXT NOT NULL
);
"""

_sqlite_init_lock = threading.Lock()


def sqlite_repository(path: str | Path, **kwargs: Any) -> SqlDraftRepository:
    """File-backed SQLite repository for tests and the zero-dependency dev mode."""
    path = str(path)

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        return _SqliteAdapter(conn)

    with _sqlite_init_lock:
        conn = sqlite3.connect(path)
        conn.executescript(_SQLITE_SCHEMA)
        conn.close()
    return SqlDraftRepository(connect=connect, dialect="sqlite", **kwargs)


class _SqliteAdapter:
    """Makes autocommit sqlite3 behave like a DB-API transaction under BEGIN IMMEDIATE."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()

    def commit(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("COMMIT")

    def rollback(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def close(self) -> None:
        self._conn.close()

    def execute(self, *args: Any) -> Any:
        return self._conn.execute(*args)


def postgres_repository(dsn: str, **kwargs: Any) -> SqlDraftRepository:
    """psycopg 3 repository. Run migrations/0001_init.sql first."""
    import psycopg  # imported lazily so tests do not need the driver

    return SqlDraftRepository(connect=lambda: psycopg.connect(dsn), dialect="postgres", **kwargs)
