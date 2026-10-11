# tests/test_tenant_isolation.py
"""Multi-tenant isolation on the surviving surfaces (service layer + sandbox).

Proves that ownership checks isolate tenants at the SubscriptionService layer
and on the /sandbox SSE route. The REST-route isolation cases were removed with
the REST API teardown (#189); the durable guarantees live at the service layer
(exercised by MCP) and the sandbox, both covered below.

Transport: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), ...) —
no lifespan runs; app.state.db is wired in each test that needs real DB access.
The ownership check runs before any handler body that needs the engine, so a 403
is delivered without requiring context engine init.
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import text

from main import app
from src.core import mcp_adapter
from src.core.authz import get_identity
from src.core.context_engine import ContextEngine
from src.core.identity import Identity
from src.core.mcp_adapter import build_mcp_server
from src.core.rbac import Role, expand_role
from src.core.subscriptions import SubscriptionService


# ── helpers ────────────────────────────────────────────────────────────────────


def _admin_identity(tenant_id: str) -> Identity:
    """Full-admin identity scoped to tenant_id (all permissions; projects=() = all)."""
    return Identity(
        key_id="k",
        scopes=expand_role(Role.ADMIN),
        tenant_id=tenant_id,
        projects=(),
        role=Role.ADMIN,
    )


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


# ── /sandbox/subscribe cross-tenant → 403 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_subscribe_cross_tenant_is_403(db, monkeypatch):
    """/sandbox/subscribe returns 403 for cross-tenant project before SSE starts.

    The ownership check (ensure_project_access) runs synchronously before the
    StreamingResponse generator is entered, so a 403 is returned immediately
    without hanging on SSE.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    await _seed_tenant_project(db, "tenant-B", "proj-b-sub")

    app.state.db = db
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(
                "/sandbox/subscribe",
                params={"project_id": "proj-b-sub", "need": "auth tokens"},
            )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


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
