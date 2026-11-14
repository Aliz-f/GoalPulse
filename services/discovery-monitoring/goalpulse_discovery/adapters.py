"""Accepted ICP-01/08 HTTP adapters and conservative Context product mapping.

Only explicit provider product fields become assertions. These assertions remain
unverified; seller cross-checking belongs to Data & Evidence's internal checker.
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from fractions import Fraction
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from jsonschema import ValidationError

from .context_provider import ProviderError, ScrapeResult, public_addresses, reject_json_constant, utc_now
from .registry import Source, canonical_url
from .validation import validator
from .workflow import Runtime


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _part(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"),
                                               allow_nan=False).encode()).rstrip(b"=").decode("ascii")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous internal JSON")
        result[key] = value
    return result


@dataclass(frozen=True)
class ServiceIdentity:
    """Server-provisioned identity; no repository defaults or browser owner claims."""

    secret: str = field(repr=False)
    issuer: str
    audience: str
    subject: UUID
    clock: Callable[[], datetime] = field(default=utc_now, repr=False)
    ttl_seconds: int = 60

    def __post_init__(self) -> None:
        if (not isinstance(self.secret, str) or not self.secret
                or not isinstance(self.issuer, str) or not self.issuer.strip()
                or not isinstance(self.audience, str) or not self.audience.strip()
                or not isinstance(self.subject, UUID) or type(self.ttl_seconds) is not int
                or not 1 <= self.ttl_seconds <= 300):
            raise ValueError("Invalid service identity configuration")

    def headers(self, scope: str, correlation_id: UUID) -> dict[str, str]:
        if (scope not in {"gm.runtime.read", "gm.goal-version.read", "de.extractions.write"}
                or not isinstance(correlation_id, UUID)):
            raise ValueError("Invalid internal request identity")
        now = int(_utc(self.clock()).timestamp())
        header = _part({"alg": "HS256", "typ": "JWT"})
        payload = _part({"sub": str(self.subject), "iss": self.issuer, "aud": self.audience,
                         "token_kind": "service", "scope": scope,
                         "iat": now, "exp": now + self.ttl_seconds})
        signature = base64.urlsafe_b64encode(hmac.new(
            self.secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256,
        ).digest()).rstrip(b"=").decode("ascii")
        return {"Authorization": f"Bearer {header}.{payload}.{signature}",
                "X-Correlation-ID": str(correlation_id), "Accept": "application/json"}


class InternalHttp:
    """Bounded requests to one explicitly configured private service origin.

    Retry scheduling belongs to the durable job runner, avoiding multiplied
    retries or new attempt IDs after an ambiguous evidence write timeout.
    """

    def __init__(self, base_url: str, identity: ServiceIdentity, client: httpx.Client, *,
                 timeout_seconds: float = 15) -> None:
        try:
            parsed = urlsplit(base_url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                    or any(c.isspace() or ord(c) < 32 for c in base_url)
                    or "\\" in base_url or not 0 < timeout_seconds <= 60):
                raise ValueError
            parsed.port
        except (ValueError, TypeError):
            raise ValueError("Invalid internal service configuration") from None
        self.base_url, self.identity, self.client = base_url.rstrip("/"), identity, client
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, *, scope: str, correlation_id: UUID,
                json_body: dict | None = None) -> tuple[int, dict]:
        if (method not in ("GET", "POST") or not path.startswith("/internal/v1/")
                or any(c in path for c in ("?", "#", "\\", "\r", "\n"))):
            raise ValueError("Invalid internal operation")
        try:
            response = self.client.request(
                method, self.base_url + path, headers=self.identity.headers(scope, correlation_id),
                json=json_body, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.RequestError:
            raise ProviderError("NETWORK_ERROR") from None
        except (TypeError, ValueError):
            raise ProviderError("INVALID_INTERNAL_REQUEST") from None
        if not 200 <= response.status_code < 300:
            delay = None
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = max(0, (_utc(parsedate_to_datetime(retry_after))
                                        - _utc(self.identity.clock())).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
            raise ProviderError("RATE_LIMITED" if response.status_code == 429 else "INTERNAL_HTTP_ERROR",
                                status=response.status_code, retry_after_seconds=delay)
        try:
            if len(response.content) > 2_000_000:
                raise ValueError
            result = json.loads(response.content, parse_float=Decimal, parse_constant=reject_json_constant,
                                object_pairs_hook=_unique_object)
            if not isinstance(result, dict):
                raise ValueError
            return response.status_code, result
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError("INVALID_INTERNAL_RESPONSE") from None


class HttpGoalReader:
    def __init__(self, http: InternalHttp, *, schema_directory: Path,
                 correlation_id: UUID) -> None:
        if not isinstance(correlation_id, UUID):
            raise ValueError("correlation_id must be UUID")
        self.http, self.correlation_id = http, correlation_id
        self.goal_validator = validator(schema_directory / "goal-spec.v1.schema.json")
        self._owners: dict[UUID, UUID] = {}

    def runtime(self, goal_id: UUID) -> Runtime:
        if not isinstance(goal_id, UUID):
            raise ValueError("goal_id must be UUID")
        status, body = self.http.request(
            "GET", f"/internal/v1/goals/{goal_id}/runtime", scope="gm.runtime.read",
            correlation_id=self.correlation_id,
        )
        try:
            if status != 200 or set(body) != {"goal_id", "current_version", "lifecycle", "owner_user_id"}:
                raise ValueError
            if body["goal_id"] != str(goal_id):
                raise ValueError
            if not isinstance(body["owner_user_id"], str):
                raise ValueError
            owner = UUID(body["owner_user_id"])
            result = Runtime(goal_id, body["current_version"], body["lifecycle"], owner_user_id=owner)
        except (ValueError, TypeError, KeyError):
            raise ProviderError("INVALID_GOAL_RUNTIME") from None
        previous = self._owners.get(goal_id)
        if previous is not None and previous != owner:
            raise ProviderError("GOAL_OWNER_MISMATCH")
        self._owners[goal_id] = owner
        return result

    def version(self, goal_id: UUID, version: int) -> dict:
        if not isinstance(goal_id, UUID) or type(version) is not int or version < 1:
            raise ValueError("Invalid immutable goal identity")
        status, body = self.http.request(
            "GET", f"/internal/v1/goals/{goal_id}/versions/{version}", scope="gm.goal-version.read",
            correlation_id=self.correlation_id,
        )
        try:
            self.goal_validator.validate(body)
            if status != 200 or body["goal_id"] != str(goal_id) or body["version"] != version:
                raise ValueError
            owner = UUID(body["owner_user_id"])
            if goal_id in self._owners and owner != self._owners[goal_id]:
                raise ValueError
        except (ValidationError, ValueError, TypeError, KeyError):
            raise ProviderError("INVALID_GOAL_VERSION") from None
        return deepcopy(body)


def _text(value, *, limit: int = 2000) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    value = value.strip()
    return value if len(value) <= limit else None


def _cents(value) -> int | None:
    if isinstance(value, (float, bool)) or not isinstance(value, (str, Decimal, int)):
        return None
    if isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d+)?", value):
        return None
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            return None
        # Bound untrusted exponent work before Fraction constructs a huge power.
        # Unrepresentable provider amounts remain extraction failures, not prices.
        if number and (number.adjusted() > 30 or number.adjusted() < -2):
            return None
        exact = Fraction(number) * 100
        return int(exact) if exact.denominator == 1 else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def extraction_submission(source: Source, result: ScrapeResult, *, attempt_id: UUID,
                          correlation_id: UUID, resolver=public_addresses) -> dict:
    """Map the documented single-product response to exact ExtractionSubmission.

    A product-page offer is a price per product (pcs), never an inferred kg/L unit
    conversion. Merchant SKU is not an agreed exact-model mapping. Shipping, FI
    eligibility and tax stay unknown; Markdown is bounded inert audit text only.
    """
    if not isinstance(attempt_id, UUID) or not isinstance(correlation_id, UUID):
        raise ValueError("Extraction identifiers must be UUIDs")
    if (result.request_id is not None and (not isinstance(result.request_id, str)
            or len(result.request_id) > 256 or _text(result.request_id, limit=256) != result.request_id)):
        raise ProviderError("INVALID_CAPTURE_METADATA")
    safe_source = canonical_url(source.canonical_url, resolver)
    if canonical_url(result.source_url, resolver) != safe_source:
        raise ProviderError("SOURCE_URL_MISMATCH")
    fetched = _utc(result.fetched_at)
    candidates = []
    if result.cache_age_ms is not None:
        if type(result.cache_age_ms) is not int or result.cache_age_ms < 0:
            raise ProviderError("INVALID_CAPTURE_METADATA")
        try:
            candidates.append(fetched - timedelta(milliseconds=result.cache_age_ms))
            if result.requested_at is not None:
                requested = _utc(result.requested_at)
                if requested > fetched:
                    raise ValueError
                candidates.append(requested - timedelta(milliseconds=result.cache_age_ms))
        except (ValueError, OverflowError):
            raise ProviderError("INVALID_CAPTURE_METADATA") from None
    for value in (result.observed_at, result.provider_observed_at):
        if value is not None:
            observed = _utc(value)
            if observed > fetched:
                raise ProviderError("INVALID_CAPTURE_METADATA")
            candidates.append(observed)
    age = None
    if candidates:
        elapsed = fetched - min(candidates)
        # Round upward to whole milliseconds, preserving an older timestamp.
        microseconds = (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
        age = (microseconds + 999) // 1000
    product = None
    if result.product.success and isinstance(result.product.data, dict):
        if result.product.data.get("isProductPage") is True:
            value = result.product.data.get("product")
            product = value if isinstance(value, dict) else None
    price = _cents(product.get("price")) if product and product.get("currency") == "EUR" else None
    stock = product.get("availability") if product else None
    excerpt = _text(result.markdown.data, limit=2_000_000) if result.markdown.success else None
    return {
        "attempt_id": str(attempt_id), "source_id": str(source.source_id), "source_url": safe_source,
        "provider": "context_dev", "extraction_method": "scrape", "provider_request_id": result.request_id,
        "fetched_at": _timestamp(fetched), "cache_age_ms": age, "correlation_id": str(correlation_id),
        "extracted": {
            "product_name": _text(product.get("name")) if product else None,
            "brand": _text(product.get("brand")) if product else None,
            "model": None, "selling_unit": "pcs" if product else None,
            "unit_price_eur_cents": price,
            "availability": stock if stock in ("in_stock", "out_of_stock") else "unknown",
            "delivery_to_fi": "unknown", "shipping_eur_cents": None, "tax_eur_cents": None,
            "evidence_excerpt": (excerpt or "")[:1000], "source_capture_url": safe_source,
            "evidence_reliability": "unverified",
        },
    }


class HttpEvidenceSink:
    def __init__(self, http: InternalHttp, *, resolver=public_addresses) -> None:
        self.http, self.resolver = http, resolver

    def submit(self, source: Source, result: ScrapeResult, *, attempt_id: UUID,
               correlation_id: UUID) -> dict:
        return self.send_submission(extraction_submission(
            source, result, attempt_id=attempt_id, correlation_id=correlation_id, resolver=self.resolver,
        ))

    def send_submission(self, body: dict) -> dict:
        """Send a persisted exact DTO; the caller preserves it on ambiguous timeout."""
        try:
            correlation = UUID(body["correlation_id"])
            if body["provider"] != "context_dev" or body["extracted"]["evidence_reliability"] != "unverified":
                raise ValueError
            json.dumps(body, allow_nan=False)
        except (ValueError, TypeError, KeyError):
            raise ProviderError("INVALID_EXTRACTION_SUBMISSION") from None
        status, receipt = self.http.request(
            "POST", "/internal/v1/extractions", scope="de.extractions.write",
            correlation_id=correlation, json_body=body,
        )
        try:
            if (set(receipt) - {"accepted", "observation_id", "reason"}
                    or not {"accepted", "reason"} <= set(receipt) or type(receipt["accepted"]) is not bool):
                raise ValueError
            if receipt["accepted"]:
                if status != 201 or receipt["reason"] is not None:
                    raise ValueError
                if not isinstance(receipt.get("observation_id"), str):
                    raise ValueError
                UUID(receipt["observation_id"])
            elif (status != 202 or receipt.get("observation_id") is not None
                  or not isinstance(receipt["reason"], str) or not receipt["reason"]):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ProviderError("INVALID_EXTRACTION_RECEIPT") from None
        return deepcopy(receipt)
