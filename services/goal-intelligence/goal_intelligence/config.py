"""Environment configuration (R2-D2-01, ADR-0003 MVP-03/04/05). No secret has a default.

Runtime profile ``GOALPULSE_PROFILE`` is required and must be ``test``, ``dev`` or ``demo``:

- ``demo`` fails closed: the LLM provider must be ``anthropic`` with a non-empty
  ``GI_LLM_MODEL`` and ``ANTHROPIC_API_KEY``. The fixture extractor is refused.
- ``test``/``dev`` may use ``GI_LLM_PROVIDER=fixture``, but only when set explicitly.

There is no default provider and no default model: the model must be one your Anthropic
account is entitled to. Provider failures never switch to another provider.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .auth import Authenticator, ServiceJwtVerifier
from .llm.base import ResilientExtractor
from .llm.fixture import FixtureExtractor
from .persistence.repository import SqlDraftRepository, postgres_repository, sqlite_repository
from .service import GoalIntelligenceService, Limits

log = logging.getLogger("goal_intelligence.config")

PROFILES = ("test", "dev", "demo")
PROVIDERS = ("anthropic", "fixture")


class ConfigError(RuntimeError):
    """Invalid or incomplete runtime configuration. Messages never contain secret values."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class Settings:
    profile: str = field(default_factory=lambda: _env("GOALPULSE_PROFILE"))
    database_url: str = field(default_factory=lambda: _env("GI_DATABASE_URL"), repr=False)
    llm_provider: str = field(default_factory=lambda: _env("GI_LLM_PROVIDER"))
    llm_model: str = field(default_factory=lambda: _env("GI_LLM_MODEL"))
    # Optional. Only for models that support the effort parameter (e.g. claude-haiku-5-5).
    llm_effort: str = field(default_factory=lambda: _env("GI_LLM_EFFORT"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"), repr=False)
    llm_timeout_s: float = field(default_factory=lambda: float(os.environ.get("GI_LLM_TIMEOUT_S", "20")))
    llm_max_attempts: int = field(default_factory=lambda: int(os.environ.get("GI_LLM_MAX_ATTEMPTS", "3")))
    max_message_chars: int = field(default_factory=lambda: int(os.environ.get("GI_MAX_MESSAGE_CHARS", "2000")))
    max_user_turns: int = field(default_factory=lambda: int(os.environ.get("GI_MAX_USER_TURNS", "20")))
    service_jwt_secret: str = field(default_factory=lambda: os.environ.get("SERVICE_JWT_SECRET", ""), repr=False)
    service_jwt_issuer: str = field(default_factory=lambda: os.environ.get("SERVICE_JWT_ISSUER", ""))
    service_jwt_audience: str = field(default_factory=lambda: os.environ.get("SERVICE_JWT_AUDIENCE", ""))


def validate_settings(settings: Settings) -> None:
    """Fail closed on any configuration the profile does not allow. Raises ConfigError."""
    problems: list[str] = []
    if settings.profile not in PROFILES:
        problems.append(f"GOALPULSE_PROFILE must be one of {', '.join(PROFILES)}")
    if settings.llm_provider not in PROVIDERS:
        problems.append("GI_LLM_PROVIDER must be set explicitly to 'anthropic' or 'fixture'")
    if settings.profile == "demo" and settings.llm_provider != "anthropic":
        problems.append("profile 'demo' requires GI_LLM_PROVIDER=anthropic; the fixture extractor is refused")
    if settings.llm_provider == "anthropic":
        if not settings.llm_model:
            problems.append("GI_LLM_MODEL is required for the anthropic provider (use a model your account can access)")
        if not settings.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is required for the anthropic provider")
    if settings.llm_effort and settings.llm_effort not in ("low", "medium", "high"):
        problems.append("GI_LLM_EFFORT must be low, medium or high when set")
    if not 1 <= settings.llm_max_attempts <= 5:
        problems.append("GI_LLM_MAX_ATTEMPTS must be between 1 and 5")
    if not 1 <= settings.llm_timeout_s <= 120:
        problems.append("GI_LLM_TIMEOUT_S must be between 1 and 120 seconds")
    if problems:
        raise ConfigError("Goal Intelligence configuration rejected: " + "; ".join(problems))


def runtime_disclosure(settings: Settings) -> dict[str, str]:
    """Non-secret description of the active runtime, logged at startup and served by /healthz."""
    info = {"profile": settings.profile, "llm_provider": settings.llm_provider}
    if settings.llm_provider == "anthropic":
        info["llm_model"] = settings.llm_model
        info["goal_text_sent_to"] = "Anthropic (Messages API)"
    else:
        info["goal_text_sent_to"] = "nobody (offline fixture; test/dev only)"
    return info


def build_repository(settings: Settings) -> SqlDraftRepository:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return sqlite_repository(url.removeprefix("sqlite:///"))
    if url.startswith(("postgresql://", "postgres://")):
        return postgres_repository(url)
    raise ConfigError("GI_DATABASE_URL must be postgresql://... or sqlite:///path")


def build_extractor(settings: Settings) -> ResilientExtractor:
    validate_settings(settings)
    if settings.llm_provider == "anthropic":
        from .llm.anthropic import AnthropicExtractor

        inner = AnthropicExtractor(settings.anthropic_api_key, settings.llm_model, effort=settings.llm_effort or None)
    else:  # validated: fixture, and only outside the demo profile
        inner = FixtureExtractor()
    return ResilientExtractor(
        inner,
        max_input_chars=settings.max_message_chars,
        max_attempts=settings.llm_max_attempts,
        timeout_s=settings.llm_timeout_s,
    )


def build_service(settings: Settings) -> GoalIntelligenceService:
    extractor = build_extractor(settings)  # validates before touching the database
    service = GoalIntelligenceService(
        build_repository(settings),
        extractor,
        Limits(max_message_chars=settings.max_message_chars, max_user_turns=settings.max_user_turns),
    )
    log.info("goal intelligence runtime: %s", runtime_disclosure(settings))
    return service


def build_authenticator(settings: Settings) -> Authenticator:
    try:
        verifier = ServiceJwtVerifier(
            secret=settings.service_jwt_secret,
            issuer=settings.service_jwt_issuer,
            audience=settings.service_jwt_audience,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return Authenticator(verifier)
