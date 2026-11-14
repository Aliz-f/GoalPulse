"""Private evaluation APIs routed only through authenticated service boundaries."""

from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response

from .auth import ServiceAuthorizer, ServicePrincipal
from .persistence import ImmutableEvaluationReader
from .processing import GoalReader, Runtime
from .readers import InternalNotFound


@dataclass(frozen=True)
class OwnerPrincipal:
    """Gateway-derived owner identity; construction alone is not authentication."""

    subject: str

    def __post_init__(self):
        object.__setattr__(self, 'subject', str(UUID(self.subject)))


def create_app(*, reader: ImmutableEvaluationReader, authorizer: ServiceAuthorizer,
               owner_authorizer: Callable, goals: GoalReader,
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

    @app.get('/v1/goals/{goal_id}/evaluations')
    def evaluation_history(
        goal_id: UUID,
        response: Response,
        limit: int = Query(20, ge=1, le=100),
        principal=Depends(owner_authorizer),
        correlation: UUID = Header(alias='X-Correlation-ID'),
    ):
        if not isinstance(principal, OwnerPrincipal):
            raise HTTPException(401, 'authentication required')
        correlation_id = str(correlation)
        try:
            runtime = goals.runtime(str(goal_id), correlation_id)
        except InternalNotFound:
            raise HTTPException(404, 'goal not found') from None
        except Exception:
            raise HTTPException(503, 'goal ownership unavailable') from None
        if (not isinstance(runtime, Runtime) or runtime.goal_id != str(goal_id)
                or runtime.owner_user_id != principal.subject):
            # Cross-owner and absent goals are deliberately indistinguishable.
            raise HTTPException(404, 'goal not found')
        try:
            results = reader.list_for_goal(str(goal_id), runtime.current_version, limit)
        except Exception:
            raise HTTPException(503, 'evaluation storage unavailable') from None
        response.headers['X-Correlation-ID'] = correlation_id
        return results

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
