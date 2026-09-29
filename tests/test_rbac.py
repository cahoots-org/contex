"""Tests for RBAC (Role-Based Access Control) with PostgreSQL"""

import pytest
from src.core.rbac import (
    Role,
    Permission,
    assign_role,
    expand_role,
    ROLE_PERMISSIONS,
)
from src.core.db_models import APIKey


async def create_api_key(db, key_id: str) -> None:
    """Helper to create an API key for testing role assignments."""
    async with db.session() as session:
        api_key = APIKey(
            key_id=key_id,
            key_hash=f"hash_{key_id}",
            name=f"Test Key {key_id}",
            prefix="ctx_",
            scopes=["read", "write"],
        )
        session.add(api_key)


def test_live_permissions_exist():
    """The enforced permissions are all defined."""
    for name in ("PUBLISH_DATA", "QUERY_DATA", "VIEW_PROJECT_DATA",
                 "VIEW_PROJECT_EVENTS", "VIEW_VERSION_HISTORY"):
        assert hasattr(Permission, name)


def test_admin_has_every_permission():
    """Admin spans the whole permission set (including future additions)."""
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)


def test_expand_role_returns_frozenset():
    """expand_role returns a frozenset of the role's permissions."""
    perms = expand_role(Role.READONLY)
    assert isinstance(perms, frozenset)
    assert Permission.QUERY_DATA in perms
    assert Permission.PUBLISH_DATA not in perms


class TestRoles:
    """Test role definitions and permissions"""

    def test_all_roles_defined(self):
        assert Role.ADMIN == "admin"
        assert Role.PUBLISHER == "publisher"
        assert Role.CONSUMER == "consumer"
        assert Role.READONLY == "readonly"

    def test_admin_has_all_permissions(self):
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for permission in Permission:
            assert permission in admin_perms

    def test_publisher_permissions(self):
        publisher_perms = ROLE_PERMISSIONS[Role.PUBLISHER]
        assert Permission.PUBLISH_DATA in publisher_perms
        assert Permission.VIEW_PROJECT_DATA in publisher_perms
        # Publishers are write-side: they do not get the query permission.
        assert Permission.QUERY_DATA not in publisher_perms

    def test_consumer_permissions(self):
        consumer_perms = ROLE_PERMISSIONS[Role.CONSUMER]
        assert Permission.QUERY_DATA in consumer_perms
        assert Permission.PUBLISH_DATA not in consumer_perms

    def test_readonly_permissions(self):
        readonly_perms = ROLE_PERMISSIONS[Role.READONLY]
        assert Permission.QUERY_DATA in readonly_perms
        assert Permission.VIEW_PROJECT_DATA in readonly_perms
        assert Permission.PUBLISH_DATA not in readonly_perms


class TestRoleAssignment:
    """Test role assignment operations"""

    @pytest.mark.asyncio
    async def test_assign_role(self, db):
        await create_api_key(db, "test_key_1")
        role_assignment = await assign_role(
            db, key_id="test_key_1", role=Role.PUBLISHER, projects=["proj1", "proj2"]
        )
        assert role_assignment.key_id == "test_key_1"
        assert role_assignment.role == Role.PUBLISHER
        assert role_assignment.projects == ["proj1", "proj2"]

    @pytest.mark.asyncio
    async def test_assign_role_all_projects(self, db):
        await create_api_key(db, "test_key_2")
        role_assignment = await assign_role(
            db, key_id="test_key_2", role=Role.ADMIN, projects=None
        )
        assert role_assignment.projects == []  # Empty list means all projects


class TestPermissionChecking:
    """Test APIKeyRole.has_permission project scoping"""

    @pytest.mark.asyncio
    async def test_has_permission_with_access(self, db):
        await create_api_key(db, "test_key_5")
        role_assignment = await assign_role(
            db, "test_key_5", Role.PUBLISHER, ["proj1", "proj2"]
        )
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "proj1")
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "proj2")

    @pytest.mark.asyncio
    async def test_has_permission_without_access(self, db):
        await create_api_key(db, "test_key_6")
        role_assignment = await assign_role(db, "test_key_6", Role.PUBLISHER, ["proj1"])
        # Wrong project → denied.
        assert not role_assignment.has_permission(Permission.PUBLISH_DATA, "proj2")
        # Permission not granted to the role → denied.
        assert not role_assignment.has_permission(Permission.QUERY_DATA, "proj1")

    @pytest.mark.asyncio
    async def test_has_permission_all_projects(self, db):
        await create_api_key(db, "test_key_7")
        role_assignment = await assign_role(db, "test_key_7", Role.ADMIN, [])
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "any_project")
        assert role_assignment.has_permission(Permission.QUERY_DATA, "another_project")

    @pytest.mark.asyncio
    async def test_has_permission_global_operation(self, db):
        await create_api_key(db, "test_key_8")
        role_assignment = await assign_role(db, "test_key_8", Role.ADMIN, ["proj1"])
        # No project_id → global check, allowed when the role has the permission.
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, None)
        assert role_assignment.has_permission(Permission.QUERY_DATA, None)
