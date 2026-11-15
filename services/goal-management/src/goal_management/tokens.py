"""Short-lived ICP-08 service tokens minted by Goal Management."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable


GOAL_MANAGEMENT_SERVICE_SUBJECT = uuid.UUID("00000000-0000-4000-8000-000000000002")


def _encode(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ServiceTokenIssuer:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < 32 or not issuer or not audience:
            raise ValueError("complete service JWT configuration is required")
        self._secret = secret.encode("utf-8")
        self._issuer = issuer
        self._audience = audience
        self._clock = clock

    def mint(self, scope: str) -> str:
        now = int(self._clock())
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(GOAL_MANAGEMENT_SERVICE_SUBJECT),
            "token_kind": "service",
            "scope": scope,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        }
        header = _encode({"alg": "HS256", "typ": "JWT"})
        payload = _encode(claims)
        signature = hmac.new(
            self._secret,
            f"{header}.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{header}.{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"
