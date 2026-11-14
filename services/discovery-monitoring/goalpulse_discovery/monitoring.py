"""Shared-monitor reservation and reconciliation; secret storage is an injected port."""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from .persistence import Database
from .registry import Source


class SecretStore(Protocol):
    def put(self, secret: str) -> str:
        """Persist securely server-side and return a reference, never the secret."""
        ...
    def get(self, reference: str) -> str: ...


@dataclass(frozen=True)
class Monitor:
    monitor_id: UUID
    source_id: UUID
    provider_id: str | None
    baseline_run_id: str | None
    secret_ref: str | None = field(repr=False)
    state: str = "creating"


class MonitorStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def reserve(self, source: Source, policy_fingerprint: str) -> tuple[UUID, bool]:
        identifier = uuid4()
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO monitors (monitor_id,source_id,policy_fingerprint,state)
                VALUES (%s,%s,%s,'creating') ON CONFLICT (source_id,policy_fingerprint) DO NOTHING
                RETURNING monitor_id""", (str(identifier), str(source.source_id), policy_fingerprint))
            row = cursor.fetchone()
            if row:
                return UUID(str(row[0])), True
            cursor.execute("SELECT monitor_id FROM monitors WHERE source_id=%s AND policy_fingerprint=%s",
                           (str(source.source_id), policy_fingerprint))
            return UUID(str(cursor.fetchone()[0])), False

    def created(self, identifier: UUID, provider_id: str, baseline_run_id: str | None, secret_ref: str | None) -> None:
        with self.database.transaction() as cursor:
            cursor.execute("""UPDATE monitors SET provider_monitor_id=%s,baseline_run_id=%s,
                secret_ref=%s,state='active',last_error_code=NULL WHERE monitor_id=%s""",
                (provider_id, baseline_run_id, secret_ref, str(identifier)))

    def state(self, identifier: UUID, state: str, error: str | None = None) -> None:
        if state not in ("active", "paused", "uncertain"):
            raise ValueError("Invalid monitor state")
        with self.database.transaction() as cursor:
            cursor.execute("UPDATE monitors SET state=%s,last_error_code=%s WHERE monitor_id=%s",
                           (state, error, str(identifier)))

    def page(self, *, after: UUID | None = None, limit: int = 100) -> list[Monitor]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid monitor page size")
        with self.database.transaction() as cursor:
            cursor.execute("""SELECT monitor_id,source_id,provider_monitor_id,baseline_run_id,secret_ref,state
                FROM monitors WHERE (%s::uuid IS NULL OR monitor_id > %s::uuid)
                ORDER BY monitor_id LIMIT %s""", (str(after) if after else None, str(after) if after else None, limit))
            return [Monitor(UUID(str(r[0])), UUID(str(r[1])), *r[2:]) for r in cursor.fetchall()]

    def baseline(self, monitor: Monitor, run: dict) -> None:
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO monitor_runs (provider_run_id,monitor_id,status,baseline_created)
                VALUES (%s,%s,%s,%s) ON CONFLICT (provider_run_id) DO UPDATE
                SET status=EXCLUDED.status,baseline_created=EXCLUDED.baseline_created""",
                (monitor.baseline_run_id, str(monitor.monitor_id), run.get("status", "unknown"),
                 run.get("baseline_created") is True))


class MonitorManager:
    def __init__(self, store, provider, secrets: SecretStore) -> None:
        self.store, self.provider, self.secrets = store, provider, secrets

    def ensure(self, source: Source, *, instructions: str = "Report product price or availability changes",
               frequency_minutes: int = 1440, webhook_url: str | None = None) -> UUID:
        policy = sha256(f"{frequency_minutes}:{instructions}:{webhook_url}".encode()).hexdigest()
        identifier, created = self.store.reserve(source, policy)
        if not created:
            # 'creating'/'uncertain' entries require reconciliation, never blind retries.
            return identifier
        result = None
        try:
            result = self.provider.create_monitor(name=f"GoalPulse source {source.source_id}",
                url=source.canonical_url, instructions=instructions, webhook_url=webhook_url,
                frequency_minutes=frequency_minutes)
            if webhook_url and not result.webhook_secret:
                raise ValueError("Missing configured webhook secret")
            secret_ref = self.secrets.put(result.webhook_secret) if result.webhook_secret else None
            self.store.created(identifier, result.monitor_id, result.initial_run_id, secret_ref)
        except Exception:
            self.store.state(identifier, "uncertain", "CREATION_REQUIRES_RECONCILIATION")
            if result is not None:
                # Best effort limit an orphan's ongoing provider cost. Its ID is
                # recoverable through provider reconciliation, never recreate here.
                try:
                    self.provider.set_monitor_status(result.monitor_id, "paused")
                except Exception:
                    pass
            raise
        return identifier

    def record_baseline(self, monitor: Monitor) -> bool:
        if not monitor.provider_id or not monitor.baseline_run_id:
            return False
        run = self.provider.fetch_run(monitor.provider_id, monitor.baseline_run_id)
        if run.get("id") != monitor.baseline_run_id or run.get("monitor_id") != monitor.provider_id:
            raise ValueError("Provider run identity mismatch")
        self.store.baseline(monitor, run)
        # This method never submits extraction or emits change/evidence events.
        return run.get("status") == "completed" and run.get("baseline_created") is True


class SubscriptionReconciler:
    def __init__(self, goals, registry, store, provider) -> None:
        self.goals, self.registry, self.store, self.provider = goals, registry, store, provider

    def reconcile_goal(self, goal_id: UUID) -> None:
        runtime = self.goals.runtime(goal_id)
        if runtime.goal_id != goal_id:
            raise ValueError("Runtime identity mismatch")
        self.registry.reconcile(goal_id, current_version=runtime.current_version, lifecycle=runtime.lifecycle)

    def reconcile_monitor(self, monitor: Monitor) -> str:
        if monitor.state not in ("active", "paused") or not monitor.provider_id:
            return monitor.state
        # Refresh every linked goal, including inactive links so a resume can
        # restore subscriptions. Any runtime failure leaves provider state alone.
        for goal_id in self.registry.goals_for_source(monitor.source_id):
            self.reconcile_goal(goal_id)
        desired = "active" if self.registry.subscriptions(monitor.source_id) else "paused"
        if desired != monitor.state:
            self.provider.set_monitor_status(monitor.provider_id, desired)
            self.store.state(monitor.monitor_id, desired)
        return desired
