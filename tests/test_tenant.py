"""Tests for tenant management with PostgreSQL"""

import pytest
import pytest_asyncio

from src.core.tenant import DEFAULT_TENANT_ID, TenantManager


class TestTenantManager:
    """Test TenantManager functionality"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_create_tenant(self, manager):
        tenant = await manager.create_tenant(tenant_id="new_org", name="New Organization")

        assert tenant.tenant_id == "new_org"
        assert tenant.name == "New Organization"
        assert tenant.is_active is True

    @pytest.mark.asyncio
    async def test_create_tenant_duplicate_fails(self, manager):
        await manager.create_tenant(tenant_id="dup_org", name="First Org")

        with pytest.raises(ValueError, match="already exists"):
            await manager.create_tenant(tenant_id="dup_org", name="Second Org")

    @pytest.mark.asyncio
    async def test_get_tenant(self, manager):
        created = await manager.create_tenant(tenant_id="get_org", name="Test Org")

        retrieved = await manager.get_tenant(created.tenant_id)

        assert retrieved is not None
        assert retrieved.tenant_id == created.tenant_id
        assert retrieved.name == "Test Org"

    @pytest.mark.asyncio
    async def test_get_nonexistent_tenant(self, manager):
        assert await manager.get_tenant("nonexistent") is None

    @pytest.mark.asyncio
    async def test_claim_project_keeps_the_first_owner(self, manager):
        await manager.create_tenant(tenant_id="acme", name="acme")

        assert await manager.claim_project("acme", "p") == "acme"
        assert await manager.claim_project(DEFAULT_TENANT_ID, "p") == "acme"
        assert await manager.get_project_tenant("p") == "acme"
