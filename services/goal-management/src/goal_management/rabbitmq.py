"""RabbitMQ publisher with persistent messages and synchronous confirms."""
from __future__ import annotations

import json
from typing import Any, Callable

import pika


class ConfirmingPublisher:
    """Reconnect on the next call after any broker/channel failure."""

    def __init__(
        self,
        url: str,
        *,
        connection_factory: Callable[[Any], Any] = pika.BlockingConnection,
    ) -> None:
        self._parameters = pika.URLParameters(url)
        self._connection_factory = connection_factory
        self._connection = None
        self._channel = None

    def _connect(self) -> None:
        if self._connection is not None and self._connection.is_open and self._channel.is_open:
            return
        self.close()
        self._connection = self._connection_factory(self._parameters)
        self._channel = self._connection.channel()
        self._channel.confirm_delivery()

    def publish_confirmed(self, routing_key: str, body: dict[str, Any]) -> None:
        try:
            self._connect()
            confirmed = self._channel.basic_publish(
                exchange="goalpulse.events",
                routing_key=routing_key,
                body=json.dumps(body, separators=(",", ":")),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                    message_id=body["event_id"],
                    correlation_id=body["correlation_id"],
                    type=body["event_type"],
                ),
                mandatory=True,
            )
            if not confirmed:
                raise RuntimeError("publisher confirm failed")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and getattr(connection, "is_open", False):
            connection.close()
