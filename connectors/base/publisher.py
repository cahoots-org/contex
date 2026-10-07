"""The MCP transport: publish batches to Contex as a service account.

ContexPublisher opens one MCP session for the life of a run and pushes each
batch through the ``contex_publish_batch`` or ``contex_delete`` tool. It is the only connector module
that touches the MCP SDK; the runner and readers stay transport-free.
"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .config import ContexConfig

# A continuous ingester will eventually trip the server's publish limiter; back
# off and retry instead of dying mid-run (mirrors the upstream-read clients).
_MAX_RETRIES = 5
_BACKOFF_CAP = 60.0  # seconds
_RETRY_AFTER = re.compile(r"retry_after=(\d+(?:\.\d+)?)")


def _backoff(error_text: str, attempt: int) -> float:
    """Seconds to wait: the server's ``retry_after`` hint, else exponential."""
    hint = _RETRY_AFTER.search(error_text)
    wait = float(hint.group(1)) if hint else 2.0**attempt
    return min(wait, _BACKOFF_CAP)


class ContexPublisher:
    """An async context manager that publishes item batches over MCP."""

    def __init__(self, config: ContexConfig):
        self._config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "ContexPublisher":
        self._stack = AsyncExitStack()
        http_client = None
        if self._config.service_account_token:
            http_client = create_mcp_http_client(
                headers={"Authorization": f"Bearer {self._config.service_account_token}"}
            )
        streams = await self._stack.enter_async_context(
            streamable_http_client(self._config.url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(streams[0], streams[1])
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def publish_batch(self, items: list[dict]) -> int:
        """Publish one batch; return how many items the server accepted."""
        result = await self._call("contex_publish_batch", {"items": items})
        return int(result.get("published", 0))

    async def delete_batch(self, data_keys: list[str]) -> int:
        """Delete one batch of keys; return how many existed."""
        result = await self._call("contex_delete", {"data_keys": data_keys})
        return len(result.get("deleted", []))

    async def _call(self, tool: str, arguments: dict) -> dict:
        """Call a project tool and return its JSON result.

        Retries with exponential backoff when the server's limiter rejects the
        call (``rate_limit_exceeded``), honoring its ``retry_after`` hint. Any
        other tool error is fatal and raised immediately.
        """
        if self._session is None:
            raise RuntimeError("ContexPublisher must be used as an async context manager")
        for attempt in range(_MAX_RETRIES):
            result = await self._session.call_tool(
                tool, {"project_id": self._config.project_id, **arguments}
            )
            text = result.content[0].text if result.content else ""
            if not result.is_error:  # mcp>=2 attribute; "isError" is only the JSON alias
                return json.loads(text)
            if "rate_limit_exceeded" not in text or attempt == _MAX_RETRIES - 1:
                raise RuntimeError(f"{tool} failed: {text or 'unknown error'}")
            await asyncio.sleep(_backoff(text, attempt))
        raise RuntimeError(f"{tool} failed: exhausted retries")  # unreachable
