"""Role-Based Access Control (RBAC) for Contex"""

from enum import Enum
from typing import List, Optional, Set

from pydantic import BaseModel
from sqlalchemy import select

from src.core.database import DatabaseManager
from src.core.db_models import APIKeyRole as APIKeyRoleModel
from src.core.logging import get_logger

logger = get_logger(__name__)


class Role(str, Enum):
    """Available roles in the system"""
    ADMIN = "admin"
    PUBLISHER = "publisher"
    CONSUMER = "consumer"
    READONLY = "readonly"


class Permission(str, Enum):
    """Granular permissions enforced on the MCP tool surface."""
    # Data operations
    PUBLISH_DATA = "publish_data"
    QUERY_DATA = "query_data"

    # Project operations
    VIEW_PROJECT_DATA = "view_project_data"
    VIEW_PROJECT_EVENTS = "view_project_events"

    # Versioning
    VIEW_VERSION_HISTORY = "view_version_history"


# Role to permissions mapping
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: set(Permission),  # admins have every permission, including future ones
    Role.PUBLISHER: {
        Permission.PUBLISH_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
    Role.CONSUMER: {
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
    Role.READONLY: {
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
}


class APIKeyRole(BaseModel):
    """Role assignment for an API key"""
    key_id: str
    role: Role
    projects: List[str]  # Empty list means all projects

    def has_permission(self, permission: Permission, project_id: Optional[str] = None) -> bool:
        """Check if this role has a specific permission for a project"""
        # Check if permission is granted to this role
        if permission not in ROLE_PERMISSIONS[self.role]:
            return False

        # If no project restriction, allow
        if not self.projects:
            return True

        # If project_id is None, we're checking a global permission
        if project_id is None:
            return True

        # Check if this specific project is allowed
        return project_id in self.projects


async def assign_role(
    db: DatabaseManager,
    key_id: str,
    role: Role,
    projects: Optional[List[str]] = None
) -> APIKeyRole:
    """
    Assign a role to an API key.

    Args:
        db: Database manager
        key_id: API key ID
        role: Role to assign
        projects: List of project IDs this role applies to (empty = all projects)

    Returns:
        APIKeyRole object
    """
    role_assignment = APIKeyRole(
        key_id=key_id,
        role=role,
        projects=projects or []
    )

    async with db.session() as session:
        # Check if role already exists
        result = await session.execute(
            select(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing role
            existing.role = role.value
            existing.projects = projects or []
        else:
            # Create new role assignment
            role_record = APIKeyRoleModel(
                key_id=key_id,
                role=role.value,
                projects=projects or [],
            )
            session.add(role_record)

    logger.info("Role assigned", key_id=key_id, role=role.value)
    return role_assignment


def expand_role(role: Role) -> frozenset[Permission]:
    """Expand a role preset into its permission set."""
    return frozenset(ROLE_PERMISSIONS[role])
