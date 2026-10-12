import base64
import json
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import select

from src.core.db_models import Embedding
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
    await engine.semantic_matcher.initialize_index()
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
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)

    for _ in range(5):
        res = await server.call_tool("contex_publish_batch", {
            "project_id": "p", "items": [{"data_key": "k", "data": {"purpose": "x"}}],
        })
        assert not res.is_error


@pytest.mark.asyncio
async def test_publish_tool_updates_subscription_bundle(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
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
async def test_batch_item_without_format_is_detected_from_its_key(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)

    await server.call_tool("contex_publish_batch", {
        "project_id": "p", "items": [{"data_key": "app.py", "data": "def hello():\n    return 1\n"}],
    })

    async with db.session() as session:
        keys = set((await session.execute(select(Embedding.node_key))).scalars())
    assert "app.py\x1fhello" in keys


@pytest.mark.asyncio
async def test_batch_reports_unreadable_items_and_empty_content_clears_old_nodes(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)
    await server.call_tool("contex_publish", {
        "project_id": "p", "data_key": "notes", "data": {"text": "old secret content here."},
    })

    res = json.loads((await server.call_tool("contex_publish_batch", {"project_id": "p", "items": [
        {"data_key": "notes", "data": ""},
        {"data_key": "bad.pdf", "data": base64.b64encode(b"%PDF-1.7 truncated").decode(), "data_format": "pdf"},
    ]})).content[0].text)

    assert res["published"] == 1
    assert [f["data_key"] for f in res["failed"]] == ["bad.pdf"]
    async with db.session() as session:
        assert (await session.execute(select(Embedding.node_key))).all() == []
