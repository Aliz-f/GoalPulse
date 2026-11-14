"""ICP-09 acceptance using synthetic recorded seller JSON-LD, without network calls.

The source representation and extracted assertion travel through separate inputs.
These fixtures demonstrate checker behavior, not a real merchant or live stock.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from fake_evidence_repository import MemoryRepository
from goalpulse_evidence.checking import EvidenceChecker, SellerRepresentation
from goalpulse_evidence.ledger import AttemptConflict, Capture, EvidenceLedger, timestamp


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
URL = "https://shop.example/products/model-128"


class RecordedRepresentations:
    def __init__(self, representation):
        self.representation = representation
        self.calls = 0

    def load(self, observation_id):
        assert isinstance(observation_id, UUID)
        self.calls += 1
        return self.representation


class Scenario:
    def __init__(self, *, observation_changes=None, capture_changes=None, source_url=URL):
        self.time = NOW
        self.repository = MemoryRepository()
        self.ledger = EvidenceLedger(
            self.repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: self.time,
        )
        self.capture = Capture(
            source_id=uuid4(), source_url=source_url, provider="context_dev",
            extraction_method="scrape", fetched_at=NOW, provider_request_id="recorded-request-1",
            cache_age_ms=0, parser_version="recorded-extractor-v1",
            field_sources={"model": "extract:model", "price": "extract:offer-price"},
        )
        if capture_changes:
            self.capture = replace(self.capture, **capture_changes)
        self.observation = {
            "observation_id": str(uuid4()), "source_id": str(self.capture.source_id),
            "source_url": source_url, "provider": self.capture.provider,
            "extraction_method": self.capture.extraction_method,
            "provider_request_id": self.capture.provider_request_id,
            "observed_at": timestamp(self.capture.observed_at()),
            "product_name": "Synthetic recorded widget", "brand": "Synthetic",
            "model": "MODEL-128", "selling_unit": "pcs", "unit_price_eur_cents": 1234,
            "availability": "in_stock", "delivery_to_fi": "unknown",
            "shipping_eur_cents": None, "tax_eur_cents": None,
            "evidence": {"text_excerpt": "Synthetic extraction assertion", "reliability": "unverified"},
        }
        if observation_changes:
            self.observation.update(observation_changes)
        self.correlation_id = uuid4()
        self.stored = self.ledger.record(
            self.observation, self.capture, correlation_id=self.correlation_id,
        )
        assert self.stored.accepted
        self.product = {
            "@context": "https://schema.org", "@type": "Product", "url": source_url,
            "name": "Synthetic recorded widget", "model": "MODEL-128",
            "offers": {
                "@type": "Offer", "url": source_url, "price": "12.34", "priceCurrency": "EUR",
                "availability": "https://schema.org/InStock",
                "itemCondition": "https://schema.org/NewCondition",
                "seller": {"@type": "Organization", "url": "https://shop.example/"},
            },
        }
        self.reader = RecordedRepresentations(self.representation())

    def representation(self):
        return SellerRepresentation(
            source_url=self.capture.source_url, fetched_at=self.capture.fetched_at,
            cache_age_ms=self.capture.cache_age_ms, provider_request_id=self.capture.provider_request_id,
            json_ld=json.dumps(self.product), resolved_addresses=("93.184.216.34",),
        )

    def checker(self, *, profile="dev", merchant_hosts=frozenset({"shop.example"})):
        return EvidenceChecker(self.ledger, self.reader, merchant_hosts=merchant_hosts, profile=profile)

    def check(self, **kwargs):
        return self.checker().check(
            UUID(self.stored.observation_id), check_id=uuid4(), correlation_id=self.correlation_id,
            **kwargs,
        )

    def by_id(self, observation_id):
        return next(row for row in self.repository.state["observations"].values()
                    if row["observation_id"] == observation_id)


def assert_no_promotion(scenario, audit):
    assert audit["decision"] in {"unverified", "disputed"}
    assert audit["reasons"]
    assert all(row["evidence"]["reliability"] != "checked"
               for row in scenario.repository.state["observations"].values())
    assert audit["original_observation_id"] == scenario.stored.observation_id
    assert audit["checked_at"] == timestamp(scenario.time)


def test_fresh_first_party_cross_check_appends_immutable_checked_snapshot_and_audit():
    scenario = Scenario()
    original = deepcopy(scenario.by_id(scenario.stored.observation_id))
    audit = scenario.check()
    assert audit["decision"] == "checked"
    assert audit["policy_version"] == "icp09-v1"
    assert audit["normalizer_version"] == "seller-jsonld-v1"
    assert len(audit["input_digest"]) == len(audit["proof_digest"]) == 64
    assert audit["proof"]
    derived = scenario.by_id(audit["result_observation_id"])
    assert derived["observation_id"] != original["observation_id"]
    assert derived["evidence"]["reliability"] == "checked"
    assert scenario.by_id(original["observation_id"]) == original
    for key in original.keys() - {"observation_id", "evidence", "brand"}:
        assert derived[key] == original[key]
    assert derived["brand"] is None  # No brand exists in the independent representation.
    assert derived["shipping_eur_cents"] is None and derived["tax_eur_cents"] is None
    assert derived["delivery_to_fi"] == "unknown"
    assert "condition" not in derived
    assert scenario.ledger.observation_validator.is_valid(derived)
    assert scenario.repository.state["provenance"][derived["observation_id"]] == scenario.capture
    assert len(scenario.repository.state["outbox"]) == 2
    event = next(event for event in scenario.repository.state["outbox"].values()
                 if event["payload"]["observation_id"] == derived["observation_id"])
    assert scenario.ledger.event_validator.is_valid(event)
    assert event["correlation_id"] == str(scenario.correlation_id)


def test_checked_capture_url_uses_verified_source_and_preserves_untrusted_original():
    forged_url = "https://unrelated.example/claimed-proof"
    scenario = Scenario(observation_changes={
        "evidence": {"text_excerpt": "Synthetic assertion with forged proof link",
                     "reliability": "unverified", "capture_url": forged_url},
    })
    audit = scenario.check()
    assert audit["decision"] == "checked"
    assert scenario.by_id(audit["result_observation_id"])["evidence"]["capture_url"] == URL
    assert scenario.by_id(scenario.stored.observation_id)["evidence"]["capture_url"] == forged_url


def test_single_assertion_and_caller_checked_cannot_bypass_checker():
    scenario = Scenario()
    scenario.reader.representation = None
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["result_observation_id"] is None
    forged = deepcopy(scenario.observation)
    forged["observation_id"] = str(uuid4())
    forged["evidence"]["reliability"] = "checked"
    result = scenario.ledger.record(forged, scenario.capture, correlation_id=uuid4())
    assert not result.accepted and result.reason == "CALLER_CHECKED"
    assert len(scenario.repository.state["outbox"]) == 1


def test_checked_core_does_not_certify_uncorroborated_shipping_or_tax_assertions():
    scenario = Scenario(observation_changes={
        "delivery_to_fi": "yes", "shipping_eur_cents": 0, "tax_eur_cents": 0,
    })
    audit = scenario.check()
    assert audit["decision"] == "checked"
    derived = scenario.by_id(audit["result_observation_id"])
    assert derived["delivery_to_fi"] == "unknown"
    assert derived["shipping_eur_cents"] is None and derived["tax_eur_cents"] is None
    original = scenario.by_id(scenario.stored.observation_id)
    assert original["delivery_to_fi"] == "yes"
    assert original["shipping_eur_cents"] == original["tax_eur_cents"] == 0


def test_unproved_brand_assertion_is_removed_from_checked_derivative():
    scenario = Scenario()
    assert "brand" not in scenario.product
    audit = scenario.check()
    assert audit["decision"] == "checked"
    assert scenario.by_id(audit["result_observation_id"])["brand"] is None
    assert scenario.by_id(scenario.stored.observation_id)["brand"] == "Synthetic"


def test_explicit_different_brand_is_disputed():
    scenario = Scenario()
    scenario.product["brand"] = {"@type": "Brand", "name": "Different recorded brand"}
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "disputed"


def test_explicit_matching_brand_is_preserved():
    scenario = Scenario()
    scenario.product["brand"] = {"@type": "Brand", "name": "Synthetic"}
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert audit["decision"] == "checked"
    assert scenario.by_id(audit["result_observation_id"])["brand"] == "Synthetic"


def test_explicit_fi_shipping_and_tax_proof_preserves_previously_asserted_known_values():
    scenario = Scenario(observation_changes={
        "delivery_to_fi": "yes", "shipping_eur_cents": 0, "tax_eur_cents": 0,
    })
    scenario.product["offers"]["shippingDetails"] = {
        "@type": "OfferShippingDetails",
        "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "FI"},
        "shippingRate": {"@type": "MonetaryAmount", "currency": "EUR", "value": "0.00"},
    }
    scenario.product["offers"]["priceSpecification"] = {
        "@type": "PriceSpecification", "valueAddedTaxIncluded": True,
    }
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert audit["decision"] == "checked"
    derived = scenario.by_id(audit["result_observation_id"])
    assert derived["delivery_to_fi"] == "yes"
    assert derived["shipping_eur_cents"] == derived["tax_eur_cents"] == 0


def test_optional_seller_proof_does_not_fill_unknown_observation_values():
    scenario = Scenario()
    scenario.product["offers"]["shippingDetails"] = {
        "@type": "OfferShippingDetails",
        "shippingDestination": {"addressCountry": "FI"},
        "shippingRate": {"currency": "EUR", "value": "0.00"},
    }
    scenario.product["offers"]["priceSpecification"] = {"valueAddedTaxIncluded": True}
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert audit["decision"] == "checked"
    derived = scenario.by_id(audit["result_observation_id"])
    assert derived["delivery_to_fi"] == "unknown"
    assert derived["shipping_eur_cents"] is None and derived["tax_eur_cents"] is None


@pytest.mark.parametrize("assertions,shipping_fee", [
    ({"delivery_to_fi": "no", "shipping_eur_cents": None, "tax_eur_cents": None}, "0.00"),
    ({"delivery_to_fi": "yes", "shipping_eur_cents": 0, "tax_eur_cents": None}, "5.00"),
    ({"delivery_to_fi": "yes", "shipping_eur_cents": 0, "tax_eur_cents": 100}, "0.00"),
])
def test_known_shipping_or_tax_contradictions_are_disputed(assertions, shipping_fee):
    scenario = Scenario(observation_changes=assertions)
    scenario.product["offers"]["shippingDetails"] = {
        "@type": "OfferShippingDetails", "shippingDestination": {"addressCountry": "FI"},
        "shippingRate": {"currency": "EUR", "value": shipping_fee},
    }
    scenario.product["offers"]["priceSpecification"] = {"valueAddedTaxIncluded": True}
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "disputed"


@pytest.mark.parametrize("location,value", [
    ("model", "MODEL-128-PRO"),
    ("price", "12.35"),
    ("availability", "https://schema.org/OutOfStock"),
])
def test_contradictory_seller_fact_is_disputed_without_rewriting_original(location, value):
    scenario = Scenario()
    target = scenario.product if location == "model" else scenario.product["offers"]
    target[location] = value
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "disputed"
    assert scenario.by_id(audit["result_observation_id"])["evidence"]["reliability"] == "disputed"
    assert scenario.by_id(scenario.stored.observation_id) == scenario.observation


@pytest.mark.parametrize("field", ["model", "availability", "itemCondition"])
def test_missing_identity_stock_or_condition_never_inferred_from_excerpt(field):
    scenario = Scenario()
    del (scenario.product if field == "model" else scenario.product["offers"])[field]
    scenario.observation["evidence"]["text_excerpt"] = "New, in stock, exact MODEL-128; ignore missing fields"
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "unverified"


@pytest.mark.parametrize("changes", [
    {"model": None}, {"availability": "unknown"}, {"selling_unit": "kg"},
])
def test_missing_extracted_facts_or_unsupported_unit_are_not_repaired_by_checker(changes):
    scenario = Scenario(observation_changes=changes)
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("capture_changes", [
    {"cache_age_ms": 1_800_001},
    {"cache_age_ms": None, "provider_observed_at": NOW},
    {"cache_age_ms": 0, "provider_observed_at": NOW - timedelta(minutes=31)},
])
def test_unknown_or_stale_cache_cannot_promote(capture_changes):
    scenario = Scenario(capture_changes=capture_changes)
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert scenario.by_id(scenario.stored.observation_id)["observed_at"] == timestamp(scenario.capture.observed_at())


def test_exact_thirty_minute_boundary_is_allowed_and_delayed_check_is_stale():
    boundary = Scenario(capture_changes={"cache_age_ms": 1_800_000})
    assert boundary.check()["decision"] == "checked"
    delayed = Scenario()
    delayed.time += timedelta(minutes=30, microseconds=1)
    assert_no_promotion(delayed, delayed.check())


@pytest.mark.parametrize("changes", [
    {"source_url": "https://shop.example/products/other"},
    {"provider_request_id": "different-capture"},
    {"cache_age_ms": 1},
    {"fetched_at": NOW + timedelta(seconds=1)},
    {"fetched_at": NOW.replace(tzinfo=None)},
    {"fetched_at": None},
])
def test_forged_or_misbound_representation_metadata_never_promotes(changes):
    scenario = Scenario()
    scenario.reader.representation = replace(scenario.reader.representation, **changes)
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("source_url", [
    "http://shop.example/products/model-128",
    "https://localhost/products/model-128",
    "https://127.0.0.1/products/model-128",
    "https://169.254.169.254/latest/meta-data/",
    "https://shop.example.attacker.example/products/model-128",
])
def test_non_https_private_or_unapproved_merchant_source_cannot_promote(source_url):
    scenario = Scenario(source_url=source_url)
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("addresses", [(), ("127.0.0.1",), ("10.0.0.8",), ("::1",),
                                       ("93.184.216.34", "169.254.169.254"), None, (1234567890,),
                                       ("93.184.216.34",) * 33])
def test_all_recorded_source_addresses_must_be_public(addresses):
    scenario = Scenario()
    scenario.reader.representation = replace(scenario.reader.representation, resolved_addresses=addresses)
    assert_no_promotion(scenario, scenario.check())


def test_marketplace_search_and_third_party_seller_cannot_qualify_as_first_party():
    scenario = Scenario()
    scenario.product["offers"]["@type"] = "AggregateOffer"
    scenario.reader.representation = scenario.representation()
    assert_no_promotion(scenario, scenario.check())
    other = Scenario()
    other.product["offers"]["seller"]["url"] = "https://marketplace.example/"
    other.reader.representation = other.representation()
    assert_no_promotion(other, other.check())


@pytest.mark.parametrize("price,currency", [
    (12.34, "EUR"), (True, "EUR"), ("12.345", "EUR"), ("NaN", "EUR"),
    ("12.34", "USD"), ("12,34", "EUR"),
])
def test_ambiguous_currency_binary_float_or_fractional_cents_never_promote(price, currency):
    scenario = Scenario()
    scenario.product["offers"].update(price=price, priceCurrency=currency)
    scenario.reader.representation = scenario.representation()
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("json_ld", ["not JSON", "[]", "{}", '{"@type":"Product","@type":"Offer"}'])
def test_invalid_or_ambiguous_representation_does_not_promote(json_ld):
    scenario = Scenario()
    scenario.reader.representation = replace(scenario.reader.representation, json_ld=json_ld)
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("field,value", [
    ("availability", {}), ("availability", []), ("itemCondition", {}), ("itemCondition", []),
])
def test_structurally_invalid_stock_or_condition_records_unknown_instead_of_crashing(field, value):
    scenario = Scenario()
    scenario.product["offers"][field] = value
    scenario.reader.representation = scenario.representation()
    assert_no_promotion(scenario, scenario.check())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonstandard_json_constants_are_unverified_even_in_unchecked_fields(value):
    scenario = Scenario()
    scenario.product["description"] = value
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "unverified"


@pytest.mark.parametrize("bad_unicode", ["\ud800", "\\ud800"])
def test_invalid_unicode_scalar_is_rejected_without_crash_or_unpersistable_proof(bad_unicode):
    scenario = Scenario()
    raw = scenario.reader.representation.json_ld
    raw = raw.replace('"@context"', f'"description":"{bad_unicode}","@context"', 1)
    scenario.reader.representation = replace(scenario.reader.representation, json_ld=raw)
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "unverified"


def test_oversized_fragment_never_enters_the_audit_or_promotes():
    scenario = Scenario()
    scenario.product["description"] = "untrusted " * 2000
    scenario.reader.representation = scenario.representation()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert "description" not in json.dumps(audit["proof"])


def test_representation_adapter_failure_is_audited_without_fixture_fallback():
    scenario = Scenario()

    class Unavailable:
        def load(self, observation_id):
            raise OSError("Recorded storage unavailable")

    scenario.reader = Unavailable()
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["result_observation_id"] is None


def test_check_id_replay_returns_original_audit_without_new_proof_or_publication():
    scenario = Scenario()
    checker, check_id = scenario.checker(), uuid4()
    first = checker.check(UUID(scenario.stored.observation_id), check_id=check_id,
                          correlation_id=scenario.correlation_id)
    state = deepcopy(scenario.repository.state)
    scenario.time += timedelta(days=1)
    scenario.reader.representation = None
    replay = checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    assert replay == first
    assert scenario.reader.calls == 1
    assert scenario.repository.state == state


def test_failed_check_id_replay_does_not_promote_after_proof_becomes_available():
    scenario = Scenario()
    scenario.reader.representation = None
    checker, check_id = scenario.checker(), uuid4()
    first = checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    scenario.reader.representation = scenario.representation()
    replay = checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    assert replay == first and replay["decision"] == "unverified"
    assert len(scenario.repository.state["observations"]) == len(scenario.repository.state["outbox"]) == 1


def test_modifying_returned_audit_does_not_rewrite_immutable_decision():
    scenario = Scenario()
    checker, check_id = scenario.checker(), uuid4()
    audit = checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    expected = deepcopy(audit)
    audit["decision"] = "disputed"
    audit["proof"].clear()
    audit["reasons"].append("caller modification")
    replay = checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    assert replay == expected


def test_check_id_cannot_be_rebound_to_another_observation():
    scenario = Scenario()
    checker, check_id = scenario.checker(), uuid4()
    checker.check(UUID(scenario.stored.observation_id), check_id=check_id, correlation_id=uuid4())
    candidate = deepcopy(scenario.observation)
    candidate["observation_id"] = str(uuid4())
    candidate["evidence"]["text_excerpt"] = "Another recorded assertion"
    second = scenario.ledger.record(candidate, scenario.capture, correlation_id=uuid4())
    with pytest.raises(AttemptConflict):
        checker.check(UUID(second.observation_id), check_id=check_id, correlation_id=uuid4())


def test_same_recorded_proof_cannot_promote_another_assertion():
    scenario = Scenario()
    assert scenario.check()["decision"] == "checked"
    candidate = deepcopy(scenario.observation)
    candidate["observation_id"] = str(uuid4())
    candidate["evidence"]["text_excerpt"] = "Replayed capture under another assertion"
    second = scenario.ledger.record(candidate, scenario.capture, correlation_id=uuid4())
    audit = scenario.checker().check(UUID(second.observation_id), check_id=uuid4(), correlation_id=uuid4())
    assert audit["decision"] != "checked"
    assert sum(row["evidence"]["reliability"] == "checked"
               for row in scenario.repository.state["observations"].values()) == 1


def test_current_same_capture_conflict_is_disputed_and_historical_price_change_is_allowed():
    scenario = Scenario()
    contradictory = deepcopy(scenario.observation)
    contradictory["observation_id"] = str(uuid4())
    contradictory["unit_price_eur_cents"] += 1
    assert scenario.ledger.record(contradictory, scenario.capture, correlation_id=uuid4()).accepted
    audit = scenario.check()
    assert_no_promotion(scenario, audit)
    assert audit["decision"] == "disputed"

    historical = Scenario()
    older = deepcopy(historical.observation)
    older["observation_id"] = str(uuid4())
    older["unit_price_eur_cents"] += 1
    older["observed_at"] = timestamp(NOW - timedelta(minutes=5))
    old_capture = replace(historical.capture, fetched_at=NOW - timedelta(minutes=5),
                          provider_request_id="recorded-older-request")
    older["provider_request_id"] = old_capture.provider_request_id
    assert historical.ledger.record(older, old_capture, correlation_id=uuid4()).accepted
    assert historical.check()["decision"] == "checked"


@pytest.mark.parametrize("check_previous_checked", [False, True])
def test_same_time_conflict_supersedes_previously_checked_snapshot(check_previous_checked):
    scenario = Scenario()
    checked_audit = scenario.check()
    checked_id = checked_audit["result_observation_id"]
    contradictory = deepcopy(scenario.observation)
    contradictory["observation_id"] = str(uuid4())
    contradictory["unit_price_eur_cents"] += 1
    second = scenario.ledger.record(contradictory, scenario.capture, correlation_id=uuid4())
    target_id = checked_id if check_previous_checked else second.observation_id
    audit = scenario.checker().check(UUID(target_id), check_id=uuid4(), correlation_id=uuid4())
    assert audit["decision"] == "disputed"
    with scenario.repository.transaction() as tx:
        current = tx.source_observations(str(scenario.capture.source_id), NOW - timedelta(minutes=30))
    assert current
    assert all(item["evidence"]["reliability"] != "checked" for item in current)
    # The historical checked fact is preserved exactly; its new dispute supersedes it.
    assert scenario.by_id(checked_id)["evidence"]["reliability"] == "checked"


@pytest.mark.parametrize("changed_field,value", [
    ("delivery_to_fi", "no"), ("shipping_eur_cents", 500), ("tax_eur_cents", 100),
])
def test_same_time_optional_fact_conflict_disputes_prior_checked_without_new_representation(changed_field, value):
    scenario = Scenario(observation_changes={
        "brand": None, "delivery_to_fi": "yes", "shipping_eur_cents": 0, "tax_eur_cents": 0,
    })
    scenario.product["offers"]["shippingDetails"] = {
        "@type": "OfferShippingDetails", "shippingDestination": {"addressCountry": "FI"},
        "shippingRate": {"currency": "EUR", "value": "0.00"},
    }
    scenario.product["offers"]["priceSpecification"] = {"valueAddedTaxIncluded": True}
    scenario.reader.representation = scenario.representation()
    checked_audit = scenario.check()
    assert checked_audit["decision"] == "checked"
    contradictory = deepcopy(scenario.observation)
    contradictory["observation_id"] = str(uuid4())
    contradictory[changed_field] = value
    second = scenario.ledger.record(contradictory, scenario.capture, correlation_id=uuid4())
    scenario.reader.representation = None
    audit = scenario.checker().check(UUID(second.observation_id), check_id=uuid4(), correlation_id=uuid4())
    assert audit["decision"] == "disputed"
    with scenario.repository.transaction() as tx:
        current = tx.source_observations(str(scenario.capture.source_id), NOW - timedelta(minutes=30))
    assert all(item["evidence"]["reliability"] != "checked" for item in current)
    assert scenario.by_id(checked_audit["result_observation_id"])["evidence"]["reliability"] == "checked"


def test_same_time_condition_conflict_uses_immutable_audited_claims():
    scenario = Scenario(observation_changes={"brand": None})
    checked_audit = scenario.check()
    assert checked_audit["decision"] == "checked"
    second_capture = replace(scenario.capture, provider_request_id="recorded-request-2")
    candidate = deepcopy(scenario.observation)
    candidate["observation_id"] = str(uuid4())
    candidate["provider_request_id"] = second_capture.provider_request_id
    candidate["evidence"]["text_excerpt"] = "Different assertion for the same captured product"
    second = scenario.ledger.record(candidate, second_capture, correlation_id=uuid4())
    assert not second.duplicate
    scenario.product["offers"]["itemCondition"] = "https://schema.org/UsedCondition"
    scenario.reader.representation = replace(
        scenario.representation(), provider_request_id=second_capture.provider_request_id,
    )
    audit = scenario.checker().check(UUID(second.observation_id), check_id=uuid4(), correlation_id=uuid4())
    assert audit["decision"] == "disputed"
    with scenario.repository.transaction() as tx:
        current = tx.source_observations(str(scenario.capture.source_id), NOW - timedelta(minutes=30))
    assert all(item["evidence"]["reliability"] != "checked" for item in current)
    assert scenario.by_id(checked_audit["result_observation_id"])["evidence"]["reliability"] == "checked"


@pytest.mark.parametrize("profile", ["dev", "demo"])
def test_fixture_provenance_is_rejected_in_non_test_profile(profile):
    scenario = Scenario(capture_changes={"provider": "fixture", "extraction_method": "fixture"})
    audit = scenario.checker(profile=profile).check(
        UUID(scenario.stored.observation_id), check_id=uuid4(), correlation_id=uuid4(),
    )
    assert_no_promotion(scenario, audit)


def test_fixture_checker_requires_explicit_test_profile():
    scenario = Scenario(capture_changes={"provider": "fixture", "extraction_method": "fixture"})
    audit = scenario.checker(profile="test").check(
        UUID(scenario.stored.observation_id), check_id=uuid4(), correlation_id=uuid4(),
    )
    assert audit["decision"] == "checked"


@pytest.mark.parametrize("fail_at", ["provenance", "outbox", "check"])
def test_derived_snapshot_audit_and_event_roll_back_together(fail_at):
    scenario = Scenario()
    original_state = deepcopy(scenario.repository.state)
    scenario.repository.fail_at = fail_at
    with pytest.raises(OSError, match="Injected"):
        scenario.check()
    assert scenario.repository.state == original_state


def test_unknown_observation_cannot_create_audit_or_snapshot():
    scenario = Scenario()
    original_state = deepcopy(scenario.repository.state)
    with pytest.raises(ValueError):
        scenario.checker().check(uuid4(), check_id=uuid4(), correlation_id=uuid4())
    assert scenario.repository.state == original_state
