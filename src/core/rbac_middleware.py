"""RBAC Middleware for enforcing role-based access control"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.asyncio import Redis
from src.core.rbac import get_role, Permission
from typing import Optional


# Mapping of endpoint patterns to required permissions
ENDPOINT_PERMISSIONS = {
    # Data operations
    "/api/data/publish": Permission.PUBLISH_DATA,
    "/api/publish": Permission.PUBLISH_DATA,
    
    # Agent operations
    "/api/agents/register": Permission.REGISTER_AGENT,
    "/api/register": Permission.REGISTER_AGENT,
    "/api/agents": Permission.LIST_AGENTS,  # GET
    
    # Query operations
    "/api/query": Permission.QUERY_DATA,
    "/api/projects/*/query": Permission.QUERY_DATA,
    
    # Project data
    "/api/projects/*/data": Permission.VIEW_PROJECT_DATA,
    "/api/projects/*/events": Permission.VIEW_PROJECT_EVENTS,
    
    # Admin operations
    "/api/auth/keys": Permission.CREATE_API_KEY,  # POST
    "/api/admin/rate-limits": Permission.VIEW_RATE_LIMITS,
}

# Method-specific permissions
METHOD_PERMISSIONS = {
    ("GET", "/api/agents"): Permission.LIST_AGENTS,
    ("DELETE", "/api/agents/*"): Permission.DELETE_AGENT,
    ("POST", "/api/auth/keys"): Permission.CREATE_API_KEY,
    ("GET", "/api/auth/keys"): Permission.LIST_API_KEYS,
    ("DELETE", "/api/auth/keys/*"): Permission.REVOKE_API_KEY,
}


class RBACMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce role-based access control"""

    def __init__(self, app):
        super().__init__(app)

    def get_required_permission(self, method: str, path: str) -> Optional[Permission]:
        """Get the required permission for a method and path"""
        # Check method-specific permissions first
        for (req_method, pattern), permission in METHOD_PERMISSIONS.items():
            if method == req_method and self._path_matches(path, pattern):
                return permission

        # Check general endpoint permissions
        for pattern, permission in ENDPOINT_PERMISSIONS.items():
            if self._path_matches(path, pattern):
                return permission

        return None

    def _path_matches(self, path: str, pattern: str) -> bool:
        """Check if a path matches a pattern (supports * wildcard)"""
        if "*" not in pattern:
            return path.startswith(pattern)

        # Simple wildcard matching
        parts = pattern.split("*")
        if not path.startswith(parts[0]):
            return False

        current_pos = len(parts[0])
        for part in parts[1:]:
            if not part:
                continue
            pos = path.find(part, current_pos)
            if pos == -1:
                return False
            current_pos = pos + len(part)

        return True

    def extract_project_id(self, path: str, body: Optional[dict] = None) -> Optional[str]:
        """Extract project_id from path or request body"""
        # Try to extract from path (e.g., /api/projects/proj123/query)
        if "/projects/" in path:
            parts = path.split("/projects/")
            if len(parts) > 1:
                project_part = parts[1].split("/")[0]
                if project_part:
                    return project_part

        # Try to extract from body
        if body and "project_id" in body:
            return body["project_id"]

        return None

    async def dispatch(self, request: Request, call_next):
        # Skip RBAC for public endpoints
        if request.url.path in ["/health", "/", "/docs", "/openapi.json", "/redoc", "/sandbox"]:
            return await call_next(request)

        # Skip RBAC for static files
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        # Get API key from request (should be set by APIKeyMiddleware)
        api_key_header = request.headers.get("X-API-Key")
        if not api_key_header:
            # No API key - let APIKeyMiddleware handle this
            return await call_next(request)

        # Extract key_id from state (set by APIKeyMiddleware)
        key_id = getattr(request.state, "api_key_id", None)
        if not key_id:
            # Try to get from header (fallback)
            key_id = api_key_header[:16] if len(api_key_header) >= 16 else api_key_header

        # Get Redis from app state
        redis = request.app.state.redis

        # Get role for this API key
        role_assignment = await get_role(redis, key_id)

        # Get required permission for this endpoint
        required_permission = self.get_required_permission(
            request.method,
            request.url.path
        )

        # If no specific permission required, allow
        if not required_permission:
            return await call_next(request)

        # Extract project_id if applicable
        project_id = None
        if request.method in ["POST", "PUT", "PATCH"]:
            try:
                body = await request.json()
                project_id = self.extract_project_id(request.url.path, body)
                # Re-create request with body (since we consumed it)
                request._body = None
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
                # Body is not JSON or couldn't be decoded, extract from URL instead
                print(f"[RBAC] Could not decode request body: {type(e).__name__}, extracting project_id from URL")
                project_id = self.extract_project_id(request.url.path)
        else:
            project_id = self.extract_project_id(request.url.path)

        # Check if role has permission
        if not role_assignment.has_permission(required_permission, project_id):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Your role '{role_assignment.role.value}' does not have permission to perform this action",
                    "required_permission": required_permission.value,
                    "your_role": role_assignment.role.value
                }
            )

        # Store role in request state for use by endpoints
        request.state.role = role_assignment.role
        request.state.role_assignment = role_assignment

        return await call_next(request)
