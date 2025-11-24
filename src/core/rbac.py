"""Role-Based Access Control (RBAC) for Contex"""

from enum import Enum
from typing import List, Optional, Set
from pydantic import BaseModel
from redis.asyncio import Redis
import json


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
    redis: Redis,
    key_id: str,
    role: Role,
    projects: Optional[List[str]] = None
) -> APIKeyRole:
    """
    Assign a role to an API key.
    
    Args:
        redis: Redis connection
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
    
    # Store in Redis
    await redis.hset(
        f"api_key_role:{key_id}",
        mapping={
            "role": role.value,
            "projects": json.dumps(projects or [])
        }
    )
    
    return role_assignment


async def get_role(redis: Redis, key_id: str) -> Optional[APIKeyRole]:
    """
    Get the role assignment for an API key.
    
    Args:
        redis: Redis connection
        key_id: API key ID
    
    Returns:
        APIKeyRole if found, None otherwise
    """
    data = await redis.hgetall(f"api_key_role:{key_id}")
    
    if not data:
        # Default to readonly if no role assigned
        return APIKeyRole(
            key_id=key_id,
            role=Role.READONLY,
            projects=[]
        )
    
    return APIKeyRole(
        key_id=key_id,
        role=Role(data[b"role"].decode()),
        projects=json.loads(data[b"projects"].decode())
    )


async def revoke_role(redis: Redis, key_id: str) -> bool:
    """
    Revoke role assignment for an API key.
    
    Args:
        redis: Redis connection
        key_id: API key ID
    
    Returns:
        True if role was revoked, False if no role existed
    """
    result = await redis.delete(f"api_key_role:{key_id}")
    return result > 0


async def list_roles(redis: Redis) -> List[APIKeyRole]:
    """
    List all role assignments.
    
    Args:
        redis: Redis connection
    
    Returns:
        List of APIKeyRole objects
    """
    # Get all role keys
    keys = []
    async for key in redis.scan_iter(match="api_key_role:*"):
        keys.append(key)
    
    roles = []
    for key in keys:
        key_id = key.decode().split(":", 1)[1]
        role = await get_role(redis, key_id)
        if role:
            roles.append(role)
    
    return roles


def get_role_permissions(role: Role) -> Set[Permission]:
    """Get all permissions for a role"""
    return ROLE_PERMISSIONS[role].copy()


def check_permission(role: Role, permission: Permission) -> bool:
    """Check if a role has a specific permission"""
    return permission in ROLE_PERMISSIONS[role]
