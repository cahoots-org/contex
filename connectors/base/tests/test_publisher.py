"""Unit tests for ContexPublisher.publish_batch error handling."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from connectors.base.publisher import ContexPublisher


def _publisher(result) -> ContexPublisher:
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(return_value=result))
    return pub


def test_publish_batch_raises_on_tool_error():
    # A tool error carries isError=True and a plain (non-JSON) text payload.
    result = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="Error executing tool contex_publish_batch")],
    )
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(_publisher(result).publish_batch([{"data_key": "k", "data": {}}]))


def test_publish_batch_returns_published_count():
    result = SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text='{"published": 3}')],
    )
    assert asyncio.run(_publisher(result).publish_batch([{}, {}, {}])) == 3
