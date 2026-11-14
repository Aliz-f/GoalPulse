"""Seller-proof persistence boundary tests; no live database is used."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from goalpulse_evidence.checking import SellerRepresentation
from goalpulse_evidence.ledger import AttemptConflict
from goalpulse_evidence.proofs import PostgresProofStore


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


class ConnectionDouble:
    autocommit = False

    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []
        self.committed = self.rolled_back = self.closed = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def representation():
    return SellerRepresentation(
        source_url="https://shop.example/products/model-128", fetched_at=NOW,
        cache_age_ms=0, provider_request_id="context-request-1", json_ld="{}",
        resolved_addresses=("93.184.216.34",), access_policy_ids=("pol_demo_allowlist",),
    )


def test_proof_insert_first_binds_to_exact_durable_attempt():
    attempt_id, source_id, proof = uuid4(), uuid4(), representation()
    connection = ConnectionDouble([
        (source_id, proof.source_url, proof.fetched_at, proof.cache_age_ms, proof.provider_request_id),
        (attempt_id,),
    ])
    PostgresProofStore(lambda: connection).put(attempt_id, source_id, proof)
    assert connection.committed and not connection.rolled_back and connection.closed
    assert "FROM extraction_attempts" in connection.statements[0][0]
    insert_sql, params = connection.statements[1]
    assert "INSERT INTO evidence_capture_proofs" in insert_sql
    assert params[:2] == (str(attempt_id), proof.source_url)
    assert proof.json_ld not in insert_sql


def test_proof_mismatch_rolls_back_before_insert():
    attempt_id, source_id, proof = uuid4(), uuid4(), representation()
    connection = ConnectionDouble([
        (source_id, "https://shop.example/different", proof.fetched_at,
         proof.cache_age_ms, proof.provider_request_id),
    ])
    with pytest.raises(AttemptConflict, match="does not match"):
        PostgresProofStore(lambda: connection).put(attempt_id, source_id, proof)
    assert connection.rolled_back and not connection.committed and connection.closed
    assert len(connection.statements) == 1


def test_attempt_replay_requires_identical_proof_hash():
    attempt_id, source_id, proof = uuid4(), uuid4(), representation()
    connection = ConnectionDouble([
        (source_id, proof.source_url, proof.fetched_at, proof.cache_age_ms, proof.provider_request_id),
        None,
        ("different-hash",),
    ])
    with pytest.raises(AttemptConflict, match="different seller proof"):
        PostgresProofStore(lambda: connection).put(attempt_id, source_id, proof)
    assert connection.rolled_back and connection.closed
