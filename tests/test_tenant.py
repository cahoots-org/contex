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
    async def test_claim_project_keeps_the_first_owner(self, manager):
        await manager.ensure_tenant("acme")
        await manager.ensure_tenant("acme")

        assert await manager.claim_project("acme", "p") == "acme"
        assert await manager.claim_project(DEFAULT_TENANT_ID, "p") == "acme"
        assert await manager.get_project_tenant("p") == "acme"
