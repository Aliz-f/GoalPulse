"""Password hashing and signed, cookie-only demo sessions."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


_PASSWORD_SCHEME = "pbkdf2_sha256"
_MIN_PASSWORD_ITERATIONS = 210_000


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_demo_password(password: str, *, iterations: int = 310_000, salt: bytes | None = None) -> str:
    """Create the documented PBKDF2 hash format; plaintext is never persisted."""
    if not password or iterations < _MIN_PASSWORD_ITERATIONS:
        raise ValueError("password and strong iteration count are required")
    selected_salt = secrets.token_bytes(16) if salt is None else salt
    if len(selected_salt) < 16:
        raise ValueError("password salt must be at least 16 bytes")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), selected_salt, iterations)
    return f"{_PASSWORD_SCHEME}${iterations}${_b64encode(selected_salt)}${_b64encode(digest)}"


def validate_password_hash(encoded: str) -> None:
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$")
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        digest = _b64decode(raw_digest)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("invalid password hash") from exc
    if scheme != _PASSWORD_SCHEME or iterations < _MIN_PASSWORD_ITERATIONS:
        raise ValueError("invalid password hash")
    if len(salt) < 16 or len(digest) != hashlib.sha256().digest_size:
        raise ValueError("invalid password hash")


def verify_demo_password(password: str, encoded: str) -> bool:
    try:
        validate_password_hash(encoded)
        _scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$")
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64decode(raw_salt), int(raw_iterations)
        )
        return hmac.compare_digest(actual, _b64decode(raw_digest))
    except (ValueError, UnicodeError):
        return False


@dataclass(frozen=True)
class DemoUser:
    user_id: uuid.UUID
    username: str
    password_hash: str
    display_name: str


@dataclass(frozen=True)
class DemoSession:
    user: DemoUser
    expires_at: int
    csrf_token: str

    def public_json(self) -> dict[str, str]:
        timestamp = datetime.fromtimestamp(self.expires_at, tz=timezone.utc)
        return {
            "user_id": str(self.user.user_id),
            "display_name": self.user.display_name,
            "expires_at": timestamp.isoformat().replace("+00:00", "Z"),
        }


class SessionError(ValueError):
    """A cookie did not establish a valid current demo session."""


class DemoSessionManager:
    def __init__(
        self,
        *,
        secret: str,
        users: tuple[DemoUser, ...],
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("session secret must be at least 32 characters")
        if len(users) != 2:
            raise ValueError("exactly two demo users are required")
        self._secret = secret.encode("utf-8")
        self._by_username = {user.username: user for user in users}
        self._by_id = {str(user.user_id): user for user in users}
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # Keep unknown-user password checks on the same expensive code path.
        sample_iterations = int(users[0].password_hash.split("$")[1])
        self._dummy_password_hash = hash_demo_password(
            "not-a-real-password", iterations=sample_iterations, salt=b"\0" * 16
        )

    def authenticate(self, username: str, password: str) -> DemoUser | None:
        user = self._by_username.get(username)
        encoded = self._dummy_password_hash if user is None else user.password_hash
        valid = verify_demo_password(password, encoded)
        return user if user is not None and valid else None

    def create(self, user: DemoUser) -> tuple[str, DemoSession]:
        now = int(self._clock())
        jti = secrets.token_urlsafe(24)
        payload = {"sub": str(user.user_id), "iat": now, "exp": now + self._ttl_seconds, "jti": jti}
        encoded = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        csrf = _b64encode(hmac.new(self._secret, f"csrf:{jti}".encode("utf-8"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", DemoSession(user, payload["exp"], csrf)

    def verify(self, token: str | None) -> DemoSession:
        try:
            if not token:
                raise ValueError
            encoded, signature = token.split(".")
            expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                raise ValueError
            payload = json.loads(_b64decode(encoded))
            if not isinstance(payload, dict) or set(payload) != {"sub", "iat", "exp", "jti"}:
                raise ValueError
            now = int(self._clock())
            if not isinstance(payload["iat"], int) or not isinstance(payload["exp"], int):
                raise ValueError
            if payload["iat"] > now or payload["exp"] <= now or payload["exp"] - payload["iat"] != self._ttl_seconds:
                raise ValueError
            user = self._by_id[str(uuid.UUID(payload["sub"]))]
            csrf = _b64encode(
                hmac.new(self._secret, f"csrf:{payload['jti']}".encode("utf-8"), hashlib.sha256).digest()
            )
            return DemoSession(user, payload["exp"], csrf)
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
            raise SessionError("invalid demo session") from exc
