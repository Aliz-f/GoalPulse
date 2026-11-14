"""Exact-route, bounded downstream forwarding."""
from __future__ import annotations

from typing import Protocol

import httpx


class ProxyTransport(Protocol):
    async def request(
        self, method: str, url: str, *, headers: dict[str, str], content: bytes, params: list[tuple[str, str]]
    ) -> httpx.Response: ...


class HttpxProxyTransport:
    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], content: bytes, params: list[tuple[str, str]]
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            return await client.request(method, url, headers=headers, content=content, params=params)
