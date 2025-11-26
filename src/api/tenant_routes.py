"""API routes for tenant management"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from src.core.tenant import (
    TenantManager,
    Tenant,
    TenantPlan,
    TenantQuotas,
    TenantUsage,
)
from src.core.rbac import Role, Permission
from src.core.logging import get_logger
from src.core.audit import (
    audit_log,
    AuditEventType,
    AuditEventSeverity,
)

logger = get_logger(__name__)


def _get_request_context(request: Request) -> dict:
    """Extract common audit context from request"""
    return {
        "actor_id": getattr(request.state, 'api_key_id', None),
        "actor_type": "api_key" if getattr(request.state, 'api_key_id', None) else None,
        "actor_ip": request.client.host if request.client else None,
        "tenant_id": getattr(request.state, 'tenant_id', None),
        "request_id": getattr(request.state, 'request_id', None),
        "endpoint": str(request.url.path),
        "method": request.method,
    }

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


# ============================================================
# Request/Response Models
# ============================================================

class CreateTenantRequest(BaseModel):
    """Request to create a new tenant"""
    tenant_id: str = Field(..., description="Unique tenant identifier", min_length=3, max_length=64)
    name: str = Field(..., description="Display name", min_length=1, max_length=128)
    plan: TenantPlan = Field(default=TenantPlan.FREE, description="Subscription plan")
    owner_email: Optional[str] = Field(None, description="Owner email address")
    settings: Optional[Dict[str, Any]] = Field(None, description="Tenant-specific settings")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class UpdateTenantRequest(BaseModel):
    """Request to update a tenant"""
    name: Optional[str] = Field(None, description="New display name")
    plan: Optional[TenantPlan] = Field(None, description="New plan")
    quotas: Optional[TenantQuotas] = Field(None, description="Custom quotas")
    settings: Optional[Dict[str, Any]] = Field(None, description="Updated settings")
    is_active: Optional[bool] = Field(None, description="Active status")


class TenantResponse(BaseModel):
    """Tenant response"""
    tenant_id: str
    name: str
    plan: TenantPlan
    quotas: TenantQuotas
    settings: Dict[str, Any]
    created_at: str
    updated_at: Optional[str]
    is_active: bool
    owner_email: Optional[str]


class TenantListResponse(BaseModel):
    """List of tenants response"""
    tenants: List[TenantResponse]
    total: int


class TenantUsageResponse(BaseModel):
    """Tenant usage response"""
    tenant_id: str
    usage: TenantUsage
    quotas: TenantQuotas
    usage_percentage: Dict[str, float]


# ============================================================
# Helper Functions
# ============================================================

def get_tenant_manager(request: Request) -> TenantManager:
    """Get TenantManager from request state or create new one"""
    manager = getattr(request.state, 'tenant_manager', None)
    if not manager:
        manager = TenantManager(request.app.state.redis)
    return manager


async def require_admin_permission(request: Request):
    """
    Dependency to require admin permission for tenant management.

    In production, this should check the API key's role.
    """
    # Get role from request state (set by RBAC middleware)
    role = getattr(request.state, 'api_key_role', None)

    if role is None:
        # For now, allow if no RBAC middleware (development mode)
        logger.warning("No RBAC context, allowing tenant management")
        return

    if role.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin role required for tenant management"
        )


# ============================================================
# Tenant CRUD Endpoints
# ============================================================

@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    request: Request,
    body: CreateTenantRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Create a new tenant.

    Requires admin role.

    Args:
        body: Tenant creation request

    Returns:
        Created tenant
    """
    manager = get_tenant_manager(request)
    ctx = _get_request_context(request)

    try:
        tenant = await manager.create_tenant(
            tenant_id=body.tenant_id,
            name=body.name,
            plan=body.plan,
            owner_email=body.owner_email,
            settings=body.settings,
            metadata=body.metadata,
        )

        # Audit log tenant creation
        await audit_log(
            event_type=AuditEventType.TENANT_CREATED,
            action=f"Created tenant '{body.tenant_id}'",
            resource_type="tenant",
            resource_id=body.tenant_id,
            details={
                "name": body.name,
                "plan": body.plan.value,
                "owner_email": body.owner_email,
            },
            **ctx
        )

        logger.info("Tenant created via API",
                   tenant_id=tenant.tenant_id,
                   plan=tenant.plan.value)

        return TenantResponse(
            tenant_id=tenant.tenant_id,
            name=tenant.name,
            plan=tenant.plan,
            quotas=tenant.quotas,
            settings=tenant.settings,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            is_active=tenant.is_active,
            owner_email=tenant.owner_email,
        )

    except ValueError as e:
        await audit_log(
            event_type=AuditEventType.TENANT_CREATED,
            action=f"Failed to create tenant '{body.tenant_id}'",
            resource_type="tenant",
            resource_id=body.tenant_id,
            result="failure",
            severity=AuditEventSeverity.WARNING,
            details={"error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    request: Request,
    plan: Optional[TenantPlan] = None,
    is_active: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    _: None = Depends(require_admin_permission),
):
    """
    List all tenants with optional filtering.

    Requires admin role.

    Args:
        plan: Filter by subscription plan
        is_active: Filter by active status
        limit: Maximum results (default 100)
        offset: Skip first N results

    Returns:
        List of tenants
    """
    manager = get_tenant_manager(request)

    tenants = await manager.list_tenants(
        plan=plan,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )

    return TenantListResponse(
        tenants=[
            TenantResponse(
                tenant_id=t.tenant_id,
                name=t.name,
                plan=t.plan,
                quotas=t.quotas,
                settings=t.settings,
                created_at=t.created_at,
                updated_at=t.updated_at,
                is_active=t.is_active,
                owner_email=t.owner_email,
            )
            for t in tenants
        ],
        total=len(tenants),
    )


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    request: Request,
    tenant_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Get tenant by ID.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier

    Returns:
        Tenant details
    """
    manager = get_tenant_manager(request)

    tenant = await manager.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    return TenantResponse(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        plan=tenant.plan,
        quotas=tenant.quotas,
        settings=tenant.settings,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        is_active=tenant.is_active,
        owner_email=tenant.owner_email,
    )


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    request: Request,
    tenant_id: str,
    body: UpdateTenantRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Update tenant properties.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier
        body: Update request

    Returns:
        Updated tenant
    """
    manager = get_tenant_manager(request)
    ctx = _get_request_context(request)

    # Get current state for audit diff
    before_tenant = await manager.get_tenant(tenant_id)

    tenant = await manager.update_tenant(
        tenant_id=tenant_id,
        name=body.name,
        plan=body.plan,
        quotas=body.quotas,
        settings=body.settings,
        is_active=body.is_active,
    )

    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    # Audit log tenant update
    changes = {}
    if body.name is not None:
        changes["name"] = body.name
    if body.plan is not None:
        changes["plan"] = body.plan.value
    if body.is_active is not None:
        changes["is_active"] = body.is_active

    await audit_log(
        event_type=AuditEventType.TENANT_UPDATED,
        action=f"Updated tenant '{tenant_id}'",
        resource_type="tenant",
        resource_id=tenant_id,
        details={"changes": changes},
        before={"name": before_tenant.name, "plan": before_tenant.plan.value, "is_active": before_tenant.is_active} if before_tenant else None,
        after={"name": tenant.name, "plan": tenant.plan.value, "is_active": tenant.is_active},
        **ctx
    )

    logger.info("Tenant updated via API", tenant_id=tenant_id)

    return TenantResponse(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        plan=tenant.plan,
        quotas=tenant.quotas,
        settings=tenant.settings,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        is_active=tenant.is_active,
        owner_email=tenant.owner_email,
    )


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(
    request: Request,
    tenant_id: str,
    force: bool = False,
    _: None = Depends(require_admin_permission),
):
    """
    Delete a tenant.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier
        force: Force delete even if tenant has data

    Returns:
        204 No Content on success
    """
    manager = get_tenant_manager(request)
    ctx = _get_request_context(request)

    try:
        deleted = await manager.delete_tenant(tenant_id, force=force)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

        # Audit log tenant deletion
        await audit_log(
            event_type=AuditEventType.TENANT_DELETED,
            action=f"Deleted tenant '{tenant_id}'",
            resource_type="tenant",
            resource_id=tenant_id,
            severity=AuditEventSeverity.WARNING,
            details={"force": force},
            **ctx
        )

        logger.warning("Tenant deleted via API", tenant_id=tenant_id, force=force)

    except ValueError as e:
        await audit_log(
            event_type=AuditEventType.TENANT_DELETED,
            action=f"Failed to delete tenant '{tenant_id}'",
            resource_type="tenant",
            resource_id=tenant_id,
            result="failure",
            severity=AuditEventSeverity.WARNING,
            details={"force": force, "error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# Usage and Quota Endpoints
# ============================================================

@router.get("/{tenant_id}/usage", response_model=TenantUsageResponse)
async def get_tenant_usage(
    request: Request,
    tenant_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Get current usage for a tenant.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier

    Returns:
        Usage statistics and quota percentages
    """
    manager = get_tenant_manager(request)

    tenant = await manager.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    usage = await manager.get_usage(tenant_id)
    if not usage:
        raise HTTPException(status_code=404, detail=f"Usage data not found")

    # Calculate percentage of quota used
    quotas = tenant.quotas
    usage_percentage = {
        "projects": (usage.projects_count / quotas.max_projects * 100) if quotas.max_projects > 0 else 0,
        "api_keys": (usage.api_keys_count / quotas.max_api_keys * 100) if quotas.max_api_keys > 0 else 0,
        "events": (usage.events_this_month / quotas.max_events_per_month * 100) if quotas.max_events_per_month > 0 else 0,
        "storage": (usage.storage_used_mb / quotas.max_storage_mb * 100) if quotas.max_storage_mb > 0 else 0,
    }

    return TenantUsageResponse(
        tenant_id=tenant_id,
        usage=usage,
        quotas=quotas,
        usage_percentage=usage_percentage,
    )


@router.post("/{tenant_id}/reset-monthly-usage", status_code=204)
async def reset_monthly_usage(
    request: Request,
    tenant_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Reset monthly usage counters for a tenant.

    Typically called by billing system at the start of a new billing cycle.
    Requires admin role.

    Args:
        tenant_id: Tenant identifier

    Returns:
        204 No Content on success
    """
    manager = get_tenant_manager(request)

    tenant = await manager.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    await manager.reset_monthly_usage(tenant_id)

    logger.info("Monthly usage reset via API", tenant_id=tenant_id)


# ============================================================
# Project Management Endpoints
# ============================================================

@router.get("/{tenant_id}/projects", response_model=List[str])
async def list_tenant_projects(
    request: Request,
    tenant_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    List all projects for a tenant.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier

    Returns:
        List of project IDs
    """
    manager = get_tenant_manager(request)

    tenant = await manager.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    return await manager.list_projects(tenant_id)


@router.post("/{tenant_id}/projects/{project_id}", status_code=201)
async def add_project_to_tenant(
    request: Request,
    tenant_id: str,
    project_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Add a project to a tenant.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier
        project_id: Project identifier

    Returns:
        201 Created with project key
    """
    manager = get_tenant_manager(request)

    tenant = await manager.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    try:
        project_key = await manager.add_project(tenant_id, project_id)
        logger.info("Project added to tenant",
                   tenant_id=tenant_id,
                   project_id=project_id)
        return {"project_key": project_key}

    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))


@router.delete("/{tenant_id}/projects/{project_id}", status_code=204)
async def remove_project_from_tenant(
    request: Request,
    tenant_id: str,
    project_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Remove a project from a tenant.

    Requires admin role.

    Args:
        tenant_id: Tenant identifier
        project_id: Project identifier

    Returns:
        204 No Content on success
    """
    manager = get_tenant_manager(request)

    removed = await manager.remove_project(tenant_id, project_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in tenant")

    logger.info("Project removed from tenant",
               tenant_id=tenant_id,
               project_id=project_id)
