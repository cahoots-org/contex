import pytest
from src.core.protected_mode import check_protected_mode


def test_blocks_remote_bind_when_unconfigured():
    with pytest.raises(RuntimeError):
        check_protected_mode("0.0.0.0", auth_on=False, protected=True)


def test_allows_loopback_when_unconfigured():
    check_protected_mode("127.0.0.1", auth_on=False, protected=True)  # no raise


def test_allows_remote_when_auth_on():
    check_protected_mode("0.0.0.0", auth_on=True, protected=True)  # no raise


def test_escape_hatch_allows_remote():
    check_protected_mode("0.0.0.0", auth_on=False, protected=False)  # no raise
