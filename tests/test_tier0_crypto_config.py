"""End-to-end integration tests for Tier 0 crypto/config hardening (#68, #69)."""
import hashlib
import secrets
from datetime import datetime, timezone

import pytest

import src.core.keyhash as keyhash
import src.core.service_accounts as sa_module
from src.core.auth import create_api_key
from src.core.db_models import APIKey as APIKeyModel, ServiceAccount as ServiceAccountModel
from src.core.db_models import ServiceAccountKey as ServiceAccountKeyModel
from src.core.identity import resolve_identity
from src.core.service_accounts import (
    ServiceAccount,
    ServiceAccountManager,
    ServiceAccountType,
    _jwt_secret,
)
from src.core.rbac import Role
from src.core.tenant import DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# #69 — API-key pepper round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_pepper_roundtrip(db, monkeypatch):
    """Peppered create → resolve_identity succeeds."""
    monkeypatch.setattr(keyhash, "_get_salt", lambda: "test-pepper-roundtrip")

    raw_key, _meta = await create_api_key(
        db, name="pepper-rt", tenant_id=DEFAULT_TENANT_ID
    )

    identity = await resolve_identity(db, raw_key)
    assert identity is not None
    assert identity.tenant_id == DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# #69 — Legacy dual-verify: plain-SHA row resolves when salt is set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_legacy_dual_verify(db, monkeypatch):
    """Legacy plain-SHA key_hash row resolves even when API_KEY_SALT is active."""
    monkeypatch.setattr(keyhash, "_get_salt", lambda: "test-pepper-legacy")

    raw_key = f"ck_{secrets.token_urlsafe(32)}"
    legacy_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = secrets.token_hex(8)

    async with db.session() as session:
        session.add(APIKeyModel(
            key_id=key_id,
            key_hash=legacy_hash,
            name="legacy-sha-key",
            prefix=raw_key[:7],
            scopes=[],
            tenant_id=DEFAULT_TENANT_ID,
            created_at=datetime.now(timezone.utc),
        ))

    identity = await resolve_identity(db, raw_key)
    assert identity is not None
    assert identity.key_id == key_id


# ---------------------------------------------------------------------------
# Salted hash differs from plain SHA
# ---------------------------------------------------------------------------


def test_hash_api_key_salted_differs_from_plain_sha(monkeypatch):
    """hash_api_key output with a salt must not equal the plain SHA-256."""
    monkeypatch.setattr(keyhash, "_get_salt", lambda: "some-pepper")
    raw = "ck_test_value"
    assert keyhash.hash_api_key(raw) != hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# #68 — JWT persistence: issue → validate, two independent reads same secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_persistence_env_secret(monkeypatch):
    """JWT secret from env survives simulated restart; issue→validate succeeds."""
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "jwt-persistence-secret-padded-1234")
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)

    mgr = ServiceAccountManager(db=None)
    account = ServiceAccount(
        account_id="sa_persist_test",
        name="persist-sa",
        account_type=ServiceAccountType.INTERNAL,
        role=Role.READONLY,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    token_obj = await mgr.issue_token(account, expires_in=60)

    # Simulate independent process read
    read1 = _jwt_secret()
    read2 = _jwt_secret()
    assert read1 == read2 == "jwt-persistence-secret-padded-1234"

    claims = await mgr.validate_token(token_obj.access_token)
    assert claims is not None
    assert claims["sub"] == "sa_persist_test"


# ---------------------------------------------------------------------------
# ServiceAccount dual-verify: plain-SHA SA key resolves when salt is set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_account_key_legacy_dual_verify(db, monkeypatch):
    """Legacy plain-SHA service_account_keys row authenticates when API_KEY_SALT is set."""
    monkeypatch.setattr(keyhash, "_get_salt", lambda: "sa-test-pepper")

    raw_key = f"sak_{secrets.token_urlsafe(32)}"
    legacy_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    account_id = f"sa_{secrets.token_hex(8)}"
    key_id = secrets.token_hex(8)
    now = datetime.now(timezone.utc)

    async with db.session() as session:
        session.add(ServiceAccountModel(
            account_id=account_id,
            name="legacy-sa",
            account_type=ServiceAccountType.INTERNAL.value,
            role=Role.READONLY.value,
            tenant_id=DEFAULT_TENANT_ID,
            allowed_projects=[],
            scopes=[],
            keys=[{"key_id": key_id, "created_at": now.isoformat(), "expires_at": None}],
            created_at=now,
            is_active=True,
            total_requests=0,
        ))
        session.add(ServiceAccountKeyModel(
            key_hash=legacy_hash,
            account_id=account_id,
            key_id=key_id,
        ))

    mgr = ServiceAccountManager(db=db)
    account = await mgr.authenticate(raw_key)
    assert account is not None
    assert account.account_id == account_id
