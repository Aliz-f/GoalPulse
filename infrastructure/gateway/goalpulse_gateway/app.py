"""Minimal security shell; public route proxying belongs to D1-GW-01."""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import AuthenticationError, HS256TokenVerifier, correlation_id


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    verifier = HS256TokenVerifier(
        secret=os.environ["GATEWAY_DEV_JWT_SECRET"],
        issuer=os.environ["GATEWAY_JWT_ISSUER"],
        audience=os.environ["GATEWAY_JWT_AUDIENCE"],
        token_kind="user",
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path == "/health/live":
            return await call_next(request)
        try:
            request.state.principal = verifier.verify(request.headers.get("Authorization"))
            request.state.correlation_id = correlation_id(request.headers.get("X-Correlation-ID"))
        except AuthenticationError as exc:
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    return app
