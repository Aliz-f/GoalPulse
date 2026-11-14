"""Opt-in Discovery PostgreSQL checks, without substitute DB or provider access."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from goalpulse_discovery.jobs import DiscoveryJobs
from goalpulse_discovery.limits import UsageStore
from goalpulse_discovery.monitoring import MonitorStore
from goalpulse_discovery.persistence import Database
from goalpulse_discovery.registry import SourceRegistry
from goalpulse_discovery.webhooks import ReceiverConfig, WebhookInbox
from goalpulse_discovery.workflow import ActivationInbox

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]


@pytest.fixture(scope="module")
def database():
    dsn = os.environ.get("GOALPULSE_DM_TEST_DSN")
    if not dsn:
        pytest.skip("PostgreSQL NOT RUN: GOALPULSE_DM_TEST_DSN is not configured")
    import psycopg

    def connect():
        connection = psycopg.connect(dsn, autocommit=False, connect_timeout=5)
        if connection.info.dbname != "discovery_monitoring_test":
            connection.close()
            raise ValueError("Tests require the owned disposable discovery_monitoring_test DB")
        return connection

    db = Database(connect)
    db.migrate(SERVICE / "migrations")
    return db


def test_registry_shared_subscriptions_and_reconciliation(database):
    registry = SourceRegistry(database, resolver=lambda _: ["93.184.216.34"])
    url = f"https://example.com/fixture/{uuid4()}"
    first = registry.register(url + "?utm_source=one")
    second = registry.register(url + "?utm_source=two")
    assert first.source_id == second.source_id
    one, two = uuid4(), uuid4()
    registry.subscribe(one, 1, first.source_id)
    registry.subscribe(one, 1, first.source_id)
    registry.subscribe(two, 2, first.source_id)
    assert set(registry.subscriptions(first.source_id)) == {(one, 1), (two, 2)}
    registry.reconcile(one, current_version=1, lifecycle="paused")
    assert registry.subscriptions(first.source_id) == [(two, 2)]
    registry.reconcile(two, current_version=3, lifecycle="active")
    assert registry.subscriptions(first.source_id) == []
    assert database.migrate(SERVICE / "migrations") == []


def test_activation_inbox_and_webhook_run_deduplication(database):
    event = json.loads((ROOT / "contracts/examples/event.goal-activated.json").read_text(encoding="utf-8"))
    event["event_id"], event["aggregate_id"] = str(uuid4()), str(uuid4())
    event["payload"]["goal_id"] = event["aggregate_id"]
    inbox = ActivationInbox(DiscoveryJobs(database), schema_directory=ROOT / "contracts/schemas")
    assert inbox.accept(event) is True and inbox.accept(event) is False
    registry = SourceRegistry(database, resolver=lambda _: ["93.184.216.34"])
    source = registry.register(f"https://example.com/fixture/{uuid4()}")
    monitors = MonitorStore(database)
    identifier, fresh = monitors.reserve(source, "a" * 64)
    assert fresh and monitors.reserve(source, "a" * 64) == (identifier, False)
    provider_id = f"mon_{uuid4().hex}"
    monitors.created(identifier, provider_id, None, "secret/ref")
    config = ReceiverConfig(identifier, provider_id, "secret/ref")
    webhook = WebhookInbox(database)
    now, run = datetime.now(timezone.utc), f"run_{uuid4().hex}"
    event_id = f"evt_{uuid4().hex}"
    assert webhook.accept(config, event_id, run, "b" * 64, changed=True, received_at=now)
    assert not webhook.accept(config, event_id, run, "b" * 64, changed=True, received_at=now)
    assert webhook.accept(config, f"evt_{uuid4().hex}", run, "c" * 64, changed=True, received_at=now)
    with database.transaction() as cursor:
        cursor.execute("SELECT count(*) FROM source_check_jobs WHERE monitor_id=%s AND provider_run_id=%s",
                       (str(identifier), run))
        assert cursor.fetchone()[0] == 1


def test_quota_unknown_cost_keeps_budget_reserved(database):
    # Isolate this run's accounting on a random far-future date inside the test DB.
    day_offset = uuid4().int % 100000
    now = datetime(3000, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)
    usage = UsageStore(database, max_operations=2, max_credits=6)
    operation = usage.reserve(uuid4().hex.ljust(64, "0"), now, ttl_seconds=600)
    usage.finish(operation, credits=None, error_code="PROVIDER_FAILURE", now=now)
    health = usage.health(now.date())
    assert health["direct_operations"] == 1 and health["unknown_usage_operations"] == 1
    assert health["reserved_credits"] == 3 and health["pending_operations"] == 0
