"""Service Account API routes"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from src.core.service_accounts import (
    ServiceAccountManager,
    ServiceAccount,
    ServiceAccountType,
    ServiceAccountKey,
    ServiceAccountToken,
    get_service_account_manager,
)
from src.core.rbac import Role
from src.core.logging import get_logger
from src.core.audit import (
    audit_log,
    AuditEventType,
    AuditEventSeverity,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/service-accounts", tags=["service-accounts"])


# ============================================================
# Request/Response Models
# ============================================================

class CreateServiceAccountRequest(BaseModel):
    """Request to create a service account"""
    name: str = Field(..., description="Display name", min_length=1, max_length=128)
    description: Optional[str] = Field(None, description="Description", max_length=512)
    account_type: ServiceAccountType = Field(default=ServiceAccountType.INTEGRATION)
    role: Role = Field(default=Role.READONLY, description="RBAC role")
    allowed_projects: List[str] = Field(default_factory=list, description="Allowed project IDs (empty = all)")
    scopes: List[str] = Field(default_factory=list, description="Additional permission scopes")


class UpdateServiceAccountRequest(BaseModel):
    """Request to update a service account"""
    name: Optional[str] = None
    description: Optional[str] = None
    role: Optional[Role] = None
    allowed_projects: Optional[List[str]] = None
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class CreateKeyRequest(BaseModel):
    """Request to create a service account key"""
    description: Optional[str] = Field(None, description="Key description")
    expires_in_days: Optional[int] = Field(None, ge=1, le=365, description="Key expiration in days")


class ServiceAccountKeyResponse(BaseModel):
    """Service account key response (without secret)"""
    key_id: str
    created_at: str
    expires_at: Optional[str]
    last_used: Optional[str]
    description: Optional[str]


class ServiceAccountResponse(BaseModel):
    """Service account response"""
    account_id: str
    name: str
    description: Optional[str]
    account_type: str
    tenant_id: Optional[str]
    role: str
    allowed_projects: List[str]
    scopes: List[str]
    keys: List[ServiceAccountKeyResponse]
    created_at: str
    updated_at: Optional[str]
    is_active: bool
    last_active: Optional[str]
    total_requests: int


class CreateServiceAccountResponse(BaseModel):
    """Response when creating a service account (includes key)"""
    account: ServiceAccountResponse
    key: str  # The actual key - only shown once!


class CreateKeyResponse(BaseModel):
    """Response when creating a key (includes secret)"""
    key: str  # The actual key - only shown once!
    key_info: ServiceAccountKeyResponse


class TokenRequest(BaseModel):
    """Request to exchange key for token"""
    key: str = Field(..., description="Service account key")
    expires_in: int = Field(default=3600, ge=60, le=86400, description="Token lifetime in seconds")


class TokenResponse(BaseModel):
    """Token response"""
    access_token: str
    token_type: str
    expires_in: int
    expires_at: str


# ============================================================
# Helper Functions
# ============================================================

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


def get_manager(request: Request) -> ServiceAccountManager:
    """Get service account manager or create one"""
    manager = get_service_account_manager()
    if not manager:
        from src.core.service_accounts import init_service_account_manager
        manager = init_service_account_manager(request.app.state.redis)
    return manager


async def require_admin_permission(request: Request):
    """Require admin role for service account management"""
    role = getattr(request.state, 'api_key_role', None)
    if role is None:
        logger.warning("No RBAC context for service account management")
        return
    if role.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin role required for service account management"
        )


def account_to_response(account: ServiceAccount) -> ServiceAccountResponse:
    """Convert ServiceAccount to response model"""
    return ServiceAccountResponse(
        account_id=account.account_id,
        name=account.name,
        description=account.description,
        account_type=account.account_type.value,
        tenant_id=account.tenant_id,
        role=account.role.value,
        allowed_projects=account.allowed_projects,
        scopes=account.scopes,
        keys=[
            ServiceAccountKeyResponse(
                key_id=k.key_id,
                created_at=k.created_at,
                expires_at=k.expires_at,
                last_used=k.last_used,
                description=k.description,
            )
            for k in account.keys
        ],
        created_at=account.created_at,
        updated_at=account.updated_at,
        is_active=account.is_active,
        last_active=account.last_active,
        total_requests=account.total_requests,
    )


# ============================================================
# Endpoints
# ============================================================

@router.post("", response_model=CreateServiceAccountResponse, status_code=201)
async def create_service_account(
    request: Request,
    body: CreateServiceAccountRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Create a new service account.

    Returns the service account with an initial key.
    **Important:** The key is only shown once, save it securely!

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = get_manager(request)
    tenant_id = getattr(request.state, 'tenant_id', None)

    try:
        account, raw_key = await manager.create_account(
            name=body.name,
            description=body.description,
            account_type=body.account_type,
            tenant_id=tenant_id,
            role=body.role,
            allowed_projects=body.allowed_projects,
            scopes=body.scopes,
            created_by=ctx.get("actor_id"),
        )

        # Audit log
        await audit_log(
            event_type=AuditEventType.AUTH_API_KEY_CREATED,
            action=f"Created service account '{body.name}'",
            resource_type="service_account",
            resource_id=account.account_id,
            details={
                "name": body.name,
                "account_type": body.account_type.value,
                "role": body.role.value,
            },
            **ctx
        )

        return CreateServiceAccountResponse(
            account=account_to_response(account),
            key=raw_key,
        )

    except Exception as e:
        logger.error("Failed to create service account", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[ServiceAccountResponse])
async def list_service_accounts(
    request: Request,
    account_type: Optional[ServiceAccountType] = None,
    is_active: Optional[bool] = None,
    limit: int = 100,
    _: None = Depends(require_admin_permission),
):
    """
    List service accounts.

    Requires admin role.
    """
    manager = get_manager(request)
    tenant_id = getattr(request.state, 'tenant_id', None)

    accounts = await manager.list_accounts(
        tenant_id=tenant_id,
        account_type=account_type,
        is_active=is_active,
        limit=limit,
    )

    return [account_to_response(a) for a in accounts]


@router.get("/{account_id}", response_model=ServiceAccountResponse)
async def get_service_account(
    request: Request,
    account_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Get service account by ID.

    Requires admin role.
    """
    manager = get_manager(request)

    account = await manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail=f"Service account '{account_id}' not found")

    return account_to_response(account)


@router.patch("/{account_id}", response_model=ServiceAccountResponse)
async def update_service_account(
    request: Request,
    account_id: str,
    body: UpdateServiceAccountRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Update service account.

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = get_manager(request)

    account = await manager.update_account(
        account_id=account_id,
        name=body.name,
        description=body.description,
        role=body.role,
        allowed_projects=body.allowed_projects,
        scopes=body.scopes,
        is_active=body.is_active,
    )

    if not account:
        raise HTTPException(status_code=404, detail=f"Service account '{account_id}' not found")

    # Audit log
    await audit_log(
        event_type=AuditEventType.CONFIG_UPDATED,
        action=f"Updated service account '{account_id}'",
        resource_type="service_account",
        resource_id=account_id,
        details={k: v for k, v in body.model_dump().items() if v is not None},
        **ctx
    )

    return account_to_response(account)


@router.delete("/{account_id}", status_code=204)
async def delete_service_account(
    request: Request,
    account_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Delete service account.

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = get_manager(request)

    deleted = await manager.delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Service account '{account_id}' not found")

    # Audit log
    await audit_log(
        event_type=AuditEventType.AUTH_API_KEY_REVOKED,
        action=f"Deleted service account '{account_id}'",
        resource_type="service_account",
        resource_id=account_id,
        severity=AuditEventSeverity.WARNING,
        **ctx
    )


@router.post("/{account_id}/keys", response_model=CreateKeyResponse, status_code=201)
async def create_key(
    request: Request,
    account_id: str,
    body: CreateKeyRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Create a new key for a service account.

    **Important:** The key is only shown once, save it securely!

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = get_manager(request)

    result = await manager.create_key(
        account_id=account_id,
        description=body.description,
        expires_in_days=body.expires_in_days,
    )

    if not result:
        raise HTTPException(status_code=404, detail=f"Service account '{account_id}' not found")

    raw_key, key_info = result

    # Audit log
    await audit_log(
        event_type=AuditEventType.AUTH_API_KEY_CREATED,
        action=f"Created key for service account '{account_id}'",
        resource_type="service_account_key",
        resource_id=key_info.key_id,
        details={"account_id": account_id, "expires_in_days": body.expires_in_days},
        **ctx
    )

    return CreateKeyResponse(
        key=raw_key,
        key_info=ServiceAccountKeyResponse(
            key_id=key_info.key_id,
            created_at=key_info.created_at,
            expires_at=key_info.expires_at,
            last_used=key_info.last_used,
            description=key_info.description,
        ),
    )


@router.delete("/{account_id}/keys/{key_id}", status_code=204)
async def revoke_key(
    request: Request,
    account_id: str,
    key_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Revoke a service account key.

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = get_manager(request)

    revoked = await manager.revoke_key(account_id, key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found")

    # Audit log
    await audit_log(
        event_type=AuditEventType.AUTH_API_KEY_REVOKED,
        action=f"Revoked key '{key_id}' from service account '{account_id}'",
        resource_type="service_account_key",
        resource_id=key_id,
        details={"account_id": account_id},
        **ctx
    )


@router.post("/token", response_model=TokenResponse)
async def get_token(
    request: Request,
    body: TokenRequest,
):
    """
    Exchange a service account key for a JWT token.

    This endpoint does NOT require existing authentication.
    The service account key itself is the authentication.
    """
    manager = get_manager(request)
    ctx = _get_request_context(request)

    # Authenticate with key
    account = await manager.authenticate(body.key)
    if not account:
        await audit_log(
            event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="Service account authentication failed",
            result="failure",
            severity=AuditEventSeverity.WARNING,
            details={"key_prefix": body.key[:10] + "..." if len(body.key) > 10 else body.key},
            **ctx
        )
        raise HTTPException(status_code=401, detail="Invalid or expired service account key")

    # Issue token
    token = await manager.issue_token(account, expires_in=body.expires_in)

    # Audit log
    await audit_log(
        event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
        action=f"Service account '{account.account_id}' authenticated",
        actor_id=account.account_id,
        actor_type="service_account",
        tenant_id=account.tenant_id,
        details={
            "account_name": account.name,
            "account_type": account.account_type.value,
            "token_expires_in": body.expires_in,
        },
        **ctx
    )

    return TokenResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        expires_in=token.expires_in,
        expires_at=token.expires_at,
    )


@router.post("/token/validate")
async def validate_token(
    request: Request,
    token: str,
):
    """
    Validate a JWT token and return its claims.

    Useful for debugging and token inspection.
    """
    manager = get_manager(request)

    claims = await manager.validate_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {
        "valid": True,
        "claims": claims,
    }
