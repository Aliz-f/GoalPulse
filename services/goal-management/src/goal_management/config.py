"""Fail-closed Goal Management runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    pass


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


def _http_origin(environ: Mapping[str, str], name: str) -> str:
    value = _required(environ, name).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigurationError(f"{name} must be an HTTP(S) origin without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must not include a path, query, or fragment")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    goal_intelligence_url: str
    gateway_jwt_secret: str
    gateway_jwt_issuer: str
    gateway_jwt_audience: str
    service_jwt_secret: str
    service_jwt_issuer: str
    service_jwt_audience: str
    internal_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        database_url = _required(env, "GOAL_MANAGEMENT_DATABASE_URL")
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ConfigurationError("GOAL_MANAGEMENT_DATABASE_URL must be a PostgreSQL URL")
        try:
            timeout = float(env.get("GOAL_MANAGEMENT_INTERNAL_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise ConfigurationError("GOAL_MANAGEMENT_INTERNAL_TIMEOUT_SECONDS must be numeric") from exc
        if not 0.1 <= timeout <= 30:
            raise ConfigurationError("GOAL_MANAGEMENT_INTERNAL_TIMEOUT_SECONDS must be between 0.1 and 30")
        return cls(
            database_url=database_url,
            goal_intelligence_url=_http_origin(env, "GOAL_INTELLIGENCE_URL"),
            gateway_jwt_secret=_secret(env, "GATEWAY_DEV_JWT_SECRET"),
            gateway_jwt_issuer=_required(env, "GATEWAY_JWT_ISSUER"),
            gateway_jwt_audience=_required(env, "GATEWAY_JWT_AUDIENCE"),
            service_jwt_secret=_secret(env, "SERVICE_JWT_SECRET"),
            service_jwt_issuer=_required(env, "SERVICE_JWT_ISSUER"),
            service_jwt_audience=_required(env, "SERVICE_JWT_AUDIENCE"),
            internal_timeout_seconds=timeout,
        )
