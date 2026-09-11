"""Boot-time hardened-config preflight: fail closed when auth is on but secrets are missing."""
from __future__ import annotations

import os

from src.core.authz import auth_enabled
from src.core.logging import get_logger

logger = get_logger(__name__)


def check_hardened_config() -> None:
    """Refuse to boot when auth is on but a no-safe-default secret is unset.

    Soft gaps (missing pepper, wildcard CORS) are warnings, not failures.
    No-op when auth is off (demo mode).
    """
    if not auth_enabled():
        return

    if not os.getenv("SERVICE_ACCOUNT_JWT_SECRET"):
        raise RuntimeError(
            "SERVICE_ACCOUNT_JWT_SECRET must be set when AUTH_ENABLED=true"
        )

    if not os.getenv("API_KEY_PEPPER"):
        logger.warning(
            "API_KEY_PEPPER not set - API keys hashed with plain SHA-256 "
            "(acceptable for high-entropy keys; set a pepper for defense in depth)"
        )

    cors = os.getenv("CORS_ORIGINS", "*")
    if "*" in cors:
        logger.warning(
            "CORS_ORIGINS allows a wildcard origin; credentials are disabled under wildcard"
        )
