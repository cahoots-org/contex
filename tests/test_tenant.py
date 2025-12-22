"""Tests for Multi-Tenant Management with PostgreSQL"""

import pytest
import pytest_asyncio

from src.core.tenant import (
    TenantManager,
    Tenant,
    TenantPlan,
    TenantQuotas,
    TenantUsage,
    ensure_default_tenant,
    DEFAULT_TENANT_ID,
    get_tenant_project_key,
    parse_tenant_project_key,
)


class TestTenantModels:
    """Test tenant model validation"""

    def test_tenant_plan_enum(self):
        """Test TenantPlan enum values"""
        assert TenantPlan.FREE == "free"
        assert TenantPlan.STARTER == "starter"
        assert TenantPlan.PRO == "pro"
        assert TenantPlan.ENTERPRISE == "enterprise"

    def test_tenant_quotas_model(self):
        """Test TenantQuotas model"""
        quota = TenantQuotas(
            max_projects=10,
            max_agents_per_project=100,
            max_api_keys=50,
            max_events_per_month=100000,
            max_storage_mb=1024,
            max_requests_per_minute=1000,
        )
        assert quota.max_projects == 10
        assert quota.max_storage_mb == 1024

    def test_tenant_quotas_for_plan(self):
        """Test getting quotas for each plan"""
        free = TenantQuotas.for_plan(TenantPlan.FREE)
        pro = TenantQuotas.for_plan(TenantPlan.PRO)
        enterprise = TenantQuotas.for_plan(TenantPlan.ENTERPRISE)

        # Pro should have higher quotas than free
        assert pro.max_projects > free.max_projects
        assert pro.max_api_keys > free.max_api_keys

        # Enterprise should have highest
        assert enterprise.max_projects > pro.max_projects

    def test_tenant_usage_model(self):
        """Test TenantUsage model"""
        usage = TenantUsage(
            projects_count=5,
            agents_count=20,
            api_keys_count=3,
            events_this_month=1000,
            storage_used_mb=50.5,
        )
        assert usage.projects_count == 5
        assert usage.storage_used_mb == 50.5

    def test_tenant_model(self):
        """Test Tenant model"""
        tenant = Tenant(
            tenant_id="tenant_123",
            name="Test Organization",
            plan=TenantPlan.PRO,
        )
        assert tenant.tenant_id == "tenant_123"
        assert tenant.plan == TenantPlan.PRO
        assert tenant.is_active is True

    def test_tenant_with_custom_quota(self):
        """Test Tenant with custom quota"""
        quota = TenantQuotas(
            max_projects=50,
            max_api_keys=100,
        )
        tenant = Tenant(
            tenant_id="tenant_456",
            name="Enterprise Org",
            plan=TenantPlan.ENTERPRISE,
            quotas=quota,
        )
        assert tenant.quotas.max_projects == 50


class TestTenantManager:
    """Test TenantManager functionality"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_create_tenant(self, manager):
        """Test creating a tenant"""
        tenant = await manager.create_tenant(
            tenant_id="new_org",
            name="New Organization",
            plan=TenantPlan.STARTER,
        )

        assert tenant.tenant_id == "new_org"
        assert tenant.name == "New Organization"
        assert tenant.plan == TenantPlan.STARTER
        assert tenant.is_active is True

    @pytest.mark.asyncio
    async def test_create_tenant_duplicate_fails(self, manager):
        """Test creating duplicate tenant fails"""
        await manager.create_tenant(
            tenant_id="dup_org",
            name="First Org",
            plan=TenantPlan.FREE,
        )

        with pytest.raises(ValueError, match="already exists"):
            await manager.create_tenant(
                tenant_id="dup_org",
                name="Second Org",
                plan=TenantPlan.FREE,
            )

    @pytest.mark.asyncio
    async def test_get_tenant(self, manager):
        """Test retrieving a tenant"""
        created = await manager.create_tenant(
            tenant_id="get_org",
            name="Test Org",
            plan=TenantPlan.FREE,
        )

        retrieved = await manager.get_tenant(created.tenant_id)

        assert retrieved is not None
        assert retrieved.tenant_id == created.tenant_id
        assert retrieved.name == "Test Org"

    @pytest.mark.asyncio
    async def test_get_nonexistent_tenant(self, manager):
        """Test getting tenant that doesn't exist"""
        result = await manager.get_tenant("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_tenant(self, manager):
        """Test updating a tenant"""
        tenant = await manager.create_tenant(
            tenant_id="update_org",
            name="Original Name",
            plan=TenantPlan.FREE,
        )

        updated = await manager.update_tenant(
            tenant.tenant_id,
            name="Updated Name",
            plan=TenantPlan.PRO,
        )

        assert updated.name == "Updated Name"
        assert updated.plan == TenantPlan.PRO
        assert updated.updated_at is not None

    @pytest.mark.asyncio
    async def test_update_nonexistent_tenant(self, manager):
        """Test updating tenant that doesn't exist"""
        result = await manager.update_tenant("nonexistent", name="New Name")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_tenant(self, manager):
        """Test deleting a tenant"""
        tenant = await manager.create_tenant(
            tenant_id="delete_org",
            name="To Delete",
            plan=TenantPlan.FREE,
        )

        result = await manager.delete_tenant(tenant.tenant_id, force=True)
        assert result is True

        # Should be gone
        retrieved = await manager.get_tenant(tenant.tenant_id)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_tenants(self, manager):
        """Test listing tenants"""
        # Create several tenants
        for i in range(3):
            await manager.create_tenant(
                tenant_id=f"list_org_{i}",
                name=f"Org {i}",
                plan=TenantPlan.STARTER,
            )

        tenants = await manager.list_tenants()

        assert len(tenants) == 3

    @pytest.mark.asyncio
    async def test_list_tenants_by_plan(self, manager):
        """Test listing tenants filtered by plan"""
        await manager.create_tenant(
            tenant_id="free_org",
            name="Free Org",
            plan=TenantPlan.FREE
        )
        await manager.create_tenant(
            tenant_id="pro_org_1",
            name="Pro Org 1",
            plan=TenantPlan.PRO
        )
        await manager.create_tenant(
            tenant_id="pro_org_2",
            name="Pro Org 2",
            plan=TenantPlan.PRO
        )

        free_tenants = await manager.list_tenants(plan=TenantPlan.FREE)
        pro_tenants = await manager.list_tenants(plan=TenantPlan.PRO)

        assert len(free_tenants) == 1
        assert len(pro_tenants) == 2

    @pytest.mark.asyncio
    async def test_list_tenants_by_active_status(self, manager):
        """Test listing tenants filtered by active status"""
        await manager.create_tenant(
            tenant_id="active_org",
            name="Active Org",
            plan=TenantPlan.PRO,
        )

        inactive_tenant = await manager.create_tenant(
            tenant_id="inactive_org",
            name="Inactive Org",
            plan=TenantPlan.FREE,
        )
        await manager.update_tenant(inactive_tenant.tenant_id, is_active=False)

        active_tenants = await manager.list_tenants(is_active=True)
        inactive_tenants = await manager.list_tenants(is_active=False)

        assert len(active_tenants) == 1
        assert len(inactive_tenants) == 1


class TestTenantQuotasMgmt:
    """Test tenant quota management"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_default_quotas_by_plan(self, manager):
        """Test default quotas are set by plan"""
        free = await manager.create_tenant(
            tenant_id="free_quota",
            name="Free",
            plan=TenantPlan.FREE
        )
        pro = await manager.create_tenant(
            tenant_id="pro_quota",
            name="Pro",
            plan=TenantPlan.PRO
        )

        # Pro should have higher quotas than free
        assert pro.quotas.max_projects > free.quotas.max_projects
        assert pro.quotas.max_api_keys > free.quotas.max_api_keys

    @pytest.mark.asyncio
    async def test_check_quota_allowed(self, manager):
        """Test checking if operation is within quota"""
        tenant = await manager.create_tenant(
            tenant_id="quota_check",
            name="Quota Test",
            plan=TenantPlan.STARTER,
        )

        # Should be within quota initially
        allowed, message = await manager.check_quota(
            tenant.tenant_id,
            "projects",
            amount=1,
        )
        assert allowed is True
        assert message == "OK"

    @pytest.mark.asyncio
    async def test_check_quota_exceeded(self, manager):
        """Test quota exceeded check"""
        # Create tenant with minimal quota
        tenant = await manager.create_tenant(
            tenant_id="limited_tenant",
            name="Limited Tenant",
            plan=TenantPlan.FREE,  # Has max 1 project
        )

        # Add a project to use quota
        await manager.add_project(tenant.tenant_id, "project1")

        # Try to add another
        allowed, message = await manager.check_quota(
            tenant.tenant_id,
            "projects",
            amount=1,
        )
        assert allowed is False
        assert "exceeded" in message.lower()

    @pytest.mark.asyncio
    async def test_enforce_quota_raises(self, manager):
        """Test enforce_quota raises when exceeded"""
        tenant = await manager.create_tenant(
            tenant_id="enforce_tenant",
            name="Enforce Tenant",
            plan=TenantPlan.FREE,
        )

        # Use up quota
        await manager.add_project(tenant.tenant_id, "project1")

        # Should raise
        with pytest.raises(ValueError, match="exceeded"):
            await manager.enforce_quota(tenant.tenant_id, "projects")


class TestTenantUsageTracking:
    """Test tenant usage tracking"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_get_usage(self, manager):
        """Test getting usage data"""
        tenant = await manager.create_tenant(
            tenant_id="usage_test",
            name="Usage Test",
            plan=TenantPlan.STARTER,
        )

        usage = await manager.get_usage(tenant.tenant_id)

        assert usage is not None
        assert usage.projects_count == 0
        assert usage.api_keys_count == 0

    @pytest.mark.asyncio
    async def test_increment_usage(self, manager):
        """Test incrementing usage counters"""
        tenant = await manager.create_tenant(
            tenant_id="incr_test",
            name="Increment Test",
            plan=TenantPlan.PRO,
        )

        new_value = await manager.increment_usage(
            tenant.tenant_id,
            "events_this_month",
            100,
        )

        assert new_value == 100

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.events_this_month == 100

    @pytest.mark.asyncio
    async def test_reset_monthly_usage(self, manager):
        """Test resetting monthly usage"""
        tenant = await manager.create_tenant(
            tenant_id="reset_test",
            name="Reset Test",
            plan=TenantPlan.STARTER,
        )

        # Add some usage
        await manager.increment_usage(tenant.tenant_id, "events_this_month", 1000)

        # Reset
        await manager.reset_monthly_usage(tenant.tenant_id)

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.events_this_month == 0


class TestTenantProjectManagement:
    """Test tenant project management"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_add_project(self, manager):
        """Test adding a project to tenant"""
        tenant = await manager.create_tenant(
            tenant_id="proj_tenant",
            name="Project Tenant",
            plan=TenantPlan.PRO,
        )

        key = await manager.add_project(tenant.tenant_id, "my_project")

        assert key == f"{tenant.tenant_id}:project:my_project"

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.projects_count == 1

    @pytest.mark.asyncio
    async def test_remove_project(self, manager):
        """Test removing a project from tenant"""
        tenant = await manager.create_tenant(
            tenant_id="rm_proj_tenant",
            name="Remove Project Tenant",
            plan=TenantPlan.PRO,
        )

        await manager.add_project(tenant.tenant_id, "remove_me")
        result = await manager.remove_project(tenant.tenant_id, "remove_me")

        assert result is True

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.projects_count == 0

    @pytest.mark.asyncio
    async def test_list_projects(self, manager):
        """Test listing tenant projects"""
        tenant = await manager.create_tenant(
            tenant_id="list_proj_tenant",
            name="List Projects Tenant",
            plan=TenantPlan.PRO,
        )

        await manager.add_project(tenant.tenant_id, "project1")
        await manager.add_project(tenant.tenant_id, "project2")
        await manager.add_project(tenant.tenant_id, "project3")

        projects = await manager.list_projects(tenant.tenant_id)

        assert len(projects) == 3
        assert "project1" in projects
        assert "project2" in projects


class TestTenantAPIKeyManagement:
    """Test tenant API key management"""

    @pytest_asyncio.fixture
    async def manager(self, db):
        """Create a tenant manager"""
        return TenantManager(db)

    @pytest.mark.asyncio
    async def test_add_api_key(self, manager):
        """Test adding API key to tenant"""
        tenant = await manager.create_tenant(
            tenant_id="key_tenant",
            name="API Key Tenant",
            plan=TenantPlan.PRO,
        )

        await manager.add_api_key(tenant.tenant_id, "key_123")

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.api_keys_count == 1

    @pytest.mark.asyncio
    async def test_remove_api_key(self, manager):
        """Test removing API key from tenant"""
        tenant = await manager.create_tenant(
            tenant_id="rm_key_tenant",
            name="Remove Key Tenant",
            plan=TenantPlan.PRO,
        )

        await manager.add_api_key(tenant.tenant_id, "key_to_remove")
        result = await manager.remove_api_key(tenant.tenant_id, "key_to_remove")

        assert result is True

        usage = await manager.get_usage(tenant.tenant_id)
        assert usage.api_keys_count == 0

    @pytest.mark.asyncio
    async def test_get_api_key_tenant(self, manager, db):
        """Test getting tenant for API key"""
        from src.core.auth import create_api_key

        tenant = await manager.create_tenant(
            tenant_id="find_key_tenant",
            name="Find Key Tenant",
            plan=TenantPlan.STARTER,
        )

        # First create an API key (returns tuple of raw_key, APIKey model)
        raw_key, api_key = await create_api_key(db, "Test Key", scopes=["read"])
        key_id = api_key.key_id

        # Then associate it with the tenant
        await manager.add_api_key(tenant.tenant_id, key_id)

        found = await manager.get_api_key_tenant(key_id)

        assert found == tenant.tenant_id


class TestTenantUtilityFunctions:
    """Test tenant utility functions"""

    def test_get_tenant_project_key(self):
        """Test getting tenant project key"""
        key = get_tenant_project_key("tenant_abc", "project_xyz")
        assert key == "tenant_abc:project:project_xyz"

    def test_parse_tenant_project_key(self):
        """Test parsing tenant project key"""
        tenant_id, project_id = parse_tenant_project_key("tenant_abc:project:project_xyz")
        assert tenant_id == "tenant_abc"
        assert project_id == "project_xyz"

    def test_parse_tenant_project_key_invalid(self):
        """Test parsing invalid key"""
        tenant_id, project_id = parse_tenant_project_key("invalid_key")
        assert tenant_id is None
        assert project_id is None


class TestDefaultTenant:
    """Test default tenant for backward compatibility"""

    @pytest.mark.asyncio
    async def test_ensure_default_tenant_creates(self, db):
        """Test default tenant is created if not exists"""
        tenant = await ensure_default_tenant(db)

        assert tenant is not None
        assert tenant.tenant_id == DEFAULT_TENANT_ID
        assert tenant.plan == TenantPlan.ENTERPRISE

    @pytest.mark.asyncio
    async def test_ensure_default_tenant_returns_existing(self, db):
        """Test default tenant returns existing if already created"""
        tenant1 = await ensure_default_tenant(db)
        tenant2 = await ensure_default_tenant(db)

        assert tenant1.tenant_id == tenant2.tenant_id
