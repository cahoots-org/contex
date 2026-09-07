"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from fastapi import HTTPException

from src.core.identity import Identity
from src.core.tenant_middleware import MULTI_TENANT_ENABLED


async def check_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Enforce that `project_id` belongs to the caller's tenant.

    No-op when multi-tenancy is off or the caller carries no tenant. When
    `create_if_absent` is true (publish paths), an unowned project is bound to
    the caller's tenant; otherwise an unowned or cross-tenant project is denied.
    """
    if not MULTI_TENANT_ENABLED or identity.tenant_id is None:
        return
    if not identity.has_project(project_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    owner = await tenant_mgr.get_project_tenant(project_id)
    if owner is None:
        if create_if_absent:
            await tenant_mgr.add_project(identity.tenant_id, project_id)
            return
        raise HTTPException(status_code=403, detail="Forbidden")
    if owner != identity.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
