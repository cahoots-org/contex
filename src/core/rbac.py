"""Role-Based Access Control (RBAC) for Contex"""

from enum import Enum
from typing import List, Optional, Set

from src.core.database import DatabaseManager
from src.core.db_models import APIKeyRole as APIKeyRoleModel
from src.core.logging import get_logger

logger = get_logger(__name__)


class Role(str, Enum):
    """Available roles in the system"""
    ADMIN = "admin"
    PUBLISHER = "publisher"
    CONSUMER = "consumer"


class Permission(str, Enum):
    """Granular permissions enforced on the MCP tool surface."""
    PUBLISH_DATA = "publish_data"
    QUERY_DATA = "query_data"


ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: set(Permission),  # admins have every permission, including future ones
    Role.PUBLISHER: {Permission.PUBLISH_DATA},
    Role.CONSUMER: {Permission.QUERY_DATA},
}


async def assign_role(
    db: DatabaseManager,
    key_id: str,
    role: Role,
    projects: Optional[List[str]] = None
) -> None:
    """Assign `role` to a new API key, limited to `projects` (empty or None = all projects)."""
    async with db.session() as session:
        session.add(APIKeyRoleModel(key_id=key_id, role=role.value, projects=projects or []))

    logger.info("Role assigned", key_id=key_id, role=role.value)


def expand_role(role: Role) -> frozenset[Permission]:
    """Expand a role preset into its permission set."""
    return frozenset(ROLE_PERMISSIONS[role])
