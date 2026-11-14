"""GoalPulse edge Gateway for the seven frozen v1 routes and demo sessions."""
from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .auth import AuthenticationError, HS256TokenVerifier, Principal, correlation_id
from .config import GatewaySettings
from .proxy import HttpxProxyTransport, ProxyTransport
from .sessions import DemoSession, DemoSessionManager, SessionError
from .tokens import GATEWAY_SERVICE_SUBJECT, DownstreamTokenIssuer


SESSION_COOKIE = "goalpulse_session"
CSRF_COOKIE = "goalpulse_csrf"
CSRF_HEADER = "X-CSRF-Token"
_PUBLIC_PATHS = {"/health/live", "/health/ready", "/v2/demo/sessions"}
_MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class LoginRateLimiter:
    """Small local-demo limiter: five failed attempts per identity per minute."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def limited(self, key: str) -> bool:
        async with self._lock:
            attempts = self._attempts[key]
            cutoff = self._clock() - 60
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            return len(attempts) >= 5

    async def failure(self, key: str) -> None:
        async with self._lock:
            self._attempts[key].append(self._clock())

    async def success(self, key: str) -> None:
        async with self._lock:
            self._attempts.pop(key, None)


def _unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": "unauthorized"})


def _forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "request integrity check failed"})


def _origin_allowed(request: Request, settings: GatewaySettings) -> bool:
    origin = request.headers.get("Origin")
    return origin is not None and hmac.compare_digest(origin, settings.allowed_origin)


def _csrf_valid(request: Request, session: DemoSession) -> bool:
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(header and cookie) and hmac.compare_digest(header, cookie) and hmac.compare_digest(
        header, session.csrf_token
    )


def _set_session_cookies(response: Response, token: str, session: DemoSession, settings: GatewaySettings) -> None:
    secure = settings.allowed_origin.startswith("https://")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.demo_session_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # Double-submit token: intentionally browser-readable, signed and bound to
    # the HttpOnly session's random jti.
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        max_age=settings.demo_session_ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, settings: GatewaySettings) -> None:
    secure = settings.allowed_origin.startswith("https://")
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="lax")


def create_app(
    settings: GatewaySettings | None = None,
    transport: ProxyTransport | None = None,
    *,
    session_clock: Callable[[], float] = time.time,
    rate_limit_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    config = GatewaySettings.from_env() if settings is None else settings
    verifier = HS256TokenVerifier(
        secret=config.gateway_jwt_secret,
        issuer=config.gateway_jwt_issuer,
        audience=config.gateway_jwt_audience,
        token_kind="user",
    )
    issuer = DownstreamTokenIssuer(
        gateway_secret=config.gateway_jwt_secret,
        gateway_issuer=config.gateway_jwt_issuer,
        gateway_audience=config.gateway_jwt_audience,
        service_secret=config.service_jwt_secret,
        service_issuer=config.service_jwt_issuer,
        service_audience=config.service_jwt_audience,
        clock=session_clock,
    )
    proxy_transport = transport or HttpxProxyTransport(config.downstream_timeout_seconds)
    session_manager = (
        DemoSessionManager(
            secret=config.demo_session_secret or "",
            users=config.demo_users,
            ttl_seconds=config.demo_session_ttl_seconds,
            clock=session_clock,
        )
        if config.demo_enabled
        else None
    )
    login_limiter = LoginRateLimiter(clock=rate_limit_clock)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):  # type: ignore[no-untyped-def]
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > config.request_body_limit_bytes:
                    return JSONResponse(status_code=413, content={"detail": "request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid Content-Length"})
        if (
            request.url.path in _PUBLIC_PATHS
            or request.method == "OPTIONS"
            or (request.url.path.startswith("/v2/demo/") and not config.demo_enabled)
        ):
            return await call_next(request)

        try:
            request.state.correlation_id = correlation_id(request.headers.get("X-Correlation-ID"))
        except AuthenticationError:
            return JSONResponse(status_code=400, content={"detail": "invalid correlation ID"})

        authorization = request.headers.get("Authorization")
        if authorization:
            try:
                request.state.principal = verifier.verify(authorization)
                request.state.demo_session = None
            except AuthenticationError:
                return _unauthorized()
        elif session_manager is not None:
            try:
                session = session_manager.verify(request.cookies.get(SESSION_COOKIE))
            except SessionError:
                return _unauthorized()
            request.state.principal = Principal(session.user.user_id, "user", frozenset())
            request.state.demo_session = session
            if request.method in _MUTATING_METHODS and (
                not _origin_allowed(request, config) or not _csrf_valid(request, session)
            ):
                return _forbidden()
        else:
            return _unauthorized()

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> Response:
        correlation = str(uuid.uuid4())
        try:
            gi, gm = await asyncio.gather(
                proxy_transport.request(
                    "GET",
                    f"{config.goal_intelligence_url}/healthz",
                    headers={"X-Correlation-ID": correlation},
                    content=b"",
                    params=[],
                ),
                proxy_transport.request(
                    "GET",
                    f"{config.goal_management_url}/v1/goals",
                    headers={
                        "Authorization": issuer.for_goal_management(GATEWAY_SERVICE_SUBJECT),
                        "X-Correlation-ID": correlation,
                    },
                    content=b"",
                    params=[],
                ),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        if not (200 <= gi.status_code < 300 and 200 <= gm.status_code < 300):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse({"status": "ready"})

    @app.post("/v2/demo/sessions")
    async def login(request: Request) -> Response:
        if session_manager is None:
            return JSONResponse(status_code=503, content={"detail": "demo authentication disabled"})
        if not _origin_allowed(request, config):
            return _forbidden()
        try:
            raw_body = await request.body()
            if len(raw_body) > config.request_body_limit_bytes:
                return JSONResponse(status_code=413, content={"detail": "request body too large"})
            body = json.loads(raw_body)
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                raise ValueError
            username, password = body["username"], body["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError
            if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 1024:
                raise ValueError
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return JSONResponse(status_code=422, content={"detail": "invalid login request"})
        peer = request.client.host if request.client else "unknown"
        key = peer
        if await login_limiter.limited(key):
            return JSONResponse(status_code=429, content={"detail": "too many authentication attempts"})
        user = session_manager.authenticate(username, password)
        if user is None:
            await login_limiter.failure(key)
            return JSONResponse(status_code=401, content={"detail": "invalid credentials"})
        await login_limiter.success(key)
        token, session = session_manager.create(user)
        response = JSONResponse(session.public_json())
        _set_session_cookies(response, token, session, config)
        return response

    @app.get("/v2/demo/session")
    async def current_session(request: Request) -> Response:
        if session_manager is None:
            return JSONResponse(status_code=503, content={"detail": "demo authentication disabled"})
        session: DemoSession | None = request.state.demo_session
        if session is None:
            return _unauthorized()
        return JSONResponse(session.public_json())

    @app.post("/v2/demo/sessions/logout", status_code=204)
    async def logout() -> Response:
        if session_manager is None:
            return JSONResponse(status_code=503, content={"detail": "demo authentication disabled"})
        response = Response(status_code=204)
        _clear_session_cookies(response, config)
        return response

    async def forward(request: Request, *, service: str, path: str) -> Response:
        owner: uuid.UUID = request.state.principal.subject
        if service == "gi":
            base_url = config.goal_intelligence_url
            authorization = issuer.for_goal_intelligence()
            identity_headers = {"X-Authenticated-User-Id": str(owner)}
        else:
            base_url = config.goal_management_url
            authorization = issuer.for_goal_management(owner)
            identity_headers = {}
        body = await request.body()
        if len(body) > config.request_body_limit_bytes:
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        headers = {
            "Authorization": authorization,
            "X-Correlation-ID": str(request.state.correlation_id),
            **identity_headers,
        }
        content_type = request.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        try:
            downstream = await proxy_transport.request(
                request.method,
                f"{base_url}{path}",
                headers=headers,
                content=body,
                params=list(request.query_params.multi_items()),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return JSONResponse(status_code=503, content={"detail": "downstream service unavailable"})
        response_headers = {}
        if "content-type" in downstream.headers:
            response_headers["Content-Type"] = downstream.headers["content-type"]
        return Response(content=downstream.content, status_code=downstream.status_code, headers=response_headers)

    @app.post("/v1/goal-sessions")
    async def create_goal_session(request: Request) -> Response:
        return await forward(request, service="gi", path="/v1/goal-sessions")

    @app.post("/v1/goal-sessions/{session_id}/messages")
    async def send_goal_message(session_id: uuid.UUID, request: Request) -> Response:
        return await forward(request, service="gi", path=f"/v1/goal-sessions/{session_id}/messages")

    @app.get("/v1/goal-sessions/{session_id}")
    async def get_goal_session(session_id: uuid.UUID, request: Request) -> Response:
        return await forward(request, service="gi", path=f"/v1/goal-sessions/{session_id}")

    @app.post("/v1/goal-sessions/{session_id}/confirm")
    async def confirm_goal(session_id: uuid.UUID, request: Request) -> Response:
        return await forward(request, service="gm", path=f"/v1/goal-sessions/{session_id}/confirm")

    @app.get("/v1/goals")
    async def list_goals(request: Request) -> Response:
        return await forward(request, service="gm", path="/v1/goals")

    @app.get("/v1/goals/{goal_id}")
    async def get_goal(goal_id: uuid.UUID, request: Request) -> Response:
        return await forward(request, service="gm", path=f"/v1/goals/{goal_id}")

    @app.patch("/v1/goals/{goal_id}/lifecycle")
    async def update_goal_lifecycle(goal_id: uuid.UUID, request: Request) -> Response:
        return await forward(request, service="gm", path=f"/v1/goals/{goal_id}/lifecycle")

    return app
