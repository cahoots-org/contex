"""Audit logging for Contex

Provides comprehensive audit logging for all state-changing operations,
authentication events, and authorization decisions. Critical for:
- Compliance (SOC 2, GDPR, HIPAA)
- Security incident investigation
- Change tracking and accountability
"""

import json
import uuid
from datetime import datetime, UTC
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from src.core.logging import get_logger

logger = get_logger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events"""
    # Authentication events
    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILURE = "auth.login.failure"
    AUTH_LOGOUT = "auth.logout"
    AUTH_API_KEY_CREATED = "auth.api_key.created"
    AUTH_API_KEY_REVOKED = "auth.api_key.revoked"
    AUTH_API_KEY_USED = "auth.api_key.used"

    # Authorization events
    AUTHZ_PERMISSION_GRANTED = "authz.permission.granted"
    AUTHZ_PERMISSION_DENIED = "authz.permission.denied"
    AUTHZ_ROLE_ASSIGNED = "authz.role.assigned"
    AUTHZ_ROLE_REVOKED = "authz.role.revoked"

    # Data operations
    DATA_PUBLISHED = "data.published"
    DATA_UPDATED = "data.updated"
    DATA_DELETED = "data.deleted"
    DATA_EXPORTED = "data.exported"
    DATA_IMPORTED = "data.imported"

    # Agent operations
    AGENT_REGISTERED = "agent.registered"
    AGENT_UNREGISTERED = "agent.unregistered"
    AGENT_UPDATED = "agent.updated"

    # Project operations
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"

    # Tenant operations
    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_DELETED = "tenant.deleted"
    TENANT_SUSPENDED = "tenant.suspended"
    TENANT_REACTIVATED = "tenant.reactivated"

    # Configuration changes
    CONFIG_UPDATED = "config.updated"
    QUOTA_CHANGED = "quota.changed"
    RATE_LIMIT_CHANGED = "rate_limit.changed"

    # Security events
    SECURITY_RATE_LIMITED = "security.rate_limited"
    SECURITY_QUOTA_EXCEEDED = "security.quota_exceeded"
    SECURITY_INVALID_ACCESS = "security.invalid_access"
    SECURITY_SUSPICIOUS_ACTIVITY = "security.suspicious_activity"


class AuditEventSeverity(str, Enum):
    """Severity levels for audit events"""
    INFO = "info"          # Normal operations
    WARNING = "warning"    # Potential issues
    ERROR = "error"        # Errors or failures
    CRITICAL = "critical"  # Security-critical events


class AuditEvent(BaseModel):
    """Audit event model"""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_type: AuditEventType
    severity: AuditEventSeverity = AuditEventSeverity.INFO

    # Actor information
    actor_id: Optional[str] = None          # API key ID or user ID
    actor_type: Optional[str] = None        # 'api_key', 'user', 'system', 'service_account'
    actor_ip: Optional[str] = None          # IP address
    actor_user_agent: Optional[str] = None  # User agent string

    # Context
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    resource_type: Optional[str] = None     # 'data', 'agent', 'project', etc.
    resource_id: Optional[str] = None       # ID of the affected resource

    # Event details
    action: str                              # Human-readable action description
    details: Dict[str, Any] = Field(default_factory=dict)
    result: str = "success"                  # 'success', 'failure', 'partial'

    # Request context
    request_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None

    # Diff for updates (before/after)
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


class AuditLogger:
    """
    Audit logger for recording and querying audit events.

    Events are stored in Redis with the following structure:
    - audit:events:{event_id} - Individual event (hash)
    - audit:stream - Event stream for real-time processing
    - audit:index:tenant:{tenant_id} - Index by tenant
    - audit:index:actor:{actor_id} - Index by actor
    - audit:index:type:{event_type} - Index by type
    - audit:index:date:{date} - Index by date

    Retention: Events are retained based on configuration (default 90 days).
    """

    def __init__(
        self,
        redis: Redis,
        retention_days: int = 90,
        max_stream_length: int = 100000,
    ):
        """
        Initialize audit logger.

        Args:
            redis: Redis connection
            retention_days: How long to retain audit events
            max_stream_length: Maximum events in the stream
        """
        self.redis = redis
        self.retention_days = retention_days
        self.max_stream_length = max_stream_length
        self.retention_seconds = retention_days * 24 * 60 * 60

    async def log(self, event: AuditEvent) -> str:
        """
        Log an audit event.

        Args:
            event: AuditEvent to log

        Returns:
            Event ID
        """
        event_data = event.model_dump()

        # Serialize complex fields
        event_data['details'] = json.dumps(event_data['details'])
        if event_data['before']:
            event_data['before'] = json.dumps(event_data['before'])
        if event_data['after']:
            event_data['after'] = json.dumps(event_data['after'])

        # Remove None values (Redis doesn't accept None)
        event_data = {k: v for k, v in event_data.items() if v is not None}

        # Store event
        event_key = f"audit:events:{event.event_id}"
        await self.redis.hset(event_key, mapping=event_data)
        await self.redis.expire(event_key, self.retention_seconds)

        # Add to stream for real-time processing
        await self.redis.xadd(
            "audit:stream",
            {"event_id": event.event_id, "type": event.event_type.value},
            maxlen=self.max_stream_length,
            approximate=True,
        )

        # Add to indices
        if event.tenant_id:
            await self._add_to_index(f"audit:index:tenant:{event.tenant_id}", event.event_id)
        if event.actor_id:
            await self._add_to_index(f"audit:index:actor:{event.actor_id}", event.event_id)
        await self._add_to_index(f"audit:index:type:{event.event_type.value}", event.event_id)

        date_str = event.timestamp[:10]  # YYYY-MM-DD
        await self._add_to_index(f"audit:index:date:{date_str}", event.event_id)

        # Log to structured logging as well
        logger.info("Audit event recorded",
                   event_id=event.event_id,
                   event_type=event.event_type.value,
                   severity=event.severity.value,
                   action=event.action,
                   tenant_id=event.tenant_id,
                   actor_id=event.actor_id)

        return event.event_id

    async def _add_to_index(self, index_key: str, event_id: str):
        """Add event to an index"""
        await self.redis.zadd(
            index_key,
            {event_id: datetime.now(UTC).timestamp()},
        )
        await self.redis.expire(index_key, self.retention_seconds)

    async def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """
        Get a single audit event by ID.

        Args:
            event_id: Event identifier

        Returns:
            AuditEvent if found
        """
        data = await self.redis.hgetall(f"audit:events:{event_id}")
        if not data:
            return None

        return self._parse_event(data)

    async def query_events(
        self,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEvent]:
        """
        Query audit events with filtering.

        Args:
            tenant_id: Filter by tenant
            actor_id: Filter by actor
            event_type: Filter by event type
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum results
            offset: Skip first N results

        Returns:
            List of AuditEvent objects
        """
        # Determine which index to use
        if tenant_id:
            index_key = f"audit:index:tenant:{tenant_id}"
        elif actor_id:
            index_key = f"audit:index:actor:{actor_id}"
        elif event_type:
            index_key = f"audit:index:type:{event_type.value}"
        else:
            # No filter, use date index for recent events
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            index_key = f"audit:index:date:{date_str}"

        # Get event IDs from index (sorted by timestamp, newest first)
        min_score = start_time.timestamp() if start_time else "-inf"
        max_score = end_time.timestamp() if end_time else "+inf"

        event_ids = await self.redis.zrevrangebyscore(
            index_key,
            max=max_score,
            min=min_score,
            start=offset,
            num=limit,
        )

        # Fetch events
        events = []
        for event_id in event_ids:
            if isinstance(event_id, bytes):
                event_id = event_id.decode()
            event = await self.get_event(event_id)
            if event:
                # Apply additional filters
                if tenant_id and event.tenant_id != tenant_id:
                    continue
                if actor_id and event.actor_id != actor_id:
                    continue
                if event_type and event.event_type != event_type:
                    continue
                events.append(event)

        return events

    def _parse_event(self, data: Dict[bytes, bytes]) -> AuditEvent:
        """Parse event data from Redis"""
        decoded = {k.decode(): v.decode() for k, v in data.items()}

        # Parse JSON fields
        decoded['details'] = json.loads(decoded.get('details', '{}'))
        if decoded.get('before'):
            decoded['before'] = json.loads(decoded['before'])
        if decoded.get('after'):
            decoded['after'] = json.loads(decoded['after'])

        return AuditEvent(**decoded)

    async def export_events(
        self,
        tenant_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Export audit events for compliance reporting.

        Args:
            tenant_id: Filter by tenant
            start_time: Start of time range
            end_time: End of time range

        Returns:
            List of event dictionaries
        """
        events = await self.query_events(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        return [event.model_dump() for event in events]


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def init_audit_logger(redis: Redis, retention_days: int = 90) -> AuditLogger:
    """Initialize global audit logger"""
    global _audit_logger
    _audit_logger = AuditLogger(redis, retention_days=retention_days)
    return _audit_logger


def get_audit_logger() -> Optional[AuditLogger]:
    """Get global audit logger instance"""
    return _audit_logger


async def audit_log(
    event_type: AuditEventType,
    action: str,
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_ip: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    result: str = "success",
    severity: AuditEventSeverity = AuditEventSeverity.INFO,
    request_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Convenience function to log an audit event.

    Returns:
        Event ID if logged, None if audit logger not initialized
    """
    if not _audit_logger:
        logger.warning("Audit logger not initialized, event not recorded",
                      event_type=event_type.value,
                      action=action)
        return None

    event = AuditEvent(
        event_type=event_type,
        action=action,
        severity=severity,
        tenant_id=tenant_id,
        project_id=project_id,
        actor_id=actor_id,
        actor_type=actor_type,
        actor_ip=actor_ip,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        result=result,
        request_id=request_id,
        endpoint=endpoint,
        method=method,
        before=before,
        after=after,
    )

    return await _audit_logger.log(event)


# ============================================================================
# AUDIT DECORATORS
# ============================================================================

def audited(
    event_type: AuditEventType,
    action: str,
    resource_type: Optional[str] = None,
):
    """
    Decorator to automatically audit a function call.

    Usage:
        @audited(AuditEventType.DATA_PUBLISHED, "Published data", resource_type="data")
        async def publish_data(project_id: str, data_key: str, data: dict):
            ...

    The decorator will automatically extract tenant_id, project_id, and other
    context from the function arguments or request state.
    """
    from functools import wraps

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract context from kwargs
            tenant_id = kwargs.get('tenant_id')
            project_id = kwargs.get('project_id')
            resource_id = kwargs.get('data_key') or kwargs.get('agent_id') or kwargs.get('resource_id')

            # Extract from request if available
            request = kwargs.get('request')
            actor_id = None
            actor_ip = None
            request_id = None

            if request:
                actor_id = getattr(request.state, 'api_key_id', None)
                tenant_id = tenant_id or getattr(request.state, 'tenant_id', None)
                request_id = getattr(request.state, 'request_id', None)
                if hasattr(request, 'client') and request.client:
                    actor_ip = request.client.host

            try:
                result = await func(*args, **kwargs)

                # Log success
                await audit_log(
                    event_type=event_type,
                    action=action,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    actor_type="api_key" if actor_id else None,
                    actor_ip=actor_ip,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    result="success",
                    request_id=request_id,
                )

                return result

            except Exception as e:
                # Log failure
                await audit_log(
                    event_type=event_type,
                    action=f"{action} (failed)",
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    actor_type="api_key" if actor_id else None,
                    actor_ip=actor_ip,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    result="failure",
                    severity=AuditEventSeverity.ERROR,
                    details={"error": str(e)},
                    request_id=request_id,
                )
                raise

        return wrapper
    return decorator
