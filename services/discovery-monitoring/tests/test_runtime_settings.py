"""Runtime fail-closed configuration and migration-aware readiness."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from goalpulse_discovery.config import ConfigurationError
from goalpulse_discovery.runtime import PipelineRuntime, RuntimeSettings, SERVICE, owned_connection_factory


def environment():
    return {
        "GOALPULSE_PROFILE": "demo", "DISCOVERY_MONITORING_DATABASE_URL": "postgresql://localhost/discovery_monitoring",
        "RABBITMQ_URL": "amqp://localhost/", "GOAL_MANAGEMENT_BASE_URL": "http://goal-management:8000",
        "DATA_EVIDENCE_BASE_URL": "http://data-evidence:8000", "DISCOVERY_SERVICE_ID": str(uuid4()),
        "SERVICE_JWT_SECRET": "runtime-secret-at-least-thirty-two-bytes", "SERVICE_JWT_ISSUER": "tests",
        "SERVICE_JWT_AUDIENCE": "goalpulse-internal", "CONTEXT_DEV_API_KEY": "synthetic-test-key",
    }


def test_runtime_environment_and_repr_do_not_expose_secrets():
    env = environment()
    settings = RuntimeSettings.from_env(env)
    assert settings.profile == "demo" and settings.max_sources == 3
    assert env["SERVICE_JWT_SECRET"] not in repr(settings)
    assert env["CONTEXT_DEV_API_KEY"] not in repr(settings)
    assert env["DISCOVERY_MONITORING_DATABASE_URL"] not in repr(settings)


@pytest.mark.parametrize("name", [
    "GOALPULSE_PROFILE", "DISCOVERY_MONITORING_DATABASE_URL", "RABBITMQ_URL", "GOAL_MANAGEMENT_BASE_URL",
    "DATA_EVIDENCE_BASE_URL", "DISCOVERY_SERVICE_ID", "SERVICE_JWT_SECRET", "SERVICE_JWT_ISSUER", "SERVICE_JWT_AUDIENCE",
])
def test_missing_runtime_configuration_fails_without_printing_values(name):
    env = environment()
    del env[name]
    with pytest.raises(ConfigurationError) as error:
        RuntimeSettings.from_env(env)
    assert env.get("CONTEXT_DEV_API_KEY") not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"GOALPULSE_PROFILE": "prod"}, {"DM_PROVIDER": "fixture"},
    {"CONTEXT_DEV_API_KEY": "replace_with_your_context_dev_api_key"},
    {"SERVICE_JWT_SECRET": "change_this_local_service_hs256_secret_at_least_32_chars"},
    {"DISCOVERY_MONITORING_DATABASE_URL": "postgresql://localhost/goal_management"},
    {"DATA_EVIDENCE_BASE_URL": "http://user:password@data-evidence/"},
    {"GOAL_MANAGEMENT_BASE_URL": "http://goal-management/path"},
    {"DM_MAX_SOURCES_PER_GOAL": "4"}, {"DM_INITIAL_MAX_OPERATIONS_PER_DAY": "101"},
    {"DISCOVERY_SERVICE_ID": "not-a-uuid"},
])
def test_unsafe_or_fixture_runtime_configuration_is_rejected(changes):
    env = environment()
    env.update(changes)
    with pytest.raises(ConfigurationError):
        RuntimeSettings.from_env(env)


class ReadinessDatabase:
    def __init__(self, records=None, fail=False):
        self.records, self.fail = records, fail

    def transaction(self):
        return self

    def __enter__(self):
        if self.fail:
            raise OSError("database password must not escape")
        return self

    def __exit__(self, *args):
        pass

    def execute(self, _query):
        pass

    def fetchall(self):
        return self.records


def test_ready_requires_all_exact_owned_migration_checksums():
    records = [(p.name, sha256(p.read_text(encoding="utf-8").encode()).hexdigest())
               for p in (SERVICE / "migrations").glob("[0-9]*.sql")]
    runtime = object.__new__(PipelineRuntime)
    runtime.database = ReadinessDatabase(records)
    assert runtime.ready()
    runtime.database.records = records[:-1]
    assert not runtime.ready()
    runtime.database.fail = True
    assert not runtime.ready()


def test_database_connection_errors_are_sanitized(monkeypatch):
    settings = RuntimeSettings.from_env(environment())
    def unavailable(*args, **kwargs):
        raise OSError("database credentials must not escape")
    monkeypatch.setattr("goalpulse_discovery.runtime.psycopg.connect", unavailable)
    with pytest.raises(RuntimeError) as error:
        owned_connection_factory(settings)()
    assert str(error.value) == "Discovery database unavailable"


def test_http_dependency_failure_never_reaches_server_error_logging(caplog):
    import asyncio
    import httpx
    from goalpulse_discovery.api import create_app

    def unavailable():
        raise RuntimeError("synthetic-database-password-must-not-escape")
    app = create_app(object(), readiness=unavailable)
    async def request():
        # Default raise_app_exceptions=True also proves the underlying failure
        # is caught before Starlette's re-raising server error logger.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dm") as client:
            return await client.get("/health/ready")
    response = asyncio.run(request())
    assert response.status_code == 503
    assert "synthetic-database-password" not in response.text + caplog.text
