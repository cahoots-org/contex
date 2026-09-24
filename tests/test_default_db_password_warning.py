import logging

from src.core.config import DEFAULT_DATABASE_URL
from src.core.hardened_config import check_hardened_config


def _warned(records) -> bool:
    return any("default database credentials" in r.message.lower() for r in records)


def test_warns_when_database_url_unset(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert _warned(caplog.records)


def test_warns_when_database_url_is_builtin_default(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert _warned(caplog.records)


def test_no_warning_when_custom_database_url_set(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://contex:s3cret-unique@db.internal:5432/contex",
    )
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert not _warned(caplog.records)
