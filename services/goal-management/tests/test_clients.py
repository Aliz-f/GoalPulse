import asyncio
import json
from pathlib import Path
import sys
import uuid

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from goal_management.clients import CandidateConflict, CandidateUnavailable, GoalIntelligenceClient


SESSION = uuid.UUID("ae000000-0000-4000-8000-000000000003")
CORRELATION = uuid.UUID("ae000000-0000-4000-8000-000000000099")


def call(response_status, response_body=None):
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(response_status, json=response_body)

    client = GoalIntelligenceClient(
        base_url="http://goal-intelligence",
        service_bearer_token="signed-token",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(client.get_candidate(SESSION, 2, CORRELATION))
    return result, seen["request"]


def test_candidate_client_propagates_revision_identity_and_correlation():
    candidate = {"session_id": str(SESSION)}
    result, request = call(200, candidate)
    assert result == candidate
    assert request.url.params["expected_revision"] == "2"
    assert request.headers["Authorization"] == "Bearer signed-token"
    assert request.headers["X-Correlation-ID"] == str(CORRELATION)


def test_candidate_client_maps_not_found_conflict_and_unavailable():
    assert call(404)[0] is None
    with pytest.raises(CandidateConflict):
        call(409)
    with pytest.raises(CandidateUnavailable):
        call(503)


def test_mark_confirmed_posts_exact_revision_goal_and_correlation():
    seen = {}
    goal_id = uuid.UUID("ae000000-0000-4000-8000-000000000004")

    def handler(request):
        seen["request"] = request
        return httpx.Response(204)

    client = GoalIntelligenceClient(
        base_url="http://goal-intelligence",
        service_bearer_token="signed-token",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(client.mark_confirmed(SESSION, goal_id, 2, CORRELATION))
    request = seen["request"]
    assert request.url.path == f"/internal/v1/goal-sessions/{SESSION}/mark-confirmed"
    assert request.headers["Authorization"] == "Bearer signed-token"
    assert request.headers["X-Correlation-ID"] == str(CORRELATION)
    assert json.loads(request.content) == {"goal_id": str(goal_id), "expected_revision": 2}


def test_mark_confirmed_maps_conflict_and_unavailable():
    async def invoke(status):
        client = GoalIntelligenceClient(
            base_url="http://goal-intelligence",
            service_bearer_token="signed-token",
            transport=httpx.MockTransport(lambda _request: httpx.Response(status)),
        )
        await client.mark_confirmed(SESSION, uuid.uuid4(), 2, CORRELATION)

    with pytest.raises(CandidateConflict):
        asyncio.run(invoke(409))
    with pytest.raises(CandidateUnavailable):
        asyncio.run(invoke(503))
