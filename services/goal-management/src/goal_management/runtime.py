"""Production API composition for Goal Management."""
from __future__ import annotations

import httpx

from .api import create_app as create_api_app
from .clients import GoalIntelligenceClient
from .config import Settings
from .repository import DatabaseRepository
from .tokens import ServiceTokenIssuer


class RuntimeReadiness:
    def __init__(
        self,
        repository: DatabaseRepository,
        *,
        goal_intelligence_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._goal_intelligence_url = goal_intelligence_url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def check(self) -> bool:
        try:
            self._repository.ping()
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(f"{self._goal_intelligence_url}/healthz")
            return response.status_code == 200
        except Exception:
            # Readiness is a fail-closed boundary; fixed response content avoids
            # leaking DSNs, provider URLs, or driver errors.
            return False


def create_app():
    settings = Settings.from_env()
    repository = DatabaseRepository(settings.database_url)
    issuer = ServiceTokenIssuer(
        secret=settings.service_jwt_secret,
        issuer=settings.service_jwt_issuer,
        audience=settings.service_jwt_audience,
    )
    candidates = GoalIntelligenceClient(
        base_url=settings.goal_intelligence_url,
        token_provider=issuer.mint,
        timeout_seconds=settings.internal_timeout_seconds,
    )
    readiness = RuntimeReadiness(
        repository,
        goal_intelligence_url=settings.goal_intelligence_url,
        timeout_seconds=settings.internal_timeout_seconds,
    )
    return create_api_app(repository, candidates, readiness=readiness)
