"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from src.core.authz import auth_enabled
from src.core.tenant import DEFAULT_TENANT_ID


async def ensure_project_access(
    tenant_id: str | None, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Raise PermissionError if the caller's tenant may not access `project_id`.

    Publish paths (`create_if_absent`) bind an unowned project to the caller's
    tenant, or to the default tenant when auth is off, when nothing is checked.
    """
    if not auth_enabled():
        if create_if_absent:
            await tenant_mgr.claim_project(DEFAULT_TENANT_ID, project_id)
        return
    if create_if_absent:
        owner = await tenant_mgr.claim_project(tenant_id, project_id)
    else:
        owner = await tenant_mgr.get_project_tenant(project_id)
    if owner != tenant_id:
        raise PermissionError("Permission denied")
