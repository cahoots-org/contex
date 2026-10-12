"""Tests for RBAC role presets."""

from src.core.rbac import Role, Permission, expand_role, ROLE_PERMISSIONS


def test_admin_has_every_permission():
    """Admin spans the whole permission set (including future additions)."""
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)


def test_publishers_write_and_consumers_read():
    assert expand_role(Role.PUBLISHER) == {Permission.PUBLISH_DATA}
    assert expand_role(Role.CONSUMER) == {Permission.QUERY_DATA}
