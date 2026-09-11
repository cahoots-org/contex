import json
import pytest
from sqlalchemy import text
from unittest.mock import MagicMock

from src.core import mcp_adapter
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server
from mcp.server.auth.middleware.auth_context import get_access_token


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


@pytest.mark.asyncio
async def test_multitenant_create_read_delete_lifecycle(db, redis, monkeypatch):
    """Full create→get_bundle→delete lifecycle succeeds for a real-tenant caller.

    Regression test for the bug where contex_create_subscription stored
    tenant_id='default' regardless of the caller's token claims, causing
    _assert_sub_tenant to raise PermissionError on subsequent read/delete.
    """
    tenant_id = "tenant-A"

    async with db.session() as session:
        await session.execute(
            text("INSERT INTO tenants (tenant_id, name, is_active) VALUES (:tid, :name, true) ON CONFLICT DO NOTHING"),
            {"tid": tenant_id, "name": tenant_id},
        )
        await session.execute(
            text("INSERT INTO tenant_projects (tenant_id, project_id) VALUES (:tid, :pid) ON CONFLICT DO NOTHING"),
            {"tid": tenant_id, "pid": "proj-mt"},
        )
        await session.commit()

    fake_token = MagicMock()
    fake_token.scopes = ["query_data"]
    fake_token.claims = {"tenant_id": tenant_id, "projects": []}

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: fake_token)

    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)

    created = json.loads((await server.call_tool("contex_create_subscription", {
        "project_id": "proj-mt", "needs": ["auth config"], "top_k": 5,
    })).content[0].text)
    sub_id = created["subscription_id"]

    bundle = await engine.subscriptions.get_bundle(sub_id, tenant_id=tenant_id)
    assert bundle is not None

    deleted = json.loads((await server.call_tool("contex_delete_subscription", {
        "subscription_id": sub_id,
    })).content[0].text)
    assert deleted["deleted"] == sub_id

    with pytest.raises(KeyError):
        await engine.subscriptions.get_bundle(sub_id, tenant_id=tenant_id)
