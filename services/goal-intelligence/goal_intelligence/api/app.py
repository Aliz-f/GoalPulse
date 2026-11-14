"""FastAPI routes: frozen public /v1 session endpoints + ICP-01 internal GM endpoints.

Run: ``uvicorn goal_intelligence.api.app:create_app --factory --port 8102``
(from services/goal-intelligence, with GI_* environment variables set).

Errors use the ICP-01 ``Error`` shape ``{"code", "message"}`` everywhere. The
service returns explicit authentication and input-validation failures. Some of
those public failure responses are not enumerated in frozen Gateway v1; the
Gateway must not expose a different shape without a coordinated contract release.
"""

# No "from __future__ import annotations": FastAPI must see real types on the
# locally defined dependencies below.
import logging
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthError, Authenticator, Caller, GM_CANDIDATE_SCOPE, GM_CONFIRM_SCOPE
from ..config import Settings, build_authenticator, build_service
from ..service import GoalDraftOut, GoalIntelligenceService, ServiceError

log = logging.getLogger("goal_intelligence.api")

SessionId = Annotated[uuid.UUID, Path()]
CORRELATION_HEADER = "X-Correlation-ID"


class CreateSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_message: str = Field(min_length=3)


class PostMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)


class MarkConfirmedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: uuid.UUID
    expected_revision: int = Field(ge=1)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


def _correlation_id(raw: str | None) -> str:
    """Propagate a valid UUID correlation id; mint one only when absent."""
    return str(uuid.uuid4()) if raw is None else str(uuid.UUID(raw))


def create_app(
    service: GoalIntelligenceService | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    settings = Settings()
    svc = service or build_service(settings)
    auth = authenticator or build_authenticator(settings)
    app = FastAPI(title="GoalPulse Goal Intelligence", version="1.0.0", docs_url=None, redoc_url=None)

    def user(
        authorization: Annotated[str | None, Header()] = None,
        x_authenticated_user_id: Annotated[str | None, Header()] = None,
    ) -> Caller:
        return auth.user_caller(authorization, x_authenticated_user_id)

    def gm_candidate(authorization: Annotated[str | None, Header()] = None) -> Caller:
        return auth.internal_caller(authorization, GM_CANDIDATE_SCOPE)

    def gm_confirm(authorization: Annotated[str | None, Header()] = None) -> Caller:
        return auth.internal_caller(authorization, GM_CONFIRM_SCOPE)

    @app.middleware("http")
    async def correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            cid = _correlation_id(request.headers.get(CORRELATION_HEADER))
        except ValueError:
            return error(400, "invalid_correlation_id", "X-Correlation-ID must be a UUID")
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return error(exc.status, "forbidden" if exc.status == 403 else "unauthorized", str(exc))

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        log.info("request %s -> %s", getattr(request.state, "correlation_id", "-"), exc.code)
        return error(exc.status, exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Name the fields, never echo the submitted text back (it may be private).
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return error(422, "invalid_input", "invalid fields: " + ", ".join(fields))

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/goal-sessions", status_code=201, response_model=GoalDraftOut)
    def create_session(body: CreateSessionIn, caller: Annotated[Caller, Depends(user)]) -> GoalDraftOut:
        return svc.create_session(caller.user_id or "", body.initial_message)

    @app.post("/v1/goal-sessions/{session_id}/messages", response_model=GoalDraftOut)
    def post_message(session_id: SessionId, body: PostMessageIn, caller: Annotated[Caller, Depends(user)]) -> GoalDraftOut:
        return svc.post_message(caller.user_id or "", str(session_id), body.message, body.expected_revision)

    @app.get("/v1/goal-sessions/{session_id}", response_model=GoalDraftOut)
    def get_session(session_id: SessionId, caller: Annotated[Caller, Depends(user)]) -> GoalDraftOut:
        return svc.get_session(caller.user_id or "", str(session_id))

    # ---- internal, Goal Management only (ICP-01; never route through the public gateway)

    @app.get("/internal/v1/goal-sessions/{session_id}/candidate", response_model=GoalDraftOut)
    def get_candidate(
        session_id: SessionId,
        expected_revision: Annotated[int, Query(ge=1)],
        _: Annotated[Caller, Depends(gm_candidate)],
    ) -> GoalDraftOut:
        return svc.get_candidate(str(session_id), expected_revision)

    @app.post("/internal/v1/goal-sessions/{session_id}/mark-confirmed", status_code=204)
    def mark_confirmed(
        session_id: SessionId,
        body: MarkConfirmedIn,
        _: Annotated[Caller, Depends(gm_confirm)],
    ) -> Response:
        svc.mark_confirmed(str(session_id), str(body.goal_id), body.expected_revision)
        return Response(status_code=204)

    return app
