"""The MCP transport: publish batches to Contex with an API key.

ContexPublisher opens one MCP session for the life of a run, reopening it when
the connection drops, and pushes each batch through the ``contex_publish_batch``
or ``contex_delete`` tool. It is the only connector module that touches the MCP
SDK; the runner and readers stay transport-free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, METHOD_NOT_FOUND

from .config import ContexConfig

# A continuous ingester will eventually trip the server's publish limiter; back
# off and retry instead of dying mid-run (mirrors the upstream-read clients).
_MAX_RETRIES = 5
_BACKOFF_CAP = 60.0  # seconds
# A publish that hasn't answered in this long is treated as a dropped connection.
_CALL_TIMEOUT = 300.0
# Protocol errors a retry can't fix; every other raised error is a transport failure.
_FATAL_CODES = (INVALID_PARAMS, METHOD_NOT_FOUND)
_RETRY_AFTER = re.compile(r"retry_after=(\d+(?:\.\d+)?)")

log = logging.getLogger(__name__)


def _backoff(error_text: str, attempt: int) -> float:
    """Seconds to wait: the server's ``retry_after`` hint, else exponential."""
    hint = _RETRY_AFTER.search(error_text)
    wait = float(hint.group(1)) if hint else 2.0 ** min(attempt, 10)
    return min(wait, _BACKOFF_CAP)


class ContexPublisher:
    """An async context manager that publishes item batches over MCP."""

    def __init__(self, config: ContexConfig, origin: str | None = None, retry_forever: bool = False):
        self._config = config
        self._origin = origin
        self._retry_forever = retry_forever
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "ContexPublisher":
        await self._open()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._close()

    async def _open(self) -> None:
        self._stack = AsyncExitStack()
        http_client = None
        if self._config.api_key:
            http_client = create_mcp_http_client(
                headers={"Authorization": f"Bearer {self._config.api_key}"}
            )
        streams = await self._stack.enter_async_context(
            streamable_http_client(self._config.url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(streams[0], streams[1])
        )
        await self._session.initialize()

    async def _close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def _reopen(self) -> None:
        """Replace a broken session with a fresh one."""
        try:
            await self._close()
        except Exception:
            log.debug("closing a broken MCP session failed", exc_info=True)
            self._stack = None
            self._session = None
        await self._open()

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

    async def _call(self, tool: str, arguments: dict) -> dict:
        """Call a project tool and return its JSON result.

        Retries with exponential backoff when the server's limiter rejects the
        call (``rate_limit_exceeded``), honoring its ``retry_after`` hint, and
        when the connection fails, reopening the session first. A streaming
        publisher (``retry_forever``) never gives up on those; any other tool
        error is fatal and raised immediately.
        """
        if self._session is None:
            raise RuntimeError("ContexPublisher must be used as an async context manager")
        attempt = 0
        broken = False
        while True:
            try:
                if broken:
                    await self._reopen()
                    broken = False
                result = await self._session.call_tool(
                    tool,
                    {"project_id": self._config.project_id, **arguments},
                    read_timeout_seconds=_CALL_TIMEOUT,
                )
            except MCPError as exc:
                if exc.code in _FATAL_CODES:
                    raise RuntimeError(f"{tool} failed: {exc.message}") from exc
                error, broken = str(exc), True
            except Exception as exc:
                error, broken = repr(exc), True
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
