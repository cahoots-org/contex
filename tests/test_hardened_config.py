import pytest

from src.core.hardened_config import check_hardened_config


def test_noop_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    check_hardened_config()  # must not raise


def test_raises_when_auth_on_and_jwt_secret_missing(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET"):
        check_hardened_config()


def test_passes_when_auth_on_and_jwt_secret_set(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "x" * 32)
    check_hardened_config()  # must not raise


def test_warns_on_missing_pepper(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "x" * 32)
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert any("API_KEY_PEPPER" in r.message for r in caplog.records)
