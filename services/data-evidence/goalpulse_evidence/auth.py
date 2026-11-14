"""Minimal ServiceBearer JWT verification aligned with accepted ICP-08."""

import base64
import hashlib
import hmac
import json
import os
import time
from uuid import UUID


class ServiceAuthError(ValueError):
    pass


def _segment(value: str) -> dict:
    decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    if not isinstance(decoded, dict):
        raise ServiceAuthError("JWT segment must be an object")
    return decoded


def require_service_scope(value: str | None, required_scope: str) -> UUID:
    try:
        if not value or not value.startswith("Bearer "):
            raise ServiceAuthError("missing bearer token")
        header_part, payload_part, signature_part = value[7:].split(".")
        header, claims = _segment(header_part), _segment(payload_part)
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        expected = hmac.new(os.environ["SERVICE_JWT_SECRET"].encode(),
                            f"{header_part}.{payload_part}".encode(), hashlib.sha256).digest()
        audience = claims.get("aud")
        required_audience = os.environ["SERVICE_JWT_AUDIENCE"]
        audience_matches = audience == required_audience or (
            isinstance(audience, list) and required_audience in audience
        )
        scopes = claims.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else scopes
        now = int(time.time())
        if (
            header.get("alg") != "HS256"
            or header.get("typ") != "JWT"
            or not hmac.compare_digest(signature, expected)
            or claims.get("iss") != os.environ["SERVICE_JWT_ISSUER"]
            or not audience_matches
            or claims.get("token_kind") != "service"
            or not isinstance(claims.get("exp"), int)
            or claims["exp"] <= now
            or (isinstance(claims.get("nbf"), int) and claims["nbf"] > now)
            or not isinstance(scopes, list)
            or not all(isinstance(scope, str) for scope in scopes)
            or required_scope not in scopes
        ):
            raise ServiceAuthError("invalid service token")
        return UUID(str(claims["sub"]))
    except Exception as exc:
        if isinstance(exc, ServiceAuthError):
            raise
        raise ServiceAuthError("invalid service token") from exc
