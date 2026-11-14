"""App factory; no server/listener is started and no default identity is supplied."""
from typing import Callable
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from .service import AuthenticationRequired, PackageGone, PackageNotFound, PackageReader, Principal


def create_app(reader: PackageReader, authenticate: Callable) -> FastAPI:
    """D1 must inject validated bearer/service auth, never an unsigned user header.

    D1 owns mounting this route behind Gateway. This factory edits no Gateway
    configuration and exposes no other service-private endpoint.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return response

    @app.get('/v1/action-packages/{package_id}')
    def get_package(package_id: UUID, principal: Principal = Depends(authenticate)):
        try:
            package = reader.get(str(package_id), principal)
        except AuthenticationRequired:
            raise HTTPException(401, 'Authentication required') from None
        except PackageNotFound:
            raise HTTPException(404, 'Package not found') from None
        except PackageGone:
            raise HTTPException(410, 'Package is no longer actionable; revalidation is required') from None
        except Exception:
            raise HTTPException(503, 'Package temporarily unavailable') from None
        return JSONResponse(package, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})

    return app
