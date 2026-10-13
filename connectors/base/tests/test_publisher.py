"""Unit tests for ContexPublisher.publish_batch error handling."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, CallToolResult, TextContent

from connectors.base.config import ContexConfig
from connectors.base.publisher import ContexPublisher


def _publisher(*effects, **kwargs) -> ContexPublisher:
    pub = ContexPublisher(SimpleNamespace(project_id="proj"), **kwargs)
    pub._call_once = AsyncMock(side_effect=list(effects))
    return pub


def _error(text: str) -> CallToolResult:
    return CallToolResult(isError=True, content=[TextContent(type="text", text=text)])


def _json(text: str) -> CallToolResult:
    return CallToolResult(isError=False, content=[TextContent(type="text", text=text)])


def _ok(n: int) -> CallToolResult:
    return _json(f'{{"published": {n}}}')


def _no_sleep(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("connectors.base.publisher.asyncio.sleep", fake_sleep)
    return slept


# Use the real CallToolResult so the test tracks the SDK's actual shape. A
# hand-rolled fake let the mcp 1.x->2.x rename (isError -> is_error attribute,
# isError now only the JSON alias) slip through green.
def test_publish_batch_raises_on_tool_error():
    pub = _publisher(_error("Error executing tool contex_publish_batch"))
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{"data_key": "k", "data": {}}]))


def test_publish_batch_returns_published_count():
    pub = _publisher(_json('{"published": 3, "failed": []}'))
    assert asyncio.run(pub.publish_batch([{}, {}, {}])) == 3


def test_publish_batch_backs_off_and_retries_on_rate_limit(monkeypatch):
    slept = _no_sleep(monkeypatch)
    limited = "Error executing tool contex_publish_batch: rate_limit_exceeded retry_after=2"
    pub = _publisher(_error(limited), _error(limited), _ok(1))
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert slept == [2.0, 2.0]


def test_publish_batch_does_not_retry_non_rate_limit_errors():
    pub = _publisher(_error("Error executing tool contex_publish_batch: boom"))
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{}]))
    assert pub._call_once.await_count == 1


def test_delete_batch_calls_contex_delete_and_counts_deleted():
    pub = _publisher(_json('{"deleted": ["a", "b"], "missing": ["c"]}'))
    assert asyncio.run(pub.delete_batch(["a", "b", "c"])) == 2
    pub._call_once.assert_awaited_once_with("contex_delete", {"data_keys": ["a", "b", "c"]})


def test_publish_batch_sends_the_origin():
    pub = _publisher(_ok(1), origin="s3:b/docs")
    asyncio.run(pub.publish_batch([{"data_key": "k", "data": {}}]))
    assert pub._call_once.call_args.args[1]["origin"] == "s3:b/docs"


def test_list_keys_follows_pages():
    pub = _publisher(
        _json('{"keys": ["a", "b"], "next": "b"}'),
        _json('{"keys": ["c"], "next": null}'),
        origin="o",
    )
    assert asyncio.run(pub.list_keys()) == ["a", "b", "c"]
    second = pub._call_once.call_args_list[1].args
    assert second == ("contex_list_keys", {"origin": "o", "after": "b"})


def test_transport_error_is_retried_with_the_same_arguments(monkeypatch):
    _no_sleep(monkeypatch)
    pub = _publisher(MCPError(CONNECTION_CLOSED, "Connection closed"), _ok(2))
    assert asyncio.run(pub.publish_batch([{}, {}])) == 2
    first, second = pub._call_once.call_args_list
    assert first == second


def test_non_mcp_exception_is_treated_as_transport(monkeypatch):
    _no_sleep(monkeypatch)
    pub = _publisher(OSError("reset"), _ok(1))
    assert asyncio.run(pub.publish_batch([{}])) == 1


def test_protocol_errors_are_fatal():
    pub = _publisher(MCPError(INVALID_PARAMS, "bad items"))
    with pytest.raises(RuntimeError, match="contex_publish_batch failed: bad items"):
        asyncio.run(pub.publish_batch([{}]))
    assert pub._call_once.await_count == 1


def test_snapshot_publisher_gives_up_after_max_retries(monkeypatch):
    _no_sleep(monkeypatch)
    pub = _publisher(*[OSError("down")] * 5)
    with pytest.raises(RuntimeError, match="contex_publish_batch failed"):
        asyncio.run(pub.publish_batch([{}]))
    assert pub._call_once.await_count == 5


def test_streaming_publisher_retries_past_max_retries(monkeypatch):
    slept = _no_sleep(monkeypatch)
    pub = _publisher(*[OSError("down")] * 8, _ok(1), retry_forever=True)
    assert asyncio.run(pub.publish_batch([{}])) == 1
    assert max(slept) == 60.0


def _http_publisher(monkeypatch, status: int, body: dict | None = None, **kwargs):
    """A publisher whose HTTP clients answer every request with ``status`` and ``body``."""
    clients = []

    def client(headers=None):
        made = httpx2.AsyncClient(
            headers=headers,
            transport=httpx2.MockTransport(lambda request: httpx2.Response(status, json=body)),
        )
        clients.append(made)
        return made

    monkeypatch.setattr("connectors.base.publisher.create_mcp_http_client", client)
    config = ContexConfig(url="http://contex.test/mcp", project_id="proj", api_key="k")
    return ContexPublisher(config, **kwargs), clients


def test_each_attempt_closes_its_http_client(monkeypatch):
    pub, clients = _http_publisher(monkeypatch, 503)
    with pytest.raises(Exception):
        asyncio.run(pub._call_once("contex_publish_batch", {"items": []}))
    assert [c.is_closed for c in clients] == [True]


def test_protocol_error_from_the_server_is_fatal(monkeypatch):
    _no_sleep(monkeypatch)
    rejected = {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_PARAMS, "message": "bad items"}}
    pub, clients = _http_publisher(monkeypatch, 400, rejected)
    with pytest.raises(RuntimeError, match="contex_publish_batch failed: bad items"):
        asyncio.run(pub.publish_batch([{}]))
    assert len(clients) == 1
