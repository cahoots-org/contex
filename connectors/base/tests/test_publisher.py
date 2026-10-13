"""Unit tests for ContexPublisher.publish_batch error handling."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, CallToolResult, TextContent

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


def _error(text: str) -> CallToolResult:
    return CallToolResult(isError=True, content=[TextContent(type="text", text=text)])


def _ok(n: int) -> CallToolResult:
    return CallToolResult(isError=False, content=[TextContent(type="text", text=f'{{"published": {n}}}')])


def test_publish_batch_backs_off_and_retries_on_rate_limit(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("connectors.base.publisher.asyncio.sleep", fake_sleep)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    # Rate-limited twice (honoring retry_after=2), then accepted.
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[
        _error("Error executing tool contex_publish_batch: rate_limit_exceeded retry_after=2"),
        _error("Error executing tool contex_publish_batch: rate_limit_exceeded retry_after=2"),
        _ok(1),
    ]))
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert slept == [2.0, 2.0]


def test_publish_batch_does_not_retry_non_rate_limit_errors():
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    call = AsyncMock(return_value=_error("Error executing tool contex_publish_batch: boom"))
    pub._session = SimpleNamespace(call_tool=call)
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{}]))
    assert call.call_count == 1


def test_delete_batch_calls_contex_delete_and_counts_deleted():
    result = CallToolResult(
        isError=False,
        content=[TextContent(type="text", text='{"deleted": ["a", "b"], "missing": ["c"]}')],
    )
    pub = _publisher(result)
    assert asyncio.run(pub.delete_batch(["a", "b", "c"])) == 2
    pub._session.call_tool.assert_awaited_once_with(
        "contex_delete", {"project_id": "proj", "data_keys": ["a", "b", "c"]},
        read_timeout_seconds=300.0,
    )


def _json(text: str) -> CallToolResult:
    return CallToolResult(isError=False, content=[TextContent(type="text", text=text)])


def test_publish_batch_sends_the_origin():
    pub = ContexPublisher(SimpleNamespace(project_id="proj"), origin="s3:b/docs")
    pub._session = SimpleNamespace(call_tool=AsyncMock(return_value=_ok(1)))
    asyncio.run(pub.publish_batch([{"data_key": "k", "data": {}}]))
    assert pub._session.call_tool.call_args.args[1]["origin"] == "s3:b/docs"


def test_list_keys_follows_pages():
    pub = ContexPublisher(SimpleNamespace(project_id="proj"), origin="o")
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[
        _json('{"keys": ["a", "b"], "next": "b"}'),
        _json('{"keys": ["c"], "next": null}'),
    ]))
    assert asyncio.run(pub.list_keys()) == ["a", "b", "c"]
    second = pub._session.call_tool.call_args_list[1].args
    assert second == ("contex_list_keys", {"project_id": "proj", "origin": "o", "after": "b"})


def _no_sleep(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("connectors.base.publisher.asyncio.sleep", fake_sleep)
    return slept


def test_transport_error_reopens_and_retries(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[
        MCPError(CONNECTION_CLOSED, "Connection closed"),
        _ok(2),
    ]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}, {}])) == 2
    pub._reopen.assert_awaited_once()
    first, second = pub._session.call_tool.call_args_list
    assert first == second


def test_non_mcp_exception_is_treated_as_transport(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("reset"), _ok(1)]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}])) == 1
    pub._reopen.assert_awaited_once()


def test_reopen_failure_counts_as_an_attempt(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("down"), _ok(1)]))
    pub._reopen = AsyncMock(side_effect=[OSError("still down"), None])
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert pub._reopen.await_count == 2


def test_protocol_errors_are_fatal_without_reopen():
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=MCPError(INVALID_PARAMS, "bad items")))
    pub._reopen = AsyncMock()
    with pytest.raises(RuntimeError, match="contex_publish_batch failed: bad items"):
        asyncio.run(pub.publish_batch([{}]))
    pub._reopen.assert_not_awaited()


def test_snapshot_publisher_gives_up_after_max_retries(monkeypatch):
    _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"))
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=OSError("down")))
    pub._reopen = AsyncMock()
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{}]))
    assert pub._session.call_tool.await_count == 5


def test_streaming_publisher_retries_past_max_retries(monkeypatch):
    slept = _no_sleep(monkeypatch)
    pub = ContexPublisher(SimpleNamespace(project_id="proj"), retry_forever=True)
    pub._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("down")] * 8 + [_ok(1)]))
    pub._reopen = AsyncMock()
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert max(slept) == 60.0
