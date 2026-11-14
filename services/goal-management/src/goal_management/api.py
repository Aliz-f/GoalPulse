"""Authenticated Goal Management HTTP API."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Literal, Protocol
import uuid

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field
import psycopg

from .clients import CandidateConflict, CandidateUnavailable
from .repository import GoalVersionRecord


GoalStatus = Literal["active", "paused", "completed", "cancelled"]


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    approved: Literal[True]


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "resume", "cancel", "complete"]


class Reads(Protocol):
    def get_version(self, goal_id: uuid.UUID, version: int) -> dict[str, Any] | None: ...
    def get_runtime(self, goal_id: uuid.UUID) -> dict[str, Any] | None: ...
    def list_active(self, cursor: uuid.UUID | None, limit: int) -> tuple[list[dict[str, Any]], uuid.UUID | None]: ...
    def list_for_owner(self, owner: uuid.UUID, status: str | None) -> list[dict[str, Any]]: ...
    def get_for_owner(self, owner: uuid.UUID, goal_id: uuid.UUID) -> dict[str, Any] | None: ...
    def transition(self, owner: uuid.UUID, goal_id: uuid.UUID, action: str) -> str: ...
    def create_version_one(self, record: GoalVersionRecord) -> None: ...


class CandidateReader(Protocol):
    async def get_candidate(
        self,
        session_id: uuid.UUID,
        expected_revision: int,
        correlation_id: uuid.UUID,
    ) -> dict[str, Any] | None: ...


_ROOT = Path(__file__).resolve().parents[4]
_GOAL_SPEC_VALIDATOR = Draft202012Validator(
    json.loads((_ROOT / "contracts" / "schemas" / "goal-spec.v1.schema.json").read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)
_GOAL_DRAFT_VALIDATOR = Draft202012Validator(
    json.loads((_ROOT / "contracts" / "schemas" / "goal-draft.v1.schema.json").read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)


def _decode_json_segment(value: str) -> dict[str, Any]:
    decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    if not isinstance(decoded, dict):
        raise ValueError("JWT segment must be an object")
    return decoded


def _verified_claims(value: str | None, *, secret: str, issuer: str, audience: str, token_kind: str) -> dict[str, Any]:
    if not value or not value.startswith("Bearer "):
        raise ValueError("missing bearer token")
    header_segment, payload_segment, signature_segment = value[7:].split(".")
    header = _decode_json_segment(header_segment)
    claims = _decode_json_segment(payload_segment)
    signature = base64.urlsafe_b64decode(signature_segment + "=" * (-len(signature_segment) % 4))
    expected = hmac.new(secret.encode(), f"{header_segment}.{payload_segment}".encode(), hashlib.sha256).digest()
    audience_claim = claims.get("aud")
    audience_matches = audience_claim == audience or (isinstance(audience_claim, list) and audience in audience_claim)
    now = int(time.time())
    if (
        header.get("alg") != "HS256"
        or header.get("typ") != "JWT"
        or not hmac.compare_digest(signature, expected)
        or claims.get("iss") != issuer
        or not audience_matches
        or claims.get("token_kind") != token_kind
        or not isinstance(claims.get("exp"), int)
        or claims["exp"] <= now
        or (isinstance(claims.get("nbf"), int) and claims["nbf"] > now)
    ):
        raise ValueError("invalid bearer token")
    uuid.UUID(str(claims["sub"]))
    return claims


def _user(value: str | None) -> uuid.UUID:
    try:
        claims = _verified_claims(
            value,
            secret=os.environ["GATEWAY_DEV_JWT_SECRET"],
            issuer=os.environ["GATEWAY_JWT_ISSUER"],
            audience=os.environ["GATEWAY_JWT_AUDIENCE"],
            token_kind="user",
        )
        return uuid.UUID(str(claims["sub"]))
    except Exception as exc:
        raise HTTPException(401, "unauthorized") from exc


def _service_token(value: str | None, required_scope: str) -> uuid.UUID:
    try:
        claims = _verified_claims(
            value,
            secret=os.environ["SERVICE_JWT_SECRET"],
            issuer=os.environ["SERVICE_JWT_ISSUER"],
            audience=os.environ["SERVICE_JWT_AUDIENCE"],
            token_kind="service",
        )
        raw_scopes = claims.get("scope", "")
        scopes = raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes) or required_scope not in scopes:
            raise ValueError("service scope is not permitted")
        return uuid.UUID(str(claims["sub"]))
    except Exception as exc:
        raise HTTPException(403, "service identity required") from exc


def _correlation_id(value: str | None) -> uuid.UUID:
    return uuid.uuid4() if value is None else uuid.UUID(value)


def _assemble_goal_spec(
    candidate: dict[str, Any],
    *,
    goal_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    approved_at: datetime,
) -> dict[str, Any]:
    candidate_goal = candidate.get("candidate_goal")
    if not isinstance(candidate_goal, dict):
        raise ValueError("candidate_goal must be an object")
    allowed_fields = {"title", "goal_type", "criteria", "monitoring", "actions"}
    if set(candidate_goal) != allowed_fields:
        raise ValueError("candidate_goal must contain the exact canonical goal fields")
    goal_spec = {
        "goal_id": str(goal_id),
        "owner_user_id": str(owner_user_id),
        "version": 1,
        **candidate_goal,
        "approved_at": approved_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    errors = sorted(_GOAL_SPEC_VALIDATOR.iter_errors(goal_spec), key=lambda error: list(error.path))
    if errors:
        raise ValueError(errors[0].message)
    if goal_spec["criteria"]["delivery_country"] != "FI":
        raise ValueError("Phase 1 activation requires delivery_country=FI")
    return goal_spec


def create_app(
    reads: Reads,
    candidates: CandidateReader | None = None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    goal_id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def internal_correlation(request: Request, call_next):
        if not request.url.path.startswith("/internal/"):
            return await call_next(request)
        try:
            correlation = _correlation_id(request.headers.get("X-Correlation-ID"))
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "X-Correlation-ID must be a UUID"})
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(correlation)
        return response

    @app.get("/internal/v1/goals/{goal_id}/versions/{version}")
    async def version(goal_id: uuid.UUID, version: int, authorization: str | None = Header(default=None)):
        _service_token(authorization, "gm.goal-version.read")
        result = reads.get_version(goal_id, version)
        if result is None:
            raise HTTPException(404, "version not found")
        return result

    @app.get("/internal/v1/goals/{goal_id}/runtime")
    async def runtime(goal_id: uuid.UUID, authorization: str | None = Header(default=None)):
        _service_token(authorization, "gm.runtime.read")
        result = reads.get_runtime(goal_id)
        if result is None:
            raise HTTPException(404, "goal not found")
        return result

    @app.get("/internal/v1/goals/active")
    async def active(
        cursor: uuid.UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        _service_token(authorization, "gm.active.read")
        items, next_cursor = reads.list_active(cursor, limit)
        return {"items": items, "next_cursor": None if next_cursor is None else str(next_cursor)}

    @app.post("/v1/goal-sessions/{session_id}/confirm", status_code=201)
    async def confirm(
        session_id: uuid.UUID,
        body: ConfirmRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        owner = _user(authorization)
        if candidates is None:
            raise HTTPException(503, "goal intelligence unavailable")
        correlation = _correlation_id(request.headers.get("X-Correlation-ID"))
        try:
            candidate = await candidates.get_candidate(session_id, body.expected_revision, correlation)
        except CandidateConflict as exc:
            raise HTTPException(409, "draft incomplete or stale") from exc
        except CandidateUnavailable as exc:
            raise HTTPException(503, "goal intelligence unavailable") from exc
        if candidate is not None:
            draft_errors = sorted(_GOAL_DRAFT_VALIDATOR.iter_errors(candidate), key=lambda error: list(error.path))
            if draft_errors:
                raise HTTPException(422, "GoalDraft validation failed")
        if candidate is None or candidate.get("owner_user_id") != str(owner):
            raise HTTPException(404, "session not found")
        if (
            candidate.get("session_id") != str(session_id)
            or candidate.get("revision") != body.expected_revision
            or candidate.get("state") != "ready_for_confirmation"
            or candidate.get("ready_for_confirmation") is not True
            or candidate.get("missing_fields") != []
            or candidate.get("next_question") is not None
        ):
            raise HTTPException(409, "draft incomplete or stale")
        goal_id = goal_id_factory()
        approved_at = clock()
        try:
            goal_spec = _assemble_goal_spec(candidate, goal_id=goal_id, owner_user_id=owner, approved_at=approved_at)
            reads.create_version_one(
                GoalVersionRecord(
                    goal_id=goal_id,
                    owner_user_id=owner,
                    confirmed_session_id=session_id,
                    version=1,
                    spec=goal_spec,
                    approved_at=approved_at,
                    approved_draft_revision=body.expected_revision,
                    correlation_id=correlation,
                )
            )
        except ValueError as exc:
            raise HTTPException(422, "GoalSpec validation failed") from exc
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(409, "session already confirmed") from exc
        return goal_spec

    @app.get("/v1/goals")
    async def goals(status: GoalStatus | None = Query(default=None), authorization: str | None = Header(default=None)):
        return reads.list_for_owner(_user(authorization), status)

    @app.get("/v1/goals/{goal_id}")
    async def goal(goal_id: uuid.UUID, authorization: str | None = Header(default=None)):
        result = reads.get_for_owner(_user(authorization), goal_id)
        if result is None:
            raise HTTPException(404, "not found")
        return result

    @app.patch("/v1/goals/{goal_id}/lifecycle", status_code=204)
    async def lifecycle(goal_id: uuid.UUID, body: LifecycleRequest, authorization: str | None = Header(default=None)):
        outcome = reads.transition(_user(authorization), goal_id, body.action)
        if outcome == "missing":
            raise HTTPException(404, "not found")
        if outcome == "invalid":
            raise HTTPException(409, "invalid lifecycle transition")

    return app
