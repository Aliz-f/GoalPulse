"""Private product-fact normalization. No proposed HTTP DTO or v1 extensions.

Inputs must be explicitly extracted facts. Excerpts are audit text, never a
structured source of model, product condition, attributes, shipping or tax proof.
Prices that cannot be represented exactly in integer cents are rejected rather
than rounded down into a misleading offer. No automatic trust upgrade exists.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID, uuid4

from .ledger import Capture, EvidenceLedger, Stored, timestamp


class NormalizationError(ValueError):
    pass


def decimal_amount(value: str | Decimal | int) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise NormalizationError("INVALID_MONEY")
    if isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise NormalizationError("INVALID_MONEY")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise NormalizationError("INVALID_MONEY") from None


def cents(value: str | Decimal | int | None) -> int | None:
    if value is None:
        return None
    amount = decimal_amount(value) * 100
    if amount != amount.to_integral_value():
        raise NormalizationError("NON_INTEGRAL_PRICE")
    return int(amount)


@dataclass(frozen=True)
class ProductFacts:
    """Service-private facts, not Observation fields or an ingestion wire format."""

    product_name: str | None
    price_eur: str | Decimal | None
    currency: str | None
    selling_unit: str | None
    package_quantity: str | Decimal | None = None
    package_unit: str | None = None
    price_is_per_unit: bool = False
    brand: str | None = None
    model: str | None = None
    availability: str = "unknown"
    delivery_to_fi: str = "unknown"
    delivery_country: str = "FI"
    shipping_eur: str | Decimal | None = None
    tax_treatment: str = "unknown"
    additional_tax_eur: str | Decimal | None = None
    excerpt: str = ""
    source_disputed: bool = False


def normalize(facts: ProductFacts, capture: Capture) -> dict:
    """Produce exactly Observation.v1; never parse missing facts from the excerpt."""
    if facts.currency != "EUR":
        raise NormalizationError("UNSUPPORTED_CURRENCY")
    if facts.delivery_country != "FI":
        raise NormalizationError("UNSUPPORTED_DESTINATION")
    if facts.price_eur is None:
        raise NormalizationError("MISSING_UNIT_PRICE")
    if not facts.product_name or not facts.product_name.strip():
        raise NormalizationError("MISSING_PRODUCT_NAME")
    if facts.selling_unit not in ("pcs", "kg", "L"):
        raise NormalizationError("INVALID_UNIT")
    price = decimal_amount(facts.price_eur)
    if not facts.price_is_per_unit:
        units = {"pcs": ("pcs", Decimal(1)), "kg": ("kg", Decimal(1)),
                 "g": ("kg", Decimal("0.001")), "L": ("L", Decimal(1)),
                 "ml": ("L", Decimal("0.001"))}
        if facts.package_quantity is None or facts.package_unit not in units:
            raise NormalizationError("UNKNOWN_PACKAGING")
        unit, multiplier = units[facts.package_unit]
        quantity = decimal_amount(facts.package_quantity) * multiplier
        if unit != facts.selling_unit or quantity <= 0:
            raise NormalizationError("UNKNOWN_PACKAGING")
        # Decimal division itself may round under its context. Verify the exact
        # integer relationship, which avoids accepting a rounded-down unit price.
        unit_cents = price * 100 / quantity
        if unit_cents != unit_cents.to_integral_value() or unit_cents * quantity != price * 100:
            raise NormalizationError("NON_INTEGRAL_PRICE")
        price_cents = int(unit_cents)
    else:
        price_cents = cents(price)
    if facts.availability not in ("in_stock", "out_of_stock", "unknown"):
        raise NormalizationError("INVALID_FACTS")
    if facts.delivery_to_fi not in ("yes", "no", "unknown"):
        raise NormalizationError("INVALID_FACTS")
    if facts.tax_treatment not in ("inclusive", "additional", "unknown"):
        raise NormalizationError("INVALID_FACTS")
    if facts.tax_treatment == "inclusive":
        if facts.additional_tax_eur is not None and cents(facts.additional_tax_eur) != 0:
            raise NormalizationError("CONFLICTING_TAX")
        tax = 0
    else:
        tax = cents(facts.additional_tax_eur) if facts.tax_treatment == "additional" else None
    observed_at = capture.observed_at()
    if observed_at is None:
        raise NormalizationError("UNKNOWN_OBSERVED_AT")
    shipping = cents(facts.shipping_eur) if facts.delivery_to_fi == "yes" else None
    return {
        "observation_id": str(uuid4()), "source_id": str(capture.source_id),
        "source_url": capture.source_url, "provider": capture.provider,
        "extraction_method": capture.extraction_method, "provider_request_id": capture.provider_request_id,
        "observed_at": timestamp(observed_at), "product_name": facts.product_name.strip(),
        "brand": facts.brand, "model": facts.model, "selling_unit": facts.selling_unit,
        "unit_price_eur_cents": price_cents, "availability": facts.availability,
        "delivery_to_fi": facts.delivery_to_fi, "shipping_eur_cents": shipping, "tax_eur_cents": tax,
        "evidence": {"text_excerpt": facts.excerpt[:1000],
                     "reliability": "disputed" if facts.source_disputed else "unverified",
                     "capture_url": None},
    }


class FactIngestor:
    """Normalize private facts into the ledger; HTTP integration awaits ICP-01."""

    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    def record(self, facts: ProductFacts, capture: Capture, *, correlation_id: UUID) -> Stored:
        try:
            observation = normalize(facts, capture)
        except NormalizationError as error:
            # Only a digest is persisted for invalid facts; these values are not
            # logged, stored as a raw payload, or emitted in the domain event.
            fingerprint_data = {key: str(value) for key, value in asdict(facts).items()}
            return self.ledger.record_failure(capture, reason=str(error),
                                              fingerprint_data=fingerprint_data,
                                              correlation_id=correlation_id)
        return self.ledger.record(observation, capture, correlation_id=correlation_id)
