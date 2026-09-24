import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_publish_tool_updates_subscription_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
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
async def test_publish_tool_stamps_mcp_source(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
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
async def test_publish_batch_tool_stamps_mcp_source(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
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
async def test_query_methods_surface_source(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
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
