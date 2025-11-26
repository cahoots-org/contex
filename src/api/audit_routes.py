"""API routes for audit log access"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field

from src.core.audit import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    AuditEventSeverity,
    get_audit_logger,
)
from src.core.rbac import Role
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


# ============================================================
# Response Models
# ============================================================

class AuditEventResponse(BaseModel):
    """Audit event response"""
    event_id: str
    timestamp: str
    event_type: str
    severity: str
    actor_id: Optional[str]
    actor_type: Optional[str]
    actor_ip: Optional[str]
    tenant_id: Optional[str]
    project_id: Optional[str]
    resource_type: Optional[str]
    resource_id: Optional[str]
    action: str
    details: Dict[str, Any]
    result: str
    request_id: Optional[str]
    endpoint: Optional[str]
    method: Optional[str]


class AuditLogResponse(BaseModel):
    """Audit log query response"""
    events: List[AuditEventResponse]
    total: int
    limit: int
    offset: int


class AuditExportResponse(BaseModel):
    """Audit export response"""
    events: List[Dict[str, Any]]
    export_timestamp: str
    filters: Dict[str, Any]
    total_events: int


# ============================================================
# Helper Functions
# ============================================================

async def require_admin_permission(request: Request):
    """Require admin role for audit access"""
    role = getattr(request.state, 'api_key_role', None)
    if role is None:
        logger.warning("No RBAC context for audit access")
        return
    if role.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin role required to access audit logs"
        )


def get_audit_logger_dependency(request: Request) -> AuditLogger:
    """Get audit logger or raise if not available"""
    audit_logger = get_audit_logger()
    if not audit_logger:
        raise HTTPException(
            status_code=503,
            detail="Audit logging is not enabled"
        )
    return audit_logger


def event_to_response(event: AuditEvent) -> AuditEventResponse:
    """Convert AuditEvent to response model"""
    return AuditEventResponse(
        event_id=event.event_id,
        timestamp=event.timestamp,
        event_type=event.event_type.value,
        severity=event.severity.value,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        actor_ip=event.actor_ip,
        tenant_id=event.tenant_id,
        project_id=event.project_id,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        action=event.action,
        details=event.details,
        result=event.result,
        request_id=event.request_id,
        endpoint=event.endpoint,
        method=event.method,
    )


# ============================================================
# Endpoints
# ============================================================

@router.get("/events", response_model=AuditLogResponse)
async def query_audit_events(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    actor_id: Optional[str] = Query(None, description="Filter by actor (API key) ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    start_time: Optional[datetime] = Query(None, description="Start of time range (ISO 8601)"),
    end_time: Optional[datetime] = Query(None, description="End of time range (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Skip first N results"),
    _: None = Depends(require_admin_permission),
):
    """
    Query audit events with filtering.

    Requires admin role.

    Filter options:
    - tenant_id: Filter by specific tenant
    - actor_id: Filter by specific actor (API key)
    - event_type: Filter by event type (e.g., 'data.published', 'auth.login.success')
    - start_time: Start of time range
    - end_time: End of time range

    Returns paginated list of audit events, newest first.
    """
    audit_logger = get_audit_logger_dependency(request)

    # Parse event_type if provided
    parsed_event_type = None
    if event_type:
        try:
            parsed_event_type = AuditEventType(event_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event_type: {event_type}. Valid types: {[e.value for e in AuditEventType]}"
            )

    events = await audit_logger.query_events(
        tenant_id=tenant_id,
        actor_id=actor_id,
        event_type=parsed_event_type,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )

    return AuditLogResponse(
        events=[event_to_response(e) for e in events],
        total=len(events),
        limit=limit,
        offset=offset,
    )


@router.get("/events/{event_id}", response_model=AuditEventResponse)
async def get_audit_event(
    request: Request,
    event_id: str,
    _: None = Depends(require_admin_permission),
):
    """
    Get a single audit event by ID.

    Requires admin role.
    """
    audit_logger = get_audit_logger_dependency(request)

    event = await audit_logger.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Audit event '{event_id}' not found")

    return event_to_response(event)


@router.get("/events/types", response_model=List[str])
async def list_event_types(
    _: None = Depends(require_admin_permission),
):
    """
    List all available audit event types.

    Requires admin role.
    """
    return [e.value for e in AuditEventType]


@router.get("/export", response_model=AuditExportResponse)
async def export_audit_events(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    start_time: Optional[datetime] = Query(None, description="Start of time range"),
    end_time: Optional[datetime] = Query(None, description="End of time range"),
    days: int = Query(30, ge=1, le=365, description="Number of days to export (if no start_time)"),
    _: None = Depends(require_admin_permission),
):
    """
    Export audit events for compliance reporting.

    Requires admin role.

    This endpoint returns a complete export of audit events for the specified
    time range. Use this for compliance reports, SIEM integration, or backups.

    If start_time is not provided, exports events from the last N days.
    Maximum export is 10,000 events.
    """
    audit_logger = get_audit_logger_dependency(request)

    # Default time range if not specified
    if not start_time:
        start_time = datetime.utcnow() - timedelta(days=days)
    if not end_time:
        end_time = datetime.utcnow()

    events = await audit_logger.export_events(
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
    )

    filters = {
        "tenant_id": tenant_id,
        "start_time": start_time.isoformat() if start_time else None,
        "end_time": end_time.isoformat() if end_time else None,
    }

    logger.info("Audit events exported",
               tenant_id=tenant_id,
               event_count=len(events),
               start_time=filters['start_time'],
               end_time=filters['end_time'])

    return AuditExportResponse(
        events=events,
        export_timestamp=datetime.utcnow().isoformat(),
        filters=filters,
        total_events=len(events),
    )


@router.get("/summary")
async def get_audit_summary(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    days: int = Query(7, ge=1, le=90, description="Number of days for summary"),
    _: None = Depends(require_admin_permission),
):
    """
    Get audit event summary statistics.

    Requires admin role.

    Returns counts of events by type and severity for the specified time range.
    """
    audit_logger = get_audit_logger_dependency(request)

    start_time = datetime.utcnow() - timedelta(days=days)
    events = await audit_logger.query_events(
        tenant_id=tenant_id,
        start_time=start_time,
        limit=10000,
    )

    # Count by type
    type_counts = {}
    severity_counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    result_counts = {"success": 0, "failure": 0, "partial": 0}

    for event in events:
        event_type = event.event_type.value
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        severity_counts[event.severity.value] = severity_counts.get(event.severity.value, 0) + 1
        result_counts[event.result] = result_counts.get(event.result, 0) + 1

    return {
        "period_days": days,
        "total_events": len(events),
        "events_by_type": type_counts,
        "events_by_severity": severity_counts,
        "events_by_result": result_counts,
        "start_time": start_time.isoformat(),
        "end_time": datetime.utcnow().isoformat(),
    }
