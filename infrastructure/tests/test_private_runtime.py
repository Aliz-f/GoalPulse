from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infrastructure/runtime"))
sys.path.insert(0, str(ROOT / "services/goal-evaluation"))

from goalpulse_runtime.common import ConfigurationError, ServiceTokenIssuer, service_owner_subject
from goalpulse_runtime.d4 import _goal_sources_reader
from goal_evaluation.processing import RetryableUpstreamError, Subscription


def environment() -> dict[str, str]:
    return {
        "SERVICE_JWT_SECRET": "s" * 32,
        "SERVICE_JWT_ISSUER": "goalpulse-internal-dev",
        "SERVICE_JWT_AUDIENCE": "goalpulse-internal",
        "PLATFORM_SERVICE_ID": "00000000-0000-4000-8000-000000000009",
    }


def test_platform_tokens_are_short_lived_scoped_and_not_owner_forgeable(monkeypatch):
    for name, value in environment().items():
        monkeypatch.setenv(name, value)
    token = ServiceTokenIssuer().mint("ge.evaluations.read")
    authorization = f"Bearer {token}"
    assert service_owner_subject(authorization, "ge.evaluations.read") == environment()["PLATFORM_SERVICE_ID"]
    with pytest.raises(PermissionError):
        service_owner_subject(authorization, "dm.sources.read")


def test_platform_identity_configuration_fails_closed(monkeypatch):
    for name, value in environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SERVICE_JWT_SECRET", "change_this_local_service_hs256_secret_at_least_32_chars")
    with pytest.raises(ConfigurationError, match="SERVICE_JWT_SECRET"):
        ServiceTokenIssuer()
    monkeypatch.setenv("SERVICE_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("PLATFORM_SERVICE_ID", "not-a-uuid")
    with pytest.raises(ValueError):
        ServiceTokenIssuer()


def test_d4_runtime_uses_the_approved_bounded_reverse_source_binding():
    source = (ROOT / "infrastructure/runtime/goalpulse_runtime/d4.py").read_text(encoding="utf-8")
    assert '/internal/v1/goals/{quote(subscription.goal_id)}/versions/' in source
    assert 'issuer.mint("dm.sources.read")' in source
    assert 'len(source_ids) > 3' in source
    assert 'normalized != sorted(normalized)' in source
    assert 'DM_GOAL_SOURCES_BINDING_APPROVED' not in source


def test_goal_sources_reader_propagates_identity_scope_and_correlation():
    goal_id = str(uuid.uuid4())
    correlation = str(uuid.uuid4())
    sources = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
    calls = []

    class Issuer:
        def mint(self, scope):
            assert scope == "dm.sources.read"
            return "scoped-token"

    def get(url, token, *, headers):
        calls.append((url, token, headers))
        return 200, {"goal_id": goal_id, "goal_version": 2, "source_ids": sources}

    reader = _goal_sources_reader("http://discovery-monitoring:8000", Issuer(), get=get)
    assert reader(Subscription(goal_id, 2), correlation) == sources
    assert calls == [(
        f"http://discovery-monitoring:8000/internal/v1/goals/{goal_id}/versions/2/sources",
        "scoped-token",
        {"X-Correlation-ID": correlation},
    )]


@pytest.mark.parametrize(
    "status,body,error",
    [
        (503, None, RetryableUpstreamError),
        (404, None, ValueError),
        (200, {"goal_id": str(uuid.uuid4()), "goal_version": 1, "source_ids": []}, ValueError),
        (200, None, ValueError),
    ],
)
def test_goal_sources_reader_fails_closed(status, body, error):
    goal_id, correlation = str(uuid.uuid4()), str(uuid.uuid4())

    class Issuer:
        def mint(self, _scope):
            return "token"

    reader = _goal_sources_reader(
        "http://dm", Issuer(), get=lambda *_args, **_kwargs: (status, body)
    )
    with pytest.raises(error):
        reader(Subscription(goal_id, 1), correlation)


def test_goal_sources_reader_rejects_duplicates_unsorted_and_over_bound():
    goal_id, correlation = str(uuid.uuid4()), str(uuid.uuid4())

    class Issuer:
        def mint(self, _scope):
            return "token"

    def reader_for(source_ids):
        body = {"goal_id": goal_id, "goal_version": 1, "source_ids": source_ids}
        return _goal_sources_reader(
            "http://dm", Issuer(), get=lambda *_args, **_kwargs: (200, body)
        )

    source = str(uuid.uuid4())
    with pytest.raises(ValueError, match="unique and ordered"):
        reader_for([source, source])(Subscription(goal_id, 1), correlation)
    descending = sorted([str(uuid.uuid4()), str(uuid.uuid4())], reverse=True)
    with pytest.raises(ValueError, match="unique and ordered"):
        reader_for(descending)(Subscription(goal_id, 1), correlation)
    with pytest.raises(ValueError, match="bounds"):
        reader_for([str(uuid.uuid4()) for _ in range(4)])(
            Subscription(goal_id, 1), correlation
        )
