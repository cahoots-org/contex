# src/core/authz.py
"""Route-level authorization dependencies. Fail-closed when AUTH_ENABLED."""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from src.core.identity import Identity, ANONYMOUS_IDENTITY, resolve_identity
from src.core.rbac import Permission


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def _extract_credential(request: Request) -> str | None:
    key = request.headers.get("X-API-Key")
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


async def get_identity(request: Request) -> Identity:
    """Resolve the caller. Anonymous (all scopes) when auth is off; 401 when on and invalid."""
    if not auth_enabled():
        return ANONYMOUS_IDENTITY
    credential = _extract_credential(request)
    if not credential:
        raise HTTPException(status_code=401, detail="Missing API Key")
    identity = await resolve_identity(request.app.state.db, credential)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    request.state.identity = identity
    return identity


def require(*permissions: Permission):
    """Return a dependency that requires all `permissions`. Empty = authenticated-only."""
    from fastapi import Depends

    async def _dep(identity: Identity = Depends(get_identity)) -> Identity:
        if not set(permissions).issubset(identity.scopes):
            missing = sorted(p.value for p in set(permissions) - identity.scopes)
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "missing_permissions": missing},
            )
        return identity

    _dep._authz_marker = permissions  # sentinel for the coverage walker
    return _dep


async def public() -> None:
    """Explicit public marker. No-op dependency; presence = intentionally unauthenticated."""
    return None


public._public_marker = True  # type: ignore[attr-defined]
