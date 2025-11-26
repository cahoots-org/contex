"""Webhook Management API routes"""

import secrets
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field, HttpUrl

from src.core.webhooks import (
    WebhookManager,
    WebhookEndpoint,
    WebhookEventType,
    WebhookEventCategory,
    WebhookDelivery,
    EVENT_CATALOG,
    get_webhook_manager,
    init_webhook_manager,
)
from src.core.rbac import Role
from src.core.logging import get_logger
from src.core.audit import (
    audit_log,
    AuditEventType,
    AuditEventSeverity,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


# ============================================================
# Request/Response Models
# ============================================================

class CreateEndpointRequest(BaseModel):
    """Request to create a webhook endpoint"""
    name: str = Field(..., description="Endpoint name", min_length=1, max_length=128)
    url: str = Field(..., description="Webhook URL (HTTPS recommended)")
    description: Optional[str] = Field(None, max_length=512)
    events: List[WebhookEventType] = Field(
        default_factory=list,
        description="Event types to subscribe to (empty = all)"
    )
    categories: List[WebhookEventCategory] = Field(
        default_factory=list,
        description="Event categories to subscribe to (empty = all)"
    )
    project_ids: List[str] = Field(
        default_factory=list,
        description="Project IDs to filter by (empty = all)"
    )
    timeout_seconds: int = Field(default=30, ge=5, le=60)
    max_retries: int = Field(default=3, ge=0, le=5)


class UpdateEndpointRequest(BaseModel):
    """Request to update a webhook endpoint"""
    name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    events: Optional[List[WebhookEventType]] = None
    categories: Optional[List[WebhookEventCategory]] = None
    project_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None


class EndpointResponse(BaseModel):
    """Webhook endpoint response"""
    endpoint_id: str
    tenant_id: Optional[str]
    name: str
    description: Optional[str]
    url: str
    events: List[str]
    categories: List[str]
    project_ids: List[str]
    is_active: bool
    timeout_seconds: int
    max_retries: int
    created_at: str
    updated_at: Optional[str]


class EndpointWithSecretResponse(EndpointResponse):
    """Endpoint response with secret (only on create)"""
    secret: str


class DeliveryResponse(BaseModel):
    """Webhook delivery response"""
    delivery_id: str
    event_id: str
    endpoint_id: str
    attempt: int
    status: str
    status_code: Optional[int]
    response_body: Optional[str]
    error: Optional[str]
    created_at: str
    delivered_at: Optional[str]
    duration_ms: Optional[float]


class EventCatalogEntry(BaseModel):
    """Event catalog entry"""
    event_type: str
    category: str
    description: str
    payload_schema: dict


class TestEventRequest(BaseModel):
    """Request to send a test event"""
    event_type: WebhookEventType = Field(
        default=WebhookEventType.SYSTEM_HEALTH_CHANGED,
        description="Event type to test"
    )
    data: dict = Field(
        default_factory=lambda: {"test": True, "message": "This is a test event"},
        description="Test payload data"
    )


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


async def get_manager(request: Request) -> WebhookManager:
    """Get webhook manager or initialize one"""
    manager = get_webhook_manager()
    if not manager:
        manager = init_webhook_manager(request.app.state.redis)
    return manager


async def require_admin_permission(request: Request):
    """Require admin role for webhook management"""
    role = getattr(request.state, 'api_key_role', None)
    if role is None:
        logger.warning("No RBAC context for webhook management")
        return
    if role.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin role required for webhook management"
        )


def endpoint_to_response(endpoint: WebhookEndpoint) -> EndpointResponse:
    """Convert endpoint to response model (without secret)"""
    return EndpointResponse(
        endpoint_id=endpoint.endpoint_id,
        tenant_id=endpoint.tenant_id,
        name=endpoint.name,
        description=endpoint.description,
        url=endpoint.url,
        events=[e.value for e in endpoint.events],
        categories=[c.value for c in endpoint.categories],
        project_ids=endpoint.project_ids,
        is_active=endpoint.is_active,
        timeout_seconds=endpoint.timeout_seconds,
        max_retries=endpoint.max_retries,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


# ============================================================
# Event Catalog Endpoints
# ============================================================

@router.get("/events", response_model=List[EventCatalogEntry])
async def get_event_catalog(
    category: Optional[WebhookEventCategory] = Query(
        None,
        description="Filter by category"
    ),
):
    """
    Get the webhook event catalog.

    Returns all available event types with their descriptions and payload schemas.
    Use this to understand what events you can subscribe to.
    """
    entries = []

    for event_type, info in EVENT_CATALOG.items():
        if category and info["category"] != category:
            continue

        entries.append(EventCatalogEntry(
            event_type=event_type.value,
            category=info["category"].value,
            description=info["description"],
            payload_schema=info["payload_schema"],
        ))

    return entries


@router.get("/events/categories", response_model=List[str])
async def get_event_categories():
    """Get all available event categories"""
    return [c.value for c in WebhookEventCategory]


@router.get("/events/types", response_model=List[str])
async def get_event_types():
    """Get all available event types"""
    return [e.value for e in WebhookEventType]


# ============================================================
# Endpoint Management
# ============================================================

@router.post("/endpoints", response_model=EndpointWithSecretResponse, status_code=201)
async def create_endpoint(
    request: Request,
    body: CreateEndpointRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Create a new webhook endpoint.

    Requires admin role.

    The secret returned in this response is used for HMAC signature verification.
    Store it securely - it cannot be retrieved again.
    """
    import uuid

    ctx = _get_request_context(request)
    manager = await get_manager(request)
    tenant_id = getattr(request.state, 'tenant_id', None)

    # Generate endpoint ID and secret
    endpoint_id = f"whep_{uuid.uuid4().hex[:16]}"
    secret = secrets.token_urlsafe(32)

    endpoint = WebhookEndpoint(
        endpoint_id=endpoint_id,
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        url=body.url,
        secret=secret,
        events=body.events,
        categories=body.categories,
        project_ids=body.project_ids,
        timeout_seconds=body.timeout_seconds,
        max_retries=body.max_retries,
    )

    try:
        created = await manager.create_endpoint(endpoint)

        # Audit log
        await audit_log(
            event_type=AuditEventType.CONFIG_UPDATED,
            action=f"Created webhook endpoint '{body.name}'",
            resource_type="webhook_endpoint",
            resource_id=endpoint_id,
            details={
                "url": body.url,
                "events_count": len(body.events),
                "categories_count": len(body.categories),
            },
            **ctx
        )

        # Return response with secret
        response = endpoint_to_response(created)
        return EndpointWithSecretResponse(
            **response.model_dump(),
            secret=secret,
        )

    except Exception as e:
        logger.error("Failed to create webhook endpoint", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/endpoints", response_model=List[EndpointResponse])
async def list_endpoints(
    request: Request,
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    _: None = Depends(require_admin_permission),
):
    """
    List all webhook endpoints.

    Requires admin role.
    """
    manager = await get_manager(request)
    tenant_id = getattr(request.state, 'tenant_id', None)

    endpoints = await manager.list_endpoints(
        tenant_id=tenant_id,
        is_active=is_active,
    )

    return [endpoint_to_response(e) for e in endpoints]


@router.get("/endpoints/{endpoint_id}", response_model=EndpointResponse)
async def get_endpoint(
    request: Request,
    endpoint_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Get webhook endpoint by ID.

    Requires admin role.
    """
    manager = await get_manager(request)

    endpoint = await manager.get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    return endpoint_to_response(endpoint)


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointResponse)
async def update_endpoint(
    request: Request,
    endpoint_id: str,
    body: UpdateEndpointRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Update a webhook endpoint.

    Requires admin role.
    Note: Cannot update the secret. Delete and recreate for a new secret.
    """
    ctx = _get_request_context(request)
    manager = await get_manager(request)

    updates = body.model_dump(exclude_unset=True)
    endpoint = await manager.update_endpoint(endpoint_id, **updates)

    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Audit log
    await audit_log(
        event_type=AuditEventType.CONFIG_UPDATED,
        action=f"Updated webhook endpoint '{endpoint_id}'",
        resource_type="webhook_endpoint",
        resource_id=endpoint_id,
        details={k: v for k, v in updates.items() if v is not None},
        **ctx
    )

    return endpoint_to_response(endpoint)


@router.delete("/endpoints/{endpoint_id}", status_code=204)
async def delete_endpoint(
    request: Request,
    endpoint_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Delete a webhook endpoint.

    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = await get_manager(request)

    deleted = await manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Audit log
    await audit_log(
        event_type=AuditEventType.CONFIG_UPDATED,
        action=f"Deleted webhook endpoint '{endpoint_id}'",
        resource_type="webhook_endpoint",
        resource_id=endpoint_id,
        severity=AuditEventSeverity.WARNING,
        **ctx
    )


@router.post("/endpoints/{endpoint_id}/rotate-secret", response_model=EndpointWithSecretResponse)
async def rotate_secret(
    request: Request,
    endpoint_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Rotate the webhook secret.

    Requires admin role.
    Store the new secret securely - it cannot be retrieved again.
    """
    ctx = _get_request_context(request)
    manager = await get_manager(request)

    endpoint = await manager.get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Generate new secret
    new_secret = secrets.token_urlsafe(32)
    endpoint = await manager.update_endpoint(endpoint_id, secret=new_secret)

    # Audit log
    await audit_log(
        event_type=AuditEventType.CONFIG_UPDATED,
        action=f"Rotated webhook secret for '{endpoint_id}'",
        resource_type="webhook_endpoint",
        resource_id=endpoint_id,
        severity=AuditEventSeverity.WARNING,
        **ctx
    )

    response = endpoint_to_response(endpoint)
    return EndpointWithSecretResponse(
        **response.model_dump(),
        secret=new_secret,
    )


# ============================================================
# Delivery Management
# ============================================================

@router.get("/endpoints/{endpoint_id}/deliveries", response_model=List[DeliveryResponse])
async def get_deliveries(
    request: Request,
    endpoint_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    _: None = Depends(require_admin_permission),
):
    """
    Get delivery log for an endpoint.

    Returns recent delivery attempts (newest first).
    Requires admin role.
    """
    manager = await get_manager(request)

    # Verify endpoint exists
    endpoint = await manager.get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    deliveries = await manager.get_delivery_log(endpoint_id, limit)

    return [
        DeliveryResponse(
            delivery_id=d.delivery_id,
            event_id=d.event_id,
            endpoint_id=d.endpoint_id,
            attempt=d.attempt,
            status=d.status,
            status_code=d.status_code,
            response_body=d.response_body,
            error=d.error,
            created_at=d.created_at,
            delivered_at=d.delivered_at,
            duration_ms=d.duration_ms,
        )
        for d in deliveries
    ]


@router.post("/endpoints/{endpoint_id}/test")
async def send_test_event(
    request: Request,
    endpoint_id: str,
    body: TestEventRequest,
    _: None = Depends(require_admin_permission),
):
    """
    Send a test event to an endpoint.

    Useful for verifying endpoint configuration.
    Requires admin role.
    """
    ctx = _get_request_context(request)
    manager = await get_manager(request)
    tenant_id = getattr(request.state, 'tenant_id', None)

    # Verify endpoint exists
    endpoint = await manager.get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Emit test event
    event_id = await manager.emit_event(
        event_type=body.event_type,
        data=body.data,
        tenant_id=tenant_id,
    )

    return {
        "status": "sent",
        "event_id": event_id,
        "event_type": body.event_type.value,
        "message": "Test event queued for delivery. Check deliveries endpoint for status.",
    }
