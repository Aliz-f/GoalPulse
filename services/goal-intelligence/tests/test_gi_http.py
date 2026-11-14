"""D2-GI-04 HTTP layer. Needs FastAPI + httpx; skipped (NOT RUN) when they are missing."""

from __future__ import annotations

import uuid

import pytest
from conftest import USER_A, USER_B

from test_gi_compiler import PRODUCT_FLOW
from test_gi_endpoints import GM, GW, authenticator


fastapi = pytest.importorskip("fastapi", reason="FastAPI not installed; HTTP layer NOT RUN")
pytest.importorskip("httpx", reason="httpx not installed; HTTP layer NOT RUN")
from fastapi.testclient import TestClient  # noqa: E402

from goal_intelligence.api.app import create_app  # noqa: E402


@pytest.fixture
def client(service):
    return TestClient(create_app(service=service, authenticator=authenticator()))


def as_user(user=USER_A):
    return {"Authorization": f"Bearer {GW}", "X-Authenticated-User-Id": user}


def as_gm():
    return {"Authorization": f"Bearer {GM}"}


def test_http_public_flow_and_status_codes(client):
    r = client.post("/v1/goal-sessions", json={"initial_message": PRODUCT_FLOW[0]}, headers=as_user())
    assert r.status_code == 201
    draft = r.json()
    sid = draft["session_id"]
    for text in PRODUCT_FLOW[1:]:
        r = client.post(f"/v1/goal-sessions/{sid}/messages",
                        json={"message": text, "expected_revision": draft["revision"]}, headers=as_user())
        assert r.status_code == 200
        draft = r.json()
    assert draft["ready_for_confirmation"] is True
    r = client.post(f"/v1/goal-sessions/{sid}/messages", json={"message": "x", "expected_revision": 1}, headers=as_user())
    assert r.status_code == 409
    assert set(r.json()) == {"code", "message"} and r.json()["code"] == "conflict"  # ICP-01 Error shape
    assert client.get(f"/v1/goal-sessions/{sid}", headers=as_user(USER_B)).status_code == 404
    assert client.get(f"/v1/goal-sessions/{sid}").status_code == 401
    r = client.post("/v1/goal-sessions", json={"initial_message": "hi", "owner_user_id": USER_B}, headers=as_user())
    assert r.status_code == 422  # extra fields refused, client cannot set the owner
    assert set(r.json()) == {"code", "message"} and "hi" not in r.json()["message"]
    # the old custom header is not a credential
    assert client.get(f"/v1/goal-sessions/{sid}", headers={"X-Service-Token": GW, "X-Authenticated-User-Id": USER_A}).status_code == 401

    # internal candidate: GM only
    url = f"/internal/v1/goal-sessions/{sid}/candidate?expected_revision={draft['revision']}"
    assert client.get(url, headers={"Authorization": f"Bearer {GW}"}).status_code == 403
    assert client.get(url).status_code == 403  # contract lists only 403 for identity failures
    r = client.get(url, headers=as_gm())
    assert r.status_code == 200 and r.json() == draft
    stale = f"/internal/v1/goal-sessions/{sid}/candidate?expected_revision=1"
    assert client.get(stale, headers=as_gm()).status_code == 409
    assert client.get(f"/internal/v1/goal-sessions/{uuid.uuid4()}/candidate?expected_revision=1", headers=as_gm()).status_code == 404

    body = {"goal_id": str(uuid.uuid4()), "expected_revision": draft["revision"]}
    mc = f"/internal/v1/goal-sessions/{sid}/mark-confirmed"
    assert client.post(mc, json=body, headers=as_gm()).status_code == 204
    assert client.post(mc, json=body, headers=as_gm()).status_code == 204
    assert client.post(mc, json=body).status_code == 403
    unknown = f"/internal/v1/goal-sessions/{uuid.uuid4()}/mark-confirmed"
    assert client.post(unknown, json=body, headers=as_gm()).status_code == 409


def test_correlation_id_is_propagated_or_minted(client):
    cid = str(uuid.uuid4())
    r = client.get(f"/v1/goal-sessions/{uuid.uuid4()}", headers={**as_user(), "X-Correlation-ID": cid})
    assert r.status_code == 404 and r.headers["X-Correlation-ID"] == cid
    minted = client.get("/healthz").headers["X-Correlation-ID"]
    assert str(uuid.UUID(minted)) == minted
    invalid = client.get("/healthz", headers={"X-Correlation-ID": "not-a-uuid"})
    assert invalid.status_code == 400
    assert invalid.json() == {"code": "invalid_correlation_id", "message": "X-Correlation-ID must be a UUID"}
