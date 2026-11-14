"""Follow-up scrape worker; a webhook is a trigger, never an Observation."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .context_provider import ProviderError
from .persistence import Database


class SourceCheck:
    def __init__(self, goals, registry, provider, evidence) -> None:
        self.goals, self.registry, self.provider, self.evidence = goals, registry, provider, evidence

    def active(self, source_id: UUID) -> bool:
        for goal_id in self.registry.goals_for_source(source_id):
            runtime = self.goals.runtime(goal_id)
            if runtime.goal_id != goal_id:
                raise ValueError("Runtime identity mismatch")
            self.registry.reconcile(goal_id, current_version=runtime.current_version, lifecycle=runtime.lifecycle)
        return bool(self.registry.subscriptions(source_id))

    def run(self, source_id: UUID, correlation_id: UUID) -> str:
        if not self.active(source_id):
            return "inactive"
        source = self.registry.get(source_id)
        if source is None:
            raise ValueError("Registered source missing")
        result = self.provider.scrape(source.canonical_url)
        if not self.active(source_id):
            return "inactive"
        self.evidence.submit(source, result, attempt_id=uuid4(), correlation_id=correlation_id)
        return "extracted" if result.product.success else "no_evidence"


class SourceCheckJobs:
    def __init__(self, database: Database, *, clock=lambda: datetime.now(timezone.utc), max_attempts=5) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid job retry bound")
        self.database, self.clock, self.max_attempts = database, clock, max_attempts

    def run_once(self, worker) -> str:
        with self.database.transaction() as cursor:
            now = self.clock()
            cursor.execute("""SELECT j.job_id,m.source_id,j.correlation_id,j.attempts,j.deadline_at
                FROM source_check_jobs j JOIN monitors m ON m.monitor_id=j.monitor_id
                WHERE j.status='pending' AND j.next_attempt_at <= %s
                ORDER BY j.next_attempt_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED""", (now,))
            row = cursor.fetchone()
            if row is None:
                return "idle"
            job_id, source_id, correlation, attempts, deadline = row
            delay = min(2 ** (attempts + 1), 300)
            if deadline <= now:
                state, result = "dead", "DEADLINE_EXCEEDED"
            else:
                try:
                    result = worker.run(UUID(str(source_id)), UUID(str(correlation)))
                    state = "done"
                except ProviderError as error:
                    retryable = error.code in ("NETWORK_ERROR", "RATE_LIMITED") or error.status in (408, 500, 502, 503, 504)
                    state = "pending" if retryable and attempts + 1 < self.max_attempts else "dead"
                    result, delay = error.code, max(delay, error.retry_after_seconds or 0)
                except ValueError:
                    state, result = "dead", "INVALID_SOURCE_OR_PAYLOAD"
                except Exception:
                    state = "pending" if attempts + 1 < self.max_attempts else "dead"
                    result = "DEPENDENCY_FAILURE"
            cursor.execute("""UPDATE source_check_jobs SET status=%s,attempts=attempts+1,
                last_result=%s,next_attempt_at=%s WHERE job_id=%s""",
                (state, result, now + timedelta(seconds=delay), str(job_id)))
            return state
