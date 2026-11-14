"""Offline production-wiring tests using explicit injected transport fixtures."""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from fake_evidence_repository import MemoryRepository
from goalpulse_evidence.postgres import PostgresRepository
from goalpulse_evidence.reads import EvidenceReads
from goalpulse_evidence.runtime import (
    RuntimeConfigurationError, RuntimeUnavailable, Settings, build_runtime, connection_factory,
    open_broker, publish_once,
)
from test_evidence_internal_api import payload, request, token

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def configured(monkeypatch):
    env = {
        "GOALPULSE_PROFILE": "demo",
        "DATA_EVIDENCE_DATABASE_URL": "postgresql://de:synthetic-password@localhost/data_evidence",
        "SERVICE_JWT_SECRET": "synthetic-runtime-signing-secret-32-bytes",
        "SERVICE_JWT_ISSUER": "goalpulse-tests",
        "SERVICE_JWT_AUDIENCE": "goalpulse-internal",
        "RABBITMQ_URL": "amqp://de:synthetic-broker-password@localhost/",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


class DatabaseConnection:
    autocommit = False

    def __init__(self, database):
        self.database = database
        self.info = SimpleNamespace(dbname="data_evidence")
        self.closed, self.rolled_back = False, False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params

    def fetchall(self):
        return list(self.database.migrations.items())

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class Database:
    def __init__(self):
        self.migrations, self.connections, self.arguments = {}, [], []
        self.fail = False

    def connect(self, *args, **kwargs):
        if self.fail:
            raise OSError("synthetic-password must never appear in response")
        self.arguments.append((args, kwargs))
        connection = DatabaseConnection(self)
        self.connections.append(connection)
        return connection

    def migrate(self, connection, directory):
        self.migrations = {p.name: sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
                           for p in directory.glob("[0-9]*.sql")}
        return list(self.migrations)


def runtime_for(configured):
    database = Database()
    runtime = build_runtime(Settings.from_env(configured), connector=database.connect,
                            migration_runner=database.migrate)
    return runtime, database


@pytest.mark.parametrize("key", ["GOALPULSE_PROFILE", "DATA_EVIDENCE_DATABASE_URL", "SERVICE_JWT_SECRET",
                                  "SERVICE_JWT_ISSUER", "SERVICE_JWT_AUDIENCE"])
def test_runtime_fails_closed_on_missing_configuration(configured, key):
    del configured[key]
    with pytest.raises(RuntimeConfigurationError, match=key):
        Settings.from_env(configured)


@pytest.mark.parametrize("key,value", [
    ("GOALPULSE_PROFILE", "prod"), ("SERVICE_JWT_SECRET", "short"),
    ("DE_EVIDENCE_MODE", "fixture"), ("DATA_EVIDENCE_DATABASE_URL", "not a valid DSN"),
    ("DATA_EVIDENCE_DATABASE_URL", "postgresql://localhost/goal_management"),
    ("RABBITMQ_URL", "http://localhost"),
])
def test_invalid_or_unsafe_runtime_configuration_is_redacted(configured, key, value):
    configured[key] = value
    with pytest.raises(RuntimeConfigurationError) as caught:
        Settings.from_env(configured)
    assert "synthetic-password" not in str(caught.value)
    assert "synthetic-broker-password" not in str(caught.value)


def test_publisher_requires_broker_but_api_can_start_without_one(configured):
    configured.pop("RABBITMQ_URL")
    assert Settings.from_env(configured).rabbitmq_url is None
    with pytest.raises(RuntimeConfigurationError, match="RABBITMQ_URL"):
        Settings.from_env(configured, publisher=True)


def test_runtime_uses_owned_database_adapters_migrates_and_checks_readiness(configured):
    runtime, database = runtime_for(configured)
    assert isinstance(runtime.ledger.repository, PostgresRepository)
    assert isinstance(runtime.reads, EvidenceReads)
    assert database.migrations and database.connections[0].closed
    assert request(runtime.app, "GET", "/health/live").json() == {"status": "live"}
    assert request(runtime.app, "GET", "/health/ready").json() == {"status": "ready"}
    assert len(database.connections) == 2 and database.connections[-1].closed
    for _args, kwargs in database.arguments:
        assert kwargs["autocommit"] is False and kwargs["connect_timeout"] == 5
        assert "statement_timeout=10000" in kwargs["options"]
        assert "lock_timeout=5000" in kwargs["options"]
    database.migrations.pop(next(iter(database.migrations)))
    assert request(runtime.app, "GET", "/health/ready").status_code == 503
    database.fail = True
    response = request(runtime.app, "GET", "/health/ready")
    assert response.status_code == 503 and "synthetic-password" not in response.text
    assert request(runtime.app, "GET", "/health/live").status_code == 200
    assert "synthetic-password" not in repr(runtime)


def test_migration_failure_stops_startup_without_exposing_driver_error(configured):
    database = Database()

    def fail(*args):
        raise OSError("synthetic-password")

    with pytest.raises(RuntimeUnavailable, match="^DATA_EVIDENCE_STARTUP_FAILED$") as caught:
        build_runtime(Settings.from_env(configured), connector=database.connect, migration_runner=fail)
    assert database.connections[0].closed and "synthetic-password" not in str(caught.value)


def test_connection_factory_checks_actual_database_identity(configured):
    connection = Mock(info=SimpleNamespace(dbname="goal_management"))
    connect = connection_factory(Settings.from_env(configured), connector=lambda *args, **kwargs: connection)
    with pytest.raises(RuntimeUnavailable, match="OWNERSHIP_DENIED"):
        connect()
    connection.close.assert_called_once()


@pytest.mark.parametrize("profile", ["dev", "demo"])
@pytest.mark.parametrize("field", ["provider", "extraction_method"])
def test_non_test_http_ingestion_rejects_fixture_before_persistence(configured, profile, field):
    configured["GOALPULSE_PROFILE"] = profile
    runtime, _database = runtime_for(configured)
    repository = MemoryRepository()
    runtime.ledger.repository = repository
    body = payload()
    body.update(provider="context_dev", extraction_method="scrape")
    body[field] = "fixture"
    response = request(runtime.app, "POST", "/internal/v1/extractions", body=body,
                       headers={"Authorization": token("de.extractions.write", secret=configured["SERVICE_JWT_SECRET"])})
    assert response.status_code == 400
    assert all(not rows for rows in repository.state.values())


def test_demo_http_observation_and_failure_remain_real_unverified_and_unknown(configured):
    runtime, _database = runtime_for(configured)
    repository = MemoryRepository()
    runtime.ledger.repository = repository  # Explicit test-only persistence fixture.
    body = payload()
    body.update(provider="context_dev", extraction_method="scrape")
    headers = {"Authorization": token("de.extractions.write", secret=configured["SERVICE_JWT_SECRET"])}
    created = request(runtime.app, "POST", "/internal/v1/extractions", body=body, headers=headers)
    assert created.status_code == 201
    observation = next(iter(repository.state["observations"].values()))
    assert observation["provider"] == "context_dev" and observation["evidence"]["reliability"] == "unverified"
    assert observation["shipping_eur_cents"] is None and observation["tax_eur_cents"] is None
    assert observation["availability"] == "unknown" and observation["delivery_to_fi"] == "unknown"
    missing = deepcopy(body)
    missing["attempt_id"] = str(uuid4())
    missing["extracted"]["unit_price_eur_cents"] = None
    failure = request(runtime.app, "POST", "/internal/v1/extractions", body=missing, headers=headers)
    assert failure.status_code == 202 and failure.json()["reason"] == "MISSING_UNIT_PRICE"
    assert len(repository.state["observations"]) == len(repository.state["outbox"]) == 1


def test_demo_reads_refuse_stored_fixture_and_redact_database_failures(configured):
    runtime, _database = runtime_for(configured)
    fixture = json.loads((ROOT / "contracts/examples/observation.unknown.fixture.json").read_text(encoding="utf-8"))
    runtime.reads.by_id = lambda _identifier: fixture
    runtime.reads.by_source = lambda *_args, **_kwargs: [fixture]
    headers = {"Authorization": token("de.observations.read", secret=configured["SERVICE_JWT_SECRET"])}
    assert request(runtime.app, "GET", f"/internal/v1/observations/{fixture['observation_id']}",
                   headers=headers).status_code == 503
    assert request(runtime.app, "GET", f"/internal/v1/observations?source_id={fixture['source_id']}",
                   headers=headers).status_code == 503

    def failed_read(_identifier):
        raise OSError("synthetic-password full scraped payload")

    runtime.reads.by_id = failed_read
    response = request(runtime.app, "GET", f"/internal/v1/observations/{fixture['observation_id']}", headers=headers)
    assert response.status_code == 503 and response.json() == {"detail": "evidence service unavailable"}


def test_broker_wiring_uses_durable_exchange_confirms_timeouts_and_closes(configured):
    channel = Mock()
    channel.basic_publish.return_value = None
    connection = Mock(is_open=True)
    connection.channel.return_value = channel
    pika = SimpleNamespace(URLParameters=lambda url: SimpleNamespace(url=url),
                           BlockingConnection=Mock(return_value=connection), BasicProperties=lambda **kw: kw)
    with open_broker(Settings.from_env(configured), pika_module=pika) as broker:
        assert broker.publish(exchange="goalpulse.events", routing_key="evidence.updated.v1", body=b"{}",
                              message_id=str(uuid4()), correlation_id=str(uuid4()))
    parameters = pika.BlockingConnection.call_args.args[0]
    assert parameters.connection_attempts == 2 and parameters.socket_timeout == 5
    assert parameters.stack_timeout == 10 and parameters.blocked_connection_timeout == 5
    channel.exchange_declare.assert_called_once_with(exchange="goalpulse.events", exchange_type="topic", durable=True)
    channel.confirm_delivery.assert_called_once()
    assert channel.basic_publish.call_args.kwargs["properties"]["delivery_mode"] == 2
    connection.close.assert_called_once()


def test_broker_setup_failure_is_redacted(configured):
    pika = SimpleNamespace(URLParameters=lambda url: (_ for _ in ()).throw(ValueError("synthetic-password")))
    with pytest.raises(RuntimeUnavailable, match="^DATA_EVIDENCE_BROKER_UNAVAILABLE$"):
        with open_broker(Settings.from_env(configured), pika_module=pika):
            pytest.fail("Must not enter failed broker context")


def test_publish_once_uses_real_outbox_worker_and_confirmed_broker(configured):
    from test_publishing import OutboxConnection, event

    connection = OutboxConnection(event())
    broker = Mock()
    broker.publish.return_value = True

    @contextmanager
    def factory(settings):
        yield broker

    runtime = SimpleNamespace(settings=Settings.from_env(configured), connect=lambda: connection)
    assert publish_once(runtime, broker_factory=factory) == "published"
    assert broker.publish.call_args.kwargs["routing_key"] == "evidence.updated.v1"


def test_publisher_cli_once_is_bounded_and_redacts_startup(monkeypatch, configured, capsys):
    import goalpulse_evidence.__main__ as entrypoint

    monkeypatch.setattr(entrypoint, "build_runtime", lambda _settings: SimpleNamespace())
    monkeypatch.setattr(entrypoint, "publish_once", lambda _runtime: "idle")
    assert entrypoint.main(["publisher", "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"

    def failed(_settings):
        raise OSError("synthetic-password")

    monkeypatch.setattr(entrypoint, "build_runtime", failed)
    assert entrypoint.main(["publisher", "--once"]) == 1
    output = capsys.readouterr().out
    assert "synthetic-password" not in output and "startup_failed" in output
