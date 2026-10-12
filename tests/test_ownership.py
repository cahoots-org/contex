import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.ownership import ensure_project_access
from src.core.identity import Identity
from src.core.rbac import Permission
from src.core.tenant import TenantManager


def _ident(tenant_id, projects=()):
    return Identity(key_id="k", scopes=frozenset({Permission.PUBLISH_DATA}),
                    tenant_id=tenant_id, projects=tuple(projects), role=None)


@pytest.mark.asyncio
async def test_read_unchecked_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mgr = AsyncMock()
    await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_publish_binds_to_default_tenant_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mgr = AsyncMock()
    await ensure_project_access(_ident(None), "p1", mgr, create_if_absent=True)
    mgr.claim_project.assert_awaited_once_with("default", "p1")


@pytest.mark.asyncio
async def test_read_denies_unowned_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_denies_other_tenant_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t2"
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_allows_owned_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t1"
    await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_publish_binds_new_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.claim_project.return_value = "t1"
    await ensure_project_access(_ident("t1", projects=["pnew"]), "pnew", mgr, create_if_absent=True)
    mgr.claim_project.assert_awaited_once_with("t1", "pnew")


@pytest.mark.asyncio
async def test_concurrent_first_publishes_leave_one_owner(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = TenantManager(db)
    await mgr.create_tenant(tenant_id="acme", name="acme")
    results = await asyncio.gather(
        *(ensure_project_access(_ident(t), "racy", mgr, create_if_absent=True)
          for t in ("default", "default", "acme")),
        return_exceptions=True,
    )
    owner = await mgr.get_project_tenant("racy")
    denied = [r for r in results if isinstance(r, PermissionError)]
    assert len(denied) == (2 if owner == "acme" else 1)
    assert all(r is None or isinstance(r, PermissionError) for r in results)
