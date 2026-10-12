# tests/test_tenant_isolation.py
"""Multi-tenant isolation on the MCP surface and the subscription service.

The sandbox is only served with auth off, so with auth on it must 404.
"""
from unittest.mock import MagicMock

import httpx
import pytest
from mcp import Client
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from sqlalchemy import text

from main import app
from src.core import mcp_adapter
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import resource_uri_for
from src.core.rbac import Role, expand_role
from src.core.subscriptions import SubscriptionService


# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_tenant(db, tenant_id: str) -> None:
    async with db.session() as session:
        exists = (await session.execute(
            text("SELECT 1 FROM tenants WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )).scalar_one_or_none()
        if not exists:
            await session.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name, plan, quotas, settings, metadata, is_active)"
                    " VALUES (:tid, :name, 'enterprise', '{}', '{}', '{}', true)"
                ),
                {"tid": tenant_id, "name": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO tenant_usage (tenant_id, projects_count, agents_count,"
                    " api_keys_count, events_this_month, storage_used_mb)"
                    " VALUES (:tid, 0, 0, 0, 0, 0.0)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"tid": tenant_id},
            )
            await session.commit()


async def _seed_tenant_project(db, tenant_id: str, project_id: str) -> None:
    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO tenant_projects (tenant_id, project_id)"
                " VALUES (:tid, :pid) ON CONFLICT DO NOTHING"
            ),
            {"tid": tenant_id, "pid": project_id},
        )
        await session.commit()


# ── subscription ownership at service layer ─────────────────────────────────────


@pytest.mark.asyncio
async def test_subscription_cross_tenant_denied_at_service(db, monkeypatch):
    """SubscriptionService.get_bundle raises PermissionError for cross-tenant access."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")

    class _StubMatcher:
        async def match(self, project_id, needs, top_k=None, threshold=None, since=None):
            return {n: [] for n in needs}

    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("proj-b", ["some need"], tenant_id="tenant-B")

    with pytest.raises(PermissionError):
        await svc.get_bundle(sub_id, tenant_id="tenant-A")


# ── /sandbox is not served with auth on ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,form", [
    ("GET", "/sandbox/", None),
    ("POST", "/sandbox/query", {"project_id": "p", "query": "secrets"}),
    ("GET", "/sandbox/projects/p/stats", None),
    ("GET", "/sandbox/projects/p/data", None),
    ("GET", "/sandbox/subscribe?project_id=p&need=secrets", None),
])
async def test_sandbox_is_not_served_with_auth_on(monkeypatch, method, path, form):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app.state.context_engine = MagicMock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8001") as c:
        r = await c.request(method, path, data=form)
    assert r.status_code == 404


# ── MCP project-scoped tools: cross-tenant → denied ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("contex_query", {"query": "secrets"}),
    ("contex_create_subscription", {"needs": ["secrets"]}),
    ("contex_publish", {"data_key": "k", "data": {"a": 1}}),
    ("contex_publish_batch", {"items": [{"data_key": "k", "data": {"a": 1}}]}),
    ("contex_list_keys", {"origin": "o"}),
    ("contex_delete", {"data_keys": ["k"]}),
])
async def test_mcp_tool_cross_tenant_is_denied(db, monkeypatch, tool, args):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    await _seed_tenant_project(db, "tenant-B", "proj-b")

    token = AccessToken(token="t", client_id="key-a", scopes=[p.value for p in expand_role(Role.ADMIN)],
                        claims={"tenant_id": "tenant-A", "projects": []})
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: token)
    server, _ = build_mcp_server(ContextEngine(db=db, similarity_threshold=0.0, max_matches=10))

    with pytest.raises(ToolError) as denied:
        await server.call_tool(tool, {"project_id": "proj-b", **args})
    assert isinstance(denied.value.__cause__, PermissionError)


@pytest.mark.asyncio
async def test_mcp_publish_claims_new_projects_for_the_callers_tenant(db, monkeypatch):
    """Publishing binds new projects to the caller's tenant, with the default tenant as migrations leave it."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    async with db.session() as session:
        await session.execute(text("UPDATE tenants SET quotas = '{}' WHERE tenant_id = 'default'"))
        await session.execute(text("DELETE FROM tenant_usage WHERE tenant_id = 'default'"))
    token = AccessToken(token="t", client_id="key-d", scopes=[p.value for p in expand_role(Role.ADMIN)],
                        claims={"tenant_id": "default", "projects": []})
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: token)
    server, _ = build_mcp_server(ContextEngine(db=db, similarity_threshold=0.0, max_matches=10))

    for i in range(5):
        await server.call_tool("contex_publish", {"project_id": f"p{i}", "data_key": "k", "data": {"a": 1}})
    await server.call_tool("contex_query", {"project_id": "p4", "query": "a"})


@pytest.mark.asyncio
async def test_projects_published_with_auth_off_stay_with_the_default_tenant(db, monkeypatch):
    server, _ = build_mcp_server(ContextEngine(db=db, similarity_threshold=0.0, max_matches=10))
    await server.call_tool("contex_publish", {"project_id": "my-app", "data_key": "k", "data": {"a": 1}})

    monkeypatch.setenv("AUTH_ENABLED", "true")
    await _ensure_tenant(db, "acme")
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: _admin_token("acme"))
    with pytest.raises(ToolError, match="Permission denied"):
        await server.call_tool("contex_publish", {"project_id": "my-app", "data_key": "x", "data": {"a": 1}})
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: _admin_token("default"))
    await server.call_tool("contex_query", {"project_id": "my-app", "query": "a"})


def _admin_token(tenant_id: str, key_id: str = "key-a") -> AccessToken:
    return AccessToken(token="t", client_id=key_id, scopes=[p.value for p in expand_role(Role.ADMIN)],
                       claims={"tenant_id": tenant_id, "projects": []})


@pytest.mark.asyncio
async def test_mcp_listen_on_other_tenants_subscription_is_denied(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    engine = ContextEngine(db=db, similarity_threshold=0.0, max_matches=10)
    sub_id = await engine.subscriptions.create("proj-b", ["secrets"], tenant_id="tenant-B")
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: _admin_token("tenant-A"))
    server, _ = build_mcp_server(engine)

    async with Client(server) as client:
        with pytest.raises(MCPError):
            async with client.listen(resource_subscriptions=[resource_uri_for(sub_id)]):
                pass


@pytest.mark.asyncio
async def test_mcp_listen_streams_are_capped_per_key(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(mcp_adapter, "_MAX_LISTEN_STREAMS_PER_KEY", 1)
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: _admin_token("default"))
    server, _ = build_mcp_server(ContextEngine(db=db, similarity_threshold=0.0, max_matches=10))

    async with Client(server) as client:
        async with client.listen(tools_list_changed=True):
            with pytest.raises(MCPError):
                async with client.listen(tools_list_changed=True):
                    pass
