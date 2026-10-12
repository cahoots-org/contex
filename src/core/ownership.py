"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from src.core.authz import auth_enabled
from src.core.identity import Identity
from src.core.tenant import DEFAULT_TENANT_ID


async def ensure_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Raise PermissionError if the caller's tenant may not access `project_id`.

    Publish paths (`create_if_absent`) bind an unowned project to the caller's
    tenant, or to the default tenant when auth is off, when nothing is checked.
    """
    if not auth_enabled():
        if create_if_absent:
            await tenant_mgr.claim_project(DEFAULT_TENANT_ID, project_id)
        return
    if not identity.has_project(project_id):
        raise PermissionError("Permission denied")
    if create_if_absent:
        owner = await tenant_mgr.claim_project(identity.tenant_id, project_id)
    else:
        owner = await tenant_mgr.get_project_tenant(project_id)
    if owner != identity.tenant_id:
        raise PermissionError("Permission denied")
