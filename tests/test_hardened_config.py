import logging

import pytest

from src.core.hardened_config import check_hardened_config


def test_noop_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    check_hardened_config()  # must not raise


def test_passes_when_auth_on_with_defaults(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_KEY", raising=False)
    check_hardened_config()  # must not raise


def test_warns_on_missing_pepper(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert any("API_KEY_PEPPER" in r.message for r in caplog.records)


def test_rejects_bootstrap_key_that_cannot_authenticate(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_KEY", "my-admin-key-1234567890")
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_KEY"):
        check_hardened_config()
