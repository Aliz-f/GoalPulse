"""Frozen-v1 evidence limitations: excerpts never create structured facts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from goalpulse_evidence.ledger import Capture, EvidenceLedger
from goalpulse_evidence.normalization import FactIngestor, ProductFacts
from fake_evidence_repository import MemoryRepository

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("excerpt", ["new", "condition=new", "tax included; shipping free; in stock", "model=claimed in prose"])
def test_excerpt_is_not_structured_proof_and_preserves_unknowns(excerpt):
    repository = MemoryRepository()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    facts = ProductFacts("Synthetic fixture item", "6.00", "EUR", "pcs", "1", "pcs", excerpt=excerpt)
    capture = Capture(uuid4(), "https://example.com/fixture", "fixture", "fixture", NOW, cache_age_ms=0)
    assert FactIngestor(ledger).record(facts, capture, correlation_id=uuid4()).accepted
    stored = next(iter(repository.state["observations"].values()))
    assert stored["availability"] == "unknown" and stored["delivery_to_fi"] == "unknown"
    assert stored["tax_eur_cents"] is None and stored["shipping_eur_cents"] is None
    assert stored["model"] is None and stored["unit_price_eur_cents"] == 600
    assert stored["product_name"] == facts.product_name
    assert "condition" not in stored and "attributes" not in stored
    assert stored["evidence"]["reliability"] == "unverified"


def test_cached_disputed_fact_stays_old_disputed_and_strict():
    repository = MemoryRepository()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    facts = ProductFacts("Synthetic fixture item", "6.00", "EUR", "pcs", "1", "pcs", source_disputed=True)
    capture = Capture(uuid4(), "https://example.com/fixture", "fixture", "fixture", NOW, cache_age_ms=86_400_000)
    assert FactIngestor(ledger).record(facts, capture, correlation_id=uuid4()).accepted
    stored = next(iter(repository.state["observations"].values()))
    assert stored["observed_at"] == "2026-11-13T12:00:00Z"
    assert stored["evidence"]["reliability"] == "disputed"
    assert ledger.observation_validator.is_valid(stored)
    for extra in ({"condition": "new"}, {"attributes": {"memory": "large"}}):
        assert not ledger.observation_validator.is_valid({**stored, **extra})
