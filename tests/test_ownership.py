import pytest
from unittest.mock import AsyncMock

from src.core import ownership
from src.core.ownership import ensure_project_access
from src.core.identity import Identity
from src.core.rbac import Permission


def _ident(tenant_id, projects=()):
    return Identity(key_id="k", scopes=frozenset({Permission.PUBLISH_DATA}),
                    tenant_id=tenant_id, projects=tuple(projects), role=None)


@pytest.mark.asyncio
async def test_noop_when_multitenant_off(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", False)
    mgr = AsyncMock()
    await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_identity_has_no_tenant(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock()
    await ensure_project_access(_ident(None), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_read_denies_unowned_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_denies_other_tenant_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t2"
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_allows_owned_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t1"
    await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_publish_binds_new_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    await ensure_project_access(_ident("t1"), "pnew", mgr, create_if_absent=True)
    mgr.add_project.assert_awaited_once_with("t1", "pnew")


@pytest.mark.asyncio
async def test_key_project_scope_denies_out_of_scope(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock()
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1", projects=["allowed"]), "other", mgr, create_if_absent=True)
    mgr.get_project_tenant.assert_not_called()  # scope check short-circuits
