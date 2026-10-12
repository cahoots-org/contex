"""Tests for RBAC (Role-Based Access Control) with PostgreSQL"""

import pytest
from sqlalchemy import select

from src.core.rbac import (
    Role,
    Permission,
    assign_role,
    expand_role,
    ROLE_PERMISSIONS,
)
from src.core.db_models import APIKey, APIKeyRole


async def create_api_key(db, key_id: str) -> None:
    """Helper to create an API key for testing role assignments."""
    async with db.session() as session:
        api_key = APIKey(
            key_id=key_id,
            key_hash=f"hash_{key_id}",
            name=f"Test Key {key_id}",
            prefix="ctx_",
        )
        session.add(api_key)


def test_admin_has_every_permission():
    """Admin spans the whole permission set (including future additions)."""
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)


def test_publishers_write_and_consumers_read():
    assert expand_role(Role.PUBLISHER) == {Permission.PUBLISH_DATA}
    assert expand_role(Role.CONSUMER) == {Permission.QUERY_DATA}


@pytest.mark.asyncio
async def test_assign_role_replaces_the_previous_role(db):
    await create_api_key(db, "test_key_1")
    await assign_role(db, key_id="test_key_1", role=Role.PUBLISHER, projects=["proj1", "proj2"])
    await assign_role(db, key_id="test_key_1", role=Role.CONSUMER)

    async with db.session() as session:
        row = (await session.execute(
            select(APIKeyRole).where(APIKeyRole.key_id == "test_key_1")
        )).scalar_one()
    assert row.role == "consumer"
    assert row.projects == []  # Empty list means all projects
