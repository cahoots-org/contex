"""The MCP transport: publish batches to Contex with an API key.

ContexPublisher pushes each batch through the ``contex_publish_batch`` or
``contex_delete`` tool, opening one MCP session per call attempt so a dropped
connection is simply retried. It is the only connector module that touches the
MCP SDK; the runner and readers stay transport-free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, METHOD_NOT_FOUND, CallToolResult

from .config import ContexConfig

# A continuous ingester will eventually trip the server's publish limiter; back
# off and retry instead of dying mid-run (mirrors the upstream-read clients).
_MAX_RETRIES = 5
_BACKOFF_CAP = 60.0  # seconds
# A publish that hasn't answered in this long is treated as a dropped connection.
_CALL_TIMEOUT = 300.0
# Protocol errors a retry can't fix; every other raised error is a transport failure.
_FATAL_CODES = (INVALID_PARAMS, METHOD_NOT_FOUND)
_AUTH_REJECTED = (401, 403)
_RETRY_AFTER = re.compile(r"retry_after=(\d+(?:\.\d+)?)")

log = logging.getLogger(__name__)


def _backoff(error_text: str, attempt: int) -> float:
    """Seconds to wait: the server's ``retry_after`` hint, else exponential."""
    hint = _RETRY_AFTER.search(error_text)
    wait = float(hint.group(1)) if hint else 2.0 ** min(attempt, 10)
    return min(wait, _BACKOFF_CAP)


def _single_error(exc: Exception) -> Exception:
    """The one error inside the transport's nested task-group ExceptionGroups."""
    while isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


class ContexPublisher:
    """An async context manager that publishes item batches over MCP."""

    def __init__(self, config: ContexConfig, origin: str | None = None, retry_forever: bool = False):
        self._config = config
        self._origin = origin
        self._retry_forever = retry_forever
        self._auth_rejected: int | None = None

    async def __aenter__(self) -> "ContexPublisher":
        return self

    async def __aexit__(self, *exc_info) -> None:
        pass

    async def publish_batch(self, items: list[dict]) -> int:
        """Publish one batch; return how many items the server accepted."""
        arguments = {"items": items}
        if self._origin is not None:
            arguments["origin"] = self._origin
        result = await self._call("contex_publish_batch", arguments)
        for failure in result.get("failed", []):
            log.warning("contex rejected %s: %s", failure["data_key"], failure["error"])
        return int(result.get("published", 0))

    async def list_keys(self) -> list[str]:
        """Every key this publisher's origin has in Contex."""
        keys: list[str] = []
        after = None
        while True:
            arguments = {"origin": self._origin}
            if after is not None:
                arguments["after"] = after
            page = await self._call("contex_list_keys", arguments)
            keys += page["keys"]
            after = page.get("next")
            if after is None:
                return keys

    async def delete_batch(self, data_keys: list[str]) -> int:
        """Delete one batch of keys; return how many existed."""
        result = await self._call("contex_delete", {"data_keys": data_keys})
        return len(result.get("deleted", []))

    async def _call_once(self, tool: str, arguments: dict) -> CallToolResult:
        """Open a session, make one tool call, and close the session."""
        self._auth_rejected = None
        headers = None
        if self._config.api_key:
            headers = {"Authorization": f"Bearer {self._config.api_key}"}
        async with create_mcp_http_client(headers=headers) as http_client:
            http_client.event_hooks = {"response": [self._note_auth_rejection]}
            async with (
                streamable_http_client(self._config.url, http_client=http_client) as streams,
                ClientSession(streams[0], streams[1]) as session,
            ):
                await session.initialize()
                return await session.call_tool(
                    tool,
                    {"project_id": self._config.project_id, **arguments},
                    read_timeout_seconds=_CALL_TIMEOUT,
                )

    async def _note_auth_rejection(self, response) -> None:
        """Remember a 401/403, which the MCP client reports only as a generic error."""
        if response.status_code in _AUTH_REJECTED:
            self._auth_rejected = response.status_code

    async def _call(self, tool: str, arguments: dict) -> dict:
        """Call a project tool and return its JSON result.

        Retries with exponential backoff when the server's limiter rejects the
        call (``rate_limit_exceeded``), honoring its ``retry_after`` hint, and
        when the connection fails. A streaming publisher (``retry_forever``)
        never gives up on those; a rejected API key or any other tool error is
        fatal and raised immediately.
        """
        attempt = 0
        while True:
            try:
                result = await self._call_once(tool, arguments)
            except Exception as exc:
                if self._auth_rejected is not None:
                    raise RuntimeError(
                        f"{tool} failed: Contex rejected the API key (HTTP {self._auth_rejected}); "
                        "check contex.api_key"
                    ) from exc
                cause = _single_error(exc)
                if isinstance(cause, MCPError) and cause.code in _FATAL_CODES:
                    raise RuntimeError(f"{tool} failed: {cause.message}") from exc
                error = str(cause) if isinstance(cause, MCPError) else repr(cause)
            else:
                text = result.content[0].text if result.content else ""
                if not result.is_error:  # mcp>=2 attribute; "isError" is only the JSON alias
                    return json.loads(text)
                if "rate_limit_exceeded" not in text:
                    raise RuntimeError(f"{tool} failed: {text or 'unknown error'}")
                error = text
            if not self._retry_forever and attempt == _MAX_RETRIES - 1:
                raise RuntimeError(f"{tool} failed: {error}")
            log.warning("%s failed (attempt %d), retrying: %s", tool, attempt + 1, error)
            await asyncio.sleep(_backoff(error, attempt))
            attempt += 1
