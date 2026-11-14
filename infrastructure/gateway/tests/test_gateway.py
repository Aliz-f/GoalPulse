from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest


sys.path.insert(0, str(Path(__file__).parents[1]))

from goalpulse_gateway.app import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, create_app
from goalpulse_gateway.config import ConfigurationError, GatewaySettings
from goalpulse_gateway.sessions import DemoUser, hash_demo_password


SECRET = "g" * 32
SERVICE_SECRET = "s" * 32
OWNER_A = uuid.UUID("ae000000-0000-4000-8000-00000000000a")
OWNER_B = uuid.UUID("ae000000-0000-4000-8000-00000000000b")
SESSION = uuid.UUID("ae000000-0000-4000-8000-000000000011")
GOAL = uuid.UUID("ae000000-0000-4000-8000-000000000012")
CORRELATION = uuid.UUID("ae000000-0000-4000-8000-000000000099")
NOW = 2_000_000_000


def _user(user_id: uuid.UUID, username: str, password: str) -> DemoUser:
    return DemoUser(
        user_id,
        username,
        hash_demo_password(password, salt=(username.encode() + b"0" * 16)[:16]),
        username.title(),
    )


USERS = (_user(OWNER_A, "owner-a", "password-a"), _user(OWNER_B, "owner-b", "password-b"))


def settings(*, profile: str = "demo", body_limit: int = 65_536) -> GatewaySettings:
    return GatewaySettings(
        profile=profile,
        allowed_origin="http://localhost:3000",
        gateway_jwt_secret=SECRET,
        gateway_jwt_issuer="goalpulse-dev",
        gateway_jwt_audience="goalpulse-api",
        service_jwt_secret=SERVICE_SECRET,
        service_jwt_issuer="goalpulse-internal-dev",
        service_jwt_audience="goalpulse-internal",
        goal_intelligence_url="http://goal-intelligence:8000",
        goal_management_url="http://goal-management:8000",
        demo_session_secret="d" * 32 if profile == "demo" else None,
        demo_session_ttl_seconds=1800,
        demo_users=USERS if profile == "demo" else (),
        request_body_limit_bytes=body_limit,
    )


def _decode_claims(authorization: str) -> dict:
    token = authorization.removeprefix("Bearer ")
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))


def user_bearer(owner: uuid.UUID = OWNER_A) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": "goalpulse-dev",
        "aud": "goalpulse-api",
        "sub": str(owner),
        "token_kind": "user",
        "exp": NOW + 300,
    }
    encode = lambda value: base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    head, body = encode(header), encode(claims)
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"Bearer {head}.{body}.{signature}"


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def request(self, method, url, *, headers, content, params):
        call = {"method": method, "url": url, "headers": headers, "content": content, "params": params}
        self.calls.append(call)
        if url.endswith("/healthz"):
            return httpx.Response(200, json={"status": "ok"})
        # Prove that a downstream authorization decision remains opaque.
        owner = headers.get("X-Authenticated-User-Id")
        if owner is None:
            owner = _decode_claims(headers["Authorization"])["sub"]
        if url.endswith(f"/v1/goals/{GOAL}") and owner == str(OWNER_B):
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(
            409 if url.endswith("/confirm") else 200,
            json={"proxied": True, "url": url},
            headers={"content-type": "application/json"},
        )


class UnavailableTransport(FakeTransport):
    async def request(self, method, url, *, headers, content, params):
        if "goal-management" in url:
            raise httpx.ConnectError("unavailable")
        return await super().request(method, url, headers=headers, content=content, params=params)


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app(transport):
    return create_app(settings(), transport, session_clock=lambda: NOW)


async def login(client: httpx.AsyncClient, username="owner-a", password="password-a"):
    return await client.post(
        "/v2/demo/sessions",
        json={"username": username, "password": password},
        headers={"Origin": "http://localhost:3000"},
    )


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return {"Origin": "http://localhost:3000", CSRF_HEADER: client.cookies[CSRF_COOKIE]}


@pytest.mark.anyio
async def test_demo_session_is_cookie_only_http_only_and_csrf_protected(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
    response = await login(client)
    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(OWNER_A),
        "display_name": "Owner-A",
        "expires_at": "2033-05-18T04:03:20Z",
    }
    assert set(response.json()).isdisjoint({"token", "password", "csrf"})
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.startswith(f"{SESSION_COOKIE}="))
    csrf_cookie = next(item for item in cookies if item.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert "HttpOnly" not in csrf_cookie

    assert (await client.get("/v2/demo/session")).status_code == 200
    assert (await client.patch(f"/v1/goals/{GOAL}/lifecycle", json={"action": "pause"})).status_code == 403
    assert (await client.patch(
        f"/v1/goals/{GOAL}/lifecycle", json={"action": "pause"}, headers=csrf_headers(client)
    )).status_code == 200
    assert (await client.post("/v2/demo/sessions/logout", headers=csrf_headers(client))).status_code == 204
    assert (await client.get("/v2/demo/session")).status_code == 401


@pytest.mark.anyio
async def test_login_requires_origin_rejects_bad_credentials_and_rate_limits(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
    assert (await client.post(
        "/v2/demo/sessions", json={"username": "owner-a", "password": "password-a"}
    )).status_code == 403
    for _ in range(5):
        assert (await login(client, password="wrong")).status_code == 401
    assert (await login(client, password="wrong")).status_code == 429


@pytest.mark.anyio
async def test_all_seven_frozen_routes_forward_exactly_and_preserve_status(app, transport):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
    assert (await login(client)).status_code == 200
    mutations = csrf_headers(client)
    cases = [
        ("POST", "/v1/goal-sessions", "/v1/goal-sessions", {"initial_message": "find product"}),
        ("POST", f"/v1/goal-sessions/{SESSION}/messages", f"/v1/goal-sessions/{SESSION}/messages", {"message": "x", "expected_revision": 1}),
        ("GET", f"/v1/goal-sessions/{SESSION}", f"/v1/goal-sessions/{SESSION}", None),
        ("POST", f"/v1/goal-sessions/{SESSION}/confirm", f"/v1/goal-sessions/{SESSION}/confirm", {"expected_revision": 1, "approved": True}),
        ("GET", "/v1/goals?status=active", "/v1/goals", None),
        ("GET", f"/v1/goals/{GOAL}", f"/v1/goals/{GOAL}", None),
        ("PATCH", f"/v1/goals/{GOAL}/lifecycle", f"/v1/goals/{GOAL}/lifecycle", {"action": "pause"}),
    ]
    for method, public_path, downstream_path, body in cases:
        response = await client.request(method, public_path, json=body, headers=mutations if method != "GET" else {})
        assert response.status_code == (409 if public_path.endswith("/confirm") else 200)
        assert transport.calls[-1]["url"].endswith(downstream_path)
    assert transport.calls[4]["params"] == [("status", "active")]


@pytest.mark.anyio
async def test_gateway_replaces_browser_identity_headers_and_mints_expected_tokens(app, transport):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
    assert (await login(client)).status_code == 200
    hostile = {
        **csrf_headers(client),
        "Authorization": "not-forwarded",
        "X-Authenticated-User-Id": str(OWNER_B),
        "X-User-Id": str(OWNER_B),
        "X-Correlation-ID": str(CORRELATION),
    }
    # Invalid Authorization wins over the cookie; callers cannot smuggle one.
    assert (await client.post("/v1/goal-sessions", json={"initial_message": "hello"}, headers=hostile)).status_code == 401
    hostile.pop("Authorization")
    assert (await client.post("/v1/goal-sessions", json={"initial_message": "hello"}, headers=hostile)).status_code == 200
    call = transport.calls[-1]
    claims = _decode_claims(call["headers"]["Authorization"])
    assert claims["token_kind"] == "service" and claims["scope"] == "gi.session.write"
    assert call["headers"]["X-Authenticated-User-Id"] == str(OWNER_A)
    assert call["headers"]["X-Correlation-ID"] == str(CORRELATION)
    assert "X-User-Id" not in call["headers"]

    assert (await client.get(f"/v1/goals/{GOAL}")).status_code == 200
    claims = _decode_claims(transport.calls[-1]["headers"]["Authorization"])
    assert claims["token_kind"] == "user" and claims["sub"] == str(OWNER_A)


@pytest.mark.anyio
async def test_unauthenticated_and_cross_owner_requests_do_not_disclose_resources(transport):
    app = create_app(settings(), transport, session_clock=lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as owner_a:
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as owner_b:
        assert (await owner_a.get(f"/v1/goals/{GOAL}")).status_code == 401
        assert (await login(owner_a, "owner-a", "password-a")).status_code == 200
        assert (await login(owner_b, "owner-b", "password-b")).status_code == 200
        assert (await owner_a.get(f"/v1/goals/{GOAL}")).status_code == 200
        denied = await owner_b.get(f"/v1/goals/{GOAL}")
        assert denied.status_code == 404 and denied.json() == {"detail": "not found"}


@pytest.mark.anyio
async def test_bearer_auth_body_limit_and_disabled_demo_routes(transport):
  app = create_app(settings(profile="dev", body_limit=20), transport, session_clock=lambda: NOW)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as dev:
    assert (await dev.post("/v2/demo/sessions", json={})).status_code == 503
    assert (await dev.get("/v2/demo/session")).status_code == 503
    assert (await dev.post("/v2/demo/sessions/logout")).status_code == 503
    assert (await dev.get("/v2/demo/session", headers={"Authorization": user_bearer()})).status_code == 503
    assert (await dev.get("/v1/goals", headers={"Authorization": user_bearer()})).status_code == 200
    too_large = await dev.post(
        "/v1/goal-sessions",
        content=b"x" * 21,
        headers={"Authorization": user_bearer(), "Content-Type": "application/json"},
    )
    assert too_large.status_code == 413


@pytest.mark.anyio
async def test_readiness_requires_both_goal_services():
    healthy_app = create_app(settings(), FakeTransport(), session_clock=lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=healthy_app), base_url="http://gateway") as client:
        assert (await client.get("/health/ready")).json() == {"status": "ready"}
    blocked_app = create_app(settings(), UnavailableTransport(), session_clock=lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=blocked_app), base_url="http://gateway") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 503 and response.json() == {"status": "not_ready"}


def complete_demo_env() -> dict[str, str]:
    result = {
        "GOALPULSE_PROFILE": "demo",
        "GATEWAY_DEMO_ALLOWED_ORIGIN": "http://localhost:3000",
        "GATEWAY_DEV_JWT_SECRET": SECRET,
        "GATEWAY_JWT_ISSUER": "goalpulse-dev",
        "GATEWAY_JWT_AUDIENCE": "goalpulse-api",
        "SERVICE_JWT_SECRET": SERVICE_SECRET,
        "SERVICE_JWT_ISSUER": "goalpulse-internal-dev",
        "SERVICE_JWT_AUDIENCE": "goalpulse-internal",
        "GOAL_INTELLIGENCE_URL": "http://goal-intelligence:8000",
        "GOAL_MANAGEMENT_URL": "http://goal-management:8000",
        "GATEWAY_DEMO_SESSION_SECRET": "d" * 32,
        "GATEWAY_DEMO_SESSION_TTL_SECONDS": "1800",
        "NEXT_PUBLIC_API_MODE": "gateway",
        "GI_LLM_PROVIDER": "anthropic",
        "GI_LLM_MODEL": "configured-model",
        "ANTHROPIC_API_KEY": "configured-secret",
        "CONTEXT_DEV_API_KEY": "configured-secret",
    }
    for slot, user in zip(("A", "B"), USERS):
        result.update({
            f"GATEWAY_DEMO_USER_{slot}_ID": str(user.user_id),
            f"GATEWAY_DEMO_USER_{slot}_USERNAME": user.username,
            f"GATEWAY_DEMO_USER_{slot}_PASSWORD_HASH": user.password_hash,
            f"GATEWAY_DEMO_USER_{slot}_DISPLAY_NAME": user.display_name,
        })
    return result


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"NEXT_PUBLIC_API_MODE": "mock"}, "NEXT_PUBLIC_API_MODE"),
        ({"GI_LLM_PROVIDER": "fixture"}, "GI_LLM_PROVIDER"),
        ({"DE_EVIDENCE_PROVIDER": "fixture"}, "DE_EVIDENCE_PROVIDER"),
        ({"CONTEXT_DEV_API_KEY": ""}, "CONTEXT_DEV_API_KEY"),
        ({"GATEWAY_DEMO_SESSION_SECRET": ""}, "GATEWAY_DEMO_SESSION_SECRET"),
    ],
)
def test_demo_configuration_fails_closed_without_leaking_values(change, message):
    env = complete_demo_env()
    env.update(change)
    with pytest.raises(ConfigurationError, match=message):
        GatewaySettings.from_env(env)
