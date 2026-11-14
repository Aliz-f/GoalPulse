"""Extractor interface and the resilience wrapper around it (D2-GI-02).

Every model provider is wrapped in :class:`ResilientExtractor`, which enforces
an input size limit, a bounded number of retries with exponential backoff for
transient failures, and converts anything unusable into :class:`ExtractorUnavailable`.
Callers then reject the turn (revision unchanged) instead of guessing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from ..domain.extraction import ExtractionPatch, ExtractionRequest

log = logging.getLogger("goal_intelligence.llm")


class ExtractorError(Exception):
    """Base class; ``code`` is safe to log and store (no prompt, no key)."""

    code = "extractor_error"
    transient = False


class ExtractorTimeout(ExtractorError):
    code, transient = "timeout", True


class ExtractorRateLimited(ExtractorError):
    code, transient = "rate_limited", True


class ExtractorMalformedOutput(ExtractorError):
    code, transient = "malformed_output", True


class ExtractorUnavailable(ExtractorError):
    """Final failure after retries. The turn must not change the draft."""

    code = "unavailable"

    def __init__(self, last_code: str):
        super().__init__(f"extractor unavailable ({last_code})")
        self.last_code = last_code


class InputTooLarge(ExtractorError):
    code = "input_too_large"


class Extractor(Protocol):
    """A provider turns one user message into an untrusted patch."""

    model_id: str

    def extract(self, request: ExtractionRequest, *, timeout_s: float) -> ExtractionPatch: ...


@dataclass
class AttemptRecord:
    model_id: str
    error_code: str | None
    latency_ms: int


def parse_patch(raw: object) -> ExtractionPatch:
    """Strictly validate provider output; any deviation is malformed output."""
    try:
        if isinstance(raw, (str, bytes)):
            return ExtractionPatch.model_validate_json(raw)
        return ExtractionPatch.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        raise ExtractorMalformedOutput("provider output does not match ExtractionPatch") from exc


class ResilientExtractor:
    def __init__(
        self,
        inner: Extractor,
        *,
        max_input_chars: int = 2000,
        max_attempts: int = 3,
        timeout_s: float = 20.0,
        base_backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.max_input_chars = max_input_chars
        self.max_attempts = max(1, max_attempts)
        self.timeout_s = timeout_s
        self.base_backoff_s = base_backoff_s
        self._sleep = sleep
        self.attempts: list[AttemptRecord] = []

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def extract(self, request: ExtractionRequest) -> ExtractionPatch:
        if len(request.message) > self.max_input_chars:
            raise InputTooLarge(f"message longer than {self.max_input_chars} characters")
        self.attempts = []
        last_code = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                patch = self.inner.extract(request, timeout_s=self.timeout_s)
                if not isinstance(patch, ExtractionPatch):
                    patch = parse_patch(patch)
                self.attempts.append(AttemptRecord(self.model_id, None, _ms(started)))
                return patch
            except ExtractorError as exc:
                last_code = exc.code
                self.attempts.append(AttemptRecord(self.model_id, exc.code, _ms(started)))
                # Log only the error class: never the prompt, the response or credentials.
                log.warning("extractor attempt %d/%d failed: %s", attempt, self.max_attempts, exc.code)
                if not exc.transient or attempt == self.max_attempts:
                    break
                self._sleep(self.base_backoff_s * (2 ** (attempt - 1)))
            except Exception:  # noqa: BLE001 - provider bugs must not leak details or mark ready
                last_code = "provider_exception"
                self.attempts.append(AttemptRecord(self.model_id, last_code, _ms(started)))
                log.warning("extractor attempt %d/%d raised an unexpected error", attempt, self.max_attempts)
                break
        raise ExtractorUnavailable(last_code)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
