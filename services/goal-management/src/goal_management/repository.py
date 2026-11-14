"""Direct, typed persistence boundary; no other service database is accessed."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
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

class GoalRepository:
    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def create_version_one(self, record: GoalVersionRecord) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("INSERT INTO goals (goal_id, owner_user_id, lifecycle, current_version, confirmed_session_id) VALUES (%s,%s,'active',1,%s)", (record.goal_id, record.owner_user_id, record.confirmed_session_id))
            cursor.execute("INSERT INTO goal_versions (goal_id, version, spec, approved_at, approved_draft_revision) VALUES (%s,%s,%s,%s,%s)", (record.goal_id, record.version, Jsonb(record.spec), record.approved_at, record.approved_draft_revision))

    def get_version(self, goal_id: UUID, version: int) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT spec FROM goal_versions WHERE goal_id=%s AND version=%s", (goal_id, version))
            row = cursor.fetchone()
        return None if row is None else row[0]

    def get_runtime(self, goal_id: UUID) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT goal_id, owner_user_id, current_version, lifecycle FROM goals WHERE goal_id=%s", (goal_id,))
            row=cursor.fetchone()
        return None if row is None else {'goal_id':str(row[0]),'owner_user_id':str(row[1]),'current_version':row[2],'lifecycle':row[3]}

    def list_active(self, cursor: UUID | None, limit: int) -> tuple[list[dict[str, Any]], UUID | None]:
        query="SELECT goal_id,current_version FROM goals WHERE lifecycle='active'"; params:list[Any]=[]
        if cursor is not None: query+=" AND goal_id>%s"; params.append(cursor)
        query+=" ORDER BY goal_id LIMIT %s"; params.append(limit+1)
        with self._connection.cursor() as cur: cur.execute(query,params); rows=cur.fetchall()
        page=rows[:limit]; return ([{'goal_id':str(r[0]),'current_version':r[1]} for r in page], page[-1][0] if len(rows)>limit else None)

    def list_for_owner(self, owner: UUID, status: str | None) -> list[dict[str, Any]]:
        sql="SELECT g.goal_id,g.current_version,g.lifecycle,v.spec->>'title' FROM goals g JOIN goal_versions v ON v.goal_id=g.goal_id AND v.version=g.current_version WHERE g.owner_user_id=%s"; args=[owner]
        if status: sql+=' AND g.lifecycle=%s'; args.append(status)
        sql+=' ORDER BY g.created_at DESC'
        with self._connection.cursor() as cur: cur.execute(sql,args); rows=cur.fetchall()
        return [{'goal_id':str(r[0]),'version':r[1],'lifecycle':r[2],'title':r[3] or 'Goal','opportunity_status':'not_found','last_evaluated_at':None} for r in rows]

    def get_for_owner(self, owner: UUID, goal_id: UUID) -> dict[str, Any] | None:
        with self._connection.cursor() as cur:
            cur.execute("SELECT v.spec FROM goals g JOIN goal_versions v ON v.goal_id=g.goal_id AND v.version=g.current_version WHERE g.goal_id=%s AND g.owner_user_id=%s",(goal_id,owner)); row=cur.fetchone()
        return None if row is None else row[0]

    def transition(self, owner: UUID, goal_id: UUID, action: str | None) -> str:
        from .lifecycle import transition
        with self._connection.transaction(),self._connection.cursor() as cur:
            cur.execute("SELECT lifecycle FROM goals WHERE goal_id=%s AND owner_user_id=%s FOR UPDATE",(goal_id,owner)); row=cur.fetchone()
            if row is None: return 'missing'
            target=transition(row[0],action)
            if target is None: return 'invalid'
            cur.execute("UPDATE goals SET lifecycle=%s,updated_at=now() WHERE goal_id=%s",(target,goal_id))
        return 'ok'

    def inbox_seen(self,event_id:str)->bool:
        with self._connection.cursor() as c: c.execute('SELECT 1 FROM inbox WHERE event_id=%s',(event_id,)); return c.fetchone() is not None
    def mark_inbox(self,event_id:str,event_type:str)->None:
        with self._connection.cursor() as c: c.execute('INSERT INTO inbox(event_id,event_type) VALUES(%s,%s) ON CONFLICT DO NOTHING',(event_id,event_type))
    def current_version(self,goal_id:str)->int|None:
        with self._connection.cursor() as c: c.execute('SELECT current_version FROM goals WHERE goal_id=%s',(goal_id,)); r=c.fetchone(); return None if r is None else r[0]
    def update_projection(self,goal_id:str,version:int,evaluation_id:str,status:str)->None:
        with self._connection.cursor() as c: c.execute("INSERT INTO goal_evaluation_projection(goal_id,version,evaluation_id,status,last_evaluated_at,opportunity_status) VALUES(%s,%s,%s,%s,now(),CASE WHEN %s='satisfied' THEN 'verified' ELSE 'unknown' END) ON CONFLICT(goal_id) DO UPDATE SET version=EXCLUDED.version,evaluation_id=EXCLUDED.evaluation_id,status=EXCLUDED.status,last_evaluated_at=EXCLUDED.last_evaluated_at,opportunity_status=EXCLUDED.opportunity_status,updated_at=now()",(goal_id,version,evaluation_id,status,status))
    def claim_outbox(self)->list[dict[str,Any]]:
        with self._connection.transaction(),self._connection.cursor() as c:
            c.execute("SELECT event_id,event_type,payload_jsonb FROM outbox WHERE status='pending' AND next_attempt_at<=now() FOR UPDATE SKIP LOCKED")
            rows=c.fetchall(); c.executemany("UPDATE outbox SET status='publishing',attempts=attempts+1 WHERE event_id=%s",[(r[0],) for r in rows])
        return [{'event_id':str(r[0]),'event_type':r[1],**r[2]} for r in rows]
    def mark_delivered(self,event_id:str)->None:
        with self._connection.cursor() as c: c.execute("UPDATE outbox SET status='delivered',delivered_at=now() WHERE event_id=%s",(event_id,))
    def mark_retry(self,event_id:str)->None:
        with self._connection.cursor() as c: c.execute("UPDATE outbox SET status='pending',next_attempt_at=now()+interval '1 minute' WHERE event_id=%s",(event_id,))
