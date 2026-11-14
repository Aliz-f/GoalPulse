"""Authenticated internal Data & Evidence API from accepted ICP-01."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from .auth import ServiceAuthError, require_service_scope
from .ledger import AttemptConflict, Capture, EvidenceLedger, valid_uri


class ExtractedFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_name: str | None
    brand: str | None = None
    model: str | None = None
    selling_unit: Literal["pcs", "kg", "L"] | None
    unit_price_eur_cents: StrictInt | None = Field(ge=0)
    availability: Literal["in_stock", "out_of_stock", "unknown"]
    delivery_to_fi: Literal["yes", "no", "unknown"]
    shipping_eur_cents: StrictInt | None = Field(ge=0)
    tax_eur_cents: StrictInt | None = Field(ge=0)
    evidence_excerpt: str = Field(max_length=1000)
    source_capture_url: str | None = None
    evidence_reliability: Literal["unverified", "checked", "disputed"] = "unverified"


class ExtractionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID
    source_id: UUID
    source_url: str
    provider: Literal["context_dev", "fixture"]
    extraction_method: Literal["search", "scrape", "monitor", "fixture"]
    provider_request_id: str | None = None
    fetched_at: AwareDatetime
    cache_age_ms: StrictInt | None = Field(default=None, ge=0)
    correlation_id: UUID
    extracted: ExtractedFacts


def _authorize(value: str | None, scope: str) -> None:
    try:
        require_service_scope(value, scope)
    except ServiceAuthError as exc:
        raise HTTPException(403, "service identity required") from exc


def _failure_reason(facts: ExtractedFacts, capture: Capture) -> str | None:
    if capture.observed_at() is None:
        return "UNKNOWN_OBSERVED_AT"
    if facts.unit_price_eur_cents is None:
        return "MISSING_UNIT_PRICE"
    if not facts.product_name or not facts.product_name.strip():
        return "MISSING_PRODUCT_NAME"
    if facts.selling_unit is None:
        return "INVALID_UNIT"
    return None


def create_app(ledger: EvidenceLedger, reads, *, profile: str = "test") -> FastAPI:
    if profile not in ("test", "dev", "demo"):
        raise ValueError("Unsupported runtime profile")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def ensure_runtime_evidence(observation: dict) -> None:
        if profile != "test" and (observation.get("provider") == "fixture"
                                  or observation.get("extraction_method") == "fixture"):
            raise HTTPException(503, "fixture evidence disabled")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # ICP-01 defines malformed extraction/query input as HTTP 400, not 422.
        return JSONResponse(status_code=400, content={"detail": "invalid request"})

    @app.post("/internal/v1/extractions")
    async def submit_extraction(body: ExtractionSubmission, response: Response,
                                authorization: str | None = Header(default=None)):
        _authorize(authorization, "de.extractions.write")
        if profile != "test" and (body.provider == "fixture" or body.extraction_method == "fixture"):
            raise HTTPException(400, "fixture provenance disabled")
        try:
            capture = Capture(
                source_id=body.source_id,
                source_url=body.source_url,
                provider=body.provider,
                extraction_method=body.extraction_method,
                fetched_at=body.fetched_at,
                provider_request_id=body.provider_request_id,
                cache_age_ms=body.cache_age_ms,
                field_sources={
                    "evidence": body.extracted.source_capture_url or body.source_url,
                    "reliability_suggestion": body.extracted.evidence_reliability,
                },
                parser_version="internal-http-v1",
            )
            facts = body.extracted
            if facts.source_capture_url is not None and not valid_uri(facts.source_capture_url):
                raise ValueError("Invalid source capture URL")
            reason = _failure_reason(facts, capture)
            if reason:
                stored = ledger.record_failure(
                    capture,
                    reason=reason,
                    fingerprint_data=body.model_dump(mode="json")["extracted"],
                    correlation_id=body.correlation_id,
                    attempt_id=body.attempt_id,
                )
            else:
                observed_at = capture.observed_at()
                candidate = {
                    "observation_id": str(uuid4()),
                    "source_id": str(body.source_id),
                    "source_url": body.source_url,
                    "provider": body.provider,
                    "extraction_method": body.extraction_method,
                    "provider_request_id": body.provider_request_id,
                    "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                    "product_name": facts.product_name.strip(),
                    "brand": facts.brand,
                    "model": facts.model,
                    "selling_unit": facts.selling_unit,
                    "unit_price_eur_cents": facts.unit_price_eur_cents,
                    "availability": facts.availability,
                    "delivery_to_fi": facts.delivery_to_fi,
                    "shipping_eur_cents": facts.shipping_eur_cents,
                    "tax_eur_cents": facts.tax_eur_cents,
                    "evidence": {
                        "text_excerpt": facts.evidence_excerpt,
                        # ICP-09: only the internal checker can promote a snapshot.
                        "reliability": "disputed" if facts.evidence_reliability == "disputed" else "unverified",
                        "capture_url": facts.source_capture_url,
                    },
                }
                stored = ledger.record(candidate, capture, correlation_id=body.correlation_id,
                                       attempt_id=body.attempt_id)
        except AttemptConflict as exc:
            raise HTTPException(409, "attempt_id reused with different extraction") from exc
        except ValueError as exc:
            raise HTTPException(400, "invalid extraction") from exc
        response.status_code = 201 if stored.accepted else 202
        return {"accepted": stored.accepted, "observation_id": stored.observation_id,
                "reason": stored.reason}

    @app.get("/internal/v1/observations")
    async def observations(source_id: UUID, limit: int = Query(default=20, ge=1, le=100),
                           authorization: str | None = Header(default=None)):
        _authorize(authorization, "de.observations.read")
        found = reads.by_source(source_id, limit=limit)
        for item in found:
            ensure_runtime_evidence(item)
        return {"observations": found}

    @app.get("/internal/v1/observations/{observation_id}")
    async def observation(observation_id: UUID, authorization: str | None = Header(default=None)):
        _authorize(authorization, "de.observations.read")
        found = reads.by_id(observation_id)
        if found is None:
            raise HTTPException(404, "observation not found")
        ensure_runtime_evidence(found)
        return found

    return app
