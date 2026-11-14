"""Explicit transactional-store doubles for deterministic scheduling tests only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fake_pipeline import MemoryJournal, MemoryUsage
from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.jobs import execute_attempt
from goalpulse_discovery.scheduling import effective_interval


class MemoryScanStore:
    def __init__(self, initial_jobs=None, *, clock=lambda: datetime.now(timezone.utc), max_active=2):
        self.initial_jobs, self.clock, self.max_active = initial_jobs, clock, max_active
        self.schedules, self.scans, self.requests, self.scan_plans = {}, {}, {}, {}
        self.last_result = None

    def candidates(self):
        known = set(self.schedules)
        if self.initial_jobs:
            known.update(UUID(k[0]) for k in self.initial_jobs.jobs)
        return sorted(known, key=str)

    def admit(self, goal, *, initial=False):
        key, owner = UUID(goal["goal_id"]), goal["owner_user_id"]
        old = self.schedules.get(key)
        if old and old["owner"] != owner:
            raise ValueError("Owner changed")
        reason = None
        try:
            interval = effective_interval(goal)
        except ValueError:
            interval, reason = goal["monitoring"]["frequency_minutes"], "INTERVAL_RECONFIRMATION_REQUIRED"
        if goal["criteria"]["delivery_country"] != "FI":
            reason = "UNSUPPORTED_DESTINATION"
        active = sum(s["owner"] == owner and s["state"] == "active" for gid, s in self.schedules.items() if gid != key)
        if not reason and active >= self.max_active:
            reason = "ACTIVE_GOAL_LIMIT"
        due = None if initial else self.clock()
        if old and old["version"] == goal["version"] and old["state"] == "active" and reason is None:
            due = old["next_due_at"]
        self.schedules[key] = {"owner": owner, "version": goal["version"], "interval_minutes": interval,
                               "state": "blocked" if reason else "active", "reason": reason,
                               "next_due_at": due, "last_result": reason or (old or {}).get("last_result")}
        return reason

    def inactive(self, state):
        if state.goal_id in self.schedules:
            self.schedules[state.goal_id].update(state="inactive", reason="GOAL_" + state.lifecycle.upper(), next_due_at=None)

    def initial_finished(self, goal_id, version, result):
        s = self.schedules[goal_id]
        if s["state"] == "active" and s["version"] == version:
            s["last_result"] = result
            if s["next_due_at"] is None:
                s["next_due_at"] = self.clock() + timedelta(minutes=s["interval_minutes"])

    def enqueue(self, goal_id, version, *, principal=None, request_id=None):
        if request_id in self.requests:
            old = self.requests[request_id]
            if old[:3] != (principal, goal_id, version):
                raise ValueError("Operator identity reused")
            return {"status": "duplicate", "scan_id": str(old[3])}
        s = self.schedules.get(goal_id)
        if not s or s["state"] != "active":
            return {"status": "blocked", "reason": s["reason"] if s else "UNKNOWN_GOAL"}
        existing = next((scan for scan in self.scans.values() if scan["goal_id"] == goal_id
                         and scan["version"] == version and scan["status"] == "pending"), None)
        initial = self.initial_jobs.jobs.get((str(goal_id), version)) if self.initial_jobs else None
        if existing or (initial and initial["status"] == "pending"):
            scan_id, status = existing["scan_id"] if existing else uuid5(NAMESPACE_URL, f"initial:{goal_id}:{version}"), "coalesced"
        elif request_id is None and s["next_due_at"] is not None and s["next_due_at"] > self.clock():
            return {"status": "not_due"}
        else:
            due = self.clock() if request_id is not None or s["next_due_at"] is None else s["next_due_at"]
            identity = f"goalpulse:operator:{request_id}" if request_id else f"goalpulse:scan:{goal_id}:{version}:{due.isoformat()}"
            scan_id = uuid5(NAMESPACE_URL, identity)
            self.scans.setdefault(scan_id, {"scan_id": scan_id, "goal_id": goal_id, "version": version,
                "correlation_id": uuid4(), "status": "pending", "attempts": 0, "next_attempt_at": self.clock(),
                "deadline": self.clock() + timedelta(hours=1)})
            s["next_due_at"], status = self.clock() + timedelta(minutes=s["interval_minutes"]), "queued"
        if request_id is not None:
            self.requests[request_id] = (principal, goal_id, version, scan_id)
        return {"status": status, "scan_id": str(scan_id)}

    def run_once(self, worker):
        scan = next((s for s in self.scans.values() if s["status"] == "pending" and s["next_attempt_at"] <= self.clock()), None)
        if scan is None:
            self.last_result = None
            return "idle"
        state, result, retry_at = execute_attempt(
            lambda: worker.run(scan["goal_id"], scan["version"], scan["correlation_id"], scan_id=scan["scan_id"]),
            now=self.clock(), deadline=scan["deadline"], attempts=scan["attempts"])
        scan.update(status=state, result=result, next_attempt_at=retry_at, attempts=scan["attempts"] + 1)
        self.schedules[scan["goal_id"]]["last_result"] = result
        self.last_result = result
        return state

    def plans(self, scan_id):
        return self.scan_plans.setdefault(scan_id, MemoryJournal())

    def status(self, goal_id):
        return deepcopy(self.schedules.get(goal_id))


class MemoryUserUsage(MemoryUsage):
    def __init__(self, maximum=100):
        super().__init__()
        self.maximum, self.counts = maximum, {}

    def reserve(self, resource, now, *, owner_user_id, **kwargs):
        key = (owner_user_id, now.date())
        if self.counts.get(key, 0) >= self.maximum:
            raise ProviderError("USER_DAILY_CALL_LIMIT")
        self.counts[key] = self.counts.get(key, 0) + 1
        return super().reserve(resource, now, owner_user_id=owner_user_id, **kwargs)
