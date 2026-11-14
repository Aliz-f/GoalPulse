from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infrastructure/runtime"))

from goalpulse_runtime.common import ConfigurationError, ServiceTokenIssuer, service_owner_subject


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


def test_d4_runtime_keeps_reverse_source_binding_fail_closed():
    source = (ROOT / "infrastructure/runtime/goalpulse_runtime/d4.py").read_text(encoding="utf-8")
    assert 'DM_GOAL_SOURCES_BINDING_APPROVED' in source
    assert '/internal/v1/goals/{quote(subscription.goal_id)}/versions/' in source
    assert 'raise RuntimeError("complete source binding is not approved")' in source
