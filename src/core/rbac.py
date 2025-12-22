"""Role-Based Access Control (RBAC) for Contex"""

from enum import Enum
from typing import List, Optional, Set

from pydantic import BaseModel
from sqlalchemy import delete, select

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
    """Granular permissions"""
    # Data operations
    PUBLISH_DATA = "publish_data"
    QUERY_DATA = "query_data"

    # Agent operations
    REGISTER_AGENT = "register_agent"
    LIST_AGENTS = "list_agents"
    DELETE_AGENT = "delete_agent"

    # Admin operations
    CREATE_API_KEY = "create_api_key"
    LIST_API_KEYS = "list_api_keys"
    REVOKE_API_KEY = "revoke_api_key"
    MANAGE_ROLES = "manage_roles"
    VIEW_RATE_LIMITS = "view_rate_limits"

    # Project operations
    VIEW_PROJECT_DATA = "view_project_data"
    VIEW_PROJECT_EVENTS = "view_project_events"

    # System operations (cross-project)
    SYSTEM_CLEANUP = "system_cleanup"


# Role to permissions mapping
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        # Admins have all permissions
        Permission.PUBLISH_DATA,
        Permission.QUERY_DATA,
        Permission.REGISTER_AGENT,
        Permission.LIST_AGENTS,
        Permission.DELETE_AGENT,
        Permission.CREATE_API_KEY,
        Permission.LIST_API_KEYS,
        Permission.REVOKE_API_KEY,
        Permission.MANAGE_ROLES,
        Permission.VIEW_RATE_LIMITS,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.SYSTEM_CLEANUP,
    },
    Role.PUBLISHER: {
        # Publishers can publish data and view project info
        Permission.PUBLISH_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
    },
    Role.CONSUMER: {
        # Consumers can register agents and query
        Permission.REGISTER_AGENT,
        Permission.LIST_AGENTS,
        Permission.DELETE_AGENT,
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
    },
    Role.READONLY: {
        # Readonly can only query and view
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.LIST_AGENTS,
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


async def get_role(db: DatabaseManager, key_id: str) -> Optional[APIKeyRole]:
    """
    Get the role assignment for an API key.

    Args:
        db: Database manager
        key_id: API key ID

    Returns:
        APIKeyRole if found, None otherwise
    """
    async with db.session() as session:
        result = await session.execute(
            select(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key_id)
        )
        role_record = result.scalar_one_or_none()

        if not role_record:
            # Default to readonly if no role assigned
            return APIKeyRole(
                key_id=key_id,
                role=Role.READONLY,
                projects=[]
            )

        return APIKeyRole(
            key_id=key_id,
            role=Role(role_record.role),
            projects=role_record.projects or []
        )


async def revoke_role(db: DatabaseManager, key_id: str) -> bool:
    """
    Revoke role assignment for an API key.

    Args:
        db: Database manager
        key_id: API key ID

    Returns:
        True if role was revoked, False if no role existed
    """
    async with db.session() as session:
        result = await session.execute(
            delete(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key_id)
        )

        if result.rowcount > 0:
            logger.info("Role revoked", key_id=key_id)
            return True

    return False


async def list_roles(db: DatabaseManager) -> List[APIKeyRole]:
    """
    List all role assignments.

    Args:
        db: Database manager

    Returns:
        List of APIKeyRole objects
    """
    async with db.session() as session:
        result = await session.execute(select(APIKeyRoleModel))
        role_records = result.scalars().all()

        return [
            APIKeyRole(
                key_id=r.key_id,
                role=Role(r.role),
                projects=r.projects or []
            )
            for r in role_records
        ]


def get_role_permissions(role: Role) -> Set[Permission]:
    """Get all permissions for a role"""
    return ROLE_PERMISSIONS[role].copy()


def check_permission(role: Role, permission: Permission) -> bool:
    """Check if a role has a specific permission"""
    return permission in ROLE_PERMISSIONS[role]
