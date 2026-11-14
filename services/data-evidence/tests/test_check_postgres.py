"""Checker persistence boundary tests; live SQL coverage is explicitly opt-in."""

from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from goalpulse_evidence.postgres import PostgresTransaction


class CursorDouble:
    def __init__(self, rows=()):
        self.rows, self.statements = list(rows), []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def audit_record(original, result=None):
    return {
        "check_id": str(uuid4()), "original_observation_id": original,
        "result_observation_id": result, "decision": "checked" if result else "unverified",
        "reasons": ["SOURCE_CROSS_CHECKED"] if result else ["MISSING_PROOF"],
        "policy_version": "icp09-v1", "normalizer_version": "seller-jsonld-v1",
        "request_fingerprint": "a" * 64, "input_digest": "b" * 64,
        "proof_digest": "c" * 64 if result else None,
        "proof": {"fragment": "untrusted ' ; DROP TABLE observations; --"},
        "correlation_id": str(uuid4()), "checked_at": "2026-11-14T12:00:00Z",
    }


def test_check_audit_uses_bound_values_and_no_observation_update():
    cursor = CursorDouble()
    audit = audit_record(str(uuid4()))
    PostgresTransaction(cursor).add_check(audit)
    sql, params = cursor.statements[0]
    assert "INSERT INTO evidence_checks" in sql and "UPDATE" not in sql
    assert audit["proof"]["fragment"] not in sql
    assert json.loads(params[10]) == audit["proof"]
    assert params[0] == audit["check_id"] and params[-1] == audit["checked_at"]


def test_check_audit_round_trip_returns_stable_uuid_and_time_strings():
    original, result = str(uuid4()), str(uuid4())
    audit = audit_record(original, result)
    values = list(audit.values())
    for index in (0, 1, 2, 11):
        values[index] = UUID(values[index])
    values[12] = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    assert PostgresTransaction(CursorDouble([tuple(values)])).get_check(audit["check_id"]) == audit


def test_capture_reconstruction_preserves_original_time_cache_and_normalizer():
    source_id = uuid4()
    fetched = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    observed = datetime(2026, 11, 14, 11, 58, tzinfo=timezone.utc)
    cursor = CursorDouble([(source_id, "https://merchant.example/product", "context_dev", "scrape",
                            fetched, "request-1", observed, 120000, "http-v1", {"model": "data.model"})])
    capture = PostgresTransaction(cursor).get_capture(str(uuid4()))
    assert capture.source_id == source_id and capture.fetched_at == fetched
    assert capture.observed_at() == observed and capture.cache_age_ms == 120000
    assert capture.parser_version == "http-v1" and capture.field_sources == {"model": "data.model"}


def test_source_query_excludes_superseded_without_relabeling_payload():
    cursor = CursorDouble([({"observation_id": "stored"},)])
    source = str(uuid4())
    since = datetime(2026, 11, 14, 11, 30, tzinfo=timezone.utc)
    assert PostgresTransaction(cursor).source_observations(source, since) == [{"observation_id": "stored"}]
    sql, params = cursor.statements[0]
    assert "NOT EXISTS" in sql and "c.result_observation_id IS NOT NULL" in sql
    assert params == (source, since)


def test_current_claims_only_returns_object_claims_for_current_equal_time_checks():
    cursor = CursorDouble([({"condition": "new"},), (None,), (["not an object"],)])
    source, observed = str(uuid4()), datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    assert PostgresTransaction(cursor).current_claims(source, observed) == [{"condition": "new"}]
    sql, params = cursor.statements[0]
    assert params == (source, observed)
    assert "c.decision = 'checked'" in sql and "o.observed_at = %s" in sql
    assert "newer.original_observation_id = o.observation_id" in sql
