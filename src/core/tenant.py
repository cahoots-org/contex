"""Tenants and the projects they own."""

from datetime import UTC, datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database import DatabaseManager
from src.core.db_models import Tenant as TenantModel
from src.core.db_models import TenantProject as TenantProjectModel
from src.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TENANT_ID = "default"


class Tenant(BaseModel):
    """Tenant (organization/workspace) model"""
    tenant_id: str = Field(..., description="Unique tenant identifier")
    name: str = Field(..., description="Display name")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Tenant-specific settings")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: Optional[str] = None
    is_active: bool = Field(default=True, description="Whether tenant is active")

    owner_email: Optional[str] = None
    billing_email: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TenantManager:
    """Creates tenants and records which tenant owns each project."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def create_tenant(
        self,
        tenant_id: str,
        name: str,
        owner_email: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tenant:
        """Create a tenant; raise ValueError if it already exists."""
        now = datetime.now(UTC)

        async with self.db.session() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"Tenant '{tenant_id}' already exists")

            session.add(TenantModel(
                tenant_id=tenant_id,
                name=name,
                settings=settings or {},
                metadata_=metadata or {},
                owner_email=owner_email,
                created_at=now,
            ))

        logger.info("Tenant created", tenant_id=tenant_id, name=name)

        return Tenant(
            tenant_id=tenant_id,
            name=name,
            owner_email=owner_email,
            settings=settings or {},
            metadata=metadata or {},
            created_at=now.isoformat(),
        )

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Return the tenant, or None if it doesn't exist."""
        async with self.db.session() as session:
            record = (await session.execute(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if not record:
                return None
            return Tenant(
                tenant_id=record.tenant_id,
                name=record.name,
                settings=record.settings or {},
                metadata=record.metadata_ or {},
                owner_email=record.owner_email,
                billing_email=record.billing_email,
                is_active=record.is_active,
                created_at=record.created_at.isoformat() if record.created_at else "",
                updated_at=record.updated_at.isoformat() if record.updated_at else None,
            )

    async def claim_project(self, tenant_id: str, project_id: str) -> str:
        """Bind `project_id` to `tenant_id` unless it already has an owner; return the owner."""
        async with self.db.session() as session:
            await session.execute(
                pg_insert(TenantProjectModel)
                .values(tenant_id=tenant_id, project_id=project_id)
                .on_conflict_do_nothing(index_elements=[TenantProjectModel.project_id])
            )
            return (await session.execute(
                select(TenantProjectModel.tenant_id).where(TenantProjectModel.project_id == project_id)
            )).scalar_one()

    async def get_project_tenant(self, project_id: str) -> Optional[str]:
        """Return the tenant that owns `project_id`, or None."""
        async with self.db.session() as session:
            return (await session.execute(
                select(TenantProjectModel.tenant_id).where(TenantProjectModel.project_id == project_id)
            )).scalar_one_or_none()
