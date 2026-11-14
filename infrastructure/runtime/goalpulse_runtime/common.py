"""Shared fail-closed deployment adapters for private GoalPulse runtimes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

import pika
import psycopg
from psycopg.conninfo import conninfo_to_dict


class ConfigurationError(RuntimeError):
    """A fixed, non-secret deployment configuration error."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def owned_database_url(name: str, expected_database: str) -> str:
    value = required(name)
    try:
        if conninfo_to_dict(value).get("dbname") != expected_database:
            raise ValueError
    except Exception:
        raise ConfigurationError(f"{name} must select the owned database") from None
    return value


def connection_factory(database_url: str) -> Callable[[], Any]:
    def connect():
        return psycopg.connect(
            database_url,
            autocommit=False,
            connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )

    return connect


def apply_sql_migrations(database_url: str, directory: Path, ledger: str) -> None:
    """Apply ordered owner SQL once with checksums and one DB transaction."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", ledger):
        raise ConfigurationError("invalid migration ledger name")
    migrations = sorted(directory.glob("[0-9]*.sql"))
    if not migrations:
        raise ConfigurationError("owned migration directory is empty")
    with psycopg.connect(database_url, autocommit=False, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {ledger} ("
                "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor.execute(f"SELECT version,checksum FROM {ledger}")
            applied = dict(cursor.fetchall())
            for path in migrations:
                raw = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if path.name in applied:
                    if applied[path.name] != checksum:
                        raise ConfigurationError("applied migration checksum changed")
                    continue
                # D4's reviewed scripts wrap themselves in BEGIN/COMMIT. The
                # deployment runner owns one atomic transaction with its ledger.
                sql = re.sub(r"\A\s*BEGIN\s*;", "", raw, count=1, flags=re.IGNORECASE)
                sql = re.sub(r"COMMIT\s*;\s*\Z", "", sql, count=1, flags=re.IGNORECASE)
                cursor.execute(sql)
                cursor.execute(
                    f"INSERT INTO {ledger}(version,checksum) VALUES(%s,%s)",
                    (path.name, checksum),
                )
        connection.commit()


def _encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class ServiceTokenIssuer:
    def __init__(self) -> None:
        self.secret = required("SERVICE_JWT_SECRET")
        if len(self.secret.encode()) < 32 or self.secret.startswith("change_this"):
            raise ConfigurationError("SERVICE_JWT_SECRET must be a non-placeholder 32-byte secret")
        self.issuer = required("SERVICE_JWT_ISSUER")
        self.audience = required("SERVICE_JWT_AUDIENCE")
        self.subject = UUID(required("PLATFORM_SERVICE_ID"))

    def mint(self, scope: str) -> str:
        if not scope or any(character.isspace() for character in scope):
            raise ValueError("invalid operation scope")
        now = int(time.time())
        header = _encode({"alg": "HS256", "typ": "JWT"})
        claims = _encode({
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(self.subject),
            "token_kind": "service",
            "scope": scope,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        })
        signature = hmac.new(
            self.secret.encode(),
            f"{header}.{claims}".encode(),
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{header}.{claims}.{encoded}"


def service_owner_subject(authorization: str | None, required_scope: str) -> str:
    """Validate an owner-bound service JWT minted only by Gateway."""
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError
        head, body, signature = authorization[7:].split(".")
        header = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        expected = hmac.new(
            required("SERVICE_JWT_SECRET").encode(),
            f"{head}.{body}".encode(),
            hashlib.sha256,
        ).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        scopes = claims.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else scopes
        now = int(time.time())
        audience = claims.get("aud")
        if (
            header != {"alg": "HS256", "typ": "JWT"}
            or not hmac.compare_digest(expected, actual)
            or claims.get("iss") != required("SERVICE_JWT_ISSUER")
            or not (audience == required("SERVICE_JWT_AUDIENCE") or (
                isinstance(audience, list) and required("SERVICE_JWT_AUDIENCE") in audience
            ))
            or claims.get("token_kind") != "service"
            or type(claims.get("exp")) is not int
            or claims["exp"] <= now
            or type(claims.get("nbf", 0)) is not int
            or claims.get("nbf", 0) > now
            or not isinstance(scopes, list)
            or required_scope not in scopes
        ):
            raise ValueError
        return str(UUID(claims["sub"]))
    except Exception:
        raise PermissionError("authorized owner service identity required") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def json_get(
    url: str,
    token: str | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    try:
        response = _OPENER.open(Request(url, headers=request_headers), timeout=5)
    except HTTPError as error:
        status = error.code
        error.close()
        return status, None
    except (URLError, TimeoutError, OSError):
        return 503, None
    with response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            return 503, None
        try:
            return response.status, json.loads(body)
        except (ValueError, UnicodeError):
            return 503, None


def dependencies_ready(origins: Iterable[str]) -> bool:
    return all(json_get(origin.rstrip("/") + "/health/ready")[0] == 200 for origin in origins)


def open_broker():
    parameters = pika.URLParameters(required("RABBITMQ_URL"))
    parameters.connection_attempts = 2
    parameters.retry_delay = 1
    parameters.socket_timeout = 5
    parameters.stack_timeout = 10
    parameters.blocked_connection_timeout = 5
    parameters.heartbeat = 30
    return pika.BlockingConnection(parameters)


def run_supervised(factory: Callable[[], Any]) -> None:
    delay = 1
    while True:
        try:
            factory().run(lambda: False)
            delay = 1
        except KeyboardInterrupt:
            return
        except Exception:
            # Fixed output only: driver/provider exceptions can contain secrets.
            print("private_worker=unavailable", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
