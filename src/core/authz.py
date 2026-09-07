# src/core/authz.py
"""Route-level authorization dependencies. Fail-closed when AUTH_ENABLED."""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request

from src.core.identity import Identity, ANONYMOUS_IDENTITY, resolve_identity
from src.core.rbac import Permission


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def extract_credential(request: Request) -> str | None:
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
    credential = extract_credential(request)
    if not credential:
        raise HTTPException(status_code=401, detail="Missing API Key")
    identity = await resolve_identity(request.app.state.db, credential)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    request.state.identity = identity
    return identity


class require:
    """Route dependency: the caller must hold all `permissions`. No args = any authenticated caller."""

    def __init__(self, *permissions: Permission):
        self.permissions = frozenset(permissions)

    async def __call__(self, identity: Identity = Depends(get_identity)) -> Identity:
        if not self.permissions.issubset(identity.scopes):
            raise HTTPException(status_code=403, detail="Forbidden")
        return identity


class _Public:
    """Route dependency marking a route as intentionally unauthenticated."""

    async def __call__(self) -> None:
        return None


public = _Public()
