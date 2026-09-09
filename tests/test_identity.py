# tests/test_identity.py
import hashlib
import pytest
from src.core.identity import resolve_identity, Identity, ANONYMOUS_IDENTITY
from src.core import keyhash
from src.core.auth import create_api_key
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


@pytest.mark.asyncio
async def test_powerless_key_has_empty_scopes(db):
    """A key with no role and empty scopes is valid but grants nothing."""
    key_id = await _make_key(db, "ck_powerless_dddd4444", scopes=[])
    ident = await resolve_identity(db, "ck_powerless_dddd4444")
    assert ident is not None, "Key exists — must not be None"
    assert ident.scopes == frozenset(), "No role, no scopes — must be empty frozenset"


@pytest.mark.asyncio
async def test_parse_scopes_ignores_legacy_strings(db):
    """Unknown scope strings are silently dropped; valid Permission values are kept."""
    key_id = await _make_key(db, "ck_legacy_scopes_eeee5555",
                             scopes=["read", "write", Permission.QUERY_DATA.value])
    ident = await resolve_identity(db, "ck_legacy_scopes_eeee5555")
    assert ident is not None
    assert ident.scopes == frozenset({Permission.QUERY_DATA})


@pytest.mark.asyncio
async def test_keyhash_plain_roundtrip(db):
    """No salt: create_api_key + resolve_identity round-trips via plain sha256."""
    raw_key, _ = await create_api_key(db, "plain-roundtrip")
    ident = await resolve_identity(db, raw_key)
    assert ident is not None


@pytest.mark.asyncio
async def test_keyhash_peppered_roundtrip(db, monkeypatch):
    """With salt set: create_api_key + resolve_identity round-trips via HMAC pepper."""
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "test-pepper")
    raw_key, _ = await create_api_key(db, "peppered-roundtrip")
    ident = await resolve_identity(db, raw_key)
    assert ident is not None


@pytest.mark.asyncio
async def test_keyhash_dual_verify_legacy(db, monkeypatch):
    """With salt set, a key stored with legacy plain sha256 is still found (dual-verify)."""
    raw_key = "ck_legacy_plain_key_for_dual_verify"
    key_id = raw_key[-8:]
    legacy_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db.session() as session:
        session.add(APIKey(
            key_id=key_id,
            key_hash=legacy_hash,
            name="legacy-plain",
            prefix=raw_key[:7],
            scopes=[],
            tenant_id="default",
        ))
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "test-pepper")
    ident = await resolve_identity(db, raw_key)
    assert ident is not None, "dual-verify must find legacy plain-sha key when salt is active"
