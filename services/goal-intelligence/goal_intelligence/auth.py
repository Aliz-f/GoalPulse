"""Caller identification for Goal Intelligence.

Follows the ICP-01 proposal (contracts/openapi/internal.proposed.v1.yaml): every
call carries a machine-to-machine credential as ``Authorization: Bearer <token>``
(security scheme ``ServiceBearer``).

INTERIM until ICP-08 fixes the identity design (Developer 1):
- Service tokens are opaque per-service secrets from ``GI_SERVICE_TOKENS``
  ("gateway=<secret>,goal-management=<secret>"), compared in constant time.
  ICP-01 says ``bearerFormat: JWT``; swap ``_service_for`` for JWT verification
  (issuer/audience/expiry) once ICP-08 picks the issuer. Handlers only see ``Caller``.
- Public ``/v1`` routes accept only the ``gateway`` service. The gateway has already
  validated the end-user JWT and passes the canonical user UUID in
  ``X-Authenticated-User-Id``; that header is trusted *only* together with a valid
  gateway credential, and the gateway must strip any client-supplied copy.
- Internal ``/internal/v1`` routes accept only ``goal-management``. The contract
  lists 403 for every identity failure there, so missing/invalid credentials are 403.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass


class AuthError(Exception):
    status = 401


class Forbidden(AuthError):
    status = 403


@dataclass(frozen=True)
class Caller:
    service: str
    user_id: str | None = None


def parse_service_tokens(raw: str | None) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for part in (raw or "").split(","):
        name, sep, secret = part.strip().partition("=")
        if sep and name.strip() and len(secret.strip()) >= 16:
            tokens[name.strip()] = secret.strip()
    return tokens


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


class Authenticator:
    def __init__(self, tokens: dict[str, str]):
        self._tokens = dict(tokens)

    def _service_for(self, token: str | None) -> str | None:
        if not token:
            return None
        match = None
        for name, secret in self._tokens.items():
            if hmac.compare_digest(secret.encode(), token.encode()):
                match = name
        return match

    def user_caller(self, authorization: str | None, user_header: str | None) -> Caller:
        service = self._service_for(bearer_token(authorization))
        if service is None:
            raise AuthError("missing or invalid service credential")
        if service != "gateway":
            raise Forbidden("only the gateway may call public routes")
        try:
            user_id = str(uuid.UUID(user_header or ""))
        except ValueError as exc:
            raise AuthError("missing authenticated user") from exc
        return Caller(service=service, user_id=user_id)

    def internal_caller(self, authorization: str | None, allowed: tuple[str, ...] = ("goal-management",)) -> Caller:
        service = self._service_for(bearer_token(authorization))
        if service is None or service not in allowed:
            raise Forbidden("Goal Management service identity required")
        return Caller(service=service)
