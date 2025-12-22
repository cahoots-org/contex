"""Authentication module for Contex"""

import asyncio
import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel
from src.core.logging import get_logger

logger = get_logger(__name__)


def _record_auth_event(
    event_type: str,
    action: str,
    actor_ip: str = None,
    api_key_prefix: str = None,
    key_id: str = None,
    endpoint: str = None,
    success: bool = True,
):
    """Record authentication event in audit log (async fire-and-forget)"""
    try:
        from src.core.audit import audit_log, AuditEventType, AuditEventSeverity

        async def _log():
            if event_type == "failure":
                await audit_log(
                    event_type=AuditEventType.AUTH_LOGIN_FAILURE,
                    action=action,
                    actor_ip=actor_ip,
                    endpoint=endpoint,
                    severity=AuditEventSeverity.WARNING,
                    result="failure",
                    details={"api_key_prefix": api_key_prefix},
                )
            elif event_type == "success":
                await audit_log(
                    event_type=AuditEventType.AUTH_API_KEY_USED,
                    action=action,
                    actor_id=key_id,
                    actor_type="api_key",
                    actor_ip=actor_ip,
                    endpoint=endpoint,
                    result="success",
                )

        asyncio.create_task(_log())
    except Exception:
        pass


class APIKey(BaseModel):
    """API Key response model"""
    key_id: str
    name: str
    prefix: str
    scopes: List[str] = []
    created_at: str
    tenant_id: Optional[str] = None


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API keys"""

    def __init__(self, app, public_paths: List[str] = None):
        super().__init__(app)
        self.public_paths = public_paths or [
            "/health",
            "/api/docs",
            "/api/openapi.json",
            "/sandbox",
            "/static",
            "/favicon.ico",
        ]

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path == "/" or any(
            request.url.path.startswith(path) for path in self.public_paths
        ):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        actor_ip = request.client.host if request.client else None
        endpoint = str(request.url.path)

        if not api_key:
            _record_auth_event(
                event_type="failure",
                action="Authentication failed: Missing API Key",
                actor_ip=actor_ip,
                endpoint=endpoint,
            )
            return JSONResponse(status_code=401, content={"detail": "Missing API Key"})

        key_id = await self.validate_key(request, api_key)
        if not key_id:
            _record_auth_event(
                event_type="failure",
                action="Authentication failed: Invalid API Key",
                actor_ip=actor_ip,
                api_key_prefix=api_key[:10] if len(api_key) >= 10 else api_key,
                endpoint=endpoint,
            )
            return JSONResponse(status_code=401, content={"detail": "Invalid API Key"})

        # Store key_id in request state for RBAC middleware
        request.state.api_key_id = key_id

        # Record successful authentication (only for state-changing operations)
        if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
            _record_auth_event(
                event_type="success",
                action=f"API key authenticated for {request.method} {endpoint}",
                actor_ip=actor_ip,
                key_id=key_id,
                endpoint=endpoint,
            )

        return await call_next(request)

    async def validate_key(self, request: Request, api_key: str) -> Optional[str]:
        """Validate API key against database and return key_id if valid"""
        # Key format: ck_<random>
        if not api_key.startswith("ck_"):
            return None

        # Hash key for lookup
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        # Get database from app state
        db: DatabaseManager = request.app.state.db

        async with db.session() as session:
            result = await session.execute(
                select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
            )
            api_key_record = result.scalar_one_or_none()

            if api_key_record:
                return api_key_record.key_id

        return None


async def create_api_key(
    db: DatabaseManager,
    name: str,
    scopes: List[str] = None,
    tenant_id: Optional[str] = None,
) -> tuple[str, APIKey]:
    """
    Create a new API key.

    Args:
        db: Database manager
        name: Name for the API key
        scopes: List of scopes/permissions
        tenant_id: Optional tenant ID to associate with key

    Returns:
        Tuple of (raw_key, APIKey model)
    """
    # Generate key
    raw_key = f"ck_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = secrets.token_hex(8)
    created_at = datetime.now(timezone.utc)

    async with db.session() as session:
        # Create database record
        api_key_record = APIKeyModel(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            prefix=raw_key[:7],
            scopes=scopes or [],
            tenant_id=tenant_id,
            created_at=created_at,
        )
        session.add(api_key_record)

    # Return response model
    api_key = APIKey(
        key_id=key_id,
        name=name,
        prefix=raw_key[:7],
        scopes=scopes or [],
        created_at=created_at.isoformat(),
        tenant_id=tenant_id,
    )

    logger.info("API key created", key_id=key_id, name=name)

    return raw_key, api_key


async def revoke_api_key(db: DatabaseManager, key_id: str) -> bool:
    """
    Revoke an API key by ID.

    Args:
        db: Database manager
        key_id: Key ID to revoke

    Returns:
        True if key was revoked, False if not found
    """
    from sqlalchemy import delete

    async with db.session() as session:
        result = await session.execute(
            delete(APIKeyModel).where(APIKeyModel.key_id == key_id)
        )

        if result.rowcount > 0:
            logger.info("API key revoked", key_id=key_id)
            return True

    return False


async def list_api_keys(db: DatabaseManager, tenant_id: Optional[str] = None) -> List[APIKey]:
    """
    List all API keys.

    Args:
        db: Database manager
        tenant_id: Optional tenant ID to filter by

    Returns:
        List of API keys
    """
    async with db.session() as session:
        query = select(APIKeyModel)
        if tenant_id:
            query = query.where(APIKeyModel.tenant_id == tenant_id)

        result = await session.execute(query)
        keys = result.scalars().all()

        return [
            APIKey(
                key_id=k.key_id,
                name=k.name,
                prefix=k.prefix,
                scopes=k.scopes or [],
                created_at=k.created_at.isoformat() if k.created_at else "",
            )
            for k in keys
        ]


async def get_api_key(db: DatabaseManager, key_id: str) -> Optional[APIKey]:
    """
    Get an API key by ID.

    Args:
        db: Database manager
        key_id: Key ID to look up

    Returns:
        APIKey if found, None otherwise
    """
    async with db.session() as session:
        result = await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_id == key_id)
        )
        k = result.scalar_one_or_none()

        if k:
            return APIKey(
                key_id=k.key_id,
                name=k.name,
                prefix=k.prefix,
                scopes=k.scopes or [],
                created_at=k.created_at.isoformat() if k.created_at else "",
            )

    return None


async def get_api_key_by_hash(db: DatabaseManager, key_hash: str) -> Optional[APIKeyModel]:
    """
    Get an API key record by hash (internal use).

    Args:
        db: Database manager
        key_hash: SHA256 hash of the raw key

    Returns:
        APIKeyModel if found, None otherwise
    """
    async with db.session() as session:
        result = await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        )
        return result.scalar_one_or_none()
