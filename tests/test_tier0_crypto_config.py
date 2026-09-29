"""End-to-end integration tests for Tier 0 crypto/config hardening (#68, #69)."""
import hashlib
import secrets
from datetime import datetime, timezone

import pytest

import src.core.keyhash as keyhash
from src.core.auth import create_api_key
from src.core.db_models import APIKey as APIKeyModel
from src.core.identity import resolve_identity
from src.core.tenant import DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# #69 — API-key pepper round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_pepper_roundtrip(db, monkeypatch):
    """Peppered create → resolve_identity succeeds."""
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "test-pepper-roundtrip")

    raw_key, _meta = await create_api_key(
        db, name="pepper-rt", tenant_id=DEFAULT_TENANT_ID
    )

    identity = await resolve_identity(db, raw_key)
    assert identity is not None
    assert identity.tenant_id == DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# #69 — Legacy dual-verify: plain-SHA row resolves when a pepper is set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_legacy_dual_verify(db, monkeypatch):
    """Legacy plain-SHA key_hash row resolves even when API_KEY_PEPPER is active."""
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "test-pepper-legacy")

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
# Peppered hash differs from plain SHA
# ---------------------------------------------------------------------------


def test_hash_api_key_peppered_differs_from_plain_sha(monkeypatch):
    """hash_api_key output with a pepper must not equal the plain SHA-256."""
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "some-pepper")
    raw = "ck_test_value"
    assert keyhash.hash_api_key(raw) != hashlib.sha256(raw.encode()).hexdigest()
