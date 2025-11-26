"""Multi-tenant management for Contex

Provides tenant isolation, quotas, and management capabilities for
supporting multiple organizations/workspaces on a single Contex instance.
"""

import json
from datetime import datetime, UTC
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from src.core.logging import get_logger

logger = get_logger(__name__)


class TenantPlan(str, Enum):
    """Available tenant plans with different quota levels"""
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantQuotas(BaseModel):
    """Resource quotas for a tenant"""
    max_projects: int = Field(default=3, description="Maximum number of projects")
    max_agents_per_project: int = Field(default=10, description="Maximum agents per project")
    max_api_keys: int = Field(default=5, description="Maximum API keys")
    max_events_per_month: int = Field(default=10000, description="Maximum events per month")
    max_storage_mb: int = Field(default=100, description="Maximum storage in MB")
    max_requests_per_minute: int = Field(default=100, description="Rate limit per minute")
    webhook_enabled: bool = Field(default=True, description="Whether webhooks are enabled")

    @classmethod
    def for_plan(cls, plan: TenantPlan) -> "TenantQuotas":
        """Get default quotas for a plan"""
        quotas = {
            TenantPlan.FREE: cls(
                max_projects=1,
                max_agents_per_project=5,
                max_api_keys=2,
                max_events_per_month=1000,
                max_storage_mb=10,
                max_requests_per_minute=30,
                webhook_enabled=False,
            ),
            TenantPlan.STARTER: cls(
                max_projects=3,
                max_agents_per_project=10,
                max_api_keys=5,
                max_events_per_month=10000,
                max_storage_mb=100,
                max_requests_per_minute=100,
                webhook_enabled=True,
            ),
            TenantPlan.PRO: cls(
                max_projects=10,
                max_agents_per_project=50,
                max_api_keys=20,
                max_events_per_month=100000,
                max_storage_mb=1000,
                max_requests_per_minute=500,
                webhook_enabled=True,
            ),
            TenantPlan.ENTERPRISE: cls(
                max_projects=100,
                max_agents_per_project=500,
                max_api_keys=100,
                max_events_per_month=1000000,
                max_storage_mb=10000,
                max_requests_per_minute=2000,
                webhook_enabled=True,
            ),
        }
        return quotas.get(plan, cls())


class TenantUsage(BaseModel):
    """Current resource usage for a tenant"""
    projects_count: int = 0
    agents_count: int = 0
    api_keys_count: int = 0
    events_this_month: int = 0
    storage_used_mb: float = 0.0
    last_updated: Optional[str] = None


class Tenant(BaseModel):
    """Tenant (organization/workspace) model"""
    tenant_id: str = Field(..., description="Unique tenant identifier")
    name: str = Field(..., description="Display name")
    plan: TenantPlan = Field(default=TenantPlan.FREE, description="Subscription plan")
    quotas: TenantQuotas = Field(default_factory=TenantQuotas, description="Resource quotas")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Tenant-specific settings")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: Optional[str] = None
    is_active: bool = Field(default=True, description="Whether tenant is active")

    # Metadata
    owner_email: Optional[str] = None
    billing_email: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TenantManager:
    """
    Manages tenant lifecycle and operations.

    All tenant data is stored in Redis with the following key patterns:
    - tenant:{tenant_id} - Tenant configuration (hash)
    - tenant:{tenant_id}:usage - Usage counters (hash)
    - tenant:{tenant_id}:projects - Set of project IDs
    - tenant:{tenant_id}:api_keys - Set of API key IDs
    - tenant_index - Set of all tenant IDs

    Project isolation is enforced by prefixing all project data with tenant_id:
    - {tenant_id}:project:{project_id}:* - All project data
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    # ============================================================
    # Tenant CRUD Operations
    # ============================================================

    async def create_tenant(
        self,
        tenant_id: str,
        name: str,
        plan: TenantPlan = TenantPlan.FREE,
        owner_email: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tenant:
        """
        Create a new tenant.

        Args:
            tenant_id: Unique identifier (e.g., 'acme-corp', 'org_123')
            name: Display name
            plan: Subscription plan
            owner_email: Email of tenant owner
            settings: Optional tenant-specific settings
            metadata: Optional metadata

        Returns:
            Created Tenant object

        Raises:
            ValueError: If tenant already exists
        """
        # Check if tenant exists
        if await self.redis.exists(f"tenant:{tenant_id}"):
            raise ValueError(f"Tenant '{tenant_id}' already exists")

        # Create tenant with plan-specific quotas
        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            plan=plan,
            quotas=TenantQuotas.for_plan(plan),
            owner_email=owner_email,
            settings=settings or {},
            metadata=metadata or {},
        )

        # Store tenant in Redis
        tenant_data = tenant.model_dump()
        tenant_data['quotas'] = json.dumps(tenant_data['quotas'])
        tenant_data['settings'] = json.dumps(tenant_data['settings'])
        tenant_data['metadata'] = json.dumps(tenant_data['metadata'])

        # Convert booleans to strings and remove None values (Redis only accepts bytes, strings, int, float)
        tenant_data = {
            k: str(v) if isinstance(v, bool) else v
            for k, v in tenant_data.items()
            if v is not None
        }

        await self.redis.hset(f"tenant:{tenant_id}", mapping=tenant_data)

        # Add to tenant index
        await self.redis.sadd("tenant_index", tenant_id)

        # Initialize usage counters
        await self.redis.hset(f"tenant:{tenant_id}:usage", mapping={
            "projects_count": 0,
            "agents_count": 0,
            "api_keys_count": 0,
            "events_this_month": 0,
            "storage_used_mb": 0.0,
            "last_updated": datetime.now(UTC).isoformat(),
        })

        logger.info("Tenant created",
                   tenant_id=tenant_id,
                   name=name,
                   plan=plan.value)

        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """
        Get tenant by ID.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Tenant object if found, None otherwise
        """
        data = await self.redis.hgetall(f"tenant:{tenant_id}")
        if not data:
            return None

        # Decode and parse
        decoded = {k.decode(): v.decode() for k, v in data.items()}
        decoded['quotas'] = json.loads(decoded.get('quotas', '{}'))
        decoded['settings'] = json.loads(decoded.get('settings', '{}'))
        decoded['metadata'] = json.loads(decoded.get('metadata', '{}'))
        decoded['is_active'] = decoded.get('is_active', 'True').lower() == 'true'

        return Tenant(**decoded)

    async def update_tenant(
        self,
        tenant_id: str,
        name: Optional[str] = None,
        plan: Optional[TenantPlan] = None,
        quotas: Optional[TenantQuotas] = None,
        settings: Optional[Dict[str, Any]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Tenant]:
        """
        Update tenant properties.

        Args:
            tenant_id: Tenant identifier
            name: New display name
            plan: New plan (updates quotas automatically unless custom quotas provided)
            quotas: Custom quotas (overrides plan defaults)
            settings: Updated settings (merged with existing)
            is_active: Active status

        Returns:
            Updated Tenant object, None if not found
        """
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return None

        # Update fields
        if name is not None:
            tenant.name = name
        if plan is not None:
            tenant.plan = plan
            if quotas is None:
                tenant.quotas = TenantQuotas.for_plan(plan)
        if quotas is not None:
            tenant.quotas = quotas
        if settings is not None:
            tenant.settings.update(settings)
        if is_active is not None:
            tenant.is_active = is_active

        tenant.updated_at = datetime.now(UTC).isoformat()

        # Store updates
        tenant_data = tenant.model_dump()
        tenant_data['quotas'] = json.dumps(tenant_data['quotas'])
        tenant_data['settings'] = json.dumps(tenant_data['settings'])
        tenant_data['metadata'] = json.dumps(tenant_data['metadata'])

        # Convert booleans to strings and remove None values (Redis only accepts bytes, strings, int, float)
        tenant_data = {
            k: str(v) if isinstance(v, bool) else v
            for k, v in tenant_data.items()
            if v is not None
        }

        await self.redis.hset(f"tenant:{tenant_id}", mapping=tenant_data)

        logger.info("Tenant updated", tenant_id=tenant_id)

        return tenant

    async def delete_tenant(self, tenant_id: str, force: bool = False) -> bool:
        """
        Delete a tenant and all associated data.

        Args:
            tenant_id: Tenant identifier
            force: If True, delete even if tenant has data

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If tenant has data and force=False
        """
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False

        # Check for data if not forcing
        if not force:
            usage = await self.get_usage(tenant_id)
            if usage and (usage.projects_count > 0 or usage.api_keys_count > 0):
                raise ValueError(
                    f"Tenant has {usage.projects_count} projects and "
                    f"{usage.api_keys_count} API keys. Use force=True to delete."
                )

        # Delete all tenant data
        # This would need to cascade delete projects, agents, etc.
        # For now, just delete the tenant record

        # Remove from index
        await self.redis.srem("tenant_index", tenant_id)

        # Delete tenant records
        await self.redis.delete(f"tenant:{tenant_id}")
        await self.redis.delete(f"tenant:{tenant_id}:usage")
        await self.redis.delete(f"tenant:{tenant_id}:projects")
        await self.redis.delete(f"tenant:{tenant_id}:api_keys")

        logger.warning("Tenant deleted", tenant_id=tenant_id, force=force)

        return True

    async def list_tenants(
        self,
        plan: Optional[TenantPlan] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Tenant]:
        """
        List tenants with optional filtering.

        Args:
            plan: Filter by plan
            is_active: Filter by active status
            limit: Maximum results
            offset: Skip first N results

        Returns:
            List of Tenant objects
        """
        # Get all tenant IDs
        tenant_ids = await self.redis.smembers("tenant_index")

        tenants = []
        for tid in tenant_ids:
            if isinstance(tid, bytes):
                tid = tid.decode()
            tenant = await self.get_tenant(tid)
            if not tenant:
                continue

            # Apply filters
            if plan is not None and tenant.plan != plan:
                continue
            if is_active is not None and tenant.is_active != is_active:
                continue

            tenants.append(tenant)

        # Sort by creation date
        tenants.sort(key=lambda t: t.created_at, reverse=True)

        # Apply pagination
        return tenants[offset:offset + limit]

    # ============================================================
    # Usage Tracking
    # ============================================================

    async def get_usage(self, tenant_id: str) -> Optional[TenantUsage]:
        """
        Get current usage for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            TenantUsage object if tenant exists
        """
        data = await self.redis.hgetall(f"tenant:{tenant_id}:usage")
        if not data:
            return None

        decoded = {k.decode(): v.decode() for k, v in data.items()}

        return TenantUsage(
            projects_count=int(decoded.get('projects_count', 0)),
            agents_count=int(decoded.get('agents_count', 0)),
            api_keys_count=int(decoded.get('api_keys_count', 0)),
            events_this_month=int(decoded.get('events_this_month', 0)),
            storage_used_mb=float(decoded.get('storage_used_mb', 0.0)),
            last_updated=decoded.get('last_updated'),
        )

    async def increment_usage(
        self,
        tenant_id: str,
        field: str,
        amount: int = 1,
    ) -> int:
        """
        Increment a usage counter.

        Args:
            tenant_id: Tenant identifier
            field: Usage field to increment
            amount: Amount to increment by

        Returns:
            New value after increment
        """
        new_value = await self.redis.hincrby(
            f"tenant:{tenant_id}:usage",
            field,
            amount
        )

        # Update timestamp
        await self.redis.hset(
            f"tenant:{tenant_id}:usage",
            "last_updated",
            datetime.now(UTC).isoformat()
        )

        return new_value

    async def reset_monthly_usage(self, tenant_id: str) -> None:
        """
        Reset monthly usage counters (call on billing cycle).

        Args:
            tenant_id: Tenant identifier
        """
        await self.redis.hset(
            f"tenant:{tenant_id}:usage",
            mapping={
                "events_this_month": 0,
                "last_updated": datetime.now(UTC).isoformat(),
            }
        )

        logger.info("Monthly usage reset", tenant_id=tenant_id)

    # ============================================================
    # Quota Enforcement
    # ============================================================

    async def check_quota(
        self,
        tenant_id: str,
        resource: str,
        amount: int = 1,
    ) -> tuple[bool, str]:
        """
        Check if a tenant has quota available for a resource.

        Args:
            tenant_id: Tenant identifier
            resource: Resource type (projects, agents, api_keys, events, storage)
            amount: Amount to check

        Returns:
            Tuple of (allowed: bool, message: str)
        """
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False, f"Tenant '{tenant_id}' not found"

        if not tenant.is_active:
            return False, f"Tenant '{tenant_id}' is inactive"

        usage = await self.get_usage(tenant_id)
        if not usage:
            return False, f"Usage data not found for tenant '{tenant_id}'"

        quotas = tenant.quotas

        # Check specific resource
        checks = {
            "projects": (usage.projects_count + amount, quotas.max_projects),
            "agents": (usage.agents_count + amount, quotas.max_agents_per_project * quotas.max_projects),
            "api_keys": (usage.api_keys_count + amount, quotas.max_api_keys),
            "events": (usage.events_this_month + amount, quotas.max_events_per_month),
            "storage": (usage.storage_used_mb + amount, quotas.max_storage_mb),
        }

        if resource not in checks:
            return True, "Unknown resource"

        current, limit = checks[resource]

        if current > limit:
            return False, f"Quota exceeded: {resource} ({current}/{limit})"

        return True, "OK"

    async def enforce_quota(
        self,
        tenant_id: str,
        resource: str,
        amount: int = 1,
    ) -> None:
        """
        Enforce quota for a resource, raising if exceeded.

        Args:
            tenant_id: Tenant identifier
            resource: Resource type
            amount: Amount to check

        Raises:
            ValueError: If quota exceeded
        """
        allowed, message = await self.check_quota(tenant_id, resource, amount)
        if not allowed:
            raise ValueError(message)

    # ============================================================
    # Project Management
    # ============================================================

    async def add_project(self, tenant_id: str, project_id: str) -> str:
        """
        Add a project to a tenant.

        Args:
            tenant_id: Tenant identifier
            project_id: Project identifier

        Returns:
            Full project key (tenant_id:project:project_id)

        Raises:
            ValueError: If quota exceeded
        """
        await self.enforce_quota(tenant_id, "projects")

        # Add to tenant's project set
        await self.redis.sadd(f"tenant:{tenant_id}:projects", project_id)

        # Increment usage
        await self.increment_usage(tenant_id, "projects_count")

        # Return the full project key for namespacing
        return f"{tenant_id}:project:{project_id}"

    async def remove_project(self, tenant_id: str, project_id: str) -> bool:
        """
        Remove a project from a tenant.

        Args:
            tenant_id: Tenant identifier
            project_id: Project identifier

        Returns:
            True if removed, False if not found
        """
        removed = await self.redis.srem(f"tenant:{tenant_id}:projects", project_id)
        if removed:
            await self.increment_usage(tenant_id, "projects_count", -1)
        return bool(removed)

    async def list_projects(self, tenant_id: str) -> List[str]:
        """
        List all projects for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            List of project IDs
        """
        projects = await self.redis.smembers(f"tenant:{tenant_id}:projects")
        return [p.decode() if isinstance(p, bytes) else p for p in projects]

    async def get_project_tenant(self, project_id: str) -> Optional[str]:
        """
        Get the tenant ID for a project.

        This searches through all tenants to find which one owns the project.
        For production, consider maintaining a reverse index.

        Args:
            project_id: Project identifier

        Returns:
            Tenant ID if found, None otherwise
        """
        tenant_ids = await self.redis.smembers("tenant_index")
        for tid in tenant_ids:
            if isinstance(tid, bytes):
                tid = tid.decode()
            if await self.redis.sismember(f"tenant:{tid}:projects", project_id):
                return tid
        return None

    # ============================================================
    # API Key Association
    # ============================================================

    async def add_api_key(self, tenant_id: str, key_id: str) -> None:
        """
        Associate an API key with a tenant.

        Args:
            tenant_id: Tenant identifier
            key_id: API key ID

        Raises:
            ValueError: If quota exceeded
        """
        await self.enforce_quota(tenant_id, "api_keys")

        await self.redis.sadd(f"tenant:{tenant_id}:api_keys", key_id)
        await self.increment_usage(tenant_id, "api_keys_count")

        # Store reverse mapping
        await self.redis.set(f"api_key_tenant:{key_id}", tenant_id)

    async def remove_api_key(self, tenant_id: str, key_id: str) -> bool:
        """
        Remove API key association from a tenant.

        Args:
            tenant_id: Tenant identifier
            key_id: API key ID

        Returns:
            True if removed
        """
        removed = await self.redis.srem(f"tenant:{tenant_id}:api_keys", key_id)
        if removed:
            await self.increment_usage(tenant_id, "api_keys_count", -1)
            await self.redis.delete(f"api_key_tenant:{key_id}")
        return bool(removed)

    async def get_api_key_tenant(self, key_id: str) -> Optional[str]:
        """
        Get the tenant ID for an API key.

        Args:
            key_id: API key ID

        Returns:
            Tenant ID if found
        """
        tenant_id = await self.redis.get(f"api_key_tenant:{key_id}")
        if tenant_id:
            return tenant_id.decode() if isinstance(tenant_id, bytes) else tenant_id
        return None


# ============================================================
# Utility Functions
# ============================================================

def get_tenant_project_key(tenant_id: str, project_id: str) -> str:
    """
    Get the full Redis key prefix for tenant-scoped project data.

    Args:
        tenant_id: Tenant identifier
        project_id: Project identifier

    Returns:
        Key prefix like 'tenant_id:project:project_id'
    """
    return f"{tenant_id}:project:{project_id}"


def parse_tenant_project_key(key: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse a tenant-scoped project key.

    Args:
        key: Full key like 'tenant_id:project:project_id'

    Returns:
        Tuple of (tenant_id, project_id), or (None, None) if invalid
    """
    parts = key.split(":", 2)
    if len(parts) >= 3 and parts[1] == "project":
        return parts[0], parts[2]
    return None, None


# ============================================================
# Default Tenant (for backward compatibility)
# ============================================================

DEFAULT_TENANT_ID = "default"


async def ensure_default_tenant(redis: Redis) -> Tenant:
    """
    Ensure the default tenant exists for backward compatibility.

    This is used when multi-tenancy is not explicitly configured,
    allowing existing single-tenant deployments to continue working.

    Args:
        redis: Redis connection

    Returns:
        Default Tenant object
    """
    manager = TenantManager(redis)

    tenant = await manager.get_tenant(DEFAULT_TENANT_ID)
    if tenant:
        return tenant

    # Create default tenant with enterprise quotas
    return await manager.create_tenant(
        tenant_id=DEFAULT_TENANT_ID,
        name="Default Tenant",
        plan=TenantPlan.ENTERPRISE,
        settings={"is_default": True},
    )
