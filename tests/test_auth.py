"""Tests for authentication with PostgreSQL"""

import pytest
from src.core.auth import create_api_key, list_api_keys, revoke_api_key
from src.core.db_models import Tenant as TenantModel


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


async def _ensure_tenant(db, tenant_id: str) -> None:
    async with db.session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(TenantModel).where(TenantModel.tenant_id == tenant_id)
        )
        if result.scalar_one_or_none() is None:
            session.add(TenantModel(tenant_id=tenant_id, name=tenant_id, plan="free"))


@pytest.mark.asyncio
async def test_list_api_keys_tenant_filter(db):
    """list_api_keys with tenant_id returns only that tenant's keys"""
    await _ensure_tenant(db, "t1")
    await _ensure_tenant(db, "t2")

    _, key_t1 = await create_api_key(db, "t1-key", tenant_id="t1")
    _, key_t2 = await create_api_key(db, "t2-key", tenant_id="t2")

    t1_keys = await list_api_keys(db, tenant_id="t1")
    t1_ids = {k.key_id for k in t1_keys}
    assert key_t1.key_id in t1_ids
    assert key_t2.key_id not in t1_ids


@pytest.mark.asyncio
async def test_revoke_api_key_tenant_mismatch(db):
    """revoke_api_key returns False when key belongs to a different tenant"""
    await _ensure_tenant(db, "t1")
    await _ensure_tenant(db, "t2")

    _, key_t2 = await create_api_key(db, "t2-to-revoke", tenant_id="t2")

    result = await revoke_api_key(db, key_t2.key_id, tenant_id="t1")
    assert result is False

    # Key must still exist
    from sqlalchemy import select
    from src.core.db_models import APIKey
    async with db.session() as session:
        row = await session.execute(
            select(APIKey).where(APIKey.key_id == key_t2.key_id)
        )
        assert row.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_revoke_api_key_tenant_match(db):
    """revoke_api_key returns True and deletes the key when tenant matches"""
    await _ensure_tenant(db, "t1")

    _, key_t1 = await create_api_key(db, "t1-to-revoke", tenant_id="t1")

    result = await revoke_api_key(db, key_t1.key_id, tenant_id="t1")
    assert result is True

    from sqlalchemy import select
    from src.core.db_models import APIKey
    async with db.session() as session:
        row = await session.execute(
            select(APIKey).where(APIKey.key_id == key_t1.key_id)
        )
        assert row.scalar_one_or_none() is None
