"""Accepted private immutable evaluation read; never publicly routed."""

from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from .auth import ServiceAuthorizer, ServicePrincipal
from .persistence import ImmutableEvaluationReader


def create_app(*, reader: ImmutableEvaluationReader, authorizer: ServiceAuthorizer,
               ready: Callable[[], bool]) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/internal/v1/evaluations/{evaluation_id}')
    def evaluation(evaluation_id: UUID, response: Response,
                   principal: ServicePrincipal = Depends(authorizer),
                   correlation: UUID = Header(alias='X-Correlation-ID')):
        if not isinstance(principal, ServicePrincipal) or authorizer.required_scope not in principal.scopes:
            raise HTTPException(403, 'authorized service identity required')
        try:
            result = reader.get(str(evaluation_id))
        except Exception:
            raise HTTPException(503, 'evaluation storage unavailable') from None
        if result is None:
            raise HTTPException(404, 'evaluation not found')
        response.headers['X-Correlation-ID'] = str(correlation)
        # The reader validates and detaches the exact immutable stored DTO.
        # A GET never reevaluates or changes its historic verification flag.
        return result

    @app.get('/health/live')
    def live():
        return {'status': 'live'}

    @app.get('/health/ready')
    def readiness():
        try:
            healthy = ready() is True
        except Exception:
            healthy = False
        if not healthy:
            raise HTTPException(503, 'evaluation dependencies unavailable')
        return {'status': 'ready'}

    return app
