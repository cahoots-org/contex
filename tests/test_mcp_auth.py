# tests/test_mcp_auth.py
import hashlib

import pytest

from src.core.mcp_adapter import ApiKeyVerifier


@pytest.mark.asyncio
async def test_verifier_rejects_unknown_key(db):
    v = ApiKeyVerifier(lambda: db)
    assert await v.verify_token("ck_nope_nope_nope") is None


@pytest.mark.asyncio
async def test_verifier_accepts_and_carries_claims(db):
    from src.core.db_models import APIKey, Tenant
    from src.core.rbac import assign_role, Role

    raw = "ck_mcp_verifier_dddd4444"
    async with db.session() as s:
        # FK: api_keys.tenant_id references tenants.tenant_id — create tenant first.
        s.add(Tenant(tenant_id="tenant-y", name="tenant-y"))
        s.add(APIKey(
            key_id="dddd4444",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            name="t",
            prefix=raw[:7],
            scopes=[],
            tenant_id="tenant-y",
        ))
    await assign_role(db, "dddd4444", Role.PUBLISHER, projects=["p1"])
    tok = await ApiKeyVerifier(lambda: db).verify_token(raw)
    assert tok is not None
    assert tok.client_id == "dddd4444"
    assert "publish_data" in tok.scopes
    assert tok.claims["tenant_id"] == "tenant-y"
    assert tok.claims["projects"] == ["p1"]
