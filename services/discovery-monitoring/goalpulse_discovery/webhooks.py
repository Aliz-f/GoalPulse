"""Signed provider receiver core; HTTP routing is intentionally not exposed here."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from .persistence import Database


class WebhookError(ValueError):
    def __init__(self, code: str, status: int) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ReceiverConfig:
    """Loaded from trusted receiver configuration, never selected by body claims."""

    monitor_id: UUID
    provider_monitor_id: str
    secret_ref: str = field(repr=False)
    baseline_run_id: str | None = None


def verify_signature(raw: bytes, header: str, secret: str, *, now: datetime, tolerance=300) -> bool:
    try:
        pairs = [part.strip().split("=") for part in header.split(",")]
        if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
            return False
        parts = dict(pairs)
        if (set(parts) != {"t", "v1"} or not re.fullmatch(r"\d{1,12}", parts["t"])
                or not re.fullmatch(r"[a-fA-F0-9]{64}", parts["v1"]) or not secret
                or now.utcoffset() is None or abs(now.timestamp() - int(parts["t"])) > tolerance):
            return False
        expected = hmac.new(secret.encode(), parts["t"].encode() + b"." + raw, sha256).hexdigest()
        return hmac.compare_digest(expected, parts["v1"].lower())
    except (ValueError, KeyError, TypeError):
        return False


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def identifier(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is not None


class WebhookReceiver:
    def __init__(self, store, secrets, *, clock=lambda: datetime.now(timezone.utc), max_body_bytes=262144) -> None:
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 1_000_000:
            raise ValueError("Invalid webhook body limit")
        self.store, self.secrets, self.clock, self.max_body_bytes = store, secrets, clock, max_body_bytes

    def receive(self, config: ReceiverConfig, raw: bytes, headers: Mapping[str, str]) -> tuple[bool, bool]:
        """Return (new event, change job requested) only after durable acceptance."""
        if not isinstance(raw, bytes) or len(raw) > self.max_body_bytes:
            raise WebhookError("BODY_TOO_LARGE", 413)
        lowered = {key.lower(): value for key, value in headers.items()}
        secret = self.secrets.get(config.secret_ref)
        now = self.clock()
        if not verify_signature(raw, lowered.get("x-context-signature", ""), secret, now=now):
            raise WebhookError("INVALID_SIGNATURE", 401)
        try:
            event = json.loads(raw, object_pairs_hook=unique_pairs)
            if not isinstance(event, dict) or not identifier(event.get("id")):
                raise ValueError
            if (event["id"] != lowered.get("x-context-id")
                    or event.get("event") != lowered.get("x-context-event")
                    or event.get("event") not in ("change.detected", "run.completed")):
                raise ValueError
            created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            if created.utcoffset() is None:
                raise ValueError
            data = event["data"]
            if event["event"] == "change.detected":
                change = data["change"]
                monitor, run_id = change["monitor_id"], change["run_id"]
                changed = run_id != config.baseline_run_id
            else:
                run = data["run"]
                monitor, run_id = run["monitor_id"], run["id"]
                changed = (run.get("status") == "completed" and run.get("change_detected") is True
                           and run.get("baseline_created") is not True and run_id != config.baseline_run_id)
                change = data.get("change")
                if change is not None and (change["monitor_id"] != monitor or change["run_id"] != run_id):
                    raise ValueError
            if monitor != config.provider_monitor_id or not identifier(run_id):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise WebhookError("INVALID_PROVIDER_EVENT", 400) from None
        # Persist only IDs + hash. Webhook excerpts and target URLs cannot request
        # fetches. Workers resolve the registered source and recheck subscriptions.
        accepted = self.store.accept(config, event["id"], run_id, sha256(raw).hexdigest(),
                                     changed=changed, received_at=now)
        return accepted, changed


class WebhookInbox:
    def __init__(self, database: Database) -> None:
        self.database = database

    def accept(self, config: ReceiverConfig, event_id: str, run_id: str, payload_hash: str,
               *, changed: bool, received_at: datetime) -> bool:
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO webhook_inbox
                (provider_event_id,monitor_id,provider_run_id,payload_hash,received_at)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING provider_event_id""",
                (event_id, str(config.monitor_id), run_id, payload_hash, received_at))
            if cursor.fetchone() is None:
                cursor.execute("SELECT monitor_id,payload_hash FROM webhook_inbox WHERE provider_event_id=%s", (event_id,))
                existing = cursor.fetchone()
                if str(existing[0]) != str(config.monitor_id) or existing[1] != payload_hash:
                    raise WebhookError("EVENT_ID_CONFLICT", 409)
                return False
            if changed:
                cursor.execute("""INSERT INTO source_check_jobs
                    (job_id,monitor_id,provider_run_id,correlation_id,next_attempt_at,deadline_at)
                    VALUES (%s,%s,%s,%s,%s,%s + interval '1 hour')
                    ON CONFLICT (monitor_id,provider_run_id) DO NOTHING""",
                    (str(uuid5(NAMESPACE_URL, f"context:run:{config.monitor_id}:{run_id}")),
                     str(config.monitor_id), run_id,
                     str(uuid5(NAMESPACE_URL, f"context:event:{event_id}")), received_at, received_at))
            return True
