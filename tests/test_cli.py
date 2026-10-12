import pytest

from src.cli import create_key, list_keys
from src.core.identity import resolve_identity
from src.core.rbac import Permission, Role


@pytest.mark.asyncio
async def test_created_key_carries_role_tenant_and_projects(db):
    raw_key = await create_key(db, "ingest", Role.PUBLISHER, "acme", ["p1"])

    ident = await resolve_identity(db, raw_key)
    assert ident.tenant_id == "acme"
    assert ident.role == Role.PUBLISHER
    assert ident.projects == ("p1",)
    assert Permission.PUBLISH_DATA in ident.scopes

    assert [row[1:] for row in await list_keys(db)] == [
        ("ingest", raw_key[:7], "acme", "publisher", "p1")
    ]
