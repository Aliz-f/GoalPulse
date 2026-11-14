"""Pika-compatible durable delivery and confirmed-publication adapters."""

import json
import math
import signal
import threading
from typing import Callable


class PublishDeadlineExceeded(TimeoutError):
    """The channel's confirmation state is indeterminate; retire its runtime."""


class ConfirmedPublisher:
    def __init__(self, channel, properties_factory=None, timeout_seconds: float = 5):
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError('Publisher deadline must be positive and at most 30 seconds')
        if properties_factory is None:
            from pika import BasicProperties
            properties_factory = BasicProperties
        self.channel, self.properties_factory = channel, properties_factory
        self.timeout_seconds, self.usable = timeout_seconds, True
        channel.exchange_declare(exchange='goalpulse.events', exchange_type='topic', durable=True)
        channel.confirm_delivery()

    def publish_confirmed(self, exchange: str, routing_key: str, body: bytes, persistent: bool) -> bool:
        if (exchange != 'goalpulse.events' or routing_key not in {
                'goal.evaluated.v1', 'goal.opportunity_verified.v1'} or persistent is not True):
            raise ValueError('Unsupported publication')
        if not self.usable:
            raise ConnectionError('Retired publisher requires a new connection')
        # The approved Docker/Linux worker runs on its process main thread.
        # A Pika socket/block timeout alone does not bound a missing ACK while
        # heartbeats continue. Interrupt the actual synchronous confirmation.
        if (threading.current_thread() is not threading.main_thread()
                or not hasattr(signal, 'setitimer')):
            raise RuntimeError('Confirmed publishing requires a POSIX main-thread worker')
        if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
            raise RuntimeError('Publisher cannot replace an existing process alarm')
        previous = signal.getsignal(signal.SIGALRM)
        def expired(signum, frame):
            self.usable = False
            raise PublishDeadlineExceeded('Publisher confirmation deadline exceeded')
        signal.signal(signal.SIGALRM, expired)
        try:
            signal.setitimer(signal.ITIMER_REAL, self.timeout_seconds)
            result = self.channel.basic_publish(exchange=exchange, routing_key=routing_key,
                body=body, mandatory=True,
                properties=self.properties_factory(content_type='application/json', delivery_mode=2))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        # Pika BlockingChannel with confirm_delivery raises on Basic.Return or
        # broker Nack; successful basic_publish normally returns None.
        return result is not False


class EvaluationWorker:
    def __init__(self, connection, coordinator, publisher, *, queue: str,
                 dead_letter_exchange: str, dead_letter_routing_key: str,
                 ready: Callable[[], bool], max_body_bytes: int = 2_000_000):
        if (not isinstance(queue, str) or not queue.strip()
                or not isinstance(dead_letter_exchange, str)
                or not isinstance(dead_letter_routing_key, str) or not dead_letter_routing_key.strip()):
            raise ValueError('Explicit consumer queue/dead-letter configuration required')
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 10_000_000:
            raise ValueError('Invalid event size bound')
        self.connection, self.coordinator, self.publisher = connection, coordinator, publisher
        self.ready, self.queue, self.max_body_bytes = ready, queue, max_body_bytes
        self.channel = connection.channel()
        self.channel.exchange_declare(exchange='goalpulse.events', exchange_type='topic', durable=True)
        if dead_letter_exchange:
            self.channel.exchange_declare(exchange=dead_letter_exchange, passive=True)
        self.channel.queue_declare(queue=queue, durable=True, arguments={
            'x-dead-letter-exchange': dead_letter_exchange,
            'x-dead-letter-routing-key': dead_letter_routing_key,
        })
        self.channel.queue_bind(queue=queue, exchange='goalpulse.events', routing_key='evidence.updated.v1')
        self.channel.basic_qos(prefetch_count=1)

    def run_once(self) -> bool:
        if self.ready() is not True:
            raise RuntimeError('Evaluation dependencies not ready')
        method, properties, body = self.channel.basic_get(queue=self.queue, auto_ack=False)
        worked = method is not None
        if method is not None:
            if (method.exchange != 'goalpulse.events' or method.routing_key != 'evidence.updated.v1'
                    or len(body) > self.max_body_bytes):
                event = {}
            else:
                try:
                    event = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    event = {}
            decision = self.coordinator.run(event)
            if decision.action == 'ack':
                self.channel.basic_ack(delivery_tag=method.delivery_tag)
            elif decision.action == 'dead_letter':
                self.channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            elif decision.action == 'retry':
                if not math.isfinite(decision.delay_seconds) or not 0 < decision.delay_seconds <= 300:
                    raise ValueError('Invalid retry delay')
                self.connection.sleep(decision.delay_seconds)
                self.channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            else:
                raise ValueError('Invalid processing decision')
        # Outbox publication has its own transaction/retry budget. It also runs
        # while the input queue is empty, so a restart can drain committed intent.
        return self.publisher.run_once() or worked

    def run(self, stop: Callable[[], bool]) -> None:
        try:
            while not stop():
                if not self.run_once():
                    self.connection.sleep(0.5)
        finally:
            self.connection.close()
