"""Tenants and the projects they own."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database import DatabaseManager
from src.core.db_models import Tenant as TenantModel
from src.core.db_models import TenantProject as TenantProjectModel

DEFAULT_TENANT_ID = "default"


class TenantManager:
    """Creates tenants and records which tenant owns each project."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def ensure_tenant(self, tenant_id: str) -> None:
        """Create the tenant, named after its id, unless it already exists."""
        async with self.db.session() as session:
            await session.execute(
                pg_insert(TenantModel).values(tenant_id=tenant_id, name=tenant_id).on_conflict_do_nothing()
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
