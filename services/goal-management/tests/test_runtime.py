import asyncio
import base64
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.config import ConfigurationError, Settings
from goal_management.runtime import RuntimeReadiness
from goal_management.tokens import GOAL_MANAGEMENT_SERVICE_SUBJECT, ServiceTokenIssuer


def env():
    return {
        "GOAL_MANAGEMENT_DATABASE_URL": "postgresql://goal_management:secret@postgres/goal_management",
        "GOAL_INTELLIGENCE_URL": "http://goal-intelligence:8000",
        "GATEWAY_DEV_JWT_SECRET": "g" * 32,
        "GATEWAY_JWT_ISSUER": "goalpulse-dev",
        "GATEWAY_JWT_AUDIENCE": "goalpulse-api",
        "SERVICE_JWT_SECRET": "s" * 32,
        "SERVICE_JWT_ISSUER": "goalpulse-internal-dev",
        "SERVICE_JWT_AUDIENCE": "goalpulse-internal",
    }


def test_runtime_configuration_is_fail_closed_and_redacts_values():
    assert Settings.from_env(env()).goal_intelligence_url == "http://goal-intelligence:8000"
    for key in ("GOAL_MANAGEMENT_DATABASE_URL", "SERVICE_JWT_SECRET", "GATEWAY_DEV_JWT_SECRET"):
        invalid = env()
        invalid[key] = ""
        with pytest.raises(ConfigurationError, match=key):
            Settings.from_env(invalid)


def test_service_tokens_are_short_lived_scoped_machine_identities():
    token = ServiceTokenIssuer(
        secret="s" * 32,
        issuer="goalpulse-internal-dev",
        audience="goalpulse-internal",
        clock=lambda: 100,
    ).mint("gi.candidate.read")
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims == {
        "aud": "goalpulse-internal",
        "exp": 160,
        "iat": 100,
        "iss": "goalpulse-internal-dev",
        "nbf": 100,
        "scope": "gi.candidate.read",
        "sub": str(GOAL_MANAGEMENT_SERVICE_SUBJECT),
        "token_kind": "service",
    }


class Repository:
    def __init__(self, failure=False):
        self.failure = failure

    def ping(self):
        if self.failure:
            raise RuntimeError("database unavailable")


def readiness(*, db_failure=False, gi_status=200):
    transport = httpx.MockTransport(lambda _request: httpx.Response(gi_status))
    return RuntimeReadiness(
        Repository(db_failure),
        goal_intelligence_url="http://goal-intelligence:8000",
        timeout_seconds=1,
        transport=transport,
    )


def test_readiness_requires_owned_database_and_goal_intelligence():
    assert asyncio.run(readiness().check()) is True
    assert asyncio.run(readiness(db_failure=True).check()) is False
    assert asyncio.run(readiness(gi_status=503).check()) is False
