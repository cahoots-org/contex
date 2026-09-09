"""Tests for service-account JWT secret management (_jwt_secret)."""
import importlib
import sys

import pytest

import src.core.service_accounts as sa_module
from src.core.rbac import Role
from src.core.service_accounts import (
    ServiceAccount,
    ServiceAccountManager,
    ServiceAccountType,
    _jwt_secret,
)


def _make_account() -> ServiceAccount:
    return ServiceAccount(
        account_id="acct_test",
        name="test-sa",
        account_type=ServiceAccountType.INTERNAL,
        role=Role.READONLY,
        created_at="2026-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# _jwt_secret() with env var set
# ---------------------------------------------------------------------------


def test_jwt_secret_returns_env_value(monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "fixed-secret-abc")
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    assert _jwt_secret() == "fixed-secret-abc"


def test_jwt_secret_consistent_across_reads(monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "stable-secret-xyz")
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    assert _jwt_secret() == _jwt_secret()


# ---------------------------------------------------------------------------
# issue + validate round-trip with env secret (simulates restart stability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_validate_with_env_secret(monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "env-secret-roundtrip")
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)

    mgr = ServiceAccountManager(db=None)
    account = _make_account()

    token_obj = await mgr.issue_token(account, expires_in=60)

    # Simulate a "new process read" by calling _jwt_secret() fresh — still same env value
    assert _jwt_secret() == "env-secret-roundtrip"

    claims = await mgr.validate_token(token_obj.access_token)
    assert claims is not None
    assert claims["sub"] == "acct_test"


# ---------------------------------------------------------------------------
# Fail-closed: auth ON + secret unset → RuntimeError
# ---------------------------------------------------------------------------


def test_jwt_secret_raises_when_auth_on_and_secret_unset(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    monkeypatch.setattr("src.core.service_accounts.auth_enabled", lambda: True)

    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET must be set"):
        _jwt_secret()


@pytest.mark.asyncio
async def test_issue_token_raises_when_auth_on_and_secret_unset(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    monkeypatch.setattr("src.core.service_accounts.auth_enabled", lambda: True)

    mgr = ServiceAccountManager(db=None)
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET must be set"):
        await mgr.issue_token(_make_account())


@pytest.mark.asyncio
async def test_validate_token_raises_when_auth_on_and_secret_unset(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    monkeypatch.setattr("src.core.service_accounts.auth_enabled", lambda: True)

    mgr = ServiceAccountManager(db=None)
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET must be set"):
        await mgr.validate_token("any.token.here")


# ---------------------------------------------------------------------------
# Auth OFF + secret unset → ephemeral fallback still works within process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_validate_auth_off_no_secret(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    monkeypatch.setattr("src.core.service_accounts.auth_enabled", lambda: False)

    mgr = ServiceAccountManager(db=None)
    account = _make_account()

    token_obj = await mgr.issue_token(account, expires_in=60)
    claims = await mgr.validate_token(token_obj.access_token)

    assert claims is not None
    assert claims["sub"] == "acct_test"


def test_fallback_secret_stable_within_process(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    monkeypatch.setattr(sa_module, "_fallback_jwt_secret", None)
    monkeypatch.setattr("src.core.service_accounts.auth_enabled", lambda: False)

    first = _jwt_secret()
    second = _jwt_secret()
    assert first == second
