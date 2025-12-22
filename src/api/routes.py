"""REST API routes for Contex"""

from fastapi import APIRouter, HTTPException, Request
from src.core.models import (
    AgentRegistration,
    DataPublishEvent,
    RegistrationResponse,
    QueryRequest,
    QueryResponse,
    QueryResponse,
    MatchedDataSource,
)
from src.core.auth import create_api_key, revoke_api_key, list_api_keys, APIKey
from src.core.logging import get_logger
from src.core.audit import (
    audit_log,
    AuditEventType,
    AuditEventSeverity,
)
from src.core.webhooks import emit_webhook, WebhookEventType
from typing import List

router = APIRouter()
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


@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Contex",
        "status": "running",
        "version": "0.2.0"
    }


@router.get("/health")
async def health(request: Request):
    """Comprehensive health check endpoint"""
    from src.core.health import HealthChecker
    
    # Get health checker from app state (will be initialized in main.py)
    if hasattr(request.app.state, 'health_checker'):
        health_data = await request.app.state.health_checker.get_full_health()
        
        # Return 503 if unhealthy
        status_code = 200 if health_data["status"] != "unhealthy" else 503
        
        from fastapi.responses import JSONResponse
        return JSONResponse(content=health_data, status_code=status_code)
    else:
        # Fallback if health checker not initialized
        return {"status": "healthy"}


@router.get("/health/ready")
async def readiness(request: Request):
    """Readiness check for Kubernetes"""
    from src.core.health import HealthChecker
    from fastapi.responses import JSONResponse
    
    if hasattr(request.app.state, 'health_checker'):
        readiness_data = await request.app.state.health_checker.get_readiness()
        status_code = 200 if readiness_data["ready"] else 503
        return JSONResponse(content=readiness_data, status_code=status_code)
    else:
        return {"ready": True}


@router.get("/health/live")
async def liveness(request: Request):
    """Liveness check for Kubernetes"""
    from src.core.health import HealthChecker
    
    if hasattr(request.app.state, 'health_checker'):
        liveness_data = await request.app.state.health_checker.get_liveness()
        return liveness_data
    else:
        return {"alive": True}


@router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from fastapi.responses import Response
    from src.core.metrics import get_metrics
    
    metrics_output = get_metrics()
    return Response(content=metrics_output, media_type="text/plain; version=0.0.4")


@router.post("/auth/keys", response_model=dict)
async def create_key(name: str, request: Request):
    """Create a new API key"""
    ctx = _get_request_context(request)
    try:
        db = request.app.state.db
        raw_key, api_key = await create_api_key(db, name)

        # Audit log API key creation
        await audit_log(
            event_type=AuditEventType.AUTH_API_KEY_CREATED,
            action=f"Created API key '{name}'",
            resource_type="api_key",
            resource_id=api_key.key_id,
            details={"key_name": name},
            **ctx
        )

        return {
            "api_key": raw_key,
            "key_id": api_key.key_id,
            "name": api_key.name
        }
    except Exception as e:
        await audit_log(
            event_type=AuditEventType.AUTH_API_KEY_CREATED,
            action=f"Failed to create API key '{name}'",
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"key_name": name, "error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/keys", response_model=List[APIKey])
async def list_keys(request: Request):
    """List all API keys"""
    try:
        db = request.app.state.db
        return await list_api_keys(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/keys/{key_id}")
async def revoke_key(key_id: str, request: Request):
    """Revoke an API key"""
    ctx = _get_request_context(request)
    try:
        db = request.app.state.db
        success = await revoke_api_key(db, key_id)
        if not success:
            await audit_log(
                event_type=AuditEventType.AUTH_API_KEY_REVOKED,
                action=f"Failed to revoke API key (not found)",
                resource_type="api_key",
                resource_id=key_id,
                result="failure",
                severity=AuditEventSeverity.WARNING,
                **ctx
            )
            raise HTTPException(status_code=404, detail="Key not found")

        # Audit log successful revocation
        await audit_log(
            event_type=AuditEventType.AUTH_API_KEY_REVOKED,
            action=f"Revoked API key",
            resource_type="api_key",
            resource_id=key_id,
            **ctx
        )

        return {"status": "revoked", "key_id": key_id}
    except HTTPException:
        raise
    except Exception as e:
        await audit_log(
            event_type=AuditEventType.AUTH_API_KEY_REVOKED,
            action=f"Failed to revoke API key",
            resource_type="api_key",
            resource_id=key_id,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/rate-limits")
async def get_rate_limits(request: Request):
    """Get current rate limit status for the authenticated API key"""
    try:
        from src.core.rate_limiter import get_rate_limit_status
        db = request.app.state.db
        api_key = request.headers.get("X-API-Key", "anonymous")

        status = await get_rate_limit_status(db, api_key)
        return {
            "api_key_prefix": api_key[:7] if len(api_key) > 7 else api_key,
            "limits": status
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/roles")
async def assign_role_endpoint(
    key_id: str,
    role: str,
    projects: List[str] = None,
    request: Request = None
):
    """Assign a role to an API key"""
    ctx = _get_request_context(request)
    try:
        from src.core.rbac import assign_role, Role
        db = request.app.state.db

        # Validate role
        try:
            role_enum = Role(role)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {[r.value for r in Role]}"
            )

        role_assignment = await assign_role(db, key_id, role_enum, projects)

        # Audit log role assignment
        await audit_log(
            event_type=AuditEventType.AUTHZ_ROLE_ASSIGNED,
            action=f"Assigned role '{role}' to API key",
            resource_type="api_key",
            resource_id=key_id,
            details={"role": role, "projects": projects},
            **ctx
        )

        return {
            "key_id": role_assignment.key_id,
            "role": role_assignment.role.value,
            "projects": role_assignment.projects
        }
    except HTTPException:
        raise
    except Exception as e:
        await audit_log(
            event_type=AuditEventType.AUTHZ_ROLE_ASSIGNED,
            action=f"Failed to assign role '{role}'",
            resource_type="api_key",
            resource_id=key_id,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"role": role, "error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/roles")
async def list_roles_endpoint(request: Request):
    """List all role assignments"""
    try:
        from src.core.rbac import list_roles
        db = request.app.state.db

        roles = await list_roles(db)
        return [
            {
                "key_id": r.key_id,
                "role": r.role.value,
                "projects": r.projects
            }
            for r in roles
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/roles/{key_id}")
async def get_role_endpoint(key_id: str, request: Request):
    """Get role assignment for a specific API key"""
    try:
        from src.core.rbac import get_role
        db = request.app.state.db

        role_assignment = await get_role(db, key_id)
        if not role_assignment:
            raise HTTPException(status_code=404, detail="Role not found")

        return {
            "key_id": role_assignment.key_id,
            "role": role_assignment.role.value,
            "projects": role_assignment.projects
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/roles/{key_id}")
async def revoke_role_endpoint(key_id: str, request: Request):
    """Revoke role assignment for an API key"""
    ctx = _get_request_context(request)
    try:
        from src.core.rbac import revoke_role
        db = request.app.state.db

        success = await revoke_role(db, key_id)
        if not success:
            raise HTTPException(status_code=404, detail="Role not found")

        # Audit log role revocation
        await audit_log(
            event_type=AuditEventType.AUTHZ_ROLE_REVOKED,
            action=f"Revoked role from API key",
            resource_type="api_key",
            resource_id=key_id,
            **ctx
        )

        return {"status": "revoked", "key_id": key_id}
    except HTTPException:
        raise
    except Exception as e:
        await audit_log(
            event_type=AuditEventType.AUTHZ_ROLE_REVOKED,
            action=f"Failed to revoke role",
            resource_type="api_key",
            resource_id=key_id,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/permissions")
async def list_permissions():
    """List all available permissions and roles"""
    from src.core.rbac import Role, Permission, ROLE_PERMISSIONS
    
    return {
        "roles": {
            role.value: {
                "permissions": [p.value for p in perms]
            }
            for role, perms in ROLE_PERMISSIONS.items()
        },
        "all_permissions": [p.value for p in Permission]
    }


@router.post("/data/publish", response_model=dict)
async def publish_data(event: DataPublishEvent, request: Request):
    """
    Main app publishes data change in ANY format.

    Supports multiple formats:
    - JSON (dict/object)
    - YAML (string)
    - TOML (string)
    - Plain text (string)
    - Markdown (string)

    Examples:
        # JSON (structured)
        POST /data/publish
        {
            "project_id": "proj_123",
            "data_key": "tech_stack",
            "data": {"backend": "Python/FastAPI", "frontend": "React"}
        }

        # YAML (structured)
        POST /data/publish
        {
            "project_id": "proj_123",
            "data_key": "database_config",
            "data": "database:\n  host: localhost\n  port: 5432",
            "data_format": "yaml"
        }

        # Plain text (unstructured)
        POST /data/publish
        {
            "project_id": "proj_123",
            "data_key": "architecture_notes",
            "data": "We use a microservices architecture with Redis for caching"
        }
    """
    ctx = _get_request_context(request)
    try:
        from src.core.metrics import record_event_published, publish_duration_seconds
        import time

        logger.info("Publishing data",
                   project_id=event.project_id,
                   data_key=event.data_key,
                   data_format=event.data_format)

        start_time = time.time()
        engine = request.app.state.context_engine
        sequence = await engine.publish_data(event)
        duration = time.time() - start_time

        # Record metrics
        record_event_published(event.project_id, event.data_format or "json")
        publish_duration_seconds.labels(project_id=event.project_id).observe(duration)

        # Data versioning now built on event sourcing - no separate version creation needed

        # Audit log data publication
        audit_details = {
            "data_format": event.data_format or "json",
            "sequence": sequence,
            "duration_ms": round(duration * 1000, 2),
        }

        await audit_log(
            event_type=AuditEventType.DATA_PUBLISHED,
            action=f"Published data '{event.data_key}'",
            project_id=event.project_id,
            resource_type="data",
            resource_id=event.data_key,
            details=audit_details,
            **ctx
        )

        logger.info("Data published successfully",
                   project_id=event.project_id,
                   data_key=event.data_key,
                   sequence=sequence,
                   duration_ms=round(duration * 1000, 2))

        # Emit webhook event
        webhook_data = {
            "project_id": event.project_id,
            "data_key": event.data_key,
            "sequence": sequence,
            "data_format": event.data_format or "json",
        }
        await emit_webhook(
            WebhookEventType.DATA_PUBLISHED,
            webhook_data,
            tenant_id=ctx.get("tenant_id"),
            project_id=event.project_id,
        )

        return {
            "status": "published",
            "project_id": event.project_id,
            "data_key": event.data_key,
            "sequence": sequence
        }
    except Exception as e:
        logger.error("Failed to publish data",
                    project_id=event.project_id,
                    data_key=event.data_key,
                    error=str(e))
        await audit_log(
            event_type=AuditEventType.DATA_PUBLISHED,
            action=f"Failed to publish data '{event.data_key}'",
            project_id=event.project_id,
            resource_type="data",
            resource_id=event.data_key,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"data_format": event.data_format or "json", "error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/register", response_model=RegistrationResponse)
async def register_agent(registration: AgentRegistration, request: Request):
    """
    Agent registers with semantic data needs.

    Example:
        POST /agents/register
        {
            "agent_id": "task-decomposer",
            "project_id": "proj_123",
            "data_needs": [
                "programming languages and frameworks",
                "event model with events and commands"
            ],
            "last_seen_sequence": "0"
        }
    """
    ctx = _get_request_context(request)
    try:
        from src.core.metrics import record_agent_registered, registration_duration_seconds
        import time

        logger.info("Registering agent",
                   agent_id=registration.agent_id,
                   project_id=registration.project_id,
                   needs_count=len(registration.data_needs),
                   notification_method=registration.notification_method)

        start_time = time.time()
        engine = request.app.state.context_engine
        response = await engine.register_agent(registration)
        duration = time.time() - start_time

        # Record metrics
        record_agent_registered(
            registration.project_id,
            registration.notification_method or "redis"
        )
        registration_duration_seconds.labels(project_id=registration.project_id).observe(duration)

        # Audit log agent registration
        await audit_log(
            event_type=AuditEventType.AGENT_REGISTERED,
            action=f"Registered agent '{registration.agent_id}'",
            project_id=registration.project_id,
            resource_type="agent",
            resource_id=registration.agent_id,
            details={
                "data_needs_count": len(registration.data_needs),
                "notification_method": registration.notification_method or "redis",
                "matched_needs_count": sum(response.matched_needs.values()),
                "duration_ms": round(duration * 1000, 2),
            },
            **ctx
        )

        logger.info("Agent registered successfully",
                   agent_id=registration.agent_id,
                   project_id=registration.project_id,
                   matched_needs_count=sum(response.matched_needs.values()),
                   duration_ms=round(duration * 1000, 2))

        # Emit webhook event
        await emit_webhook(
            WebhookEventType.AGENT_REGISTERED,
            {
                "agent_id": registration.agent_id,
                "project_id": registration.project_id,
                "data_needs": registration.data_needs,
                "matched_needs_count": sum(response.matched_needs.values()),
            },
            tenant_id=ctx.get("tenant_id"),
            project_id=registration.project_id,
        )

        return response
    except Exception as e:
        logger.error("Failed to register agent",
                    agent_id=registration.agent_id,
                    project_id=registration.project_id,
                    error=str(e))
        await audit_log(
            event_type=AuditEventType.AGENT_REGISTERED,
            action=f"Failed to register agent '{registration.agent_id}'",
            project_id=registration.project_id,
            resource_type="agent",
            resource_id=registration.agent_id,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    """Unregister an agent"""
    ctx = _get_request_context(request)
    engine = request.app.state.context_engine
    await engine.unregister_agent(agent_id)

    # Audit log agent unregistration
    await audit_log(
        event_type=AuditEventType.AGENT_UNREGISTERED,
        action=f"Unregistered agent '{agent_id}'",
        resource_type="agent",
        resource_id=agent_id,
        **ctx
    )

    # Emit webhook event
    await emit_webhook(
        WebhookEventType.AGENT_UNREGISTERED,
        {"agent_id": agent_id},
        tenant_id=ctx.get("tenant_id"),
    )

    return {"status": "unregistered", "agent_id": agent_id}


@router.get("/agents")
async def list_agents(request: Request):
    """List all registered agents"""
    engine = request.app.state.context_engine
    agents = engine.get_registered_agents()
    return {"agents": agents, "count": len(agents)}


@router.get("/agents/{agent_id}")
async def get_agent_info(agent_id: str, request: Request):
    """Get info about a registered agent"""
    engine = request.app.state.context_engine
    info = engine.get_agent_info(agent_id)
    if not info:
        raise HTTPException(status_code=404, detail="Agent not found")
    return info


@router.get("/projects/{project_id}/events")
async def get_project_events(project_id: str, request: Request, since: str = "0", count: int = 100):
    """Get events for a project"""
    engine = request.app.state.context_engine
    events = await engine.event_store.get_events_since(
        project_id,
        since,
        count
    )
    return {"events": events, "count": len(events)}


@router.get("/projects/{project_id}/data")
async def get_project_data(
    project_id: str,
    request: Request,
    include_content: bool = True,
    format: str = "json",
    include_events: bool = False,
    include_embeddings: bool = False,
    include_agents: bool = False,
):
    """
    Get all registered data for a project.

    Enhanced to replace /export endpoint functionality.

    Args:
        project_id: Project identifier
        include_content: Include full data content
        format: Output format (json, yaml, toml, csv, xml, markdown, toon, text)
        include_events: Include event stream data
        include_embeddings: Include embeddings data
        include_agents: Include agent registrations
    """
    ctx = _get_request_context(request)
    engine = request.app.state.context_engine

    # If requesting full export with events/embeddings/agents, use export manager
    if include_events or include_embeddings or include_agents:
        try:
            from src.core.export_import import ExportImportManager

            if format not in ["json", "toon", "yaml", "toml", "csv", "xml", "markdown", "text"]:
                raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")

            db = request.app.state.db
            export_manager = ExportImportManager(db)

            exported_data = await export_manager.export_project(
                project_id=project_id,
                format=format if format in ["json", "toon"] else "json",  # ExportManager only supports json/toon
                include_events=include_events,
                include_embeddings=include_embeddings,
                include_agents=include_agents,
            )

            # Audit log data export
            await audit_log(
                event_type=AuditEventType.DATA_EXPORTED,
                action=f"Exported project data via /data endpoint",
                project_id=project_id,
                resource_type="project",
                resource_id=project_id,
                details={
                    "format": format,
                    "include_events": include_events,
                    "include_embeddings": include_embeddings,
                    "include_agents": include_agents,
                },
                **ctx
            )

            # Return as response with appropriate content type
            content_type_map = {
                "json": "application/json",
                "yaml": "application/x-yaml",
                "toml": "application/toml",
                "csv": "text/csv",
                "xml": "application/xml",
                "markdown": "text/markdown",
                "toon": "text/plain",
                "text": "text/plain",
            }
            from fastapi.responses import Response
            return Response(content=exported_data, media_type=content_type_map.get(format, "application/json"))

        except Exception as e:
            logger.error("Project export failed", project_id=project_id, error=str(e))
            await audit_log(
                event_type=AuditEventType.DATA_EXPORTED,
                action=f"Failed to export project data",
                project_id=project_id,
                resource_type="project",
                resource_id=project_id,
                result="failure",
                severity=AuditEventSeverity.ERROR,
                details={"format": format, "error": str(e)},
                **ctx
            )
            raise HTTPException(status_code=500, detail=str(e))

    # Standard data listing
    data_keys = await engine.semantic_matcher.get_registered_data(project_id)

    if not include_content:
        return {"project_id": project_id, "data_keys": data_keys, "count": len(data_keys)}

    # Fetch full data for each key from PostgreSQL
    from src.core.db_models import Embedding
    from sqlalchemy import select

    result = []
    db = request.app.state.db
    async with db.session() as session:
        for key in data_keys:
            # Fetch embedding data from PostgreSQL
            stmt = select(Embedding).where(
                Embedding.project_id == project_id,
                Embedding.data_key == key
            )
            query_result = await session.execute(stmt)
            rows = query_result.scalars().all()

            for row in rows:
                data_obj = row.data if isinstance(row.data, dict) else {}

                # Generate TOON format
                try:
                    import toon_format as toon
                    toon_str = toon.encode(data_obj)
                except:
                    toon_str = "TOON format not available"

                result.append({
                    "data_key": row.node_key or key,
                    "description": row.description,
                    "data": data_obj,
                    "data_format": row.data_format or "json",
                    "is_structured": row.node_type in ["object", "row"],
                    "toon": toon_str
                })

    return result


@router.post("/projects/{project_id}/query")
async def query_project(project_id: str, query_req: QueryRequest, request: Request):
    """
    Search project data by semantic similarity without agent registration.

    This endpoint finds and returns data that semantically matches your search terms,
    returning complete data sources ranked by similarity. Perfect for:
    - One-off data lookups from CLIs or scripts
    - Interactive exploration of what data is available
    - Testing semantic matching quality
    - Finding relevant data during development

    Example:
        POST /projects/my-app/query
        {
            "query": "authentication methods OAuth JWT",
            "top_k": 3,
            "response_format": "toon"
        }

    Returns:
        Complete data sources that match your search, ranked by semantic similarity,
        formatted as TOON or JSON.
    """
    try:
        engine = request.app.state.context_engine
        matches = await engine.query_project_data(
            project_id=project_id,
            query=query_req.query,
            top_k=query_req.top_k,
            threshold=query_req.threshold
        )

        # Apply token limit truncation if specified
        if query_req.max_tokens and matches:
            matches_dict = {query_req.query: matches}
            truncated = engine._truncate_matches(matches_dict, query_req.max_tokens)
            matches = truncated.get(query_req.query, [])

        # Convert to MatchedDataSource models with enhanced metadata
        import json
        import toon_format as toon
        import tiktoken

        # Initialize tokenizer for counting
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except:
            enc = None

        matched_sources = []
        for match in matches:
            # Ensure data is a dict (nodes can have string content)
            data = match["data"]
            if not isinstance(data, dict):
                data = {"content": data}

            # Calculate token count
            token_count = None
            if enc:
                try:
                    data_str = json.dumps(data)
                    token_count = len(enc.encode(data_str))
                except:
                    pass

            # Generate preview
            preview = None
            try:
                data_str = json.dumps(data, indent=2)
                preview = data_str[:200] + "..." if len(data_str) > 200 else data_str
            except:
                pass

            matched_sources.append(
                MatchedDataSource(
                    data_key=match["data_key"],
                    similarity=match["similarity"],
                    data=data,
                    description=match.get("description"),
                    token_count=token_count,
                    preview=preview
                )
            )

        # Build response object
        response_data = QueryResponse(
            query=query_req.query,
            matches=matched_sources,
            total_matches=len(matched_sources)
        )

        # Format according to preference
        if query_req.response_format == "toon":
            from fastapi.responses import PlainTextResponse
            try:
                content = toon.encode(response_data.model_dump())
                return PlainTextResponse(content=content, media_type="text/plain")
            except NotImplementedError:
                # TOON encoder not yet available, fall back to JSON
                return response_data
        else:
            # Default JSON response
            return response_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/cleanup")
async def cleanup_all_projects(request: Request):
    """
    Run cleanup for all projects (admin only).

    Applies retention policies:
    - TTL for events
    - Event count limits
    - Stale agent cleanup

    Returns:
        Cleanup statistics
    """
    try:
        from src.core.retention import get_retention_manager_from_env

        db = request.app.state.db
        retention_manager = get_retention_manager_from_env(db)

        stats = await retention_manager.cleanup_all_projects()

        logger.info("Admin cleanup executed", **stats)

        return {
            "status": "success",
            "stats": stats
        }
    except Exception as e:
        logger.error("Cleanup failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/cleanup/{project_id}")
async def cleanup_project(project_id: str, request: Request):
    """
    Run cleanup for a specific project (admin only).

    Args:
        project_id: Project identifier

    Returns:
        Cleanup statistics
    """
    try:
        from src.core.retention import get_retention_manager_from_env

        db = request.app.state.db
        retention_manager = get_retention_manager_from_env(db)

        stats = await retention_manager.cleanup_project(project_id)

        logger.info("Project cleanup executed", **stats)

        return {
            "status": "success",
            "stats": stats
        }
    except Exception as e:
        logger.error("Project cleanup failed",
                    project_id=project_id,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/retention/{project_id}")
async def get_retention_stats(project_id: str, request: Request):
    """
    Get retention statistics for a project.

    Args:
        project_id: Project identifier

    Returns:
        Retention statistics
    """
    try:
        from src.core.retention import get_retention_manager_from_env

        db = request.app.state.db
        retention_manager = get_retention_manager_from_env(db)

        stats = await retention_manager.get_retention_stats(project_id)

        return stats
    except Exception as e:
        logger.error("Failed to get retention stats",
                    project_id=project_id,
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ========================================================================
# Import Endpoints
# ========================================================================

@router.post("/projects/{project_id}/import")
async def import_project(
    project_id: str,
    request: Request,
    format: str = "json",
    validate_only: bool = False,
    overwrite: bool = False,
):
    """
    Import project data.

    Args:
        project_id: Project identifier (must match data)
        format: Import format (json or toon)
        validate_only: If True, only validate without importing
        overwrite: If True, overwrite existing data

    Returns:
        Import statistics and validation results
    """
    ctx = _get_request_context(request)
    try:
        from src.core.export_import import ExportImportManager

        if format not in ["json", "toon"]:
            raise HTTPException(status_code=400, detail="Format must be 'json' or 'toon'")

        # Read request body
        body = await request.body()
        data = body.decode("utf-8")

        db = request.app.state.db
        import_manager = ExportImportManager(db)

        result = await import_manager.import_project(
            data=data,
            format=format,
            validate_only=validate_only,
            overwrite=overwrite,
        )

        # Verify project_id matches
        if result.get("status") == "success":
            import_project_id = result.get("stats", {}).get("project_id") or result.get("validation", {}).get("project_id")
            if import_project_id and import_project_id != project_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Project ID mismatch: URL has '{project_id}' but data has '{import_project_id}'"
                )

        # Audit log data import
        await audit_log(
            event_type=AuditEventType.DATA_IMPORTED,
            action=f"Imported project data" + (" (validation only)" if validate_only else ""),
            project_id=project_id,
            resource_type="project",
            resource_id=project_id,
            details={
                "format": format,
                "validate_only": validate_only,
                "overwrite": overwrite,
                "status": result.get("status"),
            },
            **ctx
        )

        logger.info("Project import processed",
                   project_id=project_id,
                   status=result.get("status"))

        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message", "Import failed"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Project import failed",
                    project_id=project_id,
                    error=str(e))
        await audit_log(
            event_type=AuditEventType.DATA_IMPORTED,
            action=f"Failed to import project data",
            project_id=project_id,
            resource_type="project",
            resource_id=project_id,
            result="failure",
            severity=AuditEventSeverity.ERROR,
            details={"format": format, "error": str(e)},
            **ctx
        )
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# BATCH OPERATIONS - Phase 2 Performance Enhancement
# ============================================================================

@router.post("/batch/publish", response_model=dict)
async def batch_publish_data(events: List[DataPublishEvent], request: Request):
    """
    Batch publish multiple data items in a single request.

    Reduces round-trip overhead for bulk operations.

    Example:
        POST /batch/publish
        [
            {
                "project_id": "proj_123",
                "data_key": "config_1",
                "data": {"setting": "value1"}
            },
            {
                "project_id": "proj_123",
                "data_key": "config_2",
                "data": {"setting": "value2"}
            }
        ]

    Returns:
        {
            "status": "completed",
            "total": 2,
            "successful": 2,
            "failed": 0,
            "results": [...]
        }
    """
    try:
        import time
        from src.core.metrics import record_event_published, publish_duration_seconds

        logger.info(f"Batch publishing {len(events)} items")
        start_time = time.time()
        engine = request.app.state.context_engine

        results = []
        successful = 0
        failed = 0

        for event in events:
            try:
                sequence = await engine.publish_data(event)
                record_event_published(event.project_id, event.data_format or "json")
                results.append({
                    "status": "success",
                    "project_id": event.project_id,
                    "data_key": event.data_key,
                    "sequence": sequence
                })
                successful += 1
            except Exception as e:
                results.append({
                    "status": "failed",
                    "project_id": event.project_id,
                    "data_key": event.data_key,
                    "error": str(e)
                })
                failed += 1
                logger.error(f"Failed to publish {event.data_key}: {e}")

        duration = time.time() - start_time

        logger.info(f"Batch publish completed",
                   total=len(events),
                   successful=successful,
                   failed=failed,
                   duration_ms=round(duration * 1000, 2))

        return {
            "status": "completed",
            "total": len(events),
            "successful": successful,
            "failed": failed,
            "duration_ms": round(duration * 1000, 2),
            "results": results
        }

    except Exception as e:
        logger.error(f"Batch publish failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch/register", response_model=dict)
async def batch_register_agents(registrations: List[AgentRegistration], request: Request):
    """
    Batch register multiple agents in a single request.

    Reduces round-trip overhead for bulk agent registrations.

    Example:
        POST /batch/register
        [
            {
                "agent_id": "agent_1",
                "project_id": "proj_123",
                "data_needs": ["api endpoints", "authentication"]
            },
            {
                "agent_id": "agent_2",
                "project_id": "proj_123",
                "data_needs": ["database schema", "models"]
            }
        ]

    Returns:
        {
            "status": "completed",
            "total": 2,
            "successful": 2,
            "failed": 0,
            "results": [...]
        }
    """
    try:
        import time
        from src.core.metrics import record_agent_registered, registration_duration_seconds

        logger.info(f"Batch registering {len(registrations)} agents")
        start_time = time.time()
        engine = request.app.state.context_engine

        results = []
        successful = 0
        failed = 0

        for registration in registrations:
            try:
                response = await engine.register_agent(registration)
                record_agent_registered(
                    registration.project_id,
                    registration.notification_method
                )
                results.append({
                    "status": "success",
                    "agent_id": registration.agent_id,
                    "project_id": registration.project_id,
                    "matched_needs": response.matched_needs,
                    "notification_channel": response.notification_channel
                })
                successful += 1
            except Exception as e:
                results.append({
                    "status": "failed",
                    "agent_id": registration.agent_id,
                    "project_id": registration.project_id,
                    "error": str(e)
                })
                failed += 1
                logger.error(f"Failed to register {registration.agent_id}: {e}")

        duration = time.time() - start_time

        logger.info(f"Batch registration completed",
                   total=len(registrations),
                   successful=successful,
                   failed=failed,
                   duration_ms=round(duration * 1000, 2))

        return {
            "status": "completed",
            "total": len(registrations),
            "successful": successful,
            "failed": failed,
            "duration_ms": round(duration * 1000, 2),
            "results": results
        }

    except Exception as e:
        logger.error(f"Batch registration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
