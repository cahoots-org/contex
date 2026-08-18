import json
import pytest
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server


@pytest.mark.asyncio
async def test_create_and_delete_subscription_tools(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    created = json.loads((await server.call_tool("contex_create_subscription", {
        "project_id": "p", "needs": ["auth config"], "top_k": 10, "threshold": 0.1,
    })).content[0].text)
    sub_id = created["subscription_id"]
    assert created["resource_uri"] == f"contex://subscriptions/{sub_id}"
    # bundle now exists
    assert await engine.subscriptions.get_bundle(sub_id) is not None

    deleted = json.loads((await server.call_tool("contex_delete_subscription", {
        "subscription_id": sub_id,
    })).content[0].text)
    assert deleted["deleted"] == sub_id
    with pytest.raises(KeyError):
        await engine.subscriptions.get_bundle(sub_id)
