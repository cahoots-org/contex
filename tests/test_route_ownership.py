"""Tests: project→tenant ownership enforcement on data and cleanup routes.

With MULTI_TENANT_ENABLED=True and AUTH_ENABLED=True:
- tenant A gets 403 on a project owned by tenant B
- tenant A gets non-403 on its own project
- publish to a new project binds it (no 403)

With MULTI_TENANT_ENABLED=False: all pass unchanged.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from httpx import AsyncClient

from src.api.routes import router as api_router
from src.core import authz, ownership
from src.core.authz import get_identity
from src.core.identity import Identity
from src.core.rbac import Permission


def _identity(tenant_id, projects=()):
    return Identity(
        key_id="k",
        scopes=frozenset(Permission),
        tenant_id=tenant_id,
        projects=tuple(projects),
        role=None,
    )


def _build_app(identity: Identity):
    """Build a minimal FastAPI app with the API router and a fake engine."""
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    mock_engine = MagicMock()
    mock_engine.publish_data = AsyncMock(return_value="1")
    mock_engine.query_project_data = AsyncMock(return_value=[])
    mock_engine.event_store = MagicMock()
    mock_engine.event_store.get_events_since = AsyncMock(return_value=[])
    mock_engine.semantic_matcher = MagicMock()
    mock_engine.semantic_matcher.get_registered_data = AsyncMock(return_value=[])
    mock_engine._truncate_matches = MagicMock(return_value={})
    app.state.context_engine = mock_engine

    # Stub db — the TenantManager constructor needs it but we'll patch check_project_access
    app.state.db = MagicMock()

    app.dependency_overrides[get_identity] = lambda: identity
    return app


# ─── Multi-tenant ON ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_endpoint_403_cross_tenant(monkeypatch):
    """With multi-tenant on, tenant A gets 403 on tenant B's project."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    # TenantManager will say the project belongs to tenant-b
    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with patch("src.api.routes.get_tenant_manager", return_value=mock_mgr):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/projects/project-b/data")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_data_endpoint_200_own_project(monkeypatch):
    """With multi-tenant on, tenant A gets 200 on its own project."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-a")

    with (
        patch("src.api.routes.get_tenant_manager", return_value=mock_mgr),
        patch("src.api.routes.audit_log", new=AsyncMock()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/projects/project-a/data")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_publish_new_project_binds_to_tenant(monkeypatch):
    """With multi-tenant on, publishing to an unowned project binds it (no 403)."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value=None)
    mock_mgr.add_project = AsyncMock(return_value="tenant-a:project:new-proj")

    chainable_hist = MagicMock()
    chainable_hist.labels.return_value = MagicMock()
    chainable_hist.labels.return_value.observe = MagicMock()

    with (
        patch("src.api.routes.get_tenant_manager", return_value=mock_mgr),
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.emit_webhook", new=AsyncMock()),
        patch("src.core.metrics.record_event_published", new=MagicMock()),
        patch("src.core.metrics.publish_duration_seconds", new=chainable_hist),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/data/publish",
                json={"project_id": "new-proj", "data_key": "k", "data": {"x": 1}},
            )

    assert resp.status_code == 200
    mock_mgr.add_project.assert_awaited_once_with("tenant-a", "new-proj")


@pytest.mark.asyncio
async def test_events_endpoint_403_cross_tenant(monkeypatch):
    """With multi-tenant on, tenant A gets 403 on tenant B's events."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with patch("src.api.routes.get_tenant_manager", return_value=mock_mgr):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/projects/project-b/events")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cleanup_project_403_cross_tenant(monkeypatch):
    """With multi-tenant on, tenant A gets 403 on cleanup of tenant B's project."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with patch("src.api.routes.get_tenant_manager", return_value=mock_mgr):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/admin/cleanup/project-b")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_retention_stats_403_cross_tenant(monkeypatch):
    """With multi-tenant on, tenant A gets 403 on retention stats for tenant B's project."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with patch("src.api.routes.get_tenant_manager", return_value=mock_mgr):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/admin/retention/project-b")

    assert resp.status_code == 403


# ─── Multi-tenant OFF ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_endpoint_passes_when_multitenant_off(monkeypatch):
    """With multi-tenant off, cross-tenant calls are not 403."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", False)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with (
        patch("src.api.routes.get_tenant_manager", return_value=mock_mgr),
        patch("src.api.routes.audit_log", new=AsyncMock()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/projects/project-b/data")

    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_cleanup_passes_when_multitenant_off(monkeypatch):
    """With multi-tenant off, cleanup of any project is not 403."""
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", False)

    identity_a = _identity("tenant-a")
    app = _build_app(identity_a)

    mock_mgr = AsyncMock()
    mock_mgr.get_project_tenant = AsyncMock(return_value="tenant-b")

    with (
        patch("src.api.routes.get_tenant_manager", return_value=mock_mgr),
        patch("src.core.retention.get_retention_manager_from_env") as mock_ret,
    ):
        mock_ret_mgr = AsyncMock()
        mock_ret_mgr.cleanup_project = AsyncMock(return_value={"project_id": "project-b", "events_deleted": 0, "agents_cleaned": 0})
        mock_ret.return_value = mock_ret_mgr
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/admin/cleanup/project-b")

    assert resp.status_code != 403
