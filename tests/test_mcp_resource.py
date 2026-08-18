import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_read_subscription_resource_returns_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    sub_id = await engine.subscriptions.create("p", ["auth config"], top_k=10, threshold=0.1)

    contents = await server.read_resource(f"contex://subscriptions/{sub_id}")
    # read_resource returns an iterable of ReadResourceContents; take the first's text
    first = list(contents)[0]
    bundle = json.loads(first.content)
    assert "auth config" in bundle
