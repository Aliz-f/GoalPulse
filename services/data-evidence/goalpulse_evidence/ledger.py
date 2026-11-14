"""Validate already-normalized observations and persist their publication intent.

This is a private storage boundary for D3-DE-01, not ExtractionSubmission or an
HTTP endpoint. Reliability promotion belongs exclusively to the internal checker;
this ingestion boundary cannot accept a checked assertion.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker


def utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone aware")
    return value.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    """Validate RFC3339 even when jsonschema's optional format extras are absent."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        raise ValueError("Expected RFC3339 timestamp with timezone")
    offset = re.search(r"([+-])(\d{2}):(\d{2})$", value)
    if offset and (int(offset.group(2)) > 23 or int(offset.group(3)) > 59):
        raise ValueError("Expected a valid RFC3339 timezone offset")
    return utc(datetime.fromisoformat(value.upper().replace("Z", "+00:00")))


def valid_uri(value: str) -> bool:
    """Portable URI check for frozen fields, without optional validator packages."""
    if (not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) > 127 for c in value)
            or re.search(r'[<>"{}|\\^`]', value)
            or re.search(r"%(?![0-9A-Fa-f]{2})", value)):
        return False
    try:
        parsed = urlsplit(value)
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
            return False
        if parsed.scheme in ("http", "https"):
            if not parsed.hostname:
                return False
            # Accessing this property also validates malformed/out-of-range ports.
            parsed.port
            return True
        return True
    except ValueError:
        return False


def digest(value: Any) -> str:
    """Stable JSON digest; IDs/correlation/fetch time are removed by the caller."""
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Capture:
    """Server-side capture metadata. No raw full-page storage or user identity."""

    source_id: UUID
    source_url: str
    provider: str
    extraction_method: str
    fetched_at: datetime
    provider_request_id: str | None = None
    provider_observed_at: datetime | None = None
    cache_age_ms: int | None = None
    parser_version: str = "storage-v1"
    field_sources: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise ValueError("source_id must be UUID")
        if self.provider not in ("context_dev", "fixture"):
            raise ValueError("Unsupported provider")
        if self.extraction_method not in ("search", "scrape", "monitor", "fixture"):
            raise ValueError("Unsupported extraction method")
        if (not valid_uri(self.source_url) or not self.source_url.startswith(("https://", "http://"))):
            raise ValueError("Invalid source URL")
        utc(self.fetched_at)
        if self.provider_observed_at is not None:
            if utc(self.provider_observed_at) > utc(self.fetched_at):
                raise ValueError("Source capture cannot be later than fetch")
        if self.cache_age_ms is not None and (type(self.cache_age_ms) is not int or self.cache_age_ms < 0):
            raise ValueError("Cache age must be nonnegative whole milliseconds")
        if self.provider_request_id is not None and (
                not isinstance(self.provider_request_id, str) or len(self.provider_request_id) > 256):
            raise ValueError("Invalid provider request ID")
        if (not self.parser_version or len(self.parser_version) > 100
                or len(self.field_sources) > 50
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or len(k) > 100 or len(v) > 1000 for k, v in self.field_sources.items())):
            raise ValueError("Invalid bounded provenance")
        # Copy at the boundary so callers cannot change committed provenance later.
        object.__setattr__(self, "field_sources", deepcopy(dict(self.field_sources)))

    def observed_at(self) -> datetime | None:
        """Use the oldest known capture time, never the webhook receipt time."""
        candidates = []
        if self.provider_observed_at is not None:
            candidates.append(utc(self.provider_observed_at))
        if self.cache_age_ms is not None:
            try:
                candidates.append(utc(self.fetched_at) - timedelta(milliseconds=self.cache_age_ms))
            except OverflowError:
                raise ValueError("Cache age is outside representable capture time") from None
        return min(candidates) if candidates else None


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    capture: Capture
    fingerprint: str
    outcome: str
    error_code: str | None
    created_at: datetime


@dataclass(frozen=True)
class Stored:
    accepted: bool
    attempt_id: str
    observation_id: str | None = None
    duplicate: bool = False
    reason: str | None = None


class AttemptConflict(ValueError):
    """An external idempotency key was reused for different extraction data."""


class Transaction(Protocol):
    def lock_source(self, source_id: str) -> None: ...
    def add_attempt(self, attempt: Attempt) -> tuple[str, bool]: ...
    def add_observation(self, observation: dict, attempt_id: str,
                        fingerprint: str, created_at: datetime) -> tuple[str, bool]: ...
    def add_provenance(self, observation_id: str, capture: Capture) -> None: ...
    def enqueue(self, event: dict, created_at: datetime) -> None: ...


class Repository(Protocol):
    def transaction(self) -> AbstractContextManager[Transaction]: ...


class EvidenceLedger:
    def __init__(self, repository: Repository, *, schema_directory: Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.repository, self.clock = repository, clock
        self.observation_validator = self._validator(schema_directory / "observation.v1.schema.json")
        self.event_validator = self._validator(schema_directory / "domain-event.v1.schema.json")

    @staticmethod
    def _validator(path: Path) -> Draft202012Validator:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        formats = FormatChecker()

        @formats.checks("uri")
        def uri_format(value):
            return not isinstance(value, str) or valid_uri(value)

        @formats.checks("date-time", raises=(ValueError, OverflowError))
        def datetime_format(value):
            if isinstance(value, str):
                parse_timestamp(value)
            return True

        return Draft202012Validator(schema, format_checker=formats)

    def _reason(self, observation: dict | None, capture: Capture, now: datetime) -> str | None:
        if observation is None:
            return "NO_OBSERVATION"
        if observation.get("unit_price_eur_cents") is None:
            return "MISSING_UNIT_PRICE"
        if not self.observation_validator.is_valid(observation):
            return "INVALID_OBSERVATION"
        if observation["evidence"]["reliability"] == "checked":
            return "CALLER_CHECKED"
        # JSON Schema considers 10.0 an integer; money must never use binary floats.
        for name in ("unit_price_eur_cents", "shipping_eur_cents", "tax_eur_cents"):
            if observation[name] is not None and type(observation[name]) is not int:
                return "INVALID_MONEY"
        if any(observation.get(key) != value for key, value in {
            "source_id": str(capture.source_id), "source_url": capture.source_url,
            "provider": capture.provider, "extraction_method": capture.extraction_method,
            "provider_request_id": capture.provider_request_id,
        }.items()):
            return "PROVENANCE_MISMATCH"
        observed_at = capture.observed_at()
        if observed_at is None:
            return "UNKNOWN_OBSERVED_AT"
        if observed_at > now:
            return "FUTURE_OBSERVED_AT"
        try:
            supplied_time = parse_timestamp(observation["observed_at"])
        except ValueError:
            return "INVALID_OBSERVATION"
        if supplied_time != observed_at:
            return "OBSERVED_AT_MISMATCH"
        return None

    def record(self, observation: dict | None, capture: Capture, *, correlation_id: UUID,
               attempt_id: UUID | None = None) -> Stored:
        """Atomically record an attempt, valid snapshot, provenance and outbox event.

        The input is a normalized v1 candidate from private service code. Invalid or
        price-missing JSON is retained only as a digest plus deterministic error code.
        No raw invalid values or validation exception messages enter the ledger.
        """
        if not isinstance(correlation_id, UUID):
            raise ValueError("correlation_id must be UUID")
        if attempt_id is not None and not isinstance(attempt_id, UUID):
            raise ValueError("attempt_id must be UUID")
        observation = deepcopy(observation)
        now = utc(self.clock())
        reason = self._reason(observation, capture, now)
        semantic = {k: v for k, v in (observation or {}).items()
                    if k not in ("observation_id", "provider_request_id", "extraction_method")}
        if reason is None:
            # Equivalent timezone spellings denote the same immutable capture.
            observation["observed_at"] = timestamp(capture.observed_at())
            semantic["observed_at"] = observation["observed_at"]
        attempt_fingerprint = digest({
            "source_id": str(capture.source_id), "source_url": capture.source_url,
            "provider": capture.provider, "method": capture.extraction_method,
            "provider_request_id": capture.provider_request_id,
            "observed_at": timestamp(capture.observed_at()) if capture.observed_at() else None,
            "fallback_fetched_at": timestamp(capture.fetched_at) if capture.observed_at() is None else None,
            "candidate": semantic, "reason": reason,
        })
        attempt = Attempt(str(attempt_id or uuid4()), capture, attempt_fingerprint,
                          "rejected" if reason else "accepted", reason, now)
        with self.repository.transaction() as tx:
            tx.lock_source(str(capture.source_id))
            attempt_id, new_attempt = tx.add_attempt(attempt)
            if reason:
                return Stored(False, attempt_id, duplicate=not new_attempt, reason=reason)
            observation_id, created = tx.add_observation(observation, attempt_id, digest(semantic), now)
            if created:
                tx.add_provenance(observation_id, capture)
                event = {
                    "event_id": str(uuid4()), "event_type": "evidence.updated.v1",
                    "schema_version": 1, "occurred_at": timestamp(now), "producer": "data-evidence",
                    "correlation_id": str(correlation_id), "aggregate_id": str(capture.source_id),
                    "payload": {"source_id": str(capture.source_id), "observation_id": observation_id},
                }
                self.event_validator.validate(event)
                tx.enqueue(event, now)
            return Stored(True, attempt_id, observation_id, duplicate=not created)

    def record_failure(self, capture: Capture, *, reason: str,
                       fingerprint_data: dict, correlation_id: UUID,
                       attempt_id: UUID | None = None) -> Stored:
        """Persist a private normalizer failure without inventing an Observation."""
        allowed = {"UNSUPPORTED_CURRENCY", "UNSUPPORTED_DESTINATION", "MISSING_UNIT_PRICE",
                   "MISSING_PRODUCT_NAME", "INVALID_UNIT", "UNKNOWN_PACKAGING", "INVALID_MONEY",
                   "NON_INTEGRAL_PRICE", "INVALID_FACTS", "CONFLICTING_TAX", "UNKNOWN_OBSERVED_AT"}
        if (reason not in allowed or not isinstance(correlation_id, UUID)
                or (attempt_id is not None and not isinstance(attempt_id, UUID))):
            raise ValueError("Invalid internal failure metadata")
        now = utc(self.clock())
        fingerprint = digest({"source_id": str(capture.source_id), "reason": reason,
                              "request_id": capture.provider_request_id,
                              "fetched_at": timestamp(capture.fetched_at), "facts": fingerprint_data})
        with self.repository.transaction() as tx:
            stored_attempt_id, created = tx.add_attempt(Attempt(str(attempt_id or uuid4()), capture,
                                                        fingerprint, "rejected", reason, now))
            return Stored(False, stored_attempt_id, duplicate=not created, reason=reason)
