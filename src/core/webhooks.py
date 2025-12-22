"""Webhook System for Contex

Provides webhook delivery for real-time event notifications:
- Event catalog with standardized event types
- Webhook endpoint management
- Reliable delivery with retries
- Delivery logging and metrics
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from src.core.database import DatabaseManager
from src.core.db_models import WebhookDelivery as WebhookDeliveryModel
from src.core.db_models import WebhookEndpoint as WebhookEndpointModel
from src.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# EVENT CATALOG
# ============================================================================

class WebhookEventType(str, Enum):
    """
    Webhook Event Catalog

    All events follow the pattern: {resource}.{action}
    """
    # Data Events
    DATA_PUBLISHED = "data.published"
    DATA_UPDATED = "data.updated"
    DATA_DELETED = "data.deleted"
    DATA_VERSION_CREATED = "data.version.created"
    DATA_VERSION_RESTORED = "data.version.restored"

    # Agent Events
    AGENT_REGISTERED = "agent.registered"
    AGENT_UNREGISTERED = "agent.unregistered"
    AGENT_SUBSCRIBED = "agent.subscribed"
    AGENT_UNSUBSCRIBED = "agent.unsubscribed"

    # Context Events
    CONTEXT_QUERIED = "context.queried"
    CONTEXT_MATCHED = "context.matched"

    # Project Events
    PROJECT_CREATED = "project.created"
    PROJECT_EXPORTED = "project.exported"
    PROJECT_IMPORTED = "project.imported"
    PROJECT_DELETED = "project.deleted"

    # Security Events
    API_KEY_CREATED = "security.api_key.created"
    API_KEY_REVOKED = "security.api_key.revoked"
    AUTH_FAILED = "security.auth.failed"
    RATE_LIMITED = "security.rate_limited"
    PERMISSION_DENIED = "security.permission_denied"

    # Service Account Events
    SERVICE_ACCOUNT_CREATED = "service_account.created"
    SERVICE_ACCOUNT_KEY_ROTATED = "service_account.key_rotated"
    SERVICE_ACCOUNT_DELETED = "service_account.deleted"

    # Tenant Events
    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_SUSPENDED = "tenant.suspended"
    TENANT_QUOTA_WARNING = "tenant.quota.warning"
    TENANT_QUOTA_EXCEEDED = "tenant.quota.exceeded"

    # System Events
    SYSTEM_HEALTH_CHANGED = "system.health.changed"
    SYSTEM_DEGRADED = "system.degraded"
    SYSTEM_RECOVERED = "system.recovered"


class WebhookEventCategory(str, Enum):
    """Event categories for filtering"""
    DATA = "data"
    AGENT = "agent"
    CONTEXT = "context"
    PROJECT = "project"
    SECURITY = "security"
    SERVICE_ACCOUNT = "service_account"
    TENANT = "tenant"
    SYSTEM = "system"


# Event metadata for the catalog
EVENT_CATALOG: Dict[WebhookEventType, Dict[str, Any]] = {
    WebhookEventType.DATA_PUBLISHED: {
        "category": WebhookEventCategory.DATA,
        "description": "Data was published to a project",
        "payload_schema": {
            "project_id": "string",
            "data_key": "string",
            "sequence": "integer",
            "data_format": "string",
            "version": "integer (optional)",
        },
    },
    WebhookEventType.DATA_UPDATED: {
        "category": WebhookEventCategory.DATA,
        "description": "Existing data was updated",
        "payload_schema": {
            "project_id": "string",
            "data_key": "string",
            "sequence": "integer",
            "previous_sequence": "integer",
        },
    },
    WebhookEventType.DATA_DELETED: {
        "category": WebhookEventCategory.DATA,
        "description": "Data was deleted from a project",
        "payload_schema": {
            "project_id": "string",
            "data_key": "string",
        },
    },
    WebhookEventType.DATA_VERSION_CREATED: {
        "category": WebhookEventCategory.DATA,
        "description": "New version of data was created",
        "payload_schema": {
            "project_id": "string",
            "data_key": "string",
            "version": "integer",
            "data_hash": "string",
            "change_type": "string",
        },
    },
    WebhookEventType.DATA_VERSION_RESTORED: {
        "category": WebhookEventCategory.DATA,
        "description": "Previous version of data was restored",
        "payload_schema": {
            "project_id": "string",
            "data_key": "string",
            "restored_from_version": "integer",
            "new_version": "integer",
        },
    },
    WebhookEventType.AGENT_REGISTERED: {
        "category": WebhookEventCategory.AGENT,
        "description": "Agent registered with the system",
        "payload_schema": {
            "agent_id": "string",
            "project_id": "string",
            "capabilities": "array of strings",
        },
    },
    WebhookEventType.AGENT_UNREGISTERED: {
        "category": WebhookEventCategory.AGENT,
        "description": "Agent was unregistered",
        "payload_schema": {
            "agent_id": "string",
            "project_id": "string",
        },
    },
    WebhookEventType.AGENT_SUBSCRIBED: {
        "category": WebhookEventCategory.AGENT,
        "description": "Agent subscribed to data needs",
        "payload_schema": {
            "agent_id": "string",
            "project_id": "string",
            "needs": "array of strings",
        },
    },
    WebhookEventType.AGENT_UNSUBSCRIBED: {
        "category": WebhookEventCategory.AGENT,
        "description": "Agent unsubscribed from data needs",
        "payload_schema": {
            "agent_id": "string",
            "project_id": "string",
            "needs": "array of strings",
        },
    },
    WebhookEventType.CONTEXT_QUERIED: {
        "category": WebhookEventCategory.CONTEXT,
        "description": "Context was queried",
        "payload_schema": {
            "project_id": "string",
            "agent_id": "string (optional)",
            "query": "string",
            "results_count": "integer",
        },
    },
    WebhookEventType.CONTEXT_MATCHED: {
        "category": WebhookEventCategory.CONTEXT,
        "description": "Context was matched to agent needs",
        "payload_schema": {
            "project_id": "string",
            "agent_id": "string",
            "matches_count": "integer",
            "top_score": "float",
        },
    },
    WebhookEventType.PROJECT_CREATED: {
        "category": WebhookEventCategory.PROJECT,
        "description": "New project was created",
        "payload_schema": {
            "project_id": "string",
        },
    },
    WebhookEventType.PROJECT_EXPORTED: {
        "category": WebhookEventCategory.PROJECT,
        "description": "Project data was exported",
        "payload_schema": {
            "project_id": "string",
            "export_format": "string",
        },
    },
    WebhookEventType.PROJECT_IMPORTED: {
        "category": WebhookEventCategory.PROJECT,
        "description": "Project data was imported",
        "payload_schema": {
            "project_id": "string",
            "events_count": "integer",
            "agents_count": "integer",
        },
    },
    WebhookEventType.PROJECT_DELETED: {
        "category": WebhookEventCategory.PROJECT,
        "description": "Project was deleted",
        "payload_schema": {
            "project_id": "string",
        },
    },
    WebhookEventType.API_KEY_CREATED: {
        "category": WebhookEventCategory.SECURITY,
        "description": "New API key was created",
        "payload_schema": {
            "key_id": "string",
            "name": "string",
            "role": "string",
        },
    },
    WebhookEventType.API_KEY_REVOKED: {
        "category": WebhookEventCategory.SECURITY,
        "description": "API key was revoked",
        "payload_schema": {
            "key_id": "string",
        },
    },
    WebhookEventType.AUTH_FAILED: {
        "category": WebhookEventCategory.SECURITY,
        "description": "Authentication attempt failed",
        "payload_schema": {
            "reason": "string",
            "ip_address": "string",
            "endpoint": "string",
        },
    },
    WebhookEventType.RATE_LIMITED: {
        "category": WebhookEventCategory.SECURITY,
        "description": "Request was rate limited",
        "payload_schema": {
            "key_id": "string (optional)",
            "ip_address": "string",
            "endpoint": "string",
        },
    },
    WebhookEventType.PERMISSION_DENIED: {
        "category": WebhookEventCategory.SECURITY,
        "description": "Permission was denied for an operation",
        "payload_schema": {
            "key_id": "string",
            "action": "string",
            "resource": "string",
        },
    },
    WebhookEventType.SERVICE_ACCOUNT_CREATED: {
        "category": WebhookEventCategory.SERVICE_ACCOUNT,
        "description": "Service account was created",
        "payload_schema": {
            "account_id": "string",
            "name": "string",
            "type": "string",
        },
    },
    WebhookEventType.SERVICE_ACCOUNT_KEY_ROTATED: {
        "category": WebhookEventCategory.SERVICE_ACCOUNT,
        "description": "Service account key was rotated",
        "payload_schema": {
            "account_id": "string",
            "old_key_id": "string",
            "new_key_id": "string",
        },
    },
    WebhookEventType.SERVICE_ACCOUNT_DELETED: {
        "category": WebhookEventCategory.SERVICE_ACCOUNT,
        "description": "Service account was deleted",
        "payload_schema": {
            "account_id": "string",
        },
    },
    WebhookEventType.TENANT_CREATED: {
        "category": WebhookEventCategory.TENANT,
        "description": "New tenant was created",
        "payload_schema": {
            "tenant_id": "string",
            "name": "string",
            "plan": "string",
        },
    },
    WebhookEventType.TENANT_UPDATED: {
        "category": WebhookEventCategory.TENANT,
        "description": "Tenant settings were updated",
        "payload_schema": {
            "tenant_id": "string",
            "updated_fields": "array of strings",
        },
    },
    WebhookEventType.TENANT_SUSPENDED: {
        "category": WebhookEventCategory.TENANT,
        "description": "Tenant was suspended",
        "payload_schema": {
            "tenant_id": "string",
            "reason": "string",
        },
    },
    WebhookEventType.TENANT_QUOTA_WARNING: {
        "category": WebhookEventCategory.TENANT,
        "description": "Tenant approaching quota limit",
        "payload_schema": {
            "tenant_id": "string",
            "quota_type": "string",
            "current_usage": "integer",
            "limit": "integer",
            "percentage": "float",
        },
    },
    WebhookEventType.TENANT_QUOTA_EXCEEDED: {
        "category": WebhookEventCategory.TENANT,
        "description": "Tenant exceeded quota",
        "payload_schema": {
            "tenant_id": "string",
            "quota_type": "string",
            "current_usage": "integer",
            "limit": "integer",
        },
    },
    WebhookEventType.SYSTEM_HEALTH_CHANGED: {
        "category": WebhookEventCategory.SYSTEM,
        "description": "System health status changed",
        "payload_schema": {
            "previous_status": "string",
            "current_status": "string",
            "components": "object",
        },
    },
    WebhookEventType.SYSTEM_DEGRADED: {
        "category": WebhookEventCategory.SYSTEM,
        "description": "System entered degraded mode",
        "payload_schema": {
            "reason": "string",
            "affected_components": "array of strings",
        },
    },
    WebhookEventType.SYSTEM_RECOVERED: {
        "category": WebhookEventCategory.SYSTEM,
        "description": "System recovered from degraded mode",
        "payload_schema": {
            "downtime_seconds": "float",
            "recovered_components": "array of strings",
        },
    },
}


# ============================================================================
# MODELS
# ============================================================================

class WebhookEndpoint(BaseModel):
    """Webhook endpoint configuration"""
    endpoint_id: str
    tenant_id: Optional[str] = None

    # Endpoint configuration
    url: str
    secret: str  # For HMAC signature

    # Event filtering
    events: List[str] = Field(default_factory=list)  # Empty = all events
    categories: List[str] = Field(default_factory=list)  # Empty = all categories

    # Optional filters
    project_ids: List[str] = Field(default_factory=list)  # Empty = all projects

    # Configuration
    is_active: bool = True
    timeout_seconds: int = 30
    max_retries: int = 3

    # Metadata
    name: str
    description: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: Optional[str] = None


class WebhookEvent(BaseModel):
    """Webhook event payload"""
    event_id: str
    event_type: WebhookEventType
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Context
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None

    # Payload
    data: Dict[str, Any] = Field(default_factory=dict)

    # Metadata
    source: str = "contex"
    version: str = "1.0"


class WebhookDelivery(BaseModel):
    """Record of a webhook delivery attempt"""
    delivery_id: str
    event_id: str
    endpoint_id: str

    # Delivery info
    attempt: int = 1
    status: str = "pending"  # pending, success, failed, retrying
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error: Optional[str] = None

    # Timing
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    delivered_at: Optional[str] = None
    duration_ms: Optional[float] = None


# ============================================================================
# WEBHOOK MANAGER
# ============================================================================

class WebhookManager:
    """
    Manages webhook endpoints and delivery.

    Features:
    - Endpoint CRUD operations
    - Event filtering by type, category, project
    - HMAC signature for security
    - Reliable delivery with retries
    - Delivery logging
    """

    MAX_DELIVERY_LOG_SIZE = 100  # Per endpoint

    def __init__(self, db: DatabaseManager, default_timeout: int = 30, max_retries: int = 3):
        """
        Initialize webhook manager.

        Args:
            db: Database manager
            default_timeout: Default request timeout in seconds
            max_retries: Default max retry attempts
        """
        self.db = db
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.default_timeout)
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ========================================
    # Endpoint Management
    # ========================================

    async def create_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        """Create a new webhook endpoint"""
        async with self.db.session() as session:
            record = WebhookEndpointModel(
                endpoint_id=endpoint.endpoint_id,
                tenant_id=endpoint.tenant_id,
                url=endpoint.url,
                secret=endpoint.secret,
                events=endpoint.events,
                categories=endpoint.categories,
                project_ids=endpoint.project_ids,
                is_active=endpoint.is_active,
                timeout_seconds=endpoint.timeout_seconds,
                max_retries=endpoint.max_retries,
                name=endpoint.name,
                description=endpoint.description,
            )
            session.add(record)

        logger.info("Webhook endpoint created",
                   endpoint_id=endpoint.endpoint_id,
                   url=endpoint.url,
                   events=len(endpoint.events))

        return endpoint

    async def get_endpoint(self, endpoint_id: str) -> Optional[WebhookEndpoint]:
        """Get endpoint by ID"""
        async with self.db.session() as session:
            result = await session.execute(
                select(WebhookEndpointModel).where(WebhookEndpointModel.endpoint_id == endpoint_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return self._record_to_endpoint(record)

    async def update_endpoint(
        self,
        endpoint_id: str,
        **updates
    ) -> Optional[WebhookEndpoint]:
        """Update an endpoint"""
        async with self.db.session() as session:
            result = await session.execute(
                select(WebhookEndpointModel).where(WebhookEndpointModel.endpoint_id == endpoint_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            for key, value in updates.items():
                if hasattr(record, key) and value is not None:
                    setattr(record, key, value)

            record.updated_at = datetime.now(UTC)

            return self._record_to_endpoint(record)

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        """Delete an endpoint"""
        async with self.db.session() as session:
            # Delete deliveries first
            await session.execute(
                delete(WebhookDeliveryModel).where(WebhookDeliveryModel.endpoint_id == endpoint_id)
            )

            # Delete endpoint
            result = await session.execute(
                delete(WebhookEndpointModel).where(WebhookEndpointModel.endpoint_id == endpoint_id)
            )

            if result.rowcount > 0:
                logger.warning("Webhook endpoint deleted", endpoint_id=endpoint_id)
                return True

        return False

    async def list_endpoints(
        self,
        tenant_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> List[WebhookEndpoint]:
        """List all endpoints with optional filtering"""
        async with self.db.session() as session:
            query = select(WebhookEndpointModel)

            if tenant_id:
                query = query.where(WebhookEndpointModel.tenant_id == tenant_id)
            if is_active is not None:
                query = query.where(WebhookEndpointModel.is_active == is_active)

            result = await session.execute(query)
            records = result.scalars().all()

            return [self._record_to_endpoint(r) for r in records]

    # ========================================
    # Event Delivery
    # ========================================

    async def emit_event(
        self,
        event_type: WebhookEventType,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> str:
        """
        Emit a webhook event to all matching endpoints.

        Args:
            event_type: Type of event
            data: Event payload
            tenant_id: Tenant context
            project_id: Project context

        Returns:
            Event ID
        """
        event = WebhookEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            tenant_id=tenant_id,
            project_id=project_id,
            data=data,
        )

        # Find matching endpoints
        endpoints = await self._get_matching_endpoints(event)

        if not endpoints:
            logger.debug("No matching endpoints for event",
                        event_type=event_type.value,
                        event_id=event.event_id)
            return event.event_id

        # Deliver to each endpoint (fire and forget)
        for endpoint in endpoints:
            asyncio.create_task(
                self._deliver_event(event, endpoint)
            )

        logger.info("Webhook event emitted",
                   event_type=event_type.value,
                   event_id=event.event_id,
                   endpoints_count=len(endpoints))

        return event.event_id

    async def _get_matching_endpoints(
        self,
        event: WebhookEvent
    ) -> List[WebhookEndpoint]:
        """Get endpoints that should receive this event"""
        endpoints = await self.list_endpoints(is_active=True)
        matching = []

        event_category = EVENT_CATALOG.get(event.event_type, {}).get("category")
        event_category_value = event_category.value if event_category else None

        for endpoint in endpoints:
            # Check tenant match
            if endpoint.tenant_id and endpoint.tenant_id != event.tenant_id:
                continue

            # Check event type filter
            if endpoint.events and event.event_type.value not in endpoint.events:
                continue

            # Check category filter
            if endpoint.categories and event_category_value not in endpoint.categories:
                continue

            # Check project filter
            if endpoint.project_ids and event.project_id not in endpoint.project_ids:
                continue

            matching.append(endpoint)

        return matching

    async def _deliver_event(
        self,
        event: WebhookEvent,
        endpoint: WebhookEndpoint,
        attempt: int = 1,
    ):
        """Deliver event to an endpoint with retries"""
        delivery = WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            event_id=event.event_id,
            endpoint_id=endpoint.endpoint_id,
            attempt=attempt,
        )

        # Prepare payload
        payload = event.model_dump()
        payload_json = json.dumps(payload, sort_keys=True, default=str)

        # Generate HMAC signature
        signature = self._generate_signature(payload_json, endpoint.secret)

        headers = {
            "Content-Type": "application/json",
            "X-Contex-Event": event.event_type.value,
            "X-Contex-Signature": signature,
            "X-Contex-Timestamp": event.timestamp,
            "X-Contex-Delivery-ID": delivery.delivery_id,
        }

        start_time = time.time()

        try:
            client = await self._get_client()
            response = await client.post(
                endpoint.url,
                content=payload_json,
                headers=headers,
                timeout=endpoint.timeout_seconds,
            )

            duration = (time.time() - start_time) * 1000

            delivery.status_code = response.status_code
            delivery.duration_ms = duration
            delivery.delivered_at = datetime.now(UTC).isoformat()

            if 200 <= response.status_code < 300:
                delivery.status = "success"
                logger.info("Webhook delivered",
                           endpoint_id=endpoint.endpoint_id,
                           event_id=event.event_id,
                           status_code=response.status_code,
                           duration_ms=round(duration, 2))
            else:
                delivery.status = "failed"
                delivery.response_body = response.text[:500]  # Limit response size
                logger.warning("Webhook delivery failed",
                              endpoint_id=endpoint.endpoint_id,
                              event_id=event.event_id,
                              status_code=response.status_code)

                # Retry if appropriate
                if attempt < endpoint.max_retries:
                    delivery.status = "retrying"
                    await self._schedule_retry(event, endpoint, attempt)

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            delivery.status = "failed"
            delivery.error = str(e)
            delivery.duration_ms = duration

            logger.error("Webhook delivery error",
                        endpoint_id=endpoint.endpoint_id,
                        event_id=event.event_id,
                        error=str(e))

            # Retry on errors
            if attempt < endpoint.max_retries:
                delivery.status = "retrying"
                await self._schedule_retry(event, endpoint, attempt)

        # Log delivery
        await self._log_delivery(delivery)

    async def _schedule_retry(
        self,
        event: WebhookEvent,
        endpoint: WebhookEndpoint,
        current_attempt: int,
    ):
        """Schedule a retry with exponential backoff"""
        delay = min(60, 2 ** current_attempt)  # Max 60 seconds

        await asyncio.sleep(delay)
        await self._deliver_event(event, endpoint, current_attempt + 1)

    def _generate_signature(self, payload: str, secret: str) -> str:
        """Generate HMAC-SHA256 signature"""
        return hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

    async def _log_delivery(self, delivery: WebhookDelivery):
        """Log delivery attempt"""
        async with self.db.session() as session:
            record = WebhookDeliveryModel(
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
                endpoint_id=delivery.endpoint_id,
                attempt=delivery.attempt,
                status=delivery.status,
                status_code=delivery.status_code,
                response_body=delivery.response_body,
                error=delivery.error,
                delivered_at=datetime.fromisoformat(delivery.delivered_at) if delivery.delivered_at else None,
                duration_ms=delivery.duration_ms,
            )
            session.add(record)

    async def get_delivery_log(
        self,
        endpoint_id: str,
        limit: int = 50,
    ) -> List[WebhookDelivery]:
        """Get delivery log for an endpoint"""
        async with self.db.session() as session:
            result = await session.execute(
                select(WebhookDeliveryModel)
                .where(WebhookDeliveryModel.endpoint_id == endpoint_id)
                .order_by(WebhookDeliveryModel.created_at.desc())
                .limit(limit)
            )
            records = result.scalars().all()

            return [
                WebhookDelivery(
                    delivery_id=r.delivery_id,
                    event_id=r.event_id,
                    endpoint_id=r.endpoint_id,
                    attempt=r.attempt,
                    status=r.status,
                    status_code=r.status_code,
                    response_body=r.response_body,
                    error=r.error,
                    created_at=r.created_at.isoformat() if r.created_at else "",
                    delivered_at=r.delivered_at.isoformat() if r.delivered_at else None,
                    duration_ms=r.duration_ms,
                )
                for r in records
            ]

    # ========================================
    # Event Catalog
    # ========================================

    def get_event_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Get the full event catalog"""
        catalog = {}

        for event_type, info in EVENT_CATALOG.items():
            catalog[event_type.value] = {
                "event_type": event_type.value,
                "category": info["category"].value,
                "description": info["description"],
                "payload_schema": info["payload_schema"],
            }

        return catalog

    def get_events_by_category(
        self,
        category: WebhookEventCategory
    ) -> List[Dict[str, Any]]:
        """Get events filtered by category"""
        events = []

        for event_type, info in EVENT_CATALOG.items():
            if info["category"] == category:
                events.append({
                    "event_type": event_type.value,
                    "description": info["description"],
                    "payload_schema": info["payload_schema"],
                })

        return events

    def _record_to_endpoint(self, record: WebhookEndpointModel) -> WebhookEndpoint:
        """Convert database record to Pydantic model"""
        return WebhookEndpoint(
            endpoint_id=record.endpoint_id,
            tenant_id=record.tenant_id,
            url=record.url,
            secret=record.secret,
            events=record.events or [],
            categories=record.categories or [],
            project_ids=record.project_ids or [],
            is_active=record.is_active,
            timeout_seconds=record.timeout_seconds,
            max_retries=record.max_retries,
            name=record.name,
            description=record.description,
            created_at=record.created_at.isoformat() if record.created_at else "",
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
        )


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_webhook_manager: Optional[WebhookManager] = None


def init_webhook_manager(
    db: DatabaseManager,
    default_timeout: int = 30,
    max_retries: int = 3,
) -> WebhookManager:
    """Initialize global webhook manager"""
    global _webhook_manager
    _webhook_manager = WebhookManager(db, default_timeout, max_retries)
    return _webhook_manager


def get_webhook_manager() -> Optional[WebhookManager]:
    """Get global webhook manager"""
    return _webhook_manager


async def emit_webhook(
    event_type: WebhookEventType,
    data: Dict[str, Any],
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """
    Convenience function to emit a webhook event.

    Returns event ID if manager is initialized, None otherwise.
    """
    manager = get_webhook_manager()
    if not manager:
        return None

    return await manager.emit_event(
        event_type=event_type,
        data=data,
        tenant_id=tenant_id,
        project_id=project_id,
    )
