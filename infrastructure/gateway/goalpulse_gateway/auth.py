"""Strict, dependency-free HS256 JWT verification for the local dev issuer."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any


class AuthenticationError(ValueError):
    """Raised when a bearer token cannot establish a trusted principal."""


class AuthorizationError(PermissionError):
    """Raised when a trusted service principal lacks a required scope."""


@dataclass(frozen=True)
class Principal:
    subject: uuid.UUID
    token_kind: str
    scopes: frozenset[str]


def _decode_segment(value: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuthenticationError("malformed bearer token") from exc
    if not isinstance(decoded, dict):
        raise AuthenticationError("invalid bearer token JSON")
    return decoded


def _audience_matches(value: Any, expected: str) -> bool:
    return value == expected or (isinstance(value, list) and expected in value)


class HS256TokenVerifier:
    """Validate only locally issued HS256 tokens; no unsigned/JWK fallback exists."""

    def __init__(self, *, secret: str, issuer: str, audience: str, token_kind: str) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret.encode("utf-8")
        self._issuer = issuer
        self._audience = audience
        self._token_kind = token_kind

    def verify(self, authorization: str | None, *, now: int | None = None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("missing bearer token")
        token = authorization[7:]
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("malformed bearer token")
        header = _decode_segment(parts[0])
        claims = _decode_segment(parts[1])
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise AuthenticationError("unsupported token algorithm")
        expected = hmac.new(self._secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        try:
            signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        except ValueError as exc:
            raise AuthenticationError("malformed bearer signature") from exc
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("invalid bearer signature")
        clock = int(time.time()) if now is None else now
        if claims.get("iss") != self._issuer or not _audience_matches(claims.get("aud"), self._audience):
            raise AuthenticationError("invalid token issuer or audience")
        if claims.get("token_kind") != self._token_kind or not isinstance(claims.get("exp"), int):
            raise AuthenticationError("invalid token claims")
        if claims["exp"] <= clock or (isinstance(claims.get("nbf"), int) and claims["nbf"] > clock):
            raise AuthenticationError("expired or inactive token")
        try:
            subject = uuid.UUID(str(claims["sub"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise AuthenticationError("token subject must be a UUID") from exc
        raw_scopes = claims.get("scope", [])
        scopes = frozenset(raw_scopes.split()) if isinstance(raw_scopes, str) else frozenset(raw_scopes)
        if not all(isinstance(scope, str) for scope in scopes):
            raise AuthenticationError("invalid token scopes")
        return Principal(subject=subject, token_kind=self._token_kind, scopes=scopes)


def require_service_scope(principal: Principal, scope: str) -> None:
    if principal.token_kind != "service" or scope not in principal.scopes:
        raise AuthorizationError("service scope is not permitted")


def correlation_id(value: str | None) -> uuid.UUID:
    """Preserve valid caller correlation IDs, otherwise create a trusted one."""
    if value is None:
        return uuid.uuid4()
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AuthenticationError("X-Correlation-ID must be a UUID") from exc
