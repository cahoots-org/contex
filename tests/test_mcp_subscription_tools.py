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


@pytest.mark.asyncio
async def test_delete_nonexistent_is_idempotent(db, redis):
    """Deleting a subscription that doesn't exist must succeed with no error
    and return {"deleted": <id>} (idempotent delete)."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    result = await server.call_tool("contex_delete_subscription", {"subscription_id": "sub_nope"})
    payload = json.loads(result.content[0].text)
    assert payload == {"deleted": "sub_nope"}


@pytest.mark.asyncio
async def test_create_with_empty_needs(db, redis):
    """Creating a subscription with no needs should succeed and produce an empty bundle."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    result = await server.call_tool("contex_create_subscription", {
        "project_id": "p", "needs": [], "top_k": 10, "threshold": 0.1,
    })
    payload = json.loads(result.content[0].text)
    sub_id = payload["subscription_id"]
    assert "subscription_id" in payload
    assert payload["resource_uri"] == f"contex://subscriptions/{sub_id}"

    bundle = await engine.subscriptions.get_bundle(sub_id)
    # No needs → empty bundle (nothing to match)
    assert bundle == {}
