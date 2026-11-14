"""Exact staged HTTP replay using a transactional SQL-recording fixture.

This verifies orchestration and bound values, not execution by PostgreSQL.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

import pytest

from goalpulse_discovery.context_provider import Output, ProviderError, ScrapeResult
from goalpulse_discovery.deliveries import JournaledEvidenceSink, SubmissionStore
from goalpulse_discovery.registry import Source

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


class DatabaseFixture:
    def __init__(self):
        self.state = {"plans": {}, "deliveries": {}}
        self.statements, self.events = [], []
        self.fail_complete = False

    @contextmanager
    def transaction(self):
        pending = deepcopy(self.state)
        yield CursorFixture(self, pending)
        self.state = deepcopy(pending)
        self.events.append("commit")


class CursorFixture:
    def __init__(self, database, state):
        self.database, self.state, self.row = database, state, None

    def execute(self, sql, params=()):
        self.database.statements.append((sql, params))
        if sql.startswith("INSERT INTO discovery_plans"):
            self.state["plans"].setdefault(params[:2], json.loads(params[2]))
        elif sql.startswith("SELECT urls FROM discovery_plans"):
            value = self.state["plans"].get(params)
            self.row = (deepcopy(value),) if value is not None else None
        elif sql.startswith("INSERT INTO extraction_deliveries"):
            self.state["deliveries"].setdefault(params[0], {
                "submission": json.loads(params[2]), "receipt": None, "submission_hash": params[3],
                "capture_metadata": json.loads(params[4]),
            })
            self.database.events.append("stage")
        elif sql.startswith("SELECT submission,receipt,submission_hash"):
            item = self.state["deliveries"].get(params[0])
            self.row = (item["submission"], item["receipt"], item["submission_hash"],
                        item["capture_metadata"]) if item else None
        elif sql.startswith("SELECT submission,receipt,capture_metadata"):
            item = self.state["deliveries"].get(params[0])
            self.row = (item["submission"], item["receipt"], item["capture_metadata"]) if item else None
        elif sql.startswith("SELECT submission,receipt"):
            item = self.state["deliveries"].get(params[0])
            self.row = (item["submission"], item["receipt"]) if item else None
        elif sql.startswith("UPDATE extraction_deliveries"):
            if self.database.fail_complete:
                raise OSError("Injected receipt transaction failure")
            item = self.state["deliveries"].get(params[1])
            if item is not None and item["receipt"] is None:
                item["receipt"] = json.loads(params[0])
            self.database.events.append("complete")
        else:
            pytest.fail("Unexpected SQL operation in transport fixture")

    def fetchone(self):
        return deepcopy(self.row)


def source_and_result():
    source = Source(uuid4(), "https://seller.example/product", "https://seller.example/product", "seller.example")
    result = ScrapeResult(
        source.canonical_url, "recorded-request", NOW, 0, NOW,
        Output(True, {"isProductPage": True, "product": {
            "name": "Recorded product", "price": "49.95", "currency": "EUR", "availability": "in_stock",
        }}), Output(True, "Recorded seller data; no assumptions about shipping or tax"),
    )
    return source, result


def submission():
    source, result = source_and_result()
    from goalpulse_discovery.adapters import extraction_submission

    return extraction_submission(source, result, attempt_id=uuid4(), correlation_id=uuid4(),
                                 resolver=lambda _host: ["93.184.216.34"])


class ClientFixture:
    def __init__(self, database, *, fail=0, receipt=None):
        self.database, self.fail = database, fail
        self.receipt = receipt or {"accepted": True, "observation_id": str(uuid4()), "reason": None}
        self.sent = []

    def send_submission(self, body):
        assert self.database.state["deliveries"][body["attempt_id"]]["submission"] == body
        self.database.events.append("http")
        self.sent.append(deepcopy(body))
        if self.fail:
            self.fail -= 1
            raise ProviderError("NETWORK_ERROR")
        return deepcopy(self.receipt)


def test_submission_is_committed_before_http_and_receipt_is_reused_without_http():
    database = DatabaseFixture()
    store, client = SubmissionStore(database), ClientFixture(database)
    sink = JournaledEvidenceSink(client, store, resolver=lambda _host: ["93.184.216.34"])
    source, result = source_and_result()
    attempt, correlation = uuid4(), uuid4()
    receipt = sink.submit(source, result, attempt_id=attempt, correlation_id=correlation)
    assert receipt == client.receipt
    assert database.events.index("stage") < database.events.index("commit") < database.events.index("http")
    assert len(client.sent) == 1
    recorded_capture = database.state["deliveries"][str(attempt)]["capture_metadata"]
    assert recorded_capture["fetched_at"] == "2026-11-14T12:00:00Z"
    assert recorded_capture["provider_request_id"] == "recorded-request"
    assert recorded_capture["source_url"] == source.canonical_url
    assert "capture_metadata" not in client.sent[0]
    assert sink.resume(attempt) == receipt
    assert sink.submit(source, result, attempt_id=attempt, correlation_id=correlation) == receipt
    assert len(client.sent) == 1


def test_ambiguous_http_failure_replays_exact_committed_body_without_new_capture():
    database = DatabaseFixture()
    store, client = SubmissionStore(database), ClientFixture(database, fail=1)
    sink = JournaledEvidenceSink(client, store, resolver=lambda _host: ["93.184.216.34"])
    source, result = source_and_result()
    attempt, correlation = uuid4(), uuid4()
    with pytest.raises(ProviderError, match="NETWORK_ERROR"):
        sink.submit(source, result, attempt_id=attempt, correlation_id=correlation)
    pending = store.get(attempt)
    assert pending["receipt"] is None
    assert sink.resume(attempt) == client.receipt
    assert client.sent[0] == client.sent[1] == pending["submission"]
    assert client.sent[1]["correlation_id"] == str(correlation)
    assert len(database.state["deliveries"]) == 1


def test_receipt_transaction_failure_keeps_original_submission_available_for_replay():
    database = DatabaseFixture()
    database.fail_complete = True
    store, client = SubmissionStore(database), ClientFixture(database)
    sink = JournaledEvidenceSink(client, store, resolver=lambda _host: ["93.184.216.34"])
    source, result = source_and_result()
    attempt = uuid4()
    with pytest.raises(OSError, match="Injected"):
        sink.submit(source, result, attempt_id=attempt, correlation_id=uuid4())
    assert store.get(attempt)["receipt"] is None
    database.fail_complete = False
    assert sink.resume(attempt) == client.receipt
    assert client.sent[0] == client.sent[1]


def test_durable_failure_receipt_is_terminal_and_missing_attempt_has_no_side_effect():
    database = DatabaseFixture()
    receipt = {"accepted": False, "observation_id": None, "reason": "MISSING_UNIT_PRICE"}
    client, store = ClientFixture(database, receipt=receipt), SubmissionStore(database)
    sink = JournaledEvidenceSink(client, store, resolver=lambda _host: ["93.184.216.34"])
    assert sink.resume(uuid4()) is None and not client.sent
    source, result = source_and_result()
    attempt = uuid4()
    assert sink.submit(source, result, attempt_id=attempt, correlation_id=uuid4()) == receipt
    assert sink.resume(attempt) == receipt and len(client.sent) == 1


def test_attempt_reuse_cannot_change_immutable_submission_and_sql_is_parameterized():
    database, body = DatabaseFixture(), submission()
    body["extracted"]["evidence_excerpt"] = "Untrusted '); DROP TABLE extraction_deliveries; --"
    store = SubmissionStore(database)
    stored = store.put(body)
    stored["submission"]["extracted"]["evidence_excerpt"] = "changed after boundary"
    assert store.get(UUID(body["attempt_id"]))["submission"] == body
    for sql, _params in database.statements:
        assert body["extracted"]["evidence_excerpt"] not in sql
    changed = deepcopy(body)
    changed["extracted"]["unit_price_eur_cents"] += 1
    with pytest.raises(ValueError, match="Attempt identity reused"):
        store.put(changed)
    assert store.get(UUID(body["attempt_id"]))["submission"] == body
    receipt = {"accepted": True, "observation_id": str(uuid4()), "reason": None}
    store.complete(UUID(body["attempt_id"]), receipt)
    store.complete(UUID(body["attempt_id"]), {**receipt, "observation_id": str(uuid4())})
    assert store.get(UUID(body["attempt_id"]))["receipt"] == receipt
    assert all("receipt IS NULL" in sql for sql, _params in database.statements if sql.startswith("UPDATE"))


def test_initial_source_plan_is_immutable_and_loaded_by_goal_version():
    database, goal_id = DatabaseFixture(), uuid4()
    store = SubmissionStore(database)
    assert store.load_plan(goal_id, 1) is None
    urls = ["https://seller.example/product"]
    saved = store.save_plan(goal_id, 1, urls)
    saved.append("https://injected.example")
    assert store.save_plan(goal_id, 1, ["https://other.example/product"]) == urls
    assert store.load_plan(goal_id, 1) == urls and store.load_plan(goal_id, 2) is None


def test_private_capture_provenance_survives_same_submission_replay_unchanged():
    database, body = DatabaseFixture(), submission()
    store = SubmissionStore(database)
    original_capture = {"requested_at": "2026-11-14T11:59:59Z", "fetched_at": "2026-11-14T12:00:00Z",
                        "provider_observed_at": "2026-11-14T11:59:00Z", "cache_age_ms": 60000,
                        "provider_request_id": "recorded-request"}
    store.put(body, capture_metadata=original_capture)
    with pytest.raises(ValueError, match="different capture proof"):
        store.put(body, capture_metadata={**original_capture, "requested_at": "2026-11-14T13:00:00Z"})
    assert database.state["deliveries"][body["attempt_id"]]["capture_metadata"] == original_capture


@pytest.mark.parametrize("version,urls", [(True, []), (1.0, []), (0, []), (1, [None]),
                                        (1, ["https://seller.example"] * 6)])
def test_invalid_source_plan_does_not_touch_database(version, urls):
    database = DatabaseFixture()
    with pytest.raises(ValueError):
        SubmissionStore(database).save_plan(uuid4(), version, urls)
    assert not database.statements
