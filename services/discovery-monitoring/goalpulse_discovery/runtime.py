"""Production composition for initial activation -> Context.dev -> Evidence.

Only explicit test-profile callers may replace infrastructure with deterministic
fixtures. Normal startup always uses PostgreSQL, authenticated HTTP and the real
ContextProvider; provider failures never select a different implementation.
"""

from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from hashlib import sha256
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pika
import psycopg
from psycopg.conninfo import conninfo_to_dict

from .adapters import HttpEvidenceSink, HttpGoalReader, InternalHttp, ServiceIdentity
from .api import create_app
from .config import ConfigurationError, read_context_api_key
from .consumer import ActivationConsumer
from .context_provider import ContextProvider, public_addresses
from .deliveries import JournaledEvidenceSink, SubmissionStore
from .jobs import DiscoveryJobs
from .limits import GuardedProvider, UsageStore
from .persistence import Database
from .registry import SourceRegistry
from .scheduling import ScanScheduler, ScanStore
from .operator import require_local_operator
from .context_provider import ProviderError
from .workflow import ActivationInbox, DiscoveryWorker

SERVICE = Path(__file__).resolve().parents[1]
SCHEMAS = SERVICE.parents[1] / "contracts/schemas"


@dataclass(frozen=True)
class RuntimeSettings:
    profile: str
    database_url: str = field(repr=False)
    rabbitmq_url: str = field(repr=False)
    goal_management_url: str
    data_evidence_url: str
    service_subject: UUID
    service_secret: str = field(repr=False)
    service_issuer: str
    service_audience: str
    context_api_key: str = field(repr=False)
    context_access_policy_ids: tuple[str, ...] = ()
    schema_directory: Path = SCHEMAS
    max_sources: int = 3
    max_operations: int = 100
    max_active_goals: int = 2
    max_user_operations: int = 100
    operator_ids: str = field(default="", repr=False)

    def __post_init__(self):
        if self.profile not in ("test", "dev", "demo"):
            raise ConfigurationError("GOALPULSE_PROFILE must be test, dev or demo")
        if (not isinstance(self.service_subject, UUID) or len(self.service_secret.encode()) < 32
                or self.service_secret.startswith("change_this") or not self.service_issuer
                or not self.service_audience or not self.context_api_key
                or any(c.isspace() for c in self.context_api_key)):
            raise ConfigurationError("Valid service identity and Context.dev configuration are required")
        if (not isinstance(self.context_access_policy_ids, tuple)
                or (self.profile == "demo" and not self.context_access_policy_ids)
                or len(self.context_access_policy_ids) > 16
                or len(set(self.context_access_policy_ids)) != len(self.context_access_policy_ids)
                or any(not re.fullmatch(r"pol_[A-Za-z0-9_-]{1,196}", value)
                       for value in self.context_access_policy_ids)):
            raise ConfigurationError("Context.dev access policy configuration is invalid")
        if (type(self.max_sources) is not int or not 1 <= self.max_sources <= 3
                or type(self.max_operations) is not int or not 1 <= self.max_operations <= 100):
            raise ConfigurationError("Initial pipeline budgets exceed supported bounds")
        if (type(self.max_active_goals) is not int or not 1 <= self.max_active_goals <= 2
                or type(self.max_user_operations) is not int or not 1 <= self.max_user_operations <= 100):
            raise ConfigurationError("Per-user monitoring limits exceed supported bounds")
        try:
            dbname = conninfo_to_dict(self.database_url).get("dbname")
            allowed = {"discovery_monitoring"}
            if self.profile == "test":
                allowed.add("discovery_monitoring_test")
            if dbname not in allowed:
                raise ValueError
            for value in (self.goal_management_url, self.data_evidence_url):
                url = urlsplit(value)
                if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                        or url.query or url.fragment or url.path not in ("", "/")):
                    raise ValueError
            broker = urlsplit(self.rabbitmq_url)
            if broker.scheme not in ("amqp", "amqps") or not broker.hostname:
                raise ValueError
        except Exception:
            raise ConfigurationError("Invalid owned database, broker or internal service URL") from None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        required = ("GOALPULSE_PROFILE", "DISCOVERY_MONITORING_DATABASE_URL", "RABBITMQ_URL",
                    "GOAL_MANAGEMENT_BASE_URL", "DATA_EVIDENCE_BASE_URL", "DISCOVERY_SERVICE_ID",
                    "SERVICE_JWT_SECRET", "SERVICE_JWT_ISSUER", "SERVICE_JWT_AUDIENCE")
        if any(not env.get(name, "").strip() for name in required):
            raise ConfigurationError("Missing Discovery runtime configuration")
        if env.get("DM_PROVIDER", "context_dev") != "context_dev":
            raise ConfigurationError("Discovery runtime requires the real Context.dev provider")
        key = read_context_api_key(environ=env)
        if not key:
            raise ConfigurationError("CONTEXT_DEV_API_KEY is required")
        try:
            return cls(env["GOALPULSE_PROFILE"], env["DISCOVERY_MONITORING_DATABASE_URL"], env["RABBITMQ_URL"],
                       env["GOAL_MANAGEMENT_BASE_URL"], env["DATA_EVIDENCE_BASE_URL"], UUID(env["DISCOVERY_SERVICE_ID"]),
                       env["SERVICE_JWT_SECRET"], env["SERVICE_JWT_ISSUER"], env["SERVICE_JWT_AUDIENCE"], key,
                       tuple(part.strip() for part in env.get("CONTEXT_DEV_ACCESS_POLICY_IDS", "").split(",")
                             if part.strip()),
                       Path(env.get("GOALPULSE_SCHEMA_DIRECTORY", str(SCHEMAS))),
                       int(env.get("DM_MAX_SOURCES_PER_GOAL", "3")),
                       int(env.get("DM_INITIAL_MAX_OPERATIONS_PER_DAY", "100")),
                       int(env.get("DM_MAX_ACTIVE_GOALS_PER_USER", "2")),
                       int(env.get("DM_MAX_REQUESTS_PER_USER_PER_DAY", "100")),
                       env.get("DM_OPERATOR_IDS", ""))
        except ConfigurationError:
            raise
        except (ValueError, TypeError):
            raise ConfigurationError("Invalid Discovery runtime configuration") from None


def owned_connection_factory(settings: RuntimeSettings):
    def connect():
        try:
            return psycopg.connect(settings.database_url, autocommit=False, connect_timeout=5,
                                   options="-c statement_timeout=10000 -c lock_timeout=5000")
        except Exception:
            raise RuntimeError("Discovery database unavailable") from None
    return connect


class PipelineWorker:
    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, goal_id: UUID, version: int, correlation_id: UUID, *, scan_id: UUID | None = None) -> str:
        r = self.runtime
        goals = HttpGoalReader(r.goal_http, schema_directory=r.settings.schema_directory,
                               correlation_id=correlation_id)
        goal, reason = r.scheduler.prepare(goals, goal_id, version, initial=scan_id is None)
        if reason:
            return reason
        owner = UUID(goal["owner_user_id"])
        def current():
            state = goals.runtime(goal_id)
            if state.lifecycle != "active" or state.current_version != version or state.owner_user_id != owner:
                r.registry.reconcile(goal_id, current_version=state.current_version, lifecycle=state.lifecycle)
                raise ProviderError("OBSOLETE_GOAL")
        provider = r.provider.for_owner(owner, before_request=current)
        worker = DiscoveryWorker(goals, provider, r.registry, r.evidence,
                                 schema_directory=r.settings.schema_directory,
                                 max_sources=r.settings.max_sources,
                                 plans=r.journal if scan_id is None else r.scans.plans(scan_id),
                                 resume_evidence=r.evidence.resume, scan_id=scan_id)
        result = worker.run(goal_id, version, correlation_id)
        if scan_id is None:
            r.scans.initial_finished(goal_id, version, result)
        return result


class PipelineRuntime:
    def __init__(self, settings, database, *, http_client, provider_transport, resolver,
                 registry, jobs, usage, journal, scans=None, clock=None):
        self.settings, self.database, self.http_client = settings, database, http_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.registry = registry if registry is not None else SourceRegistry(database, resolver=resolver)
        self.jobs = jobs if jobs is not None else DiscoveryJobs(database, clock=self.clock)
        self.journal = journal if journal is not None else SubmissionStore(database)
        self.usage = usage if usage is not None else UsageStore(database, max_operations=settings.max_operations,
                                                              max_user_operations=settings.max_user_operations)
        self.scans = scans if scans is not None else ScanStore(database, clock=self.clock, max_active=settings.max_active_goals)
        identity = ServiceIdentity(settings.service_secret, settings.service_issuer,
                                   settings.service_audience, settings.service_subject)
        self.goal_http = InternalHttp(settings.goal_management_url, identity, http_client)
        self.evidence_http = InternalHttp(settings.data_evidence_url, identity, http_client)
        client = HttpEvidenceSink(self.evidence_http, resolver=resolver)
        self.evidence = JournaledEvidenceSink(client, self.journal, resolver=resolver)
        # One HTTP attempt per reservation: retries belong to the durable job,
        # keeping the operation budget honest even on network/429/5xx failures.
        context = ContextProvider(settings.context_api_key, transport=provider_transport,
                                  resolver=resolver, max_attempts=1, timeout_seconds=20, clock=self.clock,
                                  access_policy_ids=settings.context_access_policy_ids)
        self.provider = GuardedProvider(context, self.usage, clock=self.clock)
        self.consumer = ActivationConsumer(ActivationInbox(self.jobs, schema_directory=settings.schema_directory))
        self.worker = PipelineWorker(self)
        self.scheduler = ScanScheduler(self.scans, lambda correlation: HttpGoalReader(
            self.goal_http, schema_directory=settings.schema_directory, correlation_id=correlation), self.registry)
        self.last_result = None
        self.app = create_app(self.registry, readiness=self.ready)

    def process_once(self) -> str:
        result = self.jobs.run_once(self.worker)
        self.last_result = getattr(self.jobs, "last_result", None)
        if result == "idle":
            result = self.scans.run_once(self.worker)
            self.last_result = self.scans.last_result
        return result

    def check_now(self, goal_id: UUID, request_id: UUID):
        principal = require_local_operator(self.settings.operator_ids)
        return self.scheduler.check_now(goal_id, request_id, principal)

    def ready(self) -> bool:
        try:
            with self.database.transaction() as cursor:
                cursor.execute("SELECT version,checksum FROM dm_schema_migrations")
                applied = dict(cursor.fetchall())
                expected = sorted((SERVICE / "migrations").glob("[0-9]*.sql"))
                if not expected:
                    return False
                return all(applied.get(p.name) == sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
                           for p in expected)
        except Exception:
            return False

    def close(self):
        self.http_client.close()


def build_runtime(settings: RuntimeSettings, *, connect=None, provider_transport=None,
                  resolver=public_addresses, http_client=None, registry=None, jobs=None,
                  usage=None, journal=None, scans=None, clock=None, migrate=True) -> PipelineRuntime:
    substitutions = (connect, provider_transport, http_client, registry, jobs, usage, journal, scans, clock)
    if settings.profile != "test" and (any(item is not None for item in substitutions) or not migrate):
        raise ConfigurationError("Runtime infrastructure substitutions require the test profile")
    database = Database(connect or owned_connection_factory(settings))
    if migrate:
        database.migrate(SERVICE / "migrations")
    client = http_client if http_client is not None else httpx.Client(follow_redirects=False, timeout=15, trust_env=False)
    return PipelineRuntime(settings, database, http_client=client, provider_transport=provider_transport,
                           resolver=resolver, registry=registry, jobs=jobs, usage=usage, journal=journal,
                           scans=scans, clock=clock)


def from_env() -> PipelineRuntime:
    return build_runtime(RuntimeSettings.from_env())


def app_from_env():
    """Private Uvicorn factory with startup migrations and HTTP-client cleanup."""
    runtime = from_env()
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            runtime.close()
    runtime.app.router.lifespan_context = lifespan
    runtime.app.state.pipeline_runtime = runtime
    return runtime.app


def open_broker(settings: RuntimeSettings):
    try:
        parameters = pika.URLParameters(settings.rabbitmq_url)
        parameters.connection_attempts, parameters.retry_delay = 2, 1
        parameters.socket_timeout, parameters.stack_timeout = 5, 10
        parameters.blocked_connection_timeout, parameters.heartbeat = 5, 30
        return pika.BlockingConnection(parameters)
    except Exception:
        raise RuntimeError("Discovery broker unavailable") from None
