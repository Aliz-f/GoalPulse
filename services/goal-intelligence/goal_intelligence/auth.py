"""Caller identification for Goal Intelligence.

INTERIM until ICP-08 is accepted (Developer 1 owns the final design):
- Every request must carry ``X-Service-Token``; tokens are configured per
  calling service in ``GI_SERVICE_TOKENS`` ("gateway=<secret>,goal-management=<secret>")
  and compared in constant time.
- Public ``/v1`` routes accept only the ``gateway`` service. The gateway has
  already validated the end-user JWT and passes the canonical user UUID in
  ``X-Authenticated-User-Id``. That header is trusted *only* together with a
  valid gateway token; the gateway must strip any client-supplied copy.
- Internal ``/internal/v1`` routes accept only ``goal-management``.
Swap this module for JWT/mTLS verification once ICP-08 lands; handlers depend
only on :class:`Caller`.
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


class Authenticator:
    def __init__(self, tokens: dict[str, str]):
        self._tokens = dict(tokens)

    def _service_for(self, token: str | None) -> str:
        if not token:
            raise AuthError("missing service credential")
        match = None
        for name, secret in self._tokens.items():
            if hmac.compare_digest(secret.encode(), token.encode()):
                match = name
        if match is None:
            raise AuthError("invalid service credential")
        return match

    def user_caller(self, service_token: str | None, user_header: str | None) -> Caller:
        service = self._service_for(service_token)
        if service != "gateway":
            raise Forbidden("only the gateway may call public routes")
        try:
            user_id = str(uuid.UUID(user_header or ""))
        except ValueError as exc:
            raise AuthError("missing authenticated user") from exc
        return Caller(service=service, user_id=user_id)

    def internal_caller(self, service_token: str | None, allowed: tuple[str, ...] = ("goal-management",)) -> Caller:
        service = self._service_for(service_token)
        if service not in allowed:
            raise Forbidden(f"{service} may not call this internal route")
        return Caller(service=service)
