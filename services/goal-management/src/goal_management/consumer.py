"""Manual-ack RabbitMQ consumer for frozen goal.evaluated.v1 events."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import pika

from .events import consume_evaluated
from .repository import DatabaseRepository


EVALUATED_QUEUE = "goalpulse.goal-management.goal.evaluated.v1"
MAX_PROCESSING_RETRIES = 3


def process_delivery(
    channel: Any,
    method: Any,
    properties: Any,
    body: bytes,
    store: Any,
    *,
    wait: Callable[[float], None] = time.sleep,
) -> None:
    """ACK only durable processing or a confirmed retry copy; reject permanent failures."""
    try:
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError("event body must be an object")
        consume_evaluated(store, event)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    except Exception:
        headers = dict(getattr(properties, "headers", None) or {})
        attempts = headers.get("x-goalpulse-processing-attempt", 0)
        if not isinstance(attempts, int) or attempts >= MAX_PROCESSING_RETRIES:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        wait(min(2**attempts, 8))
        headers["x-goalpulse-processing-attempt"] = attempts + 1
        confirmed = channel.basic_publish(
            exchange="",
            routing_key=EVALUATED_QUEUE,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                headers=headers,
            ),
            mandatory=True,
        )
        if not confirmed:
            raise RuntimeError("retry publish was not confirmed")
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    channel.basic_ack(delivery_tag=method.delivery_tag)


def run() -> None:
    database_url = os.environ["GOAL_MANAGEMENT_DATABASE_URL"]
    broker_url = os.environ["RABBITMQ_URL"]
    store = DatabaseRepository(database_url)
    delay = 1
    while True:
        broker = None
        try:
            broker = pika.BlockingConnection(pika.URLParameters(broker_url))
            channel = broker.channel()
            channel.confirm_delivery()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=EVALUATED_QUEUE,
                auto_ack=False,
                on_message_callback=lambda ch, method, properties, body: process_delivery(
                    ch, method, properties, body, store
                ),
            )
            delay = 1
            channel.start_consuming()
        except KeyboardInterrupt:
            return
        except (pika.AMQPError, OSError):
            time.sleep(delay)
            delay = min(delay * 2, 30)
        finally:
            if broker is not None and broker.is_open:
                broker.close()


if __name__ == "__main__":
    run()
