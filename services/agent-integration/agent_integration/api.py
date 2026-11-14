"""Private owner-only read handler matching the Phase 2 Gateway extension.

D1 must supply validated service/owner authentication before routing through
Gateway. No header-based identity fallback or write/checkout route exists.
"""

from typing import Callable
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from .service import AuthenticationRequired, PackageGone, PackageNotFound, PackageReader, Principal


def create_app(*, reader: PackageReader, authenticate: Callable,
               ready: Callable[[], bool]) -> FastAPI:
    if reader.evidence is None or reader.contracts is None:
        raise ValueError('HTTP package reads require current-evidence validation')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/v1/action-packages/{package_id}')
    def get_package(package_id: UUID, response: Response,
                    principal=Depends(authenticate),
                    correlation: UUID | None = Header(default=None, alias='X-Correlation-ID')):
        if not isinstance(principal, Principal):
            raise HTTPException(401, 'authentication required')
        correlation_id = str(correlation or uuid4())
        try:
            package = reader.get(str(package_id), principal, correlation_id)
        except AuthenticationRequired:
            raise HTTPException(401, 'authentication required') from None
        except PackageNotFound:
            raise HTTPException(404, 'package not found') from None
        except PackageGone:
            raise HTTPException(410, 'package is no longer usable') from None
        except Exception:
            raise HTTPException(503, 'package dependencies unavailable') from None
        response.headers['X-Correlation-ID'] = correlation_id
        return package

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
            raise HTTPException(503, 'package dependencies unavailable')
        return {'status': 'ready'}

    return app
