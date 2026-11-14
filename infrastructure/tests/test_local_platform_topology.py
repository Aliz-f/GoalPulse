"""Static checks for the local-only PostgreSQL and RabbitMQ bootstrap."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE = ROOT / "infrastructure"


def test_postgres_bootstrap_creates_isolated_non_superuser_roles():
    script = (INFRASTRUCTURE / "postgres" / "01-create-service-dbs.sh").read_text(
        encoding="utf-8"
    )
    services = (
        "goal_intelligence",
        "goal_management",
        "discovery_monitoring",
        "data_evidence",
        "goal_evaluation",
        "notification",
        "agent_integration",
    )
    for service in services:
        assert f"CREATE ROLE {service} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT" in script
        assert f"CREATE DATABASE {service} OWNER {service};" in script
        assert f"REVOKE CONNECT ON DATABASE {service} FROM PUBLIC;" in script
        assert f"GRANT CONNECT ON DATABASE {service} TO {service};" in script
        assert f"GRANT USAGE, CREATE ON SCHEMA public TO {service};" in script


def test_rabbitmq_topology_matches_frozen_event_routing():
    routing = yaml.safe_load((ROOT / "contracts" / "event-routing.yaml").read_text(encoding="utf-8"))
    topology = json.loads((INFRASTRUCTURE / "rabbitmq" / "definitions.json").read_text(encoding="utf-8"))

    exchanges = {exchange["name"]: exchange for exchange in topology["exchanges"]}
    assert exchanges["goalpulse.events"]["type"] == routing["exchange_type"]
    assert exchanges["goalpulse.events"]["durable"] is True
    assert exchanges["goalpulse.dlx"]["durable"] is True

    queues = {queue["name"]: queue for queue in topology["queues"]}
    event_bindings = [binding for binding in topology["bindings"] if binding["source"] == "goalpulse.events"]
    assert len(event_bindings) == sum(len(item["consumers"]) for item in routing["routing"].values())

    for event_type, item in routing["routing"].items():
        for consumer in item["consumers"]:
            queue_name = f"goalpulse.{consumer}.{event_type}"
            assert queues[queue_name]["durable"] is True
            assert queues[queue_name]["arguments"]["x-queue-type"] == "quorum"
            assert queues[queue_name]["arguments"]["x-dead-letter-exchange"] == "goalpulse.dlx"
            assert any(
                binding["destination"] == queue_name and binding["routing_key"] == event_type
                for binding in event_bindings
            )
            dlq_name = f"{queue_name}.dlq"
            assert queues[dlq_name]["durable"] is True
            assert queues[queue_name]["arguments"]["x-dead-letter-routing-key"] == dlq_name


def test_compose_mounts_bootstrap_and_broker_definitions():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    postgres_volumes = compose["services"]["postgres"]["volumes"]
    assert any("01-create-service-dbs.sh" in volume for volume in postgres_volumes)
    init_service = compose["services"]["rabbitmq-init"]
    assert any("definitions.json" in volume for volume in init_service["volumes"])
    assert init_service["depends_on"]["rabbitmq"]["condition"] == "service_healthy"
    assert "rabbitmqadmin" in init_service["command"][2]
    postgres_environment = compose["services"]["postgres"]["environment"]
    for name in (
        "GOAL_INTELLIGENCE_DB_PASSWORD",
        "GOAL_MANAGEMENT_DB_PASSWORD",
        "DISCOVERY_MONITORING_DB_PASSWORD",
        "DATA_EVIDENCE_DB_PASSWORD",
        "GOAL_EVALUATION_DB_PASSWORD",
        "NOTIFICATION_DB_PASSWORD",
        "AGENT_INTEGRATION_DB_PASSWORD",
    ):
        assert name in postgres_environment


def test_gateway_is_only_application_port_and_services_use_private_networks():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["gateway"]["ports"] == ["127.0.0.1:${GATEWAY_HOST_PORT:-8000}:8000"]
    assert "ports" not in services["goal-intelligence"]
    assert services["goal-intelligence"]["expose"] == ["8000"]
    assert services["gateway"]["networks"] == ["app"]
    assert set(services["goal-intelligence"]["networks"]) == {"app", "data"}
    assert "data" not in services["gateway"]["networks"]
    for service in ("postgres", "rabbitmq", "goal-intelligence"):
        assert "app" not in services[service].get("networks", []) or service == "goal-intelligence"


def test_goal_intelligence_migrates_before_api_and_gateway_waits_for_health():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["goal-intelligence-migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["goal-intelligence"]["depends_on"]["goal-intelligence-migrate"]["condition"] == "service_completed_successfully"
    assert services["gateway"]["depends_on"]["goal-intelligence"]["condition"] == "service_healthy"
    assert services["gateway"]["environment"]["GOAL_INTELLIGENCE_URL"] == "http://goal-intelligence:8000"
