"""Minimal GitHub REST client with rate-limit backoff.

Uses raw httpx rather than a higher-level SDK to keep the dependency light and
give direct access to the rate-limit response headers needed for backoff.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

_BASE = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
# GitHub's current published REST API version (dates advance as GitHub ships
# new versions): https://docs.github.com/en/rest/about-the-rest-api/api-versions
_API_VERSION = "2026-03-10"


class RateLimitError(Exception):
    """Raised when the GitHub API signals rate exhaustion."""


class GitHubClient:
    """Async GitHub REST client.

    Reads ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset`` on every response
    and sleeps until the reset epoch when remaining hits zero, then retries.
    """

    def __init__(self, token: str, *, timeout: float = 30.0):
        headers = {
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning(
                "No GitHub token set (source.token) — running unauthenticated: "
                "60 requests/hour and no access to private repos."
            )
        self._http = httpx.AsyncClient(
            base_url=_BASE,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "GitHubClient":
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._http.__aexit__(*exc_info)

    async def get(self, path: str, **params: Any) -> Any:
        """GET a single resource; back off on rate limit."""
        return await self._request("GET", path, params=params or None)

    async def paginate(
        self, path: str, *, per_page: int = 100, **params: Any
    ) -> AsyncIterator[dict]:
        """Yield every item across all pages for a list endpoint."""
        url: str | None = path
        query = {"per_page": per_page, **params}
        while url:
            data, headers = await self._request_with_headers("GET", url, params=query)
            if not isinstance(data, list):
                break
            for item in data:
                yield item
            url = _next_link(headers.get("link", ""))
            query = {}  # link header carries full query string

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        data, _ = await self._request_with_headers(method, url, **kwargs)
        return data

    async def _request_with_headers(
        self, method: str, url: str, **kwargs
    ) -> tuple[Any, httpx.Headers]:
        for attempt in range(3):
            response = await self._http.request(method, url, **kwargs)
            if response.status_code == 403 or response.status_code == 429:
                wait = _rate_limit_wait(response)
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue
            response.raise_for_status()
            return response.json(), response.headers
        raise RateLimitError(f"Rate limited after retries on {url}")


def _next_link(link_header: str) -> str | None:
    """Parse the ``rel="next"`` URL from a GitHub Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) == 2 and segments[1] == 'rel="next"':
            return segments[0].strip("<>")
    return None


def _rate_limit_wait(response: httpx.Response) -> float:
    """Seconds to sleep based on rate-limit response headers.

    Secondary (abuse) limits send ``Retry-After``; primary limits send
    ``X-RateLimit-Remaining: 0`` with an ``X-RateLimit-Reset`` epoch. A 403/429
    with neither is a real error (bad token, no access) — wait 0 so the caller
    raises.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return 60.0
    if response.headers.get("X-RateLimit-Remaining", "1") != "0":
        return 0.0
    reset = response.headers.get("X-RateLimit-Reset")
    if not reset:
        return 60.0
    return max(0.0, float(reset) - time.time()) + 1.0
