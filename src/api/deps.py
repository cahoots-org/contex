"""Shared FastAPI dependencies for API routes."""
from fastapi import Request

from src.core.tenant import TenantManager


def get_tenant_manager(request: Request) -> TenantManager:
    return TenantManager(request.app.state.db)
