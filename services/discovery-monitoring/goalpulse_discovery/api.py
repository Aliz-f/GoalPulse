"""Authenticated internal Discovery & Monitoring reads from accepted ICP-01."""

from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .auth import ServiceAuthError, require_service_scope


def _authorize(value: str | None) -> None:
    try:
        require_service_scope(value, "dm.sources.read")
    except ServiceAuthError as exc:
        raise HTTPException(403, "service identity required") from exc


def _correlation(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, "X-Correlation-ID must be a UUID") from None


def create_app(registry, *, readiness=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready():
        if readiness is None or not readiness():
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    @app.middleware("http")
    async def dependency_failure(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            # Catch before server error logging can expose driver/DSN details.
            return JSONResponse(status_code=503, content={"detail": "discovery dependency unavailable"})

    @app.get("/internal/v1/sources/{source_id}/subscriptions")
    async def subscriptions(source_id: UUID, authorization: str | None = Header(default=None)):
        _authorize(authorization)
        if registry.get(source_id) is None:
            raise HTTPException(404, "source not found")
        return {
            "source_id": str(source_id),
            "subscriptions": [
                {"goal_id": str(goal_id), "goal_version": version}
                for goal_id, version in registry.subscriptions(source_id)
            ],
        }

    @app.get("/internal/v1/sources/{source_id}")
    async def source(source_id: UUID, authorization: str | None = Header(default=None)):
        _authorize(authorization)
        found = registry.get(source_id)
        if found is None:
            raise HTTPException(404, "source not found")
        return {
            "source_id": str(found.source_id),
            "source_url": found.source_url,
            "canonical_url": found.canonical_url,
            "hostname": found.hostname,
        }

    @app.get("/internal/v1/goals/{goal_id}/versions/{version}/sources")
    async def goal_sources(
        goal_id: UUID,
        version: int,
        authorization: str | None = Header(default=None),
        x_correlation_id: str | None = Header(default=None),
    ):
        _authorize(authorization)
        _correlation(x_correlation_id)
        if version < 1:
            raise HTTPException(404, "goal version not found")
        source_ids = registry.sources_for_goal(goal_id, version)
        if source_ids is None:
            raise HTTPException(404, "goal version not found")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) > 3
            or not all(isinstance(source_id, UUID) for source_id in source_ids)
            or len(set(source_ids)) != len(source_ids)
            or source_ids != sorted(source_ids, key=str)
        ):
            raise RuntimeError("goal source state violates the approved contract")
        return {
            "goal_id": str(goal_id),
            "goal_version": version,
            "source_ids": [str(source_id) for source_id in source_ids],
        }

    return app
