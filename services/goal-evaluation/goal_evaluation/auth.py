"""Local ICP-08 HS256 service verifier, aligned with D1's accepted profile.

No user token, unsigned owner header, remote JWK or default secret is accepted.
D1 supplies the signing configuration and exact operation scope at composition.
"""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class ServicePrincipal:
    subject: UUID
    scopes: frozenset[str]


def _decode(value: str) -> bytes:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid JWT segment')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JWT claim')
        result[key] = value
    return result


class ServiceAuthorizer:
    def __init__(self, *, secret: str, issuer: str, audience: str, required_scope: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if (not isinstance(secret, str) or len(secret) < 32 or not issuer or not audience
                or not required_scope or any(c.isspace() for c in required_scope)):
            raise ValueError('Explicit ICP-08 signing configuration and operation scope required')
        self._secret, self.issuer, self.audience = secret.encode(), issuer, audience
        self.required_scope, self.clock = required_scope, clock

    def __call__(self, authorization: str | None = Header(default=None)) -> ServicePrincipal:
        try:
            if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization) > 8192:
                raise ValueError('Invalid bearer')
            header_segment, claims_segment, signature_segment = authorization[7:].split('.')
            header = json.loads(_decode(header_segment), object_pairs_hook=_unique_object)
            claims = json.loads(_decode(claims_segment), object_pairs_hook=_unique_object)
            if not isinstance(header, dict) or not isinstance(claims, dict):
                raise ValueError('Invalid JWT object')
            expected = hmac.new(self._secret, f'{header_segment}.{claims_segment}'.encode(), hashlib.sha256).digest()
            if (header.get('alg') != 'HS256' or header.get('typ') != 'JWT' or 'crit' in header
                    or not hmac.compare_digest(expected, _decode(signature_segment))):
                raise ValueError('Invalid JWT signature or algorithm')
            now = self.clock()
            if now.utcoffset() is None:
                raise ValueError('Aware clock required')
            scopes = claims.get('scope', [])
            scopes = scopes.split() if isinstance(scopes, str) else scopes
            audience = claims.get('aud')
            audience_ok = audience == self.audience or (
                isinstance(audience, list) and all(isinstance(a, str) for a in audience)
                and self.audience in audience)
            if (claims.get('iss') != self.issuer or not audience_ok
                    or claims.get('token_kind') != 'service'
                    or type(claims.get('exp')) is not int or claims['exp'] <= now.timestamp()
                    or ('nbf' in claims and (type(claims['nbf']) is not int or claims['nbf'] > now.timestamp()))
                    or not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes)
                    or self.required_scope not in scopes):
                raise ValueError('Invalid service claims')
            return ServicePrincipal(UUID(claims['sub']), frozenset(scopes))
        except Exception:
            raise HTTPException(403, 'authorized service identity required') from None
