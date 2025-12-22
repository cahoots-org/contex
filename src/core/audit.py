"""Audit logging for Contex

Provides comprehensive audit logging for all state-changing operations,
authentication events, and authorization decisions. Critical for:
- Compliance (SOC 2, GDPR, HIPAA)
- Security incident investigation
- Change tracking and accountability
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, select

from src.core.database import DatabaseManager
from src.core.db_models import AuditEvent as AuditEventModel
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

    Events are stored in PostgreSQL in the audit_events table.

    Retention: Events are retained based on configuration (default 90 days).
    """

    def __init__(
        self,
        db: DatabaseManager,
        retention_days: int = 90,
    ):
        """
        Initialize audit logger.

        Args:
            db: Database manager
            retention_days: How long to retain audit events
        """
        self.db = db
        self.retention_days = retention_days

    async def log(self, event: AuditEvent) -> str:
        """
        Log an audit event.

        Args:
            event: AuditEvent to log

        Returns:
            Event ID
        """
        async with self.db.session() as session:
            # Create audit event record
            record = AuditEventModel(
                event_id=event.event_id,
                timestamp=datetime.fromisoformat(event.timestamp) if isinstance(event.timestamp, str) else event.timestamp,
                event_type=event.event_type.value,
                severity=event.severity.value,
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                actor_ip=event.actor_ip,
                actor_user_agent=event.actor_user_agent,
                tenant_id=event.tenant_id,
                project_id=event.project_id,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                action=event.action,
                details=event.details or {},
                result=event.result,
                request_id=event.request_id,
                endpoint=event.endpoint,
                method=event.method,
                before_state=event.before,
                after_state=event.after,
            )
            session.add(record)

        # Log to structured logging as well
        logger.info("Audit event recorded",
                   event_id=event.event_id,
                   event_type=event.event_type.value,
                   severity=event.severity.value,
                   action=event.action,
                   tenant_id=event.tenant_id,
                   actor_id=event.actor_id)

        return event.event_id

    async def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """
        Get a single audit event by ID.

        Args:
            event_id: Event identifier

        Returns:
            AuditEvent if found
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(AuditEventModel).where(AuditEventModel.event_id == event_id)
            )
            record = result.scalar_one_or_none()

            if not record:
                return None

            return self._record_to_model(record)

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
        async with self.db.session() as session:
            query = select(AuditEventModel)

            # Apply filters
            if tenant_id:
                query = query.where(AuditEventModel.tenant_id == tenant_id)
            if actor_id:
                query = query.where(AuditEventModel.actor_id == actor_id)
            if event_type:
                query = query.where(AuditEventModel.event_type == event_type.value)
            if start_time:
                query = query.where(AuditEventModel.timestamp >= start_time)
            if end_time:
                query = query.where(AuditEventModel.timestamp <= end_time)

            # Order by timestamp descending (newest first)
            query = query.order_by(AuditEventModel.timestamp.desc())
            query = query.offset(offset).limit(limit)

            result = await session.execute(query)
            records = result.scalars().all()

            return [self._record_to_model(r) for r in records]

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

    async def cleanup_old_events(self) -> int:
        """
        Delete audit events older than retention period.

        Returns:
            Number of deleted events
        """
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)

        async with self.db.session() as session:
            result = await session.execute(
                delete(AuditEventModel).where(AuditEventModel.timestamp < cutoff)
            )
            deleted_count = result.rowcount

            if deleted_count > 0:
                logger.info("Cleaned up old audit events",
                           deleted_count=deleted_count,
                           retention_days=self.retention_days)

            return deleted_count

    def _record_to_model(self, record: AuditEventModel) -> AuditEvent:
        """Convert database record to Pydantic model"""
        return AuditEvent(
            event_id=record.event_id,
            timestamp=record.timestamp.isoformat() if record.timestamp else "",
            event_type=AuditEventType(record.event_type),
            severity=AuditEventSeverity(record.severity),
            actor_id=record.actor_id,
            actor_type=record.actor_type,
            actor_ip=record.actor_ip,
            actor_user_agent=record.actor_user_agent,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            action=record.action,
            details=record.details or {},
            result=record.result,
            request_id=record.request_id,
            endpoint=record.endpoint,
            method=record.method,
            before=record.before_state,
            after=record.after_state,
        )


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def init_audit_logger(db: DatabaseManager, retention_days: int = 90) -> AuditLogger:
    """Initialize global audit logger"""
    global _audit_logger
    _audit_logger = AuditLogger(db, retention_days=retention_days)
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
