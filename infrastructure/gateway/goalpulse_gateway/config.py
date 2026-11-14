"""Validated Gateway runtime configuration.

Configuration errors intentionally name keys, never values.  The demo profile
is local-only and fails closed when any real-provider or identity setting is
missing.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from .sessions import DemoUser, validate_password_hash


class ConfigurationError(RuntimeError):
    """Raised when Gateway configuration is incomplete or unsafe."""


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _secret(environ: Mapping[str, str], name: str) -> str:
    value = _required(environ, name)
    if len(value) < 32:
        raise ConfigurationError(f"{name} must be at least 32 characters")
    return value


def _url(environ: Mapping[str, str], name: str) -> str:
    value = _required(environ, name).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigurationError(f"{name} must be an HTTP(S) origin without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must not include a path, query, or fragment")
    return value


def _demo_user(environ: Mapping[str, str], slot: str) -> DemoUser:
    prefix = f"GATEWAY_DEMO_USER_{slot}_"
    try:
        user_id = uuid.UUID(_required(environ, prefix + "ID"))
    except ValueError as exc:
        raise ConfigurationError(f"{prefix}ID must be a UUID") from exc
    password_hash = _required(environ, prefix + "PASSWORD_HASH")
    try:
        validate_password_hash(password_hash)
    except ValueError as exc:
        raise ConfigurationError(f"{prefix}PASSWORD_HASH is invalid") from exc
    return DemoUser(
        user_id=user_id,
        username=_required(environ, prefix + "USERNAME"),
        password_hash=password_hash,
        display_name=_required(environ, prefix + "DISPLAY_NAME"),
    )


@dataclass(frozen=True)
class GatewaySettings:
    profile: str
    allowed_origin: str
    gateway_jwt_secret: str
    gateway_jwt_issuer: str
    gateway_jwt_audience: str
    service_jwt_secret: str
    service_jwt_issuer: str
    service_jwt_audience: str
    goal_intelligence_url: str
    goal_management_url: str
    demo_session_secret: str | None = None
    demo_session_ttl_seconds: int = 1800
    demo_users: tuple[DemoUser, ...] = ()
    request_body_limit_bytes: int = 65_536
    downstream_timeout_seconds: float = 5.0

    @property
    def demo_enabled(self) -> bool:
        return self.profile == "demo"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GatewaySettings":
        env = os.environ if environ is None else environ
        profile = _required(env, "GOALPULSE_PROFILE")
        if profile not in {"test", "dev", "demo"}:
            raise ConfigurationError("GOALPULSE_PROFILE must be test, dev, or demo")

        allowed_origin = _url(env, "GATEWAY_DEMO_ALLOWED_ORIGIN")
        common = dict(
            profile=profile,
            allowed_origin=allowed_origin,
            gateway_jwt_secret=_secret(env, "GATEWAY_DEV_JWT_SECRET"),
            gateway_jwt_issuer=_required(env, "GATEWAY_JWT_ISSUER"),
            gateway_jwt_audience=_required(env, "GATEWAY_JWT_AUDIENCE"),
            service_jwt_secret=_secret(env, "SERVICE_JWT_SECRET"),
            service_jwt_issuer=_required(env, "SERVICE_JWT_ISSUER"),
            service_jwt_audience=_required(env, "SERVICE_JWT_AUDIENCE"),
            goal_intelligence_url=_url(env, "GOAL_INTELLIGENCE_URL"),
            goal_management_url=_url(env, "GOAL_MANAGEMENT_URL"),
        )
        if profile != "demo":
            return cls(**common)

        try:
            ttl = int(_required(env, "GATEWAY_DEMO_SESSION_TTL_SECONDS"))
        except ValueError as exc:
            raise ConfigurationError("GATEWAY_DEMO_SESSION_TTL_SECONDS must be an integer") from exc
        if not 300 <= ttl <= 3600:
            raise ConfigurationError("GATEWAY_DEMO_SESSION_TTL_SECONDS must be between 300 and 3600")

        if env.get("NEXT_PUBLIC_API_MODE", "").strip() != "gateway":
            raise ConfigurationError("NEXT_PUBLIC_API_MODE must be gateway in demo")
        if env.get("GI_LLM_PROVIDER", "").strip() != "anthropic":
            raise ConfigurationError("GI_LLM_PROVIDER must be anthropic in demo")
        for name in ("GI_LLM_MODEL", "ANTHROPIC_API_KEY", "CONTEXT_DEV_API_KEY"):
            _required(env, name)
        if env.get("DE_EVIDENCE_PROVIDER", "").strip().lower() == "fixture":
            raise ConfigurationError("DE_EVIDENCE_PROVIDER cannot be fixture in demo")
        if env.get("DE_ALLOW_FIXTURE", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise ConfigurationError("DE_ALLOW_FIXTURE cannot be enabled in demo")

        users = (_demo_user(env, "A"), _demo_user(env, "B"))
        if users[0].user_id == users[1].user_id or users[0].username == users[1].username:
            raise ConfigurationError("demo users must have distinct IDs and usernames")
        return cls(
            **common,
            demo_session_secret=_secret(env, "GATEWAY_DEMO_SESSION_SECRET"),
            demo_session_ttl_seconds=ttl,
            demo_users=users,
        )
