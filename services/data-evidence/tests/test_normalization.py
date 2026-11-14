from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from goalpulse_evidence.ledger import Capture, EvidenceLedger
from goalpulse_evidence.normalization import FactIngestor, NormalizationError, ProductFacts, normalize
from fake_evidence_repository import MemoryRepository

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def capture():
    return Capture(uuid4(), "https://example.com/product", "fixture", "fixture", NOW, cache_age_ms=0)


@pytest.fixture
def facts():
    return ProductFacts("Synthetic carton", "3.00", "EUR", "L", "1.5", "L")


@pytest.mark.parametrize("unit,quantity,package_unit,expected", [
    ("L", "1.5", "L", 200), ("L", "1500", "ml", 200),
    ("kg", "500", "g", 600), ("pcs", "2", "pcs", 150),
])
def test_confirmed_package_conversion(facts, capture, unit, quantity, package_unit, expected):
    result = normalize(replace(facts, selling_unit=unit, package_quantity=quantity,
                               package_unit=package_unit), capture)
    assert result["unit_price_eur_cents"] == expected


@pytest.mark.parametrize("changes,reason", [
    ({"package_quantity": None}, "UNKNOWN_PACKAGING"),
    ({"package_unit": "kg"}, "UNKNOWN_PACKAGING"),
    ({"package_quantity": "0"}, "UNKNOWN_PACKAGING"),
    ({"price_eur": "1.00", "package_quantity": "3"}, "NON_INTEGRAL_PRICE"),
    ({"price_eur": None}, "MISSING_UNIT_PRICE"),
    ({"price_eur": 3.0}, "INVALID_MONEY"),
    ({"price_eur": "3,00"}, "INVALID_MONEY"),
    ({"price_eur": "2.00000000000000000000000000000001", "price_is_per_unit": True}, "NON_INTEGRAL_PRICE"),
    ({"currency": None}, "UNSUPPORTED_CURRENCY"),
    ({"currency": "USD"}, "UNSUPPORTED_CURRENCY"),
    ({"delivery_country": "DE"}, "UNSUPPORTED_DESTINATION"),
    ({"tax_treatment": "inclusive", "additional_tax_eur": "0.50"}, "CONFLICTING_TAX"),
])
def test_unrepresentable_facts_create_only_failure_ledger(facts, capture, changes, reason):
    repository = MemoryRepository()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    ingestor = FactIngestor(ledger)
    invalid = replace(facts, **changes)
    result = ingestor.record(invalid, capture, correlation_id=uuid4())
    replay = ingestor.record(invalid, capture, correlation_id=uuid4())
    assert result.reason == reason and not result.accepted
    assert replay.duplicate and replay.attempt_id == result.attempt_id
    assert len(repository.state["attempts"]) == 1
    assert not repository.state["observations"] and not repository.state["outbox"]


def test_inclusive_additional_and_unknown_tax(facts, capture):
    assert normalize(replace(facts, tax_treatment="inclusive"), capture)["tax_eur_cents"] == 0
    assert normalize(replace(facts, tax_treatment="additional", additional_tax_eur="0.40"), capture)["tax_eur_cents"] == 40
    result = normalize(facts, capture)
    assert result["tax_eur_cents"] is None and result["shipping_eur_cents"] is None
    assert result["availability"] == "unknown"
    assert normalize(replace(facts, shipping_eur="1.00"), capture)["shipping_eur_cents"] is None
    assert normalize(replace(facts, delivery_to_fi="yes", shipping_eur="1.00"), capture)["shipping_eur_cents"] == 100


def test_explicit_model_preserved_and_excerpt_cannot_supply_missing_fields(facts, capture):
    facts = replace(facts, model="Explicit extracted model", excerpt="condition=new; attributes: fast; free shipping")
    result = normalize(facts, capture)
    assert result["model"] == facts.model
    assert "condition" not in result and "attributes" not in result
    assert result["product_name"] == facts.product_name and result["unit_price_eur_cents"] == 200
    assert result["evidence"]["reliability"] == "unverified"
    assert normalize(replace(facts, model=None), capture)["model"] is None


def test_valid_normalization_stores_strict_observation_and_event(facts, capture):
    repository = MemoryRepository()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    result = FactIngestor(ledger).record(facts, capture, correlation_id=uuid4())
    assert result.accepted
    stored = next(iter(repository.state["observations"].values()))
    assert ledger.observation_validator.is_valid(stored)
    assert stored["evidence"]["reliability"] == "unverified"
    disputed = normalize(replace(facts, source_disputed=True), capture)
    assert disputed["evidence"]["reliability"] == "disputed"
