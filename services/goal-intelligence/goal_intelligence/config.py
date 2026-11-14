"""Environment configuration. No secret has a default value."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .auth import Authenticator, parse_service_tokens
from .llm.base import ResilientExtractor
from .llm.fixture import FixtureExtractor
from .persistence.repository import SqlDraftRepository, postgres_repository, sqlite_repository
from .service import GoalIntelligenceService, Limits


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("GI_DATABASE_URL", ""))
    llm_provider: str = field(default_factory=lambda: os.environ.get("GI_LLM_PROVIDER", "fixture"))
    llm_model: str = field(default_factory=lambda: os.environ.get("GI_LLM_MODEL", "claude-haiku-5-5"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.environ.get("GI_LLM_TIMEOUT_S", "20")))
    llm_max_attempts: int = field(default_factory=lambda: int(os.environ.get("GI_LLM_MAX_ATTEMPTS", "3")))
    max_message_chars: int = field(default_factory=lambda: int(os.environ.get("GI_MAX_MESSAGE_CHARS", "2000")))
    max_user_turns: int = field(default_factory=lambda: int(os.environ.get("GI_MAX_USER_TURNS", "20")))
    service_tokens: str = field(default_factory=lambda: os.environ.get("GI_SERVICE_TOKENS", ""), repr=False)


def build_repository(settings: Settings) -> SqlDraftRepository:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return sqlite_repository(url.removeprefix("sqlite:///"))
    if url.startswith(("postgresql://", "postgres://")):
        return postgres_repository(url)
    raise RuntimeError("GI_DATABASE_URL must be postgresql://... or sqlite:///path")


def build_extractor(settings: Settings) -> ResilientExtractor:
    if settings.llm_provider == "fixture":
        inner = FixtureExtractor()
    elif settings.llm_provider == "anthropic":
        from .llm.anthropic import AnthropicExtractor

        inner = AnthropicExtractor(os.environ.get("ANTHROPIC_API_KEY", ""), settings.llm_model)
    else:
        raise RuntimeError(f"unknown GI_LLM_PROVIDER {settings.llm_provider!r}")
    return ResilientExtractor(
        inner,
        max_input_chars=settings.max_message_chars,
        max_attempts=settings.llm_max_attempts,
        timeout_s=settings.llm_timeout_s,
    )


def build_service(settings: Settings) -> GoalIntelligenceService:
    return GoalIntelligenceService(
        build_repository(settings),
        build_extractor(settings),
        Limits(max_message_chars=settings.max_message_chars, max_user_turns=settings.max_user_turns),
    )


def build_authenticator(settings: Settings) -> Authenticator:
    tokens = parse_service_tokens(settings.service_tokens)
    if not tokens:
        raise RuntimeError("GI_SERVICE_TOKENS must define at least one service token (>=16 chars)")
    return Authenticator(tokens)
