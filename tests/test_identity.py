# tests/test_identity.py
import hashlib
import pytest
import pytest_asyncio
from src.core.identity import resolve_identity, Identity, ANONYMOUS_IDENTITY
from src.core.rbac import Permission, Role, assign_role
from src.core.db_models import APIKey, Tenant


async def _make_key(db, raw_key: str, *, scopes=None, tenant_id=None) -> str:
    key_id = raw_key[-8:]
    async with db.session() as session:
        # FK: api_keys.tenant_id references tenants.tenant_id — create tenant first.
        if tenant_id is not None:
            session.add(Tenant(tenant_id=tenant_id, name=tenant_id))
        session.add(APIKey(
            key_id=key_id,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            name="t", prefix=raw_key[:7], scopes=scopes or [], tenant_id=tenant_id,
        ))
    return key_id


@pytest.mark.asyncio
async def test_unknown_key_denies(db):
    assert await resolve_identity(db, "ck_does_not_exist") is None


@pytest.mark.asyncio
async def test_role_preset_expands_to_scopes(db):
    key_id = await _make_key(db, "ck_role_preset_aaaa1111")
    await assign_role(db, key_id, Role.PUBLISHER, projects=["p1"])
    ident = await resolve_identity(db, "ck_role_preset_aaaa1111")
    assert Permission.PUBLISH_DATA in ident.scopes
    assert Permission.QUERY_DATA not in ident.scopes
    assert ident.role == Role.PUBLISHER
    assert ident.projects == ("p1",)


@pytest.mark.asyncio
async def test_explicit_scopes_are_authoritative(db):
    # explicit scopes on the key override role expansion
    key_id = await _make_key(db, "ck_custom_scopes_bbbb2222",
                             scopes=[Permission.PUBLISH_DATA.value, Permission.QUERY_DATA.value])
    await assign_role(db, key_id, Role.READONLY)  # readonly would NOT include PUBLISH_DATA
    ident = await resolve_identity(db, "ck_custom_scopes_bbbb2222")
    assert ident.scopes == frozenset({Permission.PUBLISH_DATA, Permission.QUERY_DATA})


@pytest.mark.asyncio
async def test_tenant_comes_from_key(db):
    await _make_key(db, "ck_tenant_cccc3333", tenant_id="tenant-x")
    ident = await resolve_identity(db, "ck_tenant_cccc3333")
    assert ident.tenant_id == "tenant-x"


def test_anonymous_identity_has_all_scopes():
    assert ANONYMOUS_IDENTITY.scopes == frozenset(Permission)


def test_has_project():
    scoped = Identity(key_id="k", scopes=frozenset(), tenant_id=None, projects=("p1",), role=None)
    assert scoped.has_project("p1") is True
    assert scoped.has_project("p2") is False
    unscoped = Identity(key_id="k", scopes=frozenset(), tenant_id=None, projects=(), role=None)
    assert unscoped.has_project("anything") is True  # empty = all projects
