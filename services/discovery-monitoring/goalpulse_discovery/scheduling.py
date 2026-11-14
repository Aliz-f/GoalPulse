"""Durable polling and admission; no public endpoint or provider-native monitor.

Only authenticated GM state supplies owner, lifecycle, version and approved
cadence. Storage locks serialize admission/due slots across scheduler processes.
"""

from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from .jobs import execute_attempt


def utc(value):
    if value.utcoffset() is None:
        raise ValueError("Timezone-aware scheduler clock required")
    return value.astimezone(timezone.utc)


def effective_interval(goal: dict) -> int:
    """60 is the drafting default; never replace an explicit unsupported request."""
    interval = goal.get("monitoring", {}).get("frequency_minutes", 60)
    if type(interval) is not int or interval < 30 or interval > 10080:
        raise ValueError("INTERVAL_RECONFIRMATION_REQUIRED")
    return interval


class ScanStore:
    def __init__(self, database, *, clock=lambda: datetime.now(timezone.utc), max_active=2):
        if type(max_active) is not int or not 1 <= max_active <= 2:
            raise ValueError("Invalid per-user active goal limit")
        self.database, self.clock, self.max_active = database, clock, max_active
        self.last_result = None

    def candidates(self):
        with self.database.transaction() as c:
            c.execute("""SELECT known.goal_id FROM
                (SELECT goal_id FROM discovery_jobs UNION SELECT goal_id FROM scan_schedules
                 UNION SELECT goal_id FROM goal_source_links) known
                LEFT JOIN scan_schedules s ON s.goal_id=known.goal_id
                ORDER BY s.last_checked_at NULLS FIRST,known.goal_id LIMIT 100""")
            return [UUID(str(r[0])) for r in c.fetchall()]

    def admit(self, goal: dict, *, initial=False) -> str | None:
        now = utc(self.clock())
        goal_id, owner, version = goal["goal_id"], goal["owner_user_id"], goal["version"]
        try:
            interval, reason = effective_interval(goal), None
        except ValueError:
            interval = goal["monitoring"]["frequency_minutes"]
            reason = "INTERVAL_RECONFIRMATION_REQUIRED"
        if goal["criteria"]["delivery_country"] != "FI":
            reason = "UNSUPPORTED_DESTINATION"
        with self.database.transaction() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("admission:" + owner,))
            c.execute("SELECT owner_user_id,goal_version,state,next_due_at FROM scan_schedules WHERE goal_id=%s FOR UPDATE", (goal_id,))
            previous = c.fetchone()
            if previous and str(previous[0]) != owner:
                raise ValueError("Authenticated goal owner changed")
            c.execute("SELECT count(*) FROM scan_schedules WHERE owner_user_id=%s AND state='active' AND goal_id<>%s", (owner, goal_id))
            if reason is None and c.fetchone()[0] >= self.max_active:
                reason = "ACTIVE_GOAL_LIMIT"
            state = "blocked" if reason else "active"
            due = None if initial else now
            if previous and previous[1] == version and previous[2] == "active" and reason is None:
                due = previous[3]
            c.execute("""INSERT INTO scan_schedules
                (goal_id,goal_version,owner_user_id,interval_minutes,state,reason,next_due_at,last_checked_at,last_result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(goal_id) DO UPDATE SET goal_version=EXCLUDED.goal_version,
                interval_minutes=EXCLUDED.interval_minutes,state=EXCLUDED.state,reason=EXCLUDED.reason,
                next_due_at=EXCLUDED.next_due_at,last_checked_at=EXCLUDED.last_checked_at,
                last_result=COALESCE(EXCLUDED.reason,scan_schedules.last_result)""",
                (goal_id, version, owner, interval, state, reason, due, now, reason))
            return reason

    def inactive(self, state):
        with self.database.transaction() as c:
            c.execute("""UPDATE scan_schedules SET state='inactive',reason=%s,next_due_at=NULL,last_checked_at=%s
                WHERE goal_id=%s""", ("GOAL_" + state.lifecycle.upper(), utc(self.clock()), str(state.goal_id)))

    def initial_finished(self, goal_id, version, result):
        with self.database.transaction() as c:
            c.execute("""UPDATE scan_schedules SET last_result=%s,
                next_due_at=COALESCE(next_due_at,%s+interval_minutes*interval '1 minute')
                WHERE goal_id=%s AND goal_version=%s AND state='active'""",
                (result, utc(self.clock()), str(goal_id), version))

    def enqueue(self, goal_id, version, *, principal=None, request_id=None):
        now = utc(self.clock())
        with self.database.transaction() as c:
            if request_id is not None:
                c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("operator:" + str(request_id),))
                c.execute("SELECT principal,goal_id,goal_version,scan_id FROM operator_scan_requests WHERE request_id=%s", (str(request_id),))
                old = c.fetchone()
                if old:
                    if (old[0], str(old[1]), old[2]) != (principal, str(goal_id), version):
                        raise ValueError("Operator request identity reused")
                    return {"status": "duplicate", "scan_id": str(old[3])}
            c.execute("SELECT state,reason,next_due_at,interval_minutes FROM scan_schedules WHERE goal_id=%s AND goal_version=%s FOR UPDATE", (str(goal_id), version))
            row = c.fetchone()
            if not row or row[0] != "active":
                return {"status": "blocked", "reason": row[1] if row else "UNKNOWN_GOAL"}
            c.execute("SELECT scan_id FROM scheduled_scans WHERE goal_id=%s AND goal_version=%s AND status='pending'", (str(goal_id), version))
            pending = c.fetchone()
            # An initial pending/retrying job already owns this goal/version.
            c.execute("SELECT job_id FROM discovery_jobs WHERE goal_id=%s AND goal_version=%s AND status='pending'", (str(goal_id), version))
            initial = c.fetchone()
            if pending or initial:
                scan_id, status = str((pending or initial)[0]), "coalesced"
            elif request_id is None and row[2] is not None and row[2] > now:
                return {"status": "not_due"}
            else:
                due = now if request_id is not None or row[2] is None else row[2]
                identity = f"goalpulse:operator:{request_id}" if request_id else f"goalpulse:scan:{goal_id}:{version}:{due.isoformat()}"
                scan_id = str(uuid5(NAMESPACE_URL, identity))
                c.execute("""INSERT INTO scheduled_scans
                    (scan_id,goal_id,goal_version,correlation_id,due_at,kind,next_attempt_at,deadline_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (scan_id, str(goal_id), version, str(uuid4()), due,
                     "operator" if request_id else "periodic", now, now + timedelta(hours=1)))
                c.execute("UPDATE scan_schedules SET next_due_at=%s WHERE goal_id=%s", (now + timedelta(minutes=row[3]), str(goal_id)))
                status = "queued"
            if request_id is not None:
                c.execute("""INSERT INTO operator_scan_requests
                    (request_id,principal,goal_id,goal_version,scan_id,requested_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                    (str(request_id), principal, str(goal_id), version, scan_id, now))
            return {"status": status, "scan_id": scan_id}

    def run_once(self, worker):
        with self.database.transaction() as c:
            now = utc(self.clock())
            c.execute("""SELECT scan_id,goal_id,goal_version,correlation_id,attempts,deadline_at
                FROM scheduled_scans WHERE status='pending' AND next_attempt_at<=%s
                ORDER BY next_attempt_at,scan_id LIMIT 1 FOR NO KEY UPDATE SKIP LOCKED""", (now,))
            row = c.fetchone()
            if row is None:
                self.last_result = None
                return "idle"
            scan, goal_id, version, correlation, attempts, deadline = row
            state, result, retry_at = execute_attempt(
                lambda: worker.run(UUID(str(goal_id)), version, UUID(str(correlation)), scan_id=UUID(str(scan))),
                now=now, deadline=deadline, attempts=attempts)
            c.execute("""UPDATE scheduled_scans SET status=%s,attempts=attempts+1,last_result=%s,next_attempt_at=%s WHERE scan_id=%s""",
                      (state, result, retry_at, str(scan)))
            # The claimed job is committed atomically with its visible outcome.
            c.execute("UPDATE scan_schedules SET last_result=%s WHERE goal_id=%s AND goal_version=%s", (result, str(goal_id), version))
            self.last_result = result
            return state

    def plans(self, scan_id):
        return ScanPlans(self.database, scan_id)

    def status(self, goal_id):
        with self.database.transaction() as c:
            c.execute("SELECT state,reason,last_result,next_due_at,interval_minutes FROM scan_schedules WHERE goal_id=%s", (str(goal_id),))
            row = c.fetchone()
            return dict(zip(("state", "reason", "last_result", "next_due_at", "interval_minutes"), row)) if row else None


class ScanPlans:
    def __init__(self, database, scan_id):
        self.database, self.scan_id = database, scan_id

    def load_plan(self, _goal_id, _version):
        with self.database.transaction() as c:
            c.execute("SELECT urls FROM scan_plans WHERE scan_id=%s", (str(self.scan_id),))
            row = c.fetchone()
            return row[0] if row else None

    def save_plan(self, _goal_id, _version, urls):
        if len(urls) > 3:
            raise ValueError("Source budget exceeded")
        with self.database.transaction() as c:
            c.execute("INSERT INTO scan_plans(scan_id,urls) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING", (str(self.scan_id), json.dumps(urls)))
            c.execute("SELECT urls FROM scan_plans WHERE scan_id=%s", (str(self.scan_id),))
            return c.fetchone()[0]


class ScanScheduler:
    def __init__(self, store, reader_factory, registry):
        self.store, self.reader_factory, self.registry = store, reader_factory, registry

    def prepare(self, goals, goal_id, version, *, initial=False):
        state = goals.runtime(goal_id)
        if state.lifecycle != "active" or state.current_version != version:
            self.registry.reconcile(goal_id, current_version=state.current_version, lifecycle=state.lifecycle)
            return None, "obsolete"
        goal = goals.version(goal_id, version)
        if str(state.owner_user_id) != goal["owner_user_id"]:
            raise ValueError("Authenticated owner mismatch")
        reason = self.store.admit(goal, initial=initial)
        if reason:
            self.registry.suspend(goal_id)
        else:
            self.registry.reconcile(goal_id, current_version=version, lifecycle="active")
        return goal, reason

    def tick(self):
        states = []
        failed = 0
        for goal_id in self.store.candidates():
            try:
                reader = self.reader_factory(uuid4())
                state = reader.runtime(goal_id)
                if state.lifecycle != "active":
                    self.store.inactive(state)
                    self.registry.reconcile(goal_id, current_version=state.current_version, lifecycle=state.lifecycle)
                else:
                    states.append((reader, state))
            except Exception:
                failed += 1  # Preserve occupied slots on uncertain GM state.
        queued = 0
        for reader, state in states:
            try:
                goal = reader.version(state.goal_id, state.current_version)
                if str(state.owner_user_id) != goal["owner_user_id"]:
                    raise ValueError("Authenticated owner mismatch")
                reason = self.store.admit(goal)
                if reason is None:
                    self.registry.reconcile(state.goal_id, current_version=state.current_version, lifecycle="active")
                    queued += self.store.enqueue(state.goal_id, state.current_version)["status"] == "queued"
                else:
                    self.registry.suspend(state.goal_id)
            except Exception:
                failed += 1
        return {"queued": queued, "dependency_failures": failed}

    def check_now(self, goal_id, request_id, principal):
        reader = self.reader_factory(uuid4())
        state = reader.runtime(goal_id)
        _, reason = self.prepare(reader, goal_id, state.current_version)
        if reason:
            return {"status": "blocked", "reason": reason}
        return self.store.enqueue(goal_id, state.current_version, principal=principal, request_id=request_id)
