import base64
import asyncio
import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx

from goalpulse_discovery.api import create_app
from goalpulse_discovery.registry import Source


def _part(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


def token(scope: str, *, secret="test-secret") -> str:
    header = _part({"alg": "HS256", "typ": "JWT"})
    payload = _part({
        "sub": str(uuid4()), "iss": "goalpulse-tests", "aud": "goalpulse-internal",
        "token_kind": "service", "scope": scope, "exp": int(time.time()) + 300,
    })
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"Bearer {header}.{payload}.{signature}"


class Registry:
    def __init__(self):
        self.source = Source(uuid4(), "https://example.com/item", "https://example.com/item", "example.com")
        self.goal_id = uuid4()

    def get(self, source_id):
        return self.source if source_id == self.source.source_id else None

    def subscriptions(self, source_id):
        return [(self.goal_id, 3)] if source_id == self.source.source_id else []


def test_source_reads_require_service_scope_and_match_contract(monkeypatch):
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SERVICE_JWT_ISSUER", "goalpulse-tests")
    monkeypatch.setenv("SERVICE_JWT_AUDIENCE", "goalpulse-internal")
    registry = Registry()
    app = create_app(registry)
    def get(path, *, headers=None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://dm") as client:
                return await client.get(path, headers=headers)
        return asyncio.run(run())
    path = f"/internal/v1/sources/{registry.source.source_id}"
    assert get(path).status_code == 403
    assert get(path, headers={"Authorization": token("wrong.scope")}).status_code == 403
    response = get(path, headers={"Authorization": token("dm.sources.read")})
    assert response.status_code == 200
    assert response.json() == {
        "source_id": str(registry.source.source_id),
        "source_url": registry.source.source_url,
        "canonical_url": registry.source.canonical_url,
        "hostname": registry.source.hostname,
    }
    subscriptions = get(
        path + "/subscriptions", headers={"Authorization": token("dm.sources.read")}
    )
    assert subscriptions.json() == {
        "source_id": str(registry.source.source_id),
        "subscriptions": [{"goal_id": str(registry.goal_id), "goal_version": 3}],
    }
