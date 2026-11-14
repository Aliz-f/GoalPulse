"""Manual-ACK Rabbit delivery adapter with durable retry-journal decisions.

Use D1-supplied private queue and dead-letter settings. Queue declaration asserts
durability and DLX arguments (incompatible existing queues fail startup). The
adapter publishes no alternate event names. A retry remains unacked
while a heartbeat-aware connection sleep runs, then is requeued. A process or
connection failure returns outstanding deliveries to RabbitMQ for recovery.
"""

import json
import math
from typing import Callable

from .retry import RetryCoordinator


class NotificationWorker:
    def __init__(self, connection, coordinator: RetryCoordinator, *, queue: str,
                 dead_letter_exchange: str, dead_letter_routing_key: str,
                 max_body_bytes: int = 2_000_000):
        if not isinstance(queue, str) or not queue.strip():
            raise ValueError('Configured notification queue required')
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 10_000_000:
            raise ValueError('Invalid message size bound')
        if (not isinstance(dead_letter_exchange, str)
                or not isinstance(dead_letter_routing_key, str) or not dead_letter_routing_key.strip()):
            raise ValueError('Explicit dead-letter configuration required')
        self.connection, self.coordinator, self.queue = connection, coordinator, queue
        self.max_body_bytes = max_body_bytes
        self.channel = connection.channel()
        self.channel.exchange_declare(exchange='goalpulse.events', exchange_type='topic', durable=True)
        if dead_letter_exchange:
            self.channel.exchange_declare(exchange=dead_letter_exchange, passive=True)
        self.channel.queue_declare(queue=queue, durable=True, arguments={
            'x-dead-letter-exchange': dead_letter_exchange,
            'x-dead-letter-routing-key': dead_letter_routing_key,
        })
        self.channel.queue_bind(queue=queue, exchange='goalpulse.events',
                                routing_key='goal.opportunity_verified.v1')
        self.channel.basic_qos(prefetch_count=1)

    def run_once(self) -> bool:
        """Process at most one delivery. Exceptions must close the connection.

        The owning runner must close/reconnect on exception, with bounded
        reconnect backoff. No ACK is sent on journal/commit/transport failure.
        """
        method, properties, body = self.channel.basic_get(queue=self.queue, auto_ack=False)
        if method is None:
            return False
        # Do not decode/log an unbounded or wrongly routed payload.
        if (method.exchange != 'goalpulse.events'
                or method.routing_key != 'goal.opportunity_verified.v1'
                or len(body) > self.max_body_bytes):
            event = {}
        else:
            try:
                event = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                event = {}
        decision = self.coordinator.run(event)
        if decision.action == 'ack':
            # RetryCoordinator returns only after both business and journal
            # commits (or a safely durable duplicate/suppression decision).
            self.channel.basic_ack(delivery_tag=method.delivery_tag)
        elif decision.action == 'dead_letter':
            self.channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        elif decision.action == 'retry':
            if not math.isfinite(decision.delay_seconds) or not 0 < decision.delay_seconds <= 300:
                raise ValueError('Invalid durable retry delay')
            self.connection.sleep(decision.delay_seconds)
            self.channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        else:
            raise ValueError('Invalid durable processing decision')
        return True

    def run(self, stop: Callable[[], bool]) -> None:
        """Run until requested shutdown; always release the owned connection."""
        try:
            while not stop():
                if not self.run_once():
                    self.connection.sleep(0.5)
        finally:
            self.connection.close()
