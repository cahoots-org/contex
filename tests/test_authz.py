# tests/test_authz.py
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from src.core import authz
from src.core.authz import require, public, get_identity
from src.core.identity import Identity
from src.core.rbac import Permission


def _build_app():
    app = FastAPI()

    @app.get("/open", dependencies=[Depends(public)])
    async def open_route():
        return {"ok": True}

    @app.get("/publish", dependencies=[Depends(require(Permission.PUBLISH_DATA))])
    async def publish_route():
        return {"ok": True}

    return app


def test_require_and_public_carry_markers():
    dep = require(Permission.PUBLISH_DATA)
    assert getattr(dep, "_authz_marker") == (Permission.PUBLISH_DATA,)
    assert getattr(public, "_public_marker") is True


@pytest.mark.asyncio
async def test_auth_off_allows_everything(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: False)
    async with AsyncClient(app=_build_app(), base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 200  # no key needed when off


@pytest.mark.asyncio
async def test_auth_on_missing_key_401(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    async with AsyncClient(app=_build_app(), base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 401
        assert (await c.get("/open")).status_code == 200  # public bypasses


@pytest.mark.asyncio
async def test_auth_on_insufficient_scope_403(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    readonly = Identity(key_id="k", scopes=frozenset({Permission.QUERY_DATA}),
                        tenant_id=None, projects=(), role=None)
    app = _build_app()
    app.dependency_overrides[get_identity] = lambda: readonly
    async with AsyncClient(app=app, base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 403
