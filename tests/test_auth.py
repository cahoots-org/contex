import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from src.core.auth import APIKeyMiddleware, create_api_key, revoke_api_key

@pytest_asyncio.fixture
async def app_with_auth(redis):
    app = FastAPI()
    app.state.redis = redis  # Make Redis available via app state
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
async def test_auth_middleware_valid_key(app_with_auth, redis):
    # Create a key
    raw_key, _ = await create_api_key(redis, "test-key")
    
    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        resp = await client.get("/protected", headers={"X-API-Key": raw_key})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_key_management(redis):
    # Create
    raw_key, api_key = await create_api_key(redis, "test-mgmt")
    assert raw_key.startswith("ck_")
    assert api_key.name == "test-mgmt"
    
    # Verify existence
    import hashlib
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    assert await redis.exists(f"contex:apikey:{key_hash}")
    
    # Revoke
    success = await revoke_api_key(redis, api_key.key_id)
    assert success
    assert not await redis.exists(f"contex:apikey:{key_hash}")
