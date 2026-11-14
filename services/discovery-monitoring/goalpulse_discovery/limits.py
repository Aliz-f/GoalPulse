"""Durable direct-operation quotas and cooldowns; scheduled monitor cost is separate.

Usage counters explicitly distinguish confirmed credits, unknown usage and pending
reservations. A logical adapter operation may make bounded HTTP retries, so its
count is not presented as a count of individual network attempts or account usage.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
from threading import BoundedSemaphore
from uuid import UUID, uuid4

from .context_provider import ProviderError, ScrapeResult
from .persistence import Database


class UsageStore:
    def __init__(self, database: Database, *, max_operations=100, max_credits=1000, max_user_operations=100) -> None:
        if type(max_operations) is not int or max_operations < 1 or type(max_credits) is not int or max_credits < 1:
            raise ValueError("Invalid daily budget")
        self.database, self.max_operations, self.max_credits = database, max_operations, max_credits
        if type(max_user_operations) is not int or not 1 <= max_user_operations <= 100:
            raise ValueError("Invalid per-user call budget")
        self.max_user_operations = max_user_operations

    def reserve(self, resource: str, now: datetime, *, ttl_seconds: int, estimated_credits: int = 3,
                owner_user_id: UUID | None = None):
        if now.utcoffset() is None or type(ttl_seconds) is not int or ttl_seconds < 1 or estimated_credits < 1:
            raise ValueError("Invalid quota reservation")
        now = now.astimezone(timezone.utc)
        day, identifier = now.date(), uuid4()
        if owner_user_id is not None and not isinstance(owner_user_id, UUID):
            raise ValueError("Authenticated budget owner must be UUID")
        with self.database.transaction() as cursor:
            cursor.execute("INSERT INTO provider_usage_day(day) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
            cursor.execute("SELECT operations,confirmed_credits,reserved_credits FROM provider_usage_day WHERE day=%s FOR UPDATE", (day,))
            operations, confirmed, reserved = cursor.fetchone()
            if operations >= self.max_operations or confirmed + reserved + estimated_credits > self.max_credits:
                reset = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                raise ProviderError("RATE_LIMITED", retry_after_seconds=(reset - now).total_seconds())
            if owner_user_id is not None:
                cursor.execute("INSERT INTO provider_user_day(owner_user_id,day) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                               (str(owner_user_id), day))
                cursor.execute("SELECT operations FROM provider_user_day WHERE owner_user_id=%s AND day=%s FOR UPDATE",
                               (str(owner_user_id), day))
                if cursor.fetchone()[0] >= self.max_user_operations:
                    raise ProviderError("USER_DAILY_CALL_LIMIT")
            cursor.execute("""INSERT INTO provider_resource_state(resource_key,next_allowed_at)
                VALUES (%s,%s) ON CONFLICT DO NOTHING""", (resource, now))
            cursor.execute("SELECT next_allowed_at FROM provider_resource_state WHERE resource_key=%s FOR UPDATE", (resource,))
            next_allowed = cursor.fetchone()[0]
            if next_allowed > now:
                raise ProviderError("RATE_LIMITED", retry_after_seconds=(next_allowed - now).total_seconds())
            if owner_user_id is not None:
                cursor.execute("UPDATE provider_user_day SET operations=operations+1 WHERE owner_user_id=%s AND day=%s",
                               (str(owner_user_id), day))
            cursor.execute("""UPDATE provider_usage_day SET operations=operations+1,
                reserved_credits=reserved_credits+%s,pending_operations=pending_operations+1 WHERE day=%s""",
                (estimated_credits, day))
            cursor.execute("""INSERT INTO provider_operations
                (operation_id,day,resource_key,estimated_credits,started_at,owner_user_id) VALUES (%s,%s,%s,%s,%s,%s)""",
                (str(identifier), day, resource, estimated_credits, now, str(owner_user_id) if owner_user_id else None))
            cursor.execute("""UPDATE provider_resource_state SET next_allowed_at=%s,last_error_code=NULL
                WHERE resource_key=%s""", (now + timedelta(seconds=ttl_seconds), resource))
        return identifier

    def finish(self, identifier, *, credits: int | None, error_code: str | None,
               now: datetime, retry_after: float = 0) -> None:
        if credits is not None and (type(credits) is not int or credits < 0):
            raise ValueError("Invalid reported credits")
        if (now.utcoffset() is None or not isinstance(retry_after, (int, float))
                or isinstance(retry_after, bool) or not math.isfinite(retry_after)
                or retry_after < 0):
            raise ValueError("Invalid completion timing")
        retry_after = min(float(retry_after), 86_400)
        with self.database.transaction() as cursor:
            cursor.execute("SELECT day FROM provider_operations WHERE operation_id=%s", (str(identifier),))
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Unknown operation")
            day = row[0]
            # All budget mutations lock the daily row before the resource/operation.
            cursor.execute("SELECT day FROM provider_usage_day WHERE day=%s FOR UPDATE", (day,))
            cursor.execute("SELECT resource_key,estimated_credits,completed_at FROM provider_operations WHERE operation_id=%s FOR UPDATE", (str(identifier),))
            resource, estimate, completed = cursor.fetchone()
            if completed is not None:
                return
            cursor.execute("""UPDATE provider_usage_day SET reserved_credits=reserved_credits-%s,
                pending_operations=pending_operations-1,confirmed_credits=confirmed_credits+%s,
                unknown_usage_operations=unknown_usage_operations+%s WHERE day=%s""",
                # Keep uncertain cost reserved for the day until a reviewed usage
                # reconciliation can replace it. Unknown usage never frees budget.
                (estimate if credits is not None else 0, credits if credits is not None else 0,
                 1 if credits is None else 0, day))
            cursor.execute("""UPDATE provider_operations SET completed_at=%s,confirmed_credits=%s,
                error_code=%s WHERE operation_id=%s""", (now, credits, error_code, str(identifier)))
            cursor.execute("""UPDATE provider_resource_state SET last_error_code=%s,
                next_allowed_at=GREATEST(next_allowed_at,%s) WHERE resource_key=%s""",
                (error_code, now + timedelta(seconds=retry_after), resource))

    def health(self, day) -> dict:
        with self.database.transaction() as cursor:
            cursor.execute("""SELECT operations,confirmed_credits,reserved_credits,pending_operations,
                unknown_usage_operations FROM provider_usage_day WHERE day=%s""", (day,))
            row = cursor.fetchone() or (0, 0, 0, 0, 0)
            return dict(zip(("direct_operations", "confirmed_credits", "reserved_credits",
                             "pending_operations", "unknown_usage_operations"), row),
                        scheduled_monitor_usage="unknown")


class GuardedProvider:
    """Wrap a provider once in a process; DB quotas also coordinate other replicas."""

    def __init__(self, provider, usage, *, clock=lambda: datetime.now(timezone.utc),
                 max_concurrent: int = 2, source_ttl_seconds: int = 600) -> None:
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= 5 or source_ttl_seconds < 1:
            raise ValueError("Invalid provider concurrency/TTL")
        self.provider, self.usage, self.clock = provider, usage, clock
        self.semaphore, self.ttl = BoundedSemaphore(max_concurrent), source_ttl_seconds

    def call(self, operation: str, resource: str, *args, owner_user_id=None, **kwargs):
        if not self.semaphore.acquire(blocking=False):
            raise ProviderError("RATE_LIMITED", retry_after_seconds=1)
        reservation = None
        try:
            key = sha256(f"{operation}:{resource}".encode()).hexdigest()
            scope = {"owner_user_id": owner_user_id} if owner_user_id is not None else {}
            reservation = self.usage.reserve(key, self.clock(), ttl_seconds=self.ttl, **scope)
            try:
                result = getattr(self.provider, operation)(*args, **kwargs)
            except Exception as error:
                self.usage.finish(reservation, credits=None, error_code="PROVIDER_FAILURE",
                    now=self.clock(), retry_after=(error.retry_after_seconds or 0) if isinstance(error, ProviderError) else 0)
                raise
            credits = result.credits_consumed if isinstance(result, ScrapeResult) else None
            if isinstance(result, dict):
                metadata = result.get("key_metadata")
                value = metadata.get("credits_consumed") if isinstance(metadata, dict) else None
                credits = value if type(value) is int and value >= 0 else None
            # A final response's usage does not account for earlier failed HTTP
            # attempts. Preserve that operation as unknown instead of understating.
            if getattr(result, "request_attempts", 1) > 1:
                credits = None
            self.usage.finish(reservation, credits=credits, error_code=None, now=self.clock())
            return result
        finally:
            self.semaphore.release()

    def search(self, query: str, *, limit=5):
        return self.call("search", query, query, limit=limit)

    def scrape(self, url: str):
        return self.call("scrape", url, url)

    def create_monitor(self, *, name: str, url: str, **kwargs):
        return self.call("create_monitor", url, name=name, url=url, **kwargs)

    def fetch_run(self, monitor_id: str, run_id: str):
        return self.call("fetch_run", f"{monitor_id}:{run_id}", monitor_id, run_id)

    def set_monitor_status(self, monitor_id: str, status: str):
        return self.call("set_monitor_status", f"{monitor_id}:{status}", monitor_id, status)

    def for_owner(self, owner_user_id: UUID, *, before_request):
        return OwnerProvider(self, owner_user_id, before_request)


class OwnerProvider:
    """Share concurrency/credit guards; charge the server-derived owner per call."""
    def __init__(self, provider, owner_user_id, before_request):
        if not isinstance(owner_user_id, UUID):
            raise ValueError("Authenticated owner required")
        self.provider, self.owner_user_id, self.before_request = provider, owner_user_id, before_request

    def search(self, query, *, limit=3):
        self.before_request()
        return self.provider.call("search", query, query, limit=limit, owner_user_id=self.owner_user_id)

    def scrape(self, url):
        self.before_request()
        return self.provider.call("scrape", url, url, owner_user_id=self.owner_user_id)
