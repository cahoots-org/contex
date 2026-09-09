"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from src.core.authz import auth_enabled
from src.core.identity import Identity


async def ensure_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Raise PermissionError if the caller's tenant may not access `project_id`.

    No-op when auth is off (demo mode). When `create_if_absent` is True (publish
    paths), an unowned project is bound to the caller's tenant instead of raising.
    """
    if not auth_enabled():
        return
    if not identity.has_project(project_id):
        raise PermissionError("Permission denied")
    owner = await tenant_mgr.get_project_tenant(project_id)
    if owner is None:
        if create_if_absent:
            await tenant_mgr.add_project(identity.tenant_id, project_id)
            return
        raise PermissionError("Permission denied")
    if owner != identity.tenant_id:
        raise PermissionError("Permission denied")
