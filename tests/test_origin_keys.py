"""Origin tags on published data and listing an origin's keys (#259)."""
import json

import pytest

from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


async def _server(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    return server


async def _publish(server, origin, *keys):
    await server.call_tool("contex_publish_batch", {
        "project_id": "p", "origin": origin,
        "items": [{"data_key": k, "data": {"purpose": f"doc {k}"}} for k in keys],
    })


async def _keys(server, origin, **kwargs):
    result = await server.call_tool("contex_list_keys", {"project_id": "p", "origin": origin, **kwargs})
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_list_keys_returns_only_that_origins_documents(db):
    server = await _server(db)
    await _publish(server, "s3:bucket/docs", "b", "a")
    await _publish(server, "github:o/r:files", "c")

    assert await _keys(server, "s3:bucket/docs") == {"keys": ["a", "b"], "next": None}


@pytest.mark.asyncio
async def test_unchanged_republish_takes_the_new_origin(db):
    server = await _server(db)
    await server.call_tool("contex_publish_batch", {
        "project_id": "p", "items": [{"data_key": "a", "data": {"purpose": "doc a"}}],
    })
    await _publish(server, "s3:bucket/docs", "a")  # same content: dedup skips the re-embed

    assert (await _keys(server, "s3:bucket/docs"))["keys"] == ["a"]


@pytest.mark.asyncio
async def test_list_keys_pages(db):
    server = await _server(db)
    await _publish(server, "o", "a", "b", "c")

    first = await _keys(server, "o", limit=2)
    assert first == {"keys": ["a", "b"], "next": "b"}
    assert await _keys(server, "o", limit=2, after=first["next"]) == {"keys": ["c"], "next": None}
