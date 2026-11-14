import base64
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from fake_evidence_repository import MemoryRepository
from goalpulse_evidence.api import create_app
from goalpulse_evidence.ledger import EvidenceLedger


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


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


class Reads:
    def __init__(self):
        self.item = None

    def by_id(self, observation_id):
        return self.item if self.item and self.item["observation_id"] == str(observation_id) else None

    def by_source(self, source_id, *, limit):
        return [self.item] if self.item and self.item["source_id"] == str(source_id) else []


def payload():
    return {
        "attempt_id": str(uuid4()),
        "source_id": str(uuid4()),
        "source_url": "https://example.com/item",
        "provider": "fixture",
        "extraction_method": "fixture",
        "provider_request_id": "request-1",
        "fetched_at": "2026-11-14T12:00:00Z",
        "cache_age_ms": 0,
        "correlation_id": str(uuid4()),
        "extracted": {
            "product_name": "Synthetic item",
            "brand": None,
            "model": None,
            "selling_unit": "pcs",
            "unit_price_eur_cents": 1000,
            "availability": "unknown",
            "delivery_to_fi": "unknown",
            "shipping_eur_cents": None,
            "tax_eur_cents": None,
            "evidence_excerpt": "Synthetic fixture",
            "source_capture_url": "https://example.com/item",
            "evidence_reliability": "checked",
        },
    }


def request(app, method, path, *, headers=None, body=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://de") as client:
            return await client.request(method, path, headers=headers, json=body)
    return asyncio.run(run())


def test_extraction_endpoint_auth_idempotency_and_reliability_policy(monkeypatch):
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SERVICE_JWT_ISSUER", "goalpulse-tests")
    monkeypatch.setenv("SERVICE_JWT_AUDIENCE", "goalpulse-internal")
    repository, reads = MemoryRepository(), Reads()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    app = create_app(ledger, reads)
    body = payload()
    assert request(app, "POST", "/internal/v1/extractions", body=body).status_code == 403
    headers = {"Authorization": token("de.extractions.write")}
    created = request(app, "POST", "/internal/v1/extractions", body=body, headers=headers)
    assert created.status_code == 201 and created.json()["accepted"] is True
    stored = next(iter(repository.state["observations"].values()))
    assert stored["evidence"]["reliability"] == "unverified"
    reads.item = stored
    read_headers = {"Authorization": token("de.observations.read")}
    by_id = request(app, "GET", f"/internal/v1/observations/{stored['observation_id']}",
                    headers=read_headers)
    listed = request(app, "GET", f"/internal/v1/observations?source_id={stored['source_id']}",
                     headers=read_headers)
    assert by_id.status_code == 200 and by_id.json() == stored
    assert listed.status_code == 200 and listed.json() == {"observations": [stored]}
    replay = request(app, "POST", "/internal/v1/extractions", body=body, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["observation_id"] == created.json()["observation_id"]
    changed = deepcopy(body)
    changed["extracted"]["unit_price_eur_cents"] = 1001
    assert request(app, "POST", "/internal/v1/extractions", body=changed, headers=headers).status_code == 409


def test_missing_price_returns_202_and_observation_reads_are_scoped(monkeypatch):
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SERVICE_JWT_ISSUER", "goalpulse-tests")
    monkeypatch.setenv("SERVICE_JWT_AUDIENCE", "goalpulse-internal")
    repository, reads = MemoryRepository(), Reads()
    ledger = EvidenceLedger(repository, schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW)
    app = create_app(ledger, reads)
    body = payload()
    body["extracted"]["unit_price_eur_cents"] = None
    response = request(app, "POST", "/internal/v1/extractions", body=body,
                       headers={"Authorization": token("de.extractions.write")})
    assert response.status_code == 202
    assert response.json()["reason"] == "MISSING_UNIT_PRICE"
    observation_id = uuid4()
    assert request(app, "GET", f"/internal/v1/observations/{observation_id}").status_code == 403
    assert request(app, "GET", f"/internal/v1/observations/{observation_id}",
                   headers={"Authorization": token("de.observations.read")}).status_code == 404


def test_malformed_extraction_uses_contract_400(monkeypatch):
    monkeypatch.setenv("SERVICE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SERVICE_JWT_ISSUER", "goalpulse-tests")
    monkeypatch.setenv("SERVICE_JWT_AUDIENCE", "goalpulse-internal")
    app = create_app(
        EvidenceLedger(MemoryRepository(), schema_directory=ROOT / "contracts/schemas", clock=lambda: NOW),
        Reads(),
    )
    body = payload()
    body["fetched_at"] = "2026-11-14T12:00:00+00:99"
    response = request(app, "POST", "/internal/v1/extractions", body=body,
                       headers={"Authorization": token("de.extractions.write")})
    assert response.status_code == 400
