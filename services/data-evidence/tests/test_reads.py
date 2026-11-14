from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import ValidationError

from goalpulse_evidence.reads import EvidenceReads

ROOT = Path(__file__).resolve().parents[3]


class ReadConnection:
    def __init__(self, rows):
        self.rows, self.closed, self.rolled_back = rows, False, False
        self.query = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.query = (sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def observation():
    return json.loads((ROOT / "contracts/examples/observation.unknown.fixture.json").read_text(encoding="utf-8"))


def test_id_read_returns_strict_snapshot_and_closes_transaction():
    payload = observation()
    connection = ReadConnection([(payload,)])
    reads = EvidenceReads(lambda: connection, schema_directory=ROOT / "contracts/schemas")
    found = reads.by_id(UUID(payload["observation_id"]))
    assert found == payload and connection.closed and connection.rolled_back
    found["product_name"] = "mutated"
    assert payload["product_name"] != "mutated"
    assert str(UUID(payload["observation_id"])) in connection.query[1]


def test_missing_snapshot_returns_none():
    connection = ReadConnection([])
    assert EvidenceReads(lambda: connection, schema_directory=ROOT / "contracts/schemas").by_id(uuid4()) is None


def test_bad_persisted_snapshot_is_not_exposed():
    invalid = observation()
    invalid["condition"] = "new"
    connection = ReadConnection([(invalid,)])
    with pytest.raises(ValidationError):
        EvidenceReads(lambda: connection, schema_directory=ROOT / "contracts/schemas").by_id(uuid4())
    assert connection.closed


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_query_bounds_rejected_before_database_access(limit):
    def no_connection():
        pytest.fail("Invalid request must not query a database")
    with pytest.raises(ValueError):
        EvidenceReads(no_connection, schema_directory=ROOT / "contracts/schemas").by_source(uuid4(), limit=limit)


def test_source_query_requests_stable_newest_order_and_limit():
    payload = observation()
    connection = ReadConnection([(payload,)])
    reads = EvidenceReads(lambda: connection, schema_directory=ROOT / "contracts/schemas")
    assert reads.by_source(UUID(payload["source_id"]), limit=5) == [payload]
    assert "ORDER BY observed_at DESC, created_at DESC, observation_id DESC LIMIT %s" in connection.query[0]
    assert "c.original_observation_id = o.observation_id" in connection.query[0]
    assert "c.result_observation_id IS NOT NULL" in connection.query[0]
    assert "o.reliability <> 'checked' OR NOT EXISTS" in connection.query[0]
    assert "x.observed_at >= o.observed_at" in connection.query[0]
    for field in ("product_name", "model", "brand", "unit_price_eur_cents", "availability",
                  "delivery_to_fi", "shipping_eur_cents", "tax_eur_cents"):
        assert f"x.payload->'{field}' IS DISTINCT FROM o.payload->'{field}'" in connection.query[0]
    assert "x.payload->>'shipping_eur_cents' IS NOT NULL" in connection.query[0]
    assert "x.payload->>'delivery_to_fi' <> 'unknown'" in connection.query[0]
    assert connection.query[1] == (payload["source_id"], 5)
