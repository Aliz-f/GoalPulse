"""Outbox worker. Broker wiring is injectable; only a positive confirm marks sent."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Protocol

from .ledger import EvidenceLedger, utc
from .postgres import Connection


class ConfirmedBroker(Protocol):
    def publish(self, *, exchange: str, routing_key: str, body: bytes,
                message_id: str, correlation_id: str) -> bool:
        """Declare durable topic exchange; send persistent/mandatory and await confirm.

        Return True only for a positive broker confirmation and routable message.
        The platform adapter must never treat socket send success as a confirmation.
        """
        ...


class OutboxPublisher:
    """Claim one row with SKIP LOCKED, publish, and persist bounded retry state.

    A crash after confirm but before DB commit may redeliver the same event_id.
    Consumers must deduplicate it. The row lock is held only for the configured
    bounded broker call; deployments must set a broker timeout shorter than leases.
    """

    def __init__(self, connect: Callable[[], Connection], broker: ConfirmedBroker,
                 *, schema_directory: Path, clock=lambda: datetime.now(timezone.utc),
                 max_attempts: int = 5) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid publication retry bound")
        self.connect, self.broker, self.clock = connect, broker, clock
        self.max_attempts = max_attempts
        self.validator = EvidenceLedger._validator(schema_directory / "domain-event.v1.schema.json")

    def run_once(self) -> str:
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError("Outbox requires autocommit=False")
            with connection.cursor() as cursor:
                now = utc(self.clock())
                cursor.execute("""SELECT event_id, payload, attempts FROM outbox
                    WHERE status = 'pending' AND next_attempt_at <= %s
                    ORDER BY next_attempt_at, event_id FOR UPDATE SKIP LOCKED LIMIT 1""", (now,))
                row = cursor.fetchone()
                if row is None:
                    connection.rollback()
                    return "idle"
                event_id, event, attempts = row
                if (not self.validator.is_valid(event) or event.get("event_type") != "evidence.updated.v1"
                        or event.get("producer") != "data-evidence" or event.get("event_id") != str(event_id)):
                    cursor.execute("UPDATE outbox SET status='dead', last_error_code='INVALID_EVENT' WHERE event_id=%s",
                                   (str(event_id),))
                    connection.commit()
                    return "dead"
                try:
                    confirmed = self.broker.publish(
                        exchange="goalpulse.events", routing_key="evidence.updated.v1",
                        body=json.dumps(event, allow_nan=False).encode(), message_id=str(event_id),
                        correlation_id=event["correlation_id"])
                except Exception:
                    # Exception text may include credentials or scraped data.
                    confirmed = False
                if confirmed is True:
                    cursor.execute("""UPDATE outbox SET status='published', published_at=%s,
                        attempts=attempts+1, last_error_code=NULL WHERE event_id=%s""", (now, str(event_id)))
                    status = "published"
                else:
                    attempt = attempts + 1
                    status = "dead" if attempt >= self.max_attempts else "pending"
                    cursor.execute("""UPDATE outbox SET status=%s, attempts=%s,
                        next_attempt_at=%s, last_error_code='BROKER_UNCONFIRMED' WHERE event_id=%s""",
                        (status, attempt, now + timedelta(seconds=min(2 ** attempt, 300)), str(event_id)))
                connection.commit()
                return status
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class RabbitChannelBroker:
    """Adapter for an already authenticated Pika BlockingChannel from D1's platform.

    Supply pika.BasicProperties as properties_factory. No dependency is imported or
    installed here. Channel publisher-confirm mode raises on NACK/unroutable sends;
    successful BlockingChannel.basic_publish returns None in that mode.
    """

    def __init__(self, channel, properties_factory: Callable) -> None:
        self.channel, self.properties_factory = channel, properties_factory
        channel.exchange_declare(exchange="goalpulse.events", exchange_type="topic", durable=True)
        channel.confirm_delivery()

    def publish(self, *, exchange: str, routing_key: str, body: bytes,
                message_id: str, correlation_id: str) -> bool:
        if exchange != "goalpulse.events" or routing_key != "evidence.updated.v1":
            raise ValueError("Undeclared evidence publication")
        result = self.channel.basic_publish(exchange=exchange, routing_key=routing_key,
            body=body, mandatory=True, properties=self.properties_factory(
                delivery_mode=2, content_type="application/json", message_id=message_id,
                correlation_id=correlation_id))
        return result is None or result is True
