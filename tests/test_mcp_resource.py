import json
import pytest
from mcp.server.mcpserver.exceptions import ResourceError
from src.core import mcp_adapter
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_read_subscription_resource_returns_bundle(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)
    sub_id = await engine.subscriptions.create("p", ["auth config"], top_k=10, threshold=0.1)

    contents = await server.read_resource(f"contex://subscriptions/{sub_id}")
    # read_resource returns an iterable of ReadResourceContents; take the first's text
    first = list(contents)[0]
    bundle = json.loads(first.content)
    assert "auth config" in bundle


@pytest.mark.asyncio
async def test_read_unknown_subscription_says_how_to_resume(db):
    """Reading a gone subscription tells the client to re-create it."""
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)

    with pytest.raises(ResourceError, match="contex_create_subscription"):
        await server.read_resource("contex://subscriptions/does_not_exist")


@pytest.mark.asyncio
async def test_denied_read_reports_permission_denied(db, monkeypatch):
    server, _ = build_mcp_server(ContextEngine(db=db, similarity_threshold=0.1, max_matches=10))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: None)

    with pytest.raises(ResourceError, match="^Permission denied$"):
        await server.read_resource("contex://subscriptions/any")
