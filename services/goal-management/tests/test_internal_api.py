import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import uuid

import httpx


ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "services/goal-management/src"))

from goal_management.api import create_app
from goal_management.clients import CandidateUnavailable


GOAL = uuid.UUID("ae000000-0000-4000-8000-000000000002")
OWNER = uuid.UUID("ae000000-0000-4000-8000-000000000001")
SESSION = uuid.UUID("ae000000-0000-4000-8000-000000000003")
CORRELATION = "ae000000-0000-4000-8000-000000000099"
SERVICE_SECRET = "service_secret_for_goal_management_tests_32_chars"
USER_SECRET = "user_secret_for_goal_management_tests_32_chars"


class FakeReads:
    def __init__(self):
        self.spec = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text())
        self.created = []
        self.transitions = []
        self.confirmed = None

    def get_confirmed_for_session(self, session_id):
        if self.confirmed is None:
            return None
        spec, revision = self.confirmed
        return (spec, revision, uuid.UUID(spec["owner_user_id"]))

    def get_version(self, goal_id, version):
        return self.spec if goal_id == GOAL and version == 1 else None

    def get_runtime(self, goal_id):
        if goal_id != GOAL:
            return None
        return {"goal_id": str(GOAL), "owner_user_id": str(OWNER), "current_version": 1, "lifecycle": "paused"}

    def list_active(self, cursor, limit):
        return ([{"goal_id": str(GOAL), "current_version": 1}], None)

    def list_for_owner(self, owner, status):
        return []

    def get_for_owner(self, owner, goal_id):
        return self.spec if owner == OWNER and goal_id == GOAL else None

    def transition(self, owner, goal_id, action):
        self.transitions.append((owner, goal_id, action))
        return "ok"

    def create_version_one(self, record):
        self.created.append(record)
        self.confirmed = (record.spec, record.approved_draft_revision)
        return record.spec


class FakeCandidates:
    def __init__(self, candidate=None):
        product = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text())
        candidate_goal = {key: product[key] for key in ("title", "goal_type", "criteria", "monitoring", "actions")}
        self.candidate = candidate or {
            "session_id": str(SESSION),
            "owner_user_id": str(OWNER),
            "revision": 2,
            "state": "ready_for_confirmation",
            "candidate_goal": candidate_goal,
            "missing_fields": [],
            "next_question": None,
            "ready_for_confirmation": True,
        }
        self.confirmed = []

    async def get_candidate(self, session_id, expected_revision, correlation_id):
        return self.candidate

    async def mark_confirmed(self, session_id, goal_id, expected_revision, correlation_id):
        self.confirmed.append((session_id, goal_id, expected_revision, correlation_id))


class FailingMarkCandidates(FakeCandidates):
    async def mark_confirmed(self, session_id, goal_id, expected_revision, correlation_id):
        raise CandidateUnavailable("temporary GI failure")


def bearer(*, kind="service", scope="", subject=OWNER):
    encoder = lambda value: base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    if kind == "service":
        secret, issuer, audience = SERVICE_SECRET, "goalpulse-internal-dev", "goalpulse-internal"
    else:
        secret, issuer, audience = USER_SECRET, "goalpulse-dev", "goalpulse-api"
    header = encoder({"alg": "HS256", "typ": "JWT"})
    payload = encoder({
        "iss": issuer,
        "aud": audience,
        "sub": str(subject),
        "token_kind": kind,
        "scope": scope,
        "exp": 4102444800,
    })
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"Bearer {header}.{payload}.{signature}"


def request(method, path, *, headers=None, body=None, reads=None, candidates=None):
    async def run():
        app = create_app(
            reads or FakeReads(),
            candidates,
            clock=lambda: datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
            goal_id_factory=lambda: GOAL,
        )
        transport = httpx.ASGITransport(app=app)
        request_headers = {"X-Correlation-ID": CORRELATION}
        if headers and "X-Correlation-ID" in headers and headers["X-Correlation-ID"] is None:
            request_headers.pop("X-Correlation-ID")
        request_headers.update({key: value for key, value in (headers or {}).items() if value is not None})
        async with httpx.AsyncClient(transport=transport, base_url="http://gm") as client:
            return await client.request(method, path, headers=request_headers, json=body)

    return asyncio.run(run())


def setup_module():
    os.environ.update(
        SERVICE_JWT_SECRET=SERVICE_SECRET,
        SERVICE_JWT_ISSUER="goalpulse-internal-dev",
        SERVICE_JWT_AUDIENCE="goalpulse-internal",
        GATEWAY_DEV_JWT_SECRET=USER_SECRET,
        GATEWAY_JWT_ISSUER="goalpulse-dev",
        GATEWAY_JWT_AUDIENCE="goalpulse-api",
    )


def test_internal_reads_require_operation_specific_scopes():
    spec = request("GET", f"/internal/v1/goals/{GOAL}/versions/1", headers={"Authorization": bearer(scope="gm.goal-version.read")})
    runtime = request("GET", f"/internal/v1/goals/{GOAL}/runtime", headers={"Authorization": bearer(scope="gm.runtime.read")})
    active = request("GET", "/internal/v1/goals/active?limit=1", headers={"Authorization": bearer(scope="gm.active.read")})
    assert spec.status_code == 200 and spec.json()["goal_id"] == str(GOAL)
    assert runtime.status_code == 200 and runtime.json()["lifecycle"] == "paused"
    assert active.status_code == 200 and "owner_user_id" not in active.text
    assert active.headers["X-Correlation-ID"] == CORRELATION
    assert request("GET", f"/internal/v1/goals/{GOAL}/runtime", headers={"Authorization": bearer(scope="gm.read")}).status_code == 403


def test_internal_auth_rejects_missing_scope_and_non_uuid_subject():
    assert request("GET", f"/internal/v1/goals/{GOAL}/runtime").status_code == 403
    assert request(
        "GET",
        f"/internal/v1/goals/{GOAL}/runtime",
        headers={"Authorization": bearer(scope="gm.runtime.read", subject="not-a-uuid")},
    ).status_code == 403


def test_internal_calls_reject_invalid_correlation_ids_and_generate_missing_ones():
    auth = {"Authorization": bearer(scope="gm.runtime.read")}
    response = request("GET", f"/internal/v1/goals/{GOAL}/runtime", headers={**auth, "X-Correlation-ID": "not-a-uuid"})
    assert response.status_code == 400
    response = request("GET", f"/internal/v1/goals/{GOAL}/runtime", headers={**auth, "X-Correlation-ID": None})
    assert response.status_code == 200
    uuid.UUID(response.headers["X-Correlation-ID"])


def test_confirm_builds_strict_server_owned_spec_and_persists_activation_intent():
    reads = FakeReads()
    candidates = FakeCandidates()
    response = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        reads=reads,
        candidates=candidates,
    )
    assert response.status_code == 201
    assert response.json()["goal_id"] == str(GOAL)
    assert response.json()["owner_user_id"] == str(OWNER)
    assert len(reads.created) == 1
    assert reads.created[0].correlation_id == uuid.UUID(CORRELATION)
    assert candidates.confirmed == [(SESSION, GOAL, 2, uuid.UUID(CORRELATION))]


def test_confirmed_goal_is_not_rolled_back_when_gi_ux_mark_is_unavailable():
    reads = FakeReads()
    response = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        reads=reads,
        candidates=FailingMarkCandidates(),
    )
    assert response.status_code == 201
    assert len(reads.created) == 1


def test_exact_confirmation_replay_returns_durable_goal_without_second_activation():
    reads = FakeReads()
    candidates = FakeCandidates()
    first = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        reads=reads,
        candidates=candidates,
    )
    second = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        reads=reads,
        candidates=candidates,
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(reads.created) == 1
    assert len(candidates.confirmed) == 1

    stale = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 3, "approved": True},
        reads=reads,
        candidates=candidates,
    )
    assert stale.status_code == 409
    foreign = request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user", subject=uuid.uuid4())},
        body={"expected_revision": 2, "approved": True},
        reads=reads,
        candidates=candidates,
    )
    assert foreign.status_code == 404
    assert len(reads.created) == 1


def test_confirm_rejects_stale_foreign_and_invalid_candidates():
    stale = FakeCandidates()
    stale.candidate["revision"] = 3
    assert request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        candidates=stale,
    ).status_code == 409
    foreign = FakeCandidates()
    foreign.candidate["owner_user_id"] = str(uuid.uuid4())
    assert request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        candidates=foreign,
    ).status_code == 404
    invalid = FakeCandidates()
    invalid.candidate["candidate_goal"]["criteria"]["delivery_country"] = "SE"
    assert request(
        "POST",
        f"/v1/goal-sessions/{SESSION}/confirm",
        headers={"Authorization": bearer(kind="user")},
        body={"expected_revision": 2, "approved": True},
        candidates=invalid,
    ).status_code == 422


def test_public_filters_and_lifecycle_bodies_are_strict():
    user_auth = {"Authorization": bearer(kind="user")}
    assert request("GET", "/v1/goals?status=bogus", headers=user_auth).status_code == 422
    assert request(
        "PATCH",
        f"/v1/goals/{GOAL}/lifecycle",
        headers=user_auth,
        body={"action": "pause", "unexpected": "value"},
    ).status_code == 422


def test_public_goal_detail_masks_cross_owner_and_remains_exact_goal_spec():
    own = request(
        "GET",
        f"/v1/goals/{GOAL}",
        headers={"Authorization": bearer(kind="user")},
    )
    foreign = request(
        "GET",
        f"/v1/goals/{GOAL}",
        headers={"Authorization": bearer(kind="user", subject=uuid.uuid4())},
    )
    assert own.status_code == 200
    assert set(own.json()) == {
        "goal_id", "owner_user_id", "version", "title", "goal_type",
        "criteria", "monitoring", "actions", "approved_at",
    }
    assert foreign.status_code == 404
