"""Injectable internal HTTP clients owned by Goal Management."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid

import httpx


class CandidateConflict(RuntimeError):
    """The requested draft revision is stale or not ready."""


class CandidateUnavailable(RuntimeError):
    """Goal Intelligence could not serve the candidate read."""


class GoalIntelligenceClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_bearer_token: str | None = None,
        token_provider: Callable[[str], str] | None = None,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if (service_bearer_token is None) == (token_provider is None):
            raise ValueError("configure exactly one service token source")
        self._service_bearer_token = service_bearer_token
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _authorization(self, scope: str) -> str:
        token = (
            self._token_provider(scope)
            if self._token_provider is not None
            else self._service_bearer_token
        )
        return f"Bearer {token}"

    async def get_candidate(
        self,
        session_id: uuid.UUID,
        expected_revision: int,
        correlation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        headers = {
            "Authorization": self._authorization("gi.candidate.read"),
            "X-Correlation-ID": str(correlation_id),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/goal-sessions/{session_id}/candidate",
                    params={"expected_revision": expected_revision},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise CandidateUnavailable from exc
        if response.status_code == 404:
            return None
        if response.status_code == 409:
            raise CandidateConflict
        if response.status_code != 200:
            raise CandidateUnavailable(f"unexpected Goal Intelligence status {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise CandidateUnavailable("Goal Intelligence returned a non-object candidate")
        return payload

    async def mark_confirmed(
        self,
        session_id: uuid.UUID,
        goal_id: uuid.UUID,
        expected_revision: int,
        correlation_id: uuid.UUID,
    ) -> None:
        """Best-effort post-commit UX mark; GM remains authoritative."""
        headers = {
            "Authorization": self._authorization("gi.confirmation.write"),
            "X-Correlation-ID": str(correlation_id),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"/internal/v1/goal-sessions/{session_id}/mark-confirmed",
                    json={"goal_id": str(goal_id), "expected_revision": expected_revision},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise CandidateUnavailable from exc
        if response.status_code == 204:
            return
        if response.status_code == 409:
            raise CandidateConflict("Goal Intelligence rejected the confirmation mark")
        raise CandidateUnavailable(f"unexpected Goal Intelligence status {response.status_code}")
