"""Durable activation inbox + bounded discovery jobs in the owned PostgreSQL DB."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid5, NAMESPACE_URL

from .context_provider import ProviderError
from .persistence import Database


class DiscoveryJobs:
    def __init__(self, database: Database, *, clock=lambda: datetime.now(timezone.utc), max_attempts=5) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid job retry bound")
        self.database, self.clock, self.max_attempts = database, clock, max_attempts

    def accept(self, event: dict) -> bool:
        fingerprint = sha256(json.dumps(event, sort_keys=True, allow_nan=False).encode()).hexdigest()
        payload, now = event["payload"], self.clock()
        job_id = uuid5(NAMESPACE_URL, f"goalpulse:discovery:{payload['goal_id']}:{payload['goal_version']}")
        with self.database.transaction() as cursor:
            cursor.execute("INSERT INTO inbox (event_id,payload_hash) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING event_id",
                           (event["event_id"], fingerprint))
            if cursor.fetchone() is None:
                cursor.execute("SELECT payload_hash FROM inbox WHERE event_id=%s", (event["event_id"],))
                if cursor.fetchone()[0] != fingerprint:
                    raise ValueError("Reused event ID with different content")
                return False
            cursor.execute("""INSERT INTO discovery_jobs
                (job_id,goal_id,goal_version,correlation_id,next_attempt_at,deadline_at)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (goal_id,goal_version) DO NOTHING""",
                (str(job_id), payload["goal_id"], payload["goal_version"], event["correlation_id"],
                 now, now + timedelta(hours=1)))
            return True

    def run_once(self, worker) -> str:
        with self.database.transaction() as cursor:
            now = self.clock()
            cursor.execute("""SELECT job_id,goal_id,goal_version,correlation_id,attempts,deadline_at
                FROM discovery_jobs WHERE status='pending' AND next_attempt_at <= %s
                ORDER BY next_attempt_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (now,))
            row = cursor.fetchone()
            if row is None:
                return "idle"
            job_id, goal_id, version, correlation, attempts, deadline = row
            retry_after = 0
            if deadline <= now:
                result, state = "DEADLINE_EXCEEDED", "dead"
            else:
                try:
                    result = worker.run(UUID(str(goal_id)), version, UUID(str(correlation)))
                    state = "done"
                except ProviderError as error:
                    retry_after = error.retry_after_seconds or 0
                    transient = error.code in ("NETWORK_ERROR", "RATE_LIMITED") or error.status in (408, 500, 502, 503, 504)
                    state = "pending" if transient and attempts + 1 < self.max_attempts else "dead"
                    result = error.code
                except ValueError:
                    state, result = "dead", "INVALID_GOAL_OR_PAYLOAD"
                except Exception:
                    state = "pending" if attempts + 1 < self.max_attempts else "dead"
                    result = "DEPENDENCY_FAILURE"
            delay = max(min(2 ** (attempts + 1), 300), retry_after)
            cursor.execute("""UPDATE discovery_jobs SET status=%s,attempts=attempts+1,
                last_result=%s,next_attempt_at=%s WHERE job_id=%s""",
                (state, result, now + timedelta(seconds=delay), str(job_id)))
            return state
