"""Manual-ACK activation ingress using D1's existing durable queue and DLQ."""

import json
import time

import pika
from jsonschema import ValidationError

from .context_provider import ProviderError, reject_json_constant

ACTIVATED_QUEUE = "goalpulse.discovery-monitoring.goal.activated.v1"
ATTEMPT_HEADER = "x-goalpulse-processing-attempt"


class ActivationConsumer:
    def __init__(self, inbox, *, properties_factory=pika.BasicProperties, wait=time.sleep, max_retries=3):
        if type(max_retries) is not int or not 0 <= max_retries <= 5:
            raise ValueError("Invalid activation retry bound")
        self.inbox, self.properties_factory, self.wait, self.max_retries = inbox, properties_factory, wait, max_retries

    def on_message(self, channel, method, properties, body: bytes) -> None:
        try:
            if not isinstance(body, bytes) or len(body) > 65_536:
                raise ValueError("Invalid activation size")
            event = json.loads(body, parse_constant=reject_json_constant)
            if not isinstance(event, dict):
                raise ValueError("Invalid activation")
            self.inbox.accept(event)
        except (ValueError, UnicodeError, ValidationError, RecursionError):
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        except Exception:
            headers = dict(getattr(properties, "headers", None) or {})
            attempts = headers.get(ATTEMPT_HEADER, 0)
            if type(attempts) is not int or not 0 <= attempts < self.max_retries:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            self.wait(min(2 ** attempts, 8))
            headers[ATTEMPT_HEADER] = attempts + 1
            try:
                confirmed = channel.basic_publish(
                    exchange="", routing_key=ACTIVATED_QUEUE, body=body, mandatory=True,
                    properties=self.properties_factory(
                        content_type="application/json", delivery_mode=2, headers=headers,
                        message_id=getattr(properties, "message_id", None),
                        correlation_id=getattr(properties, "correlation_id", None)))
                if confirmed is not None and confirmed is not True:
                    raise ProviderError("BROKER_UNCONFIRMED")
            except Exception:
                # Closing the broker connection requeues the still-unacked
                # original if a confirmed retry copy could not be established.
                raise ProviderError("BROKER_UNCONFIRMED") from None
        channel.basic_ack(delivery_tag=method.delivery_tag)

    def configure(self, channel, *, consume: bool = True) -> None:
        # Topology belongs to D1; passive checks avoid creating incompatible queues.
        channel.exchange_declare(exchange="goalpulse.events", exchange_type="topic", durable=True, passive=True)
        channel.queue_declare(queue=ACTIVATED_QUEUE, passive=True)
        channel.confirm_delivery()
        channel.basic_qos(prefetch_count=1)
        if consume:
            channel.basic_consume(queue=ACTIVATED_QUEUE, auto_ack=False, on_message_callback=self.on_message)
