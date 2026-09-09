# tests/test_tenant_isolation.py
"""End-to-end multi-tenant isolation matrix.

Proves that the ownership checks actually isolate tenants at the HTTP and
service layers, and that isolation is a no-op when auth is off.

Transport: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), ...) —
no lifespan runs; app.state.db is wired in each test that needs real DB access.
Ownership and auth checks run before any handler body that needs the engine,
so 403s are delivered without requiring Redis / context engine init.
"""
import hashlib
import secrets
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import text

from main import app
from src.core.authz import get_identity
from src.core.identity import Identity
from src.core.rbac import Permission, Role, expand_role
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


def _stub_context_engine() -> MagicMock:
    """Minimal stub so handler bodies after the ownership check don't AttributeError."""
    engine = MagicMock()
    engine.semantic_matcher = MagicMock()
    engine.semantic_matcher.get_registered_data = AsyncMock(return_value=[])
    engine.publish_data = AsyncMock(return_value="1")
    engine.event_store = MagicMock()
    engine.event_store.get_events_since = AsyncMock(return_value=[])
    return engine


async def _seed_api_key(db, key_id: str, tenant_id: str) -> None:
    """Insert a minimal api_keys row for tenant_id (no real hash needed)."""
    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO api_keys (key_id, key_hash, name, prefix, scopes, tenant_id)"
                " VALUES (:kid, :hash, :name, 'ck_tst', '{}', :tid)"
            ),
            {
                "kid": key_id,
                "hash": hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
                "name": f"key-{key_id}",
                "tid": tenant_id,
            },
        )
        await session.commit()


# ── Case 1: cross-tenant project data → 403; own project → not 403 ─────────────


@pytest.mark.asyncio
async def test_cross_tenant_project_data_is_403(db, monkeypatch):
    """tenant-A identity gets 403 on tenant-B's project."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    await _seed_tenant_project(db, "tenant-A", "proj-a")
    await _seed_tenant_project(db, "tenant-B", "proj-b")

    app.state.db = db
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/v1/projects/proj-b/data")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_own_project_data_is_not_403(db, monkeypatch):
    """tenant-A identity gets non-403 on its own project."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _seed_tenant_project(db, "tenant-A", "proj-a")

    app.state.db = db
    app.state.context_engine = _stub_context_engine()
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/v1/projects/proj-a/data")
        assert r.status_code != 403
    finally:
        app.dependency_overrides.clear()


# ── Case 2: GET /auth/keys returns only caller's tenant keys ────────────────────


@pytest.mark.asyncio
async def test_list_keys_scoped_to_caller_tenant(db, monkeypatch):
    """GET /auth/keys returns only tenant-A's keys when called as tenant-A."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    await _seed_api_key(db, "key-a1", "tenant-A")
    await _seed_api_key(db, "key-a2", "tenant-A")
    await _seed_api_key(db, "key-b1", "tenant-B")

    app.state.db = db
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/v1/auth/keys")
        assert r.status_code == 200
        ids = {k["key_id"] for k in r.json()}
        assert "key-a1" in ids
        assert "key-a2" in ids
        assert "key-b1" not in ids
    finally:
        app.dependency_overrides.clear()


# ── Case 3: publish to new project → binds to tenant-A ─────────────────────────


@pytest.mark.asyncio
async def test_publish_new_project_binds_to_tenant_a(db, monkeypatch):
    """Publishing to an unowned project binds it to the caller's tenant."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    # proj-new is intentionally absent from tenant_projects

    app.state.db = db
    app.state.context_engine = _stub_context_engine()
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/v1/data/publish",
                json={"project_id": "proj-new", "data_key": "dk1", "data": {"x": 1}},
            )
        # Ownership auto-binding happens; handler may 500 if engine isn't full, but not 403.
        assert r.status_code != 403

        # Assert the tenant_projects row was created for tenant-A.
        async with db.session() as session:
            row = (await session.execute(
                text(
                    "SELECT tenant_id FROM tenant_projects"
                    " WHERE project_id = 'proj-new'"
                )
            )).first()
        assert row is not None and row[0] == "tenant-A"
    finally:
        app.dependency_overrides.clear()


# ── Case 4: subscription ownership at service layer ─────────────────────────────


@pytest.mark.asyncio
async def test_subscription_cross_tenant_denied_at_service(db, redis, monkeypatch):
    """SubscriptionService.get_bundle raises PermissionError for cross-tenant access."""
    monkeypatch.setenv("AUTH_ENABLED", "true")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")

    class _StubMatcher:
        async def match(self, project_id, needs, top_k=None, threshold=None):
            return {n: [] for n in needs}

    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("proj-b", ["some need"], tenant_id="tenant-B")

    with pytest.raises(PermissionError):
        await svc.get_bundle(sub_id, tenant_id="tenant-A")


# ── Case 5: /sandbox/subscribe cross-tenant → 403 ──────────────────────────────


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


# ── Case 6: auth OFF → cross-tenant call is NOT 403 ───────────────────────────


@pytest.mark.asyncio
async def test_cross_tenant_data_not_403_when_auth_off(db, monkeypatch):
    """With auth off, cross-tenant project access is not blocked."""
    monkeypatch.setenv("AUTH_ENABLED", "false")

    await _ensure_tenant(db, "tenant-A")
    await _ensure_tenant(db, "tenant-B")
    await _seed_tenant_project(db, "tenant-B", "proj-b-off")

    app.state.db = db
    app.state.context_engine = _stub_context_engine()
    app.dependency_overrides[get_identity] = lambda: _admin_identity("tenant-A")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/v1/projects/proj-b-off/data")
        assert r.status_code != 403
    finally:
        app.dependency_overrides.clear()
