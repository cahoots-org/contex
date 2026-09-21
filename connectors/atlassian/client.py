"""Minimal Atlassian Cloud REST client (Jira + Confluence) with backoff.

Basic auth with an account email + API token against one site base URL. Kept to
raw ``httpx`` (no Atlassian SDK) to stay light and expose response headers for
429 backoff, mirroring the GitHub connector's client.

Two pagination styles live here because the two products differ:

* **Jira** enhanced search (``POST /rest/api/3/search/jql``) uses opaque
  ``nextPageToken`` cursors and an ``isLast`` flag.
* **Confluence v2** uses ``_links.next`` relative-URL cursors.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

_JIRA_SEARCH = "/rest/api/3/search/jql"
_MAX_RETRIES = 4
_DEFAULT_RETRY_WAIT = 60.0


class RateLimitError(Exception):
    """Raised when the Atlassian API keeps returning 429 after retries."""


class AtlassianClient:
    """Async Atlassian Cloud REST client for one site.

    Retries on ``429`` using the ``Retry-After`` header, then raises
    :class:`RateLimitError` if still limited.
    """

    def __init__(
        self,
        site_url: str,
        email: str,
        token: str,
        *,
        timeout: float = 30.0,
    ):
        base = (site_url or "").rstrip("/")
        headers = {"Accept": "application/json"}
        if email and token:
            credential = base64.b64encode(f"{email}:{token}".encode()).decode()
            headers["Authorization"] = f"Basic {credential}"
        else:
            log.warning(
                "No Atlassian credentials (source.email / source.token) — "
                "requests will be unauthenticated and most will fail."
            )
        self._http = httpx.AsyncClient(
            base_url=base,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "AtlassianClient":
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._http.__aexit__(*exc_info)

    # -- single requests ---------------------------------------------------

    async def get(self, path: str, **params: Any) -> Any:
        """GET one resource, backing off on rate limit."""
        return await self._request("GET", path, params=params or None)

    async def post(self, path: str, json_body: dict) -> Any:
        """POST a JSON body and return the parsed response."""
        return await self._request("POST", path, json=json_body)

    # -- pagination --------------------------------------------------------

    async def paginate_jira(
        self,
        jql: str,
        *,
        fields: list[str],
        page_size: int = 100,
    ) -> AsyncIterator[dict]:
        """Yield every issue matching ``jql`` across all search pages."""
        next_token: str | None = None
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": fields,
            }
            if next_token:
                body["nextPageToken"] = next_token
            data = await self._request("POST", _JIRA_SEARCH, json=body)
            for issue in data.get("issues", []):
                yield issue
            if data.get("isLast", True):
                return
            next_token = data.get("nextPageToken")
            if not next_token:
                return

    async def paginate_confluence(
        self, path: str, **params: Any
    ) -> AsyncIterator[dict]:
        """Yield every item across a Confluence v2 list endpoint's cursor pages."""
        url: str | None = path
        query: dict[str, Any] | None = {k: v for k, v in params.items() if v is not None}
        while url:
            data = await self._request("GET", url, params=query or None)
            for item in data.get("results", []):
                yield item
            next_link = (data.get("_links") or {}).get("next")
            url = next_link or None
            query = None  # the next link already carries the query string

    # -- transport ---------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(_MAX_RETRIES):
            response = await self._http.request(method, url, **kwargs)
            if response.status_code == 429:
                wait = _retry_after(response)
                if attempt < _MAX_RETRIES - 1:
                    log.warning("429 from %s — sleeping %.1fs", url, wait)
                    await asyncio.sleep(wait)
                    continue
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        raise RateLimitError(f"Rate limited after {_MAX_RETRIES} attempts on {url}")


def _retry_after(response: httpx.Response) -> float:
    """Seconds to sleep from a ``Retry-After`` header (numeric seconds only)."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return _DEFAULT_RETRY_WAIT
    try:
        return max(0.0, float(raw))
    except ValueError:
        # An HTTP-date Retry-After — fall back to a fixed wait.
        return _DEFAULT_RETRY_WAIT
