"""Validated service-JWT authentication for Goal Intelligence.

The public Gateway authenticates the end user, then calls GI with its own
service JWT and the canonical user UUID. Goal Management uses a separate
service JWT for each accepted internal operation. Browser-provided identity
headers are never sufficient on their own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any


GATEWAY_SESSION_SCOPE = "gi.session.write"
GM_CANDIDATE_SCOPE = "gi.candidate.read"
GM_CONFIRM_SCOPE = "gi.confirmation.write"


class AuthError(Exception):
    status = 401


class Forbidden(AuthError):
    status = 403


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class Caller:
    subject: str
    user_id: str | None = None


def _decode_segment(value: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuthError("malformed bearer token") from exc
    if not isinstance(decoded, dict):
        raise AuthError("invalid bearer token JSON")
    return decoded


def _audience_matches(value: Any, expected: str) -> bool:
    return value == expected or (isinstance(value, list) and expected in value)


class ServiceJwtVerifier:
    """Verify the accepted local HS256 service-token profile from ADR-0002."""

    def __init__(self, *, secret: str, issuer: str, audience: str) -> None:
        if len(secret) < 32:
            raise ValueError("SERVICE_JWT_SECRET must be at least 32 characters")
        if not issuer or not audience:
            raise ValueError("SERVICE_JWT_ISSUER and SERVICE_JWT_AUDIENCE are required")
        self._secret = secret.encode("utf-8")
        self._issuer = issuer
        self._audience = audience

    def verify(self, authorization: str | None, *, now: int | None = None) -> Principal:
        if not authorization:
            raise AuthError("missing bearer token")
        scheme, separator, token = authorization.strip().partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise AuthError("missing bearer token")
        parts = token.strip().split(".")
        if len(parts) != 3:
            raise AuthError("malformed bearer token")
        header = _decode_segment(parts[0])
        claims = _decode_segment(parts[1])
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise AuthError("unsupported token algorithm")
        expected = hmac.new(self._secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        try:
            signature = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        except ValueError as exc:
            raise AuthError("malformed bearer signature") from exc
        if not hmac.compare_digest(expected, signature):
            raise AuthError("invalid bearer signature")

        clock = int(time.time()) if now is None else now
        if claims.get("iss") != self._issuer or not _audience_matches(claims.get("aud"), self._audience):
            raise AuthError("invalid token issuer or audience")
        if claims.get("token_kind") != "service" or not isinstance(claims.get("exp"), int):
            raise AuthError("invalid token claims")
        if claims["exp"] <= clock or (isinstance(claims.get("nbf"), int) and claims["nbf"] > clock):
            raise AuthError("expired or inactive token")
        try:
            subject = str(uuid.UUID(str(claims["sub"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise AuthError("token subject must be a UUID") from exc

        raw_scopes = claims.get("scope", [])
        if isinstance(raw_scopes, str):
            scopes = raw_scopes.split()
        elif isinstance(raw_scopes, list):
            scopes = raw_scopes
        else:
            raise AuthError("invalid token scopes")
        if not all(isinstance(scope, str) for scope in scopes):
            raise AuthError("invalid token scopes")
        return Principal(subject=subject, scopes=frozenset(scopes))


class Authenticator:
    def __init__(self, verifier: ServiceJwtVerifier):
        self._verifier = verifier

    def _with_scope(self, authorization: str | None, required_scope: str, *, internal: bool) -> Principal:
        try:
            principal = self._verifier.verify(authorization)
        except AuthError as exc:
            if internal:
                raise Forbidden("Goal Management service identity required") from exc
            raise
        if required_scope not in principal.scopes:
            raise Forbidden("service scope is not permitted")
        return principal

    def user_caller(self, authorization: str | None, user_header: str | None) -> Caller:
        principal = self._with_scope(authorization, GATEWAY_SESSION_SCOPE, internal=False)
        try:
            user_id = str(uuid.UUID(user_header or ""))
        except ValueError as exc:
            raise AuthError("missing authenticated user") from exc
        return Caller(subject=principal.subject, user_id=user_id)

    def internal_caller(self, authorization: str | None, required_scope: str) -> Caller:
        principal = self._with_scope(authorization, required_scope, internal=True)
        return Caller(subject=principal.subject)
