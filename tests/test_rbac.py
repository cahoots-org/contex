"""Tests for RBAC (Role-Based Access Control)"""

import pytest
import pytest_asyncio
from src.core.rbac import (
    Role,
    Permission,
    assign_role,
    get_role,
    revoke_role,
    list_roles,
    get_role_permissions,
    check_permission,
    ROLE_PERMISSIONS
)


class TestRoles:
    """Test role definitions and permissions"""

    def test_all_roles_defined(self):
        """Test that all roles are defined"""
        assert Role.ADMIN == "admin"
        assert Role.PUBLISHER == "publisher"
        assert Role.CONSUMER == "consumer"
        assert Role.READONLY == "readonly"

    def test_all_permissions_defined(self):
        """Test that all permissions are defined"""
        assert Permission.PUBLISH_DATA == "publish_data"
        assert Permission.QUERY_DATA == "query_data"
        assert Permission.REGISTER_AGENT == "register_agent"

    def test_admin_has_all_permissions(self):
        """Test that admin role has all permissions"""
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        
        # Admin should have all permissions
        for permission in Permission:
            assert permission in admin_perms

    def test_publisher_permissions(self):
        """Test publisher role permissions"""
        publisher_perms = ROLE_PERMISSIONS[Role.PUBLISHER]
        
        # Publishers can publish
        assert Permission.PUBLISH_DATA in publisher_perms
        assert Permission.VIEW_PROJECT_DATA in publisher_perms
        
        # Publishers cannot manage agents
        assert Permission.REGISTER_AGENT not in publisher_perms
        assert Permission.DELETE_AGENT not in publisher_perms
        
        # Publishers cannot manage API keys
        assert Permission.CREATE_API_KEY not in publisher_perms

    def test_consumer_permissions(self):
        """Test consumer role permissions"""
        consumer_perms = ROLE_PERMISSIONS[Role.CONSUMER]
        
        # Consumers can register agents and query
        assert Permission.REGISTER_AGENT in consumer_perms
        assert Permission.QUERY_DATA in consumer_perms
        assert Permission.LIST_AGENTS in consumer_perms
        
        # Consumers cannot publish
        assert Permission.PUBLISH_DATA not in consumer_perms
        
        # Consumers cannot manage API keys
        assert Permission.CREATE_API_KEY not in consumer_perms

    def test_readonly_permissions(self):
        """Test readonly role permissions"""
        readonly_perms = ROLE_PERMISSIONS[Role.READONLY]
        
        # Readonly can only query and view
        assert Permission.QUERY_DATA in readonly_perms
        assert Permission.VIEW_PROJECT_DATA in readonly_perms
        assert Permission.LIST_AGENTS in readonly_perms
        
        # Readonly cannot modify anything
        assert Permission.PUBLISH_DATA not in readonly_perms
        assert Permission.REGISTER_AGENT not in readonly_perms
        assert Permission.DELETE_AGENT not in readonly_perms


class TestRoleAssignment:
    """Test role assignment operations"""

    @pytest.mark.asyncio
    async def test_assign_role(self, redis):
        """Test assigning a role to an API key"""
        role_assignment = await assign_role(
            redis,
            key_id="test_key_1",
            role=Role.PUBLISHER,
            projects=["proj1", "proj2"]
        )
        
        assert role_assignment.key_id == "test_key_1"
        assert role_assignment.role == Role.PUBLISHER
        assert role_assignment.projects == ["proj1", "proj2"]

    @pytest.mark.asyncio
    async def test_assign_role_all_projects(self, redis):
        """Test assigning a role with access to all projects"""
        role_assignment = await assign_role(
            redis,
            key_id="test_key_2",
            role=Role.ADMIN,
            projects=None
        )
        
        assert role_assignment.projects == []  # Empty list means all projects

    @pytest.mark.asyncio
    async def test_get_role(self, redis):
        """Test retrieving a role assignment"""
        # Assign a role first
        await assign_role(redis, "test_key_3", Role.CONSUMER, ["proj1"])
        
        # Retrieve it
        role_assignment = await get_role(redis, "test_key_3")
        
        assert role_assignment.key_id == "test_key_3"
        assert role_assignment.role == Role.CONSUMER
        assert role_assignment.projects == ["proj1"]

    @pytest.mark.asyncio
    async def test_get_role_default_readonly(self, redis):
        """Test that non-existent roles default to readonly"""
        role_assignment = await get_role(redis, "nonexistent_key")
        
        assert role_assignment.role == Role.READONLY
        assert role_assignment.projects == []

    @pytest.mark.asyncio
    async def test_revoke_role(self, redis):
        """Test revoking a role assignment"""
        # Assign a role
        await assign_role(redis, "test_key_4", Role.PUBLISHER, ["proj1"])
        
        # Revoke it
        success = await revoke_role(redis, "test_key_4")
        assert success
        
        # Should now default to readonly
        role_assignment = await get_role(redis, "test_key_4")
        assert role_assignment.role == Role.READONLY

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_role(self, redis):
        """Test revoking a non-existent role"""
        success = await revoke_role(redis, "nonexistent_key")
        assert not success

    @pytest.mark.asyncio
    async def test_list_roles(self, redis):
        """Test listing all role assignments"""
        # Assign multiple roles
        await assign_role(redis, "key1", Role.ADMIN, [])
        await assign_role(redis, "key2", Role.PUBLISHER, ["proj1"])
        await assign_role(redis, "key3", Role.CONSUMER, ["proj2", "proj3"])
        
        # List all roles
        roles = await list_roles(redis)
        
        assert len(roles) >= 3
        key_ids = [r.key_id for r in roles]
        assert "key1" in key_ids
        assert "key2" in key_ids
        assert "key3" in key_ids


class TestPermissionChecking:
    """Test permission checking logic"""

    @pytest.mark.asyncio
    async def test_has_permission_with_access(self, redis):
        """Test permission check when access is granted"""
        role_assignment = await assign_role(
            redis,
            "test_key_5",
            Role.PUBLISHER,
            ["proj1", "proj2"]
        )
        
        # Publisher can publish to assigned projects
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "proj1")
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "proj2")

    @pytest.mark.asyncio
    async def test_has_permission_without_access(self, redis):
        """Test permission check when access is denied"""
        role_assignment = await assign_role(
            redis,
            "test_key_6",
            Role.PUBLISHER,
            ["proj1"]
        )
        
        # Publisher cannot publish to non-assigned projects
        assert not role_assignment.has_permission(Permission.PUBLISH_DATA, "proj2")
        
        # Publisher cannot register agents (not in role permissions)
        assert not role_assignment.has_permission(Permission.REGISTER_AGENT, "proj1")

    @pytest.mark.asyncio
    async def test_has_permission_all_projects(self, redis):
        """Test permission check with access to all projects"""
        role_assignment = await assign_role(
            redis,
            "test_key_7",
            Role.ADMIN,
            []  # Empty = all projects
        )
        
        # Admin can access any project
        assert role_assignment.has_permission(Permission.PUBLISH_DATA, "any_project")
        assert role_assignment.has_permission(Permission.QUERY_DATA, "another_project")

    @pytest.mark.asyncio
    async def test_has_permission_global_operation(self, redis):
        """Test permission check for global operations (no project)"""
        role_assignment = await assign_role(
            redis,
            "test_key_8",
            Role.ADMIN,
            ["proj1"]  # Limited to proj1
        )
        
        # Global operations (no project_id) should be allowed if permission exists
        assert role_assignment.has_permission(Permission.CREATE_API_KEY, None)
        assert role_assignment.has_permission(Permission.LIST_API_KEYS, None)

    def test_get_role_permissions(self):
        """Test getting all permissions for a role"""
        admin_perms = get_role_permissions(Role.ADMIN)
        publisher_perms = get_role_permissions(Role.PUBLISHER)
        
        # Admin should have more permissions than publisher
        assert len(admin_perms) > len(publisher_perms)
        
        # Publisher perms should be subset of admin perms
        assert publisher_perms.issubset(admin_perms)

    def test_check_permission(self):
        """Test checking if a role has a specific permission"""
        # Admin has all permissions
        assert check_permission(Role.ADMIN, Permission.PUBLISH_DATA)
        assert check_permission(Role.ADMIN, Permission.CREATE_API_KEY)
        
        # Publisher can publish but not create keys
        assert check_permission(Role.PUBLISHER, Permission.PUBLISH_DATA)
        assert not check_permission(Role.PUBLISHER, Permission.CREATE_API_KEY)
        
        # Consumer can register agents but not publish
        assert check_permission(Role.CONSUMER, Permission.REGISTER_AGENT)
        assert not check_permission(Role.CONSUMER, Permission.PUBLISH_DATA)
        
        # Readonly can only view
        assert check_permission(Role.READONLY, Permission.QUERY_DATA)
        assert not check_permission(Role.READONLY, Permission.PUBLISH_DATA)
        assert not check_permission(Role.READONLY, Permission.REGISTER_AGENT)
