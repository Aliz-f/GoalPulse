"""Private Notification HTTP adapter for the Phase 2 Gateway extension.

D1 must supply service-JWT/owner authorization dependencies. There is no
unsigned-header, bearer-user-token or demo-cookie authentication fallback.
Gateway owns browser Origin/CSRF enforcement; the write dependency must enforce
the authenticated forwarding policy before returning a principal.
"""

from datetime import datetime
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .reads import (
    AuthenticationRequired, NotificationAccess, NotificationItem,
    NotificationNotFound, VerifiedPrincipal,
)


class NotificationDTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    notification_id: UUID
    goal_id: UUID
    goal_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    created_at: AwareDatetime
    is_read: bool

    @classmethod
    def from_item(cls, item: NotificationItem) -> 'NotificationDTO':
        return cls(notification_id=UUID(item.notification_id), goal_id=UUID(item.goal_id),
                   goal_version=item.goal_version, title=item.title, summary=item.summary,
                   created_at=item.created_at, is_read=item.is_read)


def create_app(*, store, authorize_read: Callable, authorize_write: Callable,
               clock: Callable[[], datetime], ready: Callable[[], bool]) -> FastAPI:
    """Construct handlers only; never open ports or connect on import.

    The two required authorization dependencies return VerifiedPrincipal after
    validating D1's service identity and Gateway-derived owner context. Passing
    an arbitrary string/dict identity cannot grant access.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def private_cache(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    def access(principal) -> NotificationAccess:
        if not isinstance(principal, VerifiedPrincipal):
            raise HTTPException(401, 'authentication required')
        return NotificationAccess(store, lambda: principal, clock)

    @app.exception_handler(AuthenticationRequired)
    async def unauthorized(request, exc):
        return JSONResponse(status_code=401, content={'detail': 'authentication required'})

    @app.exception_handler(NotificationNotFound)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={'detail': 'notification not found'})

    @app.get('/v1/notifications', response_model=list[NotificationDTO])
    def list_notifications(response: Response, limit: int = Query(20, ge=1, le=100),
                           principal=Depends(authorize_read),
                           correlation: UUID = Header(alias='X-Correlation-ID')):
        service = access(principal)
        try:
            items = [NotificationDTO.from_item(item) for item in service.list(limit=limit).items]
        except AuthenticationRequired:
            raise
        except Exception:
            raise HTTPException(503, 'notification storage unavailable') from None
        response.headers['X-Correlation-ID'] = str(correlation)
        return items

    @app.patch('/v1/notifications/{notification_id}/read', status_code=204)
    def mark_read(notification_id: UUID, principal=Depends(authorize_write),
                  correlation: UUID = Header(alias='X-Correlation-ID')):
        service = access(principal)
        try:
            service.mark_read(str(notification_id))
        except (NotificationNotFound, AuthenticationRequired):
            raise
        except Exception:
            raise HTTPException(503, 'notification storage unavailable') from None
        return Response(status_code=204, headers={'X-Correlation-ID': str(correlation)})

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
            raise HTTPException(503, 'notification dependencies unavailable')
        return {'status': 'ready'}

    return app
