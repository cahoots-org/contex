"""Boot-time config preflight: refuse to boot on settings that would silently break auth."""
from __future__ import annotations

import os

from src.core.authz import auth_enabled
from src.core.config import DEFAULT_DATABASE_URL
from src.core.logging import get_logger

logger = get_logger(__name__)


def check_hardened_config() -> None:
    """Raise on a config that can't work; warn on soft gaps (pepper, default DB password)."""
    if os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL) == DEFAULT_DATABASE_URL:
        logger.warning(
            "Using built-in default database credentials - set DATABASE_URL "
            "before any network-reachable deployment"
        )

    for name in ("API_KEY_PEPPER", "METRICS_TOKEN"):
        value = os.getenv(name)
        if value and len(value) < 16:
            raise RuntimeError(f"{name} must be at least 16 characters")

    if not auth_enabled():
        return

    bootstrap_key = os.getenv("BOOTSTRAP_ADMIN_KEY")
    if bootstrap_key and not bootstrap_key.startswith("ck_"):
        raise RuntimeError("BOOTSTRAP_ADMIN_KEY must start with ck_")

    if not os.getenv("API_KEY_PEPPER"):
        logger.warning(
            "API_KEY_PEPPER not set - API keys hashed with plain SHA-256 "
            "(acceptable for high-entropy keys; set a pepper for defense in depth)"
        )
