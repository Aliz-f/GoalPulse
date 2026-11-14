"""Short-lived downstream JWT creation for the accepted identity profiles."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Callable


GATEWAY_SERVICE_SUBJECT = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _encode(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class DownstreamTokenIssuer:
    """Mint only the two server-to-server token shapes recorded in ADR-0003."""

    def __init__(
        self,
        *,
        gateway_secret: str,
        gateway_issuer: str,
        gateway_audience: str,
        service_secret: str,
        service_issuer: str,
        service_audience: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._gateway = (gateway_secret, gateway_issuer, gateway_audience)
        self._service = (service_secret, service_issuer, service_audience)
        self._clock = clock

    def _mint(self, profile: tuple[str, str, str], subject: uuid.UUID, kind: str, scope: str | None = None) -> str:
        secret, issuer, audience = profile
        now = int(self._clock())
        claims: dict[str, object] = {
            "iss": issuer,
            "aud": audience,
            "sub": str(subject),
            "token_kind": kind,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        }
        if scope is not None:
            claims["scope"] = scope
        header = _encode({"alg": "HS256", "typ": "JWT"})
        body = _encode(claims)
        signature = hmac.new(secret.encode("utf-8"), f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return f"Bearer {header}.{body}.{encoded_signature}"

    def for_goal_intelligence(self) -> str:
        return self._mint(self._service, GATEWAY_SERVICE_SUBJECT, "service", "gi.session.write")

    def for_goal_management(self, owner: uuid.UUID) -> str:
        return self._mint(self._gateway, owner, "user")

    def for_owner_service(self, owner: uuid.UUID, scope: str) -> str:
        """Bind a Gateway-derived owner to one least-privilege D4 operation."""
        if not scope or any(character.isspace() for character in scope):
            raise ValueError("an exact downstream operation scope is required")
        return self._mint(self._service, owner, "service", scope)
