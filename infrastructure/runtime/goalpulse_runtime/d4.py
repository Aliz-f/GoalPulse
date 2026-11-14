"""Runnable D4 API/worker composition owned by the D1 deployment boundary."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import Header, HTTPException
import pika
import psycopg
import uvicorn

from .common import (
    ConfigurationError,
    ServiceTokenIssuer,
    apply_sql_migrations,
    connection_factory,
    dependencies_ready,
    json_get,
    open_broker,
    owned_database_url,
    required,
    run_supervised,
    service_owner_subject,
    utc_now,
)


ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "contracts/schemas"
MIGRATIONS = {
    "goal-evaluation": ROOT / "services/goal-evaluation/migrations",
    "notification": ROOT / "services/notification/migrations",
    "agent-integration": ROOT / "services/agent-integration/migrations",
}
DATABASES = {
    "goal-evaluation": ("GOAL_EVALUATION_DATABASE_URL", "goal_evaluation", "ge_schema_migrations"),
    "notification": ("NOTIFICATION_DATABASE_URL", "notification", "ns_schema_migrations"),
    "agent-integration": ("AGENT_INTEGRATION_DATABASE_URL", "agent_integration", "ai_schema_migrations"),
}


def _database(service: str) -> tuple[str, Any]:
    variable, expected, _ledger = DATABASES[service]
    url = owned_database_url(variable, expected)
    return url, connection_factory(url)


def migrate(service: str) -> None:
    variable, expected, ledger = DATABASES[service]
    apply_sql_migrations(
        owned_database_url(variable, expected),
        MIGRATIONS[service],
        ledger,
    )


def _owner_dependency(scope: str, principal_type):
    def authorize(authorization: str | None = Header(default=None)):
        try:
            return principal_type(service_owner_subject(authorization, scope))
        except PermissionError:
            raise HTTPException(403, "authorized owner service identity required") from None

    return authorize


def _upstream_origins() -> tuple[str, str, str]:
    return (
        required("GOAL_MANAGEMENT_BASE_URL"),
        required("GOAL_EVALUATION_BASE_URL"),
        required("DATA_EVIDENCE_BASE_URL"),
    )


def _ge_runtime():
    from goal_evaluation.processing import Subscription
    from goal_evaluation.runtime import build_runtime

    _url, connect = _database("goal-evaluation")
    issuer = ServiceTokenIssuer()
    broker = open_broker()
    gm = required("GOAL_MANAGEMENT_BASE_URL")
    dm = required("DISCOVERY_MONITORING_BASE_URL")
    de = required("DATA_EVIDENCE_BASE_URL")

    def sources_for_goal(subscription: Subscription, correlation_id: str) -> list[str]:
        if os.environ.get("DM_GOAL_SOURCES_BINDING_APPROVED", "").lower() != "true":
            raise RuntimeError("complete source binding is not approved")
        path = (
            f"/internal/v1/goals/{quote(subscription.goal_id)}/versions/"
            f"{subscription.goal_version}/sources"
        )
        status, body = json_get(
            dm.rstrip("/") + path,
            issuer.mint("dm.sources.read"),
            headers={"X-Correlation-ID": str(UUID(correlation_id))},
        )
        if status != 200 or not isinstance(body, dict) or set(body) != {
            "goal_id", "goal_version", "source_ids"
        }:
            raise RuntimeError("complete source binding unavailable")
        if (body["goal_id"], body["goal_version"]) != (
            subscription.goal_id,
            subscription.goal_version,
        ):
            raise ValueError("complete source binding identity mismatch")
        source_ids = body["source_ids"]
        if not isinstance(source_ids, list) or len(source_ids) > 1000:
            raise ValueError("complete source binding exceeds bounds")
        normalized = [str(UUID(value)) for value in source_ids]
        if len(set(normalized)) != len(normalized):
            raise ValueError("complete source binding contains duplicates")
        return normalized

    def ready() -> bool:
        return (
            os.environ.get("DM_GOAL_SOURCES_BINDING_APPROVED", "").lower() == "true"
            and dependencies_ready((gm, dm, de))
        )

    return build_runtime(
        connect_database=connect,
        broker_connection=broker,
        queue="goalpulse.goal-evaluation.evidence.updated.v1",
        dead_letter_exchange="goalpulse.dlx",
        dead_letter_routing_key="goalpulse.goal-evaluation.evidence.updated.v1.dlq",
        schemas=SCHEMAS,
        gm_origin=gm,
        dm_origin=dm,
        de_origin=de,
        tokens=issuer.mint,
        profile=required("GOALPULSE_PROFILE"),
        sources_for_goal=sources_for_goal,
        dependencies_ready=ready,
        properties_factory=pika.BasicProperties,
    )


def ge_api():
    from goal_evaluation.api import OwnerPrincipal, create_app
    from goal_evaluation.auth import ServiceAuthorizer

    runtime = _ge_runtime()
    authorizer = ServiceAuthorizer(
        secret=required("SERVICE_JWT_SECRET"),
        issuer=required("SERVICE_JWT_ISSUER"),
        audience=required("SERVICE_JWT_AUDIENCE"),
        required_scope="ge.evaluations.read",
    )
    app = create_app(
        reader=runtime.reader,
        authorizer=authorizer,
        owner_authorizer=_owner_dependency("ge.history.read", OwnerPrincipal),
        goals=runtime.goals,
        ready=runtime.ready,
    )
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="warning")


def ge_worker():
    run_supervised(lambda: _ge_runtime().worker)


def _notification_ready(connect, origins: tuple[str, ...]) -> bool:
    try:
        if not dependencies_ready(origins):
            return False
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT notification_id FROM notifications LIMIT 0")
            cursor.execute("SELECT event_id FROM notification_processing LIMIT 0")
        return True
    except Exception:
        return False


def notification_api():
    from notification.api import create_app
    from notification.reads import VerifiedPrincipal
    from notification.store import PostgresStore

    _url, connect = _database("notification")
    origins = _upstream_origins()
    app = create_app(
        store=PostgresStore(connect),
        authorize_read=_owner_dependency("ns.notifications.read", VerifiedPrincipal),
        authorize_write=_owner_dependency("ns.notifications.write", VerifiedPrincipal),
        clock=utc_now,
        ready=lambda: _notification_ready(connect, origins),
    )
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="warning")


def _notification_worker():
    from notification.runtime import build_worker

    _url, connect = _database("notification")
    issuer = ServiceTokenIssuer()
    gm, ge, de = _upstream_origins()
    broker = open_broker()
    return build_worker(
        connect_database=connect,
        broker_connection=broker,
        queue="goalpulse.notification.goal.opportunity_verified.v1",
        dead_letter_exchange="goalpulse.dlx",
        dead_letter_routing_key="goalpulse.notification.goal.opportunity_verified.v1.dlq",
        schemas=SCHEMAS,
        gm_origin=gm,
        ge_origin=ge,
        de_origin=de,
        tokens=issuer.mint,
        evaluation_read_scope="ge.evaluations.read",
        profile=required("GOALPULSE_PROFILE"),
        rate_limit_minutes=int(os.environ.get("NOTIFY_GOAL_RATE_LIMIT_MINUTES", "60")),
    )


def notification_worker():
    run_supervised(_notification_worker)


def _agent_runtime():
    from agent_integration.runtime import build_runtime

    _url, connect = _database("agent-integration")
    issuer = ServiceTokenIssuer()
    gm, ge, de = _upstream_origins()
    broker = open_broker()
    return build_runtime(
        connect_database=connect,
        broker_connection=broker,
        queue="goalpulse.agent-integration.goal.opportunity_verified.v1",
        dead_letter_exchange="goalpulse.dlx",
        dead_letter_routing_key="goalpulse.agent-integration.goal.opportunity_verified.v1.dlq",
        schemas=SCHEMAS,
        gm_origin=gm,
        ge_origin=ge,
        de_origin=de,
        tokens=issuer.mint,
        evaluation_read_scope="ge.evaluations.read",
        profile=required("GOALPULSE_PROFILE"),
        dependencies_ready=lambda: dependencies_ready((gm, ge, de)),
    )


def agent_api():
    from agent_integration.api import create_app
    from agent_integration.service import Principal

    runtime = _agent_runtime()
    app = create_app(
        reader=runtime.reader,
        authenticate=_owner_dependency("ai.packages.read", Principal),
        ready=runtime.ready,
    )
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="warning")


def agent_worker():
    run_supervised(lambda: _agent_runtime().worker)


COMMANDS = {
    "ge-api": ge_api,
    "ge-worker": ge_worker,
    "notification-api": notification_api,
    "notification-worker": notification_worker,
    "agent-api": agent_api,
    "agent-worker": agent_worker,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GoalPulse private D4 runtime")
    parser.add_argument("command", choices=(*COMMANDS, "migrate"))
    parser.add_argument("service", nargs="?", choices=tuple(DATABASES))
    args = parser.parse_args(argv)
    try:
        if args.command == "migrate":
            if args.service is None:
                parser.error("migrate requires a service")
            migrate(args.service)
        else:
            if args.service is not None:
                parser.error("service is valid only with migrate")
            COMMANDS[args.command]()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ConfigurationError, ValueError, RuntimeError):
        print("private_runtime=startup_failed", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
