"""FastAPI routes: frozen public /v1 session endpoints + proposed internal GM endpoints.

Run: ``uvicorn goal_intelligence.api.app:create_app --factory --port 8102``
(from services/goal-intelligence, with GI_* environment variables set).
"""

# No "from __future__ import annotations": FastAPI must see real types on the
# locally defined dependencies below.
import uuid
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthError, Authenticator, Caller
from ..config import Settings, build_authenticator, build_service
from ..service import Conflict, GoalDraftOut, GoalIntelligenceService, ServiceError


SessionId = Annotated[uuid.UUID, Path()]


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


def create_app(
    service: GoalIntelligenceService | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    settings = Settings()
    svc = service or build_service(settings)
    auth = authenticator or build_authenticator(settings)
    app = FastAPI(title="GoalPulse Goal Intelligence", version="1.0.0", docs_url=None, redoc_url=None)

    def user(
        x_service_token: Annotated[str | None, Header()] = None,
        x_authenticated_user_id: Annotated[str | None, Header()] = None,
    ) -> Caller:
        return auth.user_caller(x_service_token, x_authenticated_user_id)

    def gm(x_service_token: Annotated[str | None, Header()] = None) -> Caller:
        return auth.internal_caller(x_service_token)

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
        code = "forbidden" if exc.status == 403 else "unauthorized"
        return JSONResponse({"error": code, "message": str(exc)}, status_code=exc.status)

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.code, "message": str(exc)}
        if isinstance(exc, Conflict) and exc.current_revision is not None:
            body["current_revision"] = exc.current_revision
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo the submitted text back (it may be private).
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return JSONResponse({"error": "invalid_input", "fields": fields}, status_code=422)

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

    # ---- internal, Goal Management only (ICP-01 proposal; do not expose through the gateway)

    @app.get("/internal/v1/goal-sessions/{session_id}/candidate", response_model=GoalDraftOut)
    def get_candidate(
        session_id: SessionId,
        expected_revision: Annotated[int, Query(ge=1)],
        _: Annotated[Caller, Depends(gm)],
    ) -> GoalDraftOut:
        return svc.get_candidate(str(session_id), expected_revision)

    @app.post("/internal/v1/goal-sessions/{session_id}/mark-confirmed", status_code=204)
    def mark_confirmed(session_id: SessionId, body: MarkConfirmedIn, _: Annotated[Caller, Depends(gm)]) -> Response:
        svc.mark_confirmed(str(session_id), str(body.goal_id), body.expected_revision)
        return Response(status_code=204)

    return app
