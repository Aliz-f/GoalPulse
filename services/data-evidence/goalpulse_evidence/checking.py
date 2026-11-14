"""ICP-09 internal verification of immutable, already-recorded observations.

This module has no HTTP endpoint and makes no network calls. The injected reader
loads a bounded seller JSON-LD representation from trusted capture storage, never
from ExtractionSubmission's parsed fields or excerpt. Discovery & Monitoring owns
provider access and must screen DNS/redirects when capturing that representation.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import ip_address
import json
import math
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from .ledger import AttemptConflict, Capture, EvidenceLedger, digest, parse_timestamp, timestamp, utc, valid_uri
from .normalization import NormalizationError, cents

POLICY_VERSION = "icp09-v1"
NORMALIZER_VERSION = "seller-jsonld-v1"
MAX_AGE = timedelta(minutes=30)


@dataclass(frozen=True)
class SellerRepresentation:
    """A recorded seller fragment and the original transport/capture provenance.

    resolved_addresses are the actual vetted public addresses from capture, not a
    new DNS lookup. A redirect to another URL requires a new observation/capture;
    this checker never grants trust across an unrecorded redirect.
    """

    source_url: str
    fetched_at: datetime
    cache_age_ms: int | None
    provider_request_id: str | None
    json_ld: str
    resolved_addresses: tuple[str, ...]


class RepresentationReader(Protocol):
    def load(self, observation_id: UUID) -> SellerRepresentation | None:
        """Read original provider capture, not a regenerated normalized assertion."""
        ...


class CheckTransaction(Protocol):
    def lock_source(self, source_id: str) -> None: ...
    def get_observation(self, observation_id: str) -> dict | None: ...
    def get_capture(self, observation_id: str) -> Capture | None: ...
    def get_attempt_id(self, observation_id: str) -> str: ...
    def get_check(self, check_id: str) -> dict | None: ...
    def proof_used(self, proof_digest: str) -> bool: ...
    def source_observations(self, source_id: str, since: datetime) -> list[dict]: ...
    def current_claims(self, source_id: str, observed_at: datetime) -> list[dict]: ...
    def add_check(self, audit: dict) -> None: ...
    def add_observation(self, observation: dict, attempt_id: str,
                        fingerprint: str, created_at: datetime) -> tuple[str, bool]: ...
    def add_provenance(self, observation_id: str, capture: Capture) -> None: ...
    def enqueue(self, event: dict, created_at: datetime) -> None: ...


class ProofError(ValueError):
    pass


def _conflicts(left: dict, right: dict) -> list[str]:
    keys = ("model", "brand", "product_name", "unit_price_eur_cents", "availability", "condition",
            "delivery_to_fi", "shipping_eur_cents", "tax_eur_cents")
    return [key for key in keys if left.get(key) not in (None, "", "unknown")
            and right.get(key) not in (None, "", "unknown") and left[key] != right[key]]


def _public_https(url: str, merchant_hosts: frozenset[str]) -> bool:
    if not valid_uri(url):
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.fragment or host not in merchant_hosts
            or "." not in host or host.endswith((".localhost", ".local", ".internal", ".localdomain"))
            or host in ("metadata.google.internal", "metadata.azure.internal")
            or parsed.path.rstrip("/").lower().endswith(("/search", "/marketplace"))):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return True


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProofError("AMBIGUOUS_REPRESENTATION")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ProofError("INVALID_REPRESENTATION")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProofError("INVALID_REPRESENTATION")
    return result


def _seller_claims(raw: str, source_url: str, hosts: frozenset[str]) -> dict:
    """Parse one exact Product/Offer, refusing aggregates, fuzzy matches and floats."""
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_384:
            raise ProofError("INVALID_REPRESENTATION")
        data = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant, parse_float=_finite_float)
        # PostgreSQL jsonb rejects NUL and lone surrogate escapes, including inside
        # ignored fields. Reject before constructing any persistable audit proof.
        decoded = json.dumps(data, ensure_ascii=False)
        decoded.encode("utf-8")
        if "\x00" in decoded or "\\u0000" in decoded:
            raise ProofError("INVALID_REPRESENTATION")
    except (ValueError, UnicodeError, RecursionError):
        raise ProofError("INVALID_REPRESENTATION") from None
    if (not isinstance(data, dict) or data.get("@context") != "https://schema.org"
            or data.get("@type") != "Product" or data.get("url") != source_url):
        raise ProofError("NOT_PRODUCT_REPRESENTATION")
    offer = data.get("offers")
    if (not isinstance(offer, dict) or offer.get("@type") != "Offer"
            or offer.get("url") != source_url):
        raise ProofError("NOT_SINGLE_SELLER_OFFER")
    seller = offer.get("seller")
    if (not isinstance(seller, dict) or not isinstance(seller.get("url"), str)
            or not _public_https(seller["url"], hosts)
            or urlsplit(seller["url"]).hostname != urlsplit(source_url).hostname):
        raise ProofError("NOT_FIRST_PARTY_SELLER")
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        raise ProofError("MISSING_EXACT_MODEL")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ProofError("MISSING_PRODUCT_NAME")
    if offer.get("priceCurrency") != "EUR":
        raise ProofError("NON_EUR_PRICE")
    try:
        price = cents(offer.get("price"))
    except NormalizationError:
        raise ProofError("INVALID_EUR_PRICE") from None
    if price is None:
        raise ProofError("MISSING_EUR_PRICE")
    stock_value, condition_value = offer.get("availability"), offer.get("itemCondition")
    stock = {"https://schema.org/InStock": "in_stock",
             "https://schema.org/OutOfStock": "out_of_stock"}.get(stock_value) if isinstance(stock_value, str) else None
    condition = {"https://schema.org/NewCondition": "new",
                 "https://schema.org/UsedCondition": "used",
                 "https://schema.org/RefurbishedCondition": "refurbished"}.get(condition_value) if isinstance(condition_value, str) else None
    if stock is None:
        raise ProofError("MISSING_EXPLICIT_STOCK")
    if condition is None:
        raise ProofError("MISSING_EXPLICIT_CONDITION")
    brand = data.get("brand")
    if isinstance(brand, dict) and brand.get("@type") == "Brand":
        brand = brand.get("name")
    brand = brand.strip() if isinstance(brand, str) and brand.strip() else None
    claims = {"model": data["model"].strip(), "product_name": data["name"].strip(), "brand": brand,
              "unit_price_eur_cents": price, "availability": stock, "condition": condition,
              "delivery_to_fi": "unknown", "shipping_eur_cents": None, "tax_eur_cents": None}
    # These optional facts are deliberately separate from core price checking.
    shipping = offer.get("shippingDetails")
    if isinstance(shipping, dict) and shipping.get("@type") == "OfferShippingDetails":
        destination = shipping.get("shippingDestination")
        if isinstance(destination, dict) and destination.get("addressCountry") == "FI":
            claims["delivery_to_fi"] = "yes"
            rate = shipping.get("shippingRate")
            if isinstance(rate, dict) and rate.get("currency") == "EUR":
                try:
                    claims["shipping_eur_cents"] = cents(rate.get("value"))
                except NormalizationError:
                    pass  # Unusable fee stays missing, never rounded or assumed free.
    tax = offer.get("priceSpecification")
    if isinstance(tax, dict) and tax.get("valueAddedTaxIncluded") is True:
        claims["tax_eur_cents"] = 0
    return claims


class EvidenceChecker:
    """Append a trust decision plus, when applicable, a new immutable snapshot.

    merchant_hosts is a server-owned explicit merchant registry, never a request
    field. No default merchant is trusted. P0 supports exact pcs product offers;
    package/kg/L cross-check rules remain outside this policy version.
    """

    def __init__(self, ledger: EvidenceLedger, representations: RepresentationReader, *,
                 merchant_hosts: frozenset[str], profile: str = "dev") -> None:
        if profile not in ("test", "dev", "demo"):
            raise ValueError("Unsupported profile")
        self.ledger, self.representations, self.profile = ledger, representations, profile
        self.merchant_hosts = frozenset(h.lower() for h in merchant_hosts)

    def _decide(self, original: dict, capture: Capture, representation: SellerRepresentation | None,
                now: datetime, tx: CheckTransaction) -> tuple[str, list[str], dict, str | None]:
        if original["evidence"]["reliability"] == "disputed":
            return "disputed", ["EXISTING_DISPUTE"], {}, None
        if self.profile != "test" and (capture.provider == "fixture" or capture.extraction_method == "fixture"):
            return "unverified", ["FIXTURE_PROVENANCE"], {}, None
        if not _public_https(original["source_url"], self.merchant_hosts):
            return "unverified", ["NOT_PUBLIC_FIRST_PARTY_HTTPS"], {}, None
        observed = capture.observed_at()
        if (observed is None or capture.cache_age_ms is None or capture.fetched_at > now
                or parse_timestamp(original["observed_at"]) != observed):
            return "unverified", ["UNKNOWN_OR_INVALID_CAPTURE_TIME"], {}, None
        if not timedelta(0) <= now - observed <= MAX_AGE:
            return "unverified", ["STALE_OR_FUTURE_CAPTURE"], {}, None
        current = tx.source_observations(original["source_id"], now - MAX_AGE)
        if not any(item["observation_id"] == original["observation_id"] for item in current):
            return "unverified", ["SUPERSEDED_OBSERVATION"], {}, None
        for other in current:
            if (other["observation_id"] != original["observation_id"]
                    and parse_timestamp(other["observed_at"]) >= observed
                    and _conflicts(other, original)):
                return "disputed", ["CONFLICTING_CURRENT_OBSERVATION"], {
                    "conflicting_observation_id": other["observation_id"]}, None
        if original["evidence"]["reliability"] == "checked":
            return "unverified", ["ALREADY_CHECKED"], {}, None
        if representation is None:
            return "unverified", ["NO_RECORDED_REPRESENTATION"], {}, None
        try:
            represented_fetch = utc(representation.fetched_at)
        except (AttributeError, TypeError, ValueError):
            return "unverified", ["REPRESENTATION_PROVENANCE_MISMATCH"], {}, None
        if (representation.source_url != capture.source_url
                or represented_fetch != utc(capture.fetched_at)
                or type(representation.cache_age_ms) is not int
                or representation.cache_age_ms != capture.cache_age_ms
                or representation.provider_request_id != capture.provider_request_id):
            return "unverified", ["REPRESENTATION_PROVENANCE_MISMATCH"], {}, None
        try:
            if (not isinstance(representation.resolved_addresses, tuple)
                    or not 1 <= len(representation.resolved_addresses) <= 32 or any(
                        not isinstance(address, str) or not ip_address(address).is_global
                        for address in representation.resolved_addresses)):
                return "unverified", ["NON_PUBLIC_CAPTURE_ADDRESS"], {}, None
        except ValueError:
            return "unverified", ["NON_PUBLIC_CAPTURE_ADDRESS"], {}, None
        try:
            claims = _seller_claims(representation.json_ld, capture.source_url, self.merchant_hosts)
        except ProofError as exc:
            return "unverified", [str(exc)], {}, None
        proof = {"source_url": capture.source_url, "fetched_at": timestamp(capture.fetched_at),
                 "observed_at": timestamp(observed), "cache_age_ms": capture.cache_age_ms,
                 "provider_request_id": capture.provider_request_id,
                 "resolved_addresses": list(representation.resolved_addresses),
                 "representation": json.loads(representation.json_ld), "claims": claims}
        # Excludes receipt/observation IDs and claimed fresh timestamps. Replaying
        # the same recorded response cannot create a second checked observation.
        proof_digest = digest({"url": capture.source_url, "request": capture.provider_request_id,
                               "claims": claims})
        tx.lock_source("proof:" + proof_digest)
        if tx.proof_used(proof_digest):
            return "unverified", ["REPLAYED_PROOF"], proof, proof_digest
        if any(_conflicts(claims, previous) for previous in tx.current_claims(original["source_id"], observed)):
            return "disputed", ["CONFLICTING_CAPTURE_REPRESENTATION"], proof, proof_digest
        if original["selling_unit"] != "pcs" or not original.get("model") or original["availability"] == "unknown":
            return "unverified", ["INCOMPLETE_PRODUCT_FACTS"], proof, proof_digest
        mismatches = [key for key in ("model", "product_name", "unit_price_eur_cents", "availability")
                      if original.get(key) != claims[key]]
        if original.get("brand") is not None and claims["brand"] is not None and original["brand"] != claims["brand"]:
            mismatches.append("brand")
        for key in ("delivery_to_fi", "shipping_eur_cents", "tax_eur_cents"):
            unknown = "unknown" if key == "delivery_to_fi" else None
            if original[key] != unknown and claims[key] != unknown and original[key] != claims[key]:
                mismatches.append(key)
        if mismatches:
            return "disputed", ["CONTRADICTORY_" + key.upper() for key in mismatches], proof, proof_digest
        return "checked", ["SELLER_REPRESENTATION_CORROBORATED"], proof, proof_digest

    def _append_snapshot(self, tx: CheckTransaction, original: dict, capture: Capture, *,
                         decision: str, proof: dict, now: datetime, correlation_id: UUID) -> str | None:
        if decision == "unverified" or original["evidence"]["reliability"] == decision:
            return None
        result = deepcopy(original)
        result["observation_id"] = str(uuid4())
        result["evidence"]["reliability"] = decision
        if decision == "checked":
            result["evidence"]["capture_url"] = capture.source_url
            # A core price check cannot certify unrelated caller assertions.
            for key in ("brand", "delivery_to_fi", "shipping_eur_cents", "tax_eur_cents"):
                unknown = "unknown" if key == "delivery_to_fi" else None
                if proof["claims"][key] == unknown:
                    result[key] = unknown
        self.ledger.observation_validator.validate(result)
        semantic = {k: v for k, v in result.items()
                    if k not in ("observation_id", "provider_request_id", "extraction_method")}
        result_id, created = tx.add_observation(result, tx.get_attempt_id(original["observation_id"]),
                                               digest(semantic), now)
        if created:
            tx.add_provenance(result_id, capture)
            event = {"event_id": str(uuid4()), "event_type": "evidence.updated.v1",
                     "schema_version": 1, "occurred_at": timestamp(now), "producer": "data-evidence",
                     "correlation_id": str(correlation_id), "aggregate_id": original["source_id"],
                     "payload": {"source_id": original["source_id"], "observation_id": result_id}}
            self.ledger.event_validator.validate(event)
            tx.enqueue(event, now)
        return result_id

    def check(self, observation_id: UUID, *, check_id: UUID, correlation_id: UUID) -> dict:
        """Verify stored facts; a repeated check_id returns its original decision.

        Negative decisions never acquire later proof during replay. Rechecking
        requires a new check_id and a genuine new capture when old proof is stale.
        """
        if not all(isinstance(value, UUID) for value in (observation_id, check_id, correlation_id)):
            raise ValueError("Identifiers must be UUIDs")
        request_fingerprint = digest({"observation_id": str(observation_id), "policy": POLICY_VERSION})
        with self.ledger.repository.transaction() as tx:
            tx.lock_source("check:" + str(check_id))
            original = tx.get_observation(str(observation_id))
            if original is None:
                raise ValueError("Observation not found")
            tx.lock_source(original["source_id"])
            previous = tx.get_check(str(check_id))
            if previous is not None:
                if previous["request_fingerprint"] != request_fingerprint:
                    raise AttemptConflict("check_id reused for different observation")
                return deepcopy(previous)
            capture = tx.get_capture(str(observation_id))
            if capture is None:
                raise ValueError("Stored capture missing")
            self.ledger.observation_validator.validate(original)
            now = utc(self.ledger.clock())
            try:
                representation = self.representations.load(observation_id)
            except OSError:
                representation = None  # Visible audited unknown; never fixture fallback.
            decision, reasons, proof, proof_digest = self._decide(original, capture, representation, now, tx)
            audit = {"check_id": str(check_id), "original_observation_id": str(observation_id),
                     "result_observation_id": None, "decision": decision, "reasons": reasons,
                     "request_fingerprint": request_fingerprint, "policy_version": POLICY_VERSION,
                     "normalizer_version": NORMALIZER_VERSION, "input_digest": digest(original),
                     "proof_digest": proof_digest, "proof": proof, "correlation_id": str(correlation_id),
                     "checked_at": timestamp(now)}
            audit["result_observation_id"] = self._append_snapshot(
                tx, original, capture, decision=decision, proof=proof, now=now, correlation_id=correlation_id)
            if decision == "disputed":
                # A contradictory assertion for the same capture invalidates any
                # earlier checked interpretation, too. Historical captures remain
                # intact; a real later price change is not itself a contradiction.
                for other in tx.source_observations(original["source_id"], now - MAX_AGE):
                    if (other["observation_id"] == original["observation_id"]
                            or other["evidence"]["reliability"] != "checked"
                            or parse_timestamp(other["observed_at"]) != parse_timestamp(original["observed_at"])):
                        continue
                    other_capture = tx.get_capture(other["observation_id"])
                    if other_capture is None:
                        raise ValueError("Stored capture missing")
                    invalidation = {**audit, "check_id": str(uuid5(check_id, other["observation_id"])),
                                    "original_observation_id": other["observation_id"],
                                    "reasons": ["CONFLICTING_CAPTURE_CHECK"], "input_digest": digest(other),
                                    "request_fingerprint": digest({"trigger_check_id": str(check_id),
                                                                   "observation_id": other["observation_id"]}),
                                    "proof_digest": None, "proof": {"trigger_check_id": str(check_id),
                                                                               "conflicting_observation_id": str(observation_id)}}
                    invalidation["result_observation_id"] = self._append_snapshot(
                        tx, other, other_capture, decision="disputed", proof={}, now=now, correlation_id=correlation_id)
                    tx.add_check(invalidation)
            tx.add_check(audit)
            return deepcopy(audit)
