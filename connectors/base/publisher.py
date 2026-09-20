"""The MCP transport: publish batches to Contex as a service account.

ContexPublisher opens one MCP session for the life of a run and pushes each
batch through the ``contex_publish_batch`` tool. It is the only connector module
that touches the MCP SDK; the runner and readers stay transport-free.
"""
from __future__ import annotations

import json
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .config import ContexConfig


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
        if self._session is None:
            raise RuntimeError("ContexPublisher must be used as an async context manager")
        result = await self._session.call_tool(
            "contex_publish_batch",
            {"project_id": self._config.project_id, "items": items},
        )
        payload = json.loads(result.content[0].text)
        return int(payload.get("published", 0))
