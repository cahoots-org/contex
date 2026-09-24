"""Boot-time hardened-config preflight: fail closed when auth is on but secrets are missing."""
from __future__ import annotations

import os

from src.core.authz import auth_enabled
from src.core.config import DEFAULT_DATABASE_URL
from src.core.logging import get_logger

logger = get_logger(__name__)


def check_hardened_config() -> None:
    """Refuse to boot when auth is on but a no-safe-default secret is unset.

    Soft gaps (missing pepper, wildcard CORS, default DB credentials) are
    warnings, not failures. The default-credential check runs in every mode,
    since demo mode is where the built-in password is normally used.
    """
    if os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL) == DEFAULT_DATABASE_URL:
        logger.warning(
            "Using the built-in default database credentials - safe for local "
            "development only; set DATABASE_URL to a unique password for any "
            "network-reachable deployment"
        )

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
