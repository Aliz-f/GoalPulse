"""Opt-in PostgreSQL acceptance. Never substitutes another DB engine.

Requires a disposable service-owned database named data_evidence_test, psycopg 3,
and GOALPULSE_DE_TEST_DSN configured locally. Tests append randomly identified
fixtures. No database/schema drops, truncation, or shared-service access occurs.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from goalpulse_evidence.ledger import Capture, EvidenceLedger
from goalpulse_evidence.migrations import apply_migrations
from goalpulse_evidence.postgres import PostgresRepository
from goalpulse_evidence.reads import EvidenceReads

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]


@pytest.fixture(scope="module")
def database():
    dsn = os.environ.get("GOALPULSE_DE_TEST_DSN")
    if not dsn:
        pytest.skip("PostgreSQL NOT RUN: GOALPULSE_DE_TEST_DSN is not configured")
    # A configured environment must fail if its driver is missing, rather than
    # reporting a false green integration job with everything skipped.
    import psycopg

    def connect():
        connection = psycopg.connect(dsn, autocommit=False, connect_timeout=5)
        if connection.info.dbname != "data_evidence_test":
            connection.close()
            raise ValueError("Integration tests require the dedicated data_evidence_test DB")
        return connection

    connection = connect()
    try:
        apply_migrations(connection, SERVICE / "migrations")
    finally:
        connection.close()
    return connect


def candidate():
    observation = json.loads((ROOT / "contracts/examples/observation.unknown.fixture.json").read_text(encoding="utf-8"))
    observation["source_id"] = str(uuid4())
    observation["observation_id"] = str(uuid4())
    capture = Capture(source_id=UUID(observation["source_id"]), source_url=observation["source_url"],
                      provider="fixture", extraction_method="fixture",
                      fetched_at=datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
                      provider_observed_at=datetime(2026, 11, 14, 10, tzinfo=timezone.utc))
    return observation, capture


def test_postgres_migrations_and_atomic_snapshot_outbox(database):
    connection = database()
    try:
        assert apply_migrations(connection, SERVICE / "migrations") == []
    finally:
        connection.close()
    observation, capture = candidate()
    service = EvidenceLedger(PostgresRepository(database), schema_directory=ROOT / "contracts/schemas")
    first = service.record(observation, capture, correlation_id=uuid4())
    duplicate = service.record(observation, capture, correlation_id=uuid4())
    assert duplicate.duplicate and duplicate.observation_id == first.observation_id
    with database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM observations WHERE observation_id = %s", (first.observation_id,))
            assert service.observation_validator.is_valid(cursor.fetchone()[0])
            cursor.execute("SELECT payload FROM outbox WHERE observation_id = %s", (first.observation_id,))
            assert service.event_validator.is_valid(cursor.fetchone()[0])


def test_postgres_missing_price_and_immutable_snapshot(database):
    import psycopg

    observation, capture = candidate()
    service = EvidenceLedger(PostgresRepository(database), schema_directory=ROOT / "contracts/schemas")
    invalid = deepcopy(observation)
    del invalid["unit_price_eur_cents"]
    rejected = service.record(invalid, capture, correlation_id=uuid4())
    assert rejected.reason == "MISSING_UNIT_PRICE"
    valid = service.record(observation, capture, correlation_id=uuid4())
    for statement in ("UPDATE observations SET source_url = source_url WHERE observation_id = %s",
                      "DELETE FROM observations WHERE observation_id = %s"):
        with pytest.raises(psycopg.Error) as error:
            with database() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement, (valid.observation_id,))
        assert error.value.sqlstate == "55000"


def test_postgres_outbox_failure_rolls_back_observation_and_attempt(database):
    observation, capture = candidate()
    repository = PostgresRepository(database)

    # Fail after the real observation and provenance SQL has executed, so the
    # actual DB transaction must undo those writes without invoking RabbitMQ.
    class FailingRepository:
        @contextmanager
        def transaction(self):
            with repository.transaction() as tx:
                def fail(*args):
                    raise OSError("Injected before outbox write")
                tx.enqueue = fail
                yield tx

    service = EvidenceLedger(FailingRepository(), schema_directory=ROOT / "contracts/schemas")
    with pytest.raises(OSError, match="Injected"):
        service.record(observation, capture, correlation_id=uuid4())
    with database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM observations WHERE source_id = %s", (str(capture.source_id),))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM extraction_attempts WHERE source_id = %s", (str(capture.source_id),))
            assert cursor.fetchone()[0] == 0


def test_postgres_concurrent_replays_share_one_observation_and_event(database):
    observation, capture = candidate()

    def submit(index):
        service = EvidenceLedger(PostgresRepository(database), schema_directory=ROOT / "contracts/schemas")
        copy = deepcopy(observation)
        copy["observation_id"] = str(uuid4())
        return service.record(copy, capture, correlation_id=uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(submit, range(2)))
    assert first.observation_id == second.observation_id
    assert sum(result.duplicate for result in (first, second)) == 1
    with database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM outbox WHERE observation_id = %s", (first.observation_id,))
            assert cursor.fetchone()[0] == 1


def test_postgres_check_audit_is_immutable_and_source_read_uses_derived_snapshot(database):
    import psycopg

    from test_check_postgres import audit_record

    observation, capture = candidate()
    repository = PostgresRepository(database)
    service = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas")
    original = service.record(observation, capture, correlation_id=uuid4())
    derived = deepcopy(observation)
    derived["observation_id"] = str(uuid4())
    derived["evidence"]["reliability"] = "checked"
    audit = audit_record(original.observation_id, derived["observation_id"])
    audit["proof"] = {"claims": {"condition": "new", "shipping_eur_cents": None}}
    # Randomize proof key so repeated opt-in integration runs remain append-only.
    audit["proof_digest"] = uuid4().hex + uuid4().hex
    with repository.transaction() as tx:
        tx.lock_source(str(capture.source_id))
        assert tx.get_observation(original.observation_id) == observation
        assert tx.get_capture(original.observation_id) == capture
        result_id, created = tx.add_observation(derived, tx.get_attempt_id(original.observation_id),
                                               uuid4().hex + uuid4().hex, audit["checked_at"])
        assert created and result_id == derived["observation_id"]
        tx.add_provenance(result_id, capture)
        tx.add_check(audit)
        assert tx.get_check(audit["check_id"]) == audit
        assert tx.proof_used(audit["proof_digest"])
        assert tx.current_claims(str(capture.source_id), capture.observed_at()) == [audit["proof"]["claims"]]

    reads = EvidenceReads(database, schema_directory=ROOT / "contracts/schemas")
    assert reads.by_id(UUID(original.observation_id)) == observation
    assert reads.by_source(capture.source_id) == [derived]
    for sql in ("UPDATE evidence_checks SET decision = decision WHERE check_id = %s",
                "DELETE FROM evidence_checks WHERE check_id = %s"):
        with pytest.raises(psycopg.Error) as error:
            with database() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, (audit["check_id"],))
        assert error.value.sqlstate == "55000"

    replay = dict(audit, check_id=str(uuid4()))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with repository.transaction() as tx:
            tx.add_check(replay)

    # A newly ingested contradictory candidate suppresses a checked source read
    # immediately, even before the internal checker appends a dispute decision.
    conflict = deepcopy(observation)
    conflict["observation_id"] = str(uuid4())
    conflict["unit_price_eur_cents"] += 1
    accepted = service.record(conflict, capture, correlation_id=uuid4())
    assert accepted.accepted
    assert reads.by_source(capture.source_id) == [conflict]
    assert reads.by_id(UUID(derived["observation_id"])) == derived
