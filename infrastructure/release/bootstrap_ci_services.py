#!/usr/bin/env python3
"""Create fixed disposable CI databases and Rabbit exchanges without deleting data."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import pika
import psycopg
from psycopg import sql


DATABASES = ("goal_management_test", "discovery_monitoring_test", "data_evidence_test")


def _local_url(name: str, schemes: set[str]) -> str:
    value = os.environ.get(name, "")
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"{name} must target a loopback disposable service")
    return value


def main() -> None:
    postgres_url = _local_url("PHASE2_CI_POSTGRES_ADMIN_URL", {"postgres", "postgresql"})
    rabbit_url = _local_url("PHASE2_CI_RABBITMQ_URL", {"amqp", "amqps"})
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        if connection.info.dbname != "postgres":
            raise RuntimeError("PHASE2_CI_POSTGRES_ADMIN_URL must select the postgres maintenance DB")
        for database in DATABASES:
            exists = connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone()
            if not exists:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    parameters = pika.URLParameters(rabbit_url)
    parameters.socket_timeout = 5
    parameters.stack_timeout = 10
    with pika.BlockingConnection(parameters) as connection:
        channel = connection.channel()
        channel.exchange_declare("goalpulse.events", exchange_type="topic", durable=True)
        channel.exchange_declare("goalpulse.dlx", exchange_type="topic", durable=True)
    print("PASS disposable CI PostgreSQL databases and RabbitMQ exchanges are ready")


if __name__ == "__main__":
    main()
