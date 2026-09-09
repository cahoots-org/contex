# tests/test_authz_matrix.py
"""
End-to-end authz matrix + negative-auth integration tests.

These tests exercise the REAL main.app (same routers, same dependency chain) to
prove that the authorization wiring is correct for each role.

Transport approach: httpx.ASGITransport (no lifespan)
-------------------------------------------------------
In httpx 0.27.x, AsyncClient(app=app, ...) and
AsyncClient(transport=ASGITransport(app=app), ...) both skip the lifespan.
We rely on this: the lifespan is heavy (migrations, Redis, context engine) and
fragile in a unit-test context.  The assertions here — 401 (no credential),
403 (wrong role), 404 (missing route) — all resolve before any handler runs
and therefore before any DB or Redis access is needed:

  * 401 → get_identity() raises HTTPException before touching db
  * 403 → require() raises HTTPException before the route body runs
  * 404 → FastAPI routing layer, no handler at all
  * != 403 (publisher) → authz passes; if the engine isn't initialised we get
    a 500, which is still != 403 and satisfies the assertion

We use AsyncClient(app=app_authed, base_url="http://t") (the convenience form)
rather than explicitly constructing ASGITransport — both behave identically in
this httpx version and the former is the idiomatic form used in the brief.
"""
import pytest
import httpx
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock

from src.core import authz
from src.core.authz import get_identity
from src.core.identity import Identity
from src.core.rbac import Permission, Role, expand_role


def _identity(role: Role) -> Identity:
    return Identity(key_id="k", scopes=expand_role(role), tenant_id="t", projects=(), role=role)


@pytest.fixture
def app_authed(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    from main import app

    mock_engine = MagicMock()
    mock_engine.publish_data = AsyncMock(return_value="1")
    app.state.context_engine = mock_engine
    app.state.db = MagicMock()

    return app


@pytest.mark.asyncio
async def test_missing_key_is_401(app_authed):
    async with AsyncClient(transport=httpx.ASGITransport(app=app_authed), base_url="http://t") as c:
        r = await c.post("/api/v1/data/publish", json={})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_readonly_cannot_publish(app_authed):
    app_authed.dependency_overrides[get_identity] = lambda: _identity(Role.READONLY)
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app_authed), base_url="http://t") as c:
            r = await c.post("/api/v1/data/publish",
                             json={"project_id": "p", "data_key": "k", "data": {}})
            assert r.status_code == 403
    finally:
        app_authed.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_publisher_can_reach_publish(app_authed):
    app_authed.dependency_overrides[get_identity] = lambda: _identity(Role.PUBLISHER)
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app_authed), base_url="http://t") as c:
            r = await c.post("/api/v1/data/publish",
                             json={"project_id": "p", "data_key": "k", "data": {}})
            assert r.status_code != 403  # authz passes (may 4xx/5xx on body/engine, not 403)
    finally:
        app_authed.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_legacy_api_alias_is_gone(app_authed):
    async with AsyncClient(transport=httpx.ASGITransport(app=app_authed), base_url="http://t") as c:
        # /api/... (non-v1) should 404 now that the alias is removed
        r = await c.post("/api/data/publish", json={})
        assert r.status_code == 404
