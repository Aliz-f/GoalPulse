"""Opt-in real SQL checks for initial discovery and immutable HTTP intents."""

from copy import deepcopy
from uuid import uuid4

import pytest

from goalpulse_discovery.deliveries import SubmissionStore
from goalpulse_discovery.registry import SourceRegistry
from test_discovery_postgres_live import database


def test_postgres_initial_plan_and_submission_intent_are_immutable(database):
    import psycopg

    source = SourceRegistry(database, resolver=lambda _: ["93.184.216.34"]).register(
        f"https://example.com/recorded/{uuid4()}")
    store = SubmissionStore(database)
    goal_id, attempt_id = uuid4(), uuid4()
    assert store.save_plan(goal_id, 1, [source.canonical_url]) == [source.canonical_url]
    assert store.save_plan(goal_id, 1, []) == [source.canonical_url]
    body = {"attempt_id": str(attempt_id), "source_id": str(source.source_id),
            "source_url": source.canonical_url, "provider": "context_dev", "extraction_method": "scrape",
            "provider_request_id": "recorded-test-request", "fetched_at": "2026-11-14T12:00:00Z",
            "cache_age_ms": 0, "correlation_id": str(uuid4()),
            "extracted": {"product_name": None, "selling_unit": None, "unit_price_eur_cents": None,
                          "availability": "unknown", "delivery_to_fi": "unknown", "shipping_eur_cents": None,
                          "tax_eur_cents": None, "evidence_excerpt": "", "evidence_reliability": "unverified"}}
    capture_metadata = {"requested_at": "2026-11-14T12:00:00Z"}
    record = store.put(body, capture_metadata=capture_metadata)
    assert record == {"submission": body, "receipt": None,
                      "capture_metadata": capture_metadata}
    assert store.get(attempt_id) == record
    changed = deepcopy(body)
    changed["extracted"]["product_name"] = "different assertion"
    with pytest.raises(ValueError):
        store.put(changed)
    receipt = {"accepted": False, "observation_id": None, "reason": "MISSING_UNIT_PRICE"}
    store.complete(attempt_id, receipt)
    store.complete(attempt_id, {"accepted": True})  # First durable receipt wins.
    assert store.get(attempt_id) == {"submission": body, "receipt": receipt,
                                     "capture_metadata": capture_metadata}
    with pytest.raises(psycopg.Error) as error:
        with database.transaction() as cursor:
            cursor.execute("UPDATE extraction_deliveries SET submission='{}'::jsonb WHERE attempt_id=%s",
                           (str(attempt_id),))
    assert error.value.sqlstate == "55000"
    assert store.get(attempt_id)["submission"] == body
