"""Run the durable Goal Management activation outbox publisher."""
from __future__ import annotations

import os
import time

from .events import publish_pending
from .rabbitmq import ConfirmingPublisher
from .repository import DatabaseRepository


def run() -> None:
    database_url = os.environ["GOAL_MANAGEMENT_DATABASE_URL"]
    broker_url = os.environ["RABBITMQ_URL"]
    store = DatabaseRepository(database_url)
    publisher = ConfirmingPublisher(broker_url)
    delay = 1
    try:
        while True:
            try:
                publish_pending(store, publisher)
                delay = 1
            except Exception:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            time.sleep(1)
    finally:
        publisher.close()


if __name__ == "__main__":
    run()
