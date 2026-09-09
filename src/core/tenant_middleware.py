"""Tenant middleware for request isolation and quota enforcement"""

from typing import Optional, List
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core import authz as _authz
from src.core.authz import extract_credential
from src.core.identity import resolve_identity
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



class _TenantSpoofingError(Exception):
    """Raised when a caller's X-Tenant-ID disagrees with their identity tenant."""


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
    ):
        """Initialize tenant middleware.

        Args:
            app: FastAPI application
            public_paths: Paths that don't require tenant context
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

    async def dispatch(self, request: Request, call_next):
        # Skip for public paths
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        # Demo mode (auth off): single implicit default tenant, no enforcement.
        if not _authz.auth_enabled():
            request.state.tenant_id = DEFAULT_TENANT_ID
            request.state.tenant = None
            return await call_next(request)

        try:
            # Get database and create manager
            db = request.app.state.db
            manager = TenantManager(db)
            request.state.tenant_manager = manager

            # Identify tenant
            try:
                tenant_id = await self._identify_tenant(request, manager)
            except _TenantSpoofingError:
                logger.warning("Tenant spoofing attempt rejected",
                               path=path,
                               method=request.method,
                               requested_tenant=request.headers.get("X-Tenant-ID"))
                return JSONResponse(
                    status_code=403,
                    content={"detail": "X-Tenant-ID does not match authenticated tenant"}
                )

            # Fall back to default tenant when no credential is present;
            # route-level get_identity handles 401 for unauthenticated access.
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
        """Derive tenant authoritatively from the caller's identity (auth is on here).

        Raises _TenantSpoofingError if X-Tenant-ID disagrees with identity.tenant_id.
        """
        return await self._identify_tenant_from_identity(request)

    async def _identify_tenant_from_identity(
        self,
        request: Request,
    ) -> Optional[str]:
        """Resolve tenant from the caller's credential (identity not yet in state at middleware time).

        Missing/invalid credentials are not rejected here — get_identity handles 401 downstream.
        Raises _TenantSpoofingError if X-Tenant-ID header disagrees with identity.tenant_id.
        """
        credential = extract_credential(request)
        if not credential:
            return None

        identity = await resolve_identity(request.app.state.db, credential)
        if identity is None:
            return None

        requested = request.headers.get("X-Tenant-ID")
        if requested is not None and requested != identity.tenant_id:
            raise _TenantSpoofingError()

        return identity.tenant_id


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
