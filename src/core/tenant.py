"""Multi-tenant management for Contex

Provides tenant isolation, quotas, and management capabilities for
supporting multiple organizations/workspaces on a single Contex instance.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from src.core.database import DatabaseManager
from src.core.db_models import Tenant as TenantModel
from src.core.db_models import TenantProject as TenantProjectModel
from src.core.db_models import TenantUsage as TenantUsageModel
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

    All tenant data is stored in PostgreSQL with the following tables:
    - tenants - Tenant configuration
    - tenant_usage - Usage counters
    - tenant_projects - Project associations

    Project isolation is enforced through tenant_id foreign keys.
    """

    def __init__(self, db: DatabaseManager):
        self.db = db

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
        quotas = TenantQuotas.for_plan(plan)
        now = datetime.now(UTC)

        async with self.db.session() as session:
            # Check if tenant exists
            result = await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"Tenant '{tenant_id}' already exists")

            # Create tenant record
            tenant_record = TenantModel(
                tenant_id=tenant_id,
                name=name,
                plan=plan.value,
                quotas=quotas.model_dump(),
                settings=settings or {},
                metadata=metadata or {},
                owner_email=owner_email,
                created_at=now,
            )
            session.add(tenant_record)

            # Create usage record
            usage_record = TenantUsageModel(
                tenant_id=tenant_id,
                projects_count=0,
                agents_count=0,
                api_keys_count=0,
                events_this_month=0,
                storage_used_mb=0.0,
                last_updated=now,
            )
            session.add(usage_record)

        # Build response model
        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            plan=plan,
            quotas=quotas,
            owner_email=owner_email,
            settings=settings or {},
            metadata=metadata or {},
            created_at=now.isoformat(),
        )

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
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return self._record_to_model(record)

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
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            if name is not None:
                record.name = name
            if plan is not None:
                record.plan = plan.value
                if quotas is None:
                    record.quotas = TenantQuotas.for_plan(plan).model_dump()
            if quotas is not None:
                record.quotas = quotas.model_dump()
            if settings is not None:
                current_settings = record.settings or {}
                current_settings.update(settings)
                record.settings = current_settings
            if is_active is not None:
                record.is_active = is_active

            record.updated_at = datetime.now(UTC)

            logger.info("Tenant updated", tenant_id=tenant_id)

            return self._record_to_model(record)

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
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return False

            # Check for data if not forcing
            if not force:
                usage = await self.get_usage(tenant_id)
                if usage and (usage.projects_count > 0 or usage.api_keys_count > 0):
                    raise ValueError(
                        f"Tenant has {usage.projects_count} projects and "
                        f"{usage.api_keys_count} API keys. Use force=True to delete."
                    )

            # Delete tenant projects
            await session.execute(
                delete(TenantProjectModel).where(TenantProjectModel.tenant_id == tenant_id)
            )

            # Delete usage record
            await session.execute(
                delete(TenantUsageModel).where(TenantUsageModel.tenant_id == tenant_id)
            )

            # Delete tenant record (cascades will handle related records)
            await session.execute(
                delete(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )

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
        async with self.db.session() as session:
            query = select(TenantModel)

            if plan is not None:
                query = query.where(TenantModel.plan == plan.value)
            if is_active is not None:
                query = query.where(TenantModel.is_active == is_active)

            query = query.order_by(TenantModel.created_at.desc())
            query = query.offset(offset).limit(limit)

            result = await session.execute(query)
            records = result.scalars().all()

            return [self._record_to_model(r) for r in records]

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
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantUsageModel).where(TenantUsageModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return TenantUsage(
                projects_count=record.projects_count or 0,
                agents_count=record.agents_count or 0,
                api_keys_count=record.api_keys_count or 0,
                events_this_month=record.events_this_month or 0,
                storage_used_mb=record.storage_used_mb or 0.0,
                last_updated=record.last_updated.isoformat() if record.last_updated else None,
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
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantUsageModel).where(TenantUsageModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return 0

            current_value = getattr(record, field, 0) or 0
            new_value = current_value + amount
            setattr(record, field, new_value)
            record.last_updated = datetime.now(UTC)

            return new_value

    async def reset_monthly_usage(self, tenant_id: str) -> None:
        """
        Reset monthly usage counters (call on billing cycle).

        Args:
            tenant_id: Tenant identifier
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantUsageModel).where(TenantUsageModel.tenant_id == tenant_id)
            )
            record = result.scalar_one_or_none()

            if record:
                record.events_this_month = 0
                record.last_updated = datetime.now(UTC)

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

        async with self.db.session() as session:
            # Add to tenant projects
            project_record = TenantProjectModel(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            session.add(project_record)

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
        async with self.db.session() as session:
            result = await session.execute(
                delete(TenantProjectModel)
                .where(TenantProjectModel.tenant_id == tenant_id)
                .where(TenantProjectModel.project_id == project_id)
            )

            if result.rowcount > 0:
                await self.increment_usage(tenant_id, "projects_count", -1)
                return True

        return False

    async def list_projects(self, tenant_id: str) -> List[str]:
        """
        List all projects for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            List of project IDs
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantProjectModel.project_id)
                .where(TenantProjectModel.tenant_id == tenant_id)
            )
            return [row[0] for row in result]

    async def get_project_tenant(self, project_id: str) -> Optional[str]:
        """
        Get the tenant ID for a project.

        Args:
            project_id: Project identifier

        Returns:
            Tenant ID if found, None otherwise
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(TenantProjectModel.tenant_id)
                .where(TenantProjectModel.project_id == project_id)
            )
            row = result.first()
            return row[0] if row else None

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
        from src.core.db_models import APIKey as APIKeyModel

        await self.enforce_quota(tenant_id, "api_keys")

        # Update the API key's tenant_id
        async with self.db.session() as session:
            result = await session.execute(
                select(APIKeyModel).where(APIKeyModel.key_id == key_id)
            )
            api_key = result.scalar_one_or_none()
            if api_key:
                api_key.tenant_id = tenant_id

        await self.increment_usage(tenant_id, "api_keys_count")

    async def remove_api_key(self, tenant_id: str, key_id: str) -> bool:
        """
        Remove API key association from a tenant.

        Note: This just updates the usage counter.
        The actual API key record should be updated separately.

        Args:
            tenant_id: Tenant identifier
            key_id: API key ID

        Returns:
            True if removed
        """
        await self.increment_usage(tenant_id, "api_keys_count", -1)
        return True

    async def get_api_key_tenant(self, key_id: str) -> Optional[str]:
        """
        Get the tenant ID for an API key.

        Args:
            key_id: API key ID

        Returns:
            Tenant ID if found
        """
        from src.core.db_models import APIKey as APIKeyModel

        async with self.db.session() as session:
            result = await session.execute(
                select(APIKeyModel.tenant_id).where(APIKeyModel.key_id == key_id)
            )
            row = result.first()
            return row[0] if row else None

    # ============================================================
    # Helper Methods
    # ============================================================

    def _record_to_model(self, record: TenantModel) -> Tenant:
        """Convert database record to Pydantic model"""
        return Tenant(
            tenant_id=record.tenant_id,
            name=record.name,
            plan=TenantPlan(record.plan),
            quotas=TenantQuotas(**(record.quotas or {})),
            settings=record.settings or {},
            metadata=record.metadata_ or {},  # Use metadata_ to avoid SQLAlchemy conflict
            owner_email=record.owner_email,
            billing_email=record.billing_email,
            is_active=record.is_active,
            created_at=record.created_at.isoformat() if record.created_at else "",
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
        )


# ============================================================
# Utility Functions
# ============================================================

def get_tenant_project_key(tenant_id: str, project_id: str) -> str:
    """
    Get the full key prefix for tenant-scoped project data.

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


async def ensure_default_tenant(db: DatabaseManager) -> Tenant:
    """
    Ensure the default tenant exists for backward compatibility.

    This is used when multi-tenancy is not explicitly configured,
    allowing existing single-tenant deployments to continue working.

    Args:
        db: Database manager

    Returns:
        Default Tenant object
    """
    manager = TenantManager(db)

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
