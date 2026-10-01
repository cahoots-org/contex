"""Unit tests for ContexPublisher.publish_batch error handling."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from connectors.base.publisher import ContexPublisher


def _publisher(result) -> ContexPublisher:
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(return_value=result))
    return pub


# Use the real CallToolResult so the test tracks the SDK's actual shape. A
# hand-rolled fake let the mcp 1.x->2.x rename (isError -> is_error attribute,
# isError now only the JSON alias) slip through green.
def test_publish_batch_raises_on_tool_error():
    result = CallToolResult(
        isError=True,  # wire alias; the object exposes .is_error
        content=[TextContent(type="text", text="Error executing tool contex_publish_batch")],
    )
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(_publisher(result).publish_batch([{"data_key": "k", "data": {}}]))


def test_publish_batch_returns_published_count():
    result = CallToolResult(
        isError=False,
        content=[TextContent(type="text", text='{"published": 3}')],
    )
    assert asyncio.run(_publisher(result).publish_batch([{}, {}, {}])) == 3
