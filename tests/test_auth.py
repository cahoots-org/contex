"""Tests for authentication with PostgreSQL"""

import hashlib

import pytest
from sqlalchemy import select
from src.core.auth import bootstrap_admin, create_api_key, revoke_api_key
from src.core.db_models import APIKey
from src.core.identity import resolve_identity
from src.core.rbac import Role, expand_role


@pytest.mark.asyncio
async def test_key_management(db):
    # Create
    raw_key, key_id = await create_api_key(db, "test-mgmt")
    assert raw_key.startswith("ck_")

    # Verify existence in database
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db.session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        stored_key = result.scalar_one_or_none()
        assert stored_key is not None
        assert stored_key.name == "test-mgmt"

    # Revoke
    success = await revoke_api_key(db, key_id)
    assert success

    # Verify deleted
    async with db.session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        stored_key = result.scalar_one_or_none()
        assert stored_key is None


@pytest.mark.asyncio
async def test_revoke_nonexistent_key(db):
    """Test revoking a key that doesn't exist"""
    success = await revoke_api_key(db, "nonexistent_key_id")
    assert success is False


@pytest.mark.asyncio
async def test_bootstrap_admin_uses_provided_key_once(db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_KEY", "ck_provided_admin_key_123")
    await bootstrap_admin(db)
    await bootstrap_admin(db)

    ident = await resolve_identity(db, "ck_provided_admin_key_123")
    assert ident.scopes == expand_role(Role.ADMIN)
    async with db.session() as session:
        assert len((await session.execute(select(APIKey))).all()) == 1
