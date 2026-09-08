"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from src.core.identity import Identity
from src.core.tenant_middleware import MULTI_TENANT_ENABLED


async def check_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> bool:
    """Return True when `project_id` is accessible to the caller's tenant.

    Returns True (allowed) when multi-tenancy is off or the caller carries no
    tenant. When `create_if_absent` is true (publish paths), an unowned project
    is bound to the caller's tenant and True is returned. Returns False on any
    ownership mismatch; callers must raise.
    """
    if not MULTI_TENANT_ENABLED or identity.tenant_id is None:
        return True
    if not identity.has_project(project_id):
        return False
    owner = await tenant_mgr.get_project_tenant(project_id)
    if owner is None:
        if create_if_absent:
            await tenant_mgr.add_project(identity.tenant_id, project_id)
            return True
        return False
    return owner == identity.tenant_id
