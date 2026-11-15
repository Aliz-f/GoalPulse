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
            # Shipped D4 consumers declare durable classic queues. Keep the
            # imported topology equivalent so RabbitMQ does not close their
            # channels with PRECONDITION_FAILED.
            assert "x-queue-type" not in queues[queue_name]["arguments"]
            assert queues[queue_name]["arguments"]["x-dead-letter-exchange"] == "goalpulse.dlx"
            assert any(
                binding["destination"] == queue_name and binding["routing_key"] == event_type
                for binding in event_bindings
            )
            dlq_name = f"{queue_name}.dlq"
            assert queues[dlq_name]["durable"] is True
            assert "x-queue-type" not in queues[dlq_name]["arguments"]
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


def test_only_frontend_and_gateway_bind_loopback_and_services_use_private_networks():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["gateway"]["ports"] == ["127.0.0.1:${GATEWAY_HOST_PORT:-8000}:8000"]
    assert services["frontend"]["ports"] == ["127.0.0.1:${FRONTEND_HOST_PORT:-3000}:3000"]
    assert services["frontend"]["depends_on"]["gateway"]["condition"] == "service_healthy"
    assert services["frontend"]["networks"] == ["app"]
    api_services = (
        "goal-intelligence",
        "goal-management",
        "discovery-monitoring",
        "data-evidence",
        "goal-evaluation",
        "notification",
        "agent-integration",
    )
    for service in api_services:
        assert "ports" not in services[service]
        assert services[service]["expose"] == ["8000"]
        assert services[service]["healthcheck"]
    assert services["gateway"]["networks"] == ["app"]
    for service in api_services:
        assert set(services[service]["networks"]) == {"app", "data"}
    assert "data" not in services["gateway"]["networks"]
    assert "data" not in services["frontend"]["networks"]
    for service in ("postgres", "rabbitmq"):
        assert "app" not in services[service].get("networks", [])


def test_goal_intelligence_migrates_before_api_and_gateway_waits_for_health():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["goal-intelligence-migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["goal-intelligence"]["depends_on"]["goal-intelligence-migrate"]["condition"] == "service_completed_successfully"
    assert services["gateway"]["depends_on"]["goal-intelligence"]["condition"] == "service_healthy"
    assert services["gateway"]["environment"]["GOAL_INTELLIGENCE_URL"] == "http://goal-intelligence:8000"


def test_all_service_migrations_workers_and_gateway_dependencies_are_declared():
    compose = yaml.safe_load((INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    migrations = {
        "goal-intelligence": "goal-intelligence-migrate",
        "goal-management": "goal-management-migrate",
        "goal-evaluation": "goal-evaluation-migrate",
        "notification": "notification-migrate",
        "agent-integration": "agent-integration-migrate",
    }
    for api, migration in migrations.items():
        assert services[api]["depends_on"][migration]["condition"] == "service_completed_successfully"
        assert services[migration]["restart"] == "no"

    workers = {
        "goal-management-outbox",
        "goal-management-evaluations",
        "discovery-monitoring-consumer",
        "discovery-monitoring-worker",
        "discovery-monitoring-scheduler",
        "data-evidence-publisher",
        "goal-evaluation-worker",
        "notification-worker",
        "agent-integration-worker",
    }
    assert workers <= services.keys()
    for worker in workers:
        inherited = services[worker]
        while "restart" not in inherited:
            inherited = services[inherited["extends"]["service"]]
        assert inherited["restart"] == "unless-stopped"
        assert "ports" not in services[worker]

    api_urls = {
        "GOAL_INTELLIGENCE_URL": "goal-intelligence",
        "GOAL_MANAGEMENT_URL": "goal-management",
        "DISCOVERY_MONITORING_URL": "discovery-monitoring",
        "DATA_EVIDENCE_URL": "data-evidence",
        "GOAL_EVALUATION_URL": "goal-evaluation",
        "NOTIFICATION_URL": "notification",
        "AGENT_INTEGRATION_URL": "agent-integration",
    }
    gateway = services["gateway"]
    for variable, service in api_urls.items():
        assert gateway["environment"][variable] == f"http://{service}:8000"
        assert gateway["depends_on"][service]["condition"] == "service_healthy"


def test_demo_runtime_has_no_fixture_or_mock_fallback_in_compose():
    compose_text = (INFRASTRUCTURE / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    services = compose["services"]
    assert services["goal-intelligence"]["environment"]["GI_LLM_PROVIDER"] == "${GI_LLM_PROVIDER:-fixture}"
    assert services["discovery-monitoring"]["environment"]["CONTEXT_DEV_API_KEY"] == "${CONTEXT_DEV_API_KEY:?Set CONTEXT_DEV_API_KEY}"
    assert "fixture" not in " ".join(services["data-evidence"]["command"])
    assert services["gateway"]["environment"]["NEXT_PUBLIC_API_MODE"] == "${NEXT_PUBLIC_API_MODE:-}"
    frontend_args = services["frontend"]["build"]["args"]
    assert frontend_args["NEXT_PUBLIC_GOALPULSE_PROFILE"] == "${GOALPULSE_PROFILE:?Set GOALPULSE_PROFILE}"
    assert frontend_args["NEXT_PUBLIC_API_MODE"] == "${NEXT_PUBLIC_API_MODE:?Set NEXT_PUBLIC_API_MODE}"
    assert frontend_args["NEXT_PUBLIC_GATEWAY_URL"] == "${NEXT_PUBLIC_GATEWAY_URL:-}"
    assert frontend_args["NEXT_PUBLIC_DEMO_USER_A_USERNAME"] == "${GATEWAY_DEMO_USER_A_USERNAME:-}"
    assert frontend_args["NEXT_PUBLIC_DEMO_USER_A_PASSWORD"] == "${DEMO_USER_A_PUBLIC_PASSWORD:-}"
    assert frontend_args["NEXT_PUBLIC_DEMO_USER_B_USERNAME"] == "${GATEWAY_DEMO_USER_B_USERNAME:-}"
    assert frontend_args["NEXT_PUBLIC_DEMO_USER_B_PASSWORD"] == "${DEMO_USER_B_PUBLIC_PASSWORD:-}"


def test_frontend_image_is_node_22_standalone_and_non_root():
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    next_config = (ROOT / "frontend" / "next.config.ts").read_text(encoding="utf-8")
    assert "node:22.21.0-bookworm-slim" in dockerfile
    assert 'USER goalpulse' in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile
    assert 'output: "standalone"' in next_config
    assert 'poweredByHeader: false' in next_config
