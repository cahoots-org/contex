"""Redis-style protected mode: refuse remote binds when auth is unconfigured."""
from __future__ import annotations

import ipaddress

from src.core.logging import get_logger

logger = get_logger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_protected_mode(host: str, *, auth_on: bool, protected: bool) -> None:
    if auth_on:
        return
    if not protected:
        logger.warning(
            "Protected mode disabled and AUTH_ENABLED=false — server is OPEN to network",
            host=host,
        )
        return
    if not _is_loopback(host):
        raise RuntimeError(
            f"Protected mode: refusing to bind {host} with authentication disabled. "
            "Set AUTH_ENABLED=true, bind to loopback, or set CONTEX_PROTECTED_MODE=false."
        )
