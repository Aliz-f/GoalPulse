#!/usr/bin/env python3
"""Write a non-live, disposable Compose environment under /tmp for CI."""
from __future__ import annotations

import argparse
from pathlib import Path
import secrets


BASE_VALUES = {
    "POSTGRES_USER": "goalpulse_ci_admin",
    "POSTGRES_DB": "postgres",
    "RABBITMQ_DEFAULT_USER": "goalpulse_ci",
    "GATEWAY_JWT_ISSUER": "goalpulse-ci",
    "GATEWAY_JWT_AUDIENCE": "goalpulse-api",
    "SERVICE_JWT_ISSUER": "goalpulse-internal-ci",
    "SERVICE_JWT_AUDIENCE": "goalpulse-internal",
    "GOALPULSE_PROFILE": "dev",
    "GATEWAY_DEMO_ALLOWED_ORIGIN": "http://localhost:3000",
    "GI_LLM_PROVIDER": "fixture",
    "GI_LLM_MODEL": "",
    "ANTHROPIC_API_KEY": "",
    "CONTEXT_DEV_API_KEY": "ci-no-external-provider-call",
    "CONTEXT_DEV_ACCESS_POLICY_IDS": "",
    "DE_MERCHANT_HOSTS": "example.com",
    "NEXT_PUBLIC_API_MODE": "mock",
    "DE_ALLOW_FIXTURE": "false",
    "POSTGRES_HOST_PORT": "55432",
    "RABBITMQ_HOST_PORT": "55672",
    "RABBITMQ_MANAGEMENT_HOST_PORT": "15673",
    "GATEWAY_HOST_PORT": "58000",
    "FRONTEND_HOST_PORT": "53000",
}


def values() -> dict[str, str]:
    result = dict(BASE_VALUES)
    for name in (
        "POSTGRES_PASSWORD",
        "GOAL_INTELLIGENCE_DB_PASSWORD",
        "GOAL_MANAGEMENT_DB_PASSWORD",
        "DISCOVERY_MONITORING_DB_PASSWORD",
        "DATA_EVIDENCE_DB_PASSWORD",
        "GOAL_EVALUATION_DB_PASSWORD",
        "NOTIFICATION_DB_PASSWORD",
        "AGENT_INTEGRATION_DB_PASSWORD",
        "RABBITMQ_DEFAULT_PASS",
        "GATEWAY_DEV_JWT_SECRET",
        "SERVICE_JWT_SECRET",
    ):
        result[name] = secrets.token_urlsafe(36)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if target != Path("/tmp") and Path("/tmp") not in target.parents:
        raise SystemExit("Compose CI environment must be written below /tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{name}={value}\n" for name, value in values().items()), encoding="utf-8")
    target.chmod(0o600)
    print("PASS wrote non-live disposable Compose environment (values not displayed)")


if __name__ == "__main__":
    main()
