"""Run the durable Goal Management outbox publisher."""
from __future__ import annotations
import os
import time
import psycopg
from .events import publish_pending
from .rabbitmq import ConfirmingPublisher
from .repository import GoalRepository

def run() -> None:
    database_url=os.environ['GOAL_MANAGEMENT_DATABASE_URL']
    broker_url=os.environ['RABBITMQ_URL']
    with psycopg.connect(database_url,autocommit=True) as connection:
        store=GoalRepository(connection); publisher=ConfirmingPublisher(broker_url)
        while True:
            publish_pending(store,publisher)
            time.sleep(1)

if __name__ == '__main__':
    run()
