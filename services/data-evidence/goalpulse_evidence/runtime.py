"""Production wiring for the owned PostgreSQL API and confirmed outbox worker.

No provider calls or fixture backend exist here. HTTP assertions remain unverified;
recorded checker proof requires its own approved capture-storage integration.
"""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api import create_app
from .ledger import EvidenceLedger
from .migrations import apply_migrations
from .postgres import Connection, PostgresRepository
from .publishing import OutboxPublisher, RabbitChannelBroker
from .reads import EvidenceReads

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]


class RuntimeConfigurationError(ValueError):
    """A named configuration requirement failed; values are never included."""


class RuntimeUnavailable(RuntimeError):
    """A redacted operational error safe for CLI/status output."""


@dataclass(frozen=True)
class Settings:
    profile: str
    database_url: str = field(repr=False)
    rabbitmq_url: str | None = field(default=None, repr=False)
    schema_directory: Path = ROOT / "contracts/schemas"
    migrations_directory: Path = SERVICE / "migrations"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, publisher: bool = False) -> "Settings":
        env = os.environ if env is None else env
        profile = env.get("GOALPULSE_PROFILE", "")
        if profile not in ("test", "dev", "demo"):
            raise RuntimeConfigurationError("GOALPULSE_PROFILE must be test, dev, or demo")
        for key in ("DATA_EVIDENCE_DATABASE_URL", "SERVICE_JWT_SECRET", "SERVICE_JWT_ISSUER",
                    "SERVICE_JWT_AUDIENCE"):
            if not env.get(key, "").strip():
                raise RuntimeConfigurationError(f"{key} is required")
        if len(env["SERVICE_JWT_SECRET"].encode()) < 32:
            raise RuntimeConfigurationError("SERVICE_JWT_SECRET must contain at least 32 bytes")
        if profile != "test" and env.get("DE_EVIDENCE_MODE", "real") != "real":
            raise RuntimeConfigurationError("DE_EVIDENCE_MODE must be real outside tests")
        database_url = env["DATA_EVIDENCE_DATABASE_URL"]
        try:
            from psycopg.conninfo import conninfo_to_dict

            database_name = conninfo_to_dict(database_url).get("dbname")
        except Exception:
            raise RuntimeConfigurationError("DATA_EVIDENCE_DATABASE_URL is invalid") from None
        allowed_names = ("data_evidence", "data_evidence_test") if profile == "test" else ("data_evidence",)
        if database_name not in allowed_names:
            raise RuntimeConfigurationError("DATA_EVIDENCE_DATABASE_URL must select the owned database")
        rabbitmq_url = env.get("RABBITMQ_URL") or None
        if publisher and not rabbitmq_url:
            raise RuntimeConfigurationError("RABBITMQ_URL is required for publisher")
        if rabbitmq_url and not rabbitmq_url.startswith(("amqp://", "amqps://")):
            raise RuntimeConfigurationError("RABBITMQ_URL must use amqp or amqps")
        return cls(profile, database_url, rabbitmq_url)


def connection_factory(settings: Settings, *, connector: Callable[..., Connection] | None = None):
    """Return fresh bounded connections to Data & Evidence's logical database."""
    if connector is None:
        import psycopg

        connector = psycopg.connect

    def connect() -> Connection:
        connection = connector(settings.database_url, autocommit=False, connect_timeout=5,
                               options="-c statement_timeout=10000 -c lock_timeout=5000")
        allowed = ("data_evidence", "data_evidence_test") if settings.profile == "test" else ("data_evidence",)
        if connection.info.dbname not in allowed:
            connection.close()
            raise RuntimeUnavailable("DATA_EVIDENCE_DATABASE_OWNERSHIP_DENIED")
        return connection

    return connect


@dataclass
class Runtime:
    settings: Settings
    connect: Callable[[], Connection] = field(repr=False)
    ledger: EvidenceLedger = field(repr=False)
    reads: EvidenceReads = field(repr=False)
    app: FastAPI = field(repr=False)


def build_runtime(settings: Settings | None = None, *,
                  connector: Callable[..., Connection] | None = None,
                  migration_runner: Callable[..., list[str]] = apply_migrations) -> Runtime:
    """Validate configuration and apply owned migrations before reporting ready."""
    settings = settings or Settings.from_env()
    connect = connection_factory(settings, connector=connector)
    connection = None
    try:
        expected_migrations = {path.name: sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
                               for path in sorted(settings.migrations_directory.glob("[0-9]*.sql"))}
        if not expected_migrations:
            raise RuntimeUnavailable("DATA_EVIDENCE_MIGRATIONS_MISSING")
        connection = connect()
        migration_runner(connection, settings.migrations_directory)
        repository = PostgresRepository(connect)
        ledger = EvidenceLedger(repository, schema_directory=settings.schema_directory)
        reads = EvidenceReads(connect, schema_directory=settings.schema_directory)
    except Exception:
        raise RuntimeUnavailable("DATA_EVIDENCE_STARTUP_FAILED") from None
    finally:
        if connection is not None:
            connection.close()
    app = create_app(ledger, reads, profile=settings.profile)

    @app.middleware("http")
    async def redact_operational_failure(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            # Driver exception strings may contain DSNs or submitted payloads.
            return JSONResponse(status_code=503, content={"detail": "evidence service unavailable"})

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        connection = None
        try:
            connection = connect()
            with connection.cursor() as cursor:
                cursor.execute("SELECT version, checksum FROM de_schema_migrations")
                applied = dict(cursor.fetchall())
                if any(applied.get(version) != checksum for version, checksum in expected_migrations.items()):
                    raise RuntimeUnavailable("MIGRATIONS_NOT_READY")
            connection.rollback()
            return {"status": "ready"}
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        finally:
            if connection is not None:
                connection.close()

    return Runtime(settings, connect, ledger, reads, app)


def from_env() -> FastAPI:
    """Uvicorn factory; runtime construction completes migrations first."""
    return build_runtime().app


@contextmanager
def open_broker(settings: Settings, *, pika_module: Any = None):
    """Bound RabbitMQ connection setup and close every acquired connection."""
    if not settings.rabbitmq_url:
        raise RuntimeConfigurationError("RABBITMQ_URL is required for publisher")
    if pika_module is None:
        import pika as pika_module
    connection = None
    try:
        parameters = pika_module.URLParameters(settings.rabbitmq_url)
        parameters.heartbeat = 30
        parameters.blocked_connection_timeout = 5
        parameters.socket_timeout = 5
        parameters.stack_timeout = 10
        parameters.connection_attempts = 2
        parameters.retry_delay = 1
        connection = pika_module.BlockingConnection(parameters)
        yield RabbitChannelBroker(connection.channel(), pika_module.BasicProperties)
    except Exception:
        raise RuntimeUnavailable("DATA_EVIDENCE_BROKER_UNAVAILABLE") from None
    finally:
        if connection is not None and connection.is_open:
            connection.close()


def publish_once(runtime: Runtime, *, broker_factory=open_broker) -> str:
    """Publish one due durable outbox row using confirmed persistent delivery."""
    with broker_factory(runtime.settings) as broker:
        return OutboxPublisher(runtime.connect, broker,
                               schema_directory=runtime.settings.schema_directory).run_once()
