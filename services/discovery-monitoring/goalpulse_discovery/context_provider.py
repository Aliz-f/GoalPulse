"""D3-DM-01: injectable, bounded Context.dev REST adapter.

Only this service handles the API key. Provider output is untrusted data and
never becomes an Observation here. Transport and clock/sleep/DNS are injectable
so unit tests never need the provider or a network connection.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import http.client
import ipaddress
import json
import math
import re
import socket
import time
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


class ProviderError(Exception):
    """Sanitized error: never contains request bodies, keys or provider messages."""

    def __init__(self, code: str, *, status: int | None = None,
                 retry_after_seconds: float | None = None) -> None:
        if retry_after_seconds is not None:
            if not isinstance(retry_after_seconds, (int, float)) or isinstance(retry_after_seconds, bool):
                retry_after_seconds = None
            elif not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
                retry_after_seconds = None
            else:
                # Provider headers are untrusted. Preserve long backoff intent while
                # keeping datetime arithmetic bounded for durable schedulers.
                retry_after_seconds = min(float(retry_after_seconds), 86_400)
        self.code = code
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


def public_addresses(hostname: str) -> list[str]:
    return list({entry[4][0] for entry in socket.getaddrinfo(
        hostname, 443, type=socket.SOCK_STREAM
    )})


def public_https_url(url: str, resolver: Callable[[str], list[str]] = public_addresses) -> str:
    """Validate initial target DNS; provider-side redirect/egress policy is also needed.

    The remote provider resolves targets independently. This local preflight cannot
    pin its DNS or enforce its redirect policy; it is not a complete SSRF boundary.
    """
    try:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or "\\" in url):
            raise ValueError
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        addresses = resolver(host)
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError, OSError):
        raise ProviderError("UNSAFE_TARGET") from None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes = field(default=b"", repr=False)


class Transport(Protocol):
    def request(self, method: str, path: str, *, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> HttpResponse: ...


class ProviderPayload(dict):
    """Local transport instrumentation stored as an attribute, never a wire field."""

    def __init__(self, value: dict, request_attempts: int) -> None:
        super().__init__(value)
        self.request_attempts = request_attempts


class ContextHttpsTransport:
    """TLS to a fixed provider host. No proxy, redirect, or arbitrary-host support."""

    def __init__(self, max_response_bytes: int = 2_000_000) -> None:
        if not 1 <= max_response_bytes <= 20_000_000:
            raise ValueError("Invalid response limit")
        self.max_response_bytes = max_response_bytes

    def request(self, method: str, path: str, *, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> HttpResponse:
        if not path.startswith("/v1/") or "\r" in path or "\n" in path:
            raise ProviderError("INVALID_PROVIDER_PATH")
        connection = http.client.HTTPSConnection("api.context.dev", timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            content = response.read(self.max_response_bytes + 1)
            if len(content) > self.max_response_bytes:
                raise ProviderError("RESPONSE_TOO_LARGE")
            return HttpResponse(response.status, dict(response.getheaders()), content)
        except (OSError, http.client.HTTPException):
            raise ProviderError("NETWORK_ERROR") from None
        finally:
            connection.close()


@dataclass(frozen=True)
class Output:
    success: bool
    data: Any = field(repr=False)


@dataclass(frozen=True)
class ScrapeResult:
    source_url: str
    request_id: str | None
    fetched_at: datetime
    cache_age_ms: int | None
    observed_at: datetime | None
    product: Output
    markdown: Output
    credits_consumed: int | None = None
    request_attempts: int = 1
    requested_at: datetime | None = None
    provider_observed_at: datetime | None = None


@dataclass(frozen=True)
class MonitorCreated:
    monitor_id: str
    initial_run_id: str | None
    webhook_secret: str | None = field(default=None, repr=False)


class Provider(Protocol):
    """Private adapter port, not a proposed inter-service HTTP contract."""

    def search(self, query: str, *, limit: int = 5) -> Mapping[str, Any]: ...
    def scrape(self, url: str) -> ScrapeResult: ...
    def create_monitor(self, *, name: str, url: str, instructions: str | None = None,
                       webhook_url: str | None = None, frequency_minutes: int = 1440) -> MonitorCreated: ...
    def fetch_run(self, monitor_id: str, run_id: str) -> Mapping[str, Any]: ...


class ContextProvider:
    def __init__(self, api_key: str, *, transport: Transport | None = None,
                 resolver: Callable[[str], list[str]] = public_addresses,
                 clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout_seconds: float = 30, max_attempts: int = 3,
                 max_retry_delay_seconds: float = 30) -> None:
        if not api_key or any(c.isspace() or ord(c) < 32 for c in api_key):
            raise ValueError("A server-side Context.dev API key is required")
        if (not 1 <= max_attempts <= 5 or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 60
                or not math.isfinite(max_retry_delay_seconds)
                or not 0 < max_retry_delay_seconds <= 60):
            raise ValueError("Invalid request/retry bounds")
        self._api_key = api_key
        self._transport = transport or ContextHttpsTransport()
        self._resolver, self._clock, self._sleep = resolver, clock, sleep
        self._timeout, self._attempts = timeout_seconds, max_attempts
        self._max_delay = max_retry_delay_seconds

    def __repr__(self) -> str:
        return "ContextProvider(api_key=<redacted>)"

    def _request(self, method: str, path: str, payload: dict | None = None,
                 *, retry_safe: bool = True) -> Mapping[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        attempts = self._attempts if retry_safe else 1
        for attempt in range(attempts):
            try:
                response = self._transport.request(method, path, headers=headers, body=body,
                                                   timeout=self._timeout)
            except (OSError, TimeoutError, ProviderError) as exc:
                if isinstance(exc, ProviderError) and exc.code != "NETWORK_ERROR":
                    raise
                if attempt + 1 == attempts:
                    raise ProviderError("NETWORK_ERROR") from None
                self._sleep(min(2 ** attempt, self._max_delay))
                continue
            if 200 <= response.status < 300:
                try:
                    result = json.loads(response.body, parse_float=Decimal,
                                        parse_constant=reject_json_constant)
                    if not isinstance(result, dict):
                        raise ValueError
                    return ProviderPayload(result, attempt + 1)
                except (ValueError, UnicodeError):
                    raise ProviderError("INVALID_RESPONSE") from None
            transient = response.status in (408, 429, 500, 502, 503, 504)
            code = "RATE_LIMITED" if response.status == 429 else "HTTP_ERROR"
            delay = min(2 ** attempt, self._max_delay)
            retry_after = next((v for k, v in response.headers.items()
                                if k.lower() == "retry-after"), None)
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                    if not math.isfinite(delay) or delay < 0:
                        raise ValueError
                except ValueError:
                    try:
                        until = parsedate_to_datetime(retry_after)
                        if until.tzinfo is None:
                            raise ValueError
                        delay = max(0, (until - self._clock()).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        delay = min(2 ** attempt, self._max_delay)
            if not transient or attempt + 1 == attempts or delay > self._max_delay:
                raise ProviderError(code, status=response.status,
                                    retry_after_seconds=delay if transient else None)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def search(self, query: str, *, limit: int = 5) -> Mapping[str, Any]:
        if not query.strip() or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("Search requires a query and a limit between 1 and 5")
        result = self._request("POST", "/v1/web/search", {"query": query, "numResults": limit})
        if not isinstance(result.get("results"), list):
            raise ProviderError("INVALID_RESPONSE")
        # URLs are evidence candidates. The caller must validate each before scraping.
        return result

    def scrape(self, url: str) -> ScrapeResult:
        target = public_https_url(url, self._resolver)
        requested_at = self._clock()
        if requested_at.utcoffset() is None:
            raise ValueError("Clock must be timezone aware")
        requested_at = requested_at.astimezone(timezone.utc)
        result = self._request("POST", "/v1/web/scrape", {
            "url": target, "formats": {"product": True, "markdown": True},
            "sharedParams": {"mainContentOnly": True}, "maxAgeMs": 0,
        })
        fetched_at = self._clock()
        if fetched_at.utcoffset() is None:
            raise ValueError("Clock must be timezone aware")
        fetched_at = fetched_at.astimezone(timezone.utc)
        if fetched_at < requested_at:
            raise ProviderError("INVALID_CAPTURE_METADATA")
        final_url = public_https_url(result.get("url", target), self._resolver)
        cache = result.get("cache_metadata")
        age = cache.get("age_ms") if isinstance(cache, dict) else None
        if type(age) is not int or age < 0:
            age = None
        try:
            observed_at = None if age is None else fetched_at - timedelta(milliseconds=age)
        except OverflowError:
            raise ProviderError("INVALID_RESPONSE") from None

        def output(key: str, expected_type: type) -> Output:
            item = result.get(key)
            success = (isinstance(item, dict) and item.get("requested") is True
                       and item.get("success") is True and isinstance(item.get("data"), expected_type))
            return Output(success, item["data"] if success else None)

        request_id = result.get("request_id")
        if request_id is not None and not isinstance(request_id, str):
            raise ProviderError("INVALID_RESPONSE")
        usage = result.get("key_metadata")
        credits = usage.get("credits_consumed") if isinstance(usage, dict) else None
        if type(credits) is not int or credits < 0:
            credits = None
        return ScrapeResult(final_url, request_id, fetched_at, age, observed_at,
                            output("product", dict), output("markdown", str), credits,
                            getattr(result, "request_attempts", 1), requested_at=requested_at)

    def create_monitor(self, *, name: str, url: str, instructions: str | None = None,
                       webhook_url: str | None = None, frequency_minutes: int = 1440) -> MonitorCreated:
        if not 1 <= len(name.strip()) <= 200:
            raise ValueError("Monitor name must contain 1 to 200 characters")
        target = {"type": "page", "url": public_https_url(url, self._resolver)}
        if instructions is not None:
            if not instructions.strip():
                raise ValueError("Instructions must not be blank")
            target["instructions"] = instructions
        if type(frequency_minutes) is not int or not 10 <= frequency_minutes <= 10080:
            raise ValueError("Invalid monitor interval")
        payload: dict[str, Any] = {"name": name, "target": target,
                                  "schedule": {"type": "interval", "frequency": frequency_minutes, "unit": "minutes"}}
        if webhook_url is not None:
            # Only server configuration may supply this, never a browser request.
            payload["webhook"] = {"url": public_https_url(webhook_url, self._resolver), "retry": {}}
        # No documented idempotency key: ambiguous creation failures require reconciliation.
        result = self._request("POST", "/v1/monitors", payload, retry_safe=False)
        monitor_id, run_id = result.get("id"), result.get("initial_run_id")
        if not isinstance(monitor_id, str) or not monitor_id or (run_id is not None and not isinstance(run_id, str)):
            raise ProviderError("INVALID_RESPONSE")
        webhook = result.get("webhook")
        secret = webhook.get("secret") if isinstance(webhook, dict) else None
        if secret is not None and not isinstance(secret, str):
            raise ProviderError("INVALID_RESPONSE")
        return MonitorCreated(monitor_id, run_id, secret)

    def set_monitor_status(self, monitor_id: str, status: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", monitor_id) or status not in ("active", "paused"):
            raise ValueError("Invalid monitor status request")
        self._request("PATCH", f"/v1/monitors/{quote(monitor_id)}", {"status": status})

    def fetch_run(self, monitor_id: str, run_id: str) -> Mapping[str, Any]:
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) for value in (monitor_id, run_id)):
            raise ValueError("Invalid provider monitor/run ID")
        return self._request("GET", f"/v1/monitors/{quote(monitor_id)}/runs/{quote(run_id)}")
