import json
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


async def _publish_once(server, key):
    return await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": key, "data": {"purpose": "x"},
    })


@pytest.mark.asyncio
async def test_direct_publish_throttled_at_configured_limit(db, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PUBLISH", "2")
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    await _publish_once(server, "a")
    await _publish_once(server, "b")
    # Over the wire this surfaces as is_error with the message; the in-process
    # call_tool re-raises the ToolError carrying the retry hint the publisher reads.
    with pytest.raises(ToolError, match="rate_limit_exceeded retry_after="):
        await _publish_once(server, "c")


@pytest.mark.asyncio
async def test_bulk_ingest_exempt_by_default(db, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PUBLISH", "1")  # direct is tight...
    monkeypatch.delenv("RATE_LIMIT_INGEST", raising=False)  # ...but ingest is exempt
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    for _ in range(5):
        res = await server.call_tool("contex_publish_batch", {
            "project_id": "p", "items": [{"data_key": "k", "data": {"purpose": "x"}}],
        })
        assert not res.is_error


@pytest.mark.asyncio
async def test_publish_tool_updates_subscription_bundle(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)

    res = json.loads((await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "db",
        "data": {"purpose": "database connection settings"}, "data_format": "json",
    })).content[0].text)
    assert res["published"] == "db"

    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert any(m["data_key"].startswith("db.") or m["data_key"] == "db" for matches in bundle.values() for m in matches)


@pytest.mark.asyncio
async def test_publish_tool_stamps_mcp_source(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "db",
        "data": {"purpose": "database connection settings"}, "data_format": "json",
    })

    events = await engine.event_store.get_all_events("p")
    assert events
    assert all(e["source"] == "mcp" for e in events)


@pytest.mark.asyncio
async def test_publish_batch_tool_stamps_mcp_source(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    await server.call_tool("contex_publish_batch", {
        "project_id": "p",
        "items": [
            {"data_key": "db", "data": {"purpose": "database connection settings"}},
            {"data_key": "cache", "data": {"purpose": "cache settings"}},
        ],
    })

    events = await engine.event_store.get_all_events("p")
    assert len(events) == 2
    assert all(e["source"] == "mcp" for e in events)


@pytest.mark.asyncio
async def test_query_methods_surface_source(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "db",
        "data": {"purpose": "database connection settings"}, "data_format": "json",
    })

    all_events = await engine.event_store.get_all_events("p")
    since_events = await engine.event_store.get_events_since("p", "0")
    assert all("source" in e for e in all_events)
    assert all("source" in e for e in since_events)
