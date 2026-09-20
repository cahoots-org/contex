import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_contex_query_tool_returns_matches(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db", data={"purpose": "database connection settings"}, data_format="json",
    ))
    server, bus = build_mcp_server(engine)
    result = await server.call_tool("contex_query", {
        "project_id": "p", "query": "database connection settings", "top_k": 10, "threshold": 0.1,
    })
    # call_tool returns a CallToolResult; its content carries the JSON payload text
    text = result.content[0].text
    payload = json.loads(text)
    # Engine decomposes JSON objects into nodes; "db" data_key becomes "db.root" (or "db.<path>")
    assert any(m["data_key"].startswith("db") for m in payload["matches"])


@pytest.mark.asyncio
async def test_query_no_matches_returns_empty(db, redis):
    """A query against a project with no data (or no matches above threshold)
    must return an empty matches list."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.99, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    result = await server.call_tool("contex_query", {
        # Use an isolated project so no data exists, and a very high threshold
        # so even if stray data were present it would not match.
        "project_id": "empty_project_xyz", "query": "this query matches nothing", "top_k": 10, "threshold": 0.99,
    })
    payload = json.loads(result.content[0].text)
    assert payload["matches"] == []


@pytest.mark.asyncio
async def test_contex_publish_batch_publishes_all_items(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    result = await server.call_tool("contex_publish_batch", {
        "project_id": "batch_p",
        "items": [
            {"data_key": "svc.a", "data": {"role": "payments api"}},
            {"data_key": "svc.b", "data": {"role": "billing worker"}},
            {"data_key": "svc.c", "data": {"role": "notification dispatcher"}},
        ],
    })
    payload = json.loads(result.content[0].text)
    assert payload["published"] == 3

    # the batched items are queryable
    q = await server.call_tool("contex_query", {
        "project_id": "batch_p", "query": "payments api", "top_k": 5, "threshold": 0.1,
    })
    matches = json.loads(q.content[0].text)["matches"]
    assert any(m["data_key"].startswith("svc.a") for m in matches)


@pytest.mark.asyncio
async def test_contex_publish_batch_rejects_oversized_batch(db, redis, monkeypatch):
    import src.core.limits as limits
    monkeypatch.setattr(limits, "MAX_BATCH_SIZE", 2)
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    items = [{"data_key": f"k{i}", "data": {"v": i}} for i in range(3)]
    with pytest.raises(Exception):
        await server.call_tool("contex_publish_batch", {"project_id": "p", "items": items})
