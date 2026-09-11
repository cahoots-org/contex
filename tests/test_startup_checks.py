"""Regression tests for the boot-time startup checks.

These guard the lifespan startup path, which no test previously exercised — a
shadowed `auth_enabled` (a bool local calling the function) crash-looped the
server on real startup while every ASGITransport test (which skips the lifespan)
stayed green.
"""
import pytest

import main


def test_startup_checks_pass_when_auth_off_on_loopback(monkeypatch):
    # Regression: this calls auth_enabled() — a shadowing bool local would raise
    # "'bool' object is not callable" here.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("CONTEX_HOST", "127.0.0.1")
    main.run_startup_checks(main.app)


def test_startup_checks_fail_closed_when_auth_on_without_jwt_secret(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET"):
        main.run_startup_checks(main.app)
