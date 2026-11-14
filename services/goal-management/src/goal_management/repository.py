"""Goal Management-owned PostgreSQL persistence boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class GoalVersionRecord:
    goal_id: UUID
    owner_user_id: UUID
    confirmed_session_id: UUID
    version: int
    spec: dict[str, Any]
    approved_at: datetime
    approved_draft_revision: int
    correlation_id: UUID


class ConfirmationConflict(RuntimeError):
    """A confirmed session was replayed with incompatible owner/revision data."""


class GoalRepository:
    """Operations sharing a caller-supplied connection and transaction scope."""

    OUTBOX_CLAIM_LEASE_SECONDS = 300
    MAX_OUTBOX_ATTEMPTS = 10

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def get_confirmed_for_session(
        self, session_id: UUID
    ) -> tuple[dict[str, Any], int, UUID] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT v.spec,v.approved_draft_revision,g.owner_user_id "
                "FROM goals g JOIN goal_versions v ON v.goal_id=g.goal_id AND v.version=g.current_version "
                "WHERE g.confirmed_session_id=%s",
                (session_id,),
            )
            row = cursor.fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def create_version_one(self, record: GoalVersionRecord) -> dict[str, Any]:
        """Atomically persist a version and one activation intent.

        Concurrent exact replays return the already-committed immutable spec;
        incompatible reuse of a confirmed session is rejected.
        """
        event_id = uuid4()
        event = {
            "event_id": str(event_id),
            "event_type": "goal.activated.v1",
            "schema_version": 1,
            "occurred_at": record.approved_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "producer": "goal-management",
            "correlation_id": str(record.correlation_id),
            "aggregate_id": str(record.goal_id),
            "payload": {"goal_id": str(record.goal_id), "goal_version": record.version},
        }
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO goals "
                "(goal_id,owner_user_id,lifecycle,current_version,confirmed_session_id) "
                "VALUES(%s,%s,'active',1,%s) "
                "ON CONFLICT(confirmed_session_id) DO NOTHING RETURNING goal_id",
                (record.goal_id, record.owner_user_id, record.confirmed_session_id),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    "SELECT g.owner_user_id,v.approved_draft_revision,v.spec "
                    "FROM goals g JOIN goal_versions v "
                    "ON v.goal_id=g.goal_id AND v.version=g.current_version "
                    "WHERE g.confirmed_session_id=%s",
                    (record.confirmed_session_id,),
                )
                existing = cursor.fetchone()
                if (
                    existing is not None
                    and existing[0] == record.owner_user_id
                    and existing[1] == record.approved_draft_revision
                ):
                    return existing[2]
                raise ConfirmationConflict("confirmed session replay does not match the durable goal")
            cursor.execute(
                "INSERT INTO goal_versions "
                "(goal_id,version,spec,approved_at,approved_draft_revision) VALUES(%s,%s,%s,%s,%s)",
                (
                    record.goal_id,
                    record.version,
                    Jsonb(record.spec),
                    record.approved_at,
                    record.approved_draft_revision,
                ),
            )
            cursor.execute(
                "INSERT INTO outbox "
                "(id,event_id,event_type,payload_jsonb,status,next_attempt_at) "
                "VALUES(%s,%s,'goal.activated.v1',%s,'pending',now())",
                (event_id, event_id, Jsonb(event)),
            )
        return record.spec

    def get_version(self, goal_id: UUID, version: int) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT spec FROM goal_versions WHERE goal_id=%s AND version=%s",
                (goal_id, version),
            )
            row = cursor.fetchone()
        return None if row is None else row[0]

    def get_runtime(self, goal_id: UUID) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT goal_id,owner_user_id,current_version,lifecycle FROM goals WHERE goal_id=%s",
                (goal_id,),
            )
            row = cursor.fetchone()
        return None if row is None else {
            "goal_id": str(row[0]),
            "owner_user_id": str(row[1]),
            "current_version": row[2],
            "lifecycle": row[3],
        }

    def list_active(self, cursor: UUID | None, limit: int) -> tuple[list[dict[str, Any]], UUID | None]:
        query = "SELECT goal_id,current_version FROM goals WHERE lifecycle='active'"
        parameters: list[Any] = []
        if cursor is not None:
            query += " AND goal_id>%s"
            parameters.append(cursor)
        query += " ORDER BY goal_id LIMIT %s"
        parameters.append(limit + 1)
        with self._connection.cursor() as db_cursor:
            db_cursor.execute(query, parameters)
            rows = db_cursor.fetchall()
        page = rows[:limit]
        return (
            [{"goal_id": str(row[0]), "current_version": row[1]} for row in page],
            page[-1][0] if len(rows) > limit else None,
        )

    def list_for_owner(self, owner: UUID, status: str | None) -> list[dict[str, Any]]:
        sql = (
            "SELECT g.goal_id,g.current_version,g.lifecycle,v.spec->>'title',"
            "COALESCE(p.opportunity_status,'not_found'),p.last_evaluated_at "
            "FROM goals g "
            "JOIN goal_versions v ON v.goal_id=g.goal_id AND v.version=g.current_version "
            "LEFT JOIN goal_evaluation_projection p "
            "ON p.goal_id=g.goal_id AND p.version=g.current_version "
            "WHERE g.owner_user_id=%s"
        )
        parameters: list[Any] = [owner]
        if status:
            sql += " AND g.lifecycle=%s"
            parameters.append(status)
        sql += " ORDER BY g.created_at DESC"
        with self._connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return [
            {
                "goal_id": str(row[0]),
                "version": row[1],
                "lifecycle": row[2],
                "title": row[3] or "Goal",
                "opportunity_status": row[4],
                "last_evaluated_at": (
                    None
                    if row[5] is None
                    else row[5].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                ),
            }
            for row in rows
        ]

    def get_for_owner(self, owner: UUID, goal_id: UUID) -> dict[str, Any] | None:
        # Frozen Gateway v1 requires this response to remain an exact GoalSpec;
        # evaluation detail belongs to Goal Evaluation's additive Phase 2 route.
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT v.spec FROM goals g JOIN goal_versions v "
                "ON v.goal_id=g.goal_id AND v.version=g.current_version "
                "WHERE g.goal_id=%s AND g.owner_user_id=%s",
                (goal_id, owner),
            )
            row = cursor.fetchone()
        return None if row is None else row[0]

    def transition(self, owner: UUID, goal_id: UUID, action: str | None) -> str:
        from .lifecycle import transition

        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT lifecycle FROM goals WHERE goal_id=%s AND owner_user_id=%s FOR UPDATE",
                (goal_id, owner),
            )
            row = cursor.fetchone()
            if row is None:
                return "missing"
            target = transition(row[0], action)
            if target is None:
                return "invalid"
            cursor.execute(
                "UPDATE goals SET lifecycle=%s,updated_at=now() WHERE goal_id=%s",
                (target, goal_id),
            )
        return "ok"

    def process_evaluated(
        self,
        *,
        event_id: str,
        event_type: str,
        goal_id: str,
        goal_version: int,
        evaluation_id: str,
        status: str,
        occurred_at: datetime,
    ) -> str:
        """Deduplicate, current-version gate, and project in one transaction."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO inbox(event_id,event_type) VALUES(%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING event_id",
                (event_id, event_type),
            )
            if cursor.fetchone() is None:
                return "duplicate"
            cursor.execute(
                "SELECT current_version,lifecycle FROM goals WHERE goal_id=%s FOR UPDATE",
                (goal_id,),
            )
            runtime = cursor.fetchone()
            if runtime is None or runtime[0] != goal_version:
                return "stale"
            if runtime[1] != "active":
                return "inactive"
            cursor.execute(
                "INSERT INTO goal_evaluation_projection "
                "(goal_id,version,evaluation_id,status,last_evaluated_at,opportunity_status) "
                "VALUES(%s,%s,%s,%s,%s,CASE "
                "WHEN %s='satisfied' THEN 'verified' "
                "WHEN %s='unsatisfied' THEN 'not_found' ELSE 'unknown' END) "
                "ON CONFLICT(goal_id) DO UPDATE SET "
                "version=EXCLUDED.version,evaluation_id=EXCLUDED.evaluation_id,status=EXCLUDED.status,"
                "last_evaluated_at=EXCLUDED.last_evaluated_at,"
                "opportunity_status=EXCLUDED.opportunity_status,updated_at=now() "
                "WHERE goal_evaluation_projection.version<EXCLUDED.version OR "
                "(goal_evaluation_projection.version=EXCLUDED.version AND "
                "goal_evaluation_projection.last_evaluated_at<=EXCLUDED.last_evaluated_at) "
                "RETURNING goal_id",
                (goal_id, goal_version, evaluation_id, status, occurred_at, status, status),
            )
            return "projected" if cursor.fetchone() is not None else "older"

    def claim_outbox(self) -> list[dict[str, Any]]:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE outbox SET status='pending',claimed_at=NULL "
                "WHERE status='publishing' AND claimed_at<=now()-(%s * INTERVAL '1 second')",
                (self.OUTBOX_CLAIM_LEASE_SECONDS,),
            )
            cursor.execute(
                "SELECT event_id,event_type,payload_jsonb FROM outbox "
                "WHERE status='pending' AND next_attempt_at<=now() "
                "ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED"
            )
            rows = cursor.fetchall()
            cursor.executemany(
                "UPDATE outbox SET status='publishing',attempts=attempts+1,claimed_at=now() "
                "WHERE event_id=%s",
                [(row[0],) for row in rows],
            )
        return [
            {"event_id": str(row[0]), "event_type": row[1], **row[2]}
            for row in rows
        ]

    def mark_delivered(self, event_id: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE outbox SET status='delivered',claimed_at=NULL,delivered_at=now() WHERE event_id=%s",
                (event_id,),
            )

    def mark_retry(self, event_id: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE outbox SET "
                "status=CASE WHEN attempts>=%s THEN 'failed' ELSE 'pending' END,"
                "claimed_at=NULL,"
                "next_attempt_at=now()+(LEAST(power(2,attempts),300) * INTERVAL '1 second') "
                "WHERE event_id=%s",
                (self.MAX_OUTBOX_ATTEMPTS, event_id),
            )


class DatabaseRepository:
    """Resilient connection-per-operation adapter for APIs and workers."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        self._database_url = database_url
        self._connect = connect

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._connect(self._database_url, autocommit=True) as connection:
            return getattr(GoalRepository(connection), method)(*args, **kwargs)

    def ping(self) -> None:
        with self._connect(self._database_url, autocommit=True) as connection:
            connection.execute("SELECT 1")

    def get_confirmed_for_session(self, session_id: UUID):
        return self._call("get_confirmed_for_session", session_id)

    def create_version_one(self, record: GoalVersionRecord):
        return self._call("create_version_one", record)

    def get_version(self, goal_id: UUID, version: int):
        return self._call("get_version", goal_id, version)

    def get_runtime(self, goal_id: UUID):
        return self._call("get_runtime", goal_id)

    def list_active(self, cursor: UUID | None, limit: int):
        return self._call("list_active", cursor, limit)

    def list_for_owner(self, owner: UUID, status: str | None):
        return self._call("list_for_owner", owner, status)

    def get_for_owner(self, owner: UUID, goal_id: UUID):
        return self._call("get_for_owner", owner, goal_id)

    def transition(self, owner: UUID, goal_id: UUID, action: str | None):
        return self._call("transition", owner, goal_id, action)

    def process_evaluated(self, **kwargs: Any):
        return self._call("process_evaluated", **kwargs)

    def claim_outbox(self):
        return self._call("claim_outbox")

    def mark_delivered(self, event_id: str):
        return self._call("mark_delivered", event_id)

    def mark_retry(self, event_id: str):
        return self._call("mark_retry", event_id)
