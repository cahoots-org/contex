"""Tests for authentication with PostgreSQL"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.auth import APIKeyMiddleware, create_api_key, revoke_api_key


@pytest_asyncio.fixture
async def app_with_auth(db):
    app = FastAPI()
    app.state.db = db  # Make DB available via app state

    app.add_middleware(APIKeyMiddleware)

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.mark.asyncio
async def test_auth_middleware_no_key(app_with_auth):
    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        # Public path should work
        resp = await client.get("/health")
        assert resp.status_code == 200

        # Protected path should fail
        resp = await client.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing API Key"


@pytest.mark.asyncio
async def test_auth_middleware_invalid_key(app_with_auth):
    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        resp = await client.get("/protected", headers={"X-API-Key": "invalid_key"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API Key"


@pytest.mark.asyncio
async def test_auth_middleware_valid_key(app_with_auth, db):
    # Create a key
    raw_key, _ = await create_api_key(db, "test-key")

    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        resp = await client.get("/protected", headers={"X-API-Key": raw_key})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_key_management(db):
    # Create
    raw_key, api_key = await create_api_key(db, "test-mgmt")
    assert raw_key.startswith("ck_")
    assert api_key.name == "test-mgmt"

    # Verify existence in database
    from sqlalchemy import select
    from src.core.db_models import APIKey
    import hashlib

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db.session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        stored_key = result.scalar_one_or_none()
        assert stored_key is not None
        assert stored_key.name == "test-mgmt"

    # Revoke
    success = await revoke_api_key(db, api_key.key_id)
    assert success

    # Verify deleted
    async with db.session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        stored_key = result.scalar_one_or_none()
        assert stored_key is None


@pytest.mark.asyncio
async def test_create_key_with_scopes(db):
    """Test creating API key with scopes"""
    raw_key, api_key = await create_api_key(
        db, "scoped-key", scopes=["read", "write"]
    )

    assert "read" in api_key.scopes
    assert "write" in api_key.scopes


@pytest.mark.asyncio
async def test_create_key_with_tenant(db):
    """Test creating API key with tenant association"""
    # First create a tenant
    from src.core.db_models import Tenant as TenantModel
    async with db.session() as session:
        tenant = TenantModel(
            tenant_id="test_tenant",
            name="Test Tenant",
            plan="free",
        )
        session.add(tenant)

    # Create key with tenant
    raw_key, api_key = await create_api_key(
        db, "tenant-key", tenant_id="test_tenant"
    )

    assert api_key.tenant_id == "test_tenant"


@pytest.mark.asyncio
async def test_revoke_nonexistent_key(db):
    """Test revoking a key that doesn't exist"""
    success = await revoke_api_key(db, "nonexistent_key_id")
    assert success is False
