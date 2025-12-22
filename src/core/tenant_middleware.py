"""Tenant middleware for request isolation and quota enforcement"""

import os
from typing import Optional, List
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.logging import get_logger
from src.core.tenant import (
    TenantManager,
    Tenant,
    DEFAULT_TENANT_ID,
    ensure_default_tenant,
)

logger = get_logger(__name__)


def _record_tenant_metrics(tenant_id: str, method: str, endpoint: str):
    """Record tenant request metrics (lazy import to avoid circular deps)"""
    try:
        from src.core.metrics import record_tenant_request
        record_tenant_request(tenant_id, method, endpoint)
    except Exception:
        pass  # Don't fail requests if metrics fail


def _record_quota_exceeded(tenant_id: str, resource: str):
    """Record quota exceeded metrics"""
    try:
        from src.core.metrics import record_tenant_quota_exceeded
        record_tenant_quota_exceeded(tenant_id, resource)
    except Exception:
        pass


# Environment variable to enable/disable multi-tenancy
MULTI_TENANT_ENABLED = os.getenv("MULTI_TENANT_ENABLED", "false").lower() == "true"


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to handle tenant isolation and context.

    This middleware:
    1. Identifies the tenant from the request (header, API key, or path)
    2. Validates tenant exists and is active
    3. Enforces tenant quotas
    4. Adds tenant context to the request state

    Tenant identification methods (in order of precedence):
    1. X-Tenant-ID header (explicit, for admin operations)
    2. API key association (automatic, most common)
    3. Default tenant (for backward compatibility when multi-tenancy disabled)

    Request state after middleware:
    - request.state.tenant_id: Current tenant ID
    - request.state.tenant: Full Tenant object
    - request.state.tenant_manager: TenantManager instance
    """

    def __init__(
        self,
        app,
        public_paths: Optional[List[str]] = None,
        require_tenant: bool = True,
    ):
        """
        Initialize tenant middleware.

        Args:
            app: FastAPI application
            public_paths: Paths that don't require tenant context
            require_tenant: Whether to require tenant context (can disable for migration)
        """
        super().__init__(app)
        self.public_paths = public_paths or [
            "/health",
            "/api/docs",
            "/api/openapi.json",
            "/sandbox",
            "/static",
            "/favicon.ico",
            "/api/v1/metrics",
        ]
        self.require_tenant = require_tenant

    async def dispatch(self, request: Request, call_next):
        # Skip for public paths
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        # Skip if multi-tenancy is disabled
        if not MULTI_TENANT_ENABLED:
            # Use default tenant for all requests
            request.state.tenant_id = DEFAULT_TENANT_ID
            request.state.tenant = None  # Lazy load if needed
            return await call_next(request)

        try:
            # Get database and create manager
            db = request.app.state.db
            manager = TenantManager(db)
            request.state.tenant_manager = manager

            # Identify tenant
            tenant_id = await self._identify_tenant(request, manager)

            if not tenant_id and self.require_tenant:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Tenant identification required"}
                )

            # Use default tenant if none identified
            if not tenant_id:
                tenant_id = DEFAULT_TENANT_ID
                await ensure_default_tenant(db)

            # Get and validate tenant
            tenant = await manager.get_tenant(tenant_id)

            if not tenant:
                return JSONResponse(
                    status_code=404,
                    content={"detail": f"Tenant '{tenant_id}' not found"}
                )

            if not tenant.is_active:
                return JSONResponse(
                    status_code=403,
                    content={"detail": f"Tenant '{tenant_id}' is inactive"}
                )

            # Set tenant context
            request.state.tenant_id = tenant_id
            request.state.tenant = tenant

            # Log tenant context
            logger.debug("Tenant context set",
                        tenant_id=tenant_id,
                        path=path,
                        method=request.method)

            # Record tenant metrics
            _record_tenant_metrics(tenant_id, request.method, path)

            return await call_next(request)

        except Exception as e:
            logger.error("Tenant middleware error",
                        error=str(e),
                        path=path,
                        exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal error in tenant resolution"}
            )

    async def _identify_tenant(
        self,
        request: Request,
        manager: TenantManager,
    ) -> Optional[str]:
        """
        Identify tenant from request.

        Tries multiple methods in order:
        1. X-Tenant-ID header
        2. API key association
        3. Path prefix (e.g., /t/tenant_id/...)

        Args:
            request: FastAPI request
            manager: TenantManager instance

        Returns:
            Tenant ID if identified, None otherwise
        """
        # Method 1: Explicit header
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return tenant_id

        # Method 2: API key association
        # Check if APIKeyMiddleware has already set the key_id
        key_id = getattr(request.state, 'api_key_id', None)
        if key_id:
            tenant_id = await manager.get_api_key_tenant(key_id)
            if tenant_id:
                return tenant_id

        # Method 3: Path prefix (e.g., /t/acme-corp/api/v1/...)
        path = request.url.path
        if path.startswith("/t/"):
            parts = path.split("/")
            if len(parts) >= 3:
                return parts[2]

        return None


class TenantQuotaMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce tenant quotas on write operations.

    This middleware checks quotas before allowing:
    - Creating projects
    - Creating agents
    - Publishing events
    - Creating API keys

    Should be added after TenantMiddleware.
    """

    # Map of paths to quota resources
    QUOTA_CHECKS = {
        "/api/v1/data/publish": ("events", 1),
        "/api/v1/agents/register": ("agents", 1),
        "/api/v1/projects": ("projects", 1),
        "/api/v1/auth/keys": ("api_keys", 1),
    }

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        # Skip if disabled or not a write operation
        if not self.enabled or request.method not in ["POST", "PUT"]:
            return await call_next(request)

        # Skip if no tenant context
        tenant_id = getattr(request.state, 'tenant_id', None)
        if not tenant_id:
            return await call_next(request)

        # Check if this path requires quota check
        path = request.url.path
        for check_path, (resource, amount) in self.QUOTA_CHECKS.items():
            if path.startswith(check_path):
                # Get manager
                manager = getattr(request.state, 'tenant_manager', None)
                if not manager:
                    db = request.app.state.db
                    manager = TenantManager(db)

                # Check quota
                allowed, message = await manager.check_quota(
                    tenant_id, resource, amount
                )

                if not allowed:
                    logger.warning("Quota exceeded",
                                 tenant_id=tenant_id,
                                 resource=resource,
                                 message=message)
                    # Record quota exceeded metric
                    _record_quota_exceeded(tenant_id, resource)
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": message,
                            "error_code": "QUOTA_EXCEEDED",
                            "resource": resource,
                        }
                    )
                break

        return await call_next(request)


def get_tenant_id(request: Request) -> str:
    """
    Get tenant ID from request state.

    Args:
        request: FastAPI request

    Returns:
        Tenant ID

    Raises:
        HTTPException: If no tenant context
    """
    tenant_id = getattr(request.state, 'tenant_id', None)
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="Tenant context required"
        )
    return tenant_id


def get_tenant(request: Request) -> Optional[Tenant]:
    """
    Get Tenant object from request state.

    Args:
        request: FastAPI request

    Returns:
        Tenant object or None
    """
    return getattr(request.state, 'tenant', None)


def get_tenant_manager(request: Request) -> Optional[TenantManager]:
    """
    Get TenantManager from request state.

    Args:
        request: FastAPI request

    Returns:
        TenantManager or None
    """
    return getattr(request.state, 'tenant_manager', None)
